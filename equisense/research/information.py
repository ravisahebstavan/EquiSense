"""Information-arrival features: separating price moves that carry news from
price moves that carry none.

The gap this closes is real and specific. Every price-only signal in this
system — momentum, sector-relative momentum, participation heat — sees only
that a price moved. A human reading the tape sees *why*, and knows that a
stock drifting up on a stream of disclosures is a different animal from one
drifting up on nothing. The finance literature agrees: post-earnings-
announcement drift (Ball & Brown 1968; Bernard & Thomas 1989) exists precisely
because information diffuses into prices over WEEKS rather than instantly, and
Chan-Jegadeesh-Lakonishok (1996) showed momentum and information-driven drift
are distinct effects.

What is measurable here for free, from data already in Neon:

  Overnight gap    (open_t - close_{t-1}) / close_{t-1}. The market was shut,
                   so the move is pure information repricing, uncontaminated
                   by intraday flow. This is the single cleanest news marker
                   available without a paid feed — 10 years and 483 names of it.
  Volume shock     volume against its own trailing median. Information arrives
                   with participation; a move on no volume is usually noise.
  Range shock      true range against its trailing median — disagreement about
                   what the news means.

Deliberately NOT used: NSE delivery percentage. It is the better ownership
marker but the archive yields one file per day, so only ~450 rows exist
against ~962k price bars. Using it would restrict every study to a few months.
It stays a live-only signal until enough history accumulates.

Nothing here is trusted until it has been through the same IC, quantile-
portfolio and multiplicity machinery as every other hypothesis. An untested
information feature is a story, not a signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Company, PriceObservation

# A gap must clear this many trailing standard deviations to count as news
# rather than as ordinary open-auction noise.
GAP_Z_THRESHOLD = 2.0
SHOCK_WINDOW = 63          # trailing window for the "normal" baseline
MIN_BASELINE = 40


def load_ohlc_panel(session: Session) -> dict[str, pd.DataFrame]:
    """{open, high, low, close, close_raw, volume} panels: index=date, columns=ticker.

    `close_raw` and OHLC are the NOMINAL series and belong together; `close` is
    total-return adjusted. Gaps must be computed on the nominal pair, because a
    dividend adjustment shifts the historical close and would manufacture a
    phantom overnight gap on every ex-date — an artefact that looks exactly like
    the news events this module exists to find.
    """
    rows = session.execute(
        select(PriceObservation.company_id, PriceObservation.obs_date,
               PriceObservation.open_price, PriceObservation.high_price,
               PriceObservation.low_price, PriceObservation.close,
               PriceObservation.close_raw, PriceObservation.volume)).all()
    tickers = {c.id: c.ticker for c in session.scalars(select(Company)).all()}
    df = pd.DataFrame(rows, columns=["cid", "date", "open", "high", "low",
                                     "close", "close_raw", "volume"])
    df["ticker"] = df["cid"].map(tickers)
    out = {}
    for col in ("open", "high", "low", "close", "close_raw", "volume"):
        out[col] = df.pivot_table(index="date", columns="ticker",
                                  values=col).sort_index()
    return out


# ------------------------------------------------------------------ features

def overnight_gap(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Signed overnight gap, on the NOMINAL price pair (see load_ohlc_panel)."""
    prev_close = panel["close_raw"].shift(1)
    return panel["open"] / prev_close - 1


def gap_z(panel: dict[str, pd.DataFrame], window: int = SHOCK_WINDOW) -> pd.DataFrame:
    """Gap in units of its own trailing volatility.

    Scaled per name rather than pooled: a 2% gap is routine for a small-cap and
    extraordinary for a large-cap, and an unscaled threshold would simply select
    the most volatile names every time — a volatility factor wearing a news
    factor's clothes.
    """
    g = overnight_gap(panel)
    sd = g.rolling(window, min_periods=MIN_BASELINE).std()
    # np.nan, NOT pd.NA. A suspended or barely-traded name can post an unbroken
    # run of identical opens, making the rolling std exactly zero; pd.NA then
    # survives the division and detonates on .astype(float) with
    # "float() argument must be ... not 'NAType'". Synthetic data never produces
    # an exact zero, so this only appears against the real panel — the same
    # trap that took down the IC study at 200 names.
    return g / sd.replace(0, np.nan)


def volume_shock(panel: dict[str, pd.DataFrame],
                 window: int = SHOCK_WINDOW) -> pd.DataFrame:
    """Volume relative to its trailing MEDIAN.

    Median, not mean: volume is heavily right-skewed, so a mean baseline is
    dragged upward by the very spikes being detected, which shrinks exactly the
    signal this is meant to measure.
    """
    v = panel["volume"]
    base = v.rolling(window, min_periods=MIN_BASELINE).median()
    return v / base.replace(0, np.nan)      # np.nan for the same reason as gap_z


