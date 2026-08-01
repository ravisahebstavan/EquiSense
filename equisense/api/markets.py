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
from .snapshot import get_universe

DEFAULT_RISK_FREE = 0.065


def latest_derivative_date(session: Session,
                           symbol: Optional[str] = None) -> Optional[date]:
    q = select(func.max(DerivativeQuote.trade_date))
    if symbol:
        q = q.where(DerivativeQuote.symbol == symbol.upper())
    return session.scalar(q)


# ------------------------------------------------------------- derivatives

def derivative_snapshot(session: Session, symbol: str = "NIFTY",
                        risk_free: float = DEFAULT_RISK_FREE,
                        live: bool = True) -> dict:
    """Full derivatives view for one underlying: futures curve + option chain.

    LIVE BY DEFAULT — nothing is persisted. An option chain is decision-relevant
    only while its expiry is running: no engine here studies historical open
    interest or a past IV surface, so storing 35k rows per day bought nothing and
    was the single largest consumer of a free-tier database (23 MB of 58 MB).
    The whole file is one HTTP request, so the honest design is to fetch it when
    asked and throw it away.

    Contrast with delivery % and index valuation, which ARE persisted: their
    value is the percentile-vs-own-history, the exchange publishes one file per
    day, and rebuilding a year of history live would mean ~250 requests.

    `live=False` falls back to stored rows, which is what tests and offline use
    want.
    """
    symbol = symbol.upper()
    if live:
        snap = _live_derivative_snapshot(symbol, risk_free)
        if snap.get("available"):
            return snap
    td = latest_derivative_date(session, symbol)
    if td is None:
        return {"available": False, "symbol": symbol,
                "reason": "F&O archive unreachable and nothing stored for this symbol",
                "hint": "check /api/markets/sources"}

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
    # Reuses the LIVE snapshot (and its cache) so a proposed position is priced
    # off the same chain the user is looking at, with nothing persisted.
    snap = derivative_snapshot(session, symbol, risk_free)
    if not snap.get("available"):
        return {"available": False, "reason": snap.get("reason", "no F&O data")}
    oc = snap.get("option_chain", {})
    td = date.fromisoformat(snap["trade_date"])
    spot = oc.get("underlying") or _underlying_price(session, symbol, td)
    lot = oc.get("lot_size") or 1
    dte = max(oc.get("days_to_expiry") or 7, 1)
    if not spot:
        return {"available": False, "reason": "no underlying reference price"}
    atm_iv = oc.get("atm_iv_pct")
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


# --------------------------------------------------- live (unstored) chains

_LIVE_CACHE: dict[str, tuple[float, dict]] = {}
LIVE_TTL_SECONDS = 900          # EOD archives change once a day; 15 min is ample


def _recent_trading_days(n: int = 5) -> list[date]:
    out, d = [], date.today()
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def _live_derivative_snapshot(symbol: str, risk_free: float) -> dict:
    """Fetch and analyse today's F&O file without storing a single row.

    Process-memory cached for LIVE_TTL_SECONDS: the underlying archive is an
    end-of-day file that changes once per trading day, so re-downloading 1.2 MB
    per page view would be pure waste and unkind to a free source.
    """
    import time as _t

    key = symbol.upper()
    hit = _LIVE_CACHE.get(key)
    if hit and (_t.time() - hit[0]) < LIVE_TTL_SECONDS:
        return hit[1]

    rows: list[dict] = []
    td: Optional[date] = None
    for d in _recent_trading_days(5):
        rows = [r for r in NA.fetch_fo_bhavcopy(d)
                if r["symbol"].upper() == key]
        if rows:
            td = d
            break
    if not rows or td is None:
        return {"available": False, "symbol": key,
                "reason": "no F&O rows for this symbol in the last 5 trading files"}

    futures = sorted((r["expiry"], r["settlement_price"] or r["close"])
                     for r in rows
                     if r["instrument_type"] in ("IDF", "STF")
                     and (r["settlement_price"] or r["close"]))
    spot = next((r["underlying_price"] for r in rows if r["underlying_price"]), None)
    out: dict = {"available": True, "symbol": key, "trade_date": td.isoformat(),
                 "source": "live NSE archive (not stored)"}
    out["term_structure"] = (D.term_structure(spot, futures, as_of=td) if futures and spot
                             else {"contracts": [], "reason": "no futures rows"})

    opts = [r for r in rows if r["instrument_type"] in ("IDO", "STO")
            and r["strike"] and r["option_type"]]
    live_expiries = sorted({r["expiry"] for r in opts if r["expiry"] > td})
    if not opts or not live_expiries:
        out["option_chain"] = {"computable": False,
                               "reason": "no options with a live expiry"}
    else:
        expiry = live_expiries[0]
        sel = [r for r in opts if r["expiry"] == expiry]
        lot = next((r["lot_size"] for r in sel if r["lot_size"]), 1)
        quotes = [D.OptionQuote(
            strike=r["strike"],
            kind="call" if r["option_type"] == "CE" else "put",
            # settlement price exists for untraded strikes where close is 0;
            # using close there would make IV unsolvable across the wings
            price=r["settlement_price"] or r["close"] or 0.0,
            open_interest=r["open_interest"] or 0.0,
            change_in_oi=r["change_in_oi"] or 0.0,
            volume=r["volume"] or 0.0) for r in sel]
        quotes = [q for q in quotes if q.price > 0]
        analytics = D.option_chain_analytics(
            quotes, spot or 0.0, (expiry - td).days, risk_free=risk_free,
            lot_size=lot or 1, period=td.isoformat())
        analytics["expiry"] = expiry.isoformat()
        analytics["expiries_available"] = [e.isoformat() for e in live_expiries]
        analytics["iv_surface"] = analytics.get("iv_surface", [])[:400]
        out["option_chain"] = analytics

    _LIVE_CACHE[key] = (_t.time(), out)
    return out


