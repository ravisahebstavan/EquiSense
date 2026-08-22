"""Twelve Data provider — parsing and batching, exercised offline.

The network call is monkeypatched (as the Yahoo provider's `_download` is), so
these assert the contract the rest of the pipeline depends on: the 7-tuple shape,
ascending date order, graceful handling of Twelve Data's single- vs multi-symbol
response shapes, and per-symbol error isolation.
"""
from __future__ import annotations

from datetime import date

import pytest

from equisense.ingestion import twelvedata as td


def _block(rows):
    # Twelve Data returns newest-first
    return {"meta": {"symbol": "X"}, "status": "ok",
            "values": [{"datetime": d, "open": o, "high": h, "low": lo,
                        "close": c, "volume": v} for (d, o, h, lo, c, v) in rows]}


DESC_ROWS = [
    ("2026-08-20", "101", "104", "100", "103", "1500"),
    ("2026-08-19", "100", "102", "99", "101", "1200"),
    ("2026-08-18", "98", "101", "97", "100", "1000"),
]


def test_values_parse_to_ascending_seven_tuple():
    s = td._values_to_series(_block(DESC_ROWS)["values"])
    assert s is not None
    dates, closes, volumes, nominal, opens, highs, lows = s
    assert dates == [date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)]
    assert closes == [100.0, 101.0, 103.0]
    assert nominal == closes                      # no adj close on free tier
    assert volumes == [1000.0, 1200.0, 1500.0]
    assert opens[0] == 98.0 and highs[-1] == 104.0 and lows[-1] == 100.0


def test_bad_and_missing_values_are_skipped_not_fatal():
    vals = [{"datetime": "2026-08-20", "close": "103"},
            {"datetime": "bad-date", "close": "5"},
            {"datetime": "2026-08-19", "close": None},
            {"close": "9"}]
    s = td._values_to_series(vals)
    assert s is not None
    assert s[0] == [date(2026, 8, 20)] and s[1] == [103.0]


def test_multi_symbol_response_is_unwrapped(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "k")
    body = {"RELIANCE": _block(DESC_ROWS), "TCS": _block(DESC_ROWS)}
    monkeypatch.setattr(td, "_request", lambda params: body)
    series, status = td.fetch_series(["RELIANCE", "TCS"], years=1)
    assert set(series) == {"RELIANCE", "TCS"}
    assert status["coverage_pct"] == 100.0 and status["returned"] == 2


def test_single_symbol_response_is_handled(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "k")
    monkeypatch.setattr(td, "_request", lambda params: _block(DESC_ROWS))
    series, status = td.fetch_series(["RELIANCE"], years=1)
    assert list(series) == ["RELIANCE"] and status["returned"] == 1


def test_per_symbol_error_isolated_and_reported(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "k")
    body = {"RELIANCE": _block(DESC_ROWS),
            "BADSYM": {"status": "error", "message": "symbol not found"}}
    monkeypatch.setattr(td, "_request", lambda params: body)
    series, status = td.fetch_series(["RELIANCE", "BADSYM"], years=1)
    assert list(series) == ["RELIANCE"]
    assert status["missing_count"] == 1 and "BADSYM" in status["missing"]


def test_no_key_returns_empty_with_reason(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    series, status = td.fetch_series(["RELIANCE"], years=1)
    assert series == {} and status["returned"] == 0
    assert any("key" in e.lower() for e in status["errors"])


def test_transport_failure_degrades_to_missing(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "k")
    def boom(params):
        raise RuntimeError("429 rate limited")
    monkeypatch.setattr(td, "_request", boom)
    series, status = td.fetch_series(["RELIANCE", "TCS"], years=1)
    assert series == {} and status["missing_count"] == 2
    assert status["errors"] and "429" in status["errors"][0]
