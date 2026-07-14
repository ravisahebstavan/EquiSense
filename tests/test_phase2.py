"""Phase II Wave R: vault, N_eff, percentile normalization, admission caps,
wrongful-abstention accounting."""
import json

import pytest

from equisense.engine.evidence import ev, xsec_strength
from equisense.engine.synthesis import synthesize
from equisense.engine.types import Metric
from equisense.research.base_rates import n_effective
from equisense.research.registry import REGISTRY, admission_cap


# ------------------------------------------------------------------- vault

def test_vault_roundtrip_and_idempotency(tmp_path, monkeypatch):
    from equisense.ingestion import vault as V
    monkeypatch.setattr(V, "VAULT_DIR", tmp_path)
    monkeypatch.setattr(V, "INDEX_PATH", tmp_path / "index.jsonl")
    h1 = V.store_raw({"a": 1, "b": [2, 3]}, "prov", "ep1", {"k": "v"})
    h2 = V.store_raw({"b": [2, 3], "a": 1}, "prov", "ep2")  # same content, sorted keys
    assert h1 == h2  # content-addressed: identical payloads share one blob
    assert json.loads(V.read_raw(h1)) == {"a": 1, "b": [2, 3]}
    stats = V.vault_stats()
    assert stats["unique_blobs"] == 1 and stats["artifacts"] == 2


def test_vault_blobs_are_immutable(tmp_path, monkeypatch):
    from equisense.ingestion import vault as V
    monkeypatch.setattr(V, "VAULT_DIR", tmp_path)
    monkeypatch.setattr(V, "INDEX_PATH", tmp_path / "index.jsonl")
    h = V.store_raw({"x": 1}, "p", "e")
    blob = tmp_path / h[:2] / f"{h}.json.gz"
    mtime = blob.stat().st_mtime_ns
    V.store_raw({"x": 1}, "p", "e")  # re-store identical
    assert blob.stat().st_mtime_ns == mtime  # never rewritten


# ------------------------------------------------------------------- N_eff

def test_n_effective_overlap_correction():
    assert n_effective(1000, 126) == 166   # 1000 × 21/126
    assert n_effective(1000, 63) == 333
    assert n_effective(1000, 21) == 1000   # no overlap at sampling cadence
    assert n_effective(100, 10) == 100     # horizon shorter than sampling


# ------------------------------------------- percentile normalization (A2)

def test_xsec_strength_is_rank_based():
    values = {f"S{i}": float(i) for i in range(20)}
    top = xsec_strength(values, "S19")
    bottom = xsec_strength(values, "S0")
    mid = xsec_strength(values, "S10")
    assert top == pytest.approx(1.0)
    assert bottom == pytest.approx(-0.9)   # rank 1/20 → 2·(0.05−0.5)
    assert -0.1 < mid < 0.2
    assert xsec_strength(values, "S19", invert=True) == pytest.approx(-1.0)


def test_xsec_strength_needs_enough_peers():
    values = {f"S{i}": float(i) for i in range(9)}
    assert xsec_strength(values, "S1") is None  # 9 peers → noise, not context


def test_xsec_strength_outlier_robust():
    values = {f"S{i}": float(i) for i in range(19)}
    values["MOON"] = 1e9  # absurd outlier
    # rank-based: outlier takes top slot but does not distort others' strengths
    assert xsec_strength(values, "S18") == pytest.approx(2 * (19 / 20 - 0.5))


# ----------------------------------------------------- admission caps (A3)

M = Metric(key="k", label="L", value=1.0, unit="x", formula="f", inputs={})


def test_deferred_hypotheses_are_shadow():
    cap, _ = admission_cap("novel.ccs")
    assert cap == 0.0
    e = ev("novel", "novel.ccs", "quality", 0.9, "CCS high", [M])
    assert e.direction == "shadow"
    assert e.strength == 0.0
    assert any("SHADOW" in c for c in e.caveats)


def test_exploratory_families_capped():
    cap, _ = admission_cap("technical.trend")
    assert cap == 0.25
    e = ev("technical", "technical.trend", "trend", 0.95, "strong momentum", [M])
    assert e.strength == pytest.approx(0.25)
    assert any("capped" in c for c in e.caveats)


def test_shadow_excluded_from_synthesis():
    strong_shadow = ev("novel", "novel.ccs", "quality", 1.0, "s", [M])
    real = [ev("t", "technical.trend", "trend", 0.9, "s", [M]),
            ev("t", "novel.value", "value", 0.9, "s", [M]),
            ev("t", "risk.volatility", "risk", 0.9, "s", [M])]
    s = synthesize(real + [strong_shadow])
    assert "quality" not in s.cluster_scores  # shadow never aggregates
    # all real evidence capped at 0.25 → net small but positive
    assert 0 < s.net_score <= 0.25


def test_reg001_is_registered():
    assert "REG-001" in REGISTRY
    assert REGISTRY["REG-001"]["family"] == "meta.regime"


# ------------------------------------------- wrongful abstention (A10 fix)

def test_calibration_report_counts_wrongful_abstentions(tmp_path, monkeypatch):
    from equisense import ledger as L
    monkeypatch.setattr(L, "LEDGER_PATH", tmp_path / "dossiers.jsonl")
    L._write({"kind": "score", "claim_type": "abstention_counterfactual",
              "created_at": "2026-01-01", "scores_dossier_hash": "h1",
              "company": "A", "realized_excess_pct": 12.0,
              "wrongful_abstention": True, "hit": None,
              "stated_probability": None, "brier": None})
    L._write({"kind": "score", "claim_type": "abstention_counterfactual",
              "created_at": "2026-01-01", "scores_dossier_hash": "h2",
              "company": "B", "realized_excess_pct": -3.0,
              "wrongful_abstention": False, "hit": None,
              "stated_probability": None, "brier": None})
    L._write({"kind": "score", "claim_type": "directional_excess",
              "created_at": "2026-01-01", "scores_dossier_hash": "h3",
              "company": "C", "realized_excess_pct": 4.0, "hit": True,
              "stated_probability": 0.6, "brier": 0.16})
    rep = L.calibration_report()
    assert rep["scored_claims"] == 1
    assert rep["scored_abstentions"] == 2
    assert rep["wrongful_abstention_rate"] == pytest.approx(0.5)
    assert rep["hit_rate"] == 1.0