# ------------------------------------------------------------------- rates

_RATE_CACHE: dict = {"ts": 0.0, "value": None}
RATE_TTL_SECONDS = 3600


def market_rates(session: Session) -> dict:
    """Risk-free rate and ERP sanity check, DERIVED from free official data.

    Replaces a hardcoded 7.0% risk_free_rate that fed cost of equity -> WACC ->
    every reverse-DCF implied-growth figure the platform publishes. Two free
    exchange files are enough: the futures curve gives implied (r − q) and the
    index close file gives q.
    """
    import time as _t

    from ..engine import rates as R

    if _RATE_CACHE["value"] and (_t.time() - _RATE_CACHE["ts"]) < RATE_TTL_SECONDS:
        return _RATE_CACHE["value"]

    snap = derivative_snapshot(session, "NIFTY")
    mv = market_valuation(session, "Nifty 50")
    contracts = []
    spot = None
    if snap.get("available"):
        ts = snap.get("term_structure", {})
        spot = ts.get("spot")
        for c in ts.get("contracts", []):
            try:
                contracts.append((date.fromisoformat(c["expiry"]), c["futures"]))
            except Exception:                      # noqa: BLE001
                continue
    q = mv.get("div_yield") if mv.get("available") else None
    pe = mv.get("pe") if mv.get("available") else None

    if not contracts or not spot:
        out = {"available": False,
               "reason": "no futures curve available to imply a rate",
               "fallback_risk_free_pct": R.FALLBACK_RISK_FREE * 100}
        _RATE_CACHE.update(ts=_t.time(), value=out)
        return out

    rf = R.implied_risk_free_rate(spot, contracts, q,
                                  as_of=date.fromisoformat(snap["trade_date"]))
    erp = R.equity_risk_premium_from_earnings_yield(pe, rf.value)
    out = {
        "available": True,
        "as_of": snap["trade_date"],
        "risk_free": rf.to_dict(),
        "erp_check": erp.to_dict(),
        "effective_risk_free_pct": rf.value if rf.value is not None
        else R.FALLBACK_RISK_FREE * 100,
        "source": ("derived from the NSE futures curve and the exchange's own "
                   "index dividend yield — no hardcoded rate, no paid feed"),
    }
    _RATE_CACHE.update(ts=_t.time(), value=out)
    return out


def effective_risk_free(session: Session) -> tuple[float, str]:
    """(rate as a decimal, provenance). Never raises — valuation must still run
    when the rate cannot be derived, but it must SAY which rate it used."""
    from ..engine import rates as R
    try:
        r = market_rates(session)
        if r.get("available") and r.get("risk_free", {}).get("value") is not None:
            return r["risk_free"]["value"] / 100.0, "market-implied (futures basis + index dividend yield)"
    except Exception:                              # noqa: BLE001 - never block valuation
        pass
    return R.FALLBACK_RISK_FREE, f"stated fallback {R.FALLBACK_RISK_FREE:.2%} (could not derive)"


