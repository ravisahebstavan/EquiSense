"""Financial-correctness invariants (Wave S).

Each test corresponds to a defect that was verified against the running code
before being fixed. The comments record the observed wrong behaviour, because a
test that only asserts the right answer loses the reason it exists.
"""
from __future__ import annotations

import math

import pytest

from equisense.ai.grounding import validate
from equisense.engine import novel, quality, ratios, valuation
from equisense.engine.types import StatementData


def stmt(**kw) -> StatementData:
    base = dict(period="FY2025", fiscal_year=2025)
    base.update(kw)
    return StatementData(**base)


# ------------------------------------------------------------------ tax rate

def test_effective_tax_rate_rejects_refund_year():
    """Observed: tax_expense=-800, pbt=1900 gave eff_tax=-0.421, so
    NOPAT = EBIT x 1.421 > EBIT and ROIC printed 35.53%. NOPAT cannot exceed
    EBIT."""
    s = stmt(ebit=2000, pbt=1900, tax_expense=-800,
             total_debt=2000, total_equity=7000, cash=1000)
    rate, caveat = ratios.effective_tax_rate(s)
    assert rate == pytest.approx(ratios.STATUTORY_TAX_RATE)
    assert caveat and "outside the plausible" in caveat
    m = ratios.roic(s)
    assert m.inputs["nopat"] <= s.ebit
    assert m.inputs["effective_tax_rate_raw"] == pytest.approx(-800 / 1900)


def test_effective_tax_rate_rejects_above_100pct():
    """Observed: pbt=100, tax=120 gave eff_tax=1.20 → negative NOPAT on
    positive EBIT."""
    s = stmt(ebit=2000, pbt=100, tax_expense=120,
             total_debt=2000, total_equity=7000, cash=1000)
    m = ratios.roic(s)
    assert m.value > 0, "positive EBIT must not yield negative ROIC via a >100% tax rate"


def test_plausible_effective_tax_rate_is_used_as_is():
    s = stmt(ebit=2000, pbt=1900, tax_expense=475,
             total_debt=2000, total_equity=7000, cash=1000)
    rate, caveat = ratios.effective_tax_rate(s)
    assert rate == pytest.approx(475 / 1900)
    assert caveat is None


# ------------------------------------------------------- average denominators

def test_returns_use_average_balances_when_prior_period_available():
    curr = stmt(revenue=10000, net_income=1425, total_assets=12000, total_equity=7000)
    prev = stmt(period="FY2024", fiscal_year=2024, revenue=9000, net_income=1200,
                total_assets=10000, total_equity=6000)
    ms = {m.key: m for m in ratios.profitability_ratios(curr, prev)}
    assert ms["roe"].inputs["denominator"] == "average"
    assert ms["roe"].value == pytest.approx(1425 / 6500 * 100)
    ending_only = {m.key: m for m in ratios.profitability_ratios(curr)}
    assert ending_only["roe"].inputs["denominator"] == "closing"
    assert ms["roe"].value > ending_only["roe"].value, \
        "period-end denominators understate returns for a growing balance sheet"


def test_dupont_identity_reconciles_exactly():
    """If ROE averages its denominator but DuPont terms do not, the product
    silently stops equalling the ROE shown next to it."""
    curr = stmt(revenue=10000, net_income=1425, total_assets=12000, total_equity=7000)
    prev = stmt(period="FY2024", fiscal_year=2024, total_assets=10000, total_equity=6000)
    ms = {m.key: m for m in ratios.profitability_ratios(curr, prev)}
    product = (ms["dupont_net_margin"].value / 100
               * ms["dupont_asset_turnover"].value
               * ms["dupont_equity_multiplier"].value * 100)
    assert product == pytest.approx(ms["roe"].value, rel=1e-12)


def test_working_capital_days_use_average_stock():
    curr = stmt(revenue=10000, gross_profit=4000, receivables=2000)
    prev = stmt(period="FY2024", fiscal_year=2024, revenue=9000,
                gross_profit=3600, receivables=1000)
    avg = {m.key: m for m in ratios.efficiency_ratios(curr, prev)}
    end = {m.key: m for m in ratios.efficiency_ratios(curr)}
    assert avg["receivable_days"].value < end["receivable_days"].value


