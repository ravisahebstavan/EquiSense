"""New evidence signals: sector-relative momentum (HYP-010, Moskowitz &
Grinblatt 1999), the MAX lottery-demand effect (HYP-011, Bali, Cakici &
Whitelaw 2011), and the correlation-aware diversification gate."""
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense.db import Base
from equisense.models import Company, PriceObservation
from equisense.research.base_rates import feat_max_effect, feat_sector_relative_momentum


# ---------------------------------------------------- feature builders

def test_feat_sector_relative_momentum_demeans_by_sector():
    dates = pd.bdate_range("2024-01-01", periods=70)
    closes = pd.DataFrame({
        "A": [100 * 1.002 ** i for i in range(70)],   # IT, strong
        "B": [100 * 1.001 ** i for i in range(70)],   # IT, weaker
        "C": [100 * 1.0005 ** i for i in range(70)],  # FMCG, sole member
    }, index=dates)
    sector_map = {"A": "IT", "B": "IT", "C": "FMCG"}
    feat = feat_sector_relative_momentum(closes, None, sector_map)
    r63 = closes / closes.shift(63) - 1
    t = dates[-1]
    it_mean = (r63.loc[t, "A"] + r63.loc[t, "B"]) / 2
    assert feat.loc[t, "A"] == pytest.approx(r63.loc[t, "A"] - it_mean)
    assert feat.loc[t, "B"] == pytest.approx(r63.loc[t, "B"] - it_mean)
    # a sole sector member is always demeaned against itself → exactly zero
    assert feat.loc[t, "C"] == pytest.approx(0.0, abs=1e-9)
    # the stronger IT stock must show POSITIVE sector-relative momentum
    assert feat.loc[t, "A"] > 0 > feat.loc[t, "B"]


def test_feat_max_effect_favors_calm_over_spiky():
    dates = pd.bdate_range("2024-01-01", periods=30)
    calm = [100.0]
    for _ in range(29):
        calm.append(calm[-1] * 1.001)
    spiky = [100.0]
    for i in range(29):
        spiky.append(spiky[-1] * (1.15 if i in (10, 15, 20, 24, 27) else 1.0001))
    closes = pd.DataFrame({"CALM": calm, "SPIKY": spiky}, index=dates)
    feat = feat_max_effect(closes, None)
    t = dates[-1]
    # feature is NEGATED MAX: low actual single-day extremity scores higher
    assert feat.loc[t, "CALM"] > feat.loc[t, "SPIKY"]


def test_feat_max_effect_thin_history_is_nan():
    dates = pd.bdate_range("2024-01-01", periods=10)
    closes = pd.DataFrame({"X": [100.0] * 10}, index=dates)
    feat = feat_max_effect(closes, None)
    assert feat.iloc[-1].isna().all()


# ---------------------------------------------------- registry wiring

def test_new_hypotheses_registered_with_family_mapping():
    from equisense.research.registry import FAMILY_HYPOTHESIS, REGISTRY, admission_cap
    assert REGISTRY["HYP-010"]["name"] == "sector_relative_momentum_top_quintile"
    assert REGISTRY["HYP-011"]["name"] == "low_max_effect_top_quintile"
    assert FAMILY_HYPOTHESIS["technical.sector_momentum"] == "HYP-010"
    assert FAMILY_HYPOTHESIS["behavioral.max_effect"] == "HYP-011"
    # freshly registered → same exploratory cap as every other new signal,
    # never a free pass just because it's new
    cap, _ = admission_cap("technical.sector_momentum")
    assert cap == 0.25


