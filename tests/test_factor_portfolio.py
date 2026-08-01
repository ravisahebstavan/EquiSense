"""Quantile-portfolio factor evaluation.

Every expectation here is derived from a constructed factor whose answer is
known in advance, so the module is validated against arithmetic rather than
against its own output.
"""
import datetime as dt

import pytest

from equisense.research.factor_portfolio import (DEFAULT_QUANTILES,
                                                 _quantile_buckets,
                                                 factor_autocorrelation,
                                                 factor_quantile_study)


def _dates(n, step=21):
    return [dt.date(2020, 1, 1) + dt.timedelta(days=i * step) for i in range(n)]


def test_buckets_are_equal_sized_and_ordered_low_to_high():
    b = _quantile_buckets({f"S{i}": float(i) for i in range(50)}, 5)
    assert [len(v) for v in b.values()] == [10] * 5
    assert b[1] == [f"S{i}" for i in range(10)]      # lowest factor values
    assert b[5] == [f"S{i}" for i in range(40, 50)]  # highest


def test_buckets_refuse_a_universe_too_small_to_fill_them():
    assert _quantile_buckets({f"S{i}": float(i) for i in range(9)}, 5) is None


def test_perfectly_monotone_factor_recovers_its_exact_spread():
    """Forward return is defined as factor_rank/100, so quantile means and the
    top-minus-bottom spread are known exactly before running anything."""
    rows = []
    for d in _dates(40):
        values = {f"S{i}": float(i) for i in range(50)}
        forwards = {f"S{i}": i / 100.0 for i in range(50)}
        rows.append((d, values, forwards))
    r = factor_quantile_study(rows, horizon_days=21, sampling_days=21)
    assert r["computable"]
    # bucket 1 holds ranks 0-9 -> mean 4.5 -> 4.5%; bucket 5 holds 40-49 -> 44.5%
    assert r["quantile_mean_return_pct"][1] == pytest.approx(4.5)
    assert r["quantile_mean_return_pct"][5] == pytest.approx(44.5)
    assert r["spread_mean_pct"] == pytest.approx(40.0)
    assert r["monotonicity"] == pytest.approx(1.0)
    assert r["hit_rate"] == 1.0
    # a fixed factor never reshuffles, so nothing turns over and nothing is paid
    assert r["turnover_per_rebalance"] == pytest.approx(0.0)
    assert r["cost_annual_pct"] == pytest.approx(0.0)
    assert r["net_annual_pct"] == pytest.approx(r["gross_annual_pct"])


def test_a_factor_carried_only_by_its_extremes_is_flagged_non_monotone():
    """The failure IC cannot see: top and bottom behave, the middle is ranked
    backwards. That is a handful of outliers, not an effect that orders the
    universe, and it will not survive a live book."""
    rows = []
    for k, d in enumerate(_dates(40)):
        values = {f"S{i}": float(i) for i in range(50)}
        # per-date wobble applied to the TOP bucket only, so the spread itself
        # varies and a t-stat exists. A wobble common to every bucket would
        # cancel in the difference and leave the spread constant.
        wob = ((k * 7) % 5 - 2) * 0.002
        forwards = {}
        for i in range(50):
            q = i // 10                      # 0..4
            # extremes ordered correctly, middle three deliberately inverted
            base = {0: -0.10, 1: 0.04, 2: 0.02, 3: 0.00, 4: 0.10}[q]
            forwards[f"S{i}"] = base + (wob if q == 4 else 0.0)
        rows.append((d, values, forwards))
    r = factor_quantile_study(rows, horizon_days=21, sampling_days=21)
    assert r["spread_mean_pct"] == pytest.approx(20.0)   # extremes look great
    assert r["spread_t_stat"] > 2.0                       # and are significant
    assert r["monotonicity"] < 0.7                        # ...but ordering is not
    assert "NOT monotone" in r["verdict"]


def test_turnover_is_charged_and_can_consume_the_entire_edge():
    """The decisive question IC cannot answer. A factor that reshuffles its
    baskets completely every rebalance pays the round trip every rebalance."""
    rows = []
    for k, d in enumerate(_dates(40)):
        # the ranking flips every period, so both traded baskets are replaced
        # wholesale each rebalance — the worst realistic case for turnover
        values = {f"S{i}": float(i if k % 2 == 0 else 49 - i) for i in range(50)}
        forwards = {f"S{i}": (values[f"S{i}"] - 24.5) * 0.0002 for i in range(50)}
        rows.append((d, values, forwards))
    r = factor_quantile_study(rows, horizon_days=21, sampling_days=21)
    assert r["turnover_per_rebalance"] == pytest.approx(1.0), (
        "a full rank reversal replaces both baskets entirely")
    assert r["cost_annual_pct"] > 0
    assert r["net_annual_pct"] < r["gross_annual_pct"]


