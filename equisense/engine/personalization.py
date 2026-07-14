"""Personalization engine (PROJECT_DRAFT §12).

Two deterministic functions make personalization structural, not cosmetic
(Commitment 2.2):

1. attention_score() — a weighted function of the investor profile against
   each company's computed attributes, driving watchlist/dashboard ordering.
2. card_order() — the lens system (§18.3): the same company cards, reordered
   by profile. Two different profiles MUST produce visibly different orders
   (the §12.2 acceptance test — covered by a unit test).

The ranking itself is rules-based; the LLM only ever narrates it (§13.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InvestorProfile:
    name: str = "default"
    horizon: str = "long"                  # short | medium | long
    horizon_target_year: Optional[int] = None
    risk_tolerance: str = "moderate"       # conservative | moderate | aggressive
    style: float = 50.0                    # 0 = deep value … 100 = pure growth (spectrum, §12.1)
    dividend_preference: float = 20.0      # 0 = indifferent … 100 = primary objective
    quality_emphasis: float = 60.0         # 0 … 100, weight on quality/governance signals
    sector_preferences: list[str] = field(default_factory=list)
    sector_exclusions: list[str] = field(default_factory=list)
    max_position_pct: float = 10.0
    max_sector_pct: float = 30.0
    max_drawdown_pct: float = 25.0
    preferred_lens: str = "balanced"       # balanced | quality_first | growth_first | income_first | value_first
    rules: list[str] = field(default_factory=list)  # explicit investment rules (checked in Phase 1)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "name", "horizon", "horizon_target_year", "risk_tolerance", "style",
            "dividend_preference", "quality_emphasis", "sector_preferences",
            "sector_exclusions", "max_position_pct", "max_sector_pct",
            "max_drawdown_pct", "preferred_lens", "rules")}


@dataclass
class CompanySignals:
    """Engine-computed attributes a profile is scored against.
    All values come from the deterministic engines — never the LLM."""
    sector: str = ""
    f_score: Optional[float] = None          # 0–9
    z_zone: Optional[str] = None             # safe | grey | distress
    roic_pct: Optional[float] = None
    revenue_cagr_pct: Optional[float] = None
    dividend_yield_pct: Optional[float] = None
    pe: Optional[float] = None
    pb: Optional[float] = None
    debt_to_equity: Optional[float] = None
    implied_growth_gap_pct: Optional[float] = None  # implied growth − historical CAGR


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def attention_score(profile: InvestorProfile, sig: CompanySignals) -> dict:
    """Deterministic 0–100 priority score with a full component breakdown
    (the breakdown IS the explainability — §19.1 layer 2)."""
    components: dict[str, float] = {}

    # Quality (F-score 0–9 → 0–1, Z zone bonus/penalty)
    q = 0.5
    if sig.f_score is not None:
        q = sig.f_score / 9.0
    if sig.z_zone == "distress":
        q -= 0.35
    elif sig.z_zone == "grey":
        q -= 0.1
    components["quality"] = _clamp(q)

    # Growth (revenue CAGR, 0% → 0, 25%+ → 1)
    g = 0.4
    if sig.revenue_cagr_pct is not None:
        g = _clamp(sig.revenue_cagr_pct / 25.0)
    components["growth"] = g

    # Value (cheapness: lower P/E better; 10x → 1, 60x+ → 0)
    v = 0.4
    if sig.pe is not None and sig.pe > 0:
        v = _clamp((60.0 - sig.pe) / 50.0)
    components["value"] = v

    # Income
    i = 0.0
    if sig.dividend_yield_pct is not None:
        i = _clamp(sig.dividend_yield_pct / 4.0)
    components["income"] = i

    # Capital-return efficiency (ROIC 0% → 0, 25%+ → 1)
    r = 0.4
    if sig.roic_pct is not None:
        r = _clamp(sig.roic_pct / 25.0)
    components["reinvestment"] = r

    # Expectation risk: how much more growth the market prices in vs. history.
    # Positive gap = aggressive expectations → lower score.
    e = 0.5
    if sig.implied_growth_gap_pct is not None:
        e = _clamp(0.5 - sig.implied_growth_gap_pct / 20.0)
    components["expectation_risk"] = e

    # Profile → weights (structural: the profile changes the function, not a theme)
    growth_w = profile.style / 100.0                     # style spectrum
    value_w = 1.0 - growth_w
    weights = {
        "quality": 0.8 + 1.2 * (profile.quality_emphasis / 100.0),
        "growth": 0.5 + 1.5 * growth_w,
        "value": 0.5 + 1.5 * value_w,
        "income": 0.1 + 1.9 * (profile.dividend_preference / 100.0),
        "reinvestment": 1.0,
        "expectation_risk": 1.2 if profile.risk_tolerance == "conservative"
        else (0.8 if profile.risk_tolerance == "moderate" else 0.4),
    }

    sector_adj = 0.0
    if sig.sector in profile.sector_preferences:
        sector_adj = 0.05
    if sig.sector in profile.sector_exclusions:
        sector_adj = -1.0  # effectively removes it from priority

    wsum = sum(weights.values())
    raw = sum(components[k] * weights[k] for k in components) / wsum
    score = _clamp(raw + sector_adj) * 100

    return {
        "score": round(score, 1),
        "components": {k: round(v, 3) for k, v in components.items()},
        "weights": {k: round(v, 3) for k, v in weights.items()},
        "sector_adjustment": sector_adj,
        "explanation": ("Weighted average of engine-computed component scores; weights "
                        "derive from your profile (style, quality emphasis, dividend "
                        "preference, risk tolerance). Fully deterministic — no AI in the "
                        "ranking (§13.3)."),
    }


# --------------------------------------------------------------- lens system

CARD_KEYS = [
    "quality_scores",      # F-score, Z-score
    "profitability",       # margins, ROE/DuPont, ROIC
    "growth_trends",       # revenue/profit trajectory
    "valuation",           # reverse DCF, P/E, EV/EBITDA
    "cash_flow_quality",   # accruals, CFO/NI, capex
    "income",              # dividend yield / payout
    "leverage_liquidity",  # D/E, coverage, current ratio
    "efficiency",          # turnover, CCC
    "peer_comparison",
]

_LENS_ORDERS: dict[str, list[str]] = {
    "balanced": ["profitability", "quality_scores", "valuation", "growth_trends",
                 "cash_flow_quality", "peer_comparison", "leverage_liquidity",
                 "efficiency", "income"],
    "quality_first": ["quality_scores", "leverage_liquidity", "cash_flow_quality",
                      "profitability", "peer_comparison", "valuation",
                      "efficiency", "income", "growth_trends"],
    "growth_first": ["growth_trends", "profitability", "valuation",
                     "peer_comparison", "quality_scores", "cash_flow_quality",
                     "efficiency", "leverage_liquidity", "income"],
    "income_first": ["income", "cash_flow_quality", "quality_scores",
                     "leverage_liquidity", "valuation", "profitability",
                     "peer_comparison", "efficiency", "growth_trends"],
    "value_first": ["valuation", "cash_flow_quality", "quality_scores",
                    "peer_comparison", "profitability", "leverage_liquidity",
                    "efficiency", "growth_trends", "income"],
}


def card_order(profile: InvestorProfile) -> list[str]:
    """Card ordering for a company page under this profile's lens (§18.3).
    Falls back from an explicit lens choice to one inferred from the profile."""
    lens = profile.preferred_lens
    if lens not in _LENS_ORDERS or lens == "balanced":
        # infer from profile when the user hasn't picked a specific lens
        if profile.dividend_preference >= 70:
            lens = "income_first"
        elif profile.quality_emphasis >= 80:
            lens = "quality_first"
        elif profile.style >= 75:
            lens = "growth_first"
        elif profile.style <= 25:
            lens = "value_first"
        elif profile.preferred_lens == "balanced":
            lens = "balanced"
    return list(_LENS_ORDERS[lens])
