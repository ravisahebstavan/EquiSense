"""Ratio engine vs. hand-computed reference values (§29.1)."""
import pytest

from equisense.engine import ratios

PRICE = 300.0


def by_key(metrics):
    return {m.key: m for m in metrics}


def test_liquidity(fy2025):
    m = by_key(ratios.liquidity_ratios(fy2025))
    assert m["current_ratio"].value == pytest.approx(2.0)        # 500 / 250
    assert m["quick_ratio"].value == pytest.approx(1.4)          # (500-150) / 250
    assert m["cash_ratio"].value == pytest.approx(0.4)           # 100 / 250
    assert m["working_capital"].value == pytest.approx(250.0)    # 500 - 250


def test_leverage(fy2025):
    m = by_key(ratios.leverage_ratios(fy2025))
    assert m["debt_to_equity"].value == pytest.approx(200 / 700)
    assert m["net_debt_to_ebitda"].value == pytest.approx(0.4)   # (200-100)/250
    assert m["interest_coverage"].value == pytest.approx(10.0)   # 200/20


def test_profitability(fy2025):
    m = by_key(ratios.profitability_ratios(fy2025))
    assert m["gross_margin"].value == pytest.approx(40.0)
    assert m["operating_margin"].value == pytest.approx(20.0)
    assert m["net_margin"].value == pytest.approx(13.5)
    assert m["roe"].value == pytest.approx(135 / 700 * 100)
    assert m["roa"].value == pytest.approx(11.25)
    # DuPont components must multiply back to ROE (internal consistency)
    roe_reconstructed = (m["dupont_net_margin"].value / 100
                         * m["dupont_asset_turnover"].value
                         * m["dupont_equity_multiplier"].value) * 100
    assert roe_reconstructed == pytest.approx(m["roe"].value)


def test_roic(fy2025):
    # eff tax = 45/180 = 25%; NOPAT = 200*0.75 = 150; IC = 200+700-100 = 800
    m = ratios.roic(fy2025)
    assert m.value == pytest.approx(150 / 800 * 100)  # 18.75%
    assert "effective_tax_rate" in m.inputs
    assert m.inputs["effective_tax_rate"] == pytest.approx(0.25)


def test_efficiency(fy2025):
    m = by_key(ratios.efficiency_ratios(fy2025))
    assert m["asset_turnover"].value == pytest.approx(1000 / 1200)
    assert m["inventory_days"].value == pytest.approx(150 / 600 * 365)   # 91.25
    assert m["receivable_days"].value == pytest.approx(120 / 1000 * 365)  # 43.8
    assert m["payable_days"].value == pytest.approx(80 / 600 * 365)
    assert m["cash_conversion_cycle"].value == pytest.approx(
        91.25 + 43.8 - 80 / 600 * 365)


def test_per_share(fy2025):
    m = by_key(ratios.per_share_ratios(fy2025, price=PRICE))
    assert m["eps"].value == pytest.approx(13.5)
    assert m["book_value_per_share"].value == pytest.approx(70.0)
    assert m["pe"].value == pytest.approx(300 / 13.5)
    assert m["pb"].value == pytest.approx(300 / 70)
    assert m["ev_ebitda"].value == pytest.approx(3100 / 250)  # EV = 3000+200-100
    assert m["dividend_yield"].value == pytest.approx(1.0)    # DPS 3 / 300


def test_missing_inputs_yield_none_not_zero(fy2025):
    fy2025.inventory = None
    m = by_key(ratios.liquidity_ratios(fy2025))
    assert m["quick_ratio"].value is None  # never silently 0 (§types)


def test_every_metric_shows_its_work(fy2025):
    for m in ratios.all_ratios(fy2025, price=PRICE):
        assert m.formula, f"{m.key} has no formula"
        assert m.inputs, f"{m.key} has no inputs"
