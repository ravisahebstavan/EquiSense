"""Banking engine invariants.

Financials were previously skipped entirely, leaving 11 of the NIFTY-50 with no
fundamental analysis at all. These tests pin the model that replaced that
blindness — and, just as importantly, pin what it REFUSES to say.
"""
from __future__ import annotations

import pytest

from equisense.engine import banking
from equisense.engine.types import StatementData


def bank(**kw) -> StatementData:
    base = dict(period="FY2026", fiscal_year=2026, revenue=192_567.0,
                interest_income=336_026.0, interest_expense=186_050.0,
                net_interest_income=149_977.0, net_income=70_479.0,
                pbt=93_903.0, tax_expense=21_193.0, ebit=93_903.0,
                total_assets=5_262_119.0, total_equity=816_740.0,
                shares_outstanding=1_539.0)
    base.update(kw)
    return StatementData(**base)


def test_net_interest_income_falls_back_to_the_spread():
    s = bank(net_interest_income=None)
    assert banking.net_interest_income(s) == pytest.approx(336_026.0 - 186_050.0)


def test_bank_is_not_analyzable_without_assets_or_interest():
    assert banking.is_bank_analyzable(bank()) is True
    assert banking.is_bank_analyzable(
        StatementData(period="FY26", fiscal_year=2026, revenue=100.0)) is False


def test_dupont_reconciles_roa_times_leverage_to_roe():
    """ROE = ROA x leverage exactly. Two banks with identical ROE can be
    completely different businesses — one earning it, one borrowing it — and
    this decomposition is what says which."""
    m = {x.key: x for x in banking.banking_ratios(bank())}
    roa, lev, roe = m["bank_roa"].value, m["equity_multiplier"].value, m["bank_roe"].value
    assert m["bank_dupont"].value == pytest.approx(roe, rel=1e-9)
    assert roa * lev == pytest.approx(roe, rel=1e-9)


def test_spread_equals_yield_minus_cost_of_funds():
    m = {x.key: x for x in banking.banking_ratios(bank())}
    assert m["interest_spread"].value == pytest.approx(
        m["yield_on_assets"].value - m["cost_of_funds"].value, rel=1e-9)


def test_returns_use_average_balances_when_prior_period_supplied():
    curr = bank()
    prev = bank(period="FY2025", fiscal_year=2025, total_assets=4_000_000.0,
                total_equity=700_000.0)
    avg = {x.key: x for x in banking.banking_ratios(curr, prev)}
    end = {x.key: x for x in banking.banking_ratios(curr)}
    assert avg["bank_roa"].inputs["denominator"] == "average"
    assert avg["bank_roa"].value > end["bank_roa"].value


def test_quality_score_refuses_to_produce_a_number():
    """The load-bearing refusal. A composite built only from margin, leverage
    and returns would rate a bank with a rotting loan book as high quality,
    because asset-quality deterioration surfaces in provisions AFTER it surfaces
    in nothing else."""
    m = banking.bank_quality_score([bank()])
    assert m.value is None
    assert "NOT COMPUTED BY DESIGN" in m.caveat
    assert m.inputs["missing_for_a_defensible_score"] == banking.BANK_DATA_GAPS
    assert m.inputs["profitability_trend"], "the trend must still be shown"


def test_summary_names_the_models_that_do_not_apply():
    out = banking.bank_summary([bank()])
    assert out["analyzable"] is True
    na = out["not_applicable"]
    assert "altman_z" in na and "leverage is the business model" in na["altman_z"]
    assert "piotroski_f" in na
    assert "reverse_dcf" in na


def test_summary_reports_the_data_gaps_it_cannot_close():
    out = banking.bank_summary([bank()])
    joined = " ".join(out["data_gaps"]).lower()
    for term in ("asset quality", "capital adequacy", "casa"):
        assert term in joined


def test_nim_is_flagged_as_an_assets_denominator_not_earning_assets():
    m = {x.key: x for x in banking.banking_ratios(bank())}
    assert "EARNING assets" in m["net_interest_margin"].caveat


def test_leverage_caveat_explains_the_amplification():
    m = {x.key: x for x in banking.banking_ratios(bank())}
    assert "not a distress signal" in m["equity_multiplier"].caveat


def test_unanalyzable_bank_reports_gaps_rather_than_raising():
    out = banking.bank_summary([StatementData(period="FY26", fiscal_year=2026)])
    assert out["analyzable"] is False
    assert out["data_gaps"]
