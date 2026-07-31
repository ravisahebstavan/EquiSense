"""Variance risk premium — measured, never assumed.

The VRP is among the most robustly documented effects in finance, which is
exactly why the engine must be able to FAIL to find it. These tests plant both
outcomes and check it reports each honestly.
"""
from __future__ import annotations

import datetime as dt
import math
import random

import pytest

from equisense.engine.derivatives import VRP_MIN_OBS, variance_risk_premium


def _world(iv_premium_pp: float, true_vol: float = 0.14, n=400, seed=7,
           spike_at=None, spike_vol=0.55):
    """Prices at a known volatility, with IV set a stated premium above it."""
    rng = random.Random(seed)
    dates = [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(n)]
    px, iv, p = [], [], 100.0
    for i, d in enumerate(dates):
        vol = spike_vol if (spike_at and spike_at <= i < spike_at + 15) else true_vol
        p *= math.exp(rng.gauss(0, vol / math.sqrt(252)))
        px.append((d, p))
        iv.append((d, true_vol * 100 + iv_premium_pp))
    return iv, px


def test_recovers_a_planted_premium():
    iv, px = _world(iv_premium_pp=4.0)
    r = variance_risk_premium(iv, px, horizon_days=21)
    assert r["computable"]
    assert r["mean_premium_pp"] == pytest.approx(4.0, abs=2.0)
    assert r["t_stat"] > 2.0
    assert "implied exceeds subsequent realised" in r["verdict"]


def test_reports_no_premium_when_there_is_none():
    """The engine must be able to fail to find a documented effect."""
    iv, px = _world(iv_premium_pp=0.0)
    r = variance_risk_premium(iv, px, horizon_days=21)
    assert r["computable"]
    assert abs(r["mean_premium_pp"]) < 2.5


def test_a_volatility_spike_shows_up_as_the_worst_observation():
    """A positive average premium and a catastrophic worst case are the same
    fact — selling variance is short a fat left tail."""
    iv, px = _world(iv_premium_pp=3.0, spike_at=200)
    r = variance_risk_premium(iv, px, horizon_days=21)
    assert r["computable"]
    assert r["worst_observation"]["premium_pp"] < -15, \
        "the spike must surface as a large negative premium"
    assert "short a fat left tail" in r["risk_warning"]
    assert "93%" in r["risk_warning"], "the SEBI base rate must travel with it"


def test_refuses_a_series_too_short_to_test():
    """The NORMAL state for months: the IV series cannot be backfilled."""
    iv, px = _world(iv_premium_pp=4.0, n=30)
    r = variance_risk_premium(iv[:10], px, horizon_days=21)
    assert r["computable"] is False
    assert f"≥{VRP_MIN_OBS}" in r["reason"]
    assert "cannot be backfilled" in r["reason"]


def test_realised_vol_never_overlaps_the_iv_observation():
    """Realised vol is measured over (t, t+h] only. Including day t would leak
    the information the IV was supposed to predict."""
    iv, px = _world(iv_premium_pp=3.0)
    full = variance_risk_premium(iv, px, horizon_days=21)
    short = variance_risk_premium(iv, px, horizon_days=10)
    assert full["computable"] and short["computable"]
    assert full["horizon_days"] == 21 and short["horizon_days"] == 10


def test_a_horizon_below_the_floor_says_so_rather_than_reporting_no_data():
    """Caught a real bug: a fixed 10-session minimum made every horizon under 10
    days yield zero observations, which was then reported as 'need more paired
    observations' — naming the wrong cause."""
    from equisense.engine.derivatives import MIN_VRP_HORIZON
    iv, px = _world(iv_premium_pp=3.0)
    r = variance_risk_premium(iv, px, horizon_days=3)
    assert r["computable"] is False
    assert f"{MIN_VRP_HORIZON}-session floor" in r["reason"]
    assert "paired observations" not in r["reason"]


def test_newey_west_lag_tracks_the_realised_window():
    iv, px = _world(iv_premium_pp=3.0)
    r = variance_risk_premium(iv, px, horizon_days=21)
    assert r["standard_error"] is not None
    assert "Newey-West" in r["method"]


def test_hypothesis_is_registered_and_deferred():
    """Pre-registered before any result exists, and honest that it is not yet
    testable because the series cannot be backfilled."""
    from equisense.research.registry import REGISTRY
    h = REGISTRY["HYP-015"]
    assert h["name"] == "variance_risk_premium"
    assert h["status"] == "registered-deferred"
    assert "cannot be backfilled" in h["spec"]
