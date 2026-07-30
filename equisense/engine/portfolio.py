"""Portfolio intelligence engine (PROJECT_DRAFT §11).

Portfolio state is always derived from the transaction ledger — never stored
as a mutable "current holding" (§11.1). XIRR is the primary money-weighted
return measure (§11.2). Concentration is computed on four axes, including the
distinctive quality-score-band axis (§11.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .types import Metric, fmt


@dataclass
class Transaction:
    company_id: int
    side: str          # "buy" | "sell"
    quantity: float
    price: float       # ₹ per share
    trade_date: date
    fees: float = 0.0


@dataclass
class Position:
    company_id: int
    quantity: float
    avg_cost: float            # ₹ per share, average cost of open quantity
    invested: float            # ₹, cost basis of open quantity
    realized_pnl: float        # ₹, from sells (FIFO)
    lots: list[dict]           # open lots: {date, quantity, price} for tax aging


def positions_from_ledger(txns: list[Transaction]) -> dict[int, Position]:
    """Derive open positions from the ledger using FIFO lot matching."""
    by_company: dict[int, list[Transaction]] = {}
    for t in sorted(txns, key=lambda t: t.trade_date):
        by_company.setdefault(t.company_id, []).append(t)

    out: dict[int, Position] = {}
    for cid, ts in by_company.items():
        lots: list[dict] = []
        realized = 0.0
        for t in ts:
            if t.side == "buy":
                lots.append({"date": t.trade_date, "quantity": t.quantity,
                             "price": t.price + (t.fees / t.quantity if t.quantity else 0)})
            else:
                remaining = t.quantity
                while remaining > 1e-9 and lots:
                    lot = lots[0]
                    take = min(lot["quantity"], remaining)
                    realized += take * (t.price - lot["price"]) - (t.fees * take / t.quantity)
                    lot["quantity"] -= take
                    remaining -= take
                    if lot["quantity"] <= 1e-9:
                        lots.pop(0)
        qty = sum(l["quantity"] for l in lots)
        invested = sum(l["quantity"] * l["price"] for l in lots)
        if qty > 1e-9 or abs(realized) > 1e-9:
            out[cid] = Position(company_id=cid, quantity=qty,
                                avg_cost=invested / qty if qty > 1e-9 else 0.0,
                                invested=invested, realized_pnl=realized, lots=lots)
    return out


# --------------------------------------------------------------------- XIRR

def xnpv(rate: float, cashflows: list[tuple[date, float]]) -> float:
    t0 = min(d for d, _ in cashflows)
    return sum(cf / (1 + rate) ** ((d - t0).days / 365.0) for d, cf in cashflows)


def xirr(cashflows: list[tuple[date, float]]) -> Optional[float]:
    """Money-weighted annualized return via bisection on XNPV.

    Requires at least one negative and one positive cashflow.
    """
    if len(cashflows) < 2:
        return None
    amounts = [cf for _, cf in cashflows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return None
    lo, hi = -0.9999, 100.0
    f_lo = xnpv(lo, cashflows)
    f_hi = xnpv(hi, cashflows)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = xnpv(mid, cashflows)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def dividend_cashflows(txns: list[Transaction],
                       dividends_by_company: dict[int, list[tuple]],
                       as_of: date) -> list[tuple[date, float]]:
    """Cash dividends received, based on shares HELD on each ex-date.

    Reconstructed from the transaction ledger rather than stored as a balance,
    which is the only way to get it right: entitlement depends on the position
    size on the ex-date, so a dividend must be multiplied by the holding as it
    stood THEN, not by today's position. Buying after the ex-date earns nothing;
    selling before it earns nothing.
    """
    flows: list[tuple[date, float]] = []
    by_company: dict[int, list[Transaction]] = {}
    for t in txns:
        by_company.setdefault(t.company_id, []).append(t)
    for cid, events in dividends_by_company.items():
        ts = sorted(by_company.get(cid, []), key=lambda t: t.trade_date)
        if not ts:
            continue
        for ex_date, dps in sorted(events):
            if ex_date > as_of or not dps:
                continue
            held = 0.0
            for t in ts:
                if t.trade_date > ex_date:
                    break
                held += t.quantity if t.side == "buy" else -t.quantity
            if held > 1e-9:
                flows.append((ex_date, held * float(dps)))
    return flows


def portfolio_xirr(txns: list[Transaction], current_values: dict[int, float],
                   as_of: date,
                   dividends_by_company: Optional[dict[int, list[tuple]]] = None
                   ) -> Optional[Metric]:
    """XIRR of the whole book: buys are outflows, sells and DIVIDENDS are
    inflows, plus the current market value of open positions as a terminal
    inflow.

    Dividends were previously omitted entirely. A dividend is cash that actually
    arrived, so leaving it out understates the money-weighted return by roughly
    the portfolio's yield — every year, compounding. For an income-tilted Indian
    book that is a material, one-directional error in the platform's headline
    performance number.
    """
    flows: list[tuple[date, float]] = []
    for t in txns:
        if t.side == "buy":
            flows.append((t.trade_date, -(t.quantity * t.price + t.fees)))
        else:
            flows.append((t.trade_date, t.quantity * t.price - t.fees))
    div_flows = dividend_cashflows(txns, dividends_by_company or {}, as_of)
    flows.extend(div_flows)
    dividend_total = sum(v for _d, v in div_flows)
    terminal = sum(current_values.values())
    if terminal > 0:
        flows.append((as_of, terminal))
    r = xirr(flows)
    ex_div = xirr([f for f in flows if f not in div_flows]) if div_flows else r
    return Metric(
        key="portfolio_xirr", label="Portfolio XIRR",
        value=None if r is None else r * 100, unit="%",
        formula=f"Money-weighted return over {len(flows)} cashflows "
                f"(buys as outflows; sells and ₹{fmt(dividend_total)} of dividends "
                f"as inflows; current value ₹{fmt(terminal)} as terminal inflow on "
                f"{as_of.isoformat()})",
        inputs={"cashflow_count": len(flows), "terminal_value": terminal,
                "dividend_cashflows": len(div_flows),
                "dividends_received": round(dividend_total, 2),
                "xirr_excluding_dividends_pct": (None if ex_div is None
                                                 else round(ex_div * 100, 3))},
        period=f"as of {as_of.isoformat()}", family="performance",
        caveat=("Dividends are counted as cash inflows on their ex-date, sized by "
                "the position held at that time. The ex-dividend XIRR is shown in "
                "inputs so the income contribution is visible rather than blended "
                "invisibly into one number."
                if div_flows else
                "No dividend events found for the held names over this period — "
                "the return is price-only."))


def position_xirr(txns: list[Transaction], company_id: int,
                  current_value: float, as_of: date,
                  dividends: Optional[list[tuple]] = None) -> Optional[float]:
    """Money-weighted return for one position, dividends included."""
    own = [t for t in txns if t.company_id == company_id]
    flows = []
    for t in own:
        if t.side == "buy":
            flows.append((t.trade_date, -(t.quantity * t.price + t.fees)))
        else:
            flows.append((t.trade_date, t.quantity * t.price - t.fees))
    if dividends:
        flows.extend(dividend_cashflows(own, {company_id: dividends}, as_of))
    if current_value > 0:
        flows.append((as_of, current_value))
    return xirr(flows)


# ------------------------------------------------------------ concentration

def concentration(positions: dict[int, Position],
                  current_prices: dict[int, float],
                  sectors: dict[int, str],
                  cap_bands: dict[int, str],
                  quality_tiers: dict[int, Optional[str]]) -> dict:
    """Four-axis concentration diagnostics (§11.3): position, sector,
    market-cap band, and quality-score band."""
    values = {cid: p.quantity * current_prices.get(cid, 0.0)
              for cid, p in positions.items() if p.quantity > 1e-9}
    total = sum(values.values())
    if total <= 0:
        return {"total_value": 0, "by_position": {}, "by_sector": {},
                "by_cap_band": {}, "by_quality_tier": {}}

    def agg(mapping: dict[int, Optional[str]]) -> dict[str, float]:
        acc: dict[str, float] = {}
        for cid, v in values.items():
            key = mapping.get(cid) or "unclassified"
            acc[key] = acc.get(key, 0.0) + v
        return {k: round(v / total * 100, 2) for k, v in sorted(acc.items(), key=lambda kv: -kv[1])}

    by_position = {cid: round(v / total * 100, 2)
                   for cid, v in sorted(values.items(), key=lambda kv: -kv[1])}
    return {
        "total_value": round(total, 2),
        "by_position": by_position,
        "by_sector": agg(sectors),
        "by_cap_band": agg(cap_bands),
        "by_quality_tier": agg(quality_tiers),
    }


# ----------------------------------------------------------- tax-lot aging

LTCG_THRESHOLD_DAYS = 365  # listed equity: long-term after 12 months


def lot_aging(position: Position, as_of: date) -> list[dict]:
    """Days until each open lot becomes long-term (§11.6, India-specific)."""
    out = []
    for lot in position.lots:
        held = (as_of - lot["date"]).days
        out.append({
            "acquired": lot["date"].isoformat(),
            "quantity": round(lot["quantity"], 4),
            "cost_price": round(lot["price"], 2),
            "days_held": held,
            "is_long_term": held >= LTCG_THRESHOLD_DAYS,
            "days_to_long_term": max(0, LTCG_THRESHOLD_DAYS - held),
        })
    return out
