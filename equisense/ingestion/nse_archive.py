"""NSE official public archive ingestion — the exchange as a primary source.

WHY THIS EXISTS
---------------
Yahoo is an unofficial, undocumented endpoint that serves restated fundamentals
and no Indian derivatives at all. NSE publishes the same market's EOD data as
plain archive files, free, keyless, and authoritative:

  cash bhavcopy    ~3,000 instruments/day (vs the 50 currently tracked),
                   including ETFs and Sovereign Gold Bonds
  F&O bhavcopy     ~35,000 rows/day — every strike, both option types,
                   settlement price, OPEN INTEREST, change in OI, lot size and
                   the underlying's price. A real option chain and a real
                   futures term structure.
  MTO              security-wise DELIVERY percentage. engine/novel.py's crowding
                   proxy documents this as "not available from the free source";
                   it is published daily by the exchange.
  index close      ~141 indices with the exchange's own P/E, P/B and dividend
                   yield — index-level valuation history, which makes "is the
                   market itself expensive versus its own history" answerable.

DISCIPLINE (PROJECT_DRAFT §7.10)
--------------------------------
These are published archive FILES, not a scraped API: one request per file per
trading day, no session/cookie games, no polling, and a descriptive User-Agent.
That stays inside "clearly licensed or explicitly-permitted-for-personal-use"
and is deliberately different from hammering nseindia.com/api endpoints.

All parsers take bytes and are pure, so they are unit-tested against fixtures
with no network. Fetching is isolated in `_get`.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date, datetime, timedelta
from typing import Iterable, Optional
from urllib.request import Request, urlopen

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import DeliveryStat, DerivativeQuote, IndexObservation

log = logging.getLogger("equisense.ingest.nse")

BASE = "https://nsearchives.nseindia.com"
UA = ("Mozilla/5.0 (compatible; EquiSense/1.0; personal research tool; "
      "+https://github.com/ravisahebstavan)")
TIMEOUT = 45


def cm_bhavcopy_url(d: date) -> str:
    return f"{BASE}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def fo_bhavcopy_url(d: date) -> str:
    return f"{BASE}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def mto_url(d: date) -> str:
    return f"{BASE}/archives/equities/mto/MTO_{d:%d%m%Y}.DAT"


def index_close_url(d: date) -> str:
    return f"{BASE}/content/indices/ind_close_all_{d:%d%m%Y}.csv"


def _get(url: str) -> Optional[bytes]:
    """Single archive-file fetch. Returns None on any failure — a missing file
    is the NORMAL case for holidays and weekends and must not raise."""
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 200:
                return None
            return r.read()
    except Exception as exc:                      # noqa: BLE001 - source liveness
        log.info("archive fetch failed %s: %s", url, exc)
        return None


def _unzip_csv(raw: bytes) -> Optional[str]:
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
        return None if name is None else z.read(name).decode("utf-8", "replace")
    except Exception as exc:                      # noqa: BLE001
        log.warning("bhavcopy unzip failed: %s", exc)
        return None


def _f(v: str) -> Optional[float]:
    v = (v or "").strip()
    if not v or v in ("-", "NA", "nan"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _d(v: str) -> Optional[date]:
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ parsers

def parse_fo_bhavcopy(csv_text: str) -> list[dict]:
    """UDiFF F&O bhavcopy → derivative rows.

    Untraded strikes carry close/volume of 0 but still have a valid settlement
    price and open interest, so they are KEPT: the OI structure across untraded
    strikes is a large part of what makes a chain informative, and dropping them
    would silently truncate the wings of the volatility surface.
    """
    out: list[dict] = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        trade_dt = _d(r.get("TradDt", ""))
        expiry = _d(r.get("XpryDt", ""))
        if trade_dt is None or expiry is None:
            continue
        instrument = (r.get("FinInstrmTp") or "").strip()
        if instrument not in ("IDF", "IDO", "STF", "STO"):
            continue
        opt = (r.get("OptnTp") or "").strip() or None
        strike = _f(r.get("StrkPric", ""))
        lot = _f(r.get("NewBrdLotQty", ""))
        out.append({
            "trade_date": trade_dt,
            "symbol": (r.get("TckrSymb") or "").strip(),
            "instrument_type": instrument,
            "expiry": expiry,
            "strike": None if instrument in ("IDF", "STF") else strike,
            "option_type": None if instrument in ("IDF", "STF") else opt,
            "open_price": _f(r.get("OpnPric", "")),
            "high_price": _f(r.get("HghPric", "")),
            "low_price": _f(r.get("LwPric", "")),
            "close": _f(r.get("ClsPric", "")),
            "settlement_price": _f(r.get("SttlmPric", "")),
            "underlying_price": _f(r.get("UndrlygPric", "")),
            "open_interest": _f(r.get("OpnIntrst", "")),
            "change_in_oi": _f(r.get("ChngInOpnIntrst", "")),
            "volume": _f(r.get("TtlTradgVol", "")),
            "lot_size": None if lot is None else int(lot),
        })
    return out


def parse_cm_bhavcopy(csv_text: str) -> list[dict]:
    """UDiFF cash bhavcopy → nominal EOD bars for the whole listed market."""
    out: list[dict] = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        trade_dt = _d(r.get("TradDt", ""))
        close = _f(r.get("ClsPric", ""))
        if trade_dt is None or close is None:
            continue
        out.append({
            "trade_date": trade_dt,
            "symbol": (r.get("TckrSymb") or "").strip(),
            "series": (r.get("SctySrs") or "").strip(),
            "isin": (r.get("ISIN") or "").strip() or None,
            "open": _f(r.get("OpnPric", "")),
            "high": _f(r.get("HghPric", "")),
            "low": _f(r.get("LwPric", "")),
            "close": close,
            "prev_close": _f(r.get("PrvsClsgPric", "")),
            "volume": _f(r.get("TtlTradgVol", "")),
            "turnover": _f(r.get("TtlTrfVal", "")),
            "trades": _f(r.get("TtlNbOfTxsExctd", "")),
        })
    return out


def parse_mto(text: str) -> list[dict]:
    """Security-wise delivery position (MTO_DDMMYYYY.DAT).

    Layout: four preamble lines, then fixed records
      20,<srno>,<symbol>,<series>,<traded qty>,<delivered qty>,<pct>
    The trade date lives in the preamble as `Trade Date <24-JUL-2025>`, which is
    the only place it appears — the data rows carry no date.
    """
    trade_dt: Optional[date] = None
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if trade_dt is None and "Trade Date" in line:
            start = line.find("<")
            end = line.find(">", start + 1)
            if start != -1 and end != -1:
                trade_dt = _d(line[start + 1:end])
            continue
        if not line.startswith("20,"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        traded, delivered, pct = _f(parts[4]), _f(parts[5]), _f(parts[6])
        if traded is None or delivered is None:
            continue
        rows.append({"symbol": parts[2], "series": parts[3],
                     "traded_qty": traded, "delivered_qty": delivered,
                     "delivery_pct": pct if pct is not None else
                     (delivered / traded * 100 if traded else 0.0)})
    if trade_dt is None:
        return []
    for r in rows:
        r["trade_date"] = trade_dt
    return rows


def parse_index_close(csv_text: str) -> list[dict]:
    """ind_close_all_DDMMYYYY.csv → per-index EOD bar plus P/E, P/B, div yield."""
    out: list[dict] = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        obs = _d(r.get("Index Date", ""))
        close = _f(r.get("Closing Index Value", ""))
        name = (r.get("Index Name") or "").strip()
        if obs is None or close is None or not name:
            continue
        out.append({
            "index_name": name, "obs_date": obs,
            "open_value": _f(r.get("Open Index Value", "")),
            "high_value": _f(r.get("High Index Value", "")),
            "low_value": _f(r.get("Low Index Value", "")),
            "close": close,
            "volume": _f(r.get("Volume", "")),
            "turnover_cr": _f(r.get("Turnover (Rs. Cr.)", "")),
            # NSE writes "-" for indices where the metric is undefined
            "pe": _f(r.get("P/E", "")), "pb": _f(r.get("P/B", "")),
            "div_yield": _f(r.get("Div Yield", "")),
        })
    return out


# ----------------------------------------------------------------- ingestion

def fetch_fo_bhavcopy(d: date) -> list[dict]:
    raw = _get(fo_bhavcopy_url(d))
    text = _unzip_csv(raw) if raw else None
    return parse_fo_bhavcopy(text) if text else []


def fetch_cm_bhavcopy(d: date) -> list[dict]:
    raw = _get(cm_bhavcopy_url(d))
    text = _unzip_csv(raw) if raw else None
    return parse_cm_bhavcopy(text) if text else []


def fetch_delivery(d: date) -> list[dict]:
    raw = _get(mto_url(d))
    return parse_mto(raw.decode("utf-8", "replace")) if raw else []


def fetch_index_close(d: date) -> list[dict]:
    raw = _get(index_close_url(d))
    return parse_index_close(raw.decode("utf-8", "replace")) if raw else []


def _existing_dates(session: Session, model, col) -> set:
    return {r[0] for r in session.execute(select(col).distinct()).all()}


def ingest_derivatives(session: Session, d: date,
                       symbols: Optional[Iterable[str]] = None) -> int:
    """One trading day of F&O. Idempotent: re-ingesting a date replaces it.

    `symbols` restricts to a watchlist — the full file is ~35k rows/day, which
    is fine locally but wasteful on a free hosted Postgres if only a handful of
    underlyings are ever analysed.
    """
    rows = fetch_fo_bhavcopy(d)
    if not rows:
        return 0
    if symbols:
        keep = {s.upper() for s in symbols}
        rows = [r for r in rows if r["symbol"].upper() in keep]
    if not rows:
        return 0
    session.execute(delete(DerivativeQuote).where(DerivativeQuote.trade_date == d))
    session.bulk_insert_mappings(DerivativeQuote, rows)
    session.commit()
    return len(rows)


def ingest_delivery(session: Session, d: date,
                    symbols: Optional[Iterable[str]] = None) -> int:
    rows = fetch_delivery(d)
    if not rows:
        return 0
    if symbols:
        keep = {s.upper() for s in symbols}
        rows = [r for r in rows if r["symbol"].upper() in keep]
    if not rows:
        return 0
    session.execute(delete(DeliveryStat).where(DeliveryStat.trade_date == d))
    session.bulk_insert_mappings(DeliveryStat, rows)
    session.commit()
    return len(rows)


def ingest_index_close(session: Session, d: date) -> int:
    rows = fetch_index_close(d)
    if not rows:
        return 0
    session.execute(delete(IndexObservation).where(IndexObservation.obs_date == d))
    session.bulk_insert_mappings(IndexObservation, rows)
    session.commit()
    return len(rows)


def backfill(session: Session, days: int = 30, end: Optional[date] = None,
             symbols: Optional[Iterable[str]] = None,
             include_derivatives: bool = True) -> dict:
    """Walk back `days` calendar days, skipping weekends and missing files.

    Deliberately sequential and unthreaded: this is someone else's free
    infrastructure, and a backfill that hammers it is exactly the behaviour
    PROJECT_DRAFT §7.10 rules out.
    """
    end = end or date.today()
    stats = {"days_attempted": 0, "days_with_data": 0, "derivative_rows": 0,
             "delivery_rows": 0, "index_rows": 0, "dates": []}
    have_deriv = _existing_dates(session, DerivativeQuote, DerivativeQuote.trade_date)
    have_idx = _existing_dates(session, IndexObservation, IndexObservation.obs_date)
    for i in range(days):
        d = end - timedelta(days=i)
        if d.weekday() >= 5:                       # NSE is closed Sat/Sun
            continue
        stats["days_attempted"] += 1
        got = 0
        if d not in have_idx:
            got += (n := ingest_index_close(session, d))
            stats["index_rows"] += n
        if include_derivatives and d not in have_deriv:
            got += (n := ingest_derivatives(session, d, symbols))
            stats["derivative_rows"] += n
        n = ingest_delivery(session, d, symbols)
        stats["delivery_rows"] += n
        got += n
        if got:
            stats["days_with_data"] += 1
            stats["dates"].append(d.isoformat())
    stats["source"] = "NSE official public archives (nsearchives.nseindia.com)"
    stats["note"] = ("Exchange-published EOD archive files. Missing days are "
                     "holidays or not-yet-published files, not errors.")
    return stats


# -------------------------------------------------------------- chain access

def option_chain(session: Session, symbol: str, trade_date: date,
                 expiry: Optional[date] = None,
                 include_expiring_today: bool = False) -> dict:
    """Assemble one expiry's chain from stored quotes, ready for
    engine.derivatives.option_chain_analytics.

    Expiry selection defaults to the nearest expiry STRICTLY AFTER `trade_date`.
    India has weekly index expiries, so on an expiry day the nearest contract has
    zero time to run: every option is pure intrinsic, implied volatility is
    undefined, and the whole surface comes back empty (observed: 0 of 176 strikes
    solved). Set `include_expiring_today=True` only if you specifically want that
    day's settlement mechanics.
    """
    q = select(DerivativeQuote).where(
        DerivativeQuote.symbol == symbol.upper(),
        DerivativeQuote.trade_date == trade_date,
        DerivativeQuote.instrument_type.in_(("IDO", "STO")))
    if expiry is not None:
        q = q.where(DerivativeQuote.expiry == expiry)
    rows = session.scalars(q).all()
    if not rows:
        return {"symbol": symbol.upper(), "rows": 0, "quotes": [],
                "expiries": [], "reason": "no stored option quotes for this date"}
    all_expiries = sorted({r.expiry for r in rows})
    if expiry is None:
        live = [e for e in all_expiries
                if e > trade_date or (include_expiring_today and e == trade_date)]
        if not live:
            return {"symbol": symbol.upper(), "rows": 0, "quotes": [],
                    "expiries": [e.isoformat() for e in all_expiries],
                    "reason": "every stored expiry is on or before the trade date"}
        expiry = live[0]
    rows = [r for r in rows if r.expiry == expiry]
    underlying = next((r.underlying_price for r in rows if r.underlying_price), None)
    lot = next((r.lot_size for r in rows if r.lot_size), 1)
    return {
        "symbol": symbol.upper(), "trade_date": trade_date, "expiry": expiry,
        "days_to_expiry": (expiry - trade_date).days,
        "expiries_available": [e.isoformat() for e in all_expiries],
        "underlying": underlying, "lot_size": lot, "rows": len(rows),
        "quotes": [{"strike": r.strike,
                    "kind": "call" if r.option_type == "CE" else "put",
                    # settlement price is the exchange's own mark and exists for
                    # untraded strikes, where `close` is 0 and would poison IV
                    "price": r.settlement_price if r.settlement_price else r.close,
                    "open_interest": r.open_interest or 0.0,
                    "change_in_oi": r.change_in_oi or 0.0,
                    "volume": r.volume or 0.0}
                   for r in rows if r.strike and r.option_type],
    }


def futures_curve(session: Session, symbol: str, trade_date: date) -> list[tuple]:
    """[(expiry, settlement price), ...] for engine.derivatives.term_structure."""
    rows = session.scalars(
        select(DerivativeQuote).where(
            DerivativeQuote.symbol == symbol.upper(),
            DerivativeQuote.trade_date == trade_date,
            DerivativeQuote.instrument_type.in_(("IDF", "STF")))).all()
    return sorted((r.expiry, r.settlement_price or r.close)
                  for r in rows if (r.settlement_price or r.close))
