"""Self-refinement loop: cluster posteriors, gated weights, calibrated
probabilities — all from synthetic ledger histories with known properties."""
import pytest

from equisense.research import learning as L


def _dossier(h, clusters, net=0.2, verdict="long_candidate"):
    return {"kind": "dossier", "hash": h, "verdict": verdict, "net_score": net,
            "cluster_scores": clusters, "claim": {"direction": 1}}


def _score(h, realized, hit, p=0.55):
    return {"kind": "score", "scores_dossier_hash": h, "company": "T",
            "realized_excess_pct": realized, "hit": hit,
            "stated_probability": p, "brier": (p - (1 if hit else 0)) ** 2}


def _history(n, trend_right_rate=0.8):
    """n scored claims where the trend cluster is right `trend_right_rate` of
    the time and the value cluster is right 50/50."""
    recs = []
    for i in range(n):
        trend_right = (i % 10) < trend_right_rate * 10
        realized = 5.0 if trend_right else -5.0
        recs.append(_dossier(f"h{i}", {"trend": 0.3, "value": 0.3 if i % 2 else -0.3}))
        recs.append(_score(f"h{i}", realized, hit=realized > 0))
    return recs


def test_posteriors_learn_which_cluster_is_right():
    recs = _history(60)
    post = L.cluster_posteriors(recs)
    assert post["trend"]["n"] == 60
    assert post["trend"]["posterior_mean"] > 0.7   # trend called it ~80%
    assert 0.35 < post["value"]["posterior_mean"] < 0.65  # value = coin flip
    assert not post["trend"]["unlocked"]           # 60 < 150: still gated


def test_weights_stay_uniform_below_gate():
    w, status = L.cluster_weights(_history(60))
    assert w is None
    assert "provisional" in status


def test_weights_unlock_past_gate_and_are_bounded():
    w, status = L.cluster_weights(_history(200))
    assert w is not None and "learned" in status
    assert w["trend"] > 1.0            # the right cluster earns weight
    assert 0.5 <= min(w.values()) and max(w.values()) <= 1.5
    assert w["value"] == pytest.approx(1.0, abs=0.35)  # near-coin-flip ≈ neutral


def test_learned_weights_change_synthesis():
    from equisense.engine.evidence import Evidence
    from equisense.engine.synthesis import synthesize
    def E(cluster, s):
        return Evidence(engine="t", family="f", cluster=cluster, tier="T1",
                        direction="long" if s > 0 else "short", strength=s,
                        horizon="months", statement="s")
    ev = [E("trend", 0.25), E("value", -0.25), E("quality", 0.05)]
    uniform = synthesize(ev)
    weighted = synthesize(ev, weights={"trend": 1.5, "value": 0.5, "quality": 1.0})
    assert weighted.net_score > uniform.net_score  # trend now speaks louder


def test_probability_provisional_below_min():
    p, basis = L.calibrated_probability(0.3, _history(10))
    assert p == pytest.approx(0.575)
    assert "provisional" in basis


def test_probability_calibrates_from_history():
    # 80% realized hit rate → calibrated p should sit well above the
    # provisional map's 0.55-ish and reflect history (shrunk toward 0.5)
    p, basis = L.calibrated_probability(0.2, _history(90, trend_right_rate=0.8))
    assert "calibrated" in basis
    assert 0.6 < p < 0.85


def test_old_records_without_attribution_are_skipped():
    recs = [{"kind": "dossier", "hash": "old", "verdict": "long_candidate",
             "net_score": 0.2, "claim": {"direction": 1}},  # no cluster_scores
            _score("old", 4.0, True)]
    assert L.cluster_posteriors(recs) == {}


def test_learning_state_is_self_describing():
    st = L.learning_state(_history(20))
    assert st["scored_claims"] == 20
    assert not st["calibration_engaged"]
    assert "pre-registered" in st["how_it_learns"]
    assert len(st["recent_outcomes"]) == 12
