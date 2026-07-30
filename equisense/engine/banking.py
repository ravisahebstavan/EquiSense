"""Banking / financial-sector analysis engine.

WHY THIS EXISTS
---------------
Eleven of the NIFTY-50 are financials, and the platform previously skipped them
entirely: `ingest_fundamentals` returned early for `is_financial`, so 22% of the
universe had NO fundamental analysis at all. The reasoning was sound — a bank's
income statement has no gross profit, no EBITDA, no inventory and no capex, so
the industrial ratio engine produces either nulls or nonsense — but the
conclusion was wrong. The answer to "the industrial model does not fit" is a
model that does, not blindness.

WHAT A BANK ACTUALLY IS
-----------------------
A bank is a leveraged spread business. It borrows (deposits, wholesale funding),
lends, and earns the difference; equity is thin by design and the leverage IS
the business model rather than a warning sign. That inverts several industrial
intuitions:

  * Debt/equity of 8-12x is NORMAL, not distress. Altman Z is meaningless here
    and is deliberately not computed.
  * ROA around 1.5-2% is EXCELLENT (an industrial at 2% would be failing),
    because ROE = ROA x leverage and leverage does the multiplying.
  * "Revenue" is not comparable to an industrial's: interest income is a gross
    figure against which interest expense is the direct cost.

WHAT IS AND IS NOT COMPUTABLE FROM FREE DATA
--------------------------------------------
Derivable from what Yahoo serves: net interest margin (on assets), spread, cost
of funds, yield on assets, cost-to-income, ROA, ROE, leverage, and the two-factor
DuPont that reconciles them.

NOT derivable, and these are the three that matter most for a bank's actual risk:
  * ASSET QUALITY  — GNPA / NNPA / provision coverage / slippage
  * CAPITAL        — CAR, CET1, and the regulatory buffer above the minimum
  * FUNDING MIX    — CASA ratio, deposit concentration, LCR

Their absence is stated on every output rather than papered over, and
`bank_quality_score` REFUSES to emit a composite because a bank quality score
without asset quality is not a weak score, it is a misleading one. That refusal
is the honest position and it is the point of this module's design.
"""
from __future__ import annotations

from typing import Optional

from .types import Metric, StatementData, fmt, safe_div

BANK_DATA_GAPS = [
    "asset quality (GNPA / NNPA / provision coverage / slippage)",
    "capital adequacy (CAR, CET1, buffer over regulatory minimum)",
    "funding mix (CASA ratio, deposit concentration, LCR)",
]

BANK_CAVEAT = (
    "Financial-sector model. Free sources do not publish asset quality, capital "
    "adequacy or funding mix, which are the three largest drivers of a bank's "
    "real risk — every figure here describes profitability and leverage ONLY, "
    "and a bank can post excellent spreads right up until its loan book turns."
)


