"""Paper trading account — the live validation loop.

A virtual account (default ₹10,00,000) that executes at the latest EOD close,
links fills to the dossiers that motivated them, records every fill in the
hash-chained decision ledger, and — the part that matters — continuously
measures ALPHA: the account's return versus putting the identical cashflows
into NIFTY. No self-flattery: if the system isn't beating the index, this
page says so, which is exactly how it gets better (RESEARCH_BLUEPRINT §18).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ledger
from ..engine.portfolio import (Transaction, ledger_integrity,
                                positions_from_ledger, xirr)
from ..models import (AppSnapshot, Company, MacroObservation, PaperTrade,
                      PriceObservation)

CONFIG_KEY = "paper_config"
DEFAULT_CASH = 1_000_000.0


def _config(session: Session) -> dict:
    row = session.get(AppSnapshot, CONFIG_KEY)
    if row is None:
        row = AppSnapshot(key=CONFIG_KEY, as_of=str(date.today()),
                          payload=json.dumps({"starting_cash": DEFAULT_CASH,
                                              "opened": str(date.today())}))
        session.add(row)
        session.commit()
    return json.loads(row.payload)


def latest_close(session: Session, company_id: int) -> tuple[float, date] | None:
    row = session.execute(
        select(PriceObservation.close, PriceObservation.obs_date)
        .where(PriceObservation.company_id == company_id)
        .order_by(PriceObservation.obs_date.desc()).limit(1)).first()
    return (row[0], row[1]) if row else None


def place_trade(session: Session, company_id: int, side: str, quantity: float,
                dossier_hash: str | None = None) -> dict:
    """Execute at the latest EOD close. Validates cash/position sufficiency.
    Every fill is a Decision object in the hash-chained ledger."""
    company = session.get(Company, company_id)
    if company is None:
        raise ValueError("unknown company")
    if side not in ("buy", "sell") or quantity <= 0:
        raise ValueError("side must be buy/sell with positive quantity")
    px = latest_close(session, company_id)
    if px is None:
        raise ValueError("no price available for this company")
    price, px_date = px

    acct = account(session, include_curve=False)
    if side == "buy" and quantity * price > acct["cash"] + 1e-6:
        raise ValueError(f"insufficient cash: need ₹{quantity * price:,.0f}, "
                         f"have ₹{acct['cash']:,.0f}")
    if side == "sell":
        held = next((p["quantity"] for p in acct["positions"]
                     if p["company_id"] == company_id), 0.0)
        if quantity > held + 1e-6:
            raise ValueError(f"insufficient position: selling {quantity}, hold {held}")

    trade = PaperTrade(company_id=company_id, side=side, quantity=quantity,
                       price=price, trade_date=px_date, dossier_hash=dossier_hash)
    session.add(trade)
    session.commit()
    ledger_rec = ledger._write({
        "kind": "paper_trade",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "company": {"ticker": company.ticker},
        "side": side, "quantity": quantity, "price": price,
        "fill_date": str(px_date),
        "from_dossier": dossier_hash,
    })
    return {"trade_id": trade.id, "ticker": company.ticker, "side": side,
            "quantity": quantity, "fill_price": price, "fill_date": str(px_date),
            "ledger_hash": ledger_rec["hash"]}


def reset_account(session: Session, starting_cash: float = DEFAULT_CASH) -> dict:
    from sqlalchemy import delete
    session.execute(delete(PaperTrade))
    row = session.get(AppSnapshot, CONFIG_KEY)
    payload = json.dumps({"starting_cash": starting_cash, "opened": str(date.today())})
    if row is None:
        session.add(AppSnapshot(key=CONFIG_KEY, as_of=str(date.today()), payload=payload))
    else:
        row.payload = payload
        row.as_of = str(date.today())
    session.commit()
    ledger._write({"kind": "paper_reset",
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "starting_cash": starting_cash})
    return {"ok": True, "starting_cash": starting_cash}


def account(session: Session, include_curve: bool = True) -> dict:
    cfg = _config(session)
    starting_cash = cfg["starting_cash"]
    trades = session.scalars(select(PaperTrade).order_by(PaperTrade.trade_date,
                                                         PaperTrade.id)).all()
    companies = {c.id: c for c in session.scalars(select(Company)).all()}

    cash = starting_cash
    flows = []
    for t in trades:
        if t.side == "buy":
            cash -= t.quantity * t.price
        else:
            cash += t.quantity * t.price
    positions = positions_from_ledger([
        Transaction(t.company_id, t.side, t.quantity, t.price, t.trade_date)
        for t in trades])

    pos_rows, invested_value = [], 0.0
    for cid, p in positions.items():
        if p.quantity <= 1e-9:
            continue
        px = latest_close(session, cid)
        value = p.quantity * (px[0] if px else 0.0)
        invested_value += value
        pos_rows.append({
            "company_id": cid, "ticker": companies[cid].ticker,
            "name": companies[cid].name, "sector": companies[cid].sector,
            "quantity": round(p.quantity, 4), "avg_cost": round(p.avg_cost, 2),
            "price": px[0] if px else None, "value": round(value, 2),
            "unrealized_pnl": round(value - p.invested, 2),
            "realized_pnl": round(p.realized_pnl, 2),
        })
    equity = cash + invested_value

    # XIRR of the account: treat starting cash as day-0 outflow? No — measure
    # the account itself: flows are trades against cash; simplest honest
    # measure is total return on starting capital since open.
    total_return_pct = (equity / starting_cash - 1) * 100 if starting_cash else None

    out = {
        "opened": cfg.get("opened"),
        "starting_cash": starting_cash,
        "cash": round(cash, 2),
        "positions_value": round(invested_value, 2),
        "equity": round(equity, 2),
        "total_return_pct": None if total_return_pct is None else round(total_return_pct, 2),
        "realized_pnl": round(sum(r["realized_pnl"] for r in pos_rows)
                              + sum(p.realized_pnl for cid, p in positions.items()
                                    if p.quantity <= 1e-9), 2),
        "positions": sorted(pos_rows, key=lambda r: -r["value"]),
        # place_trade() rejects an oversell, so this should always be clean.
        # That is exactly why it is worth reporting: if it ever fires, the
        # validation was bypassed and the cash balance above is wrong too —
        # `cash` is summed from raw trades, so a phantom sell mints phantom cash.
        "data_integrity": ledger_integrity(positions),
        "trades": [{
            "id": t.id, "ticker": companies[t.company_id].ticker, "side": t.side,
            "quantity": t.quantity, "price": t.price, "date": str(t.trade_date),
            "from_dossier": (t.dossier_hash or "")[:16] or None,
        } for t in reversed(trades)],
    }
    if include_curve and trades:
        out["curve"] = _equity_curve(session, trades, starting_cash)
        out["benchmark"] = _nifty_counterfactual(session, trades, starting_cash)
        if out["benchmark"] and out["total_return_pct"] is not None:
            out["alpha_pct"] = round(out["total_return_pct"]
                                     - out["benchmark"]["total_return_pct"], 2)
            out["alpha_note"] = (
                "Account return minus NIFTY with the IDENTICAL cashflows on the "
                "identical dates — the honest alpha measure. Statistically "
                "meaningful only after many independent decisions; until then "
                "it is weather, not climate.")
    return out


def _equity_curve(session: Session, trades: list[PaperTrade],
                  starting_cash: float) -> list[dict]:
    """Daily account equity since the first trade (cash + marked positions)."""
    held_ids = sorted({t.company_id for t in trades})
    start = min(t.trade_date for t in trades)
    rows = session.execute(
        select(PriceObservation.company_id, PriceObservation.obs_date,
               PriceObservation.close)
        .where(PriceObservation.company_id.in_(held_ids),
               PriceObservation.obs_date >= start)
        .order_by(PriceObservation.obs_date)).all()
    by_date: dict = {}
    for cid, d, c in rows:
        by_date.setdefault(d, {})[cid] = c
    last_px: dict[int, float] = {}
    curve = []
    ti = 0
    trades_sorted = sorted(trades, key=lambda t: (t.trade_date, t.id))
    cash = starting_cash
    qty: dict[int, float] = {}
    for d in sorted(by_date):
        while ti < len(trades_sorted) and trades_sorted[ti].trade_date <= d:
            t = trades_sorted[ti]
            if t.side == "buy":
                cash -= t.quantity * t.price
                qty[t.company_id] = qty.get(t.company_id, 0) + t.quantity
            else:
                cash += t.quantity * t.price
                qty[t.company_id] = qty.get(t.company_id, 0) - t.quantity
            ti += 1
        last_px.update(by_date[d])
        equity = cash + sum(q * last_px.get(cid, 0.0) for cid, q in qty.items())
        curve.append({"date": str(d), "equity": round(equity, 0)})
    step = max(1, len(curve) // 120)
    return curve[::step][-120:]


def _nifty_counterfactual(session: Session, trades: list[PaperTrade],
                          starting_cash: float) -> dict | None:
    """Same cashflows, same dates, bought NIFTY instead. The benchmark that
    cannot be argued with."""
    closes = dict(session.execute(
        select(MacroObservation.obs_date, MacroObservation.close)
        .where(MacroObservation.symbol == "^NSEI")
        .order_by(MacroObservation.obs_date)).all())
    if not closes:
        return None
    dates = sorted(closes)

    def px_on(d: date) -> float | None:
        # last close on/before d
        lo, hi = 0, len(dates) - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if dates[mid] <= d:
                best = dates[mid]; lo = mid + 1
            else:
                hi = mid - 1
        return closes[best] if best else None

    cash = starting_cash
    units = 0.0
    for t in sorted(trades, key=lambda t: (t.trade_date, t.id)):
        p = px_on(t.trade_date)
        if p is None:
            return None
        amount = t.quantity * t.price
        if t.side == "buy":
            cash -= amount
            units += amount / p
        else:
            cash += amount
            units -= amount / p
    p_now = closes[dates[-1]]
    equity = cash + units * p_now
    return {"equity": round(equity, 2),
            "total_return_pct": round((equity / starting_cash - 1) * 100, 2),
            "as_of": str(dates[-1])}
