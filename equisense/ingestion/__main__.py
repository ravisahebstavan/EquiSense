"""CLI: full data bootstrap.

    python -m equisense.ingestion                      # everything, 10y
    python -m equisense.ingestion --index nifty500     # wider universe
    python -m equisense.ingestion --skip-nse           # Yahoo only
    python -m equisense.ingestion --refetch-prices     # rewrite bars to backfill
                                                       # the nominal close series
    python -m equisense.ingestion --health             # source reachability only

Works identically against local SQLite and a hosted Neon Postgres — the target
is whatever DATABASE_URL points at. Nothing here writes to the filesystem except
the raw vault, and that moves into the database too when STORAGE=db (the default
on any non-SQLite URL).
"""
import argparse
import json
import logging

from ..db import DB_URL, IS_SQLITE, ensure_schema, get_session
from .nse_archive import backfill as nse_backfill
from .nse_archive import health_check
from .yahoo import run_full_ingest

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

parser = argparse.ArgumentParser(description="EquiSense live data ingestion")
parser.add_argument("--years", type=int, default=10)
parser.add_argument("--index", default="nifty50",
                    help="NSE index whose PUBLISHED constituents define the universe")
parser.add_argument("--skip-fundamentals", action="store_true")
parser.add_argument("--skip-nse", action="store_true",
                    help="skip NSE archives (derivatives, delivery %%, index valuation)")
parser.add_argument("--nse-days", type=int, default=30,
                    help="calendar days of NSE archive history to backfill")
parser.add_argument("--refetch-prices", action="store_true",
                    help="rewrite existing price bars — needed once to backfill "
                         "close_raw, the nominal series valuation depends on")
parser.add_argument("--health", action="store_true",
                    help="probe data-source reachability and exit")
args = parser.parse_args()

if args.health:
    print(json.dumps(health_check(), indent=2))
    raise SystemExit(0)

# Never print the credential: on a hosted URL show only host/database.
target = "SQLite (local)" if IS_SQLITE else "Postgres (hosted)"
print(f"target: {target} — {DB_URL if IS_SQLITE else DB_URL.split('@')[-1]}")

ensure_schema()
with get_session() as s:
    report = run_full_ingest(s, years=args.years,
                             skip_fundamentals=args.skip_fundamentals,
                             index_key=args.index,
                             refetch_prices=args.refetch_prices)
    print("yahoo:", {k: v for k, v in report.items() if k != "tickers"})
    if not args.skip_nse:
        nse = nse_backfill(s, days=args.nse_days, symbols=report.get("tickers"))
        print("nse:", {k: v for k, v in nse.items() if k != "dates"})
