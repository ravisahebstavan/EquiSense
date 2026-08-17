"""Live market-data provider — free data, no bulk storage (§5 rearchitecture).

The original data plane STORED a decade of daily bars for the whole universe in
Postgres and read them back to build every view. On a free tier that meters data
transfer, reading the price panel is the single largest recurring egress in the
system — it is what exhausted the quota and, once the writes could no longer land,
what left every page STALE. Storing bulk market data you can re-fetch for free is
paying rent, in the one currency the tier actually rations, for a copy of
something the source gives away.

This module removes that. It fetches the universe's recent OHLCV **live** from
Yahoo (keyless, free) and caches it IN PROCESS for the life of a warm serverless
instance. Nothing bulk is persisted: the only thing that survives a cold start is
the few-hundred-KB computed snapshot and the KB-scale ledger. The consequence the
user asked for falls straight out — the data cannot go stale, because it is
re-fetched from the source rather than read from a store that a broken cron stopped
updating.

Design constraints honoured:
  * ONE batch request per chunk (yf.download group_by ticker), never per-name.
  * In-process TTL cache, tighter while NSE is open, loose after the close — a
    final EOD bar does not change, so re-fetching it is pure waste.
  * Failure is REPORTED, never silent: a partial fetch returns what it got plus a
    status naming what it missed, so a thin universe is visible instead of being
    read as "the market moved".
  * Two price conventions per bar, exactly as the stored path did (total-return
    `close` for returns/vol, nominal `close_raw` where a price meets a per-share
    figure) — the distinction is not optional and re-deriving it wrong would
    reintroduce the historical-P/E bias the stored path was careful to avoid.

The returned series shape is IDENTICAL to snapshot._bulk_prices' value tuple —
(dates, closes, volumes, nominal, opens, highs, lows) — so the snapshot builder
consumes it with no knowledge of where the bars came from.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Optional

log = logging.getLogger("equisense.live_provider")

# Recent-history window fetched live. Two years covers every signal the snapshot
# computes — 12-1 momentum needs ~13 months, the 200-day trend and 252-day spark
# less — with headroom for holidays and gaps. It is deliberately NOT ten years:
# the daily view needs recency, not depth, and depth is what made the stored panel
# expensive. Deep history for base-rate studies is a separate, occasional job.
DEFAULT_YEARS = 2

# Cache freshness. During the NSE session prices move, so a short TTL keeps the
# view live; after the close the EOD bar is final and a long TTL avoids re-paying
# for identical numbers (the same clock the client's poll loop already respects).
TTL_OPEN_S = 300.0        # 5 min while the exchange is open
TTL_CLOSED_S = 3600.0     # 1 h once it has closed

# Batch size for yf.download. Yahoo throttles large single requests and cloud IPs
# both; 40 keeps each request modest while staying far below a per-name loop.
CHUNK = 40

# {(-frozenset key not used-) } — cache is keyed by the ticker tuple + years.
_CACHE: dict = {"key": None, "series": None, "status": None, "fetched_at": 0.0}
_INDEX_CACHE: dict = {}


def _nse_open_now() -> bool:
    """NSE cash session in IST, weekday 09:15–15:30. Holidays are not modelled
    here — treating a holiday as 'open' only shortens the cache TTL, never
    corrupts data, so the conservative direction is cheap."""
    ist = datetime.now(timezone.utc).astimezone(
        timezone(_ist_offset()))
    if ist.weekday() >= 5:
        return False
    hm = ist.hour * 60 + ist.minute
    return 9 * 60 + 15 <= hm <= 15 * 60 + 30


def _ist_offset():
    from datetime import timedelta
    return timedelta(hours=5, minutes=30)


def _ttl_now() -> float:
    return TTL_OPEN_S if _nse_open_now() else TTL_CLOSED_S


def _frame_to_series(frame) -> Optional[tuple]:
    """One yfinance per-ticker frame → the snapshot's 7-tuple, or None if empty.

    Pure and dependency-light so it is unit-testable without a network: hand it a
    DataFrame shaped like yfinance's (Close / Adj Close / Volume / Open / High /
    Low, DatetimeIndex) and it returns exactly what the stored loader produced.
    """
    try:
        nominal_s = frame["Close"].dropna()
    except Exception:                                  # noqa: BLE001
        return None
    if len(nominal_s) == 0:
        return None
    has_adj = "Adj Close" in frame.columns
    dates: list[date] = []
    closes: list[float] = []
    volumes: list = []
    nominal: list = []
    opens: list = []
    highs: list = []
    lows: list = []
    o_s = frame["Open"] if "Open" in frame.columns else None
    h_s = frame["High"] if "High" in frame.columns else None
    l_s = frame["Low"] if "Low" in frame.columns else None
    v_s = frame["Volume"] if "Volume" in frame.columns else None
    adj_s = frame["Adj Close"] if has_adj else None

    def val(series, idx):
        if series is None:
            return None
        try:
            x = series.loc[idx]
        except Exception:                              # noqa: BLE001
            return None
        try:
            import math
            fx = float(x)
            return None if math.isnan(fx) else fx
        except Exception:                              # noqa: BLE001
            return None

    for idx, nom in nominal_s.items():
        try:
            nom_f = float(nom)
        except Exception:                              # noqa: BLE001
            continue
        d = idx.date() if hasattr(idx, "date") else idx
        adj = val(adj_s, idx)
        dates.append(d)
        closes.append(nom_f if adj is None else adj)   # total-return basis
        nominal.append(nom_f)                          # nominal (split-only)
        volumes.append(val(v_s, idx))
        opens.append(val(o_s, idx))
        highs.append(val(h_s, idx))
        lows.append(val(l_s, idx))
    if not dates:
        return None
    return (dates, closes, volumes, nominal, opens, highs, lows)


# Hard per-request network timeout. A serverless function has a fixed budget and
# a hung fetch that never returns will burn the whole of it and 5xx the request
# — worse than a reported partial fetch. yfinance passes this to the underlying
# HTTP session, so a throttling or unreachable Yahoo fails fast and the caller's
# degrade-and-report path takes over instead of the platform's timeout.
FETCH_TIMEOUT_S = 20


def _download(symbols: list[str], years: int):
    """Isolated so tests can monkeypatch it. Imports yfinance lazily so importing
    this module never requires the network dependency to be resolvable."""
    import yfinance as yf
    return yf.download(symbols, period=f"{years}y", interval="1d",
                       auto_adjust=False, actions=False, progress=False,
                       group_by="ticker", threads=True, timeout=FETCH_TIMEOUT_S)


def _yahoo_symbol(ticker: str) -> str:
    from .universe import yahoo_symbol
    return yahoo_symbol(ticker)


def get_universe_prices(tickers: list[str], years: int = DEFAULT_YEARS,
                        force: bool = False) -> tuple[dict[str, tuple], dict]:
    """{ticker: 7-tuple series} fetched live from Yahoo, in-process TTL-cached.

    Returns (series_by_ticker, status). `status` reports coverage and any names
    that came back empty, so a degraded fetch is visible rather than silently
    thinning the cross-section (which would move every percentile rank).
    """
    key = (tuple(sorted(tickers)), years)
    now = time.time()
    if (not force and _CACHE["key"] == key and _CACHE["series"] is not None
            and now - _CACHE["fetched_at"] < _ttl_now()):
        return _CACHE["series"], _CACHE["status"]

    series: dict[str, tuple] = {}
    missing: list[str] = []
    errors: list[str] = []
    for i in range(0, len(tickers), CHUNK):
        batch = tickers[i:i + CHUNK]
        symbols = [_yahoo_symbol(t) for t in batch]
        try:
            data = _download(symbols, years)
        except Exception as exc:                       # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {str(exc)[:80]}")
            missing.extend(batch)
            continue
        for t, sym in zip(batch, symbols):
            try:
                frame = data[sym] if len(symbols) > 1 else data
            except Exception:                          # noqa: BLE001
                missing.append(t)
                continue
            s = _frame_to_series(frame)
            if s is None:
                missing.append(t)
            else:
                series[t] = s

    status = {
        "provider": "yahoo_live",
        "requested": len(tickers),
        "returned": len(series),
        "coverage_pct": round(100 * len(series) / len(tickers), 1) if tickers else 0.0,
        "missing": missing[:50],
        "missing_count": len(missing),
        "errors": errors[:10],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "window_years": years,
        "note": ("Prices fetched live from Yahoo Finance (free, keyless) and cached "
                 "in-process; nothing bulk is stored. A partial fetch is reported "
                 "here rather than read as market movement."),
    }
    # Only overwrite the cache on a materially complete fetch. A transient Yahoo
    # failure returning almost nothing must not evict a good recent snapshot and
    # replace every view with holes — the stale-but-real prior is better than a
    # freshly-fetched near-empty cross-section.
    if series and (status["coverage_pct"] >= 50.0 or _CACHE["series"] is None):
        _CACHE.update(key=key, series=series, status=status, fetched_at=now)
        return series, status
    if _CACHE["series"] is not None:
        degraded = dict(_CACHE["status"] or {})
        degraded["stale_served"] = True
        degraded["last_fetch_attempt"] = status
        return _CACHE["series"], degraded
    return series, status


def _index_series(symbol: str, years: int) -> tuple[list, list]:
    """(dates, closes) for a macro index, live + cached. The dated form is what
    the benchmark counterfactual needs (align trades to index prices by date);
    callers wanting just closes use get_index_series."""
    now = time.time()
    c = _INDEX_CACHE.get(symbol)
    if c and now - c["fetched_at"] < _ttl_now():
        return c["dates"], c["closes"]
    dates: list = []
    closes: list = []
    try:
        s = _frame_to_series(_download([symbol], years))
        if s is not None:
            dates, closes = s[0], s[1]                 # total-return closes
    except Exception as exc:                           # noqa: BLE001
        log.warning("index fetch failed for %s: %s", symbol, exc)
        if c:
            return c["dates"], c["closes"]
    _INDEX_CACHE[symbol] = {"dates": dates, "closes": closes, "fetched_at": now}
    return dates, closes


def get_index_series(symbol: str = "^NSEI", years: int = DEFAULT_YEARS) -> list[float]:
    """Total-return-ish close series for a macro index (NIFTY), live + cached.
    Used for relative strength."""
    return _index_series(symbol, years)[1]


def index_price_on(symbol: str, on_date, years: int = DEFAULT_YEARS) -> Optional[float]:
    """Last index close on or before `on_date`, from the live cache — the
    benchmark leg of the paper account's alpha measurement in live mode."""
    dates, closes = _index_series(symbol, years)
    if not dates:
        return None
    target = on_date.date() if hasattr(on_date, "date") else on_date
    lo, hi, best = 0, len(dates) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= target:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return closes[best] if best is not None else None


