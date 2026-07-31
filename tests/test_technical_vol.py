"""Range-based volatility estimators, validated against a known sigma.

Volatility sets the stop distance and the stop distance sets the position size,
so an estimator bug here costs money directly. These tests simulate paths with a
KNOWN true sigma and an explicit overnight-gap share, so every estimator can be
checked against ground truth rather than against each other.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from equisense.engine.technical import (best_available_vol, garman_klass_vol,
                                        parkinson_vol, realized_vol,
                                        rogers_satchell_vol, yang_zhang_vol)

SIGMA_TRUE = 0.20
GAP_SHARE = 0.35


def _paths(n_days=3000, steps=80, seed=7):
    rng = np.random.default_rng(seed)
    daily = SIGMA_TRUE / math.sqrt(252)
    s_on = daily * math.sqrt(GAP_SHARE)
    s_id = daily * math.sqrt(1 - GAP_SHARE)
    prev, O, H, L, C = 100.0, [], [], [], []
    for _ in range(n_days):
        o = prev * math.exp(rng.normal(0, s_on))
        path = o * np.exp(np.cumsum(rng.normal(0, s_id / math.sqrt(steps), steps)))
        O.append(o); H.append(max(path.max(), o))
        L.append(min(path.min(), o)); C.append(path[-1])
        prev = C[-1]
    return O, H, L, C


def test_yang_zhang_recovers_a_known_sigma():
    """The only gap-inclusive estimator of the four, and the sizing input."""
    O, H, L, C = _paths()
    m = yang_zhang_vol(O, H, L, C, window=250)
    assert m.value == pytest.approx(SIGMA_TRUE * 100, abs=2.0)


def test_range_only_estimators_exclude_the_overnight_gap():
    """Parkinson / Garman-Klass / Rogers-Satchell measure INTRADAY variance and
    are blind to gaps by construction. That is correct behaviour, and it is
    exactly why they are the wrong default for Indian equities."""
    O, H, L, C = _paths()
    intraday_true = SIGMA_TRUE * math.sqrt(1 - GAP_SHARE) * 100
    for m in (parkinson_vol(H, L, 250), garman_klass_vol(O, H, L, C, 250),
              rogers_satchell_vol(O, H, L, C, 250)):
        assert m.value < SIGMA_TRUE * 100, f"{m.key} must not capture the gap"
        assert m.value == pytest.approx(intraday_true, abs=3.0)


def test_garman_klass_subtracts_the_open_close_term():
    """The open-to-close term is SUBTRACTED. Writing it as a plus inflates the
    estimate — a signed-wrong variant returned 0.2032 against a true 0.2000 in
    simulation, looking right purely by coincidence of the gap fraction."""
    O, H, L, C = _paths()
    gk = garman_klass_vol(O, H, L, C, 250).value / 100
    lh, ll = np.log(H), np.log(L)
    lo, lc = np.log(O), np.log(C)
    hl, oc = lh - ll, lc - lo
    correct = float(np.mean(0.5 * hl ** 2 - (2 * math.log(2) - 1) * oc ** 2))
    assert gk ** 2 / 252 == pytest.approx(correct, rel=0.05)


def test_yang_zhang_k_depends_only_on_window_length():
    """k = 0.34 / (1.34 + (n+1)/(n-1)) is a CONSTANT chosen to minimise estimator
    variance. Deriving it from a ratio of the observed components breaks the
    minimum-variance property and inflates the result."""
    O, H, L, C = _paths()
    m = yang_zhang_vol(O, H, L, C, window=250)
    n = m.inputs["bars"]
    assert m.inputs["k"] == pytest.approx(0.34 / (1.34 + (n + 1) / (n - 1)), abs=1e-4)


def test_yang_zhang_is_more_efficient_than_close_to_close():
    """~6x efficiency for the same window is the entire reason to use it: it
    means the stop distance stops jittering month to month."""
    O, H, L, C = _paths()
    cc, yz = [], []
    for i in range(300, len(C), 5):
        cc.append(realized_vol(C[:i], 21).value)
        v = yang_zhang_vol(O[:i], H[:i], L[:i], C[:i], 21).value
        if v:
            yz.append(v)
    assert np.var(cc) / np.var(yz) > 3.0


def test_yang_zhang_reports_the_overnight_share():
    O, H, L, C = _paths()
    m = yang_zhang_vol(O, H, L, C, window=250)
    assert 20 <= m.inputs["overnight_variance_share_pct"] <= 55


def test_best_available_falls_back_and_says_so():
    O, H, L, C = _paths(n_days=400)
    with_ohlc = best_available_vol(C, O, H, L, window=100)
    without = best_available_vol(C, window=100)
    assert with_ohlc.inputs["estimator"] == "yang_zhang"
    assert without.inputs["estimator"] == "close_to_close"
    assert "6x less efficient" in without.caveat


def test_estimators_refuse_thin_or_broken_input():
    assert yang_zhang_vol([1] * 3, [1] * 3, [1] * 3, [1] * 3, 21).value is None
    assert parkinson_vol([1] * 3, [1] * 3, 21).value is None
    # high < low is impossible; those bars must be dropped, not used
    assert garman_klass_vol([10] * 30, [8] * 30, [12] * 30, [10] * 30, 21).value is None