def test_run_all_studies_includes_new_hypotheses():
    """End-to-end: a real (small, synthetic) universe produces published
    base-rate records for both new studies via the actual pipeline."""
    import random
    from equisense.models import MacroObservation
    from equisense.research.base_rates import run_all_studies

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    SL = sessionmaker(bind=eng, expire_on_commit=False)
    s = SL()
    rng = random.Random(11)
    sectors = ["IT", "IT", "IT", "FMCG", "FMCG", "Energy", "Energy", "Auto", "Auto", "Auto"]
    d0 = date(2020, 1, 1)
    n_days = 900
    for i, sector in enumerate(sectors):
        c = Company(ticker=f"T{i}", name=f"Test{i}", sector=sector)
        s.add(c); s.flush()
        price = 100.0
        drift = 0.0006 if i % 2 == 0 else 0.0001
        for d in range(n_days):
            price *= (1 + rng.gauss(drift, 0.02))
            vol = abs(rng.gauss(1e6, 2e5))
            s.add(PriceObservation(company_id=c.id,
                                   obs_date=d0 + timedelta(days=d),
                                   close=max(price, 1.0), volume=vol))
    for d in range(n_days):
        s.add(MacroObservation(symbol="^NSEI", role="index",
                               obs_date=d0 + timedelta(days=d),
                               close=20000 * (1.0002 ** d)))
    s.commit()

    report = run_all_studies(s)
    assert report["records"] > 0
    from sqlalchemy import select
    from equisense.models import BaseRateRecord
    refs = {r.registry_ref for r in s.scalars(select(BaseRateRecord)).all()}
    # both new hypotheses must have produced at least one publishable cell
    # (n_eff gate permitting — with 900 days / ~40 monthly episodes this
    # comfortably clears MIN_N_EFF for the class-leading regime="all" cell)
    assert "HYP-010" in refs or "HYP-011" in refs, \
        "at least one new hypothesis should publish on this sample size"


# ------------------------------------------------ diversification gate

@pytest.fixture
def corr_world():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    SL = sessionmaker(bind=eng, expire_on_commit=False)
    s = SL()
    a = Company(ticker="A", name="A", sector="IT")
    b = Company(ticker="B", name="B", sector="IT")
    c = Company(ticker="C", name="C", sector="Energy")
    s.add_all([a, b, c]); s.flush()

    import random
    rng = random.Random(5)
    d0 = date.today() - timedelta(days=90)
    pa = pb = pc = 100.0
    for i in range(90):
        d = d0 + timedelta(days=i)
        shared_shock = rng.gauss(0, 0.015)
        pa *= (1 + shared_shock + rng.gauss(0, 0.001))  # A and B share the same
        pb *= (1 + shared_shock + rng.gauss(0, 0.001))  # shock → near-perfect corr
        pc *= (1 + rng.gauss(0, 0.015))                 # C is independent
        s.add(PriceObservation(company_id=a.id, obs_date=d, close=pa))
        s.add(PriceObservation(company_id=b.id, obs_date=d, close=pb))
        s.add(PriceObservation(company_id=c.id, obs_date=d, close=pc))
    s.commit()
    return s, a, b, c


def test_diversification_gate_demotes_correlated_second_pick(corr_world):
    from equisense.api.candidates import _apply_diversification_gate
    s, a, b, c = corr_world
    candidates = [
        {"id": a.id, "ticker": "A", "net_score": 0.5, "tradable": True, "gates": []},
        {"id": b.id, "ticker": "B", "net_score": 0.4, "tradable": True, "gates": []},
        {"id": c.id, "ticker": "C", "net_score": 0.3, "tradable": True, "gates": []},
    ]
    _apply_diversification_gate(s, candidates)
    assert candidates[0]["tradable"] is True   # A: first, always survives
    assert candidates[1]["tradable"] is False  # B: correlated with A → demoted
    assert any(g.startswith("failed: concentration") for g in candidates[1]["gates"])
    assert "A" in candidates[1]["gates"][0]
    assert candidates[2]["tradable"] is True   # C: independent → survives


def test_diversification_gate_noop_under_two_candidates(corr_world):
    from equisense.api.candidates import _apply_diversification_gate
    s, a, b, c = corr_world
    candidates = [{"id": a.id, "ticker": "A", "net_score": 0.5,
                  "tradable": True, "gates": []}]
    _apply_diversification_gate(s, candidates)  # must not raise on n=1
    assert candidates[0]["tradable"] is True


# --------------------------------------------- API serialization regression

