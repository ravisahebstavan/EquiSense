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
import time
import zipfile
from datetime import date, datetime, timedelta
from typing import Iterable, Optional
from urllib.request import Request, urlopen

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import DeliveryStat, DerivativeQuote, IndexObservation

log = logging.getLogger("equisense.ingest.nse")

BASE = "https://nsearchives.nseindia.com"
# NSE's edge silently STALLS on a long descriptive User-Agent — the connection is
# accepted and then never returns, so the request dies on read timeout rather
# than with an HTTP error. Verified: the descriptive UA times out on every
# archive URL while a plain browser UA returns 200 immediately. Because `_get`
# swallows failures by design (a missing file is normal on holidays), that
# would have made EVERY NSE fetch return an empty list with no error surfaced.
# Keep this short and browser-like.
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 45
RETRIES = 3


def cm_bhavcopy_url(d: date) -> str:
    return f"{BASE}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def fo_bhavcopy_url(d: date) -> str:
    return f"{BASE}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def mto_url(d: date) -> str:
    return f"{BASE}/archives/equities/mto/MTO_{d:%d%m%Y}.DAT"


def index_close_url(d: date) -> str:
    return f"{BASE}/content/indices/ind_close_all_{d:%d%m%Y}.csv"


def _get(url: str, retries: int = RETRIES) -> Optional[bytes]:
    """Single archive-file fetch. Returns None on any failure — a missing file
    is the NORMAL case for holidays and weekends and must not raise.

    Failures are logged at WARNING (not INFO) because this function returning
    None is indistinguishable, to the caller, from "the exchange published
    nothing" — and a silent transport failure looks exactly like an empty
    dataset. Use `health_check()` to tell the two apart.
    """
    last = "unknown"
    for attempt in range(max(1, retries)):
        try:
            req = Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/csv,application/zip,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urlopen(req, timeout=TIMEOUT) as r:
                if r.status != 200:
                    last = f"HTTP {r.status}"
                    continue
                return r.read()
        except Exception as exc:                  # noqa: BLE001 - source liveness
            last = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < max(1, retries):
                time.sleep(1.5 * (attempt + 1))   # be gentle on a free source
    log.warning("NSE archive fetch failed after %d attempts: %s (%s)",
                max(1, retries), url, last)
    return None


def health_check() -> dict:
    """Is the archive source actually reachable, and does each file parse?

    Exists because every fetch here fails CLOSED (returns None → empty list), so
    a transport problem is otherwise indistinguishable from a quiet market day.
    Surfacing this in the Data Health page is the difference between "no
    derivatives data today" and "we have been silently blind for a week".
    """
    probe_day = date.today()
    for _ in range(7):                            # walk back to a trading day
        if probe_day.weekday() < 5:
            break
        probe_day -= timedelta(days=1)
    checks = {}
    cons = fetch_index_constituents("nifty50")
    checks["index_constituents"] = {"ok": len(cons) > 10, "rows": len(cons)}
    for name, fn in (("index_close", fetch_index_close),
                     ("delivery", fetch_delivery),
                     ("fo_bhavcopy", fetch_fo_bhavcopy)):
        rows = []
        d = probe_day
        for _ in range(5):                        # tolerate holidays / lag
            rows = fn(d)
            if rows:
                break
            d -= timedelta(days=1)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
        checks[name] = {"ok": bool(rows), "rows": len(rows), "probed_date": d.isoformat()}
    healthy = all(c["ok"] for c in checks.values())
    return {"source": "NSE official public archives", "healthy": healthy,
            "checks": checks,
            "note": ("Every fetch fails closed (None → empty), so an unreachable "
                     "source looks identical to a quiet market. This check "
                     "distinguishes them.")}


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


# Index underlyings are ALWAYS kept, whatever equity symbol filter is applied.
# They are not in the equity universe (there is no "NIFTY" company row), so a
# naive ticker filter silently discards exactly the most liquid and most useful
# contracts in the file — observed: 24k derivative rows ingested and the NIFTY
# chain still reported "no F&O data".
INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
                     "NIFTYNXT50", "NIFTYIT"}


def ingest_derivatives(session: Session, d: date,
                       symbols: Optional[Iterable[str]] = None) -> int:
    """One trading day of F&O. Idempotent: re-ingesting a date replaces it.

    `symbols` restricts to a watchlist — the full file is ~35k rows/day, which
    is fine locally but wasteful on a free hosted Postgres if only a handful of
    underlyings are ever analysed. Index underlyings are always retained.
    """
    rows = fetch_fo_bhavcopy(d)
    if not rows:
        return 0
    if symbols:
        keep = {s.upper() for s in symbols} | INDEX_UNDERLYINGS
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
             include_derivatives: bool = True,
             derivative_symbols: Optional[Iterable[str]] = None) -> dict:
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
            # Default to INDEX UNDERLYINGS ONLY. The full F&O file is ~35k
            # rows/day (~9.5 MB) and fills a free-tier Postgres in six weeks;
            # index contracts are ~4.4k rows and carry most of the liquidity.
            got += (n := ingest_derivatives(
                session, d, derivative_symbols if derivative_symbols is not None
                else list(INDEX_UNDERLYINGS)))
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


