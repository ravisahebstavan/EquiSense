"""Technical + novel engines on synthetic series with known properties."""
import math

import pytest

from equisense.engine import novel, technical

UP = [100 * (1.001 ** i) for i in range(300)]          # smooth uptrend
DOWN = [100 * (0.999 ** i) for i in range(300)]        # smooth downtrend
FLAT = [100.0] * 300
VOLS = [1_000_000.0] * 300


def test_momentum_sign():
    assert technical.momentum_12_1(UP).value > 0
    assert technical.momentum_12_1(DOWN).value < 0
    assert technical.momentum_12_1(UP[:100]).value is None  # insufficient history


def test_52w_high_distance():
    m = technical.pct_from_52w_high(UP)
    assert m.value == pytest.approx(0.0, abs=1e-9)  # uptrend sits at its high
    assert technical.pct_from_52w_high(DOWN).value < -20


def test_trend_200dma_sign():
    assert technical.trend_200dma(UP).value > 0
    assert technical.trend_200dma(DOWN).value < 0
    assert technical.trend_200dma(UP).inputs["ma200_slope_21d_pct"] > 0


def test_realized_vol_zero_for_constant_growth():
    # constant multiplicative growth → zero log-return variance
    assert technical.realized_vol(UP).value == pytest.approx(0.0, abs=1e-9)


def test_relative_strength():
    m = technical.relative_strength(UP, FLAT)
    assert m.value > 0


def test_volume_anomaly_surge():
    vols = [1e6] * 250 + [3e6] * 50
    assert technical.volume_anomaly(vols).value > 1.5


def test_adv_crore():
    adv = technical.adv_crore([100.0] * 100, [1e6] * 100)
    assert adv == pytest.approx(100 * 1e6 / 1e7)  # ₹10 cr/day


def test_max_drawdown():
    series = [100, 120, 60, 80]
    assert technical.max_drawdown(series).value == pytest.approx(-50.0)


def test_mqi_prefers_smooth_paths():
    # same endpoints, one smooth, one violent — MQI must rank smooth higher
    smooth = UP
    violent = [100 * (1.001 ** i) * (1 + 0.05 * math.sin(i)) for i in range(300)]
    mqi_s = novel.momentum_quality(smooth).value
    mqi_v = novel.momentum_quality(violent).value
    assert mqi_s is not None and mqi_v is not None
    assert mqi_s > mqi_v


def test_cash_conviction_clean_vs_dirty(fy2025, fy2024):
    clean = novel.cash_conviction([fy2024, fy2025])
    assert clean.value > 70  # CFO/NI 1.33, negative accruals, sane capex
    fy2025.cfo = 30.0        # profit without cash
    dirty = novel.cash_conviction([fy2024, fy2025])
    assert dirty.value < clean.value


def test_fragility_leverage_sensitivity(fy2025):
    low = novel.fragility([fy2025], UP)
    fy2025.total_debt = 2000.0
    fy2025.cash = 0.0
    high = novel.fragility([fy2025], UP)
    assert high.value > low.value


def test_tvt_quadrants():
    m = novel.trend_value_tension(20.0, 5.0)   # cheap + uptrend
    assert "coiled value" in m.inputs["quadrant"]
    m2 = novel.trend_value_tension(85.0, -4.0)  # expensive + downtrend
    assert "unwinding" in m2.inputs["quadrant"]
    assert novel.trend_value_tension(None, 5.0).value is None