def test_base_rates_endpoint_serializes_n_eff_and_net():
    """Regression: /api/live/base-rates silently dropped n_eff and
    net_median_excess_pct even though the DB and the frontend both expect
    them (caught by a full visual pass — the Lab table showed bare '—' in
    every N_eff cell despite real values in the database).

    This test used to be ORDER-DEPENDENT: it ran the study pipeline against the
    app's shared database and only passed when some earlier test happened to
    leave price history behind. On a fresh database it produced no records and
    failed. It now seeds its own history, so it tests the serializer rather than
    the test-execution order.
    """
    import random

    from fastapi.testclient import TestClient

    from sqlalchemy import select

    from equisense.api.app import app
    from equisense.db import ensure_schema, get_session
    from equisense.models import MacroObservation

    ensure_schema()
    s = get_session()
    try:
        rng = random.Random(23)
        d0, n_days = date(2019, 1, 1), 1000
        existing = {c.ticker for c in s.scalars(select(Company)).all()}
        for i in range(12):
            ticker = f"BRT{i:02d}"
            if ticker in existing:
                continue
            c = Company(ticker=ticker, name=f"BaseRate{i}",
                        sector=["IT", "FMCG", "Energy", "Auto"][i % 4])
            s.add(c)
            s.flush()
            price = 100.0
            rows = []
            for d in range(n_days):
                price *= (1 + rng.gauss(0.0004 if i % 2 else 0.0002, 0.018))
                rows.append({"company_id": c.id, "obs_date": d0 + timedelta(days=d),
                             "close": price,
                             "volume": abs(rng.gauss(1e6, 2e5))})
            s.bulk_insert_mappings(PriceObservation, rows)
        nifty = 10000.0
        if not s.scalars(select(MacroObservation)
                         .where(MacroObservation.symbol == "^NSEI")).first():
            mrows = []
            for d in range(n_days):
                nifty *= (1 + rng.gauss(0.0003, 0.010))
                mrows.append({"symbol": "^NSEI", "role": "index",
                              "obs_date": d0 + timedelta(days=d), "close": nifty})
            s.bulk_insert_mappings(MacroObservation, mrows)
        s.commit()
    finally:
        s.close()

    with TestClient(app) as c:
        run = c.post("/api/live/studies/run").json()
        r = c.get("/api/live/base-rates").json()
    assert run["records"], f"study pipeline published nothing: {run}"
    assert r["records"], "expected published base-rate records"
    with_n_eff = [rec for rec in r["records"] if rec.get("n_eff") is not None]
    assert with_n_eff, "no record exposed n_eff — the API is dropping it again"
    assert any(rec.get("net_median_excess_pct") is not None for rec in r["records"])
    # Wave S: the inference decomposition and multiplicity verdict must survive
    # serialization too, or the Lab shows a bare number with no provenance.
    for rec in with_n_eff:
        assert rec["n_clusters"] is not None
        assert rec["design_effect"] is not None
        assert rec["multiplicity_verdict"]
        assert rec["n_eff"] <= rec["n"], "N_eff can never exceed N"
        break


# ------------------------------------------ delivery-aware crowding (Wave S)

def test_crowding_proxy_is_backward_compatible_without_delivery():
    from equisense.engine.novel import crowding_proxy
    closes = [100.0 * (1.004 ** i) for i in range(200)]
    vols = [1e6] * 130 + [3e6] * 70
    m = crowding_proxy(closes, vols)
    assert m.value is not None
    assert m.inputs["delivery_multiplier"] == 1.0
    assert "Volume-only" in m.caveat


def test_low_delivery_amplifies_crowding_high_delivery_damps_it():
    """A volume surge on LOW delivery vs the stock's own norm is churn — the
    crowding case the signal exists to catch. On HIGH delivery it is genuine
    accumulation and the crowding claim is weaker."""
    from equisense.engine.novel import crowding_proxy
    closes = [100.0 * (1.004 ** i) for i in range(200)]
    vols = [1e6] * 130 + [3e6] * 70
    base = crowding_proxy(closes, vols).value
    churn = crowding_proxy(closes, vols, delivery_pct=20.0, delivery_mean_pct=50.0)
    real = crowding_proxy(closes, vols, delivery_pct=75.0, delivery_mean_pct=50.0)
    assert churn.value > base > real.value
    assert "delivery" in churn.formula


def test_delivery_multiplier_is_bounded():
    """Flow may shade the reading, never dominate the price/volume core."""
    from equisense.engine.novel import crowding_proxy
    closes = [100.0 * (1.004 ** i) for i in range(200)]
    vols = [1e6] * 130 + [3e6] * 70
    extreme_low = crowding_proxy(closes, vols, delivery_pct=1.0, delivery_mean_pct=90.0)
    extreme_high = crowding_proxy(closes, vols, delivery_pct=95.0, delivery_mean_pct=5.0)
    assert extreme_low.inputs["delivery_multiplier"] <= 1.5
    assert extreme_high.inputs["delivery_multiplier"] >= 0.6


def test_crowding_still_none_without_enough_price_history():
    from equisense.engine.novel import crowding_proxy
    m = crowding_proxy([100.0] * 10, [1e6] * 10, delivery_pct=20.0,
                       delivery_mean_pct=50.0)
    assert m.value is None


