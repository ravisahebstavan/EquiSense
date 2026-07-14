"""Technical engine (RESEARCH_BLUEPRINT §7.2).

Small, defensible roster only: cross-sectional momentum (12-1), 52-week-high
proximity, 200DMA trend regime, volatility structure, relative strength vs.
index, volume anomaly, ATR-proxy stop distance. Indicator folklore is
deliberately absent; anything here must be validatable by the base-rate
module against the platform's own stored history.

Pure functions over aligned (dates, closes[, volumes]) sequences,
oldest → newest. All outputs are Metric objects (show-the-work preserved).
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional, Sequence

from .types import Metric, fmt

TRADING_DAYS = 252


def _ret(closes: Sequence[float], lookback: int, skip: int = 0) -> Optional[float]:
    """Total return over [t-lookback-skip, t-skip]."""
    if len(closes) < lookback + skip + 1:
        return None
    end = closes[-1 - skip]
    start = closes[-1 - skip - lookback]
    if start <= 0:
        return None
    return end / start - 1


def momentum_12_1(closes: Sequence[float], period: str = "") -> Metric:
    """Classic 12-1 momentum: trailing 12m return excluding the most recent
    month (short-term reversal exclusion). The single most robust equity
    anomaly in Indian academic studies."""
    r = _ret(closes, TRADING_DAYS - 21, skip=21)
    return Metric(
        key="momentum_12_1", label="Momentum (12-1)",
        value=None if r is None else r * 100, unit="%",
        formula="Return over trailing 252d excluding most recent 21d",
        inputs={"window_days": TRADING_DAYS - 21, "skip_days": 21,
                "observations": len(closes)},
        period=period, family="technical")


def pct_from_52w_high(closes: Sequence[float], period: str = "") -> Metric:
    window = closes[-TRADING_DAYS:]
    if not window:
        return Metric(key="pct_from_52w_high", label="Distance from 52w High",
                      value=None, unit="%", formula="", inputs={}, period=period,
                      family="technical")
    high = max(window)
    v = (closes[-1] / high - 1) * 100 if high > 0 else None
    return Metric(
        key="pct_from_52w_high", label="Distance from 52w High", value=v, unit="%",
        formula=f"Close {fmt(closes[-1])} / 52w high {fmt(high)} − 1",
        inputs={"close": closes[-1], "high_52w": high},
        period=period, family="technical")


def trend_200dma(closes: Sequence[float], period: str = "") -> Metric:
    """Price vs. 200DMA and the 200DMA's own 21d slope — the trend regime."""
    if len(closes) < 221:
        return Metric(key="trend_200dma", label="Trend vs 200DMA", value=None,
                      unit="%", formula="needs ≥221 observations", inputs={},
                      period=period, family="technical")
    ma_now = sum(closes[-200:]) / 200
    ma_prev = sum(closes[-221:-21]) / 200
    above_pct = (closes[-1] / ma_now - 1) * 100
    slope_pct = (ma_now / ma_prev - 1) * 100
    return Metric(
        key="trend_200dma", label="Trend vs 200DMA", value=above_pct, unit="%",
        formula=f"Close {fmt(closes[-1])} vs 200DMA {fmt(ma_now)}; "
                f"200DMA 21d slope {slope_pct:+.2f}%",
        inputs={"close": closes[-1], "ma200": ma_now, "ma200_slope_21d_pct": slope_pct},
        period=period, family="technical")


def realized_vol(closes: Sequence[float], window: int = 63,
                 period: str = "") -> Metric:
    if len(closes) < window + 1:
        return Metric(key="realized_vol", label="Realized Volatility (ann.)",
                      value=None, unit="%", formula="", inputs={}, period=period,
                      family="technical")
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - window, len(closes))
            if closes[i - 1] > 0]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    vol = math.sqrt(var) * math.sqrt(TRADING_DAYS) * 100
    return Metric(
        key="realized_vol", label="Realized Volatility (ann.)", value=vol, unit="%",
        formula=f"Stdev of {window}d log returns × √252",
        inputs={"window_days": window}, period=period, family="technical")


