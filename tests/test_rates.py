"""Market-implied rates: derived from data, never asserted as a constant."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from equisense.engine import rates


def _curve(spot: float, rate: float, q: float, tenors=(30, 60, 90)):
    """Futures priced exactly off F = S·e^{(r−q)T} so the estimator must invert it."""
    ref = date(2026, 7, 30)
    return ref, [(ref + timedelta(days=d),
                  spot * math.exp((rate - q) * d / 365.0)) for d in tenors]


def test_recovers_a_known_risk_free_rate():
    ref, curve = _curve(24000.0, 0.0625, 0.012)
    m = rates.implied_risk_free_rate(24000.0, curve, 1.2, as_of=ref)
    assert m.value == pytest.approx(6.25, abs=0.02)


def test_uses_the_longest_tenor_available():
    """Carry noise is roughly fixed in price terms, so a longer T shrinks its
    effect on the annualised rate. Near months look precise and are not."""
    ref, curve = _curve(24000.0, 0.06, 0.012, tenors=(25, 60, 120))
    m = rates.implied_risk_free_rate(24000.0, curve, 1.2, as_of=ref)
    assert m.inputs["chosen_contract"]["days_to_expiry"] == 120


def test_skips_contracts_that_are_too_near_dated():
    ref, curve = _curve(24000.0, 0.06, 0.012, tenors=(3, 8))
    m = rates.implied_risk_free_rate(24000.0, curve, 1.2, as_of=ref)
    assert m.value is None
    assert "fall back" in m.caveat
    assert m.inputs["fallback"] == pytest.approx(rates.FALLBACK_RISK_FREE * 100)


def test_rejects_an_implausible_implied_rate_rather_than_reporting_it():
    """A basis outside the band is telling you about the contract — roll
    congestion, a dividend in the window, a dead far month — not about rates."""
    ref, curve = _curve(24000.0, 0.45, 0.012, tenors=(60, 90))
    m = rates.implied_risk_free_rate(24000.0, curve, 1.2, as_of=ref)
    assert m.value is None
    assert "OUTSIDE the plausible" in m.caveat
    assert m.inputs["in_plausible_band"] is False


def test_flags_a_curve_that_disagrees_with_itself():
    ref = date(2026, 7, 30)
    curve = [(ref + timedelta(days=40), 24050.0),
             (ref + timedelta(days=100), 25200.0)]   # wildly inconsistent carry
    m = rates.implied_risk_free_rate(24000.0, curve, 1.2, as_of=ref)
    assert m.inputs["carry_spread_across_expiries_pp"] > 3.0
    assert "disagrees with itself" in m.caveat


def test_dividend_yield_is_added_back():
    """Futures imply (r − q); the risk-free rate is only recovered by adding the
    exchange's published dividend yield back."""
    ref, curve = _curve(24000.0, 0.06, 0.02)
    with_q = rates.implied_risk_free_rate(24000.0, curve, 2.0, as_of=ref)
    without_q = rates.implied_risk_free_rate(24000.0, curve, 0.0, as_of=ref)
    assert with_q.value - without_q.value == pytest.approx(2.0, abs=1e-6)


def test_erp_check_is_earnings_yield_minus_risk_free():
    m = rates.equity_risk_premium_from_earnings_yield(20.0, 6.0)
    assert m.value == pytest.approx(5.0 - 6.0, abs=1e-9)
    assert "NOT an equity risk premium" in m.caveat


def test_erp_check_refuses_bad_inputs():
    assert rates.equity_risk_premium_from_earnings_yield(None, 6.0).value is None
    assert rates.equity_risk_premium_from_earnings_yield(-5.0, 6.0).value is None
    assert rates.equity_risk_premium_from_earnings_yield(20.0, None).value is None


def test_empty_curve_reports_the_fallback():
    m = rates.implied_risk_free_rate(24000.0, [], 1.2)
    assert m.value is None
    assert m.inputs["fallback"] == pytest.approx(rates.FALLBACK_RISK_FREE * 100)
