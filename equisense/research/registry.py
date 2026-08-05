"""Hypothesis Registry (§9.2).

Hypotheses are registered *in code, in the repo, before results exist* — the
registry is version-controlled pre-registration. No evidence family may cite
a T2 base rate without a registry entry. Failed hypotheses stay here
permanently with status="rejected"; deleting one is falsifying the record.

Status lifecycle: registered → computed → (validated | weak | rejected)
— set by humans reading the study output, never automatically.
"""

# §7.3 — influence is earned through the lifecycle, mechanically.
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
    # Split out 2026-08-02. These three emitted family "technical.trend" and so
    # inherited HYP-001's status, whose spec covers 12-1 momentum ONLY — three
    # untested signals wearing a tested one's credential. Each now carries its
    # own governance, so a status change reaches the signal it is about.
    "technical.anchor_52w": "HYP-021",
    "technical.trend_200dma": "HYP-022",
    "technical.rel_strength": "HYP-023",
    "novel.mqi": "HYP-004",
    "novel.ccs": "HYP-005",
    "novel.fragility": "HYP-006",
    "novel.crowding": "HYP-007",
    "risk.volatility": "HYP-008",
    "technical.sector_momentum": "HYP-010",
    "behavioral.max_effect": "HYP-011",
}

DEFAULT_UNVALIDATED_CAP = 0.25


def admission_cap(family: str) -> tuple[float, str]:
    """(max |strength| into synthesis, reason). §7.3 / autopsy A3."""
    hyp_id = FAMILY_HYPOTHESIS.get(family)
    if hyp_id is None:
        return DEFAULT_UNVALIDATED_CAP, f"unregistered T1 context (cap {DEFAULT_UNVALIDATED_CAP})"
    status = REGISTRY[hyp_id]["status"]
    cap = STATUS_CAPS.get(status, 0.0)
    return cap, f"{hyp_id} status={status} → cap {cap}"


