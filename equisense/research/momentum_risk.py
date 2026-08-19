"""Risk-managed momentum — forecasting and defusing the crash (Barroso &
Santa-Clara 2015, "Momentum has its moments", J. Financial Economics).

Momentum is this platform's primary measured edge (§1), and it has one famous,
ruinous flaw: rare but violent CRASHES. They happen in panic rebounds — the
beaten-down losers the strategy is short rip upward together while the crowded
winners it is long stall — and a single one (2009: momentum fell ~-73% in three
months) erases years of the premium. The crashes are not the price of the edge;
they are a separable, and largely FORECASTABLE, risk.

Barroso & Santa-Clara's finding is that momentum's own realized volatility
predicts its crashes far better than market volatility does, and that scaling the
sleeve's exposure inversely to that realized vol — targeting a constant risk
level — removes almost all the crash and roughly DOUBLES the strategy's Sharpe.
It is not market timing: the scalar is a function of the strategy's own recent
risk, not a forecast of its direction.

This module measures exactly that for THIS system's 12-1 momentum factor (the same
`feat_momentum_12_1` the live verdicts use, so the risk model and the traded
signal can never diverge), and reports the exposure scalar and a crash-regime
flag. The scalar is a lever the sizing/autopilot layer can pull; the flag is a
caveat the decision layer can act on, mirroring the 200DMA trend filter.

Everything here is a pure function of a price panel, unit-tested against
hand-computed values (§15).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base_rates import feat_momentum_12_1

# The momentum sleeve's target annualised volatility. 12% is a deliberately
# moderate risk budget for a single factor; the scalar scales exposure toward it.
DEFAULT_TARGET_ANN_VOL = 0.12
# Trailing window for the strategy's realized vol. Barroso & Santa-Clara use ~6
# months of daily returns — long enough to be stable, short enough to react
# before a crash rather than after it.
VOL_WINDOW = 126
# Never lever the sleeve past this. Vol-scaling can suggest >1 in calm regimes; a
# cap keeps a quiet market from quietly building leverage that the next vol spike
# then punishes.
SCALAR_CAP = 2.0
TRADING_DAYS = 252
# Momentum vol in its own top decile is Barroso & Santa-Clara's danger zone.
CRASH_PERCENTILE = 0.90


def momentum_ls_returns(closes: pd.DataFrame, quantile: float = 0.2,
                        min_names: int = 10) -> pd.Series:
    """Daily equal-weight long-short 12-1 momentum return series.

    Long the top `quantile`, short the bottom `quantile`, ranked by YESTERDAY's
    12-1 momentum so the series is tradeable (no look-ahead). Fully vectorised.
    """
    rets = closes.pct_change()
    sig = feat_momentum_12_1(closes, None).shift(1)     # decide on prior-day signal
    ranks = sig.rank(axis=1, pct=True)
    valid = sig.notna().sum(axis=1)
    long_mask = ranks >= (1.0 - quantile)
    short_mask = ranks <= quantile
    long_r = rets.where(long_mask).mean(axis=1)
    short_r = rets.where(short_mask).mean(axis=1)
    ls = (long_r - short_r).where(valid >= min_names)
    return ls.dropna()


def scale_from_ls(ls: pd.Series, vol_window: int = VOL_WINDOW,
                  target_ann_vol: float = DEFAULT_TARGET_ANN_VOL,
                  cap: float = SCALAR_CAP) -> dict:
    """Barroso–Santa-Clara exposure scalar from a momentum return series.

    Pure and hand-verifiable: realized_ann_vol = std(last `vol_window` daily
    returns) × √252; scalar = min(cap, target / realized). Also reports where the
    current vol sits in its own history and whether it is in the crash-prone tail.
    """
    ls = ls.dropna()
    if len(ls) < vol_window + 5:
        return {"computable": False,
                "reason": f"need >{vol_window} daily momentum obs, have {len(ls)}"}
    realized = ls.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    hist = realized.dropna()
    cur = float(hist.iloc[-1]) if not hist.empty else 0.0
    if cur <= 0 or hist.empty:
        return {"computable": False, "reason": "degenerate (zero) momentum volatility"}
    scalar = min(cap, target_ann_vol / cur)
    pctile = float((hist < cur).mean())
    crash_prone = pctile >= CRASH_PERCENTILE
    return {
        "computable": True,
        "realized_ann_vol_pct": round(cur * 100, 2),
        "vol_percentile": round(pctile, 3),
        "target_ann_vol_pct": round(target_ann_vol * 100, 2),
        "exposure_scalar": round(float(scalar), 3),
        "scalar_cap": cap,
        "crash_prone": crash_prone,
        "note": (
            f"Momentum's own realized volatility is {cur * 100:.0f}%/yr, the "
            f"{pctile * 100:.0f}th percentile of its history. Risk-managed momentum "
            f"(Barroso & Santa-Clara 2015) scales the sleeve by "
            f"target/realized = {scalar:.2f}× to hold risk near {target_ann_vol * 100:.0f}%"
            + (". This is the crash-prone tail — momentum vol predicts its own "
               "crashes, so exposure is cut HARD here." if crash_prone else
               ". Not a direction call; a risk-targeting scalar on the sleeve.")),
    }


def risk_managed_momentum(closes: pd.DataFrame, quantile: float = 0.2,
                          vol_window: int = VOL_WINDOW,
                          target_ann_vol: float = DEFAULT_TARGET_ANN_VOL,
                          cap: float = SCALAR_CAP) -> dict:
    """End-to-end: build the 12-1 momentum L/S series from the panel and return
    its risk-management scalar and crash-regime flag."""
    if closes is None or getattr(closes, "empty", True) or closes.shape[1] < 10:
        return {"computable": False, "reason": "need ≥10 names to form momentum quantiles"}
    ls = momentum_ls_returns(closes, quantile=quantile)
    out = scale_from_ls(ls, vol_window=vol_window,
                        target_ann_vol=target_ann_vol, cap=cap)
    out["ls_observations"] = int(len(ls))
    out["citation"] = "Barroso & Santa-Clara (2015), Journal of Financial Economics"
    return out
