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
    """Midrank percentile: (below + half the ties) / n.

    The old `x <= v` convention was ASYMMETRIC — the minimum mapped to -0.90
    while the maximum mapped to +1.00, so the scale had a built-in bullish tilt
    even with no ties at all. Midrank makes it symmetric."""
    values = {f"S{i}": float(i) for i in range(20)}
    top = xsec_strength(values, "S19")
    bottom = xsec_strength(values, "S0")
    mid = xsec_strength(values, "S10")
    assert top == pytest.approx(0.95)      # (19 + 0.5)/20 → 2·(0.975−0.5)
    assert bottom == pytest.approx(-0.95)  # (0 + 0.5)/20  → 2·(0.025−0.5)
    assert top == pytest.approx(-bottom), "the scale must be symmetric"
    assert -0.1 < mid < 0.2
    assert xsec_strength(values, "S19", invert=True) == pytest.approx(-0.95)


def test_xsec_strength_centres_a_fully_tied_universe():
    """The bug this replaced: with every value identical, `x <= v` gave EVERY
    name rank 1.0 — maximum bullish conviction from a signal carrying no
    information whatsoever."""
    tied = {f"S{i}": 5.0 for i in range(20)}
    assert xsec_strength(tied, "S0") == pytest.approx(0.0)


def test_xsec_strength_ties_do_not_inflate_the_modal_cohort():
    """Integer signals like Piotroski F make ties the norm, not the exception.
    Measured on a realistic 50-name distribution the modal F=7 cohort scored
    +0.48 under the old convention against a correct +0.24 — an error of 0.24
    on a [-1,+1] scale, always in the same direction."""
    dist = [7] * 12 + [6] * 10 + [8] * 8 + [5] * 7 + [9] * 5 + [4] * 4 + [3] * 4
    values = {f"S{i}": float(v) for i, v in enumerate(dist)}
    name = next(k for k, v in values.items() if v == 7.0)
    n = len(values)
    less = sum(1 for v in values.values() if v < 7.0)
    eq = sum(1 for v in values.values() if v == 7.0)
    assert xsec_strength(values, name) == pytest.approx(2 * ((less + 0.5 * eq) / n - 0.5))
    assert xsec_strength(values, name) < 0.30, "the modal cohort must not read as strong"


def test_xsec_strength_needs_enough_peers():
    values = {f"S{i}": float(i) for i in range(9)}
    assert xsec_strength(values, "S1") is None  # 9 peers → noise, not context


def test_xsec_strength_outlier_robust():
    values = {f"S{i}": float(i) for i in range(19)}
    values["MOON"] = 1e9  # absurd outlier
    # rank-based: outlier takes top slot but does not distort others' strengths
    assert xsec_strength(values, "S18") == pytest.approx(2 * ((18 + 0.5) / 20 - 0.5))


# ----------------------------------------------------- admission caps (A3)

M = Metric(key="k", label="L", value=1.0, unit="x", formula="f", inputs={})


def test_deferred_hypotheses_are_shadow():
    cap, _ = admission_cap("novel.ccs")
    assert cap == 0.0
    e = ev("novel", "novel.ccs", "quality", 0.9, "CCS high", [M])
    assert e.direction == "shadow"
    assert e.strength == 0.0
    assert any("SHADOW" in c for c in e.caveats)


def test_exploratory_families_carry_admission_weight_not_clipped_strength():
    """Wave S: admission control is a WEIGHT, not a clip.

    Clipping the measurement to the cap destroyed information and — because
    every live hypothesis sits at 0.25 — had made 'high' conviction and
    'abstain_disagreement' unreachable by construction. The measurement is now
    preserved at full range and the trust in it travels alongside.
    """
    cap, _ = admission_cap("technical.trend")
    assert cap == 0.25
    e = ev("technical", "technical.trend", "trend", 0.95, "strong momentum", [M])
    assert e.strength == pytest.approx(0.95), "measurement must not be clipped"
    assert e.admission_weight == pytest.approx(0.25)
    assert any("admission weight" in c for c in e.caveats)


