"""REG-001: does regime conditioning improve out-of-sample calibration?
(§11 — the regime engine is itself a registered hypothesis.)

Design: momentum-top-quintile episodes (the family regime conditioning is
actually used on), split at the median episode date. On the train half, fit
(a) the unconditional hit rate and (b) per-regime hit rates (with shrinkage
toward unconditional for thin cells). On the test half, Brier-score both as
probability forecasts of each episode's outcome. Conditional must beat
unconditional out-of-sample, or the regime engine demotes to descriptive
dashboard context.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from ..engine.regime import regime_series
from .base_rates import (STUDIES, _top_quantile, load_nifty, load_price_panel,
                         n_effective)

SHRINK_N = 50  # pseudo-observations pulling regime cells toward unconditional


def _episodes(closes: pd.DataFrame, volumes: pd.DataFrame,
              regimes: pd.Series, horizon: int = 126) -> pd.DataFrame:
    feat = STUDIES["HYP-001"]["feature"](closes, volumes)
    fwd = closes.shift(-horizon) / closes - 1
    rows = []
    for t in closes.index[::21]:
        if t not in feat.index or t not in fwd.index:
            continue
        mask = _top_quantile(feat.loc[t], 0.2)
        orow = fwd.loc[t]
        med = orow.dropna().median()
        if pd.isna(med):
            continue
        for ticker, r in orow[mask & orow.notna()].items():
            rows.append({"date": t, "regime": regimes.get(t, "unknown"),
                         "win": (r - med) > 0})
    return pd.DataFrame(rows)


def run_reg001(session: Session, horizon: int = 126) -> dict:
    closes, volumes = load_price_panel(session)
    nifty = load_nifty(session)
    regimes = pd.Series(regime_series(nifty.tolist()), index=nifty.index)
    regimes = regimes.reindex(closes.index, method="ffill").fillna("unknown")

    ep = _episodes(closes, volumes, regimes, horizon)
    if len(ep) < 200:
        return {"hypothesis": "REG-001", "verdict": "insufficient_data",
                "episodes": len(ep)}
    dates = pd.to_datetime(ep["date"])
    split = dates.median()
    train, test = ep[dates <= split], ep[dates > split]

    p_uncond = train["win"].mean()
    counts = train.groupby("regime")["win"].agg(["sum", "count"])
    p_regime = {}
    for reg, row in counts.iterrows():
        # shrinkage toward unconditional: thin cells borrow strength (§11)
        p_regime[reg] = (row["sum"] + SHRINK_N * p_uncond) / (row["count"] + SHRINK_N)

    y = test["win"].astype(float)
    brier_uncond = float(((p_uncond - y) ** 2).mean())
    preds = test["regime"].map(lambda r: p_regime.get(r, p_uncond))
    brier_cond = float(((preds - y) ** 2).mean())

    improvement = brier_uncond - brier_cond
    verdict = "conditioning_helps" if improvement > 0.001 else (
        "no_measurable_value" if improvement > -0.001 else "conditioning_hurts")
    return {
        "hypothesis": "REG-001",
        "spec": f"momentum top-quintile, {horizon}d horizon, median-date split, "
                f"shrinkage n={SHRINK_N}",
        "train_episodes": len(train), "test_episodes": len(test),
        "test_n_eff": n_effective(len(test), horizon),
        "train_hit_rate_unconditional": round(float(p_uncond), 3),
        "train_hit_rate_by_regime": {k: round(float(v), 3) for k, v in p_regime.items()},
        "brier_unconditional_oos": round(brier_uncond, 5),
        "brier_conditional_oos": round(brier_cond, 5),
        "improvement": round(improvement, 5),
        "verdict": verdict,
        "consequence": ("regime conditioning keeps its role in base-rate slicing"
                        if verdict == "conditioning_helps" else
                        "regime demotes to descriptive context; base rates serve "
                        "unconditional cells until a better regime definition passes"),
        "caveats": ["single split (not walk-forward) — a first-pass test, not a "
                    "confirmatory one", "overlapping episodes: read against N_eff",
                    "survivorship-tilted universe"],
    }
