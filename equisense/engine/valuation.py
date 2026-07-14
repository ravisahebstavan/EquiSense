"""Valuation engine: WACC estimation and reverse DCF (PROJECT_DRAFT §10.3, §10.5).

The reverse DCF deliberately does NOT produce a fair value (Commitment 2.4).
It solves backward from the current market price: what near-term growth rate
would have to be true to justify today's price, under stated assumptions?
Every assumption is exposed and user-adjustable (§19.2).
"""
from __future__ import annotations

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


def compute_wacc(s: StatementData, price: float, a: WaccAssumptions) -> Metric:
    """WACC = E/V·Ke + D/V·Kd·(1−t), with Ke from CAPM and Kd from actual
    interest expense over total debt unless overridden."""
    ke = a.risk_free_rate + a.beta * a.equity_risk_premium
    kd = a.cost_of_debt
    if kd is None:
        if s.interest_expense is not None and s.total_debt not in (None, 0):
            kd = s.interest_expense / s.total_debt
        else:
            kd = a.risk_free_rate + 0.02  # stated fallback: rf + 200bps spread
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
        inputs={"market_value_equity": mve, "total_debt": debt,
                "cost_of_equity": ke, "cost_of_debt": kd, **a.to_dict()},
        period=s.period, family="valuation",
        caveat="WACC is assumption-sensitive. Every input here is an estimate you "
               "can and should adjust (§10.3).")


def _pv_of_fcf(fcf0: float, g: float, wacc: float, horizon: int, g_term: float) -> float:
    """PV of FCF growing at g for `horizon` years, then a Gordon terminal at g_term."""
    pv = 0.0
    fcf = fcf0
    for t in range(1, horizon + 1):
        fcf = fcf * (1 + g)
        pv += fcf / (1 + wacc) ** t
    terminal = fcf * (1 + g_term) / (wacc - g_term)
    pv += terminal / (1 + wacc) ** horizon
    return pv


def reverse_dcf(s: StatementData, price: float,
                a: Optional[ReverseDcfAssumptions] = None) -> dict:
    """Solve for the constant FCF growth rate over the horizon that makes the
    DCF equal today's enterprise value. Returns the implied growth Metric plus
    the WACC metric and all assumptions (for the editable-assumptions UI)."""
    a = a or ReverseDcfAssumptions()
    wacc_metric = compute_wacc(s, price, a.wacc)
    fcf0 = None
    if s.cfo is not None and s.capex is not None:
        fcf0 = s.cfo - s.capex
    mve = None if s.shares_outstanding is None else price * s.shares_outstanding
    ev = None
    if mve is not None and s.total_debt is not None and s.cash is not None:
        ev = mve + s.total_debt - s.cash

    implied = Metric(key="implied_growth", label="Market-Implied FCF Growth",
                     value=None, unit="%", formula="", inputs={}, period=s.period,
                     family="valuation")
    if fcf0 is None or fcf0 <= 0 or ev is None or ev <= 0 or wacc_metric.value is None:
        implied.caveat = ("Not computable: requires positive base free cash flow, "
                          "a positive enterprise value and a WACC estimate.")
        return {"implied_growth": implied, "wacc": wacc_metric,
                "assumptions": a.to_dict(),
                "enterprise_value": ev, "base_fcf": fcf0}

    wacc = wacc_metric.value / 100
    # Bisection on g in (−0.5, wacc-adjacent upper bound). PV is monotonically
    # increasing in g, so bisection is safe.
    lo, hi = -0.50, min(a.terminal_growth + 0.60, wacc + 0.55)
    if _pv_of_fcf(fcf0, hi, wacc, a.horizon_years, a.terminal_growth) < ev:
        implied.value = None
        implied.caveat = ("Even implausibly high near-term growth cannot justify the "
                          "current price under these assumptions — the market is pricing "
                          "something outside this model (or assumptions need revisiting).")
        return {"implied_growth": implied, "wacc": wacc_metric,
                "assumptions": a.to_dict(), "enterprise_value": ev, "base_fcf": fcf0}
    for _ in range(100):
        mid = (lo + hi) / 2
        if _pv_of_fcf(fcf0, mid, wacc, a.horizon_years, a.terminal_growth) < ev:
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
                      "terminal_growth": a.terminal_growth}
    implied.caveat = ("This is NOT a forecast. It answers: what FCF growth is the "
                      "market currently pricing in? Compare it against the company's "
                      "own history and peers to judge whether expectations look "
                      "conservative or aggressive.")
    return {"implied_growth": implied, "wacc": wacc_metric,
            "assumptions": a.to_dict(), "enterprise_value": ev, "base_fcf": fcf0}


def historical_fcf_cagr(statements: list[StatementData]) -> Optional[Metric]:
    """FCF CAGR over the available history — the comparison anchor for the
    implied-growth output (§10.5)."""
    pts = [(s.fiscal_year, s.cfo - s.capex) for s in statements
           if s.cfo is not None and s.capex is not None]
    if len(pts) < 2:
        return None
    pts.sort()
    (y0, f0), (y1, f1) = pts[0], pts[-1]
    if f0 <= 0 or f1 <= 0 or y1 == y0:
        return Metric(key="fcf_cagr", label="Historical FCF CAGR", value=None,
                      unit="%", formula="Not computable with non-positive endpoint FCF",
                      inputs={"fcf_start": f0, "fcf_end": f1},
                      period=f"FY{y0}–FY{y1}", family="valuation")
    cagr = (f1 / f0) ** (1 / (y1 - y0)) - 1
    return Metric(
        key="fcf_cagr", label="Historical FCF CAGR", value=cagr * 100, unit="%",
        formula=f"({fmt(f1)} / {fmt(f0)})^(1/{y1 - y0}) − 1",
        inputs={"fcf_start": f0, "fcf_end": f1, "years": y1 - y0},
        period=f"FY{y0}–FY{y1}", family="valuation")
