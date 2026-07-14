"""CLI: python -m equisense.ingestion [--years 10] [--skip-fundamentals]"""
import argparse
import logging

from ..db import ensure_schema, get_session
from .yahoo import run_full_ingest

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

parser = argparse.ArgumentParser(description="EquiSense live data ingestion")
parser.add_argument("--years", type=int, default=10)
parser.add_argument("--skip-fundamentals", action="store_true")
args = parser.parse_args()

ensure_schema()
with get_session() as s:
    report = run_full_ingest(s, years=args.years, skip_fundamentals=args.skip_fundamentals)
print(report)
