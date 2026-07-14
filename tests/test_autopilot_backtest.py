"""Autopilot policy behavior + backtest statistics."""
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense.db import Base
from equisense.models import AppSnapshot, Company, MacroObservation, PriceObservation


@pytest.fixture
def world(monkeypatch):
    """Two companies with prices, a crafted universe snapshot, stubbed regime
    and dossier builder, file-mode temp ledger."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    SL = sessionmaker(bind=eng, expire_on_commit=False)
    s = SL()
    a = Company(ticker="AAA", name="Alpha", sector="IT")
    b = Company(ticker="BBB", name="Beta", sector="Energy")
    s.add_all([a, b]); s.flush()
    d0 = date.today() - timedelta(days=400)
    for i in range(400):
        d = d0 + timedelta(days=i)
        s.add(PriceObservation(company_id=a.id, obs_date=d, close=100.0, volume=1e6))
        s.add(PriceObservation(company_id=b.id, obs_date=d, close=50.0, volume=1e6))
        s.add(MacroObservation(symbol="^NSEI", role="index", obs_date=d, close=20000.0))
    def item(c, price):
        return {"id": c.id, "ticker": c.ticker, "name": c.name, "sector": c.sector,
                "cap_band": "large", "is_financial": False, "price": price,
                "chg_1d_pct": 0.0, "adv_cr": 50.0, "spark": [price] * 10,
                "signals": {k: 1.0 for k in
                            ["momentum", "dist_52w", "trend", "rel_strength", "mqi",
                             "vol", "heat", "f_score", "z_score", "ccs", "fragility",
                             "exp_gap", "pe_pctile", "revenue_cagr_pct", "roic_pct",
                             "pe", "dividend_yield_pct", "debt_to_equity",
                             "implied_growth_gap_pct"]}}
    snap = {"as_of": str(date.today()), "version": 3, "built_at": "t",
            "companies": [item(a, 100.0), item(b, 50.0)]}
    s.add(AppSnapshot(key="universe", as_of=snap["as_of"], payload=json.dumps(snap)))
    s.commit()

    import equisense.db as D
    import equisense.ledger as L
    monkeypatch.setattr(D, "get_session", lambda: SL())
    monkeypatch.setattr(L, "get_session", lambda: SL())
    monkeypatch.setattr(L, "STORAGE", "file")
    monkeypatch.setattr(L, "LEDGER_PATH",
                        __import__("pathlib").Path("/tmp") / "ap_test_ledger.jsonl")
    L.LEDGER_PATH.unlink(missing_ok=True)

    import equisense.api.live as live
    monkeypatch.setattr(live, "current_regime",
                        lambda s_: {"label": "test", "conditioning_key": "all",
                                    "flags": [], "components": []})
    monkeypatch.setattr(live, "build_dossier",
                        lambda s_, c, book_value=0, **kw: {
                            "sizing": {"recommended_shares": 5},
                            "ledger": {"hash": "d" * 64}})
    return s, a, b


def _force_long(monkeypatch, tickers):
    """Make qualified_candidates deterministic: given tickers become tradable
    long candidates with fixed sizing."""
    import equisense.api.candidates as C
    def fake(session, top_n=8, book_value=None, cash=None):
        return {"as_of": "t", "regime": "test", "scanned": 2,
                "verdict_counts": {"long": len(tickers), "avoid": 0, "abstain": 0},
                "weights_status": "uniform",
                "candidates": [{"id": i + 1, "ticker": t, "name": t, "sector": "IT",
                                "price": 100.0, "net_score": 0.2,
                                "conviction_band": "low", "dispersion": 0.1,
                                "drivers": ["momentum +10%"], "dissent": [],
                                "sizing": {"shares": 5, "value": 500.0,
                                           "stop_distance_pct": 3.0, "binding": "risk_budget"},
                                "round_trip_cost_pct": 0.25, "breakeven_move_pct": 0.25,
                                "gates": [], "tradable": True}
                               for i, t in enumerate(tickers)],
                "discipline_note": ""}
    import equisense.api.autopilot as AP
    monkeypatch.setattr(C, "qualified_candidates", fake)
    return fake


def test_autopilot_enters_qualified_candidates(world, monkeypatch):
    s, a, b = world
    _force_long(monkeypatch, ["AAA", "BBB"])
    from equisense.api.autopilot import run_autopilot, set_config
    set_config(s, {"enabled": True, "max_new_per_run": 1})
    rep = run_autopilot(s)
    assert len(rep["entries"]) == 1                      # run cap respected
    assert rep["entries"][0]["dossier"] == "d" * 16      # dossier-linked
    assert any("run cap" in x for x in rep["skipped"])


def test_autopilot_respects_cash_reserve(world, monkeypatch):
    s, a, b = world
    _force_long(monkeypatch, ["AAA"])
    from equisense.api.autopilot import run_autopilot, set_config
    from equisense.api.paper import reset_account
    reset_account(s, 600.0)  # tiny account: 5 sh × 100 = 500 > 600 − 10% reserve
    set_config(s, {"enabled": True, "cash_reserve_pct": 20.0})
    rep = run_autopilot(s)
    assert not rep["entries"]
    assert any("cash reserve" in x for x in rep["skipped"])


def test_autopilot_time_exit(world, monkeypatch):
    s, a, b = world
    from equisense.api.autopilot import run_autopilot, set_config
    from equisense.models import PaperTrade
    _force_long(monkeypatch, [])
    s.add(PaperTrade(company_id=a.id, side="buy", quantity=10, price=100.0,
                     trade_date=date.today() - timedelta(days=400)))
    s.commit()
    set_config(s, {"enabled": True})
    rep = run_autopilot(s)
    assert len(rep["exits"]) == 1
    assert "time exit" in rep["exits"][0]["reason"]


def test_autopilot_stop_breach_exit(world, monkeypatch):
    s, a, b = world
    from equisense.api.autopilot import run_autopilot, set_config
    from equisense.models import PaperTrade
    _force_long(monkeypatch, [])
    # bought at 200, price is 100 → far beyond any stop
    s.add(PaperTrade(company_id=a.id, side="buy", quantity=10, price=200.0,
                     trade_date=date.today() - timedelta(days=5)))
    s.commit()
    set_config(s, {"enabled": True})
    rep = run_autopilot(s)
    assert len(rep["exits"]) == 1
    assert "stop breach" in rep["exits"][0]["reason"]


def test_disabled_autopilot_does_nothing(world, monkeypatch):
    s, a, b = world
    _force_long(monkeypatch, ["AAA"])
    from equisense.api.autopilot import run_autopilot, set_config
    set_config(s, {"enabled": False})
    rep = run_autopilot(s)
    assert not rep["entries"] and not rep["exits"]
    assert any("disabled" in x for x in rep["skipped"])


# ------------------------------------------------------------- backtest

def test_bootstrap_ci_detects_signal_and_null():
    from equisense.research.backtest import moving_block_bootstrap_ci
    import random
    rng = random.Random(1)
    signal = [rng.gauss(5.0, 2.0) for _ in range(200)]   # true median ≈ 5
    lo, hi = moving_block_bootstrap_ci(signal, block_len=6)
    assert lo > 0, "CI must exclude zero for a real effect"
    null = [rng.gauss(0.0, 2.0) for _ in range(200)]
    lo0, hi0 = moving_block_bootstrap_ci(null, block_len=6)
    assert lo0 < 0 < hi0, "CI must straddle zero for noise"


def test_bootstrap_ci_small_sample_is_nan():
    from equisense.research.backtest import moving_block_bootstrap_ci
    lo, hi = moving_block_bootstrap_ci([1.0, 2.0], block_len=1)
    assert lo != lo and hi != hi  # NaN — no fake certainty from n=2