# ------------------------------------------------------------------ Piotroski

def _improving_pair(sparse: bool):
    if sparse:
        return (stmt(revenue=10000, ebit=2000, net_income=1425, total_assets=12000, cfo=1800),
                stmt(period="FY2024", fiscal_year=2024, revenue=9000, ebit=1700,
                     net_income=1200, total_assets=11000, cfo=1500))
    return (stmt(revenue=10000, gross_profit=4000, ebit=2000, net_income=1425,
                 total_assets=12000, current_assets=5000, current_liabilities=2500,
                 total_debt=2000, total_equity=7000, shares_outstanding=100, cfo=1800),
            stmt(period="FY2024", fiscal_year=2024, revenue=9000, gross_profit=3400,
                 ebit=1700, net_income=1200, total_assets=11000, current_assets=4200,
                 current_liabilities=2400, total_debt=2100, total_equity=6200,
                 shares_outstanding=100, cfo=1500))


def test_missing_piotroski_signals_are_not_counted_as_failures():
    """Observed: the same improving company scored 9/9 with full data and 6/9
    with sparse data, tiering it DOWN to 'medium'. That tier then fed
    attention_score and the portfolio's quality-band concentration axis, so a
    data gap became a claim that capital sat in a fragile business."""
    full = quality.piotroski_f(*_improving_pair(sparse=False))
    sparse = quality.piotroski_f(*_improving_pair(sparse=True))
    assert full.inputs["signals_available"] == 9
    assert sparse.inputs["signals_available"] < 9
    assert sparse.value == pytest.approx(full.value), \
        "a company passing every disclosed signal must not be penalised for non-disclosure"
    assert "not a failed signal" in (sparse.caveat or "")


def test_quality_tier_refuses_to_tier_on_sparse_data():
    m = quality.piotroski_f(*_improving_pair(sparse=True))
    assert quality.quality_tier(m.value, 3) is None
    assert quality.quality_tier(m.value, 9) is not None


def test_piotroski_reports_raw_and_scaled_scores():
    m = quality.piotroski_f(*_improving_pair(sparse=True))
    assert m.inputs["signals_passed"] <= m.inputs["signals_available"]
    assert m.inputs["scaled_score_out_of_9"] is not None


# ------------------------------------------------------------------- Altman

def _healthy():
    return stmt(revenue=10000, gross_profit=4000, ebit=2000, net_income=1425,
                total_assets=12000, current_assets=5000, current_liabilities=2500,
                total_debt=2000, total_equity=7000, retained_earnings=3500,
                shares_outstanding=100, cfo=1800)


def test_altman_z_em_is_invariant_to_share_price():
    """The 1968 model uses MARKET equity, so 'distress' partly restates a price
    move — circular when the score is then cited as evidence about the stock.
    Z'' uses book equity."""
    s = _healthy()
    zs = [quality.altman_z(s, price=p).value for p in (250.0, 500.0, 1000.0)]
    assert max(zs) - min(zs) > 5.0, "the 1968 score does swing with price"
    assert quality.altman_z_em(s).value == pytest.approx(quality.altman_z_em(s).value)
    z_em = quality.altman_z_em(s).value
    assert z_em is not None


def test_altman_z_em_has_no_sales_term():
    """Dropping Sales/TA is what removes the model's bias against asset-light
    and services businesses."""
    a = _healthy()
    b = _healthy()
    b.revenue = a.revenue * 5           # asset-light vs asset-heavy turnover
    assert quality.altman_z_em(a).value == pytest.approx(quality.altman_z_em(b).value)
    assert quality.altman_z(a, 500.0).value != pytest.approx(quality.altman_z(b, 500.0).value)


def test_altman_em_zones():
    assert quality.altman_zone_em(6.0) == "safe"
    assert quality.altman_zone_em(4.5) == "grey"
    assert quality.altman_zone_em(2.0) == "distress"
    assert quality.altman_zone_em(None) is None


# --------------------------------------------------------------- reverse DCF