def test_cost_arithmetic_is_exactly_reproducible():
    """Guards the annualisation, which is where this kind of module usually
    goes quietly wrong."""
    rows = []
    for k, d in enumerate(_dates(40)):
        values = {f"S{i}": float(i if k % 2 == 0 else 49 - i) for i in range(50)}
        forwards = {f"S{i}": 0.01 for i in range(50)}
        rows.append((d, values, forwards))
    r = factor_quantile_study(rows, horizon_days=21, sampling_days=21,
                              round_trip_cost=0.002)
    rebalances = 252.0 / 21
    expected = r["effective_turnover"] * 0.002 * rebalances * 2 * 100
    assert r["cost_annual_pct"] == pytest.approx(expected, rel=1e-6)


def test_overlapping_tranches_divide_turnover_rather_than_multiplying_it():
    """Holding 126 days while rebalancing every 21 runs 6 tranches, so only a
    sixth of the book turns over on any date. Charging the full basket turnover
    six times a year would overstate costs by 6x and wrongly kill good signals."""
    rows = []
    for k, d in enumerate(_dates(40)):
        values = {f"S{i}": float((i * 7 + k * 13) % 50) for i in range(50)}
        forwards = {f"S{i}": (values[f"S{i}"] - 24.5) * 0.001 for i in range(50)}
        rows.append((d, values, forwards))
    short = factor_quantile_study(rows, horizon_days=21, sampling_days=21)
    long = factor_quantile_study(rows, horizon_days=126, sampling_days=21)
    assert long["effective_turnover"] == pytest.approx(
        long["turnover_per_rebalance"] / 6, abs=1e-4)   # module rounds to 4dp
    assert short["effective_turnover"] > long["effective_turnover"]


def test_newey_west_lag_follows_the_overlap():
    rows = [(d, {f"S{i}": float(i) for i in range(50)},
             {f"S{i}": i / 1000.0 for i in range(50)}) for d in _dates(40)]
    assert factor_quantile_study(rows, 21, 21)["newey_west_lag"] == 0
    assert factor_quantile_study(rows, 63, 21)["newey_west_lag"] == 2
    assert factor_quantile_study(rows, 126, 21)["newey_west_lag"] == 5


def test_too_few_dates_refuses_rather_than_reporting_a_number():
    rows = [(d, {f"S{i}": float(i) for i in range(50)},
             {f"S{i}": i / 100.0 for i in range(50)}) for d in _dates(5)]
    r = factor_quantile_study(rows, horizon_days=21)
    assert r["computable"] is False and "usable dates" in r["reason"]


def test_autocorrelation_separates_a_stable_factor_from_a_churning_one():
    stable = [(d, {f"S{i}": float(i) for i in range(50)}, {}) for d in _dates(30)]
    churn = [(d, {f"S{i}": float((i * 37 + k * 101) % 50) for i in range(50)}, {})
             for k, d in enumerate(_dates(30))]
    a = factor_autocorrelation(stable)
    b = factor_autocorrelation(churn)
    assert a["mean_autocorrelation"] == pytest.approx(1.0)
    assert "persistent" in a["reading"]
    assert b["mean_autocorrelation"] < a["mean_autocorrelation"]


def test_a_tail_driven_spread_is_flagged_not_reported_as_an_edge():
    """Measured on real data for the low-volatility factor: the mean Q5-Q1
    spread was -18.6%/yr while the MEDIAN spread was +2.3%/yr — the sign flips.
    The whole mean effect was a few enormous winners in one bucket. Reporting
    the mean alone would present that as a large tradeable edge when the typical
    name does the opposite, and a small book cannot concentrate into the tail
    that carries it."""
    rows = []
    for k, d in enumerate(_dates(40)):
        values = {f"S{i}": float(i) for i in range(50)}
        forwards = {}
        for i in range(50):
            # every name flat except one huge winner in the TOP bucket
            forwards[f"S{i}"] = 0.0
        forwards["S49"] = 5.0 + (k % 3) * 0.1
        rows.append((d, values, forwards))
    r = factor_quantile_study(rows, horizon_days=21, sampling_days=21)
    assert r["spread_mean_pct"] > 40, "the mean is dragged up by the one winner"
    assert r["spread_median_pct"] == pytest.approx(0.0, abs=1e-9)
    assert r["tail_driven"] is True
    assert "MEDIAN spread" in r["verdict"]


def test_a_broad_effect_is_not_flagged_as_tail_driven():
    """The flag must fire on concentration, not on any positive spread."""
    rows = []
    for k, d in enumerate(_dates(40)):
        values = {f"S{i}": float(i) for i in range(50)}
        # every name in a bucket shares the bucket's return, so mean == median
        forwards = {f"S{i}": (i // 10) * 0.01 + (k % 3) * 0.0005 for i in range(50)}
        rows.append((d, values, forwards))
    r = factor_quantile_study(rows, horizon_days=21, sampling_days=21)
    assert r["spread_mean_pct"] > 0
    assert r["tail_driven"] is False
    assert "MEDIAN spread" not in r["verdict"]
