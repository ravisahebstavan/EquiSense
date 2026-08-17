"""Short-side executability — the money-making-realism gate (§8.1 ext).

These assert the one thing the signal stack cannot see: whether a bearish verdict
is a trade a retail Indian cash account can actually hold. A short is only holdable
as a single-stock future; a name with no future cannot be shorted overnight at all.
Hand-checked values, in the §15 tradition.
"""
from equisense.engine import india_market as im
from equisense.engine.sizing import (cost_tax_breakeven, futures_cost_breakeven,
                                     DP_CHARGE_PER_SELL, FNO_BROKERAGE_PER_ORDER)


def test_fno_name_is_shortable_via_future():
    ex = im.short_executability("RELIANCE")
    assert ex.executable is True
    assert ex.instrument == "single_stock_future"
    assert ex.lot_size and ex.lot_size > 0
    assert 0 < ex.margin_fraction < 1


def test_non_fno_name_is_not_shortable():
    ex = im.short_executability("NOSUCHTINYCO")
    assert ex.executable is False
    assert ex.instrument == "none"
    # the reason must name the real constraint, not a vague failure
    assert "overnight" in ex.reason.lower() or "future" in ex.reason.lower()


def test_round_to_lot_rounds_down_never_up():
    # 7 lots of 500 fit in 3999 shares; must never round UP past the risk budget
    assert im.round_to_lot(3999, 500) == 3500
    assert im.round_to_lot(499, 500) == 0          # below one lot → no position
    assert im.round_to_lot(1000, 500) == 1000
    assert im.round_to_lot(1234, None) == 1234     # unknown lot passes through


def test_futures_short_is_cheaper_than_delivery_on_a_large_ticket():
    notional = 1_500_000.0
    fut = futures_cost_breakeven(notional, adv_cr=50.0)
    deliv = cost_tax_breakeven(notional, adv_cr=50.0, expected_hold_months=6)
    assert fut["instrument"] == "single_stock_future"
    assert deliv["instrument"] == "delivery_equity"
    # futures statutory stack is materially lighter than delivery's double STT
    assert fut["statutory_pct"] < deliv["statutory_pct"]


def test_flat_costs_dominate_a_small_delivery_ticket():
    # The whole point of modelling flat costs: on a tiny slot they swamp the bps.
    small = cost_tax_breakeven(10_000.0, adv_cr=50.0, expected_hold_months=6)
    large = cost_tax_breakeven(10_000_000.0, adv_cr=50.0, expected_hold_months=6)
    assert small["flat_cost_pct"] > large["flat_cost_pct"]
    # ₹15 DP on a ₹10k ticket is 0.15% — a material hurdle that a proportional-
    # only model would have reported as exactly zero.
    assert small["flat_cost_pct"] >= 0.14
    assert large["flat_cost_pct"] < 0.001


def test_eligibility_provenance_is_reported_not_silent():
    prov = im.eligibility_provenance()
    assert prov["source"] == "pinned_snapshot"
    assert prov["snapshot_date"]
    assert prov["count"] == len(im.FNO_ELIGIBLE)
