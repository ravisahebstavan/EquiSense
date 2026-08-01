"""Information-arrival features.

The correctness that matters most here is NOT that the numbers look plausible —
it is that no feature can see the future, and that the dividend adjustment does
not manufacture phantom news. Both are silent failures: they produce beautiful
backtests and lose real money.
"""
import numpy as np
import pandas as pd
import pytest

from equisense.research.information import (GAP_Z_THRESHOLD, feat_confirmed_momentum,
                                            feat_gap_drift,
                                            feat_information_shock,
                                            feat_unconfirmed_momentum, gap_z,
                                            overnight_gap, volume_shock)


def _panel(n=400, names=("A", "B"), seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    out = {}
    close = pd.DataFrame(
        {t: 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n))) for t in names},
        index=idx)
    out["close"] = close
    out["close_raw"] = close.copy()
    # open sits a small random gap away from the previous close
    out["open"] = close.shift(1) * (1 + rng.normal(0, 0.004, (n, len(names))))
    out["high"] = np.maximum(close, out["open"]) * 1.005
    out["low"] = np.minimum(close, out["open"]) * 0.995
    out["volume"] = pd.DataFrame(
        rng.lognormal(12, 0.4, (n, len(names))), index=idx, columns=list(names))
    return out


def test_overnight_gap_uses_the_nominal_close_not_the_adjusted_one():
    """The trap this avoids: dividend adjustment back-deflates the historical
    ADJUSTED close, so `open / adjusted_prev_close` shows a jump on every
    ex-date. That artefact is indistinguishable from real news and would seed
    the whole study with fake events."""
    p = _panel(n=120)
    # a 5% dividend adjustment applied to the adjusted series only
    p["close"] = p["close"] * 0.95
    g = overnight_gap(p)
    # gap is computed from close_raw, so shifting `close` must not move it
    p2 = dict(p)
    p2["close"] = p["close"] * 0.5
    assert np.allclose(g.dropna().values, overnight_gap(p2).dropna().values)


def test_gap_z_is_scaled_per_name_not_pooled():
    """An unscaled threshold selects the most volatile names every time, which
    is a volatility factor wearing a news factor's clothes."""
    p = _panel(n=300, names=("CALM", "WILD"), seed=3)
    # make WILD ten times as jumpy at the open
    p["open"]["WILD"] = p["close_raw"]["WILD"].shift(1) * (
        1 + np.random.default_rng(5).normal(0, 0.04, 300))
    z = gap_z(p).dropna()
    # both names should cross the threshold at a broadly similar RATE
    rate_calm = (z["CALM"].abs() > GAP_Z_THRESHOLD).mean()
    rate_wild = (z["WILD"].abs() > GAP_Z_THRESHOLD).mean()
    assert abs(rate_calm - rate_wild) < 0.06, (
        f"per-name scaling failed: CALM {rate_calm:.3f} vs WILD {rate_wild:.3f}")


def test_volume_shock_uses_median_so_spikes_do_not_raise_their_own_baseline():
    idx = pd.bdate_range("2020-01-01", periods=120)
    v = pd.DataFrame({"A": [100.0] * 120}, index=idx)
    v.iloc[100:105] = 10000.0            # a violent spike
    p = {"volume": v}
    vs = volume_shock(p)
    # with a median baseline the spike reads ~100x; a mean baseline would have
    # been dragged up by the spike itself and reported far less
    assert vs.iloc[104]["A"] > 50


def test_no_feature_can_see_the_future():
    """The single most important property. Every feature must be computable
    from data up to and including date t, using nothing after it.

    Verified by mutating the FUTURE of the panel and asserting past feature
    values are byte-identical. A look-ahead bug produces an excellent backtest
    and loses real money, and it is invisible in any summary statistic.
    """
    p = _panel(n=400, seed=11)
    cut = 300
    builders = [feat_gap_drift, feat_information_shock,
                feat_confirmed_momentum, feat_unconfirmed_momentum]
    before = {f.__name__: f(p["close"], p["volume"], p).iloc[:cut].copy()
              for f in builders}

    tampered = {k: df.copy() for k, df in p.items()}
    for k in ("open", "high", "low", "close", "close_raw"):
        tampered[k].iloc[cut:] *= 3.0
    tampered["volume"].iloc[cut:] *= 50.0

    for f in builders:
        after = f(tampered["close"], tampered["volume"], tampered).iloc[:cut]
        pd.testing.assert_frame_equal(
            before[f.__name__], after,
            obj=f"{f.__name__} changed when only the FUTURE was altered")


