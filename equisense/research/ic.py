"""Information Coefficient — does a signal actually predict, and for how long?

WHY THIS EXISTS
---------------
The synthesis plane weights every evidence cluster uniformly until the
calibration ledger accumulates ≥150 scored claims per cluster. At the rate live
dossiers get scored that is years away, and in the meantime a signal with no
predictive power carries exactly as much weight as one that works.

But ten years of history is already stored. The Information Coefficient
(Grinold & Kahn, *Active Portfolio Management*) is the standard way to ask that
history a direct question: **what is the cross-sectional rank correlation
between a signal today and the forward return?** Averaged over time, its mean
and stability are the two numbers that decide whether a signal deserves weight.

THE INFERENCE IS FAMA-MACBETH, NOT POOLED
------------------------------------------
IC is computed per DATE across names, producing a time series of cross-sectional
correlations. Testing the mean of that series is exactly the Fama-MacBeth (1973)
procedure, and it is the right one here: it sidesteps the same-date clustering
that makes pooled regression wrong, because each date contributes exactly one
observation by construction.

Overlapping forward windows still induce serial correlation in the IC series, so
the t-statistic uses a Newey-West HAC standard error with lag = overlap span.
Without it, a 126-day forward return sampled monthly would overstate
significance by roughly √6.

PURGING AND EMBARGO
-------------------
IC-derived weights are a fitted quantity. Using IC computed over the whole
history to weight signals evaluated on that same history is in-sample fitting
dressed as evidence. `walk_forward_ic` therefore computes IC on a training
window and applies it only to a strictly later test window, with an EMBARGO gap
of one full forward horizon between them — otherwise the last training
observation's forward window overlaps the first test observation, which leaks.

WHAT IC IS NOT
--------------
An IC of 0.03 is normal and useful for a real signal; an IC of 0.30 on daily
equity data means a bug, not an edge. The module reports the IC's own t-stat and
refuses to weight on samples too thin to distinguish either case.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from .stats import newey_west_se, t_two_sided_p

MIN_NAMES_PER_DATE = 10       # a rank correlation over fewer names is noise
MIN_DATES = 24                # below this the IC mean is not testable
IC_IMPLAUSIBLE = 0.25         # |IC| above this on EOD equity data means a bug


def _rank(values: Sequence[float]) -> list[float]:
    """Average ranks, ties shared — the standard Spearman treatment."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Rank correlation. Used rather than Pearson because the signals are
    already percentile-normalised and because rank is robust to the fat tails
    that dominate single-name equity returns."""
    n = len(x)
    if n < 3 or n != len(y):
        return None
    rx, ry = _rank(x), _rank(y)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def ic_series(by_date: Sequence[tuple]) -> list[tuple]:
    """[(date, {name: signal}, {name: forward_return}), ...] → [(date, ic), ...].

    One IC per date, computed only over names present in BOTH maps on that date.
    Dates with too few overlapping names are dropped rather than contributing a
    noisy correlation.
    """
    out = []
    for dt, signals, forwards in sorted(by_date, key=lambda r: r[0]):
        common = [n for n in signals
                  if n in forwards
                  and signals[n] is not None and forwards[n] is not None]
        if len(common) < MIN_NAMES_PER_DATE:
            continue
        ic = spearman([signals[n] for n in common], [forwards[n] for n in common])
        if ic is not None:
            out.append((dt, ic, len(common)))
    return out


def evaluate_ic(by_date: Sequence[tuple], horizon_days: int,
                sampling_days: int = 21) -> dict:
    """Full IC diagnostic for one signal at one horizon.

    Returns the mean IC, its Newey-West t-statistic, the Information Ratio
    (mean/sd — the signal's own consistency), the hit rate of IC sign, and an
    explicit verdict on whether the signal has earned any weight.
    """
    series = ic_series(by_date)
    if len(series) < MIN_DATES:
        return {"computable": False,
                "reason": f"need ≥{MIN_DATES} dates with ≥{MIN_NAMES_PER_DATE} "
                          f"names, have {len(series)}",
                "dates": len(series)}
    ics = [ic for _d, ic, _n in series]
    n = len(ics)
    mean = sum(ics) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in ics) / (n - 1)) if n > 1 else 0.0

    # Overlapping forward windows correlate consecutive ICs; NW with lag =
    # overlap span is the standard correction.
    lag = max(0, math.ceil(horizon_days / sampling_days) - 1)
    se = newey_west_se(ics, lags=lag) if lag > 0 else (sd / math.sqrt(n) if sd else None)
    t = (mean / se) if se and se > 0 else None
    p = t_two_sided_p(t, n - 1) if t is not None else None

    implausible = abs(mean) > IC_IMPLAUSIBLE
    verdict = _verdict(mean, t, p, implausible, n)
    return {
        "computable": True,
        "horizon_days": horizon_days,
        "dates": n,
        "mean_ic": round(mean, 5),
        "ic_sd": round(sd, 5),
        "information_ratio": round(mean / sd, 4) if sd > 0 else None,
        "newey_west_lag": lag,
        "standard_error": None if se is None else round(se, 5),
        "t_stat": None if t is None else round(t, 3),
        "p_value": None if p is None else round(p, 5),
        "ic_hit_rate": round(sum(1 for x in ics if x > 0) / n, 3),
        "mean_names_per_date": round(sum(k for _d, _i, k in series) / n, 1),
        "implausible": implausible,
        "verdict": verdict,
        "method": ("per-date cross-sectional Spearman IC (Fama-MacBeth), "
                   "Newey-West HAC t-stat for overlapping forward windows"),
        "caveat": ("An IC of 0.02-0.05 is normal and useful for a real equity "
                   "signal. |IC| above 0.25 on EOD data indicates a leak or a "
                   "bug, not an edge — check the feature builder before "
                   "believing it."),
    }


def _verdict(mean: float, t: Optional[float], p: Optional[float],
             implausible: bool, n: int) -> str:
    if implausible:
        return (f"IMPLAUSIBLE (mean IC {mean:+.3f}) — suspect look-ahead in the "
                "feature builder rather than a genuine edge")
    if t is None:
        return "not testable"
    if p is not None and p < 0.05 and abs(t) >= 2.0:
        return (f"predictive: mean IC {mean:+.4f}, t={t:+.2f} over {n} dates "
                "(Newey-West corrected)")
    return (f"no detectable predictive power: mean IC {mean:+.4f}, t={t:+.2f} "
            f"over {n} dates")


def ic_decay(by_date_by_horizon: dict[int, Sequence[tuple]],
             sampling_days: int = 21) -> dict:
    """IC across several forward horizons → the signal's half-life.

    The practical use is holding period. A signal whose IC has decayed to noise
    by 63 days should not be held for 126; one still strong at 126 is being
    traded too often. Turnover is a cost, so this is a direct P&L question, not
    a curiosity.
    """
    points = {}
    for h, rows in sorted(by_date_by_horizon.items()):
        ev = evaluate_ic(rows, h, sampling_days)
        if ev.get("computable"):
            points[h] = {"mean_ic": ev["mean_ic"], "t_stat": ev["t_stat"],
                         "dates": ev["dates"]}
    if len(points) < 2:
        return {"computable": False, "reason": "need ≥2 horizons with enough data"}

    horizons = sorted(points)
    peak_h = max(horizons, key=lambda h: abs(points[h]["mean_ic"]))
    peak = abs(points[peak_h]["mean_ic"])
    half_life = None
    for h in horizons:
        if h > peak_h and abs(points[h]["mean_ic"]) <= peak / 2:
            half_life = h
            break
    return {
        "computable": True, "by_horizon": points,
        "peak_horizon_days": peak_h, "peak_ic": round(peak, 5),
        "half_life_days": half_life,
        "reading": (
            f"IC peaks at {peak_h}d"
            + (f" and halves by {half_life}d — holding much past that is paying "
               "turnover for decayed signal"
               if half_life else
               " and has not halved within the horizons tested, so the signal "
               "may support a longer hold than currently used")),
    }


def walk_forward_ic(by_date: Sequence[tuple], horizon_days: int,
                    train_dates: int = 36, sampling_days: int = 21) -> dict:
    """Out-of-sample IC with purging and an embargo.

    Weighting signals by an IC computed over the whole history and then
    evaluating on that same history is in-sample fitting. This splits the IC
    series into expanding train windows and strictly-later test windows, with an
    EMBARGO of one full forward horizon between them.

    The embargo is not optional. Without it the last training date's forward
    window still overlaps the first test date, so training outcomes and test
    outcomes share returns — the leak that makes most published backtests
    optimistic.
    """
    series = ic_series(by_date)
    embargo = max(1, math.ceil(horizon_days / sampling_days))
    if len(series) < train_dates + embargo + MIN_DATES // 2:
        return {"computable": False,
                "reason": (f"need ≥{train_dates + embargo + MIN_DATES // 2} dates "
                           f"for a purged split, have {len(series)}"),
                "embargo_dates": embargo}
    ics = [ic for _d, ic, _n in series]
    folds = []
    start = train_dates
    while start + embargo < len(ics):
        train = ics[:start]
        test = ics[start + embargo:start + embargo + train_dates]
        if len(test) < 6:
            break
        tr_mean = sum(train) / len(train)
        te_mean = sum(test) / len(test)
        folds.append({"train_end": start, "train_ic": round(tr_mean, 5),
                      "test_ic": round(te_mean, 5),
                      "sign_agrees": (tr_mean > 0) == (te_mean > 0)})
        start += train_dates
    if not folds:
        return {"computable": False, "reason": "no usable folds after purging",
                "embargo_dates": embargo}
    oos = [f["test_ic"] for f in folds]
    agree = sum(1 for f in folds if f["sign_agrees"]) / len(folds)
    return {
        "computable": True,
        "folds": folds, "n_folds": len(folds),
        "embargo_dates": embargo,
        "mean_oos_ic": round(sum(oos) / len(oos), 5),
        "sign_agreement_rate": round(agree, 3),
        "verdict": (
            "IC sign is stable out of sample" if agree >= 0.7 else
            "IC sign flips out of sample — the in-sample IC is not evidence"),
        "method": (f"expanding train / {train_dates}-date test, embargo "
                   f"{embargo} sample dates (= one {horizon_days}d forward "
                   "window) to prevent outcome overlap between train and test"),
    }


def ic_weights(ic_by_signal: dict[str, dict],
               floor: float = 0.0, cap: float = 2.0) -> dict:
    """Signal weights from IC, normalised to mean 1.0.

    Weight ∝ mean_IC × (1 if the IC is statistically distinguishable from zero,
    else 0). Squaring IC — a common suggestion — is wrong here: it discards the
    SIGN, and a signal with a reliably negative IC is informative (invert it),
    not weightless.

    Signals whose IC fails its own t-test get weight zero rather than a small
    weight, because a weight is a claim of predictive power and a failed test is
    the absence of that claim.
    """
    raw: dict[str, float] = {}
    for name, ev in ic_by_signal.items():
        if not ev.get("computable") or ev.get("implausible"):
            raw[name] = 0.0
            continue
        t = ev.get("t_stat")
        mean = ev.get("mean_ic") or 0.0
        raw[name] = abs(mean) if (t is not None and abs(t) >= 2.0) else 0.0
    live = {k: v for k, v in raw.items() if v > 0}
    if not live:
        return {"weights": {k: 1.0 for k in raw},
                "status": ("no signal passed its IC t-test — weights stay UNIFORM, "
                           "which is the honest default when nothing has "
                           "demonstrated predictive power"),
                "raw_ic": {k: (ic_by_signal[k] or {}).get("mean_ic") for k in raw}}
    avg = sum(live.values()) / len(live)
    weights = {k: max(floor, min(cap, (v / avg) if v > 0 else 0.0))
               for k, v in raw.items()}
    return {"weights": weights,
            "status": (f"{len(live)} of {len(raw)} signals passed their IC t-test "
                       "and carry weight; the rest are zeroed"),
            "raw_ic": {k: (ic_by_signal[k] or {}).get("mean_ic") for k in raw}}


# --------------------------------------------------------------- study runner

def run_ic_studies(session, horizons: Sequence[int] = (21, 63, 126),
                   sampling_days: int = 21) -> dict:
    """IC for every registered feature against the platform's own price history.

    Reuses the SAME feature builders the base-rate studies use, so a signal
    cannot pass here under one definition and be traded under another — the bug
    that made the MQI base rate describe a different construction from the one
    the dossier displayed.
    """
    import pandas as pd

    from .base_rates import (STUDIES, feat_max_effect,
                             feat_sector_relative_momentum, load_price_panel,
                             load_sector_map)

    closes, volumes = load_price_panel(session)
    if closes.empty or closes.shape[1] < MIN_NAMES_PER_DATE:
        return {"computable": False,
                "reason": f"need ≥{MIN_NAMES_PER_DATE} names with price history"}
    sector_map = load_sector_map(session)

    builders = {hyp: cfg["feature"] for hyp, cfg in STUDIES.items()}
    builders["HYP-010"] = lambda c, v: feat_sector_relative_momentum(c, v, sector_map)
    builders["HYP-011"] = feat_max_effect

    month_ends = closes.index[::sampling_days]
    results: dict[str, dict] = {}
    for hyp, build in builders.items():
        try:
            feat = build(closes, volumes)
        except Exception as exc:                   # noqa: BLE001
            results[hyp] = {"computable": False, "reason": f"builder failed: {exc}"}
            continue
        if feat is None or getattr(feat, "empty", True):
            results[hyp] = {"computable": False, "reason": "empty feature frame"}
            continue
        # boolean features carry no cross-sectional ordering, so a rank IC over
        # them is degenerate — reported rather than silently computed
        if feat.dtypes.astype(str).str.contains("bool").any():
            results[hyp] = {"computable": False,
                            "reason": "boolean cohort feature — no ranking to correlate"}
            continue

        per_horizon = {}
        for h in horizons:
            fwd = closes.shift(-h) / closes - 1
            rows = []
            for t in month_ends:
                if t not in feat.index or t not in fwd.index:
                    continue
                frow, orow = feat.loc[t], fwd.loc[t]
                sig = {k: float(v) for k, v in frow.items()
                       if v is not None and v == v}
                ret = {k: float(v) for k, v in orow.items()
                       if v is not None and v == v}
                if sig and ret:
                    rows.append((t, sig, ret))
            per_horizon[h] = rows
        evals = {h: evaluate_ic(rows, h, sampling_days)
                 for h, rows in per_horizon.items()}
        best_h = max(horizons, key=lambda h: abs(
            (evals[h].get("mean_ic") or 0) if evals[h].get("computable") else 0))
        results[hyp] = {
            "computable": True,
            "by_horizon": {h: e for h, e in evals.items()},
            "best_horizon": best_h,
            "decay": ic_decay(per_horizon, sampling_days),
            "walk_forward": walk_forward_ic(per_horizon[best_h], best_h),
        }
    passing = {h: r for h, r in results.items()
               if r.get("computable")
               and (r["by_horizon"][r["best_horizon"]].get("t_stat") or 0)
               and abs(r["by_horizon"][r["best_horizon"]]["t_stat"]) >= 2.0}
    return {"computable": True, "signals": results,
            "universe": int(closes.shape[1]), "history_days": int(closes.shape[0]),
            "passing_ic_t_test": sorted(passing),
            "note": ("IC is measured on the SAME feature builders the base-rate "
                     "studies use, so a signal cannot pass here under one "
                     "definition and trade under another.")}
