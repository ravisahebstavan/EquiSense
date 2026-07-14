"""Core ratio engine (PROJECT_DRAFT §10.2).

Five families: liquidity, leverage, profitability, efficiency, per-share /
valuation-adjacent. Ratio definitions live here and only here — no other
module recomputes them (single source of truth, §10.2, §17).

Every function is pure: StatementData in, list[Metric] out.
"""
from __future__ import annotations

from typing import Optional

from .types import Metric, StatementData, fmt, safe_div


def _m(key, label, value, unit, formula, inputs, period, family, caveat=None) -> Metric:
    return Metric(key=key, label=label, value=value, unit=unit, formula=formula,
                  inputs=inputs, period=period, family=family, caveat=caveat)


# ---------------------------------------------------------------- liquidity

def liquidity_ratios(s: StatementData) -> list[Metric]:
    out = []
    cr = safe_div(s.current_assets, s.current_liabilities)
    out.append(_m("current_ratio", "Current Ratio", cr, "x",
                  f"Current assets {fmt(s.current_assets)} / Current liabilities {fmt(s.current_liabilities)}",
                  {"current_assets": s.current_assets, "current_liabilities": s.current_liabilities},
                  s.period, "liquidity"))
    quick_assets = None
    if s.current_assets is not None and s.inventory is not None:
        quick_assets = s.current_assets - s.inventory
    qr = safe_div(quick_assets, s.current_liabilities)
    out.append(_m("quick_ratio", "Quick Ratio", qr, "x",
                  f"(Current assets {fmt(s.current_assets)} − Inventory {fmt(s.inventory)}) / Current liabilities {fmt(s.current_liabilities)}",
                  {"current_assets": s.current_assets, "inventory": s.inventory,
                   "current_liabilities": s.current_liabilities},
                  s.period, "liquidity"))
    cashr = safe_div(s.cash, s.current_liabilities)
    out.append(_m("cash_ratio", "Cash Ratio", cashr, "x",
                  f"Cash & equivalents {fmt(s.cash)} / Current liabilities {fmt(s.current_liabilities)}",
                  {"cash": s.cash, "current_liabilities": s.current_liabilities},
                  s.period, "liquidity"))
    wc = None
    if s.current_assets is not None and s.current_liabilities is not None:
        wc = s.current_assets - s.current_liabilities
    out.append(_m("working_capital", "Working Capital", wc, "₹ cr",
                  f"Current assets {fmt(s.current_assets)} − Current liabilities {fmt(s.current_liabilities)}",
                  {"current_assets": s.current_assets, "current_liabilities": s.current_liabilities},
                  s.period, "liquidity"))
    return out


# ----------------------------------------------------------------- leverage

def leverage_ratios(s: StatementData) -> list[Metric]:
    out = []
    de = safe_div(s.total_debt, s.total_equity)
    out.append(_m("debt_to_equity", "Debt / Equity", de, "x",
                  f"Total debt {fmt(s.total_debt)} / Total equity {fmt(s.total_equity)}",
                  {"total_debt": s.total_debt, "total_equity": s.total_equity},
                  s.period, "leverage"))
    net_debt = None
    if s.total_debt is not None and s.cash is not None:
        net_debt = s.total_debt - s.cash
    nde = safe_div(net_debt, s.ebitda)
    out.append(_m("net_debt_to_ebitda", "Net Debt / EBITDA", nde, "x",
                  f"(Total debt {fmt(s.total_debt)} − Cash {fmt(s.cash)}) / EBITDA {fmt(s.ebitda)}",
                  {"total_debt": s.total_debt, "cash": s.cash, "ebitda": s.ebitda},
                  s.period, "leverage"))
    ic = safe_div(s.ebit, s.interest_expense)
    out.append(_m("interest_coverage", "Interest Coverage", ic, "x",
                  f"EBIT {fmt(s.ebit)} / Interest expense {fmt(s.interest_expense)}",
                  {"ebit": s.ebit, "interest_expense": s.interest_expense},
                  s.period, "leverage"))
    return out


# ------------------------------------------------------------- profitability

