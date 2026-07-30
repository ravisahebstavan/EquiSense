"""Monte Carlo simulation engine — distributions where the platform had points.

WHY THIS MODULE EXISTS
----------------------
Most of this platform's outputs are point estimates: one implied growth rate,
one position size, one portfolio value. Every one of them is a single draw from
a distribution the user never sees. Monte Carlo makes that distribution explicit,
which changes the questions you can ask from "what is the number" to "how bad is
the bad case, and how likely is it".

Three things here are deliberately non-standard for a retail tool:

1. **Three return models are always run side by side** — Gaussian, Student-t and
   a stationary block bootstrap of the actual history. The COMPARISON is the
   output. Gaussian VaR is the industry's most common risk number and it
   systematically understates tail loss for equities; showing the three together
   makes that failure visible instead of asking the user to take it on faith.

2. **Every estimate carries its own Monte Carlo standard error.** A simulated
   number without an error bar invites the reader to over-read the last digit,
   and MC error falls only as 1/sqrt(n) — worth seeing.

3. **Block bootstrap, not iid resampling, for paths.** Daily equity returns
   exhibit volatility clustering; iid resampling destroys it and produces
   drawdown probabilities that are far too benign. Blocks preserve it.

Methods are standard and citable: Cholesky factorisation for correlated draws,
antithetic variates for variance reduction, Politis–Romano stationary bootstrap,
and Expected Shortfall (Artzner et al. 1999) alongside VaR because VaR is not
sub-additive and says nothing about how bad the tail actually is.

Pure functions over numpy. Seeded, so every published figure is reproducible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

TRADING_DAYS = 252
DEFAULT_PATHS = 20_000


# ------------------------------------------------------------------ helpers

def _mc_stderr(samples: np.ndarray) -> float:
    """Standard error of the simulated MEAN — the honest 'how precise is this
    simulation' number, distinct from the dispersion of outcomes."""
    n = samples.size
    return float(samples.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")


def _quantile_stderr(samples: np.ndarray, q: float) -> float:
    """Asymptotic SE of a sample quantile (Bahadur): sqrt(q(1-q)/n) / f(x_q),
    with the density estimated from a local histogram. Quantile estimates are
    much noisier than means, which matters directly for VaR."""
    n = samples.size
    if n < 100:
        return float("nan")
    x = np.quantile(samples, q)
    h = 1.06 * samples.std(ddof=1) * n ** (-0.2)      # Silverman bandwidth
    if h <= 0:
        return float("nan")
    dens = np.mean(np.abs(samples - x) < h) / (2 * h)
    if dens <= 0:
        return float("nan")
    return float(math.sqrt(q * (1 - q) / n) / dens)


# Cholesky requires strict positive DEFINITENESS. A perfectly correlated pair
# (an ETF and its index, two share classes, duplicated data) gives a matrix that
# is positive semi-definite with a zero eigenvalue — mathematically fine, but
# np.linalg.cholesky raises on it. Eigenvalues are floored at this value so the
# factorisation always exists.
_PSD_EPS = 1e-10


def nearest_psd_correlation(corr: np.ndarray) -> tuple[np.ndarray, bool]:
    """Project a correlation matrix onto the nearest positive DEFINITE one.

    Two separate things break Cholesky here and both occur with real data:
      * negative eigenvalues, from sample correlations built on unequal-length
        or noisy series;
      * a zero eigenvalue, from perfectly correlated assets — an ETF against its
        own index, two listings of the same company, or a duplicated column.
    Clipping eigenvalues to a small positive floor and renormalising the diagonal
    handles both. The caller is told the repair happened rather than being
    silently handed a different matrix from the one it passed in.
    """
    c = np.array(corr, dtype=float)
    c = (c + c.T) / 2.0
    vals, vecs = np.linalg.eigh(c)
    if vals.min() >= _PSD_EPS:
        return c, False
    vals = np.clip(vals, _PSD_EPS, None)
    repaired = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(repaired))
    repaired = repaired / np.outer(d, d)
    np.fill_diagonal(repaired, 1.0)
    return repaired, True


def _safe_cholesky(psd: np.ndarray) -> np.ndarray:
    """Cholesky with a ridge retry — floating-point error can still leave a
    repaired matrix a hair short of positive definite."""
    try:
        return np.linalg.cholesky(psd)
    except np.linalg.LinAlgError:
        k = psd.shape[0]
        for ridge in (1e-10, 1e-8, 1e-6, 1e-4):
            try:
                return np.linalg.cholesky(psd + np.eye(k) * ridge)
            except np.linalg.LinAlgError:
                continue
        return np.eye(k)     # last resort: treat assets as independent


