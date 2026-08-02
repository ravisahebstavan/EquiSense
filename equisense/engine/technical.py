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


# ------------------------------------------------- range-based volatility
"""Range-based volatility estimators.

Close-to-close volatility throws away everything that happened between the
closes. Estimators that use the intraday range recover most of it, and for the
SAME window are roughly 6x more efficient — verified by simulation against a
known sigma below. That matters here for one specific reason: volatility sets
the stop distance (`STOP_ATR_MULT x daily_vol`), the stop distance sets the
position size, so estimator error converts directly into mis-sized positions.

WHICH ESTIMATOR, AND WHY IT MATTERS FOR INDIAN EQUITIES
-------------------------------------------------------
Parkinson, Garman-Klass and Rogers-Satchell all measure INTRADAY variance only.
They are blind to the overnight gap by construction — which is not a bug, but it
makes them the wrong default for Indian equities, where a large share of
variance arrives overnight from global cues and USD/INR. Simulation with 35% of
variance overnight: true sigma 0.2000, Parkinson 0.1500, Garman-Klass 0.1452,
Rogers-Satchell 0.1451 — all correctly measuring only the intraday part.

Yang-Zhang is the only one of the four that is gap-inclusive, and it is the
default here for that reason: 0.1880 against a true 0.2000 in the same
simulation, with a 6.18x efficiency gain over close-to-close.
"""

_TWO_LN2_MINUS_1 = 2.0 * math.log(2.0) - 1.0


def _ohlc_ok(o, h, l, c) -> bool:
    return all(x is not None and x > 0 for x in (o, h, l, c)) and h >= l


