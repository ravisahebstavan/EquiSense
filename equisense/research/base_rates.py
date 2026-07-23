"""Base-rate studies (RESEARCH_BLUEPRINT §10.1 tier 1–2).

Cross-sectional event studies computed from the platform's OWN stored price
history — every T2 number in a dossier traces back to a study run here,
keyed to a registered hypothesis.

Leakage discipline: features at month-end t use data ≤ t only; outcomes use
(t, t+h] only. Excess return = stock forward return − universe median forward
return the same date (peer-relative, drift-neutral).

Known bias, stated on every record: the universe is *current* index
constituents backfilled — survivorship-tilted. Absolute levels are optimistic;
cross-sectional *rankings* are less affected. (§6.1: reconstructed grade.)
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import partial

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..engine.regime import regime_series
from ..models import BaseRateRecord, Company, MacroObservation, PriceObservation
from .registry import REGISTRY

SURVIVORSHIP_CAVEAT = ("universe=current constituents backfilled (survivorship-"
                       "tilted); prices PIT-safe, membership not")

# Phase II §8: overlap-corrected effective sample size. Monthly (21d) sampling
# with an h-day outcome window means consecutive episodes of the same stock
# share ≈(h-21)/h of their window; N_eff ≈ N × 21/h is the honest first-order
# correction (cross-sectional same-date correlation is partially removed by
# median-demeaning; residual commonality makes even this slightly generous —
# stated, not hidden).
SAMPLING_DAYS = 21
MIN_N_EFF = 30
DEFAULT_ROUND_TRIP_COST_PCT = 0.35   # statutory + typical large-cap impact
BROAD_COHORT_PCT = 40.0


def n_effective(n: int, horizon_days: int) -> int:
    return int(n * SAMPLING_DAYS / max(horizon_days, SAMPLING_DAYS))


def load_price_panel(session: Session) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(closes, volumes) DataFrames: index=date, columns=ticker."""
    rows = session.execute(
        select(PriceObservation.company_id, PriceObservation.obs_date,
               PriceObservation.close, PriceObservation.volume)).all()
    tickers = {c.id: c.ticker for c in session.scalars(select(Company)).all()}
    df = pd.DataFrame(rows, columns=["cid", "date", "close", "volume"])
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


