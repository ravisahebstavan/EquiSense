"""The self-refinement loop (PHASE2 §5.4, §7 — now implemented).

Two learned artifacts, both from the platform's own scored history, both
gated by pre-registered sample thresholds so nothing ever runs on noise:

1. CLUSTER POSTERIORS — every scored directional claim credits or debits the
   evidence clusters that voted with/against the realized outcome, via
   Beta-Binomial updating (uniform Beta(1,1) prior). Posterior means become
   synthesis weight multipliers ONLY once a cluster has ≥ UNLOCK_N scored
   alignments (Gate 4 discipline); below that they are displayed as "learning
   in progress" and the synthesis stays uniform.

2. CALIBRATED PROBABILITIES — the stated P(hit) on new claims is fitted from
   realized hit rates in |net score| bins once ≥ CAL_MIN claims are scored,
   replacing the founding-era linear map. Shrunk toward 0.5 by sample size.

Everything here is deterministic, inspectable, and recomputed from the
hash-chained ledger — the model literally learns from its own trade history
and mistakes, with the receipts to prove it.
"""
from __future__ import annotations

from .. import ledger

UNLOCK_N = 150          # scored alignments per cluster before weights unlock

# Raw claim COUNT is not evidence count. The book holds ~15 names simultaneously
# across overlapping tranches, so when a 63-day horizon matures it retires a
# batch of claims that all lived through the SAME market regime. If the market
# rose, most succeeded; if it fell, most failed. Treating those as independent
# observations inflates the degrees of freedom and would unlock live weights on
# what is really one or two regime observations — precisely the failure the
# cluster-robust machinery in research/stats.py exists to prevent everywhere
# else in this system.
#
# So the gate requires BOTH: enough scored alignments AND enough temporally
# distinct forecast cohorts. Three non-overlapping 63-day cycles is roughly nine
# months of live history, which guarantees the system has survived at least two
# regime transitions before it is allowed to reweight itself.
MIN_INDEPENDENT_CYCLES = 3
CYCLE_DAYS = 63
CAL_MIN = 30            # scored claims before probability calibration engages
ALIGN_THRESHOLD = 0.1   # |cluster score| above which a cluster "voted"
MAG_MIN = 30            # same-direction scored claims before magnitude calibration engages
PROVISIONAL_MAG_SCALE = 8.0  # pp of excess return at |net_score|=1, 126d horizon —
# deliberately modest until real history exists, exactly like _stated_probability's
# founding-era linear map for direction confidence


def _scored_pairs(records: list[dict] | None = None) -> list[tuple[dict, dict]]:
    """(dossier_record, score_record) for every scored directional claim."""
    records = records if records is not None else ledger.read_all()
    dossiers = {r["hash"]: r for r in records if r["kind"] == "dossier"}
    out = []
    for r in records:
        if r["kind"] != "score" or r.get("hit") is None:
            continue
        d = dossiers.get(r.get("scores_dossier_hash"))
        if d is not None:
            out.append((d, r))
    return out


def cluster_posteriors(records: list[dict] | None = None) -> dict:
    """Beta-Binomial per cluster: a 'win' = the cluster's vote agreed with the
    realized excess direction. Old dossier records without cluster_scores are
    skipped (learning starts from when attribution began being recorded)."""
    tally: dict[str, dict] = {}
    for d, s in _scored_pairs(records):
        clusters = d.get("cluster_scores") or {}
        realized_up = s["realized_excess_pct"] > 0
        for cluster, score in clusters.items():
            if abs(score) < ALIGN_THRESHOLD:
                continue
            t = tally.setdefault(cluster, {"wins": 0, "n": 0})
            t["n"] += 1
            if (score > 0) == realized_up:
                t["wins"] += 1
    out = {}
    for cluster, t in tally.items():
        mean = (t["wins"] + 1) / (t["n"] + 2)  # Beta(1,1) posterior mean
        out[cluster] = {
            "wins": t["wins"], "n": t["n"],
            "posterior_mean": round(mean, 3),
            "unlocked": t["n"] >= UNLOCK_N,
            "unlock_progress": round(min(1.0, t["n"] / UNLOCK_N), 3),
        }
    return out


