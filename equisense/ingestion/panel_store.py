"""KV-backed price panel — the durable market data that isn't metered.

The live model fetched the whole universe from Yahoo on demand and cached it only
in process, so every cold start re-fetched. That worked because Yahoo was keyless
and unlimited. Twelve Data's free tier is not: 8 credits/minute means the ~226
name universe cannot be pulled in one serverless invocation.

The resolution reuses the storage migration the platform was already making. The
panel lives in the REST KV — which, unlike the Neon tier that started all this,
does not meter data transfer — and the cron refreshes it INCREMENTALLY, a bounded
round-robin batch per run that stays inside the rate limit. Reads serve the whole
panel straight from KV, instantly, on any instance; there is no per-request fetch
on the hot path at all. Over a day every name is refreshed; an EOD bar that has
not changed costs nothing to leave in place.

One ticker is one KV key (`px:{ticker}`), so a refresh writes only the names it
pulled and a read fetches exactly the universe asked for. A round-robin cursor
(`px:cursor`) and per-ticker refresh stamps make "which names are stalest" a
cheap, exact question rather than a guess.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from typing import Callable, Optional

from .. import kv

_PX = "px:"                     # per-ticker series
_CURSOR = "px:cursor"           # round-robin refresh position
_STAMP = "px:stamp:"           # per-ticker last-refresh epoch seconds

# 7-tuple field order, kept in one place so encode/decode cannot drift apart.
_FIELDS = ("d", "c", "v", "n", "o", "h", "l")


def _encode(series: tuple) -> str:
    dates, closes, volumes, nominal, opens, highs, lows = series
    return json.dumps({
        "d": [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in dates],
        "c": closes, "v": volumes, "n": nominal,
        "o": opens, "h": highs, "l": lows})


def _decode(raw: str) -> Optional[tuple]:
    try:
        o = json.loads(raw)
        dates = [datetime.strptime(s[:10], "%Y-%m-%d").date() for s in o["d"]]
    except (ValueError, KeyError, TypeError):
        return None
    if not dates:
        return None
    return (dates, o.get("c", []), o.get("v", []), o.get("n", []),
            o.get("o", []), o.get("h", []), o.get("l", []))


def put(ticker: str, series: tuple) -> None:
    kv.set(_PX + ticker, _encode(series))
    kv.set(_STAMP + ticker, str(time.time()))


def get(ticker: str) -> Optional[tuple]:
    raw = kv.get(_PX + ticker)
    return _decode(raw) if raw is not None else None


def get_many(tickers: list[str]) -> tuple[dict[str, tuple], list[str]]:
    """(series_by_ticker, missing). Reads the panel for exactly these names."""
    out: dict[str, tuple] = {}
    missing: list[str] = []
    for t in tickers:
        s = get(t)
        if s is None:
            missing.append(t)
        else:
            out[t] = s
    return out, missing


def _cursor() -> int:
    raw = kv.get(_CURSOR)
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _age(ticker: str) -> Optional[float]:
    st = kv.get(_STAMP + ticker)
    if st is None:
        return None
    try:
        return time.time() - float(st)
    except (TypeError, ValueError):
        return None


def due_batch(all_tickers: list[str], batch: int,
              min_age_s: float = 0.0) -> list[str]:
    """The next up-to-`batch` names to refresh, scanning round-robin from the
    stored cursor and SKIPPING any refreshed within `min_age_s`. Skipping fresh
    names is what bounds daily API credits to roughly one pull per name per day
    (an EOD bar is final; re-fetching it is pure waste) — without it a long
    app-open session would re-sweep the universe every few minutes and blow a
    free tier's daily quota."""
    n = len(all_tickers)
    if n == 0:
        return []
    start = _cursor() % n
    picked: list[str] = []
    for i in range(n):                          # at most one full lap
        t = all_tickers[(start + i) % n]
        if min_age_s <= 0.0 or (_age(t) is None) or (_age(t) >= min_age_s):
            picked.append(t)
            if len(picked) >= batch:
                break
    return picked


def refresh_due(all_tickers: list[str], years: int, batch: int,
                min_age_s: float = 0.0, fetch: Optional[Callable] = None) -> dict:
    """Fetch the next round-robin batch of DUE names from the active provider and
    write it to the panel, advancing the cursor past what it examined. `fetch` is
    injectable for tests; in production it is prices.fetch_series. Rate-limit
    compliance is the CALLER's choice of `batch` (Twelve Data free tier: keep
    batch ≤ 8 to stay under 8 credits/min, or size it to the paid tier)."""
    if fetch is None:
        from .prices import fetch_series as fetch
    picked = due_batch(all_tickers, batch, min_age_s)
    if not picked:
        return {"refreshed": 0, "requested": 0, "names": [],
                "note": "nothing due — every name is within min_age", "cursor": _cursor()}
    series, status = fetch(picked, years)
    for t, s in series.items():
        put(t, s)
    # Advance the cursor past the LAST name we picked so the next call resumes
    # after it, whether or not intervening names were skipped as fresh.
    last = picked[-1]
    kv.set(_CURSOR, str((all_tickers.index(last) + 1) % max(1, len(all_tickers))))
    return {
        "requested": len(picked),
        "refreshed": len(series),
        "names": picked,
        "missing": [t for t in picked if t not in series],
        "provider": status.get("provider"),
        "cursor": _cursor(),
        "universe": len(all_tickers),
    }


def panel_state(all_tickers: Optional[list[str]] = None) -> dict:
    """Coverage and freshness for the data-health surface. Never fetches."""
    tickers = all_tickers or []
    present = 0
    oldest = None
    for t in tickers:
        st = kv.get(_STAMP + t)
        if kv.get(_PX + t) is not None:
            present += 1
        if st is not None:
            try:
                age = time.time() - float(st)
                oldest = age if oldest is None else max(oldest, age)
            except (TypeError, ValueError):
                pass
    return {
        "backend": kv.backend_name(),
        "universe": len(tickers),
        "present": present,
        "coverage_pct": round(100 * present / len(tickers), 1) if tickers else 0.0,
        "cursor": _cursor(),
        "stalest_age_seconds": round(oldest, 1) if oldest is not None else None,
    }
