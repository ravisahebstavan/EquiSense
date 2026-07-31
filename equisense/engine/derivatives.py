"""Derivatives engine — futures and options analytics (Indian F&O first).

Pure functions. No I/O. Every output is a Metric or a fully-decomposed dict so
the work is inspectable, per the platform's explainability contract.

WHY THIS MODULE EXISTS
----------------------
NSE publishes a complete daily F&O bhavcopy as a public archive file: every
strike, both option types, settlement price, open interest, change in OI,
underlying price and lot size. That is a real option chain and a real futures
term structure, free and keyless. This module turns it into decision-grade
analytics:

  * futures basis → the *implied financing rate*, which is the honest way to
    read contango/backwardation rather than eyeballing the price ladder;
  * Black-76 / Black-Scholes-Merton pricing with full greeks;
  * implied volatility solved from settlement prices → an IV term structure and
    a strike skew, neither of which any free Indian retail tool exposes;
  * open-interest structure (PCR, OI walls, max pain) with its interpretation
    limits stated, because OI analytics are where retail folklore is thickest;
  * leverage and margin arithmetic, with the actual base rate of retail F&O
    outcomes attached to it (see `fno_reality_check`).

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No directional option "signals", no max-pain-as-a-price-target, no volatility
forecasting dressed up as a prediction. Max pain and PCR are computed because
they are widely used and therefore worth measuring honestly — each carries an
explicit note that the published evidence for their predictive value is weak.
That is the same standard the equity engines are held to.

SPAN margin is proprietary to the exchange and cannot be reproduced exactly.
`margin_estimate` implements a scenario-based worst-case in SPAN's *spirit* and
labels itself an estimate everywhere it surfaces. It is for sizing sanity, never
for assuming a broker will accept a position.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence

from ..research.stats import norm_cdf
from .types import Metric, fmt

SQRT_2PI = math.sqrt(2.0 * math.pi)
TRADING_DAYS = 252
CALENDAR_DAYS = 365.0


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


# --------------------------------------------------------------- SEBI base rate

# Published SEBI studies of individual participants in the equity derivatives
# segment. These are the base rates for the activity itself — the single most
# decision-relevant number in this whole module, so it is a first-class output
# rather than a disclaimer in small print.
SEBI_FNO_STUDIES = {
    "FY22_FY24_individuals": {
        "share_of_individuals_with_net_losses_pct": 93.0,
        "note": "~93% of individual traders in equity F&O incurred net losses "
                "over FY2022–FY2024; aggregate individual losses exceeded "
                "₹1.8 lakh crore over the three years.",
        "source": "SEBI study on individual trading activity in the equity "
                  "derivatives segment (published Sep 2024)",
    },
    "FY24_intraday_equity": {
        "share_of_young_intraday_traders_with_losses_pct": 76.0,
        "note": "In intraday cash equity, loss-making shares are similar in "
                "magnitude and worst among the under-30 cohort.",
        "source": "SEBI study on intraday trading by individuals (Jul 2024)",
    },
}


def fno_reality_check(position_notional: float, margin_blocked: float,
                      account_equity: float) -> dict:
    """The arithmetic of leverage, with the activity's own base rate attached.

    This is not moralising: leverage is a multiplier on an edge, and if the edge
    is unproven the multiplier applies to noise. The platform's own base-rate
    machinery has so far failed to validate a single price-only edge net of
    costs on this universe (see research/base_rates.py). Sizing decisions
    should see both facts at once.
    """
    lev = position_notional / account_equity if account_equity > 0 else None
    margin_util = margin_blocked / account_equity if account_equity > 0 else None
    # move in the underlying that wipes out the blocked margin
    ruin_move_pct = (margin_blocked / position_notional * 100
                     if position_notional > 0 else None)
    return {
        "position_notional": round(position_notional, 2),
        "margin_blocked": round(margin_blocked, 2),
        "account_equity": round(account_equity, 2),
        "effective_leverage_x": None if lev is None else round(lev, 2),
        "margin_utilisation_pct": None if margin_util is None else round(margin_util * 100, 1),
        "adverse_move_that_erases_margin_pct": None if ruin_move_pct is None
        else round(ruin_move_pct, 2),
        "base_rate": SEBI_FNO_STUDIES["FY22_FY24_individuals"],
        "interpretation": (
            "Leverage multiplies whatever edge exists. This platform has not yet "
            "validated a price-only edge that survives its own cost model and "
            "multiple-testing control, so treat leveraged exposure as amplified "
            "variance until a base rate says otherwise."),
    }


# ------------------------------------------------------------- futures / carry

def futures_basis(spot: float, futures: float, days_to_expiry: int,
                  period: str = "") -> Metric:
    """Annualised implied financing rate from the cash-futures basis.

    F = S·e^{(r−q)T}  ⇒  implied (r−q) = ln(F/S) / T

    Reading the *rate* rather than the rupee basis is what makes different
    expiries and different underlyings comparable, and it immediately exposes
    the two economically interesting cases: an implied rate far above the
    risk-free rate (expensive longs / borrow demand) and a negative implied
    rate (backwardation — dividend, squeeze, or genuine selling pressure).
    """
    if spot <= 0 or futures <= 0 or days_to_expiry <= 0:
        return Metric(key="futures_basis", label="Implied Financing Rate",
                      value=None, unit="%", formula="needs positive spot, futures and tenor",
                      inputs={}, period=period, family="derivatives")
    T = days_to_expiry / CALENDAR_DAYS
    implied = math.log(futures / spot) / T
    basis_pts = (futures / spot - 1) * 100
    return Metric(
        key="futures_basis", label="Implied Financing Rate (annualised)",
        value=implied * 100, unit="%",
        formula=(f"ln(F {fmt(futures, 2)} / S {fmt(spot, 2)}) / T "
                 f"({days_to_expiry}/365) — basis {basis_pts:+.2f}%"),
        inputs={"spot": spot, "futures": futures, "days_to_expiry": days_to_expiry,
                "basis_pct": round(basis_pts, 3),
                "structure": "contango" if futures > spot else
                             ("backwardation" if futures < spot else "flat")},
        period=period, family="derivatives",
        caveat="Implied (r − q): a dividend or borrow-cost change moves this "
               "without any view changing. Compare against the prevailing "
               "risk-free rate, not against zero.")


def term_structure(spot: float, contracts: Sequence[tuple],
                   as_of: Optional[date] = None) -> dict:
    """Full futures curve → per-expiry implied rates plus the calendar spread.

    `contracts` = [(expiry_date, futures_price), ...]. The near/next implied
    rate differential is the roll cost a position actually pays, which matters
    far more to a real P&L than the headline basis.
    """
    period = as_of.isoformat() if as_of else ""
    ref = as_of or date.today()
    rows = []
    for expiry, price in sorted(contracts, key=lambda kv: kv[0]):
        dte = (expiry - ref).days
        if dte <= 0 or not price:
            continue
        m = futures_basis(spot, float(price), dte, period)
        rows.append({"expiry": expiry.isoformat(), "days_to_expiry": dte,
                     "futures": float(price),
                     "implied_rate_pct": m.rounded(3),
                     "basis_pct": m.inputs.get("basis_pct")})
    out = {"spot": spot, "as_of": period, "contracts": rows}
    if len(rows) >= 2:
        near, nxt = rows[0], rows[1]
        roll = (nxt["futures"] / near["futures"] - 1) * 100
        gap_days = nxt["days_to_expiry"] - near["days_to_expiry"]
        out["calendar_spread"] = {
            "near_expiry": near["expiry"], "next_expiry": nxt["expiry"],
            "spread_pct": round(roll, 3),
            "annualised_roll_pct": round(roll * CALENDAR_DAYS / gap_days, 2)
            if gap_days > 0 else None,
            "interpretation": (
                "Cost of carrying the position past near expiry, annualised. A "
                "steep positive roll is a recurring drag on a long that must be "
                "earned back before any thesis pays."),
        }
    out["curve_shape"] = _curve_shape([r["implied_rate_pct"] for r in rows])
    return out


def _curve_shape(rates: Sequence[Optional[float]]) -> str:
    r = [x for x in rates if x is not None]
    if len(r) < 2:
        return "insufficient contracts"
    if all(b >= a - 1e-9 for a, b in zip(r, r[1:])):
        return "upward-sloping implied-rate curve"
    if all(b <= a + 1e-9 for a, b in zip(r, r[1:])):
        return "downward-sloping implied-rate curve"
    return "non-monotonic implied-rate curve"


# ------------------------------------------------------------ option pricing

@dataclass
class OptionGreeks:
    price: float
    delta: float
    gamma: float
    vega: float          # per 1 vol point (i.e. per 1%)
    theta: float         # per calendar day
    rho: float           # per 1% rate move
    model: str
    inputs: dict

    def to_dict(self) -> dict:
        return {"price": round(self.price, 4), "delta": round(self.delta, 5),
                "gamma": round(self.gamma, 7), "vega": round(self.vega, 5),
                "theta": round(self.theta, 5), "rho": round(self.rho, 5),
                "model": self.model, "inputs": self.inputs}


def black_scholes(S: float, K: float, T: float, r: float, sigma: float,
                  kind: str = "call", q: float = 0.0) -> Optional[OptionGreeks]:
    """Black-Scholes-Merton on a spot underlying with continuous yield `q`.

    T in years, r/q/sigma as decimals. Vega is scaled per 1 percentage point of
    volatility and theta per calendar day, because those are the units a trader
    actually reasons in.
    """
    kind = kind.lower()
    if kind not in ("call", "put") or S <= 0 or K <= 0:
        return None
    if T <= 0 or sigma <= 0:
        # degenerate: intrinsic value, no sensitivities except delta
        intrinsic = max(0.0, (S - K) if kind == "call" else (K - S))
        d = (1.0 if S > K else 0.0) if kind == "call" else (-1.0 if S < K else 0.0)
        return OptionGreeks(intrinsic, d, 0.0, 0.0, 0.0, 0.0,
                            "intrinsic (T≤0 or σ≤0)",
                            {"S": S, "K": K, "T": T, "r": r, "sigma": sigma, "q": q})
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    dfr, dfq = math.exp(-r * T), math.exp(-q * T)
    nd1, nd2 = norm_cdf(d1), norm_cdf(d2)
    pdf = _npdf(d1)
    gamma = dfq * pdf / (S * sq)
    vega = S * dfq * pdf * math.sqrt(T) / 100.0
    if kind == "call":
        price = S * dfq * nd1 - K * dfr * nd2
        delta = dfq * nd1
        theta = (-S * dfq * pdf * sigma / (2 * math.sqrt(T))
                 - r * K * dfr * nd2 + q * S * dfq * nd1) / CALENDAR_DAYS
        rho = K * T * dfr * nd2 / 100.0
    else:
        price = K * dfr * norm_cdf(-d2) - S * dfq * norm_cdf(-d1)
        delta = -dfq * norm_cdf(-d1)
        theta = (-S * dfq * pdf * sigma / (2 * math.sqrt(T))
                 + r * K * dfr * norm_cdf(-d2) - q * S * dfq * norm_cdf(-d1)) / CALENDAR_DAYS
        rho = -K * T * dfr * norm_cdf(-d2) / 100.0
    return OptionGreeks(price, delta, gamma, vega, theta, rho,
                        "Black-Scholes-Merton (spot, continuous yield q)",
                        {"S": S, "K": K, "T": round(T, 6), "r": r,
                         "sigma": sigma, "q": q, "d1": round(d1, 5),
                         "d2": round(d2, 5)})


def black76(F: float, K: float, T: float, r: float, sigma: float,
            kind: str = "call") -> Optional[OptionGreeks]:
    """Black (1976) on a futures underlying — the right model when the hedging
    instrument is the future, which for NIFTY/BANKNIFTY and every commodity
    contract it is. Equivalent to BSM with q = r."""
    g = black_scholes(F, K, T, r, sigma, kind, q=r)
    if g is None:
        return None
    g.model = "Black-76 (futures underlying)"
    g.inputs = {"F": F, "K": K, "T": round(T, 6), "r": r, "sigma": sigma}
    return g


IV_MIN, IV_MAX = 1e-6, 5.0
# An option whose quote exceeds its zero-vol floor by less than this fraction of
# the underlying carries (numerically) no volatility information: price is flat
# in sigma, so ANY sigma fits and a returned number would be fabricated.
IV_IDENTIFIABILITY_TOL = 1e-7
# An IV is only REPORTED if the quote pins sigma down to better than this.
# Precision in sigma ≈ (price resolution) / (vega per unit vol); when vega is
# tiny the quote is consistent with a wide band of volatilities and the honest
# output is "no IV", not the midpoint of that band.
IV_MAX_REPORTABLE_UNCERTAINTY = 0.005      # half a volatility point
_PRICE_RESOLUTION_REL = 1e-9               # ~ double-precision noise after exp/log


def implied_vol(market_price: float, S: float, K: float, T: float, r: float,
                kind: str = "call", q: float = 0.0,
                model: str = "bsm") -> Optional[float]:
    """Solve implied volatility, or return None when IV is not identifiable.

    Newton-Raphson with a guaranteed bisection fallback (price is monotone in
    sigma, so bisection always converges).

    The important part is what this REFUSES to do. Deep in-the-money and
    near-expiry quotes are almost entirely intrinsic value; their price is
    numerically flat in volatility, so the residual |model − market| is already
    ~0 at any starting guess. A naive convergence test therefore "succeeds"
    instantly and hands back the initial guess dressed as a solved IV — a
    fabricated number that would then propagate into the ATM level, the skew,
    and every term-structure statistic built on the surface.

    So identifiability is checked BEFORE solving:
      * the quote must sit strictly inside the attainable price range
        [P(σ→0), P(σ=5)] (no-arbitrage bounds), and
      * that range must be wide enough for sigma to be pinned down at all.
    Unsolvable quotes are dropped, never clamped to a bound or to a guess.
    """
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    pricer = (lambda v: black76(S, K, T, r, v, kind)) if model == "black76" \
        else (lambda v: black_scholes(S, K, T, r, v, kind, q))

    lo_p, hi_p = pricer(IV_MIN), pricer(IV_MAX)
    if lo_p is None or hi_p is None:
        return None
    floor_price, ceil_price = lo_p.price, hi_p.price
    # Numerical epsilon ONLY — this pre-check enforces no-arbitrage bounds, not
    # identifiability. Identifiability is decided once, at the end, by the
    # vega-based precision gate; using a price-level threshold here as a proxy
    # wrongly discarded quotes whose extrinsic value is tiny in absolute terms
    # but whose sigma is nonetheless pinned down (deep-ITM, long-dated, low-vol).
    eps = 1e-12 * max(abs(S), abs(K), 1.0)
    tol = eps

    # (a) volatility must be able to move the price at all
    if ceil_price - floor_price <= eps:
        return None
    # (b) the quote must lie strictly inside the attainable price range
    if market_price <= floor_price + eps or market_price >= ceil_price - eps:
        return None

    v = 0.25
    solved: Optional[float] = None
    for _ in range(60):
        g = pricer(v)
        if g is None:
            break
        diff = g.price - market_price
        vega_unit = g.vega * 100.0     # back to per-1.0-vol
        # converged only if the price is genuinely SENSITIVE to sigma here;
        # a flat-vega "match" is not a solution, it is an artefact
        if abs(diff) < 1e-10 and vega_unit > tol:
            solved = v
            break
        if vega_unit <= tol:
            break
        v_new = v - diff / vega_unit
        if not (IV_MIN < v_new < IV_MAX) or v_new != v_new:
            break
        if abs(v_new - v) < 1e-12:
            solved = v_new
            break
        v = v_new
    if solved is None:
        lo, hi = IV_MIN, IV_MAX
        for _ in range(300):
            mid = 0.5 * (lo + hi)
            g = pricer(mid)
            if g is None:
                return None
            if g.price < market_price:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-12:
                break
        solved = 0.5 * (lo + hi)

    # Final gate: is sigma actually PINNED DOWN by this quote?
    # sigma uncertainty ≈ price resolution / vega(per unit vol). Report only if
    # that band is narrower than IV_MAX_REPORTABLE_UNCERTAINTY.
    g = pricer(solved)
    if g is None:
        return None
    vega_unit = g.vega * 100.0
    if vega_unit <= tol:
        return None
    price_resolution = max(_PRICE_RESOLUTION_REL * max(abs(g.price), 1.0), 1e-12)
    sigma_uncertainty = price_resolution / vega_unit
    if sigma_uncertainty > IV_MAX_REPORTABLE_UNCERTAINTY:
        return None
    if abs(g.price - market_price) > max(1e-4, 1e-7 * max(S, K)):
        return None
    return solved if IV_MIN * 10 < solved < IV_MAX * 0.999 else None


# --------------------------------------------------------- chain analytics

@dataclass
class OptionQuote:
    strike: float
    kind: str                     # "call" | "put"
    price: float                  # settlement or close
    open_interest: float = 0.0
    change_in_oi: float = 0.0
    volume: float = 0.0


def option_chain_analytics(quotes: Sequence[OptionQuote], underlying: float,
                          days_to_expiry: int, risk_free: float = 0.065,
                          lot_size: int = 1, period: str = "") -> dict:
    """Structure of one expiry's chain: IV surface, skew, OI concentration,
    PCR, max pain — each with its evidentiary status stated.

    The IV work is the analytically valuable part. PCR and max pain are computed
    because they are ubiquitous in Indian retail commentary and it is better to
    measure them properly, with their weak evidence base attached, than to leave
    the user to a WhatsApp forward.
    """
    T = max(days_to_expiry, 0) / CALENDAR_DAYS
    calls = [q for q in quotes if q.kind == "call"]
    puts = [q for q in quotes if q.kind == "put"]
    if not calls and not puts:
        return {"computable": False, "reason": "empty chain"}

    ivs: list[dict] = []
    for q in quotes:
        iv = implied_vol(q.price, underlying, q.strike, T, risk_free,
                         kind=q.kind, model="black76")
        if iv is None:
            continue
        g = black76(underlying, q.strike, T, risk_free, iv, q.kind)
        ivs.append({"strike": q.strike, "kind": q.kind, "iv_pct": round(iv * 100, 2),
                    "price": q.price, "open_interest": q.open_interest,
                    "delta": None if g is None else round(g.delta, 4),
                    "gamma": None if g is None else round(g.gamma, 7),
                    "vega": None if g is None else round(g.vega, 4),
                    "theta": None if g is None else round(g.theta, 4)})

    atm_strike = min((q.strike for q in quotes),
                     key=lambda k: abs(k - underlying), default=None)
    atm_ivs = [r["iv_pct"] for r in ivs if r["strike"] == atm_strike]
    atm_iv = sum(atm_ivs) / len(atm_ivs) if atm_ivs else None

    # 25-delta risk reversal: the standard, model-light skew statistic
    skew = _risk_reversal_25d(ivs)

    call_oi = sum(q.open_interest for q in calls)
    put_oi = sum(q.open_interest for q in puts)
    pcr_oi = put_oi / call_oi if call_oi > 0 else None

    oi_by_strike: dict[float, dict] = {}
    for q in quotes:
        e = oi_by_strike.setdefault(q.strike, {"call_oi": 0.0, "put_oi": 0.0})
        e["call_oi" if q.kind == "call" else "put_oi"] += q.open_interest
    walls = sorted(oi_by_strike.items(),
                   key=lambda kv: -(kv[1]["call_oi"] + kv[1]["put_oi"]))[:5]

    return {
        "computable": True,
        "as_of": period,
        "underlying": underlying,
        "days_to_expiry": days_to_expiry,
        "lot_size": lot_size,
        "atm_strike": atm_strike,
        "atm_iv_pct": None if atm_iv is None else round(atm_iv, 2),
        "iv_points_solved": len(ivs),
        "iv_surface": sorted(ivs, key=lambda r: (r["kind"], r["strike"])),
        "skew_25d_risk_reversal_pct": skew,
        "put_call_ratio_oi": None if pcr_oi is None else round(pcr_oi, 3),
        "total_call_oi": call_oi, "total_put_oi": put_oi,
        "max_pain_strike": max_pain(quotes),
        "oi_walls": [{"strike": k, **v} for k, v in walls],
        "notional_per_lot": None if not lot_size else round(underlying * lot_size, 2),
        "methodology": {
            "iv_model": "Black-76 on the futures/underlying settlement price; "
                        "IV solved by Newton with bisection fallback and "
                        "no-arbitrage bounds enforced (unsolvable quotes are "
                        "dropped, never clamped).",
            "skew": "25-delta risk reversal = IV(25Δ put) − IV(25Δ call), "
                    "interpolated on solved deltas. Positive = downside "
                    "protection is bid, the normal state for equity indices.",
            "pcr_caveat": "Put-call ratio is a positioning descriptor, not a "
                          "signal. Published evidence for PCR as a standalone "
                          "timing tool is weak and regime-dependent; it is "
                          "reported here so it can be measured rather than "
                          "assumed.",
            "max_pain_caveat": "Max pain is the strike minimising aggregate "
                               "option-writer payout at expiry given CURRENT "
                               "OI. It is a static accounting identity, not a "
                               "forecast, and OI changes daily. Treating it as "
                               "a price target is unsupported by evidence.",
        },
    }


def _interp_iv_at_delta(rows: list[dict], kind: str, target_abs_delta: float) -> Optional[float]:
    pts = sorted(((abs(r["delta"]), r["iv_pct"]) for r in rows
                  if r["kind"] == kind and r.get("delta") is not None),
                 key=lambda kv: kv[0])
    if len(pts) < 2:
        return None
    if target_abs_delta <= pts[0][0]:
        return pts[0][1]
    if target_abs_delta >= pts[-1][0]:
        return pts[-1][1]
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if d0 <= target_abs_delta <= d1:
            if d1 == d0:
                return v0
            w = (target_abs_delta - d0) / (d1 - d0)
            return v0 + w * (v1 - v0)
    return None


def _risk_reversal_25d(ivs: list[dict]) -> Optional[float]:
    p = _interp_iv_at_delta(ivs, "put", 0.25)
    c = _interp_iv_at_delta(ivs, "call", 0.25)
    if p is None or c is None:
        return None
    return round(p - c, 3)


def max_pain(quotes: Sequence[OptionQuote]) -> Optional[float]:
    """Strike minimising total in-the-money payout to option holders, given
    current OI. An accounting identity over the existing book — computed
    because it is widely quoted, captioned because it is widely misused."""
    strikes = sorted({q.strike for q in quotes})
    if not strikes:
        return None
    best, best_pain = None, None
    for s in strikes:
        pain = 0.0
        for q in quotes:
            if q.kind == "call" and s > q.strike:
                pain += (s - q.strike) * q.open_interest
            elif q.kind == "put" and s < q.strike:
                pain += (q.strike - s) * q.open_interest
        if best_pain is None or pain < best_pain:
            best, best_pain = s, pain
    return best


# ------------------------------------------------------------- strategy payoff

@dataclass
class Leg:
    kind: str            # "call" | "put" | "future" | "spot"
    strike: float        # ignored for future/spot
    quantity: int        # +long / −short, in lots
    premium: float       # per unit paid (+) or received (−) is handled by sign of qty
    lot_size: int = 1


def payoff_curve(legs: Sequence[Leg], spot_lo: float, spot_hi: float,
                 points: int = 81) -> dict:
    """Expiry payoff of an arbitrary multi-leg position, plus breakevens and
    max/min outcomes over the sampled range.

    Reported bounds are over the SAMPLED RANGE only — an unhedged short option
    has unbounded loss and the output says so explicitly rather than printing a
    finite worst case that a user might mistake for a real floor.
    """
    if spot_hi <= spot_lo or points < 3 or not legs:
        return {"computable": False, "reason": "bad range or no legs"}
    step = (spot_hi - spot_lo) / (points - 1)
    xs = [spot_lo + i * step for i in range(points)]
    curve = []
    for s in xs:
        total = 0.0
        for L in legs:
            mult = L.quantity * L.lot_size
            if L.kind == "call":
                total += mult * (max(0.0, s - L.strike) - L.premium)
            elif L.kind == "put":
                total += mult * (max(0.0, L.strike - s) - L.premium)
            else:  # future / spot
                total += mult * (s - L.premium)
        curve.append({"spot": round(s, 2), "payoff": round(total, 2)})

    breakevens = []
    for (a, b) in zip(curve, curve[1:]):
        ya, yb = a["payoff"], b["payoff"]
        if ya == 0.0:
            breakevens.append(a["spot"])
        elif ya * yb < 0:
            t = abs(ya) / (abs(ya) + abs(yb))
            breakevens.append(round(a["spot"] + t * (b["spot"] - a["spot"]), 2))

    naked_short = any(L.quantity < 0 and L.kind in ("call", "put")
                      and not _is_covered(L, legs) for L in legs)
    net_premium = sum(L.quantity * L.lot_size * L.premium
                      for L in legs if L.kind in ("call", "put"))
    return {
        "computable": True,
        "curve": curve,
        "breakevens": breakevens,
        "max_profit_in_range": max(c["payoff"] for c in curve),
        "max_loss_in_range": min(c["payoff"] for c in curve),
        "net_premium_paid": round(net_premium, 2),
        "range": [spot_lo, spot_hi],
        "risk_note": (
            "UNBOUNDED RISK: this position contains a short option leg that is "
            "not covered by a further-out long of the same type. The max loss "
            "shown is the worst outcome inside the sampled range only — the "
            "true loss is unbounded."
            if naked_short else
            "Loss is bounded by construction within this leg set; the max loss "
            "shown is the structural worst case."),
    }


def _is_covered(short_leg: Leg, legs: Sequence[Leg]) -> bool:
    """A short call is covered by a long call at a higher strike (and a short
    put by a long put at a lower strike) with at least equal quantity."""
    need = abs(short_leg.quantity)
    have = 0
    for L in legs:
        if L.kind != short_leg.kind or L.quantity <= 0:
            continue
        if short_leg.kind == "call" and L.strike >= short_leg.strike:
            have += L.quantity
        elif short_leg.kind == "put" and L.strike <= short_leg.strike:
            have += L.quantity
    return have >= need


# --------------------------------------------------------------- margin math

# Exchange SPAN parameters are proprietary and change; these are conservative
# public-domain approximations used ONLY to sanity-check sizing.
SCENARIO_MOVE_PCT = 6.0        # worst-case underlying move scanned
SCENARIO_VOL_SHIFT = 0.10      # +/- 10 vol points
EXPOSURE_MARGIN_PCT = 3.0      # broadly the exchange's additional exposure leg


def margin_estimate(legs: Sequence[Leg], underlying: float, T: float,
                    sigma: float, risk_free: float = 0.065) -> dict:
    """Scenario-based initial-margin ESTIMATE in SPAN's spirit: revalue the whole
    position across a grid of underlying and volatility shocks, take the worst
    loss, add an exposure leg.

    This is an estimate and is labelled as one everywhere. Do not use it to
    conclude a broker will accept a trade — use it to notice when a position is
    far larger than the account can support.
    """
    if not legs or underlying <= 0:
        return {"computable": False, "reason": "no legs"}

    def value(spot: float, vol: float) -> float:
        total = 0.0
        for L in legs:
            mult = L.quantity * L.lot_size
            if L.kind in ("call", "put"):
                g = black76(spot, L.strike, max(T, 1e-6), risk_free,
                            max(vol, 1e-4), L.kind)
                total += mult * (g.price if g else 0.0)
            else:
                total += mult * spot
        return total

    base = value(underlying, sigma)
    worst = 0.0
    worst_scn = None
    n = 8
    for i in range(-n, n + 1):
        move = SCENARIO_MOVE_PCT * i / n
        for dv in (-SCENARIO_VOL_SHIFT, 0.0, SCENARIO_VOL_SHIFT):
            pnl = value(underlying * (1 + move / 100.0), sigma + dv) - base
            if pnl < worst:
                worst, worst_scn = pnl, {"underlying_move_pct": round(move, 2),
                                         "vol_shift_pts": round(dv * 100, 1)}
    notional = sum(abs(L.quantity) * L.lot_size * underlying for L in legs)
    exposure = notional * EXPOSURE_MARGIN_PCT / 100.0
    span_like = abs(worst)
    return {
        "computable": True,
        "scenario_margin_estimate": round(span_like, 2),
        "exposure_margin_estimate": round(exposure, 2),
        "total_margin_estimate": round(span_like + exposure, 2),
        "worst_scenario": worst_scn,
        "position_notional": round(notional, 2),
        "scan_range": {"underlying_move_pct": SCENARIO_MOVE_PCT,
                       "vol_shift_points": SCENARIO_VOL_SHIFT * 100},
        "caveat": ("ESTIMATE ONLY. Real initial margin is set by the exchange's "
                   "SPAN + exposure calculation with proprietary, "
                   "regularly-updated risk arrays, plus broker add-ons. Use this "
                   "for sizing sanity, never as the margin a broker will block."),
    }


# ------------------------------------------------------ variance risk premium

VRP_MIN_OBS = 40
MIN_VRP_HORIZON = 5    # fewer returns than this makes realised vol meaningless


def variance_risk_premium(iv_history: Sequence[tuple], close_history: Sequence[tuple],
                          horizon_days: int = 21) -> dict:
    """Implied volatility versus the volatility that SUBSEQUENTLY realised.

    The variance risk premium is among the most robustly documented effects in
    finance (Carr & Wu 2009; Bollerslev, Tauchen & Zhou 2009): index option
    implied volatility systematically exceeds subsequent realised volatility,
    because option sellers demand compensation for bearing crash risk. Unlike
    stock-selection alpha, it is something a retail participant can actually
    harvest — and unlike most of what this platform measures, it does not require
    predicting direction.

    That is also exactly why it must be measured before it is believed. The
    premium is compensation for a REAL risk: the seller is short a fat left tail,
    and the payoff profile is many small gains punctuated by rare large losses.
    A positive average VRP and a catastrophic worst case are the same fact.

    `iv_history`   [(date, atm_iv_pct), ...]
    `close_history`[(date, close), ...] on the same underlying

    For each IV observation, realised volatility is computed over the FOLLOWING
    `horizon_days` — never overlapping the IV date itself, which would leak.
    """
    # A realised-vol estimate needs a floor of returns to be meaningful, but the
    # floor must scale with the horizon rather than sit at a constant: a fixed
    # minimum of 10 sessions made every horizon under 10 days silently produce
    # ZERO observations and then report "need more paired observations", which
    # named the wrong cause entirely.
    if horizon_days < MIN_VRP_HORIZON:
        return {"computable": False, "horizon_days": horizon_days,
                "observations": 0,
                "reason": (f"horizon of {horizon_days} sessions is below the "
                           f"{MIN_VRP_HORIZON}-session floor — realised "
                           "volatility from fewer returns is too noisy to "
                           "compare against implied")}
    closes = {d: c for d, c in close_history if c and c > 0}
    dates = sorted(closes)
    idx = {d: i for i, d in enumerate(dates)}
    rows = []
    for d, iv in sorted(iv_history):
        if iv is None or d not in idx:
            continue
        i = idx[d]
        window = dates[i:i + horizon_days + 1]
        if len(window) < max(MIN_VRP_HORIZON, horizon_days // 2):
            continue
        rets = [math.log(closes[window[k]] / closes[window[k - 1]])
                for k in range(1, len(window))
                if closes[window[k - 1]] > 0]
        if len(rets) < 5:
            continue
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
        rv = math.sqrt(max(var, 0.0)) * math.sqrt(TRADING_DAYS) * 100
        rows.append({"date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                     "implied_pct": round(float(iv), 2),
                     "realised_pct": round(rv, 2),
                     "premium_pp": round(float(iv) - rv, 2)})

    if len(rows) < VRP_MIN_OBS:
        return {"computable": False,
                "observations": len(rows),
                "reason": (f"need ≥{VRP_MIN_OBS} paired observations, have "
                           f"{len(rows)} — the IV series cannot be backfilled, so "
                           "this becomes testable only after ~2 months of daily "
                           "capture"),
                "horizon_days": horizon_days}

    prem = [r["premium_pp"] for r in rows]
    n = len(prem)
    mean = sum(prem) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in prem) / (n - 1))
    # overlapping realised-vol windows: consecutive observations share returns
    from ..research.stats import newey_west_se, t_two_sided_p
    se = newey_west_se(prem, lags=max(1, horizon_days - 1))
    t = mean / se if se and se > 0 else None
    worst = min(rows, key=lambda r: r["premium_pp"])
    return {
        "computable": True,
        "observations": n, "horizon_days": horizon_days,
        "mean_premium_pp": round(mean, 3),
        "median_premium_pp": round(sorted(prem)[n // 2], 3),
        "sd_pp": round(sd, 3),
        "share_positive_pct": round(sum(1 for x in prem if x > 0) / n * 100, 1),
        "worst_observation": worst,
        "standard_error": None if se is None else round(se, 4),
        "t_stat": None if t is None else round(t, 3),
        "p_value": None if t is None else round(t_two_sided_p(t, n - 1), 5),
        "recent": rows[-20:],
        "verdict": _vrp_verdict(mean, t, n),
        "risk_warning": (
            "A positive mean premium is COMPENSATION FOR RISK, not free money. "
            f"The worst single observation here was {worst['premium_pp']:+.1f}pp "
            f"(implied {worst['implied_pct']:.1f}% vs realised "
            f"{worst['realised_pct']:.1f}%). Harvesting this premium means "
            "selling volatility, which is short a fat left tail: many small gains "
            "and rare large losses. SEBI's own study found ~93% of individual F&O "
            "participants lost money over FY22-24, and undersized tail risk is "
            "the usual mechanism."),
        "method": ("implied vol at t versus realised vol over (t, t+h], "
                   "non-overlapping with the IV observation; Newey-West t for "
                   "the overlapping realised windows"),
        "citation": "Carr & Wu (2009), RFS; Bollerslev, Tauchen & Zhou (2009), RFS",
    }


def _vrp_verdict(mean: float, t: Optional[float], n: int) -> str:
    if t is None:
        return "not testable"
    if abs(t) < 2.0:
        return (f"no measurable premium: mean {mean:+.2f}pp over {n} observations, "
                f"t={t:+.2f}")
    if mean > 0:
        return (f"implied exceeds subsequent realised by {mean:+.2f}pp on average "
                f"(t={t:+.2f}, n={n}) — consistent with the documented variance "
                "risk premium, and the tail risk that justifies it is unchanged")
    return (f"implied UNDERSTATES subsequent realised by {mean:+.2f}pp (t={t:+.2f}) "
            "— the opposite of the documented effect; check the IV series before "
            "acting on it")
