"""Autopilot — the system trades its own book (paper), on policy, with reasons.

Runs after every data refresh (cron + pipeline) when enabled:
  EXITS first, positions checked against three triggers —
    stop breach   price ≤ avg cost × (1 − 2.5 × daily vol)
    time exit     oldest lot older than the claim horizon (~126 trading days)
    verdict flip  the synthesis now says avoid/short for a held name
  ENTRIES second — top qualified candidates (full gate stack) not already
    held, capped by policy: max new/run, max open positions, cash reserve.
    Each entry generates a FULL dossier first (claim + cluster attribution)
    and fills at the live price with the dossier hash attached — so every
    autopilot trade feeds the learning loop.

Every action AND every non-action is reported with its reason and the run is
recorded in the hash-chained ledger. Real-world grounding without real money.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ledger
from ..models import AppSnapshot, Company, PaperTrade

CONFIG_KEY = "autopilot_config"
LAST_RUN_KEY = "autopilot_last"
DEFAULTS = {"enabled": False, "max_new_per_run": 2, "max_open_positions": 8,
            "cash_reserve_pct": 10.0, "time_exit_days": 185}


def get_config(session: Session) -> dict:
    row = session.get(AppSnapshot, CONFIG_KEY)
    cfg = dict(DEFAULTS)
    if row is not None:
        cfg.update(json.loads(row.payload))
    return cfg


def set_config(session: Session, updates: dict) -> dict:
    cfg = get_config(session)
    cfg.update({k: v for k, v in updates.items() if k in DEFAULTS})
    row = session.get(AppSnapshot, CONFIG_KEY)
    if row is None:
        row = AppSnapshot(key=CONFIG_KEY, as_of=str(date.today()), payload="")
        session.add(row)
    row.payload = json.dumps(cfg)
    row.as_of = str(date.today())
    session.commit()
    return cfg


def last_run(session: Session) -> dict | None:
    row = session.get(AppSnapshot, LAST_RUN_KEY)
    return json.loads(row.payload) if row else None


def _held_verdicts(session: Session, tickers: list[str]) -> dict[str, str]:
    """Re-synthesize just the held names to detect verdict flips."""
    from ..engine.synthesis import synthesize
    from ..research.learning import cluster_weights
    from .candidates import evidence_from_snapshot
    from .live import current_regime, universe_signals
    from .snapshot import get_universe
    universe = {c["ticker"]: c for c in get_universe(session)["companies"]}
    sigs = universe_signals(session)
    rk = current_regime(session)["conditioning_key"]
    weights, _ = cluster_weights()
    out = {}
    for t in tickers:
        item = universe.get(t)
        if item:
            out[t] = synthesize(evidence_from_snapshot(item, sigs, rk, {}),
                                weights=weights).verdict
    return out


def run_autopilot(session: Session, force: bool = False) -> dict:
    from .candidates import qualified_candidates
    from .live import build_dossier
    from .paper import account, place_trade
    from .snapshot import get_universe

    cfg = get_config(session)
    report = {"ran_at": datetime.now(timezone.utc).isoformat(),
              "enabled": cfg["enabled"], "entries": [], "exits": [], "skipped": []}
    if not cfg["enabled"] and not force:
        report["skipped"].append("autopilot disabled — enable it on the Trading Desk")
        _store(session, report)
        return report

    universe = {c["ticker"]: c for c in get_universe(session)["companies"]}
    acct = account(session, include_curve=False)

    # ---------------------------------------------------------------- exits
    held = {p["ticker"]: p for p in acct["positions"]}
    verdicts = _held_verdicts(session, list(held)) if held else {}
    oldest_fill: dict[str, date] = {}
    for t in session.scalars(select(PaperTrade)).all():
        c = session.get(Company, t.company_id)
        if c and c.ticker in held and t.side == "buy":
            oldest_fill[c.ticker] = min(oldest_fill.get(c.ticker, t.trade_date),
                                        t.trade_date)
    for ticker, pos in held.items():
        item = universe.get(ticker, {})
        dv = ((item.get("signals") or {}).get("vol") or 25.0) / (252 ** 0.5)
        stop_pct = 2.5 * dv
        reason = None
        if pos["price"] is not None and pos["price"] <= pos["avg_cost"] * (1 - stop_pct / 100):
            reason = (f"stop breach: price ₹{pos['price']:,.1f} is more than "
                      f"{stop_pct:.1f}% below avg cost ₹{pos['avg_cost']:,.1f}")
        elif (date.today() - oldest_fill.get(ticker, date.today())).days > cfg["time_exit_days"]:
            reason = (f"time exit: held past the {cfg['time_exit_days']}-day claim "
                      "horizon — realize and let scoring judge the call")
        elif verdicts.get(ticker) == "avoid_short_candidate":
            reason = "verdict flip: synthesis now reads Avoid/Short for this name"
        if reason:
            fill = place_trade(session, pos["company_id"], "sell", pos["quantity"])
            report["exits"].append({"ticker": ticker, "quantity": pos["quantity"],
                                    "fill_price": fill["fill_price"], "reason": reason})

    # -------------------------------------------------------------- entries
    acct = account(session, include_curve=False)  # refresh after exits
    open_names = {p["ticker"] for p in acct["positions"]}
    reserve = acct["equity"] * cfg["cash_reserve_pct"] / 100
    cash = acct["cash"]
    cands = qualified_candidates(session, top_n=10,
                                 book_value=acct["equity"], cash=cash)
    new_count = 0
    for c in cands["candidates"]:
        if new_count >= cfg["max_new_per_run"]:
            report["skipped"].append(f"{c['ticker']}: run cap reached "
                                     f"({cfg['max_new_per_run']} new positions/run)")
            continue
        if len(open_names) >= cfg["max_open_positions"]:
            report["skipped"].append(f"{c['ticker']}: portfolio at max "
                                     f"{cfg['max_open_positions']} open positions")
            continue
        if c["ticker"] in open_names:
            report["skipped"].append(f"{c['ticker']}: already held — no pyramiding")
            continue
        if not c["tradable"]:
            report["skipped"].append(f"{c['ticker']}: failed gates — "
                                     + "; ".join(c["gates"]))
            continue
        if cash - c["sizing"]["value"] < reserve:
            report["skipped"].append(f"{c['ticker']}: would breach the "
                                     f"{cfg['cash_reserve_pct']:.0f}% cash reserve")
            continue
        company = session.get(Company, c["id"])
        dossier = build_dossier(session, company, book_value=acct["equity"])
        shares = (dossier["sizing"] or {}).get("recommended_shares") or c["sizing"]["shares"]
        if shares <= 0:
            report["skipped"].append(f"{c['ticker']}: dossier sizing came back zero")
            continue
        fill = place_trade(session, c["id"], "buy", shares,
                           dossier_hash=dossier["ledger"]["hash"])
        cash -= shares * fill["fill_price"]
        open_names.add(c["ticker"])
        new_count += 1
        report["entries"].append({"ticker": c["ticker"], "quantity": shares,
                                  "fill_price": fill["fill_price"],
                                  "dossier": dossier["ledger"]["hash"][:16],
                                  "reason": "qualified candidate: "
                                            + "; ".join(c["drivers"][:2])})
    if not report["entries"] and not report["exits"]:
        report["skipped"].append("no action required — standing aside is the "
                                 "defensible position this run")

    ledger._write({"kind": "autopilot_run",
                   "created_at": report["ran_at"],
                   "entries": len(report["entries"]),
                   "exits": len(report["exits"]),
                   "skipped": len(report["skipped"])})
    _store(session, report)
    return report


def _store(session: Session, report: dict) -> None:
    row = session.get(AppSnapshot, LAST_RUN_KEY)
    if row is None:
        row = AppSnapshot(key=LAST_RUN_KEY, as_of=str(date.today()), payload="")
        session.add(row)
    row.payload = json.dumps(report)
    row.as_of = str(date.today())
    session.commit()
