"""Sizing math, cost/tax physics, regime classifier, and ledger chain integrity."""
import pytest

from equisense.engine.regime import classify_regime, regime_series
from equisense.engine.sizing import (ROUND_TRIP_STATUTORY, SizingInputs,
                                     cost_tax_breakeven, recommend_size)

UP = [100 * (1.001 ** i) for i in range(300)]
DOWN = [100 * (0.999 ** i) for i in range(300)]


def _inputs(**kw):
    base = dict(book_value=1_000_000, price=500.0, daily_vol_pct=1.5,
                conviction_band="moderate", net_score=0.3, adv_cr=50.0,
                max_position_pct=10.0)
    base.update(kw)
    return SizingInputs(**base)


def test_sizing_shows_all_work():
    r = recommend_size(_inputs())
    # risk budget: 1,000,000 × 0.75% × 1.0 × 0.5 haircut = 3,750
    assert r["working"]["risk_budget"] == pytest.approx(3750.0)
    # stop distance: 2.5 × 1.5% = 3.75%; raw value = 3750 / 0.0375 = 100,000
    assert r["stop_distance_pct"] == pytest.approx(3.75)
    assert r["working"]["raw_value_from_risk"] == pytest.approx(100_000.0)
    assert r["recommended_value"] <= 100_000.0
    assert r["binding_constraint"] in ("risk_budget", "position_cap",
                                       "heat_room", "liquidity_cap")


def test_sizing_respects_position_cap():
    r = recommend_size(_inputs(daily_vol_pct=0.2, max_position_pct=5.0))
    assert r["pct_of_book"] <= 5.0 + 0.1


def test_sizing_liquidity_cap_binds_for_illiquid():
    r = recommend_size(_inputs(adv_cr=0.01))  # ₹10 lakh/day ADV
    assert r["binding_constraint"] == "liquidity_cap"
    assert r["recommended_value"] <= 0.01 * 1e7 * 0.05 * 3 + 1


def test_sizing_higher_vol_smaller_position():
    lo = recommend_size(_inputs(daily_vol_pct=1.0))
    hi = recommend_size(_inputs(daily_vol_pct=3.0))
    assert hi["recommended_value"] < lo["recommended_value"]


def test_cost_tax_swing_vs_hold():
    swing = cost_tax_breakeven(100_000, adv_cr=50.0, expected_hold_months=6)
    hold = cost_tax_breakeven(100_000, adv_cr=50.0, expected_hold_months=18)
    assert swing["applicable_tax"] == "STCG 20%"
    assert hold["applicable_tax"] == "LTCG 12.5%"
    assert swing["ltcg_cliff_note"] and "7.5pp" in swing["ltcg_cliff_note"]
    assert swing["statutory_pct"] == pytest.approx(ROUND_TRIP_STATUTORY * 100, abs=1e-3)


def test_no_adv_data_is_punitive_not_optimistic():
    r = cost_tax_breakeven(100_000, adv_cr=None, expected_hold_months=6)
    assert r["impact_estimate_pct"] >= 0.75


def test_regime_classifier():
    vix = [15.0] * 800
    r = classify_regime(UP, vix, [83.0] * 300, [70.0] * 300)
    assert r["label"].startswith("uptrend")
    assert r["conditioning_key"] == "uptrend"
    r2 = classify_regime(DOWN, vix, [83.0] * 300, [70.0] * 300)
    assert r2["conditioning_key"] == "downtrend"


def test_regime_flags_inr_and_crude():
    inr = [80.0] * 240 + [80 * 1.0006 ** i for i in range(60)]  # ~3.7% in 3m
    crude = [70.0] * 240 + [70 * 1.003 ** i for i in range(60)]  # ~20% in 3m
    r = classify_regime(UP, [15.0] * 800, inr, crude)
    assert any("INR" in f for f in r["flags"])
    assert any("crude spiking" in f for f in r["flags"])


def test_regime_series_matches_live_definition():
    labels = regime_series(UP)
    assert labels[100] == "unknown"      # <200 obs
    assert labels[-1] == "uptrend"
    assert regime_series(DOWN)[-1] == "downtrend"


def test_ledger_chain_tamper_evidence(tmp_path, monkeypatch):
    from equisense import ledger as L
    monkeypatch.setattr(L, "LEDGER_PATH", tmp_path / "dossiers.jsonl")
    dossier = {"synthesis": {"verdict": "long_candidate", "net_score": 0.4,
                             "conviction_band": "moderate"},
               "company": {"ticker": "TEST", "price": 100.0},
               "claim_horizon_days": 126}
    r1 = L.register_dossier(dossier)
    L.register_dossier({**dossier, "company": {"ticker": "TEST2", "price": 50.0}})
    assert L.verify_chain()["intact"]
    assert r1["claim"]["stated_probability"] == pytest.approx(0.6)  # 0.5 + 0.4×0.25
    # tamper with record 1 → chain must break
    lines = L.LEDGER_PATH.read_text().splitlines()
    lines[0] = lines[0].replace("TEST", "HACK")
    L.LEDGER_PATH.write_text("\n".join(lines) + "\n")
    assert not L.verify_chain()["intact"]


def test_abstention_registers_counterfactual_claim(tmp_path, monkeypatch):
    """PHASE2 §7.1 (A10): abstentions are scoreable — direction 0, no stated
    probability, but a claim exists so wrongful abstention gets measured."""
    from equisense import ledger as L
    monkeypatch.setattr(L, "LEDGER_PATH", tmp_path / "dossiers.jsonl")
    rec = L.register_dossier({"synthesis": {"verdict": "abstain_no_edge",
                                            "net_score": 0.05, "conviction_band": "none"},
                              "company": {"ticker": "T", "price": 10.0}})
    assert rec["claim"]["type"] == "abstention_counterfactual"
    assert rec["claim"]["direction"] == 0
    assert rec["claim"]["stated_probability"] is None
    assert L.verify_chain()["intact"]