def index_latest(symbol: str, years: int = DEFAULT_YEARS) -> tuple[Optional[float], Optional[object]]:
    """(latest close, its date) for a macro index from the live cache."""
    dates, closes = _index_series(symbol, years)
    if not dates:
        return None, None
    return closes[-1], dates[-1]


def latest_price(ticker: str) -> Optional[float]:
    """Most recent live close for one ticker, from the warm cache if present.
    Lets price consumers (paper marks) work without a stored bar."""
    series = _CACHE.get("series") or {}
    s = series.get((ticker or "").upper()) or series.get(ticker)
    if s and s[1]:
        return s[1][-1]
    return None


def price_on(ticker: str, on_date, series_key: int = 1) -> Optional[float]:
    """Total-return close on or before `on_date` from the warm live cache.

    This is what lets the SELF-IMPROVING LOOP keep working with nothing stored:
    scoring a claim means comparing the price when it was made against the price
    at its horizon, and in live mode both live in the cached 2-year series rather
    than a price table. Binary search over the (ascending) dates for the last bar
    at or before the target date — the same 'last close on/before d' rule the
    stored path used. `series_key` selects total-return (1) vs nominal (3).
    """
    series = _CACHE.get("series") or {}
    s = series.get((ticker or "").upper()) or series.get(ticker)
    if not s or not s[0]:
        return None
    dates, vals = s[0], s[series_key]
    target = on_date.date() if hasattr(on_date, "date") else on_date
    lo, hi, best = 0, len(dates) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= target:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return vals[best] if best is not None else None


def cache_state() -> dict:
    """Introspection for the data-health surface: is the live cache warm, how old,
    how much coverage. Never touches the network."""
    st = _CACHE.get("status") or {}
    return {
        "warm": _CACHE.get("series") is not None,
        "age_seconds": round(time.time() - _CACHE["fetched_at"], 1)
        if _CACHE.get("fetched_at") else None,
        "ttl_seconds": _ttl_now(),
        "nse_open": _nse_open_now(),
        "coverage_pct": st.get("coverage_pct"),
        "returned": st.get("returned"),
        "requested": st.get("requested"),
    }
