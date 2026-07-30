"""Synthesis plane — mechanical, monotone, inspectable aggregation of Evidence.

  1. group by correlation cluster; combine within cluster (double-count control)
  2. combine clusters, weighting each by the RELIABILITY of the evidence in it
  3. dispersion (disagreement) is an output, not noise
  4. abstain when the net signal is indistinguishable from noise, coverage is
     thin, or the engines disagree too much
  5. confidence = decomposable function, never a vibe

No LLM anywhere in this module. Nothing here predicts; it weighs.

WAVE S REDESIGN — why this module changed
-----------------------------------------
The Phase II admission-cap retrofit CLIPPED each evidence strength to its
hypothesis's cap. Since every live hypothesis sits at "registered" (cap 0.25),
that made cluster scores, and therefore `net_score`, live inside [-0.25, +0.25].
Two decision rules were then unreachable *by construction*:

  * `conviction_band == "high"` required |net| ≥ 0.45 — impossible;
  * `abstain_disagreement` required dispersion > 0.55, but dispersion could not
    exceed 0.25 — impossible.

and the usable decision band had collapsed to [0.12, 0.25]. The thresholds were
calibrated before the caps existed and were never re-derived, so the caps had
silently disabled part of the decision logic. (Verified empirically before the
rewrite.)

The fix separates two things the clip had conflated:

  * DIRECTION AND MAGNITUDE — `net_score` uses the full percentile strength over
    [-1, +1], because that is the measurement;
  * HOW MUCH THAT MEASUREMENT IS TRUSTED — a hypothesis's admission cap becomes
    a RELIABILITY WEIGHT on its contribution, and independently imposes a hard
    CEILING on the conviction band.

That preserves "influence is earned" more faithfully than clipping did — an
exploratory family still cannot produce high conviction — while restoring a
working decision range. Shadow evidence (cap 0) is still rendered and never
aggregated.

Thresholds are now DERIVED, not chosen. Under percentile normalization an
uninformative name's cluster scores are draws from U(-1, 1), so the null
standard deviation of `net_score` has a closed form:

    Var(net) = (1 / 3C²) · Σ_c (1 / m_c)          C clusters, m_c evidence each

verified against Monte-Carlo (C=5, m=3: closed form 0.149 vs simulated 0.149;
C=3, m=1: 0.333 vs 0.333). `net_z = net / sd_null` is therefore a proper
standardized score, and abstention is a statement about significance rather than
an arbitrary constant. A name with thin coverage automatically needs a larger
raw net to clear the bar, which is the correct behaviour and something a fixed
threshold could never express.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import fmean, pstdev
from typing import Optional

from .evidence import Evidence

CLUSTERS = ["trend", "value", "quality", "flow", "macro", "risk", "portfolio"]

# --- decision thresholds, all expressed against the derived null ------------
ABSTAIN_Z = 2.0          # |net_z| below this → not distinguishable from noise
MIN_CLUSTERS = 3         # coverage floor → "too hard" pile
MAX_DISPERSION = 0.55    # cluster stdev above this → engines disagree too much
                         # (null median 0.28, p95 0.45, p99 0.52 → 0.55 is a
                         #  genuine tail, and is now reachable)
UNIFORM_VARIANCE = 1.0 / 3.0   # Var of U(-1, 1)

# Conviction ceilings by the maturity of the best evidence present. An
# exploratory family cannot buy high conviction no matter how strong its
# percentile reading is — this is the admission-cap discipline, stated
# explicitly instead of emerging as a side effect of clipping.
CONVICTION_CEILING = [
    (1.00, "high"),      # a "deployed" hypothesis is present
    (0.60, "moderate"),  # a "validated" hypothesis is present
    (0.00, "low"),       # exploratory / unregistered only
]

PROVISIONAL_NOTE = ("Cluster weights are UNIFORM (provisional): the calibration "
                    "ledger has not yet accumulated the ≥150 scored claims per "
                    "family required to unlock learned weights (§17 Gate 4).")


def null_sd(cluster_counts: dict[str, int],
            weights: Optional[dict[str, float]] = None,
            cluster_corr: Optional[dict] = None) -> float:
    """Null standard deviation of `net_score`, correlation-aware.

    Under the percentile normalization an uninformative name's evidence
    strengths are U(-1, 1), so a cluster holding m_c evidence has null variance
    v_c = (1/3)/m_c. The net score is a weighted mean of cluster scores, so:

        Var(net) = Σ_i Σ_j w_i w_j ρ_ij √(v_i v_j)

    With ρ = I and equal weights this collapses to the independence form
    (1/3C²)·Σ(1/m_c), which is what this used to assume unconditionally.

    WHY THE GENERAL FORM MATTERS. Cluster scores are NOT independent — measured
    on the live universe, trend~value is −0.39 and flow~risk is +0.57. Those are
    large. They happen to nearly cancel under EQUAL weights (mean off-diagonal
    ρ = +0.008, variance ratio 1.03, so the independence assumption is currently
    only ~1.5% optimistic). But that cancellation is a coincidence of the weight
    vector, not a property of the clusters: the moment learned weights unlock
    (research/learning.py, at ≥150 scored alignments) the weights stop being
    uniform and the offsetting terms stop offsetting. Carrying the full
    quadratic form means the null stays correct through that transition instead
    of silently degrading.

    `cluster_corr` is {"clusters": [...], "matrix": [[...]]} measured across the
    universe. Missing pairs fall back to independence for that pair only.
    """
    counts = {c: max(1, n) for c, n in cluster_counts.items()}
    names = list(counts)
    C = len(names)
    if C == 0:
        return float("nan")
    v = {c: UNIFORM_VARIANCE / counts[c] for c in names}
    w = {c: (weights or {}).get(c, 1.0) for c in names}
    wsum = sum(w.values())
    if wsum <= 0:
        return float("nan")
    w = {c: x / wsum for c, x in w.items()}

    rho = _corr_lookup(cluster_corr)
    total = 0.0
    for i in names:
        for j in names:
            r = 1.0 if i == j else rho(i, j)
            total += w[i] * w[j] * r * math.sqrt(v[i] * v[j])
    return math.sqrt(total) if total > 0 else float("nan")


def _corr_lookup(cluster_corr: Optional[dict]):
    """(a, b) -> correlation, defaulting to 0 for unmeasured pairs."""
    if not cluster_corr:
        return lambda a, b: 0.0
    idx = {c: i for i, c in enumerate(cluster_corr.get("clusters", []))}
    m = cluster_corr.get("matrix") or []

    def look(a: str, b: str) -> float:
        ia, ib = idx.get(a), idx.get(b)
        if ia is None or ib is None or ia >= len(m) or ib >= len(m[ia]):
            return 0.0
        val = m[ia][ib]
        return 0.0 if val is None or val != val else max(-0.99, min(0.99, float(val)))

    return look


def shrink_correlation(matrix: list[list[float]], n_obs: int,
                       target_identity: bool = True) -> list[list[float]]:
    """Linear shrinkage of a sample correlation matrix toward the identity.

    A 5×5 correlation matrix estimated from ~39 complete cases is noisy: the
    sampling sd of each off-diagonal element is roughly 1/√n ≈ 0.16, which is
    the same order as several of the entries. Shrinking toward independence
    keeps a noisy estimate from making the null *less* conservative than the
    honest independence assumption it replaced.

    Intensity 1/(1+n/k²) is deliberately crude and deliberately conservative:
    with few observations relative to the number of parameters it shrinks hard.
    """
    k = len(matrix)
    if k < 2 or n_obs < 4:
        return [[1.0 if i == j else 0.0 for j in range(k)] for i in range(k)]
    intensity = 1.0 / (1.0 + n_obs / float(k * k))
    intensity = max(0.0, min(1.0, intensity))
    out = []
    for i in range(k):
        row = []
        for j in range(k):
            if i == j:
                row.append(1.0)
                continue
            raw = matrix[i][j]
            raw = 0.0 if raw is None or raw != raw else float(raw)
            tgt = 0.0 if target_identity else 0.0
            row.append((1 - intensity) * raw + intensity * tgt)
        out.append(row)
    return out


@dataclass
class Synthesis:
    verdict: str                      # long_candidate | avoid_short_candidate | abstain_no_edge | abstain_insufficient | abstain_disagreement
    net_score: float                  # [-1, +1] reliability-weighted evidence balance
    conviction_band: str              # none | low | moderate | high
    cluster_scores: dict = field(default_factory=dict)
    cluster_counts: dict = field(default_factory=dict)
    dispersion: float = 0.0
    dissent: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    net_z: float = 0.0                # net_score standardized by the null sd
    null_sd: float = 0.0
    reliability: float = 0.0          # mean admission weight of aggregated evidence
    conviction_ceiling: str = "low"   # cap imposed by hypothesis maturity

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "net_score": round(self.net_score, 3),
            "conviction_band": self.conviction_band,
            "cluster_scores": {k: round(v, 3) for k, v in self.cluster_scores.items()},
            "cluster_counts": self.cluster_counts,
            "dispersion": round(self.dispersion, 3), "dissent": self.dissent,
            "coverage": self.coverage, "confidence": self.confidence,
            "notes": self.notes,
            "net_z": round(self.net_z, 2), "null_sd": round(self.null_sd, 4),
            "reliability": round(self.reliability, 3),
            "conviction_ceiling": self.conviction_ceiling,
            "decision_rule": {
                "abstain_below_abs_z": ABSTAIN_Z,
                "min_clusters": MIN_CLUSTERS,
                "max_dispersion": MAX_DISPERSION,
                "explanation": (
                    "net_z = net_score / null_sd, where null_sd is the closed-form "
                    "standard deviation of net_score for an uninformative name "
                    "given this exact coverage. |net_z| < "
                    f"{ABSTAIN_Z} is not distinguishable from noise, so the "
                    "verdict abstains. The conviction band is separately capped "
                    "by the maturity of the strongest hypothesis behind the "
                    "evidence."),
            },
        }


def _ceiling_for(reliability_max: float) -> str:
    for threshold, band in CONVICTION_CEILING:
        if reliability_max >= threshold:
            return band
    return "low"


def _band_rank(band: str) -> int:
    return {"none": 0, "low": 1, "moderate": 2, "high": 3}.get(band, 0)


def synthesize(evidence: list[Evidence],
               weights: dict[str, float] | None = None,
               empirical_null_sd: Optional[float] = None,
               cluster_corr: Optional[dict] = None) -> Synthesis:
    """`weights` (cluster → multiplier) come ONLY from the learning module's
    gated posteriors (research/learning.py); None = uniform provisional.

    `empirical_null_sd`, when supplied, replaces the closed-form null with a
    measured cross-sectional dispersion of net_score over the universe — which
    accounts for correlation between clusters instead of assuming independence.
    """
    # Shadow evidence (admission cap 0) is rendered but never aggregated.
    items = [e for e in evidence if e is not None and e.direction != "shadow"]
    by_cluster: dict[str, list[Evidence]] = {}
    for e in items:
        by_cluster.setdefault(e.cluster, []).append(e)

    # Within a cluster: reliability-weighted mean of full-range strengths.
    cluster_scores: dict[str, float] = {}
    cluster_reliability: dict[str, float] = {}
    for c, es in by_cluster.items():
        wsum = sum(e.admission_weight for e in es)
        if wsum <= 0:
            cluster_scores[c] = 0.0
            cluster_reliability[c] = 0.0
            continue
        cluster_scores[c] = sum(e.strength * e.admission_weight for e in es) / wsum
        cluster_reliability[c] = wsum / len(es)

    cluster_counts = {c: len(es) for c, es in by_cluster.items()}
    present = list(cluster_scores)
    coverage = {"clusters_present": present,
                "clusters_missing": [c for c in CLUSTERS if c not in present],
                "evidence_count": len(items)}

    reliability = fmean([e.admission_weight for e in items]) if items else 0.0
    reliability_max = max((e.admission_weight for e in items), default=0.0)
    ceiling = _ceiling_for(reliability_max)

    if len(present) < MIN_CLUSTERS:
        return Synthesis(
            verdict="abstain_insufficient", net_score=0.0, conviction_band="none",
            cluster_scores=cluster_scores, cluster_counts=cluster_counts,
            coverage=coverage,
            confidence=_confidence(cluster_scores, items, 0.0, thin=True),
            reliability=reliability, conviction_ceiling=ceiling,
            notes=[PROVISIONAL_NOTE,
                   f"Only {len(present)} evidence clusters available — "
                   f"below the {MIN_CLUSTERS}-cluster floor. Too hard pile."])

    # Across clusters: learned weights (if unlocked) times cluster reliability.
    eff_w = {c: (weights.get(c, 1.0) if weights else 1.0) * max(cluster_reliability[c], 1e-9)
             for c in present}
    wsum = sum(eff_w.values())
    net = (sum(cluster_scores[c] * eff_w[c] for c in present) / wsum
           if wsum > 0 else 0.0)
    dispersion = pstdev(cluster_scores.values()) if len(present) > 1 else 0.0

    sd = empirical_null_sd if (empirical_null_sd and empirical_null_sd > 0) \
        else null_sd(cluster_counts, weights=eff_w, cluster_corr=cluster_corr)
    net_z = net / sd if sd and sd == sd and sd > 0 else 0.0

    dissent = []
    if abs(net_z) > ABSTAIN_Z:
        for c, s in cluster_scores.items():
            if s * net < 0 and abs(s) > 0.2:
                dissent.append(f"{c} cluster dissents ({s:+.2f}) from the "
                               f"{'long' if net > 0 else 'short'} consensus")

    confidence = _confidence(cluster_scores, items, dispersion, thin=False)

    if dispersion > MAX_DISPERSION:
        verdict, band = "abstain_disagreement", "none"
    elif abs(net_z) < ABSTAIN_Z:
        verdict, band = "abstain_no_edge", "none"
    else:
        verdict = "long_candidate" if net > 0 else "avoid_short_candidate"
        # magnitude proposes, evidence maturity and confidence dispose
        proposed = ("high" if abs(net_z) >= 3.0 and confidence["score"] >= 0.6
                    else "moderate" if abs(net_z) >= 2.5 else "low")
        band = proposed if _band_rank(proposed) <= _band_rank(ceiling) else ceiling

    notes = [PROVISIONAL_NOTE if not weights else
             "Cluster weights are LEARNED from scored trade history "
             "(gated Beta-Binomial posteriors — see the learning panel)."]
    notes.append(
        f"net_score {net:+.3f} = {abs(net_z):.2f}× the null sd ({sd:.3f}) for this "
        f"coverage ({len(present)} clusters, {len(items)} evidence).")
    if _band_rank(ceiling) < 3:
        notes.append(
            f"Conviction capped at '{ceiling}': the strongest hypothesis behind "
            f"this evidence has admission weight {reliability_max:.2f}. "
            "Validated hypotheses raise the ceiling; nothing else does.")
    return Synthesis(verdict=verdict, net_score=net, conviction_band=band,
                     cluster_scores=cluster_scores, cluster_counts=cluster_counts,
                     dispersion=dispersion, dissent=dissent, coverage=coverage,
                     confidence=confidence, notes=notes, net_z=net_z, null_sd=sd,
                     reliability=reliability, conviction_ceiling=ceiling)


def _confidence(cluster_scores: dict, items: list[Evidence],
                dispersion: float, thin: bool) -> dict:
    """Decomposable, no vibes. Components each in [0,1], shown."""
    agreement = max(0.0, 1.0 - dispersion / 0.6)
    coverage = min(1.0, len(cluster_scores) / len(CLUSTERS))
    # depth reads the overlap- AND cross-section-corrected N_eff, and only from
    # base rates that actually passed their admissibility gate (T2).
    t2 = [e for e in items if e.tier == "T2" and e.base_rate]
    n_min = min((e.base_rate.get("n_eff") or 0 for e in t2), default=0)
    sample_depth = min(1.0, n_min / 150) if t2 else 0.0
    calibration_history = _calibration_component()
    components = {"agreement": round(agreement, 2), "coverage": round(coverage, 2),
                  "base_rate_depth": round(sample_depth, 2),
                  "calibration_history": round(calibration_history, 2)}
    score = 0.0 if thin else round(
        0.35 * agreement + 0.30 * coverage + 0.25 * sample_depth
        + 0.10 * calibration_history, 2)
    return {"score": score, "components": components,
            "label": _confidence_label(calibration_history)}


def _calibration_component() -> float:
    """Read real calibration progress from the ledger instead of hardcoding 0.

    Previously pinned at 0.0 with the comment "earned, not asserted" — correct
    in spirit, but it also meant the component could never become non-zero even
    once the ledger HAD scored claims, silently capping confidence at 0.90
    forever. It is now earned *and* actually credited.
    """
    try:
        from ..research.learning import CAL_MIN, _scored_pairs
        n = len(_scored_pairs())
    except Exception:
        return 0.0
    if n <= 0:
        return 0.0
    return min(1.0, n / float(CAL_MIN))


def _confidence_label(calibration_component: float) -> str:
    if calibration_component <= 0.0:
        return "uncalibrated — provisional (no scored-claim history yet)"
    if calibration_component < 1.0:
        return (f"partially calibrated — scored-claim history is "
                f"{calibration_component * 100:.0f}% of the gate")
    return "calibrated against realized outcomes"
