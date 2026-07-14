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


def sync_universe(session: Session) -> dict[str, int]:
    """Create/refresh Company rows for the configured universe. Companies that
    were demo-seeded flip to real once live data lands."""
    ids: dict[str, int] = {}
    for ticker, (name, sector, cap_band, peer_group) in NIFTY50.items():
        row = session.scalars(select(Company).where(Company.ticker == ticker)).first()
        if row is None:
            row = Company(ticker=ticker, name=name, sector=sector)
            session.add(row)
        row.name, row.sector, row.cap_band, row.peer_group = name, sector, cap_band, peer_group
        row.is_financial = is_financial(sector)
        row.is_demo_data = False
        session.flush()
        ids[ticker] = row.id
    session.commit()
    return ids


def ingest_prices(session: Session, ids: dict[str, int], years: int = 10,
                  chunk: int = 25) -> int:
    """Append-only daily close ingestion (only rows newer than each company's
    stored max date). Batch-downloaded to respect the source."""
    tickers = list(ids)
    inserted = 0
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        symbols = [yahoo_symbol(t) for t in batch]
        data = yf.download(symbols, period=f"{years}y", interval="1d",
                           auto_adjust=True, progress=False, group_by="ticker",
                           threads=True)
        for t, sym in zip(batch, symbols):
            cid = ids[t]
            latest = session.scalar(select(func.max(PriceObservation.obs_date))
                                    .where(PriceObservation.company_id == cid))
            try:
                frame = data[sym] if len(symbols) > 1 else data
                closes = frame["Close"].dropna()
                volumes = frame["Volume"]
            except KeyError:
                log.warning("no price data for %s", sym)
                continue
            store_raw(frame, "yahoo", f"history/{sym}", {"years": years}, session=session)
            rows = []
            for d, c in closes.items():
                if latest is not None and d.date() <= latest:
                    continue
                v = volumes.get(d)
                rows.append({"company_id": cid, "obs_date": d.date(),
                             "close": float(c),
                             "volume": None if v is None or math.isnan(v) else float(v)})
            if rows:
                session.bulk_insert_mappings(PriceObservation, rows)
                inserted += len(rows)
        session.commit()
    return inserted


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
    """Annual statements per company. Financial-sector companies are skipped —
    bank statements don't fit the industrial canonical schema (flagged in the
    Company row rather than ingested wrong)."""
    added = 0
    companies = {t: session.get(Company, cid) for t, cid in ids.items()}
    for ticker, comp in companies.items():
        if comp.is_financial:
            continue
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
                    skip_fundamentals: bool = False) -> dict:
    """One-shot pipeline: universe → prices → macro → fundamentals."""
    t0 = time.time()
    ids = sync_universe(session)
    prices = ingest_prices(session, ids, years=years)
    macro = ingest_macro(session, years=years)
    fundamentals = 0 if skip_fundamentals else ingest_fundamentals(session, ids)
    return {"companies": len(ids), "price_rows": prices, "macro_rows": macro,
            "filing_rows": fundamentals, "seconds": round(time.time() - t0, 1),
            "as_of": datetime.now(timezone.utc).isoformat()}
