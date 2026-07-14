"""Cash-flow quality, distress and quality scoring (PROJECT_DRAFT §10.4, §10.7).

All methodologies here are published and externally verifiable by design —
Altman (1968) Z-score and Piotroski (2000) F-Score — per the explainability
commitment. Proprietary black-box scores were explicitly rejected (§10.7).
"""
from __future__ import annotations

from typing import Optional

from .types import Metric, StatementData, fmt, safe_div

Z_CAVEAT = (
    "Altman Z was calibrated on U.S. manufacturing firms (1968). For Indian "
    "services / asset-light / financial businesses treat it as a directional "
    "flag, not a calibrated probability of distress."
)


def cash_flow_quality(s: StatementData) -> list[Metric]:
    """Accruals ratio, CFO/NI, capex intensity (§10.4)."""
    out = []
    accruals = None
    if s.net_income is not None and s.cfo is not None and s.total_assets not in (None, 0):
        accruals = (s.net_income - s.cfo) / s.total_assets
    out.append(Metric(
        key="accruals_ratio", label="Accruals Ratio",
        value=None if accruals is None else accruals * 100, unit="%",
        formula=f"(Net income {fmt(s.net_income)} − CFO {fmt(s.cfo)}) / Total assets {fmt(s.total_assets)}",
        inputs={"net_income": s.net_income, "cfo": s.cfo, "total_assets": s.total_assets},
        period=s.period, family="cash_flow_quality",
        caveat="Persistently positive accruals (profit not backed by cash) are the "
               "classic earnings-quality warning sign."))
    cfo_ni = safe_div(s.cfo, s.net_income)
    out.append(Metric(
        key="cfo_to_net_income", label="CFO / Net Income", value=cfo_ni, unit="x",
        formula=f"CFO {fmt(s.cfo)} / Net income {fmt(s.net_income)}",
        inputs={"cfo": s.cfo, "net_income": s.net_income},
        period=s.period, family="cash_flow_quality",
        caveat="Sustained values well below 1.0 suggest reported profit is not converting to cash."))
    capex_rev = safe_div(s.capex, s.revenue)
    out.append(Metric(
        key="capex_intensity", label="Capex / Revenue",
        value=None if capex_rev is None else capex_rev * 100, unit="%",
        formula=f"Capex {fmt(s.capex)} / Revenue {fmt(s.revenue)}",
        inputs={"capex": s.capex, "revenue": s.revenue},
        period=s.period, family="cash_flow_quality"))
    capex_dep = safe_div(s.capex, s.depreciation)
    out.append(Metric(
        key="capex_to_depreciation", label="Capex / Depreciation", value=capex_dep, unit="x",
        formula=f"Capex {fmt(s.capex)} / Depreciation {fmt(s.depreciation)}",
        inputs={"capex": s.capex, "depreciation": s.depreciation},
        period=s.period, family="cash_flow_quality",
        caveat="Sustained values below 1.0 can indicate under-investment in the asset base."))
    fcf = None
    if s.cfo is not None and s.capex is not None:
        fcf = s.cfo - s.capex
    out.append(Metric(
        key="free_cash_flow", label="Free Cash Flow", value=fcf, unit="₹ cr",
        formula=f"CFO {fmt(s.cfo)} − Capex {fmt(s.capex)}",
        inputs={"cfo": s.cfo, "capex": s.capex},
        period=s.period, family="cash_flow_quality"))
    return out


def altman_z(s: StatementData, price: Optional[float] = None) -> Metric:
    """Original Altman Z (1968):
    Z = 1.2·(WC/TA) + 1.4·(RE/TA) + 3.3·(EBIT/TA) + 0.6·(MVE/TL) + 1.0·(Sales/TA)
    """
    required = [s.current_assets, s.current_liabilities, s.retained_earnings,
                s.ebit, s.total_assets, s.revenue, s.total_equity]
    mve = None
    if price is not None and s.shares_outstanding is not None:
        mve = price * s.shares_outstanding
    total_liabilities = None
    if s.total_assets is not None and s.total_equity is not None:
        total_liabilities = s.total_assets - s.total_equity
    if any(v is None for v in required) or mve is None or total_liabilities in (None, 0) \
            or s.total_assets in (None, 0):
        return Metric(key="altman_z", label="Altman Z-Score", value=None, unit="score",
                      formula="1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA",
                      inputs={}, period=s.period, family="distress", caveat=Z_CAVEAT)
    wc = s.current_assets - s.current_liabilities
    x1 = wc / s.total_assets
    x2 = s.retained_earnings / s.total_assets
    x3 = s.ebit / s.total_assets
    x4 = mve / total_liabilities
    x5 = s.revenue / s.total_assets
    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    return Metric(
        key="altman_z", label="Altman Z-Score", value=z, unit="score",
        formula=(f"1.2×{x1:.3f} (WC/TA) + 1.4×{x2:.3f} (RE/TA) + 3.3×{x3:.3f} (EBIT/TA) "
                 f"+ 0.6×{x4:.3f} (MVE/TL) + 1.0×{x5:.3f} (Sales/TA)"),
        inputs={"working_capital": wc, "retained_earnings": s.retained_earnings,
                "ebit": s.ebit, "market_value_equity": mve,
                "total_liabilities": total_liabilities, "revenue": s.revenue,
                "total_assets": s.total_assets},
        period=s.period, family="distress", caveat=Z_CAVEAT)


