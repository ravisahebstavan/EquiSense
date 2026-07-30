"""Database setup.

Local default: a SQLite file under ./data (gitignored — real user data never
enters the repo, PROJECT_DRAFT §28.2).

Hosted: set DATABASE_URL (e.g. a free Neon Postgres connection string) and
everything persistent — including the ledger and raw vault — lives in the
database, because free web hosts have ephemeral filesystems (DEPLOYMENT.md).
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

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if IS_SQLITE else {},
    pool_pre_ping=not IS_SQLITE,  # free Postgres tiers suspend when idle
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Persistent research records (ledger, vault) default to DB rows on a real
# database, files on local SQLite. Overridable via EQUISENSE_STORAGE=db|file.
STORAGE = os.environ.get("EQUISENSE_STORAGE", "file" if IS_SQLITE else "db")


class Base(DeclarativeBase):
    pass


def get_session() -> Session:
    return SessionLocal()


# Columns added after a DB may already exist: (table, column, DDL type+default).
_SOFT_MIGRATIONS = [
    ("companies", "is_financial", "BOOLEAN DEFAULT 0"),
    ("filing_periods", "source", "VARCHAR(20) DEFAULT 'manual'"),
    ("filing_periods", "pit_grade", "VARCHAR(20) DEFAULT 'reconstructed'"),
    ("price_observations", "volume", "FLOAT"),
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
    ("base_rates", "admissible", "BOOLEAN DEFAULT 0"),
    ("base_rates", "admissibility_reason", "TEXT"),
    ("base_rates", "multiplicity_verdict", "TEXT"),
    ("base_rates", "survives_multiplicity", "BOOLEAN DEFAULT 0"),
]


def ensure_schema() -> None:
    """create_all for new tables + additive column migrations, dialect-portable
    (inspector-based — works on SQLite and Postgres alike)."""
    from . import models  # noqa: F401 — registers all tables on Base.metadata
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, col, ddl in _SOFT_MIGRATIONS:
            if not insp.has_table(table):
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        # hot-path composite indexes (IF NOT EXISTS works on SQLite + Postgres)
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prices_cid_date "
                          "ON price_observations (company_id, obs_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_macro_sym_date "
                          "ON macro_observations (symbol, obs_date)"))
