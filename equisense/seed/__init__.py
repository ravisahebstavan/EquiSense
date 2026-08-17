"""Seeding is DISABLED — the platform runs on real data only.

The demonstration seed fabricated fundamentals and prices for real Indian
companies to give the UI something to render before any ingestion had run. That
is exactly the "fake numbers that read as real signals" this product is built to
never show, and the owner's standing instruction is that no demo or synthetic
data appear anywhere. So `seed` is now a no-op: a fresh database stays empty until
live data is fetched (which, in live mode, happens on the first request). The
legacy generator survives in `demo_data.py` for reference only and is never
called from the application.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("equisense.seed")


def seed(session=None, *args, **kwargs) -> None:  # noqa: D401 - intentional no-op
    """No-op. Real data only; nothing synthetic is ever written."""
    _log.info("seed() called but seeding is disabled — real data only.")
    return None
