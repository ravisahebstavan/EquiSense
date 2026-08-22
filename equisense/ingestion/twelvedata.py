"""Twelve Data price provider — keyed REST, works from a cloud IP.

Replaces the yfinance feed. yfinance is an unofficial Yahoo scraper, and Yahoo
blocks the datacentre IPs a serverless deployment runs on — the direct cause of
much of the staleness this platform fought. Twelve Data is a real REST API with a
static bearer key: no daily OAuth (unlike broker APIs), no cloud-IP blocking, and
NSE coverage on the free tier, which suffices for an end-of-day signal platform
that already caches hard and is explicitly "not an HFT".

Contract: `fetch_series` returns {ticker: 7-tuple} where the tuple is exactly the
shape the rest of the pipeline already speaks —
(dates, closes, volumes, nominal, opens, highs, lows) — so live_provider,
the snapshot builder and the studies consume it with no idea the source changed.

RATE LIMIT — read before wiring a full-universe pull. The free tier is 8 API
credits per minute (800/day), and one symbol is one credit, so fetching ~226
names at once is ~28 minutes and will never complete inside a serverless
function. This module is the transport only; the caller is responsible for
staying inside the budget — the design that does so is an INCREMENTAL refresh
into the KV panel (a bounded number of names per cron run), not a live pull of
the whole universe on one request.

TOTAL-RETURN CAVEAT: the free tier returns raw OHLCV, not a dividend-adjusted
close. The pipeline's two-price convention (total-return `close` vs nominal
`close_raw`) therefore collapses to nominal on this provider until dividend data
is layered in from FMP, so total return is understated by the dividend yield.
Stated here so it is never mistaken for a dividend-reinvested series.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timezone
from typing import Optional

log = logging.getLogger("equisense.twelvedata")

API_URL = "https://api.twelvedata.com/time_series"
TIMEOUT_S = 20
# Symbols per HTTP request. Twelve Data accepts a comma-separated batch; each
# symbol still costs one credit, so this bounds the RESPONSE size, not the credit
# spend (the caller bounds that by how many names it asks for per run).
BATCH = 30

# NSE is the exchange for the whole universe. Kept explicit in every request so a
# ticker that also lists on another exchange never resolves to the wrong quote.
EXCHANGE = "NSE"

# Provider-specific symbol fixes. The Yahoo feed remapped TATAMOTORS to a
# demerger artefact (TMPV); Twelve Data quotes NSE tickers directly, so the
# override set starts empty and is the place to pin any that need it.
TD_SYMBOL_OVERRIDES: dict[str, str] = {}


def api_key() -> str:
    return os.environ.get("TWELVEDATA_API_KEY", "")


def configured() -> bool:
    return bool(api_key())


def td_symbol(ticker: str) -> str:
    return TD_SYMBOL_OVERRIDES.get(ticker, ticker)


def _request(params: dict) -> dict:
    """One Twelve Data GET. Isolated so tests monkeypatch it — the parser and
    batching are then exercised offline with fixture JSON, never the network."""
    import requests                                    # declared dep, lazy import

    r = requests.get(API_URL, params=params, timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _values_to_series(values: list) -> Optional[tuple]:
    """A Twelve Data `values` array → the pipeline's 7-tuple, ascending by date.

    Each element is {datetime, open, high, low, close, volume}. Twelve Data
    returns newest-first; we sort ascending so the tuple matches the stored
    loader's order (oldest → newest), which every downstream binary search over
    dates assumes."""
    if not values:
        return None
    rows = []
    for v in values:
        ds = v.get("datetime")
        close = _num(v.get("close"))
        if not ds or close is None:
            continue
        try:
            d = datetime.strptime(ds[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        rows.append((d, close, _num(v.get("volume")), _num(v.get("open")),
                     _num(v.get("high")), _num(v.get("low"))))
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]              # no adj close on free tier
    volumes = [r[2] for r in rows]
    opens = [r[3] for r in rows]
    highs = [r[4] for r in rows]
    lows = [r[5] for r in rows]
    # closes serves BOTH the total-return and nominal slots (no adj available);
    # documented at module top. nominal == closes here.
    return (dates, closes, volumes, list(closes), opens, highs, lows)


def _parse_response(body: dict, symbols: list[str]) -> dict[str, tuple]:
    """Normalise a single- or multi-symbol response into {td_symbol: 7-tuple}.

    Twelve Data wraps a multi-symbol reply as {SYM: {...}, ...} but returns a
    single-symbol reply bare, so both shapes are handled. A per-symbol error
    (status != 'ok') is skipped, not raised — one bad ticker must not lose the
    batch."""
    out: dict[str, tuple] = {}
    # single-symbol shape: the object itself carries 'values'
    if "values" in body or body.get("status") == "error":
        blocks = {symbols[0]: body} if len(symbols) == 1 else {}
    else:
        blocks = body
    for sym, block in blocks.items():
        if not isinstance(block, dict) or block.get("status") == "error":
            continue
        s = _values_to_series(block.get("values") or [])
        if s is not None:
            out[sym] = s
    return out


def fetch_series(tickers: list[str], years: int) -> tuple[dict[str, tuple], dict]:
    """{ticker: 7-tuple} for the given tickers over `years` of daily bars, plus a
    status dict reporting coverage and any names that came back empty — the same
    degrade-and-report contract the Yahoo provider honoured.

    outputsize is capped at Twelve Data's per-request maximum; a 10-year daily
    pull (~2500 rows) fits under the 5000-row cap."""
    if not configured():
        return {}, {"provider": "twelvedata", "requested": len(tickers),
                    "returned": 0, "coverage_pct": 0.0,
                    "errors": ["TWELVEDATA_API_KEY is not set"],
                    "note": "No Twelve Data key configured; returned nothing."}
    outputsize = min(5000, years * 260 + 20)
    series: dict[str, tuple] = {}
    missing: list[str] = []
    errors: list[str] = []
    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i + BATCH]
        symbols = [td_symbol(t) for t in batch]
        try:
            body = _request({"symbol": ",".join(symbols), "exchange": EXCHANGE,
                             "interval": "1day", "outputsize": outputsize,
                             "order": "DESC", "apikey": api_key()})
        except Exception as exc:                        # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {str(exc)[:80]}")
            missing.extend(batch)
            continue
        by_sym = _parse_response(body, symbols)
        for t, sym in zip(batch, symbols):
            s = by_sym.get(sym)
            if s is None:
                missing.append(t)
            else:
                series[t] = s
    status = {
        "provider": "twelvedata",
        "requested": len(tickers),
        "returned": len(series),
        "coverage_pct": round(100 * len(series) / len(tickers), 1) if tickers else 0.0,
        "missing": missing[:50],
        "missing_count": len(missing),
        "errors": errors[:10],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "window_years": years,
        "note": ("Prices from Twelve Data (keyed REST, cloud-friendly). Free tier "
                 "returns raw OHLCV, so total-return close equals nominal until "
                 "dividends are layered in."),
    }
    return series, status