# ------------------------------------------------------- cross-asset relations

def relationships(session: Session, lookback: int = 900,
                  extra_tickers: Optional[list[str]] = None) -> dict:
    """Cross-asset relationship map: correlation, stress-conditional
    correlation, and the assets that stop diversifying when it matters.

    Series are intersected on COMMON DATES. Equity bars and macro bars come from
    different providers with different holiday calendars, so tail-aligning by
    position pairs one day's rupee move with another day's equity move — that
    bug put RELIANCE-vs-NIFTY at +0.05 instead of +0.69.
    """
    from ..engine import crossasset as CA
    from ..models import MacroObservation

    # Batched, not per-symbol. This ran one query per macro series and TWO per
    # held ticker (an id lookup, then its prices) — 19 round trips for a single
    # page. Over a network database each round trip is a few hundred
    # milliseconds, so the query COUNT, not the data volume, was the latency.
    MACRO = {"NIFTY": "^NSEI", "USDINR": "INR=X", "BRENT": "BZ=F",
             "GOLD": "GC=F", "SP500": "^GSPC"}
    macro_rows: dict[str, list] = {}
    for sym, d, c in session.execute(
            select(MacroObservation.symbol, MacroObservation.obs_date,
                   MacroObservation.close)
            .where(MacroObservation.symbol.in_(list(MACRO.values())))
            .order_by(MacroObservation.symbol,
                      MacroObservation.obs_date.desc())).all():
        bucket = macro_rows.setdefault(sym, [])
        if len(bucket) < lookback:
            bucket.append((d, c))
    dated = {label: CA.returns_from_dated_closes(macro_rows.get(sym, []))
             for label, sym in MACRO.items()}

    def equity_returns_batch(tickers: list[str]) -> dict[str, dict]:
        if not tickers:
            return {}
        ids = dict(session.execute(
            select(Company.ticker, Company.id)
            .where(Company.ticker.in_(tickers))).all())
        if not ids:
            return {}
        by_cid: dict[int, list] = {}
        for cid, d, c in session.execute(
                select(PriceObservation.company_id, PriceObservation.obs_date,
                       PriceObservation.close)
                .where(PriceObservation.company_id.in_(list(ids.values())))
                .order_by(PriceObservation.company_id,
                          PriceObservation.obs_date.desc())).all():
            bucket = by_cid.setdefault(cid, [])
            if len(bucket) < lookback:
                bucket.append((d, c))
        return {t: CA.returns_from_dated_closes(by_cid.get(cid, []))
                for t, cid in ids.items()}
    held = [t for t in (extra_tickers or []) if t]
    if not held:
        from .live import portfolio_state
        book = portfolio_state(session)
        wids = list(book.get("weights") or {})
        if wids:
            held.extend(t for (t,) in session.execute(
                select(Company.ticker).where(Company.id.in_(wids))).all())
    if not held:
        # No book: fall back to the most LIQUID current constituents rather than
        # an arbitrary slice. `limit(N)` without an order returns whatever the
        # planner emits first, which on a database still carrying departed
        # members surfaced exactly those stale names.
        from .snapshot import get_universe
        try:
            snap = get_universe(session)["companies"]
            held = [c["ticker"] for c in
                    sorted(snap, key=lambda c: -(c.get("adv_cr") or 0))[:6]]
        except Exception:                          # noqa: BLE001
            held = list(session.scalars(
                select(Company.ticker)
                .where(Company.is_index_member == True)
                .order_by(Company.ticker).limit(6)).all())  # noqa: E712
    eq = equity_returns_batch(held[:12])
    for t in held[:12]:
        r = eq.get(t) or {}
        if r:
            dated[t] = r

    dated = {k: v for k, v in dated.items() if len(v) >= CA.MIN_OBS}
    if "NIFTY" not in dated or len(dated) < 3:
        return {"available": False,
                "reason": "need the NIFTY series and at least two other assets"}
    series = CA.align_on_dates(dated)
    n_common = len(next(iter(series.values()))) if series else 0
    if n_common < CA.MIN_OBS:
        return {"available": False,
                "reason": f"only {n_common} common dates across the requested series"}
    out = CA.relationship_matrix(series, "NIFTY")
    out["available"] = out.get("computable", False)
    out["common_dates"] = n_common
    out["assets_included"] = list(series)
    return out


