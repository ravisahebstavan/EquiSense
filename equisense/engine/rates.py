"""Market-implied rates — derived from data, not asserted as a constant.

THE PROBLEM THIS SOLVES
-----------------------
`WaccAssumptions.risk_free_rate` defaulted to a hardcoded 7.0%. That single
constant propagates into the cost of equity, into WACC, and therefore into every
reverse-DCF implied-growth figure the platform publishes. A hardcoded rate is
wrong the moment the rate cycle moves, and it is wrong silently.

No free source publishes an Indian 10-year G-sec yield in a machine-readable
form (Yahoo carries no Indian government bond series). But the rate does not
have to be looked up — it can be BACKED OUT of instruments the platform already
downloads:

    Index futures price:  F = S · e^{(r − q)T}      ⇒  implied (r − q)
    NSE index close file: dividend yield q          (the exchange's own figure)
    ⇒  r  =  implied(r − q)  +  q

Both inputs are free, official and updated daily. The result is the financing
rate the market is actually transacting at, which is arguably a better discount
-rate anchor than a headline G-sec print: it is the rate at which this specific
equity exposure can genuinely be carried.

Cross-check on live data (30-Jul-2026): NIFTY's far contract implied 4.89% and
the exchange's Nifty-50 dividend yield was 1.2%, giving ~6.1% — squarely in the
range of the prevailing Indian policy corridor, from two independent free files.

WHAT THIS IS NOT
----------------
Not a forecast, and not a substitute for a G-sec quote. Calendar-basis rates
carry real noise: a single expiry can be distorted by roll congestion, dividend
timing inside the contract window, or thin far-month liquidity. The estimator
therefore prefers the longest contract with genuine open interest, reports every
contract's implied rate so the dispersion is visible, and refuses rather than
guessing when the curve is degenerate.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional, Sequence

from .types import Metric, fmt

CALENDAR_DAYS = 365.0
# A basis-implied rate outside this band is telling you about the contract, not
# about interest rates: roll congestion, a dividend inside the window, or a
# near-dead far month.
PLAUSIBLE_RF = (0.02, 0.12)
MIN_DAYS_FOR_RATE = 20          # very near expiries are dominated by roll noise
FALLBACK_RISK_FREE = 0.065


def implied_risk_free_rate(spot: float,
                           contracts: Sequence[tuple],
                           dividend_yield_pct: Optional[float],
                           as_of: Optional[date] = None,
                           min_open_interest: float = 0.0) -> Metric:
    """Risk-free rate implied by the index futures curve plus the index's own
    dividend yield.

    `contracts` = [(expiry, futures_price[, open_interest]), ...].

    Uses the longest contract that clears `min_open_interest` and the minimum
    tenor: cost-of-carry noise is roughly fixed in price terms, so dividing by a
    longer T shrinks its effect on the annualised rate. Near-month contracts look
    precise and are not.
    """
    ref = as_of or date.today()
    rows = []
    for c in sorted(contracts, key=lambda kv: kv[0]):
        expiry, price = c[0], c[1]
        oi = c[2] if len(c) > 2 else None
        dte = (expiry - ref).days
        if dte < MIN_DAYS_FOR_RATE or not price or price <= 0 or spot <= 0:
            continue
        if oi is not None and min_open_interest and oi < min_open_interest:
            continue
        T = dte / CALENDAR_DAYS
        rows.append({"expiry": expiry.isoformat(), "days_to_expiry": dte,
                     "futures": float(price), "open_interest": oi,
                     "implied_carry_pct": round(math.log(price / spot) / T * 100, 3)})

    if not rows:
        return Metric(
            key="implied_risk_free", label="Market-Implied Risk-Free Rate",
            value=None, unit="%",
            formula=f"needs a futures contract at least {MIN_DAYS_FOR_RATE} days out",
            inputs={"contracts_considered": len(contracts),
                    "fallback": FALLBACK_RISK_FREE * 100},
            period=ref.isoformat(), family="rates",
            caveat="Not derivable from this curve; callers should fall back to the "
                   f"stated {FALLBACK_RISK_FREE:.2%} assumption and show it as one.")

    chosen = rows[-1]                      # longest tenor available
    q = (dividend_yield_pct or 0.0) / 100.0
    rf = chosen["implied_carry_pct"] / 100.0 + q
    in_band = PLAUSIBLE_RF[0] <= rf <= PLAUSIBLE_RF[1]
    caveats = [
        "Backed out of the cash-futures basis, not quoted: r = implied(r − q) + q, "
        "with q the exchange's own published index dividend yield. It is the rate "
        "at which this exposure can actually be carried, which is the economically "
        "relevant discount anchor — but it is a market price, so it moves with roll "
        "demand and borrow conditions as well as with policy rates."]
    if not in_band:
        caveats.append(
            f"OUTSIDE the plausible {PLAUSIBLE_RF[0]:.0%}–{PLAUSIBLE_RF[1]:.0%} band, "
            "which points at the contract rather than at rates (roll congestion, a "
            "dividend inside the window, or a thin far month). Callers should use "
            "the stated fallback instead.")
    spread = None
    if len(rows) > 1:
        spread = round(max(r["implied_carry_pct"] for r in rows)
                       - min(r["implied_carry_pct"] for r in rows), 3)
        if spread > 3.0:
            caveats.append(
                f"Implied carry varies {spread:.1f}pp across expiries — the curve "
                "disagrees with itself, so treat this estimate as weak.")
    return Metric(
        key="implied_risk_free", label="Market-Implied Risk-Free Rate",
        value=rf * 100 if in_band else None, unit="%",
        formula=(f"implied carry {chosen['implied_carry_pct']:.3f}% "
                 f"({chosen['days_to_expiry']}d contract) + index dividend yield "
                 f"{(dividend_yield_pct or 0):.2f}% = {rf * 100:.3f}%"),
        inputs={"spot": spot, "chosen_contract": chosen,
                "dividend_yield_pct": dividend_yield_pct,
                "implied_risk_free_pct": round(rf * 100, 3),
                "carry_spread_across_expiries_pp": spread,
                "all_contracts": rows,
                "in_plausible_band": in_band,
                "fallback_pct": FALLBACK_RISK_FREE * 100},
        period=ref.isoformat(), family="rates",
        caveat=" ".join(caveats))


def equity_risk_premium_from_earnings_yield(index_pe: Optional[float],
                                            risk_free_pct: Optional[float],
                                            period: str = "") -> Metric:
    """A data-derived ERP sanity check: earnings yield minus the risk-free rate.

    ERP is the single most contested input in any DCF and the platform ships a
    0.065 default with no evidence attached. The earnings-yield spread is not the
    ERP — it ignores growth and payout entirely, which is exactly why it is
    reported as a CHECK rather than a replacement. When 1/PE − rf collapses
    toward zero, the market is pricing equities at a bond-like earnings yield and
    a 6.5% ERP assumption is doing heavy lifting the data does not support.
    """
    if not index_pe or index_pe <= 0 or risk_free_pct is None:
        return Metric(key="erp_check", label="ERP Sanity Check (earnings-yield spread)",
                      value=None, unit="pp",
                      formula="needs a positive index P/E and a risk-free rate",
                      inputs={}, period=period, family="rates")
    ey = 100.0 / index_pe
    spread = ey - risk_free_pct
    return Metric(
        key="erp_check", label="ERP Sanity Check (earnings-yield spread)",
        value=round(spread, 3), unit="pp",
        formula=f"earnings yield {ey:.2f}% (1 / P/E {index_pe:.2f}) − "
                f"risk-free {risk_free_pct:.2f}%",
        inputs={"index_pe": index_pe, "earnings_yield_pct": round(ey, 3),
                "risk_free_pct": risk_free_pct},
        period=period, family="rates",
        caveat=("NOT an equity risk premium — it ignores growth and payout, so it "
                "understates the ERP for a growing market. It is a directional "
                "check: a spread near or below zero means equities yield no more "
                "than cash on trailing earnings, and any DCF then rests almost "
                "entirely on the growth assumption rather than on the discount "
                "rate."))
