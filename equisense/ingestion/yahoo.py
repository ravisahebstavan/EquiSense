"""Yahoo Finance ingestion adapter (free, keyless).

Isolated per §5.2 — source death is a *when*, not an *if*;
nothing outside this module knows Yahoo exists. All fundamentals ingested here
are flagged pit_grade="reconstructed" (§6.1): Yahoo serves latest-known, not
point-in-time, figures. Prices don't restate, so price history is PIT-safe.

Monetary values normalized to ₹ crore; shares to crore shares.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime, timedelta, timezone

import yfinance as yf
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ..db import upsert
from ..models import Company, FilingPeriod, MacroObservation, PriceObservation
from .universe import MACRO_SERIES, NIFTY50, is_financial, yahoo_symbol
from .validate import SeriesReport, clean_bar
from .vault import store_raw

log = logging.getLogger("equisense.ingest")
CRORE = 1e7


def _f(df, labels: list[str], col) -> float | None:
    """First matching row label, ₹→crore, NaN→None."""
    if df is None or df.empty:
        return None
    for label in labels:
        if label in df.index:
            v = df.loc[label, col]
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            if math.isnan(v):
                return None
            return v / CRORE
    return None


def active_index_key(session: Session, default: str = "nifty500") -> str:
    """Which NSE index defines the live analytical universe.

    Defaults to the BROADEST index rather than NIFTY 50, because universe size
    is the binding constraint on this platform's ability to learn anything. The
    calibration gates need scored claims, claims come from names, and a 50-name
    cross-section produces them roughly ten times slower than a 500-name one —
    while also giving percentile normalisation only 50 points to rank against,
    which makes every "top quintile" a 10-name bet.
    """
    from ..models import AppSnapshot
    row = session.get(AppSnapshot, "universe_index")
    if row is None:
        return default
    try:
        import json as _json
        return (_json.loads(row.payload) or {}).get("index_key") or default
    except Exception:                                  # noqa: BLE001
        return default


def set_index_key(session: Session, index_key: str) -> str:
    import json as _json

    from ..models import AppSnapshot
    from .universe import INDEX_CAP_BAND
    key = (index_key or "").strip().lower()
    if key not in INDEX_CAP_BAND:
        raise ValueError(f"unknown index {index_key!r}; "
                         f"choose from {sorted(INDEX_CAP_BAND)}")
    row = session.get(AppSnapshot, "universe_index")
    payload = _json.dumps({"index_key": key})
    if row is None:
        session.add(AppSnapshot(key="universe_index", as_of=str(date.today()),
                                payload=payload))
    else:
        row.payload, row.as_of = payload, str(date.today())
    session.commit()
    return key


def sync_universe(session: Session, index_key: str | None = None) -> dict[str, int]:
    """Create/refresh Company rows from the EXCHANGE'S OWN index membership.

    Membership, industry classification and ISINs come from NSE's published
    constituent CSV rather than a hand-maintained dict, so an index reshuffle is
    picked up automatically. The pinned NIFTY50 map remains only as an offline
    fallback and the fallback is reported, never silent — analysing a stale
    membership list means holding names the index dropped and missing the ones
    it added.

    The index is configuration, not a constant: see active_index_key.
    """
    from .universe import resolve_universe

    index_key = index_key or active_index_key(session)
    universe, source = resolve_universe(index_key)
    log.info("universe source: %s", source)
    fell_back = "FALLBACK" in source
    if not universe:
        universe = dict(NIFTY50)
        source = "PINNED FALLBACK (empty resolution)"
        fell_back = True

    today = date.today()
    ids: dict[str, int] = {}
    for ticker, (name, sector, cap_band, peer_group) in universe.items():
        row = session.scalars(select(Company).where(Company.ticker == ticker)).first()
        if row is None:
            row = Company(ticker=ticker, name=name, sector=sector)
            session.add(row)
        row.name, row.sector, row.cap_band, row.peer_group = name, sector, cap_band, peer_group
        row.is_financial = is_financial(sector)
        row.is_demo_data = False
        row.is_index_member = True
        row.last_seen_in_index = today
        session.flush()
        ids[ticker] = row.id

    # Names the index no longer holds are DEACTIVATED, not deleted. Keeping the
    # rows preserves their price history (deleting it would manufacture the very
    # survivorship bias every base-rate record is caveated for), while clearing
    # the membership flag removes them from the live analytical universe so they
    # stop contaminating cross-sectional percentile ranking and new cohorts.
    #
    # Skipped entirely when the constituent list was unreachable: a transient
    # network failure must never be allowed to deactivate the whole universe.
    departed: list[str] = []
    if not fell_back:
        for row in session.scalars(select(Company).where(
                Company.is_demo_data == False)).all():          # noqa: E712
            if row.ticker not in universe and row.is_index_member:
                row.is_index_member = False
                departed.append(row.ticker)
    session.commit()
    log.info("synced %d companies (%s); deactivated %d departed: %s",
             len(ids), source, len(departed), ", ".join(departed) or "none")
    return ids


# Every optional field of a bar. Updated only when the incoming value is
# non-null, so a cheap refetch that carries fewer columns than the expensive one
# can never blank out what is already stored (see db.upsert).
_BAR_COALESCE = ("close_raw", "open_price", "high_price", "low_price",
                 "volume", "dividend")


def _at(series, d):
    """One value from a pandas Series by index, or None for missing/NaN.

    yfinance uses NaN, not absence, for a gap inside an otherwise valid frame,
    and every arithmetic comparison against NaN returns False — so a plain
    `if x > 0` guard passes it straight through into the database.
    """
    if series is None:
        return None
    try:
        x = series.get(d)
    except (KeyError, TypeError):
        return None
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def ingest_prices(session: Session, ids: dict[str, int], years: int = 10,
                  chunk: int = 25, refetch: bool = False) -> int:
    """Daily price ingestion, storing BOTH price conventions per bar.

    Two series are required and they are not interchangeable:

      close      total-return (splits AND dividends adjusted) — the correct basis
                 for returns, momentum, volatility and correlation.
      close_raw  nominal (splits/bonus only) — the price that actually traded,
                 needed wherever a price meets a per-share accounting figure.

    Why both: this previously fetched with ``auto_adjust=True`` and stored only
    the total-return close. Dividend adjustment back-deflates HISTORICAL prices
    (the most recent bar is unadjusted), so dividing that series by nominal
    filing EPS produced a historical P/E series biased low — which made
    ``pe_percentile_vs_history`` read systematically "expensive" and put a
    standing bearish tilt on the value cluster. At ~1.3% yield over 10 years the
    oldest bar is deflated ~12%.

    Writes are UPSERTS keyed on (company_id, obs_date), so re-running this is
    always safe and always healing. The previous shape read each company's
    maximum stored date and skipped everything at or below it, which is only
    equivalent to "already have it" when the series has no holes — and holes are
    exactly what this path exists to repair. A name that missed a fortnight in
    the middle of 2024 and is current today has a maximum date of today, so
    append-only never looked at the gap again. `refetch` is retained for the
    caller that wants the whole window rewritten unconditionally; the difference
    is now only how many bars are downloaded, not whether a gap can be fixed.
    """
    tickers = list(ids)
    inserted = 0
    report = SeriesReport()
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        symbols = [yahoo_symbol(t) for t in batch]
        # auto_adjust=False keeps BOTH columns: "Close" is split-adjusted only,
        # "Adj Close" is split+dividend adjusted.
        # actions=True returns Dividends/Stock Splits in the SAME request, so
        # dividend capture costs nothing extra. Without them, XIRR silently
        # understates money-weighted return by roughly the dividend yield.
        data = yf.download(symbols, period=f"{years}y", interval="1d",
                           auto_adjust=False, actions=True, progress=False,
                           group_by="ticker", threads=True)
        for t, sym in zip(batch, symbols):
            cid = ids[t]
            try:
                frame = data[sym] if len(symbols) > 1 else data
                nominal = frame["Close"].dropna()
                total_ret = frame["Adj Close"] if "Adj Close" in frame else frame["Close"]
                volumes = frame["Volume"]
                divs = frame["Dividends"] if "Dividends" in frame else None
                # OHLC arrives in the SAME request already being made. Storing it
                # costs nothing and unlocks range-based volatility estimators.
                o_s = frame["Open"] if "Open" in frame else None
                h_s = frame["High"] if "High" in frame else None
                l_s = frame["Low"] if "Low" in frame else None
            except KeyError:
                log.warning("no price data for %s", sym)
                continue
            store_raw(frame, "yahoo", f"history/{sym}", {"years": years}, session=session)
            rows = []
            for d, nom in nominal.items():
                adj = _at(total_ret, d)
                bar = clean_bar(close=nom if adj is None else adj, close_raw=nom,
                                open_=_at(o_s, d), high=_at(h_s, d),
                                low=_at(l_s, d), volume=_at(volumes, d),
                                dividend=_at(divs, d), rejects=report.rejects)
                if bar is None:
                    continue
                rows.append({"company_id": cid, "obs_date": d.date(), **bar})
            report.kept += len(rows)
            inserted += upsert(session, PriceObservation, rows,
                               conflict_cols=("company_id", "obs_date"),
                               update_cols=("close", "source"),
                               coalesce_cols=_BAR_COALESCE)
            # Measured prices supersede seeded ones. The synthetic path exists
            # only so the UI has something to render before any ingestion has
            # run; once this name has real bars the fabricated ones are strictly
            # worse than nothing, because they sit on real dates and no
            # downstream query can tell them apart.
            if rows:
                session.execute(
                    delete(PriceObservation)
                    .where(PriceObservation.company_id == cid,
                           PriceObservation.source == "demo"))
        session.commit()
    if report.rejects.total():
        log.warning("price ingest rejected fields on %d bars: %s",
                    report.rejects.total(), report.rejects.as_dict())
    return inserted


def _refresh_period(session: Session, ids: dict[str, int]) -> str:
    """Yahoo period long enough to cover the FURTHEST-BEHIND name in `ids`.

    A fixed 5-bar window is only correct while nothing ever falls behind. The
    moment a name misses more than five sessions — one network failure over a
    long weekend is enough — a 5-bar refresh inserts the newest bars and leaves
    a permanent hole in the middle of the series, and the name then reports as
    FRESH because its last observation is current. A hole is worse than a lag:
    it silently corrupts every return, volatility and base-rate computed across
    it, and nothing routine ever heals it.
    """
    if not ids:
        return "5d"
    last = session.execute(
        select(PriceObservation.company_id, func.max(PriceObservation.obs_date))
        .where(PriceObservation.company_id.in_(list(ids.values())))
        .group_by(PriceObservation.company_id)).all()
    today = datetime.now(timezone.utc).date()
    # a name with no rows at all is a backfill job, not a refresh one; sizing the
    # window to it would drag the whole batch to 10y for no benefit
    behind = max((today - d).days for _cid, d in last) if last else 0
    for days, period in ((5, "5d"), (25, "1mo"), (80, "3mo"),
                         (170, "6mo"), (350, "1y")):
        if behind <= days:
            return period
    return "2y"


def refresh_quotes(session: Session, ids: dict[str, int]) -> dict:
    """Near-live price refresh: re-fetch recent daily bars and UPSERT — today's
    running bar updates intraday (Yahoo serves the live running candle,
    typically ≤15 min delayed for NSE). This is what keeps paper fills anchored
    to executable reality while the site is open.

    The window is sized to the furthest-behind name rather than fixed, so a name
    that missed a week heals itself instead of acquiring a permanent gap.

    This path writes COMPLETE bars, and that is not a detail. It is the primary
    inserter of new rows — the cron calls it daily and the browser calls it
    every few minutes — while `ingest_prices` only ever runs for names with no
    history at all. It used to fetch and store close, close_raw and volume only,
    so every bar it created was permanently missing its intraday range and any
    dividend on that date, and the append-only backfill could never revisit
    them. Measured on the live database: 3,500 of the current month's 5,000 bars
    had no open/high/low.

    What that cost is specific. Yang-Zhang volatility needs consecutive valid
    OHLC and falls back to close-to-close when it cannot get it — a estimator
    roughly six times less efficient for the same window — and that volatility
    is the stop distance, which is the position size. The range arrives in the
    same HTTP response either way; only the storing was missing.
    """
    symbols = [yahoo_symbol(t) for t in ids]
    period = _refresh_period(session, ids)
    # actions=True carries Dividends in the SAME request. Without it the
    # money-weighted return understates by roughly the dividend yield, every
    # year, and the ex-date is unrecoverable later.
    data = yf.download(symbols, period=period, interval="1d", auto_adjust=False,
                       actions=True, progress=False, group_by="ticker",
                       threads=True)
    latest_prices: dict[str, float] = {}
    report = SeriesReport()
    rows: list[dict] = []

    for t, sym in zip(ids, symbols):
        cid = ids[t]
        try:
            frame = data[sym] if len(symbols) > 1 else data
            nominal = frame["Close"].dropna()
        except KeyError:
            continue
        total_ret = frame["Adj Close"] if "Adj Close" in frame else frame["Close"]
        volumes = frame["Volume"] if "Volume" in frame else None
        divs = frame["Dividends"] if "Dividends" in frame else None
        o_s = frame["Open"] if "Open" in frame else None
        h_s = frame["High"] if "High" in frame else None
        l_s = frame["Low"] if "Low" in frame else None
        for d, nom in nominal.items():
            adj = _at(total_ret, d)
            bar = clean_bar(close=nom if adj is None else adj, close_raw=nom,
                            open_=_at(o_s, d), high=_at(h_s, d), low=_at(l_s, d),
                            volume=_at(volumes, d), dividend=_at(divs, d),
                            rejects=report.rejects)
            if bar is None:
                continue
            rows.append({"company_id": cid, "obs_date": d.date(), **bar})
        # paper fills and displayed quotes must use the NOMINAL traded price
        if len(nominal):
            latest_prices[t] = round(float(nominal.iloc[-1]), 2)

    # One upsert instead of a prefetch-compare-write cycle. The prefetch that
    # used to sit here read every stored bar in the window as a full ORM entity
    # purely to decide, in the overwhelmingly common case, that nothing needed
    # writing — pure metered data transfer spent to do nothing. ON CONFLICT
    # pushes that decision to the database, which already has the rows, and is
    # correct when two refreshes overlap where the read-then-write was not.
    report.kept = len(rows)
    written = upsert(session, PriceObservation, rows,
                     conflict_cols=("company_id", "obs_date"),
                     update_cols=("close",), coalesce_cols=_BAR_COALESCE)
    session.commit()
    if report.rejects.total():
        log.warning("quote refresh rejected fields on %d bars: %s",
                    report.rejects.total(), report.rejects.as_dict())
    return {"bars_written": written, "prices": latest_prices,
            "window": period, "quality": report.as_dict(),
            "as_of_utc": datetime.now(timezone.utc).isoformat()}


def market_open_ist() -> dict:
    """NSE regular session: 09:15–15:30 IST, Mon–Fri (holidays not modeled —
    stated, not hidden)."""
    from datetime import timedelta
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    open_now = (ist.weekday() < 5
                and (ist.hour, ist.minute) >= (9, 15)
                and (ist.hour, ist.minute) <= (15, 30))
    return {"ist": ist.strftime("%H:%M"), "open": open_now,
            "note": "NSE 09:15–15:30 IST Mon–Fri; exchange holidays not modeled"}


def ingest_macro(session: Session, years: int = 10) -> int:
    """Regime inputs (NIFTY, India VIX, USD/INR, Brent).

    Upserted on (symbol, obs_date) rather than appended past the stored maximum.
    These four series drive the regime description and every conditional base
    rate; a hole in the middle of one is not visible from its latest date, and
    the append-only filter guaranteed the hole could never be seen again. They
    are also small enough — four series, ~2,500 bars each — that rewriting the
    window costs nothing worth optimising for.
    """
    inserted = 0
    for symbol, (name, role) in MACRO_SERIES.items():
        hist = yf.Ticker(symbol).history(period=f"{years}y", interval="1d")
        if hist is None or hist.empty:
            log.warning("no macro data for %s", symbol)
            continue
        store_raw(hist, "yahoo", f"macro/{symbol}", {"years": years}, session=session)
        closes = hist["Close"].dropna()
        rows = []
        for d, c in closes.items():
            v = _at(closes, d)
            if v is None or v <= 0:
                continue
            rows.append({"symbol": symbol, "role": role,
                         "obs_date": d.date(), "close": v})
        inserted += upsert(session, MacroObservation, rows,
                           conflict_cols=("symbol", "obs_date"),
                           update_cols=("close", "role"))
    session.commit()
    return inserted


# Canonical field -> candidate Yahoo row labels
_IS_MAP = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "gross_profit": ["Gross Profit"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "ebit": ["EBIT", "Operating Income"],
    "interest_expense": ["Interest Expense"],
    "pbt": ["Pretax Income"],
    "tax_expense": ["Tax Provision"],
    "net_income": ["Net Income", "Net Income Common Stockholders"],
    "interest_income": ["Interest Income"],
    "net_interest_income": ["Net Interest Income"],
    "depreciation": ["Reconciled Depreciation"],
}
_BS_MAP = {
    "total_assets": ["Total Assets"],
    "current_assets": ["Current Assets"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "inventory": ["Inventory"],
    "receivables": ["Accounts Receivable", "Receivables"],
    "current_liabilities": ["Current Liabilities"],
    "payables": ["Accounts Payable", "Payables"],
    "total_debt": ["Total Debt"],
    "total_equity": ["Stockholders Equity", "Common Stock Equity"],
    "retained_earnings": ["Retained Earnings"],
    "shares_outstanding": ["Ordinary Shares Number", "Share Issued"],
}
_CF_MAP = {
    "cfo": ["Operating Cash Flow"],
    "capex": ["Capital Expenditure"],           # negative in Yahoo → abs below
    "dividends_paid": ["Cash Dividends Paid"],  # negative in Yahoo → abs below
    "depreciation": ["Depreciation And Amortization", "Depreciation Amortization Depletion"],
}


def _fiscal_year(end: date) -> int:
    # Indian FY label: period ending Mar-2025 → FY2025; Dec-enders label by
    # following FY for consistent ordering. It is a label, not a claim.
    return end.year if end.month <= 3 else end.year + 1


def ingest_fundamentals(session: Session, ids: dict[str, int],
                        pause: float = 0.4) -> int:
    """Annual statements per company.

    Financial-sector companies are NO LONGER skipped. Their industrial line
    items (EBIT, gross profit, inventory, capex) are genuinely meaningless and
    stay null, but Yahoo does serve Interest Income, Interest Expense, Net
    Interest Income, assets and equity for banks — which is enough for the
    banking engine. Skipping them entirely left 11 of the NIFTY-50 with zero
    fundamental analysis; ingesting the fields that ARE meaningful and analysing
    them with a bank-appropriate model is strictly better than blindness.
    """
    added = 0
    companies = {t: session.get(Company, cid) for t, cid in ids.items()}
    for ticker, comp in companies.items():
        try:
            tk = yf.Ticker(yahoo_symbol(ticker))
            inc, bal, cf = tk.income_stmt, tk.balance_sheet, tk.cashflow
        except Exception as e:
            log.warning("fundamentals fetch failed for %s: %s", ticker, e)
            continue
        if inc is None or inc.empty:
            continue
        for name, frame in (("income", inc), ("balance", bal), ("cashflow", cf)):
            if frame is not None and not frame.empty:
                store_raw(frame, "yahoo", f"fundamentals/{ticker}/{name}", {}, session=session)
        for col in inc.columns:
            end = col.date() if hasattr(col, "date") else None
            if end is None:
                continue
            fy = _fiscal_year(end)
            existing = session.scalars(select(FilingPeriod).where(
                FilingPeriod.company_id == comp.id,
                FilingPeriod.fiscal_year == fy,
                FilingPeriod.scope == "consolidated",
                FilingPeriod.source == "yahoo")).first()
            if existing:
                continue  # append-only; re-ingest same FY is a no-op
            vals = {k: _f(inc, labels, col) for k, labels in _IS_MAP.items()}
            bcol = col if (bal is not None and not bal.empty and col in bal.columns) else None
            if bcol is not None:
                vals.update({k: _f(bal, labels, bcol) for k, labels in _BS_MAP.items()})
            ccol = col if (cf is not None and not cf.empty and col in cf.columns) else None
            if ccol is not None:
                cfvals = {k: _f(cf, labels, ccol) for k, labels in _CF_MAP.items()}
                for k in ("capex", "dividends_paid"):
                    if cfvals.get(k) is not None:
                        cfvals[k] = abs(cfvals[k])
                if vals.get("depreciation") is None:
                    vals["depreciation"] = cfvals.pop("depreciation", None)
                else:
                    cfvals.pop("depreciation", None)
                vals.update(cfvals)
            if vals.get("revenue") is None and vals.get("net_income") is None:
                continue
            # supersede any demo rows for the same fiscal year (§5.2 spirit)
            for old in session.scalars(select(FilingPeriod).where(
                    FilingPeriod.company_id == comp.id,
                    FilingPeriod.fiscal_year == fy,
                    FilingPeriod.source != "yahoo")).all():
                old.is_latest = False
            session.add(FilingPeriod(
                company_id=comp.id, period=f"FY{fy}", fiscal_year=fy,
                scope="consolidated", filing_date=end, restatement_version=1,
                is_latest=True, source="yahoo", pit_grade="reconstructed", **vals))
            added += 1
        session.commit()
        time.sleep(pause)  # be polite to the free source
    return added


def run_full_ingest(session: Session, years: int = 10,
                    skip_fundamentals: bool = False,
                    index_key: str = "nifty50",
                    refetch_prices: bool = False) -> dict:
    """One-shot pipeline: universe → prices → macro → fundamentals."""
    t0 = time.time()
    ids = sync_universe(session, index_key=index_key)
    prices = ingest_prices(session, ids, years=years, refetch=refetch_prices)
    macro = ingest_macro(session, years=years)
    fundamentals = 0 if skip_fundamentals else ingest_fundamentals(session, ids)
    return {"companies": len(ids), "price_rows": prices, "macro_rows": macro,
            "filing_rows": fundamentals, "seconds": round(time.time() - t0, 1),
            "index": index_key, "tickers": list(ids),
            "as_of": datetime.now(timezone.utc).isoformat()}


def backfill_ohlc(session: Session, ids: dict[str, int], years: int = 10,
                  chunk: int = 10) -> dict:
    """Populate open/high/low on existing price rows, one company per commit.

    Separate from `ingest_prices(refetch=True)` for an operational reason: that
    path loads every stored row into the identity map and issues one ORM UPDATE
    per row inside a single transaction. For 50 names x ~2,500 bars that is
    ~130,000 buffered updates, which a free-tier Postgres connection does not
    survive (observed: server terminated abnormally, whole transaction rolled
    back).

    This instead issues one bulk UPDATE per company keyed on (company_id,
    obs_date) and commits between companies, so a failure costs one name rather
    than the entire backfill, and memory stays flat.
    """
    tickers = list(ids)
    updated = skipped = 0
    failures: list[str] = []
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        symbols = [yahoo_symbol(t) for t in batch]
        try:
            data = yf.download(symbols, period=f"{years}y", interval="1d",
                               auto_adjust=False, actions=False, progress=False,
                               group_by="ticker", threads=True)
        except Exception as exc:                   # noqa: BLE001
            log.warning("OHLC batch download failed for %s: %s", batch, exc)
            failures.extend(batch)
            continue
        for t, sym in zip(batch, symbols):
            cid = ids[t]
            try:
                frame = data[sym] if len(symbols) > 1 else data
                o_s, h_s, l_s = frame["Open"], frame["High"], frame["Low"]
            except (KeyError, TypeError):
                failures.append(t)
                continue
            payload = []
            for d, op in o_s.items():
                hi, lo = h_s.get(d), l_s.get(d)
                if any(x is None or math.isnan(x) for x in (op, hi, lo)):
                    continue
                payload.append({"cid": cid, "d": d.date(), "o": float(op),
                                "h": float(hi), "l": float(lo)})
            if not payload:
                skipped += 1
                continue
            try:
                session.execute(text(
                    "UPDATE price_observations SET open_price=:o, high_price=:h, "
                    "low_price=:l WHERE company_id=:cid AND obs_date=:d"), payload)
                session.commit()
                updated += len(payload)
            except Exception as exc:               # noqa: BLE001
                session.rollback()
                log.warning("OHLC backfill failed for %s: %s", t, exc)
                failures.append(t)
        time.sleep(0.2)
    return {"rows_updated": updated, "companies_skipped": skipped,
            "failures": failures}
