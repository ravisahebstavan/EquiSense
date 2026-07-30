"""Valuation engine: WACC estimation and reverse DCF (PROJECT_DRAFT §10.3, §10.5).

The reverse DCF deliberately does NOT produce a fair value (Commitment 2.4).
It solves backward from the current market price: what near-term growth rate
would have to be true to justify today's price, under stated assumptions?
Every assumption is exposed and user-adjustable (§19.2).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .types import Metric, StatementData, fmt


@dataclass
class WaccAssumptions:
    """CAPM-based WACC inputs. Defaults are stated, inspectable starting points
    for the Indian market — not hidden truths. All user-adjustable."""
    risk_free_rate: float = 0.070      # ~10-year G-sec yield
    equity_risk_premium: float = 0.065
    beta: float = 1.0
    tax_rate: float = 0.2517           # 22% + surcharge/cess (new regime)
    cost_of_debt: Optional[float] = None  # derived from interest/debt when None

    def to_dict(self) -> dict:
        return {
            "risk_free_rate": self.risk_free_rate,
            "equity_risk_premium": self.equity_risk_premium,
            "beta": self.beta,
            "tax_rate": self.tax_rate,
            "cost_of_debt": self.cost_of_debt,
        }


@dataclass
class ReverseDcfAssumptions:
    """Structural assumptions for the implied-growth solve. User-adjustable."""
    horizon_years: int = 10
    terminal_growth: float = 0.04      # long-run nominal GDP-ish anchor
    wacc: WaccAssumptions = field(default_factory=WaccAssumptions)

    def to_dict(self) -> dict:
        return {"horizon_years": self.horizon_years,
                "terminal_growth": self.terminal_growth,
                "wacc": self.wacc.to_dict()}


# ------------------------------------------------------------ beta estimation
# Typical cross-sectional dispersion of large-cap betas — the Vasicek prior's
# variance. Stated as an assumption because it is one.
BETA_PRIOR = 1.0
BETA_PRIOR_SD = 0.30
BETA_MIN_OBS = 52          # ≥1 year of weekly observations
BETA_WEEKLY_STRIDE = 5     # sample weekly from daily closes
MAX_COST_OF_DEBT = 0.25    # sanity ceiling on a derived Kd


def estimate_beta(stock_closes: list[float], index_closes: list[float],
                  years: float = 2.0, period: str = "") -> Metric:
    """Equity beta by OLS of stock excess returns on index returns, then shrunk
    toward 1.0 by Vasicek (1973).

    Why this exists: `WaccAssumptions.beta` defaulted to a hardcoded 1.0 even
    though the platform stores ten years of daily closes for every name and the
    NIFTY series alongside it. Beta drives Ke, Ke drives WACC, and WACC is the
    discount rate the entire reverse DCF pivots on — so assuming 1.0 for a
    defensive FMCG name and for a high-beta financial alike quietly injected a
    large, one-directional error into the headline valuation output.

    Design choices, each standard practice:
      * WEEKLY returns, not daily — daily returns for Indian mid-caps are
        contaminated by non-synchronous trading, which biases OLS beta downward.
      * VASICEK shrinkage rather than Blume's fixed 0.67/0.33 — the shrinkage
        weight is set by this stock's own estimation error, so a precisely
        measured beta is shrunk less than a noisy one. Blume applies the same
        haircut regardless of estimate quality.
    """
    n_needed = int(years * 52) * BETA_WEEKLY_STRIDE
    a_px = stock_closes[-n_needed:] if n_needed else stock_closes
    b_px = index_closes[-n_needed:] if n_needed else index_closes
    m = min(len(a_px), len(b_px))
    a_px, b_px = a_px[-m:], b_px[-m:]

    def weekly(px: list[float]) -> list[float]:
        pts = px[::BETA_WEEKLY_STRIDE]
        return [pts[i] / pts[i - 1] - 1 for i in range(1, len(pts))
                if pts[i - 1] and pts[i - 1] > 0]

    rs, ri = weekly(a_px), weekly(b_px)
    k = min(len(rs), len(ri))
    rs, ri = rs[-k:], ri[-k:]
    if k < BETA_MIN_OBS:
        return Metric(
            key="beta", label="Equity Beta", value=None, unit="x",
            formula=f"needs ≥{BETA_MIN_OBS} weekly observations, have {k}",
            inputs={"observations": k, "fallback_used": BETA_PRIOR},
            period=period, family="valuation",
            caveat=(f"Insufficient overlapping history to estimate beta; callers "
                    f"should fall back to the stated prior of {BETA_PRIOR:.2f} and "
                    "show it as an assumption."))
    mi = sum(ri) / k
    ms = sum(rs) / k
    sxx = sum((x - mi) ** 2 for x in ri)
    if sxx <= 0:
        return Metric(key="beta", label="Equity Beta", value=None, unit="x",
                      formula="index returns have zero variance", inputs={},
                      period=period, family="valuation")
    raw = sum((x - mi) * (y - ms) for x, y in zip(ri, rs)) / sxx
    alpha = ms - raw * mi
    resid = [y - (alpha + raw * x) for x, y in zip(ri, rs)]
    ss_res = sum(e * e for e in resid)
    ss_tot = sum((y - ms) ** 2 for y in rs)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    # standard error of the slope
    se_beta = math.sqrt((ss_res / max(k - 2, 1)) / sxx) if k > 2 else None

    if se_beta and se_beta > 0:
        w = (BETA_PRIOR_SD ** 2) / (BETA_PRIOR_SD ** 2 + se_beta ** 2)
        adj = w * raw + (1 - w) * BETA_PRIOR
    else:
        w, adj = 1.0, raw
    return Metric(
        key="beta", label="Equity Beta (Vasicek-adjusted)", value=adj, unit="x",
        formula=(f"OLS β {raw:.3f} on {k} weekly returns (R²={r2:.2f}, "
                 f"SE={se_beta:.3f}) shrunk toward {BETA_PRIOR:.2f} with weight "
                 f"{w:.2f} → {adj:.3f}" if r2 is not None and se_beta is not None
                 else f"OLS β {raw:.3f} on {k} weekly returns"),
        inputs={"beta_raw_ols": round(raw, 4),
                "beta_adjusted": round(adj, 4),
                "shrinkage_weight_on_estimate": round(w, 4),
                "standard_error": None if se_beta is None else round(se_beta, 4),
                "r_squared": None if r2 is None else round(r2, 4),
                "observations_weekly": k,
                "prior_mean": BETA_PRIOR, "prior_sd": BETA_PRIOR_SD,
                "return_frequency": "weekly (5-day stride)"},
        period=period, family="valuation",
        caveat=("Beta is an estimate with real standard error, shrunk toward 1.0 by "
                "Vasicek (1973) in proportion to that error. Weekly returns are "
                "used because daily returns understate beta for less liquid names "
                "(non-synchronous trading). R² tells you how much of this stock's "
                "variance the index explains at all — a low R² means beta is a "
                "weak description of its risk, however precisely measured."))


def compute_wacc(s: StatementData, price: float, a: WaccAssumptions) -> Metric:
    """WACC = E/V·Ke + D/V·Kd·(1−t), with Ke from CAPM and Kd from actual
    interest expense over total debt unless overridden."""
    ke = a.risk_free_rate + a.beta * a.equity_risk_premium
    kd = a.cost_of_debt
    kd_note = None
    if kd is None:
        derived = None
        if s.interest_expense is not None and s.total_debt not in (None, 0):
            derived = s.interest_expense / s.total_debt
        if derived is None:
            kd = a.risk_free_rate + 0.02  # stated fallback: rf + 200bps spread
            kd_note = ("Cost of debt not derivable (no interest expense or no debt); "
                       "using risk-free + 200bps as a stated assumption.")
        elif not (0.0 <= derived <= MAX_COST_OF_DEBT):
            # interest expense / period-end debt explodes when debt was repaid
            # during the year, or goes negative on capitalised-interest credits
            kd = a.risk_free_rate + 0.02
            kd_note = (f"Filing-implied cost of debt {derived:.1%} is implausible "
                       f"(interest expense is a full-year flow measured against "
                       f"period-END debt, which explodes if debt was repaid during "
                       f"the year); using risk-free + 200bps instead. Raw value "
                       f"retained in inputs.")
        else:
            kd = derived
    mve = None if s.shares_outstanding is None else price * s.shares_outstanding
    debt = s.total_debt or 0.0
    if mve is None or (mve + debt) == 0:
        return Metric(key="wacc", label="WACC", value=None, unit="%",
                      formula="E/V·Ke + D/V·Kd·(1−t)", inputs=a.to_dict(),
                      period=s.period, family="valuation")
    v = mve + debt
    wacc = (mve / v) * ke + (debt / v) * kd * (1 - a.tax_rate)
    return Metric(
        key="wacc", label="WACC (estimated)", value=wacc * 100, unit="%",
        formula=(f"E/V {mve / v:.2f} × Ke {ke * 100:.2f}% + D/V {debt / v:.2f} × "
                 f"Kd {kd * 100:.2f}% × (1 − tax {a.tax_rate * 100:.1f}%). "
                 f"Ke = rf {a.risk_free_rate * 100:.2f}% + β {a.beta:.2f} × ERP {a.equity_risk_premium * 100:.2f}%"),
        # NOTE: a.to_dict() is spread FIRST. It carries cost_of_debt=None whenever
        # the user did not override it, and spreading it last (as this previously
        # did) overwrote the DERIVED kd with None — so the one assumption most
        # likely to be estimated was the one never shown. Derived values win.
        inputs={**a.to_dict(),
                "market_value_equity": mve, "total_debt": debt,
                "cost_of_equity": ke, "cost_of_debt": kd,
                "cost_of_debt_source": ("user override" if a.cost_of_debt is not None
                                        else ("derived from interest/debt" if kd_note is None
                                              else "fallback (rf + 200bps)")),
                "cost_of_debt_raw": (s.interest_expense / s.total_debt
                                     if (s.interest_expense is not None
                                         and s.total_debt not in (None, 0)) else None)},
        period=s.period, family="valuation",
        caveat=("WACC is assumption-sensitive. Every input here is an estimate you "
                "can and should adjust (§10.3)."
                + (f" {kd_note}" if kd_note else "")))


TERMINAL_SPREAD_FLOOR = 0.01   # WACC must exceed terminal growth by ≥100bps


def _pv_of_fcf(fcf0: float, g: float, wacc: float, horizon: int, g_term: float) -> float:
    """PV of FCF growing at g for `horizon` years, then a Gordon terminal at g_term.

    Raises when WACC ≤ g_term: the Gordon denominator (wacc − g_term) goes to
    zero and then negative, which silently produced a NEGATIVE present value and
    hence a confidently-reported nonsense implied growth rate. Verified: at
    terminal_growth = 13% against a WACC of 13.12% the solver returned
    "market-implied growth = −17.9%" with no caveat at all. A Gordon model is
    simply undefined in that region, so the correct behaviour is to refuse.
    """
    if wacc - g_term < TERMINAL_SPREAD_FLOOR:
        raise ValueError(
            f"Gordon terminal value undefined: WACC {wacc:.2%} must exceed terminal "
            f"growth {g_term:.2%} by at least {TERMINAL_SPREAD_FLOOR:.2%}. "
            "A perpetuity growing at or above its discount rate has infinite value.")
    pv = 0.0
    fcf = fcf0
    for t in range(1, horizon + 1):
        fcf = fcf * (1 + g)
        pv += fcf / (1 + wacc) ** t
    terminal = fcf * (1 + g_term) / (wacc - g_term)
    pv += terminal / (1 + wacc) ** horizon
    return pv


NORMALIZATION_YEARS = 3


def normalized_base_fcf(statements: list[StatementData]) -> tuple[Optional[float], dict]:
    """Base FCF for the reverse DCF, averaged over the most recent years.

    A single year's CFO − capex is one of the noisiest inputs in fundamental
    analysis: one lumpy capex cycle or working-capital swing moves it by tens of
    percent, and because the reverse DCF solves for the growth that reconciles
    base FCF to enterprise value, that noise lands directly on the headline
    "market-implied growth" figure and makes it swing year to year for reasons
    that have nothing to do with the market's expectations.

    Returns (value, working) where `working` exposes every year used.
    """
    pts = [(s.fiscal_year, s.cfo - s.capex) for s in statements
           if s.cfo is not None and s.capex is not None]
    if not pts:
        return None, {"years_used": 0}
    pts.sort()
    recent = pts[-NORMALIZATION_YEARS:]
    vals = [v for _y, v in recent]
    avg = sum(vals) / len(vals)
    latest = pts[-1][1]
    return avg, {
        "years_used": len(recent),
        "by_year": {f"FY{y}": round(v, 1) for y, v in recent},
        "normalized_base_fcf": round(avg, 1),
        "latest_year_fcf": round(latest, 1),
        "dispersion_vs_latest_pct": (round((latest / avg - 1) * 100, 1)
                                     if avg not in (0, None) else None),
        "note": (f"Base FCF is the mean of the last {len(recent)} fiscal years, not "
                 "the latest year alone, because a single lumpy capex or "
                 "working-capital year would otherwise dominate the implied-growth "
                 "solve."),
    }


GROWTH_FLOOR = -0.50


def reverse_dcf(s: StatementData, price: float,
                a: Optional[ReverseDcfAssumptions] = None,
                statements: Optional[list[StatementData]] = None) -> dict:
    """Solve for the constant FCF growth rate over the horizon that makes the
    DCF equal today's enterprise value.

    Pass `statements` (the full history, oldest → newest) to use a normalized
    multi-year base FCF instead of the single latest year — strongly preferred,
    because the solve is highly sensitive to the base.

    Every failure mode now returns an explicit, labelled non-answer rather than a
    number: an undefined Gordon region (WACC ≤ terminal growth), a price that no
    plausible growth can justify, and a price so low that it implies decline
    beyond the model's floor. That last case previously returned a silent −50%
    pinned at the bisection boundary.
    """
    a = a or ReverseDcfAssumptions()
    wacc_metric = compute_wacc(s, price, a.wacc)

    if statements:
        fcf0, fcf_working = normalized_base_fcf(statements)
    elif s.cfo is not None and s.capex is not None:
        fcf0 = s.cfo - s.capex
        fcf_working = {"years_used": 1, "normalized_base_fcf": round(fcf0, 1),
                       "note": "Single-period base FCF — pass the full statement "
                               "history to normalize it across years."}
    else:
        fcf0, fcf_working = None, {"years_used": 0}

    mve = None if s.shares_outstanding is None else price * s.shares_outstanding
    ev = None
    if mve is not None and s.total_debt is not None and s.cash is not None:
        ev = mve + s.total_debt - s.cash

    implied = Metric(key="implied_growth", label="Market-Implied FCF Growth",
                     value=None, unit="%", formula="", inputs={}, period=s.period,
                     family="valuation")
    result = {"implied_growth": implied, "wacc": wacc_metric,
              "assumptions": a.to_dict(), "enterprise_value": ev,
              "base_fcf": fcf0, "base_fcf_working": fcf_working}

    if fcf0 is None or fcf0 <= 0 or ev is None or ev <= 0 or wacc_metric.value is None:
        implied.caveat = ("Not computable: requires positive base free cash flow, "
                          "a positive enterprise value and a WACC estimate.")
        return result

    wacc = wacc_metric.value / 100
    # Guard the Gordon region BEFORE solving. Assumptions are user-editable, so
    # this is a reachable state, not a theoretical one.
    if wacc - a.terminal_growth < TERMINAL_SPREAD_FLOOR:
        implied.caveat = (
            f"Not computable: the terminal growth assumption ({a.terminal_growth:.2%}) "
            f"is not at least {TERMINAL_SPREAD_FLOOR:.0%} below the estimated WACC "
            f"({wacc:.2%}). A perpetuity growing at or above its discount rate has "
            "infinite value, so no finite implied growth exists. Lower terminal "
            "growth or revisit the WACC inputs.")
        implied.inputs = {"wacc": wacc, "terminal_growth": a.terminal_growth}
        return result

    def pv(g: float) -> float:
        return _pv_of_fcf(fcf0, g, wacc, a.horizon_years, a.terminal_growth)

    lo, hi = GROWTH_FLOOR, min(a.terminal_growth + 0.60, wacc + 0.55)
    try:
        pv_hi, pv_lo = pv(hi), pv(lo)
    except ValueError as exc:
        implied.caveat = f"Not computable: {exc}"
        return result

    if pv_hi < ev:
        implied.caveat = ("Even implausibly high near-term growth cannot justify the "
                          "current price under these assumptions — the market is pricing "
                          "something outside this model (or assumptions need revisiting).")
        return result
    if pv_lo > ev:
        implied.caveat = (
            f"Not computable: the price implies FCF DECLINE steeper than the model's "
            f"{GROWTH_FLOOR:.0%} floor. Either the market expects severe "
            f"deterioration, or the normalized base FCF ({fmt(fcf0)} cr) is "
            "unrepresentative of the business's run-rate.")
        return result

    for _ in range(200):
        mid = (lo + hi) / 2
        if pv(mid) < ev:
            lo = mid
        else:
            hi = mid
    g = (lo + hi) / 2
    implied.value = g * 100
    implied.formula = (
        f"Solve g such that PV of FCF (base {fmt(fcf0)} cr growing at g for "
        f"{a.horizon_years}y, terminal {a.terminal_growth * 100:.1f}%, discounted at "
        f"WACC {wacc * 100:.2f}%) = EV {fmt(ev)} cr")
    implied.inputs = {"base_fcf": fcf0, "enterprise_value": ev,
                      "wacc": wacc, "horizon_years": a.horizon_years,
                      "terminal_growth": a.terminal_growth,
                      "base_fcf_years_used": fcf_working.get("years_used")}
    implied.caveat = ("This is NOT a forecast. It answers: what FCF growth is the "
                      "market currently pricing in? Compare it against the company's "
                      "own history and peers to judge whether expectations look "
                      "conservative or aggressive.")
    return result


def historical_fcf_cagr(statements: list[StatementData]) -> Optional[Metric]:
    """Delivered FCF growth — the comparison anchor for market-implied growth.

    Fitted by ordinary least squares on ln(FCF) against fiscal year, so the
    trend uses EVERY observation. Endpoint-to-endpoint CAGR, which this replaces,
    depends on exactly two numbers and is therefore maximally fragile: on an
    unchanged 10% trend, perturbing a single endpoint moved the measured CAGR to
    4.6% or 16.4% (verified). Since this figure is one half of the Expectations
    Gap — the platform's headline valuation signal — that fragility propagated
    straight into a decision input.

    R² travels with the estimate: a growth rate fitted through scattered cash
    flows is not the same evidence as one fitted through a clean trend, and the
    consumer needs to see which it has.
    """
    pts = [(s.fiscal_year, s.cfo - s.capex) for s in statements
           if s.cfo is not None and s.capex is not None]
    pts.sort()
    usable = [(y, v) for y, v in pts if v > 0]
    period = f"FY{pts[0][0]}–FY{pts[-1][0]}" if pts else ""
    if len(usable) < 3:
        # fall back to endpoints only when there is genuinely nothing to fit
        if len(usable) == 2 and usable[0][0] != usable[1][0]:
            (y0, f0), (y1, f1) = usable
            cagr = (f1 / f0) ** (1 / (y1 - y0)) - 1
            return Metric(
                key="fcf_cagr", label="Historical FCF Growth (endpoints)",
                value=cagr * 100, unit="%",
                formula=f"({fmt(f1)} / {fmt(f0)})^(1/{y1 - y0}) − 1",
                inputs={"fcf_start": f0, "fcf_end": f1, "years": y1 - y0,
                        "method": "endpoint CAGR", "observations": 2},
                period=period, family="valuation",
                caveat="Only two positive FCF years available, so this is an "
                       "endpoint CAGR and depends entirely on those two numbers. "
                       "Treat as indicative only.")
        return Metric(
            key="fcf_cagr", label="Historical FCF Growth", value=None, unit="%",
            formula="Needs ≥2 fiscal years of positive free cash flow",
            inputs={"positive_fcf_years": len(usable), "total_years": len(pts)},
            period=period, family="valuation",
            caveat="Free cash flow was non-positive in too many years to fit a "
                   "growth rate. That is itself the finding.")

    xs = [float(y) for y, _ in usable]
    ys = [math.log(v) for _, v in usable]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    growth = math.exp(slope) - 1.0
    excluded = len(pts) - n
    caveats = []
    if r2 is not None and r2 < 0.5:
        caveats.append(
            f"Poor fit (R²={r2:.2f}): free cash flow is scattered rather than "
            "trending, so this growth rate is a weak anchor. Read the per-year "
            "figures, not the fitted slope.")
    if excluded:
        caveats.append(f"{excluded} year(s) with non-positive FCF excluded from the "
                       "log-linear fit; they are visible in inputs.")
    return Metric(
        key="fcf_cagr", label="Historical FCF Growth (log-linear)",
        value=growth * 100, unit="%",
        formula=(f"OLS slope of ln(FCF) on fiscal year over {n} observations → "
                 f"e^{slope:.4f} − 1 = {growth * 100:.2f}%/yr (R²={r2:.2f})"
                 if r2 is not None else f"OLS slope over {n} observations"),
        inputs={"method": "log-linear OLS", "observations": n,
                "r_squared": None if r2 is None else round(r2, 4),
                "slope_log_per_year": round(slope, 5),
                "excluded_nonpositive_years": excluded,
                "by_year": {f"FY{y}": round(v, 1) for y, v in pts}},
        period=period, family="valuation",
        caveat=" ".join(caveats) or None)
