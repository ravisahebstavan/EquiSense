import pytest

from equisense.engine.types import StatementData


@pytest.fixture
def fy2025() -> StatementData:
    """Synthetic reference company. Every expected value in the test suite was
    hand-computed from these inputs (PROJECT_DRAFT §29.1 hard gate)."""
    return StatementData(
        period="FY2025", fiscal_year=2025,
        revenue=1000.0, gross_profit=400.0, ebitda=250.0, depreciation=50.0,
        ebit=200.0, interest_expense=20.0, pbt=180.0, tax_expense=45.0,
        net_income=135.0,
        total_assets=1200.0, current_assets=500.0, cash=100.0, inventory=150.0,
        receivables=120.0, current_liabilities=250.0, payables=80.0,
        total_debt=200.0, total_equity=700.0, retained_earnings=400.0,
        shares_outstanding=10.0,
        cfo=180.0, capex=60.0, dividends_paid=30.0,
    )


@pytest.fixture
def fy2024() -> StatementData:
    return StatementData(
        period="FY2024", fiscal_year=2024,
        revenue=900.0, gross_profit=350.0, ebitda=215.0, depreciation=45.0,
        ebit=170.0, interest_expense=22.0, pbt=148.0, tax_expense=38.0,
        net_income=110.0,
        total_assets=1100.0, current_assets=450.0, cash=80.0, inventory=140.0,
        receivables=110.0, current_liabilities=240.0, payables=75.0,
        total_debt=220.0, total_equity=620.0, retained_earnings=330.0,
        shares_outstanding=10.0,
        cfo=150.0, capex=55.0, dividends_paid=25.0,
    )
