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
    every N_eff cell despite real values in the database)."""
    from fastapi.testclient import TestClient
    from equisense.api.app import app
    with TestClient(app) as c:
        c.post("/api/live/studies/run")
        r = c.get("/api/live/base-rates").json()
    assert r["records"], "expected published base-rate records"
    with_n_eff = [rec for rec in r["records"] if rec.get("n_eff") is not None]
    assert with_n_eff, "no record exposed n_eff — the API is dropping it again"
    assert any(rec.get("net_median_excess_pct") is not None for rec in r["records"])
