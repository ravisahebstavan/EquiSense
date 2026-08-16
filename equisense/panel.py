"""Columnar price panel, stored in the database as compressed arrays (§5.3).

WHY THIS EXISTS
---------------
`price_observations` is both the largest table and the hottest read in the
system, and on a metered free Postgres tier it is the only thing that has ever
actually taken the deployment down. Two separate costs:

  STORAGE  measured at 157 bytes per row. A 500-name universe over ten years is
           1.25M rows — 196 MB, roughly 40% of a 0.5 GB tier consumed by one
           table, before a single day of new data.
  EGRESS   every base-rate study, IC run, factor-portfolio fit and backtest
           calls `load_price_panel`, which reads the table UNFILTERED (the
           survivorship correction needs the delisted names). That is ~60 MB per
           run of metered transfer, and it is what exhausted the quota.

Both costs come from the same mismatch: the data is a dense two-dimensional
panel of floats, and it is being stored and shipped one row at a time, each row
carrying its own integer id, company id, date and per-column overhead. The
values themselves are a small fraction of the bytes.

WHAT THIS IS NOT
----------------
It is NOT the "compute the summary statistics and delete the raw data" pattern
that `ingestion/retention.py` argues against, and the distinction is the whole
justification. That pattern destroys information: a new hypothesis can never be
tested, and a feature builder found to be wrong can never be recomputed. This
stores **every bar, every field, at full fidelity** — the same numbers, in
column-major order instead of row-major. Nothing is summarised and nothing is
discarded, so every future hypothesis remains testable against exactly the
history the row store held.

THE SPLIT
---------
Two blobs, not one, because they are read by different things at different
rates. The research panel — close and volume — is what every study needs and is
kept as small as possible. The accounting fields — nominal close, intraday
range, dividends — are read by the volatility estimators and the per-share
valuation paths, and would otherwise triple the bytes every study transfers to
carry columns it never touches.
"""
from __future__ import annotations

import io
import json
import zlib
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Company, PanelBlob, PriceObservation

CORE = "prices_core"          # close + volume: the research panel
ACCOUNTING = "prices_acct"    # close_raw + OHLC + dividend

# float32 throughout. The mantissa carries ~7 significant decimal digits, and
# the most expensive share in the universe trades near ₹140,000 — six digits —
# so the representation error is about 1 part in 10^7 of the price. Propagated
# into a daily return that is an error of roughly 2e-5 OF THE RETURN, i.e. a
# 0.500% move reads as 0.50001%. Against that, float64 doubles both the stored
# bytes and the metered transfer of every study run. The precision that matters
# here is in the data, not the encoding.
DTYPE = np.float32

_CORE_FIELDS = ("close", "volume")
_ACCT_FIELDS = ("close_raw", "open_price", "high_price", "low_price", "dividend")


def _pack(dates: list[date], tickers: list[str],
          frames: dict[str, np.ndarray]) -> bytes:
    """Serialize a panel to one compressed byte string.

    savez_compressed rather than a bespoke format: it is numpy's own container,
    it is self-describing, and a blob written today can still be read by any
    numpy in ten years without this module. Reproducing that guarantee by hand
    would be the kind of clever infrastructure the project deliberately avoids.
    """
    buf = io.BytesIO()
    np.savez_compressed(
        buf,
        dates=np.array([d.toordinal() for d in dates], dtype=np.int32),
        tickers=np.array(tickers, dtype="U20"),
        **frames)
    return buf.getvalue()


def _unpack(blob: bytes) -> tuple[pd.DatetimeIndex, list[str], "np.lib.npyio.NpzFile"]:
    z = np.load(io.BytesIO(blob), allow_pickle=False)
    dates = [date.fromordinal(int(o)) for o in z["dates"]]
    tickers = [str(t) for t in z["tickers"]]
    return dates, tickers, z


def _frame(values: np.ndarray, dates: list[date],
           tickers: list[str]) -> pd.DataFrame:
    """Decode one field to a frame INDISTINGUISHABLE from the SQL path's.

    Both properties here are load-bearing, and getting either wrong fails
    silently rather than loudly:

    * The index is a plain object Index of ``datetime.date``, which is what
      ``pivot_table`` over a Date column produces. A DatetimeIndex of Timestamps
      looks equivalent and prints identically, but aligning it against the macro
      series — which still comes from the row store as date objects — matches on
      nothing, and every study joined against the index would quietly return an
      empty frame rather than raise.
    * Values are widened back to float64. float32 is the storage decision, not
      the compute one; leaving it narrow would make rolling sums and variances
      accumulate at reduced precision and give the two paths visibly different
      answers in the last digits.
    """
    return pd.DataFrame(values.astype(np.float64), index=pd.Index(dates),
                        columns=list(tickers))


def panel_fingerprint(session: Session) -> str:
    """Cheap identity of the current price table.

    `max(obs_date)` alone is not enough. Gap healing writes bars in the MIDDLE
    of a series — that is its entire purpose — and leaves the maximum date
    exactly where it was, so a panel rebuilt on date alone would serve the old
    holes forever. `max(id)` moves on any insert and is a primary-key lookup, so
    the pair costs two index probes rather than a count over a million rows.
    """
    latest, top_id = session.execute(
        select(func.max(PriceObservation.obs_date),
               func.max(PriceObservation.id))).one()
    return f"{latest}:{top_id}"


