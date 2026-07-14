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
CAL_MIN = 30            # scored claims before probability calibration engages
ALIGN_THRESHOLD = 0.1   # |cluster score| above which a cluster "voted"


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


def cluster_weights(records: list[dict] | None = None) -> tuple[dict | None, str]:
    """(weights, status). None → synthesis stays uniform (the honest default).
    Unlocks per cluster only at ≥ UNLOCK_N scored alignments; weight =
    posterior mean rescaled around 1.0, capped to [0.5, 1.5]."""
    post = cluster_posteriors(records)
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
    return weights, f"learned — {len(unlocked)} cluster(s) past the {UNLOCK_N}-sample gate"


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


def learning_state(records: list[dict] | None = None) -> dict:
    records = records if records is not None else ledger.read_all()
    weights, status = cluster_weights(records)
    pairs = _scored_pairs(records)
    recent = [{"company": s["company"], "realized_excess_pct": s["realized_excess_pct"],
               "hit": s["hit"], "stated_probability": s["stated_probability"],
               "brier": s["brier"],
               "verdict": d["verdict"]} for d, s in pairs[-12:]][::-1]
    return {
        "cluster_posteriors": cluster_posteriors(records),
        "weights": weights, "weights_status": status,
        "scored_claims": len(pairs),
        "calibration_engaged": len(pairs) >= CAL_MIN,
        "calibration_note": (f"probability map calibrated from history"
                             if len(pairs) >= CAL_MIN else
                             f"{len(pairs)}/{CAL_MIN} scored claims until the "
                             "probability map is fitted from real outcomes"),
        "unlock_n": UNLOCK_N,
        "recent_outcomes": recent,
        "how_it_learns": (
            "Every non-abstain dossier and every paper fill is a pre-registered, "
            "hash-chained claim. When a claim's horizon expires it is scored "
            "against realized universe-relative returns. Scored outcomes update "
            "per-cluster Beta-Binomial posteriors (which clusters actually call "
            "direction right?) and refit the probability calibration. Influence "
            "unlocks only past pre-registered sample gates — the system refines "
            "itself from its own trade history without ever running on noise."),
    }
