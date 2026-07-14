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
    strength: float             # [-1, +1]
    horizon: str                # weeks | months | quarters
    statement: str
    metrics: list[dict] = field(default_factory=list)
    base_rate: Optional[dict] = None       # attached T2 record (with N, hit rate, IQR)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "engine": self.engine, "family": self.family, "cluster": self.cluster,
            "tier": self.tier, "direction": self.direction,
            "strength": round(self.strength, 3), "horizon": self.horizon,
            "statement": self.statement, "metrics": self.metrics,
            "base_rate": self.base_rate, "caveats": self.caveats,
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

    Admission caps (PHASE2 §5.2, A3 fix) are applied HERE — the one
    enforcement point engines cannot route around. Cap 0 → shadow evidence:
    rendered, never aggregated.
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
        capped = _clip(raw, -cap, cap)
        if abs(raw) > cap:
            caveats.append(f"strength capped at ±{cap} ({reason}); "
                           f"uncapped percentile strength was {raw:+.2f}")
        direction = "long" if capped > 0.1 else ("short" if capped < -0.1 else "neutral")
    if base_rate is not None and direction != "shadow":
        tier = "T2"
    return Evidence(engine=engine, family=family, cluster=cluster, tier=tier,
                    direction=direction, strength=capped, horizon=horizon,
                    statement=statement, metrics=[m.to_dict() for m in metrics],
                    base_rate=base_rate, caveats=caveats)