# ------------------------------------------------------- index constituents

INDEX_LISTS = {
    "nifty50": "ind_nifty50list",
    "niftynext50": "ind_niftynext50list",
    "nifty100": "ind_nifty100list",
    "nifty200": "ind_nifty200list",
    "nifty500": "ind_nifty500list",
    "niftymidcap150": "ind_niftymidcap150list",
    "niftysmallcap250": "ind_niftysmallcap250list",
}


def index_constituents_url(index_key: str) -> str:
    slug = INDEX_LISTS.get(index_key.lower(), index_key)
    return f"{BASE}/content/indices/{slug}.csv"


def parse_constituents(csv_text: str) -> list[dict]:
    """`Company Name,Industry,Symbol,Series,ISIN Code` → universe rows.

    The exchange's own membership and its own industry classification, which
    removes the need to hand-maintain either. `Industry` here is NSE's macro
    classification, not GICS — mapped to the platform's internal sector labels
    by `universe.sector_from_nse_industry`.
    """
    out: list[dict] = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        sym = (r.get("Symbol") or "").strip()
        if not sym:
            continue
        out.append({
            "ticker": sym,
            "name": (r.get("Company Name") or "").strip(),
            "nse_industry": (r.get("Industry") or "").strip(),
            "series": (r.get("Series") or "EQ").strip(),
            "isin": (r.get("ISIN Code") or "").strip() or None,
        })
    return out


def fetch_index_constituents(index_key: str = "nifty50") -> list[dict]:
    """Live index membership from NSE. Empty list on any failure so the caller
    can fall back to its pinned snapshot rather than starting with no universe."""
    raw = _get(index_constituents_url(index_key))
    return parse_constituents(raw.decode("utf-8", "replace")) if raw else []


# ------------------------------------------------------------ retention
# Free-tier Neon is ~0.5 GB. The F&O bhavcopy alone is ~35k rows/day (~9.5 MB),
# which fills the tier in about six weeks. These are the defaults that keep the
# hosted database inside its budget indefinitely; a local SQLite install can
# raise them freely.
RETAIN_DERIVATIVE_DAYS = 45      # ~1.2 MB/day at index-only scope
RETAIN_DELIVERY_DAYS = 400       # tiny rows, and the history is the signal
RETAIN_INDEX_DAYS = 4000         # ~160 rows/day, keep for valuation percentiles
VAULT_MAX_BLOBS = 500            # raw payload archive, largest single consumer


def prune(session: Session,
          derivative_days: int = RETAIN_DERIVATIVE_DAYS,
          delivery_days: int = RETAIN_DELIVERY_DAYS,
          index_days: int = RETAIN_INDEX_DAYS) -> dict:
    """Enforce the retention window. Safe to run on every refresh.

    Derivatives age out fastest and are pruned hardest: an option chain is only
    decision-relevant while its expiry is live, and the historical OI surface is
    not something the platform currently studies. Delivery and index valuation
    are kept long because their VALUE is the percentile history.
    """
    today = date.today()
    out = {}
    for name, model, col, days in (
            ("derivative_quotes", DerivativeQuote, DerivativeQuote.trade_date, derivative_days),
            ("delivery_stats", DeliveryStat, DeliveryStat.trade_date, delivery_days),
            ("index_observations", IndexObservation, IndexObservation.obs_date, index_days)):
        cutoff = today - timedelta(days=days)
        res = session.execute(delete(model).where(col < cutoff))
        out[name] = {"deleted": res.rowcount or 0, "cutoff": cutoff.isoformat()}
    session.commit()
    out["policy"] = (f"derivatives {derivative_days}d, delivery {delivery_days}d, "
                     f"index {index_days}d — sized for a ~0.5 GB free tier")
    return out


def storage_report(session: Session) -> dict:
    """Row counts and, on Postgres, real on-disk size per table."""
    from sqlalchemy import func as _f
    counts = {}
    for name, model in (("derivative_quotes", DerivativeQuote),
                        ("delivery_stats", DeliveryStat),
                        ("index_observations", IndexObservation)):
        counts[name] = session.scalar(select(_f.count()).select_from(model)) or 0
    out = {"rows": counts, "retention": {
        "derivative_days": RETAIN_DERIVATIVE_DAYS,
        "delivery_days": RETAIN_DELIVERY_DAYS,
        "index_days": RETAIN_INDEX_DAYS}}
    try:
        from ..db import IS_SQLITE
        if not IS_SQLITE:
            from sqlalchemy import text as _t
            out["database_size"] = session.execute(
                _t("select pg_size_pretty(pg_database_size(current_database()))")).scalar()
            out["largest_tables"] = [
                {"table": r[0], "rows": r[1], "size": r[2]}
                for r in session.execute(_t(
                    "select relname, n_live_tup, "
                    "pg_size_pretty(pg_total_relation_size(relid)) "
                    "from pg_stat_user_tables "
                    "order by pg_total_relation_size(relid) desc limit 6")).all()]
    except Exception:                              # noqa: BLE001 - reporting only
        pass
    return out
