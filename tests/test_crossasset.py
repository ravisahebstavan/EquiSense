"""Cross-asset relationship invariants."""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from equisense.engine import crossasset as CA


def _dates(n, start=date(2022, 1, 3)):
    return [start + timedelta(days=i) for i in range(n)]


def test_align_on_dates_intersects_rather_than_tail_aligning():
    """The bug this exists to prevent: equity and macro bars come from providers
    with different holiday calendars, so position-alignment pairs one day's move
    with another day's and destroys the correlation being measured."""
    d = _dates(10)
    a = {d[i]: float(i) for i in range(10)}
    b = {d[i]: float(i) for i in range(10) if i != 3}      # a missing holiday
    out = CA.align_on_dates({"a": a, "b": b})
    assert len(out["a"]) == len(out["b"]) == 9
    assert out["a"] == out["b"], "values must stay paired to the same date"


def test_alignment_recovers_a_correlation_that_position_alignment_destroys():
    rng = random.Random(4)
    d = _dates(400)
    common = {dt: rng.gauss(0, 0.01) for dt in d}
    a = dict(common)
    b = {dt: v + rng.gauss(0, 0.002) for dt, v in common.items()}
    del b[d[5]]                                            # one missing day
    aligned = CA.align_on_dates({"a": a, "b": b})
    r_aligned = CA.correlation_with_ci(aligned["a"], aligned["b"])["r"]
    # position-aligned: tails line up but everything before the gap is shifted
    r_positional = CA.correlation_with_ci(
        list(a.values()), list(b.values()))["r"]
    assert r_aligned > 0.95
    assert r_aligned > r_positional


def test_returns_from_dated_closes_skips_the_first_bar():
    rows = [(date(2024, 1, i + 1), 100.0 * (1.01 ** i)) for i in range(5)]
    out = CA.returns_from_dated_closes(rows)
    assert len(out) == 4
    assert all(abs(v - 0.01) < 1e-9 for v in out.values())


def test_correlation_ci_brackets_the_estimate():
    rng = random.Random(1)
    x = [rng.gauss(0, 1) for _ in range(300)]
    y = [v * 0.5 + rng.gauss(0, 1) for v in x]
    c = CA.correlation_with_ci(x, y)
    lo, hi = c["ci95"]
    assert lo < c["r"] < hi
    assert c["significant"] is True


def test_uncorrelated_series_are_not_called_significant():
    rng = random.Random(2)
    x = [rng.gauss(0, 1) for _ in range(400)]
    y = [rng.gauss(0, 1) for _ in range(400)]
    c = CA.correlation_with_ci(x, y)
    assert c["significant"] is False
    lo, hi = c["ci95"]
    assert lo < 0 < hi


def test_stress_conditional_detects_correlation_that_rises_in_a_drawdown():
    """The failure mode diversification claims hide: an asset uncorrelated on
    average that converges to the market exactly when protection is needed."""
    rng = random.Random(3)
    market, asset = [], []
    for _ in range(600):
        m = rng.gauss(0, 0.012)
        if m < -0.015:                       # in stress it tracks the market
            a = m * 0.9 + rng.gauss(0, 0.002)
        else:                                # otherwise unrelated
            a = rng.gauss(0, 0.012)
        market.append(m); asset.append(a)
    out = CA.stress_conditional_correlation(asset, market)
    assert out["computable"]
    assert out["stress"]["r"] > out["unconditional"]["r"]
    assert out["correlation_gap_in_stress"] > 0.1
    assert "weakest exactly when it is needed" in out["reading"]


def test_stress_conditional_refuses_thin_history():
    assert CA.stress_conditional_correlation([0.01] * 10, [0.01] * 10)["computable"] is False


def test_lead_lag_reports_contemporaneous_as_contemporaneous():
    rng = random.Random(5)
    d = [rng.gauss(0, 0.01) for _ in range(500)]
    f = [v + rng.gauss(0, 0.003) for v in d]       # same-day, no lead
    out = CA.lead_lag(d, f, max_lag=3)
    assert out["computable"]
    assert out["strongest"]["lag"] == 0
    assert "CONTEMPORANEOUS" in out["reading"]


def test_lead_lag_applies_fdr_across_the_lag_scan():
    """Scanning lags and reporting the best is how spurious leads are made.

    FDR controls the EXPECTED PROPORTION of false discoveries, not the
    probability of zero — so on null data a single run can and does throw one
    up (observed: lag +5, r=-0.148, p=0.003 on independent series). Testing one
    seed would therefore be testing the seed. This asserts the RATE across many
    independent null draws stays near the 5% the procedure promises.
    """
    discoveries = trials = 0
    for seed in range(40):
        rng = random.Random(seed)
        d = [rng.gauss(0, 0.01) for _ in range(400)]
        f = [rng.gauss(0, 0.01) for _ in range(400)]   # genuinely unrelated
        out = CA.lead_lag(d, f, max_lag=5)
        assert out["computable"]
        assert all("q_value" in r for r in out["lags"])
        trials += 1
        if out["predictive_lags_surviving_fdr"]:
            discoveries += 1
    assert discoveries / trials <= 0.20, (
        f"{discoveries}/{trials} null runs produced a 'predictive' lag — FDR "
        "control is not working")


def test_lead_lag_reading_matches_what_survived():
    rng = random.Random(6)
    d = [rng.gauss(0, 0.01) for _ in range(400)]
    f = [rng.gauss(0, 0.01) for _ in range(400)]
    out = CA.lead_lag(d, f, max_lag=5)
    if out["predictive_lags_surviving_fdr"]:
        assert "survive FDR" in out["reading"]
    else:
        assert "no positive lag survives" in out["reading"]


def test_beta_recovers_a_known_sensitivity_and_reports_r_squared():
    rng = random.Random(7)
    drv = [rng.gauss(0, 0.01) for _ in range(500)]
    asset = [1.8 * v + rng.gauss(0, 0.004) for v in drv]
    out = CA.beta_to(asset, drv)
    assert out["computable"]
    assert out["beta"] == pytest.approx(1.8, abs=0.1)
    assert out["r_squared"] > 0.8


def test_relationship_matrix_controls_for_multiple_testing():
    rng = random.Random(8)
    series = {f"A{i}": [rng.gauss(0, 0.01) for _ in range(400)] for i in range(8)}
    series["NIFTY"] = [rng.gauss(0, 0.01) for _ in range(400)]
    m = CA.relationship_matrix(series, "NIFTY")
    assert m["computable"]
    assert m["pairs_tested"] == 36            # 9 choose 2
    # all series independent -> few if any should survive FDR
    assert m["pairs_surviving_fdr"] <= 3
    assert all("q_value" in p for p in m["pairs"])
