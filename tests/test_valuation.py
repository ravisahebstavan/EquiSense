"""Valuation engine: WACC vs. hand-computed value; reverse-DCF solver
round-trips a known growth rate (§15)."""
import pytest

from equisense.engine import valuation
from equisense.engine.valuation import ReverseDcfAssumptions, WaccAssumptions, _pv_of_fcf

PRICE = 300.0


def test_wacc_hand_computed(fy2025):
    # ke = 0.07 + 1.0*0.065 = 0.135 ; kd = 20/200 = 0.10
    # E = 3000, D = 200, V = 3200
    # wacc = 0.9375*0.135 + 0.0625*0.10*(1-0.2517) = 0.1265625 + 0.00467688
    m = valuation.compute_wacc(fy2025, PRICE, WaccAssumptions())
    assert m.value == pytest.approx((0.9375 * 0.135 + 0.0625 * 0.10 * (1 - 0.2517)) * 100)
    # assumptions must be exposed as inputs (§15 transparency requirement)
    for k in ("risk_free_rate", "equity_risk_premium", "beta", "tax_rate"):
        assert k in m.inputs


def test_reverse_dcf_recovers_known_growth(fy2025):
    """If the market price implies exactly g=8% under the model, the solver
    must recover 8%. Debt is zeroed so WACC = cost of equity (13.5%) and is
    independent of the derived price."""
    fy2025.total_debt = 0.0
    a = ReverseDcfAssumptions()
    wacc = 0.07 + 1.0 * 0.065  # ke, since D=0
    fcf0 = 120.0  # 180 - 60
    target_ev = _pv_of_fcf(fcf0, 0.08, wacc, a.horizon_years, a.terminal_growth)
    # Back out the share price producing this EV: EV = P*shares + debt(0) - cash
    price = (target_ev + 100.0) / 10.0
    result = valuation.reverse_dcf(fy2025, price, a)
    assert result["wacc"].value == pytest.approx(13.5)
    assert result["implied_growth"].value == pytest.approx(8.0, abs=0.01)


def test_reverse_dcf_flags_non_positive_fcf(fy2025):
    fy2025.capex = 300.0  # FCF = -120
    result = valuation.reverse_dcf(fy2025, PRICE)
    assert result["implied_growth"].value is None
    assert result["implied_growth"].caveat


def test_reverse_dcf_never_framed_as_forecast(fy2025):
    result = valuation.reverse_dcf(fy2025, PRICE)
    assert "NOT a forecast" in result["implied_growth"].caveat  # Commitment 2.4


def test_historical_fcf_cagr(fy2025, fy2024):
    # FCF: FY2024 = 95, FY2025 = 120 → CAGR over 1y = 120/95 - 1
    m = valuation.historical_fcf_cagr([fy2024, fy2025])
    assert m.value == pytest.approx((120 / 95 - 1) * 100)
