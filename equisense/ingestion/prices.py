"""Price-provider selection — one seam the rest of the data plane fetches through.

The live feed is provider-pluggable so the source can change without the
snapshot builder, the studies or the paper loop knowing. Selection is by
EQUISENSE_PRICE_PROVIDER, defaulting to Twelve Data when its key is present (the
cloud-friendly keyed replacement for yfinance) and otherwise to the legacy Yahoo
path — so a deployment with no key configured still runs rather than going dark.

Every provider returns the SAME {ticker: 7-tuple} contract
(dates, closes, volumes, nominal, opens, highs, lows), so this module is a
dispatch and nothing more.
"""
from __future__ import annotations

import os
from typing import Optional


def active_provider() -> str:
    """'twelvedata' | 'yahoo'. An explicit EQUISENSE_PRICE_PROVIDER wins.

    Otherwise Twelve Data is selected only when BOTH its key AND a REST KV are
    configured — because Twelve Data's rate limit forces the KV price panel, and a
    panel with nowhere durable to live would read empty on every serverless cold
    start. Requiring both makes the migration safe: setting the key alone changes
    nothing, and the legacy keyless Yahoo path keeps serving until the KV store is
    provisioned too."""
    choice = os.environ.get("EQUISENSE_PRICE_PROVIDER", "").strip().lower()
    if choice in ("twelvedata", "yahoo"):
        return choice
    from . import twelvedata
    from .. import kv
    return "twelvedata" if (twelvedata.configured() and kv.rest_configured()) else "yahoo"


def fetch_series(tickers: list[str], years: int) -> tuple[dict[str, tuple], dict]:
    """{ticker: 7-tuple} for `tickers` over `years`, plus a status dict.

    A pull of many names against Twelve Data's free tier is rate-limited, so
    callers that need the WHOLE universe must go through the incremental KV panel
    (panel_store), not this direct fetch — this is the primitive both the panel
    refresh and any bounded ad-hoc fetch share."""
    if active_provider() == "twelvedata":
        from . import twelvedata
        return twelvedata.fetch_series(tickers, years)
    return _yahoo_fetch(tickers, years)


def _yahoo_fetch(tickers: list[str], years: int) -> tuple[dict[str, tuple], dict]:
    """Legacy Yahoo path, kept as a keyless fallback. Reuses live_provider's
    tested download+parse so there is exactly one Yahoo implementation."""
    from . import live_provider as lp
    series: dict[str, tuple] = {}
    missing: list[str] = []
    for i in range(0, len(tickers), lp.CHUNK):
        batch = tickers[i:i + lp.CHUNK]
        symbols = [lp._yahoo_symbol(t) for t in batch]
        try:
            data = lp._download(symbols, years)
        except Exception:                              # noqa: BLE001
            missing.extend(batch)
            continue
        for t, sym in zip(batch, symbols):
            try:
                frame = data[sym] if len(symbols) > 1 else data
            except Exception:                          # noqa: BLE001
                missing.append(t)
                continue
            s = lp._frame_to_series(frame)
            if s is None:
                missing.append(t)
            else:
                series[t] = s
    status = {"provider": "yahoo", "requested": len(tickers), "returned": len(series),
              "coverage_pct": round(100 * len(series) / len(tickers), 1) if tickers else 0.0,
              "missing": missing[:50], "missing_count": len(missing)}
    return series, status
