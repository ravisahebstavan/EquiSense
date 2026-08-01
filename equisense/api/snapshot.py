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
SNAP_VERSION = 5  # bump when the item schema changes → forces a rebuild

# A name whose last price is more than this many TRADING SESSIONS behind the
# universe is treated as not currently priced. Sessions, not calendar days:
# weekends and the long Indian holiday calendar would otherwise make a healthy
# name look stale, and a genuinely frozen one look fine across a holiday week.
STALE_SESSIONS = 3


def _bulk_delivery(session: Session) -> dict:
    """{ticker: {latest, mean}} delivery percentage from the NSE MTO archive.

    The MEAN is the reference the latest reading is judged against: absolute
    delivery levels differ enormously by name (an ETF sits near 90%, a
    speculative small cap near 10%), so only the deviation from a stock's OWN
    norm carries information.
    """
    from ..models import DeliveryStat
    rows = session.execute(
        select(DeliveryStat.symbol, DeliveryStat.trade_date,
               DeliveryStat.delivery_pct)
        .order_by(DeliveryStat.symbol, DeliveryStat.trade_date)).all()
    acc: dict = {}
    for sym, _d, pct in rows:
        acc.setdefault(sym, []).append(float(pct))
    return {sym: {"latest": vals[-1], "mean": sum(vals) / len(vals),
                  "observations": len(vals)}
            for sym, vals in acc.items() if vals}


def _bulk_prices(session: Session) -> dict[int, tuple[list, list, list, list]]:
    """One query → {company_id: (dates, closes, volumes, nominal_closes)}.

    `closes` is the TOTAL-RETURN series (returns/momentum/vol basis);
    `nominal_closes` is split-adjusted only and is what anything dividing a
    price by a per-share accounting figure must use. See PriceObservation.
    """
    rows = session.execute(
        select(PriceObservation.company_id, PriceObservation.obs_date,
               PriceObservation.close, PriceObservation.volume,
               PriceObservation.close_raw, PriceObservation.open_price,
               PriceObservation.high_price, PriceObservation.low_price)
        .order_by(PriceObservation.company_id, PriceObservation.obs_date)).all()
    out: dict[int, tuple] = {}
    for cid, d, c, v, raw, op, hi, lo in rows:
        if cid not in out:
            out[cid] = ([], [], [], [], [], [], [])
        out[cid][0].append(d)
        out[cid][1].append(c)
        out[cid][2].append(v)
        out[cid][3].append(raw)
        out[cid][4].append(op)
        out[cid][5].append(hi)
        out[cid][6].append(lo)
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


def _ret_n(closes: list[float], n: int) -> Optional[float]:
    if len(closes) < n + 1 or not closes[-n - 1]:
        return None
    return closes[-1] / closes[-n - 1] - 1


def _max5_21d(closes: list[float]) -> Optional[float]:
    """Mean of the top-5 daily returns in the trailing 21 sessions — the raw
    MAX-effect input (Bali, Cakici & Whitelaw 2011); see research/base_rates.py."""
    window = closes[-21:]
    if len(window) < 15:
        return None
    daily = [window[i] / window[i - 1] - 1 for i in range(1, len(window)) if window[i - 1]]
    if len(daily) < 5:
        return None
    return sum(sorted(daily)[-5:]) / 5


