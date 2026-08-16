"""Regression tests for the storage-integrity and panel layers (§5.3, §5.6).

Every test here fails against the code as it stood before: each pins a defect
that was found on the LIVE database rather than a hypothetical one.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense.db import Base, upsert
from equisense.ingestion.validate import BarRejects, clean_bar
from equisense.models import Company, PriceObservation


@pytest.fixture
def db():
    import equisense.models  # noqa: F401 - registers tables before create_all
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_prices_cid_date "
                          "ON price_observations (company_id, obs_date)"))
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _company(session, ticker="TEST"):
    c = Company(ticker=ticker, name=ticker, sector="IT")
    session.add(c)
    session.commit()
    return c.id


# ------------------------------------------------------------------- upserts

def test_reingesting_the_same_bar_does_not_duplicate_it(db):
    """The natural key is (company, date) and a second row for it is never new
    information — it is a duplicate that `pivot_table` silently AVERAGES into a
    price that never traded.

    Nothing prevented this before: the writers deduplicated in Python, which
    holds only while one writer runs. Two do — the daily cron and the browser's
    own refresh loop — and both read "absent" before either writes.
    """
    cid = _company(db)
    row = {"company_id": cid, "obs_date": dt.date(2026, 8, 14),
           "close": 100.0, "close_raw": 100.0, "volume": 1000.0,
           "open_price": 99.0, "high_price": 101.0, "low_price": 98.0,
           "dividend": None, "source": "yahoo"}
    for _ in range(3):
        upsert(db, PriceObservation, [dict(row)],
               conflict_cols=("company_id", "obs_date"),
               update_cols=("close",),
               coalesce_cols=("close_raw", "open_price", "high_price",
                              "low_price", "volume", "dividend"))
    db.commit()
    assert db.scalar(select(func.count(PriceObservation.id))) == 1


def test_a_partial_refetch_cannot_erase_fields_it_does_not_carry(db):
    """This is the bug that cost 70% of a month's intraday ranges.

    The near-live quote refresh carries fewer columns than the full history
    pull. Under an unconditional overwrite the cheap frequent path blanks what
    the expensive one populated, and Yang-Zhang volatility — which sets the stop
    distance and therefore the position size — silently degrades to a
    six-times-noisier close-to-close estimate.
    """
    cid = _company(db)
    full = {"company_id": cid, "obs_date": dt.date(2026, 8, 14), "close": 100.0,
            "close_raw": 100.0, "open_price": 99.0, "high_price": 101.0,
            "low_price": 98.0, "volume": 1000.0, "dividend": 2.5,
            "source": "yahoo"}
    coalesce = ("close_raw", "open_price", "high_price", "low_price",
                "volume", "dividend")
    upsert(db, PriceObservation, [full], ("company_id", "obs_date"),
           ("close",), coalesce)
    db.commit()

    thin = {"company_id": cid, "obs_date": dt.date(2026, 8, 14), "close": 100.5,
            "close_raw": 100.5, "open_price": None, "high_price": None,
            "low_price": None, "volume": 1200.0, "dividend": None,
            "source": "yahoo"}
    upsert(db, PriceObservation, [thin], ("company_id", "obs_date"),
           ("close",), coalesce)
    db.commit()

    got = db.scalars(select(PriceObservation)).one()
    assert got.close == 100.5, "the close must take the newer value"
    assert got.volume == 1200.0, "a non-null incoming value must win"
    assert got.high_price == 101.0, "OHLC must survive a refetch that omits it"
    assert got.dividend == 2.5, "a recorded dividend must not vanish"


def test_upsert_chunks_below_the_bind_parameter_ceiling(db):
    """Postgres binds every value and refuses past 65,535 parameters, so a
    single large INSERT is not merely slow, it fails outright."""
    cid = _company(db)
    rows = [{"company_id": cid, "obs_date": dt.date(2020, 1, 1) + dt.timedelta(days=i),
             "close": 100.0 + i, "close_raw": 100.0 + i, "volume": 1.0,
             "open_price": None, "high_price": None, "low_price": None,
             "dividend": None, "source": "yahoo"} for i in range(1500)]
    n = upsert(db, PriceObservation, rows, ("company_id", "obs_date"),
               ("close",), ("close_raw", "volume"), chunk=500)
    db.commit()
    assert n == 1500
    assert db.scalar(select(func.count(PriceObservation.id))) == 1500


# ------------------------------------------------------- boundary validation

def test_a_zero_or_negative_close_is_rejected_outright():
    r = BarRejects()
    assert clean_bar(close=0.0, rejects=r) is None
    assert clean_bar(close=-5.0, rejects=r) is None
    assert clean_bar(close=float("nan"), rejects=r) is None
    assert r.no_close == 3


def test_nan_does_not_slip_through_a_greater_than_zero_guard():
    """NaN compares False to everything, so `if x > 0` passes it straight into
    the database, where it propagates through every sum and rolling window it
    touches without ever raising."""
    bar = clean_bar(close=100.0, close_raw=100.0, volume=float("nan"))
    assert bar is not None
    assert bar["volume"] is None


def test_an_inverted_range_drops_ohlc_but_keeps_the_close():
    """Yang-Zhang takes log(h/c) and log(l/o). An inverted bar contributes a
    NEGATIVE variance term — it does not raise and does not produce NaN, it just
    makes the estimator read low, which is the direction that argues for a
    LARGER position."""
    r = BarRejects()
    bar = clean_bar(close=100.0, close_raw=100.0, open_=99.0,
                    high=97.0, low=98.0, rejects=r)     # high < low
    assert bar is not None and bar["close"] == 100.0
    assert bar["high_price"] is None and bar["low_price"] is None
    assert r.bad_range == 1


def test_the_range_is_checked_against_the_nominal_close_not_the_adjusted_one():
    """`close` is dividend-adjusted and sits BELOW the traded price by the
    cumulative yield since the bar — about 12% at the ten-year end. Bracketing
    the range around that number would reject the perfectly good intraday range
    of every older bar in the database."""
    bar = clean_bar(close=88.0,          # total-return, deflated by a decade of yield
                    close_raw=100.0,     # what actually traded
                    open_=99.5, high=101.0, low=99.0)
    assert bar is not None
    assert bar["high_price"] == 101.0, "a valid historical range was discarded"


def test_a_good_bar_passes_through_unchanged():
    bar = clean_bar(close=105.0, close_raw=105.0, open_=100.0, high=106.0,
                    low=99.0, volume=5000.0, dividend=1.25)
    assert bar == {"close": 105.0, "close_raw": 105.0, "open_price": 100.0,
                   "high_price": 106.0, "low_price": 99.0, "volume": 5000.0,
                   "dividend": 1.25}


def test_zero_dividend_is_stored_as_null():
    """The column's convention is that null means "ordinary day"; a stored 0.0
    would make every session look like an ex-date with no payment."""
    assert clean_bar(close=100.0, dividend=0.0)["dividend"] is None


# --------------------------------------------------------------- gap finding

def test_a_hole_in_the_middle_of_a_series_is_detected(db):
    """Staleness structurally cannot see this: the newest bar is current, so the
    name reports as perfectly FRESH while every return, volatility and
    correlation computed across the gap is wrong."""
    from equisense.ingestion.coverage import price_coverage

    ids = [_company(db, f"T{i}") for i in range(4)]
    start = dt.date(2024, 1, 1)
    days = [start + dt.timedelta(days=i) for i in range(120)]
    rows = []
    for k, cid in enumerate(ids):
        for i, d in enumerate(days):
            # one name loses a fortnight from the MIDDLE, then resumes
            if k == 0 and 40 <= i < 54:
                continue
            rows.append({"company_id": cid, "obs_date": d, "close": 100.0 + i,
                         "close_raw": 100.0 + i, "volume": 1000.0,
                         "open_price": 99.0 + i, "high_price": 101.0 + i,
                         "low_price": 98.0 + i, "source": "yahoo"})
    db.bulk_insert_mappings(PriceObservation, rows)
    db.commit()

    cov = price_coverage(db, members_only=False)
    assert cov["names_with_gaps"] == 1
    assert cov["missing_sessions"] == 14
    assert cov["worst"][0]["ticker"] == "T0"


def test_missing_intraday_range_is_reported_even_when_no_session_is_missing(db):
    """The exact live failure: every session present, so nothing looks wrong,
    while the estimator that sizes positions has no range to work with."""
    from equisense.ingestion.coverage import price_coverage

    cid = _company(db, "NORANGE")
    start = dt.date(2024, 1, 1)
    rows = [{"company_id": cid, "obs_date": start + dt.timedelta(days=i),
             "close": 100.0 + i, "close_raw": 100.0 + i, "volume": 1000.0,
             # the last quarter of the series has no range, as refresh_quotes
             # used to leave it
             "open_price": None if i >= 90 else 99.0 + i,
             "high_price": None if i >= 90 else 101.0 + i,
             "low_price": None if i >= 90 else 98.0 + i,
             "source": "yahoo"} for i in range(120)]
    db.bulk_insert_mappings(PriceObservation, rows)
    db.commit()

    cov = price_coverage(db, members_only=False)
    assert cov["missing_sessions"] == 0, "no session is actually absent"
    assert cov["ohlc_complete_pct"] == 75.0
    assert cov["worst"][0]["ohlc_pct"] == 75.0


def test_departed_constituents_are_not_charged_for_leaving(db):
    """Their series legitimately stops on the day they left the index (§5.1).
    Counting that as a gap would report the survivorship-bias correction as a
    data fault, every single day."""
    from equisense.ingestion.coverage import price_coverage

    live = _company(db, "LIVE")
    gone = _company(db, "GONE")
    db.get(Company, gone).is_index_member = False
    db.commit()
    start = dt.date(2024, 1, 1)
    rows = []
    for cid, n in ((live, 120), (gone, 60)):
        rows += [{"company_id": cid, "obs_date": start + dt.timedelta(days=i),
                  "close": 100.0 + i, "close_raw": 100.0 + i, "volume": 1.0,
                  "open_price": 99.0, "high_price": 101.0 + i, "low_price": 98.0,
                  "source": "yahoo"} for i in range(n)]
    db.bulk_insert_mappings(PriceObservation, rows)
    db.commit()

    assert price_coverage(db, members_only=True)["names_with_gaps"] == 0


# --------------------------------------------------------------- panel blobs

def test_the_panel_blob_reproduces_the_sql_path_exactly(db):
    """The blob is a re-encoding, not a summary. If the two paths disagree the
    optimisation has become a correctness bug, and it would be a silent one —
    every study would simply return a slightly different answer."""
    import pandas as pd

    from equisense.panel import build_panels, load_core_panel

    ids = [_company(db, f"P{i}") for i in range(5)]
    start = dt.date(2024, 1, 1)
    rows = []
    for k, cid in enumerate(ids):
        for i in range(200):
            if k == 2 and i % 7 == 0:        # a ragged column, as real data is
                continue
            rows.append({"company_id": cid, "obs_date": start + dt.timedelta(days=i),
                         "close": 100.0 + k * 10 + i * 0.5,
                         "close_raw": 100.0 + k * 10 + i * 0.5,
                         "volume": 1000.0 + i, "open_price": 99.0,
                         "high_price": 200.0, "low_price": 50.0,
                         "source": "yahoo"})
    db.bulk_insert_mappings(PriceObservation, rows)
    db.commit()
    build_panels(db)

    closes, volumes = load_core_panel(db)
    df = pd.read_sql(
        select(PriceObservation.company_id, PriceObservation.obs_date,
               PriceObservation.close, PriceObservation.volume)
        .where(PriceObservation.source != "demo"), db.connection())
    df.columns = ["cid", "date", "close", "volume"]
    tick = {c.id: c.ticker for c in db.scalars(select(Company)).all()}
    df["ticker"] = df["cid"].map(tick)
    sql = df.pivot_table(index="date", columns="ticker", values="close").sort_index()

    assert list(closes.index) == list(sql.index)
    assert list(closes.columns) == list(sql.columns)
    a, b = closes.to_numpy(), sql.to_numpy()
    assert (np.isnan(a) == np.isnan(b)).all(), "the missing-bar pattern must match"
    both = ~np.isnan(a)
    assert np.abs(a[both] - b[both]).max() / max(1.0, np.abs(b[both]).max()) < 1e-6


def test_the_panel_index_is_dates_not_timestamps(db):
    """A DatetimeIndex prints identically and aligns against NOTHING when joined
    to the macro series, which still arrives from the row store as date objects.
    Every study joined on it would quietly return an empty frame rather than
    raise."""
    from equisense.panel import build_panels, load_core_panel

    cid = _company(db, "IDX")
    db.bulk_insert_mappings(PriceObservation, [
        {"company_id": cid, "obs_date": dt.date(2024, 1, 1) + dt.timedelta(days=i),
         "close": 100.0 + i, "close_raw": 100.0 + i, "volume": 1.0,
         "source": "yahoo"} for i in range(60)])
    db.commit()
    build_panels(db)
    closes, _ = load_core_panel(db)
    assert isinstance(closes.index[0], dt.date)
    assert not isinstance(closes.index[0], dt.datetime)


def test_a_stale_panel_is_refused_rather_than_served(db):
    """Gap healing writes bars in the MIDDLE of a series and leaves the maximum
    date untouched, so a freshness check on date alone would serve the old holes
    forever."""
    from equisense.panel import build_panels, load_core_panel

    cid = _company(db, "STALE")
    db.bulk_insert_mappings(PriceObservation, [
        {"company_id": cid, "obs_date": dt.date(2024, 1, 1) + dt.timedelta(days=i),
         "close": 100.0 + i, "close_raw": 100.0 + i, "volume": 1.0,
         "source": "yahoo"} for i in range(60)])
    db.commit()
    build_panels(db)
    assert load_core_panel(db) is not None

    # backfill a hole well before the latest bar: max(obs_date) does not move
    db.add(PriceObservation(company_id=cid, obs_date=dt.date(2023, 6, 1),
                            close=90.0, close_raw=90.0, volume=1.0,
                            source="yahoo"))
    db.commit()
    assert load_core_panel(db) is None, (
        "a panel that predates a mid-series repair must not be served")


def test_seeded_prices_never_enter_the_analytical_panel(db):
    """sync_universe clears is_demo_data on every name the index contains, and
    the seeded names are real NIFTY constituents — so nine live members were
    carrying invented prices that no query could distinguish from market data.
    Provenance belongs on the observation, not the company."""
    from equisense.panel import build_panels, load_core_panel

    cid = _company(db, "REAL")
    db.bulk_insert_mappings(PriceObservation, [
        {"company_id": cid, "obs_date": dt.date(2024, 1, 1) + dt.timedelta(days=i),
         "close": 100.0 + i, "close_raw": 100.0 + i, "volume": 1.0,
         "source": "yahoo"} for i in range(60)])
    db.add(PriceObservation(company_id=cid, obs_date=dt.date(2024, 6, 1),
                            close=99999.0, source="demo"))
    db.commit()
    build_panels(db)
    closes, _ = load_core_panel(db)
    assert 99999.0 not in set(closes.to_numpy().ravel().tolist())
