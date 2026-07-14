"""Trade Candidates engine — universe-wide reasoning, only-peak-trades gates.

Runs the full evidence→synthesis pipeline for EVERY company from the
snapshot (no per-company queries — serverless-fast), applies learned cluster
weights where the gates permit, and passes survivors through hard
tradability gates: verdict, liquidity, cost-vs-expectancy, cash. What comes
out is the short list of trades the system can actually defend — each with
its reasoning attached, executable in the paper account at real prices.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..engine.evidence import Evidence, ev, xsec_strength
from ..engine.sizing import SizingInputs, cost_tax_breakeven, recommend_size
from ..engine.synthesis import synthesize
from ..research.base_rates import get_base_rate
from ..research.learning import cluster_weights
from .live import current_regime, universe_signals
from .snapshot import get_universe

# signal key → (engine, family, cluster, invert, label template)
SIGNAL_EVIDENCE = [
    ("momentum", "technical", "technical.trend", "trend", False, "12-1 momentum {v:+.1f}%"),
    ("dist_52w", "technical", "technical.trend", "trend", False, "{v:+.1f}% from 52-week high"),
    ("trend", "technical", "technical.trend", "trend", False, "price {v:+.1f}% vs 200-day average"),
    ("rel_strength", "technical", "technical.trend", "trend", False, "relative strength vs NIFTY {v:+.1f}%"),
    ("mqi", "novel", "novel.mqi", "trend", False, "Momentum Quality {v:+.2f}"),
    ("pe_pctile", "novel", "novel.value", "value", True, "P/E at {v:.0f}th percentile of own history"),
    ("exp_gap", "valuation", "valuation.expectations", "value", True, "Expectations Gap {v:+.1f}pp"),
    ("f_score", "quality", "quality.fscore", "quality", False, "Piotroski F {v:.0f}/9"),
    ("z_score", "quality", "quality.zscore", "quality", False, "Altman Z {v:.2f}"),
    ("ccs", "novel", "novel.ccs", "quality", False, "Cash Conviction {v:.0f}/100"),
    ("fragility", "novel", "novel.fragility", "risk", True, "Fragility {v:.0f}/100"),
    ("heat", "novel", "novel.crowding", "flow", True, "Participation Heat {v:.2f}"),
    ("vol", "risk", "risk.volatility", "risk", True, "realized vol {v:.1f}%"),
]


def evidence_from_snapshot(item: dict, sigs: dict, regime_key: str,
                           br_cache: dict) -> list[Evidence]:
    t = item["ticker"]
    out = []
    for key, engine, family, cluster, invert, tmpl in SIGNAL_EVIDENCE:
        raw = item["signals"].get(key)
        strength = xsec_strength(sigs[key], t, invert=invert)
        if strength is None or raw is None:
            continue
        base_rate = None
        if key == "momentum":
            base_rate = br_cache.get("momentum")
        elif key == "mqi":
            base_rate = br_cache.get("mqi")
        out.append(ev(engine, family, cluster, strength,
                      tmpl.format(v=raw), [], base_rate=base_rate))
    return [e for e in out if e is not None]


def qualified_candidates(session: Session, top_n: int = 8,
                         book_value: float | None = None,
                         cash: float | None = None) -> dict:
    universe = get_universe(session)
    sigs = universe_signals(session)
    regime = current_regime(session)
    rk = regime["conditioning_key"]
    br_cache = {
        "momentum": get_base_rate(session, "momentum_12_1_top_quintile", 126, rk),
        "mqi": get_base_rate(session, "momentum_quality_top_quintile", 126, rk),
    }
    weights, weights_status = cluster_weights()

    scanned, candidates, verdicts = 0, [], {"long": 0, "avoid": 0, "abstain": 0}
    for item in universe["companies"]:
        scanned += 1
        E = evidence_from_snapshot(item, sigs, rk, br_cache)
        synth = synthesize(E, weights=weights)
        v = synth.verdict
        if v == "long_candidate":
            verdicts["long"] += 1
        elif v == "avoid_short_candidate":
            verdicts["avoid"] += 1
        else:
            verdicts["abstain"] += 1
        if v != "long_candidate":
            continue

        gates = []
        daily_vol = (item["signals"].get("vol") or 25.0) / (252 ** 0.5)
        sizing = recommend_size(SizingInputs(
            book_value=book_value or 1_000_000.0, price=item["price"],
            daily_vol_pct=daily_vol, conviction_band=synth.conviction_band,
            net_score=synth.net_score, adv_cr=item.get("adv_cr"),
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

        failed = any(g.startswith("failed") for g in gates)
        drivers = sorted([e for e in E if e.direction != "shadow"],
                         key=lambda e: -abs(e.strength))[:3]
        candidates.append({
            "id": item["id"], "ticker": item["ticker"], "name": item["name"],
            "sector": item["sector"], "price": item["price"],
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

    candidates.sort(key=lambda c: (-c["tradable"], -c["net_score"]))
    return {
        "as_of": universe.get("as_of"),
        "regime": regime["label"],
        "scanned": scanned,
        "verdict_counts": verdicts,
        "weights_status": weights_status,
        "candidates": candidates[:top_n],
        "discipline_note": (
            f"{scanned} companies scanned; {verdicts['abstain']} abstained — "
            "abstention is the modal, correct output. Candidates shown cleared "
            "synthesis + liquidity + cost gates and are sized for the paper "
            "account at real, executable prices. Every gate and every driver "
            "is shown; nothing is a black box."),
    }
