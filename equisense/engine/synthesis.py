"""Synthesis plane (RESEARCH_BLUEPRINT §8).

Mechanical, monotone, inspectable aggregation of Evidence objects:
  1. group by correlation cluster; average within cluster (double-count control)
  2. combine clusters with weights — UNIFORM and flagged "provisional" until
     the calibration ledger has ≥150 scored claims per family (§17 Gate 4);
     no hand-tuned constants, ever
  3. dispersion (disagreement) is an output, not noise
  4. abstention when net signal is weak, coverage thin, or dispersion extreme
  5. confidence = decomposable function (§8.4), never a vibe

No LLM anywhere in this module. Nothing here predicts; it weighs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev

from .evidence import Evidence

CLUSTERS = ["trend", "value", "quality", "flow", "macro", "risk", "portfolio"]
PROVISIONAL_NOTE = ("Cluster weights are UNIFORM (provisional): the calibration "
                    "ledger has not yet accumulated the ≥150 scored claims per "
                    "family required to unlock learned weights (§17 Gate 4).")

ABSTAIN_NET = 0.12          # |net| below this → no detectable edge
MIN_CLUSTERS = 3            # coverage below this → too hard / insufficient data
MAX_DISPERSION = 0.55       # cluster stdev above this → engines disagree too much


@dataclass
class Synthesis:
    verdict: str                      # long_candidate | avoid_short_candidate | abstain_no_edge | abstain_insufficient | abstain_disagreement
    net_score: float                  # [-1, +1]
    conviction_band: str              # none | low | moderate | high
    cluster_scores: dict = field(default_factory=dict)
    cluster_counts: dict = field(default_factory=dict)
    dispersion: float = 0.0
    dissent: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "net_score": round(self.net_score, 3),
            "conviction_band": self.conviction_band,
            "cluster_scores": {k: round(v, 3) for k, v in self.cluster_scores.items()},
            "cluster_counts": self.cluster_counts,
            "dispersion": round(self.dispersion, 3), "dissent": self.dissent,
            "coverage": self.coverage, "confidence": self.confidence,
            "notes": self.notes,
        }


def synthesize(evidence: list[Evidence]) -> Synthesis:
    # Shadow evidence (admission cap 0, §5.2) is rendered but never aggregated.
    items = [e for e in evidence if e is not None and e.direction != "shadow"]
    by_cluster: dict[str, list[Evidence]] = {}
    for e in items:
        by_cluster.setdefault(e.cluster, []).append(e)

    cluster_scores = {c: mean(e.strength for e in es) for c, es in by_cluster.items()}
    cluster_counts = {c: len(es) for c, es in by_cluster.items()}
    present = list(cluster_scores)
    coverage = {"clusters_present": present,
                "clusters_missing": [c for c in CLUSTERS if c not in present],
                "evidence_count": len(items)}

    if len(present) < MIN_CLUSTERS:
        return Synthesis(verdict="abstain_insufficient", net_score=0.0,
                         conviction_band="none", cluster_scores=cluster_scores,
                         cluster_counts=cluster_counts, coverage=coverage,
                         confidence=_confidence(cluster_scores, items, 0.0, thin=True),
                         notes=[PROVISIONAL_NOTE,
                                f"Only {len(present)} evidence clusters available — "
                                f"below the {MIN_CLUSTERS}-cluster floor. Too hard pile."])

    # uniform provisional weights across present clusters
    net = mean(cluster_scores[c] for c in present)
    dispersion = pstdev(cluster_scores.values()) if len(present) > 1 else 0.0

    dissent = []
    if abs(net) > ABSTAIN_NET:
        for c, s in cluster_scores.items():
            if s * net < 0 and abs(s) > 0.2:
                dissent.append(f"{c} cluster dissents ({s:+.2f}) from the "
                               f"{'long' if net > 0 else 'short'} consensus")

    confidence = _confidence(cluster_scores, items, dispersion, thin=False)

    if dispersion > MAX_DISPERSION:
        verdict, band = "abstain_disagreement", "none"
    elif abs(net) < ABSTAIN_NET:
        verdict, band = "abstain_no_edge", "none"
    else:
        verdict = "long_candidate" if net > 0 else "avoid_short_candidate"
        band = ("high" if abs(net) >= 0.45 and confidence["score"] >= 0.6
                else "moderate" if abs(net) >= 0.25 else "low")

    return Synthesis(verdict=verdict, net_score=net, conviction_band=band,
                     cluster_scores=cluster_scores, cluster_counts=cluster_counts,
                     dispersion=dispersion, dissent=dissent, coverage=coverage,
                     confidence=confidence, notes=[PROVISIONAL_NOTE])


def _confidence(cluster_scores: dict, items: list[Evidence],
                dispersion: float, thin: bool) -> dict:
    """§8.4: decomposable, no vibes. Components each in [0,1], shown."""
    agreement = max(0.0, 1.0 - dispersion / 0.6)
    coverage = min(1.0, len(cluster_scores) / len(CLUSTERS))
    t2 = [e for e in items if e.tier == "T2" and e.base_rate]
    # depth reads the overlap-corrected N_eff, never raw N (PHASE2 §8, A1)
    n_min = min((e.base_rate.get("n_eff") or 0 for e in t2), default=0)
    sample_depth = min(1.0, n_min / 150) if t2 else 0.0
    calibration_history = 0.0  # no scored claims yet — earned, not asserted (§2.4)
    components = {"agreement": round(agreement, 2), "coverage": round(coverage, 2),
                  "base_rate_depth": round(sample_depth, 2),
                  "calibration_history": calibration_history}
    score = 0.0 if thin else round(
        0.35 * agreement + 0.30 * coverage + 0.25 * sample_depth
        + 0.10 * calibration_history, 2)
    return {"score": score, "components": components,
            "label": "uncalibrated — provisional (no scored-claim history yet)"}