def altman_zone(z: Optional[float]) -> Optional[str]:
    if z is None:
        return None
    if z > 2.99:
        return "safe"
    if z >= 1.81:
        return "grey"
    return "distress"


def piotroski_f(curr: StatementData, prev: StatementData,
                price: Optional[float] = None) -> Metric:
    """Piotroski F-Score (2000): 9 binary fundamental-improvement signals.

    Gross-margin signal falls back to operating margin when gross profit is
    undisclosed (caveated). Equity-offering signal uses change in shares
    outstanding as the proxy.
    """
    signals: dict[str, Optional[bool]] = {}

    roa_c = safe_div(curr.net_income, curr.total_assets)
    roa_p = safe_div(prev.net_income, prev.total_assets)
    signals["roa_positive"] = None if roa_c is None else roa_c > 0
    signals["cfo_positive"] = None if curr.cfo is None else curr.cfo > 0
    signals["roa_improving"] = None if (roa_c is None or roa_p is None) else roa_c > roa_p
    signals["accruals_ok"] = None if (curr.cfo is None or curr.net_income is None) \
        else curr.cfo > curr.net_income

    lev_c = safe_div(curr.total_debt, curr.total_assets)
    lev_p = safe_div(prev.total_debt, prev.total_assets)
    signals["leverage_decreasing"] = None if (lev_c is None or lev_p is None) else lev_c <= lev_p

    cr_c = safe_div(curr.current_assets, curr.current_liabilities)
    cr_p = safe_div(prev.current_assets, prev.current_liabilities)
    signals["liquidity_improving"] = None if (cr_c is None or cr_p is None) else cr_c > cr_p

    signals["no_dilution"] = None if (curr.shares_outstanding is None or prev.shares_outstanding is None) \
        else curr.shares_outstanding <= prev.shares_outstanding * 1.005  # tolerance for ESOP noise

    margin_fallback = False
    gm_c = safe_div(curr.gross_profit, curr.revenue)
    gm_p = safe_div(prev.gross_profit, prev.revenue)
    if gm_c is None or gm_p is None:
        gm_c = safe_div(curr.ebit, curr.revenue)
        gm_p = safe_div(prev.ebit, prev.revenue)
        margin_fallback = True
    signals["margin_improving"] = None if (gm_c is None or gm_p is None) else gm_c > gm_p

    at_c = safe_div(curr.revenue, curr.total_assets)
    at_p = safe_div(prev.revenue, prev.total_assets)
    signals["turnover_improving"] = None if (at_c is None or at_p is None) else at_c > at_p

    available = {k: v for k, v in signals.items() if v is not None}
    score = sum(1 for v in available.values() if v)
    caveats = []
    if margin_fallback:
        caveats.append("Margin signal uses operating margin (gross profit undisclosed).")
    if len(available) < 9:
        caveats.append(f"Only {len(available)}/9 signals computable from available data.")
    return Metric(
        key="piotroski_f", label="Piotroski F-Score", value=float(score), unit="score",
        formula=f"Sum of {len(available)} binary signals: "
                + ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in available.items()),
        inputs={k: (1.0 if v else 0.0) for k, v in available.items()},
        period=curr.period, family="quality",
        caveat=" ".join(caveats) if caveats else None)


def quality_tier(f_score: Optional[float]) -> Optional[str]:
    if f_score is None:
        return None
    if f_score >= 7:
        return "high"
    if f_score >= 4:
        return "medium"
    return "low"
