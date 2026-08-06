"""Base-rate studies (§9.1 tier 1–2).

Cross-sectional event studies computed from the platform's OWN stored price
history — every T2 number in a dossier traces back to a study run here,
keyed to a registered hypothesis.

Leakage discipline: features at month-end t use data ≤ t only; outcomes use
(t, t+h] only. Excess return = stock forward return − universe median forward
return the same date (peer-relative, drift-neutral).

Known bias, stated on every record: the universe is *current* index
constituents backfilled — survivorship-tilted. Absolute levels are optimistic;
cross-sectional *rankings* are less affected. (§6.1: reconstructed grade.)

INFERENCE (Wave S — replaces the Phase II first-order correction)
-----------------------------------------------------------------
The observations a cross-sectional event study produces are doubly dependent:
names selected on the same date share a market-wide shock, and h-day forward
windows sampled every 21 days overlap for ceil(h/21) consecutive dates. The
previous estimator, N_eff ≈ N × 21/h, corrected only the second effect and so
overstated independent information by roughly the cohort size — an order of
magnitude in practice (verified: N=1080 → old 360, honest 37).

Inference now runs through `research/stats.py`:
  * dependence clusters = date blocks spanning the overlap horizon;
  * the intraclass correlation is ESTIMATED from the observations (one-way
    random-effects ANOVA) rather than assumed, and drives a Kish design effect;
  * significance uses a Liang–Zeger cluster-robust SE on G−1 degrees of
    freedom with an exact Student-t p-value;
  * confidence intervals resample whole clusters (a moving-block bootstrap over
    a flattened pick list cannot preserve same-date dependence);
  * every study in a run is then FDR-controlled (Benjamini–Hochberg) and
    checked against the Harvey–Liu–Zhu |t| ≥ 3 hurdle for a new factor.

Records are ALWAYS written, never suppressed: a study that fails the power gate
is published with `admissible=False` and the reason. Suppression would destroy
the measurement; labelling it keeps the evidence and the honesty.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from functools import partial

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..engine.regime import regime_series
from ..models import BaseRateRecord, Company, MacroObservation, PriceObservation
from .registry import REGISTRY
from .stats import (HLZ_T_HURDLE, benjamini_hochberg, block_observations,
                    cluster_block_bootstrap_ci, cluster_robust_mean,
                    effective_sample_size, multiplicity_verdict)

SURVIVORSHIP_CAVEAT = ("universe=current constituents backfilled (survivorship-"
                       "tilted); prices PIT-safe, membership not")

SAMPLING_DAYS = 21
MIN_N_EFF = 30
MIN_CLUSTERS = 8                     # below this, cluster-robust inference is itself unreliable
DEFAULT_ROUND_TRIP_COST_PCT = 0.35   # statutory + typical large-cap impact
BROAD_COHORT_PCT = 40.0
FDR_ALPHA = 0.05


def overlap_span(horizon_days: int) -> int:
    """How many consecutive monthly sample dates share overlapping outcome
    windows — the temporal width of one dependence cluster."""
    return max(1, math.ceil(horizon_days / SAMPLING_DAYS))


def n_effective(n: int, horizon_days: int) -> int:
    """DEPRECATED time-overlap-only correction, retained because it is one
    factor of the full design effect and is still the right answer when there
    is exactly one observation per date.

    Do not use for cross-sectional cohorts: it ignores same-date commonality
    and overstates N_eff by ~the cohort size. Use
    `stats.effective_sample_size` on date-blocked clusters instead — that is
    what `run_study` does.
    """
    return int(n * SAMPLING_DAYS / max(horizon_days, SAMPLING_DAYS))


def load_price_panel(session: Session) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(closes, volumes) DataFrames: index=date, columns=ticker.

    Deliberately UNFILTERED — the corrected backtests need the delisted and
    departed names, which is the whole point of retaining them (§5.1).

    Read straight into pandas rather than through ``.all()``: materialising a
    million rows as Python Row objects first, only to rebuild them as a frame,
    dominated this call on the real deployment. Same rows, same order, without
    the intermediate objects.
    """
    stmt = select(PriceObservation.company_id, PriceObservation.obs_date,
                  PriceObservation.close, PriceObservation.volume)
    try:
        df = pd.read_sql(stmt, session.connection())
        df.columns = ["cid", "date", "close", "volume"]
    except Exception:                                  # noqa: BLE001 - driver quirk
        rows = session.execute(stmt).all()
        df = pd.DataFrame(rows, columns=["cid", "date", "close", "volume"])
    from ..db import note_rows
    note_rows("base_rates.load_price_panel", len(df))
    tickers = {c.id: c.ticker for c in session.scalars(select(Company)).all()}
    df["ticker"] = df["cid"].map(tickers)
    closes = df.pivot_table(index="date", columns="ticker", values="close").sort_index()
    volumes = df.pivot_table(index="date", columns="ticker", values="volume").sort_index()
    return closes, volumes