def independent_cycles(records: list[dict] | None = None) -> int:
    """How many temporally NON-OVERLAPPING forecast cohorts have been scored.

    Claims registered days apart and held over the same 63 days are one
    observation of one regime, not many. Counted by walking scored claims in
    date order and starting a new cycle only once CYCLE_DAYS have elapsed since
    the last one opened.
    """
    from datetime import date as _date
    dates = []
    for d, _s in _scored_pairs(records):
        raw = str(d.get("created_at") or "")[:10]
        try:
            y, m, dd = (int(x) for x in raw.split("-"))
            dates.append(_date(y, m, dd))
        except Exception:                              # noqa: BLE001
            continue
    if not dates:
        return 0
    dates.sort()
    cycles, anchor = 1, dates[0]
    for d in dates[1:]:
        if (d - anchor).days >= CYCLE_DAYS:
            cycles += 1
            anchor = d
    return cycles


def cluster_weights(records: list[dict] | None = None) -> tuple[dict | None, str]:
    """(weights, status). None → synthesis stays uniform (the honest default).

    Unlocks only when a cluster has ≥ UNLOCK_N scored alignments AND the ledger
    spans ≥ MIN_INDEPENDENT_CYCLES non-overlapping horizons. The second
    condition is what stops a single good quarter — 15 correlated names all
    succeeding in one rising regime — from being mistaken for 15 independent
    confirmations and unlocking live weights.
    """
    post = cluster_posteriors(records)
    cycles = independent_cycles(records)
    if cycles < MIN_INDEPENDENT_CYCLES:
        n_max = max((p["n"] for p in post.values()), default=0)
        return None, (
            f"uniform (provisional) — {cycles}/{MIN_INDEPENDENT_CYCLES} independent "
            f"{CYCLE_DAYS}d cycles scored ({n_max} raw alignments). Claims held "
            "over the same window are one regime observation, not many, so the "
            "gate counts cycles as well as claims.")
    unlocked = {c: p for c, p in post.items() if p["unlocked"]}
    if not unlocked:
        n_max = max((p["n"] for p in post.values()), default=0)
        return None, (f"uniform (provisional) — richest cluster has {n_max}/{UNLOCK_N} "
                      "scored alignments; weights unlock per cluster at the gate")
    weights = {}
    for c, p in post.items():
        if p["unlocked"]:
            weights[c] = round(max(0.5, min(1.5, 1.0 + (p["posterior_mean"] - 0.5) * 2)), 3)
        else:
            weights[c] = 1.0  # locked clusters stay uniform
    return weights, (f"learned — {len(unlocked)} cluster(s) past the "
                     f"{UNLOCK_N}-sample gate across {cycles} independent cycles")


def calibrated_probability(net_score: float,
                           records: list[dict] | None = None) -> tuple[float, str]:
    """Stated P(hit) for a new claim. Below CAL_MIN scored claims: the founding
    linear map, labeled provisional. At/above: empirical hit rate in |net|
    terciles, shrunk toward 0.5 by bin sample size."""
    pairs = _scored_pairs(records)
    default = round(min(0.65, 0.5 + abs(net_score) * 0.25), 3)
    if len(pairs) < CAL_MIN:
        return default, f"provisional map ({len(pairs)}/{CAL_MIN} scored claims)"
    rows = [(abs(d["net_score"]), 1.0 if s["hit"] else 0.0) for d, s in pairs]
    rows.sort(key=lambda r: r[0])  # by score ONLY — sorting on the tuple would
    # tiebreak on the outcome label and poison bin composition
    k = len(rows) // 3 or 1
    bins = [rows[:k], rows[k:2 * k], rows[2 * k:]]
    x = abs(net_score)
    for b in bins:
        if b and x <= b[-1][0]:
            hits = sum(h for _, h in b)
            shrunk = (hits + 0.5 * 10) / (len(b) + 10)  # shrink toward 0.5, pseudo-n=10
            return round(shrunk, 3), f"calibrated from {len(pairs)} scored claims"
    b = bins[-1] or rows
    hits = sum(h for _, h in b)
    return round((hits + 5) / (len(b) + 10), 3), f"calibrated from {len(pairs)} scored claims"


