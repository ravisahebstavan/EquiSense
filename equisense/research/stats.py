"""Statistical inference primitives for the research plane.

Everything in this module exists because the platform makes *quantitative
claims about edges* and those claims have to survive the standard critiques of
empirical asset pricing. Four of those critiques are addressed here:

1. **Dependence.** Cross-sectional event studies produce observations that are
   NOT independent: names picked on the same date share a common market shock,
   and h-day forward windows sampled every 21 days overlap. Naive N wildly
   overstates information content. `effective_sample_size` applies the Kish
   (1965) design effect with an intraclass correlation estimated *from the
   data* (one-way random-effects ANOVA), and `cluster_robust_mean` gives the
   Liang–Zeger (1986) cluster-robust standard error. Both treat a *date block*
   spanning the overlap horizon as the cluster, so serial and cross-sectional
   dependence are captured by one data-driven estimator rather than a
   multiplicative guess.

2. **Multiplicity.** Testing ~50 hypothesis/horizon/regime cells at 95%
   confidence yields false positives by construction. `benjamini_hochberg`
   controls the false discovery rate across a run, and `HLZ_T_HURDLE` encodes
   the Harvey, Liu & Zhu (2016, Review of Financial Studies, "…and the
   Cross-Section of Expected Returns") recommendation that a *new* factor
   clear |t| ≈ 3.0, not 2.0, given the size of the published factor zoo.

3. **Backtest selection bias.** A best-of-N backtest Sharpe is upward-biased.
   `deflated_sharpe_ratio` implements Bailey & López de Prado (2014), which
   discounts an observed Sharpe by the number of trials and by the return
   distribution's own skew/kurtosis.

4. **Small samples.** p-values use the exact Student-t CDF (regularized
   incomplete beta, Lentz continued fraction) rather than a normal
   approximation, because cluster counts here are routinely 15–40.

No scipy: the deployment target is a size-capped serverless bundle. Every
routine below is stdlib + numpy and is unit-tested against known values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Optional, Sequence

_ND = NormalDist()

# Harvey, Liu & Zhu (2016) multiple-testing hurdle for a *newly proposed*
# factor. Their headline: with hundreds of published factors, a t of 2.0 is
# roughly a coin flip; ~3.0 is the defensible bar.
HLZ_T_HURDLE = 3.0

EULER_GAMMA = 0.5772156649015329


def norm_cdf(z: float) -> float:
    return _ND.cdf(z)


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF, clamped away from the singular endpoints."""
    p = min(max(p, 1e-15), 1 - 1e-15)
    return _ND.inv_cdf(p)


# --------------------------------------------------------------- Student-t