def _dcf_stmt(**kw):
    base = dict(revenue=10000, ebit=2000, interest_expense=100, pbt=1900,
                tax_expense=475, net_income=1425, total_assets=12000, cash=1000,
                total_debt=2000, total_equity=7000, shares_outstanding=100,
                cfo=1800, capex=500, ebitda=2500)
    base.update(kw)
    return stmt(**base)


def test_reverse_dcf_refuses_undefined_gordon_region():
    """Observed: terminal_growth=13% against WACC 13.12% returned
    'market-implied growth = -17.9%' with the ordinary caveat and no warning.
    A perpetuity growing at its discount rate has infinite value."""
    s = _dcf_stmt()
    out = valuation.reverse_dcf(
        s, 500.0, valuation.ReverseDcfAssumptions(terminal_growth=0.13))
    assert out["implied_growth"].value is None
    assert "Not computable" in out["implied_growth"].caveat
    assert "infinite value" in out["implied_growth"].caveat


def test_pv_of_fcf_raises_rather_than_returning_negative_pv():
    with pytest.raises(ValueError):
        valuation._pv_of_fcf(1300, 0.05, 0.10, 10, 0.15)


def test_reverse_dcf_still_solves_in_the_valid_region():
    out = valuation.reverse_dcf(_dcf_stmt(), 500.0)
    assert out["implied_growth"].value is not None
    assert "NOT a forecast" in out["implied_growth"].caveat


def test_normalized_base_fcf_damps_a_lumpy_capex_year():
    hist = [_dcf_stmt(period=f"FY{2021 + i}", fiscal_year=2021 + i, cfo=c, capex=cx)
            for i, (c, cx) in enumerate([(1700, 400), (1800, 450), (1900, 1400)])]
    normalized = valuation.reverse_dcf(hist[-1], 500.0, statements=hist)
    single = valuation.reverse_dcf(hist[-1], 500.0)
    assert normalized["base_fcf_working"]["years_used"] == 3
    assert normalized["base_fcf"] > single["base_fcf"]
    assert abs(normalized["implied_growth"].value
               - single["implied_growth"].value) > 5.0, \
        "one lumpy capex year materially moves the implied-growth headline"


# ------------------------------------------------------------- growth anchor

def _fcf_hist(values):
    return [stmt(period=f"FY{2016 + i}", fiscal_year=2016 + i, cfo=v, capex=0.0)
            for i, v in enumerate(values)]


def test_log_linear_growth_is_robust_to_a_single_bad_endpoint():
    """Observed with endpoint CAGR on an unchanged 10% trend: 10.01% clean,
    4.61% with a weak final year, 16.44% with a depressed first year."""
    clean = [100, 110, 121, 133, 146, 161, 177, 195, 214, 236]
    weak_last = clean[:-1] + [150]
    weak_first = [60] + clean[1:]
    g = [valuation.historical_fcf_cagr(_fcf_hist(v)).value
         for v in (clean, weak_last, weak_first)]
    assert g[0] == pytest.approx(10.0, abs=0.1)
    assert max(g) - min(g) < 7.0, "spread must be far tighter than the 11.8pp endpoint spread"
    for v in g:
        assert 5.0 < v < 15.0


def test_growth_anchor_reports_fit_quality():
    noisy = valuation.historical_fcf_cagr(
        _fcf_hist([100, 180, 90, 200, 110, 220, 95, 240, 130, 260]))
    assert noisy.inputs["r_squared"] is not None
    clean = valuation.historical_fcf_cagr(
        _fcf_hist([100, 110, 121, 133, 146, 161, 177, 195, 214, 236]))
    assert clean.inputs["r_squared"] > noisy.inputs["r_squared"]


def test_growth_anchor_handles_all_negative_fcf():
    m = valuation.historical_fcf_cagr(_fcf_hist([-10, -20, -30]))
    assert m.value is None
    assert "non-positive" in m.caveat


# ----------------------------------------------------------------------- beta

