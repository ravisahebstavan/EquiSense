"""Portfolio engine: FIFO ledger, XIRR vs. closed-form values, concentration
axes summing to 100% (§15)."""
from datetime import date

import pytest

from equisense.engine import portfolio as pf
from equisense.engine.portfolio import Position, Transaction


def test_positions_fifo():
    txns = [
        Transaction(1, "buy", 100, 50.0, date(2024, 1, 10)),
        Transaction(1, "buy", 100, 70.0, date(2024, 6, 10)),
        Transaction(1, "sell", 120, 90.0, date(2025, 1, 10)),
    ]
    pos = pf.positions_from_ledger(txns)[1]
    # FIFO: sell consumes 100 @50 + 20 @70 → realized = 100*40 + 20*20 = 4400
    assert pos.realized_pnl == pytest.approx(4400.0)
    assert pos.quantity == pytest.approx(80.0)
    assert pos.avg_cost == pytest.approx(70.0)
    assert pos.invested == pytest.approx(5600.0)


def test_xirr_closed_form():
    # -1000 → +1210 over exactly 730 days (2 years): r = sqrt(1.21) - 1 = 10%
    flows = [(date(2021, 1, 1), -1000.0), (date(2023, 1, 1), 1210.0)]
    assert pf.xirr(flows) == pytest.approx(0.10, abs=1e-6)


def test_xirr_requires_sign_change():
    assert pf.xirr([(date(2021, 1, 1), -100.0), (date(2022, 1, 1), -100.0)]) is None


def test_portfolio_xirr_with_terminal_value():
    txns = [Transaction(1, "buy", 10, 100.0, date(2021, 1, 1))]
    m = pf.portfolio_xirr(txns, current_values={1: 1210.0}, as_of=date(2023, 1, 1))
    assert m.value == pytest.approx(10.0, abs=1e-4)


def test_concentration_axes_sum_to_100():
    positions = {
        1: Position(1, quantity=10, avg_cost=100, invested=1000, realized_pnl=0, lots=[]),
        2: Position(2, quantity=5, avg_cost=200, invested=1000, realized_pnl=0, lots=[]),
    }
    prices = {1: 150.0, 2: 300.0}  # values: 1500, 1500 → 50/50
    c = pf.concentration(positions, prices,
                         sectors={1: "Healthcare", 2: "IT"},
                         cap_bands={1: "large", 2: "mid"},
                         quality_tiers={1: "high", 2: None})
    assert c["total_value"] == pytest.approx(3000.0)
    assert c["by_position"][1] == pytest.approx(50.0)
    assert c["by_sector"]["Healthcare"] == pytest.approx(50.0)
    assert sum(c["by_sector"].values()) == pytest.approx(100.0)
    assert c["by_quality_tier"]["unclassified"] == pytest.approx(50.0)


def test_lot_aging_ltcg_threshold():
    p = Position(1, quantity=10, avg_cost=100, invested=1000, realized_pnl=0,
                 lots=[{"date": date(2025, 1, 1), "quantity": 10, "price": 100.0}])
    aging = pf.lot_aging(p, as_of=date(2025, 12, 27))  # 360 days held
    assert aging[0]["is_long_term"] is False
    assert aging[0]["days_to_long_term"] == 5
