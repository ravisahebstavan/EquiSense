"""Quantile-portfolio evaluation of a factor — what it PAYS, not just how it ranks.

The IC study answers "does this factor order names correctly?" That is
necessary and not sufficient. Three things it cannot tell you, each of which
has sunk real strategies:

  1. **How much money.** A rank correlation is unitless. The tradeable quantity
     is the spread between the top and bottom baskets, in percent.
  2. **Whether the ordering is real.** A factor can post a good IC while only
     its extreme decile behaves, with the middle ranked backwards. That is the
     signature of a few outliers, not of a monotone effect, and it does not
     survive contact with a live book.
  3. **Whether the edge survives its own turnover.** Harvesting a signal means
     replacing part of the basket every rebalance, and in India that costs
     ~0.22% statutory round trip before impact. A factor earning 4%/yr gross at
     80% turnover per month is a losing strategy, and IC cannot see it.

This module measures all three from the same feature builders the IC and
base-rate studies use, so a factor cannot pass under one definition and be
traded under another.

Convention: rebalance every `sampling_days`, hold `horizon_days`. When the
holding period exceeds the rebalance interval the book runs
`horizon_days / sampling_days` overlapping tranches (Jegadeesh-Titman), so only
1/k of capital turns over on any date — turnover is divided accordingly rather
than being counted k times.
"""
from __future__ import annotations

import math
from typing import Optional

from .ic import spearman
from .stats import newey_west_se, t_two_sided_p

# India delivery-equity round trip, from the sizing engine's named constants.
# Imported rather than restated so a tax change moves both together.
from ..engine.sizing import ROUND_TRIP_STATUTORY

MIN_NAMES_PER_DATE = 30      # 5 quantiles needs enough names to fill each
MIN_DATES = 24               # below this the spread mean is not testable
DEFAULT_QUANTILES = 5


def _quantile_buckets(values: dict[str, float], n_quantiles: int
                      ) -> Optional[dict[int, list[str]]]:
    """Split names into `n_quantiles` equal-count buckets, 1 = lowest factor
    value. Ties are broken by the sort, which is unavoidable and harmless here
    because bucket membership, not exact rank, is what gets traded."""
    ranked = sorted(values.items(), key=lambda kv: kv[1])
    n = len(ranked)
    if n < n_quantiles * 2:
        return None
    out: dict[int, list[str]] = {}
    for q in range(n_quantiles):
        lo = q * n // n_quantiles
        hi = (q + 1) * n // n_quantiles
        out[q + 1] = [t for t, _v in ranked[lo:hi]]
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None and x == x]
    return sum(xs) / len(xs) if xs else None


