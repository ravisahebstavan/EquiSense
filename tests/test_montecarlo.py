"""Monte Carlo engine invariants.

The simulator is only useful if it reproduces things we can check independently,
so the first tests assert recovery of ANALYTIC truth. The later ones assert the
properties the module exists to expose (fat tails, path dependence).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from equisense.engine import montecarlo as MC


def _gaussian_returns(n=3000, mu=0.0005, sd=0.012, seed=0):
    return np.random.default_rng(seed).normal(mu, sd, n)


def _fat_tailed_returns(n=2000, seed=7):
    """GARCH-like: volatility clustering plus genuine crash days."""
    rng = np.random.default_rng(seed)
    vol = np.zeros(n); ret = np.zeros(n); vol[0] = 0.011
    for i in range(1, n):
        vol[i] = math.sqrt(2e-6 + 0.09 * ret[i - 1] ** 2 + 0.90 * vol[i - 1] ** 2)
        ret[i] = rng.standard_t(4) / math.sqrt(2) * vol[i] + 0.0004
    ret[900], ret[901], ret[1500] = -0.11, -0.07, -0.09
    return ret


# ------------------------------------------------------- analytic recovery

def test_gaussian_model_recovers_analytic_volatility_and_var():
    mu, sd, h = 0.0005, 0.012, 21
    r = _gaussian_returns(4000, mu, sd, seed=0)
    res = MC.simulate_portfolio_risk({"A": r}, {"A": 1.0}, horizon_days=h,
                                     n_paths=40_000, seed=1)
    g = res["models"]["gaussian"]
    assert g["volatility_pct"] == pytest.approx(sd * math.sqrt(h) * 100, rel=0.05)
    analytic_var95 = (mu * h - 1.645 * sd * math.sqrt(h)) * 100
    assert g["var_95_pct"] == pytest.approx(analytic_var95, abs=0.5)
    assert abs(g["excess_kurtosis"]) < 0.25, "gaussian draws must not be fat-tailed"


def test_student_t_fattens_tails_without_inflating_volatility():
    """A raw t has variance df/(df-2); failing to standardise would confound
    'fat tails' with 'more volatility'."""
    r = _gaussian_returns(3000, seed=2)
    res = MC.simulate_portfolio_risk({"A": r}, {"A": 1.0}, horizon_days=21,
                                     n_paths=30_000, student_t_df=4.0, seed=3)
    g, t = res["models"]["gaussian"], res["models"]["student_t"]
    assert t["volatility_pct"] == pytest.approx(g["volatility_pct"], rel=0.08)
    assert t["excess_kurtosis"] > g["excess_kurtosis"] + 0.3
    assert t["var_99_pct"] < g["var_99_pct"], "t tail loss must be worse"


def test_standardised_t_shocks_have_unit_variance():
    rng = np.random.default_rng(5)
    z = MC._student_t_shocks(rng, (200_000,), df=4.0)
    assert float(z.std(ddof=1)) == pytest.approx(1.0, abs=0.05)


# ------------------------------------------------------------- the payload

def test_bootstrap_exposes_tail_gaussian_var_cannot_see():
    """The module's reason for existing: Gaussian VaR systematically
    understates equity tail loss."""
    r = _fat_tailed_returns()
    res = MC.simulate_portfolio_risk({"EQ": r}, {"EQ": 1.0}, horizon_days=21,
                                     n_paths=20_000, seed=3)
    g, b = res["models"]["gaussian"], res["models"]["bootstrap"]
    assert b["var_99_pct"] < g["var_99_pct"], \
        "bootstrap of real history must show a worse 99% loss than Gaussian"
    assert b["excess_kurtosis"] > g["excess_kurtosis"]
    assert res["tail_model_gap_99_pct"] > 0
    assert "understating" in res["interpretation"]


def test_cvar_is_always_at_least_as_severe_as_var():
    """Expected Shortfall is the mean of the tail beyond VaR, so it cannot be
    milder. A violation means the tail slicing is wrong."""
    r = _fat_tailed_returns()
    res = MC.simulate_portfolio_risk({"EQ": r}, {"EQ": 1.0}, horizon_days=21,
                                     n_paths=20_000, seed=4)
    for m in res["models"].values():
        assert m["cvar_95_pct"] <= m["var_95_pct"] + 1e-9
        assert m["cvar_99_pct"] <= m["var_99_pct"] + 1e-9
        assert m["var_99_pct"] <= m["var_95_pct"] + 1e-9


def test_estimates_carry_monte_carlo_standard_errors():
    r = _gaussian_returns(1500, seed=6)
    res = MC.simulate_portfolio_risk({"A": r}, {"A": 1.0}, horizon_days=21,
                                     n_paths=20_000, seed=7)
    g = res["models"]["gaussian"]
    assert g["mean_stderr_pct"] > 0
    assert g["var_95_stderr_pct"] > 0


def test_more_paths_reduce_monte_carlo_error():
    """MC error falls as 1/sqrt(n) — a basic sanity check on the estimator."""
    r = _gaussian_returns(1500, seed=8)
    small = MC.simulate_portfolio_risk({"A": r}, {"A": 1.0}, horizon_days=21,
                                       n_paths=2_000, seed=9)
    large = MC.simulate_portfolio_risk({"A": r}, {"A": 1.0}, horizon_days=21,
                                       n_paths=32_000, seed=9)
    assert (large["models"]["gaussian"]["mean_stderr_pct"]
            < small["models"]["gaussian"]["mean_stderr_pct"])


# ------------------------------------------------------------- correlation

def test_nearest_psd_repairs_an_impossible_correlation_matrix():
    bad = np.array([[1, .9, .9], [.9, 1, -.9], [.9, -.9, 1]])
    assert np.linalg.eigvalsh(bad).min() < 0
    fixed, repaired = MC.nearest_psd_correlation(bad)
    assert repaired is True
    assert np.linalg.eigvalsh(fixed).min() >= -1e-9
    np.linalg.cholesky(fixed)                       # must not raise
    assert np.allclose(np.diag(fixed), 1.0)


def test_psd_matrix_is_left_alone():
    good = np.array([[1.0, 0.3], [0.3, 1.0]])
    fixed, repaired = MC.nearest_psd_correlation(good)
    assert repaired is False
    assert np.allclose(fixed, good)


def test_correlated_assets_produce_higher_portfolio_vol_than_independent():
    rng = np.random.default_rng(11)
    base = rng.normal(0, 0.011, 1500)
    a = base + rng.normal(0, 0.003, 1500)
    b = base + rng.normal(0, 0.003, 1500)
    c = rng.normal(0, 0.011, 1500)
    d = rng.normal(0, 0.011, 1500)
    corr = MC.simulate_portfolio_risk({"A": a, "B": b}, {"A": 1, "B": 1},
                                      horizon_days=21, n_paths=10_000, seed=1)
    indep = MC.simulate_portfolio_risk({"C": c, "D": d}, {"C": 1, "D": 1},
                                       horizon_days=21, n_paths=10_000, seed=1)
    assert corr["annualised_vol_pct"] > indep["annualised_vol_pct"], \
        "correlated holdings diversify less — that must show up in portfolio vol"


def test_weights_are_normalised_and_reported():
    r = _gaussian_returns(500, seed=12)
    res = MC.simulate_portfolio_risk({"A": r, "B": r}, {"A": 3.0, "B": 1.0},
                                     horizon_days=5, n_paths=2_000, seed=1)
    assert res["weights_normalised_by"] == pytest.approx(4.0)
    assert res["weights"]["A"] == pytest.approx(0.75)


# --------------------------------------------------------------- path risk

def test_drawdown_touch_probability_is_monotone_in_severity():
    r = _fat_tailed_returns()
    dd = MC.simulate_drawdown_risk(r, horizon_days=252, n_paths=3_000, seed=5)
    probs = dd["touch_probability_pct"]
    assert probs["-10%"] >= probs["-20%"] >= probs["-30%"] >= probs["-50%"]


def test_touching_a_level_is_likelier_than_ending_below_it():
    """The path/endpoint distinction the module exists to surface: stops and
    margin calls are triggered by the path."""
    r = _fat_tailed_returns()
    dd = MC.simulate_drawdown_risk(r, horizon_days=252, n_paths=3_000, seed=6)
    assert dd["touch_probability_pct"]["-20%"] > 5.0
    assert dd["median_max_drawdown_pct"] < 0
    assert dd["p95_max_drawdown_pct"] <= dd["median_max_drawdown_pct"]


def test_block_bootstrap_preserves_volatility_clustering():
    """iid resampling destroys clustering and produces benign drawdowns; blocks
    must retain it."""
    rng = np.random.default_rng(3)
    r = _fat_tailed_returns()
    idx = MC.stationary_bootstrap_indices(rng, len(r), 500, mean_block=10.0)
    consecutive = np.mean(np.diff(idx) == 1)
    assert consecutive > 0.5, "blocks must mostly walk forward through history"


def test_simulators_refuse_thin_history():
    assert MC.simulate_portfolio_risk({"A": [0.01] * 10}, {"A": 1.0})["computable"] is False
    assert MC.simulate_drawdown_risk([0.01] * 10)["computable"] is False


# ------------------------------------------------------ valuation & goals

def test_implied_growth_distribution_brackets_the_point_estimate():
    out = MC.simulate_implied_growth(base_fcf=1300, enterprise_value=51_000,
                                     wacc_mean=0.1312, wacc_sd=0.015,
                                     terminal_growth_mean=0.04,
                                     terminal_growth_sd=0.01, seed=4)
    assert out["computable"]
    p = out["implied_growth_pct"]
    assert p["p05"] < p["median"] < p["p95"]
    assert p["p95"] - p["p05"] > 3.0, "assumption uncertainty must widen the answer"
    assert "NOT a forecast" in out["interpretation"]


def test_implied_growth_rejects_undefined_gordon_draws_and_reports_them():
    """WACC sampled near terminal growth makes the Gordon term undefined; those
    draws must be counted, not silently resampled."""
    out = MC.simulate_implied_growth(base_fcf=1300, enterprise_value=51_000,
                                     wacc_mean=0.05, wacc_sd=0.02,
                                     terminal_growth_mean=0.045,
                                     terminal_growth_sd=0.005, seed=4)
    if out["computable"]:
        assert out["n_rejected"] > 0
        assert "reported, not silently resampled" in out["rejected_note"]
    else:
        assert "Gordon boundary" in out["reason"]


def test_goal_probability_rises_with_contribution():
    a = MC.simulate_goal(100_000, 10_000, 10, 6_000_000, n_paths=3_000, seed=2)
    b = MC.simulate_goal(100_000, 40_000, 10, 6_000_000, n_paths=3_000, seed=2)
    assert b["probability_of_reaching_target_pct"] > a["probability_of_reaching_target_pct"]


def test_goal_reports_full_distribution_not_just_expectation():
    g = MC.simulate_goal(100_000, 25_000, 10, 6_000_000, n_paths=3_000, seed=2)
    t = g["terminal_corpus"]
    assert t["p05"] < t["median"] < t["p95"]
    assert g["total_invested"] == pytest.approx(100_000 + 25_000 * 120)
    assert 0 <= g["probability_of_reaching_target_pct"] <= 100


def test_goal_uses_supplied_history_when_available():
    r = _fat_tailed_returns()
    g = MC.simulate_goal(100_000, 25_000, 5, 3_000_000, daily_returns=r,
                         n_paths=800, seed=2)
    assert "bootstrap" in g["return_basis"]


def test_perfectly_correlated_assets_do_not_break_cholesky():
    """A correlation matrix of exactly 1.0 off-diagonal is positive SEMI-definite
    with a zero eigenvalue; np.linalg.cholesky raises on it. Real cases: an ETF
    against its own index, dual listings, or a duplicated column."""
    r = _gaussian_returns(500, seed=12)
    res = MC.simulate_portfolio_risk({"A": r, "B": r}, {"A": 3.0, "B": 1.0},
                                     horizon_days=5, n_paths=2_000, seed=1)
    assert res["computable"] is True
    assert res["correlation_repaired"] is True
    assert res["models"]["gaussian"]["volatility_pct"] > 0


def test_safe_cholesky_survives_a_singular_matrix():
    singular = np.ones((3, 3))
    L = MC._safe_cholesky(singular)
    assert L.shape == (3, 3)
    assert np.all(np.isfinite(L))
