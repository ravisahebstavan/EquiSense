"""Live dossier assembly (PHASE2 §5, §9, §11).

Phase II discipline applied:
- Evidence strengths are cross-sectional percentiles (§5.1) — no hand scales.
- Admission caps enforced in the evidence layer (§5.2) — deferred hypotheses
  (CCS, Fragility) render as SHADOW, influencing nothing.
- Portfolio-fit evidence evaluates every candidate against the actual book
  (A11), and sizing consumes real portfolio heat (A12).
Orchestration only — all math lives in the engines.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ledger
from ..engine import novel, quality, ratios, technical, valuation
from ..engine.evidence import Evidence, ev, xsec_strength
from ..engine.portfolio import positions_from_ledger
from ..engine.regime import classify_regime
from ..engine.sizing import SizingInputs, STOP_ATR_MULT, cost_tax_breakeven, recommend_size
from ..engine.synthesis import synthesize
from ..models import Company, MacroObservation, PriceObservation, TransactionRow
from ..research.base_rates import get_base_rate
from . import services


def _series(session: Session, cid: int):
    """(dates, total_return_closes, volumes) — the return-computation basis."""
    rows = session.execute(
        select(PriceObservation.obs_date, PriceObservation.close, PriceObservation.volume)
        .where(PriceObservation.company_id == cid)
        .order_by(PriceObservation.obs_date)).all()
    return ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows])


def _nominal_closes(session: Session, cid: int) -> list[float] | None:
    """Split-adjusted-only closes, for anything that divides a price by a
    per-share accounting figure. None when the column was never backfilled, so
    consumers can say so rather than silently using the wrong convention."""
    rows = session.execute(
        select(PriceObservation.close_raw)
        .where(PriceObservation.company_id == cid)
        .order_by(PriceObservation.obs_date)).all()
    vals = [r[0] for r in rows]
    if not vals or any(v is None for v in vals):
        return None
    return vals


def _macro(session: Session, symbol: str, limit: int | None = None) -> list[float]:
    q = (select(MacroObservation.close)
         .where(MacroObservation.symbol == symbol)
         .order_by(MacroObservation.obs_date.desc()))
    if limit:
        q = q.limit(limit)
    rows = [r[0] for r in session.execute(q).all()]
    return rows[::-1]


def current_regime(session: Session) -> dict:
    # regime needs ≤3y of history (VIX percentile window) — don't drag 10y
    # over the network per dossier
    L = 800
    return classify_regime(_macro(session, "^NSEI", L), _macro(session, "^INDIAVIX", L),
                           _macro(session, "INR=X", L), _macro(session, "BZ=F", L),
                           as_of=date.today().isoformat())


# ------------------------------------------------- cross-sectional signals

_SIG_CACHE: dict = {"key": None, "signals": None}

SIGNAL_KEYS = ["momentum", "dist_52w", "trend", "rel_strength", "mqi", "vol",
               "heat", "f_score", "z_score", "ccs", "fragility", "exp_gap",
               "pe_pctile", "sector_rel_mom", "max_effect"]


def universe_signals(session: Session) -> dict[str, dict[str, Optional[float]]]:
    """Raw signal values for every company — the reference distribution that
    §5.1 percentile normalization ranks against. Served from the universe
    snapshot (one row) instead of a 50-company recompute."""
    from .snapshot import get_universe
    universe = get_universe(session)
    key = universe.get("as_of")
    if _SIG_CACHE["key"] == key:
        return _SIG_CACHE["signals"]
    out: dict[str, dict[str, Optional[float]]] = {k: {} for k in SIGNAL_KEYS}
    for item in universe["companies"]:
        t = item["ticker"]
        for k in SIGNAL_KEYS:
            out[k][t] = item["signals"].get(k)
    _SIG_CACHE.update(key=key, signals=out)
    return out


# ------------------------------------------------------------ portfolio fit

def portfolio_state(session: Session) -> dict:
    """Current book from the transaction ledger: values, weights, daily return
    series (63d), and real portfolio heat (A12)."""
    txns = session.scalars(select(TransactionRow)).all()
    from ..engine.portfolio import Transaction
    positions = positions_from_ledger([
        Transaction(r.company_id, r.side, r.quantity, r.price, r.trade_date, r.fees)
        for r in txns])
    holdings = {cid: p for cid, p in positions.items() if p.quantity > 1e-9}
    if not holdings:
        return {"has_book": False, "book_value": 0.0, "open_heat_pct": 0.0,
                "book_returns": [], "weights": {}}
    values, rets, vols = {}, {}, {}
    for cid, p in holdings.items():
        _, closes, _v = _series(session, cid)
        if len(closes) < 70:
            continue
        values[cid] = p.quantity * closes[-1]
        rets[cid] = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 63, len(closes))]
        daily_vol = technical.realized_vol(closes).value
        vols[cid] = (daily_vol / math.sqrt(252)) if daily_vol else None
    book = sum(values.values())
    if book <= 0:
        return {"has_book": False, "book_value": 0.0, "open_heat_pct": 0.0,
                "book_returns": [], "weights": {}}
    weights = {cid: v / book for cid, v in values.items()}
    book_returns = [sum(weights[cid] * rets[cid][i] for cid in rets)
                    for i in range(63)] if rets else []
    heat = sum(values[cid] * STOP_ATR_MULT * (vols[cid] or 0) / 100
               for cid in values) / book * 100
    return {"has_book": True, "book_value": book, "weights": weights,
            "book_returns": book_returns, "open_heat_pct": round(heat, 2)}


def _corr(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) != len(b) or len(a) < 20:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


# ------------------------------------------------------------------ evidence

def build_evidence(session: Session, company: Company, regime_key: str,
                   book: dict) -> tuple[list[Evidence], dict]:
    dates, closes, volumes = _series(session, company.id)
    stmts = services.latest_statements(session, company.id)
    price = closes[-1] if closes else None
    period = dates[-1].isoformat() if dates else ""
    sig = universe_signals(session)
    t = company.ticker
    E: list[Evidence] = []

    def S(key, invert=False):
        return xsec_strength(sig[key], t, invert=invert)

    # ---- trend cluster ----
    mom = technical.momentum_12_1(closes, period)
    E.append(ev("technical", "technical.trend", "trend", S("momentum"),
                f"12-1 momentum {mom.value:+.1f}% (cross-sectional percentile strength)"
                if mom.value is not None else "", [mom],
                base_rate=get_base_rate(session, "momentum_12_1_top_quintile", 126, regime_key)))
    hi = technical.pct_from_52w_high(closes, period)
    E.append(ev("technical", "technical.trend", "trend", S("dist_52w"),
                f"{hi.value:+.1f}% from 52w high" if hi.value is not None else "", [hi],
                base_rate=get_base_rate(session, "near_52w_high", 126, regime_key)
                if (hi.value or -99) > -5 else None))
    tr = technical.trend_200dma(closes, period)
    E.append(ev("technical", "technical.trend", "trend", S("trend"),
                f"price {tr.value:+.1f}% vs 200DMA" if tr.value is not None else "", [tr],
                base_rate=get_base_rate(session, "above_200dma", 126, regime_key)))
    rs = technical.relative_strength(closes, _macro(session, "^NSEI", 300), period=period)
    E.append(ev("technical", "technical.trend", "trend", S("rel_strength"),
                f"relative strength vs NIFTY {rs.value:+.1f}% (63d)"
                if rs.value is not None else "", [rs]))
    mqi = novel.momentum_quality(closes, period)
    E.append(ev("novel", "novel.mqi", "trend", S("mqi"),
                f"Momentum Quality Index {mqi.value:+.2f}" if mqi.value is not None else "",
                [mqi], base_rate=get_base_rate(session, "momentum_quality_top_quintile",
                                               126, regime_key)))
    srm = sig["sector_rel_mom"].get(t)
    if srm is not None:
        from ..engine.types import Metric
        srm_m = Metric(key="sector_rel_momentum", label="63d Return vs Own Sector",
                       value=srm, unit="pp",
                       formula=f"Company 63d return minus {company.sector} sector's "
                               f"mean 63d return",
                       inputs={"sector": company.sector}, period=period, family="technical",
                       caveat="Moskowitz & Grinblatt (1999), J. Finance — distinct from, "
                              "not a restatement of, NIFTY-relative momentum above.")
        E.append(ev("technical", "technical.sector_momentum", "trend", S("sector_rel_mom"),
                    f"{srm:+.1f}pp vs {company.sector} sector (63d)", [srm_m],
                    base_rate=get_base_rate(session, "sector_relative_momentum_top_quintile",
                                            126, regime_key)))

    # ---- value cluster ----
    pe_pct = novel.pe_percentile_vs_history(
        closes, dates, stmts, period,
        nominal_closes=_nominal_closes(session, company.id))
    E.append(ev("novel", "novel.value", "value", S("pe_pctile", invert=True),
                f"P/E at {pe_pct.value:.0f}th percentile of own history"
                if pe_pct.value is not None else "", [pe_pct]))
    if stmts and price and not company.is_financial:
        # Beta is ESTIMATED from this name's own history against NIFTY and shrunk
        # toward 1.0 (Vasicek), instead of the previous hardcoded 1.0. Beta drives
        # Ke → WACC → the entire reverse-DCF solve, so assuming 1.0 for every
        # company injected a large, one-directional error into the headline
        # valuation output.
        beta_m = valuation.estimate_beta(closes, _macro(session, "^NSEI", 600), period=period)
        wacc_a = valuation.WaccAssumptions()
        if beta_m.value is not None:
            wacc_a.beta = beta_m.value
        rd = valuation.reverse_dcf(
            stmts[-1], price,
            valuation.ReverseDcfAssumptions(wacc=wacc_a),
            statements=stmts)
        hist = valuation.historical_fcf_cagr(stmts)
        if rd["implied_growth"].value is not None and hist and hist.value is not None:
            gap = rd["implied_growth"].value - hist.value
            metrics = [rd["implied_growth"], hist, rd["wacc"]]
            if beta_m.value is not None:
                metrics.append(beta_m)
            E.append(ev("valuation", "valuation.expectations", "value",
                        S("exp_gap", invert=True),
                        f"Expectations Gap {gap:+.1f}pp (implied {rd['implied_growth'].value:.1f}% "
                        f"vs delivered {hist.value:.1f}%)",
                        metrics,
                        caveats=[c for c in (rd["implied_growth"].caveat,
                                             hist.caveat, beta_m.caveat) if c]))

    # ---- quality cluster (F/Z exploratory-capped; CCS/Fragility SHADOW) ----
    if stmts and not company.is_financial:
        curr, prev = stmts[-1], (stmts[-2] if len(stmts) >= 2 else None)
        if prev:
            f = quality.piotroski_f(curr, prev, price)
            if f.value is not None:
                n_avail = int(f.inputs.get("signals_available", 0))
                E.append(ev("quality", "quality.fscore", "quality", S("f_score"),
                            f"Piotroski F {f.value:.1f}/9"
                            + ("" if n_avail >= 9 else f" ({n_avail}/9 signals disclosed)"),
                            [f], caveats=[f.caveat] if f.caveat else None))
        # Both distress models are emitted: Z''-EM is the calibration-appropriate
        # one here and is price-invariant, while the 1968 Z is retained for
        # comparability. Only the EM variant carries evidence weight, so a share
        # price fall cannot circularly become "distress evidence" about itself.
        z = quality.altman_z(curr, price)
        z_em = quality.altman_z_em(curr)
        if z_em.value is not None:
            E.append(ev("quality", "quality.zscore", "quality", S("z_score"),
                        f"Altman Z''-EM {z_em.value:.2f} "
                        f"({quality.altman_zone_em(z_em.value)})",
                        [z_em] + ([z] if z.value is not None else []),
                        caveats=[c for c in (z_em.caveat,) if c]))
        elif z.value is not None:
            E.append(ev("quality", "quality.zscore", "quality", S("z_score"),
                        f"Altman Z {z.value:.2f} ({quality.altman_zone(z.value)})",
                        [z], caveats=[z.caveat or ""]))
        ccs = novel.cash_conviction(stmts, period)
        if ccs.value is not None:
            E.append(ev("novel", "novel.ccs", "quality", S("ccs"),
                        f"Cash Conviction Score {ccs.value:.0f}/100", [ccs]))
        frag = novel.fragility(stmts, closes, period)
        if frag.value is not None:
            E.append(ev("novel", "novel.fragility", "risk", S("fragility", invert=True),
                        f"Fragility Index {frag.value:.0f}/100", [frag]))

    # ---- flow cluster ----
    heat = novel.crowding_proxy(closes, volumes, period)
    if heat.value is not None:
        E.append(ev("novel", "novel.crowding", "flow", S("heat", invert=True),
                    f"Participation Heat {heat.value:.2f}", [heat],
                    base_rate=get_base_rate(session, "participation_heat_top_decile",
                                            21, regime_key) if heat.value > 4 else None))
    max5 = sig["max_effect"].get(t)
    if max5 is not None:
        from ..engine.types import Metric
        max_m = Metric(key="max_effect", label="Lottery-Demand (MAX) Score",
                       value=max5, unit="score",
                       formula="Negated mean of the 5 highest daily returns in the "
                               "trailing 21 sessions — higher score = lower recent "
                               "single-day extremity",
                       inputs={}, period=period, family="behavioral",
                       caveat="Bali, Cakici & Whitelaw (2011), J. Financial Economics — "
                              "extreme recent daily upside attracts lottery-seeking "
                              "demand and has historically preceded underperformance.")
        E.append(ev("behavioral", "behavioral.max_effect", "flow", S("max_effect"),
                    f"MAX-effect score {max5:+.2f} (low recent single-day extremity)",
                    [max_m], base_rate=get_base_rate(session, "low_max_effect_top_quintile",
                                                     63, regime_key)))

    # ---- risk cluster ----
    vol = technical.realized_vol(closes, period=period)
    dd = technical.max_drawdown(closes[-252:], period)
    if vol.value is not None:
        E.append(ev("risk", "risk.volatility", "risk", S("vol", invert=True),
                    f"realized vol {vol.value:.1f}% ann.; 1y max DD {dd.value:.1f}%",
                    [vol, dd]))

    # ---- portfolio-fit cluster (A11 fix) ----
    if book["has_book"] and len(closes) >= 70:
        cand_rets = [closes[i] / closes[i - 1] - 1
                     for i in range(len(closes) - 63, len(closes))]
        corr = _corr(cand_rets, book["book_returns"])
        if corr is not None:
            from ..engine.types import Metric
            m = Metric(key="book_correlation", label="Correlation to Current Book (63d)",
                       value=corr, unit="ρ",
                       formula="Pearson corr of candidate daily returns vs "
                               "value-weighted book returns, 63 trading days",
                       inputs={"days": 63, "book_value": round(book["book_value"], 0),
                               "open_heat_pct": book["open_heat_pct"]},
                       period=period, family="portfolio",
                       caveat="Correlations are regime-dependent and estimated with "
                              "error; treat as context, not a constant.")
            E.append(ev("portfolio", "portfolio.fit", "portfolio", -corr,
                        f"63d correlation to current book: {corr:+.2f} "
                        f"({'adds little diversification' if corr > 0.6 else 'diversifying'})",
                        [m]))

    context = {"price": price, "as_of": period,
               "daily_vol_pct": None if vol.value is None else vol.value / math.sqrt(252),
               "adv_cr": technical.adv_crore(closes, volumes),
               "tvt": novel.trend_value_tension(pe_pct.value, tr.value, period).to_dict()}
    return [e for e in E if e is not None], context


def build_dossier(session: Session, company: Company, book_value: float = 1_000_000,
                  max_position_pct: float = 10.0,
                  expected_hold_months: float = 6.0) -> dict:
    regime = current_regime(session)
    book = portfolio_state(session)
    evidence, ctx = build_evidence(session, company, regime["conditioning_key"], book)
    synth = synthesize(evidence)

    sizing, costs = None, None
    if synth.verdict == "long_candidate" and ctx["price"] and ctx["daily_vol_pct"]:
        sizing = recommend_size(SizingInputs(
            book_value=book["book_value"] or book_value, price=ctx["price"],
            daily_vol_pct=ctx["daily_vol_pct"], conviction_band=synth.conviction_band,
            net_score=synth.net_score, adv_cr=ctx["adv_cr"],
            max_position_pct=max_position_pct,
            open_heat_pct=book["open_heat_pct"]))
        costs = cost_tax_breakeven(sizing["recommended_value"] or 1.0,
                                   ctx["adv_cr"], expected_hold_months)

    shadow_count = sum(1 for e in evidence if e.direction == "shadow")
    missing = {
        "coverage": synth.coverage,
        "shadow_evidence": shadow_count,
        "not_ingested": ["earnings-call transcripts", "shareholding pattern",
                         "promoter pledges", "delivery %", "insider trades",
                         "quarterly statements"],
        "note": "Free-source limits (PHASE2 wave II plan): flow/governance "
                "evidence is proxy-only; deferred hypotheses render as SHADOW.",
    }
    if company.is_financial:
        missing["note"] += " Financial-sector company: statement engines skipped by design."
    if not book["has_book"]:
        missing["note"] += " No open positions — portfolio-fit evidence unavailable."

    dossier = {
        "company": {"ticker": company.ticker, "name": company.name,
                    "sector": company.sector, "price": ctx["price"],
                    "is_financial": company.is_financial},
        "as_of": ctx["as_of"],
        "regime": regime,
        "synthesis": synth.to_dict(),
        "evidence": [e.to_dict() for e in evidence],
        "trend_value_tension": ctx["tvt"],
        "portfolio_context": {"has_book": book["has_book"],
                              "book_value": round(book["book_value"], 2),
                              "open_heat_pct": book["open_heat_pct"]},
        "sizing": sizing,
        "costs_taxes": costs,
        "missing_information": missing,
        "claim_horizon_days": 126,
        "epistemics": {
            "normalization": "strengths = cross-sectional percentiles (PHASE2 §5.1); "
                             "admission caps by hypothesis status (§5.2); "
                             "base-rate depth reads N_eff, not N (§8)",
            "not_advice": "Decision-support dossier, not a recommendation to trade.",
        },
    }
    ledger_rec = ledger.register_dossier(dossier)
    dossier["ledger"] = {"hash": ledger_rec["hash"], "prev_hash": ledger_rec["prev_hash"],
                         "registered_at": ledger_rec["created_at"],
                         "claim": ledger_rec["claim"]}
    return dossier
