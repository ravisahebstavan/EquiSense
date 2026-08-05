"""Macro regime engine (§11).

Regimes are *conditioning descriptions*, never timing signals. Deliberately
coarse (2×2 core states + context flags): ~25 years of Indian data contain
single-digit regime episodes, so anything finer is regime-mining (§11).

Inputs: aligned macro series (oldest → newest closes).
"""
from __future__ import annotations

from typing import Optional, Sequence

from .types import Metric, fmt


def _trend(closes: Sequence[float], window: int = 200) -> Optional[float]:
    if len(closes) < window:
        return None
    ma = sum(closes[-window:]) / window
    return (closes[-1] / ma - 1) * 100


def _pctile(closes: Sequence[float], lookback: int = 756) -> Optional[float]:
    """Midrank percentile of the latest value within its own history.

    Counting `v <= current` puts a value tied with its own history at the 100th
    percentile — so a flat VIX classified a perfectly calm market as STRESSED,
    since every identical reading counted as being at or below itself. Midrank
    places a fully tied value at 50.
    """
    hist = closes[-lookback:]
    if len(hist) < 60:
        return None
    cur = closes[-1]
    less = sum(1 for v in hist if v < cur)
    equal = sum(1 for v in hist if v == cur)
    return (less + 0.5 * equal) / len(hist) * 100


def _ret(closes: Sequence[float], days: int) -> Optional[float]:
    if len(closes) < days + 1 or closes[-days - 1] <= 0:
        return None
    return (closes[-1] / closes[-days - 1] - 1) * 100


def classify_regime(nifty: Sequence[float], vix: Sequence[float],
                    inr: Sequence[float], crude: Sequence[float],
                    as_of: str = "") -> dict:
    """Returns the regime label, its components (each a Metric), and the
    conditioning key used to slice base-rate tables."""
    trend_pct = _trend(nifty)
    vix_pct = _pctile(vix)
    inr_3m = _ret(inr, 63)     # +ve = INR weakening (USDINR up)
    crude_3m = _ret(crude, 63)

    trend_state = None if trend_pct is None else ("uptrend" if trend_pct > 0 else "downtrend")
    vol_state = None if vix_pct is None else ("stressed" if vix_pct >= 75 else
                                              ("elevated" if vix_pct >= 50 else "calm"))
    core = f"{trend_state or 'unknown'}-{vol_state or 'unknown'}"

    flags = []
    if inr_3m is not None and inr_3m > 3:
        flags.append("INR weakening fast")
    if crude_3m is not None and crude_3m > 15:
        flags.append("crude spiking (import-bill stress)")
    if crude_3m is not None and crude_3m < -15:
        flags.append("crude falling (macro tailwind)")

    components = [
        Metric(key="nifty_trend", label="NIFTY vs 200DMA", value=trend_pct, unit="%",
               formula=f"NIFTY {fmt(nifty[-1] if nifty else None)} vs its 200DMA",
               inputs={"nifty_close": nifty[-1] if nifty else None},
               period=as_of, family="regime"),
        Metric(key="vix_percentile", label="India VIX Percentile (3y)", value=vix_pct,
               unit="pctile",
               formula=f"VIX {fmt(vix[-1] if vix else None, 2)} vs trailing 3y distribution",
               inputs={"vix": vix[-1] if vix else None}, period=as_of, family="regime"),
        Metric(key="inr_trend_3m", label="USD/INR 3m Change", value=inr_3m, unit="%",
               formula="63-day % change in USDINR (positive = rupee weakening)",
               inputs={"usdinr": inr[-1] if inr else None}, period=as_of, family="regime"),
        Metric(key="crude_trend_3m", label="Brent 3m Change", value=crude_3m, unit="%",
               formula="63-day % change in Brent crude",
               inputs={"brent": crude[-1] if crude else None}, period=as_of, family="regime"),
    ]
    return {
        "label": core,
        "conditioning_key": (trend_state or "unknown"),  # base rates slice on trend only (sample-size honesty)
        "flags": flags,
        "components": [m.to_dict() for m in components],
        "caveat": ("Regime is a description of present conditions used to condition "
                   "historical base rates — it is not a market-timing forecast (§7.2)."),
    }


def regime_series(nifty: Sequence[float]) -> list[str]:
    """Historical daily regime labels (trend axis only) for conditioning
    base-rate studies. Same definition as the live classifier — ex-ante, fixed."""
    out: list[str] = []
    for i in range(len(nifty)):
        if i + 1 < 200:
            out.append("unknown")
        else:
            ma = sum(nifty[i - 199:i + 1]) / 200
            out.append("uptrend" if nifty[i] > ma else "downtrend")
    return out