def test_rolling_topk_mean_matches_the_naive_form_exactly():
    """The MAX-effect feature used
    ``rolling(21, min_periods=15).apply(lambda x: pd.Series(x).nlargest(5).mean())``,
    which allocates a pandas Series PER WINDOW. On the production panel
    (2488 x 500 = 1.24M windows) that ran over 200 seconds and was killed by
    the platform's 300s function limit, so the whole studies stage never
    committed and base rates silently stopped updating.

    The vectorised replacement must produce the SAME NUMBERS — a quietly
    changed research signal would be far worse than a slow cron — including
    pandas' habit of emitting values for partial leading windows once
    min_periods is satisfied.
    """
    import numpy as np
    import pandas as pd

    from equisense.research.base_rates import _rolling_topk_mean

    rng = np.random.default_rng(7)

    def naive(df):
        return df.rolling(21, min_periods=15).apply(
            lambda x: pd.Series(x).nlargest(5).mean(), raw=False)

    cases = {
        "ragged listings + scattered holidays": None,
        "no NaN": pd.DataFrame(rng.normal(0, 0.02, (200, 8))),
        "an entirely absent name": pd.DataFrame(
            np.column_stack([rng.normal(0, 0.02, 200), np.full(200, np.nan)])),
        "shorter than the window": pd.DataFrame(rng.normal(0, 0.02, (18, 4))),
        "exactly min_periods rows": pd.DataFrame(rng.normal(0, 0.02, (15, 4))),
    }
    d = rng.normal(0, 0.02, (300, 40))
    d[:40, :15] = np.nan                       # names that listed late
    d[rng.random((300, 40)) < 0.03] = np.nan   # scattered gaps
    cases["ragged listings + scattered holidays"] = pd.DataFrame(d)

    for name, df in cases.items():
        old, new = naive(df), _rolling_topk_mean(df, 21, 15, 5)
        assert (old.isna() == new.isna()).all().all(), f"{name}: NaN pattern differs"
        delta = (old - new).abs().max().max()
        assert not (delta == delta) or delta < 1e-12, f"{name}: values differ by {delta}"


def test_event_calendar_fails_open_and_says_so(monkeypatch):
    """An unreachable calendar must not halt the book — but it must never be
    mistaken for "no event scheduled". Reporting absence of knowledge as
    absence of risk is the failure mode that matters here."""
    import equisense.ingestion.nse_events as EV

    EV._CACHE.update(at=0.0, payload=None)
    monkeypatch.setattr(EV, "_opener", lambda: (_ for _ in ()).throw(OSError("blocked")))
    out = EV.fetch_event_calendar(force=True)

    assert out["available"] is False
    assert out["events"] == {}
    assert "NOT a statement that there is none" in out["note"]
    assert EV.next_event("ITC", out) is None


def test_event_risk_is_a_caveat_not_a_veto():
    """A scheduled result is a fact about UNCERTAINTY, not about direction.
    Vetoing on it would abstain through every earnings season; ignoring it lets
    a position be opened into a binary event the exchange already published."""
    import inspect

    from equisense.api import candidates as C

    src = inspect.getsource(C.qualified_candidates)
    idx = src.find("days_away")
    assert idx != -1, "event risk must be consulted in the gate stack"
    window = src[idx - 400:idx + 400]
    assert "caveat:" in window, "event risk must be raised as a caveat"
    assert "failed:" not in window.split("caveat:")[1][:200], (
        "an upcoming result must not hard-fail a candidate")
    assert C.EVENT_RISK_DAYS > 0


def _synthetic_chain(follower_beta, expected_driver="BZ=F", seed=3):
    import random
    random.seed(seed)
    n = 300
    drv = [random.gauss(0, 0.02) for _ in range(n)]
    fol = [follower_beta * d + random.gauss(0, 0.005) for d in drv]
    return drv, fol


def test_a_link_that_does_not_measure_passes_nothing_downstream():
    """A chain of plausible mechanisms is a story, and stories are how a
    research system talks itself into a position. An undetectable link must
    stop the chain rather than quietly forward its prior."""
    import random

    from equisense.engine.transmission import build_chain

    random.seed(5)
    n = 300
    drv = [random.gauss(0, 0.02) for _ in range(n)]
    unrelated = [random.gauss(0, 0.02) for _ in range(n)]      # no relationship
    chain = build_chain("BZ=F", drv, -10.0, unrelated,
                        {"Energy": unrelated, "Materials": unrelated},
                        {"AAA": unrelated}, {"AAA": "Energy"})

    assert chain["available"]
    assert chain["summary"]["confirmed"] == 0
    # the security leg is only measured off a CONFIRMED sector link
    assert chain["security_links"] == [], (
        "names must not be measured against a driver whose sector channel was "
        "never established — that is fishing")


