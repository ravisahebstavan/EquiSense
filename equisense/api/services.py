"""Application services: assemble deterministic engine outputs for the API.

This layer orchestrates; it computes nothing itself (§17 dependency rule —
all ratio math lives in equisense.engine and only there).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import banking, personalization as pers
from ..engine import portfolio as pf
from ..engine import quality, ratios, valuation
from ..engine.types import Metric, StatementData
from ..models import (Company, FilingPeriod, InvestorProfileRow,
                      PriceObservation, SectorAttribute, Thesis,
                      TransactionRow, WatchlistItem)


def statement_data(fp: FilingPeriod) -> StatementData:
    fields = [f for f in StatementData.__dataclass_fields__
              if f not in ("period", "fiscal_year", "scope")]
    return StatementData(period=fp.period, fiscal_year=fp.fiscal_year, scope=fp.scope,
                         **{f: getattr(fp, f) for f in fields})


def latest_statements(session: Session, company_id: int,
                      scope: str = "consolidated") -> list[StatementData]:
    """Latest restatement version per fiscal year, oldest→newest (§14.4)."""
    rows = session.scalars(
        select(FilingPeriod)
        .where(FilingPeriod.company_id == company_id,
               FilingPeriod.scope == scope,
               FilingPeriod.is_latest.is_(True))
        .order_by(FilingPeriod.fiscal_year)).all()
    return [statement_data(r) for r in rows]


def latest_price(session: Session, company_id: int) -> Optional[float]:
    row = session.scalars(
        select(PriceObservation)
        .where(PriceObservation.company_id == company_id)
        .order_by(PriceObservation.obs_date.desc())).first()
    return row.close if row else None


def cagr(first: Optional[float], last: Optional[float], years: int) -> Optional[float]:
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


# ------------------------------------------------------------------ profile

def profile_from_row(row: InvestorProfileRow) -> pers.InvestorProfile:
    return pers.InvestorProfile(
        name=row.name, horizon=row.horizon,
        horizon_target_year=row.horizon_target_year,
        risk_tolerance=row.risk_tolerance, style=row.style,
        dividend_preference=row.dividend_preference,
        quality_emphasis=row.quality_emphasis,
        sector_preferences=[s.strip() for s in row.sector_preferences.split(",") if s.strip()],
        sector_exclusions=[s.strip() for s in row.sector_exclusions.split(",") if s.strip()],
        max_position_pct=row.max_position_pct, max_sector_pct=row.max_sector_pct,
        max_drawdown_pct=row.max_drawdown_pct, preferred_lens=row.preferred_lens,
        rules=[r.strip() for r in row.rules.splitlines() if r.strip()])


def active_profile(session: Session) -> pers.InvestorProfile:
    row = session.scalars(select(InvestorProfileRow)
                          .where(InvestorProfileRow.is_active.is_(True))).first()
    if row is None:
        row = session.scalars(select(InvestorProfileRow)).first()
    return profile_from_row(row) if row else pers.InvestorProfile()


# ---------------------------------------------------------------- signals

def company_signals(session: Session, company: Company) -> pers.CompanySignals:
    stmts = latest_statements(session, company.id)
    price = latest_price(session, company.id)
    sig = pers.CompanySignals(sector=company.sector)
    if not stmts:
        return sig
    curr = stmts[-1]
    if len(stmts) >= 2:
        f = quality.piotroski_f(curr, stmts[-2], price)
        sig.f_score = f.value
        sig.revenue_cagr_pct = cagr(stmts[0].revenue, curr.revenue,
                                    curr.fiscal_year - stmts[0].fiscal_year)
    prev = stmts[-2] if len(stmts) >= 2 else None
    # Z''-EM is the calibration-appropriate variant for Indian non-manufacturers
    # and does not move with the share price; keep the 1968 zone as a fallback.
    z_em = quality.altman_z_em(curr)
    if z_em.value is not None:
        sig.z_zone = quality.altman_zone_em(z_em.value)
    else:
        sig.z_zone = quality.altman_zone(quality.altman_z(curr, price).value)
    sig.roic_pct = ratios.roic(curr, prev=prev).value
    ps = {m.key: m for m in ratios.per_share_ratios(curr, price)}
    sig.pe = ps.get("pe").value if "pe" in ps else None
    sig.pb = ps.get("pb").value if "pb" in ps else None
    sig.dividend_yield_pct = ps.get("dividend_yield").value if "dividend_yield" in ps else None
    lev = {m.key: m for m in ratios.leverage_ratios(curr)}
    sig.debt_to_equity = lev["debt_to_equity"].value
    if price is not None:
        rd = valuation.reverse_dcf(curr, price, statements=stmts)
        hist = valuation.historical_fcf_cagr(stmts)
        if rd["implied_growth"].value is not None and hist and hist.value is not None:
            sig.implied_growth_gap_pct = rd["implied_growth"].value - hist.value
    return sig


# --------------------------------------------------------- company analysis

def _series(stmts: list[StatementData], fn) -> list[dict]:
    out = []
    for s in stmts:
        v = fn(s)
        out.append({"period": s.period, "value": None if v is None else round(v, 2)})
    return out


def _stale_sessions(session, ticker: str) -> int:
    """How many trading sessions this name's last price is behind the universe.

    Read from the universe snapshot rather than recomputed, so the detail page
    and the listings can never disagree about whether a name is stale.
    """
    try:
        from .snapshot import get_universe
        for item in get_universe(session)["companies"]:
            if item["ticker"] == ticker:
                return int(item.get("stale_sessions") or 0)
    except Exception:                                  # noqa: BLE001
        pass
    return 0


def company_analysis(session: Session, company: Company,
                     profile: pers.InvestorProfile,
                     dcf_assumptions: Optional[valuation.ReverseDcfAssumptions] = None) -> dict:
    stmts = latest_statements(session, company.id)
    price = latest_price(session, company.id)
    if not stmts:
        return {"error": "no filings"}
    curr = stmts[-1]
    prev = stmts[-2] if len(stmts) >= 2 else None
    years = curr.fiscal_year - stmts[0].fiscal_year

    def md(ms: list[Metric]) -> list[dict]:
        return [m.to_dict() for m in ms]

    # Financial-sector names get the banking model instead of the industrial
    # one: gross margin, inventory turns, Altman Z and a reverse DCF are all
    # meaningless for a leveraged spread business (see engine/banking.py).
    bank = banking.bank_summary(stmts) if company.is_financial else None
    f = (quality.piotroski_f(curr, prev, price)
         if prev and not company.is_financial else None)
    z = None if company.is_financial else quality.altman_z(curr, price)
    z_em = None if company.is_financial else quality.altman_z_em(curr)
    rd = (valuation.reverse_dcf(curr, price, dcf_assumptions, statements=stmts)
          if price and not company.is_financial else None)
    hist_fcf = valuation.historical_fcf_cagr(stmts)
    per_share = ratios.per_share_ratios(curr, price)
    ps_map = {m.key: m for m in per_share}

    trends = {
        "revenue": _series(stmts, lambda s: s.revenue),
        "net_income": _series(stmts, lambda s: s.net_income),
        "operating_margin": _series(stmts, lambda s: None if not s.revenue or s.ebit is None
                                    else s.ebit / s.revenue * 100),
        "roic": [{"period": st.period, "fiscal_year": st.fiscal_year,
                  "value": ratios.roic(st, prev=(stmts[i - 1] if i else None)).rounded(2)}
                 for i, st in enumerate(stmts)],
        "fcf": _series(stmts, lambda s: None if s.cfo is None or s.capex is None
                       else s.cfo - s.capex),
        "eps": _series(stmts, lambda s: None if not s.shares_outstanding or s.net_income is None
                       else s.net_income / s.shares_outstanding),
    }

    sector_attrs = session.scalars(
        select(SectorAttribute).where(SectorAttribute.company_id == company.id)).all()

    cards = {
        "quality_scores": {
            "title": "Banking Model" if company.is_financial else "Quality & Distress",
            "metrics": (bank["metrics"] + [bank["quality"]]
                        if bank and bank.get("analyzable")
                        else md([m for m in [f, z_em, z] if m])),
            "extras": ({"model": "banking", "data_gaps": bank["data_gaps"],
                        "not_applicable": bank["not_applicable"],
                        "note": bank["model_note"]}
                       if bank and bank.get("analyzable") else
                       {"model": "banking",
                        "unavailable": bank.get("reason")} if bank else
                       {"z_zone": quality.altman_zone(z.value) if z else None,
                        "z_em_zone": quality.altman_zone_em(z_em.value) if z_em else None,
                        "quality_tier": quality.quality_tier(
                            f.value if f else None,
                            int(f.inputs.get("signals_available", 0)) if f else None)}),
        },
        "profitability": {
            "title": "Spread & Returns" if company.is_financial else "Profitability & Returns",
            "metrics": (md(banking.banking_ratios(curr, prev)) if company.is_financial
                        else md(ratios.profitability_ratios(curr, prev)
                                + [ratios.roic(curr, prev=prev)])),
        },
        "growth_trends": {
            "title": "Growth & Trajectory",
            "metrics": [],
            "extras": {
                "revenue_cagr_pct": cagr(stmts[0].revenue, curr.revenue, years),
                "net_income_cagr_pct": cagr(stmts[0].net_income, curr.net_income, years),
                "window": f"{stmts[0].period}–{curr.period}",
            },
        },
        "valuation": {
            "title": "Valuation (implied expectations)",
            "metrics": md([m for m in [
                rd["implied_growth"] if rd else None,
                rd["wacc"] if rd else None,
                hist_fcf,
                ps_map.get("pe"), ps_map.get("pb"), ps_map.get("ev_ebitda")] if m]),
            "extras": {"assumptions": rd["assumptions"] if rd else None,
                       "enterprise_value": rd.get("enterprise_value") if rd else None,
                       "base_fcf": rd.get("base_fcf") if rd else None,
                       "price": price},
        },
        "cash_flow_quality": {
            "title": "Cash-Flow Quality",
            "metrics": md(quality.cash_flow_quality(curr)),
        },
        "income": {
            "title": "Income & Payout",
            "metrics": md([m for m in [ps_map.get("dividend_yield")] if m]),
            "extras": {"payout_ratio_pct": None if not curr.net_income or not curr.dividends_paid
                       else round(curr.dividends_paid / curr.net_income * 100, 1)},
        },
        "leverage_liquidity": {
            "title": "Leverage & Liquidity",
            "metrics": md(ratios.leverage_ratios(curr) + ratios.liquidity_ratios(curr)),
        },
        "efficiency": {
            "title": "Efficiency",
            "metrics": md(ratios.efficiency_ratios(curr, prev)),
        },
        "peer_comparison": {
            "title": "Peer Comparison",
            "table": peer_table(session, company),
        },
    }

    return {
        "company": {"id": company.id, "ticker": company.ticker, "name": company.name,
                    "sector": company.sector, "industry": company.industry,
                    "cap_band": company.cap_band, "description": company.description,
                    "is_demo_data": company.is_demo_data, "price": price,
                    # Carried so the page can mark a price that stopped updating.
                    # Without it the detail view renders a frozen quote exactly
                    # like a live one — the single most likely place for someone
                    # to read a price immediately before acting on it.
                    "stale_sessions": _stale_sessions(session, company.ticker)},
        "period": curr.period,
        "card_order": pers.card_order(profile),
        "cards": cards,
        "trends": trends,
        "per_share": md(per_share),
        "sector_attributes": [{"name": a.name, "value": a.value, "unit": a.unit,
                               "period": a.period} for a in sector_attrs],
        "statements": [vars(s) for s in stmts],
    }


def peer_table(session: Session, company: Company) -> list[dict]:
    peers = session.scalars(select(Company)
                            .where(Company.peer_group == company.peer_group)).all()
    rows = []
    for p in peers:
        stmts = latest_statements(session, p.id)
        if not stmts:
            continue
        curr = stmts[-1]
        prev = stmts[-2] if len(stmts) >= 2 else None
        price = latest_price(session, p.id)
        ps = {m.key: m for m in ratios.per_share_ratios(curr, price)}
        f = quality.piotroski_f(curr, prev, price) if prev else None
        z = quality.altman_z(curr, price)
        years = curr.fiscal_year - stmts[0].fiscal_year
        rows.append({
            "id": p.id, "ticker": p.ticker, "name": p.name,
            "is_self": p.id == company.id,
            "revenue": curr.revenue,
            "revenue_cagr_pct": cagr(stmts[0].revenue, curr.revenue, years),
            "operating_margin_pct": None if not curr.revenue or curr.ebit is None
            else round(curr.ebit / curr.revenue * 100, 1),
            "roic_pct": ratios.roic(curr, prev=prev).rounded(1),
            "pe": ps["pe"].rounded(1) if "pe" in ps else None,
            "ev_ebitda": ps["ev_ebitda"].rounded(1) if "ev_ebitda" in ps else None,
            "f_score": f.value if f else None,
            "z_zone": (quality.altman_zone_em(quality.altman_z_em(curr).value)
                       or quality.altman_zone(z.value)),
        })
    rows.sort(key=lambda r: -(r["revenue"] or 0))
    return rows


# ---------------------------------------------------------------- portfolio

def _txns(session: Session) -> list[pf.Transaction]:
    rows = session.scalars(select(TransactionRow)).all()
    return [pf.Transaction(company_id=r.company_id, side=r.side, quantity=r.quantity,
                           price=r.price, trade_date=r.trade_date, fees=r.fees)
            for r in rows]


def _dividends_by_company(session: Session, company_ids) -> dict:
    """{company_id: [(ex_date, dividend_per_share), ...]} for XIRR."""
    ids = [c for c in company_ids]
    if not ids:
        return {}
    rows = session.execute(
        select(PriceObservation.company_id, PriceObservation.obs_date,
               PriceObservation.dividend)
        .where(PriceObservation.company_id.in_(ids),
               PriceObservation.dividend.isnot(None),
               PriceObservation.dividend > 0)).all()
    out: dict = {}
    for cid, d, amt in rows:
        out.setdefault(cid, []).append((d, float(amt)))
    return out


def portfolio_view(session: Session, profile: pers.InvestorProfile) -> dict:
    from .snapshot import get_universe
    universe = {c["id"]: c for c in get_universe(session)["companies"]}
    txns = _txns(session)
    positions = pf.positions_from_ledger(txns)
    companies = {c.id: c for c in session.scalars(select(Company)).all()}
    today = date.today()

    prices, values = {}, {}
    for cid, pos in positions.items():
        snap = universe.get(cid)
        prices[cid] = (snap["price"] if snap else latest_price(session, cid)) or 0.0
        values[cid] = pos.quantity * prices[cid]

    quality_tiers = {}
    for cid in positions:
        snap = universe.get(cid)
        f = snap["signals"].get("f_score") if snap else None
        n_sig = snap["signals"].get("f_signals_available") if snap else None
        quality_tiers[cid] = quality.quality_tier(f, n_sig)

    conc = pf.concentration(
        positions, prices,
        sectors={cid: companies[cid].sector for cid in positions},
        cap_bands={cid: companies[cid].cap_band for cid in positions},
        quality_tiers=quality_tiers)

    # Fetched BEFORE the holdings loop, which consumes it per position.
    divs = _dividends_by_company(session, list(positions))

    holdings = []
    for cid, pos in sorted(positions.items(), key=lambda kv: -values[kv[0]]):
        if pos.quantity <= 1e-9:
            continue
        r = pf.position_xirr(txns, cid, values[cid], today, divs.get(cid))
        c = companies[cid]
        holdings.append({
            "company_id": cid, "ticker": c.ticker, "name": c.name,
            "sector": c.sector, "quantity": round(pos.quantity, 4),
            "avg_cost": round(pos.avg_cost, 2), "invested": round(pos.invested, 2),
            "price": prices[cid], "value": round(values[cid], 2),
            "unrealized_pnl": round(values[cid] - pos.invested, 2),
            "realized_pnl": round(pos.realized_pnl, 2),
            "xirr_pct": None if r is None else round(r * 100, 2),
            "weight_pct": conc["by_position"].get(cid),
            "quality_tier": quality_tiers.get(cid),
            "lots": pf.lot_aging(pos, today),
        })

    xirr_metric = pf.portfolio_xirr(txns, values, today, divs)

    # Profile-limit checks (diagnostic facts only — no trade suggestions, §11.5)
    breaches = []
    for cid, w in conc["by_position"].items():
        if w > profile.max_position_pct:
            breaches.append(f"{companies[cid].ticker} is {w:.1f}% of the book — above "
                            f"your stated {profile.max_position_pct:.0f}% single-position limit.")
    for sector, w in conc["by_sector"].items():
        if w > profile.max_sector_pct:
            breaches.append(f"{sector} is {w:.1f}% of the book — above your stated "
                            f"{profile.max_sector_pct:.0f}% sector limit.")

    # Deliberately NOT folded into `breaches`: a policy breach means the book is
    # correctly recorded and outside your rules; an unmatched sell means the book
    # is not correctly recorded at all. Merging them would let a broken ledger
    # read as a mere preference violation.
    integrity = pf.ledger_integrity(positions)

    invested_total = sum(p.invested for p in positions.values())
    return {
        "as_of": today.isoformat(),
        "total_value": conc["total_value"],
        "total_invested": round(invested_total, 2),
        "unrealized_pnl": round(conc["total_value"] - invested_total, 2),
        "realized_pnl": round(sum(p.realized_pnl for p in positions.values()), 2),
        "xirr": xirr_metric.to_dict() if xirr_metric else None,
        "holdings": holdings,
        "concentration": {
            "by_position": {companies[cid].ticker: w
                            for cid, w in conc["by_position"].items()},
            "by_sector": conc["by_sector"],
            "by_cap_band": conc["by_cap_band"],
            "by_quality_tier": conc["by_quality_tier"],
        },
        "profile_limit_breaches": breaches,
        # An unmatched sell means the ledger is not a possible trade sequence,
        # so every number above it is computed on a book that never existed.
        # It used to be absorbed in silence — worse, a fully-oversold name drops
        # out of `holdings` entirely (quantity 0), so the one place it could have
        # been noticed showed nothing at all.
        "data_integrity": {
            **integrity,
            "unmatched_sells": {companies[cid].ticker: round(q, 4)
                                for cid, q in integrity["unmatched_sells"].items()
                                if cid in companies},
        },
    }


# ---------------------------------------------------------------- dashboard

def signals_from_snapshot(item: dict) -> pers.CompanySignals:
    s = item["signals"]
    return pers.CompanySignals(
        sector=item["sector"], f_score=s.get("f_score"), z_zone=s.get("z_zone"),
        roic_pct=s.get("roic_pct"), revenue_cagr_pct=s.get("revenue_cagr_pct"),
        dividend_yield_pct=s.get("dividend_yield_pct"), pe=s.get("pe"),
        debt_to_equity=s.get("debt_to_equity"),
        implied_growth_gap_pct=s.get("implied_growth_gap_pct"))


def dashboard(session: Session, profile: pers.InvestorProfile,
              top: int = 40) -> dict:
    """Snapshot-backed: one row fetch + three small queries — serverless-fast
    (the naive per-company version cost 40s over network Postgres)."""
    from .snapshot import get_universe
    universe = get_universe(session)
    watch_ids = {w.company_id: w for w in session.scalars(select(WatchlistItem)).all()}
    positions = pf.positions_from_ledger(_txns(session))
    held_ids = {cid for cid, p in positions.items() if p.quantity > 1e-9}

    ranked = []
    for item in universe["companies"]:
        sig = signals_from_snapshot(item)
        score = pers.attention_score(profile, sig)
        cid = item["id"]
        s = item["signals"]
        ranked.append({
            "id": cid, "ticker": item["ticker"], "name": item["name"],
            "sector": item["sector"],
            "held": cid in held_ids, "watched": cid in watch_ids,
            "watch_rationale": watch_ids[cid].rationale if cid in watch_ids else None,
            "price": item["price"], "chg_1d_pct": item.get("chg_1d_pct"),
            "spark": item.get("spark", []),
            "priority": score,
            "signals": {"f_score": s.get("f_score"), "z_zone": s.get("z_zone"),
                        "roic_pct": s.get("roic_pct"),
                        "revenue_cagr_pct": s.get("revenue_cagr_pct"),
                        "pe": s.get("pe"),
                        "dividend_yield_pct": s.get("dividend_yield_pct"),
                        "implied_growth_gap_pct": s.get("implied_growth_gap_pct")},
        })
    ranked.sort(key=lambda r: -r["priority"]["score"])
    # The view renders a handful of rows but the payload shipped all 395 WITH
    # their 40-point sparklines: 471 KB to draw 8 rows, a quarter of it
    # sparkline arrays that were discarded. The full ranking is still returned —
    # it is cheap and callers count on it — but the price history rides along
    # only for the rows that can actually display one.
    for r in ranked[max(0, top):]:
        r["spark"] = []

    today = date.today()
    reviews_due = []
    for t in session.scalars(select(Thesis)).all():
        if t.status == "active" and t.review_date and t.review_date <= today:
            c = next((x for x in universe["companies"] if x["id"] == t.company_id), None)
            reviews_due.append({"thesis_id": t.id, "ticker": c["ticker"] if c else "?",
                                "statement": t.statement,
                                "review_date": t.review_date.isoformat()})

    return {"profile": profile.to_dict(), "ranked": ranked,
            "thesis_reviews_due": reviews_due}
