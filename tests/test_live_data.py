"""Live-data plane — free data, no bulk storage (§5 rearchitecture).

Yahoo is not reachable from CI, so these exercise the PURE logic: the yfinance
frame → series transform, and that the snapshot builds from INJECTED live series
with the price panel never read from the DB. That is the whole point of the
rearchitecture — the bars come from the source, not a metered store — so the
build path must not depend on stored prices at all.
"""
import datetime as dt

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense.db import Base
from equisense.ingestion import live_provider as lp


@pytest.fixture
def db():
    import equisense.models  # noqa: F401 - registers tables
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


class _FakeFrame:
    """Minimal stand-in for a yfinance per-ticker DataFrame."""
    def __init__(self, index, cols):
        self.index = index
        self._cols = cols
        self.columns = list(cols)

    def __getitem__(self, k):
        return _FakeSeries(self.index, self._cols[k])


class _FakeSeries:
    def __init__(self, index, values):
        self.index = index
        self._v = list(values)

    def dropna(self):
        idx = [i for i, v in zip(self.index, self._v) if v is not None]
        vals = [v for v in self._v if v is not None]
        return _FakeSeries(idx, vals)

    def __len__(self):
        return len(self._v)

    def items(self):
        return zip(self.index, self._v)

    @property
    def loc(self):
        m = {i: v for i, v in zip(self.index, self._v)}
        return _Loc(m)


class _Loc:
    def __init__(self, m):
        self._m = m

    def __getitem__(self, k):
        return self._m[k]


def _synth_frame(n=300, start=100.0):
    idx = [dt.datetime(2025, 1, 1) + dt.timedelta(days=i) for i in range(n)]
    close = [start + i * 0.1 for i in range(n)]
    return _FakeFrame(idx, {
        "Close": close,
        "Adj Close": [c * 0.99 for c in close],   # total-return diverges from nominal
        "Volume": [100000 + i for i in range(n)],
        "Open": [c - 0.05 for c in close],
        "High": [c + 0.2 for c in close],
        "Low": [c - 0.2 for c in close],
    })


def test_frame_to_series_splits_the_two_price_conventions():
    s = lp._frame_to_series(_synth_frame(120))
    assert s is not None
    dates, closes, volumes, nominal, opens, highs, lows = s
    assert len(dates) == 120
    # total-return close (Adj) must differ from nominal (Close) — the distinction
    # that a naive single-series fetch would silently collapse
    assert closes[-1] != nominal[-1]
    assert abs(closes[-1] - nominal[-1] * 0.99) < 1e-6
    assert volumes[-1] is not None and highs[-1] > closes[-1] * 0  # present


def test_empty_frame_returns_none():
    assert lp._frame_to_series(_FakeFrame([], {"Close": []})) is None


def test_snapshot_builds_from_live_series_without_reading_stored_prices(db, monkeypatch):
    import equisense.models as M
    from equisense.api import snapshot as snap

    # Two index members, NO PriceObservation rows anywhere.
    for i, tk in enumerate(["AAA", "BBB"]):
        db.add(M.Company(ticker=tk, name=tk, sector="IT", cap_band="large",
                         is_index_member=True))
    db.commit()

    # The provider hands back live series; the DB has no bars to fall back on, so
    # a green build here proves the panel is not on the build path.
    series = {"AAA": lp._frame_to_series(_synth_frame(300, 100.0)),
              "BBB": lp._frame_to_series(_synth_frame(300, 250.0))}
    monkeypatch.setattr(lp, "get_universe_prices",
                        lambda tickers, **k: (series, {"provider": "yahoo_live",
                                                        "returned": len(series),
                                                        "requested": len(tickers),
                                                        "coverage_pct": 100.0}))
    monkeypatch.setattr(lp, "get_index_series", lambda *a, **k: [100.0 + i for i in range(300)])

    out = snap.build_universe_snapshot_live(db)
    assert out["data_source"]["provider"] == "yahoo_live"
    assert len(out["companies"]) == 2
    tickers = {c["ticker"] for c in out["companies"]}
    assert tickers == {"AAA", "BBB"}
    # every company priced from the live series
    assert all(c["price"] > 0 for c in out["companies"])


def test_price_on_reads_realized_price_from_the_live_cache():
    """The self-improving loop scores a claim by comparing the price when it was
    made against the price at its horizon. In live mode both come from the cached
    series, not a stored table — so price_on must find the last close on/before a
    date, exactly as the stored path did."""
    dates = [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(400)]
    closes = [100.0 + i for i in range(400)]
    lp._CACHE.update(key="x", fetched_at=1e12, status={},
                     series={"AAA": (dates, closes, [0] * 400, closes,
                                     [0] * 400, [0] * 400, [0] * 400)})
    assert lp.price_on("AAA", dt.date(2025, 3, 1)) == 159.0     # day index 59
    assert lp.price_on("AAA", dt.date(2026, 6, 1)) == 499.0     # clamps to last
    assert lp.price_on("AAA", dt.date(2024, 1, 1)) is None      # before the series
    assert lp.price_on("MISSING", dt.date(2025, 3, 1)) is None
    lp._CACHE.update(key=None, series=None, status=None, fetched_at=0.0)  # reset


