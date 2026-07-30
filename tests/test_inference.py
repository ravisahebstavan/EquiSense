"""Wave S: statistical-inference and synthesis-calibration invariants.

Every test here corresponds to a defect that was found, verified empirically,
and fixed. They exist so those defects cannot silently return — each one was
originally *undetectable* from the outside, which is exactly why they survived
so long.
"""
from __future__ import annotations

import math
import random
import statistics as st

import pytest

from equisense.engine.evidence import Evidence, ev
from equisense.engine.synthesis import (ABSTAIN_Z, MAX_DISPERSION, null_sd,
                                        shrink_correlation, synthesize)
from equisense.engine.types import Metric
from equisense.research.stats import (benjamini_hochberg, block_observations,
                                      cluster_block_bootstrap_ci,
                                      cluster_robust_mean,
                                      deflated_sharpe_ratio,
                                      effective_sample_size,
                                      intraclass_correlation, newey_west_se,
                                      norm_ppf, t_two_sided_p)

M = Metric(key="m", label="m", value=1.0, unit="x", formula="f")


def E(cluster, strength, **kw):
    return Evidence(engine="t", family="f", cluster=cluster, tier="T1",
                    direction="long" if strength > 0 else "short",
                    strength=strength, horizon="months", statement="s", **kw)


# ------------------------------------------------------------ distributions

@pytest.mark.parametrize("t,df,expected", [
    (2.228, 10, 0.05), (2.0, 10, 0.0734), (3.169, 10, 0.01),
    (2.086, 20, 0.05), (1.96, 10_000_000, 0.05),
])
def test_student_t_matches_published_critical_values(t, df, expected):
    """Exact t, not a normal approximation — cluster counts here are 15–40."""
    assert t_two_sided_p(t, df) == pytest.approx(expected, abs=0.002)


def test_norm_ppf_known_value():
    assert norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)


# ----------------------------------------------------------------- clustering

def test_icc_recovers_extremes():
    perfect = [[5.0] * 4, [1.0] * 4, [9.0] * 4, [3.0] * 4]   # zero within-variance
    assert intraclass_correlation(perfect) == pytest.approx(1.0, abs=1e-9)
    rng = random.Random(1)
    independent = [[rng.gauss(0, 1) for _ in range(4)] for _ in range(80)]
    assert intraclass_correlation(independent) < 0.1


def test_effective_sample_size_collapses_clustered_observations():
    """The defect: N_eff = N x 21/h corrected only serial overlap and ignored
    same-date commonality, overstating independent information ~10x."""
    rng = random.Random(2)
    groups = []
    for _ in range(36):
        shock = rng.gauss(0, 4.0)                       # common date factor
        groups.append([shock + rng.gauss(0, 1.0) for _ in range(30)])
    ess = effective_sample_size(groups)
    assert ess["n"] == 1080
    assert ess["n_clusters"] == 36
    assert ess["icc"] > 0.8                            # strong clustering detected
    assert ess["n_eff"] < 60, "N_eff must collapse toward the cluster count"


def test_effective_sample_size_is_conservative_when_icc_unestimable():
    single = [[1.0]]
    ess = effective_sample_size(single)
    assert ess["n_eff"] <= ess["n"]


def test_block_observations_groups_by_overlap_span():
    dated = [(i, [float(i)]) for i in range(12)]
    clusters = block_observations(dated, overlap_span=3)
    assert len(clusters) == 4
    assert clusters[0] == [0.0, 1.0, 2.0]


def test_cluster_robust_mean_rejects_a_spurious_edge():
    """The load-bearing test. Data has TRUE mean zero but strong within-date
    correlation. Naive iid inference calls it significant; cluster-robust
    inference must not."""
    rng = random.Random(3)
    groups = []
    for _ in range(36):
        shock = rng.gauss(0, 4.0)
        groups.append([shock + rng.gauss(0, 1.0) for _ in range(30)])
    flat = [x for g in groups for x in g]
    naive_mean = st.fmean(flat)
    naive_se = st.stdev(flat) / math.sqrt(len(flat))
    mt = cluster_robust_mean(groups)
    assert mt is not None
    assert mt.df == 35, "df must be G-1, not N-1"
    assert mt.se > 3 * naive_se, "clustering must inflate the standard error"
    assert abs(mt.t_stat) < abs(naive_mean / naive_se)
    assert mt.p_value > 0.05, "a zero-mean series must not look significant"


def test_cluster_bootstrap_is_wider_than_observation_level_resampling():
    rng = random.Random(4)
    groups = [[rng.gauss(0, 4.0) + rng.gauss(0, 1.0) for _ in range(20)]
              for _ in range(40)]
    lo, hi = cluster_block_bootstrap_ci(groups, statistic="median")
    assert not math.isnan(lo) and hi > lo


