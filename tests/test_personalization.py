"""The §12.2 acceptance test, encoded: two profiles must produce visibly
different card ordering and different priority rankings for the same data."""
from equisense.engine.personalization import (CARD_KEYS, CompanySignals,
                                              InvestorProfile, attention_score,
                                              card_order)

GROWTH_CO = CompanySignals(sector="IT", f_score=6, z_zone="safe", roic_pct=22,
                           revenue_cagr_pct=20, dividend_yield_pct=0.5, pe=45,
                           pb=10, debt_to_equity=0.1, implied_growth_gap_pct=6)
INCOME_CO = CompanySignals(sector="FMCG", f_score=7, z_zone="safe", roic_pct=15,
                           revenue_cagr_pct=6, dividend_yield_pct=3.5, pe=22,
                           pb=6, debt_to_equity=0.05, implied_growth_gap_pct=-2)

GROWTH_PROFILE = InvestorProfile(name="growth", style=90, dividend_preference=5,
                                 quality_emphasis=40, risk_tolerance="aggressive",
                                 preferred_lens="growth_first")
INCOME_PROFILE = InvestorProfile(name="income", style=15, dividend_preference=90,
                                 quality_emphasis=85, risk_tolerance="conservative",
                                 preferred_lens="income_first")


def test_same_company_two_profiles_different_card_order():
    """§12.2 / §18.3: same underlying cards, provably different ordering."""
    a = card_order(GROWTH_PROFILE)
    b = card_order(INCOME_PROFILE)
    assert a != b
    assert sorted(a) == sorted(b) == sorted(CARD_KEYS)  # reordered, not different data
    assert a[0] == "growth_trends"
    assert b[0] == "income"


def test_profiles_reorder_priority_not_just_theme():
    """The income profile must rank the dividend payer above the grower and
    the growth profile must reverse it — ordering, not decoration."""
    g_growthco = attention_score(GROWTH_PROFILE, GROWTH_CO)["score"]
    g_incomeco = attention_score(GROWTH_PROFILE, INCOME_CO)["score"]
    i_growthco = attention_score(INCOME_PROFILE, GROWTH_CO)["score"]
    i_incomeco = attention_score(INCOME_PROFILE, INCOME_CO)["score"]
    assert g_growthco > g_incomeco
    assert i_incomeco > i_growthco


def test_sector_exclusion_suppresses_priority():
    p = InvestorProfile(sector_exclusions=["IT"])
    assert attention_score(p, GROWTH_CO)["score"] == 0.0


def test_score_breakdown_is_exposed():
    """Explainability layer 2 (§19.1): weighting logic itself is displayed."""
    r = attention_score(INCOME_PROFILE, INCOME_CO)
    assert set(r["components"]) == set(r["weights"])
    assert r["explanation"]