def calibrated_magnitude(net_score: float, horizon_days: int = 126,
                         records: list[dict] | None = None) -> tuple[float, str]:
    """Predicted excess-return MAGNITUDE for a new claim — the counterpart to
    calibrated_probability, answering 'how big a move, not just which way'.
    Below MAG_MIN same-direction scored claims: a deliberately modest linear
    map, scaled to the claim's own horizon. At/above: the empirical mean
    realized excess return within the matching |net score| tercile, computed
    separately for long-side and short-side claims so the two populations
    never get averaged into each other."""
    default = round(net_score * PROVISIONAL_MAG_SCALE * (horizon_days / 126.0), 2)
    pairs = _scored_pairs(records)
    same_side = sorted(
        ((abs(d["net_score"]), s["realized_excess_pct"]) for d, s in pairs
         if (d["net_score"] >= 0) == (net_score >= 0)),
        key=lambda r: r[0])
    if len(same_side) < MAG_MIN:
        return default, f"provisional linear map ({len(same_side)}/{MAG_MIN} same-direction scored claims)"
    k = len(same_side) // 3 or 1
    bins = [same_side[:k], same_side[k:2 * k], same_side[2 * k:]]
    x = abs(net_score)
    basis = f"calibrated from {len(same_side)} same-direction scored claims"
    for b in bins:
        if b and x <= b[-1][0]:
            return round(sum(r[1] for r in b) / len(b), 2), basis
    b = bins[-1] or same_side
    return round(sum(r[1] for r in b) / len(b), 2), basis


def learning_state(records: list[dict] | None = None) -> dict:
    records = records if records is not None else ledger.read_all()
    weights, status = cluster_weights(records)
    pairs = _scored_pairs(records)
    recent = [{"company": s["company"], "realized_excess_pct": s["realized_excess_pct"],
               "predicted_excess_pct": s.get("predicted_excess_pct"),
               "forecast_error_pct": s.get("forecast_error_pct"),
               "hit": s["hit"], "stated_probability": s["stated_probability"],
               "brier": s["brier"],
               "verdict": d["verdict"]} for d, s in pairs[-12:]][::-1]
    mag_errors = [s["abs_forecast_error_pct"] for d, s in pairs
                 if s.get("abs_forecast_error_pct") is not None]
    checkpoints = [r for r in records if r["kind"] == "checkpoint"][-12:][::-1]
    return {
        "cluster_posteriors": cluster_posteriors(records),
        "weights": weights, "weights_status": status,
        "scored_claims": len(pairs),
        "calibration_engaged": len(pairs) >= CAL_MIN,
        "calibration_note": (f"probability map calibrated from history"
                             if len(pairs) >= CAL_MIN else
                             f"{len(pairs)}/{CAL_MIN} scored claims until the "
                             "probability map is fitted from real outcomes"),
        "mean_abs_forecast_error_pct": (round(sum(mag_errors) / len(mag_errors), 2)
                                        if mag_errors else None),
        "magnitude_calibration_note": (
            f"magnitude map calibrated from history" if len(pairs) >= MAG_MIN else
            f"{len(pairs)}/{MAG_MIN} same-direction scored claims until predicted "
            "excess magnitude is fitted from real outcomes"),
        "unlock_n": UNLOCK_N,
        "recent_outcomes": recent,
        "recent_checkpoints": checkpoints,
        "how_it_learns": (
            "Every non-abstain dossier and every paper fill is a pre-registered, "
            "hash-chained claim: a DIRECTION, a stated PROBABILITY, and a "
            "predicted excess-return MAGNITUDE, all fixed before the outcome is "
            "known. An interim checkpoint fires partway through the horizon "
            "(T so-far vs the pro-rated prediction) so drift is visible early, "
            "and the full claim is scored when its horizon expires against "
            "realized universe-relative returns — direction (hit/Brier) AND "
            "magnitude (predicted vs realized excess, forecast error). Scored "
            "outcomes update per-cluster Beta-Binomial posteriors, refit the "
            "probability calibration, and refit the magnitude calibration. "
            "Influence unlocks only past pre-registered sample gates — the "
            "system refines itself from its own trade history without ever "
            "running on noise."),
    }
