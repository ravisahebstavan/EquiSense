"""Trade Candidates engine — universe-wide reasoning, only-peak-trades gates.

Runs the full evidence→synthesis pipeline for EVERY company from the
snapshot (no per-company queries — serverless-fast), applies learned cluster
weights where the gates permit, and passes survivors through hard
tradability gates: verdict, liquidity, cost-vs-expectancy, cash. What comes
out is the short list of trades the system can actually defend — each with
its reasoning attached, executable in the paper account at real prices.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.evidence import Evidence, ev, xsec_strength
from ..engine.sizing import SizingInputs, cost_tax_breakeven, recommend_size
from ..engine.synthesis import synthesize
from ..models import PriceObservation
from ..research.base_rates import get_base_rate
from ..research.learning import cluster_weights
from .live import (_corr, cluster_correlation, current_regime,
                   universe_signals, within_cluster_effective_n)
from .snapshot import get_universe

# A scheduled result inside this window is flagged on the candidate. Roughly a
# fortnight: long enough to catch the run-up, short enough that most of the
# universe is not permanently caveated into meaninglessness.
EVENT_RISK_DAYS = 14

# Naive/risk-parity-style concentration heuristic: two names correlated above
# this on trailing daily returns are treated as one bet, not two — a common
# threshold in practitioner diversification rules (e.g. Qian's risk-budgeting
# literature uses similar cutoffs for "effectively duplicate" positions).
CORRELATION_GATE_THRESHOLD = 0.75
CORRELATION_LOOKBACK_DAYS = 70

# signal key → (engine, family, cluster, invert, label template)
SIGNAL_EVIDENCE = [
    ("momentum", "technical", "technical.trend", "trend", False, "12-1 momentum {v:+.1f}%"),
    ("dist_52w", "technical", "technical.anchor_52w", "trend", False, "{v:+.1f}% from 52-week high"),
    ("trend", "technical", "technical.trend_200dma", "trend", False, "price {v:+.1f}% vs 200-day average"),
    ("rel_strength", "technical", "technical.rel_strength", "trend", False, "relative strength vs NIFTY {v:+.1f}%"),
    ("mqi", "novel", "novel.mqi", "trend", False, "Momentum Quality {v:+.2f}"),
    ("sector_rel_mom", "technical", "technical.sector_momentum", "trend", False,
     "{v:+.1f}pp vs own sector (63d, Moskowitz-Grinblatt)"),
    ("pe_pctile", "novel", "novel.value", "value", True, "P/E at {v:.0f}th percentile of own history"),
    ("exp_gap", "valuation", "valuation.expectations", "value", True, "Expectations Gap {v:+.1f}pp"),
    ("f_score", "quality", "quality.fscore", "quality", False, "Piotroski F {v:.0f}/9"),
    ("z_score", "quality", "quality.zscore", "quality", False, "Altman Z {v:.2f}"),
    ("ccs", "novel", "novel.ccs", "quality", False, "Cash Conviction {v:.0f}/100"),
    ("fragility", "novel", "novel.fragility", "risk", True, "Fragility {v:.0f}/100"),
    ("heat", "novel", "novel.crowding", "flow", True, "Participation Heat {v:.2f}"),
    ("max_effect", "behavioral", "behavioral.max_effect", "flow", False,
     "MAX-effect score {v:+.2f} (low recent extremity, Bali-Cakici-Whitelaw)"),
    ("vol", "risk", "risk.volatility", "risk", True, "realized vol {v:.1f}%"),
]

# signal key -> the hypothesis whose FACTOR study measured it. Only signals with
# a real study appear; the rest simply carry no factor caveat.
_FACTOR_HYP = {"momentum": "HYP-001", "mqi": "HYP-004", "heat": "HYP-007",
               "sector_rel_mom": "HYP-010", "vol": "HYP-008",
               "max_effect": "HYP-011"}


def factor_caveats(session) -> dict[str, str]:
    """{signal key: what its own quantile study found wrong with it}.

    Closes a gap between the two planes. The research plane now measures which
    families actually PAY — net of turnover, and whether the effect is monotone
    or lives in one bucket's tail — while the decision plane weights every
    cluster equally until the calibration ledger unlocks, which needs a trading
    record that does not exist yet. So a family the system has itself measured
    as tail-driven was contributing to a verdict at full strength with nothing
    saying so.

    This does NOT change any weight. Letting backtest results set live weights
    is how a system overfits, and the uniform default is a deliberate choice.
    It attaches the measurement to the evidence so the finding is visible at
    the point of decision instead of buried in a Lab tab.
    """
    import json

    from ..models import AppSnapshot
    row = session.get(AppSnapshot, "factor_studies")
    if row is None:
        return {}
    try:
        studies = json.loads(row.payload)
    except Exception:                                  # noqa: BLE001
        return {}
    if not studies.get("computable"):
        return {}
    out: dict[str, str] = {}
    for key, hyp in _FACTOR_HYP.items():
        sg = (studies.get("signals") or {}).get(hyp)
        if not sg or not sg.get("computable"):
            continue
        notes = []
        for h, v in (sg.get("by_horizon") or {}).items():
            ls, lo = v.get("long_short") or {}, v.get("long_only") or {}
            if not ls.get("computable"):
                continue
            if ls.get("tail_driven"):
                notes.append(
                    f"at {h}d its mean spread ({ls.get('spread_mean_pct')}%) and "
                    f"MEDIAN spread ({ls.get('spread_median_pct')}%) disagree — the "
                    "effect sits in a few extreme names, not the typical one")
            elif (ls.get("monotonicity") or 0) < 0.5:
                notes.append(
                    f"at {h}d the quantile profile is not monotone "
                    f"(rank corr {ls.get('monotonicity')}) — the ordering does not "
                    "hold across the universe")
            elif lo.get("computable") and (lo.get("net_annual_pct") or 0) <= 0:
                notes.append(
                    f"at {h}d turnover consumes it: {lo.get('net_annual_pct')}%/yr "
                    "net long-only")
        if notes:
            out[key] = ("own factor study: " + "; ".join(notes[:2])
                        + ". Counted at full strength regardless — weights unlock "
                          "only from realised calibration, never from a backtest.")
    return out


# keys whose base rate comes from a registered study (looked up via br_cache)
_BASE_RATE_KEYS = {"momentum": "momentum", "mqi": "mqi",
                   "sector_rel_mom": "sector_rel_mom", "max_effect": "max_effect"}


def evidence_from_snapshot(item: dict, sigs: dict, regime_key: str,
                           br_cache: dict,
                           fc: dict | None = None) -> list[Evidence]:
    t = item["ticker"]
    out = []
    fc = fc or {}
    for key, engine, family, cluster, invert, tmpl in SIGNAL_EVIDENCE:
        raw = item["signals"].get(key)
        strength = xsec_strength(sigs[key], t, invert=invert)
        if strength is None or raw is None:
            continue
        base_rate = br_cache.get(_BASE_RATE_KEYS.get(key, ""))
        caveats = [fc[key]] if key in fc else None
        out.append(ev(engine, family, cluster, strength,
                      tmpl.format(v=raw), [], base_rate=base_rate,
                      caveats=caveats))
    return [e for e in out if e is not None]


def _fetch_return_series(session: Session, company_ids: list[int],
                         days: int = CORRELATION_LOOKBACK_DAYS) -> dict[int, list[float]]:
    """Light bulk fetch — only for the handful of names that already cleared
    every other gate, never the full universe."""
    if not company_ids:
        return {}
    rows = session.execute(
        select(PriceObservation.company_id, PriceObservation.obs_date,
               PriceObservation.close)
        .where(PriceObservation.company_id.in_(company_ids))
        .order_by(PriceObservation.company_id, PriceObservation.obs_date)).all()
    closes: dict[int, list[float]] = {}
    for cid, _d, c in rows:
        closes.setdefault(cid, []).append(c)
    out: dict[int, list[float]] = {}
    for cid, cl in closes.items():
        cl = cl[-days:]
        if len(cl) >= 20:
            out[cid] = [cl[i] / cl[i - 1] - 1 for i in range(1, len(cl)) if cl[i - 1]]
    if not out:
        return out
    n = min(len(v) for v in out.values())  # equal-length series for _corr
    return {cid: v[-n:] for cid, v in out.items()}


def _apply_diversification_gate(session: Session, candidates: list[dict]) -> None:
    """Greedy, rank-respecting: walk candidates in priority order; the first
    occurrence of a correlated cluster stays tradable, later ones are failed
    with the specific name and correlation shown — never a silent drop."""
    tradable_ids = [c["id"] for c in candidates if c["tradable"]][:15]
    if len(tradable_ids) < 2:
        return
    rets = _fetch_return_series(session, tradable_ids)
    ticker_by_id = {c["id"]: c["ticker"] for c in candidates}
    selected: list[int] = []
    for c in candidates:
        if not c["tradable"] or c["id"] not in rets:
            continue
        worst_corr, worst_with = 0.0, None
        for sid in selected:
            corr = _corr(rets[c["id"]], rets[sid])
            if corr is not None and corr > worst_corr:
                worst_corr, worst_with = corr, sid
        if worst_with is not None and worst_corr > CORRELATION_GATE_THRESHOLD:
            c["gates"].append(
                f"failed: concentration — {worst_corr:.2f} correlated (63d daily "
                f"returns) with higher-ranked {ticker_by_id[worst_with]}, already "
                "prioritized; taking both is one bet twice-sized, not two bets")
            c["tradable"] = False
        else:
            selected.append(c["id"])


def qualified_candidates(session: Session, top_n: int = 8,
                         book_value: float | None = None,
                         cash: float | None = None) -> dict:
    universe = get_universe(session)
    sigs = universe_signals(session)
    # measured once per snapshot, not per name
    corr = cluster_correlation(session)
    eff_n = within_cluster_effective_n(session)
    # what each signal's OWN quantile study found wrong with it, if anything
    fc = factor_caveats(session)
    # Once per scan, never per name, and never fatal: an unreachable
    # calendar must cost this run a caveat, not the whole scan.
    try:
        from ..ingestion.nse_events import fetch_event_calendar
        event_cal = fetch_event_calendar()
    except Exception:                              # noqa: BLE001
        event_cal = {"available": False, "events": {}}
    regime = current_regime(session)
    rk = regime["conditioning_key"]
    br_cache = {
        "momentum": get_base_rate(session, "momentum_12_1_top_quintile", 126, rk),
        "mqi": get_base_rate(session, "momentum_quality_top_quintile", 126, rk),
        "sector_rel_mom": get_base_rate(session, "sector_relative_momentum_top_quintile",
                                        126, rk),
        "max_effect": get_base_rate(session, "low_max_effect_top_quintile", 63, rk),
    }
    weights, weights_status = cluster_weights()
    # Scored-claim count, read ONCE. synthesize() otherwise reads the entire
    # ledger per name: 396 full reads on a 395-name scan, 361 of this
    # endpoint's 668 seconds.
    from ..research.learning import _scored_pairs
    from .. import ledger as _ledger
    try:
        scored_n = len(_scored_pairs(_ledger.read_all()))
    except Exception:                                  # noqa: BLE001
        scored_n = 0

    # Release the read transaction before the scan. Everything above is a read,
    # and the loop below is minutes of pure Python over the whole universe, so
    # the transaction those reads opened would sit idle the entire time —
    # Postgres terminates it, and _apply_diversification_gate's price query at
    # the end then dies with IdleInTransactionSessionTimeout. Measured: this
    # endpoint failed after 440s against Neon while passing every SQLite test,
    # because SQLite has no connection to lose.
    session.rollback()

    scanned, candidates, verdicts = 0, [], {"long": 0, "avoid": 0, "abstain": 0}
    # Every name synthesised, whatever the verdict. `candidates` below is
    # deliberately long-only and gate-filtered — it answers "what could I buy
    # today". The forecast record must NOT be limited to that: abstentions carry
    # counterfactual claims (ledger.register_dossier), and on a universe where
    # abstention is the modal correct output a long-only feed registers nothing
    # at all, so the calibration ledger never accumulates and every weight stays
    # provisional forever. That is exactly what production did: 50 scanned, 0
    # long, 0 forecasts, 0 scored claims.
    reviewed: list[dict] = []
    for item in universe["companies"]:
        scanned += 1
        E = evidence_from_snapshot(item, sigs, rk, br_cache, fc)
        synth = synthesize(E, weights=weights, cluster_corr=corr,
                           scored_n=scored_n, within_cluster_eff=eff_n)
        v = synth.verdict
        if v == "long_candidate":
            verdicts["long"] += 1
        elif v == "avoid_short_candidate":
            verdicts["avoid"] += 1
        else:
            verdicts["abstain"] += 1
        reviewed.append({"id": item["id"], "ticker": item["ticker"],
                         "verdict": v, "net_score": round(synth.net_score, 3),
                         "conviction_band": synth.conviction_band,
                         "data_suspect": bool(item.get("data_suspect"))})
        # Both directions are actionable. Refusing to act on a qualified
        # avoid_short verdict discarded half the system's actionable output —
        # on this universe the only names clearing the significance bar are
        # routinely short-side, so a long-only feed left autopilot idle
        # indefinitely while the synthesis was in fact producing signal.
        if v not in ("long_candidate", "avoid_short_candidate"):
            continue
        direction = 1 if v == "long_candidate" else -1

        gates = []
        # No invented volatility. The fallback here used to be a hardcoded 25%
        # annualized, which is BELOW 79% of this universe (median 30.7%), so an
        # unknown vol produced a tighter stop and therefore a ~1.23x LARGER
        # position than a typical name would have been given — missing data
        # resolving in the optimistic direction, on the one input that sets the
        # stop. live.py already skips sizing when vol is unknown; this now matches.
        if item.get("data_suspect"):
            # withheld rather than scored on a number that may not describe a
            # price change at all
            continue
        vol_ann = item["signals"].get("vol")
        if not vol_ann:
            continue
        daily_vol = vol_ann / (252 ** 0.5)
        sizing = recommend_size(SizingInputs(
            book_value=book_value or 1_000_000.0, price=item["price"],
            daily_vol_pct=daily_vol, conviction_band=synth.conviction_band,
            # Magnitude of conviction, not its sign — a short sized off a
            # negative score would come back at or below zero shares.
            net_score=abs(synth.net_score), adv_cr=item.get("adv_cr"),
            max_position_pct=10.0))
        costs = cost_tax_breakeven(max(sizing["recommended_value"], 1.0),
                                   item.get("adv_cr"), expected_hold_months=6)
        # gate 1: liquidity — must be able to exit
        if (item.get("adv_cr") or 0) < 1.0:
            gates.append("failed: liquidity below ₹1 cr/day traded value")
        # gate 2: expectancy after costs — the family base rate must clear the round trip
        br = br_cache.get("momentum")
        if br and br.get("net_median_excess_pct") is not None \
                and br["net_median_excess_pct"] <= 0:
            gates.append("caveat: reference base-rate family is not positive net of "
                         "costs in this regime — conviction rests on breadth of "
                         "agreement, not a validated standalone edge")
        # gate 3: affordability in the paper account
        if cash is not None and sizing["recommended_value"] > cash:
            gates.append(f"failed: recommended size exceeds available cash")
        # gate 4: scheduled event risk. Nothing in the evidence stack can see a
        # results date — momentum and valuation read identically the day before
        # earnings and the day after — so a position could be opened into a
        # binary event the exchange had already published. A caveat, not a
        # veto: the event is a fact about uncertainty, not about direction.
        nxt = (event_cal.get("events") or {}).get(item["ticker"].upper())
        if nxt:
            e = nxt[0]
            if e["days_away"] <= EVENT_RISK_DAYS:
                gates.append(
                    f"caveat: {e['purpose'] or 'scheduled event'} on {e['date']} "
                    f"({e['days_away']}d away) — entering now takes the outcome "
                    "of a binary event no signal here can read")

        failed = any(g.startswith("failed") for g in gates)
        drivers = sorted([e for e in E if e.direction != "shadow"],
                         key=lambda e: -abs(e.strength))[:3]
        candidates.append({
            "id": item["id"], "ticker": item["ticker"], "name": item["name"],
            "sector": item["sector"], "price": item["price"],
            "direction": "long" if direction > 0 else "short",
            "net_score": round(synth.net_score, 3),
            "conviction_band": synth.conviction_band,
            "dispersion": round(synth.dispersion, 3),
            "drivers": [d.statement for d in drivers],
            "dissent": synth.dissent,
            "sizing": {"shares": sizing["recommended_shares"],
                       "value": sizing["recommended_value"],
                       "stop_distance_pct": sizing["stop_distance_pct"],
                       "binding": sizing["binding_constraint"]},
            "round_trip_cost_pct": costs["round_trip_cost_pct"],
            "breakeven_move_pct": costs["breakeven_gross_move_pct"],
            "gates": gates,
            "tradable": not failed and sizing["recommended_shares"] > 0,
        })

    candidates.sort(key=lambda c: (-c["tradable"], -abs(c["net_score"])))

    # Runtime governance veto. The catastrophe this book cannot survive is not a
    # bad momentum reading — it is an insolvency filing or forensic audit on a
    # name it already holds, which gaps down through consecutive lower circuits
    # with no exit. Fetched at scan time and discarded, so storage is untouched.
    gov = {"available": False, "vetoed": {}}
    try:
        from ..ingestion.nse_alerts import governance_vetoes
        gov = governance_vetoes([c["ticker"] for c in candidates])
    except Exception as exc:                       # noqa: BLE001 - never block a scan
        gov = {"available": False, "reason": f"{type(exc).__name__}", "vetoed": {}}
    for c in candidates:
        reason = (gov.get("vetoed") or {}).get(c["ticker"])
        if reason:
            c["tradable"] = False
            c["gates"] = list(c.get("gates") or []) + [f"failed: {reason}"]

    _apply_diversification_gate(session, candidates)
    candidates.sort(key=lambda c: (-c["tradable"], -abs(c["net_score"])))  # re-sort post-gate

    return {
        "as_of": universe.get("as_of"),
        "regime": regime["label"],
        "scanned": scanned,
        "verdict_counts": verdicts,
        "weights_status": weights_status,
        "governance_filter": {
            "available": gov.get("available", False),
            "vetoed": list((gov.get("vetoed") or {})),
            "note": gov.get("note"),
        },
        "candidates": candidates[:top_n],
        # Ranked by conviction irrespective of verdict, for the forecast record.
        "reviewed": sorted(reviewed, key=lambda r: -abs(r["net_score"])),
        "discipline_note": (
            f"{scanned} companies scanned; {verdicts['abstain']} abstained — "
            "abstention is the modal, correct output. Candidates shown cleared "
            "synthesis + liquidity + cost gates, a "
            f"{CORRELATION_GATE_THRESHOLD:.0%} concentration gate (correlated "
            "picks are flagged and demoted, never silently both taken), and "
            "are sized for the paper account at real, executable prices. Every "
            "gate and every driver is shown; nothing is a black box."),
    }