def test_newey_west_inflates_se_under_overlap():
    rng = random.Random(5)
    base = [rng.gauss(0, 1) for _ in range(300)]
    overlapping = [st.fmean(base[i:i + 3]) for i in range(len(base) - 3)]
    iid = st.stdev(overlapping) / math.sqrt(len(overlapping))
    assert newey_west_se(overlapping, lags=2) > iid


# --------------------------------------------------------------- multiplicity

def test_benjamini_hochberg_matches_textbook():
    assert benjamini_hochberg([0.01, 0.02, 0.03, 0.04, 0.05]) == \
        pytest.approx([0.05] * 5)
    q = benjamini_hochberg([0.001, 0.30, 0.50, 0.70, 0.90])
    assert q[0] == pytest.approx(0.005)


def test_benjamini_hochberg_passes_through_none():
    assert benjamini_hochberg([0.001, None, 0.5])[1] is None


def test_deflated_sharpe_penalises_selection_from_many_trials():
    """A best-of-N backtest Sharpe is upward biased; DSR must discount it."""
    rng = random.Random(11)
    rets = [rng.gauss(0.012, 0.05) for _ in range(60)]
    one = deflated_sharpe_ratio(rets, n_trials=1)
    many = deflated_sharpe_ratio(rets, n_trials=200)
    assert one["computable"] and many["computable"]
    assert many["deflated_sharpe_probability"] < one["deflated_sharpe_probability"]
    assert many["expected_max_sharpe_under_null"] > one["expected_max_sharpe_under_null"]


def test_deflated_sharpe_needs_enough_periods():
    assert deflated_sharpe_ratio([0.1, 0.2], n_trials=5)["computable"] is False


# ---------------------------------------------- synthesis calibration (Wave S)

@pytest.mark.parametrize("counts", [
    {"a": 3, "b": 3, "c": 3, "d": 3, "e": 3},
    {"a": 1, "b": 1, "c": 1},
    {"a": 2, "b": 2, "c": 2, "d": 2},
])
def test_null_sd_closed_form_matches_monte_carlo(counts):
    rng = random.Random(9)
    sim = [st.fmean([st.fmean([rng.uniform(-1, 1) for _ in range(m)])
                     for m in counts.values()]) for _ in range(40000)]
    assert null_sd(counts) == pytest.approx(st.pstdev(sim), abs=0.01)


def test_high_conviction_is_reachable_when_a_hypothesis_is_deployed():
    """Regression: with strengths clipped to the 0.25 admission cap, |net| could
    never reach the 0.45 'high' threshold — the branch was dead code."""
    import equisense.research.registry as R
    original = R.REGISTRY["HYP-001"]["status"]
    try:
        R.REGISTRY["HYP-001"]["status"] = "deployed"
        built = [ev("t", "technical.trend", "trend", 0.95, "s", [M]),
                 ev("t", "technical.trend", "trend", 0.9, "s", [M])]
        assert max(e.admission_weight for e in built) == pytest.approx(1.0)
        s = synthesize([e for e in built if e] +
                       [E("value", 0.9), E("value", 0.85),
                        E("quality", 0.9), E("quality", 0.8),
                        E("risk", 0.85), E("risk", 0.9)])
        assert s.conviction_ceiling == "high"
        assert abs(s.net_z) >= 3.0
    finally:
        R.REGISTRY["HYP-001"]["status"] = original


def test_abstain_disagreement_is_reachable():
    """Regression: dispersion could not exceed the 0.25 cap, so the
    disagreement branch (threshold 0.55) was unreachable."""
    s = synthesize([E("trend", 0.95), E("value", -0.95),
                    E("quality", 0.9), E("risk", -0.9), E("flow", 0.85)])
    assert s.dispersion > MAX_DISPERSION
    assert s.verdict == "abstain_disagreement"


def test_abstention_is_modal_under_the_null():
    """Random (uninformative) names must overwhelmingly abstain."""
    rng = random.Random(13)
    abstains = 0
    trials = 1500
    for _ in range(trials):
        E_ = [E(c, rng.uniform(-1, 1))
              for c in ("trend", "trend", "value", "quality", "flow", "risk")]
        if synthesize(E_).verdict.startswith("abstain"):
            abstains += 1
    assert abstains / trials > 0.90


def test_confidence_components_stay_bounded():
    s = synthesize([E("trend", 0.5), E("value", 0.5), E("quality", 0.5),
                    E("risk", 0.5)])
    for k, v in s.confidence["components"].items():
        assert 0.0 <= v <= 1.0, f"{k} out of range"
    assert 0.0 <= s.confidence["score"] <= 1.0