def _median(xs):
    xs = sorted(x for x in xs if x is not None and x == x)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def factor_quantile_study(by_date, horizon_days: int,
                          sampling_days: int = 21,
                          n_quantiles: int = DEFAULT_QUANTILES,
                          round_trip_cost: float = ROUND_TRIP_STATUTORY,
                          ) -> dict:
    """`by_date` = [(date, {name: factor_value}, {name: forward_return}), ...].

    Forward returns are SIMPLE returns over `horizon_days`, matching the IC
    study's inputs exactly.
    """
    dates, per_date_q, spreads, buckets_seq, name_counts = [], [], [], [], []
    med_spreads: list[float] = []
    for dt, values, forwards in sorted(by_date, key=lambda r: r[0]):
        common = {n: v for n, v in values.items()
                  if n in forwards and v is not None and v == v
                  and forwards[n] is not None and forwards[n] == forwards[n]}
        if len(common) < MIN_NAMES_PER_DATE:
            continue
        buckets = _quantile_buckets(common, n_quantiles)
        if buckets is None:
            continue
        qmeans = {q: _mean([forwards[t] for t in names])
                  for q, names in buckets.items()}
        if any(v is None for v in qmeans.values()):
            continue
        qmeds = {q: _median([forwards[t] for t in names])
                 for q, names in buckets.items()}
        med_spreads.append(qmeds[n_quantiles] - qmeds[1])
        dates.append(dt)
        name_counts.append(sum(len(v) for v in buckets.values()))
        per_date_q.append(qmeans)
        spreads.append(qmeans[n_quantiles] - qmeans[1])
        buckets_seq.append(buckets)

    if len(dates) < MIN_DATES:
        return {"computable": False,
                "reason": f"only {len(dates)} usable dates, need ≥{MIN_DATES}"}

    n = len(spreads)
    mean_spread = sum(spreads) / n
    # Overlapping holding windows autocorrelate the spread series exactly as
    # they autocorrelate IC, so the same HAC correction applies.
    lag = max(0, horizon_days // sampling_days - 1)
    se = newey_west_se(spreads, lags=lag)
    # A zero standard error means the spread series has no variance at all. That
    # is degenerate, not insignificant, and returning t=0 would report a large
    # constant spread as "no effect" — the wrong direction to fail in. Say
    # undefined instead.
    t_stat = (mean_spread / se) if (se and se > 0) else None

    q_means = {q: _mean([d[q] for d in per_date_q]) for q in range(1, n_quantiles + 1)}

    # The MEDIAN spread, alongside the mean, because equity returns are fat-tailed
    # and the difference is not academic. Measured on this universe for the
    # low-volatility factor: the mean spread is -18.6%/yr while the median spread
    # is +2.3%/yr — the SIGN FLIPS. The entire mean effect was a handful of
    # enormous winners in one bucket. A mean-only report would have presented
    # that as a large tradeable edge when the typical name does the opposite, and
    # a small book cannot concentrate into the extreme tail that carries it.
    mean_med_spread = _mean(med_spreads) or 0.0
    tail_driven = (mean_spread != 0
                   and (mean_med_spread == 0
                        or (mean_med_spread / mean_spread) < 0.5))

    # Monotonicity: does the ordering hold ACROSS the quantiles, or is the
    # factor carried entirely by its extremes? A high IC with a non-monotone
    # profile is a small number of outliers wearing a factor's clothes.
    mono = spearman(list(range(1, n_quantiles + 1)),
                    [q_means[q] for q in range(1, n_quantiles + 1)])

    # Turnover of the two traded baskets. A name leaving the basket is sold and
    # its replacement bought, so the fraction replaced carries a full round trip.
    turnovers = []
    for prev, cur in zip(buckets_seq, buckets_seq[1:]):
        for q in (1, n_quantiles):
            a, b = set(prev[q]), set(cur[q])
            if b:
                turnovers.append(1.0 - len(a & b) / len(b))
    turnover = _mean(turnovers) or 0.0

    # Only 1/k of the book rebalances on any date when tranches overlap.
    k = max(1, horizon_days // sampling_days)
    effective_turnover = turnover / k

    rebalances_per_year = 252.0 / sampling_days
    periods_per_year = 252.0 / horizon_days
    gross_annual = mean_spread * periods_per_year * 100.0
    # both legs of a long-short spread trade, hence the factor of 2
    cost_annual = effective_turnover * round_trip_cost * rebalances_per_year * 2 * 100.0
    net_annual = gross_annual - cost_annual

    return {
        "computable": True,
        "horizon_days": horizon_days,
        "sampling_days": sampling_days,
        "n_quantiles": n_quantiles,
        "dates": n,
        "mean_names_per_date": round(sum(name_counts) / n, 1),
        "quantile_mean_return_pct": {q: round(v * 100, 4) for q, v in q_means.items()},
        "spread_mean_pct": round(mean_spread * 100, 4),
        "spread_median_pct": round(mean_med_spread * 100, 4),
        "tail_driven": bool(tail_driven),
        "spread_se_pct": round(se * 100, 4) if se else None,
        "spread_t_stat": None if t_stat is None else round(t_stat, 3),
        "spread_p_value": (None if t_stat is None
                           else round(t_two_sided_p(t_stat, max(n - 1, 1)), 5)),
        "newey_west_lag": lag,
        "hit_rate": round(sum(1 for s in spreads if s > 0) / n, 4),
        "monotonicity": None if mono is None else round(mono, 3),
        "turnover_per_rebalance": round(turnover, 4),
        "effective_turnover": round(effective_turnover, 4),
        "gross_annual_pct": round(gross_annual, 3),
        "cost_annual_pct": round(cost_annual, 3),
        "net_annual_pct": round(net_annual, 3),
        "round_trip_cost_pct": round(round_trip_cost * 100, 4),
        "verdict": _verdict(mean_spread, t_stat, mono, net_annual, gross_annual,
                            mean_med_spread, tail_driven),
        "caveat":
            "Costs are the STATUTORY round trip only (STT, stamp, exchange, SEBI); "
            "market impact and any bid-ask crossing are additional and grow with "
            "position size. Long-short is measured for factor evaluation — a "
            "long-only book earns roughly the top-quantile leg against the "
            "benchmark, not the full spread, and shorting single stocks in India "
            "is not available in the cash segment.",
    }


def _verdict(mean_spread, t_stat, mono, net_annual, gross_annual,
             median_spread=None, tail_driven=False) -> str:
    if t_stat is None:
        return (f"spread {mean_spread * 100:+.2f}% per period, but its variance is "
                "zero so no t-test is defined — check the inputs, real data does "
                "not do this")
    if abs(t_stat) < 2.0:
        return (f"no reliable spread: top-minus-bottom {mean_spread * 100:+.2f}% "
                f"per period, t={t_stat:+.2f}")
    parts = [f"spread {mean_spread * 100:+.2f}% per period (t={t_stat:+.2f}), "
             f"{gross_annual:+.1f}%/yr gross"]
    if mono is not None and mono < 0.7:
        parts.append(f"but the quantile profile is NOT monotone (rank corr "
                     f"{mono:+.2f}) — the effect is concentrated in the extremes "
                     f"rather than ordering the universe")
    if net_annual <= 0 < gross_annual:
        parts.append(f"and turnover consumes all of it: {net_annual:+.1f}%/yr net")
    else:
        parts.append(f"{net_annual:+.1f}%/yr net of statutory costs")
    if tail_driven and median_spread is not None:
        parts.append(
            f"WARNING: the MEDIAN spread is {median_spread * 100:+.2f}% per period "
            f"against a mean of {mean_spread * 100:+.2f}%, so the effect lives in "
            "the tail of one bucket rather than in the typical name. A small book "
            "cannot concentrate into that tail, and rank-based IC will not see it")
    return "; ".join(parts)


def long_only_leg(by_date, horizon_days: int, sampling_days: int = 21,
                  n_quantiles: int = DEFAULT_QUANTILES,
                  round_trip_cost: float = ROUND_TRIP_STATUTORY) -> dict:
    """What a LONG-ONLY Indian cash book actually earns from this factor.

    The long-short spread is the right way to EVALUATE a factor and the wrong
    number to trade on here: single-stock shorting is not available in the NSE
    cash segment, so the short leg is unreachable. What is reachable is the top
    quantile held against the universe. Measured on this panel that leg is worth
    56-78% of the long-short figure depending on the factor (momentum 12-1: 65%
    at 63d), so the tradeable number is materially smaller than the spread but
    not the "less than half" the textbook framing would suggest — worth stating
    as a measurement rather than an assumption, because it is the number the
    account actually earns.

    Costs are charged ONE-sided (one basket, not two), which is the only thing
    that goes in the trader's favour here.
    """
    dates, excess, buckets_seq, med_excess = [], [], [], []
    for dt, values, forwards in sorted(by_date, key=lambda r: r[0]):
        common = {n: v for n, v in values.items()
                  if n in forwards and v is not None and v == v
                  and forwards[n] is not None and forwards[n] == forwards[n]}
        if len(common) < MIN_NAMES_PER_DATE:
            continue
        buckets = _quantile_buckets(common, n_quantiles)
        if buckets is None:
            continue
        top = [forwards[t] for t in buckets[n_quantiles]]
        universe = [forwards[t] for t in common]
        # equal-weighted universe return is the benchmark a retail book is
        # realistically choosing between: this factor, or simply owning the lot
        bench_mean, bench_med = _mean(universe), _median(universe)
        t_mean, t_med = _mean(top), _median(top)
        if None in (bench_mean, t_mean):
            continue
        dates.append(dt)
        excess.append(t_mean - bench_mean)
        med_excess.append((t_med or 0.0) - (bench_med or 0.0))
        buckets_seq.append(buckets)

    if len(dates) < MIN_DATES:
        return {"computable": False,
                "reason": f"only {len(dates)} usable dates, need >={MIN_DATES}"}

    n = len(excess)
    mean_ex = sum(excess) / n
    lag = max(0, horizon_days // sampling_days - 1)
    se = newey_west_se(excess, lags=lag)
    t_stat = (mean_ex / se) if (se and se > 0) else None

    turns = []
    for prev, cur in zip(buckets_seq, buckets_seq[1:]):
        a, b = set(prev[n_quantiles]), set(cur[n_quantiles])
        if b:
            turns.append(1.0 - len(a & b) / len(b))
    turnover = _mean(turns) or 0.0
    k = max(1, horizon_days // sampling_days)
    eff_turn = turnover / k

    periods_per_year = 252.0 / horizon_days
    rebalances_per_year = 252.0 / sampling_days
    gross = mean_ex * periods_per_year * 100.0
    # ONE basket trades, so no factor of 2
    cost = eff_turn * round_trip_cost * rebalances_per_year * 100.0
    return {
        "computable": True, "horizon_days": horizon_days, "dates": n,
        "excess_mean_pct": round(mean_ex * 100, 4),
        "excess_median_pct": round((_mean(med_excess) or 0.0) * 100, 4),
        "t_stat": None if t_stat is None else round(t_stat, 3),
        "hit_rate": round(sum(1 for e in excess if e > 0) / n, 4),
        "turnover_per_rebalance": round(turnover, 4),
        "gross_annual_pct": round(gross, 3),
        "cost_annual_pct": round(cost, 3),
        "net_annual_pct": round(gross - cost, 3),
        "benchmark": "equal-weighted universe",
        "caveat":
            "Excess over an EQUAL-WEIGHTED UNIVERSE, long-only, one-sided costs. "
            "This is the tradeable figure for an NSE cash account; the long-short "
            "spread reported alongside is a factor-evaluation number requiring a "
            "short leg the cash segment does not offer. "
            "The benchmark choice is not cosmetic. Measured on this panel the "
            "equal-weighted universe returned ~24.9%/yr while NIFTY returned "
            "~11.7%/yr, so quoting excess over NIFTY would credit this factor "
            "with ~23%/yr when roughly 13pp of that is simply owning Indian "
            "mid/small caps through a decade in which they ran hard — size beta, "
            "not momentum alpha. "
            "The absolute returns are additionally survivorship-inflated: the "
            "panel is TODAY'S index membership backfilled, and only one departed "
            "constituent is retained, so names that fell out over the decade are "
            "largely absent. The EXCESS is far more trustworthy than either "
            "absolute leg, because the top quantile and the benchmark are drawn "
            "from the same biased set and the bias largely cancels in the "
            "difference. Read the excess; do not read the level.",
    }


def factor_autocorrelation(by_date, n_quantiles: int = DEFAULT_QUANTILES) -> dict:
    """Rank autocorrelation of the factor between consecutive rebalances.

    Low autocorrelation means the factor reshuffles the universe every period,
    which is turnover, which is cost. It is the cheapest early warning that a
    signal will not survive its own trading.
    """
    rows = sorted(by_date, key=lambda r: r[0])
    acs = []
    for (_d1, v1, _f1), (_d2, v2, _f2) in zip(rows, rows[1:]):
        common = [n for n in v1 if n in v2
                  and v1[n] is not None and v1[n] == v1[n]
                  and v2[n] is not None and v2[n] == v2[n]]
        if len(common) < MIN_NAMES_PER_DATE:
            continue
        c = spearman([v1[n] for n in common], [v2[n] for n in common])
        if c is not None:
            acs.append(c)
    if not acs:
        return {"computable": False, "reason": "no consecutive dates overlap"}
    mean_ac = sum(acs) / len(acs)
    return {
        "computable": True, "periods": len(acs),
        "mean_autocorrelation": round(mean_ac, 4),
        "reading": (
            "highly persistent — low turnover, cheap to hold" if mean_ac > 0.9 else
            "persistent — moderate turnover" if mean_ac > 0.7 else
            "reshuffles substantially each period — turnover will be a material "
            "drag, check the net-of-cost figure before trading it" if mean_ac > 0.3
            else "almost no persistence — this reprices the whole universe every "
                 "rebalance and is very unlikely to survive costs"),
    }


def run_factor_studies(session, horizons=(21, 63, 126),
                       sampling_days: int = 21) -> dict:
    """Quantile-portfolio evaluation of every registered feature.

    Deliberately built on the SAME builders as run_ic_studies, so the two views
    describe one object: IC says whether a factor ranks, this says what it pays.
    Where they disagree the disagreement is informative — rank IC is blind to
    fat tails and mean spreads are dominated by them, which is why the median
    spread and the tail_driven flag travel with every result.
    """
    import pandas as pd

    from .base_rates import (STUDIES, feat_max_effect,
                             feat_sector_relative_momentum, load_price_panel,
                             load_sector_map)

    closes, volumes = load_price_panel(session)
    if closes.empty or closes.shape[1] < MIN_NAMES_PER_DATE:
        return {"computable": False,
                "reason": f"need >={MIN_NAMES_PER_DATE} names with price history"}
    sector_map = load_sector_map(session)
    # Release the READ transaction before the long computation. The first
    # SELECT above opens a transaction and it stays open until commit/rollback,
    # so the minutes of compute that follow are minutes of idle-in-transaction —
    # Postgres kills that ("IdleInTransactionSessionTimeout") and the write at
    # the end fails. Moving only the write was not enough; the transaction opens
    # at the first read, not at the first write.
    session.rollback()

    builders = {hyp: cfg["feature"] for hyp, cfg in STUDIES.items()}
    builders["HYP-010"] = lambda c, v: feat_sector_relative_momentum(c, v, sector_map)
    builders["HYP-011"] = feat_max_effect

    month_ends = closes.index[::sampling_days]
    results: dict[str, dict] = {}
    for hyp, build in builders.items():
        try:
            feat = build(closes, volumes)
        except Exception as exc:                       # noqa: BLE001
            results[hyp] = {"computable": False, "reason": f"builder failed: {exc}"}
            continue
        if feat is None or getattr(feat, "empty", True):
            results[hyp] = {"computable": False, "reason": "empty feature frame"}
            continue
        if feat.dtypes.astype(str).str.contains("bool").any():
            results[hyp] = {"computable": False,
                            "reason": "boolean cohort feature — no ranking to bucket"}
            continue

        by_h = {}
        for h in horizons:
            fwd = closes.shift(-h) / closes - 1
            rows = []
            for t in month_ends:
                if t not in feat.index or t not in fwd.index:
                    continue
                frow, orow = feat.loc[t], fwd.loc[t]
                # pd.notna, not `v == v` — the NA sentinel raises on self-inequality
                sig = {k: float(v) for k, v in frow.items() if pd.notna(v)}
                ret = {k: float(v) for k, v in orow.items() if pd.notna(v)}
                if sig and ret:
                    rows.append((t, sig, ret))
            ls = factor_quantile_study(rows, h, sampling_days)
            lo = long_only_leg(rows, h, sampling_days)
            by_h[h] = {"long_short": ls, "long_only": lo}
        ac_rows = []
        for t in month_ends:
            if t not in feat.index:
                continue
            frow = feat.loc[t]
            ac_rows.append((t, {k: float(v) for k, v in frow.items()
                                if pd.notna(v)}, {}))
        results[hyp] = {"computable": True, "by_horizon": by_h,
                        "autocorrelation": factor_autocorrelation(ac_rows)}

    tradeable = sorted(
        h for h, r in results.items() if r.get("computable") and any(
            (v["long_only"].get("net_annual_pct") or 0) > 0
            and abs(v["long_only"].get("t_stat") or 0) >= 2.0
            and not v["long_short"].get("tail_driven", False)
            for v in r["by_horizon"].values()))
    return {
        "computable": True, "signals": results,
        "universe": int(closes.shape[1]), "history_days": int(closes.shape[0]),
        "tradeable_long_only": tradeable,
        "note": ("Long-only is the tradeable figure for an NSE cash account; the "
                 "long-short spread is a factor-evaluation number requiring a "
                 "short leg the cash segment does not offer. Excess is measured "
                 "against the EQUAL-WEIGHTED universe, not NIFTY: the panel is "
                 "today's index membership backfilled and returns ~12pp/yr more "
                 "than the published NIFTY 500, so absolute levels are "
                 "survivorship-inflated and only the excess is trustworthy."),
    }
