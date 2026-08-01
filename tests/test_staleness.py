"""Per-name price staleness.

Freshness had been measured only as `max(obs_date)` over the whole price table.
That is a universe-level aggregate standing in for a per-name property, so a
single name trading today made the entire dataset report itself fresh while
individual names sat frozen. Against live data it was hiding five Nifty-50
constituents 17 calendar days (13 trading sessions) behind, over a window in
which the median name in the universe moved 3.9% (p75 7.0%, max 18.7%).

The consequence is not only a wrong price on those five. `xsec_strength` ranks
every signal against the universe, so a frozen name shifts every OTHER name's
percentile — the same failure the departed index constituents caused, arriving
from a different direction.
"""
import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense.db import Base


@pytest.fixture
def db():
    import equisense.models  # noqa: F401 - registers tables before create_all
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _seed(session, tickers_and_last_session: dict[str, int], n: int = 300):
    """Each ticker gets `n` daily closes ending `lag` sessions before the
    universe's last session."""
    import equisense.models as M
    cal = [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(n + 40)]
    for ticker, lag in tickers_and_last_session.items():
        c = M.Company(ticker=ticker, name=ticker, sector="IT", cap_band="large",
                      is_index_member=True)
        session.add(c)
        session.flush()
        end = len(cal) - 1 - lag
        for i in range(end - n + 1, end + 1):
            session.add(M.PriceObservation(
                company_id=c.id, obs_date=cal[i], close=100.0 + i * 0.1,
                close_raw=100.0 + i * 0.1, volume=100000))
    session.commit()


def test_global_max_date_hides_a_frozen_name(db):
    """The bug, stated as a test: the dataset-level freshness number is
    identical whether or not a name has stopped updating."""
    from sqlalchemy import func, select

    from equisense.models import PriceObservation
    _seed(db, {"FRESH": 0, "FROZEN": 20})
    from equisense.models import Company
    fresh_id = db.query(Company).filter_by(ticker="FRESH").one().id
    frozen_id = db.query(Company).filter_by(ticker="FROZEN").one().id

    latest = db.scalar(select(func.max(PriceObservation.obs_date)))
    fresh_last = db.scalar(select(func.max(PriceObservation.obs_date))
                           .where(PriceObservation.company_id == fresh_id))
    frozen_last = db.scalar(select(func.max(PriceObservation.obs_date))
                            .where(PriceObservation.company_id == frozen_id))

    assert latest == fresh_last, "one current name sets the global maximum"
    assert (latest - frozen_last).days == 20, "while another is 20 days behind"
    # ...and the old freshness test read `latest` alone, so it saw nothing wrong.


def test_per_company_staleness_names_the_frozen_ones(db):
    from equisense.api.status import per_company_staleness
    _seed(db, {"FRESH": 0, "ALSOFRESH": 1, "FROZEN": 20})
    stale = per_company_staleness(db)
    assert set(stale) == {"FROZEN"}
    assert stale["FROZEN"] == 20


def test_a_departed_constituent_is_not_reported_as_stale(db):
    """It is expected to go quiet; flagging it would be noise that trains the
    user to ignore the warning."""
    import equisense.models as M
    from equisense.api.status import per_company_staleness
    _seed(db, {"FRESH": 0, "FROZEN": 20})
    db.query(M.Company).filter_by(ticker="FROZEN").one().is_index_member = False
    db.commit()
    assert per_company_staleness(db) == {}


def test_stale_names_stay_in_the_universe_but_leave_the_reference_distribution(db):
    """Both halves matter: the user may hold the name and must still see it,
    but it must not set anyone else's percentile."""
    from equisense.api import live
    from equisense.api.snapshot import build_universe_snapshot
    _seed(db, {f"N{i}": 0 for i in range(12)} | {"FROZEN": 20})
    snap = build_universe_snapshot(db)

    tickers = {c["ticker"] for c in snap["companies"]}
    assert "FROZEN" in tickers, "a stale name must remain visible"
    assert "FROZEN" in snap["stale_names"]
    assert snap["stale_names"]["FROZEN"] >= 1

    live._SIG_CACHE.update(key=None, signals=None)
    sigs = live.universe_signals(db)
    assert "FROZEN" not in sigs["momentum"], "frozen name must not set percentiles"
    assert len(sigs["momentum"]) == len(tickers) - 1


