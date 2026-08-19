"""Short-side executability — the money-making-realism gate (§8.1 ext).

These assert the one thing the signal stack cannot see: whether a bearish verdict
is a trade a retail Indian cash account can actually hold. A short is only holdable
as a single-stock future; a name with no future cannot be shorted overnight at all.
Hand-checked values, in the §15 tradition.
"""
from equisense.engine import india_market as im
from equisense.engine.sizing import (cost_tax_breakeven, futures_cost_breakeven,
                                     DP_CHARGE_PER_SELL, FNO_BROKERAGE_PER_ORDER)


def test_fno_name_is_shortable_via_future():
    ex = im.short_executability("RELIANCE")
    assert ex.executable is True
    assert ex.instrument == "single_stock_future"
    assert ex.lot_size and ex.lot_size > 0
    assert 0 < ex.margin_fraction < 1


def test_non_fno_name_is_not_shortable():
    ex = im.short_executability("NOSUCHTINYCO")
    assert ex.executable is False
    assert ex.instrument == "none"
    # the reason must name the real constraint, not a vague failure
    assert "overnight" in ex.reason.lower() or "future" in ex.reason.lower()


def test_round_to_lot_rounds_down_never_up():
    # 7 lots of 500 fit in 3999 shares; must never round UP past the risk budget
    assert im.round_to_lot(3999, 500) == 3500
    assert im.round_to_lot(499, 500) == 0          # below one lot → no position
    assert im.round_to_lot(1000, 500) == 1000
    assert im.round_to_lot(1234, None) == 1234     # unknown lot passes through


def test_futures_short_is_cheaper_than_delivery_on_a_large_ticket():
    notional = 1_500_000.0
    fut = futures_cost_breakeven(notional, adv_cr=50.0)
    deliv = cost_tax_breakeven(notional, adv_cr=50.0, expected_hold_months=6)
    assert fut["instrument"] == "single_stock_future"
    assert deliv["instrument"] == "delivery_equity"
    # futures statutory stack is materially lighter than delivery's double STT
    assert fut["statutory_pct"] < deliv["statutory_pct"]


def test_flat_costs_dominate_a_small_delivery_ticket():
    # The whole point of modelling flat costs: on a tiny slot they swamp the bps.
    small = cost_tax_breakeven(10_000.0, adv_cr=50.0, expected_hold_months=6)
    large = cost_tax_breakeven(10_000_000.0, adv_cr=50.0, expected_hold_months=6)
    assert small["flat_cost_pct"] > large["flat_cost_pct"]
    # ₹15 DP on a ₹10k ticket is 0.15% — a material hurdle that a proportional-
    # only model would have reported as exactly zero.
    assert small["flat_cost_pct"] >= 0.14
    assert large["flat_cost_pct"] < 0.001


def test_eligibility_provenance_is_reported_not_silent():
    prov = im.eligibility_provenance()
    assert prov["source"] == "pinned_snapshot"
    assert prov["snapshot_date"]
    assert prov["count"] == len(im.FNO_ELIGIBLE)


def _short_world():
    """A stored universe snapshot with two names — one F&O-eligible, one not —
    so the short path (which the long-only fixtures never exercised) can be driven."""
    import json, datetime as dt
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import equisense.models as M
    from equisense.db import Base
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    for tk in ["RELIANCE", "TINYCAPXYZ"]:               # F&O vs non-F&O
        s.add(M.Company(ticker=tk, name=tk, sector="IT", cap_band="large",
                        is_index_member=True))
    s.flush()
    def item(cid, tk):
        return {"id": cid, "ticker": tk, "name": tk, "sector": "IT",
                "cap_band": "large", "is_financial": False, "price": 1500.0,
                "chg_1d_pct": 0.0, "adv_cr": 50.0, "spark": [1500.0] * 10,
                "stale_sessions": 0, "data_suspect": False,
                "signals": {k: 1.0 for k in
                            ["momentum", "dist_52w", "trend", "rel_strength", "mqi",
                             "vol", "heat", "f_score", "z_score", "ccs", "fragility",
                             "exp_gap", "pe_pctile", "sector_rel_mom", "max_effect"]}}
    rows = [(c.id, c.ticker) for c in s.query(M.Company).all()]
    snap = {"as_of": str(dt.date.today()), "version": 5, "built_at": "t",
            "companies": [item(cid, tk) for cid, tk in rows], "stale_names": {},
            "data_source": {"provider": "stored_db"}}
    s.add(M.AppSnapshot(key="universe", as_of=snap["as_of"], payload=json.dumps(snap)))
    s.commit()
    return s


def test_short_verdict_does_not_crash_and_is_gated_by_executability(monkeypatch):
    """Regression: qualified_candidates referenced an undefined `t` in the short
    block, so ANY bearish verdict raised NameError and 500'd the whole scan. The
    long-only fixtures never hit it. This forces a short verdict through."""
    from equisense.api import candidates as C
    from equisense.engine.synthesis import Synthesis
    from equisense.api.snapshot import invalidate_universe_cache

    # Isolate from module-global state other tests may have left warm.
    monkeypatch.setenv("EQUISENSE_LIVE_DATA", "0")     # stored snapshot, no live fetch
    invalidate_universe_cache()
    s = _short_world()
    # Force every name to a strong short verdict.
    monkeypatch.setattr(C, "synthesize", lambda *a, **k: Synthesis(
        verdict="avoid_short_candidate", net_score=-0.5, conviction_band="low",
        net_z=-3.0))
    # No network in CI: stub the live event/governance fetches to empty.
    monkeypatch.setattr("equisense.ingestion.nse_events.fetch_event_calendar",
                        lambda *a, **k: {"available": False, "events": {}})

    result = C.qualified_candidates(s, top_n=5)          # must not raise
    assert result["scanned"] == 2
    by_ticker = {c["ticker"]: c for c in result["candidates"]}
    # non-F&O short is refused with the executability reason
    tiny = by_ticker.get("TINYCAPXYZ")
    assert tiny is not None and tiny["direction"] == "short"
    assert tiny["tradable"] is False
    assert any("not shortable" in g for g in tiny["gates"])
    # F&O short is an actionable single-stock-future instrument
    rel = by_ticker.get("RELIANCE")
    assert rel is not None and rel["instrument"] == "single_stock_future"
