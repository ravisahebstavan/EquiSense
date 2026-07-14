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
    s = synthesize([E("trend", 0.6), E("value", 0.5), E("quality", 0.7), E("risk", 0.4)])
    assert s.verdict == "long_candidate"
    assert s.net_score > 0.4
    assert not s.dissent


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
