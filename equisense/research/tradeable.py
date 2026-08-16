"""Does a signal survive contact with an account someone can actually run? (§12)

THE GAP THIS CLOSES
-------------------
`factor_portfolio.py` measures a top-QUINTILE book. On a 500-name universe that
is ~80 positions, which is not a personal portfolio, and its excess is not the
excess anyone would earn. `backtest.py` measures the platform's own composite
strategy. Neither answers the question that decides whether any of this is worth
doing: **hold the top N names on one signal, rebalance monthly, pay real costs —
does it beat the alternative, and by how much, and how often does it not?**

THREE CHOICES HERE ARE LOAD-BEARING
-----------------------------------
**The benchmark is an equal-weighted book of the SAME eligible names**, not
NIFTY. This is the difference between a defensible number and a flattering one.
Measured on this panel the equal-weighted eligible universe compounded at
~19%/yr while NIFTY did ~9%/yr, so quoting excess over NIFTY credits the signal
with ~21pp when roughly half of that is owning Indian mid- and small-caps
through a decade in which they ran hard. That half is size beta, it is available
from an index fund, and it is additionally inflated by survivorship — the panel
is today's index membership backfilled, so names that died along the way are
largely absent. Both legs of the equal-weight comparison are drawn from the same
biased set, so the bias substantially cancels in the difference. NIFTY is still
reported, and clearly labelled as the contaminated comparison.

**Costs are charged on realised turnover, including an impact estimate.** The
statutory round trip is only part of what a fill costs. Impact is an assumption
rather than a measurement, so it is a parameter and the sensitivity is reported:
a result that survives 0.2% but not 0.5% is a result about the cost model.

**A liquidity floor applies before ranking, using only trailing data.** Without
it the top of a momentum ranking fills with names that cannot absorb an order,
and the backtest earns returns nobody could have taken.

WHAT IT DOES NOT DO
-------------------
No stop losses, no vol targeting, no regime filter, no position sizing. Every
one of those is a further choice fitted on the same decade, and the point of
this module is to isolate whether the RANKING carries information that survives
costs. Overlays belong downstream, measured against this as the baseline.
"""
from __future__ import annotations

from datetime import date

import numpy as np
from sqlalchemy.orm import Session

# Statutory NSE round trip (STT, stamp, exchange, SEBI, GST) — the same figure
# the factor study uses, so the two are comparable.
STATUTORY_ROUND_TRIP = 0.002211
# Additional round-trip slippage. An assumption, not a measurement: reported as
# a parameter and swept, never buried in the headline.
DEFAULT_IMPACT = 0.0015
# Median trailing daily traded value a name must clear to be rankable.
MIN_TRADED_VALUE = 5e7          # ₹5 crore

REBALANCE_DAYS = 21
TRADING_DAYS = 252


def _newey_west_t(x: np.ndarray, lags: int) -> float:
    """t-statistic of a mean under overlap-induced autocorrelation."""
    n = len(x)
    if n < 6:
        return float("nan")
    e = x - x.mean()
    tot = float(e @ e) / n
    for L in range(1, max(0, lags) + 1):
        tot += 2.0 * (1.0 - L / (lags + 1.0)) * float(e[L:] @ e[:-L]) / n
    return float(x.mean() / np.sqrt(tot / n)) if tot > 0 else float("nan")


def _max_drawdown(equity: np.ndarray) -> float:
    return float((1.0 - equity / np.maximum.accumulate(equity)).max())


def momentum_signal(P: np.ndarray, t: int, lookback: int = 252,
                    skip: int = 21) -> np.ndarray:
    """12-1 momentum at index `t`, using only bars at or before `t`.

    The one-month skip is not decoration: Jegadeesh & Titman's construction
    excludes the most recent month because short-horizon reversal runs the other
    way and contaminates the ranking. Dropping the skip measurably weakens this.
    """
    return P[t - skip] / P[t - lookback] - 1.0


