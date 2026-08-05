"""EquiSense proprietary analytics (§6.7 opportunity map).

Novel, named composite diagnostics. "Proprietary" here means *original
construction*, not secret: every score's formula is fully documented, every
component exposed, and each is a registered hypothesis subject to the
base-rate module — the anti-Trendlyne commitment (§6.7 of v1 stands).

All pure functions. Statements oldest → newest.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from datetime import date
from typing import Optional, Sequence

from .types import Metric, StatementData, fmt, safe_div
from .technical import TRADING_DAYS, momentum_12_1, realized_vol, trend_200dma, volume_anomaly


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- MQI

def momentum_quality(closes: Sequence[float], period: str = "") -> Metric:
    """Momentum Quality Index (MQI) — EquiSense original.

    Rewards *smooth* trend over violent trend: identical 12-1 returns score
    higher in magnitude when achieved with lower volatility and more consistent
    daily progress in the trend's own direction. Construction:
        MQI = risk_adjusted_momentum × persistence_multiplier
        risk-adj momentum = (12-1 return %) / (annualized vol %)
        persistence = fraction of days moving WITH the sign of momentum
        multiplier = 0.5 + persistence  (range 0.5–1.5)

    DIRECTIONAL FIX (Wave S): persistence was previously the raw up-day fraction,
    regardless of trend direction. In a downtrend the up-day fraction is low, so
    the multiplier fell below 1 and *shrank* the bearish score — meaning a smooth,
    relentless decline was scored as WEAKER evidence than a choppy one, the exact
    inverse of the construction's stated intent. Verified before the fix: a smooth
    decline (−19.8% momentum) and a choppy decline (−44.0%) ranked in the wrong
    order relative to their path quality. Persistence is now measured as agreement
    with the direction of momentum, so path consistency amplifies the signal
    symmetrically on both sides.

    Hypothesis (registered: HYP-004): smooth momentum decays slower than raw
    momentum — path quality carries information about holder composition.
    """
    mom = momentum_12_1(closes).value
    vol = realized_vol(closes, 126).value
    window = closes[-(TRADING_DAYS - 21):-21] if len(closes) >= TRADING_DAYS else closes
    ups = sum(1 for i in range(1, len(window)) if window[i] > window[i - 1])
    n_moves = max(1, len(window) - 1)
    up_fraction = ups / n_moves
    # agreement with the trend's own direction, not "went up"
    if mom is None:
        persistence = up_fraction
    else:
        persistence = up_fraction if mom >= 0 else (1.0 - up_fraction)
    if mom is None or vol in (None, 0):
        value = None
        formula = "insufficient history"
    else:
        value = (mom / vol) * (0.5 + persistence)
        formula = (f"({fmt(mom)}% mom / {fmt(vol)}% vol) × "
                   f"(0.5 + trend-agreement {persistence:.2f})")
    return Metric(
        key="momentum_quality", label="Momentum Quality Index (MQI)",
        value=value, unit="score", formula=formula,
        inputs={"momentum_12_1_pct": mom, "vol_126d_pct": vol,
                "up_day_fraction": round(up_fraction, 3),
                "trend_agreement_fraction": round(persistence, 3)},
        period=period, family="novel",
        caveat="EquiSense-original composite; hypothesis HYP-004 in the registry. "
               "Persistence measures agreement with the direction of momentum, so "
               "the score is symmetric: a smooth decline is strongly negative, not "
               "weakly negative. Not a validated standalone signal until its "
               "base-rate table says so.")


# ---------------------------------------------------------------- CCS

def cash_conviction(stmts: list[StatementData], period: str = "") -> Metric:
    """Cash Conviction Score (CCS, 0–100) — EquiSense original.

    'How much of the reported profit story is backed by actual cash?'
    Three components over up to 3 recent fiscal years:
      conversion (50 pts): mean CFO/NI, full marks at ≥1.1, zero at ≤0.4
      accrual discipline (30 pts): mean accruals ratio, full at ≤0%, zero at ≥+8%
      asset honesty (20 pts): capex/depreciation, full in [0.8, 3.0] —
        penalizes both starvation (<0.8) and unexplained splurge (>3.0)
    """
    recent = [s for s in stmts if s.net_income and s.cfo is not None][-3:]
    if not recent:
        return Metric(key="cash_conviction", label="Cash Conviction Score (CCS)",
                      value=None, unit="score", formula="no usable statements",
                      inputs={}, period=period, family="novel")
    conv = [s.cfo / s.net_income for s in recent if s.net_income > 0]
    accr = [(s.net_income - s.cfo) / s.total_assets for s in recent if s.total_assets]
    cd = [s.capex / s.depreciation for s in recent
          if s.capex is not None and s.depreciation]
    conv_m = sum(conv) / len(conv) if conv else None
    accr_m = sum(accr) / len(accr) * 100 if accr else None
    cd_m = sum(cd) / len(cd) if cd else None

    pts_conv = 0.0 if conv_m is None else _clamp((conv_m - 0.4) / 0.7 * 50, 0, 50)
    pts_accr = 15.0 if accr_m is None else _clamp((8 - accr_m) / 8 * 30, 0, 30)
    if cd_m is None:
        pts_capex = 10.0
    elif 0.8 <= cd_m <= 3.0:
        pts_capex = 20.0
    else:
        pts_capex = _clamp(20 - abs(cd_m - (0.8 if cd_m < 0.8 else 3.0)) * 10, 0, 20)
    score = pts_conv + pts_accr + pts_capex
    return Metric(
        key="cash_conviction", label="Cash Conviction Score (CCS)",
        value=score, unit="score",
        formula=(f"conversion {pts_conv:.0f}/50 (CFO/NI {fmt(conv_m, 2)}) + "
                 f"accruals {pts_accr:.0f}/30 ({fmt(accr_m, 1)}%) + "
                 f"capex sanity {pts_capex:.0f}/20 (capex/dep {fmt(cd_m, 2)})"),
        inputs={"mean_cfo_ni": conv_m, "mean_accruals_pct": accr_m,
                "mean_capex_dep": cd_m, "years_used": len(recent)},
        period=period, family="novel",
        caveat="EquiSense-original composite (registry HYP-005). Component "
               "thresholds are stated design choices, inspectable above.")


# ---------------------------------------------------------------- Fragility

def fragility(stmts: list[StatementData], closes: Sequence[float],
              period: str = "") -> Metric:
    """Fragility Index (0–100, higher = more fragile) — EquiSense original.

    'If conditions turn hostile, how much does this break?' Four stressors:
      balance-sheet (40): net debt/EBITDA, 0 pts at ≤0x, 40 at ≥4x
      coverage (20): interest coverage, 0 pts at ≥12x, 20 at ≤1.5x
      market (25): realized vol percentile proxy — vol/60% capped
      drawdown habit (15): 1y max drawdown, 0 at ≥−10%, 15 at ≤−50%
    """
    s = stmts[-1] if stmts else None
    nd_ebitda = None
    cov = None
    if s is not None:
        if s.total_debt is not None and s.cash is not None and s.ebitda:
            nd_ebitda = (s.total_debt - s.cash) / s.ebitda
        cov = safe_div(s.ebit, s.interest_expense)
    vol = realized_vol(closes, 126).value
    peak, mdd = float("-inf"), 0.0
    for c in closes[-TRADING_DAYS:]:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)

    pts_bs = 20.0 if nd_ebitda is None else _clamp(nd_ebitda / 4 * 40, 0, 40)
    pts_cov = 10.0 if cov is None else _clamp((12 - cov) / 10.5 * 20, 0, 20)
    pts_vol = 12.5 if vol is None else _clamp(vol / 60 * 25, 0, 25)
    pts_dd = _clamp((abs(mdd) * 100 - 10) / 40 * 15, 0, 15)
    score = pts_bs + pts_cov + pts_vol + pts_dd
    return Metric(
        key="fragility", label="Fragility Index",
        value=score, unit="score",
        formula=(f"balance-sheet {pts_bs:.0f}/40 (ND/EBITDA {fmt(nd_ebitda, 2)}) + "
                 f"coverage {pts_cov:.0f}/20 ({fmt(cov, 1)}x) + "
                 f"vol {pts_vol:.0f}/25 ({fmt(vol, 1)}%) + "
                 f"drawdown {pts_dd:.0f}/15 ({mdd * 100:.1f}%)"),
        inputs={"net_debt_ebitda": nd_ebitda, "interest_coverage": cov,
                "vol_126d_pct": vol, "max_dd_1y_pct": mdd * 100},
        period=period, family="novel",
        caveat="EquiSense-original composite (registry HYP-006). Missing "
               "components take neutral midpoints, shown above.")


# ------------------------------------------------------- valuation history

def pe_percentile_vs_history(closes: Sequence[float], close_dates: Sequence[date],
                             stmts: list[StatementData], period: str = "",
                             nominal_closes: Optional[Sequence[float]] = None) -> Metric:
    """Current trailing P/E's percentile within its own multi-year P/E history.

    Historical P/E at each month-end uses the latest fiscal year EPS *known at
    that time* (PIT-honest within the reconstructed-fundamentals caveat).

    PRICE CONVENTION (Wave S): pass `nominal_closes` — the split-adjusted but
    NOT dividend-adjusted series. A total-return series back-deflates historical
    prices while leaving the most recent bar alone, so dividing it by nominal
    filing EPS understates every historical P/E and leaves today's P/E at full
    value. The percentile then reads systematically "expensive", and because that
    percentile is inverted into the value cluster it applied a standing bearish
    tilt. At ~1.3% dividend yield over 10 years the oldest bar is deflated ~12%.

    When `nominal_closes` is absent the function still computes, but says in its
    caveat that the series may be dividend-adjusted and the level may be biased.
    """
    price_series = nominal_closes if nominal_closes else closes
    using_nominal = bool(nominal_closes)
    if len(price_series) != len(close_dates):
        price_series = closes
        using_nominal = False
    closes = price_series
    eps_by_fy_end: list[tuple[date, float]] = []
    for s in stmts:
        if s.net_income and s.shares_outstanding:
            fy_end = date(s.fiscal_year, 3, 31)
            eps_by_fy_end.append((fy_end + __import__("datetime").timedelta(days=60),
                                  s.net_income / s.shares_outstanding))
    eps_by_fy_end.sort()
    if not eps_by_fy_end or len(closes) < 260:
        return Metric(key="pe_percentile", label="P/E Percentile vs Own History",
                      value=None, unit="pctile", formula="insufficient history",
                      inputs={}, period=period, family="novel")
    known_dates = [d for d, _ in eps_by_fy_end]

    def eps_known(on: date) -> Optional[float]:
        i = bisect_left(known_dates, on)
        return eps_by_fy_end[i - 1][1] if i > 0 else None

    pes: list[float] = []
    for i in range(0, len(closes), 21):  # ~monthly sampling
        e = eps_known(close_dates[i])
        if e and e > 0:
            pes.append(closes[i] / e)
    e_now = eps_known(close_dates[-1])
    pe_now = closes[-1] / e_now if e_now and e_now > 0 else None
    if pe_now is None or len(pes) < 12:
        return Metric(key="pe_percentile", label="P/E Percentile vs Own History",
                      value=None, unit="pctile", formula="insufficient P/E history",
                      inputs={}, period=period, family="novel")
    # midrank: a P/E tied with its own history sits at 50, not 100
    _less = sum(1 for p in pes if p < pe_now)
    _eq = sum(1 for p in pes if p == pe_now)
    pctile = (_less + 0.5 * _eq) / len(pes) * 100
    return Metric(
        key="pe_percentile", label="P/E Percentile vs Own History",
        value=pctile, unit="pctile",
        formula=f"Current trailing P/E {fmt(pe_now, 1)}x vs {len(pes)} monthly "
                f"observations of own history",
        inputs={"pe_now": pe_now, "pe_history_min": min(pes),
                "pe_history_median": sorted(pes)[len(pes) // 2],
                "pe_history_max": max(pes), "n_observations": len(pes),
                "price_convention": "nominal (split-adjusted)" if using_nominal
                                    else "UNKNOWN — may be dividend-adjusted"},
        period=period, family="novel",
        caveat=("EPS timeline reconstructed from latest-known filings "
                "(pit_grade: reconstructed) with a 60-day publication lag assumption."
                + ("" if using_nominal else
                   " WARNING: no nominal price series was supplied, so this P/E "
                   "history may be computed from dividend-adjusted prices. That "
                   "deflates older P/Es and biases the percentile toward "
                   "'expensive'. Re-ingest prices to populate close_raw.")))


# ---------------------------------------------------------------- TVT

TVT_QUADRANTS = {
    ("cheap", "up"): "coiled value — cheap vs own history AND in an uptrend",
    ("cheap", "down"): "falling knife or deep value — cheap but trend is against it",
    ("expensive", "up"): "momentum-carried — trend is up but priced richly vs own history",
    ("expensive", "down"): "unwinding — expensive AND breaking down; historically the worst quadrant",
}


def trend_value_tension(pe_pctile: Optional[float], trend_above_ma_pct: Optional[float],
                        period: str = "") -> Metric:
    """Trend–Value Tension (TVT) — EquiSense original quadrant diagnostic.

    Crosses valuation-vs-own-history against trend regime. The *tension*
    quadrants (cheap+down, expensive+up) are where the interesting decisions
    live; the aligned quadrants are the easy ones.
    """
    if pe_pctile is None or trend_above_ma_pct is None:
        return Metric(key="tvt", label="Trend–Value Tension (TVT)", value=None,
                      unit="quadrant", formula="needs P/E percentile + trend",
                      inputs={}, period=period, family="novel")
    val = "cheap" if pe_pctile <= 40 else ("expensive" if pe_pctile >= 60 else "fair")
    trd = "up" if trend_above_ma_pct > 0 else "down"
    quadrant = TVT_QUADRANTS.get((val, trd), f"{val} & trend {trd} — neutral zone")
    tension = abs(pe_pctile - 50) / 50 * (1 if (val == "cheap") == (trd == "down") else -1)
    return Metric(
        key="tvt", label="Trend–Value Tension (TVT)",
        value=round(tension, 3), unit="quadrant",
        formula=f"P/E percentile {pe_pctile:.0f} × trend {trend_above_ma_pct:+.1f}% "
                f"vs 200DMA → “{quadrant}”",
        inputs={"pe_percentile": pe_pctile, "trend_vs_200dma_pct": trend_above_ma_pct,
                "quadrant": quadrant},
        period=period, family="novel",
        caveat="Quadrant labels describe historical tendencies, not instructions. "
               "Tension >0 = disagreement between value and trend evidence.")


# ---------------------------------------------------------------- crowding

# Below this multiple of its own recent median volume, a session is too thin for
# its delivery percentage to mean anything. When a stock locks at a circuit the
# order book freezes, intraday square-offs cannot clear, and the delivery RATIO
# mathematically rises toward 100% on a tiny absolute number of shares. Reading
# that as institutional accumulation would point the signal at exactly the names
# that are locked and un-exitable.
#
# Not yet observed in our own data — the delivery archive currently covers 50
# Nifty-50 names over 9 sessions, delivery spans 20.91%-83.07%, and nothing sits
# above 90%. Those names are in F&O and have no circuit limits at all, only a
# 10% dynamic band, so the sample CANNOT contain the pattern. The guard is
# therefore precautionary rather than evidence-driven, and is cheap: it only
# suppresses a modifier on sessions where the underlying number is meaningless.
MIN_VOLUME_RATIO_FOR_DELIVERY = 0.30


def crowding_proxy(closes: Sequence[float], volumes: Sequence[Optional[float]],
                   period: str = "",
                   delivery_pct: Optional[float] = None,
                   delivery_mean_pct: Optional[float] = None,
                   volume_ratio: Optional[float] = None) -> Metric:
    """Participation Heat — EquiSense original crowding proxy.

    Volume surge × recent price extension. High values = late-crowd conditions;
    entries here have historically worse short-horizon distributions (hypothesis
    HYP-007, testable against stored history).

    DELIVERY REFINEMENT (Wave S). The original construction could not tell a
    volume surge that represents real accumulation from one that is pure
    intraday churn, and its own caveat said so: "true crowding needs ownership/
    flow data (delivery %, FII per-stock) not available from the free source."
    Delivery percentage IS available — NSE publishes it daily in the MTO file —
    so when supplied it modulates the score:

      * a surge on LOW delivery versus the stock's own norm is churn, which is
        the crowding case the signal is trying to catch → amplified;
      * a surge on HIGH delivery is stock genuinely changing hands and being
        taken to the demat account, which is a weaker crowding claim → damped.

    The multiplier is bounded to [0.6, 1.5] so a flow input can shade the
    reading but never dominate the price/volume core, and the score is fully
    backward compatible when delivery is absent.
    """
    vsurge = volume_anomaly(volumes).value
    r63 = None
    if len(closes) >= 64 and closes[-64] > 0:
        r63 = (closes[-1] / closes[-64] - 1) * 100
    delivery_mult = 1.0
    # a circuit-locked session reports a delivery RATIO near 100% on almost no
    # shares; suppress the modifier rather than read it as accumulation
    if (volume_ratio is not None
            and volume_ratio < MIN_VOLUME_RATIO_FOR_DELIVERY):
        delivery_pct = None
    if delivery_pct is not None and delivery_mean_pct:
        # ratio < 1 => delivery below this stock's own norm => churn
        ratio = delivery_pct / delivery_mean_pct
        delivery_mult = _clamp(1.0 + (1.0 - ratio), 0.6, 1.5)
    if vsurge is None or r63 is None:
        value = None
        formula = "needs volume + 63d of prices"
    else:
        value = vsurge * max(0.0, r63) / 10 * delivery_mult
        formula = (f"vol surge {vsurge:.2f}x × max(0, 63d return {r63:+.1f}%) / 10"
                   + (f" × delivery factor {delivery_mult:.2f} "
                      f"(delivery {delivery_pct:.1f}% vs own mean "
                      f"{delivery_mean_pct:.1f}%)"
                      if delivery_pct is not None and delivery_mean_pct else ""))
    return Metric(
        key="crowding_proxy", label="Participation Heat", value=value, unit="score",
        formula=formula,
        inputs={"volume_surge": vsurge, "return_63d_pct": r63,
                "delivery_pct": delivery_pct,
                "delivery_mean_pct": delivery_mean_pct,
                "delivery_multiplier": round(delivery_mult, 3)},
        period=period, family="novel",
        caveat=("Delivery percentage (NSE MTO file) distinguishes a churn-driven "
                "volume surge from genuine accumulation; the multiplier is capped "
                "to ±50% so flow shades the reading without overriding the "
                "price/volume core."
                if delivery_pct is not None and delivery_mean_pct else
                "Volume-only: no delivery data supplied for this name, so a surge "
                "driven by intraday churn is indistinguishable from real "
                "accumulation. Ingest the NSE MTO file to close that gap."))


# -------------------------------------------------------- institutional flow

def institutional_flow(deals: Sequence[dict], adv_cr: Optional[float] = None,
                       period: str = "") -> Metric:
    """Net disclosed institutional activity from NSE bulk/block deals.

    SEBI requires disclosure of large trades with the counterparty NAMED, which
    is the closest free data comes to observing institutional intent.

    Two things make the naive reading wrong, and both are handled here:

    1. **Gross activity is nearly meaningless.** A bulk deal is very often one
       fund selling to another, so the same volume can appear on both sides of
       the tape. Only the NET (buy value − sell value) carries a directional
       claim.
    2. **Size is only meaningful relative to liquidity.** ₹50 crore of net
       buying is enormous in a small cap and noise in Reliance, so the net is
       expressed in DAYS OF AVERAGE TRADED VALUE rather than in rupees.

    This is a descriptive flow measure, not a validated signal — it has no
    registered hypothesis and no base-rate table, and says so.
    """
    if not deals:
        return Metric(key="institutional_flow", label="Net Institutional Flow",
                      value=None, unit="days of ADV",
                      formula="no disclosed bulk/block deals in the window",
                      inputs={"deals": 0}, period=period, family="novel",
                      caveat="Absence of disclosure is not absence of activity — "
                             "only trades above the disclosure threshold appear.")
    buy = sum(d.get("value") or 0.0 for d in deals if d.get("side") == "buy")
    sell = sum(d.get("value") or 0.0 for d in deals if d.get("side") == "sell")
    net_cr = (buy - sell) / 1e7
    gross_cr = (buy + sell) / 1e7
    value = None if not adv_cr else net_cr / adv_cr
    counterparties = {d.get("client", "") for d in deals if d.get("client")}
    return Metric(
        key="institutional_flow", label="Net Institutional Flow",
        value=None if value is None else round(value, 3), unit="days of ADV",
        formula=(f"(buy ₹{buy / 1e7:,.1f}cr − sell ₹{sell / 1e7:,.1f}cr) "
                 f"= net ₹{net_cr:+,.1f}cr"
                 + (f" / ADV ₹{adv_cr:,.1f}cr = {value:+.2f} days"
                    if value is not None else " (no ADV reference)")),
        inputs={"deals": len(deals), "buy_value_cr": round(buy / 1e7, 2),
                "sell_value_cr": round(sell / 1e7, 2),
                "net_value_cr": round(net_cr, 2),
                "gross_value_cr": round(gross_cr, 2),
                "net_to_gross_ratio": (round(net_cr / gross_cr, 3)
                                       if gross_cr else None),
                "distinct_counterparties": len(counterparties),
                "adv_cr": adv_cr},
        period=period, family="novel",
        caveat=("Disclosed bulk/block deals only, so this is a floor on activity, "
                "not a measure of it. A net-to-gross ratio near zero means funds "
                "were trading with each other rather than accumulating or "
                "distributing — high gross with no net is the common case and is "
                "NOT a directional signal. No registered hypothesis backs this "
                "yet: it is measured so it can be tested, not asserted."))
