"""Hypothesis Registry (RESEARCH_BLUEPRINT §10.5).

Hypotheses are registered *in code, in the repo, before results exist* — the
registry is version-controlled pre-registration. No evidence family may cite
a T2 base rate without a registry entry. Failed hypotheses stay here
permanently with status="rejected"; deleting one is falsifying the record.

Status lifecycle: registered → computed → (validated | weak | rejected)
— set by humans reading the study output, never automatically.
"""

# PHASE2 §5.2 — influence is earned through the lifecycle, mechanically.
# The synthesis plane enforces these caps; engines cannot opt out.
STATUS_CAPS: dict[str, float] = {
    "registered": 0.25,            # exploratory results only → provisional evidence
    "computed": 0.25,
    "registered-deferred": 0.0,    # SHADOW: visible, zero influence
    "shadow": 0.0,
    "validated": 0.60,
    "deployed": 1.00,
    "weak": 0.10,
    "rejected": 0.0,
    "retired": 0.0,
}

# evidence family → governing hypothesis (families without a governing
# hypothesis are unvalidated T1 context and get the default cap)
FAMILY_HYPOTHESIS: dict[str, str] = {
    "technical.trend": "HYP-001",
    "novel.mqi": "HYP-004",
    "novel.ccs": "HYP-005",
    "novel.fragility": "HYP-006",
    "novel.crowding": "HYP-007",
    "technical.vol": "HYP-008",
}

DEFAULT_UNVALIDATED_CAP = 0.25


def admission_cap(family: str) -> tuple[float, str]:
    """(max |strength| into synthesis, reason). PHASE2 §5.2 / autopsy A3."""
    hyp_id = FAMILY_HYPOTHESIS.get(family)
    if hyp_id is None:
        return DEFAULT_UNVALIDATED_CAP, f"unregistered T1 context (cap {DEFAULT_UNVALIDATED_CAP})"
    status = REGISTRY[hyp_id]["status"]
    cap = STATUS_CAPS.get(status, 0.0)
    return cap, f"{hyp_id} status={status} → cap {cap}"


REGISTRY: dict[str, dict] = {
    "HYP-009": {
        "name": "vol_managed_momentum_overlay",
        "family": "meta.risk_management",
        "motivation": "Barroso & Santa-Clara (2015) 'Momentum Has Its Moments' "
                      "(J. Financial Economics) show momentum's worst drawdowns "
                      "coincide with spikes in the STRATEGY's own realized "
                      "volatility (post-crash reversals — see also Daniel & "
                      "Moskowitz 2016 'Momentum Crashes', JFE); scaling exposure "
                      "inversely to trailing strategy vol, targeting a constant "
                      "annualized vol, historically improves Sharpe and cuts tail "
                      "risk versus constant-capital weighting. Distinct from the "
                      "platform's existing stock-level vol scaling (MQI, HYP-004): "
                      "this targets the PORTFOLIO's own trailing realized vol.",
        "spec": "Backtest the top-N price-cluster composite two ways: (a) equal "
                "weight each period (baseline), (b) scale period exposure by "
                "target_vol / trailing realized vol of the strategy's own prior "
                "6 periods, capped to [0.3x, 1.5x]. Compare Sharpe, worst period, "
                "and max drawdown, net of costs, over the full stored history.",
        "status": "registered",
    },
    "REG-001": {
        "name": "regime_conditioning_value",
        "family": "meta.regime",
        "motivation": "The regime engine must justify its own existence: does "
                      "conditioning base rates on the trend regime improve "
                      "out-of-sample calibration versus the unconditional rate?",
        "spec": "Split momentum episodes at the median date; fit conditional "
                "(per-regime) and unconditional hit rates on the first half; "
                "Brier-score both on the second half; regime engine keeps its "
                "conditioning role only if ΔBrier favors conditional.",
        "status": "registered",
    },
    "HYP-001": {
        "name": "momentum_12_1_top_quintile",
        "family": "technical.trend",
        "motivation": "12-1 cross-sectional momentum is the most robust anomaly "
                      "in Indian academic literature; test on our own universe/history.",
        "spec": "Monthly: rank universe by 12-1 momentum; top quintile; forward "
                "63d & 126d return minus universe median forward return.",
        "status": "registered",
    },
    "HYP-002": {
        "name": "near_52w_high",
        "family": "technical.trend",
        "motivation": "Anchoring at 52w highs (George & Hwang 2004): proximity to "
                      "the high predicts continuation because holders anchor.",
        "spec": "Monthly: stocks within 5% of 52w high; forward 63d & 126d excess "
                "vs universe median.",
        "status": "registered",
    },
    "HYP-003": {
        "name": "above_200dma",
        "family": "technical.trend",
        "motivation": "Trend-regime filter: does simply being above the 200DMA "
                      "carry forward-return information in this universe?",
        "spec": "Monthly: stocks above their 200DMA; forward 63d & 126d excess "
                "vs universe median.",
        "status": "registered",
    },
    "HYP-004": {
        "name": "momentum_quality_top_quintile",
        "family": "novel.mqi",
        "motivation": "EquiSense original: smooth momentum (vol-scaled, "
                      "persistence-weighted) should decay slower than raw momentum. "
                      "Our own invention gets the same gauntlet as everything else.",
        "spec": "Monthly: rank by MQI; top quintile; forward 63d & 126d excess vs "
                "universe median; compare against HYP-001's table.",
        "status": "registered",
    },
    "HYP-005": {
        "name": "cash_conviction_score",
        "family": "novel.ccs",
        "motivation": "EquiSense original: cash-backed earnings should outperform "
                      "accrual-heavy earnings (Sloan 1996 anachronism check for India).",
        "spec": "Fundamental cohort study — requires archived (PIT) statement "
                "history; DEFERRED until the archive matures (§6.1). Not testable "
                "from reconstructed fundamentals without look-ahead risk.",
        "status": "registered-deferred",
    },
    "HYP-006": {
        "name": "fragility_index",
        "family": "novel.fragility",
        "motivation": "EquiSense original: fragile balance sheets should underperform "
                      "in downtrend regimes specifically.",
        "spec": "Same deferral as HYP-005 for the statement components; the "
                "price-only components (vol, drawdown) are testable now via HYP-008.",
        "status": "registered-deferred",
    },
    "HYP-007": {
        "name": "participation_heat_top_decile",
        "family": "novel.crowding",
        "motivation": "EquiSense original: volume-surge × extension flags late-crowd "
                      "entries; expect *negative* short-horizon excess.",
        "spec": "Monthly: top decile of Participation Heat; forward 21d & 63d excess "
                "vs universe median.",
        "status": "registered",
    },
    "HYP-008": {
        "name": "low_vol_quintile",
        "family": "technical.vol",
        "motivation": "Low-volatility anomaly check on this universe — also serves "
                      "as the price-only leg of the Fragility hypothesis.",
        "spec": "Monthly: bottom quintile of 126d realized vol; forward 126d excess "
                "vs universe median.",
        "status": "registered",
    },
}
