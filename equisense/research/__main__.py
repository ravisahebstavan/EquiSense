"""CLI: run the heavy research studies and cache their results.

    python -m equisense.research all          # everything
    python -m equisense.research ic           # information coefficient
    python -m equisense.research factors      # quantile P&L, net of costs
    python -m equisense.research baserates    # base-rate records

Why this exists: each of these is a full pass over the price panel and takes
minutes — the IC study alone took ~12 minutes over the 500-name, 10-year
panel. The Lab's buttons POST to endpoints that do the same work, and a
serverless function will time out long before any of them finish. Running
locally against the same DATABASE_URL writes the identical cached rows, and
the hosted site then serves them instantly from AppSnapshot.

Results are stored where the read endpoints look for them, so this and the
buttons are interchangeable in effect — only the time budget differs.
"""
import argparse
import json
import time
import warnings
from datetime import date

warnings.filterwarnings("ignore")

from ..db import DB_URL, IS_SQLITE, ensure_schema, get_session  # noqa: E402
from ..models import AppSnapshot  # noqa: E402

parser = argparse.ArgumentParser(description="EquiSense research studies")
parser.add_argument("study", choices=["all", "ic", "factors", "baserates"],
                    nargs="?", default="all")
parser.add_argument("--quiet", action="store_true",
                    help="print the summary only, not the full result payload")
args = parser.parse_args()

# Never print the credential: on a hosted URL show only host/database.
print(f"target: {'SQLite (local)' if IS_SQLITE else 'Postgres (hosted)'} — "
      f"{DB_URL if IS_SQLITE else DB_URL.split('@')[-1]}")
ensure_schema()


def _cache(session, key: str, payload: dict) -> None:
    row = session.get(AppSnapshot, key)
    if row is None:
        row = AppSnapshot(key=key, as_of=str(date.today()), payload="")
        session.add(row)
    row.payload = json.dumps(payload, default=str)
    row.as_of = str(date.today())
    session.commit()


def run_ic(session, panel=None) -> dict:
    from .ic import run_ic_studies
    out = run_ic_studies(session, panel=panel)
    _cache(session, "ic_studies", out)
    return {"computable": out.get("computable"),
            "universe": out.get("universe"),
            "passing_ic_t_test": out.get("passing_ic_t_test")}


def run_factors(session, panel=None) -> dict:
    from .factor_portfolio import run_factor_studies
    out = run_factor_studies(session, panel=panel)
    _cache(session, "factor_studies", out)
    return {"computable": out.get("computable"),
            "universe": out.get("universe"),
            "tradeable_long_only": out.get("tradeable_long_only")}


def run_baserates(session, panel=None) -> dict:
    from .base_rates import run_all_studies
    out = run_all_studies(session, panel=panel)
    return {k: v for k, v in out.items()
            if k not in ("caveat", "multiplicity_note")}


JOBS = {"ic": run_ic, "factors": run_factors, "baserates": run_baserates}
todo = list(JOBS) if args.study == "all" else [args.study]

# Load the price panel once and hand it to every study. Each load is a full
# read of the price table; running three studies used to pay for it three
# times, which over a network database dominated the whole run.
shared_panel = None
if len(todo) > 1:
    from .base_rates import load_price_panel
    t0 = time.time()
    with get_session() as s:
        shared_panel = load_price_panel(s)
        s.rollback()
    print(f"price panel {shared_panel[0].shape} loaded once in "
          f"{time.time() - t0:.0f}s", flush=True)

for name in todo:
    t0 = time.time()
    print(f"\n=== {name} …", flush=True)
    # A fresh session per study: these are long jobs and a connection held
    # across all three is a connection Neon will close between them.
    with get_session() as s:
        try:
            summary = JOBS[name](s, panel=shared_panel)
        except Exception as exc:                       # noqa: BLE001
            print(f"  FAILED after {time.time() - t0:.0f}s: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            continue
    print(f"  done in {time.time() - t0:.0f}s", flush=True)
    if not args.quiet:
        print("  " + json.dumps(summary, default=str), flush=True)
