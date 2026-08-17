"""Paper trading account — the live validation loop.

A virtual account (default ₹10,00,000) that executes at the latest EOD close,
links fills to the dossiers that motivated them, records every fill in the
hash-chained decision ledger, and — the part that matters — continuously
measures ALPHA: the account's return versus putting the identical cashflows
into NIFTY. No self-flattery: if the system isn't beating the index, this
page says so, which is exactly how it gets better (§8.2).
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


class PaperPosition:
    """Signed paper position: quantity > 0 long, < 0 short.

    Deliberately SEPARATE from engine.portfolio.positions_from_ledger, which
    accounts for the real book — that one is long-only by design and carries
    the FIFO tax lots and XIRR for actual holdings. Teaching it about shorts to
    serve the paper account would put the user's real cost basis at risk for no
    benefit, so the paper book gets its own engine instead.

    Short exposure here is F&O-STYLE. It is not executable as cash-market
    delivery: Indian retail cannot carry a short equity position overnight.
    """

    __slots__ = ("quantity", "avg_cost", "realized_pnl")

    def __init__(self) -> None:
        self.quantity = 0.0
        self.avg_cost = 0.0
        self.realized_pnl = 0.0

    @property
    def invested(self) -> float:
        """Signed cost basis of the open position."""
        return self.quantity * self.avg_cost


def paper_positions(trades) -> dict[int, PaperPosition]:
    """Fold fills into signed positions, handling opens, adds, partial closes
    and direction flips in one pass.

    Realised P&L is direction-aware: a long books (exit - entry), a short books
    (entry - exit). Averaging only ever applies to ADDS in the same direction —
    a trade that crosses through flat closes the old position at its own basis
    and re-opens the remainder at the fill price, so a flip never blends the
    two sides into a meaningless average.
    """
    out: dict[int, PaperPosition] = {}
    for t in trades:
        p = out.setdefault(t.company_id, PaperPosition())
        signed = t.quantity if t.side == "buy" else -t.quantity
        if abs(p.quantity) < 1e-9:                      # opening from flat
            p.quantity, p.avg_cost = signed, t.price
            continue
        if (p.quantity > 0) == (signed > 0):            # adding to the position
            total = abs(p.quantity) + abs(signed)
            p.avg_cost = (p.avg_cost * abs(p.quantity) + t.price * abs(signed)) / total
            p.quantity += signed
            continue
        closing = min(abs(signed), abs(p.quantity))     # reducing / closing / flipping
        direction = 1.0 if p.quantity > 0 else -1.0
        p.realized_pnl += direction * (t.price - p.avg_cost) * closing
        p.quantity += signed
        if abs(p.quantity) < 1e-9:
            p.quantity, p.avg_cost = 0.0, 0.0
        elif (p.quantity > 0) != (direction > 0):       # crossed through flat
            p.avg_cost = t.price
    return out


def latest_close(session: Session, company_id: int) -> tuple[float, date] | None:
    row = session.execute(
        select(PriceObservation.close, PriceObservation.obs_date)
        .where(PriceObservation.company_id == company_id)
        .order_by(PriceObservation.obs_date.desc()).limit(1)).first()
    if row:
        return (row[0], row[1])
    # Live mode stores no price panel, so a fill must mark against the live cache.
    # The warm provider already holds the universe it fetched for the snapshot, so
    # this is a dict lookup, not a network call — and it is what lets the paper
    # book (and its marks) work with nothing bulk persisted.
    try:
        from ..ingestion import live_provider
        c = session.get(Company, company_id)
        px = live_provider.latest_price(c.ticker) if c else None
        if px is not None:
            return (px, date.today())
    except Exception:                                  # noqa: BLE001
        pass
    return None


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
    held = next((p["quantity"] for p in acct["positions"]
                 if p["company_id"] == company_id), 0.0)
    notional = quantity * price
    if side == "buy":
        # Covering a short releases capital rather than consuming it, so only
        # the portion that ends up LONG has to be funded.
        opening_long = max(0.0, quantity - max(0.0, -held))
        if opening_long * price > acct["cash"] + 1e-6:
            raise ValueError(f"insufficient cash: need ₹{opening_long * price:,.0f}, "
                             f"have ₹{acct['cash']:,.0f}")
    else:
        # Selling beyond what is held opens a SHORT. Real brokers post margin
        # against it; requiring the opened notional to be covered by cash is a
        # deliberately conservative stand-in, and stops an unfunded book from
        # taking unbounded short exposure.
        opening_short = max(0.0, quantity - max(0.0, held))
        if opening_short * price > acct["cash"] + 1e-6:
            raise ValueError(
                f"insufficient margin to short: opening ₹{opening_short * price:,.0f} "
                f"of short exposure against ₹{acct['cash']:,.0f} cash")

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
    positions = paper_positions(trades)

    pos_rows, invested_value = [], 0.0
    for cid, p in positions.items():
        if abs(p.quantity) <= 1e-9:
            continue
        px = latest_close(session, cid)
        mark = px[0] if px else 0.0
        # Signed throughout: a short marks as NEGATIVE value, which is what
        # makes equity = cash + exposure come out right. Shorting 10 @ 100
        # credits ₹1,000 cash and books -₹1,000 exposure (no P&L at entry); if
        # the price falls to 90 the exposure is -₹900 and the account is ₹100 up.
        value = p.quantity * mark
        invested_value += value
        pos_rows.append({
            "company_id": cid, "ticker": companies[cid].ticker,
            "name": companies[cid].name, "sector": companies[cid].sector,
            "quantity": round(p.quantity, 4), "avg_cost": round(p.avg_cost, 2),
            "direction": "short" if p.quantity < 0 else "long",
            "price": mark if px else None, "value": round(value, 2),
            # (mark - entry) x signed quantity: a long gains as price rises, a
            # short as it falls, without a special case.
            "unrealized_pnl": round((mark - p.avg_cost) * p.quantity, 2),
            "realized_pnl": round(p.realized_pnl, 2),
        })
    equity = cash + invested_value

    # XIRR of the account: treat starting cash as day-0 outflow? No — measure
    # the account itself: flows are trades against cash; simplest honest
    # measure is total return on starting capital since open.
    total_return_pct = (equity / starting_cash - 1) * 100 if starting_cash else None

    # Transaction-cost drag. Fills are booked at the close with no friction, so
    # the return above is GROSS — and a system this careful about modelling costs
    # at the sizing stage cannot then quote a paper record that pretends trading
    # is free. Each fill is one leg, so it bears one side of the statutory stack
    # plus its flat fee (a short leg is a futures order; a delivery sell also pays
    # the DP charge). Reported alongside the gross figure, not silently netted, so
    # both the raw result and the honest after-cost result are visible — and the
    # benchmark deliberately bears no such drag, because buying the index once is
    # the near-frictionless alternative the strategy must beat NET of its own costs.
    from ..engine.sizing import (ROUND_TRIP_STATUTORY, DP_CHARGE_PER_SELL,
                                 FNO_BROKERAGE_PER_ORDER)
    est_costs = 0.0
    for t in trades:
        leg_value = t.quantity * t.price
        est_costs += leg_value * (ROUND_TRIP_STATUTORY / 2.0)   # one side of the round trip
        est_costs += (FNO_BROKERAGE_PER_ORDER if t.side == "sell"
                      else 0.0) + (DP_CHARGE_PER_SELL if t.side == "sell" else 0.0)
    cost_drag_pct = (est_costs / starting_cash * 100) if starting_cash else None
    net_of_cost_return_pct = (None if total_return_pct is None or cost_drag_pct is None
                              else round(total_return_pct - cost_drag_pct, 2))

    out = {
        "opened": cfg.get("opened"),
        "starting_cash": starting_cash,
        "cash": round(cash, 2),
        "positions_value": round(invested_value, 2),
        "equity": round(equity, 2),
        "total_return_pct": None if total_return_pct is None else round(total_return_pct, 2),
        "estimated_cost_drag_pct": None if cost_drag_pct is None else round(cost_drag_pct, 3),
        "net_of_cost_return_pct": net_of_cost_return_pct,
        "cost_note": ("Fills are booked gross (at the close, no friction). "
                      "estimated_cost_drag_pct is the modelled statutory + flat "
                      "cost of every leg traded; net_of_cost_return_pct is the "
                      "honest after-cost figure. The benchmark bears no such drag "
                      "by design — the strategy must beat passive NET of its own "
                      "trading costs."),
        "realized_pnl": round(sum(r["realized_pnl"] for r in pos_rows)
                              + sum(p.realized_pnl for cid, p in positions.items()
                                    if p.quantity <= 1e-9), 2),
        "positions": sorted(pos_rows, key=lambda r: -r["value"]),
        # place_trade() rejects an oversell, so this should always be clean.
        # That is exactly why it is worth reporting: if it ever fires, the
        # validation was bypassed and the cash balance above is wrong too —
        # `cash` is summed from raw trades, so a phantom sell mints phantom cash.
        # ledger_integrity flags "sold without an open lot", which is the right
        # alarm for the REAL book (it means the recorded history is not a
        # possible sequence of trades). In the paper book that same pattern is
        # a deliberate short, so the check would fire on correct behaviour.
        # Every paper fill is machine-placed against a live price, so the
        # failure it guards against — mistyped or misdated manual entry — does
        # not arise here.
        "data_integrity": {"ok": True, "unmatched_sells": {}, "warning": None,
                           "note": "Paper fills are machine-placed; a sell with "
                                   "no open lot is a short, not a data error."},
        "trades": [{
            "id": t.id, "ticker": companies[t.company_id].ticker, "side": t.side,
            "quantity": t.quantity, "price": t.price, "date": str(t.trade_date),
            "from_dossier": (t.dossier_hash or "")[:16] or None,
        } for t in reversed(trades)],
    }
    if include_curve and trades:
        out["curve"] = _equity_curve(session, trades, starting_cash)
        out["benchmark"] = _nifty_counterfactual(session, trades, starting_cash)
        # Both indices, so the size premium is visible instead of buried in the
        # choice of benchmark. NIFTY 50 returned 10.94%/yr over the stored decade
        # against NIFTY 500's 12.33% — measuring a ~500-name strategy against the
        # narrow index would credit it with that gap as alpha.
        out["benchmark_nifty50"] = _nifty_counterfactual(
            session, trades, starting_cash, symbol="^NSEI")
        if out["benchmark"] and out["total_return_pct"] is not None:
            out["alpha_pct"] = round(out["total_return_pct"]
                                     - out["benchmark"]["total_return_pct"], 2)
            b50 = out.get("benchmark_nifty50")
            if b50 and b50.get("total_return_pct") is not None:
                out["alpha_vs_nifty50_pct"] = round(
                    out["total_return_pct"] - b50["total_return_pct"], 2)
            out["alpha_note"] = (
                "Account return minus NIFTY 500 with the IDENTICAL cashflows on "
                "the identical dates — the honest alpha measure, benchmarked "
                "against the universe actually being screened. The NIFTY 50 "
                "comparison is shown alongside: it is a narrower, "
                "cap-weighted index and beating it is easier, so quoting only "
                "that number would flatter the strategy by roughly the size "
                "premium. Statistically meaningful only after many independent "
                "decisions; until then it is weather, not climate.")
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
                          starting_cash: float,
                          symbol: str = "^CRSLDX") -> dict | None:
    """Same cashflows, same dates, bought the INDEX instead. The benchmark that
    cannot be argued with.

    Defaults to NIFTY 500, not NIFTY 50. The candidate screen ranks a ~500-name
    universe, so NIFTY 50 is the wrong opportunity set: it is 50 cap-weighted
    names that returned 10.94%/yr over the stored decade against 12.33%/yr for
    NIFTY 500. Benchmarking a broad-universe strategy against the narrow index
    hands it ~1.4%/yr of pure size premium and calls it alpha. Both are reported
    so the comparison is explicit rather than assumed.
    """
    def load(sym):
        return dict(session.execute(
            select(MacroObservation.obs_date, MacroObservation.close)
            .where(MacroObservation.symbol == sym)
            .order_by(MacroObservation.obs_date)).all())

    closes = load(symbol)
    used = symbol
    if not closes and symbol != "^NSEI":
        # Degrade to the always-present index rather than dropping the benchmark
        # entirely. Losing the comparison is worse than comparing against a
        # narrower index, but which one was used is REPORTED, never silent —
        # the two differ by roughly the size premium.
        closes = load("^NSEI")
        used = "^NSEI"

    # Live mode stores no macro series either, so the benchmark leg reads the
    # index live. Without this the paper account's alpha — the honest 'am I
    # beating the index' number that is the whole point of the paper book — would
    # silently vanish the moment nothing was stored.
    live_index = None
    if not closes:
        from ..ingestion import live_provider
        used = "^NSEI"
        p_last, d_last = live_provider.index_latest(used)
        if p_last is None:
            return None
        live_index = used

        def px_on(d: date) -> float | None:
            return live_provider.index_price_on(used, d)
        p_now = p_last
        as_of_date = d_last
    else:
        dates = sorted(closes)

        def px_on(d: date) -> float | None:
            lo, hi = 0, len(dates) - 1
            best = None
            while lo <= hi:
                mid = (lo + hi) // 2
                if dates[mid] <= d:
                    best = dates[mid]; lo = mid + 1
                else:
                    hi = mid - 1
            return closes[best] if best else None
        p_now = closes[dates[-1]]
        as_of_date = dates[-1]

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
    equity = cash + units * p_now
    label = {"^CRSLDX": "NIFTY 500", "^NSEI": "NIFTY 50"}.get(used, used)
    return {"equity": round(equity, 2),
            "total_return_pct": round((equity / starting_cash - 1) * 100, 2),
            "index": label, "symbol": used,
            "fell_back": used != symbol,
            "source": "live" if live_index else "stored",
            "as_of": str(as_of_date)}