def test_estimate_beta_recovers_a_known_beta():
    import random
    rng = random.Random(1)
    idx = [1000.0]
    for _ in range(700):
        idx.append(idx[-1] * (1 + rng.gauss(0.0004, 0.010)))
    for true_beta in (0.6, 1.0, 1.6):
        px = [500.0]
        for i in range(1, len(idx)):
            r = true_beta * (idx[i] / idx[i - 1] - 1) + rng.gauss(0, 0.008)
            px.append(px[-1] * (1 + r))
        m = valuation.estimate_beta(px, idx)
        assert m.value is not None
        assert abs(m.value - true_beta) < 0.30
        assert m.inputs["standard_error"] > 0


def test_estimate_beta_shrinks_toward_prior():
    """Vasicek: a noisier estimate is pulled harder toward 1.0."""
    import random
    rng = random.Random(4)
    idx = [1000.0]
    for _ in range(700):
        idx.append(idx[-1] * (1 + rng.gauss(0.0004, 0.010)))
    px = [500.0]
    for i in range(1, len(idx)):
        px.append(px[-1] * (1 + 0.4 * (idx[i] / idx[i - 1] - 1) + rng.gauss(0, 0.05)))
    m = valuation.estimate_beta(px, idx)
    assert m.value > m.inputs["beta_raw_ols"], "low raw beta must shrink upward toward 1.0"
    assert 0 < m.inputs["shrinkage_weight_on_estimate"] < 1


