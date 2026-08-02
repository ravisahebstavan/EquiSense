"""Point-in-time listed universe.

The value of this module is entirely in what it lets a study EXCLUDE, so the
tests that matter are about honesty of interpretation, not fetch mechanics.
"""
import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense.db import Base
from equisense.ingestion.listings import (bhavcopy_urls, sample_dates,
                                          survivorship_report, was_tradeable)


@pytest.fixture
def db():
    import equisense.models  # noqa: F401
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def test_both_bhavcopy_layouts_are_tried():
    """NSE changed the file layout in 2024 and kept the old archive, so a date
    near the boundary exists under only one of them."""
    urls = bhavcopy_urls(dt.date(2019, 8, 1))
    assert len(urls) == 2
    assert "BhavCopy_NSE_CM" in urls[0]
    assert "cm01AUG2019bhav.csv.zip" in urls[1]


def test_sampling_grid_skips_weekends():
    """A Saturday fetch is a guaranteed miss, and a miss is indistinguishable
    from a holiday, so the grid must not spend requests on them."""
    d = sample_dates(dt.date(2020, 1, 1), dt.date(2020, 6, 30), every_days=30)
    assert d, "grid is empty"
    assert all(x.weekday() < 5 for x in d)


def test_was_tradeable_answers_none_for_an_unsampled_symbol(db):
    """None is not False. A symbol never sampled is unknown, and treating
    unknown as 'not listed' would silently drop real names from a study."""
    assert was_tradeable(db, "NOSUCH", dt.date(2020, 1, 1)) is None


def test_was_tradeable_bounds_the_window(db):
    import equisense.models as M
    db.add(M.ListingWindow(symbol="DEADCO", first_seen=dt.date(2017, 1, 3),
                           last_seen=dt.date(2019, 6, 3), sessions_sampled=30,
                           is_delisted=True, in_panel=False))
    db.commit()
    assert was_tradeable(db, "DEADCO", dt.date(2018, 5, 1)) is True
    assert was_tradeable(db, "DEADCO", dt.date(2021, 5, 1)) is False
    assert was_tradeable(db, "deadco", dt.date(2018, 5, 1)) is True  # case-insensitive


def test_report_refuses_to_call_the_raw_gap_survivorship(db):
    """The number to resist: 'N symbols traded then and are missing now' is an
    UPPER BOUND, not a measurement. The panel is deliberately a 500-name index
    and was never meant to hold every listed micro-cap. Measured on real data
    1,515 names were listed in 2017-2019 and absent from the panel, and most
    were never index constituents — quoting that as survivorship bias would be
    alarmist and wrong."""
    import equisense.models as M
    db.add_all([
        M.ListingWindow(symbol="ALIVE", first_seen=dt.date(2017, 1, 3),
                        last_seen=dt.date(2026, 7, 3), sessions_sampled=100,
                        is_delisted=False, in_panel=True),
        M.ListingWindow(symbol="DEADCO", first_seen=dt.date(2017, 1, 3),
                        last_seen=dt.date(2019, 6, 3), sessions_sampled=30,
                        is_delisted=True, in_panel=False),
    ])
    db.commit()
    r = survivorship_report(db)
    assert r["available"] is True
    assert r["delisted"] == 1
    assert r["delisted_and_absent_from_panel"] == 1
    assert "overstates survivorship" in r["caveat"]
    assert "not index membership" in r["caveat"]


def test_report_is_honest_when_nothing_is_ingested(db):
    r = survivorship_report(db)
    assert r["available"] is False and "not ingested" in r["reason"]