def test_confirmed_and_unconfirmed_momentum_partition_the_universe():
    """They must be complements: every name lands in exactly one. If both could
    be non-zero the 'control' would not be a control."""
    p = _panel(n=400, seed=7)
    c = feat_confirmed_momentum(p["close"], p["volume"], p)
    u = feat_unconfirmed_momentum(p["close"], p["volume"], p)
    mom = p["close"].shift(21) / p["close"].shift(252) - 1
    both = ((c != 0) & (u != 0)).to_numpy().sum()
    assert both == 0, "a name is both confirmed and unconfirmed"
    # and together they reconstruct plain momentum wherever it is defined
    rebuilt = c.add(u).where(mom.notna())
    pd.testing.assert_frame_equal(rebuilt.dropna(how="all"),
                                  mom.dropna(how="all"), check_exact=False)


def test_information_shock_is_unsigned_and_non_negative():
    p = _panel(n=400, seed=9)
    s = feat_information_shock(p["close"], p["volume"], p).dropna(how="all")
    assert (s.fillna(0) >= 0).all().all(), "an event COUNT cannot be negative"


def test_gap_drift_sign_follows_the_gaps_that_created_it():
    """A name given large positive confirmed gaps must show positive drift."""
    p = _panel(n=300, names=("UP",), seed=13)
    # plant unambiguous positive news: +8% opens on high volume, every 10th day
    for i in range(200, 260, 10):
        p["open"].iloc[i, 0] = p["close_raw"].iloc[i - 1, 0] * 1.08
        p["volume"].iloc[i, 0] = p["volume"].iloc[:i, 0].median() * 5
    d = feat_gap_drift(p["close"], p["volume"], p)
    assert d.iloc[255:265, 0].max() > 0, "planted positive news gave no positive drift"


def test_features_refuse_to_run_without_the_panel():
    """Silently defaulting would compute a price-only feature and label it an
    information feature."""
    p = _panel(n=100)
    for f in (feat_gap_drift, feat_information_shock, feat_confirmed_momentum,
              feat_unconfirmed_momentum):
        with pytest.raises(ValueError, match="panel"):
            f(p["close"], p["volume"])


def test_zero_variance_and_zero_volume_do_not_detonate():
    """A suspended or barely-traded name posts an unbroken run of identical
    opens (rolling std exactly 0) and can print zero volume. Using pd.NA as the
    divide-by-zero sentinel let it survive the division and blow up on
    .astype(float) — but only against the REAL panel, because synthetic data
    never lands on an exact zero. This reproduces it deliberately."""
    idx = pd.bdate_range("2020-01-01", periods=200)
    close = pd.DataFrame({"DEAD": [100.0] * 200, "LIVE": np.linspace(100, 150, 200)},
                         index=idx)
    p = {
        "close": close, "close_raw": close,
        # DEAD gaps identically every day -> rolling std is exactly zero
        "open": close.shift(1),
        "high": close * 1.01, "low": close * 0.99,
        "volume": pd.DataFrame({"DEAD": [0.0] * 200, "LIVE": [1e6] * 200}, index=idx),
    }
    z = gap_z(p)
    vs = volume_shock(p)
    assert z["DEAD"].dropna().empty or z["DEAD"].isna().all()
    assert np.isfinite(vs["LIVE"].dropna()).all()
    # and the features built on top must survive the same panel
    for f in (feat_gap_drift, feat_information_shock, feat_confirmed_momentum,
              feat_unconfirmed_momentum):
        f(p["close"], p["volume"], p)      # must not raise
