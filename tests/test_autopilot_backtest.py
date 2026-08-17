"""Autopilot policy behavior + backtest statistics."""
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense.db import Base
from equisense.models import AppSnapshot, Company, MacroObservation, PriceObservation


@pytest.fixture
def world(monkeypatch):
    """Two companies with prices, a crafted universe snapshot, stubbed regime
    and dossier builder, file-mode temp ledger."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    SL = sessionmaker(bind=eng, expire_on_commit=False)
    s = SL()
    a = Company(ticker="AAA", name="Alpha", sector="IT")
    b = Company(ticker="BBB", name="Beta", sector="Energy")
    s.add_all([a, b]); s.flush()
    d0 = date.today() - timedelta(days=400)
    for i in range(400):
        d = d0 + timedelta(days=i)
        s.add(PriceObservation(company_id=a.id, obs_date=d, close=100.0, volume=1e6))
        s.add(PriceObservation(company_id=b.id, obs_date=d, close=50.0, volume=1e6))
        s.add(MacroObservation(symbol="^NSEI", role="index", obs_date=d, close=20000.0))
    def item(c, price):
        return {"id": c.id, "ticker": c.ticker, "name": c.name, "sector": c.sector,
                "cap_band": "large", "is_financial": False, "price": price,
                "chg_1d_pct": 0.0, "adv_cr": 50.0, "spark": [price] * 10,
                "signals": {k: 1.0 for k in
                            ["momentum", "dist_52w", "trend", "rel_strength", "mqi",
                             "vol", "heat", "f_score", "z_score", "ccs", "fragility",
                             "exp_gap", "pe_pctile", "revenue_cagr_pct", "roic_pct",
                             "pe", "dividend_yield_pct", "debt_to_equity",
                             "implied_growth_gap_pct"]}}
    snap = {"as_of": str(date.today()), "version": 3, "built_at": "t",
            "companies": [item(a, 100.0), item(b, 50.0)]}
    s.add(AppSnapshot(key="universe", as_of=snap["as_of"], payload=json.dumps(snap)))
    s.commit()

    import equisense.db as D
    import equisense.ledger as L
    monkeypatch.setattr(D, "get_session", lambda: SL())
    monkeypatch.setattr(L, "get_session", lambda: SL())
    monkeypatch.setattr(L, "STORAGE", "file")
    monkeypatch.setattr(L, "LEDGER_PATH",
                        __import__("pathlib").Path("/tmp") / "ap_test_ledger.jsonl")
    L.LEDGER_PATH.unlink(missing_ok=True)

    import equisense.api.live as live
    monkeypatch.setattr(live, "current_regime",
                        lambda s_: {"label": "test", "conditioning_key": "all",
                                    "flags": [], "components": []})
    monkeypatch.setattr(live, "build_dossier",
                        lambda s_, c, book_value=0, **kw: {
                            "sizing": {"recommended_shares": 5},
                            "ledger": {"hash": "d" * 64}})
    return s, a, b


def _force_long(monkeypatch, tickers):
    """Make qualified_candidates deterministic: given tickers become tradable
    long candidates with fixed sizing."""
    import equisense.api.candidates as C
    def fake(session, top_n=8, book_value=None, cash=None):
        return {"as_of": "t", "regime": "test", "scanned": 2,
                "verdict_counts": {"long": len(tickers), "avoid": 0, "abstain": 0},
                "weights_status": "uniform",
                "candidates": [{"id": i + 1, "ticker": t, "name": t, "sector": "IT",
                                "price": 100.0, "net_score": 0.2,
                                "conviction_band": "low", "dispersion": 0.1,
                                "drivers": ["momentum +10%"], "dissent": [],
                                "sizing": {"shares": 5, "value": 500.0,
                                           "stop_distance_pct": 3.0, "binding": "risk_budget"},
                                "round_trip_cost_pct": 0.25, "breakeven_move_pct": 0.25,
                                "gates": [], "tradable": True}
                               for i, t in enumerate(tickers)],
                "discipline_note": ""}
    import equisense.api.autopilot as AP
    monkeypatch.setattr(C, "qualified_candidates", fake)
    return fake


def test_autopilot_enters_qualified_candidates(world, monkeypatch):
    s, a, b = world
    _force_long(monkeypatch, ["AAA", "BBB"])
    from equisense.api.autopilot import run_autopilot, set_config
    set_config(s, {"enabled": True, "max_new_per_run": 1})
    rep = run_autopilot(s)
    assert len(rep["entries"]) == 1                      # run cap respected
    assert rep["entries"][0]["dossier"] == "d" * 16      # dossier-linked
    assert any("run cap" in x for x in rep["skipped"])


def test_autopilot_respects_cash_reserve(world, monkeypatch):
    s, a, b = world
    _force_long(monkeypatch, ["AAA"])
    from equisense.api.autopilot import run_autopilot, set_config
    from equisense.api.paper import reset_account
    reset_account(s, 600.0)  # tiny account: 5 sh × 100 = 500 > 600 − 10% reserve
    set_config(s, {"enabled": True, "cash_reserve_pct": 20.0})
    rep = run_autopilot(s)
    assert not rep["entries"]
    assert any("cash reserve" in x for x in rep["skipped"])


def test_autopilot_time_exit(world, monkeypatch):
    s, a, b = world
    from equisense.api.autopilot import run_autopilot, set_config
    from equisense.models import PaperTrade
    _force_long(monkeypatch, [])
    s.add(PaperTrade(company_id=a.id, side="buy", quantity=10, price=100.0,
                     trade_date=date.today() - timedelta(days=400)))
    s.commit()
    set_config(s, {"enabled": True})
    rep = run_autopilot(s)
    assert len(rep["exits"]) == 1
    assert "time exit" in rep["exits"][0]["reason"]


def test_autopilot_stop_breach_exit(world, monkeypatch):
    s, a, b = world
    from equisense.api.autopilot import run_autopilot, set_config
    from equisense.models import PaperTrade
    _force_long(monkeypatch, [])
    # bought at 200, price is 100 → far beyond any stop
    s.add(PaperTrade(company_id=a.id, side="buy", quantity=10, price=200.0,
                     trade_date=date.today() - timedelta(days=5)))
    s.commit()
    set_config(s, {"enabled": True})
    rep = run_autopilot(s)
    assert len(rep["exits"]) == 1
    assert "stop breach" in rep["exits"][0]["reason"]


def test_disabled_autopilot_does_nothing(world, monkeypatch):
    s, a, b = world
    _force_long(monkeypatch, ["AAA"])
    from equisense.api.autopilot import run_autopilot, set_config
    set_config(s, {"enabled": False})
    rep = run_autopilot(s)
    assert not rep["entries"] and not rep["exits"]
    assert any("disabled" in x for x in rep["skipped"])


# ------------------------------------------------------------- backtest

def test_bootstrap_ci_detects_signal_and_null():
    from equisense.research.backtest import moving_block_bootstrap_ci
    import random
    rng = random.Random(1)
    signal = [rng.gauss(5.0, 2.0) for _ in range(200)]   # true median ≈ 5
    lo, hi = moving_block_bootstrap_ci(signal, block_len=6)
    assert lo > 0, "CI must exclude zero for a real effect"
    null = [rng.gauss(0.0, 2.0) for _ in range(200)]
    lo0, hi0 = moving_block_bootstrap_ci(null, block_len=6)
    assert lo0 < 0 < hi0, "CI must straddle zero for noise"


def test_bootstrap_ci_small_sample_is_nan():
    from equisense.research.backtest import moving_block_bootstrap_ci
    lo, hi = moving_block_bootstrap_ci([1.0, 2.0], block_len=1)
    assert lo != lo and hi != hi  # NaN — no fake certainty from n=2


# ----------------------------------------------------- vol-managed overlay

def test_vol_overlay_delevers_after_stress():
    """Barroso-Santa-Clara cuts both ways: dead-calm stretches get scaled UP
    (capped), and the period immediately AFTER a vol spike gets scaled DOWN —
    because the scale factor uses only PRIOR (trailing) realized vol, never
    the current period's own return. Check the mechanism where it actually
    applies: the post-stress factor, not the all-period average."""
    from equisense.research.backtest import vol_managed_overlay, SCALE_BOUNDS
    calm = [1.0, 1.2, 0.8, 1.1, 1.0, 0.9]
    violent = [40.0]           # huge single-period swing → trailing vol spikes
    post_stress = [1.0]        # this period's factor reflects the violent one
    rets = calm + violent + post_stress
    r = vol_managed_overlay(rets, hold_days=21)
    assert r["current_scale_factor"] == pytest.approx(SCALE_BOUNDS[0], abs=1e-6), \
        "the period right after a vol spike must be de-levered to the floor"
    assert SCALE_BOUNDS[0] <= r["mean_scale_factor"] <= SCALE_BOUNDS[1]
    assert r["citation"].startswith("Barroso")


def test_vol_overlay_early_periods_unscaled():
    from equisense.research.backtest import vol_managed_overlay
    r = vol_managed_overlay([2.0, -1.0], hold_days=21)  # < 3 trailing obs anywhere
    assert r["baseline"]["mean_period_pct"] == r["vol_managed"]["mean_period_pct"]


def test_vol_overlay_reports_both_variants_honestly():
    from equisense.research.backtest import vol_managed_overlay
    import random
    rng = random.Random(3)
    rets = [rng.gauss(1.0, 3.0) for _ in range(40)]
    r = vol_managed_overlay(rets, hold_days=21)
    assert "baseline" in r and "vol_managed" in r
    assert r["verdict"]  # always states a verdict, win or not — never silent


def test_backtest_reports_where_the_deflated_sharpe_verdict_breaks():
    """A single n_trials is a guess and the verdict is extremely sensitive to
    it: the same 105 returns read "genuine skill" at 8 trials (DSR 0.990) and
    "not distinguishable" at 75 (0.939). Worse, a hardcoded constant cannot grow
    as more variants get tried, and this strategy WAS modified after seeing
    results — HYP-008/011 demoted, the within-cluster null corrected — each of
    which is a trial the constant never learned about.

    So the result must expose the breaking point rather than assert one number.
    """
    import inspect

    from equisense.research import backtest
    src = inspect.getsource(backtest.strategy_backtest)
    assert "deflated_sharpe_sensitivity" in src
    assert "dsr_breaks_at_n_trials" in src
    # the sweep has to span both sides of the threshold to be informative
    assert "75" in src and "150" in src


def test_dsr_sensitivity_actually_flips_on_real_returns():
    """Guards the claim above with arithmetic rather than a docstring."""
    from equisense.research.stats import deflated_sharpe_ratio
    # a Sharpe strong enough to pass at low trial counts and fail at high ones
    import random
    rng = random.Random(7)
    rs = [rng.gauss(0.014, 0.033) for _ in range(105)]
    low = deflated_sharpe_ratio(rs, n_trials=8)
    high = deflated_sharpe_ratio(rs, n_trials=150)
    assert low["computable"] and high["computable"]
    assert high["expected_max_sharpe_under_null"] > low["expected_max_sharpe_under_null"], \
        "more trials must raise the bar the Sharpe has to clear"
    assert high["deflated_sharpe_probability"] <= low["deflated_sharpe_probability"]


def test_default_basket_is_wide_enough_to_survive_a_single_name_shock():
    """A three-stock book puts 33.3% in each name. One promoter default or
    forensic audit in an Indian mid-cap gaps down in consecutive lower circuits
    with no exit available, and a -50% move on a third of the book is a 16.7%
    unstoppable loss.

    Measured across N=3..30 the after-tax CAGR is nearly flat — 25.84% at N=3,
    23.80% at N=15 — while per-position weight falls 33.3% -> 6.7%. Paying ~2pp
    to cut catastrophe exposure fivefold is the trade a real book makes.
    """
    from equisense.research.backtest import DEFAULT_TOP_N
    assert DEFAULT_TOP_N >= 10, "basket too concentrated for real capital"
    assert 100 / DEFAULT_TOP_N <= 10.0, "per-position weight above 10%"


def test_widening_reduces_daily_drawdown_and_the_source_says_so():
    """REPLACES an earlier test that asserted the opposite. That test locked in
    the claim "drawdown is flat near -20% across every N", which was true only
    of the step-based measurement. On a daily mark-to-market series widening
    clearly helps: -39.25% at N=3 against -34.26% at N=15. A test that pins a
    false claim is worse than no test, because it defends the error."""
    import inspect

    from equisense.research import backtest
    head = inspect.getsource(backtest)
    head = head[:head.index("def strategy_backtest")]
    assert "artefact" in head, "the retraction is not recorded"
    assert "WORSE than" in head, "must state the drawdown is worse than passive"



def test_drawdown_is_measured_on_a_daily_series_not_rebalance_steps():
    """Peak-to-trough on 21-day steps cannot see inside a step. A book down 45%
    mid-March 2020 and back to -15% by the step boundary records -15%, while the
    holder watched half the capital vanish.

    Measured on the real panel the gap is 13-18pp: N=15 reads -20.59% on steps
    and -34.26% on a daily mark-to-market series. Sizing against the step figure
    would size for a drawdown that never happened.
    """
    import inspect

    from equisense.research import backtest
    src = inspect.getsource(backtest.strategy_backtest)
    assert "max_drawdown_daily_mtm_pct" in src
    assert "daily_mtm_sessions" in src
    # and the step figure must carry its own warning rather than sit unlabelled
    assert "UNDERSTATES" in src


def test_the_flat_drawdown_claim_is_retracted_in_the_source():
    """An earlier commit asserted drawdown was flat near -20% across every N.
    That was an artefact of step-based measurement; on daily MTM it falls from
    -39.25% (N=3) to -34.26% (N=15). Leaving the wrong claim in place would
    invite oversizing."""
    import inspect

    from equisense.research import backtest
    head = inspect.getsource(backtest)
    head = head[:head.index("def strategy_backtest")]
    assert "artefact" in head and "21-day steps" in head
    assert "-34.26" in head or "34.26" in head


def test_breadth_hysteresis_holds_through_the_middle_band():
    """A continuous scale on raw daily breadth rebalances the cash balance on
    every wiggle around the threshold, spiking costs and realising a stream of
    tiny taxable gains. Inside the band, exposure must not move."""
    from equisense.research.backtest import breadth_exposure
    e = breadth_exposure([0.70, 0.50, 0.50, 0.30, 0.50, 0.50, 0.70])
    assert e == [1.0, 1.0, 1.0, 0.2, 0.2, 0.2, 1.0]


def test_breadth_exposure_survives_missing_values():
    """A NaN early in the series (before the 200-day window fills) must carry
    the previous exposure, not crash or reset to full risk."""
    from equisense.research.backtest import breadth_exposure
    e = breadth_exposure([float("nan"), 0.30, float("nan"), 0.70])
    assert e == [1.0, 0.2, 0.2, 1.0]


def test_breadth_scaling_is_on_by_default_and_documented_with_its_cost():
    """It buys a 65% drawdown cut for 3.7pp of after-tax CAGR, which is the
    trade a real book makes — but the thresholds are extra parameters and the
    worst CPCV path degrades, so both must be recorded."""
    import inspect

    from equisense.research import backtest
    sig = inspect.signature(backtest.strategy_backtest)
    assert sig.parameters["breadth_scaled"].default is True
    head = inspect.getsource(backtest)
    head = head[:head.index("def universe_breadth")]
    assert "signature of selection" in head, (
        "must state the defaults scored best in the grid, which is a selection "
        "signature even when they were not deliberately optimised")
    assert "3.04" in head, "must record that the worst CPCV path degrades"


def test_breadth_thresholds_must_be_passed_explicitly_to_take_effect():
    """`breadth_exposure` binds the thresholds as DEFAULT ARGUMENTS, which
    Python evaluates once at import. Reassigning the module constants afterwards
    changes nothing — a sensitivity run did exactly that and returned eight
    identical rows, which looked like perfect robustness and was a bug in the
    test. Anyone varying these must pass them explicitly."""
    import equisense.research.backtest as B
    original = B.BREADTH_RISK_OFF
    try:
        B.BREADTH_RISK_OFF = 0.99          # would de-risk almost always...
        via_module = B.breadth_exposure([0.50])
        via_explicit = B.breadth_exposure([0.50], risk_off=0.99)
        assert via_module == [1.0], "module reassignment silently had no effect"
        assert via_explicit == [B.BREADTH_MIN_EXPOSURE], "explicit pass ignored"
    finally:
        B.BREADTH_RISK_OFF = original


def test_the_default_thresholds_are_recorded_as_grid_best_not_neutral():
    """They scored best of 8 configurations. Presenting their result as the
    expected outcome would overstate it; the grid median is the honest figure."""
    import inspect

    from equisense.research import backtest
    head = inspect.getsource(backtest)
    head = head[:head.index("def universe_breadth")]
    assert "signature of selection" in head
    assert "19.2" in head, "the honest median estimate must be recorded"


def test_breadth_refuses_a_reading_on_an_incomplete_session():
    """Free keyless scrapers fail partially. Carrying stale prices forward
    freezes collapsing names at pre-crash levels and INFLATES breadth exactly
    when it matters; dropping them changes the denominator and biases F if the
    failures cluster by sector. Neither is acceptable, so a thin session must
    produce no reading at all."""
    import numpy as np
    import pandas as pd

    from equisense.research.backtest import universe_breadth
    idx = pd.bdate_range("2020-01-01", periods=300)
    df = pd.DataFrame({f"S{i}": np.linspace(100, 200, 300) for i in range(20)},
                      index=idx)
    full = universe_breadth(df)
    assert full.iloc[-1] == 1.0, "a fully priced session should read normally"

    # simulate an outage: only 3 of 20 names ingest on the final session
    broken = df.copy()
    broken.iloc[-1, 3:] = np.nan
    out = universe_breadth(broken)
    assert pd.isna(out.iloc[-1]), "breadth was computed from a 15% sample"


def test_a_missing_breadth_reading_holds_exposure_rather_than_resetting():
    """The correct failure mode: an ingestion outage must not be able to move
    real money by implying the market improved."""
    from equisense.research.backtest import breadth_exposure
    e = breadth_exposure([0.30, float("nan"), float("nan"), 0.30])
    assert e == [0.2, 0.2, 0.2, 0.2], "an outage reset exposure to full risk"


def _force_direction(monkeypatch, tickers, direction):
    """qualified_candidates returning candidates in a chosen direction."""
    import equisense.api.candidates as C

    def fake(session, top_n=8, book_value=None, cash=None):
        return {"as_of": "t", "regime": "test", "scanned": 2,
                "verdict_counts": {"long": 0, "avoid": len(tickers), "abstain": 0},
                "weights_status": "uniform",
                "candidates": [{"id": i + 1, "ticker": t, "name": t, "sector": "IT",
                                "price": 100.0, "net_score": -0.6 if direction == "short" else 0.6,
                                "direction": direction,
                                "conviction_band": "low", "dispersion": 0.1,
                                "drivers": ["MAX-effect -1.4"], "dissent": [],
                                "sizing": {"shares": 5, "value": 500.0,
                                           "stop_distance_pct": 3.0,
                                           "binding": "risk_budget"},
                                "round_trip_cost_pct": 0.25, "breakeven_move_pct": 0.25,
                                "gates": [], "tradable": True}
                               for i, t in enumerate(tickers)],
                "reviewed": [], "discipline_note": ""}

    monkeypatch.setattr(C, "qualified_candidates", fake)


def test_autopilot_opens_a_short_on_an_avoid_verdict(world, monkeypatch):
    """Autopilot was long-only: it computed qualified avoid_short verdicts and
    then discarded them, so on a universe where the only names clearing the
    significance bar are short-side it sat idle indefinitely while the
    synthesis was in fact producing signal."""
    s, a, b = world
    _force_direction(monkeypatch, ["AAA"], "short")
    from equisense.api.autopilot import run_autopilot, set_config
    from equisense.api.paper import account

    set_config(s, {"enabled": True, "max_new_per_run": 1})
    rep = run_autopilot(s)

    assert len(rep["entries"]) == 1, rep
    assert rep["entries"][0]["direction"] == "short"
    pos = account(s)["positions"][0]
    assert pos["quantity"] < 0, "a short entry must leave a negative position"
    assert pos["direction"] == "short"


def test_short_stop_fires_above_entry_not_below(world, monkeypatch):
    """A short's stop sits ABOVE its entry. Sharing the long-side test would
    leave every short with no stop at all, and its verdict-flip exit would fire
    on the thesis WORKING rather than failing."""
    from datetime import date as _date

    from equisense.models import PriceObservation
    s, a, b = world
    _force_direction(monkeypatch, ["AAA"], "short")
    from equisense.api.autopilot import run_autopilot, set_config
    from equisense.api.paper import account

    set_config(s, {"enabled": True, "max_new_per_run": 1})
    run_autopilot(s)
    entry = account(s)["positions"][0]["avg_cost"]

    # price RISES hard against the short → stop must fire. Must be the LATEST
    # bar or the position still marks at the old close.
    s.add(PriceObservation(company_id=a.id, obs_date=_date.today(),
                           close=entry * 1.60, volume=1000))
    s.commit()
    _force_direction(monkeypatch, [], "short")     # no new entries this run
    rep = run_autopilot(s)

    assert len(rep["exits"]) == 1, rep
    assert rep["exits"][0]["direction"] == "short"
    assert "above" in rep["exits"][0]["reason"]
    assert account(s)["positions"] == [], "cover must flatten the position"


def _force_vix_pctile(monkeypatch, pctile):
    import equisense.api.live as live_mod
    monkeypatch.setattr(live_mod, "current_regime", lambda s: {
        "label": "test", "conditioning_key": "test",
        "components": [{"key": "vix_percentile", "value": pctile}]})


def test_autopilot_stops_adding_exposure_when_the_price_of_risk_spikes(world, monkeypatch):
    """The loss this prevents: the diversification gate treats two names
    correlated above 0.75 as one bet, but measures correlation over a TRAILING
    window. In an Indian equity crash cross-sectional correlation converges
    toward 0.9 across the board, so a book assembled as independent bets in a
    calm regime becomes one leveraged bet on index beta exactly when that
    matters — and a cash account cannot hedge it, because Indian retail cannot
    hold a short equity position overnight.

    Conditioning on VIX here is not market timing: the position COUNT is not a
    forecast of direction. It refuses to add new exposure while the price of
    risk says diversification is about to stop working.
    """
    s, a, b = world
    from equisense.api.autopilot import VIX_HALT_PCTILE, run_autopilot, set_config

    _force_long(monkeypatch, ["AAA", "BBB"])
    _force_vix_pctile(monkeypatch, VIX_HALT_PCTILE + 5)
    set_config(s, {"enabled": True, "max_new_per_run": 2, "max_open_positions": 8})

    rep = run_autopilot(s)
    assert rep["entries"] == [], "no new exposure may be opened in a halt regime"
    assert any("regime halt" in x for x in rep["skipped"]), rep["skipped"]


def test_calm_regime_still_trades(world, monkeypatch):
    """The guard must not become a permanent off switch — if it fired in calm
    conditions it would silently convert the book to cash forever."""
    s, a, b = world
    from equisense.api.autopilot import run_autopilot, set_config

    _force_long(monkeypatch, ["AAA"])
    _force_vix_pctile(monkeypatch, 30.0)
    set_config(s, {"enabled": True, "max_new_per_run": 1, "max_open_positions": 8})

    rep = run_autopilot(s)
    assert len(rep["entries"]) == 1, rep
    assert not any("regime" in x for x in rep["skipped"])


def test_forecast_density_is_configurable_and_bounded(world, monkeypatch):
    """Claim density is the single biggest lever on time-to-calibration: the
    probability map needs 30 scored claims and learned weights need 150 per
    family, and until those land every weight stays provisional.

    It is bounded rather than "register everything" for two reasons. Each claim
    is a PERMANENT ledger record in a 0.5 GB database. And claims on the most
    convicted names carry more independent information than claims on all 500,
    most of which the system has explicitly said it has no view on.
    """
    import equisense.api.candidates as C
    from equisense.api.autopilot import (DEFAULTS, register_daily_forecasts,
                                         set_config)
    import equisense.api.live as live_mod
    import equisense.ledger as L

    s, a, b = world
    reviewed = [{"id": 1, "ticker": f"T{i}", "verdict": "abstain",
                 "net_score": -1.0 + i / 100.0, "conviction_band": "low",
                 "data_suspect": False} for i in range(60)]
    monkeypatch.setattr(C, "qualified_candidates",
                        lambda *a_, **k: {"candidates": [], "reviewed": reviewed})
    monkeypatch.setattr(L, "read_all", lambda: [])
    built = []

    def fake_build(session, company, **kw):
        built.append(company)
        return {"synthesis": {"verdict": "abstain"}, "ledger": {"hash": "h" * 64}}

    monkeypatch.setattr(live_mod, "build_dossier", fake_build)

    assert DEFAULTS["daily_forecasts"] >= 20, (
        "a 500-name universe registering a handful of claims a day cannot "
        "reach the calibration gates in any useful timeframe")

    set_config(s, {"daily_forecasts": 12})
    out = register_daily_forecasts(s)
    assert len(out["registered"]) == 12, out

    built.clear()
    set_config(s, {"daily_forecasts": 3})
    out = register_daily_forecasts(s)
    assert len(out["registered"]) == 3


def _regime(monkeypatch, nifty_trend, vix=40.0):
    import equisense.api.live as live
    monkeypatch.setattr(live, "current_regime", lambda s_: {
        "label": "t", "conditioning_key": "all", "flags": [],
        "components": [{"key": "nifty_trend", "value": nifty_trend},
                       {"key": "vix_percentile", "value": vix}]})


def _force_long_directional(monkeypatch, tickers):
    import equisense.api.candidates as C
    def fake(session, top_n=8, book_value=None, cash=None):
        return {"as_of": "t", "regime": "t", "scanned": 2,
                "verdict_counts": {"long": len(tickers), "avoid": 0, "abstain": 0},
                "weights_status": "uniform",
                "candidates": [{"id": i + 1, "ticker": t, "name": t, "sector": "IT",
                                "price": 100.0, "net_score": 0.2, "direction": "long",
                                "conviction_band": "low", "dispersion": 0.1,
                                "drivers": ["momentum +10%"], "dissent": [],
                                "sizing": {"shares": 5, "value": 500.0,
                                           "stop_distance_pct": 3.0, "binding": "risk_budget"},
                                "round_trip_cost_pct": 0.25, "breakeven_move_pct": 0.25,
                                "gates": [], "tradable": True}
                               for i, t in enumerate(tickers)],
                "discipline_note": ""}
    monkeypatch.setattr(C, "qualified_candidates", fake)


def test_market_trend_filter_blocks_new_longs_in_downtrend(world, monkeypatch):
    """Faber 2007: no new longs while NIFTY is below its 200DMA — the regime
    where the large equity drawdowns concentrate."""
    s, a, b = world
    _regime(monkeypatch, nifty_trend=-8.0)
    _force_long_directional(monkeypatch, ["AAA"])
    from equisense.api.autopilot import run_autopilot, set_config
    set_config(s, {"enabled": True, "max_new_per_run": 2})
    rep = run_autopilot(s)
    assert not rep["entries"]
    assert any("market-trend filter" in x for x in rep["skipped"])


def test_market_trend_filter_allows_longs_in_uptrend(world, monkeypatch):
    """Control: the identical long IS taken when NIFTY is above its 200DMA, so the
    filter is a regime gate, not a blanket refusal."""
    s, a, b = world
    _regime(monkeypatch, nifty_trend=+5.0)
    _force_long_directional(monkeypatch, ["AAA"])
    from equisense.api.autopilot import run_autopilot, set_config
    set_config(s, {"enabled": True, "max_new_per_run": 2})
    rep = run_autopilot(s)
    assert len(rep["entries"]) == 1
    assert not any("market-trend filter" in x for x in rep["skipped"])