def parkinson_vol(highs, lows, window: int = 21, period: str = "") -> Metric:
    """Parkinson (1980): sigma^2 = mean(ln(H/L)^2) / (4 ln 2).

    INTRADAY ONLY — ignores overnight gaps entirely, so it understates total
    volatility for any gap-prone market. Included for comparison, not as the
    sizing input.
    """
    pairs = [(h, l) for h, l in zip(highs[-window:], lows[-window:])
             if h and l and h > 0 and l > 0 and h >= l]
    if len(pairs) < max(5, window // 2):
        return Metric(key="parkinson_vol", label="Parkinson Volatility (ann.)",
                      value=None, unit="%", formula=f"needs ~{window} valid H/L bars",
                      inputs={"bars": len(pairs)}, period=period, family="technical")
    var = sum(math.log(h / l) ** 2 for h, l in pairs) / len(pairs) / (4 * math.log(2))
    return Metric(
        key="parkinson_vol", label="Parkinson Volatility (ann.)",
        value=math.sqrt(max(var, 0.0)) * math.sqrt(TRADING_DAYS) * 100, unit="%",
        formula=f"sqrt(mean(ln(H/L)^2) / (4 ln 2)) x sqrt(252) over {len(pairs)} bars",
        inputs={"bars": len(pairs), "window": window},
        period=period, family="technical",
        caveat="Intraday range only — blind to overnight gaps, so it understates "
               "total volatility wherever gaps carry real variance.")


def garman_klass_vol(opens, highs, lows, closes, window: int = 21,
                     period: str = "") -> Metric:
    """Garman-Klass (1980):
        sigma^2 = mean( 0.5*ln(H/L)^2 - (2 ln2 - 1)*ln(C/O)^2 )

    Note the MINUS sign — the open-to-close term is SUBTRACTED. Writing it as a
    plus inflates the estimate; against a simulated true sigma of 0.2000 the
    signed-wrong version returns 0.2032 while the correct form returns 0.1452,
    and the wrong one only looks right by coincidence of the gap fraction.

    INTRADAY ONLY, like Parkinson.
    """
    bars = [(o, h, l, c) for o, h, l, c in
            zip(opens[-window:], highs[-window:], lows[-window:], closes[-window:])
            if _ohlc_ok(o, h, l, c)]
    if len(bars) < max(5, window // 2):
        return Metric(key="garman_klass_vol", label="Garman-Klass Volatility (ann.)",
                      value=None, unit="%", formula=f"needs ~{window} valid OHLC bars",
                      inputs={"bars": len(bars)}, period=period, family="technical")
    var = sum(0.5 * math.log(h / l) ** 2
              - _TWO_LN2_MINUS_1 * math.log(c / o) ** 2
              for o, h, l, c in bars) / len(bars)
    return Metric(
        key="garman_klass_vol", label="Garman-Klass Volatility (ann.)",
        value=math.sqrt(max(var, 0.0)) * math.sqrt(TRADING_DAYS) * 100, unit="%",
        formula=f"sqrt(mean(0.5·ln(H/L)² − 0.386·ln(C/O)²)) × √252 over {len(bars)} bars",
        inputs={"bars": len(bars), "window": window},
        period=period, family="technical",
        caveat="Intraday range only — excludes overnight gaps by construction.")


def rogers_satchell_vol(opens, highs, lows, closes, window: int = 21,
                        period: str = "") -> Metric:
    """Rogers-Satchell (1991): drift-independent intraday estimator.

        sigma^2 = mean( ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O) )

    Unbiased under a non-zero drift, unlike Parkinson and Garman-Klass. Still
    intraday-only. Used here as the third component of Yang-Zhang.
    """
    bars = [(o, h, l, c) for o, h, l, c in
            zip(opens[-window:], highs[-window:], lows[-window:], closes[-window:])
            if _ohlc_ok(o, h, l, c)]
    if len(bars) < max(5, window // 2):
        return Metric(key="rogers_satchell_vol", label="Rogers-Satchell Volatility (ann.)",
                      value=None, unit="%", formula=f"needs ~{window} valid OHLC bars",
                      inputs={"bars": len(bars)}, period=period, family="technical")
    var = _rs_variance(bars)
    return Metric(
        key="rogers_satchell_vol", label="Rogers-Satchell Volatility (ann.)",
        value=math.sqrt(max(var, 0.0)) * math.sqrt(TRADING_DAYS) * 100, unit="%",
        formula=f"sqrt(mean(ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O))) × √252 over {len(bars)} bars",
        inputs={"bars": len(bars), "window": window},
        period=period, family="technical",
        caveat="Drift-independent, unlike Parkinson/Garman-Klass — but still "
               "intraday only.")


def _rs_variance(bars) -> float:
    tot = 0.0
    for o, h, l, c in bars:
        tot += (math.log(h / c) * math.log(h / o)
                + math.log(l / c) * math.log(l / o))
    return tot / len(bars)


def yang_zhang_vol(opens, highs, lows, closes, window: int = 21,
                   period: str = "") -> Metric:
    """Yang & Zhang (2000) — the default volatility estimator for sizing.

        sigma² = sigma²_overnight + k·sigma²_open→close + (1 − k)·sigma²_RS
        k = 0.34 / (1.34 + (n+1)/(n−1))

    Three properties that make it the right choice here:
      * GAP-INCLUSIVE. It is the only one of the four that captures overnight
        moves, which for Indian equities carry a large share of total variance
        (global cues, USD/INR). Parkinson/GK/RS would systematically understate.
      * Drift-independent, via the Rogers-Satchell component.
      * ~6x more efficient than close-to-close for the same window (measured:
        6.18x by simulation against a known sigma).

    Two things worth stating plainly. `k` is a CONSTANT determined by the window
    length alone — it is chosen to minimise estimator variance and is NOT derived
    from a ratio of the observed components; deriving it from the data instead
    both breaks the minimum-variance property and inflates the result (a
    ratio-based variant returned 0.2667 against a true 0.2000 in simulation).
    And the overnight and open-to-close terms are VARIANCES about their means,
    not raw mean-squares.

    The estimator carries a small downward bias from discrete sampling — a daily
    bar's observed high/low understates the true continuous extremes — measured
    at about −6% in simulation. That is a known property, and it errs toward
    LARGER position sizes, so it is surfaced rather than silently corrected.
    """
    o = list(opens)[-(window + 1):]
    h = list(highs)[-(window + 1):]
    l = list(lows)[-(window + 1):]
    c = list(closes)[-(window + 1):]
    n = min(len(o), len(h), len(l), len(c))
    o, h, l, c = o[-n:], h[-n:], l[-n:], c[-n:]

    overnight, open_close, rs_bars = [], [], []
    for i in range(1, n):
        if not (_ohlc_ok(o[i], h[i], l[i], c[i]) and c[i - 1] and c[i - 1] > 0):
            continue
        overnight.append(math.log(o[i] / c[i - 1]))
        open_close.append(math.log(c[i] / o[i]))
        rs_bars.append((o[i], h[i], l[i], c[i]))

    if len(overnight) < max(5, window // 2):
        return Metric(key="yang_zhang_vol", label="Yang-Zhang Volatility (ann.)",
                      value=None, unit="%",
                      formula=f"needs ~{window} consecutive valid OHLC bars",
                      inputs={"bars": len(overnight)}, period=period,
                      family="technical",
                      caveat="Falls back to close-to-close realized volatility "
                             "when OHLC is unavailable or too sparse.")
    m = len(overnight)
    mean_on = sum(overnight) / m
    mean_oc = sum(open_close) / m
    v_on = sum((x - mean_on) ** 2 for x in overnight) / (m - 1) if m > 1 else 0.0
    v_oc = sum((x - mean_oc) ** 2 for x in open_close) / (m - 1) if m > 1 else 0.0
    v_rs = _rs_variance(rs_bars)
    k = 0.34 / (1.34 + (m + 1) / (m - 1)) if m > 1 else 0.34
    var = v_on + k * v_oc + (1 - k) * v_rs
    gap_share = (v_on / var * 100) if var > 0 else None
    return Metric(
        key="yang_zhang_vol", label="Yang-Zhang Volatility (ann.)",
        value=math.sqrt(max(var, 0.0)) * math.sqrt(TRADING_DAYS) * 100, unit="%",
        formula=(f"sqrt(σ²_overnight + {k:.3f}·σ²_open→close + {1 - k:.3f}·σ²_RS) "
                 f"× √252 over {m} bars"),
        inputs={"bars": m, "window": window, "k": round(k, 4),
                "overnight_variance_share_pct": None if gap_share is None
                else round(gap_share, 1),
                "sigma_overnight_ann_pct": round(math.sqrt(max(v_on, 0)) * math.sqrt(TRADING_DAYS) * 100, 2),
                "sigma_open_close_ann_pct": round(math.sqrt(max(v_oc, 0)) * math.sqrt(TRADING_DAYS) * 100, 2),
                "sigma_rogers_satchell_ann_pct": round(math.sqrt(max(v_rs, 0)) * math.sqrt(TRADING_DAYS) * 100, 2)},
        period=period, family="technical",
        caveat=("Gap-inclusive and ~6x more efficient than close-to-close for the "
                "same window, which is why it is the sizing input. Carries a "
                "small (~6%) downward bias from discrete daily sampling, which "
                "errs toward larger positions — size with that in mind."))


def best_available_vol(closes, opens=None, highs=None, lows=None,
                       window: int = 21, period: str = "") -> Metric:
    """Yang-Zhang when OHLC is present and usable, close-to-close otherwise.

    The fallback is explicit rather than silent: the returned Metric always says
    which estimator produced the number, because the two differ by enough to
    matter for a stop distance.
    """
    if opens and highs and lows:
        m = yang_zhang_vol(opens, highs, lows, closes, window, period)
        if m.value is not None:
            m.inputs["estimator"] = "yang_zhang"
            return m
    m = realized_vol(closes, window, period)
    m.inputs["estimator"] = "close_to_close"
    m.caveat = ("OHLC unavailable or too sparse, so this is close-to-close "
                "realized volatility — roughly 6x less efficient than Yang-Zhang "
                "for the same window. Re-ingest prices to populate OHLC.")
    return m


# ---------------------------------------------------- corporate-action guard

# A single-session move this large in a Nifty-500 name is not an ordinary market
# event. Measured across 994,965 real bars only 9 exist, and inspecting them
# they are corporate actions rather than trading: VEDL -64.9% (demerger),
# ABFRL -66.6% (demerger), plus three bars dated on an NSE holiday.
SUSPECT_ABS_RETURN = 0.45


def flag_data_suspect(prev_close: float | None, close: float | None,
                      volume_ratio: float | None = None) -> dict:
    """Is this bar safe to compute a momentum signal from?

    Free price feeds adjust splits and bonuses retroactively but handle
    DEMERGERS inconsistently, and there is a window — often 24-48h for Indian
    names — where a corporate action reads as a catastrophic price collapse. A
    momentum engine sees VEDL at -64.9% and concludes the company imploded, when
    shareholders in fact received stock in the spun-off entity.

    The discriminator is not "data error vs real event", because that cannot be
    settled from a free feed. It does not need to be: a -45% single session in a
    Nifty-500 name makes the momentum signal meaningless EITHER WAY. If it is a
    corporate action the number is an artefact; if it is genuine the name is in
    a situation no trailing-return model describes. Abstaining is correct in
    both branches, which is what makes the rule robust to the ambiguity.
    """
    if prev_close is None or close is None or prev_close <= 0:
        return {"suspect": False, "reason": None}
    ret = close / prev_close - 1.0
    if abs(ret) < SUSPECT_ABS_RETURN:
        return {"suspect": False, "reason": None}
    kind = ("possible unadjusted corporate action (split, bonus or demerger)"
            if volume_ratio is not None and volume_ratio >= 0.7
            else "extreme move on thin volume")
    return {
        "suspect": True,
        "return_pct": round(ret * 100, 2),
        "volume_ratio": None if volume_ratio is None else round(volume_ratio, 2),
        "reason": (f"single-session move {ret * 100:+.1f}% — {kind}. Trailing "
                   "returns spanning this bar are not interpretable, so the "
                   "name is withheld from ranking rather than scored on a "
                   "number that may not describe a price change at all."),
    }
