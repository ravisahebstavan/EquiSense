"""Storage classification and headroom — what can be dropped, and what never can.

THE QUESTION THIS ANSWERS
-------------------------
On a capped free tier the tempting move is "once storage fills, compute the
summary statistics, then delete the raw data". That is a real pattern
(TimescaleDB continuous aggregates, Prometheus downsampling, RRDtool) and the
instinct behind it is sound — raw bars are only valuable for what you compute
from them.

It is the wrong move HERE, for a reason specific to this platform. The research
plane's entire value is: pre-register a hypothesis, then test it against your own
stored history. Delete the raw bars and keep only derived statistics and:

  * a NEW hypothesis can never be tested — the data it needs is gone;
  * a feature builder that turns out to be WRONG cannot be recomputed. This is
    not hypothetical: the MQI persistence term was inverted for downtrends and
    had to be recomputed across the whole history to fix;
  * base rates are hypothesis-SPECIFIC summaries. They answer one question and
    cannot answer a different one.

So the classification below is not by size. It is by REPLACEABILITY, which is
the only property that matters when deciding what may be dropped:

  REFETCHABLE   the provider returns the full history in one request, so the
                table is a cache. Dropping it costs a re-download, nothing more.
                This is where nearly all the bytes are.
  ACCUMULATED   the source publishes one file per DAY and cannot be backfilled.
                Dropping it destroys history permanently. Usually tiny.
  DERIVED       recomputed from REFETCHABLE inputs. Safe to drop.
  IRREPLACEABLE the platform's own record — ledger, theses, transactions.
                No source can return it. Never drop, at any pressure.

Measured on the live database: REFETCHABLE is ~86% of usage, ACCUMULATED is
~1.6%. The bytes and the irreplaceability are inversely correlated, which is
what makes "drop and re-fetch" strictly better than "summarise and discard".
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

REFETCHABLE = "refetchable"
ACCUMULATED = "accumulated"
DERIVED = "derived"
IRREPLACEABLE = "irreplaceable"

FREE_TIER_BYTES = 500_000_000

# table -> (class, how to restore it)
CLASSIFICATION: dict[str, tuple[str, str]] = {
    "price_observations": (
        REFETCHABLE,
        "python -m equisense.ingestion --years 10 (Yahoo returns a decade per "
        "batch request; ~30s for 50 names)"),
    "macro_observations": (
        REFETCHABLE, "same ingestion run — macro series are batch-downloadable"),
    "filing_periods": (
        REFETCHABLE,
        "same ingestion run. NOTE: Yahoo serves RESTATED figures, so a re-fetch "
        "may not reproduce what was stored — the rows are flagged "
        "pit_grade=reconstructed for exactly this reason"),
    "vault_blobs": (REFETCHABLE, "raw provider payloads; already on a rolling window"),
    "vault_fetches": (REFETCHABLE, "index over vault_blobs"),
    "derivative_quotes": (
        ACCUMULATED,
        "F&O bhavcopy is one file per day. Currently fetched LIVE and not "
        "stored, so this table is normally empty by design"),
    "delivery_stats": (
        ACCUMULATED,
        "MTO publishes one file per trading day and NSE's archive does not go "
        "back indefinitely — dropped history is gone for good"),
    "index_observations": (
        ACCUMULATED,
        "index close file is one per day; the percentile-vs-own-history IS the "
        "value, so the long series is the asset"),
    "base_rates": (DERIVED, "POST /api/live/studies/run recomputes from prices"),
    "app_snapshots": (DERIVED, "rebuilt on the next refresh"),
    "sector_attributes": (DERIVED, "re-derived from filings"),
    "companies": (
        REFETCHABLE, "sync_universe() rebuilds from NSE published constituents"),
    "ledger_records": (
        IRREPLACEABLE,
        "the hash-chained record of pre-registered claims. No source can return "
        "it; deleting it destroys the calibration history the whole learning "
        "loop is built on"),
    "transactions": (IRREPLACEABLE, "your actual trade ledger"),
    "paper_trades": (IRREPLACEABLE, "scored paper history feeding calibration"),
    "theses": (IRREPLACEABLE, "your own reasoning, by definition unrecoverable"),
    "journal_entries": (IRREPLACEABLE, "same"),
    "watchlist_items": (IRREPLACEABLE, "same"),
    "investor_profiles": (IRREPLACEABLE, "your stated policy"),
}


def storage_report(session: Session) -> dict:
    """Per-table size, replaceability class, and honest headroom.

    On Postgres this reads real on-disk sizes; on SQLite it falls back to row
    counts, which is enough to see the shape.
    """
    from ..db import IS_SQLITE

    tables: list[dict] = []
    total = 0
    if not IS_SQLITE:
        rows = session.execute(text(
            "select relname, n_live_tup, pg_total_relation_size(relid) "
            "from pg_stat_user_tables order by pg_total_relation_size(relid) desc"
        )).all()
        for name, n, b in rows:
            cls, restore = CLASSIFICATION.get(name, (DERIVED, "unclassified"))
            total += int(b or 0)
            tables.append({"table": name, "rows": int(n or 0),
                           "bytes": int(b or 0),
                           "size": _human(int(b or 0)),
                           "class": cls, "restore": restore})
    else:
        from .. import models  # noqa: F401
        from ..db import Base
        for name in sorted(Base.metadata.tables):
            try:
                n = session.scalar(select(func.count()).select_from(
                    Base.metadata.tables[name])) or 0
            except Exception:                      # noqa: BLE001
                n = 0
            cls, restore = CLASSIFICATION.get(name, (DERIVED, "unclassified"))
            tables.append({"table": name, "rows": int(n), "bytes": None,
                           "size": "n/a (sqlite)", "class": cls,
                           "restore": restore})

    by_class: dict[str, int] = {}
    for t in tables:
        by_class[t["class"]] = by_class.get(t["class"], 0) + (t["bytes"] or 0)
    droppable = by_class.get(REFETCHABLE, 0) + by_class.get(DERIVED, 0)
    protected = by_class.get(ACCUMULATED, 0) + by_class.get(IRREPLACEABLE, 0)

    pct = (total / FREE_TIER_BYTES * 100) if total else 0.0
    return {
        "total_bytes": total, "total": _human(total),
        "free_tier_bytes": FREE_TIER_BYTES,
        "used_pct": round(pct, 2),
        "tables": tables,
        "by_class": {k: _human(v) for k, v in sorted(
            by_class.items(), key=lambda kv: -kv[1])},
        "reclaimable_now": _human(droppable),
        "must_never_drop": _human(protected),
        "pressure": _pressure(pct),
        "guidance": (
            "Reclaim by DROPPING refetchable tables and re-ingesting, not by "
            "summarising and discarding. The bytes and the irreplaceability run "
            "in opposite directions here: the largest table is the one a single "
            "Yahoo request restores, while the data that genuinely cannot be "
            "rebuilt is a rounding error on the total. Summarising raw bars into "
            "statistics would also make every FUTURE hypothesis untestable and "
            "freeze any bug in a current feature builder permanently."),
    }


def _pressure(pct: float) -> str:
    if pct < 50:
        return "comfortable — no action"
    if pct < 70:
        return "watch — plan the next reclaim, nothing urgent"
    if pct < 85:
        return ("act — drop and re-ingest the largest REFETCHABLE table "
                "(price_observations); it restores in one request")
    return ("critical — reclaim refetchable tables now; never touch ACCUMULATED "
            "or IRREPLACEABLE to make room")


def _human(n: Optional[int]) -> str:
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def projected_headroom(session: Session, universe_size: int,
                       bytes_per_price_row: float = 157.0,
                       indices_per_day: int = 160) -> dict:
    """Years until the free tier binds, at a given universe size.

    Measured constants, not guesses: 157 bytes/price-row and ~160 index rows per
    trading day come from the live database.
    """
    rep = storage_report(session)
    used = rep["total_bytes"]
    px_yr = universe_size * 250 * bytes_per_price_row
    delivery_yr = universe_size * 250 * 291
    index_yr = indices_per_day * 250 * 237
    growth = px_yr + delivery_yr + index_yr
    remaining = max(0, FREE_TIER_BYTES - used)
    return {
        "universe_size": universe_size,
        "used": rep["total"], "used_pct": rep["used_pct"],
        "growth_per_year": _human(int(growth)),
        "years_of_headroom": round(remaining / growth, 1) if growth else None,
        "initial_backfill_10y": _human(int(universe_size * 2500 * bytes_per_price_row)),
        "note": ("Growth is dominated by price_observations, which is also the "
                 "most disposable table in the database. Expanding the universe "
                 "costs storage linearly but costs nothing irreversible."),
    }
