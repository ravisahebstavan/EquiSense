"""Core value types for the computation engine.

Every number EquiSense shows a user is a Metric: the value plus the formula
and raw inputs that produced it (§6.1, §1, §1). Engines
never return bare floats to the API layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Metric:
    key: str                      # stable identifier, e.g. "roic"
    label: str                    # display name, e.g. "Return on Invested Capital"
    value: Optional[float]        # None when inputs are missing — never silently 0
    unit: str                     # "%", "x", "days", "₹ cr", "₹", "score"
    formula: str                  # human-readable formula with the numbers filled in
    inputs: dict[str, float | str] = field(default_factory=dict)
    period: str = ""              # e.g. "FY2025"
    family: str = ""              # "liquidity" | "leverage" | "profitability" | ...
    caveat: Optional[str] = None  # methodological caveats (e.g. Z-score calibration)

    def rounded(self, digits: int = 2) -> Optional[float]:
        return None if self.value is None else round(self.value, digits)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.rounded(4),
            "unit": self.unit,
            "formula": self.formula,
            "inputs": self.inputs,
            "period": self.period,
            "family": self.family,
            "caveat": self.caveat,
        }


@dataclass
class StatementData:
    """One fiscal period's canonical financial statement figures.

    All monetary values in ₹ crore; shares_outstanding in crore shares.
    Optional fields may be None when the filing doesn't disclose them —
    downstream metrics that need them return value=None rather than guessing.
    """
    period: str                   # "FY2025"
    fiscal_year: int              # 2025
    scope: str = "consolidated"   # "consolidated" | "standalone"

    # Income statement
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    ebitda: Optional[float] = None
    depreciation: Optional[float] = None
    ebit: Optional[float] = None
    interest_expense: Optional[float] = None
    # Banking-specific (financial-sector filings; None for industrials)
    interest_income: Optional[float] = None
    net_interest_income: Optional[float] = None
    pbt: Optional[float] = None
    tax_expense: Optional[float] = None
    net_income: Optional[float] = None

    # Balance sheet
    total_assets: Optional[float] = None
    current_assets: Optional[float] = None
    cash: Optional[float] = None
    inventory: Optional[float] = None
    receivables: Optional[float] = None
    current_liabilities: Optional[float] = None
    payables: Optional[float] = None
    total_debt: Optional[float] = None
    total_equity: Optional[float] = None
    retained_earnings: Optional[float] = None
    shares_outstanding: Optional[float] = None  # crore shares

    # Cash flow statement
    cfo: Optional[float] = None
    capex: Optional[float] = None              # reported as positive outflow
    dividends_paid: Optional[float] = None


def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def fmt(v: Optional[float], digits: int = 1) -> str:
    """Format a number for embedding inside a formula string."""
    if v is None:
        return "n/a"
    return f"{v:,.{digits}f}"
