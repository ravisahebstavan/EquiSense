"""Sizing & cost engine (§8.1).

Position size is a first-class advised quantity with every term shown.
Skeleton: volatility-based risk-per-trade, scaled by conviction, disciplined
by uncertainty haircuts and hard constraints (position cap, portfolio heat,
liquidity). Fractional-Kelly *spirit* under honest inputs — with base-rate
edges this thin and provisional, the risk-budget path dominates and pure
Kelly is deliberately not exposed as a number.

India cost/tax physics (§8.1): delivery equities, FY2026 rules —
  STT 0.1% each side · stamp 0.015% buy · exchange ≈0.00297% · SEBI 0.0001%
  brokerage assumed zero (discount broker delivery)
  STCG 20% (<12m) vs LTCG 12.5% (>12m, above ₹1.25L annual exemption)
All rates are named constants — inspectable, adjustable, and displayed.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- DELIVERY-EQUITY cost constants (fractions of trade value), FY2026 ---
STT_EACH_SIDE = 0.001            # 0.10% each side, delivery
STAMP_BUY = 0.00015             # 0.015% buy side only
EXCHANGE_EACH_SIDE = 0.0000297  # NSE ≈ 0.00297% (₹297/cr) each side
SEBI_EACH_SIDE = 0.000001       # 0.0001% (₹10/cr) each side
GST_RATE = 0.18                 # 18% on (brokerage + exchange + SEBI), each side
# GST rides on the exchange + SEBI charges (brokerage assumed zero, discount
# delivery). Small in isolation, but omitting it understated every hurdle by a
# real amount, so it is carried explicitly rather than rounded away.
_GST_ON_CHARGES_EACH_SIDE = GST_RATE * (EXCHANGE_EACH_SIDE + SEBI_EACH_SIDE)
ROUND_TRIP_STATUTORY = (STT_EACH_SIDE * 2 + STAMP_BUY
                        + EXCHANGE_EACH_SIDE * 2 + SEBI_EACH_SIDE * 2
                        + _GST_ON_CHARGES_EACH_SIDE * 2)

# Flat, per-transaction costs. These are percentage-invisible on a large ticket
# and dominant on a small one — exactly the regime a personal account trades in —
# so a purely proportional cost model systematically flatters small books. The DP
# (depository) charge is levied by the broker/DP on every delivery SELL, flat per
# scrip regardless of quantity; ~₹15 is representative across discount brokers.
DP_CHARGE_PER_SELL = 15.0        # ₹, flat, delivery sell only
# F&O brokerage is not zero even at discount brokers: a flat ₹20 per order is the
# near-universal floor. Applied per leg (entry + exit) on the futures path.
FNO_BROKERAGE_PER_ORDER = 20.0   # ₹, flat, per futures order

STCG_RATE = 0.20
LTCG_RATE = 0.125

# --- SINGLE-STOCK FUTURES cost constants (fractions of NOTIONAL), FY2026 ---
# A short this system can actually hold is a stock future, not delivery, and its
# statutory stack is different: STT only on the SELL leg and at a far lower rate,
# a lighter exchange charge, stamp only on BUY, GST on charges. Capital committed
# is margin (~22%), not full notional. Applying delivery math to a futures short
# overstates the cost and the capital at once.
FUT_STT_SELL = 0.0002           # 0.02% on sell side of futures (post-Oct-2024)
FUT_STAMP_BUY = 0.00002         # 0.002% buy side
FUT_EXCHANGE_EACH_SIDE = 0.0000173  # NSE futures ≈ ₹173/cr each side
FUT_SEBI_EACH_SIDE = 0.000001
_FUT_GST_EACH_SIDE = GST_RATE * (FUT_EXCHANGE_EACH_SIDE + FUT_SEBI_EACH_SIDE)
FUT_ROUND_TRIP_STATUTORY = (FUT_STT_SELL + FUT_STAMP_BUY
                            + FUT_EXCHANGE_EACH_SIDE * 2 + FUT_SEBI_EACH_SIDE * 2
                            + _FUT_GST_EACH_SIDE * 2)

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
    """After-cost, after-tax hurdle math (§8.1) — the swing-killer, by design.

    LONG DELIVERY path. Flat costs (DP charge on the sell) are folded in as a
    percentage of THIS ticket, because their whole point is that they do not
    scale: on a small slot they can dwarf the statutory bps, which is precisely
    the friction a personal account lives with and a proportional-only model
    hides.
    """
    impact = impact_cost_pct(position_value / 1e7, adv_cr)
    # Flat costs → percent of this position. DP charge hits the sell leg once.
    flat_pct = (DP_CHARGE_PER_SELL / position_value * 100
                if position_value > 0 else 0.0)
    round_trip_pct = ROUND_TRIP_STATUTORY * 100 + impact + flat_pct
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
        "flat_cost_pct": round(flat_pct, 3),
        "instrument": "delivery_equity",
        "applicable_tax": f"{'STCG 20%' if expected_hold_months < 12 else 'LTCG 12.5%'}",
        "breakeven_gross_move_pct": round(breakeven_move_pct, 2),
        "net_of_tax_multiplier": round(1 - tax_rate, 3),
        "ltcg_cliff_note": ltcg_cliff_note,
        "assumptions": "zero-brokerage delivery; GST on charges; ₹%.0f DP charge "
                       "on sell; impact ≈ f(position/ADV); LTCG exemption "
                       "(₹1.25L/yr) ignored conservatively" % DP_CHARGE_PER_SELL,
    }


def futures_cost_breakeven(notional: float, adv_cr: float | None) -> dict:
    """After-cost hurdle math for a SINGLE-STOCK FUTURES short (§8.1 ext).

    The only way a retail cash account can hold a multi-week bearish position, so
    a short candidate is costed HERE, not on the delivery stack. Differences that
    matter: STT on the sell leg only and far lower; lighter exchange charge; stamp
    on buy only; a flat ₹20/order brokerage that discount brokers do charge on
    F&O and that bites a small ticket; and futures gains are taxed as business/
    STCG-style income, with no LTCG cliff (there is no 12-month delivery holding).

    `notional` is the full contract value (lots × lot_size × price); margin, not
    notional, is what capital-gates the trade and is handled in sizing.
    """
    impact = impact_cost_pct(notional / 1e7, adv_cr)
    brokerage_pct = (FNO_BROKERAGE_PER_ORDER * 2 / notional * 100
                     if notional > 0 else 0.0)     # entry + exit orders
    round_trip_pct = FUT_ROUND_TRIP_STATUTORY * 100 + impact + brokerage_pct
    return {
        "round_trip_cost_pct": round(round_trip_pct, 3),
        "statutory_pct": round(FUT_ROUND_TRIP_STATUTORY * 100, 3),
        "impact_estimate_pct": round(impact, 3),
        "flat_cost_pct": round(brokerage_pct, 3),
        "instrument": "single_stock_future",
        "applicable_tax": "F&O gains taxed as income (no LTCG concession)",
        "breakeven_gross_move_pct": round(round_trip_pct, 2),
        "assumptions": "futures STT sell-side only; GST on charges; ₹%.0f/order "
                       "brokerage; impact ≈ f(notional/ADV); rollover/basis cost "
                       "not modelled (single-expiry hold assumed)" % FNO_BROKERAGE_PER_ORDER,
    }


# ------------------------------------------------- small-capital feasibility

def capital_feasibility(book_value: float, universe_prices: list[float],
                        top_n: int = 15) -> dict:
    """Can this book actually BUY the names the ranking picks?

    Indian equity has no fractional shares, so a slot of book_value/top_n must
    clear the whole share price. Measured on the live universe (390 priced
    names, median 836, p75 1,893): a 15-name book funded with Rs 10,000 gives
    Rs 667 per slot and can afford a single share of only 45% of the universe.
    The ranking would keep selecting names the account cannot buy, and the book
    would silently become a cheap-stock portfolio — a systematic low-price tilt
    that is not the strategy and has none of its evidence behind it.

    This is a HARD constraint, not a cost. It binds before alpha, before tax,
    before slippage.
    """
    prices = sorted(p for p in universe_prices if p and p > 0)
    if not prices or book_value <= 0 or top_n <= 0:
        return {"feasible": False, "reason": "no priced universe or no capital"}
    slot = book_value / top_n
    affordable = sum(1 for p in prices if p <= slot) / len(prices)
    # what capital would be needed to reach 85% coverage at this N
    idx = int(0.85 * (len(prices) - 1))
    needed = prices[idx] * top_n
    return {
        "feasible": affordable >= 0.85,
        "slot_value": round(slot, 2),
        "universe_affordable_pct": round(affordable * 100, 1),
        "capital_for_85pct_coverage": round(needed, -2),
        "top_n": top_n,
        "note": (
            f"A {top_n}-name book needs about Rs {needed:,.0f} for 85% of the "
            f"universe to be reachable. At Rs {book_value:,.0f} only "
            f"{affordable * 100:.0f}% is, so the ranking will repeatedly select "
            "names the account cannot buy and the book drifts into a cheap-stock "
            "tilt that carries none of this system's evidence. Below that "
            "threshold a broad index ETF gives strictly better diversification "
            "per rupee, and the right use of this system is paper-trading while "
            "the forward record accumulates."),
    }