def _correlated_normals(rng: np.random.Generator, n_paths: int, n_steps: int,
                        corr: np.ndarray, antithetic: bool = True) -> np.ndarray:
    """(n_paths, n_steps, n_assets) correlated standard normals via Cholesky.

    Antithetic variates: half the paths are drawn, the other half are their
    negatives. Because the payoff is monotone in the shocks over most of the
    range, the negative correlation between pairs cancels a large part of the
    sampling error at zero extra cost.
    """
    k = corr.shape[0]
    psd, _ = nearest_psd_correlation(corr)
    L = _safe_cholesky(psd)
    half = (n_paths + 1) // 2 if antithetic else n_paths
    z = rng.standard_normal((half, n_steps, k))
    if antithetic:
        z = np.concatenate([z, -z], axis=0)[:n_paths]
    return z @ L.T


def _student_t_shocks(rng: np.random.Generator, shape: tuple, df: float) -> np.ndarray:
    """Standardised Student-t shocks: unit variance, fat tails.

    A raw t with df=nu has variance nu/(nu-2), so drawing t directly would
    inflate volatility as well as fattening the tails and confound the two
    effects. Dividing by sqrt(nu/(nu-2)) isolates the tail behaviour, which is
    the whole point of using t here.
    """
    if df <= 2:
        df = 2.5
    raw = rng.standard_t(df, size=shape)
    return raw / math.sqrt(df / (df - 2.0))


def stationary_bootstrap_indices(rng: np.random.Generator, n_obs: int,
                                 n_steps: int, mean_block: float = 10.0) -> np.ndarray:
    """Politis & Romano (1994) stationary bootstrap index path.

    Block lengths are geometric with mean `mean_block`, so the resampled series
    is stationary (a fixed block length is not) while still preserving the
    volatility clustering that iid resampling destroys.
    """
    p = 1.0 / max(mean_block, 1.0)
    idx = np.empty(n_steps, dtype=np.int64)
    cur = rng.integers(0, n_obs)
    for t in range(n_steps):
        idx[t] = cur
        if rng.random() < p:
            cur = rng.integers(0, n_obs)
        else:
            cur = (cur + 1) % n_obs
    return idx


# ------------------------------------------------------------- risk measures

@dataclass
class RiskResult:
    model: str
    horizon_days: int
    n_paths: int
    mean_return_pct: float
    median_return_pct: float
    volatility_pct: float
    var_95_pct: float
    var_99_pct: float
    cvar_95_pct: float
    cvar_99_pct: float
    prob_loss_pct: float
    worst_path_pct: float
    best_path_pct: float
    var_95_stderr_pct: float
    mean_stderr_pct: float
    skew: float
    excess_kurtosis: float

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


def _summarise(terminal: np.ndarray, model: str, horizon: int) -> RiskResult:
    r = (terminal - 1.0) * 100.0
    mean, sd = float(r.mean()), float(r.std(ddof=1))
    z = (r - mean) / sd if sd > 0 else r * 0
    var95 = float(np.percentile(r, 5))
    var99 = float(np.percentile(r, 1))
    tail95 = r[r <= var95]
    tail99 = r[r <= var99]
    return RiskResult(
        model=model, horizon_days=horizon, n_paths=int(r.size),
        mean_return_pct=mean, median_return_pct=float(np.median(r)),
        volatility_pct=sd,
        var_95_pct=var95, var_99_pct=var99,
        cvar_95_pct=float(tail95.mean()) if tail95.size else var95,
        cvar_99_pct=float(tail99.mean()) if tail99.size else var99,
        prob_loss_pct=float((r < 0).mean() * 100),
        worst_path_pct=float(r.min()), best_path_pct=float(r.max()),
        var_95_stderr_pct=_quantile_stderr(r, 0.05),
        mean_stderr_pct=_mc_stderr(r),
        skew=float((z ** 3).mean()), excess_kurtosis=float((z ** 4).mean() - 3.0))


