"""Core ratio engine (§6.1).

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


# India's headline corporate rate under the concessional regime (22% + surcharge
# + cess). Used only as a documented fallback when a filing's implied effective
# rate is not economically meaningful.
STATUTORY_TAX_RATE = 0.2517
# Plausible band for a derived effective tax rate. Outside this, the ratio is
# being driven by exceptional items, loss carry-forwards or a refund year, and
# propagating it produces impossible NOPAT (see effective_tax_rate).
EFF_TAX_MIN, EFF_TAX_MAX = 0.0, 0.60

AVERAGE_BALANCE_NOTE = ("Return denominators use the AVERAGE of opening and "
                        "closing balances. Period-end denominators bias returns "
                        "downward for a growing balance sheet, which is the "
                        "common case.")
ENDING_BALANCE_NOTE = ("Prior-period balance sheet unavailable, so the "
                       "period-END denominator is used. For a company whose "
                       "balance sheet grew during the year this understates the "
                       "return; compare across years with that in mind.")


def effective_tax_rate(s: StatementData,
                       override: Optional[float] = None) -> tuple[Optional[float], Optional[str]]:
    """(rate, caveat) — a usable effective tax rate, or the statutory fallback.

    A raw tax_expense/PBT ratio is routinely outside [0, 1]: refund years make it
    negative, and near-breakeven PBT makes it explode. Feeding that straight into
    NOPAT = EBIT x (1 - t) produces arithmetic that cannot be true — a negative
    rate makes NOPAT EXCEED EBIT, and a rate above 1 makes NOPAT negative while
    EBIT is positive. Both were previously possible and both were verified.
    """
    if override is not None:
        return override, None
    if s.tax_expense is None or s.pbt in (None, 0):
        return STATUTORY_TAX_RATE, (
            f"Effective tax rate not derivable from this filing; using India's "
            f"statutory {STATUTORY_TAX_RATE:.2%} as a stated assumption.")
    raw = s.tax_expense / s.pbt
    if EFF_TAX_MIN <= raw <= EFF_TAX_MAX:
        return raw, None
    return STATUTORY_TAX_RATE, (
        f"Filing-implied effective tax rate {raw:.1%} is outside the plausible "
        f"{EFF_TAX_MIN:.0%}–{EFF_TAX_MAX:.0%} band (refund year, loss "
        f"carry-forward or near-zero PBT), so the statutory "
        f"{STATUTORY_TAX_RATE:.2%} is used instead. Raw ratio retained in inputs.")


def _avg(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    """Average balance when both periods are present, else the current one."""
    if curr is None:
        return None
    if prev is None:
        return curr
    return (curr + prev) / 2.0


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

def profitability_ratios(s: StatementData,
                         prev: Optional[StatementData] = None) -> list[Metric]:
    """Margins plus returns. Return denominators (equity, assets) average
    opening and closing balances when `prev` is supplied — see
    AVERAGE_BALANCE_NOTE for why period-end denominators bias returns down."""
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
    eq_prev = prev.total_equity if prev else None
    ta_prev = prev.total_assets if prev else None
    eq_avg, ta_avg = _avg(s.total_equity, eq_prev), _avg(s.total_assets, ta_prev)
    eq_averaged, ta_averaged = eq_prev is not None, ta_prev is not None

    roe = safe_div(s.net_income, eq_avg)
    out.append(_m("roe", "Return on Equity", None if roe is None else roe * 100, "%",
                  f"Net income {fmt(s.net_income)} / {'average' if eq_averaged else 'closing'} "
                  f"equity {fmt(eq_avg)}",
                  {"net_income": s.net_income, "total_equity": eq_avg,
                   "equity_opening": eq_prev, "equity_closing": s.total_equity,
                   "denominator": "average" if eq_averaged else "closing"},
                  s.period, "profitability",
                  caveat=AVERAGE_BALANCE_NOTE if eq_averaged else ENDING_BALANCE_NOTE))
    roa = safe_div(s.net_income, ta_avg)
    out.append(_m("roa", "Return on Assets", None if roa is None else roa * 100, "%",
                  f"Net income {fmt(s.net_income)} / {'average' if ta_averaged else 'closing'} "
                  f"assets {fmt(ta_avg)}",
                  {"net_income": s.net_income, "total_assets": ta_avg,
                   "assets_opening": ta_prev, "assets_closing": s.total_assets,
                   "denominator": "average" if ta_averaged else "closing"},
                  s.period, "profitability",
                  caveat=AVERAGE_BALANCE_NOTE if ta_averaged else ENDING_BALANCE_NOTE))
    out.extend(dupont_decomposition(s, prev))
    return out


def dupont_decomposition(s: StatementData,
                         prev: Optional[StatementData] = None) -> list[Metric]:
    """3-way DuPont: ROE = Net margin × Asset turnover × Equity multiplier.

    Uses the same average denominators as `profitability_ratios`, which keeps the
    identity exact: NI/avgE = (NI/Rev)·(Rev/avgA)·(avgA/avgE). Mixing averaged
    ROE with period-end DuPont terms would silently break the decomposition — the
    product would not reconcile to the ROE shown one card above it.
    """
    ta_avg = _avg(s.total_assets, prev.total_assets if prev else None)
    eq_avg = _avg(s.total_equity, prev.total_equity if prev else None)
    averaged = prev is not None
    label_suffix = " (avg)" if averaged else ""
    nm = safe_div(s.net_income, s.revenue)
    at = safe_div(s.revenue, ta_avg)
    em = safe_div(ta_avg, eq_avg)
    note = (AVERAGE_BALANCE_NOTE if averaged else ENDING_BALANCE_NOTE) + \
        " Net margin x Asset turnover x Equity multiplier reconciles exactly to the ROE above."
    out = [
        _m("dupont_net_margin", "DuPont · Net Margin", None if nm is None else nm * 100, "%",
           f"Net income {fmt(s.net_income)} / Revenue {fmt(s.revenue)}",
           {"net_income": s.net_income, "revenue": s.revenue}, s.period, "profitability"),
        _m("dupont_asset_turnover", f"DuPont · Asset Turnover{label_suffix}", at, "x",
           f"Revenue {fmt(s.revenue)} / {'average' if averaged else 'closing'} assets {fmt(ta_avg)}",
           {"revenue": s.revenue, "total_assets": ta_avg,
            "denominator": "average" if averaged else "closing"},
           s.period, "profitability", caveat=note),
        _m("dupont_equity_multiplier", f"DuPont · Equity Multiplier{label_suffix}", em, "x",
           f"{'Average' if averaged else 'Closing'} assets {fmt(ta_avg)} / "
           f"{'average' if averaged else 'closing'} equity {fmt(eq_avg)}",
           {"total_assets": ta_avg, "total_equity": eq_avg,
            "denominator": "average" if averaged else "closing"},
           s.period, "profitability", caveat=note),
    ]
    return out


def roic(s: StatementData, tax_rate: Optional[float] = None,
         prev: Optional[StatementData] = None) -> Metric:
    """ROIC = NOPAT / average invested capital.

    NOPAT = EBIT × (1 − effective tax rate); invested capital = total debt +
    total equity − cash (§6.1). The tax rate is sanity-bounded (see
    `effective_tax_rate`) and the denominator averages opening and closing
    invested capital when the prior period is available, which is standard
    practice: NOPAT is a flow earned across the year, so charging it against the
    year-end capital base understates the return of any growing company.
    """
    eff_tax, tax_caveat = effective_tax_rate(s, tax_rate)
    nopat = None if (s.ebit is None or eff_tax is None) else s.ebit * (1 - eff_tax)

    def ic_of(x: Optional[StatementData]) -> Optional[float]:
        if x is None or x.total_debt is None or x.total_equity is None or x.cash is None:
            return None
        return x.total_debt + x.total_equity - x.cash

    ic_curr, ic_prev = ic_of(s), ic_of(prev)
    ic = _avg(ic_curr, ic_prev)
    averaged = ic_curr is not None and ic_prev is not None
    val = safe_div(nopat, ic)
    caveats = [c for c in (tax_caveat,
                           AVERAGE_BALANCE_NOTE if averaged else ENDING_BALANCE_NOTE) if c]
    raw_eff = (s.tax_expense / s.pbt) if (s.tax_expense is not None
                                          and s.pbt not in (None, 0)) else None
    return _m("roic", "Return on Invested Capital", None if val is None else val * 100, "%",
              f"NOPAT (EBIT {fmt(s.ebit)} × (1 − tax {fmt(None if eff_tax is None else eff_tax * 100)}%)) "
              f"/ {'average' if averaged else 'closing'} invested capital {fmt(ic)} "
              f"(debt {fmt(s.total_debt)} + equity {fmt(s.total_equity)} − cash {fmt(s.cash)})",
              {"ebit": s.ebit, "effective_tax_rate": eff_tax,
               "effective_tax_rate_raw": raw_eff,
               "nopat": nopat, "invested_capital": ic,
               "invested_capital_opening": ic_prev, "invested_capital_closing": ic_curr,
               "denominator": "average" if averaged else "closing",
               "total_debt": s.total_debt,
               "total_equity": s.total_equity, "cash": s.cash},
              s.period, "profitability", caveat=" ".join(caveats) or None)


# ---------------------------------------------------------------- efficiency

def efficiency_ratios(s: StatementData,
                      prev: Optional[StatementData] = None) -> list[Metric]:
    """Turnover and working-capital days.

    Working-capital days compare a period-END stock against a FULL-YEAR flow, so
    an average stock is the methodologically correct numerator; a year-end
    receivables spike otherwise reads as a permanent collections problem.
    """
    out = []
    averaged = prev is not None
    denom_label = "average" if averaged else "closing"
    note = AVERAGE_BALANCE_NOTE if averaged else ENDING_BALANCE_NOTE
    ta_avg = _avg(s.total_assets, prev.total_assets if prev else None)
    inv_avg = _avg(s.inventory, prev.inventory if prev else None)
    rec_avg = _avg(s.receivables, prev.receivables if prev else None)
    pay_avg = _avg(s.payables, prev.payables if prev else None)

    at = safe_div(s.revenue, ta_avg)
    out.append(_m("asset_turnover", "Asset Turnover", at, "x",
                  f"Revenue {fmt(s.revenue)} / {denom_label} assets {fmt(ta_avg)}",
                  {"revenue": s.revenue, "total_assets": ta_avg,
                   "denominator": denom_label},
                  s.period, "efficiency", caveat=note))
    cogs = None
    if s.revenue is not None and s.gross_profit is not None:
        cogs = s.revenue - s.gross_profit
    inv_days = safe_div(inv_avg, cogs)
    inv_days = None if inv_days is None else inv_days * 365
    out.append(_m("inventory_days", "Inventory Days", inv_days, "days",
                  f"{denom_label.capitalize()} inventory {fmt(inv_avg)} / COGS {fmt(cogs)} × 365",
                  {"inventory": inv_avg, "cogs": cogs}, s.period, "efficiency",
                  caveat=note))
    rec_days = safe_div(rec_avg, s.revenue)
    rec_days = None if rec_days is None else rec_days * 365
    out.append(_m("receivable_days", "Receivable Days", rec_days, "days",
                  f"{denom_label.capitalize()} receivables {fmt(rec_avg)} / Revenue {fmt(s.revenue)} × 365",
                  {"receivables": rec_avg, "revenue": s.revenue}, s.period, "efficiency",
                  caveat=note))
    pay_days = safe_div(pay_avg, cogs)
    pay_days = None if pay_days is None else pay_days * 365
    out.append(_m("payable_days", "Payable Days", pay_days, "days",
                  f"{denom_label.capitalize()} payables {fmt(pay_avg)} / COGS {fmt(cogs)} × 365",
                  {"payables": pay_avg, "cogs": cogs}, s.period, "efficiency",
                  caveat=note))
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


def all_ratios(s: StatementData, price: Optional[float] = None,
               prev: Optional[StatementData] = None) -> list[Metric]:
    """Every ratio family for one period. Pass `prev` (the immediately preceding
    period) to get average-balance return and turnover denominators; without it
    the closing balance is used and every affected Metric says so in its caveat."""
    return (liquidity_ratios(s) + leverage_ratios(s) + profitability_ratios(s, prev)
            + [roic(s, prev=prev)] + efficiency_ratios(s, prev)
            + per_share_ratios(s, price))
