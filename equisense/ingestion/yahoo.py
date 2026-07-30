"""Yahoo Finance ingestion adapter (free, keyless).

Isolated per RESEARCH_BLUEPRINT §6.3 — source death is a *when*, not an *if*;
nothing outside this module knows Yahoo exists. All fundamentals ingested here
are flagged pit_grade="reconstructed" (§6.1): Yahoo serves latest-known, not
point-in-time, figures. Prices don't restate, so price history is PIT-safe.

Monetary values normalized to ₹ crore; shares to crore shares.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime, timezone

import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Company, FilingPeriod, MacroObservation, PriceObservation
from .universe import MACRO_SERIES, NIFTY50, is_financial, yahoo_symbol
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


def sync_universe(session: Session, index_key: str = "nifty50") -> dict[str, int]:
    """Create/refresh Company rows from the EXCHANGE'S OWN index membership.

    Membership, industry classification and ISINs come from NSE's published
    constituent CSV rather than a hand-maintained dict, so an index reshuffle is
    picked up automatically. The pinned NIFTY50 map remains only as an offline
    fallback and the fallback is reported, never silent — analysing a stale
    membership list means holding names the index dropped and missing the ones
    it added.
    """
    from .universe import resolve_universe

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

    Append-only by default. Pass ``refetch=True`` to rewrite existing bars,
    which is required once to backfill ``close_raw`` on a database ingested
    before this change.
    """
    tickers = list(ids)
    inserted = 0
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
            latest = None if refetch else session.scalar(
                select(func.max(PriceObservation.obs_date))
                .where(PriceObservation.company_id == cid))
            try:
                frame = data[sym] if len(symbols) > 1 else data
                nominal = frame["Close"].dropna()
                total_ret = frame["Adj Close"] if "Adj Close" in frame else frame["Close"]
                volumes = frame["Volume"]
                divs = frame["Dividends"] if "Dividends" in frame else None
            except KeyError:
                log.warning("no price data for %s", sym)
                continue
            store_raw(frame, "yahoo", f"history/{sym}", {"years": years}, session=session)
            existing: dict = {}
            if refetch:
                existing = {r.obs_date: r for r in session.scalars(
                    select(PriceObservation)
                    .where(PriceObservation.company_id == cid)).all()}
            rows = []
            for d, nom in nominal.items():
                day = d.date()
                if latest is not None and day <= latest:
                    continue
                adj = total_ret.get(d)
                adj = float(nom) if adj is None or math.isnan(adj) else float(adj)
                v = volumes.get(d)
                v = None if v is None or math.isnan(v) else float(v)
                dv = None
                if divs is not None:
                    raw_dv = divs.get(d)
                    if raw_dv is not None and not math.isnan(raw_dv) and raw_dv > 0:
                        dv = float(raw_dv)
                row = existing.get(day)
                if row is not None:
                    row.close, row.close_raw, row.volume = adj, float(nom), v
                    row.dividend = dv
                    inserted += 1
                    continue
                rows.append({"company_id": cid, "obs_date": day,
                             "close": adj, "close_raw": float(nom),
                             "volume": v, "dividend": dv})
            if rows:
                session.bulk_insert_mappings(PriceObservation, rows)
                inserted += len(rows)
        session.commit()
    return inserted


def refresh_quotes(session: Session, ids: dict[str, int]) -> dict:
    """Near-live price refresh: re-fetch the last 5 daily bars and UPSERT —
    today's running bar updates intraday (Yahoo serves the live running
    candle, typically ≤15 min delayed for NSE). This is what keeps paper
    fills anchored to executable reality while the site is open."""
    symbols = [yahoo_symbol(t) for t in ids]
    data = yf.download(symbols, period="5d", interval="1d", auto_adjust=False,
                       progress=False, group_by="ticker", threads=True)
    updated = inserted = 0
    latest_prices: dict[str, float] = {}
    for t, sym in zip(ids, symbols):
        cid = ids[t]
        try:
            frame = data[sym] if len(symbols) > 1 else data
            nominal = frame["Close"].dropna()
            total_ret = frame["Adj Close"] if "Adj Close" in frame else frame["Close"]
            volumes = frame["Volume"]
        except KeyError:
            continue
        for d, nom in nominal.items():
            v = volumes.get(d)
            v = None if v is None or math.isnan(v) else float(v)
            adj = total_ret.get(d)
            adj = float(nom) if adj is None or math.isnan(adj) else float(adj)
            row = session.execute(
                select(PriceObservation)
                .where(PriceObservation.company_id == cid,
                       PriceObservation.obs_date == d.date())).scalar_one_or_none()
            if row is None:
                session.add(PriceObservation(company_id=cid, obs_date=d.date(),
                                             close=adj, close_raw=float(nom),
                                             volume=v))
                inserted += 1
            elif (abs(row.close - adj) > 1e-9 or row.close_raw is None
                  or abs((row.close_raw or 0) - float(nom)) > 1e-9
                  or (v is not None and row.volume != v)):
                row.close = adj
                row.close_raw = float(nom)
                row.volume = v
                updated += 1
        # paper fills and displayed quotes must use the NOMINAL traded price
        if len(nominal):
            latest_prices[t] = round(float(nominal.iloc[-1]), 2)
    session.commit()
    return {"updated": updated, "inserted": inserted, "prices": latest_prices,
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
    inserted = 0
    for symbol, (name, role) in MACRO_SERIES.items():
        latest = session.scalar(select(func.max(MacroObservation.obs_date))
                                .where(MacroObservation.symbol == symbol))
        hist = yf.Ticker(symbol).history(period=f"{years}y", interval="1d")
        if hist is None or hist.empty:
            log.warning("no macro data for %s", symbol)
            continue
        store_raw(hist, "yahoo", f"macro/{symbol}", {"years": years}, session=session)
        closes = hist["Close"].dropna()
        rows = [{"symbol": symbol, "role": role, "obs_date": d.date(), "close": float(c)}
                for d, c in closes.items()
                if latest is None or d.date() > latest]
        if rows:
            session.bulk_insert_mappings(MacroObservation, rows)
            inserted += len(rows)
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
            # supersede any demo rows for the same fiscal year (§14.4 spirit)
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
