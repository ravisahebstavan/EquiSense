"""Risk-managed momentum (Barroso & Santa-Clara 2015) — hand-verified (§15).

The scaling relationship and the crash-tail flag are checked against values a
person can compute by hand, and the long-short construction is checked on a tiny
panel with a known momentum ordering.
"""
import numpy as np
import pandas as pd

from equisense.research import momentum_risk as mr


def test_scalar_is_target_over_realized_and_capped():
    # A deterministic series with an exactly-known standard deviation:
    # alternating +a, -a has population std = a and sample std ≈ a for large n.
    # Choose a so annualised vol is ~24% → scalar = 12/24 = 0.5.
    a = 0.24 / np.sqrt(mr.TRADING_DAYS)
    n = mr.VOL_WINDOW + 50
    ls = pd.Series([a if i % 2 == 0 else -a for i in range(n)])
    out = mr.scale_from_ls(ls, target_ann_vol=0.12, cap=2.0)
    assert out["computable"]
    # realized annualised vol should be ~24%
    assert abs(out["realized_ann_vol_pct"] - 24.0) < 1.0
    # scalar = target/realized ≈ 0.5 (tolerance covers the reported-vol rounding)
    assert abs(out["exposure_scalar"] - 12.0 / out["realized_ann_vol_pct"]) < 0.01
    assert abs(out["exposure_scalar"] - 0.5) < 0.02
    assert out["exposure_scalar"] < 1.0                 # high vol → de-lever


def test_calm_momentum_hits_the_leverage_cap():
    # Very low vol → target/realized > cap → scalar pinned at the cap, never above.
    a = 0.02 / np.sqrt(mr.TRADING_DAYS)                 # ~2%/yr realized
    n = mr.VOL_WINDOW + 50
    ls = pd.Series([a if i % 2 == 0 else -a for i in range(n)])
    out = mr.scale_from_ls(ls, target_ann_vol=0.12, cap=2.0)
    assert out["computable"]
    assert out["exposure_scalar"] == 2.0               # capped, not 6x


def test_zero_vol_is_reported_not_divided_by():
    ls = pd.Series([0.0] * (mr.VOL_WINDOW + 50))
    out = mr.scale_from_ls(ls)
    assert out["computable"] is False


def test_too_short_a_history_is_refused():
    out = mr.scale_from_ls(pd.Series([0.01, -0.01, 0.02]))
    assert out["computable"] is False


def test_crash_tail_flag_trips_in_the_top_decile():
    # Rising volatility over time puts the LAST window in the top decile of the
    # realized-vol history → crash_prone True.
    n = mr.VOL_WINDOW * 4
    amp = np.linspace(0.001, 0.03, n)                   # vol grows steadily
    ls = pd.Series([amp[i] * (1 if i % 2 == 0 else -1) for i in range(n)])
    out = mr.scale_from_ls(ls)
    assert out["computable"]
    assert out["vol_percentile"] >= 0.90
    assert out["crash_prone"] is True


def test_long_short_construction_on_a_known_panel():
    # Three names, 300 days. AAA strongly up, CCC strongly down, BBB flat — so
    # 12-1 momentum ranks AAA top and CCC bottom, and the L/S return on a given
    # day is (AAA return) − (CCC return). Verify the last day's L/S value.
    dates = pd.date_range("2023-01-01", periods=300, freq="D")
    closes = pd.DataFrame({
        "AAA": [100 * (1.001 ** i) for i in range(300)],   # compounding up
        "BBB": [100.0 for _ in range(300)],                # flat
        "CCC": [100 * (0.999 ** i) for i in range(300)],   # compounding down
    }, index=dates)
    ls = mr.momentum_ls_returns(closes, quantile=0.34, min_names=3)
    assert len(ls) > 0
    rets = closes.pct_change()
    sig = mr.feat_momentum_12_1(closes, None).shift(1)
    ranks = sig.rank(axis=1, pct=True)
    d = ls.index[-1]
    # Reproduce the function's own inclusive-quantile masking to verify the
    # vectorisation equals the definition, then confirm the winner is long and
    # the loser is short.
    q = 0.34
    long_names = ranks.loc[d][ranks.loc[d] >= 1 - q].index
    short_names = ranks.loc[d][ranks.loc[d] <= q].index
    expected = rets.loc[d, long_names].mean() - rets.loc[d, short_names].mean()
    assert abs(ls.loc[d] - expected) < 1e-12
    assert "AAA" in long_names and "CCC" in short_names   # winner long, loser short
    assert ls.loc[d] > 0                                  # up-minus-down is positive


def test_end_to_end_refuses_a_thin_universe():
    closes = pd.DataFrame({"AAA": [1, 2, 3], "BBB": [1, 2, 3]})
    assert mr.risk_managed_momentum(closes)["computable"] is False


def test_sizing_shrinks_under_the_momentum_scalar_and_never_levers():
    """The crash protection must be in the SIZE: a scalar < 1 shrinks the
    position proportionally through the risk budget, and a scalar > 1 is capped so
    it never levers up while weights are provisional."""
    from equisense.engine.sizing import recommend_size, SizingInputs
    base = dict(book_value=1_000_000.0, price=1000.0, daily_vol_pct=1.5,
                conviction_band="moderate", net_score=0.4, adv_cr=50.0,
                max_position_pct=10.0)
    full = recommend_size(SizingInputs(**base, momentum_scalar=1.0))
    half = recommend_size(SizingInputs(**base, momentum_scalar=0.5))
    over = recommend_size(SizingInputs(**base, momentum_scalar=1.8))
    # 0.5 scalar → ~half the risk-budgeted value (binding constraint is risk here)
    assert abs(half["recommended_value"] - 0.5 * full["recommended_value"]) \
        < full["recommended_value"] * 0.02
    # scalar > 1 is capped at 1.0 — no leverage
    assert over["recommended_value"] == full["recommended_value"]
    assert over["working"]["momentum_scalar"] == 1.0
    assert half["working"]["momentum_scalar"] == 0.5


def test_is_actionable_freshness_guard():
    from datetime import datetime, timedelta
    from equisense.research.momentum_risk import is_actionable
    now = datetime.utcnow()
    assert is_actionable({"computable": True, "computed_at": now.isoformat()}) is True
    assert is_actionable({"computable": True,
                          "computed_at": (now - timedelta(days=30)).isoformat()}) is False
    assert is_actionable({"computable": False}) is False
    assert is_actionable(None) is False
    assert is_actionable({"computable": True}) is True   # legacy row, no timestamp


def test_scalar_shrinks_a_position_bound_by_the_cap_not_just_risk_budget():
    """The review's finding: the scalar must de-lever even when position_cap or
    heat binds, else a fully-capped winner is left un-shrunk in a crash."""
    from equisense.engine.sizing import recommend_size, SizingInputs
    # very low vol → raw risk value is huge → position_cap (10%) binds
    base = dict(book_value=1_000_000.0, price=1000.0, daily_vol_pct=0.2,
                conviction_band="high", net_score=0.9, adv_cr=500.0,
                max_position_pct=10.0)
    full = recommend_size(SizingInputs(**base, momentum_scalar=1.0))
    half = recommend_size(SizingInputs(**base, momentum_scalar=0.5))
    assert full["binding_constraint"] == "position_cap"    # cap binds, not risk
    # the scalar still halves it — crash protection reaches the capped winner
    assert abs(half["recommended_value"] - 0.5 * full["recommended_value"]) \
        < full["recommended_value"] * 0.02