def build_universe_snapshot(session: Session) -> dict:
    """The one heavy pass: 3 bulk queries + pure CPU. Runs at refresh time."""
    # Live analytical universe = CURRENT index members only. Departed names keep
    # their history in the database (deleting it would manufacture survivorship
    # bias) but must not sit in the cross-section: xsec_strength ranks each
    # signal against this set, so a stale constituent silently shifts every
    # percentile in the universe.
    companies = session.scalars(
        select(Company).where(Company.is_index_member == True)).all()   # noqa: E712
    prices = _bulk_prices(session)
    delivery = _bulk_delivery(session)
    statements = _bulk_statements(session)
    nifty = _nifty(session)

    # The universe's own trading calendar, so staleness is measured against the
    # sessions that actually happened rather than the wall clock.
    all_dates = sorted({d for cid in prices for d in prices[cid][0]})
    session_no = {d: i for i, d in enumerate(all_dates)}
    last_session = len(all_dates) - 1

    items = []
    stale: dict[str, int] = {}
    ret63_by_ticker: dict[str, Optional[float]] = {}
    for c in companies:
        dates, closes, volumes, nominal, opens, highs, lows = prices.get(
            c.id, ([], [], [], [], [], [], []))
        # OHLC is on the NOMINAL scale, so pair it with close_raw. Yang-Zhang on
        # a total-return close mixed with nominal H/L would compare incompatible
        # series bar by bar.
        ohlc_ok = (nominal and opens and highs and lows
                   and all(x is not None for x in nominal[-260:])
                   and all(x is not None for x in opens[-260:])
                   and all(x is not None for x in highs[-260:])
                   and all(x is not None for x in lows[-260:]))
        if len(closes) < 60:
            continue
        stmts = statements.get(c.id, [])
        price = closes[-1]
        max5 = _max5_21d(closes)
        ret63_by_ticker[c.ticker] = _ret_n(closes, 63)
        sig: dict[str, Optional[float]] = {
            "momentum": _r(technical.momentum_12_1(closes).value),
            "dist_52w": _r(technical.pct_from_52w_high(closes).value),
            "trend": _r(technical.trend_200dma(closes).value),
            "rel_strength": _r(technical.relative_strength(closes, nifty).value),
            "mqi": _r(novel.momentum_quality(closes).value),
            # Yang-Zhang when OHLC is available: gap-inclusive and ~5.5x more
            # efficient than close-to-close, and this value sets the stop
            # distance and therefore the position size.
            "vol": _r((technical.best_available_vol(
                nominal if ohlc_ok else closes,
                opens if ohlc_ok else None,
                highs if ohlc_ok else None,
                lows if ohlc_ok else None, window=21)).value),
            "vol_estimator": (technical.best_available_vol(
                nominal if ohlc_ok else closes,
                opens if ohlc_ok else None,
                highs if ohlc_ok else None,
                lows if ohlc_ok else None,
                window=21).inputs.get("estimator")),
            "heat": _r(novel.crowding_proxy(
                closes, volumes,
                delivery_pct=(delivery.get(c.ticker) or {}).get("latest"),
                delivery_mean_pct=(delivery.get(c.ticker) or {}).get("mean")).value),
            "delivery_pct": _r((delivery.get(c.ticker) or {}).get("latest")),
            "max_effect": None if max5 is None else _r(-max5 * 100),  # negated: low actual MAX scores high
            "sector_rel_mom": None,  # filled below, needs the full cross-section first
            "f_score": None, "z_score": None, "z_zone": None, "ccs": None,
            "fragility": None, "exp_gap": None, "pe_pctile": None,
            "revenue_cagr_pct": None, "roic_pct": None, "pe": None,
            "dividend_yield_pct": None, "debt_to_equity": None,
            "implied_growth_gap_pct": None, "f_signals_available": None,
            "z_score_em": None, "delivery_pct": None, "vol_estimator": None,
        }
        if stmts and not c.is_financial:
            curr = stmts[-1]
            prev = stmts[-2] if len(stmts) >= 2 else None
            years = curr.fiscal_year - stmts[0].fiscal_year
            sig["revenue_cagr_pct"] = _cagr(stmts[0].revenue, curr.revenue, years)
            sig["roic_pct"] = _r(ratios.roic(curr, prev=prev).value)
            ps = {m.key: m for m in ratios.per_share_ratios(curr, price)}
            sig["pe"] = _r(ps["pe"].value) if "pe" in ps else None
            sig["dividend_yield_pct"] = _r(ps["dividend_yield"].value) if "dividend_yield" in ps else None
            lev = {m.key: m for m in ratios.leverage_ratios(curr)}
            sig["debt_to_equity"] = _r(lev["debt_to_equity"].value)
            if prev:
                f = quality.piotroski_f(curr, prev, price)
                sig["f_score"] = f.value
                # carried so quality_tier() can refuse to tier on sparse data
                sig["f_signals_available"] = int(f.inputs.get("signals_available", 0))
            z = quality.altman_z(curr, price)
            z_em = quality.altman_z_em(curr)
            sig["z_score"] = _r(z.value)
            sig["z_score_em"] = _r(z_em.value)
            # zone prefers the EM variant: price-invariant and calibrated for
            # non-manufacturers, which most of this universe is
            sig["z_zone"] = (quality.altman_zone_em(z_em.value)
                             or quality.altman_zone(z.value))
            sig["ccs"] = _r(novel.cash_conviction(stmts).value)
            sig["fragility"] = _r(novel.fragility(stmts, closes).value)
            rd = valuation.reverse_dcf(curr, price, statements=stmts)
            hist = valuation.historical_fcf_cagr(stmts)
            if rd["implied_growth"].value is not None and hist and hist.value is not None:
                sig["exp_gap"] = _r(rd["implied_growth"].value - hist.value)
                sig["implied_growth_gap_pct"] = sig["exp_gap"]
            nom = nominal if nominal and all(x is not None for x in nominal) else None
            sig["pe_pctile"] = _r(novel.pe_percentile_vs_history(
                closes, dates, stmts, nominal_closes=nom).value)

        # Staleness had been measured only as max(obs_date) across the WHOLE
        # table, so a single current name made the entire dataset read fresh and
        # a name frozen weeks ago was invisible. Measured per name: five Nifty-50
        # constituents were sitting 17 sessions behind while the median name in
        # the universe had moved 3.9% (p75 7.0%, max 18.7%).
        lag = last_session - session_no.get(dates[-1], last_session)
        if lag > STALE_SESSIONS:
            stale[c.ticker] = lag

        window = closes[-252:]  # 1y of closes, downsampled to ≤40 points
        step = max(1, len(window) // 40)
        adv = technical.adv_crore(closes, volumes)
        items.append({
            "id": c.id, "ticker": c.ticker, "name": c.name, "sector": c.sector,
            "cap_band": c.cap_band, "is_financial": c.is_financial,
            "price": round(price, 2),
            "chg_1d_pct": None if len(closes) < 2 or not closes[-2]
            else round((closes[-1] / closes[-2] - 1) * 100, 2),
            "adv_cr": None if adv is None else round(adv, 2),
            "spark": [round(v, 1) for v in window[::step]][-40:],
            "stale_sessions": stale.get(c.ticker, 0),
            "signals": sig,
        })

    # Sector-relative momentum (Moskowitz & Grinblatt 1999) needs the full
    # cross-section's 63d returns before each company's sector average is known.
    from collections import defaultdict
    sector_rets: dict[str, list[float]] = defaultdict(list)
    for item in items:
        r = ret63_by_ticker.get(item["ticker"])
        # A frozen name's 63d return is frozen too, so letting it into the
        # sector average would tilt every peer's sector-relative momentum.
        if r is not None and not item["stale_sessions"]:
            sector_rets[item["sector"]].append(r)
    sector_avg = {s: sum(v) / len(v) for s, v in sector_rets.items() if v}
    for item in items:
        r = ret63_by_ticker.get(item["ticker"])
        avg = sector_avg.get(item["sector"])
        if r is not None and avg is not None:
            item["signals"]["sector_rel_mom"] = round((r - avg) * 100, 2)

    as_of = max((prices[cid][0][-1] for cid in prices), default=None)
    snap = {"as_of": str(as_of), "version": SNAP_VERSION,
            "built_at": datetime.utcnow().isoformat(),
            "companies": items,
            "stale_names": dict(sorted(stale.items(), key=lambda kv: -kv[1]))}
    row = session.get(AppSnapshot, UNIVERSE_KEY)
    if row is None:
        row = AppSnapshot(key=UNIVERSE_KEY, as_of=snap["as_of"], payload="")
        session.add(row)
    row.as_of = snap["as_of"]
    row.payload = json.dumps(snap)
    session.commit()
    # Rebuilding IS the invalidation: any caller that just rebuilt must see its
    # own result, not a cached predecessor.
    import time as _time
    _UNIVERSE_CACHE.update(key=(snap["as_of"], SNAP_VERSION), snap=snap,
                           checked_at=_time.time())
    return snap


# The snapshot payload is a few hundred KB of JSON and several callers want it
# within one request — qualified_candidates, universe_signals,
# cluster_correlation and the company detail page all reach for it. Measured on
# the candidate screen: 5 fetches costing 37 of its 63 seconds. Keyed on the
# latest price date AND the schema version, so an ingest or a version bump
# invalidates it immediately; a plain time-based cache could serve a snapshot
# that no longer matches the prices underneath it.
_UNIVERSE_CACHE: dict = {"key": None, "snap": None, "checked_at": 0.0}

# How long the freshness probe itself may be trusted. The cache above stopped
# the payload being re-parsed, but `SELECT max(obs_date)` still ran on EVERY
# call to compute the cache key — 11 round trips on one page load, 2.75s of
# pure latency. Prices change at most once a day and the refresh pipeline
# rebuilds the snapshot explicitly, so re-probing more than once every few
# seconds buys nothing.
FRESHNESS_PROBE_TTL_S = 15.0


def invalidate_universe_cache() -> None:
    """Drop the cached snapshot. Called after an ingest so a refresh is visible
    immediately rather than up to FRESHNESS_PROBE_TTL_S later."""
    _UNIVERSE_CACHE.update(key=None, snap=None, checked_at=0.0)


def get_universe(session: Session, allow_rebuild: bool = True) -> dict:
    """Single-row read; rebuilds only when prices are newer than the snapshot."""
    import time as _time
    if (_UNIVERSE_CACHE["snap"] is not None
            and _time.time() - _UNIVERSE_CACHE["checked_at"] < FRESHNESS_PROBE_TTL_S):
        return _UNIVERSE_CACHE["snap"]
    latest = session.scalar(select(func.max(PriceObservation.obs_date)))
    ckey = (str(latest), SNAP_VERSION)
    if _UNIVERSE_CACHE["key"] == ckey and _UNIVERSE_CACHE["snap"] is not None:
        _UNIVERSE_CACHE["checked_at"] = _time.time()
        return _UNIVERSE_CACHE["snap"]
    row = session.get(AppSnapshot, UNIVERSE_KEY)
    if row is not None and (latest is None or row.as_of >= str(latest)):
        snap = json.loads(row.payload)
        if snap.get("version") == SNAP_VERSION:
            _UNIVERSE_CACHE.update(key=ckey, snap=snap, checked_at=_time.time())
            return snap
    if not allow_rebuild:
        return json.loads(row.payload) if row else {"as_of": None, "companies": []}
    snap = build_universe_snapshot(session)
    _UNIVERSE_CACHE.update(key=(str(latest), SNAP_VERSION), snap=snap,
                           checked_at=_time.time())
    return snap
