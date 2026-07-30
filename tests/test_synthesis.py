"""Synthesis plane invariants: abstention, dissent surfacing, monotonicity."""
from equisense.engine.evidence import Evidence
from equisense.engine.synthesis import synthesize


def E(cluster, strength, tier="T1"):
    return Evidence(engine="t", family="f", cluster=cluster, tier=tier,
                    direction="long" if strength > 0 else "short",
                    strength=strength, horizon="months", statement="s")


def test_insufficient_clusters_abstains():
    s = synthesize([E("trend", 0.9), E("value", 0.9)])
    assert s.verdict == "abstain_insufficient"
    assert s.conviction_band == "none"


def test_agreement_produces_candidate():
    """Broad agreement across well-covered clusters is a candidate.

    Two evidence per cluster is the realistic coverage a live dossier produces.
    With only ONE reading per cluster the null dispersion of net_score is large
    (0.289) and the same strengths sit at |z| = 1.9 — genuinely marginal, and
    correctly abstained on; see test_thin_coverage_needs_a_bigger_net.
    """
    s = synthesize([E("trend", 0.6), E("trend", 0.55),
                    E("value", 0.5), E("value", 0.45),
                    E("quality", 0.7), E("quality", 0.65),
                    E("risk", 0.4), E("risk", 0.5)])
    assert s.verdict == "long_candidate"
    assert s.net_score > 0.4
    assert abs(s.net_z) >= 2.0
    assert not s.dissent


def test_thin_coverage_needs_a_bigger_net():
    """Wave S invariant: the abstention bar scales with the null dispersion, so
    a thinly-covered name must show a LARGER raw net to qualify. A fixed
    threshold could not express this."""
    strengths = [("trend", 0.6), ("value", 0.5), ("quality", 0.7), ("risk", 0.4)]
    thin = synthesize([E(c, v) for c, v in strengths])
    thick = synthesize([E(c, v) for c, v in strengths] +
                       [E(c, v) for c, v in strengths])
    assert thin.net_score == thick.net_score  # same balance of evidence
    assert thin.null_sd > thick.null_sd       # but thin coverage is noisier
    assert abs(thin.net_z) < abs(thick.net_z)
    assert thin.verdict == "abstain_no_edge"
    assert thick.verdict == "long_candidate"


def test_disagreement_abstains_and_names_dissent():
    s = synthesize([E("trend", 0.9), E("value", -0.9), E("quality", 0.8), E("risk", -0.6)])
    assert s.verdict == "abstain_disagreement"
    assert s.dispersion > 0.55


def test_weak_signal_abstains():
    s = synthesize([E("trend", 0.05), E("value", -0.1), E("quality", 0.1), E("risk", 0.0)])
    assert s.verdict == "abstain_no_edge"


def test_dissent_named_when_candidate():
    s = synthesize([E("trend", 0.7), E("value", -0.4), E("quality", 0.6),
                    E("risk", 0.5), E("flow", 0.6)])
    if s.verdict == "long_candidate":
        assert any("value" in d for d in s.dissent)


def test_confidence_is_decomposed_and_provisional():
    s = synthesize([E("trend", 0.5), E("value", 0.5), E("quality", 0.5)])
    assert set(s.confidence["components"]) == {"agreement", "coverage",
                                               "base_rate_depth", "calibration_history"}
    assert s.confidence["components"]["calibration_history"] == 0.0  # earned, not asserted
    assert "provisional" in s.confidence["label"]


def test_uniform_weights_flagged():
    s = synthesize([E("trend", 0.5), E("value", 0.5), E("quality", 0.5)])
    assert any("provisional" in n.lower() for n in s.notes)
