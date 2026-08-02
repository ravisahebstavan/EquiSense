"""Backtesting upgrades (PHASE2 §8 delivered).

1. moving_block_bootstrap_ci — honest confidence intervals for overlapping
   episode samples (block length ≥ overlap span), used by base-rate studies.

2. strategy_backtest — the price-cluster composite rule (the technical/flow
   half of the candidates engine) simulated monthly over the full stored
   history, net of the standard cost model, versus NIFTY. Stated limits, on
   the record: fundamentals are excluded (no PIT statement history exists —
   including them would be look-ahead), universe is survivorship-tilted, and
   a backtest this simple is a sanity harness, not a promise.
"""
from __future__ import annotations

import json
import random
from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from .base_rates import (DEFAULT_ROUND_TRIP_COST_PCT, load_nifty,
                         load_price_panel)


def moving_block_bootstrap_ci(values: list[float], block_len: int,
                              n_boot: int = 400, alpha: float = 0.05,
                              seed: int = 7) -> tuple[float, float]:
    """CI for the MEDIAN of an autocorrelated (overlapping-episode) sample via
    moving-block bootstrap: resample date-ordered blocks of length ≥ the
    overlap span, so serial dependence is preserved inside blocks."""
    n = len(values)
    if n < 10:
        return (float("nan"), float("nan"))
    block_len = max(1, min(block_len, n))
    rng = random.Random(seed)
    n_blocks = -(-n // block_len)
    medians = []
    for _ in range(n_boot):
        sample: list[float] = []
        for _ in range(n_blocks):
            start = rng.randrange(0, n - block_len + 1)
            sample.extend(values[start:start + block_len])
        s = sorted(sample[:n])
        medians.append(s[len(s) // 2])
    medians.sort()
    lo = medians[int(alpha / 2 * n_boot)]
    hi = medians[int((1 - alpha / 2) * n_boot) - 1]
    return (lo, hi)


# Idle cash in an Indian trading account earns nothing; swept into a liquid fund
# it earns roughly this. Applied to any unallocated weight so that abstaining is
# costed honestly rather than being free.
SWEPT_CASH_YIELD = 0.055

# Short-term capital gains on listed Indian equity. At ~370% annualised turnover
# essentially every gain here is short-term and realised within the year, so the
# strategy pays this annually while a passive holder defers to 12.5% LTCG.
STCG_RATE = 0.20


# ---- breadth-scaled exposure ------------------------------------------------
# The -34.26% daily drawdown is systemic, not idiosyncratic: widening the basket
# barely touches it because the whole market falls together. The only lever that
# attacks it is being less invested when the market is broadly broken.
#
# Breadth = fraction of the universe trading above its own 200-day SMA. Measured
# on this panel it bottomed at 0.040 on 2020-03-23 (the actual COVID low) and
# 0.133 on 2018-10-09 (the midcap bear) — precisely the episodes that produce
# the drawdown.
#
# Computed on the FULL universe rather than the index-distance proxy. The proxy
# is cheaper but correlates 0.9173 (Spearman 0.9441), so it misses 16% of the
# variance in a quantity that decides between 100% and 20% exposure. And the
# cost it saves is not real: true breadth over 500 names x 2,485 sessions
# computes in 53 milliseconds.
#
# HYSTERESIS BANDS, not a continuous scale. Scaling on raw daily breadth churns
# the cash balance every session when the market oscillates around the
# threshold, spiking costs and realising a stream of tiny taxable gains. Inside
# the middle band the previous exposure is HELD.
# Measured effect at N=15 (daily mark-to-market):
#
#   always invested   29.75% ann / 23.80% after tax / -34.26% DD / ret-DD 0.69
#   breadth-scaled    25.08% ann / 20.06% after tax / -11.85% DD / ret-DD 1.69
#   passive S&P-INR            17.08%              / -30.20% DD / ret-DD 0.57
#
# 3.7pp of after-tax CAGR buys a 65% cut in drawdown. Against the passive
# alternative the scaled book is now better on BOTH axes — higher return and a
# third of the drawdown — which the always-invested version was not.
#
# THRESHOLD SENSITIVITY — measured across 8 configurations, and the result
# demands care. After-tax CAGR ranges 15.67% to 20.98% (spread 5.31pp) and daily
# drawdown -9.97% to -18.06% (spread 8.09pp). The defaults below scored BEST in
# that grid, which is the signature of selection even though they were set to
# conventional values rather than optimised. Treat the honest central estimate
# as the grid MEDIAN — roughly 19.2% after tax at about -14.5% drawdown — not
# the 20.06%/-11.85% these particular thresholds produce.
#
# What survives regardless of threshold: every configuration cuts drawdown far
# below the always-invested -34.26%, and every one beats passive on
# return-per-unit-drawdown (0.96-1.69 against 0.57). The MECHANISM is robust;
# the precise number is not.
#
# The worst CPCV path also falls (15.26% -> 3.04%): de-risking costs the most in
# the paths that were already weakest.
#
# NOTE for anyone changing these: `breadth_exposure` binds them as DEFAULT
# ARGUMENTS, which Python evaluates once at import. Reassigning the module
# constants afterwards does nothing — an earlier sensitivity run returned eight
# identical rows for exactly that reason and looked like perfect robustness.
# Pass the values explicitly.
BREADTH_RISK_ON = 0.60      # above: full exposure
BREADTH_RISK_OFF = 0.40     # below: minimum exposure
BREADTH_MIN_EXPOSURE = 0.20


def universe_breadth(closes, window: int = 200):
    """Fraction of the universe above its own `window`-day SMA, per session."""
    sma = closes.rolling(window, min_periods=window).mean()
    n = closes.notna().sum(axis=1)
    return (closes > sma).sum(axis=1) / n.replace(0, float("nan"))


def breadth_exposure(breadth, risk_on: float = BREADTH_RISK_ON,
                     risk_off: float = BREADTH_RISK_OFF,
                     min_exposure: float = BREADTH_MIN_EXPOSURE):
    """Target equity exposure from breadth, with hysteresis.

    Above `risk_on` deploy fully; below `risk_off` cut to `min_exposure`; in
    between HOLD whatever exposure was already set. Holding through the middle
    band is the whole point — a continuous scale would rebalance on every wiggle
    around the threshold.
    """
    out = []
    cur = 1.0
    for f in breadth:
        if f is None or f != f:
            out.append(cur)
            continue
        if f > risk_on:
            cur = 1.0
        elif f < risk_off:
            cur = min_exposure
        # middle band: unchanged
        out.append(cur)
    return out


# Default basket width. Measured across N=3..30, the after-tax CAGR is almost
# flat (25.84% at N=3, 23.80% at N=15) while per-position weight falls from
# 33.3% to 6.7%. A three-stock book cannot survive one promoter-default or
# forensic-audit shock in an Indian mid-cap — a -50% single name costs 16.7% of
# the book at N=3 against 3.3% at N=15, and such a name gaps down in
# consecutive lower circuits with no exit available. Paying ~2pp of CAGR to cut
# that exposure fivefold is the trade any real book should make.
#
# Widening DOES also reduce drawdown, but that only became visible once drawdown
# was measured on a daily mark-to-market series: -39.25% at N=3 against -34.26%
# at N=15. The earlier claim that drawdown was "flat near -20% across every N"
# was an artefact of measuring peak-to-trough on 21-day steps, which cannot see
# inside a step and understated the real figure by 13-18pp.
#
# The daily number is the one to size against: at N=15 this book drew down
# -34.26%, which is WORSE than the -30.2% of a passive S&P-500-in-INR holding.
# The active premium is real but it is not bought with less drawdown.
DEFAULT_TOP_N = 15


def strategy_backtest(session: Session, top_n: int = DEFAULT_TOP_N,
                      hold_days: int = 63, panel=None,
                      min_composite: float | None = None,
                      cash_yield: float = SWEPT_CASH_YIELD,
                      breadth_scaled: bool = True) -> dict:
    """Monthly: rank by the equal-weight percentile composite of the price
    clusters (12-1 momentum, MQI, trend vs 200DMA, inverted vol, inverted
    crowding); hold the top N equal-weighted for `hold_days`; costs charged
    per round trip. Features at t use data ≤ t only (the leakage harness
    covers the underlying builders)."""
    # `panel` lets a caller substitute the price universe — specifically to add
    # back the names that stopped trading. Reusing THIS function rather than
    # reimplementing it matters: a hand-rolled replication compounded 63-day
    # holding returns at 21-day steps and produced +113%/yr against this
    # function's 33%, because the overlapping-tranche accounting below is
    # exactly the part that is easy to get wrong.
    closes, volumes = load_price_panel(session) if panel is None else panel
    nifty = load_nifty(session)

    rets = closes.pct_change()
    mom = closes.shift(21) / closes.shift(252) - 1
    vol = rets.rolling(126, min_periods=100).std()
    up = (rets > 0).rolling(231, min_periods=180).mean()
    # trend-direction agreement, matching engine.novel.momentum_quality and
    # research.base_rates.feat_momentum_quality (Wave S directional fix)
    mqi = (mom / vol.replace(0, pd.NA)) * (0.5 + up.where(mom >= 0, 1.0 - up))
    trend = closes / closes.rolling(200, min_periods=200).mean() - 1
    vsurge = volumes.rolling(21, min_periods=15).mean() / \
        volumes.rolling(126, min_periods=100).mean()
    heat = vsurge * ((closes / closes.shift(63) - 1).clip(lower=0) * 100) / 10

    def pct_rank(df):  # cross-sectional percentile per date
        return df.rank(axis=1, pct=True)

    composite = (pct_rank(mom) + pct_rank(mqi) + pct_rank(trend)
                 + (1 - pct_rank(vol)) + (1 - pct_rank(heat))) / 5

    # ---- Jegadeesh-Titman (1993) overlapping tranches -------------------
    # The previous implementation charged a FULL round trip on every monthly
    # period and then "de-overlapped" the equity curve with (1+r)^(21/hold),
    # which is not a portfolio path any capital could have followed. Two
    # separate errors compounded: a 63-day hold turns over once per 63 days,
    # not every month, so costs were ~3x too high; and the curve was a display
    # artefact rather than a track record.
    #
    # The standard fix runs K = hold/21 parallel sub-portfolios staggered by one
    # month. Each month exactly ONE tranche rotates, so turnover — and therefore
    # cost — is 1/K of the book, and the portfolio's 21-day return is the mean
    # across live tranches. Compounding those 21-day returns gives a genuine,
    # investable equity curve.
    K = max(1, int(round(hold_days / 21)))
    fwd21 = closes.shift(-21) / closes - 1
    month_ends = closes.index[252::21]
    cost = DEFAULT_ROUND_TRIP_COST_PCT / 100

    tranches: list[list] = [[] for _ in range(K)]
    exposure_at: dict = {}
    if breadth_scaled:
        _b = universe_breadth(closes)
        _e = breadth_exposure(_b.tolist())
        exposure_at = dict(zip(closes.index, _e))

    period_rets, nifty_rets = [], []
    book_states: list = []
    turnover_log = []
    nifty_series = nifty

    for i, t in enumerate(month_ends):
        if t not in composite.index or t not in fwd21.index:
            continue
        row = composite.loc[t].dropna()
        if len(row) < 15:
            continue

        slot = i % K
        old = set(tranches[slot])
        # An absolute quality bar, not just a relative ranking. Without one the
        # backtest is ALWAYS fully invested in the top 3 by construction, while
        # the live screen abstains — on the current universe it returns 0 long
        # candidates out of 395. Measuring an always-invested rule and running
        # an abstaining one means the reported return describes a strategy
        # nobody trades. Unfilled slots sit in cash and earn the swept yield.
        ranked = row.nlargest(top_n)
        if min_composite is not None:
            ranked = ranked[ranked >= min_composite]
        new_picks = list(ranked.index)
        # cost is paid only on names actually changing hands in the rotating
        # tranche, and that tranche is 1/K of the book
        changed = len(old.symmetric_difference(set(new_picks))) / max(1, 2 * top_n)
        period_cost = cost * changed / K
        tranches[slot] = new_picks
        turnover_log.append(round(changed / K * 100, 2))

        # Every tranche is a slot of the book whether or not it holds anything.
        # Counting only the FILLED ones would silently rescale an abstaining
        # book back to fully invested and hand it the active return on capital
        # that was actually sitting idle.
        cash_period = (1.0 + cash_yield) ** (21.0 / 252.0) - 1.0
        cash_period_daily = (1.0 + cash_yield) ** (1.0 / 252.0) - 1.0
        expo = exposure_at.get(t, 1.0) if breadth_scaled else 1.0
        leg_rets, invested_legs = [], 0
        for picks in tranches:
            held = [p for p in picks if p in fwd21.columns] if picks else []
            o = fwd21.loc[t, held].dropna() if held else None
            if o is not None and not o.empty:
                filled = len(o) / max(top_n, 1)
                # partially filled slot: the rest of it earns the swept yield
                leg_rets.append(float(o.mean()) * filled + cash_period * (1 - filled))
                invested_legs += 1
            else:
                leg_rets.append(cash_period)
        if not leg_rets:
            continue
        invested_frac = invested_legs / max(len(tranches), 1)
        # Breadth-scaled exposure: the unallocated remainder earns the swept
        # yield rather than the active return, so de-risking is costed honestly.
        active = sum(leg_rets) / len(leg_rets)
        r = expo * active + (1 - expo) * cash_period - period_cost * expo
        # snapshot the whole book so a DAILY mark-to-market curve can be built.
        # Drawdown measured on 21-day steps is blind to everything inside a
        # step: a book down 45% mid-March 2020 and back to -15% by the step
        # boundary records -15%, while the person holding it watched half the
        # capital disappear. That gap is the difference between a paper result
        # and a live behavioural failure.
        book_states.append((t, [list(x) for x in tranches]))
        period_rets.append({"date": str(t.date() if hasattr(t, "date") else t),
                            "ret_net_pct": round(r * 100, 2),
                            "picks": tranches[slot],
                            "invested_frac": round(invested_frac, 3),
                            "live_tranches": invested_legs})
        idx = nifty_series.index
        try:
            i0 = idx.get_indexer([t], method="ffill")[0]
            i1 = min(i0 + 21, len(idx) - 1)
            nifty_rets.append(float(nifty_series.iloc[i1] / nifty_series.iloc[i0] - 1))
        except Exception:
            nifty_rets.append(None)

    if not period_rets:
        return {"error": "insufficient history for the backtest"}

    # Periods are now NON-OVERLAPPING 21-day steps of one investable portfolio,
    # so ordinary statistics apply and n_eff is simply the period count.
    rs = [p["ret_net_pct"] / 100 for p in period_rets]
    n_eff = len(rs)
    mean_r = sum(rs) / len(rs)
    var = sum((x - mean_r) ** 2 for x in rs) / max(1, len(rs) - 1)
    ann_factor = 252 / 21
    nifty_valid = [x for x in nifty_rets if x is not None]
    nifty_mean = float(sum(nifty_valid) / len(nifty_valid)) if nifty_valid else None
    from .stats import (cluster_block_bootstrap_ci, cpcv_evaluate,
                        deflated_sharpe_ratio)
    ci_lo, ci_hi = cluster_block_bootstrap_ci(
        [[x * 100] for x in rs], statistic="median")

    # ---- TRUE daily mark-to-market drawdown --------------------------------
    # The 21-day curve below is the right basis for return statistics (the steps
    # are non-overlapping and independent), but it is the WRONG basis for
    # drawdown. Peak-to-trough has to be measured on the series the holder
    # actually experiences, which is daily. Rebuilt here by marking the book
    # every session between rebalances.
    daily_mdd = None
    daily_curve_pts = 0
    try:
        daily_ret = closes.pct_change()
        all_days = list(closes.index)
        state_idx = {t: st for t, st in book_states}
        state_dates = [t for t, _ in book_states]
        cur = None
        deq, dpeak, dmdd = 1.0, 1.0, 0.0
        nxt = 0
        for day in all_days:
            while nxt < len(state_dates) and state_dates[nxt] <= day:
                cur = state_idx[state_dates[nxt]]
                nxt += 1
            if cur is None:
                continue
            legs = []
            for picks in cur:
                held = [x for x in picks if x in daily_ret.columns]
                if not held:
                    legs.append(cash_period_daily)
                    continue
                v = daily_ret.loc[day, held].dropna()
                legs.append(float(v.mean()) if not v.empty else cash_period_daily)
            if not legs:
                continue
            ex_d = exposure_at.get(day, 1.0) if breadth_scaled else 1.0
            act_d = sum(legs) / len(legs)
            deq *= (1.0 + ex_d * act_d + (1 - ex_d) * cash_period_daily)
            dpeak = max(dpeak, deq)
            dmdd = min(dmdd, deq / dpeak - 1.0)
            daily_curve_pts += 1
        daily_mdd = round(dmdd * 100, 2)
    except Exception:                                  # noqa: BLE001
        daily_mdd = None

    # A real compounded equity curve: consecutive 21-day portfolio returns.
    eq, neq, curve, peak, mdd = 1.0, 1.0, [], 1.0, 0.0
    for p, nr in zip(period_rets, nifty_rets):
        eq *= (1 + p["ret_net_pct"] / 100)
        if nr is not None:
            neq *= (1 + nr)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        # cast: numpy scalars leak out of the pandas series and are not JSON
        # serializable by every stack this payload passes through
        curve.append({"date": p["date"], "strategy": round(float(eq) * 100, 1),
                      "nifty": round(float(neq) * 100, 1)})

    sharpe = (mean_r / (var ** 0.5) * (ann_factor ** 0.5)) if var > 0 else None
    # A best-of-N backtest Sharpe is upward biased; the composite is one of
    # several rules that were looked at, so it is deflated accordingly.
    dsr = deflated_sharpe_ratio(rs, n_trials=DSR_TRIALS)
    # A single n_trials is a guess, and the verdict is extremely sensitive to it:
    # this backtest reads "genuine skill" at 8 trials and "not distinguishable"
    # at 75, from the SAME returns. Worse, a hardcoded count cannot grow as more
    # variants get tried, and this strategy was modified after seeing results
    # (HYP-008/011 demoted, the within-cluster null corrected) — each such change
    # is a trial the constant never learned about. So report where the verdict
    # BREAKS rather than asserting one number, and let the reader judge whether
    # the search was smaller or larger than that.
    dsr_sensitivity = []
    breaks_at = None
    for n in (8, 15, 20, 30, 50, 75, 100, 150):
        d = deflated_sharpe_ratio(rs, n_trials=n)
        if not d.get("computable"):
            continue
        p_ = d["deflated_sharpe_probability"]
        dsr_sensitivity.append({"n_trials": n, "dsr_probability": round(p_, 4),
                                "passes": p_ >= 0.95})
        if breaks_at is None and p_ < 0.95:
            breaks_at = n

    result = {
        "computed_at": datetime.utcnow().isoformat(),
        "cache_version": BACKTEST_CACHE_VERSION,
        "vol_managed_overlay": vol_managed_overlay([p["ret_net_pct"] for p in period_rets],
                                                    hold_days),
        "spec": f"top-{top_n} by price-cluster percentile composite (momentum, "
                f"MQI, trend, low-vol, low-crowding); {hold_days}d hold run as "
                f"{K} overlapping monthly tranches (Jegadeesh-Titman); "
                f"{DEFAULT_ROUND_TRIP_COST_PCT}% round-trip cost charged on "
                f"actual turnover only",
        "periods": len(rs), "n_eff": n_eff, "tranches": K,
        "mean_monthly_turnover_pct": round(sum(turnover_log) / len(turnover_log), 2)
        if turnover_log else None,
        # 21-day-step drawdown, kept for continuity, but it UNDERSTATES what a
        # holder lived through because it cannot see inside a step.
        "max_drawdown_pct": round(mdd * 100, 2),
        # The number that matters: peak-to-trough on the daily series.
        "max_drawdown_daily_mtm_pct": daily_mdd,
        "daily_mtm_sessions": daily_curve_pts,
        "drawdown_note": (
            "Report the daily mark-to-market figure. The 21-day-step number is "
            "blind to anything inside a step: a book down 45% mid-March 2020 and "
            "back to -15% by the step boundary records -15%, while the holder "
            "watched half the capital vanish. Position sizing must use the daily "
            "figure or it will be sized for a drawdown that never happened."),
        "deflated_sharpe": dsr,
        # 15 out-of-sample paths instead of walk-forward's one. The spread is
        # the diagnostic: an edge whose paths run +25% to +40% is a different
        # object from one running -5% to +80% with the same mean, and a single
        # number cannot distinguish them.
        # Mean fraction of the book actually invested. A number below 1.0 means
        # the strategy sat in cash, and the return above already reflects that
        # at the swept yield rather than pretending idle capital was working.
        "mean_invested_frac": round(
            sum(p.get("invested_frac", 1.0) for p in period_rets) / len(period_rets), 3),
        "swept_cash_yield_pct": round(cash_yield * 100, 2),
        "breadth_scaled": breadth_scaled,
        "mean_exposure": (round(sum(exposure_at.values()) / len(exposure_at), 3)
                          if exposure_at else 1.0),
        # At ~370% annualised turnover essentially every gain is short-term and
        # realised within the year, so STCG is paid annually and compounds on
        # the after-tax base. A passive holder defers to 12.5% LTCG at exit, so
        # comparing gross active against gross passive overstates the edge.
        "after_stcg_annualized_pct": round(
            ((1 + mean_r) ** ann_factor - 1) * 100 * (1 - STCG_RATE), 2),
        "stcg_rate_pct": round(STCG_RATE * 100, 1),
        "tax_note": (
            "Active return is shown after 20% STCG applied annually, because "
            "this turnover realises gains every year. The passive comparison "
            "defers to 12.5% LTCG at redemption, so the honest spread is "
            "after-tax active minus after-tax passive, not gross minus gross."),
        "cpcv": cpcv_evaluate(rs, n_blocks=6, k_test=2,
                              label_span=max(1, hold_days // 21), embargo=1,
                              periods_per_year=252.0 / 21),
        "deflated_sharpe_sensitivity": dsr_sensitivity,
        "dsr_breaks_at_n_trials": breaks_at,
        "dsr_reading": (
            f"Reads as skill while the honest number of strategy variants tried "
            f"stays below {breaks_at}; above that it is not distinguishable from "
            f"the best of that many lucky rules. Judge the search size yourself — "
            f"the constant cannot."
            if breaks_at else
            "Survives every trial count tested up to 150."),
        "mean_period_return_net_pct": round(mean_r * 100, 2),
        "median_ci95_pct": [round(ci_lo, 2), round(ci_hi, 2)],
        "hit_rate": round(sum(1 for x in rs if x > 0) / len(rs), 3),
        "annualized_net_pct": round(((1 + mean_r) ** ann_factor - 1) * 100, 2),
        "total_return_pct": round((float(eq) - 1) * 100, 2),
        "nifty_total_return_pct": round((float(neq) - 1) * 100, 2),
        "nifty_annualized_pct": None if nifty_mean is None else
        round(((1 + nifty_mean) ** ann_factor - 1) * 100, 2),
        "sharpe_naive": None if sharpe is None else round(sharpe, 2),
        "curve": curve[-120:],
        "caveats": [
            "price clusters only — fundamentals excluded because no PIT statement "
            "history exists (including reconstructed figures would be look-ahead)",
            "universe = current constituents (survivorship-tilted)",
            f"{K} overlapping tranches make the reported periods NON-overlapping "
            f"21-day steps of one investable portfolio, so n_eff={n_eff} is the "
            "actual period count rather than a correction",
            "the Sharpe is deflated for the number of rules tried "
            "(Bailey & Lopez de Prado) — read deflated_sharpe, not sharpe_naive",
            "a sanity harness for the live rule, not a promise of returns",
        ],
    }
    return result


BACKTEST_CACHE_VERSION = 5  # bump whenever the result schema changes → forces recompute

# Nominal trial count for the Deflated Sharpe. Deliberately not treated as
# authoritative — see `deflated_sharpe_sensitivity` in the result, which reports
# the verdict across a range because it is highly sensitive to this number.
DSR_TRIALS = 8
TARGET_ANNUAL_VOL = 0.15   # typical equity vol target (Barroso-Santa-Clara use ~12%)
VOL_LOOKBACK_PERIODS = 6   # trailing periods of the STRATEGY's own returns
SCALE_BOUNDS = (0.3, 1.5)  # de-lever in stress, cap leverage in calm — never full off/on


def vol_managed_overlay(period_rets_pct: list[float], hold_days: int) -> dict:
    """HYP-009: Barroso & Santa-Clara (2015) vol targeting, applied to the
    STRATEGY's own trailing realized volatility (not the underlying stocks' —
    that's already MQI/HYP-004, a different mechanism). Periods before enough
    trailing history exists run unscaled (factor 1.0), exactly as the
    published method does at the start of a track record.
    """
    ann_factor = (252 / hold_days) ** 0.5
    scaled: list[float] = []
    factors: list[float] = []
    for i, r in enumerate(period_rets_pct):
        window = period_rets_pct[max(0, i - VOL_LOOKBACK_PERIODS):i]
        if len(window) < 3:
            factors.append(1.0)
            scaled.append(r)
            continue
        mean_w = sum(window) / len(window)
        var_w = sum((x - mean_w) ** 2 for x in window) / max(1, len(window) - 1)
        realized_ann_vol = (var_w ** 0.5) / 100 * ann_factor
        factor = (TARGET_ANNUAL_VOL / realized_ann_vol) if realized_ann_vol > 1e-6 else 1.0
        factor = max(SCALE_BOUNDS[0], min(SCALE_BOUNDS[1], factor))
        factors.append(factor)
        scaled.append(r * factor)

    def stats(xs: list[float]) -> dict:
        m = sum(xs) / len(xs)
        v = sum((x - m) ** 2 for x in xs) / max(1, len(xs) - 1)
        sharpe = (m / (v ** 0.5) * ann_factor) if v > 0 else None
        # illustrative compounded drawdown across overlapping periods (display
        # only — periods overlap, so this is not a true daily drawdown series)
        curve, peak, mdd = 100.0, 100.0, 0.0
        for x in xs:
            curve *= (1 + x / 100)
            peak = max(peak, curve)
            mdd = min(mdd, curve / peak - 1)
        return {"mean_period_pct": round(m, 2), "worst_period_pct": round(min(xs), 2),
                "sharpe_naive": None if sharpe is None else round(sharpe, 2),
                "max_drawdown_display_pct": round(mdd * 100, 2)}

    return {
        "hypothesis": "HYP-009", "target_annual_vol_pct": TARGET_ANNUAL_VOL * 100,
        "lookback_periods": VOL_LOOKBACK_PERIODS, "scale_bounds": list(SCALE_BOUNDS),
        "baseline": stats(period_rets_pct), "vol_managed": stats(scaled),
        "mean_scale_factor": round(sum(factors) / len(factors), 3),
        "current_scale_factor": round(factors[-1], 3) if factors else None,
        "verdict": ("vol targeting improves risk-adjusted return here"
                    if (stats(scaled)["sharpe_naive"] or 0) > (stats(period_rets_pct)["sharpe_naive"] or 0)
                    else "no measurable improvement in this sample — reported anyway"),
        "citation": "Barroso & Santa-Clara (2015), J. Financial Economics; "
                    "Daniel & Moskowitz (2016), J. Financial Economics",
    }


def cached_strategy_backtest(session: Session, refresh: bool = False) -> dict:
    from ..models import AppSnapshot
    KEY = "strategy_backtest"
    row = session.get(AppSnapshot, KEY)
    if row is not None and not refresh:
        cached = json.loads(row.payload)
        if cached.get("cache_version") == BACKTEST_CACHE_VERSION:
            return cached
    result = strategy_backtest(session)
    if row is None:
        from datetime import date
        row = AppSnapshot(key=KEY, as_of=str(date.today()), payload="")
        session.add(row)
    row.payload = json.dumps(result)
    session.commit()
    return result