def build_panels(session: Session) -> dict:
    """Rebuild both blobs from the row store. Idempotent.

    Reads every non-demo bar exactly once. That is a large read, which is the
    point: it happens once per data refresh instead of once per study, and every
    consumer downstream of it stops touching the table at all.
    """
    stmt = (select(PriceObservation.company_id, PriceObservation.obs_date,
                   PriceObservation.close, PriceObservation.volume,
                   PriceObservation.close_raw, PriceObservation.open_price,
                   PriceObservation.high_price, PriceObservation.low_price,
                   PriceObservation.dividend)
            .where(PriceObservation.source != "demo"))
    df = pd.read_sql(stmt, session.connection())
    if df.empty:
        return {"built": False, "reason": "no measured price history stored"}
    df.columns = ["cid", "date", "close", "volume", "close_raw",
                  "open_price", "high_price", "low_price", "dividend"]
    from .db import note_rows
    note_rows("panel.build", len(df))

    tickers = {c.id: c.ticker for c in session.scalars(select(Company)).all()}
    df["ticker"] = df["cid"].map(tickers)
    df = df[df["ticker"].notna()]

    # Sorted, unique axes shared by both blobs, so the two decode onto the same
    # grid and a caller can index one with the other's labels.
    dates = sorted(df["date"].unique())
    cols = sorted(df["ticker"].unique())
    date_ix = {d: i for i, d in enumerate(dates)}
    col_ix = {t: i for i, t in enumerate(cols)}
    ri = df["date"].map(date_ix).to_numpy()
    ci = df["ticker"].map(col_ix).to_numpy()
    shape = (len(dates), len(cols))

    def grid(field: str) -> np.ndarray:
        # NaN, not zero, for absent — a missing bar is unknown, and zero would
        # be read as a real price of ₹0 by everything downstream.
        a = np.full(shape, np.nan, dtype=DTYPE)
        a[ri, ci] = df[field].to_numpy(dtype=np.float64)
        return a

    fp = panel_fingerprint(session)
    as_of = str(max(dates))
    out: dict = {"built": True, "as_of": as_of, "rows": len(dates),
                 "cols": len(cols), "source_rows": len(df), "blobs": {}}
    for key, fields in ((CORE, _CORE_FIELDS), (ACCOUNTING, _ACCT_FIELDS)):
        blob = _pack(dates, cols, {f: grid(f) for f in fields})
        row = session.get(PanelBlob, key)
        meta = json.dumps({"fields": list(fields), "dtype": str(np.dtype(DTYPE)),
                           "rows": len(dates), "cols": len(cols)})
        if row is None:
            session.add(PanelBlob(key=key, as_of=as_of, fingerprint=fp,
                                  nbytes=len(blob), blob=blob, meta=meta))
        else:
            row.as_of, row.fingerprint, row.nbytes = as_of, fp, len(blob)
            row.blob, row.meta = blob, meta
            row.built_at = datetime.now(timezone.utc)
        out["blobs"][key] = {"bytes": len(blob),
                             "mb": round(len(blob) / 1e6, 2)}
    session.commit()

    # What the row store would have cost to ship for the same information.
    # Reported rather than asserted: 157 bytes/row is measured on the live
    # database and is the number the storage projection already uses.
    raw = len(df) * 157
    shipped = sum(b["bytes"] for b in out["blobs"].values())
    out["row_store_equivalent_mb"] = round(raw / 1e6, 1)
    out["compression_ratio"] = round(raw / shipped, 1) if shipped else None
    return out


def load_core_panel(session: Session, require_fresh: bool = True):
    """(closes, volumes) from the blob, or None if it cannot be trusted.

    Returning None rather than stale data is deliberate. A silently outdated
    panel would make every study report yesterday's answer as today's, and the
    caller already has a correct — merely expensive — path to fall back to.
    """
    row = session.get(PanelBlob, CORE)
    if row is None:
        return None
    if require_fresh and row.fingerprint != panel_fingerprint(session):
        return None
    dates, tickers, z = _unpack(row.blob)
    return (_frame(z["close"], dates, tickers),
            _frame(z["volume"], dates, tickers))


def load_accounting_panel(session: Session, require_fresh: bool = True):
    """dict of field -> DataFrame for close_raw / OHLC / dividend."""
    row = session.get(PanelBlob, ACCOUNTING)
    if row is None:
        return None
    if require_fresh and row.fingerprint != panel_fingerprint(session):
        return None
    dates, tickers, z = _unpack(row.blob)
    return {f: _frame(z[f], dates, tickers) for f in _ACCT_FIELDS}


def panel_status(session: Session) -> dict:
    """What the blobs hold and whether they still match the row store."""
    fp = panel_fingerprint(session)
    out: dict = {"fingerprint": fp, "blobs": {}}
    total = 0
    for key in (CORE, ACCOUNTING):
        row = session.get(PanelBlob, key)
        if row is None:
            out["blobs"][key] = {"present": False}
            continue
        total += row.nbytes or 0
        out["blobs"][key] = {
            "present": True, "as_of": row.as_of,
            "mb": round((row.nbytes or 0) / 1e6, 2),
            "fresh": row.fingerprint == fp,
            "built_at": row.built_at.isoformat() if row.built_at else None,
            **json.loads(row.meta or "{}"),
        }
    out["total_mb"] = round(total / 1e6, 2)
    out["fresh"] = all(b.get("fresh") for b in out["blobs"].values()
                       if b.get("present")) and len(out["blobs"]) == 2
    return out