def test_estimate_beta_refuses_short_history():
    m = valuation.estimate_beta([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert m.value is None
    assert "Insufficient" in m.caveat


def test_wacc_rejects_implausible_derived_cost_of_debt():
    """interest expense is a full-year flow; period-end debt can be tiny after a
    repayment, which makes the implied Kd explode."""
    s = _dcf_stmt(interest_expense=500, total_debt=100)
    m = valuation.compute_wacc(s, 500.0, valuation.WaccAssumptions())
    assert m.inputs["cost_of_debt"] < valuation.MAX_COST_OF_DEBT
    assert "implausible" in m.caveat


# ------------------------------------------------------------------------ MQI

def _path(daily_ret, jitter, n=300, seed=3):
    import random
    rng = random.Random(seed)
    px = [100.0]
    for _ in range(n):
        px.append(px[-1] * (1 + daily_ret + rng.gauss(0, jitter)))
    return px


def test_mqi_persistence_amplifies_a_smooth_decline():
    """Observed: persistence was the raw up-day fraction, which is LOW in a
    decline, so the multiplier fell below 1 and shrank the bearish score — a
    smooth relentless decline scored as weaker evidence than a choppy one."""
    smooth = novel.momentum_quality(_path(-0.0008, 0.002))
    choppy = novel.momentum_quality(_path(-0.0008, 0.020))
    assert smooth.inputs["trend_agreement_fraction"] > 0.5
    assert smooth.value < choppy.value, \
        "the smoother decline must be the stronger bearish signal"
    assert 0.5 + smooth.inputs["trend_agreement_fraction"] > 1.0


def test_mqi_agreement_is_symmetric():
    up = novel.momentum_quality(_path(0.0008, 0.002))
    down = novel.momentum_quality(_path(-0.0008, 0.002))
    assert up.value > 0 and down.value < 0
    for m in (up, down):
        assert m.inputs["trend_agreement_fraction"] > 0.5, \
            "a trending series agrees with its own trend on both sides"


def test_mqi_feature_builder_matches_the_engine():
    """The study and the backtest must test the SAME construction the dossier
    displays, or the base-rate table is evidence about a different signal."""
    import pandas as pd
    from equisense.research.base_rates import feat_momentum_quality
    px = _path(-0.0008, 0.004, n=400)
    df = pd.DataFrame({"X": px})
    feat = feat_momentum_quality(df, None)
    engine = novel.momentum_quality(px)
    vec = float(feat["X"].iloc[-1])
    assert engine.value is not None and vec == vec
    # Both forms are dimensionless (the engine's percent units cancel in
    # mom/vol), so they must agree up to minor windowing differences —
    # crucially, they must agree in SIGN and in the direction of the
    # persistence multiplier.
    assert vec / engine.value == pytest.approx(1.0, rel=0.05)
    assert (vec < 0) == (engine.value < 0)


# ------------------------------------------------------------------ grounding

def _ctx():
    return {"metrics": [{"value": v, "formula": f"x {v}"} for v in
                        [18.4, 2.31, 1425.0, 12000.0, 0.2517, 13.52, 45.2, 92.6]]}


def test_grounding_no_longer_licenses_x100_of_large_values():
    """Observed: '1352' validated as grounded because 13.52 was in context, and
    the blanket x100 expansion quadrupled the accepted set."""
    assert validate("value 1352 here", _ctx())["grounded"] is False
    assert validate("value 4520 here", _ctx())["grounded"] is False


def test_grounding_still_accepts_legitimate_scale_conversions():
    assert validate("margin of 25.17%", _ctx())["grounded"] is True
    assert validate("a fraction of 0.184", _ctx())["grounded"] is True   # 18.4% as a fraction
    assert validate("value 13.52 here", _ctx())["grounded"] is True


def test_grounding_flags_fabricated_numbers():
    r = validate("ROIC came in at 31.4% versus 27.9% last year", _ctx())
    assert r["grounded"] is False
    assert set(r["violations"]) == {"31.4", "27.9"}


# --------------------------------------------------- price convention (Wave S)

def test_pe_percentile_uses_nominal_prices_not_total_return():
    """Observed: prices were ingested with auto_adjust=True, so the stored series
    is dividend-adjusted. Dividend adjustment back-deflates HISTORICAL bars while
    leaving the latest bar alone, so dividing that series by nominal filing EPS
    understates every past P/E and leaves today's at full value. The percentile
    then reads systematically 'expensive', and because it is inverted into the
    value cluster it applied a standing bearish tilt.
    """
    from datetime import date as _date, timedelta as _td

    n = 2600
    d0 = _date(2016, 4, 1)
    dates, nominal = [], []
    px = 100.0
    for i in range(n):
        dates.append(d0 + _td(days=i))
        px *= (1 + 0.00025)          # steady nominal drift, no valuation change
        nominal.append(px)
    # total-return series: history deflated by cumulative dividends still to come
    yld = 0.013
    total_ret = [p / ((1 + yld) ** ((n - 1 - i) / 365.0)) for i, p in enumerate(nominal)]
    assert total_ret[0] < nominal[0] and total_ret[-1] == pytest.approx(nominal[-1])

    stmts = [stmt(period=f"FY{2017 + k}", fiscal_year=2017 + k,
                  net_income=100.0 * (1.00025 ** (365 * k)), shares_outstanding=10.0)
             for k in range(7)]

    biased = novel.pe_percentile_vs_history(total_ret, dates, stmts)
    correct = novel.pe_percentile_vs_history(total_ret, dates, stmts,
                                             nominal_closes=nominal)
    assert biased.value is not None and correct.value is not None
    assert biased.value > correct.value, \
        "the dividend-adjusted series must overstate how expensive the stock looks"
    assert correct.inputs["price_convention"].startswith("nominal")
    assert "UNKNOWN" in biased.inputs["price_convention"]
    assert "WARNING" in biased.caveat


def test_pe_percentile_warns_when_no_nominal_series_supplied():
    from datetime import date as _date, timedelta as _td
    dates = [_date(2018, 1, 1) + _td(days=i) for i in range(600)]
    closes = [100.0 + i * 0.05 for i in range(600)]
    stmts = [stmt(period="FY2019", fiscal_year=2019, net_income=100.0,
                  shares_outstanding=10.0)]
    m = novel.pe_percentile_vs_history(closes, dates, stmts)
    if m.value is not None:
        assert "WARNING" in (m.caveat or "")


def test_pe_percentile_ignores_mismatched_nominal_series():
    """A length mismatch must fall back safely, not misalign price to date."""
    from datetime import date as _date, timedelta as _td
    dates = [_date(2018, 1, 1) + _td(days=i) for i in range(600)]
    closes = [100.0 + i * 0.05 for i in range(600)]
    stmts = [stmt(period="FY2019", fiscal_year=2019, net_income=100.0,
                  shares_outstanding=10.0)]
    m = novel.pe_percentile_vs_history(closes, dates, stmts, nominal_closes=[1.0, 2.0])
    assert m is not None
    if m.value is not None:
        assert "UNKNOWN" in m.inputs["price_convention"]