def profitability_ratios(s: StatementData) -> list[Metric]:
    out = []
    gm = safe_div(s.gross_profit, s.revenue)
    out.append(_m("gross_margin", "Gross Margin", None if gm is None else gm * 100, "%",
                  f"Gross profit {fmt(s.gross_profit)} / Revenue {fmt(s.revenue)}",
                  {"gross_profit": s.gross_profit, "revenue": s.revenue},
                  s.period, "profitability"))
    om = safe_div(s.ebit, s.revenue)
    out.append(_m("operating_margin", "Operating Margin (EBIT)", None if om is None else om * 100, "%",
                  f"EBIT {fmt(s.ebit)} / Revenue {fmt(s.revenue)}",
                  {"ebit": s.ebit, "revenue": s.revenue},
                  s.period, "profitability"))
    em = safe_div(s.ebitda, s.revenue)
    out.append(_m("ebitda_margin", "EBITDA Margin", None if em is None else em * 100, "%",
                  f"EBITDA {fmt(s.ebitda)} / Revenue {fmt(s.revenue)}",
                  {"ebitda": s.ebitda, "revenue": s.revenue},
                  s.period, "profitability"))
    nm = safe_div(s.net_income, s.revenue)
    out.append(_m("net_margin", "Net Margin", None if nm is None else nm * 100, "%",
                  f"Net income {fmt(s.net_income)} / Revenue {fmt(s.revenue)}",
                  {"net_income": s.net_income, "revenue": s.revenue},
                  s.period, "profitability"))
    roe = safe_div(s.net_income, s.total_equity)
    out.append(_m("roe", "Return on Equity", None if roe is None else roe * 100, "%",
                  f"Net income {fmt(s.net_income)} / Total equity {fmt(s.total_equity)}",
                  {"net_income": s.net_income, "total_equity": s.total_equity},
                  s.period, "profitability"))
    roa = safe_div(s.net_income, s.total_assets)
    out.append(_m("roa", "Return on Assets", None if roa is None else roa * 100, "%",
                  f"Net income {fmt(s.net_income)} / Total assets {fmt(s.total_assets)}",
                  {"net_income": s.net_income, "total_assets": s.total_assets},
                  s.period, "profitability"))
    out.extend(dupont_decomposition(s))
    return out


def dupont_decomposition(s: StatementData) -> list[Metric]:
    """3-way DuPont: ROE = Net margin × Asset turnover × Equity multiplier."""
    nm = safe_div(s.net_income, s.revenue)
    at = safe_div(s.revenue, s.total_assets)
    em = safe_div(s.total_assets, s.total_equity)
    out = [
        _m("dupont_net_margin", "DuPont · Net Margin", None if nm is None else nm * 100, "%",
           f"Net income {fmt(s.net_income)} / Revenue {fmt(s.revenue)}",
           {"net_income": s.net_income, "revenue": s.revenue}, s.period, "profitability"),
        _m("dupont_asset_turnover", "DuPont · Asset Turnover", at, "x",
           f"Revenue {fmt(s.revenue)} / Total assets {fmt(s.total_assets)}",
           {"revenue": s.revenue, "total_assets": s.total_assets}, s.period, "profitability"),
        _m("dupont_equity_multiplier", "DuPont · Equity Multiplier", em, "x",
           f"Total assets {fmt(s.total_assets)} / Total equity {fmt(s.total_equity)}",
           {"total_assets": s.total_assets, "total_equity": s.total_equity}, s.period, "profitability"),
    ]
    return out


def roic(s: StatementData, tax_rate: Optional[float] = None) -> Metric:
    """ROIC = NOPAT / invested capital.

    NOPAT = EBIT × (1 − effective tax rate); invested capital = total debt +
    total equity − cash (§10.3). Effective tax rate from the filing when
    derivable, else the supplied assumption.
    """
    eff_tax = tax_rate
    if eff_tax is None and s.tax_expense is not None and s.pbt not in (None, 0):
        eff_tax = s.tax_expense / s.pbt
    nopat = None if (s.ebit is None or eff_tax is None) else s.ebit * (1 - eff_tax)
    ic = None
    if s.total_debt is not None and s.total_equity is not None and s.cash is not None:
        ic = s.total_debt + s.total_equity - s.cash
    val = safe_div(nopat, ic)
    return _m("roic", "Return on Invested Capital", None if val is None else val * 100, "%",
              f"NOPAT (EBIT {fmt(s.ebit)} × (1 − tax {fmt(None if eff_tax is None else eff_tax * 100)}%)) "
              f"/ Invested capital (debt {fmt(s.total_debt)} + equity {fmt(s.total_equity)} − cash {fmt(s.cash)})",
              {"ebit": s.ebit, "effective_tax_rate": eff_tax, "total_debt": s.total_debt,
               "total_equity": s.total_equity, "cash": s.cash},
              s.period, "profitability")


# ---------------------------------------------------------------- efficiency