# ------------------------------------------------------- institutional flow

_DEALS_CACHE: dict = {"ts": 0.0, "rows": None}
DEALS_TTL_SECONDS = 1800


def _todays_deals() -> list[dict]:
    """Bulk + block deals, fetched live and NOT stored — they are a same-day
    disclosure file and nothing here studies their history yet."""
    import time as _t
    if _DEALS_CACHE["rows"] is not None and (_t.time() - _DEALS_CACHE["ts"]) < DEALS_TTL_SECONDS:
        return _DEALS_CACHE["rows"]
    rows = NA.fetch_bulk_deals() + NA.fetch_block_deals()
    _DEALS_CACHE.update(ts=_t.time(), rows=rows)
    return rows


def institutional_flow(session: Session, ticker: Optional[str] = None) -> dict:
    """Net disclosed institutional activity from NSE bulk/block deals.

    Reports NET direction scaled by liquidity, never gross activity: bulk deals
    are frequently one fund selling to another, so the same rupees appear on
    both sides. Verified live — the four largest names by gross value all had a
    net-to-gross ratio within 0.02 of zero.
    """
    from ..engine.novel import institutional_flow as flow_metric

    deals = _todays_deals()
    if not deals:
        return {"available": False,
                "reason": "no bulk/block deal file available (holiday, or not yet published)"}
    universe = {}
    try:
        universe = {c["ticker"]: c for c in get_universe(session)["companies"]}
    except Exception:                              # noqa: BLE001
        pass

    by_symbol: dict[str, list[dict]] = {}
    for d in deals:
        by_symbol.setdefault(d["symbol"], []).append(d)

    if ticker:
        t = ticker.upper()
        adv = (universe.get(t) or {}).get("adv_cr")
        m = flow_metric(by_symbol.get(t, []), adv_cr=adv,
                        period=str(deals[0]["trade_date"]))
        return {"available": True, "ticker": t, "trade_date": str(deals[0]["trade_date"]),
                "flow": m.to_dict()}

    ranked = []
    for sym, ds in by_symbol.items():
        adv = (universe.get(sym) or {}).get("adv_cr")
        m = flow_metric(ds, adv_cr=adv, period=str(deals[0]["trade_date"]))
        i = m.inputs
        ranked.append({"symbol": sym, "in_universe": sym in universe,
                       "deals": i["deals"], "net_value_cr": i["net_value_cr"],
                       "gross_value_cr": i["gross_value_cr"],
                       "net_to_gross_ratio": i["net_to_gross_ratio"],
                       "days_of_adv": m.value,
                       "counterparties": i["distinct_counterparties"]})
    directional = [r for r in ranked
                   if r["net_to_gross_ratio"] is not None
                   and abs(r["net_to_gross_ratio"]) >= 0.5]
    return {
        "available": True,
        "trade_date": str(deals[0]["trade_date"]),
        "total_deals": len(deals), "symbols": len(by_symbol),
        "by_net_value": sorted(ranked, key=lambda r: -abs(r["net_value_cr"]))[:15],
        "directional": sorted(directional, key=lambda r: -abs(r["net_value_cr"]))[:10],
        "note": ("`directional` keeps only names where NET is at least half of "
                 "GROSS. Everything else is funds crossing stock with each other: "
                 "large gross value with no net is the common case and carries no "
                 "directional claim, which is the single easiest way to misread "
                 "this file."),
    }


# ------------------------------------------------ volatility surface capture