def vol_contraction(closes: Sequence[float], period: str = "") -> Metric:
    """Short-window vol / long-window vol. <1 = contraction (coiling),
    >1 = expansion. Contraction preceding breakouts is the tested hypothesis,
    not an article of faith — see the base-rate module."""
    short = realized_vol(closes, 21).value
    long_ = realized_vol(closes, 126).value
    v = None if (short is None or long_ in (None, 0)) else short / long_
    return Metric(
        key="vol_contraction", label="Volatility Contraction (21d/126d)",
        value=v, unit="x",
        formula=f"21d vol {fmt(short)}% / 126d vol {fmt(long_)}%",
        inputs={"vol_21d": short, "vol_126d": long_},
        period=period, family="technical")


def relative_strength(closes: Sequence[float], index_closes: Sequence[float],
                      window: int = 63, period: str = "") -> Metric:
    r_stock = _ret(closes, window)
    r_index = _ret(index_closes, window)
    v = None if (r_stock is None or r_index is None) else (r_stock - r_index) * 100
    return Metric(
        key="relative_strength", label=f"Relative Strength vs NIFTY ({window}d)",
        value=v, unit="%",
        formula=f"Stock {fmt(None if r_stock is None else r_stock * 100)}% − "
                f"NIFTY {fmt(None if r_index is None else r_index * 100)}% over {window}d",
        inputs={"stock_return_pct": None if r_stock is None else r_stock * 100,
                "index_return_pct": None if r_index is None else r_index * 100},
        period=period, family="technical")


def volume_anomaly(volumes: Sequence[Optional[float]], period: str = "") -> Metric:
    """21d avg volume vs 126d avg volume — participation surge/drain."""
    vals = [v for v in volumes if v]
    if len(vals) < 126:
        return Metric(key="volume_anomaly", label="Volume Surge (21d/126d)",
                      value=None, unit="x", formula="needs ≥126 volume obs",
                      inputs={}, period=period, family="technical")
    v21 = sum(vals[-21:]) / 21
    v126 = sum(vals[-126:]) / 126
    return Metric(
        key="volume_anomaly", label="Volume Surge (21d/126d)",
        value=None if v126 == 0 else v21 / v126, unit="x",
        formula=f"21d avg vol {fmt(v21, 0)} / 126d avg vol {fmt(v126, 0)}",
        inputs={"avg_vol_21d": v21, "avg_vol_126d": v126},
        period=period, family="technical")


def adv_crore(closes: Sequence[float], volumes: Sequence[Optional[float]],
              window: int = 63) -> Optional[float]:
    """Average daily traded value in ₹ crore — the liquidity input for sizing."""
    pairs = [(c, v) for c, v in zip(closes[-window:], volumes[-window:]) if v]
    if not pairs:
        return None
    return sum(c * v for c, v in pairs) / len(pairs) / 1e7


def max_drawdown(closes: Sequence[float], period: str = "") -> Metric:
    peak, mdd = float("-inf"), 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    return Metric(
        key="max_drawdown", label="Max Drawdown (window)", value=mdd * 100,
        unit="%", formula="Worst peak-to-trough decline over the window",
        inputs={"observations": len(closes)}, period=period, family="technical")


def all_technical(closes: Sequence[float], volumes: Sequence[Optional[float]],
                  index_closes: Sequence[float], as_of: Optional[date] = None) -> list[Metric]:
    period = as_of.isoformat() if as_of else ""
    return [
        momentum_12_1(closes, period),
        pct_from_52w_high(closes, period),
        trend_200dma(closes, period),
        realized_vol(closes, period=period),
        vol_contraction(closes, period),
        relative_strength(closes, index_closes, period=period),
        volume_anomaly(volumes, period),
        max_drawdown(closes[-TRADING_DAYS:], period),
    ]