def efficiency_ratios(s: StatementData) -> list[Metric]:
    out = []
    at = safe_div(s.revenue, s.total_assets)
    out.append(_m("asset_turnover", "Asset Turnover", at, "x",
                  f"Revenue {fmt(s.revenue)} / Total assets {fmt(s.total_assets)}",
                  {"revenue": s.revenue, "total_assets": s.total_assets},
                  s.period, "efficiency"))
    cogs = None
    if s.revenue is not None and s.gross_profit is not None:
        cogs = s.revenue - s.gross_profit
    inv_days = safe_div(s.inventory, cogs)
    inv_days = None if inv_days is None else inv_days * 365
    out.append(_m("inventory_days", "Inventory Days", inv_days, "days",
                  f"Inventory {fmt(s.inventory)} / COGS {fmt(cogs)} × 365",
                  {"inventory": s.inventory, "cogs": cogs}, s.period, "efficiency"))
    rec_days = safe_div(s.receivables, s.revenue)
    rec_days = None if rec_days is None else rec_days * 365
    out.append(_m("receivable_days", "Receivable Days", rec_days, "days",
                  f"Receivables {fmt(s.receivables)} / Revenue {fmt(s.revenue)} × 365",
                  {"receivables": s.receivables, "revenue": s.revenue}, s.period, "efficiency"))
    pay_days = safe_div(s.payables, cogs)
    pay_days = None if pay_days is None else pay_days * 365
    out.append(_m("payable_days", "Payable Days", pay_days, "days",
                  f"Payables {fmt(s.payables)} / COGS {fmt(cogs)} × 365",
                  {"payables": s.payables, "cogs": cogs}, s.period, "efficiency"))
    ccc = None
    if inv_days is not None and rec_days is not None and pay_days is not None:
        ccc = inv_days + rec_days - pay_days
    out.append(_m("cash_conversion_cycle", "Cash Conversion Cycle", ccc, "days",
                  f"Inventory days {fmt(inv_days)} + Receivable days {fmt(rec_days)} − Payable days {fmt(pay_days)}",
                  {"inventory_days": inv_days, "receivable_days": rec_days, "payable_days": pay_days},
                  s.period, "efficiency"))
    return out


# ---------------------------------------------------- per-share & valuation

def per_share_ratios(s: StatementData, price: Optional[float] = None) -> list[Metric]:
    """price in ₹ per share; monetary statement values in ₹ crore, shares in crore
    → per-share values come out directly in ₹."""
    out = []
    eps = safe_div(s.net_income, s.shares_outstanding)
    out.append(_m("eps", "Earnings per Share", eps, "₹",
                  f"Net income {fmt(s.net_income)} cr / Shares {fmt(s.shares_outstanding, 2)} cr",
                  {"net_income": s.net_income, "shares_outstanding": s.shares_outstanding},
                  s.period, "per_share"))
    bvps = safe_div(s.total_equity, s.shares_outstanding)
    out.append(_m("book_value_per_share", "Book Value per Share", bvps, "₹",
                  f"Total equity {fmt(s.total_equity)} cr / Shares {fmt(s.shares_outstanding, 2)} cr",
                  {"total_equity": s.total_equity, "shares_outstanding": s.shares_outstanding},
                  s.period, "per_share"))
    if price is not None:
        pe = safe_div(price, eps)
        out.append(_m("pe", "Price / Earnings", pe, "x",
                      f"Price ₹{fmt(price)} / EPS ₹{fmt(eps)}",
                      {"price": price, "eps": eps}, s.period, "per_share"))
        pb = safe_div(price, bvps)
        out.append(_m("pb", "Price / Book", pb, "x",
                      f"Price ₹{fmt(price)} / BVPS ₹{fmt(bvps)}",
                      {"price": price, "book_value_per_share": bvps}, s.period, "per_share"))
        mcap = None
        if s.shares_outstanding is not None:
            mcap = price * s.shares_outstanding  # ₹ crore
        ev = None
        if mcap is not None and s.total_debt is not None and s.cash is not None:
            ev = mcap + s.total_debt - s.cash
        ev_ebitda = safe_div(ev, s.ebitda)
        out.append(_m("ev_ebitda", "EV / EBITDA", ev_ebitda, "x",
                      f"(Mkt cap {fmt(mcap)} + Debt {fmt(s.total_debt)} − Cash {fmt(s.cash)}) / EBITDA {fmt(s.ebitda)}",
                      {"market_cap": mcap, "total_debt": s.total_debt, "cash": s.cash, "ebitda": s.ebitda},
                      s.period, "per_share"))
        dps = safe_div(s.dividends_paid, s.shares_outstanding)
        dy = safe_div(dps, price)
        out.append(_m("dividend_yield", "Dividend Yield", None if dy is None else dy * 100, "%",
                      f"DPS ₹{fmt(dps)} / Price ₹{fmt(price)}",
                      {"dividends_paid": s.dividends_paid, "shares_outstanding": s.shares_outstanding,
                       "price": price}, s.period, "per_share"))
    return out


def all_ratios(s: StatementData, price: Optional[float] = None) -> list[Metric]:
    return (liquidity_ratios(s) + leverage_ratios(s) + profitability_ratios(s)
            + [roic(s)] + efficiency_ratios(s) + per_share_ratios(s, price))
