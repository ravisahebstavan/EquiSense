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


def strategy_backtest(session: Session, top_n: int = 3,
                      hold_days: int = 63) -> dict:
    """Monthly: rank by the equal-weight percentile composite of the price
    clusters (12-1 momentum, MQI, trend vs 200DMA, inverted vol, inverted
    crowding); hold the top N equal-weighted for `hold_days`; costs charged
    per round trip. Features at t use data ≤ t only (the leakage harness
    covers the underlying builders)."""
    closes, volumes = load_price_panel(session)
    nifty = load_nifty(session)

    rets = closes.pct_change()
    mom = closes.shift(21) / closes.shift(252) - 1
    vol = rets.rolling(126, min_periods=100).std()
    up = (rets > 0).rolling(231, min_periods=180).mean()
    mqi = (mom / vol.replace(0, pd.NA)) * (0.5 + up)
    trend = closes / closes.rolling(200, min_periods=200).mean() - 1
    vsurge = volumes.rolling(21, min_periods=15).mean() / \
        volumes.rolling(126, min_periods=100).mean()
    heat = vsurge * ((closes / closes.shift(63) - 1).clip(lower=0) * 100) / 10

    def pct_rank(df):  # cross-sectional percentile per date
        return df.rank(axis=1, pct=True)

    composite = (pct_rank(mom) + pct_rank(mqi) + pct_rank(trend)
                 + (1 - pct_rank(vol)) + (1 - pct_rank(heat))) / 5

    month_ends = closes.index[252::21]
    fwd = closes.shift(-hold_days) / closes - 1
    cost = DEFAULT_ROUND_TRIP_COST_PCT / 100
    trades, period_rets, nifty_rets = [], [], []
    nifty_series = nifty
    for t in month_ends:
        if t not in composite.index or t not in fwd.index:
            continue
        row = composite.loc[t].dropna()
        if len(row) < 15:
            continue
        picks = row.nlargest(top_n).index
        outcome = fwd.loc[t, picks].dropna()
        if outcome.empty:
            continue
        r = float(outcome.mean()) - cost
        period_rets.append({"date": str(t.date() if hasattr(t, "date") else t),
                            "ret_net_pct": round(r * 100, 2),
                            "picks": list(picks)})
        # nifty same window
        idx = nifty_series.index
        try:
            i0 = idx.get_indexer([t], method="ffill")[0]
            i1 = min(i0 + hold_days, len(idx) - 1)
            nifty_rets.append(nifty_series.iloc[i1] / nifty_series.iloc[i0] - 1)
        except Exception:
            nifty_rets.append(None)
        trades.extend(picks)

    if not period_rets:
        return {"error": "insufficient history for the backtest"}
    rs = [p["ret_net_pct"] / 100 for p in period_rets]
    n_eff = max(1, len(rs) * 21 // hold_days)  # overlapping monthly starts
    mean_r = sum(rs) / len(rs)
    var = sum((x - mean_r) ** 2 for x in rs) / max(1, len(rs) - 1)
    ann_factor = 252 / hold_days
    nifty_valid = [x for x in nifty_rets if x is not None]
    nifty_mean = sum(nifty_valid) / len(nifty_valid) if nifty_valid else None
    ci_lo, ci_hi = moving_block_bootstrap_ci(
        [x * 100 for x in rs], block_len=max(1, hold_days // 21))

    # equity curves (overlapping periods compounded naively for display only)
    eq, neq, curve = 1.0, 1.0, []
    for p, nr in zip(period_rets, nifty_rets):
        eq *= (1 + p["ret_net_pct"] / 100) ** (21 / hold_days)  # de-overlap approx
        if nr is not None:
            neq *= (1 + nr) ** (21 / hold_days)
        curve.append({"date": p["date"], "strategy": round(eq * 100, 1),
                      "nifty": round(neq * 100, 1)})

    result = {
        "computed_at": datetime.utcnow().isoformat(),
        "spec": f"monthly top-{top_n} by price-cluster percentile composite "
                f"(momentum, MQI, trend, low-vol, low-crowding), {hold_days}d hold, "
                f"{DEFAULT_ROUND_TRIP_COST_PCT}% round-trip cost",
        "periods": len(rs), "n_eff": n_eff,
        "mean_period_return_net_pct": round(mean_r * 100, 2),
        "median_ci95_pct": [round(ci_lo, 2), round(ci_hi, 2)],
        "hit_rate": round(sum(1 for x in rs if x > 0) / len(rs), 3),
        "annualized_net_pct": round(((1 + mean_r) ** ann_factor - 1) * 100, 2),
        "nifty_annualized_pct": None if nifty_mean is None else
        round(((1 + nifty_mean) ** ann_factor - 1) * 100, 2),
        "sharpe_naive": round(mean_r / (var ** 0.5) * (ann_factor ** 0.5), 2)
        if var > 0 else None,
        "curve": curve[-120:],
        "caveats": [
            "price clusters only — fundamentals excluded because no PIT statement "
            "history exists (including reconstructed figures would be look-ahead)",
            "universe = current constituents (survivorship-tilted)",
            f"overlapping {hold_days}d windows from monthly starts — read "
            f"significance against n_eff={n_eff}, and the CI is on the median "
            "period return",
            "a sanity harness for the live rule, not a promise of returns",
        ],
    }
    return result


def cached_strategy_backtest(session: Session, refresh: bool = False) -> dict:
    from ..models import AppSnapshot
    KEY = "strategy_backtest"
    row = session.get(AppSnapshot, KEY)
    if row is not None and not refresh:
        return json.loads(row.payload)
    result = strategy_backtest(session)
    if row is None:
        from datetime import date
        row = AppSnapshot(key=KEY, as_of=str(date.today()), payload="")
        session.add(row)
    row.payload = json.dumps(result)
    session.commit()
    return result