def simulate_portfolio_risk(returns_by_asset: dict[str, Sequence[float]],
                            weights: dict[str, float],
                            horizon_days: int = 21,
                            n_paths: int = DEFAULT_PATHS,
                            student_t_df: float = 4.0,
                            seed: int = 42) -> dict:
    """Portfolio VaR / Expected Shortfall under three return models.

    `returns_by_asset` is DAILY simple returns per asset; `weights` are portfolio
    weights (need not sum to 1 — they are normalised, and the normalisation is
    reported).

    The three models answer the same question with different tail assumptions:
      gaussian   the textbook / RiskMetrics assumption
      student_t  same mean and volatility, fat tails (df is exposed)
      bootstrap  stationary block resample of the ACTUAL joint history, which
                 keeps real skew, real fat tails and volatility clustering, and
                 assumes only that the past is a fair sample of the future

    Reading them together is the point: if Gaussian VaR is materially milder than
    the bootstrap, the portfolio's real risk is in the tail that Gaussian VaR
    cannot see.
    """
    names = [a for a in returns_by_asset if a in weights]
    if not names:
        return {"computable": False, "reason": "no overlapping assets and weights"}
    mat = [np.asarray(returns_by_asset[a], dtype=float) for a in names]
    n_obs = min(len(x) for x in mat)
    if n_obs < 60:
        return {"computable": False,
                "reason": f"need ≥60 overlapping daily returns, have {n_obs}"}
    R = np.vstack([x[-n_obs:] for x in mat]).T          # (n_obs, k)
    w_raw = np.array([weights[a] for a in names], dtype=float)
    total = w_raw.sum()
    if total <= 0:
        return {"computable": False, "reason": "weights sum to zero"}
    w = w_raw / total

    mu = R.mean(axis=0)
    sd = R.std(axis=0, ddof=1)
    corr = np.corrcoef(R, rowvar=False) if len(names) > 1 else np.array([[1.0]])
    psd_corr, repaired = nearest_psd_correlation(np.atleast_2d(corr))
    rng = np.random.default_rng(seed)

    results: dict[str, RiskResult] = {}

    # --- gaussian & student-t: same mu/sigma, different tail shape
    z = _correlated_normals(rng, n_paths, horizon_days, psd_corr)
    gauss_paths = mu + sd * z
    results["gaussian"] = _summarise(
        np.prod(1.0 + gauss_paths @ w, axis=1), "gaussian", horizon_days)

    t_raw = _student_t_shocks(rng, (n_paths, horizon_days, len(names)), student_t_df)
    L = _safe_cholesky(psd_corr)
    t_corr = t_raw @ L.T
    results["student_t"] = _summarise(
        np.prod(1.0 + (mu + sd * t_corr) @ w, axis=1), f"student_t(df={student_t_df:g})",
        horizon_days)

    # --- stationary block bootstrap of the real joint history
    port_hist = R @ w
    boot = np.empty(n_paths)
    mean_block = max(5.0, min(20.0, n_obs / 20.0))
    for i in range(n_paths):
        idx = stationary_bootstrap_indices(rng, n_obs, horizon_days, mean_block)
        boot[i] = np.prod(1.0 + port_hist[idx])
    results["bootstrap"] = _summarise(boot, "stationary bootstrap", horizon_days)

    gauss_var, boot_var = results["gaussian"].var_99_pct, results["bootstrap"].var_99_pct
    understatement = gauss_var - boot_var        # both negative; positive = gaussian milder
    return {
        "computable": True,
        "horizon_days": horizon_days,
        "n_paths": n_paths,
        "assets": names,
        "weights": {a: round(float(x), 4) for a, x in zip(names, w)},
        "weights_normalised_by": round(float(total), 4),
        "observations_used": int(n_obs),
        "annualised_vol_pct": round(float(np.sqrt(w @ np.cov(R, rowvar=False) @ w
                                                  if len(names) > 1 else R.var(ddof=1))
                                          * math.sqrt(TRADING_DAYS) * 100), 2),
        "correlation_repaired": repaired,
        "models": {k: v.to_dict() for k, v in results.items()},
        "tail_model_gap_99_pct": round(float(understatement), 3),
        "interpretation": (
            f"At 99% confidence over {horizon_days} trading days the Gaussian model "
            f"puts the loss at {gauss_var:.2f}% and the bootstrap of actual history at "
            f"{boot_var:.2f}%. "
            + ("The Gaussian number is the MILDER one, so it is understating the real "
               "tail — size positions against the bootstrap figure."
               if understatement > 0.25 else
               "The two broadly agree here, so the Gaussian assumption is not doing "
               "obvious damage at this horizon.")),
        "method": {
            "correlated_draws": "Cholesky factorisation of the sample correlation matrix",
            "variance_reduction": "antithetic variates on the Gaussian draws",
            "bootstrap": ("Politis–Romano stationary bootstrap, geometric block "
                          f"length mean {mean_block:.0f} days — preserves volatility "
                          "clustering that iid resampling destroys"),
            "es_note": ("Expected Shortfall (CVaR) is reported next to VaR because VaR "
                        "is not sub-additive and is silent about how bad the tail is "
                        "once breached (Artzner et al., 1999)."),
            "seed": seed,
        },
    }


# ------------------------------------------------------------ path behaviour