def run_tradeable_backtest(closes, traded_value, benchmark_close=None,
                           basket: int = 20,
                           impact: float = DEFAULT_IMPACT,
                           min_traded_value: float = MIN_TRADED_VALUE,
                           start_index: int | None = None,
                           end_index: int | None = None,
                           signal_fn=momentum_signal) -> dict:
    """One concentrated long-only book against its equal-weighted control.

    `closes` and `traded_value` are (sessions x names) arrays on a shared grid;
    `benchmark_close` is an optional index level series on the same grid.
    """
    P = np.asarray(closes, dtype=float)
    TV = np.asarray(traded_value, dtype=float)
    T, N = P.shape
    lo = TRADING_DAYS + 1 if start_index is None else max(start_index, TRADING_DAYS + 1)
    hi = (T - 1) if end_index is None else min(end_index, T - 1)
    if hi - lo < 6 * REBALANCE_DAYS:
        return {"computable": False,
                "reason": f"need ≥{6 * REBALANCE_DAYS} sessions, have {max(0, hi - lo)}"}

    cost_rt = STATUTORY_ROUND_TRIP + impact
    strat, equal, bench = [1.0], [1.0], [1.0]
    turnovers: list[float] = []
    held: set[int] = set()
    steps = 0
    t = lo
    while t + REBALANCE_DAYS <= hi:
        sig = signal_fn(P, t)
        # Liquidity measured on the trailing year as of the formation date. Any
        # forward-looking filter here would silently select the names that went
        # on to trade heavily, which is a return-predicting variable.
        liq = np.nanmedian(TV[t - TRADING_DAYS:t], axis=0)
        ok = (np.isfinite(sig) & np.isfinite(P[t]) & (P[t] > 0)
              & np.isfinite(liq) & (liq > min_traded_value))
        idx = np.where(ok)[0]
        if len(idx) < basket * 2:
            t += REBALANCE_DAYS
            continue
        pick = set(idx[np.argsort(sig[idx])[-basket:]].tolist())
        turnover = 1.0 - len(held & pick) / len(pick) if held else 1.0
        turnovers.append(turnover)
        held = pick

        nxt = min(t + REBALANCE_DAYS, hi)
        r_pick = P[nxt][list(pick)] / P[t][list(pick)] - 1.0
        r_pick = r_pick[np.isfinite(r_pick)]
        r_all = P[nxt][idx] / P[t][idx] - 1.0
        r_all = r_all[np.isfinite(r_all)]
        if len(r_pick) == 0 or len(r_all) == 0:
            t += REBALANCE_DAYS
            continue
        strat.append(strat[-1] * (1.0 + float(r_pick.mean()) - turnover * cost_rt))
        # The control is charged a small drift turnover rather than nothing: an
        # equal-weighted book still rebalances and still pays for entry and exit.
        equal.append(equal[-1] * (1.0 + float(r_all.mean()) - 0.02 * cost_rt))
        if benchmark_close is not None:
            b0, b1 = benchmark_close[t], benchmark_close[nxt]
            bench.append(bench[-1] * (b1 / b0)
                         if np.isfinite(b0) and np.isfinite(b1) and b0 > 0
                         else bench[-1])
        steps += 1
        t += REBALANCE_DAYS

    if steps < 6:
        return {"computable": False, "reason": f"only {steps} rebalances completed"}

    eq = np.array(strat)
    ew = np.array(equal)
    nb = np.array(bench)
    years = steps * REBALANCE_DAYS / TRADING_DAYS
    cagr = lambda a: (a[-1] ** (1.0 / years) - 1.0) * 100.0     # noqa: E731

    r_s = eq[1:] / eq[:-1] - 1.0
    r_w = ew[1:] / ew[:-1] - 1.0
    active = r_s - r_w
    per_yr = TRADING_DAYS / REBALANCE_DAYS
    sharpe = float(r_s.mean() / r_s.std(ddof=1) * np.sqrt(per_yr)) if r_s.std(ddof=1) > 0 else None
    ir = float(active.mean() / active.std(ddof=1) * np.sqrt(per_yr)) if active.std(ddof=1) > 0 else None

    out = {
        "computable": True,
        "basket": basket,
        "rebalances": steps,
        "years": round(years, 2),
        "impact_assumed_pct": round(impact * 100, 3),
        "round_trip_cost_pct": round(cost_rt * 100, 3),
        "mean_turnover": round(float(np.mean(turnovers)), 3),
        "strategy_cagr_pct": round(cagr(eq), 2),
        "equal_weight_cagr_pct": round(cagr(ew), 2),
        "excess_vs_equal_weight_pp": round(cagr(eq) - cagr(ew), 2),
        "active_t_stat": round(_newey_west_t(active, 2), 2),
        "information_ratio": None if ir is None else round(ir, 2),
        "sharpe": None if sharpe is None else round(sharpe, 2),
        "max_drawdown_pct": round(_max_drawdown(eq) * 100, 1),
        "equal_weight_max_drawdown_pct": round(_max_drawdown(ew) * 100, 1),
        "periods_beating_control_pct": round(float((active > 0).mean()) * 100, 1),
    }
    if benchmark_close is not None:
        out["benchmark_cagr_pct"] = round(cagr(nb), 2)
        out["excess_vs_benchmark_pp"] = round(cagr(eq) - cagr(nb), 2)
        out["benchmark_caveat"] = (
            "Excess over the index is NOT the signal's contribution. The "
            "equal-weighted control captures the size and breadth exposure that "
            "an index fund does not, and on this panel that component is worth "
            "roughly as much as the selection itself. Read "
            "excess_vs_equal_weight_pp; the index figure is reported only "
            "because it is the number people expect to see.")
    out["survivorship_caveat"] = (
        "The panel is today's index membership backfilled, so names that "
        "delisted or were dropped are largely absent and every ABSOLUTE return "
        "here is inflated. The strategy and its control are drawn from the same "
        "biased set, so the DIFFERENCE between them is far more trustworthy "
        "than either level.")
    return out


