"""Base-rate study machinery on a synthetic panel with a PLANTED signal:
the study must recover a positive edge for the planted winners and ~zero for
noise — the leak-and-power sanity check for the research harness."""
import numpy as np
import pandas as pd
import pytest

from equisense.research.base_rates import STUDIES, run_study

rng = np.random.default_rng(7)


def _panel(n_days=1300, n_stocks=30):
    """Half the stocks get persistent drift (momentum works by construction);
    half are pure noise. Volume flat."""
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    data = {}
    for i in range(n_stocks):
        drift = 0.0008 if i < n_stocks // 2 else 0.0
        rets = rng.normal(drift, 0.015, n_days)
        data[f"S{i}"] = 100 * np.exp(np.cumsum(rets))
    closes = pd.DataFrame(data, index=dates)
    volumes = pd.DataFrame(1e6, index=dates, columns=closes.columns)
    return closes, volumes


def test_momentum_study_recovers_planted_signal():
    closes, volumes = _panel()
    regimes = pd.Series("uptrend", index=closes.index)
    results = run_study(closes, volumes, regimes, "HYP-001")
    all_126 = [r for r in results if r["horizon_days"] == 126 and r["regime_filter"] == "all"]
    assert all_126, "study produced no publishable cells"
    rec = all_126[0]
    # persistent-drift stocks dominate the top quintile → positive median excess
    assert rec["median_excess_pct"] > 1.0
    assert rec["hit_rate"] > 0.55
    assert rec["n"] >= 30


def test_thin_cells_are_not_published():
    closes, volumes = _panel(n_days=300)  # too short for many horizon episodes
    regimes = pd.Series("downtrend", index=closes.index)
    results = run_study(closes, volumes, regimes, "HYP-001")
    for r in results:
        assert r["n"] >= 30  # the sample-size gate (§17)


def test_every_study_records_registry_ref_and_caveat():
    closes, volumes = _panel()
    regimes = pd.Series("uptrend", index=closes.index)
    for hyp in STUDIES:
        for r in run_study(closes, volumes, regimes, hyp):
            assert r["registry_ref"] == hyp
            assert "survivorship" in r["spec"]


def test_no_lookahead_in_momentum_feature():
    """Perturbing FUTURE prices must not change today's feature value."""
    closes, volumes = _panel()
    feat = STUDIES["HYP-001"]["feature"](closes, volumes)
    t = closes.index[600]
    before = feat.loc[t].copy()
    closes2 = closes.copy()
    closes2.iloc[601:] *= 3.0  # violent future shock
    after = STUDIES["HYP-001"]["feature"](closes2, volumes).loc[t]
    pd.testing.assert_series_equal(before, after)
