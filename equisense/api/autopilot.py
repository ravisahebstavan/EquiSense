"""Autopilot — the system trades its own book (paper), on policy, with reasons.

Runs after every data refresh (cron + pipeline) when enabled:
  EXITS first, positions checked against three triggers (each mirrored for
  shorts, whose stop sits ABOVE entry and whose adverse flip is the bullish
  one) —
    stop breach   long: price ≤ avg cost × (1 − 2.5 × daily vol)
                  short: price ≥ avg cost × (1 + 2.5 × daily vol)
    time exit     oldest lot older than the claim horizon (~126 trading days)
    verdict flip  synthesis now reads the opposite way for a held name
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

# India VIX percentile within its own trailing 3y range, above which the book
# stops ADDING exposure. Percentile rather than an absolute level because 20 on
# the VIX means something different in 2020 than in 2026; the distribution is
# the honest reference. Existing positions keep exiting on their own triggers —
# this caps new risk, it does not liquidate.
VIX_DERISK_PCTILE = 75.0
VIX_HALT_PCTILE = 90.0


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
    from .live import (cluster_correlation, current_regime, universe_signals,
                       within_cluster_effective_n)
    from .snapshot import get_universe
    universe = {c["ticker"]: c for c in get_universe(session)["companies"]}
    sigs = universe_signals(session)
    rk = current_regime(session)["conditioning_key"]
    weights, _ = cluster_weights()
    # Same null a name was ADMITTED under, or an exit is judged by a different
    # standard than the entry — the held name would be re-scored against a
    # smaller null than the candidate scan used.
    corr = cluster_correlation(session)
    eff_n = within_cluster_effective_n(session)
    out = {}
    for t in tickers:
        item = universe.get(t)
        if item:
            out[t] = synthesize(evidence_from_snapshot(item, sigs, rk, {}),
                                weights=weights, cluster_corr=corr,
                                within_cluster_eff=eff_n).verdict
    return out


def register_daily_forecasts(session: Session, top_n: int = 6) -> dict:
    """Issue and register a dossier for the day's top candidates, traded or not.

    This is what makes the system able to learn. Every gated capability here —
    calibrated probabilities (30 scored claims), calibrated magnitudes, learned
    cluster weights (150 per family) — unlocks from REALISED forecasts, and a
    forecast only exists once a dossier is registered in the hash-chained
    ledger. Until now a dossier was only ever created when a human clicked
    "Generate dossier", so the calibration ledger sat at zero indefinitely and
    every weight stayed provisional forever.

    Registering forecasts is deliberately decoupled from TRADING. A prediction
    the system is held to costs nothing and is the only way to find out whether
    its verdicts are any good; waiting until capital is committed would both
    slow the record to a trickle and bias it toward the subset that passed the
    sizing and liquidity gates. Abstentions are registered too — chronic
    abstention on winners is itself a measured cost.

    Idempotent per day: re-running does not double-register a name.
    """
    from .. import ledger as L
    from .live import build_dossier
    from .candidates import qualified_candidates

    today = date.today().isoformat()
    already = {
        (r.get("company") or {}).get("ticker")
        for r in L.read_all()
        if r.get("kind") == "dossier" and str(r.get("created_at", ""))[:10] == today
    }
    picks = qualified_candidates(session, top_n=top_n)
    # The forecast feed is every reviewed name ranked by conviction, NOT the
    # tradable-candidate list. That list is long-only and gate-filtered, so on a
    # universe where abstention is the modal correct output it is routinely
    # empty — and iterating it registered nothing, permanently. Production sat
    # at 0 scored claims for exactly this reason while the ledger's abstention
    # machinery went unused. Falls back to `candidates` if an older snapshot has
    # no `reviewed` key.
    feed = picks.get("reviewed") or picks.get("candidates", [])
    registered, skipped = [], []
    for c in feed[:top_n]:
        if c["ticker"] in already:
            skipped.append(c["ticker"])
            continue
        # A name whose price series is suspect must not become a scored claim:
        # the outcome would be measured against a number that may not describe a
        # price change at all.
        if c.get("data_suspect"):
            skipped.append(f"{c['ticker']}:data_suspect")
            continue
        company = session.get(Company, c["id"])
        if company is None:
            continue
        try:
            d = build_dossier(session, company)
        except Exception as exc:                       # noqa: BLE001 - never block the cron
            skipped.append(f"{c['ticker']}:{type(exc).__name__}")
            continue
        registered.append({"ticker": c["ticker"],
                           "verdict": d["synthesis"]["verdict"],
                           "hash": d.get("ledger", {}).get("hash", "")[:16]})
    return {"registered": registered, "skipped": skipped,
            "already_today": sorted(t for t in already if t),
            "note": ("Forecasts are registered whether or not they are traded — "
                     "the calibration record is what unlocks learned weights, and "
                     "tying it to executed trades would both starve it and bias "
                     "it toward names that happened to clear the sizing gates.")}


def run_autopilot(session: Session, force: bool = False) -> dict:
    from .candidates import qualified_candidates
    from .live import build_dossier, current_regime
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
        # Any side: a short position is OPENED by a sell, so a buy-only scan
        # left shorts with no opening date and therefore no time exit.
        if c and c.ticker in held:
            oldest_fill[c.ticker] = min(oldest_fill.get(c.ticker, t.trade_date),
                                        t.trade_date)
    for ticker, pos in held.items():
        item = universe.get(ticker, {})
        dv = ((item.get("signals") or {}).get("vol") or 25.0) / (252 ** 0.5)
        stop_pct = 2.5 * dv
        # A short loses money as the price RISES, so its stop sits above entry
        # and its adverse verdict flip is the bullish one. Sharing the long-side
        # tests would have left every short position with no stop at all and an
        # exit trigger that fires on the thesis WORKING.
        is_short = pos["quantity"] < 0
        qty = abs(pos["quantity"])
        reason = None
        if pos["price"] is not None and (
                pos["price"] >= pos["avg_cost"] * (1 + stop_pct / 100) if is_short
                else pos["price"] <= pos["avg_cost"] * (1 - stop_pct / 100)):
            reason = (f"stop breach: price ₹{pos['price']:,.1f} is more than "
                      f"{stop_pct:.1f}% {'above' if is_short else 'below'} "
                      f"avg cost ₹{pos['avg_cost']:,.1f}")
        elif (date.today() - oldest_fill.get(ticker, date.today())).days > cfg["time_exit_days"]:
            reason = (f"time exit: held past the {cfg['time_exit_days']}-day claim "
                      "horizon — realize and let scoring judge the call")
        elif verdicts.get(ticker) == ("long_candidate" if is_short
                                      else "avoid_short_candidate"):
            reason = ("verdict flip: synthesis now reads "
                      f"{'Long' if is_short else 'Avoid/Short'} for this name")
        if reason:
            # Close in the opposite direction: buy to cover a short.
            fill = place_trade(session, pos["company_id"],
                               "buy" if is_short else "sell", qty)
            report["exits"].append({"ticker": ticker, "quantity": qty,
                                    "direction": "short" if is_short else "long",
                                    "fill_price": fill["fill_price"], "reason": reason})

    # -------------------------------------------------------------- entries
    acct = account(session, include_curve=False)  # refresh after exits
    open_names = {p["ticker"] for p in acct["positions"]}
    reserve = acct["equity"] * cfg["cash_reserve_pct"] / 100
    cash = acct["cash"]

    # Regime-conditional exposure cap. The diversification gate treats two names
    # correlated above 0.75 as one bet, but it measures correlation over a
    # TRAILING window — and in an Indian equity crash cross-sectional
    # correlation goes to ~0.9 across the board. So a book assembled as five
    # independent bets in a calm regime becomes one leveraged bet on index beta
    # precisely when that matters, and a cash account cannot hedge it: shorting
    # equity delivery overnight is not available to Indian retail.
    #
    # Conditioning on VIX is not market timing — the position count is not a
    # forecast of direction. It is refusing to add NEW exposure while the price
    # of risk says diversification is about to stop working.
    max_open = cfg["max_open_positions"]
    vix_pct = next((c.get("value") for c in current_regime(session)["components"]
                    if c.get("key") == "vix_percentile"), None)
    if vix_pct is not None and vix_pct >= VIX_HALT_PCTILE:
        max_open = len(open_names)          # hold what is held; add nothing
        report["skipped"].append(
            f"regime halt: India VIX at the {vix_pct:.0f}th percentile of its "
            f"3y range (≥{VIX_HALT_PCTILE}) — cross-sectional correlation "
            "converges in stress, so new positions would concentrate rather "
            "than diversify. Existing positions still exit on their own rules.")
    elif vix_pct is not None and vix_pct >= VIX_DERISK_PCTILE:
        max_open = max(len(open_names), max_open // 2)
        report["skipped"].append(
            f"regime de-risk: India VIX at the {vix_pct:.0f}th percentile "
            f"(≥{VIX_DERISK_PCTILE}) — open-position cap halved to {max_open}.")
    cands = qualified_candidates(session, top_n=10,
                                 book_value=acct["equity"], cash=cash)
    new_count = 0
    for c in cands["candidates"]:
        if new_count >= cfg["max_new_per_run"]:
            report["skipped"].append(f"{c['ticker']}: run cap reached "
                                     f"({cfg['max_new_per_run']} new positions/run)")
            continue
        if len(open_names) >= max_open:
            report["skipped"].append(f"{c['ticker']}: portfolio at max "
                                     f"{max_open} open positions")
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
        # Enter in the candidate's own direction: buy a long, sell to open a
        # short. Sizing is already a magnitude, so the sign lives only here.
        side = "sell" if c.get("direction") == "short" else "buy"
        fill = place_trade(session, c["id"], side, shares,
                           dossier_hash=dossier["ledger"]["hash"])
        # A short credits proceeds but ties up the same margin, so either way
        # this run has that much less capital available to commit.
        cash -= shares * fill["fill_price"]
        open_names.add(c["ticker"])
        new_count += 1
        report["entries"].append({"ticker": c["ticker"], "quantity": shares,
                                  "direction": c.get("direction", "long"),
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