def _avg(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None:
        return None
    return curr if prev is None else (curr + prev) / 2.0


def is_bank_analyzable(s: StatementData) -> bool:
    """Enough of a spread business disclosed to say anything useful."""
    return (s.total_assets is not None
            and (s.net_interest_income is not None
                 or (s.interest_income is not None and s.interest_expense is not None)))


def net_interest_income(s: StatementData) -> Optional[float]:
    if s.net_interest_income is not None:
        return s.net_interest_income
    if s.interest_income is not None and s.interest_expense is not None:
        return s.interest_income - s.interest_expense
    return None


def banking_ratios(s: StatementData,
                   prev: Optional[StatementData] = None) -> list[Metric]:
    """The bank-appropriate ratio family. Averages balance-sheet denominators
    when the prior period is available, exactly as the industrial engine does."""
    out: list[Metric] = []
    ta = _avg(s.total_assets, prev.total_assets if prev else None)
    eq = _avg(s.total_equity, prev.total_equity if prev else None)
    averaged = prev is not None
    denom = "average" if averaged else "closing"
    nii = net_interest_income(s)

    # --- net interest margin -------------------------------------------------
    nim = safe_div(nii, ta)
    out.append(Metric(
        key="net_interest_margin", label="Net Interest Margin (on assets)",
        value=None if nim is None else nim * 100, unit="%",
        formula=f"Net interest income {fmt(nii)} / {denom} total assets {fmt(ta)}",
        inputs={"net_interest_income": nii, "total_assets": ta, "denominator": denom},
        period=s.period, family="banking",
        caveat=("Reported NIM uses average EARNING assets; total assets is the only "
                "denominator free data supports, so this reads slightly LOWER than "
                "the bank's own disclosed NIM. Compare it across banks and across "
                "years on this consistent basis, not against a headline figure.")))

    # --- yield, cost of funds, spread ---------------------------------------
    ya = safe_div(s.interest_income, ta)
    out.append(Metric(
        key="yield_on_assets", label="Yield on Assets",
        value=None if ya is None else ya * 100, unit="%",
        formula=f"Interest income {fmt(s.interest_income)} / {denom} assets {fmt(ta)}",
        inputs={"interest_income": s.interest_income, "total_assets": ta,
                "denominator": denom},
        period=s.period, family="banking"))

    liabilities = None
    if s.total_assets is not None and s.total_equity is not None:
        liabilities = s.total_assets - s.total_equity
    lia = _avg(liabilities,
               (prev.total_assets - prev.total_equity)
               if prev and prev.total_assets is not None and prev.total_equity is not None
               else None)
    cof = safe_div(s.interest_expense, lia)
    out.append(Metric(
        key="cost_of_funds", label="Cost of Funds",
        value=None if cof is None else cof * 100, unit="%",
        formula=f"Interest expense {fmt(s.interest_expense)} / {denom} liabilities {fmt(lia)}",
        inputs={"interest_expense": s.interest_expense, "total_liabilities": lia},
        period=s.period, family="banking",
        caveat="Denominator is ALL liabilities, not just interest-bearing funding, "
               "so this is a conservative (slightly low) cost-of-funds estimate."))

    spread = None if (ya is None or cof is None) else (ya - cof) * 100
    out.append(Metric(
        key="interest_spread", label="Interest Spread",
        value=spread, unit="pp",
        formula=f"Yield on assets {fmt(None if ya is None else ya * 100)}% − "
                f"cost of funds {fmt(None if cof is None else cof * 100)}%",
        inputs={"yield_on_assets_pct": None if ya is None else ya * 100,
                "cost_of_funds_pct": None if cof is None else cof * 100},
        period=s.period, family="banking",
        caveat="The spread IS the business. A spread compressing while assets grow "
               "is a bank buying volume with price."))

    # --- efficiency ----------------------------------------------------------
    # Yahoo's "Total Revenue" for a bank is net revenue (NII + other income),
    # so operating cost is backed out from the reported operating profit.
    other_income = None
    if s.revenue is not None and nii is not None:
        other_income = s.revenue - nii
    opex = None
    if s.revenue is not None and s.ebit is not None:
        opex = s.revenue - s.ebit
    cti = safe_div(opex, s.revenue)
    out.append(Metric(
        key="cost_to_income", label="Cost / Income",
        value=None if cti is None else cti * 100, unit="%",
        formula=f"Operating expense {fmt(opex)} / net revenue {fmt(s.revenue)}",
        inputs={"operating_expense": opex, "net_revenue": s.revenue,
                "other_income": other_income},
        period=s.period, family="banking",
        caveat="Lower is better; Indian private banks typically run 40-50%. "
               "Not computable when operating profit is undisclosed."))

    # --- returns and the leverage that produces them -------------------------
    roa = safe_div(s.net_income, ta)
    out.append(Metric(
        key="bank_roa", label="Return on Assets",
        value=None if roa is None else roa * 100, unit="%",
        formula=f"Net income {fmt(s.net_income)} / {denom} assets {fmt(ta)}",
        inputs={"net_income": s.net_income, "total_assets": ta,
                "denominator": denom},
        period=s.period, family="banking",
        caveat="THE headline bank profitability measure. ~1.5-2% is strong; an "
               "industrial business at 2% would be failing. The difference is "
               "leverage, shown below."))

    roe = safe_div(s.net_income, eq)
    out.append(Metric(
        key="bank_roe", label="Return on Equity",
        value=None if roe is None else roe * 100, unit="%",
        formula=f"Net income {fmt(s.net_income)} / {denom} equity {fmt(eq)}",
        inputs={"net_income": s.net_income, "total_equity": eq,
                "denominator": denom},
        period=s.period, family="banking"))

    lev = safe_div(ta, eq)
    out.append(Metric(
        key="equity_multiplier", label="Leverage (assets / equity)",
        value=lev, unit="x",
        formula=f"{denom.capitalize()} assets {fmt(ta)} / {denom} equity {fmt(eq)}",
        inputs={"total_assets": ta, "total_equity": eq, "denominator": denom},
        period=s.period, family="banking",
        caveat=("For a bank this is the business model, not a distress signal: "
                "8-12x is normal. It is also the amplifier — at 10x leverage a 1% "
                "loss on assets erases 10% of equity, which is why asset quality "
                "(unavailable here) dominates a bank's real risk.")))

    # ROE = ROA x leverage, the two-factor DuPont that makes the trade explicit
    if roa is not None and lev is not None:
        out.append(Metric(
            key="bank_dupont", label="DuPont · ROA × Leverage",
            value=roa * lev * 100, unit="%",
            formula=f"ROA {roa * 100:.2f}% × leverage {lev:.2f}x",
            inputs={"roa_pct": roa * 100, "leverage_x": lev},
            period=s.period, family="banking",
            caveat="Reconciles exactly to ROE. Two banks with identical ROE can be "
                   "entirely different businesses: one earning it on assets, the "
                   "other borrowing it. This decomposition says which."))

    eff_tax = safe_div(s.tax_expense, s.pbt)
    out.append(Metric(
        key="bank_effective_tax", label="Effective Tax Rate",
        value=None if eff_tax is None else eff_tax * 100, unit="%",
        formula=f"Tax {fmt(s.tax_expense)} / pre-tax profit {fmt(s.pbt)}",
        inputs={"tax_expense": s.tax_expense, "pbt": s.pbt},
        period=s.period, family="banking"))
    return out


def bank_quality_score(stmts: list[StatementData]) -> Metric:
    """DELIBERATELY REFUSES to produce a composite quality score.

    Every input this platform can obtain for a bank describes profitability and
    leverage. None of them describe the loan book. A bank with a deteriorating
    book posts strong margins, strong ROA and strong ROE right up to the moment
    provisions land — that is the ordinary sequence of events, not an edge case,
    and it is exactly what a spread-and-leverage composite would miss.

    Emitting a number here would therefore be worse than emitting nothing: it
    would look like the Piotroski/Altman scores the industrial companies carry
    and invite a comparison that the underlying data cannot support. So this
    returns value=None and says why — which is a finding, not a gap.
    """
    trend = {}
    if stmts:
        for s in stmts[-4:]:
            nii = net_interest_income(s)
            trend[s.period] = {
                "nim_pct": (round(nii / s.total_assets * 100, 2)
                            if nii is not None and s.total_assets else None),
                "roa_pct": (round(s.net_income / s.total_assets * 100, 2)
                            if s.net_income is not None and s.total_assets else None),
                "leverage_x": (round(s.total_assets / s.total_equity, 2)
                               if s.total_assets and s.total_equity else None),
            }
    return Metric(
        key="bank_quality_score", label="Bank Quality Score",
        value=None, unit="score",
        formula="not computed — see caveat",
        inputs={"missing_for_a_defensible_score": BANK_DATA_GAPS,
                "profitability_trend": trend},
        period=stmts[-1].period if stmts else "", family="banking",
        caveat=("NOT COMPUTED BY DESIGN. A bank quality composite built only from "
                "margin, leverage and returns would rate a bank with a rotting loan "
                "book as high quality, because asset-quality deterioration shows up "
                "in provisions AFTER it shows up in nothing else. The three inputs "
                "that would make a score defensible — " + "; ".join(BANK_DATA_GAPS)
                + " — are not in any free source. The profitability trend above is "
                "shown instead so the direction is visible without a fabricated "
                "verdict."))


def bank_summary(stmts: list[StatementData]) -> dict:
    """Everything the banking engine can say about one financial-sector name."""
    if not stmts:
        return {"analyzable": False, "reason": "no filings"}
    curr = stmts[-1]
    prev = stmts[-2] if len(stmts) >= 2 else None
    if not is_bank_analyzable(curr):
        return {"analyzable": False, "period": curr.period,
                "reason": ("filing lacks total assets and any interest-income "
                           "disclosure — nothing bank-specific is derivable"),
                "data_gaps": BANK_DATA_GAPS}
    metrics = banking_ratios(curr, prev)
    return {
        "analyzable": True,
        "period": curr.period,
        "metrics": [m.to_dict() for m in metrics],
        "quality": bank_quality_score(stmts).to_dict(),
        "data_gaps": BANK_DATA_GAPS,
        "model_note": BANK_CAVEAT,
        "not_applicable": {
            "altman_z": ("calibrated on industrial balance sheets where high "
                         "leverage signals distress; for a bank leverage is the "
                         "business model, so the score is meaningless"),
            "piotroski_f": ("half its signals (gross margin, asset turnover, "
                            "current ratio) have no meaning for a bank"),
            "reverse_dcf": ("free cash flow is not defined for a bank — lending "
                            "growth consumes 'cash' that is the asset being built; "
                            "bank valuation runs on residual income / P/B vs ROE"),
        },
    }
