"""Point-in-time listed universe, reconstructed from NSE daily bhavcopies.

Every study in this repo runs on TODAY'S index membership backfilled ten years.
A company that traded in 2018 and was later delisted, acquired or demoted is
absent from the panel entirely, so the backtest never had the chance to buy it
and never suffered its outcome. That is survivorship bias, and it is the one
remaining defect large enough to move every number the system reports: the
reconstructed equal-weight basket returns 24.67%/yr against the published
NIFTY 500's 12.33%.

The exchange publishes a bhavcopy for every trading day, listing every security
that traded. Those files go back years and are free and keyless, so the listed
universe as of any past date is recoverable exactly. What they do NOT carry is
index membership, so this fixes "was it tradeable" and not "was it in the Nifty
500" — the two are different and only the first is recoverable this way.

Storage: one row per SYMBOL, not per symbol-day. ~3,400 rows instead of ~5
million, which is what makes it affordable on a 512 MB tier at 196 MB used.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import time
import urllib.request
import zipfile
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Company, ListingWindow

log = logging.getLogger("equisense.ingest")

UA = {"User-Agent": "Mozilla/5.0"}
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
       "NOV", "DEC"]
TIMEOUT = 30

# How long a symbol must be absent before it counts as gone. Three sampling
# intervals: one missed monthly sample is routine (no trade that day, or a
# different series), a full quarter of silence is not.
DELISTED_ABSENT_DAYS = 100


def bhavcopy_urls(d: dt.date) -> list[str]:
    """Both formats. NSE switched layout in 2024 and kept the old archive, so a
    date near the boundary may only exist under one of them."""
    return [
        f"https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
        f"https://nsearchives.nseindia.com/content/historical/EQUITIES/"
        f"{d.year}/{MON[d.month - 1]}/cm{d.day:02d}{MON[d.month - 1]}{d.year}bhav.csv.zip",
    ]


def fetch_traded_symbols(d: dt.date) -> set[str]:
    """Every EQ-series symbol that traded on `d`. Empty set on a holiday or a
    fetch failure — the caller cannot distinguish them and must not treat an
    empty result as "nothing was listed"."""
    for url in bhavcopy_urls(d):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as f:
                raw = f.read()
            zf = zipfile.ZipFile(io.BytesIO(raw))
            text = zf.read(zf.namelist()[0]).decode("utf-8", "ignore")
            out = set()
            for row in csv.DictReader(io.StringIO(text)):
                # column names differ between the two layouts
                sym = row.get("SYMBOL") or row.get("TckrSymb")
                series = (row.get("SERIES") or row.get("SctySrs") or "").strip()
                if sym and series == "EQ":
                    out.add(sym.strip().upper())
            if out:
                return out
        except Exception:                              # noqa: BLE001 - try next
            continue
    return set()


def sample_dates(start: dt.date, end: dt.date, every_days: int = 30) -> list[dt.date]:
    """Sampling grid, skipping weekends.

    Sampled rather than exhaustive on purpose: 2,500 daily fetches to establish
    a listing WINDOW is wasted work, because the window only needs resolving to
    within the sampling interval. A monthly grid over ten years is ~120 requests
    and dates a delisting to the month, which is far finer than the 21-day
    rebalance any study here uses.
    """
    out, d = [], start
    while d <= end:
        probe = d
        while probe.weekday() >= 5:
            probe += dt.timedelta(days=1)
        if probe <= end:
            out.append(probe)
        d += dt.timedelta(days=every_days)
    return out


def ingest_listing_history(session: Session, start: Optional[dt.date] = None,
                           end: Optional[dt.date] = None,
                           every_days: int = 30,
                           pause_s: float = 0.4) -> dict:
    """Build/refresh the point-in-time listed universe."""
    end = end or dt.date.today()
    start = start or (end - dt.timedelta(days=365 * 10))
    dates = sample_dates(start, end, every_days)

    first: dict[str, dt.date] = {}
    last: dict[str, dt.date] = {}
    counts: dict[str, int] = {}
    ok_dates = 0
    for d in dates:
        syms = fetch_traded_symbols(d)
        if not syms:
            continue
        ok_dates += 1
        for s in syms:
            first.setdefault(s, d)
            last[s] = d
            counts[s] = counts.get(s, 0) + 1
        time.sleep(pause_s)

    if not ok_dates:
        return {"ok": False, "reason": "no bhavcopy could be fetched",
                "dates_attempted": len(dates)}

    newest = max(last.values())
    # A name counts as gone only if it has been absent for several consecutive
    # samples, not merely missing from the newest one. With a monthly grid a
    # live company that simply did not trade on the single sampled day — or
    # traded under a different series that day — would otherwise be recorded as
    # delisted. Measured: the naive rule flagged 1,058 symbols, and 31 of the
    # 40 longest-lived among them still return ~2,470 daily bars from the
    # price provider, i.e. they never stopped trading at all.
    gone_before = newest - dt.timedelta(days=DELISTED_ABSENT_DAYS)
    panel = {c.ticker.upper() for c in session.scalars(select(Company)).all()}
    existing = {r.symbol: r for r in session.scalars(select(ListingWindow)).all()}

    written = 0
    for sym, f in first.items():
        delisted = last[sym] < gone_before
        row = existing.get(sym)
        if row is None:
            row = ListingWindow(symbol=sym, first_seen=f, last_seen=last[sym])
            session.add(row)
        row.first_seen = min(row.first_seen, f) if row.first_seen else f
        row.last_seen = max(row.last_seen, last[sym]) if row.last_seen else last[sym]
        row.sessions_sampled = counts[sym]
        row.is_delisted = delisted
        row.in_panel = sym in panel
        written += 1
    session.commit()

    delisted = sum(1 for s in first if last[s] < gone_before)
    missing = sum(1 for s in first if last[s] < gone_before and s not in panel)
    return {
        "ok": True,
        "dates_sampled": ok_dates,
        "symbols": written,
        "delisted_or_departed": delisted,
        "delisted_and_absent_from_panel": missing,
        "panel_size": len(panel),
        "newest_session": str(newest),
        "note": ("Listing windows, not index membership — the bhavcopy says what "
                 "was TRADEABLE, not what was in the Nifty 500. Use it to bound "
                 "survivorship, not to reconstruct the index."),
    }


def was_tradeable(session: Session, symbol: str, on: dt.date) -> Optional[bool]:
    """Was `symbol` listed on `on`? None when the symbol was never sampled."""
    row = session.scalars(
        select(ListingWindow).where(ListingWindow.symbol == symbol.upper())).first()
    if row is None:
        return None
    return row.first_seen <= on <= row.last_seen


def survivorship_report(session: Session) -> dict:
    """How much of the historical listed universe the panel actually covers.

    The headline number to resist: "N symbols traded then and are missing now"
    is an UPPER BOUND on survivorship, not a measurement of it. The panel is
    deliberately a 500-name index and was never meant to hold every listed
    micro-cap. What is diagnostic is the delisted subset — those are names that
    stopped trading, and any of them that were once index members are exactly
    the ones a backfilled panel silently drops.
    """
    rows = list(session.scalars(select(ListingWindow)).all())
    if not rows:
        return {"available": False,
                "reason": "listing history not ingested — run ingest_listing_history()"}
    newest = max(r.last_seen for r in rows)
    delisted = [r for r in rows if r.is_delisted]
    return {
        "available": True,
        "symbols_tracked": len(rows),
        "newest_session": str(newest),
        "in_panel": sum(1 for r in rows if r.in_panel),
        "delisted": len(delisted),
        "delisted_and_absent_from_panel": sum(1 for r in delisted if not r.in_panel),
        "panel_coverage_of_delisted_pct": (
            round(sum(1 for r in delisted if r.in_panel) / len(delisted) * 100, 1)
            if delisted else None),
        "caveat": ("Bhavcopy gives the LISTED universe, not index membership. A "
                   "symbol missing from the panel was often never a Nifty 500 "
                   "constituent, so the raw gap overstates survivorship. The "
                   "delisted subset is the diagnostic population."),
    }