REGISTRY: dict[str, dict] = {
    "HYP-015": {
        "name": "variance_risk_premium",
        "family": "derivatives.vrp",
        "motivation": "Index option implied volatility systematically exceeds "
                      "subsequently realised volatility (Carr & Wu 2009; "
                      "Bollerslev, Tauchen & Zhou 2009) because sellers demand "
                      "compensation for bearing crash risk. It is the most "
                      "promising effect available to THIS platform for two "
                      "reasons: it does not require predicting direction, which "
                      "is where every price signal here has failed its IC test; "
                      "and it is harvestable by a retail participant, unlike "
                      "cross-sectional alpha. NSE's F&O bhavcopy makes the "
                      "implied side measurable for free.",
        "spec": "Daily: capture ATM implied volatility for NIFTY/BANKNIFTY from "
                "the settlement-price chain. For each observation compute "
                "realised volatility over the FOLLOWING 21 sessions, "
                "non-overlapping with the observation date. Test mean(IV − RV) "
                "with a Newey-West t for the overlapping realised windows. "
                "NOT YET TESTABLE: the IV series cannot be backfilled (one "
                "exchange file per day), so this needs ~2 months of capture "
                "before it has the ~40 paired observations it requires.",
        "status": "registered-deferred",
    },
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
    # ---- information-arrival family (measured 2026-08-01, full 500-name panel)
    "HYP-016": {
        "name": "confirmed_news_drift",
        "family": "information.drift",
        "motivation": "Post-earnings-announcement drift (Ball & Brown 1968; "
                      "Bernard & Thomas 1989): information diffuses into prices "
                      "over weeks, so recent confirmed news should predict the "
                      "coming weeks' returns. Overnight gaps are the cleanest "
                      "free proxy for news arrival.",
        "spec": "Monthly: sum of signed overnight gaps exceeding 2 trailing SD "
                "with volume >1.5x trailing median, over the prior 21 sessions; "
                "forward 21/63/126d.",
        "status": "measured_weak",
        "result": "IC +0.007 to +0.011 (t=0.8-1.1), spread t=1.3-1.8, turnover "
                  "0.68. Directionally present but not significant, and turnover "
                  "consumes it: +0.7% to +2.9%/yr net. NOT tradeable as built.",
    },
    "HYP-017": {
        "name": "information_confirmed_momentum",
        "family": "information.confirmation",
        "motivation": "A human reading the tape distinguishes a stock drifting "
                      "up on disclosures from one drifting up on nothing. Test "
                      "whether news confirmation strengthens momentum.",
        "spec": "12-1 momentum retained only where the prior month's confirmed "
                "news drift agrees in sign; neutralised otherwise.",
        "status": "REFUTED",
        "result": "Net +1.30%/yr at 63d against +14.79%/yr for plain momentum — "
                  "confirmation made momentum DRAMATICALLY WORSE. Zeroing "
                  "non-agreeing names destroys the cross-sectional ranking that "
                  "carries the edge. The hypothesis as constructed is refuted.",
    },
    "HYP-018": {
        "name": "unconfirmed_momentum_control",
        "family": "information.confirmation",
        "motivation": "CONTROL for HYP-017. Without it, any result for the "
                      "confirmed variant could be a selection artefact of a "
                      "smaller, higher-momentum universe rather than evidence "
                      "that information content matters.",
        "spec": "The exact complement of HYP-017.",
        "status": "control",
        "result": "Net +9.54%/yr at 63d, BEATING the confirmed variant's +1.30%. "
                  "The control earning more than the treatment is what exposed "
                  "HYP-017 as a construction failure rather than a null result.",
    },
    "HYP-019": {
        "name": "information_intensity",
        "family": "information.intensity",
        "motivation": "Unsigned count of confirmed news events — an attention "
                      "and uncertainty measure, not a direction.",
        "spec": "Count of |gap| > 2 SD with volume confirmation over 21 sessions.",
        "status": "measured_null",
        "result": "IC -0.006 to -0.008, monotonicity ~0.0, all t < 1.2. No "
                  "standalone directional content.",
    },
    # ---------------------------------------------------------------------
    # HYP-021..023 are POST-HOC, and the record must say so. Every other entry
    # here was registered before its result existed; these three were measured
    # 2026-08-02 on signals that had ALREADY been voting on live verdicts for
    # months, discovered when the family split showed they were inheriting
    # HYP-001's credential without a spec of their own. Registering them
    # afterwards is the honest repair, but it is NOT pre-registration and their
    # results carry the selection risk that implies: the signals were built and
    # deployed by the same person now measuring them.
    #
    # Measured on the ~500-name, 2485-day panel with the platform's own
    # estimators; the 12-1 momentum control reproduced the stored study exactly
    # (IC 0.0492/0.0667/0.0694, OOS 63d +0.062), which is what validates the
    # harness. Walk-forward has only 2 folds, so a sign-agreement of 1.00 is
    # weak evidence — it is 2 coin flips. None of these are promoted above the
    # default cap on this evidence.
    "HYP-021": {
        "name": "distance_from_52w_high_continuous",
        "family": "technical.anchor_52w",
        "motivation": "The CONTINUOUS form of HYP-002. HYP-002 registers a "
                      "boolean 'within 5% of the high' cohort, which the IC "
                      "study cannot rank and therefore never measured — while "
                      "the live system emits and votes on the continuous "
                      "percentage. The traded signal was the untested one.",
        "spec": "Rank by (close / 252d rolling max − 1); higher = nearer the "
                "high. Mirrors engine.technical.pct_from_52w_high.",
        "status": "computed",
        "result": "IC +0.0396/+0.0592/+0.0756 at 21/63/126d, t=2.28/2.57/2.57; "
                  "hit rate 70.9% at 126d. Walk-forward OOS IC +0.048 at 126d, "
                  "sign stable across 2 folds. Passes t>=2 but NOT the "
                  "Harvey-Liu-Zhu |t|>=3 hurdle at any horizon.",
    },
    "HYP-022": {
        "name": "trend_vs_200dma_continuous",
        "family": "technical.trend_200dma",
        "motivation": "The CONTINUOUS form of HYP-003, unmeasured for the same "
                      "reason as HYP-021: the registered spec is a boolean "
                      "above/below cohort, the live signal is the percentage.",
        "spec": "Rank by (close / 200d rolling mean − 1). Mirrors the value "
                "returned by engine.technical.trend_200dma (`above_pct`); the "
                "21d MA slope it also reports is not the ranked quantity.",
        "status": "computed",
        "result": "The STRONGEST single signal measured on this universe: IC "
                  "+0.0340/+0.0654/+0.0945 at 21/63/126d, t=2.24/3.16/3.65, hit "
                  "rate 74.8% at 126d. Clears Harvey-Liu-Zhu |t|>=3 at 126d. "
                  "Walk-forward OOS IC +0.071 at 126d — HIGHER than 12-1 "
                  "momentum's +0.062, on 2 folds. Deliberately NOT promoted "
                  "above cap 0.25: raising influence on a post-hoc in-sample "
                  "result is the overfitting failure this registry exists to "
                  "prevent, and 2 folds is not a track record. Promotion should "
                  "come from realised forecasts, not from this entry.",
    },
    "HYP-023": {
        "name": "relative_strength_vs_index",
        "family": "technical.rel_strength",
        "motivation": "Registered to record a REDUNDANCY, not an edge. The live "
                      "system counts this as a separate piece of trend evidence.",
        "spec": "63d stock return minus 63d index return "
                "(engine.technical.relative_strength).",
        "status": "computed",
        "result": "REDUNDANT BY CONSTRUCTION. The index term is one scalar per "
                  "date, identical for every name, so subtracting it cannot "
                  "change the cross-sectional ordering: this series is "
                  "numerically identical to the raw 63d return (verified, max "
                  "abs difference 0.0000000000). Its IC (+0.0648 at 126d, "
                  "t=3.67, OOS +0.039) is therefore a measurement of 63d "
                  "momentum wearing another name, not independent evidence. "
                  "Measured rho +0.94 against sector_rel_mom, which shares the "
                  "same 63d return. The within-cluster n_eff correction "
                  "(api.live.within_cluster_effective_n) is what stops this "
                  "from counting as an independent confirming vote.",
    },
    "HYP-020": {
        "name": "momentum_within_high_information_universe",
        "family": "information.conditioning",
        "motivation": "HYP-017 failed by zeroing names. The correct form of the "
                      "same idea: use information intensity to select the "
                      "UNIVERSE, then rank momentum within it, preserving the "
                      "cross-sectional ordering.",
        "spec": "Restrict to names with >=2 confirmed news events in the prior "
                "month; rank 12-1 momentum within that subset.",
        "status": "REFUTED_OUT_OF_SAMPLE",
        "result": "IN-SAMPLE it looked outstanding: IC +0.093 vs +0.067 for the "
                  "full universe, spread t=4.08, monotone, +25.9%/yr net even "
                  "after 0.87 turnover, and robust to a 10x cost assumption "
                  "(+12%/yr at a 2.22% round trip). OUT OF SAMPLE the entire "
                  "advantage disappears: walk-forward OOS IC +0.060 against "
                  "+0.062 for plain momentum on all names. The in-sample edge "
                  "was the product of searching three universe constructions "
                  "(all / quiet / noisy). Do NOT trade this in preference to "
                  "plain momentum: it carries 4x the turnover for no "
                  "out-of-sample gain.",
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
        "family": "risk.volatility",
        "motivation": "Low-volatility anomaly check on this universe — also serves "
                      "as the price-only leg of the Fragility hypothesis.",
        "spec": "Monthly: bottom quintile of 126d realized vol; forward 126d excess "
                "vs universe median.",
        # Computed 2026-08-01 on the ~500-name panel: rank IC indistinguishable from
        # zero (t=-0.23 at 63d) while the quantile profile is perfectly INVERTED
        # (monotonicity -1.0 at 21/63/126d, net -20.2%/yr long-short, -8.1% long-only).
        # Both horizons flag tail_driven, so the magnitude is untrustworthy but the
        # sign and the ordering are consistent. No demonstrated edge; evidence leans
        # negative. Demoted rather than rejected so it stays visible and recoverable
        # if the inversion proves to be a tail artefact.
        "status": "weak",
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
        # Computed 2026-08-01 alongside HYP-008 and showing the same failure shape:
        # rank IC ~zero (t=-0.73 at 63d) with a perfectly inverted quantile profile
        # (monotonicity -1.0 at 21/63/126d, net -14.8%/yr long-short, -6.1% long-only),
        # tail_driven throughout. The Bali-Cakici-Whitelaw lottery effect does not
        # reproduce on this universe as constructed. Demoted, not rejected.
        "status": "weak",
    },
}