def test_thin_live_fetch_refuses_to_publish(db, monkeypatch):
    import equisense.models as M
    from equisense.api import snapshot as snap
    for tk in ["AAA", "BBB", "CCC"]:
        db.add(M.Company(ticker=tk, name=tk, sector="IT", is_index_member=True))
    db.commit()
    monkeypatch.setattr(lp, "get_universe_prices",
                        lambda tickers, **k: ({}, {"returned": 0,
                                                   "requested": len(tickers),
                                                   "coverage_pct": 0.0, "errors": ["throttled"]}))
    with pytest.raises(RuntimeError):
        snap.build_universe_snapshot_live(db)


def test_full_live_money_making_path(db, monkeypatch):
    """End-to-end in the no-storage world: live snapshot → candidate screen →
    executability → paper fill, with NO stored prices anywhere. This is the whole
    product working on live free data, so if it holds together here it holds.
    """
    import equisense.models as M
    from equisense.api import snapshot as snap
    from equisense.api.candidates import qualified_candidates
    from equisense.api import paper

    monkeypatch.setenv("EQUISENSE_LIVE_DATA", "1")

    # A handful of index members, F&O-eligible names among them, no price rows.
    names = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC"]
    for tk in names:
        db.add(M.Company(ticker=tk, name=tk, sector="IT", cap_band="large",
                         is_index_member=True))
    db.commit()

    # Distinct trending series so the cross-section is not degenerate.
    def series(start, slope):
        return lp._frame_to_series(_trend_frame(400, start, slope))
    live = {tk: series(100 + i * 40, 0.1 + 0.02 * i) for i, tk in enumerate(names)}

    def fake_fetch(tickers, **k):
        got = {t: live[t] for t in tickers if t in live}
        status = {"provider": "yahoo_live", "returned": len(got),
                  "requested": len(tickers), "coverage_pct": 100.0}
        # Mirror the real provider: a successful fetch warms the in-process cache,
        # which is what latest_price (paper marks) reads.
        lp._CACHE.update(key="test", series=got, status=status, fetched_at=1e12)
        return got, status
    monkeypatch.setattr(lp, "get_universe_prices", fake_fetch)
    monkeypatch.setattr(lp, "get_index_series",
                        lambda *a, **k: [100.0 + i * 0.5 for i in range(400)])

    snap.invalidate_universe_cache()
    universe = snap.get_universe(db)               # live-mode self-refresh
    assert universe["data_source"]["provider"] == "yahoo_live"
    assert len(universe["companies"]) == len(names)

    # The candidate screen runs the full evidence→synthesis→gates pipeline off the
    # live snapshot and must return a coherent structure (not necessarily any
    # tradable name — abstention is the modal correct output).
    result = qualified_candidates(db, top_n=5)
    assert result["scanned"] == len(names)
    assert "candidates" in result and "reviewed" in result

    # A paper fill marks against the live cache (no stored bar exists).
    cid = db.query(M.Company).filter_by(ticker="RELIANCE").one().id
    fill = paper.place_trade(db, cid, "buy", 1)
    assert fill["fill_price"] > 0
    acct = paper.account(db, include_curve=False)
    assert acct["positions"] and acct["positions"][0]["price"] > 0
    snap.invalidate_universe_cache()
    lp._CACHE.update(key=None, series=None, status=None, fetched_at=0.0)


def _trend_frame(n, start, slope):
    idx = [dt.datetime(2024, 1, 1) + dt.timedelta(days=i) for i in range(n)]
    close = [start + i * slope for i in range(n)]
    return _FakeFrame(idx, {
        "Close": close, "Adj Close": [c * 0.99 for c in close],
        "Volume": [500000 + i for i in range(n)],
        "Open": [c - 0.1 for c in close], "High": [c + 0.3 for c in close],
        "Low": [c - 0.3 for c in close],
    })


def test_rebuild_falls_back_to_stored_and_reports(db, monkeypatch):
    """A failed live fetch must serve the stored panel and STAMP why, never fail
    the whole refresh or hide the degradation."""
    import equisense.models as M
    from equisense.api import snapshot as snap
    # stored data present
    cal = [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(120)]
    c = M.Company(ticker="AAA", name="AAA", sector="IT", is_index_member=True)
    db.add(c); db.flush()
    for i, d in enumerate(cal):
        db.add(M.PriceObservation(company_id=c.id, obs_date=d, close=100 + i,
                                  close_raw=100 + i, volume=100000))
    db.commit()
    monkeypatch.setattr(snap, "build_universe_snapshot_live",
                        lambda s: (_ for _ in ()).throw(RuntimeError("throttled")))
    out = snap.rebuild_universe_snapshot(db, prefer_live=True)
    assert out["data_source"]["provider"] == "stored_db_fallback"
    assert "throttled" in out["data_source"]["live_error"]
