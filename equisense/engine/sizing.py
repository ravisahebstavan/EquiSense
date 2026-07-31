"""Sizing & cost engine (RESEARCH_BLUEPRINT §9).

Position size is a first-class advised quantity with every term shown.
Skeleton: volatility-based risk-per-trade, scaled by conviction, disciplined
by uncertainty haircuts and hard constraints (position cap, portfolio heat,
liquidity). Fractional-Kelly *spirit* under honest inputs — with base-rate
edges this thin and provisional, the risk-budget path dominates and pure
Kelly is deliberately not exposed as a number.

India cost/tax physics (§9.4): delivery equities, FY2026 rules —
  STT 0.1% each side · stamp 0.015% buy · exchange ≈0.00297% · SEBI 0.0001%
  brokerage assumed zero (discount broker delivery)
  STCG 20% (<12m) vs LTCG 12.5% (>12m, above ₹1.25L annual exemption)
All rates are named constants — inspectable, adjustable, and displayed.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- cost constants (fractions of trade value) ---
STT_EACH_SIDE = 0.001
STAMP_BUY = 0.00015
EXCHANGE_EACH_SIDE = 0.0000297
SEBI_EACH_SIDE = 0.000001
ROUND_TRIP_STATUTORY = (STT_EACH_SIDE * 2 + STAMP_BUY
                        + EXCHANGE_EACH_SIDE * 2 + SEBI_EACH_SIDE * 2)

STCG_RATE = 0.20
LTCG_RATE = 0.125

RISK_PER_TRADE = 0.0075       # 0.75% of book at the stop
MAX_PORTFOLIO_HEAT = 0.06     # sum of open R ≤ 6% of book
STOP_ATR_MULT = 2.5           # stop distance = 2.5 × daily vol
ADV_PARTICIPATION = 0.05      # exit ≤5% of ADV per day
EXIT_DAYS = 3


def impact_cost_pct(position_value_cr: float, adv_cr: float | None) -> float:
    """Square-root impact estimate in % of trade value, per side ≈ combined
    round trip here: 0.03% floor + 0.35% × √(position/ADV). No ADV data →
    punitive default rather than optimistic silence."""
    if not adv_cr or adv_cr <= 0:
        return 0.75
    participation = position_value_cr / adv_cr
    return min(1.5, 0.03 + 0.35 * (participation ** 0.5))


@dataclass
class SizingInputs:
    book_value: float             # ₹, total portfolio value
    price: float                  # ₹/share
    daily_vol_pct: float          # 1-day realized vol %, e.g. 1.8
    conviction_band: str          # low | moderate | high
    net_score: float              # [-1, 1]
    adv_cr: float | None          # avg daily traded value, ₹ crore
    max_position_pct: float       # from investor profile
    open_heat_pct: float = 0.0    # existing portfolio heat, %


def recommend_size(i: SizingInputs) -> dict:
    """Advisory size with the full computation exposed. Never an order."""
    stop_dist_pct = STOP_ATR_MULT * i.daily_vol_pct
    conviction_mult = {"low": 0.5, "moderate": 1.0, "high": 1.25}.get(i.conviction_band, 0.0)
    provisional_haircut = 0.5     # weights uncalibrated (§17 Gate 4) → half size, always shown

    risk_budget = i.book_value * RISK_PER_TRADE * conviction_mult * provisional_haircut
    raw_value = risk_budget / (stop_dist_pct / 100) if stop_dist_pct > 0 else 0.0

    cap_position = i.book_value * i.max_position_pct / 100
    heat_room = max(0.0, (MAX_PORTFOLIO_HEAT * 100 - i.open_heat_pct)) / 100 * i.book_value \
        / (stop_dist_pct / 100) if stop_dist_pct > 0 else 0.0
    # Unknown ADV must not RELAX the cap. It used to fall back to `raw_value`,
    # i.e. no liquidity constraint at all, precisely when the ability to exit is
    # unverifiable — the opposite of how impact_cost_pct treats the same missing
    # input ("punitive default rather than optimistic silence"). Absent ADV, the
    # size is halved and the constraint is named, so the ignorance is priced and
    # visible instead of silently free.
    if i.adv_cr:
        cap_liquidity = i.adv_cr * 1e7 * ADV_PARTICIPATION * EXIT_DAYS
    else:
        cap_liquidity = raw_value * 0.5

    value = max(0.0, min(raw_value, cap_position, heat_room, cap_liquidity))
    shares = int(value / i.price) if i.price > 0 else 0
    binding = min(
        [("risk_budget", raw_value), ("position_cap", cap_position),
         ("heat_room", heat_room),
         ("liquidity_cap" if i.adv_cr else "liquidity_unverified", cap_liquidity)],
        key=lambda kv: kv[1])[0]

    return {
        "recommended_value": round(shares * i.price, 2),
        "recommended_shares": shares,
        "pct_of_book": round(shares * i.price / i.book_value * 100, 2) if i.book_value else None,
        "stop_distance_pct": round(stop_dist_pct, 2),
        "risk_at_stop": round(shares * i.price * stop_dist_pct / 100, 2),
        "binding_constraint": binding,
        "working": {
            "risk_per_trade_pct": RISK_PER_TRADE * 100,
            "conviction_multiplier": conviction_mult,
            "provisional_haircut": provisional_haircut,
            "risk_budget": round(risk_budget, 2),
            "raw_value_from_risk": round(raw_value, 2),
            "position_cap": round(cap_position, 2),
            "heat_room_value": round(heat_room, 2),
            "liquidity_cap_value": round(cap_liquidity, 2),
            "adv_known": i.adv_cr is not None,
            "stop_rule": f"{STOP_ATR_MULT} × daily vol {i.daily_vol_pct:.2f}%",
        },
        "caveat": "Advisory only. The 0.5 provisional haircut applies until the "
                  "calibration ledger unlocks learned weights (§17 Gate 4).",
    }


def cost_tax_breakeven(position_value: float, adv_cr: float | None,
                       expected_hold_months: float) -> dict:
    """After-cost, after-tax hurdle math (§9.4) — the swing-killer, by design."""
    impact = impact_cost_pct(position_value / 1e7, adv_cr)
    round_trip_pct = ROUND_TRIP_STATUTORY * 100 + impact
    tax_rate = STCG_RATE if expected_hold_months < 12 else LTCG_RATE
    # gross move g such that (g − costs) × (1 − tax) covers costs’ drag:
    # net = (g − rt) × (1 − tax)  → breakeven g at net = 0 is rt
    breakeven_move_pct = round_trip_pct
    ltcg_cliff_note = None
    if expected_hold_months < 12:
        # identical gross move, held past the cliff: tax saved = g × (STCG − LTCG)
        ltcg_cliff_note = ("Holding the identical position past 12 months cuts tax "
                          f"from {STCG_RATE:.0%} to {LTCG_RATE:.1%} of gains — a "
                          f"{(STCG_RATE - LTCG_RATE) * 100:.1f}pp hurdle difference a "
                          "marginal swing must overcome.")
    return {
        "round_trip_cost_pct": round(round_trip_pct, 3),
        "statutory_pct": round(ROUND_TRIP_STATUTORY * 100, 3),
        "impact_estimate_pct": round(impact, 3),
        "applicable_tax": f"{'STCG 20%' if expected_hold_months < 12 else 'LTCG 12.5%'}",
        "breakeven_gross_move_pct": round(breakeven_move_pct, 2),
        "net_of_tax_multiplier": round(1 - tax_rate, 3),
        "ltcg_cliff_note": ltcg_cliff_note,
        "assumptions": "zero-brokerage delivery; impact ≈ f(position/ADV); "
                       "LTCG exemption (₹1.25L/yr) ignored conservatively",
    }
