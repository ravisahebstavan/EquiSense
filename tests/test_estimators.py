"""Tests for the covariance and Sharpe estimators added to research/stats.py.

Each is checked against a property that is TRUE BY CONSTRUCTION rather than
against a number copied from a previous run, so a regression in the estimator
cannot be absorbed by updating a fixture.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from equisense.research.stats import (ledoit_wolf_covariance,
                                      lo_annualized_sharpe,
                                      probabilistic_sharpe_ratio,
                                      shrunk_correlation)


def _frob(a, b):
    return float(np.linalg.norm(a - b, "fro") / np.linalg.norm(b, "fro"))


def test_shrinkage_beats_the_sample_covariance_when_assets_rival_observations():
    """The regime that matters: the sample estimator's largest eigenvalues are
    biased up and its smallest biased down, and the small ones are what the
    Cholesky factorisation rests on."""
    rng = np.random.default_rng(0)
    n, t = 40, 60
    a = rng.standard_normal((n, n))
    true = a @ a.T / n + np.eye(n) * 0.5
    x = rng.multivariate_normal(np.zeros(n), true, size=t)

    sample = np.cov(x, rowvar=False)
    lw = ledoit_wolf_covariance(x)
    assert lw["computable"]
    assert _frob(lw["covariance"], true) < _frob(sample, true), (
        "shrinkage must reduce estimation error in the N-near-T regime")
    assert 0.0 < lw["shrinkage"] < 1.0


def test_shrinkage_is_always_positive_definite():
    """This is what removes the reason the eigenvalue repair exists: a singular
    input still yields a factorisable matrix."""
    rng = np.random.default_rng(3)
    base = rng.standard_normal((80, 1))
    x = np.hstack([base, base, base + 1e-12])      # rank 1, exactly degenerate
    cov = ledoit_wolf_covariance(x)["covariance"]
    assert np.linalg.eigvalsh(cov).min() > 0
    np.linalg.cholesky(cov)                        # must not raise


def test_shrinkage_is_mild_when_the_sample_estimate_is_well_determined():
    """It is a data-driven intensity, not a fixed haircut. Given real
    correlation structure and plenty of observations it must get out of the way,
    or it would bias every risk number the platform reports.

    The structure matters to the test. Independent unit-variance columns are the
    one case where heavy shrinkage is CORRECT rather than lazy — the scaled
    identity is then the true covariance, so shrinking all the way to it is
    optimal and the intensity legitimately approaches 1. Probing the estimator
    with such a sample would test the opposite of what this asserts.
    """
    rng = np.random.default_rng(1)
    factor = rng.standard_normal((2000, 1))
    x = factor * np.array([[1.0, 0.8, 0.6, 1.3]]) + rng.standard_normal((2000, 4)) * 0.4
    lw = ledoit_wolf_covariance(x)
    assert lw["shrinkage"] < 0.05, (
        "with 2000 observations of 4 genuinely correlated series the sample "
        "estimate is well determined and shrinkage should barely engage")


def test_duplicated_assets_are_not_credited_with_diversification():
    rng = np.random.default_rng(7)
    r = rng.standard_normal(500) * 0.01
    corr, _ = shrunk_correlation(np.vstack([r, r]).T)
    assert corr[0, 1] > 0.98, (
        "two copies of one asset must remain near-perfectly correlated")


def test_shrunk_correlation_is_a_valid_correlation_matrix():
    rng = np.random.default_rng(5)
    x = rng.standard_normal((120, 12))
    corr, delta = shrunk_correlation(x)
    assert np.allclose(corr, corr.T)
    assert np.allclose(np.diag(corr), 1.0)
    assert np.abs(corr).max() <= 1.0 + 1e-12
    assert 0.0 <= delta <= 1.0


def test_lo_correction_haircuts_a_positively_autocorrelated_sharpe():
    """A trend-following book has positively autocorrelated period returns, so
    √q understates its true multi-period volatility and FLATTERS the annualised
    Sharpe. The error has a direction, and it is the expensive one."""
    rng = np.random.default_rng(11)
    x = [0.0]
    for _ in range(400):
        x.append(0.6 * x[-1] + rng.standard_normal() * 0.01 + 0.002)
    out = lo_annualized_sharpe(x[1:], 12)
    assert out["computable"]
    assert out["eta"] < out["sqrt_q"]
    assert out["annualized_lo"] < out["annualized_naive"]
    assert "overstates" in out["reading"]


def test_lo_correction_reduces_to_sqrt_q_without_serial_correlation():
    """It must never disagree with the naive figure without cause."""
    rng = np.random.default_rng(2)
    x = rng.standard_normal(6000) * 0.01 + 0.001
    out = lo_annualized_sharpe(x, 12)
    assert out["eta"] == pytest.approx(math.sqrt(12), rel=0.05)


def test_psr_falls_when_returns_are_negatively_skewed():
    """Two series with the SAME Sharpe are not equally believable. The plain
    t-statistic cannot say so; this is the whole point of the correction."""
    rng = np.random.default_rng(4)
    # A modest Sharpe on purpose: at a high one both probabilities saturate at
    # 1.0 and the comparison can no longer discriminate.
    sym = rng.standard_normal(400) * 0.02 + 0.0022
    # same mean and sd, but a long left tail
    skewed = -np.abs(rng.standard_normal(400)) ** 2
    skewed = (skewed - skewed.mean()) / skewed.std()
    skewed = skewed * sym.std() + sym.mean()

    a = probabilistic_sharpe_ratio(sym)
    b = probabilistic_sharpe_ratio(skewed)
    assert 0.5 < a["psr"] < 0.999, "the reference case must not saturate"
    assert a["computable"] and b["computable"]
    assert a["sharpe_per_period"] == pytest.approx(b["sharpe_per_period"], abs=0.02)
    assert b["psr"] < a["psr"], (
        "a negatively skewed series must carry LESS confidence at equal Sharpe")


def test_degenerate_inputs_report_rather_than_raise():
    assert ledoit_wolf_covariance(np.zeros((1, 5)))["computable"] is False
    assert lo_annualized_sharpe([0.01] * 3, 12)["computable"] is False
    assert probabilistic_sharpe_ratio([0.01] * 50, 0.0)["computable"] is False