def simulate_drawdown_risk(daily_returns: Sequence[float],
                           horizon_days: int = TRADING_DAYS,
                           thresholds_pct: Sequence[float] = (-10, -20, -30, -50),
                           n_paths: int = 10_000, seed: int = 42) -> dict:
    """Probability of TOUCHING a drawdown at any point along the path.

    This is deliberately path-dependent. A terminal-value distribution can look
    survivable while most paths dipped through a level that would have breached a
    stop, triggered a margin call, or simply been abandoned. Position sizing and
    stop placement depend on the touch probability, not the endpoint.
    """
    r = np.asarray(daily_returns, dtype=float)
    if r.size < 60:
        return {"computable": False, "reason": f"need ≥60 daily returns, have {r.size}"}
    rng = np.random.default_rng(seed)
    mean_block = max(5.0, min(20.0, r.size / 20.0))
    max_dd = np.empty(n_paths)
    terminal = np.empty(n_paths)
    for i in range(n_paths):
        idx = stationary_bootstrap_indices(rng, r.size, horizon_days, mean_block)
        path = np.cumprod(1.0 + r[idx])
        peak = np.maximum.accumulate(path)
        max_dd[i] = float((path / peak - 1.0).min())
        terminal[i] = path[-1] - 1.0
    return {
        "computable": True,
        "horizon_days": horizon_days, "n_paths": n_paths,
        "median_max_drawdown_pct": round(float(np.median(max_dd)) * 100, 2),
        "p95_max_drawdown_pct": round(float(np.percentile(max_dd, 5)) * 100, 2),
        "worst_max_drawdown_pct": round(float(max_dd.min()) * 100, 2),
        "touch_probability_pct": {
            f"{t:g}%": round(float((max_dd <= t / 100.0).mean() * 100), 2)
            for t in thresholds_pct},
        "terminal_return_pct": {
            "median": round(float(np.median(terminal)) * 100, 2),
            "p05": round(float(np.percentile(terminal, 5)) * 100, 2),
            "p95": round(float(np.percentile(terminal, 95)) * 100, 2),
        },
        "note": ("Touch probability is the chance of EVER hitting the level during "
                 "the horizon, not of ending there. Stops and margin are triggered "
                 "by the path, not the endpoint — the two differ a lot."),
        "method": "stationary block bootstrap of the asset's own daily history",
    }


# ------------------------------------------------------- valuation uncertainty

def simulate_implied_growth(base_fcf: float, enterprise_value: float,
                            wacc_mean: float, wacc_sd: float = 0.015,
                            terminal_growth_mean: float = 0.04,
                            terminal_growth_sd: float = 0.01,
                            base_fcf_cv: float = 0.15,
                            horizon_years: int = 10,
                            n_paths: int = 5_000, seed: int = 42) -> dict:
    """Distribution of market-implied FCF growth under assumption uncertainty.

    The reverse DCF reports ONE implied growth rate conditional on a WACC and a
    terminal growth rate that are themselves estimates with real error. Sampling
    those assumptions turns a falsely precise point into an interval, which is
    the honest form of the answer: 'the market is pricing 12–19% growth' is a
    usable statement, '15.7%' is not.

    Draws are truncated to the region where a Gordon terminal value is defined
    (WACC must exceed terminal growth); the rejected fraction is reported rather
    than quietly resampled away.
    """
    from .valuation import TERMINAL_SPREAD_FLOOR, _pv_of_fcf

    if base_fcf <= 0 or enterprise_value <= 0:
        return {"computable": False,
                "reason": "needs positive base FCF and enterprise value"}
    rng = np.random.default_rng(seed)
    waccs = rng.normal(wacc_mean, wacc_sd, n_paths)
    tgs = rng.normal(terminal_growth_mean, terminal_growth_sd, n_paths)
    fcfs = base_fcf * (1.0 + rng.normal(0.0, base_fcf_cv, n_paths))

    solved: list[float] = []
    rejected = 0
    for wacc, tg, f0 in zip(waccs, tgs, fcfs):
        if f0 <= 0 or wacc - tg < TERMINAL_SPREAD_FLOOR:
            rejected += 1
            continue
        lo, hi = -0.50, min(tg + 0.60, wacc + 0.55)
        try:
            if _pv_of_fcf(f0, hi, wacc, horizon_years, tg) < enterprise_value:
                rejected += 1
                continue
            if _pv_of_fcf(f0, lo, wacc, horizon_years, tg) > enterprise_value:
                rejected += 1
                continue
            for _ in range(60):
                mid = (lo + hi) / 2
                if _pv_of_fcf(f0, mid, wacc, horizon_years, tg) < enterprise_value:
                    lo = mid
                else:
                    hi = mid
            solved.append((lo + hi) / 2 * 100)
        except ValueError:
            rejected += 1
    if len(solved) < 100:
        return {"computable": False,
                "reason": f"only {len(solved)} of {n_paths} draws produced a defined "
                          "solve — assumptions are too close to the Gordon boundary"}
    s = np.array(solved)
    return {
        "computable": True,
        "n_solved": int(s.size), "n_rejected": rejected,
        "implied_growth_pct": {
            "median": round(float(np.median(s)), 2),
            "mean": round(float(s.mean()), 2),
            "p05": round(float(np.percentile(s, 5)), 2),
            "p25": round(float(np.percentile(s, 25)), 2),
            "p75": round(float(np.percentile(s, 75)), 2),
            "p95": round(float(np.percentile(s, 95)), 2),
            "stderr": round(_mc_stderr(s), 3),
        },
        "assumptions_sampled": {
            "wacc": f"N({wacc_mean:.3f}, {wacc_sd:.3f})",
            "terminal_growth": f"N({terminal_growth_mean:.3f}, {terminal_growth_sd:.3f})",
            "base_fcf_cv": base_fcf_cv, "horizon_years": horizon_years,
        },
        "rejected_note": (
            f"{rejected} of {n_paths} draws were discarded as undefined (WACC within "
            f"{TERMINAL_SPREAD_FLOOR:.0%} of terminal growth, or a price no growth in "
            "range can justify). They are reported, not silently resampled — a high "
            "rejection rate means the assumptions themselves are near a boundary."),
        "interpretation": (
            "This is still NOT a forecast. It is the range of growth the market's "
            "price is consistent with, once the discount-rate and terminal-growth "
            "assumptions are allowed the uncertainty they actually have."),
    }