def feat_momentum_quality(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    mom = closes.shift(21) / closes.shift(252) - 1
    rets = closes.pct_change()
    vol = rets.rolling(126, min_periods=100).std() * (252 ** 0.5)
    up = (rets > 0).rolling(231, min_periods=180).mean()
    return (mom / vol.replace(0, pd.NA)) * (0.5 + up)


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


def feat_max_effect(closes: pd.DataFrame, volumes) -> pd.DataFrame:
    """Lottery-demand / MAX effect (Bali, Cakici & Whitelaw 2011, J. Financial
    Economics, 'Maxing out: Stocks as lotteries and the cross-section of
    expected returns'): stocks with extreme recent single-day upside attract
    gambling-like retail demand and subsequently UNDERPERFORM. Negated here
    so the top quantile (via _top_quantile) selects LOW-MAX names — the
    expected-outperformance direction — consistent with every other feature's
    selection convention."""
    rets = closes.pct_change()
    max5 = rets.rolling(21, min_periods=15).apply(
        lambda x: pd.Series(x).nlargest(5).mean(), raw=False)
    return -max5


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
    cfg = cfg or STUDIES[hyp_id]
    feat = cfg["feature"](closes, volumes)
    month_ends = closes.index[::21]  # ~monthly sampling
    results = []
    for horizon in cfg["horizons"]:
        fwd = closes.shift(-horizon) / closes - 1  # (t, t+h] outcome
        excess_by_regime: dict[str, list[float]] = {"all": [], "uptrend": [], "downtrend": []}
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
            regime = regimes.get(t, "unknown")
            for r in (picks - universe_median).tolist():
                excess_by_regime["all"].append(r)
                if regime in excess_by_regime:
                    excess_by_regime[regime].append(r)
        breadth = float(pd.Series(breadths).mean()) if breadths else None
        for regime_key, vals in excess_by_regime.items():
            n_eff = n_effective(len(vals), horizon)
            if n_eff < MIN_N_EFF:
                continue  # publication gate re-based on N_eff (Phase II §8, A1)
            s = pd.Series(vals) * 100
            from .backtest import moving_block_bootstrap_ci
            ci_lo, ci_hi = moving_block_bootstrap_ci(
                [v * 100 for v in vals], block_len=max(1, horizon // SAMPLING_DAYS))
            caveats = [SURVIVORSHIP_CAVEAT,
                       f"N_eff={n_eff} (overlap-corrected from N={len(vals)}); "
                       "CIs should be read against N_eff"]
            if breadth is not None and breadth > BROAD_COHORT_PCT:
                caveats.append(f"broad cohort ({breadth:.0f}% of universe) — "
                               "cross-sectionally undistinctive by construction")
            results.append({
                "study_key": f"{REGISTRY[hyp_id]['name']}_{horizon}d",
                "evidence_family": REGISTRY[hyp_id]["family"],
                "registry_ref": hyp_id,
                "horizon_days": horizon,
                "regime_filter": regime_key,
                "n": len(vals),
                "n_eff": n_eff,
                "cohort_breadth_pct": None if breadth is None else round(breadth, 1),
                "hit_rate": float((s > 0).mean()),
                "mean_excess_pct": float(s.mean()),
                "median_excess_pct": float(s.median()),
                "net_median_excess_pct": float(s.median()) - DEFAULT_ROUND_TRIP_COST_PCT,
                "median_ci95_lo_pct": None if ci_lo != ci_lo else round(ci_lo, 2),
                "median_ci95_hi_pct": None if ci_hi != ci_hi else round(ci_hi, 2),
                "q25_excess_pct": float(s.quantile(0.25)),
                "q75_excess_pct": float(s.quantile(0.75)),
                "spec": json.dumps({"hypothesis": hyp_id,
                                    "spec": REGISTRY[hyp_id]["spec"],
                                    "sampling": "monthly (21d)",
                                    "excess_vs": "universe median same-date",
                                    "cost_model_pct": DEFAULT_ROUND_TRIP_COST_PCT,
                                    "caveats": caveats}),
            })
    return results


def run_all_studies(session: Session) -> dict:
    closes, volumes = load_price_panel(session)
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

    session.execute(delete(BaseRateRecord))  # full recompute; records are cache, ledger is truth
    total = 0
    for hyp_id, cfg in all_studies.items():
        for rec in run_study(closes, volumes, regimes, hyp_id, cfg=cfg):
            session.add(BaseRateRecord(**rec, computed_at=datetime.utcnow()))
            total += 1
    session.commit()
    return {"records": total, "universe": closes.shape[1],
            "history_days": closes.shape[0],
            "caveat": SURVIVORSHIP_CAVEAT}


def get_base_rate(session: Session, study_key_prefix: str, horizon_days: int,
                  regime: str = "all") -> dict | None:
    row = session.scalars(
        select(BaseRateRecord)
        .where(BaseRateRecord.study_key == f"{study_key_prefix}_{horizon_days}d",
               BaseRateRecord.regime_filter == regime)).first()
    if row is None and regime != "all":  # shrink to unconditional when cell thin (§12.2)
        return get_base_rate(session, study_key_prefix, horizon_days, "all")
    if row is None:
        return None
    return {"study_key": row.study_key, "registry_ref": row.registry_ref,
            "regime": row.regime_filter, "horizon_days": row.horizon_days,
            "n": row.n, "n_eff": row.n_eff,
            "cohort_breadth_pct": row.cohort_breadth_pct,
            "hit_rate": round(row.hit_rate, 3),
            "median_excess_pct": round(row.median_excess_pct, 2),
            "net_median_excess_pct": None if row.net_median_excess_pct is None
            else round(row.net_median_excess_pct, 2),
            "mean_excess_pct": round(row.mean_excess_pct, 2),
            "iqr_excess_pct": [round(row.q25_excess_pct, 2), round(row.q75_excess_pct, 2)],
            "caveat": SURVIVORSHIP_CAVEAT}
