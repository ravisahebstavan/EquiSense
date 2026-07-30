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
    "novel.delivery": "HYP-012",
    "novel.institutional_flow": "HYP-013",
    # NOTE: banking.profitability / banking.spread are deliberately NOT mapped to
    # HYP-014. Mapping them would set their cap to 0.0 (SHADOW) and make every
    # financial-sector name blind again — the exact problem engine/banking.py was
    # built to fix. The distinction the STATUS_CAPS table needs is between "this
    # CLAIM cannot be tested yet" and "this MEASUREMENT cannot be used at all".
    # HYP-014 is the former: it registers the specific, testable proposition that
    # ranking banks on ROA beats ranking them on ROE. ROA and NIM themselves are
    # standard accounting measures, not EquiSense inventions like CCS or the
    # Fragility Index, so they carry the same DEFAULT_UNVALIDATED_CAP as
    # Piotroski and Altman — which are equally untested by any registry entry.
    # Treating a bank's ROA as unusable while an industrial's F-Score counts at
    # 0.25 would be an inconsistency, not extra rigour.
    "technical.trend": "HYP-001",
    "novel.mqi": "HYP-004",
    "novel.ccs": "HYP-005",
    "novel.fragility": "HYP-006",
    "novel.crowding": "HYP-007",
    "technical.vol": "HYP-008",
    "technical.sector_momentum": "HYP-010",
    "behavioral.max_effect": "HYP-011",
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
    "HYP-012": {
        "name": "delivery_percentile_accumulation",
        "family": "novel.delivery",
        "motivation": "Delivery percentage separates stock that genuinely changed "
                      "hands from intraday churn. Two names can print identical "
                      "volume while one was accumulated and the other round-tripped "
                      "by day traders. NSE publishes it daily (MTO file) and the "
                      "platform now ingests it; the claim to be tested is that a "
                      "volume surge on LOW delivery versus a stock's own norm marks "
                      "late-crowd churn and precedes weaker short-horizon returns.",
        "spec": "Monthly: rank the universe by delivery % relative to each stock's "
                "own trailing mean; bottom quintile (churn) and top quintile "
                "(accumulation); forward 21d & 63d excess vs universe median. "
                "REQUIRES ~250 trading days of accumulated MTO history — the "
                "archive publishes one file per day and cannot be backfilled "
                "cheaply, so this cannot run yet.",
        "status": "registered-deferred",
    },
    "HYP-013": {
        "name": "net_institutional_flow_direction",
        "family": "novel.institutional_flow",
        "motivation": "SEBI-mandated bulk/block disclosures name the counterparty, "
                      "the closest free data gets to institutional intent. The "
                      "testable claim is narrow on purpose: only NET flow scaled by "
                      "ADV should matter, and only where net is a large fraction of "
                      "gross. Observed live, the largest gross print of the day "
                      "(IIFL, Rs1,486cr) had net of MINUS Rs9.8cr — funds crossing "
                      "stock with each other, carrying no directional information.",
        "spec": "Daily: for names where |net| >= 50% of gross, rank by net flow in "
                "days of ADV; forward 21d & 63d excess vs universe median. "
                "REQUIRES accumulated deal history — the file is a same-day "
                "disclosure that the platform deliberately does not store, so this "
                "is not testable until a history is kept.",
        "status": "registered-deferred",
    },
    "HYP-014": {
        "name": "bank_roa_quality",
        "family": "banking.profitability",
        "motivation": "For a leveraged spread business ROA is the profitability "
                      "measure that leverage does NOT flatter, while ROE is. The "
                      "claim is that ranking banks on ROA (and on net interest "
                      "margin) identifies durable franchises better than ranking "
                      "them on ROE, which rewards balance-sheet risk. Live example: "
                      "ICICI earns ROE 16.0% on ROA 1.95% at 8.2x leverage while SBI "
                      "earns ROE 15.4% on ROA 1.07% at 14.4x — near-identical ROE, "
                      "entirely different businesses.",
        "spec": "Annual: rank financial-sector names by ROA and by NIM within the "
                "financial cohort only; forward 126d & 252d excess vs the financial "
                "cohort median. DEFERRED for the same reason as HYP-005/006: needs "
                "archived point-in-time statements, and testing on Yahoo's restated "
                "figures would be look-ahead. Also inherently incomplete — asset "
                "quality and capital adequacy, the dominant drivers of bank "
                "outcomes, are unavailable from any free source.",
        "status": "registered-deferred",
    },
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
    "HYP-010": {
        "name": "sector_relative_momentum_top_quintile",
        "family": "technical.sector_momentum",
        "motivation": "Moskowitz & Grinblatt (1999) 'Do Industries Explain "
                      "Momentum?' (J. Finance): momentum measured relative to a "
                      "stock's own industry/sector average is often a distinct "
                      "and stronger signal than momentum relative to the broad "
                      "index — a stock quietly beating its sector peers may be "
                      "missed by NIFTY-relative measures alone.",
        "spec": "Monthly: rank universe by 63d return minus own-sector mean 63d "
                "return; top quintile; forward 63d & 126d excess vs universe "
                "median.",
        "status": "registered",
    },
    "HYP-011": {
        "name": "low_max_effect_top_quintile",
        "family": "behavioral.max_effect",
        "motivation": "Bali, Cakici & Whitelaw (2011) 'Maxing Out: Stocks as "
                      "Lotteries and the Cross-Section of Expected Returns' (J. "
                      "Financial Economics): stocks with extreme recent single-day "
                      "upside attract gambling-like retail demand (lottery "
                      "preference) and subsequently underperform. A genuinely "
                      "distinct behavioral/microstructure signal from the "
                      "platform's existing crowding proxy (HYP-007, which is "
                      "volume-based, not return-extremity-based).",
        "spec": "Monthly: rank universe by the negative of (mean of the 5 highest "
                "daily returns in the trailing 21 days) — i.e. select LOW-MAX "
                "names; top quintile; forward 21d & 63d excess vs universe median.",
        "status": "registered",
    },
}