def test_exploratory_evidence_cannot_buy_high_conviction():
    """The discipline the cap existed to enforce, now enforced explicitly."""
    strong = [ev("t", f, c, 0.95, "s", [M]) for c, f in
              (("trend", "technical.trend"), ("value", "novel.value"),
               ("quality", "quality.fscore"), ("risk", "risk.volatility"),
               ("flow", "novel.crowding"))]
    s = synthesize([e for e in strong if e])
    assert s.conviction_ceiling == "low"
    assert s.conviction_band == "low", "exploratory families must not reach high conviction"


def test_shadow_excluded_from_synthesis():
    strong_shadow = ev("novel", "novel.ccs", "quality", 1.0, "s", [M])
    real = [ev("t", "technical.trend", "trend", 0.9, "s", [M]),
            ev("t", "novel.value", "value", 0.9, "s", [M]),
            ev("t", "risk.volatility", "risk", 0.9, "s", [M])]
    s = synthesize(real + [strong_shadow])
    assert "quality" not in s.cluster_scores  # shadow never aggregates
    assert strong_shadow.admission_weight == 0.0
    # full-range measurement is preserved, so net now reflects the real evidence
    assert 0 < s.net_score <= 1.0
    assert s.net_score == pytest.approx(0.9, abs=1e-6)


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


# -------------------------------------------------- ledger integrity (Wave S)

def test_unmatched_sell_is_counted_not_absorbed():
    """FIFO used to run out of lots and stop, silently. Selling 50 while holding
    10 produced the same position and the same realised P&L as selling 10, so a
    mistyped quantity was indistinguishable from a correct entry."""
    import datetime as dt
    from equisense.engine.portfolio import (Transaction, ledger_integrity,
                                            positions_from_ledger)
    pos = positions_from_ledger([
        Transaction(1, "buy", 10, 100.0, dt.date(2024, 1, 1)),
        Transaction(1, "sell", 50, 110.0, dt.date(2024, 2, 1))])
    assert pos[1].quantity == pytest.approx(0.0)
    assert pos[1].unmatched_sell_qty == pytest.approx(40.0)
    rep = ledger_integrity(pos)
    assert rep["ok"] is False
    assert rep["unmatched_sells"] == {1: pytest.approx(40.0)}
    assert "no matching open lot" in rep["warning"]


def test_sell_before_buy_is_flagged_even_though_the_position_looks_fine():
    """The dangerous case: quantity and cost basis end up plausible, so nothing
    downstream looks wrong — but realised P&L is missing a leg."""
    import datetime as dt
    from equisense.engine.portfolio import (Transaction, ledger_integrity,
                                            positions_from_ledger)
    pos = positions_from_ledger([
        Transaction(1, "sell", 5, 110.0, dt.date(2024, 1, 1)),
        Transaction(1, "buy", 10, 100.0, dt.date(2024, 2, 1))])
    assert pos[1].quantity == pytest.approx(10.0)       # looks like a clean lot
    assert pos[1].realized_pnl == pytest.approx(0.0)    # ...but earned nothing
    assert ledger_integrity(pos)["ok"] is False


def test_a_correct_ledger_reports_clean():
    import datetime as dt
    from equisense.engine.portfolio import (Transaction, ledger_integrity,
                                            positions_from_ledger)
    pos = positions_from_ledger([
        Transaction(1, "buy", 10, 100.0, dt.date(2024, 1, 1)),
        Transaction(1, "sell", 4, 110.0, dt.date(2024, 2, 1))])
    rep = ledger_integrity(pos)
    assert rep == {"ok": True, "unmatched_sells": {}, "warning": None}
    assert pos[1].realized_pnl == pytest.approx(40.0)


def test_fully_oversold_name_is_absent_from_holdings_but_present_in_the_warning():
    """Why the check has to live outside the holdings table: the position nets
    to zero quantity, so the one place a user would look shows nothing."""
    import datetime as dt
    from equisense.engine.portfolio import (Transaction, ledger_integrity,
                                            positions_from_ledger)
    pos = positions_from_ledger([
        Transaction(7, "buy", 10, 100.0, dt.date(2024, 1, 1)),
        Transaction(7, "sell", 500, 110.0, dt.date(2024, 2, 1))])
    visible = [cid for cid, p in pos.items() if p.quantity > 1e-9]
    assert visible == []
    assert ledger_integrity(pos)["unmatched_sells"] == {7: pytest.approx(490.0)}