def test_staleness_is_counted_in_sessions_not_calendar_days(db):
    """A weekend or a holiday week must not make a healthy name look stale —
    India's exchange calendar has enough closures for calendar-day counting to
    produce steady false positives."""
    from equisense.api.snapshot import STALE_SESSIONS, build_universe_snapshot
    _seed(db, {f"N{i}": 0 for i in range(12)} | {"GAPPY": STALE_SESSIONS})
    snap = build_universe_snapshot(db)
    assert "GAPPY" not in snap["stale_names"], (
        "exactly at the threshold, measured in sessions, is not stale")


def test_frozen_name_does_not_drag_the_sector_average(db):
    from equisense.api.snapshot import build_universe_snapshot
    _seed(db, {f"N{i}": 0 for i in range(12)} | {"FROZEN": 20})
    snap = build_universe_snapshot(db)
    rel = {c["ticker"]: c["signals"]["sector_rel_mom"] for c in snap["companies"]}
    fresh = [v for t, v in rel.items() if t != "FROZEN" and v is not None]
    # every fresh name has the identical return path here, so once the frozen
    # name is excluded the sector average equals their common return and every
    # sector-relative momentum is exactly zero
    assert fresh, "sector-relative momentum should be computed"
    assert all(abs(v) < 1e-6 for v in fresh), (
        f"a frozen name leaked into the sector average: {sorted(set(fresh))[:5]}")


# ------------------------------------------------- refresh window sizing

def test_refresh_window_grows_to_cover_the_furthest_behind_name(db):
    """A fixed 5-bar window is only correct while nothing ever falls behind.
    One network failure over a long weekend is enough to exceed it, and then the
    refresh inserts the newest bars and leaves a PERMANENT hole mid-series —
    after which the name reports as fresh, because its last observation is
    current. A hole is worse than a lag: it corrupts every return, volatility
    and base rate computed across it, and nothing routine ever heals it."""
    import datetime as dt

    import equisense.models as M
    from equisense.ingestion.yahoo import _refresh_period

    today = dt.datetime.now(dt.timezone.utc).date()

    def seed(ticker, days_behind):
        c = M.Company(ticker=ticker, name=ticker, sector="IT", is_index_member=True)
        db.add(c); db.flush()
        db.add(M.PriceObservation(company_id=c.id, close=100.0,
                                  obs_date=today - dt.timedelta(days=days_behind)))
        db.commit()
        return {ticker: c.id}

    assert _refresh_period(db, seed("CURRENT", 1)) == "5d"
    ids = seed("WEEKBEHIND", 12)
    assert _refresh_period(db, ids) == "1mo", "a 12-day gap needs more than 5 bars"
    ids.update(seed("MONTHSBEHIND", 60))
    assert _refresh_period(db, ids) == "3mo"
    ids.update(seed("YEARBEHIND", 300))
    assert _refresh_period(db, ids) == "1y", "the window follows the WORST name"


def test_a_name_with_no_history_does_not_drag_the_whole_batch(db):
    """That is a backfill job, not a refresh one; widening the window for it
    would cost every other name in the batch and gain nothing."""
    import datetime as dt

    import equisense.models as M
    from equisense.ingestion.yahoo import _refresh_period

    today = dt.datetime.now(dt.timezone.utc).date()
    c1 = M.Company(ticker="HASDATA", name="a", sector="IT", is_index_member=True)
    c2 = M.Company(ticker="EMPTY", name="b", sector="IT", is_index_member=True)
    db.add_all([c1, c2]); db.flush()
    db.add(M.PriceObservation(company_id=c1.id, obs_date=today, close=100.0))
    db.commit()
    assert _refresh_period(db, {"HASDATA": c1.id, "EMPTY": c2.id}) == "5d"


def test_refresh_period_on_an_empty_batch_is_harmless(db):
    from equisense.ingestion.yahoo import _refresh_period
    assert _refresh_period(db, {}) == "5d"


