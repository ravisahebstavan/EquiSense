"""Multi-asset market services — derivatives, risk simulation, flow, valuation regime.

This is the layer that turns the newly available data into decisions:

  * NSE F&O archives  → a real option chain, IV surface and futures term
    structure for Indian underlyings, plus honest leverage/margin arithmetic.
  * NSE MTO archives  → delivery percentage, which replaces the volume-only
    crowding proxy that `engine/novel.py` documents as a limitation.
  * NSE index closes  → the exchange's own index P/E, so "is the MARKET
    expensive versus its own history" is answerable on the same percentile
    footing as the single-stock question.
  * Monte Carlo       → portfolio VaR/CVaR and drawdown risk on the ACTUAL
    book rather than on an assumed Gaussian.

Orchestration only: every calculation lives in `engine/`.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engine import derivatives as D
from ..engine import montecarlo as MC
from ..ingestion import nse_archive as NA
from ..models import (Company, DeliveryStat, DerivativeQuote, IndexObservation,
                      PriceObservation)

DEFAULT_RISK_FREE = 0.065


def latest_derivative_date(session: Session,
                           symbol: Optional[str] = None) -> Optional[date]:
    q = select(func.max(DerivativeQuote.trade_date))
    if symbol:
        q = q.where(DerivativeQuote.symbol == symbol.upper())
    return session.scalar(q)


# ------------------------------------------------------------- derivatives

def derivative_snapshot(session: Session, symbol: str = "NIFTY",
                        risk_free: float = DEFAULT_RISK_FREE) -> dict:
    """Full derivatives view for one underlying: futures curve + option chain.

    Returns a `available: False` payload with the reason rather than raising,
    because "no F&O data ingested yet" is an ordinary state on a fresh database
    and the UI needs to say so plainly.
    """
    symbol = symbol.upper()
    td = latest_derivative_date(session, symbol)
    if td is None:
        return {"available": False, "symbol": symbol,
                "reason": "no F&O bhavcopy ingested for this symbol yet",
                "hint": "run: python -m equisense.ingestion --nse-days 5"}

    out: dict = {"available": True, "symbol": symbol, "trade_date": td.isoformat()}

    # --- futures term structure -> implied financing rate
    curve = NA.futures_curve(session, symbol, td)
    spot = _underlying_price(session, symbol, td)
    if curve and spot:
        out["term_structure"] = D.term_structure(spot, curve, as_of=td)
    else:
        out["term_structure"] = {"contracts": [],
                                 "reason": "no futures rows or no spot reference"}

    # --- option chain -> IV surface, skew, OI structure
    chain = NA.option_chain(session, symbol, td)
    if chain.get("rows"):
        quotes = [D.OptionQuote(strike=q["strike"], kind=q["kind"], price=q["price"],
                                open_interest=q["open_interest"],
                                change_in_oi=q["change_in_oi"], volume=q["volume"])
                  for q in chain["quotes"] if q["price"]]
        analytics = D.option_chain_analytics(
            quotes, chain["underlying"] or spot or 0.0,
            chain["days_to_expiry"], risk_free=risk_free,
            lot_size=chain["lot_size"] or 1, period=td.isoformat())
        analytics["expiry"] = chain["expiry"].isoformat()
        analytics["expiries_available"] = chain.get("expiries_available", [])
        # strip the full surface down for transport; the shape is what matters
        surface = analytics.get("iv_surface", [])
        analytics["iv_surface"] = surface[:400]
        out["option_chain"] = analytics
    else:
        out["option_chain"] = {"computable": False,
                               "reason": chain.get("reason", "no option rows")}
    return out


def _underlying_price(session: Session, symbol: str, td: date) -> Optional[float]:
    """Underlying reference: the bhavcopy's own UndrlygPric, else the stored
    nominal equity close. Never the total-return close — an option strike is a
    nominal price and comparing it to a dividend-adjusted series is meaningless."""
    px = session.scalar(
        select(DerivativeQuote.underlying_price).where(
            DerivativeQuote.symbol == symbol.upper(),
            DerivativeQuote.trade_date == td,
            DerivativeQuote.underlying_price.isnot(None)).limit(1))
    if px:
        return float(px)
    cid = session.scalar(select(Company.id).where(Company.ticker == symbol.upper()))
    if cid is None:
        return None
    row = session.execute(
        select(PriceObservation.close_raw, PriceObservation.close)
        .where(PriceObservation.company_id == cid)
        .order_by(PriceObservation.obs_date.desc()).limit(1)).first()
    if not row:
        return None
    return float(row[0] if row[0] is not None else row[1])


def position_risk(session: Session, symbol: str, legs: list[dict],
                  account_equity: float,
                  risk_free: float = DEFAULT_RISK_FREE) -> dict:
    """Greeks, margin estimate and leverage reality-check for a proposed
    multi-leg F&O position.

    `legs` = [{kind: call|put|future, strike, quantity (lots, +long/-short),
               premium}]
    """
    td = latest_derivative_date(session, symbol)
    if td is None:
        return {"available": False, "reason": "no F&O data for this symbol"}
    chain = NA.option_chain(session, symbol, td)
    spot = chain.get("underlying") or _underlying_price(session, symbol, td)
    lot = chain.get("lot_size") or 1
    dte = max(chain.get("days_to_expiry") or 7, 1)
    if not spot:
        return {"available": False, "reason": "no underlying reference price"}

    # ATM implied vol drives both the greeks and the margin scan
    quotes = [D.OptionQuote(strike=q["strike"], kind=q["kind"], price=q["price"],
                            open_interest=q["open_interest"])
              for q in chain.get("quotes", []) if q["price"]]
    atm_iv = None
    if quotes:
        a = D.option_chain_analytics(quotes, spot, dte, risk_free, lot)
        atm_iv = a.get("atm_iv_pct")
    sigma = (atm_iv / 100.0) if atm_iv else 0.15

    T = dte / D.CALENDAR_DAYS
    parsed = [D.Leg(kind=l["kind"], strike=float(l.get("strike") or 0.0),
                    quantity=int(l["quantity"]),
                    premium=float(l.get("premium") or 0.0), lot_size=lot)
              for l in legs]

    greeks = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    for L in parsed:
        mult = L.quantity * L.lot_size
        if L.kind in ("call", "put"):
            g = D.black76(spot, L.strike, T, risk_free, sigma, L.kind)
            if g:
                greeks["delta"] += mult * g.delta
                greeks["gamma"] += mult * g.gamma
                greeks["vega"] += mult * g.vega
                greeks["theta"] += mult * g.theta
        else:
            greeks["delta"] += mult

    margin = D.margin_estimate(parsed, spot, T, sigma, risk_free)
    notional = margin.get("position_notional", 0.0)
    lo, hi = spot * 0.85, spot * 1.15
    return {
        "available": True, "symbol": symbol.upper(), "trade_date": td.isoformat(),
        "underlying": spot, "lot_size": lot, "days_to_expiry": dte,
        "implied_vol_used_pct": round(sigma * 100, 2),
        "iv_source": "ATM from chain" if atm_iv else "fallback 15% (no solvable ATM IV)",
        "net_greeks": {k: round(v, 4) for k, v in greeks.items()},
        "greeks_note": ("Position greeks are in UNITS OF THE UNDERLYING per lot-set: "
                        "delta is share-equivalent exposure, theta is rupees per "
                        "calendar day, vega is rupees per volatility point."),
        "payoff": D.payoff_curve(parsed, lo, hi),
        "margin": margin,
        "leverage": D.fno_reality_check(notional, margin.get("total_margin_estimate", 0.0),
                                        account_equity),
    }


# ------------------------------------------------------------ flow / delivery

def delivery_profile(session: Session, ticker: str, lookback: int = 60) -> dict:
    """Delivery percentage and its percentile within the stock's own history.

    Delivery % separates real accumulation from intraday churn: two stocks can
    print identical volume while one changed hands for keeps and the other was
    round-tripped by day traders. `engine/novel.py`'s crowding proxy explicitly
    documents this as unavailable from free sources — it is published daily by
    the exchange in the MTO file.
    """
    rows = session.execute(
        select(DeliveryStat.trade_date, DeliveryStat.delivery_pct,
               DeliveryStat.traded_qty)
        .where(DeliveryStat.symbol == ticker.upper())
        .order_by(DeliveryStat.trade_date.desc()).limit(lookback)).all()
    if not rows:
        return {"available": False, "ticker": ticker.upper(),
                "reason": "no delivery data ingested for this symbol"}
    series = [(d, float(p), float(q or 0)) for d, p, q in rows][::-1]
    latest = series[-1]
    pcts = [p for _d, p, _q in series]
    pctile = sum(1 for p in pcts if p <= latest[1]) / len(pcts) * 100
    mean = sum(pcts) / len(pcts)
    return {
        "available": True, "ticker": ticker.upper(),
        "as_of": latest[0].isoformat(),
        "delivery_pct": round(latest[1], 2),
        "mean_delivery_pct": round(mean, 2),
        "percentile_vs_own_history": round(pctile, 1),
        "observations": len(series),
        "history": [{"date": d.isoformat(), "delivery_pct": round(p, 2)}
                    for d, p, _q in series[-30:]],
        "reading": ("delivery well above its own norm — buying is being taken to "
                    "the demat account, not round-tripped"
                    if pctile >= 75 else
                    "delivery well below its own norm — volume is dominated by "
                    "intraday churn, so it is weak evidence of accumulation"
                    if pctile <= 25 else
                    "delivery in line with this stock's own norm"),
        "caveat": ("Delivery % is a flow descriptor, not a validated signal. It is "
                   "measured here so it can be tested, not asserted — no hypothesis "
                   "in the registry has yet validated it."),
    }


# --------------------------------------------------------- valuation regime

def market_valuation(session: Session, index_name: str = "Nifty 50",
                     lookback_days: int = 750) -> dict:
    """Index P/E, P/B and dividend yield against the index's OWN history.

    The single-stock engine has asked "is this expensive versus its own history"
    since day one; the market-level version was impossible without index
    fundamentals. NSE publishes them daily, free.
    """
    rows = session.execute(
        select(IndexObservation.obs_date, IndexObservation.close,
               IndexObservation.pe, IndexObservation.pb, IndexObservation.div_yield)
        .where(IndexObservation.index_name == index_name,
               IndexObservation.pe.isnot(None))
        .order_by(IndexObservation.obs_date.desc()).limit(lookback_days)).all()
    if not rows:
        return {"available": False, "index": index_name,
                "reason": "no index valuation history ingested"}
    series = rows[::-1]
    pes = [float(r[2]) for r in series]
    now = series[-1]

    def pctile(vals, v):
        return round(sum(1 for x in vals if x <= v) / len(vals) * 100, 1)

    out = {
        "available": True, "index": index_name, "as_of": now[0].isoformat(),
        "close": float(now[1]), "pe": float(now[2]),
        "pb": None if now[3] is None else float(now[3]),
        "div_yield": None if now[4] is None else float(now[4]),
        "pe_percentile_vs_own_history": pctile(pes, float(now[2])),
        "pe_history": {"min": round(min(pes), 2), "median": round(sorted(pes)[len(pes) // 2], 2),
                       "max": round(max(pes), 2)},
        "observations": len(series),
        "source": "NSE published index close file (exchange's own P/E)",
    }
    p = out["pe_percentile_vs_own_history"]
    out["reading"] = (
        f"{index_name} trades at {out['pe']:.1f}x, the {p:.0f}th percentile of its "
        f"last {len(series)} observations. "
        + ("Rich versus its own history — forward returns start from a lower base, "
           "which is a conditioning fact, not a timing signal."
           if p >= 75 else
           "Cheap versus its own history."
           if p <= 25 else
           "Unremarkable versus its own history."))
    out["caveat"] = ("Index P/E is trailing and its composition changes with index "
                     "reshuffles; a level shift can reflect a changed index rather "
                     "than a re-rating. Descriptive, never a timing signal.")
    return out


def valuation_spread(session: Session) -> dict:
    """Large vs mid vs small P/E spread — where the froth actually is.

    A single index P/E hides the most decision-relevant fact in Indian equities:
    broad-market valuation is usually driven by the small/mid segment, and the
    SPREAD between segments is far more informative than any one level.
    """
    out = {}
    for key, name in (("large", "Nifty 50"), ("midcap", "Nifty Midcap 150"),
                      ("smallcap", "Nifty Smallcap 250")):
        v = market_valuation(session, name)
        if v.get("available"):
            out[key] = {"index": name, "pe": v["pe"],
                        "pe_percentile": v["pe_percentile_vs_own_history"]}
    if "large" in out and "smallcap" in out:
        out["smallcap_premium_x"] = round(out["smallcap"]["pe"] / out["large"]["pe"], 2)
        out["reading"] = (
            f"Smallcaps trade at {out['smallcap_premium_x']}x the large-cap multiple. "
            + ("A premium this wide has historically coincided with the late stage of "
               "a retail-led broadening; treat small-cap signals with extra scepticism."
               if out["smallcap_premium_x"] > 1.25 else
               "Segment multiples are not unusually dispersed."))
    return out if out else {"available": False, "reason": "no index valuation history"}


# ------------------------------------------------------------ portfolio risk

def portfolio_simulation(session: Session, horizon_days: int = 21,
                         n_paths: int = 20_000) -> dict:
    """Monte Carlo VaR / CVaR / drawdown risk on the ACTUAL book.

    Falls back to the live universe as an equal-weight proxy when no positions
    exist, and says which it did — a risk number computed on a portfolio you do
    not hold is context, not your risk.
    """
    from .live import portfolio_state

    book = portfolio_state(session)
    weights: dict[str, float] = {}
    basis = "actual book"
    if book["has_book"]:
        for cid, w in book["weights"].items():
            t = session.scalar(select(Company.ticker).where(Company.id == cid))
            if t:
                weights[t] = w
    if not weights:
        basis = "equal-weight proxy over the live universe (no open positions)"
        tickers = session.scalars(
            select(Company.ticker).where(Company.is_index_member == True)  # noqa: E712
            .limit(15)).all()
        weights = {t: 1.0 for t in tickers}
    if not weights:
        return {"available": False, "reason": "no positions and no universe"}

    returns: dict[str, list[float]] = {}
    for t in list(weights):
        cid = session.scalar(select(Company.id).where(Company.ticker == t))
        if cid is None:
            continue
        closes = [r[0] for r in session.execute(
            select(PriceObservation.close)
            .where(PriceObservation.company_id == cid)
            .order_by(PriceObservation.obs_date)).all()]
        if len(closes) < 260:
            continue
        closes = closes[-1000:]
        returns[t] = [closes[i] / closes[i - 1] - 1
                      for i in range(1, len(closes)) if closes[i - 1]]
    weights = {t: w for t, w in weights.items() if t in returns}
    if not weights:
        return {"available": False,
                "reason": "no holding has the ≥260 sessions of history required"}

    risk = MC.simulate_portfolio_risk(returns, weights, horizon_days=horizon_days,
                                      n_paths=n_paths)
    if not risk.get("computable"):
        return {"available": False, "reason": risk.get("reason")}

    # path risk on the blended book
    ws = {t: weights[t] for t in returns}
    total = sum(ws.values())
    n = min(len(v) for v in returns.values())
    blended = [sum(ws[t] / total * returns[t][-n:][i] for t in ws) for i in range(n)]
    dd = MC.simulate_drawdown_risk(blended, horizon_days=252,
                                   n_paths=max(2000, n_paths // 8))
    return {"available": True, "basis": basis, "risk": risk,
            "drawdown": dd if dd.get("computable") else None,
            "book_value": round(book.get("book_value", 0.0), 2)}
