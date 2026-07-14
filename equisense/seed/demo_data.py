"""Demonstration seed data (PROJECT_DRAFT §28.2, §32).

⚠️ DEMO CONTENT. Revenue and profit series are approximations of public
figures for well-known Indian listed companies; all other line items are
derived to be internally consistent, not transcribed from filings. This data
exists to exercise the product — verify against actual filings before using
any number for a real decision. Real portfolio/thesis data lives only in the
gitignored local database.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ..db import Base, engine
from ..models import (Company, FilingPeriod, InvestorProfileRow, JournalEntry,
                      PriceObservation, SectorAttribute, Thesis,
                      TransactionRow, WatchlistItem)

# ticker: (name, sector, industry, cap_band, peer_group, description,
#          structural params, [(fy, revenue, net_income, shares)], current_price)
# structural params: dict of ratios used to derive a coherent statement set.
COMPANIES: dict[str, dict] = {
    "APOLLOHOSP": dict(
        name="Apollo Hospitals Enterprise", sector="Healthcare",
        industry="Hospitals", cap_band="large", peer_group="hospitals",
        description="India's largest private hospital chain; also runs pharmacy "
                    "distribution and digital health (Apollo 24/7).",
        params=dict(debt_to_rev=0.18, dep_ratio=0.035, asset_turnover=1.35,
                    equity_ratio=0.48, cash_ratio=0.06, ca_ratio=0.30,
                    cr_target=1.3, inv_ratio=0.055, rec_ratio=0.075,
                    pay_ratio=0.09, gross_margin=0.42, cfo_ni=1.6,
                    capex_ratio=0.055, payout=0.16),
        years=[(2021, 10560, 137, 14.4), (2022, 14663, 1056, 14.4),
               (2023, 16612, 819, 14.4), (2024, 19059, 899, 14.4),
               (2025, 21794, 1446, 14.4)],
        price=7150.0,
        sector_attrs=[("arpob_lakh_per_bed_pa", 62.0, "₹ lakh"),
                      ("bed_occupancy_pct", 68.0, "%")],
    ),
    "FORTIS": dict(
        name="Fortis Healthcare", sector="Healthcare",
        industry="Hospitals", cap_band="mid", peer_group="hospitals",
        description="Pan-India hospital network with a diagnostics arm (Agilus).",
        params=dict(debt_to_rev=0.12, dep_ratio=0.045, asset_turnover=0.85,
                    equity_ratio=0.65, cash_ratio=0.04, ca_ratio=0.22,
                    cr_target=1.1, inv_ratio=0.02, rec_ratio=0.11,
                    pay_ratio=0.10, gross_margin=0.40, cfo_ni=1.7,
                    capex_ratio=0.07, payout=0.10),
        years=[(2021, 4030, -50, 75.5), (2022, 5718, 790, 75.5),
               (2023, 6298, 633, 75.5), (2024, 6893, 645, 75.5),
               (2025, 7783, 830, 75.5)],
        price=640.0,
        sector_attrs=[("arpob_lakh_per_bed_pa", 59.0, "₹ lakh"),
                      ("bed_occupancy_pct", 66.0, "%")],
    ),
    "MAXHEALTH": dict(
        name="Max Healthcare Institute", sector="Healthcare",
        industry="Hospitals", cap_band="large", peer_group="hospitals",
        description="North-India-centred premium hospital operator with "
                    "industry-leading ARPOB.",
        params=dict(debt_to_rev=0.10, dep_ratio=0.04, asset_turnover=0.65,
                    equity_ratio=0.75, cash_ratio=0.05, ca_ratio=0.18,
                    cr_target=1.4, inv_ratio=0.015, rec_ratio=0.09,
                    pay_ratio=0.07, gross_margin=0.45, cfo_ni=1.4,
                    capex_ratio=0.10, payout=0.08),
        years=[(2021, 3629, -100, 97.0), (2022, 5059, 605, 97.0),
               (2023, 5406, 1104, 97.0), (2024, 5406, 1058, 97.0),
               (2025, 7028, 1128, 97.0)],
        price=1105.0,
        sector_attrs=[("arpob_lakh_per_bed_pa", 77.0, "₹ lakh"),
                      ("bed_occupancy_pct", 75.0, "%")],
    ),
    "NH": dict(
        name="Narayana Hrudayalaya", sector="Healthcare",
        industry="Hospitals", cap_band="mid", peer_group="hospitals",
        description="Affordable-care hospital group (cardiac-led), with a "
                    "growing Cayman Islands business.",
        params=dict(debt_to_rev=0.22, dep_ratio=0.05, asset_turnover=1.0,
                    equity_ratio=0.55, cash_ratio=0.08, ca_ratio=0.28,
                    cr_target=1.2, inv_ratio=0.02, rec_ratio=0.08,
                    pay_ratio=0.09, gross_margin=0.40, cfo_ni=1.5,
                    capex_ratio=0.09, payout=0.10),
        years=[(2021, 2582, 4, 20.4), (2022, 3701, 342, 20.4),
               (2023, 4525, 606, 20.4), (2024, 5018, 790, 20.4),
               (2025, 5482, 795, 20.4)],
        price=1750.0,
        sector_attrs=[("arpob_lakh_per_bed_pa", 42.0, "₹ lakh"),
                      ("bed_occupancy_pct", 62.0, "%")],
    ),
    "INFY": dict(
        name="Infosys", sector="Information Technology",
        industry="IT Services", cap_band="large", peer_group="it_services",
        description="Tier-1 Indian IT services and consulting exporter.",
        params=dict(debt_to_rev=0.02, dep_ratio=0.03, asset_turnover=1.15,
                    equity_ratio=0.70, cash_ratio=0.20, ca_ratio=0.45,
                    cr_target=2.0, inv_ratio=0.0, rec_ratio=0.17,
                    pay_ratio=0.02, gross_margin=0.31, cfo_ni=1.05,
                    capex_ratio=0.02, payout=0.60),
        years=[(2021, 100472, 19351, 425.0), (2022, 121641, 22110, 421.0),
               (2023, 146767, 24095, 414.0), (2024, 153670, 26233, 414.0),
               (2025, 162990, 26750, 415.0)],
        price=1580.0,
    ),
    "TCS": dict(
        name="Tata Consultancy Services", sector="Information Technology",
        industry="IT Services", cap_band="large", peer_group="it_services",
        description="India's largest IT services company by revenue and market cap.",
        params=dict(debt_to_rev=0.01, dep_ratio=0.02, asset_turnover=1.55,
                    equity_ratio=0.62, cash_ratio=0.10, ca_ratio=0.55,
                    cr_target=2.4, inv_ratio=0.0, rec_ratio=0.17,
                    pay_ratio=0.03, gross_margin=0.40, cfo_ni=0.95,
                    capex_ratio=0.015, payout=0.90),
        years=[(2021, 164177, 32430, 370.0), (2022, 191754, 38327, 366.0),
               (2023, 225458, 42147, 366.0), (2024, 240893, 45908, 362.0),
               (2025, 255324, 48553, 362.0)],
        price=3420.0,
    ),
    "HINDUNILVR": dict(
        name="Hindustan Unilever", sector="Consumer Staples",
        industry="FMCG", cap_band="large", peer_group="fmcg",
        description="India's largest FMCG company: home care, beauty & "
                    "personal care, foods.",
        params=dict(debt_to_rev=0.02, dep_ratio=0.02, asset_turnover=0.85,
                    equity_ratio=0.70, cash_ratio=0.08, ca_ratio=0.22,
                    cr_target=1.3, inv_ratio=0.06, rec_ratio=0.04,
                    pay_ratio=0.15, gross_margin=0.51, cfo_ni=1.0,
                    capex_ratio=0.02, payout=0.92),
        years=[(2021, 45996, 7954, 235.0), (2022, 51193, 8818, 235.0),
               (2023, 58154, 9962, 235.0), (2024, 59579, 10114, 235.0),
               (2025, 60680, 10644, 235.0)],
        price=2340.0,
    ),
    "ITC": dict(
        name="ITC", sector="Consumer Staples",
        industry="FMCG / Diversified", cap_band="large", peer_group="fmcg",
        description="Cigarettes, FMCG, paperboards and agri-business "
                    "conglomerate (hotels demerged 2024).",
        params=dict(debt_to_rev=0.005, dep_ratio=0.025, asset_turnover=0.80,
                    equity_ratio=0.85, cash_ratio=0.12, ca_ratio=0.35,
                    cr_target=2.8, inv_ratio=0.15, rec_ratio=0.04,
                    pay_ratio=0.06, gross_margin=0.58, cfo_ni=0.90,
                    capex_ratio=0.04, payout=0.95),
        years=[(2021, 48151, 13032, 1231.0), (2022, 59101, 15058, 1232.0),
               (2023, 69481, 18753, 1242.0), (2024, 69446, 20422, 1248.0),
               (2025, 73465, 20092, 1251.0)],
        price=420.0,
    ),
    "ASIANPAINT": dict(
        name="Asian Paints", sector="Consumer Discretionary",
        industry="Paints", cap_band="large", peer_group="paints",
        description="India's dominant decorative paints franchise, facing "
                    "new well-funded entrants.",
        params=dict(debt_to_rev=0.03, dep_ratio=0.03, asset_turnover=1.05,
                    equity_ratio=0.65, cash_ratio=0.07, ca_ratio=0.45,
                    cr_target=1.9, inv_ratio=0.17, rec_ratio=0.12,
                    pay_ratio=0.12, gross_margin=0.42, cfo_ni=1.05,
                    capex_ratio=0.035, payout=0.60),
        years=[(2021, 21713, 3139, 95.9), (2022, 28927, 3053, 95.9),
               (2023, 34489, 4106, 95.9), (2024, 35382, 5460, 95.9),
               (2025, 33906, 3925, 95.9)],
        price=2290.0,
    ),
}

TAX_RATE = 0.25
INTEREST_RATE = 0.085


def _derive_filing(fy: int, revenue: float, ni: float, shares: float,
                   p: dict, prev_retained: float) -> dict:
    """Derive a full, internally consistent statement set from revenue, net
    income and structural ratios (tax = PBT − NI by construction, etc.)."""
    debt = revenue * p["debt_to_rev"]
    interest = debt * INTEREST_RATE
    pbt = ni / (1 - TAX_RATE) if ni > 0 else ni * 1.05
    tax = pbt - ni
    ebit = pbt + interest
    dep = revenue * p["dep_ratio"]
    ebitda = ebit + dep
    total_assets = revenue / p["asset_turnover"]
    equity = total_assets * p["equity_ratio"]
    retained = max(prev_retained + ni * (1 - p["payout"]), equity * 0.3)
    cash = total_assets * p["cash_ratio"]
    ca = total_assets * p["ca_ratio"]
    cl = ca / p["cr_target"]
    dividends = max(ni, 0.0) * p["payout"]
    return dict(
        period=f"FY{fy}", fiscal_year=fy, scope="consolidated",
        filing_date=date(fy, 5, 30), restatement_version=1, is_latest=True,
        revenue=round(revenue, 1), gross_profit=round(revenue * p["gross_margin"], 1),
        ebitda=round(ebitda, 1), depreciation=round(dep, 1), ebit=round(ebit, 1),
        interest_expense=round(interest, 1), pbt=round(pbt, 1),
        tax_expense=round(tax, 1), net_income=round(ni, 1),
        total_assets=round(total_assets, 1), current_assets=round(ca, 1),
        cash=round(cash, 1),
        inventory=round(revenue * p["inv_ratio"], 1),
        receivables=round(revenue * p["rec_ratio"], 1),
        current_liabilities=round(cl, 1),
        payables=round(revenue * p["pay_ratio"], 1),
        total_debt=round(debt, 1), total_equity=round(equity, 1),
        retained_earnings=round(min(retained, equity * 0.95), 1),
        shares_outstanding=shares,
        cfo=round(ni * p["cfo_ni"] if ni > 0 else revenue * 0.05, 1),
        capex=round(revenue * p["capex_ratio"], 1),
        dividends_paid=round(dividends, 1),
    )


def seed(session: Session, include_demo_portfolio: bool = True) -> None:
    Base.metadata.create_all(engine)
    if session.query(Company).count() > 0:
        return  # already seeded

    ids: dict[str, int] = {}
    for ticker, cfg in COMPANIES.items():
        c = Company(ticker=ticker, name=cfg["name"], sector=cfg["sector"],
                    industry=cfg["industry"], cap_band=cfg["cap_band"],
                    peer_group=cfg["peer_group"], description=cfg["description"],
                    is_demo_data=True)
        session.add(c)
        session.flush()
        ids[ticker] = c.id

        prev_retained = 0.0
        for fy, rev, ni, shares in cfg["years"]:
            row = _derive_filing(fy, rev, ni, shares, cfg["params"], prev_retained)
            prev_retained = row["retained_earnings"]
            session.add(FilingPeriod(company_id=c.id, **row))

        # simple annual price path ending at the current price
        price = cfg["price"]
        for i, (fy, *_rest) in enumerate(reversed(cfg["years"])):
            session.add(PriceObservation(company_id=c.id,
                                         obs_date=date(fy, 3, 31),
                                         close=round(price / (1.12 ** i), 2)))
        session.add(PriceObservation(company_id=c.id, obs_date=date(2026, 7, 10),
                                     close=price))

        for name, value, unit in cfg.get("sector_attrs", []):
            session.add(SectorAttribute(company_id=c.id, period="FY2025",
                                        name=name, value=value, unit=unit))

    # Default investor profile (Persona 1-ish, §4) — the user edits this in-app.
    session.add(InvestorProfileRow(
        name="default", is_active=True, horizon="long", horizon_target_year=2032,
        risk_tolerance="moderate", style=55.0, dividend_preference=20.0,
        quality_emphasis=70.0, sector_preferences="Healthcare",
        max_position_pct=12.0, max_sector_pct=35.0, max_drawdown_pct=25.0,
        preferred_lens="balanced",
        rules="Never buy above 60x trailing earnings\nMax single position 12%\n"
              "Always check cash-flow quality before adding"))

    if include_demo_portfolio:
        demo_txns = [
            ("APOLLOHOSP", "buy", 10, 5100.0, date(2024, 2, 12)),
            ("APOLLOHOSP", "buy", 5, 6200.0, date(2025, 1, 20)),
            ("INFY", "buy", 60, 1380.0, date(2023, 11, 3)),
            ("ITC", "buy", 200, 415.0, date(2024, 6, 18)),
            ("ITC", "sell", 50, 470.0, date(2025, 3, 7)),
            ("NH", "buy", 40, 1210.0, date(2024, 9, 25)),
        ]
        for ticker, side, qty, price, d in demo_txns:
            session.add(TransactionRow(company_id=ids[ticker], side=side,
                                       quantity=qty, price=price, trade_date=d))

        session.add(Thesis(
            company_id=ids["APOLLOHOSP"], status="active",
            statement="Apollo compounds earnings ~18% p.a. as hospital margins "
                      "normalize and 24/7 losses shrink toward breakeven.",
            assumptions="New bed additions (~2,000 beds) commissioned by FY27\n"
                        "Healthco (24/7) EBITDA loss below ₹150 cr by FY26\n"
                        "Mature-hospital occupancy stays above 65%",
            invalidation_triggers="Occupancy falls below 60% for two consecutive quarters\n"
                                  "24/7 losses widen YoY in FY26\n"
                                  "Net debt/EBITDA rises above 2.0x",
            sizing_rationale="Core position within the 12% single-position cap.",
            review_date=date(2026, 10, 1)))
        session.add(Thesis(
            company_id=ids["ITC"], status="active",
            statement="ITC is a cash-machine at an undemanding multiple; FMCG "
                      "margin expansion is optionality, cigarettes fund the yield.",
            assumptions="Cigarette volume grows 0–5% p.a. (no punitive tax shock)\n"
                        "FMCG-other EBIT margin reaches 11% by FY27\n"
                        "Payout ratio stays ≥ 85%",
            invalidation_triggers="GST/excise increase above 15% in a single budget\n"
                                  "FMCG margin regresses below 8%\n"
                                  "Large diversifying acquisition outside stated verticals",
            sizing_rationale="Income anchor; sized for dividend contribution.",
            review_date=date(2026, 8, 15)))

        session.add(JournalEntry(
            company_id=ids["MAXHEALTH"],
            content="Applied DuPont 5-way from CFA L1 to Max vs Apollo: Max's ROE "
                    "advantage is margin-driven (premium ARPOB), not leverage. "
                    "Worth watching whether new brownfield beds dilute ARPOB.",
            cfa_topic="FSA — DuPont decomposition"))
        session.add(JournalEntry(
            company_id=ids["ASIANPAINT"],
            content="Grasim's Birla Opus entry is the first credible threat to the "
                    "duopoly economics. Margin trajectory FY25 already shows it. "
                    "Not a buy-the-dip until competitive intensity is priced.",
            cfa_topic="Equity — industry analysis (Porter)"))

        for ticker, why in [
            ("MAXHEALTH", "Best-in-class ARPOB; waiting for a valuation reset "
                          "below 60x before sizing."),
            ("ASIANPAINT", "Franchise under first real competitive attack in "
                           "decades — watching margin stabilization."),
            ("TCS", "Steady compounder; would add on drawdown toward 20x."),
        ]:
            session.add(WatchlistItem(company_id=ids[ticker], rationale=why))

    session.commit()
