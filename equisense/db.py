"""Database setup.

Local default: a SQLite file under ./data (gitignored — real user data never
enters the repo, §3.3).

Hosted: set DATABASE_URL (e.g. a free Neon Postgres connection string) and
everything persistent — including the ledger and raw vault — lives in the
database, because free web hosts have ephemeral filesystems (README §3.1).
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATA_DIR = Path(os.environ.get("EQUISENSE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:  # serverless read-only filesystem (Vercel) — /tmp is the only writable path
    import tempfile
    DATA_DIR = Path(tempfile.gettempdir()) / "equisense-data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _resolve_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("EQUISENSE_DB")
    if not url:
        return f"sqlite:///{DATA_DIR / 'equisense.db'}"
    # Neon/Render hand out postgres:// — SQLAlchemy 2 + psycopg3 wants postgresql+psycopg://
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DB_URL = _resolve_url()
IS_SQLITE = DB_URL.startswith("sqlite")

# Recorded when the configured database cannot be opened at all, so the app can
# still start and SAY so. Read by api/status.py.
ENGINE_ERROR: dict = {"detail": None}


def _build_engine(url: str, sqlite: bool):
    return create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30} if sqlite else {},
        pool_pre_ping=not sqlite,  # free Postgres tiers suspend when idle
    )


try:
    engine = _build_engine(DB_URL, IS_SQLITE)
except Exception as exc:                           # noqa: BLE001
    # create_engine imports the DBAPI driver eagerly, so a missing psycopg
    # raises HERE — at module import, before any handler exists. The whole site
    # then 500s on every path including /favicon.ico, with no page left able to
    # explain why. That is exactly what a deployment saw: the requirement was
    # present in requirements.txt but the builder had skipped it.
    #
    # Degrade to local SQLite so the app can boot and report the fault. Writes
    # are refused separately (api/app.py::_reject_if_ephemeral) so a degraded
    # boot cannot quietly accept data it will lose.
    ENGINE_ERROR["detail"] = f"{type(exc).__name__}: {exc}"
    DB_URL = f"sqlite:///{DATA_DIR / 'equisense.db'}"
    IS_SQLITE = True
    engine = _build_engine(DB_URL, True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Persistent research records (ledger, vault) default to DB rows on a real
# database, files on local SQLite. Overridable via EQUISENSE_STORAGE=db|file.
STORAGE = os.environ.get("EQUISENSE_STORAGE", "file" if IS_SQLITE else "db")


class Base(DeclarativeBase):
    pass


def get_session() -> Session:
    return SessionLocal()


# Columns added after a DB may already exist: (table, column, DDL type+default).
# DDL here must be valid on BOTH SQLite and Postgres. Note `BOOLEAN DEFAULT FALSE`,
# not `DEFAULT 0`: SQLite accepts the integer literal, Postgres rejects it with
# "column is of type boolean but default expression is of type integer", which
# aborts ensure_schema() and therefore every cold start on a hosted database.
# Verified against a live Neon instance.
_SOFT_MIGRATIONS = [
    ("companies", "is_financial", "BOOLEAN DEFAULT FALSE"),
    ("companies", "is_index_member", "BOOLEAN DEFAULT TRUE"),
    ("companies", "last_seen_in_index", "DATE"),
    ("filing_periods", "interest_income", "FLOAT"),
    ("filing_periods", "net_interest_income", "FLOAT"),
    ("filing_periods", "source", "VARCHAR(20) DEFAULT 'manual'"),
    ("filing_periods", "pit_grade", "VARCHAR(20) DEFAULT 'reconstructed'"),
    ("price_observations", "source", "VARCHAR(10) DEFAULT 'yahoo'"),
    ("price_observations", "volume", "FLOAT"),
    ("price_observations", "close_raw", "FLOAT"),   # nominal (split-only) close
    ("price_observations", "dividend", "FLOAT"),    # cash dividend on the ex-date
    ("price_observations", "open_price", "FLOAT"),
    ("price_observations", "high_price", "FLOAT"),
    ("price_observations", "low_price", "FLOAT"),
    ("base_rates", "n_eff", "INTEGER"),
    ("base_rates", "cohort_breadth_pct", "FLOAT"),
    ("base_rates", "net_median_excess_pct", "FLOAT"),
    ("base_rates", "median_ci95_lo_pct", "FLOAT"),
    ("base_rates", "median_ci95_hi_pct", "FLOAT"),
    # Wave S — cluster-robust inference + multiple-testing control
    ("base_rates", "n_clusters", "INTEGER"),
    ("base_rates", "icc", "FLOAT"),
    ("base_rates", "design_effect", "FLOAT"),
    ("base_rates", "mean_se_pct", "FLOAT"),
    ("base_rates", "t_stat", "FLOAT"),
    ("base_rates", "df", "INTEGER"),
    ("base_rates", "p_value", "FLOAT"),
    ("base_rates", "q_value", "FLOAT"),
    ("base_rates", "admissible", "BOOLEAN DEFAULT FALSE"),
    ("base_rates", "admissibility_reason", "TEXT"),
    ("base_rates", "multiplicity_verdict", "TEXT"),
    ("base_rates", "survives_multiplicity", "BOOLEAN DEFAULT FALSE"),
]


# Natural keys of the time-series tables: (index name, table, key columns).
#
# Every one of these is a measurement of one thing on one day, so a second row
# for the same key is never new information — it is a duplicate, and duplicates
# here are silent rather than loud. `load_price_panel` pivots with pandas'
# default aggfunc="mean", so two bars for the same (company, date) are quietly
# AVERAGED into a price that never traded, and every return, volatility,
# correlation and base rate computed across it inherits the error with nothing
# to indicate it happened.
#
# Nothing prevented that. The writers deduplicated in application code — read
# the existing rows, decide, insert the rest — which holds only while exactly
# one writer runs at a time. On this deployment two do: the daily cron and the
# browser's own refresh, and the paper-trading loop calls refresh_quotes every
# few minutes while a tab is open. Two overlapping runs both read "absent" and
# both insert.
#
# A unique index moves that guarantee from convention into the database, and it
# is also what makes ON CONFLICT upserts (below) legal — so it buys correctness
# under concurrency and a cheaper write path at the same time.
_UNIQUE_INDEXES = [
    ("uq_prices_cid_date", "price_observations", ("company_id", "obs_date")),
    ("uq_macro_sym_date", "macro_observations", ("symbol", "obs_date")),
    ("uq_index_obs_name_date", "index_observations", ("index_name", "obs_date")),
    ("uq_delivery_date_sym", "delivery_stats", ("trade_date", "symbol", "series")),
    ("uq_volsurf_date_sym_exp", "vol_surface_observations",
     ("obs_date", "symbol", "expiry")),
]

# Superseded by the unique indexes above, which serve exactly the same lookups.
# Carrying both doubles the write cost and the disk for no read benefit.
_REDUNDANT_INDEXES = ["ix_prices_cid_date", "ix_macro_sym_date"]


# Bumped whenever _SOFT_MIGRATIONS or the table set changes, so a deployment
# running older code cannot mistake a newer schema for "already done".
SCHEMA_VERSION = "2026-08-16.1"

# Filled by ensure_schema. Read by api/status.py: a uniqueness guarantee that
# failed to apply must be visible, because everything downstream of it then
# depends on application-level deduplication that concurrency can defeat.
SCHEMA_NOTES: dict[str, str] = {}


def _apply_unique_keys() -> None:
    """Deduplicate, then enforce the natural key of each time-series table.

    Each index gets its OWN transaction. On Postgres a failed statement poisons
    the whole transaction, so sharing one would mean a single table that cannot
    be deduplicated (a foreign key elsewhere, a permission) silently taking the
    other four constraints down with it.

    Failures are recorded rather than raised. ensure_schema() runs on the
    serverless cold-start path, and an exception here would put the app back to
    failing to boot at all — which is the failure mode this codebase has already
    paid for twice. The constraint is important; being unable to serve a page
    that explains why it is missing is worse.
    """
    insp = inspect(engine)
    for name, table, cols in _UNIQUE_INDEXES:
        if not insp.has_table(table):
            continue
        key = ", ".join(cols)
        try:
            with engine.begin() as conn:
                # Keep the earliest row of each key. Ordering by id is arbitrary
                # between genuine duplicates, which is the point: they are meant
                # to be identical measurements, and any that are not were
                # produced by a bug that this index now makes impossible to
                # repeat.
                conn.execute(text(
                    f"DELETE FROM {table} WHERE id IN ("
                    f"  SELECT id FROM ("
                    f"    SELECT id, ROW_NUMBER() OVER ("
                    f"      PARTITION BY {key} ORDER BY id) AS rn FROM {table}"
                    f"  ) dupes WHERE rn > 1)"))
                conn.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({key})"))
        except Exception as exc:                       # noqa: BLE001
            SCHEMA_NOTES[name] = f"{type(exc).__name__}: {exc}"[:200]
            continue
        SCHEMA_NOTES.pop(name, None)

    # Retroactive provenance. Rows written before price_observations.source
    # existed all defaulted to 'yahoo', including the fabricated seed path.
    #
    # The signature is exact rather than heuristic: seed/demo_data.py writes a
    # close and nothing else, while every bar Yahoo has ever returned carries a
    # volume. A row with no nominal price, no volume AND no intraday range is
    # not something the provider produces — verified against the live database,
    # where the rows matching this predicate are precisely the 54 seeded ones,
    # on the six synthetic dates, across the nine seeded names.
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE price_observations SET source = 'demo' "
                "WHERE source = 'yahoo' AND close_raw IS NULL "
                "AND volume IS NULL AND open_price IS NULL"))
    except Exception as exc:                           # noqa: BLE001
        SCHEMA_NOTES["price_source_backfill"] = f"{type(exc).__name__}: {exc}"[:200]

    for name in _REDUNDANT_INDEXES:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        except Exception:                              # noqa: BLE001
            pass                                       # disk, never correctness


def unique_keys_missing() -> list[str]:
    """Natural keys that are NOT enforced by the database right now.

    Read from api/status.py rather than trusting SCHEMA_NOTES alone, because the
    fast path in ensure_schema() means a process can serve an entire deployment
    without ever having run the migration that populates it.
    """
    try:
        insp = inspect(engine)
        return [name for name, table, _cols in _UNIQUE_INDEXES
                if insp.has_table(table)
                and name not in {ix["name"] for ix in insp.get_indexes(table)}]
    except Exception as exc:                           # noqa: BLE001
        return [f"unknown ({type(exc).__name__})"]


def upsert(session, model, rows: list[dict], conflict_cols: tuple[str, ...],
           update_cols: tuple[str, ...] = (),
           coalesce_cols: tuple[str, ...] = (), chunk: int = 500) -> int:
    """INSERT ... ON CONFLICT DO UPDATE, portable across SQLite and Postgres.

    This replaces the read-modify-write shape the ingestion paths used to have:
    SELECT the rows that might already exist, compare in Python, then insert the
    remainder. That shape has two costs this deployment actually paid. It reads
    rows purely to decide not to write them, on a database that meters data
    transfer — and the common case is that the bar is already correct, so it is
    the most expensive possible way to do nothing. And it is only correct while
    exactly one writer runs, which stopped being true the moment the browser's
    refresh loop overlapped the daily cron.

    Chunked because both drivers bind every value in the statement: one 130,000
    row INSERT exceeds Postgres' 65,535 parameter ceiling outright, and the
    memory spike is what killed a free-tier connection during an OHLC backfill.

    `coalesce_cols` are updated only when the incoming value is non-null, which
    is what makes a partial refetch safe. Providers serve different column sets
    on different endpoints — the near-live quote refresh carries no dividend and
    historically carried no OHLC — so an unconditional overwrite would let the
    cheap frequent path erase fields the expensive one had populated. That is
    not hypothetical: it is how 70% of a month's bars ended up with no intraday
    range while the estimator that sizes positions quietly fell back to a
    six-times-noisier one.
    """
    if not rows:
        return 0
    from sqlalchemy import func as _func
    if IS_SQLITE:
        from sqlalchemy.dialects.sqlite import insert as _insert
    else:
        from sqlalchemy.dialects.postgresql import insert as _insert
    table = model.__table__
    written = 0
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        stmt = _insert(model).values(batch)
        assignments = {c: getattr(stmt.excluded, c) for c in update_cols}
        assignments.update({
            c: _func.coalesce(getattr(stmt.excluded, c), table.c[c])
            for c in coalesce_cols})
        stmt = stmt.on_conflict_do_update(
            index_elements=list(conflict_cols), set_=assignments)
        session.execute(stmt)
        written += len(batch)
    return written


def ensure_schema() -> None:
    """create_all for new tables + additive column migrations, dialect-portable
    (inspector-based — works on SQLite and Postgres alike).

    Fast-paths out when the schema is already at SCHEMA_VERSION. This matters
    enormously on serverless: the full path runs create_all over ~20 tables plus
    has_table and get_columns per soft migration plus index DDL — dozens of round
    trips. At a few hundred milliseconds each, against a free Postgres tier that
    auto-suspends and must be woken, that ran on EVERY cold start and was enough
    to blow the function's init budget and take the whole site down with
    FUNCTION_INVOCATION_FAILED. One SELECT replaces all of it.
    """
    from . import models  # noqa: F401 — registers all tables on Base.metadata
    try:
        with engine.connect() as conn:
            got = conn.execute(text(
                "SELECT payload FROM app_snapshots WHERE key = 'schema_version'"
            )).scalar()
        if got == SCHEMA_VERSION:
            return
    except Exception:                              # noqa: BLE001
        pass                                       # table absent on a fresh database
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, col, ddl in _SOFT_MIGRATIONS:
            if not insp.has_table(table):
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        # hot-path composite index (IF NOT EXISTS works on SQLite + Postgres).
        # The other two that used to live here are now UNIQUE indexes created
        # below, which cover the same lookups.
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vol_sym_date "
                          "ON vol_surface_observations (symbol, obs_date)"))

    _apply_unique_keys()

    # Stamped LAST, so a migration that fails part-way leaves the fast path
    # disarmed and the next boot retries instead of skipping incomplete work.
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM app_snapshots WHERE key = 'schema_version'"))
            conn.execute(text(
                "INSERT INTO app_snapshots (key, as_of, payload) "
                "VALUES ('schema_version', :d, :v)"), {"d": SCHEMA_VERSION,
                                                       "v": SCHEMA_VERSION})
    except Exception:                              # noqa: BLE001
        pass                                       # an optimisation, never required


# ---------------------------------------------------------------- egress meter
# A free Postgres tier meters DATA TRANSFER, not just storage — a limit that
# took this deployment down while disk sat at 41%. Nothing in the codebase could
# say which query was responsible, because cost had only ever been reasoned
# about in rows and seconds.
#
# pg_stat_statements would answer it, but it needs an extension this tier may
# not expose, and its bytes are a heuristic anyway. The loaders know exactly how
# many rows they pulled, so they report it themselves: no privileges, no
# extension, and the number is exact rather than inferred.
ROWS_READ: dict[str, int] = {}


def note_rows(source: str, n: int) -> None:
    ROWS_READ[source] = ROWS_READ.get(source, 0) + int(n)


def rows_read_report(reset: bool = False) -> dict:
    """Rows pulled per source since the process started (or since last reset).

    Bytes are estimated at a deliberately ROUND per-row figure. The point is
    ranking which read dominates, not a billing-accurate total — and a precise
    looking number here would invite exactly the false confidence that let the
    quota be exhausted unnoticed.
    """
    per_row_bytes = 60
    total = sum(ROWS_READ.values())
    out = {
        "rows_by_source": dict(sorted(ROWS_READ.items(), key=lambda kv: -kv[1])),
        "rows_total": total,
        "approx_mb": round(total * per_row_bytes / 1e6, 1),
        "assumed_bytes_per_row": per_row_bytes,
        "note": ("Self-reported by the loaders, so it needs no database "
                 "extension and no privileges. Ranks which read dominates; the "
                 "byte figure is a round estimate, not a billing total."),
    }
    if reset:
        ROWS_READ.clear()
    return out
