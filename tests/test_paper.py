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


def test_selling_past_the_holding_flips_to_short(sess):
    """DELIBERATE CHANGE: selling more than held is no longer an error.

    The paper book is direction-agnostic now — synthesis produces qualified
    avoid_short verdicts and refusing to act on them threw away half the
    system's actionable output. A sell that crosses through flat closes the
    long at its own basis and opens the remainder short at the fill price; the
    two sides are never blended into a meaningless average cost.
    """
    from equisense.api.paper import account, place_trade
    s, cid = sess
    place_trade(s, cid, "buy", 10)      # long 10 @ 139
    place_trade(s, cid, "sell", 30)     # close 10, open short 20 @ 139

    pos = account(s)["positions"][0]
    assert pos["quantity"] == pytest.approx(-20)
    assert pos["direction"] == "short"
    assert pos["avg_cost"] == pytest.approx(139.0)   # re-based, not averaged
    assert pos["realized_pnl"] == pytest.approx(0.0)  # closed at its entry


def test_short_is_margin_constrained(sess):
    """A short still has to be funded. Without this an unfunded book could take
    unbounded short exposure, and every risk number computed off it would be
    fiction."""
    from equisense.api.paper import place_trade
    s, cid = sess
    with pytest.raises(ValueError, match="insufficient margin to short"):
        place_trade(s, cid, "sell", 100_000)


def test_short_makes_money_when_the_price_falls(sess):
    """The P&L sign is the whole point of supporting shorts."""
    from equisense.api.paper import account, paper_positions, place_trade
    from equisense.models import PriceObservation
    s, cid = sess

    place_trade(s, cid, "sell", 100)            # short 100 @ 139
    a = account(s)
    assert a["cash"] == pytest.approx(1_000_000 + 100 * 139.0)  # proceeds credited
    assert a["equity"] == pytest.approx(1_000_000)              # no P&L at entry

    s.add(PriceObservation(company_id=cid, obs_date=date(2026, 6, 1), close=119.0))
    s.commit()
    a = account(s)
    assert a["positions"][0]["unrealized_pnl"] == pytest.approx(100 * 20.0)
    assert a["equity"] == pytest.approx(1_000_000 + 100 * 20.0)

    place_trade(s, cid, "buy", 100)             # cover
    a = account(s)
    assert a["positions"] == []
    assert a["equity"] == pytest.approx(1_000_000 + 100 * 20.0)


def test_covering_a_short_does_not_need_new_cash(sess):
    """Buying to cover releases capital; funding it like a fresh long purchase
    would block legitimate exits when cash is tied up."""
    from equisense.api.paper import account, place_trade
    s, cid = sess
    place_trade(s, cid, "sell", 7000)                 # large short, funded
    assert account(s)["cash"] < 1_000_000 + 7000 * 139.0 + 1
    place_trade(s, cid, "buy", 7000)                  # cover must be allowed
    assert account(s)["positions"] == []


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


def test_benchmark_falls_back_to_nifty50_and_says_so(sess):
    """A missing NIFTY 500 series must not silently delete the benchmark — but
    the substitution has to be visible, because the two indices differ by
    roughly the size premium (10.94%/yr vs 12.33%/yr over the stored decade)."""
    from equisense.api.paper import _nifty_counterfactual, place_trade
    from equisense.models import PaperTrade
    from sqlalchemy import select as _select
    s, cid = sess
    place_trade(s, cid, "buy", 100)
    trades = s.scalars(_select(PaperTrade)).all()
    b = _nifty_counterfactual(s, trades, 1_000_000.0, symbol="^CRSLDX")
    assert b is not None, "benchmark must not vanish when the index is absent"
    assert b["fell_back"] is True
    assert b["symbol"] == "^NSEI" and b["index"] == "NIFTY 50"


def test_benchmark_prefers_nifty500_when_present(sess):
    """The screen ranks a ~500-name universe, so NIFTY 500 is the honest
    opportunity set; NIFTY 50 would hand it the size premium as alpha."""
    import datetime as dt

    from equisense.api.paper import _nifty_counterfactual, place_trade
    from equisense.models import MacroObservation, PaperTrade
    from sqlalchemy import select as _select
    s, cid = sess
    place_trade(s, cid, "buy", 100)
    for i in range(40):
        s.add(MacroObservation(symbol="^CRSLDX", role="index",
                               obs_date=dt.date(2024, 1, 1) + dt.timedelta(days=i),
                               close=20000.0 + i))
    s.commit()
    trades = s.scalars(_select(PaperTrade)).all()
    b = _nifty_counterfactual(s, trades, 1_000_000.0, symbol="^CRSLDX")
    assert b["fell_back"] is False
    assert b["index"] == "NIFTY 500"


def test_ui_renders_both_benchmarks_and_names_the_index():
    """The two indices differ by roughly the size premium, so showing a single
    unlabelled 'Alpha vs NIFTY' number lets the easier comparison pass for
    skill. The label has to say WHICH index, and the NIFTY 50 gap has to be
    visible next to it."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    assert "alpha_vs_nifty50_pct" in js, "the NIFTY 50 comparison is not rendered"
    assert "benchmark || {}).index" in js, "the index must be named, not assumed"
    assert "fell_back" in js, "a silent fallback to the narrower index is invisible"
    assert "size premium, not skill" in js
