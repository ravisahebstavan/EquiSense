"""Paper trading account: fills at EOD close, cash/position validation,
equity math, NIFTY counterfactual alpha."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense.db import Base
from equisense.models import Company, MacroObservation, PriceObservation


@pytest.fixture
def sess(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    SL = sessionmaker(bind=eng, expire_on_commit=False)
    s = SL()
    c = Company(ticker="TEST", name="Test Co", sector="IT")
    s.add(c); s.flush()
    d0 = date(2026, 1, 5)
    for i in range(40):  # rising price 100 → 139
        d = d0 + timedelta(days=i)
        s.add(PriceObservation(company_id=c.id, obs_date=d, close=100.0 + i))
        s.add(MacroObservation(symbol="^NSEI", role="index", obs_date=d,
                               close=20000.0 + i * 10))  # NIFTY +~2%
    s.commit()
    # ledger writes go to a temp file, not the real one
    import equisense.ledger as L
    monkeypatch.setattr(L, "LEDGER_PATH", __import__("pathlib").Path("/tmp") / "paper_test_ledger.jsonl")
    L.LEDGER_PATH.unlink(missing_ok=True)
    monkeypatch.setattr(L, "STORAGE", "file")
    return s, c.id


def test_buy_fills_at_latest_close_and_debits_cash(sess):
    from equisense.api.paper import account, place_trade
    s, cid = sess
    r = place_trade(s, cid, "buy", 100)
    assert r["fill_price"] == 139.0  # latest close
    a = account(s)
    assert a["cash"] == pytest.approx(1_000_000 - 100 * 139.0)
    assert a["positions"][0]["quantity"] == 100
    assert a["equity"] == pytest.approx(1_000_000)  # bought at the mark


def test_insufficient_cash_rejected(sess):
    from equisense.api.paper import place_trade
    s, cid = sess
    with pytest.raises(ValueError, match="insufficient cash"):
        place_trade(s, cid, "buy", 100_000)


def test_oversell_rejected(sess):
    from equisense.api.paper import place_trade
    s, cid = sess
    place_trade(s, cid, "buy", 10)
    with pytest.raises(ValueError, match="insufficient position"):
        place_trade(s, cid, "sell", 20)


def test_alpha_vs_nifty_counterfactual(sess):
    """Stock flat after purchase (bought at the last close) while the same
    cash in NIFTY would also be flat → alpha ≈ 0 by construction here; the
    important assertions are the mechanics, not a fabricated win."""
    from equisense.api.paper import account, place_trade
    s, cid = sess
    place_trade(s, cid, "buy", 100)
    a = account(s)
    assert a["benchmark"] is not None
    assert a["curve"], "equity curve should exist after a trade"
    assert a["alpha_pct"] == pytest.approx(
        a["total_return_pct"] - a["benchmark"]["total_return_pct"], abs=1e-6)
    assert "honest alpha" in a["alpha_note"]


def test_trades_are_ledger_registered(sess):
    from equisense.api.paper import place_trade
    from equisense import ledger as L
    s, cid = sess
    place_trade(s, cid, "buy", 5, dossier_hash="abc123")
    recs = [r for r in L.read_all() if r["kind"] == "paper_trade"]
    assert recs and recs[-1]["from_dossier"] == "abc123"
    assert L.verify_chain()["intact"]


def test_reset(sess):
    from equisense.api.paper import account, place_trade, reset_account
    s, cid = sess
    place_trade(s, cid, "buy", 10)
    reset_account(s, 500_000)
    a = account(s)
    assert a["cash"] == 500_000 and not a["positions"] and not a["trades"]
