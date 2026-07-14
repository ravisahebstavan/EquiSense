"""Universe snapshot cache — the serverless performance fix.

The dashboard/universe views need every company's signals, which naively
costs hundreds of SQL round trips (40s over network Postgres). Instead, this
module computes everything from THREE bulk queries, stores the result as one
JSON row (AppSnapshot 'universe'), and page loads become a single fetch.
Freshness is tied to the latest price date; the refresh pipeline and cron
rebuild it explicitly.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engine import novel, quality, ratios, technical, valuation
from ..engine.types import StatementData
from ..models import (AppSnapshot, Company, FilingPeriod, MacroObservation,
                      PriceObservation)

UNIVERSE_KEY = "universe"


def _bulk_prices(session: Session) -> dict[int, tuple[list, list, list]]:
    """One query → {company_id: (dates, closes, volumes)} ordered oldest→newest."""
    rows = session.execute(
        select(PriceObservation.company_id, PriceObservation.obs_date,
               PriceObservation.close, PriceObservation.volume)
        .order_by(PriceObservation.company_id, PriceObservation.obs_date)).all()
    out: dict[int, tuple[list, list, list]] = {}
    for cid, d, c, v in rows:
        if cid not in out:
            out[cid] = ([], [], [])
        out[cid][0].append(d)
        out[cid][1].append(c)
        out[cid][2].append(v)
    return out


def _bulk_statements(session: Session) -> dict[int, list[StatementData]]:
    """One query → {company_id: [StatementData ...]} oldest→newest, latest
    restatement versions only."""
    rows = session.scalars(
        select(FilingPeriod)
        .where(FilingPeriod.scope == "consolidated", FilingPeriod.is_latest.is_(True))
        .order_by(FilingPeriod.company_id, FilingPeriod.fiscal_year)).all()
    fields = [f for f in StatementData.__dataclass_fields__
              if f not in ("period", "fiscal_year", "scope")]
    out: dict[int, list[StatementData]] = {}
    for r in rows:
        out.setdefault(r.company_id, []).append(StatementData(
            period=r.period, fiscal_year=r.fiscal_year, scope=r.scope,
            **{f: getattr(r, f) for f in fields}))
    return out


def _nifty(session: Session) -> list[float]:
    return [r[0] for r in session.execute(
        select(MacroObservation.close).where(MacroObservation.symbol == "^NSEI")
        .order_by(MacroObservation.obs_date)).all()]


def _cagr(first, last, years):
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 2)


def _r(v, d=2):
    return None if v is None else round(v, d)


def build_universe_snapshot(session: Session) -> dict:
    """The one heavy pass: 3 bulk queries + pure CPU. Runs at refresh time."""
    companies = session.scalars(select(Company)).all()
    prices = _bulk_prices(session)
    statements = _bulk_statements(session)
    nifty = _nifty(session)

    items = []
    for c in companies:
        dates, closes, volumes = prices.get(c.id, ([], [], []))
        if len(closes) < 60:
            continue
        stmts = statements.get(c.id, [])
        price = closes[-1]
        sig: dict[str, Optional[float]] = {
            "momentum": _r(technical.momentum_12_1(closes).value),
            "dist_52w": _r(technical.pct_from_52w_high(closes).value),
            "trend": _r(technical.trend_200dma(closes).value),
            "rel_strength": _r(technical.relative_strength(closes, nifty).value),
            "mqi": _r(novel.momentum_quality(closes).value),
            "vol": _r(technical.realized_vol(closes).value),
            "heat": _r(novel.crowding_proxy(closes, volumes).value),
            "f_score": None, "z_score": None, "z_zone": None, "ccs": None,
            "fragility": None, "exp_gap": None, "pe_pctile": None,
            "revenue_cagr_pct": None, "roic_pct": None, "pe": None,
            "dividend_yield_pct": None, "debt_to_equity": None,
            "implied_growth_gap_pct": None,
        }
        if stmts and not c.is_financial:
            curr = stmts[-1]
            prev = stmts[-2] if len(stmts) >= 2 else None
            years = curr.fiscal_year - stmts[0].fiscal_year
            sig["revenue_cagr_pct"] = _cagr(stmts[0].revenue, curr.revenue, years)
            sig["roic_pct"] = _r(ratios.roic(curr).value)
            ps = {m.key: m for m in ratios.per_share_ratios(curr, price)}
            sig["pe"] = _r(ps["pe"].value) if "pe" in ps else None
            sig["dividend_yield_pct"] = _r(ps["dividend_yield"].value) if "dividend_yield" in ps else None
            lev = {m.key: m for m in ratios.leverage_ratios(curr)}
            sig["debt_to_equity"] = _r(lev["debt_to_equity"].value)
            if prev:
                sig["f_score"] = quality.piotroski_f(curr, prev, price).value
            z = quality.altman_z(curr, price)
            sig["z_score"] = _r(z.value)
            sig["z_zone"] = quality.altman_zone(z.value)
            sig["ccs"] = _r(novel.cash_conviction(stmts).value)
            sig["fragility"] = _r(novel.fragility(stmts, closes).value)
            rd = valuation.reverse_dcf(curr, price)
            hist = valuation.historical_fcf_cagr(stmts)
            if rd["implied_growth"].value is not None and hist and hist.value is not None:
                sig["exp_gap"] = _r(rd["implied_growth"].value - hist.value)
                sig["implied_growth_gap_pct"] = sig["exp_gap"]
            sig["pe_pctile"] = _r(novel.pe_percentile_vs_history(closes, dates, stmts).value)

        window = closes[-252:]  # 1y of closes, downsampled to ≤40 points
        step = max(1, len(window) // 40)
        items.append({
            "id": c.id, "ticker": c.ticker, "name": c.name, "sector": c.sector,
            "cap_band": c.cap_band, "is_financial": c.is_financial,
            "price": round(price, 2),
            "spark": [round(v, 1) for v in window[::step]][-40:],
            "signals": sig,
        })

    as_of = max((prices[cid][0][-1] for cid in prices), default=None)
    snap = {"as_of": str(as_of), "built_at": datetime.utcnow().isoformat(),
            "companies": items}
    row = session.get(AppSnapshot, UNIVERSE_KEY)
    if row is None:
        row = AppSnapshot(key=UNIVERSE_KEY, as_of=snap["as_of"], payload="")
        session.add(row)
    row.as_of = snap["as_of"]
    row.payload = json.dumps(snap)
    session.commit()
    return snap


def get_universe(session: Session, allow_rebuild: bool = True) -> dict:
    """Single-row read; rebuilds only when prices are newer than the snapshot."""
    latest = session.scalar(select(func.max(PriceObservation.obs_date)))
    row = session.get(AppSnapshot, UNIVERSE_KEY)
    if row is not None and (latest is None or row.as_of >= str(latest)):
        return json.loads(row.payload)
    if not allow_rebuild:
        return json.loads(row.payload) if row else {"as_of": None, "companies": []}
    return build_universe_snapshot(session)