def test_company_detail_carries_stale_sessions_for_the_ui():
    """The detail page is the most likely place for someone to read a price
    immediately before acting on it, so a frozen quote must not render there
    identically to a live one. Read from the snapshot rather than recomputed, so
    the detail view and the listings cannot disagree."""
    import inspect

    from equisense.api import services
    src = inspect.getsource(services.company_analysis)
    assert '"stale_sessions"' in src, "company detail omits the staleness marker"
    helper = inspect.getsource(services._stale_sessions)
    assert "get_universe" in helper, "must read the snapshot, not recompute"


def test_stale_badge_is_rendered_wherever_a_price_is_shown():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    css = (Path(__file__).resolve().parent.parent / "web" / "style.css").read_text()
    assert "function staleBadge(item)" in js
    assert js.count("staleBadge(") >= 4, (
        "expected the helper plus dashboard, company list and company header")
    assert ".stale-badge" in css, "badge is unstyled and will render as plain text"


def test_universe_snapshot_is_not_refetched_within_a_request(db):
    """Several callers want the snapshot in one request — the candidate screen,
    universe_signals, cluster_correlation and the company detail page. Measured:
    5 fetches of a few hundred KB costing 37 of that endpoint's 63 seconds."""
    from equisense.api import snapshot as S
    _seed(db, {f"N{i}": 0 for i in range(12)})
    S._UNIVERSE_CACHE.update(key=None, snap=None)
    first = S.get_universe(db)
    again = S.get_universe(db)
    assert again is first, "the payload was parsed again"


def test_new_prices_are_picked_up_once_the_freshness_probe_expires(db):
    """The cache key is the latest price date, so an ingest changes it — but the
    probe that reads that date is throttled, so the pickup is bounded by
    FRESHNESS_PROBE_TTL_S rather than instant. That is the deliberate trade: 11
    `max(obs_date)` round trips per page load cost 2.75s, and prices change at
    most once a day. Anything that writes prices in-process invalidates
    explicitly (see the refresh endpoint), so the delay only ever applies to an
    external writer such as the ingest CLI."""
    import datetime as dt

    import equisense.models as M
    from equisense.api import snapshot as S
    _seed(db, {f"N{i}": 0 for i in range(12)})
    S.invalidate_universe_cache()
    first = S.get_universe(db)

    cid = db.query(M.Company).filter_by(ticker="N0").one().id
    newest = max(p.obs_date for p in db.query(M.PriceObservation).all())
    db.add(M.PriceObservation(company_id=cid, obs_date=newest + dt.timedelta(days=1),
                              close=999.0, close_raw=999.0, volume=1))
    db.commit()

    # within the probe window the cached snapshot is still served
    assert S.get_universe(db) is first

    # once the probe expires the new price date changes the key and it rebuilds
    S._UNIVERSE_CACHE["checked_at"] = 0.0
    assert S.get_universe(db) is not first, "stale snapshot survived the TTL"


def test_freshness_probe_is_throttled_but_a_rebuild_invalidates_immediately(db):
    """The snapshot cache stopped the payload being re-parsed, but
    `SELECT max(obs_date)` still ran on EVERY call to compute the cache key —
    11 round trips on one page load, 2.75s of pure latency. Throttled, because
    prices change at most once a day. The throttle is only safe if a rebuild or
    an ingest drops it, or the refresh button appears to do nothing."""
    from equisense.api import snapshot as S
    _seed(db, {f"N{i}": 0 for i in range(12)})
    S.invalidate_universe_cache()

    first = S.get_universe(db)
    assert S.get_universe(db) is first          # served from cache

    rebuilt = S.build_universe_snapshot(db)
    assert S.get_universe(db) is rebuilt, (
        "a rebuild must be visible immediately, not after the TTL")

    S.invalidate_universe_cache()
    assert S._UNIVERSE_CACHE["snap"] is None


def test_refresh_endpoint_invalidates_the_universe_cache():
    import inspect

    from equisense.api import app as A
    src = inspect.getsource(A)
    assert "invalidate_universe_cache()" in src, (
        "refresh_quotes lands new bars but leaves a stale cached snapshot")