# ------------------------------------------------------------- goal planning

def simulate_goal(initial: float, monthly_contribution: float, years: int,
                  target: float, daily_returns: Optional[Sequence[float]] = None,
                  annual_return_mean: float = 0.11, annual_vol: float = 0.18,
                  n_paths: int = 10_000, seed: int = 42) -> dict:
    """Probability of reaching a corpus target under monthly investment (SIP).

    Uses the asset's own bootstrapped history when supplied, otherwise a stated
    Gaussian assumption. Reports the full terminal distribution because the
    median outcome and the 5th percentile lead to different decisions, and a
    single "expected corpus" number hides exactly that.
    """
    rng = np.random.default_rng(seed)
    months = years * 12
    if daily_returns is not None and len(daily_returns) >= 250:
        r = np.asarray(daily_returns, dtype=float)
        mean_block = max(5.0, min(20.0, r.size / 20.0))
        basis = "stationary bootstrap of supplied daily history"
        monthly = np.empty((n_paths, months))
        for i in range(n_paths):
            idx = stationary_bootstrap_indices(rng, r.size, months * 21, mean_block)
            d = (1.0 + r[idx]).reshape(months, 21)
            monthly[i] = d.prod(axis=1) - 1.0
    else:
        mu_m = (1 + annual_return_mean) ** (1 / 12) - 1
        sd_m = annual_vol / math.sqrt(12)
        basis = (f"Gaussian assumption: {annual_return_mean:.1%} annual mean, "
                 f"{annual_vol:.1%} annual volatility")
        monthly = rng.normal(mu_m, sd_m, (n_paths, months))

    corpus = np.full(n_paths, float(initial))
    for m in range(months):
        corpus = corpus * (1.0 + monthly[:, m]) + monthly_contribution
    hit = float((corpus >= target).mean() * 100)
    invested = initial + monthly_contribution * months
    return {
        "computable": True,
        "years": years, "months": months, "n_paths": n_paths,
        "total_invested": round(invested, 2),
        "probability_of_reaching_target_pct": round(hit, 2),
        "terminal_corpus": {
            "median": round(float(np.median(corpus)), 2),
            "mean": round(float(corpus.mean()), 2),
            "p05": round(float(np.percentile(corpus, 5)), 2),
            "p25": round(float(np.percentile(corpus, 25)), 2),
            "p75": round(float(np.percentile(corpus, 75)), 2),
            "p95": round(float(np.percentile(corpus, 95)), 2),
        },
        "probability_of_losing_money_pct": round(float((corpus < invested).mean() * 100), 2),
        "return_basis": basis,
        "note": ("Compare the median against the 5th percentile before deciding this "
                 "plan is safe: the gap between them IS the risk, and an 'expected "
                 "corpus' figure conceals it entirely."),
    }
