"""Boundary validation for provider payloads (§5.2).

Every number in this system originates outside it, from free endpoints with no
contract and no support channel. Until now nothing stood between what a provider
returned and what became canonical stored data: a NaN, a zero, or an inverted
high/low went straight into `price_observations` and from there into returns,
volatility, correlation, percentile ranks and base rates — silently, because
every consumer downstream is written to trust the store.

The rule this module enforces is narrow on purpose: **reject a bar only when the
field is unusable, and degrade rather than discard when only part of it is.** A
bar with a good close and an inverted intraday range is still a valid close;
dropping it would tear a hole in the return series to protect a volatility
estimator that already knows how to say "OHLC unavailable". A bar with no close
is not a bar at all.

The counts are returned, not logged and forgotten. A provider that starts
serving garbage should show up as a number on the data-health panel on the day
it starts, not as an unexplained drift in a base rate a month later.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def _finite(x) -> float | None:
    """Coerce to a finite float, or None. NaN and ±inf both become None.

    yfinance returns NaN for missing values inside an otherwise valid frame, and
    NaN is the dangerous case precisely because it compares False to everything:
    a `> 0` guard passes it through, and it then propagates through every sum,
    mean and rolling window it touches without raising.
    """
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else v


@dataclass
class BarRejects:
    """Why bars were altered or dropped, counted by cause."""
    no_close: int = 0
    bad_close_raw: int = 0
    bad_range: int = 0
    bad_volume: int = 0
    bad_dividend: int = 0

    def total(self) -> int:
        return (self.no_close + self.bad_close_raw + self.bad_range
                + self.bad_volume + self.bad_dividend)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v}
        return d


def clean_bar(close, close_raw=None, open_=None, high=None, low=None,
              volume=None, dividend=None,
              rejects: BarRejects | None = None) -> dict | None:
    """One provider bar, reduced to what is safe to store.

    Returns the storable field mapping, or None when the bar carries no usable
    price at all. Field-level degradation is applied in place:

      close       required, must be strictly positive. A zero or negative price
                  is not a price; storing it makes the next day's return -100%
                  and the one after it undefined.
      close_raw   dropped if unusable — nullable by design, and the consumers
                  that need nominal prices already check for it.
      O/H/L       kept only as a CONSISTENT set. Any missing or non-positive
                  member, or a range that does not bracket its own open and
                  close, drops all three. Yang-Zhang takes log(h/c) and
                  log(l/o); an inverted bar contributes a NEGATIVE variance
                  term, which does not raise, does not produce NaN, and simply
                  makes the estimator read low — the direction that argues for a
                  larger position.
      volume      negative is impossible; zero is legitimate (a halted or
                  untraded session) and is kept.
      dividend    negative is impossible. Zero is stored as None, matching the
                  column's convention that null means "ordinary day".
    """
    r = rejects if rejects is not None else BarRejects()

    c = _finite(close)
    if c is None or c <= 0:
        r.no_close += 1
        return None

    raw = _finite(close_raw)
    if close_raw is not None and (raw is None or raw <= 0):
        r.bad_close_raw += 1
        raw = None

    o, h, lo = _finite(open_), _finite(high), _finite(low)
    have_any = any(x is not None for x in (open_, high, low))
    # The range must be checked against the NOMINAL close, never the
    # total-return one. `close` is dividend-adjusted and therefore sits below
    # the traded price by the cumulative yield since the bar — about 12% at the
    # ten-year end of the series. Bracketing the range around that number would
    # reject the intraday range of every older bar in the database as
    # "inconsistent" when the bars are perfectly good and only the comparison
    # was wrong.
    ref = raw if raw is not None else None
    ok = (o is not None and h is not None and lo is not None
          and o > 0 and h > 0 and lo > 0
          and h >= max(o, lo) and lo <= min(o, h)
          and (ref is None or (h >= ref and lo <= ref)))
    if not ok:
        if have_any:
            r.bad_range += 1
        o = h = lo = None

    v = _finite(volume)
    if volume is not None and (v is None or v < 0):
        r.bad_volume += 1
        v = None

    d = _finite(dividend)
    if dividend is not None and (d is None or d < 0):
        r.bad_dividend += 1
        d = None
    if d is not None and d == 0:
        d = None

    return {"close": c, "close_raw": raw, "open_price": o, "high_price": h,
            "low_price": lo, "volume": v, "dividend": d}


@dataclass
class SeriesReport:
    """What a whole ingested series looked like, for the health panel."""
    kept: int = 0
    rejects: BarRejects = field(default_factory=BarRejects)

    def as_dict(self) -> dict:
        out: dict = {"kept": self.kept}
        bad = self.rejects.as_dict()
        if bad:
            out["rejected"] = bad
        return out