def test_underpowered_base_rate_does_not_promote_to_t2():
    """An inadmissible study must not be dressed up as a validated base rate,
    because T2 status feeds confidence.base_rate_depth."""
    thin = {"admissible": False, "admissibility_reason": "underpowered: N_eff=4",
            "n_eff": 4, "survives_multiplicity": False}
    e = ev("t", "technical.trend", "trend", 0.8, "s", [M], base_rate=thin)
    assert e.tier == "T1"
    assert any("NOT admissible" in c for c in e.caveats)


def test_admissible_base_rate_promotes_to_t2():
    good = {"admissible": True, "n_eff": 200, "survives_multiplicity": True}
    e = ev("t", "technical.trend", "trend", 0.8, "s", [M], base_rate=good)
    assert e.tier == "T2"


# ------------------------------------------ correlation-aware null (Wave S+)

def test_null_sd_reduces_to_the_independence_form_without_correlation():
    counts = {"a": 3, "b": 3, "c": 3}
    closed = math.sqrt(sum(1.0 / m for m in counts.values()) / 3.0 / (len(counts) ** 2))
    assert null_sd(counts) == pytest.approx(closed, rel=1e-12)


def test_null_sd_widens_when_clusters_are_positively_correlated():
    counts = {"a": 2, "b": 2, "c": 2}
    corr = {"clusters": ["a", "b", "c"],
            "matrix": [[1.0, 0.6, 0.6], [0.6, 1.0, 0.6], [0.6, 0.6, 1.0]]}
    assert null_sd(counts, cluster_corr=corr) > null_sd(counts)


def test_null_sd_narrows_when_clusters_offset():
    """Measured live: trend~value is -0.39 and flow~risk +0.57. Negatively
    correlated clusters genuinely reduce the null dispersion of their mean."""
    counts = {"a": 2, "b": 2}
    corr = {"clusters": ["a", "b"], "matrix": [[1.0, -0.5], [-0.5, 1.0]]}
    assert null_sd(counts, cluster_corr=corr) < null_sd(counts)


def test_correlation_matters_most_once_weights_stop_being_uniform():
    """The reason the general quadratic form is worth carrying. Under equal
    weights the measured cross-cluster correlations nearly cancel; under the
    tilted weights that learned posteriors will produce, they do not."""
    counts = {"trend": 3, "value": 2, "quality": 2, "flow": 2, "risk": 2}
    clusters = ["trend", "value", "quality", "flow", "risk"]
    M = [[1.0, -0.390, -0.004, -0.350, 0.204],
         [-0.390, 1.0, 0.195, 0.027, -0.192],
         [-0.004, 0.195, 1.0, -0.101, 0.118],
         [-0.350, 0.027, -0.101, 1.0, 0.569],
         [0.204, -0.192, 0.118, 0.569, 1.0]]
    corr = {"clusters": clusters, "matrix": M}
    uniform_gap = null_sd(counts, cluster_corr=corr) / null_sd(counts)
    tilt = {"trend": 1.5, "value": 0.5, "quality": 0.6, "flow": 1.4, "risk": 1.5}
    tilted_gap = (null_sd(counts, weights=tilt, cluster_corr=corr)
                  / null_sd(counts, weights=tilt))
    assert uniform_gap < 1.05, "equal weights: offsetting pairs nearly cancel"
    assert tilted_gap > 1.08, "tilted weights: they stop cancelling"


def test_unmeasured_pairs_fall_back_to_independence():
    counts = {"a": 2, "b": 2}
    partial = {"clusters": ["a"], "matrix": [[1.0]]}
    assert null_sd(counts, cluster_corr=partial) == pytest.approx(null_sd(counts))


def test_shrinkage_pulls_a_noisy_matrix_toward_independence():
    """A 5x5 correlation from ~39 complete cases has per-element sampling sd of
    about 1/sqrt(n) = 0.16 — the same order as several entries."""
    M = [[1.0, -0.9], [-0.9, 1.0]]
    tiny = shrink_correlation(M, n_obs=8)
    large = shrink_correlation(M, n_obs=5000)
    assert abs(tiny[0][1]) < abs(large[0][1]) < 0.9 + 1e-9
    assert large[0][1] == pytest.approx(-0.9, abs=0.02)
    assert all(row[i] == 1.0 for i, row in enumerate(tiny))


def test_shrinkage_degenerates_to_identity_on_no_data():
    out = shrink_correlation([[1.0, 0.8], [0.8, 1.0]], n_obs=0)
    assert out == [[1.0, 0.0], [0.0, 1.0]]
