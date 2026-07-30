"""Evidence objects (RESEARCH_BLUEPRINT §7.1) and builders.

An Evidence is one engine's typed, tiered, clustered contribution to a
decision. `strength` is a bounded [-1, +1] direction·magnitude used by the
synthesis plane; the human-readable statement and the underlying metrics are
what the user actually reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .types import Metric


@dataclass
class Evidence:
    engine: str
    family: str                 # e.g. "technical.trend"
    cluster: str                # correlation cluster (§8.3): trend | value | quality | flow | macro | risk
    tier: str                   # T1 | T2
    direction: str              # long | short | neutral | flag
    strength: float             # [-1, +1] — the MEASUREMENT, never clipped by maturity
    horizon: str                # weeks | months | quarters
    statement: str
    metrics: list[dict] = field(default_factory=list)
    base_rate: Optional[dict] = None       # attached T2 record (with N_eff, hit rate, IQR)
    caveats: list[str] = field(default_factory=list)
    # How much this family is TRUSTED, from its hypothesis's lifecycle status.
    # The synthesis plane uses it as a weight and as a conviction ceiling; it is
    # deliberately NOT applied to `strength`, so the measurement and the
    # confidence in it stay separable and separately inspectable.
    admission_weight: float = 1.0

    def to_dict(self) -> dict:
        return {
            "engine": self.engine, "family": self.family, "cluster": self.cluster,
            "tier": self.tier, "direction": self.direction,
            "strength": round(self.strength, 3), "horizon": self.horizon,
            "statement": self.statement, "metrics": self.metrics,
            "base_rate": self.base_rate, "caveats": self.caveats,
            "admission_weight": round(self.admission_weight, 3),
        }


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def xsec_strength(values: dict[str, Optional[float]], key: str,
                  invert: bool = False) -> Optional[float]:
    """Cross-sectional percentile → strength in [-1, +1] (PHASE2 §5.1, A2 fix).

    Hand-picked scale divisors are abolished: an engine's raw measurement is
    ranked within the universe as-of-date; strength = 2·(percentile − 0.5).
    Rank-based → outlier-robust and scale-free. `invert` for measurements
    where higher is bearish (crowding, fragility, valuation richness).
    """
    v = values.get(key)
    if v is None:
        return None
    peers = [x for x in values.values() if x is not None]
    if len(peers) < 10:
        return None  # a percentile among 9 peers is noise, not context
    rank = sum(1 for x in peers if x <= v) / len(peers)
    s = 2 * (rank - 0.5)
    return -s if invert else s


def ev(engine: str, family: str, cluster: str, strength: Optional[float],
       statement: str, metrics: list[Metric],
       horizon: str = "months", tier: str = "T1",
       base_rate: Optional[dict] = None,
       caveats: Optional[list[str]] = None) -> Optional[Evidence]:
    """Build an Evidence from a pre-normalized strength (None → nothing is
    emitted: absence of data is absence, never neutral evidence).

    Admission control is enforced HERE — the one point engines cannot route
    around. Cap 0 → shadow evidence: rendered, never aggregated.

    WAVE S: the cap is now recorded as `admission_weight` instead of CLIPPING
    `strength`. Clipping destroyed information and, because every live
    hypothesis sits at cap 0.25, it had silently made the synthesis plane's
    "high conviction" and "abstain on disagreement" rules unreachable. The
    measurement is preserved at full range; the trust in it travels alongside
    as a weight, and the synthesis plane additionally uses it as a hard ceiling
    on the conviction band. Influence is still earned — just explicitly.
    """
    if strength is None:
        return None
    from ..research.registry import admission_cap  # late import: engine layer stays registry-light
    cap, reason = admission_cap(family)
    caveats = list(caveats or [])
    raw = _clip(strength)
    if cap == 0.0:
        capped = 0.0
        direction = "shadow"
        caveats.append(f"SHADOW — {reason}: displayed for context, "
                       "not influencing the verdict.")
    else:
        capped = raw
        if cap < 1.0:
            caveats.append(
                f"admission weight {cap} ({reason}) — the measurement below is "
                "at full strength, but it counts fractionally in the verdict and "
                "caps how much conviction it can support.")
        direction = "long" if capped > 0.1 else ("short" if capped < -0.1 else "neutral")
    # T2 = "carries a measured base rate". Promotion requires the study to have
    # actually passed its power and multiplicity gates; otherwise the base rate
    # is retained as context but the evidence stays T1 and says why. Dressing an
    # underpowered cell as T2 would let it drive `confidence.base_rate_depth`.
    if base_rate is not None and direction != "shadow":
        if base_rate.get("admissible", True):
            tier = "T2"
            if not base_rate.get("survives_multiplicity", True):
                caveats.append(
                    "base rate does not survive multiple-testing control: "
                    f"{base_rate.get('multiplicity_verdict', 'n/a')}")
        else:
            tier = "T1"
            caveats.append(
                "attached study is NOT admissible as a base rate "
                f"({base_rate.get('admissibility_reason', 'failed power gate')}); "
                "shown as context, contributing no base-rate depth.")
    return Evidence(engine=engine, family=family, cluster=cluster, tier=tier,
                    direction=direction, strength=capped, horizon=horizon,
                    statement=statement, metrics=[m.to_dict() for m in metrics],
                    base_rate=base_rate, caveats=caveats,
                    admission_weight=cap)
