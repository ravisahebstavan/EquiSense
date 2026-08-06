"""Scheduled corporate events from NSE's published calendar — never stored.

Why this is not in the database: the calendar is a statement about the FUTURE
and it is revised. A board meeting is announced, moved, or cancelled, so a
stored copy is a snapshot of what NSE said on the day it was captured, and the
value here is entirely "what is coming NOW". Fetched live, cached in-process
for a few minutes, and the free Neon tier is untouched (§5.2).

What it is for: event risk. Entering a position two days before a company
reports is a different trade from the same position with a clear month ahead of
it, and nothing in the evidence stack knows the difference — momentum and
valuation say the same thing either way. This does not veto anything on its
own; it attaches the date so the gate stack and the dossier can show it.

Fails OPEN and says so, exactly like nse_alerts: an unreachable calendar must
not halt the book, but a silently missing one would let the system report "no
event risk" when it simply could not look.
"""
from __future__ import annotations

import http.cookiejar
import json
import logging
import time
import urllib.request
from datetime import date, datetime
from typing import Optional

log = logging.getLogger("equisense.ingest")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
HOME = "https://www.nseindia.com"
EVENT_CALENDAR = f"{HOME}/api/event-calendar"
# Deliberately short. This is consulted inside the candidate scan, which runs
# in the daily cron against a hard function budget — a slow exchange must cost
# the run seconds, never minutes.
TIMEOUT = 10
CACHE_TTL_S = 900          # the calendar moves in days, not seconds

_CACHE: dict = {"at": 0.0, "payload": None}


def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    # NSE hands out cookies on the home page and rejects bare API calls, so the
    # home fetch is a required handshake rather than an optimisation.
    op.open(urllib.request.Request(HOME + "/", headers={**UA, "Referer": HOME + "/"}),
            timeout=TIMEOUT).read(2048)
    return op


def _parse_day(s: str) -> Optional[date]:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def fetch_event_calendar(force: bool = False) -> dict:
    """{available, events: {SYMBOL: [{date, days_away, purpose, detail}]}}."""
    now = time.time()
    if not force and _CACHE["payload"] and now - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["payload"]
    try:
        op = _opener()
        req = urllib.request.Request(EVENT_CALENDAR,
                                     headers={**UA, "Referer": HOME + "/"})
        with op.open(req, timeout=TIMEOUT) as f:
            rows = json.loads(f.read())
    except Exception as exc:                           # noqa: BLE001 - fail open
        log.warning("event calendar unavailable: %s", exc)
        out = {"available": False, "events": {},
               "reason": f"{type(exc).__name__}: {exc}"[:140],
               "note": ("Event risk could not be checked — this is NOT a "
                        "statement that there is none.")}
        _CACHE.update(at=now, payload=out)
        return out

    if not isinstance(rows, list):
        rows = rows.get("data") or []
    today = date.today()
    events: dict[str, list] = {}
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        day = _parse_day(r.get("date") or "")
        if not sym or day is None or day < today:
            continue
        events.setdefault(sym, []).append({
            "date": day.isoformat(),
            "days_away": (day - today).days,
            "purpose": (r.get("purpose") or "").strip(),
            "detail": (r.get("bm_desc") or "").strip()[:240],
        })
    for evs in events.values():
        evs.sort(key=lambda e: e["days_away"])
    out = {"available": True, "events": events, "symbols": len(events),
           "note": ("NSE's published board-meeting calendar, fetched live and "
                    "never stored — it is a forward-looking statement that gets "
                    "revised, so a stored copy would only record what was true "
                    "when it was captured.")}
    _CACHE.update(at=now, payload=out)
    return out


def next_event(symbol: str, calendar: dict | None = None) -> Optional[dict]:
    """The soonest upcoming event for one ticker, or None."""
    cal = calendar if calendar is not None else fetch_event_calendar()
    if not cal.get("available"):
        return None
    evs = (cal.get("events") or {}).get((symbol or "").upper())
    return evs[0] if evs else None