def _betacf(a: float, b: float, x: float, itmax: int = 300,
            eps: float = 3e-16) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """One-sided survival function P(T > t) for Student-t with `df` degrees."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    half = 0.5 * betainc(0.5 * df, 0.5, x)
    return half if t > 0 else 1.0 - half


def t_two_sided_p(t: float, df: float) -> float:
    """Two-sided p-value for a t statistic. Exact, not normal-approximated."""
    if df <= 0 or t != t:
        return float("nan")
    return min(1.0, 2.0 * t_sf(abs(t), df))


# ------------------------------------------------- dependence / sample size

def intraclass_correlation(groups: Sequence[Sequence[float]]) -> Optional[float]:
    """One-way random-effects ICC(1) — the fraction of total variance that is
    *between* clusters. This is the dependence parameter the design effect
    needs, estimated from the observations themselves rather than assumed.

    ICC = (MSB − MSW) / (MSB + (m0 − 1)·MSW), with Kish's m0 for unbalanced
    cluster sizes. Clamped to [0, 1]: a negative ANOVA estimate means "no
    detectable clustering", which for our purpose is 0, not a variance bonus.
    """
    gs = [list(g) for g in groups if len(g) > 0]
    G, N = len(gs), sum(len(g) for g in gs)
    if G < 2 or N <= G:
        return None
    grand = sum(sum(g) for g in gs) / N
    msb_num = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in gs)
    msw_num = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in gs)
    msb = msb_num / (G - 1)
    msw = msw_num / (N - G)
    sum_sq_sizes = sum(len(g) ** 2 for g in gs)
    m0 = (N - sum_sq_sizes / N) / (G - 1)
    denom = msb + (m0 - 1) * msw
    if denom <= 0 or m0 <= 1:
        return 0.0
    return max(0.0, min(1.0, (msb - msw) / denom))


def design_effect(mean_cluster_size: float, icc: float) -> float:
    """Kish (1965) design effect: variance inflation from clustering."""
    return max(1.0, 1.0 + (max(mean_cluster_size, 1.0) - 1.0) * max(0.0, icc))


def effective_sample_size(groups: Sequence[Sequence[float]]) -> dict:
    """Overlap- AND cross-section-corrected effective N.

    `groups` are dependence clusters: all observations that share a common
    shock. For a cross-sectional event study the cluster is a *date block*
    spanning the forward-return overlap (see `block_observations`), so one ICC
    absorbs both same-date commonality and window overlap.

    Returns the full decomposition — N, cluster count, mean cluster size, ICC,
    design effect, N_eff — because the number is only trustworthy if the user
    can see how it was reduced.
    """
    gs = [list(g) for g in groups if len(g) > 0]
    n = sum(len(g) for g in gs)
    if not gs:
        return {"n": 0, "n_clusters": 0, "mean_cluster_size": 0.0, "icc": None,
                "design_effect": 1.0, "n_eff": 0}
    n_clusters = len(gs)
    m_bar = n / n_clusters
    icc = intraclass_correlation(gs)
    if icc is None:
        # cannot estimate dependence → assume full clustering (conservative:
        # each cluster counts once), never assume independence
        return {"n": n, "n_clusters": n_clusters, "mean_cluster_size": m_bar,
                "icc": None, "design_effect": m_bar,
                "n_eff": n_clusters,
                "note": "ICC not estimable — each cluster counted once (conservative)"}
    deff = design_effect(m_bar, icc)
    return {"n": n, "n_clusters": n_clusters,
            "mean_cluster_size": round(m_bar, 2), "icc": round(icc, 4),
            "design_effect": round(deff, 3),
            "n_eff": max(1, int(n / deff))}


def block_observations(by_date: Sequence[tuple], overlap_span: int) -> list[list[float]]:
    """Group (date, [values]) pairs into dependence clusters.

    `overlap_span` = ceil(horizon / sampling interval): the number of
    consecutive sample dates whose forward windows overlap. Consecutive dates
    inside one span are pooled into a single cluster, so the resulting clusters
    are (approximately) mutually independent in time while remaining fully
    dependent within.
    """
    span = max(1, int(overlap_span))
    clusters: list[list[float]] = []
    for i, (_dt, vals) in enumerate(sorted(by_date, key=lambda kv: kv[0])):
        idx = i // span
        while len(clusters) <= idx:
            clusters.append([])
        clusters[idx].extend(vals)
    return [c for c in clusters if c]


# ----------------------------------------------------- cluster-robust mean

@dataclass
class MeanTest:
    """A mean estimate with dependence-aware inference, fully decomposed."""
    mean: float
    se: float
    t_stat: float
    df: int
    p_value: float
    n: int
    n_clusters: int
    n_eff: int
    icc: Optional[float]
    design_effect: float
    ci95: tuple[float, float]
    method: str = "cluster-robust (Liang–Zeger), exact Student-t"

    def to_dict(self) -> dict:
        return {"mean": round(self.mean, 4), "se": round(self.se, 4),
                "t_stat": round(self.t_stat, 3), "df": self.df,
                "p_value": None if self.p_value != self.p_value else round(self.p_value, 5),
                "n": self.n, "n_clusters": self.n_clusters, "n_eff": self.n_eff,
                "icc": self.icc, "design_effect": self.design_effect,
                "ci95": [round(self.ci95[0], 3), round(self.ci95[1], 3)],
                "method": self.method}


def cluster_robust_mean(groups: Sequence[Sequence[float]]) -> Optional[MeanTest]:
    """Mean with a cluster-robust SE — the correct inference for observations
    that are independent *across* clusters and arbitrarily dependent *within*.

    Var(x̄) = [G/(G−1)] · Σ_g (Σ_i (x_gi − x̄))² / N²   (CR1 sandwich)

    Degrees of freedom are G−1, not N−1: that single substitution is the
    difference between an honest and a fictitious significance claim here.
    """
    gs = [list(g) for g in groups if len(g) > 0]
    G = len(gs)
    n = sum(len(g) for g in gs)
    if G < 2 or n < 2:
        return None
    mean = sum(sum(g) for g in gs) / n
    meat = sum((sum(x - mean for x in g)) ** 2 for g in gs)
    var = (G / (G - 1)) * meat / (n * n)
    se = math.sqrt(var) if var > 0 else 0.0
    ess = effective_sample_size(gs)
    df = G - 1
    if se <= 0:
        t = 0.0
        p = 1.0
        half = 0.0
    else:
        t = mean / se
        p = t_two_sided_p(t, df)
        # exact t critical value for the CI, via bisection on the survival fn
        lo, hi = 0.0, 100.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if t_sf(mid, df) > 0.025:
                lo = mid
            else:
                hi = mid
        half = (lo + hi) / 2 * se
    return MeanTest(mean=mean, se=se, t_stat=t, df=df, p_value=p, n=n,
                    n_clusters=G, n_eff=ess["n_eff"], icc=ess["icc"],
                    design_effect=ess["design_effect"],
                    ci95=(mean - half, mean + half))


def newey_west_se(values: Sequence[float], lags: int) -> Optional[float]:
    """Newey–West (1987) HAC standard error of the mean for a series with
    known overlap-induced autocorrelation (Hansen–Hodrick setting: `lags` =
    overlap span − 1). Used for period-return series from overlapping windows.
    """
    x = [float(v) for v in values]
    n = len(x)
    if n < 3:
        return None
    m = sum(x) / n
    e = [v - m for v in x]
    gamma0 = sum(v * v for v in e) / n
    total = gamma0
    for L in range(1, min(lags, n - 1) + 1):
        w = 1.0 - L / (lags + 1.0)
        gl = sum(e[i] * e[i - L] for i in range(L, n)) / n
        total += 2.0 * w * gl
    if total <= 0:
        return None
    return math.sqrt(total / n)


# ------------------------------------------------------------- multiplicity

def benjamini_hochberg(p_values: Sequence[Optional[float]],
                       alpha: float = 0.05) -> list[Optional[float]]:
    """Benjamini–Hochberg (1995) FDR q-values, returned in input order.

    q_i = min over k ≥ i (in ascending-p order) of p_k·m/k, enforced monotone.
    None inputs pass through as None and are excluded from m.
    """
    idx = [(i, p) for i, p in enumerate(p_values)
           if p is not None and p == p]
    m = len(idx)
    out: list[Optional[float]] = [None] * len(p_values)
    if m == 0:
        return out
    idx.sort(key=lambda kv: kv[1])
    running = 1.0
    for rank in range(m, 0, -1):
        i, p = idx[rank - 1]
        running = min(running, p * m / rank)
        out[i] = min(1.0, running)
    return out


def multiplicity_verdict(t_stat: Optional[float], q_value: Optional[float],
                         alpha: float = 0.05) -> str:
    """One honest label combining the FDR result and the HLZ hurdle."""
    if t_stat is None or t_stat != t_stat:
        return "not testable"
    passes_fdr = q_value is not None and q_value <= alpha
    passes_hlz = abs(t_stat) >= HLZ_T_HURDLE
    if passes_fdr and passes_hlz:
        return "survives FDR and the HLZ |t|≥3 hurdle"
    if passes_fdr:
        return (f"survives FDR (q={q_value:.3f}) but |t|={abs(t_stat):.2f} is below "
                f"the HLZ {HLZ_T_HURDLE} hurdle for a new factor")
    if passes_hlz:
        return f"|t|={abs(t_stat):.2f} clears HLZ but fails FDR across this run"
    return (f"fails multiple-testing control (|t|={abs(t_stat):.2f}"
            + (f", q={q_value:.3f}" if q_value is not None else "") + ")")


# -------------------------------------------------- backtest selection bias

def _skew_kurt(x: Sequence[float]) -> tuple[float, float]:
    n = len(x)
    m = sum(x) / n
    s2 = sum((v - m) ** 2 for v in x) / n
    if s2 <= 0:
        return 0.0, 3.0
    s = math.sqrt(s2)
    skew = sum(((v - m) / s) ** 3 for v in x) / n
    kurt = sum(((v - m) / s) ** 4 for v in x) / n   # non-excess
    return skew, kurt


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """E[max Sharpe] under the null of zero true skill, across `n_trials`
    independent trials (Bailey & López de Prado 2014, eq. for SR*)."""
    n = max(2, int(n_trials))
    sd = math.sqrt(max(sr_variance, 0.0))
    if sd <= 0:
        return 0.0
    a = norm_ppf(1.0 - 1.0 / n)
    b = norm_ppf(1.0 - 1.0 / (n * math.e))
    return sd * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b)


def deflated_sharpe_ratio(returns: Sequence[float], n_trials: int,
                          sr_variance: Optional[float] = None) -> dict:
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

    Discounts the observed Sharpe for (a) the number of configurations tried
    and (b) non-normality of the return series. DSR is the probability that
    true skill is positive; below ~0.95 the backtest is not evidence.

    `sr_variance` is the variance of Sharpe ratios across trials. When unknown
    we use the null-model variance of a Sharpe estimate, 1/(T−1), which is the
    standard fallback and stated as such.
    """
    x = [float(v) for v in returns]
    T = len(x)
    if T < 4:
        return {"computable": False,
                "reason": f"need ≥4 periods, have {T}"}
    m = sum(x) / T
    var = sum((v - m) ** 2 for v in x) / (T - 1)
    if var <= 0:
        return {"computable": False, "reason": "zero return variance"}
    sr = m / math.sqrt(var)
    skew, kurt = _skew_kurt(x)
    srv = sr_variance if sr_variance is not None else 1.0 / (T - 1)
    sr_star = expected_max_sharpe(n_trials, srv)
    denom_sq = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom_sq <= 0:
        return {"computable": False,
                "reason": "non-normality correction degenerate (denominator ≤ 0)"}
    z = (sr - sr_star) * math.sqrt(T - 1) / math.sqrt(denom_sq)
    return {
        "computable": True,
        "sharpe_per_period": round(sr, 4),
        "expected_max_sharpe_under_null": round(sr_star, 4),
        "n_trials": int(n_trials),
        "skew": round(skew, 3), "kurtosis": round(kurt, 3),
        "periods": T,
        "deflated_sharpe_probability": round(norm_cdf(z), 4),
        "verdict": ("evidence of genuine skill (DSR ≥ 0.95)" if norm_cdf(z) >= 0.95
                    else f"NOT distinguishable from the best of {int(n_trials)} lucky "
                         "trials (DSR < 0.95)"),
        "sr_variance_source": ("supplied across-trial variance" if sr_variance is not None
                               else "null-model fallback 1/(T−1)"),
        "citation": "Bailey & López de Prado (2014), Journal of Portfolio Management",
    }