def validate_signal(session: Session, baskets=(10, 15, 20, 30, 50),
                    impacts=(0.0, 0.0015, 0.005, 0.01),
                    split_out_of_sample: bool = True) -> dict:
    """Full validation sweep over the stored panel: sizes, costs, and a split.

    The out-of-sample half is the part that matters. Everything else on this
    page was measured on the same decade that suggested measuring it, and a
    concentrated basket has enough free parameters — how many names, which
    horizon, which liquidity floor — that an in-sample number alone is worth
    very little.
    """
    from ..models import Company, MacroObservation, PriceObservation
    from sqlalchemy import select
    import pandas as pd

    stmt = (select(PriceObservation.company_id, PriceObservation.obs_date,
                   PriceObservation.close, PriceObservation.close_raw,
                   PriceObservation.volume)
            .where(PriceObservation.source != "demo"))
    df = pd.read_sql(stmt, session.connection())
    df.columns = ["cid", "date", "close", "close_raw", "volume"]
    if df.empty:
        return {"computable": False, "reason": "no measured price history"}
    closes = df.pivot_table(index="date", columns="cid", values="close").sort_index()
    df["tv"] = df["close_raw"] * df["volume"]
    tvf = (df.pivot_table(index="date", columns="cid", values="tv")
             .sort_index().reindex(columns=closes.columns))

    nif = session.execute(
        select(MacroObservation.obs_date, MacroObservation.close)
        .where(MacroObservation.symbol == "^NSEI")
        .order_by(MacroObservation.obs_date)).all()
    bench = None
    if nif:
        bs = pd.Series({d: c for d, c in nif})
        bench = bs.reindex(closes.index).ffill().to_numpy(dtype=float)

    P, TV = closes.to_numpy(dtype=float), tvf.to_numpy(dtype=float)
    T = P.shape[0]
    out: dict = {"computable": True, "sessions": T, "names": P.shape[1],
                 "panel_from": str(closes.index[0]), "panel_to": str(closes.index[-1]),
                 "by_basket": {}, "cost_sensitivity": {}}

    for b in baskets:
        full = run_tradeable_backtest(P, TV, bench, basket=b)
        entry = {"full_period": full}
        if split_out_of_sample:
            entry["out_of_sample_second_half"] = run_tradeable_backtest(
                P, TV, bench, basket=b, start_index=T // 2)
        out["by_basket"][b] = entry

    for imp in impacts:
        out["cost_sensitivity"][f"{imp * 100:.2f}%"] = run_tradeable_backtest(
            P, TV, bench, basket=20, impact=imp)

    out["method"] = (
        f"Top-{baskets} by 12-1 momentum, rebalanced every {REBALANCE_DAYS} "
        f"sessions, liquidity floor ₹{MIN_TRADED_VALUE / 1e7:.0f}cr median "
        "trailing traded value, costs charged on realised turnover. Benchmarked "
        "against an equal-weighted book of the same eligible names.")
    return out
