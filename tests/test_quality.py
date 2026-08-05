"""Quality/distress scoring vs. hand-computed values (§15)."""
import pytest

from equisense.engine import quality

PRICE = 300.0


def test_cash_flow_quality(fy2025):
    m = {x.key: x for x in quality.cash_flow_quality(fy2025)}
    assert m["accruals_ratio"].value == pytest.approx((135 - 180) / 1200 * 100)  # -3.75%
    assert m["cfo_to_net_income"].value == pytest.approx(180 / 135)
    assert m["capex_intensity"].value == pytest.approx(6.0)
    assert m["capex_to_depreciation"].value == pytest.approx(1.2)
    assert m["free_cash_flow"].value == pytest.approx(120.0)


def test_altman_z(fy2025):
    # x1=250/1200, x2=400/1200, x3=200/1200, x4=3000/500, x5=1000/1200
    # Z = 0.25 + 0.4666667 + 0.55 + 3.6 + 0.8333333 = 5.7
    m = quality.altman_z(fy2025, price=PRICE)
    assert m.value == pytest.approx(5.7, abs=1e-6)
    assert quality.altman_zone(m.value) == "safe"
    assert m.caveat and "calibrated" in m.caveat  # §15 calibration caveat is mandatory


def test_altman_zones():
    assert quality.altman_zone(1.5) == "distress"
    assert quality.altman_zone(2.5) == "grey"
    assert quality.altman_zone(3.5) == "safe"
    assert quality.altman_zone(None) is None


def test_piotroski_perfect_year(fy2025, fy2024):
    # All 9 signals improve FY2024 → FY2025 by construction of the fixtures.
    m = quality.piotroski_f(fy2025, fy2024)
    assert m.value == 9.0
    assert quality.quality_tier(m.value) == "high"


def test_piotroski_degrading_year(fy2025, fy2024):
    # Reverse the years: FY2024 relative to FY2025 fails the delta signals but
    # keeps the level signals (ROA>0, CFO>0, CFO>NI, no dilution).
    m = quality.piotroski_f(fy2024, fy2025)
    assert m.value == 4.0
    assert quality.quality_tier(m.value) == "medium"


def test_z_score_missing_price(fy2025):
    m = quality.altman_z(fy2025, price=None)
    assert m.value is None  # no market value of equity → honest None