def capture_vol_surface(session: Session,
                        symbols: Optional[list[str]] = None) -> dict:
    """Persist today's option-surface SUMMARY for each underlying.

    Six numbers per expiry, not the 35,000-row chain. The chain itself stays
    unstored because nothing studies historical open interest — but an
    implied-volatility TIME SERIES is a different asset entirely: it is the only
    route to measuring the variance risk premium, and NSE publishes one file per
    day, so it cannot be backfilled. Capture has to begin before the study can
    ever exist.

    Idempotent per (date, symbol, expiry).
    """
    from ..models import VolSurfaceObservation

    syms = symbols or ["NIFTY", "BANKNIFTY"]
    written, skipped = 0, []
    for sym in syms:
        snap = derivative_snapshot(session, sym)
        if not snap.get("available"):
            skipped.append({"symbol": sym, "reason": snap.get("reason", "unavailable")})
            continue
        oc = snap.get("option_chain") or {}
        if not oc.get("computable"):
            skipped.append({"symbol": sym, "reason": oc.get("reason", "no chain")})
            continue
        try:
            obs_date = date.fromisoformat(snap["trade_date"])
            expiry = date.fromisoformat(oc["expiry"])
        except Exception:                          # noqa: BLE001
            skipped.append({"symbol": sym, "reason": "unparseable dates"})
            continue
        existing = session.scalars(
            select(VolSurfaceObservation).where(
                VolSurfaceObservation.obs_date == obs_date,
                VolSurfaceObservation.symbol == sym.upper(),
                VolSurfaceObservation.expiry == expiry)).first()
        row = existing or VolSurfaceObservation(
            obs_date=obs_date, symbol=sym.upper(), expiry=expiry)
        row.days_to_expiry = oc.get("days_to_expiry") or 0
        row.underlying = oc.get("underlying")
        row.atm_iv_pct = oc.get("atm_iv_pct")
        row.skew_25d_pct = oc.get("skew_25d_risk_reversal_pct")
        row.put_call_ratio_oi = oc.get("put_call_ratio_oi")
        row.total_oi = (oc.get("total_call_oi") or 0) + (oc.get("total_put_oi") or 0)
        row.iv_points_solved = oc.get("iv_points_solved")
        if existing is None:
            session.add(row)
        written += 1
    session.commit()
    return {"captured": written, "skipped": skipped,
            "note": ("Six floats per underlying per expiry. The chain itself is "
                     "never stored; this series exists solely to make the "
                     "variance risk premium measurable, which requires history "
                     "that cannot be backfilled.")}


def variance_premium(session: Session, symbol: str = "NIFTY",
                     horizon_days: int = 21) -> dict:
    """Variance risk premium from the accumulated IV series versus realised vol.

    Returns an explicit not-yet-testable payload rather than a number when the
    series is too short. That state is the NORMAL one for a while: the IV series
    starts the day capture begins and cannot be backfilled, so this needs about
    two months of daily capture before it says anything.
    """
    from ..engine.derivatives import variance_risk_premium
    from ..models import VolSurfaceObservation

    sym = symbol.upper()
    iv_rows = session.execute(
        select(VolSurfaceObservation.obs_date, VolSurfaceObservation.atm_iv_pct)
        .where(VolSurfaceObservation.symbol == sym,
               VolSurfaceObservation.atm_iv_pct.isnot(None))
        .order_by(VolSurfaceObservation.obs_date)).all()
    # one IV per date: the nearest live expiry, which is what capture stores
    iv_by_date: dict = {}
    for d, iv in iv_rows:
        iv_by_date.setdefault(d, float(iv))
    iv_series = sorted(iv_by_date.items())

    closes: list[tuple] = []
    cid = session.scalar(select(Company.id).where(Company.ticker == sym))
    if cid is not None:
        closes = [(d, float(c)) for d, c in session.execute(
            select(PriceObservation.obs_date, PriceObservation.close_raw)
            .where(PriceObservation.company_id == cid,
                   PriceObservation.close_raw.isnot(None))
            .order_by(PriceObservation.obs_date)).all()]
    if not closes:
        # Prefer the MACRO series for indices: ^NSEI carries ~10 years while the
        # NSE index-close table only starts when archive capture began. Realised
        # volatility needs depth, so falling back to the 9-day table first would
        # have made the premium untestable for years rather than months.
        from ..models import IndexObservation, MacroObservation
        macro_sym = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}.get(sym)
        if macro_sym:
            closes = [(d, float(c)) for d, c in session.execute(
                select(MacroObservation.obs_date, MacroObservation.close)
                .where(MacroObservation.symbol == macro_sym)
                .order_by(MacroObservation.obs_date)).all()]
        if not closes:
            name = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank"}.get(sym)
            if name:
                closes = [(d, float(c)) for d, c in session.execute(
                    select(IndexObservation.obs_date, IndexObservation.close)
                    .where(IndexObservation.index_name == name)
                    .order_by(IndexObservation.obs_date)).all()]

    out = variance_risk_premium(iv_series, closes, horizon_days)
    out["symbol"] = sym
    out["iv_observations_stored"] = len(iv_series)
    out["close_observations"] = len(closes)
    if not out.get("computable"):
        out["hypothesis"] = "HYP-015 (registered-deferred until the series matures)"
    return out