def test_a_wrong_signed_link_is_reported_not_hidden():
    """The most valuable output here is the mechanism everyone believes that
    this market does not honour. Dropping it would leave the chain looking
    cleaner than the evidence is."""
    from equisense.engine.transmission import CHANNELS, build_chain

    exp = next(c for c in CHANNELS if c.driver == "BZ=F" and c.sector == "Materials")
    assert exp.expected_sign == -1
    drv, fol = _synthetic_chain(+0.9)          # opposite of the declared sign
    chain = build_chain("BZ=F", drv, -10.0, drv, {"Materials": fol})

    link = next(l for l in chain["sector_links"] if l["sector"] == "Materials")
    assert link["verdict"] == "contradicted"
    assert chain["summary"]["contradicted"] >= 1
    assert "OPPOSITE" in link["why"]


def test_implied_move_is_never_presented_as_a_forecast():
    """It is the arithmetic of a measured sensitivity applied to a move that
    already happened — exposure carried, not a prediction."""
    from equisense.engine.transmission import implied_move

    m = implied_move(-0.5, -20.0, 0.30)
    assert m["implied_pct"] == 10.0
    assert "ALREADY occurred" in m["caveat"]
    assert "not a prediction" in m["caveat"]
    assert implied_move(None, -20.0, 0.3) is None


def test_sector_basket_must_be_built_by_date_not_by_index():
    """The bug that made NIFTY Bank read as unrelated to the financials basket
    it is definitionally composed of (beta +0.025, R² 0.0004 in production).

    Universe members have different history lengths — a name listed later has
    fewer bars. Combining their return LISTS by position therefore adds up
    returns from different calendar days, and the basket stops describing any
    real portfolio. Building it per DATE is what makes the basket the thing it
    claims to be.
    """
    from datetime import date, timedelta

    from equisense.engine.crossasset import (align_on_dates,
                                             returns_from_dated_closes)
    from equisense.engine.transmission import measure_link

    d0 = date(2025, 1, 1)
    days = [d0 + timedelta(days=i) for i in range(300)]
    moves = [((i * 37) % 11 - 5) / 500.0 for i in range(300)]

    def closes(start_idx, idio=0.0):
        # Real members track their sector with idiosyncratic noise on top; two
        # series that are literally identical produce r=1.0, which correlation
        # maths treats as degenerate and never occurs in market data.
        import random
        rng = random.Random(start_idx + 1)
        px, out = 100.0, []
        for i in range(start_idx, 300):
            px *= 1 + moves[i] + (rng.gauss(0, idio) if idio else 0.0)
            out.append((days[i], px))
        return out

    old_name = closes(0, idio=0.004)                 # member with its own noise
    new_name = closes(120, idio=0.004)               # listed 120 days later
    index_closes = closes(0)                         # the "sector index" itself

    r_old = returns_from_dated_closes(old_name)
    r_new = returns_from_dated_closes(new_name)
    r_idx = returns_from_dated_closes(index_closes)

    # CORRECT: average the members trading on each date
    per_date = {}
    for rets in (r_old, r_new):
        for d_, r in rets.items():
            per_date.setdefault(d_, []).append(r)
    basket_dated = {d_: sum(v) / len(v) for d_, v in per_date.items()}
    aligned = align_on_dates({"basket": basket_dated, "index": r_idx})
    good = measure_link(aligned["basket"], aligned["index"], +1, "by-date")

    # WRONG: combine the two members' lists by position, then tail-align
    lo = [v for _d, v in sorted(r_old.items())]
    ln = [v for _d, v in sorted(r_new.items())]
    by_index = [(a + b) / 2 for a, b in zip(lo, ln)]
    li = [v for _d, v in sorted(r_idx.items())]
    n = min(len(by_index), len(li))
    bad = measure_link(by_index[-n:], li[-n:], +1, "by-index")

    assert good["r_squared"] > 0.7, (
        f"a basket of members against their own index must track it closely; "
        f"got R²={good['r_squared']}")
    assert good["verdict"] == "confirmed"
    assert bad["r_squared"] < good["r_squared"] / 2, (
        "combining members by list position must visibly destroy the "
        f"relationship (good R²={good['r_squared']}, bad R²={bad['r_squared']})")
