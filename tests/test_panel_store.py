"""KV price panel — serialization, round-robin refresh, coverage.

Uses the in-memory KV fallback and an injected fetch, so the incremental-refresh
logic is exercised with no network and no provider key.
"""
from __future__ import annotations

from datetime import date

import pytest

from equisense import kv
from equisense.ingestion import panel_store as ps


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("EQUISENSE_KV_DIR", raising=False)
    monkeypatch.delenv("KV_REST_API_URL", raising=False)
    kv._reset_local_for_tests()
    yield
    kv._reset_local_for_tests()


def _series(n=3):
    dates = [date(2026, 8, 18 + i) for i in range(n)]
    closes = [100.0 + i for i in range(n)]
    return (dates, closes, [1000.0] * n, closes, closes, closes, closes)


def test_encode_decode_roundtrips_dates_and_values():
    s = _series(4)
    ps.put("RELIANCE", s)
    got = ps.get("RELIANCE")
    assert got is not None
    assert got[0] == s[0] and got[1] == s[1] and got[2] == s[2]


def test_get_many_reports_missing():
    ps.put("A", _series())
    found, missing = ps.get_many(["A", "B"])
    assert list(found) == ["A"] and missing == ["B"]


def test_round_robin_sweeps_whole_universe_without_repeats():
    universe = ["A", "B", "C", "D", "E"]

    def fake_fetch(tickers, years):
        return {t: _series() for t in tickers}, {"provider": "fake"}

    seen = []
    for _ in range(3):                      # 3 runs x batch 2 = 6 picks over 5 names
        r = ps.refresh_due(universe, years=1, batch=2, fetch=fake_fetch)
        seen.extend(r["names"])
    # every name refreshed at least once within one full sweep (first 5 picks)
    assert set(seen[:5]) == set(universe)
    assert ps.panel_state(universe)["coverage_pct"] == 100.0


def test_refresh_reports_missing_names_from_provider():
    def partial_fetch(tickers, years):
        # provider returns only the first of each batch
        return ({tickers[0]: _series()} if tickers else {}), {"provider": "fake"}

    r = ps.refresh_due(["A", "B"], years=1, batch=2, fetch=partial_fetch)
    assert r["refreshed"] == 1 and r["missing"] == ["B"]


def test_fresh_names_are_skipped_to_bound_api_credits():
    universe = ["A", "B", "C"]
    f = lambda t, y: ({x: _series() for x in t}, {"provider": "fake"})
    # refresh everything, then ask again with a large min_age — nothing is due
    ps.refresh_due(universe, years=1, batch=3, fetch=f)
    r = ps.refresh_due(universe, years=1, batch=3, min_age_s=3600, fetch=f)
    assert r["refreshed"] == 0 and "nothing due" in r["note"]


def test_cursor_advances_and_wraps():
    universe = ["A", "B", "C"]
    f = lambda t, y: ({x: _series() for x in t}, {"provider": "fake"})
    ps.refresh_due(universe, years=1, batch=2, fetch=f)
    assert ps.due_batch(universe, 2) == ["C", "A"]     # continues from cursor