def feat_information_shock(closes, volumes, panel=None) -> pd.DataFrame:
    """How much NEWS arrived recently, unsigned — magnitude only, no direction.

    A count of |gap| > threshold over the last month, scaled by volume
    confirmation. High values mean the market has been repeatedly repricing this
    name on information. It says nothing about which way, which is the point:
    used alone it is a risk/attention measure, and it earns its keep by
    CONDITIONING the directional signals below.
    """
    if panel is None:
        raise ValueError("feat_information_shock needs the OHLC panel")
    z = gap_z(panel)
    vs = volume_shock(panel)
    events = ((z.abs() > GAP_Z_THRESHOLD) & (vs > 1.5)).astype(float)
    return events.rolling(21, min_periods=15).sum()


def feat_gap_drift(closes, volumes, panel=None) -> pd.DataFrame:
    """Signed, volume-confirmed information already absorbed over the last month.

    The direct post-earnings-announcement-drift analogue: sum the SIGNED gaps
    that cleared the news threshold with volume behind them. If information
    diffuses slowly, the sign of recent confirmed news predicts the sign of the
    coming weeks' returns — the effect a price-only momentum signal cannot
    separate from ordinary drift.
    """
    if panel is None:
        raise ValueError("feat_gap_drift needs the OHLC panel")
    z = gap_z(panel)
    vs = volume_shock(panel)
    g = overnight_gap(panel)
    confirmed = g.where((z.abs() > GAP_Z_THRESHOLD) & (vs > 1.5), 0.0)
    return confirmed.rolling(21, min_periods=15).sum()


def feat_confirmed_momentum(closes, volumes, panel=None) -> pd.DataFrame:
    """12-1 momentum kept only where recent news AGREES with it.

    The core hypothesis of this module, and the one that answers "a human can
    see the story developing". Momentum backed by information that points the
    same way is a story still being priced; momentum on no news, or against the
    news, is drift with nothing behind it. Neutralised (not inverted) where the
    two disagree, because the claim being tested is that confirmation ADDS
    information — not that its absence is bearish.
    """
    if panel is None:
        raise ValueError("feat_confirmed_momentum needs the OHLC panel")
    mom = closes.shift(21) / closes.shift(252) - 1
    drift = feat_gap_drift(closes, volumes, panel)
    agree = ((mom > 0) & (drift > 0)) | ((mom < 0) & (drift < 0))
    return mom.where(agree, 0.0)


def feat_unconfirmed_momentum(closes, volumes, panel=None) -> pd.DataFrame:
    """The control. Momentum where news is ABSENT or disagrees.

    Included deliberately: `feat_confirmed_momentum` beating plain momentum
    could just mean the filter shrank the universe to higher-momentum names.
    Only if confirmed beats plain AND unconfirmed underperforms it is the
    information content doing the work. Testing the complement is what
    distinguishes a real conditioning effect from a selection artefact.
    """
    if panel is None:
        raise ValueError("feat_unconfirmed_momentum needs the OHLC panel")
    mom = closes.shift(21) / closes.shift(252) - 1
    drift = feat_gap_drift(closes, volumes, panel)
    agree = ((mom > 0) & (drift > 0)) | ((mom < 0) & (drift < 0))
    return mom.where(~agree, 0.0)


# Registered so these flow through IC, quantile-portfolio and multiplicity
# control exactly like every price-only hypothesis. The panel is bound by the
# caller via a closure, matching how HYP-010 binds its sector map.
INFORMATION_STUDIES = {
    "HYP-016": {"builder": feat_gap_drift,
                "name": "confirmed_news_drift",
                "statement": "Signed, volume-confirmed overnight gaps over the "
                             "last month predict the next weeks' returns "
                             "(post-announcement drift)."},
    "HYP-017": {"builder": feat_confirmed_momentum,
                "name": "information_confirmed_momentum",
                "statement": "Momentum agreeing with recent confirmed news "
                             "outperforms momentum generally."},
    "HYP-018": {"builder": feat_unconfirmed_momentum,
                "name": "unconfirmed_momentum_control",
                "statement": "CONTROL for HYP-017: momentum without news "
                             "confirmation should be weaker if the information "
                             "content is what matters."},
    "HYP-019": {"builder": feat_information_shock,
                "name": "information_intensity",
                "statement": "Unsigned count of confirmed news events — an "
                             "attention/uncertainty measure, not a direction."},
}
