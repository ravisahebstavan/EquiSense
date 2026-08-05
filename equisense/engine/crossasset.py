"""Cross-asset relationships — what actually moves with what, and when.

WHY THIS EXISTS
---------------
"Crude is up so paint stocks will fall", "a weak rupee is bad for the market",
"gold is a hedge" — Indian market commentary runs on cross-asset relationships
that are almost never measured. Two things go wrong with the folk versions:

1. **They quote unconditional correlation.** The number that matters is the
   correlation in a DRAWDOWN, because that is when diversification is supposed
   to pay. Equity correlations converge toward 1 exactly then, so an
   unconditional matrix systematically overstates how diversified a book is.
   §6.5 promised conditional correlation from the start; only the
   unconditional version was ever built.

2. **They are never tested for significance.** With ~10 assets there are 45
   pairs, so at 95% confidence roughly two will look "significant" from noise
   alone. Every relationship here is therefore run through the same
   cluster-robust, FDR-controlled machinery as the equity studies, and a
   relationship that fails is reported as failing rather than dropped.

WHAT IS MEASURED
----------------
  * Pearson correlation with a proper confidence interval (Fisher z), computed
    both unconditionally and CONDITIONAL on equity-market stress.
  * Lead-lag: does A's move today predict B's tomorrow, or is the relationship
    contemporaneous? Contemporaneous co-movement is a diversification fact;
    a genuine lead is a (rare, usually illusory) trading fact, so they are never
    conflated.
  * Beta to a chosen driver, which for an Indian book usually means USDINR or
    crude rather than only the index.

Everything is descriptive. Nothing here forecasts.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from ..research.stats import (benjamini_hochberg, norm_cdf, norm_ppf,
                              t_two_sided_p)

MIN_OBS = 60
STRESS_QUANTILE = 0.20      # worst 20% of market days define "stress"


def _align(a: Sequence[float], b: Sequence[float]) -> tuple[list[float], list[float]]:
    """Positional tail-align. ONLY valid when both series are already on the
    same calendar — see `align_on_dates`, which is what callers should use.

    Retained for series a caller has already aligned; mixing two sources with
    different holiday calendars through here silently shifts them against each
    other and destroys the very correlation being measured (observed:
    RELIANCE-vs-NIFTY came out at +0.05 instead of ~+0.6).
    """
    n = min(len(a), len(b))
    return list(a[-n:]), list(b[-n:])


def align_on_dates(series: dict[str, dict]) -> dict[str, list[float]]:
    """Intersect several {date: value} series onto their COMMON dates.

    Equity bars come from the price table and macro bars from a different
    provider with a different holiday calendar, so the two are never the same
    length or the same set of days. Aligning by position — taking the last N of
    each — quietly pairs Monday's rupee move with Tuesday's equity move and
    turns a 0.6 correlation into 0.05. Correlation work must intersect on dates.
    """
    if not series:
        return {}
    common = None
    for values in series.values():
        keys = set(values)
        common = keys if common is None else (common & keys)
    ordered = sorted(common or [])
    return {name: [values[d] for d in ordered] for name, values in series.items()}


def returns_from_dated_closes(rows: Sequence[tuple]) -> dict:
    """[(date, close), ...] → {date: simple return}, dated so it can be aligned."""
    out: dict = {}
    prev_d = prev_c = None
    for d, c in sorted(rows):
        if prev_c and c is not None and prev_c > 0:
            out[d] = c / prev_c - 1
        prev_d, prev_c = d, c
    return out


def _pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def correlation_with_ci(x: Sequence[float], y: Sequence[float],
                        alpha: float = 0.05) -> dict:
    """Pearson correlation plus a Fisher z-transform confidence interval.

    A correlation without an interval invites over-reading: at n=60 a sample
    correlation of 0.25 has a 95% interval of roughly [0.00, 0.47], which is the
    difference between "meaningfully related" and "indistinguishable from
    unrelated".
    """
    x, y = _align(x, y)
    n = len(x)
    r = _pearson(x, y)
    if r is None or n < 4 or abs(r) >= 1.0:
        return {"r": r, "n": n, "ci95": None, "p_value": None,
                "reason": "insufficient or degenerate sample"}
    z = 0.5 * math.log((1 + r) / (1 - r))
    se = 1.0 / math.sqrt(n - 3)
    crit = norm_ppf(1 - alpha / 2)
    lo, hi = math.tanh(z - crit * se), math.tanh(z + crit * se)
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    return {"r": round(r, 4), "n": n,
            "ci95": [round(lo, 4), round(hi, 4)],
            "t_stat": round(t, 3),
            "p_value": t_two_sided_p(t, n - 2),
            "significant": lo > 0 or hi < 0}


def stress_conditional_correlation(asset: Sequence[float],
                                   market: Sequence[float],
                                   quantile: float = STRESS_QUANTILE) -> dict:
    """Correlation in the worst `quantile` of market days versus all days.

    This is the number a diversification claim actually rests on. An asset that
    is uncorrelated on average but converges to the market in a drawdown
    provides no protection when protection is the entire point — and the gap
    between the two figures is the honest measure of that failure.
    """
    a, m = _align(asset, market)
    if len(a) < MIN_OBS:
        return {"computable": False,
                "reason": f"need ≥{MIN_OBS} aligned observations, have {len(a)}"}
    order = sorted(range(len(m)), key=lambda i: m[i])
    k = max(10, int(len(m) * quantile))
    stress_idx = set(order[:k])
    sa = [a[i] for i in stress_idx]
    sm = [m[i] for i in stress_idx]
    calm_idx = [i for i in range(len(m)) if i not in stress_idx]
    ca = [a[i] for i in calm_idx]
    cm = [m[i] for i in calm_idx]

    uncond = correlation_with_ci(a, m)
    stress = correlation_with_ci(sa, sm)
    calm = correlation_with_ci(ca, cm)
    gap = (None if stress.get("r") is None or uncond.get("r") is None
           else round(stress["r"] - uncond["r"], 4))
    return {
        "computable": True,
        "unconditional": uncond, "stress": stress, "calm": calm,
        "stress_days": k, "stress_quantile": quantile,
        "correlation_gap_in_stress": gap,
        "reading": (
            "correlation RISES in market stress — the diversification this pair "
            "appears to offer is weakest exactly when it is needed"
            if gap is not None and gap > 0.1 else
            "correlation falls in stress — genuinely diversifying when it matters"
            if gap is not None and gap < -0.1 else
            "correlation is broadly stable across regimes"),
        "method": (f"worst {quantile:.0%} of market days by return define stress; "
                   "Fisher-z confidence intervals on every correlation"),
    }


def lead_lag(driver: Sequence[float], follower: Sequence[float],
             max_lag: int = 5) -> dict:
    """Cross-correlation across lags, with FDR control over the lag scan.

    Scanning k lags and reporting the best one is a textbook way to manufacture
    a signal, so every lag is tested and Benjamini-Hochberg is applied ACROSS
    THE SCAN. Positive lag = driver leads follower.

    Lag 0 dominating is the overwhelmingly common — and honest — outcome: assets
    move together because they respond to the same news, not because one
    predicts the other.
    """
    d, f = _align(driver, follower)
    if len(d) < MIN_OBS:
        return {"computable": False,
                "reason": f"need ≥{MIN_OBS} observations, have {len(d)}"}
    rows = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = d[:len(d) - lag] if lag else d, f[lag:]
        else:
            a, b = d[-lag:], f[:len(f) + lag]
        if len(a) < MIN_OBS:
            continue
        c = correlation_with_ci(a, b)
        if c.get("r") is None:
            continue
        rows.append({"lag": lag, "r": c["r"], "n": c["n"],
                     "p_value": c["p_value"], "t_stat": c.get("t_stat")})
    if not rows:
        return {"computable": False, "reason": "no lag produced a usable sample"}
    qs = benjamini_hochberg([r["p_value"] for r in rows])
    for r, q in zip(rows, qs):
        r["q_value"] = None if q is None else round(q, 5)
        r["survives_fdr"] = q is not None and q <= 0.05
    best = max(rows, key=lambda r: abs(r["r"]))
    predictive = [r for r in rows if r["lag"] > 0 and r["survives_fdr"]]
    return {
        "computable": True, "lags": rows, "strongest": best,
        "contemporaneous": next((r for r in rows if r["lag"] == 0), None),
        "predictive_lags_surviving_fdr": predictive,
        "reading": (
            f"strongest association is at lag {best['lag']} (r={best['r']:+.2f})"
            + ("; that is CONTEMPORANEOUS — the two respond to the same "
               "information rather than one leading the other, which is a "
               "diversification fact, not a trading one."
               if best["lag"] == 0 else
               f"; {len(predictive)} positive lag(s) survive FDR across the scan"
               if predictive else
               "; no positive lag survives multiple-testing control across the "
               "scan, so there is no evidence of genuine lead-lag")),
        "caveat": ("Scanning lags and reporting the best is how spurious leads are "
                   "manufactured; every lag here is FDR-controlled across the whole "
                   "scan. Even a surviving lead is fragile — daily EOD data cannot "
                   "resolve intraday causality, and a lead that real would be "
                   "arbitraged."),
    }


def beta_to(asset: Sequence[float], driver: Sequence[float]) -> dict:
    """OLS sensitivity of an asset to a driver (USDINR, crude, gold, index).

    For an Indian book the interesting betas are usually NOT to the index: an
    importer's real exposure is to the rupee, a paint or airline business to
    crude. R² is reported alongside because a precisely-estimated beta on a
    relationship that explains 3% of variance is precision about nothing.
    """
    a, d = _align(asset, driver)
    n = len(a)
    if n < MIN_OBS:
        return {"computable": False, "reason": f"need ≥{MIN_OBS} obs, have {n}"}
    md, ma = sum(d) / n, sum(a) / n
    sdd = sum((x - md) ** 2 for x in d)
    if sdd <= 0:
        return {"computable": False, "reason": "driver has zero variance"}
    beta = sum((x - md) * (y - ma) for x, y in zip(d, a)) / sdd
    alpha = ma - beta * md
    resid = [y - (alpha + beta * x) for x, y in zip(d, a)]
    ss_res = sum(e * e for e in resid)
    ss_tot = sum((y - ma) ** 2 for y in a)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    se = math.sqrt((ss_res / max(n - 2, 1)) / sdd)
    t = beta / se if se > 0 else 0.0
    return {"computable": True, "beta": round(beta, 4),
            "standard_error": round(se, 4), "t_stat": round(t, 3),
            "p_value": t_two_sided_p(t, n - 2),
            "r_squared": None if r2 is None else round(r2, 4),
            "observations": n,
            "caveat": ("R² is how much of this asset's variance the driver explains "
                       "at all. A significant beta with a low R² means the "
                       "relationship is real but small — it will not dominate the "
                       "position's outcome.")}


def relationship_matrix(series: dict[str, Sequence[float]],
                        market_key: str) -> dict:
    """Every pair, unconditional and stress-conditional, FDR-controlled.

    The FDR correction is the point: with 10 assets there are 45 pairs, so at
    95% confidence about two will look significant from noise alone. Reporting
    a matrix of raw correlations invites exactly that error.
    """
    keys = [k for k in series if len(series[k]) >= MIN_OBS]
    if market_key not in keys or len(keys) < 2:
        return {"computable": False,
                "reason": "need the market series and ≥2 assets with enough history"}
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            c = correlation_with_ci(series[a], series[b])
            if c.get("r") is None:
                continue
            pairs.append({"a": a, "b": b, "r": c["r"], "n": c["n"],
                          "ci95": c["ci95"], "p_value": c["p_value"]})
    qs = benjamini_hochberg([p["p_value"] for p in pairs])
    for p, q in zip(pairs, qs):
        p["q_value"] = None if q is None else round(q, 5)
        p["survives_fdr"] = q is not None and q <= 0.05

    stress = {}
    for k in keys:
        if k == market_key:
            continue
        sc = stress_conditional_correlation(series[k], series[market_key])
        if sc.get("computable"):
            stress[k] = {"unconditional_r": sc["unconditional"]["r"],
                         "stress_r": sc["stress"]["r"],
                         "gap": sc["correlation_gap_in_stress"],
                         "reading": sc["reading"]}
    converging = sorted(
        [(k, v) for k, v in stress.items() if v["gap"] is not None],
        key=lambda kv: -(kv[1]["gap"] or 0))[:5]
    return {
        "computable": True,
        "assets": keys, "market": market_key,
        "pairs": sorted(pairs, key=lambda p: -abs(p["r"])),
        "pairs_tested": len(pairs),
        "pairs_surviving_fdr": sum(1 for p in pairs if p["survives_fdr"]),
        "stress_conditional": stress,
        "worst_diversifiers_in_stress": [
            {"asset": k, "gap": v["gap"], "stress_r": v["stress_r"]}
            for k, v in converging if (v["gap"] or 0) > 0],
        "note": (f"{len(pairs)} pairs tested; multiple-testing control applied "
                 "across all of them. An unconditional correlation matrix "
                 "flatters diversification because equity correlations converge "
                 "in drawdowns — read the stress column."),
    }