def load_nifty(session: Session) -> pd.Series:
    rows = session.execute(
        select(MacroObservation.obs_date, MacroObservation.close)
        .where(MacroObservation.symbol == "^NSEI")
        .order_by(MacroObservation.obs_date)).all()
    return pd.Series({d: c for d, c in rows}).sort_index()


def load_sector_map(session: Session) -> dict[str, str]:
    return {c.ticker: c.sector for c in session.scalars(select(Company)).all()}


# ---------------------------------------------------------- feature builders
# Each returns a DataFrame aligned to `closes` using ONLY past data.

def feat_momentum_12_1(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    return closes.shift(21) / closes.shift(252) - 1


def feat_near_52w_high(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    high = closes.rolling(252, min_periods=200).max()
    return (closes / high - 1) >= -0.05


def feat_above_200dma(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    return closes > closes.rolling(200, min_periods=200).mean()


# The CONTINUOUS forms the live system actually emits. HYP-002/003 register the
# boolean cohort versions above, which the IC study reports "not computable"
# (a boolean carries no cross-sectional ordering) — so the signals that vote on
# every live verdict had never been measured at all. These mirror
# engine/technical.py exactly, in the same units, so a signal cannot be tested
# under one definition and traded under another.

def feat_dist_52w_high(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    """% below the 52-week high (0 = at the high). Mirrors
    technical.pct_from_52w_high. Higher = nearer the high = the
    anchoring-continuation direction of HYP-002."""
    high = closes.rolling(252, min_periods=200).max()
    return (closes / high - 1) * 100


def feat_trend_200dma(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    """% above the 200-day average. Mirrors technical.trend_200dma's returned
    value (`above_pct`); the 21d slope it also reports is not the ranked
    quantity, so it is not part of the feature."""
    ma = closes.rolling(200, min_periods=200).mean()
    return (closes / ma - 1) * 100


def feat_rel_strength(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    """63d stock return minus the 63d index return, per
    technical.relative_strength.

    NOTE, and it is the whole point of measuring this one: the index term is a
    single scalar per date, identical for every name, so subtracting it CANNOT
    change the cross-sectional ordering. Rank IC here is therefore identical by
    construction to the rank IC of the raw 63d return — this signal adds no
    cross-sectional information beyond 63d momentum, it only relabels it. The
    index series is not even needed to measure it.
    """
    return (closes / closes.shift(63) - 1) * 100


def feat_momentum_quality(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    """MQI, vectorised. Must mirror engine.novel.momentum_quality EXACTLY —
    including the Wave S directional fix, where persistence is agreement with the
    SIGN of momentum rather than the raw up-day fraction. Testing a different
    construction from the one the dossier displays would make the base-rate table
    evidence about a signal the platform does not actually use."""
    mom = closes.shift(21) / closes.shift(252) - 1
    rets = closes.pct_change()
    vol = rets.rolling(126, min_periods=100).std() * (252 ** 0.5)
    up = (rets > 0).rolling(231, min_periods=180).mean()
    agreement = up.where(mom >= 0, 1.0 - up)
    return (mom / vol.replace(0, pd.NA)) * (0.5 + agreement)


def feat_participation_heat(closes: pd.DataFrame, volumes: pd.DataFrame) -> pd.DataFrame:
    vsurge = volumes.rolling(21, min_periods=15).mean() / \
        volumes.rolling(126, min_periods=100).mean()
    r63 = (closes / closes.shift(63) - 1) * 100
    return vsurge * r63.clip(lower=0) / 10


def feat_low_vol(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    return -(closes.pct_change().rolling(126, min_periods=100).std() * (252 ** 0.5))


def feat_sector_relative_momentum(closes: pd.DataFrame, volumes,
                                  sector_map: dict[str, str]) -> pd.DataFrame:
    """Industry-relative momentum (Moskowitz & Grinblatt 1999, J. Finance,
    'Do Industries Explain Momentum?'): 63d return relative to the stock's
    OWN sector average, distinct from — and historically often stronger
    than — momentum measured against the broad index."""
    r63 = closes / closes.shift(63) - 1
    sectors = pd.Series({t: sector_map.get(t, "Unknown") for t in r63.columns})
    sector_mean = r63.T.groupby(sectors).transform("mean").T
    return r63 - sector_mean


def _rolling_topk_mean(df: pd.DataFrame, window: int, min_periods: int,
                       k: int) -> pd.DataFrame:
    """Rolling mean of the k largest values in each window, vectorised.

    Exactly equivalent to ``df.rolling(window, min_periods=min_periods).apply(
    lambda x: pd.Series(x).nlargest(k).mean())`` — NaNs ignored, fewer than k
    valid points averaged over however many there are — but without building a
    pandas Series per window.

    That naive form is why this study stalled the daily cron: a 2488 x 500
    panel is 1.24 MILLION windows, each allocating a Series and running
    nlargest. It ran for over 200 seconds and was killed by the platform's
    function limit, so base rates silently stopped updating.
    """
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view

    values = df.to_numpy(dtype="float64", copy=False)
    n_rows = values.shape[0]
    out = np.full(values.shape, np.nan)

    def topk_mean(win: np.ndarray) -> np.ndarray:
        """win: (..., w) → mean of the k largest finite entries per row."""
        valid = ~np.isnan(win)
        count = valid.sum(axis=-1)
        # -inf never wins a "largest" comparison, so NaNs drop out naturally
        filled = np.where(valid, win, -np.inf)
        kk = min(k, win.shape[-1])
        topk = np.partition(filled, -kk, axis=-1)[..., -kk:]
        # windows with fewer than k valid points average over just those
        total = np.where(np.isfinite(topk), topk, 0.0).sum(axis=-1)
        res = total / np.maximum(np.minimum(count, k), 1)
        return np.where(count >= min_periods, res, np.nan)

    # Leading PARTIAL windows: pandas emits a value as soon as min_periods is
    # satisfied, before a full window exists. At most (window - min_periods)
    # rows, so a small loop here costs nothing and keeps the NaN pattern exact.
    for i in range(min_periods - 1, min(window - 1, n_rows)):
        out[i] = topk_mean(values[: i + 1].T)

    if n_rows >= window:
        # (n_rows-window+1, n_cols, window) view — no copy of the panel
        out[window - 1:] = topk_mean(sliding_window_view(values, window, axis=0))
    return pd.DataFrame(out, index=df.index, columns=df.columns)


def feat_max_effect(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    """Lottery-demand / MAX effect (Bali, Cakici & Whitelaw 2011, J. Financial
    Economics, 'Maxing out: Stocks as lotteries and the cross-section of
    expected returns'): stocks with extreme recent single-day upside attract
    gambling-like retail demand and subsequently UNDERPERFORM. Negated here
    so the top quantile (via _top_quantile) selects LOW-MAX names — the
    expected-outperformance direction — consistent with every other feature's
    selection convention."""
    rets = closes.pct_change()
    return -_rolling_topk_mean(rets, window=21, min_periods=15, k=5)


# selector: how a feature frame becomes a boolean "in cohort" mask at date t
def _top_quantile(feat_row: pd.Series, q: float) -> pd.Series:
    valid = feat_row.dropna()
    if len(valid) < 10:
        return pd.Series(False, index=feat_row.index)
    cutoff = valid.quantile(1 - q)
    return feat_row >= cutoff


STUDIES: dict[str, dict] = {
    "HYP-001": {"feature": feat_momentum_12_1, "select": ("quantile", 0.2),
                "horizons": [63, 126]},
    "HYP-002": {"feature": feat_near_52w_high, "select": ("bool",),
                "horizons": [63, 126]},
    "HYP-003": {"feature": feat_above_200dma, "select": ("bool",),
                "horizons": [63, 126]},
    "HYP-004": {"feature": feat_momentum_quality, "select": ("quantile", 0.2),
                "horizons": [63, 126]},
    "HYP-007": {"feature": feat_participation_heat, "select": ("quantile", 0.1),
                "horizons": [21, 63]},
    "HYP-008": {"feature": feat_low_vol, "select": ("quantile", 0.2),
                "horizons": [126]},
}


def run_study(closes: pd.DataFrame, volumes: pd.DataFrame,
              regimes: pd.Series, hyp_id: str, cfg: dict | None = None) -> list[dict]:
    """Run one registered hypothesis and return one record per (horizon, regime).

    Records are returned for every cell that has any observations at all. The
    power/admissibility decision lives in the record (`admissible`,
    `admissibility_reason`), not in whether the record exists — a measurement
    that failed its power gate is still a measurement, and hiding it would let
    the absence be mistaken for "not studied".
    """
    cfg = cfg or STUDIES[hyp_id]
    feat = cfg["feature"](closes, volumes)
    month_ends = closes.index[::SAMPLING_DAYS]  # ~monthly sampling
    results = []
    for horizon in cfg["horizons"]:
        fwd = closes.shift(-horizon) / closes - 1  # (t, t+h] outcome
        # Per-DATE pick cohorts, kept grouped: the grouping IS the dependence
        # structure that inference needs. Flattening here was the old bug.
        dated_by_regime: dict[str, list[tuple]] = {"all": [], "uptrend": [],
                                                   "downtrend": []}
        breadths: list[float] = []
        for t in month_ends:
            if t not in feat.index or t not in fwd.index:
                continue
            frow, orow = feat.loc[t], fwd.loc[t]
            if cfg["select"][0] == "quantile":
                mask = _top_quantile(frow, cfg["select"][1])
            else:
                mask = frow.fillna(False).astype(bool)
            universe_median = orow.dropna().median()
            if pd.isna(universe_median):
                continue
            n_universe = int(orow.notna().sum())
            picks = orow[mask & orow.notna()]
            if n_universe:
                breadths.append(len(picks) / n_universe * 100)
            excesses = [v * 100 for v in (picks - universe_median).tolist()]
            if not excesses:
                continue
            regime = regimes.get(t, "unknown")
            dated_by_regime["all"].append((t, excesses))
            if regime in dated_by_regime:
                dated_by_regime[regime].append((t, excesses))
        breadth = float(pd.Series(breadths).mean()) if breadths else None
        span = overlap_span(horizon)

        for regime_key, dated in dated_by_regime.items():
            if not dated:
                continue
            clusters = block_observations(dated, span)
            ess = effective_sample_size(clusters)
            mt = cluster_robust_mean(clusters)
            flat = [v for c in clusters for v in c]
            s = pd.Series(flat)
            ci_lo, ci_hi = cluster_block_bootstrap_ci(clusters, statistic="median")

            reasons = []
            if ess["n_eff"] < MIN_N_EFF:
                reasons.append(
                    f"underpowered: N_eff={ess['n_eff']} < {MIN_N_EFF} "
                    f"(N={ess['n']} observations collapse to ~{ess['n_clusters']} "
                    f"independent date blocks at ICC={ess['icc']})")
            if ess["n_clusters"] < MIN_CLUSTERS:
                reasons.append(
                    f"only {ess['n_clusters']} independent clusters "
                    f"(< {MIN_CLUSTERS}) — cluster-robust inference unreliable")
            if breadth is not None and breadth > BROAD_COHORT_PCT:
                reasons.append(f"broad cohort ({breadth:.0f}% of universe) — "
                               "cross-sectionally undistinctive by construction")

            caveats = [SURVIVORSHIP_CAVEAT,
                       f"N={ess['n']} observations in {ess['n_clusters']} independent "
                       f"date blocks (overlap span {span} sample dates, "
                       f"ICC={ess['icc']}, design effect {ess['design_effect']}) "
                       f"→ N_eff={ess['n_eff']}. Read every interval against N_eff.",
                       "CI resamples whole date clusters (cluster bootstrap), so it "
                       "reflects same-date commonality, not just serial overlap."]
            caveats.extend(reasons)

            results.append({
                "study_key": f"{REGISTRY[hyp_id]['name']}_{horizon}d",
                "evidence_family": REGISTRY[hyp_id]["family"],
                "registry_ref": hyp_id,
                "horizon_days": horizon,
                "regime_filter": regime_key,
                "n": ess["n"],
                "n_eff": ess["n_eff"],
                "n_clusters": ess["n_clusters"],
                "icc": ess["icc"],
                "design_effect": ess["design_effect"],
                "cohort_breadth_pct": None if breadth is None else round(breadth, 1),
                "hit_rate": float((s > 0).mean()),
                "mean_excess_pct": float(s.mean()),
                "median_excess_pct": float(s.median()),
                "net_median_excess_pct": float(s.median()) - DEFAULT_ROUND_TRIP_COST_PCT,
                "median_ci95_lo_pct": None if ci_lo != ci_lo else round(ci_lo, 2),
                "median_ci95_hi_pct": None if ci_hi != ci_hi else round(ci_hi, 2),
                "q25_excess_pct": float(s.quantile(0.25)),
                "q75_excess_pct": float(s.quantile(0.75)),
                # cluster-robust inference on the MEAN excess return
                "mean_se_pct": None if mt is None else round(mt.se, 4),
                "t_stat": None if mt is None else round(mt.t_stat, 3),
                "df": None if mt is None else mt.df,
                "p_value": None if mt is None else mt.p_value,
                # q_value / multiplicity verdict are filled in run-wide (FDR is
                # a property of the family of tests, not of one test)
                "q_value": None,
                "admissible": not reasons,
                "admissibility_reason": ("passes power and distinctiveness gates"
                                         if not reasons else "; ".join(reasons)),
                "spec": json.dumps({"hypothesis": hyp_id,
                                    "spec": REGISTRY[hyp_id]["spec"],
                                    "sampling": "monthly (21d)",
                                    "excess_vs": "universe median same-date",
                                    "cost_model_pct": DEFAULT_ROUND_TRIP_COST_PCT,
                                    "inference": "cluster-robust (Liang–Zeger) on "
                                                 "date blocks; exact Student-t; "
                                                 "BH-FDR across the run",
                                    "caveats": caveats}),
            })
    return results


def run_all_studies(session: Session, panel=None) -> dict:
    # see run_ic_studies: `panel` shares one load across several studies
    import logging
    import time as _time
    _slog = logging.getLogger("equisense.research")
    _t = _time.monotonic()
    closes, volumes = load_price_panel(session) if panel is None else panel
    _slog.info("studies: panel %s x %s loaded in %.1fs",
               closes.shape[0], closes.shape[1], _time.monotonic() - _t)
    nifty = load_nifty(session)
    regimes = pd.Series(regime_series(nifty.tolist()), index=nifty.index)
    regimes = regimes.reindex(closes.index, method="ffill").fillna("unknown")
    sector_map = load_sector_map(session)

    # Studies needing extra bound context (sector map) are built here rather
    # than in the static STUDIES dict, which stays session-independent.
    all_studies = dict(STUDIES)
    all_studies["HYP-010"] = {
        "feature": partial(feat_sector_relative_momentum, sector_map=sector_map),
        "select": ("quantile", 0.2), "horizons": [63, 126]}
    all_studies["HYP-011"] = {
        "feature": feat_max_effect, "select": ("quantile", 0.2), "horizons": [21, 63]}

    # Release the READ transaction before the long computation. The first
    # SELECT above opens a transaction and it stays open until commit/rollback,
    # so the minutes of compute that follow are minutes of idle-in-transaction —
    # Postgres kills that ("IdleInTransactionSessionTimeout") and the write at
    # the end fails. Moving only the write was not enough; the transaction opens
    # at the first read, not at the first write.
    session.rollback()

    # Compute BEFORE touching the database. This used to DELETE first, then run
    # the studies — minutes of pure computation — and only then insert, holding
    # a transaction open and idle the whole time. Neon's free tier closes idle
    # connections, so the run died on commit with "SSL connection has been
    # closed unexpectedly" and /api/live/studies/run was broken in production
    # while passing every local SQLite test.
    records: list[dict] = []
    for hyp_id, cfg in all_studies.items():
        _ts = _time.monotonic()
        records.extend(run_study(closes, volumes, regimes, hyp_id, cfg=cfg))
        _slog.info("studies: %s in %.1fs (%.1fs total)", hyp_id,
                   _time.monotonic() - _ts, _time.monotonic() - _t)

    # FDR is computed across the WHOLE run, so this cannot be split across days
    # without corrupting the correction — the run is all-or-nothing by design.
    apply_multiplicity_control(records)

    # One short transaction: delete and reinsert together, so a failure rolls
    # back to the previous records rather than leaving the table empty.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.execute(delete(BaseRateRecord))  # full recompute; records are cache, ledger is truth
    payload = [{**rec, "computed_at": now} for rec in records]
    CHUNK = 500                              # keep any single statement modest
    for i in range(0, len(payload), CHUNK):
        session.bulk_insert_mappings(BaseRateRecord, payload[i:i + CHUNK])
    session.commit()
    admissible = [r for r in records if r["admissible"]]
    survivors = [r for r in admissible if r.get("survives_multiplicity")]
    return {"records": len(records),
            "admissible": len(admissible),
            "survive_multiplicity": len(survivors),
            "tests_in_family": sum(1 for r in records if r.get("p_value") is not None),
            "universe": closes.shape[1],
            "history_days": closes.shape[0],
            "caveat": SURVIVORSHIP_CAVEAT,
            "multiplicity_note": (
                f"{len(records)} study cells computed; FDR controlled at "
                f"{FDR_ALPHA:.0%} (Benjamini–Hochberg) across the whole run, and "
                f"cross-checked against the Harvey–Liu–Zhu |t|≥{HLZ_T_HURDLE} "
                "hurdle for a newly proposed factor. Testing many cells at 95% "
                "confidence manufactures winners by construction; this is the "
                "correction for that.")}


def apply_multiplicity_control(records: list[dict], alpha: float = FDR_ALPHA) -> None:
    """Fill q_value / multiplicity verdict across a whole run, in place.

    FDR is computed only over the *primary* (unconditional, regime="all") cells.
    Including the per-regime slices would triple-count the same underlying
    episodes and make the correction both wrong and needlessly punitive; the
    regime cells inherit the parent study's verdict as context.
    """
    primary = [r for r in records
               if r["regime_filter"] == "all" and r.get("p_value") is not None]
    qs = benjamini_hochberg([r["p_value"] for r in primary], alpha=alpha)
    for r, q in zip(primary, qs):
        r["q_value"] = None if q is None else round(q, 5)
    by_study = {(r["study_key"]): r for r in primary}
    for r in records:
        parent = by_study.get(r["study_key"])
        if r["regime_filter"] != "all" and parent is not None:
            r["q_value"] = parent["q_value"]
        verdict = multiplicity_verdict(r.get("t_stat"), r.get("q_value"), alpha)
        r["multiplicity_verdict"] = verdict
        r["survives_multiplicity"] = verdict.startswith("survives FDR and")
        spec = json.loads(r["spec"])
        spec["multiplicity"] = {
            "family_size": len(primary),
            "q_value": r["q_value"],
            "t_stat": r.get("t_stat"),
            "verdict": verdict,
            "citation": "Benjamini & Hochberg (1995); Harvey, Liu & Zhu (2016), RFS",
        }
        r["spec"] = json.dumps(spec)


def _serialize_base_rate(row: BaseRateRecord) -> dict:
    return {"study_key": row.study_key, "registry_ref": row.registry_ref,
            "regime": row.regime_filter, "horizon_days": row.horizon_days,
            "n": row.n, "n_eff": row.n_eff,
            "n_clusters": row.n_clusters, "icc": row.icc,
            "design_effect": row.design_effect,
            "cohort_breadth_pct": row.cohort_breadth_pct,
            "hit_rate": round(row.hit_rate, 3),
            "median_excess_pct": round(row.median_excess_pct, 2),
            "net_median_excess_pct": None if row.net_median_excess_pct is None
            else round(row.net_median_excess_pct, 2),
            "mean_excess_pct": round(row.mean_excess_pct, 2),
            "median_ci95_pct": None if row.median_ci95_lo_pct is None else
            [row.median_ci95_lo_pct, row.median_ci95_hi_pct],
            "iqr_excess_pct": [round(row.q25_excess_pct, 2), round(row.q75_excess_pct, 2)],
            "t_stat": row.t_stat, "p_value": row.p_value, "q_value": row.q_value,
            "df": row.df,
            "admissible": bool(row.admissible),
            "admissibility_reason": row.admissibility_reason,
            "multiplicity_verdict": row.multiplicity_verdict,
            "survives_multiplicity": bool(row.survives_multiplicity),
            "caveat": SURVIVORSHIP_CAVEAT}


def get_base_rate(session: Session, study_key_prefix: str, horizon_days: int,
                  regime: str = "all", admissible_only: bool = True) -> dict | None:
    """Look up a stored study cell.

    `admissible_only` (the default) is what callers attaching T2 evidence want:
    an underpowered cell must not be dressed up as a validated base rate. Pass
    False to retrieve the measurement anyway — the Lab surfaces every cell,
    including the ones that failed their gates, because "we looked and it was
    too thin to say" is itself a finding.

    Regime fallback: a thin conditional cell shrinks to the unconditional one
    (§9.1), and the returned record says which regime it actually came from.
    """
    row = session.scalars(
        select(BaseRateRecord)
        .where(BaseRateRecord.study_key == f"{study_key_prefix}_{horizon_days}d",
               BaseRateRecord.regime_filter == regime)).first()
    if row is not None and admissible_only and not row.admissible:
        row = None
    if row is None and regime != "all":  # shrink to unconditional when cell thin (§9.1)
        return get_base_rate(session, study_key_prefix, horizon_days, "all",
                             admissible_only=admissible_only)
    if row is None:
        return None
    return _serialize_base_rate(row)