# ------------------------------------------------------------- bootstrapping

def cluster_block_bootstrap_ci(groups: Sequence[Sequence[float]],
                               statistic: str = "median",
                               n_boot: int = 2000, alpha: float = 0.05,
                               seed: int = 7) -> tuple[float, float]:
    """Bootstrap CI that resamples whole dependence CLUSTERS, not individual
    observations.

    This is the fix for the subtle failure mode of a naive moving-block
    bootstrap over a flattened observation list: if the list is
    (date, pick)-ordered, a block of k observations sits *inside* one date
    cohort and the resample preserves none of the cross-sectional dependence,
    producing intervals that are far too narrow. Resampling clusters with
    replacement is the standard cluster bootstrap and is correct by
    construction here.
    """
    import random
    gs = [list(g) for g in groups if len(g) > 0]
    G = len(gs)
    if G < 4:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    stats_out: list[float] = []
    for _ in range(n_boot):
        sample: list[float] = []
        for _ in range(G):
            sample.extend(gs[rng.randrange(G)])
        if not sample:
            continue
        sample.sort()
        if statistic == "median":
            k = len(sample)
            stats_out.append(sample[k // 2] if k % 2
                             else 0.5 * (sample[k // 2 - 1] + sample[k // 2]))
        else:
            stats_out.append(sum(sample) / len(sample))
    if len(stats_out) < 10:
        return (float("nan"), float("nan"))
    stats_out.sort()
    lo = stats_out[int(alpha / 2 * len(stats_out))]
    hi = stats_out[min(len(stats_out) - 1,
                       int((1 - alpha / 2) * len(stats_out)))]
    return (lo, hi)


# ------------------------------------------------- combinatorial purged CV

def cpcv_splits(n_samples: int, n_blocks: int = 6, k_test: int = 2,
                label_span: int = 3, embargo: int = 1
                ) -> list[tuple[list[int], list[int]]]:
    """Combinatorial Purged Cross-Validation splits (López de Prado, AFML ch. 7).

    Walk-forward yields ONE out-of-sample path, and with a short history that
    means two or three folds — far too few to tell a stable edge from a lucky
    ordering. CPCV splits the timeline into `n_blocks` and tests on every
    combination of `k_test` of them, giving C(n_blocks, k_test) distinct paths
    (15 for the default 6-choose-2) out of the same data.

    What that buys is a DISTRIBUTION rather than a point estimate: an edge whose
    OOS paths run +25% to +40% is a different object from one running -5% to
    +80% with the same mean, and a single walk-forward number cannot tell them
    apart.

    `label_span` is the forward horizon expressed in SAMPLES (a 63-day horizon
    sampled every 21 days is 3). Training samples whose forward label overlaps
    the test block are purged; `embargo` further drops samples immediately after
    a test block, because a label ending just before a test period still shares
    the same post-event drift.
    """
    if n_samples <= 0 or n_blocks < 2 or not (1 <= k_test < n_blocks):
        return []
    edges = [round(i * n_samples / n_blocks) for i in range(n_blocks + 1)]
    blocks = [list(range(edges[i], edges[i + 1])) for i in range(n_blocks)]
    blocks = [b for b in blocks if b]
    if len(blocks) < 2:
        return []

    out: list[tuple[list[int], list[int]]] = []
    for combo in _combinations(range(len(blocks)), min(k_test, len(blocks) - 1)):
        test_idx = sorted(i for c in combo for i in blocks[c])
        if not test_idx:
            continue
        banned: set[int] = set()
        for c in combo:
            lo, hi = blocks[c][0], blocks[c][-1]
            # purge: a training sample whose label window reaches into the test
            # block shares outcome data with it
            banned.update(range(lo - label_span, hi + 1))
            # embargo: and one whose label ended just before it still shares the
            # same post-event drift
            banned.update(range(hi + 1, hi + 1 + embargo))
        train_idx = [i for i in range(n_samples) if i not in banned]
        if train_idx and test_idx:
            out.append((train_idx, test_idx))
    return out


def _combinations(seq, k):
    seq = list(seq)
    if k == 0:
        yield ()
        return
    for i in range(len(seq) - k + 1):
        for rest in _combinations(seq[i + 1:], k - 1):
            yield (seq[i],) + rest


def cpcv_evaluate(returns: Sequence[float], n_blocks: int = 6, k_test: int = 2,
                  label_span: int = 3, embargo: int = 1,
                  periods_per_year: float = 12.0) -> dict:
    """Run CPCV over a period-return series and report the OOS DISTRIBUTION.

    The headline is deliberately the worst path and the fraction of paths that
    lose money, not the mean. A strategy is only as good as the path you happen
    to live through, and the mean of many overlapping paths flatters it.
    """
    rs = [float(r) for r in returns if r is not None and r == r]
    n = len(rs)
    splits = cpcv_splits(n, n_blocks, k_test, label_span, embargo)
    if n < 24 or not splits:
        return {"computable": False,
                "reason": f"need ≥24 periods and ≥1 split, have {n} and {len(splits)}"}

    path_ann, path_mean, path_hit = [], [], []
    for _train, test in splits:
        seg = [rs[i] for i in test]
        if len(seg) < 4:
            continue
        eq = 1.0
        for r in seg:
            eq *= (1.0 + r)
        yrs = len(seg) / periods_per_year
        if yrs <= 0 or eq <= 0:
            continue
        path_ann.append((eq ** (1.0 / yrs) - 1.0) * 100.0)
        path_mean.append(sum(seg) / len(seg) * 100.0)
        path_hit.append(sum(1 for r in seg if r > 0) / len(seg))
    if not path_ann:
        return {"computable": False, "reason": "no usable test paths"}

    srt = sorted(path_ann)
    m = len(srt)
    return {
        "computable": True,
        "paths": m,
        "n_blocks": n_blocks,
        "k_test": k_test,
        "label_span": label_span,
        "embargo": embargo,
        "annualized_pct": {
            "min": round(srt[0], 2),
            "p25": round(srt[m // 4], 2),
            "median": round(srt[m // 2], 2),
            "p75": round(srt[(3 * m) // 4], 2),
            "max": round(srt[-1], 2),
            "mean": round(sum(srt) / m, 2),
        },
        "paths_losing_money_pct": round(sum(1 for x in srt if x <= 0) / m * 100, 1),
        "mean_hit_rate": round(sum(path_hit) / len(path_hit), 3),
        "reading": (
            f"{m} out-of-sample paths span {srt[0]:.1f}% to {srt[-1]:.1f}% "
            f"annualised, median {srt[m // 2]:.1f}%. "
            + (f"{sum(1 for x in srt if x <= 0)} of {m} lose money."
               if any(x <= 0 for x in srt)
               else "No path loses money.")),
        "caveat": ("Paths share data by construction, so these are not "
                   "independent samples — the SPREAD is the diagnostic, not a "
                   "confidence interval. A wide spread means the single "
                   "walk-forward number was an accident of ordering."),
    }
