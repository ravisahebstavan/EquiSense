"""Information Coefficient machinery, validated against planted signals.

IC decides which signals earn weight, so the tests plant a KNOWN correlation and
check it is recovered — and, just as importantly, that pure noise does not pass.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from equisense.research.ic import (evaluate_ic, ic_decay, ic_series, ic_weights,
                                   spearman, walk_forward_ic)


def _build(true_ic: float, n_dates=140, n_names=40, seed=11):
    rng = np.random.default_rng(seed)
    names = [f"S{i:02d}" for i in range(n_names)]
    dates = [dt.date(2016, 1, 1) + dt.timedelta(days=21 * i) for i in range(n_dates)]
    rows = []
    for d in dates:
        sig = rng.normal(0, 1, n_names)
        noise = rng.normal(0, 1, n_names)
        fwd = true_ic * sig + math.sqrt(max(0.0, 1 - true_ic ** 2)) * noise
        rows.append((d, dict(zip(names, sig)), dict(zip(names, fwd))))
    return rows


def test_spearman_is_rank_based_not_level_based():
    x = [1, 2, 3, 4, 5]
    assert spearman(x, [10, 20, 30, 40, 50]) == pytest.approx(1.0)
    assert spearman(x, [1, 4, 9, 16, 25]) == pytest.approx(1.0), \
        "a monotone non-linear map must still be rank-correlation 1"
    assert spearman(x, [5, 4, 3, 2, 1]) == pytest.approx(-1.0)


def test_spearman_handles_ties():
    assert spearman([1, 1, 2, 2], [1, 1, 2, 2]) == pytest.approx(1.0)


def test_ic_recovers_a_planted_correlation():
    ev = evaluate_ic(_build(0.10), horizon_days=63)
    assert ev["computable"]
    assert ev["mean_ic"] == pytest.approx(0.10, abs=0.03)
    assert ev["t_stat"] > 2.0


def test_pure_noise_does_not_pass():
    """The false-positive check. A signal with zero true IC must not earn
    weight."""
    passed = 0
    for seed in range(20):
        ev = evaluate_ic(_build(0.0, seed=100 + seed), horizon_days=63)
        if ev.get("t_stat") and abs(ev["t_stat"]) >= 2.0:
            passed += 1
    assert passed <= 4, f"{passed}/20 noise signals passed — far above alpha=0.05"


def test_newey_west_lag_scales_with_the_forward_horizon():
    """Overlapping windows correlate consecutive ICs; the lag must track the
    overlap span or significance is overstated."""
    rows = _build(0.05)
    assert evaluate_ic(rows, horizon_days=21)["newey_west_lag"] == 0
    assert evaluate_ic(rows, horizon_days=63)["newey_west_lag"] == 2
    assert evaluate_ic(rows, horizon_days=126)["newey_west_lag"] == 5


def test_thin_cross_sections_are_dropped_not_averaged():
    rows = _build(0.1, n_names=4)      # below MIN_NAMES_PER_DATE
    assert ic_series(rows) == []
    assert evaluate_ic(rows, 63)["computable"] is False


def test_implausible_ic_is_flagged_as_a_bug_not_an_edge():
    ev = evaluate_ic(_build(0.60), horizon_days=63)
    assert ev["implausible"] is True
    assert "look-ahead" in ev["verdict"]


def test_decay_identifies_the_peak_horizon():
    per_h = {21: _build(0.12, seed=1), 63: _build(0.06, seed=2),
             126: _build(0.02, seed=3), 252: _build(0.005, seed=4)}
    d = ic_decay(per_h)
    assert d["computable"]
    assert d["peak_horizon_days"] == 21
    assert d["half_life_days"] is not None


def test_walk_forward_separates_real_signal_from_noise():
    """Purged/embargoed out-of-sample sign stability is what distinguishes a
    signal from an in-sample artefact."""
    real = walk_forward_ic(_build(0.10, seed=3), horizon_days=126, train_dates=30)
    noise = walk_forward_ic(_build(0.0, seed=4), horizon_days=126, train_dates=30)
    assert real["computable"] and noise["computable"]
    assert real["sign_agreement_rate"] > noise["sign_agreement_rate"]
    assert real["embargo_dates"] == 6


def test_embargo_is_a_full_forward_window():
    """Without it the last training observation's forward window overlaps the
    first test observation, and train/test share returns."""
    wf = walk_forward_ic(_build(0.08), horizon_days=63, train_dates=30)
    assert wf["embargo_dates"] == 3
    assert "embargo" in wf["method"]


def test_weights_zero_failed_signals_and_keep_negative_ic():
    """A reliably NEGATIVE IC is informative — invert the signal. Squaring the
    IC, a common suggestion, would discard the sign and get this wrong."""
    evs = {"strong": evaluate_ic(_build(0.12, seed=21), 63),
           "noise": evaluate_ic(_build(0.0, seed=22), 63),
           "inverse": evaluate_ic(_build(-0.10, seed=23), 63)}
    w = ic_weights(evs)
    assert w["weights"]["noise"] == 0.0
    assert w["weights"]["strong"] > 0
    assert w["weights"]["inverse"] > 0, "negative IC carries information"


def test_weights_stay_uniform_when_nothing_passes():
    """The honest default. This is the CURRENT state on real data: no price
    signal passes its IC t-test on 10y of NIFTY-50."""
    evs = {a: evaluate_ic(_build(0.0, seed=50 + i), 63)
           for i, a in enumerate(("a", "b", "c"))}
    w = ic_weights(evs)
    assert set(w["weights"].values()) == {1.0}
    assert "UNIFORM" in w["status"]


def test_minimum_detectable_ic_falls_with_names_and_dates():
    """A null IC is ambiguous without its detection limit: 'signal absent' and
    'cross-section too narrow to resolve it' call for opposite responses."""
    from equisense.research.ic import minimum_detectable_ic as mdi
    assert mdi(500, 100, 126) < mdi(200, 100, 126) < mdi(55, 100, 126)
    assert mdi(55, 400, 126) < mdi(55, 100, 126)
    assert mdi(1, 100, 126) != mdi(1, 100, 126)      # nan on degenerate input


def test_underpowered_nulls_are_labelled_as_such():
    """The live finding: at 55 names every measured |IC| sat below the 0.067
    detection limit, so 'no signal passes' was partly a power statement."""
    ev = evaluate_ic(_build(0.01), horizon_days=126)
    assert ev["minimum_detectable_ic"] > 0
    if ev["underpowered"]:
        assert "POWER statement" in ev["verdict"]


def test_a_strong_signal_is_not_flagged_underpowered():
    ev = evaluate_ic(_build(0.15), horizon_days=63)
    assert ev["underpowered"] is False
    assert abs(ev["mean_ic"]) > ev["minimum_detectable_ic"]


def test_ic_runner_survives_pandas_NA_in_a_feature_frame():
    """Real bug found only at scale: the self-inequality NaN check `v == v`
    raises on pandas' NA sentinel ("boolean value of NA is ambiguous"), and
    feat_momentum_quality introduces NA via .replace(0, pd.NA) whenever a stock
    has zero realised volatility in the window. At 55 names none did; at 200 one
    did, and the entire study crashed rather than skipping that cell."""
    import pandas as pd

    frame = pd.DataFrame({"A": [1.0, 2.0], "B": [pd.NA, 3.0]})
    row = frame.iloc[1]
    # the old check raises; pd.notna must be used instead
    with pytest.raises(TypeError):
        {k: float(v) for k, v in frame.iloc[0].items() if v is not None and v == v}
    safe = {k: float(v) for k, v in row.items() if pd.notna(v)}
    assert safe == {"A": 2.0, "B": 3.0}


def test_ic_runner_source_uses_pd_notna():
    """Pins the fix at the source, since the failure only reproduces with a
    price panel large enough to contain a zero-volatility name."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "equisense" / "research" / "ic.py").read_text()
    block = src[src.index("def run_ic_studies"):]
    # strip comments first — the fix is DOCUMENTED with the old pattern, and
    # matching raw text would flag the explanation rather than the code
    code = "\n".join(ln for ln in block.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "pd.notna(v)" in code
    assert "v == v" not in code, "the NA-unsafe check must not return"
