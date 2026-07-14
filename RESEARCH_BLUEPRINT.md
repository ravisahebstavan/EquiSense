# EquiSense — Research & Product Blueprint (v2.0)

**Status:** Founding research blueprint. Supersedes and extends [PROJECT_DRAFT.md](PROJECT_DRAFT.md) (v1), which remains the record of the measurement-layer design and of the MVP that implements it.
**Panel:** Quantitative researcher · Buy-side equity analyst (CFA) · Principal software architect · Behavioral finance researcher · Portfolio construction specialist · ML researcher (robust financial modeling) · Systems thinker (explainability & continuous improvement).
**Scope:** Planning only. No implementation is specified here beyond contracts and invariants.

---

## How to read this document

Sections end with **Panel Challenge** blocks where members disagreed; both views and the resolution are recorded. Rejected ideas are documented with the same care as accepted ones — a blueprint that only records what was chosen hides its own reasoning. §16 consolidates every rejection; §15 is the honest risk register; §20 lists the questions we genuinely do not know the answers to.

A one-line orientation: **v1 built a calculator with a memory. v2 designs a scientist with a memory.** The v1 measurement layer (ratio engine, reverse DCF, ledger, thesis objects, grounded narration) is not discarded — it becomes Tier 0–1 of the epistemic stack defined in §2, the substrate everything else conditions on.

---

# 1. Mandate

Design the most intelligent, explainable, continuously improving **personal** equity research platform for the Indian market, supporting swing-trading (weeks–months) and medium-term investing (quarters–years), that maximizes **decision quality** rather than backtest CAGR, and that becomes measurably smarter with every decision it witnesses.

The test for every component: *does it improve the calibration, evidence-base, or repeatability of a real decision — buy what, why, when, how much, with what confidence, falsified by what?* If a component only makes the user feel more informed, it is rejected. Feeling informed and being calibrated are different states, and most existing tools sell the first while degrading the second.

## 1.1 What changed from v1, and why it is a revision rather than a betrayal

v1 drew a hard line: no signals, no recommendations, ever. That line was drawn to avoid a real failure mode — uncalibrated point forecasts dressed as advice. v2 keeps the failure mode banned but recognizes the line was drawn in the wrong place. The problem with "AI says BUY" was never that it approached a decision; it was that it was **an unfalsifiable, uncalibrated, unaccountable claim**. A statement of the form:

> "Setups sharing these measurable features have historically produced a median 6-month excess return of X% with a Y% hit rate over N=214 episodes since 2008, concentrated in easing-rate regimes; current evidence matches with these three exceptions; confidence: moderate (0.58–0.66 band, per this system's own calibration record); the thesis is invalidated if A, B, or C."

— is not fortune-telling. It is **reference-class forecasting** (the Kahneman/Lovallo "outside view"), the single most defensible forecasting technology behavioral science has produced, and it is falsifiable, scoreable, and improvable. v2's central move is to permit exactly this class of claim and nothing beyond it.

Concretely revised from v1: (a) recommendation boundary — from "never" to "calibrated conditional claims with mandatory decomposition, confidence, and invalidation"; (b) technical and flow evidence admitted, subject to the validation gauntlet of §10; (c) backtesting/validation infrastructure moves from out-of-scope to load-bearing; (d) personalization upgraded from profile-driven re-ranking to a full adaptive investor model; (e) a feedback architecture added that scores every claim the system ever makes. Unchanged from v1: explainability as architecture, the LLM-never-originates-a-number rule, bounded universe, transaction-ledger portfolio truth, boring infrastructure, single user.

---

# 2. The Epistemic Contract

This is the platform's constitution. Every output the system produces is a **Claim object** tagged with exactly one tier. The tier determines what the system is allowed to say, how the UI must render it, and how it will later be scored.

| Tier | Name | Example | Verification | Scored later? |
|---|---|---|---|---|
| **T0** | Measurement | "FY25 ROIC was 18.7% (formula, inputs, filing)" | Deterministic; unit-tested against hand-verified values | No — correct by construction |
| **T1** | Contextualization | "That ROIC is in the 84th percentile of its 10-year history and 2nd among its 5 curated peers" | Deterministic given T0 + reference sets | No |
| **T2** | Base rate | "Companies entering the top ROIC quintile with falling debt have shown +4.1% median 12-mo excess return, hit rate 61%, N=178, IQR −9% to +21%" | Point-in-time event study; sample size, CI, and regime split always attached | Yes — the base rate itself is monitored for drift |
| **T3** | Synthesis judgment | "Weighing the evidence, this is a moderate-conviction long candidate at ≤3% of book; key disagreement: valuation engine dissents" | Aggregation rules are inspectable; confidence comes from the calibration ledger, not intuition | Yes — Brier-scored at horizon expiry |
| **T4** | *Forbidden* | Unconditional price targets, "AI says BUY", certainty language, black-box scores, any number without provenance | — | — |

**Contract invariants:**
1. **No naked claims.** Every claim carries provenance (data snapshot ID, engine version, config hash), an expiry horizon, and — for T2/T3 — the sample size and calibration cell it draws on.
2. **Abstention is a first-class output.** "No detectable edge" and "too hard — insufficient data" are legitimate, expected, and *most common* answers. A system that always has an opinion is lying about some of them.
3. **Pre-registration.** T3 claims are locked into an append-only ledger at decision time. They cannot be edited, only superseded by a new claim that references the old one. The system may never quietly forget being wrong.
4. **Confidence is earned, not asserted.** The numeric confidence attached to any T3 claim is a function of the system's own historical calibration in the relevant cell (§12), evidence dispersion, base-rate sample sizes, and data completeness — a decomposable computation, never a vibe. Until enough claims have been scored, confidence displays as "uncalibrated — provisional" with the components shown.
5. **The human is the portfolio manager.** The system is a research department, a risk desk, and an honest scorekeeper. It never acts; it prepares decisions and audits them.

> **Panel Challenge — Quant vs. Analyst.** *Quant:* T2 base rates on Indian data will have painfully wide intervals; publishing IQRs like "−9% to +21%" will feel useless to the user. *Analyst:* That interval **is the information**. Retail tools manufacture false precision; institutional decision-making runs on exactly these wide, honest distributions plus position sizing that respects them. *Resolution:* publish the width, and let the sizing engine (§9) translate width into smaller positions — uncertainty gets priced, not hidden. *Behavioral researcher's addendum:* wide intervals also inoculate against the single most dangerous product failure — rigor theater (§15.6) — because nothing punctures false confidence like a visible 30-point IQR.

---

# 3. Autopsy of the Existing Category

Studied conceptually, not imitated. What each does well, where each is structurally unable to go, and the lesson taken.

| Product class | Genuinely good at | Structural ceiling | Lesson taken |
|---|---|---|---|
| **Screener.in** | Raw Indian fundamentals depth; a real query language; credibility with serious investors | Shows you ROCE; cannot tell you whether ROCE screens have ever *worked*, for whom, in what regime. No memory, no calibration, no loop | Data depth is table stakes; the differentiator is attaching an evidence record to every number class |
| **TradingView** | Charting ergonomics; alert infrastructure; community-scripted indicators | Indicators are folklore with no admission standard; the social feed is a bias amplifier (herding, recency) | Adopt the ergonomics of marking up price; subject every indicator to an event-study gauntlet before it may appear |
| **TIKR / Koyfin** | Institutional data presentation for individuals; estimates, transcripts | Terminal-shaped: data in, judgment out, nothing closes the loop between what you saw and what happened | Presentation ≠ decision support; the loop is the product |
| **Bloomberg / AlphaSense** | Breadth, speed, document search/NLP at scale | Sell information advantage, not judgment advantage; consensus features (ANR) institutionalize herding | Steal the retrieval concept for a personal corpus; reject the consensus reflex |
| **Trendlyne / StockEdge** | India-specific aggregation (deals, SAST, results calendars); composite "DVM"-style scores | The composite score is the canonical anti-pattern: opaque weights, no calibration record, no falsifiability, no accountability when wrong | Their data aggregation list is a good ingestion checklist; their scoring approach is the negative example for §8 |
| **Generic AI assistants** | Fluent synthesis; low activation energy | Uncalibrated priors narrated confidently; hallucinated numbers; zero accountability ledger | LLMs are interfaces and extractors under a grounding validator — never oracles (v1 already enforces this) |
| **A good analyst's own spreadsheet + notebook** | Total flexibility; the actual incumbent | No versioning of belief, no automated scoring, decays with attention | This remains the real competitor. EquiSense must beat "a disciplined person with Excel and a diary" — a higher bar than any listed product |

**What *nothing* in the category does — the opportunity map (§ references to where each is designed):**

1. Track whether its own outputs were right, publicly to its user (calibration ledger, §12).
2. Model the user — biases, decision quality, overrides — and hold up a mirror (§11).
3. Distinguish process quality from outcome quality (§11.3).
4. Pre-register theses and claims in an append-only, tamper-evident record (§2, §13.4).
5. Attach live historical base rates to evidence ("this pattern, in this regime: N, hit rate, distribution") (§7, §10).
6. Advise **position size** with shown work — the decision retail tools universally dodge (§9).
7. Output abstention and maintain a "too hard" pile (§2, §8.5).
8. Maintain a hypothesis registry including *failed* research, with multiple-testing discipline (§10.5).
9. Compute after-tax, after-cost, after-slippage breakeven for the *user's actual tax situation* before showing any edge (§9.4).
10. Audit the user's counterfactuals: "your overrides of the system this year cost/earned you X" (§11.4).
11. A personal bitemporal data time machine — what was knowable when (§6.2).
12. Willingness to conclude "you have no measurable edge; index" — the kill criterion (§18).

---

# 4. System Overview — Five Planes

```
┌────────────────────────────────────────────────────────────────────┐
│  V. LEARNING PLANE      claim scoring · calibration ledgers ·      │
│     (§11–12)            trust accounting · investor model ·        │
│                         thesis autopsies · hypothesis registry     │
├────────────────────────────────────────────────────────────────────┤
│  IV. DECISION PLANE     dossiers · sizing & capital allocation ·   │
│      (§8–9)             portfolio construction · execution plans · │
│                         abstention / too-hard pile                 │
├────────────────────────────────────────────────────────────────────┤
│  III. SYNTHESIS PLANE   evidence aggregation · disagreement        │
│       (§8)              surfacing · adversarial critique ·         │
│                         confidence computation                     │
├────────────────────────────────────────────────────────────────────┤
│  II. EVIDENCE PLANE     engines: fundamental · quality ·           │
│      (§7)               valuation · technical · flow/behavior ·    │
│                         macro-regime · risk · portfolio-fit ·      │
│                         research/NLP  — all emit Evidence objects  │
├────────────────────────────────────────────────────────────────────┤
│  I. DATA PLANE          bitemporal lake · ingestion adapters ·     │
│     (§6)                quality gates · corporate-action engine ·  │
│                         PIT snapshots · reference data             │
└────────────────────────────────────────────────────────────────────┘
```

Strict downward-only dependencies. The Learning plane writes *parameters* (calibrated weights, trust scores, investor-model state) that upper planes read as versioned configuration — it never reaches into their internals. This single rule is what keeps a decade of accretion from becoming a ball of mud: any engine, any aggregator, any model can be replaced if it honors its plane's contract.

---

# 5. Design Values (inherited, extended)

From v1, unchanged: explainability as architecture; deterministic engines as sole source of numbers; bounded universe (≈300–500 names: holdings, watchlist, NIFTY 500 core, curated peers — revisit trigger unchanged); single relational operational store; no microservices; no real-time. Extended for v2: **bitemporality is non-negotiable** (§6.2); **every hypothesis is registered before it is tested** (§10.5); **sample-size gates govern feature activation** (§17) — features that need data the system doesn't yet have remain visibly dormant rather than running on noise.

---

# 6. Data Plane

## 6.1 The central problem: point-in-time truth in India

Backtest validity dies at the data layer, not the model layer. Indian data sources overwhelmingly serve **restated, latest-known** figures. A screen "computed on FY19 fundamentals" using today's data secretly uses numbers revised in FY21 — look-ahead bias that inflates every fundamental signal. Three consequences drive the whole data design:

1. **Start the time machine now.** Genuine PIT data cannot be bought retroactively at personal-project cost; it can only be *accumulated*. From day one, every ingested artifact (filing, announcement, shareholding pattern, price file, transcript) is stored with two timestamps: `event_time` (what period it describes) and `knowledge_time` (when it became knowable — filing/announcement timestamp). Every historical query passes an `as_of` knowledge date. The archive's research value compounds with calendar time and cannot be accelerated — which makes it the highest-priority build item in the entire blueprint.
2. **Backfilled history is second-class.** Pre-archive history (bought or scraped restated data) is stored but *flagged* `pit_grade: reconstructed`, and every T2 base rate computed from it carries a mandatory caveat. Where possible, filing dates are reconstructed from exchange archives to approximate knowledge_time.
3. **Survivorship is handled by index-membership history and delisting registry.** Universe definitions are themselves time-series ("NIFTY 500 constituents as of date D"). A delisted-company registry (with delisting reason: merger, insolvency, voluntary) is maintained so cross-sectional studies include the dead. Where fundamentals for dead companies are unobtainable, studies report coverage and bound the bias rather than ignoring it.

## 6.2 Storage architecture — compared alternatives

| Option | Verdict | Reasoning |
|---|---|---|
| Everything in the operational relational DB | **Rejected** | Analytical scans (20y × 500 names × daily × dozens of fields) and bitemporal semantics pollute the app schema; research queries deserve columnar performance |
| Full lakehouse (Spark/Iceberg/dbt stack) | **Rejected** | Resume-keyword architecture; three orders of magnitude beyond the data volume; violates the boring-infrastructure value |
| **Two-store: columnar research lake (partitioned Parquet + DuckDB) + relational operational DB** | **Chosen** | Total data volume over a decade is tens of GB — a laptop-scale problem. Parquet partitions by dataset/year; DuckDB gives columnar SQL over it with zero servers; the operational DB (SQLite→Postgres when warranted) keeps the app transactional. Offline-first falls out for free |
| Bitemporal DBMS (XTDB-style) | **Seriously considered, rejected** | Elegant, but an exotic dependency for a 10-year solo project; bitemporality is achievable as a *convention* (two timestamp columns + `as_of` query helpers + a leak-detector test harness, §13.6) on boring storage |

Immutable ingestion: raw artifacts are content-hashed and never mutated; normalization produces new versions. A `snapshot_id` (manifest of dataset versions) pins every research result and every dossier to the exact data it saw — reproducibility by construction.

## 6.3 Dataset plan

Cadence EOD/event-driven throughout; intraday *extensibility* is honored by making bar-size a schema parameter, and honored no further (no intraday features are designed — see §16).

| Dataset | Source class | Cadence | PIT difficulty | Primary consumers |
|---|---|---|---|---|
| Daily OHLCV + **delivery %** | Exchange bhavcopies (public) | Daily | Low (archive daily) | Technical, Flow, Risk |
| Corporate actions (splits, bonuses, dividends, buybacks, **demergers**) | Exchange disclosures | Event | Medium — demergers are the hard case | Everything (adjustment engine) |
| Financial statements, quarterly results | Filings/XBRL | Quarterly | **High** — the restatement problem; archive at announcement | Fundamental, Quality, Valuation |
| Shareholding pattern (promoter/FII/DII/MF/retail buckets) | Quarterly filings | Quarterly | Medium | Flow/Behavior |
| Promoter pledge disclosures | Event filings | Event | Medium | Quality, Risk |
| Insider trades (PIT/SAST disclosures) | Exchange feeds | Event | Medium | Flow/Behavior |
| Bulk/block deals | Exchange daily files | Daily | Low | Flow/Behavior |
| FII/DII aggregate flows | Exchange/depository | Daily | Low | Macro-Regime |
| Mutual fund monthly portfolios | AMC disclosures | Monthly | Medium | Flow/Behavior |
| Index constituents & weights (history) | Index provider announcements | Event | Medium — must archive reconstitutions | Universe/survivorship, Portfolio |
| Macro: repo rate, CPI, 10y G-sec, INR, crude, India VIX | RBI/public | Daily–monthly | Low | Macro-Regime |
| Corporate announcements | Exchange feeds | Event | Low (timestamped at source) | Research/NLP, Risk (event calendar) |
| Earnings-call transcripts | Exchange filings (mandated recent years) / IR pages | Quarterly | Medium; sparse pre-2021 | Research/NLP |
| Annual reports (MD&A, auditor notes, RPTs) | Filings | Annual | Low | Research/NLP, Quality |
| News | Licensed/RSS — **narrow scope** | Daily | High (archives are licensing quicksand) | Research/NLP (entity-tagged headlines only) |
| Results calendar, budget/election dates | Exchange + manual | Event | Low | Risk (event calendar) |

Legality constraint inherited from v1 §7.10: public regulatory disclosures and licensed feeds only; no adversarial scraping. Each source gets an isolated adapter (source death is a *when*, not an *if*, over ten years — §15.9).

## 6.4 Data quality as a subsystem

Every ingestion batch passes gates: schema validation; accounting identities (assets = liabilities + equity within tolerance); cross-source reconciliation (shares outstanding from filings vs. corporate-action ledger); plausibility bands (a ratio outside sane range flags parse error before it flags "insight"); staleness monitors per dataset. Failures quarantine the batch — bad data never silently enters the lake. A data-quality dashboard is a first-class screen: **the user should trust the system partly because the system visibly distrusts its own inputs.** The corporate-action adjustment engine is designated the single most defect-dangerous component in the platform (a missed bonus issue fabricates a −50% return) and gets golden-file regression tests against known-messy cases (demergers especially).

> **Panel Challenge — Architect vs. Quant.** *Quant:* buy a commercial PIT database and skip the archive-building years. *Architect:* institutional PIT vendors are priced for funds, not persons; and a purchased feed still dies someday, leaving no in-house discipline. *Resolution:* build the archive as designed; opportunistically backfill with purchased/reconstructed data flagged `reconstructed`. The archive discipline is also, frankly, among the strongest engineering-judgment artifacts in the whole project for the portfolio/admissions narrative.

---

# 7. Evidence Plane

## 7.1 The Evidence object — the platform's core contract

Every engine emits only this (conceptually; exact schema is implementation detail):

```
Evidence {
  id, engine, engine_version, company, as_of_snapshot
  claim_tier            # T0 | T1 | T2
  direction             # supports_long | supports_short/avoid | neutral | flag
  horizon_class         # weeks | months | quarters | years
  statement             # human-readable, self-contained
  quantities            # the numbers, each with provenance (v1 Metric objects)
  base_rate             # for T2: {N, hit_rate, outcome_distribution, regime_split,
                        #          pit_grade, hypothesis_registry_ref}
  calibration_cell      # (evidence_family, horizon, regime, cap_band) key into trust ledger
  correlation_cluster   # which evidence family it belongs to (double-count control, §8.3)
  staleness, expiry
  caveats[]             # mandatory, engine-authored
}
```

Two properties do the heavy lifting: `base_rate.hypothesis_registry_ref` means no T2 evidence exists without a registered, validated study behind it (§10.5); `correlation_cluster` means the synthesis layer can avoid counting the same underlying phenomenon five times because five engines noticed it.

## 7.2 Engine roster

The brief's proposed roster (fundamental, quality, technical, behaviour, macro, valuation, portfolio, risk, capital-allocation, confidence, research) is largely adopted, with three deliberate deviations: **capital allocation and confidence are not engines** — sizing is a Decision-plane function (§9) and confidence is a computed property of synthesis (§8.4); making them "engines" would let them originate opinions without evidence discipline. The **research engine** is recast as the NLP/document subsystem feeding other engines plus the corpus. One engine is added: **portfolio-fit** is split from risk because "is this a good idea" and "is this a good idea *for this book*" are different questions with different data.

| Engine | Core outputs (all as Evidence) | Distinctive design commitments |
|---|---|---|
| **Fundamental** | Growth/margin/returns trajectories, reinvestment economics (ROIC−WACC), earnings quality (accruals, CFO/NI), balance-sheet trends — v1's engine, now emitting T1 context and T2 base rates ("improving-ROIC + deleveraging cohort behavior") | PIT-safe by construction; consolidated/standalone both first-class |
| **Quality** | Composite from published methodologies (Piotroski-class), governance flags (pledges, RPTs, auditor changes, resignations), India-specific red-flag library | Published methodologies only; the India governance red-flag library is proprietary *data assembly*, not proprietary *scoring* |
| **Valuation** | Reverse-DCF implied expectations vs. delivered history (v1); multiple-vs-history/peers percentiles; **expectations-gap** as the headline quantity; T2: subsequent-return distributions conditional on expectations-gap deciles | Never "fair value"; always "what's priced in, and what happened historically from similar pricing" |
| **Technical** | Small, validated roster: 12-1 momentum, 52-week-high proximity, 200DMA trend regime, volatility contraction, relative strength vs. sector, volume/delivery confirmation | Every indicator passes the §10 gauntlet before admission; the roster is expected to be *small*; folklore indicators die in the hypothesis registry, visibly |
| **Flow/Behavior (of others)** | Promoter buying/selling, pledge deltas, insider clusters, bulk/block footprints (with acquirer classification), MF ownership deltas, retail-bucket shareholding trend, delivery % anomalies, FII/DII context | The most India-differentiated engine; promoter-action signals in mid-caps are the single most promising under-exploited evidence family (§20 Q2) |
| **Macro-Regime** | Regime *description*: rate-cycle direction, liquidity, INR/crude trends, FII flow regime, breadth, VIX percentile → coarse regime label (2–4 states) with probabilities | Regimes are **conditioning variables for base rates**, not timing signals. Regime *prediction* is explicitly out of scope. Honesty constraint: ~25 years of data contains maybe 8–10 regime episodes — coarse states or nothing |
| **Risk** | Thesis risk (invalidation triggers armed and machine-checked), position risk (stop distance ⇒ R), event risk (results dates, budget, elections from calendar), liquidity risk (days-to-exit at ≤10% ADV, circuit-limit exposure), tail context (drawdown state, gap history) | Consumes the investor model (§11): **the user's current behavioral state is a risk input** — e.g., "3 consecutive losses closed this week: revenge-trading conditions" |
| **Portfolio-fit** | Marginal contribution to concentration axes (v1's four axes), factor-exposure deltas (measured vs. simple Indian factor proxies), correlation-cluster membership, tax-lot interactions, cash posture | Correlation claims carry instability caveats by default (correlations are regime-dependent and estimated with error — say so) |
| **Research/NLP** | Verifiable extractions from transcripts/reports: guidance figures, capex plans, one-off explanations, auditor qualifications, pledge/RPT mentions; tone-delta vs. prior quarter; retrieval over the personal corpus | Extraction only, span-cited, grounding-validated (extends v1's validator); tone-delta is admitted as evidence *only if* validated against subsequent revisions/returns (§20 Q3) — else it remains a reading aid |

## 7.3 The "What am I missing?" subsystem

Answering the brief's hardest question is a *product feature with four mechanical parts*, attached to every dossier:

1. **Coverage report** — which evidence families produced nothing because data is absent (no transcript ingested; pledge data stale) — distinguishing "checked, clean" from "never checked."
2. **Disconfirming-evidence search** — the system runs the *negative* checklist explicitly (receivables outrunning revenue? pledge creep? margin dependent on one segment? auditor language shifts?) and reports what it looked for and found/didn't.
3. **Reference-class postmortems** — nearest historical setups *that failed*, with their post-hoc failure causes from the autopsy library (§12.4). The outside view applied to failure.
4. **Staleness map** — the age of every input surface.

---

# 8. Synthesis Plane

## 8.1 Architectures considered

| # | Architecture | Verdict | Core reasoning |
|---|---|---|---|
| A | Monolithic weighted composite score | **Rejected** | Arbitrary weights; hides disagreement; unfalsifiable as a whole; the Trendlyne anti-pattern. The brief demands better and is right to |
| B | Sequential filter pipeline (screen → rank → pick) | **Rejected as the core** (retained as an *idea-sourcing* front-end) | Order-dependence smuggles in weights; early filters silently discard information later stages needed |
| C | End-to-end ML (features → return prediction) | **Rejected** | Data-starved at every level that matters (§14); unexplainable at T3; optimizes the wrong objective (prediction, not decision quality) |
| D | LLM multi-agent debate committee | **Rejected** | Non-reproducible, non-scoreable synthesis; eloquence-weighted rather than evidence-weighted; cost and drift; "debate theater" risks laundering T4 claims through dialogue. LLMs remain confined to narration/extraction |
| E | Independent evidence engines + **mechanical, calibrated aggregation** (blackboard) | Strong | Preserves engine independence, surfaces disagreement, aggregation is inspectable and improvable from the calibration ledger |
| F | **E + adversarial critique pass + human as final synthesizer ("centaur analyst")** | **Chosen** | Adds cheap, high-value red-teaming; and is honest about validation limits — full automation cannot be statistically validated at a personal decision rate (§15.3), but evidence quality-control and calibration *can*. The human remains the PM; the system makes human judgment auditable |

## 8.2 Why "centaur" is a reasoned conclusion, not a compromise

A fully automated strategy needs enough independent decisions to distinguish skill from luck — at 20–60 real decisions/year, a decade yields a few hundred, marginal for even one strategy's significance. But *evidence calibration* pools across the whole universe and all paper dossiers (§17 Gate 3 runs dossiers on hundreds of names regardless of user action), reaching statistical maturity years earlier. So the system can honestly learn "which evidence, when, how much" long before it could honestly learn "trade this autonomously." Placing the human at the point of final synthesis is therefore the *statistically correct* location for the least-validatable component — with the crucial addition that the human's syntheses are themselves logged, scored, and calibrated (§11). Neither human nor machine is trusted; both are measured.

## 8.3 Aggregation mechanics (mechanical, monotone, inspectable)

- Evidence is grouped by `correlation_cluster`; within-cluster contributions are combined first (a momentum signal echoed by three engines counts roughly once). Cluster correlation is *measured* on history, versioned, and haircut accordingly.
- Cluster contributions combine on a log-odds-like scale with weights = **shrunken empirical reliabilities** from the trust ledger (§12.2): hierarchical shrinkage toward the evidence-family prior when the (horizon, regime, cap-band) cell is thin; weights are capped (no single family may dominate); all weights visible in the dossier with their sample sizes.
- **Disagreement is output, not noise:** a dispersion statistic plus the named dissent ("valuation engine dissents; historically, when valuation dissents from an otherwise-long consensus in this regime, outcomes were X") — dissent patterns are themselves a T2 evidence family once sample permits.
- **Adversarial critique pass:** a rules-first red team (falsification checklist: which single evidence removal flips the synthesis? which assumption has the widest CI? base-rate contradictions?) optionally narrated by the LLM under grounding constraints. Output: "strongest case against," mandatory in every dossier.
- Aggregation parameters change **only** via the Learning plane's versioned releases with validation cards (§12.3) — never hand-tuned mid-flight.

## 8.4 Confidence computation

Confidence attached to a T3 synthesis = decomposable function of: (i) calibration record of this synthesis class in this cell; (ii) evidence dispersion; (iii) minimum effective sample size across load-bearing base rates; (iv) data completeness from the coverage report; (v) regime familiarity (distance from historical regime centroids). Displayed as a band with the five components shown. Verbal labels map to fixed numeric bands and are periodically re-anchored to realized calibration — words are not allowed to drift from numbers.

## 8.5 The Decision Dossier (the only recommendation artifact)

Sections, all mandatory (empty sections say "insufficient data — see coverage report"): identification & horizon class · headline synthesis (direction, conviction band, abstain/too-hard allowed) · the thirteen evidence sections required by the brief (fundamental, valuation, technical, quality, macro, behavioral/flow, risk, portfolio-fit, historical base rates, confidence decomposition, known weaknesses, alternative interpretations, missing information) · falsifiable assumptions + armed invalidation triggers · sizing recommendation with shown work (§9) · execution plan (§9.5) · after-tax/after-cost breakeven math · pre-registration hash + snapshot ID + engine versions · scheduled scoring dates.

---

# 9. Decision Plane — Sizing, Portfolio, Execution

Position size is where retail platforms universally abdicate and where most retail wealth destruction actually happens. EquiSense treats sizing as a first-class advised quantity with shown work.

## 9.1 Sizing framework

Fractional Kelly as the organizing skeleton, heavily disciplined for estimation error: edge and variance inputs come from T2 outcome distributions (never point estimates); a base fraction of Kelly (quarter-to-half) reflects the deep literature on Kelly's catastrophic sensitivity to overestimated edge; an **uncertainty haircut** scales size down proportional to the width of the edge CI and down hard when calibration cells are thin; then binding constraints: max single-position and sector caps from the investor policy, **portfolio heat** (sum of open R at stops ≤ drawdown budget), correlation-cluster caps, liquidity cap (position ≤ what exits in ~3 days at ≤10% ADV), and the tax-cliff adjustment (§9.4). Output: recommended size *with every term of the computation displayed*, plus the sensitivity ("at half the estimated edge, size falls to Y").

## 9.2 Portfolio construction

Deliberately not a black-box optimizer. Mean-variance optimization is **rejected** as the driver (error-maximizing at these estimation qualities; unstable weights would destroy user trust and be right to). Chosen: constraint-based construction — the v1 four-axis concentration limits, factor-exposure bounds vs. simple Indian factor proxies, correlation-cluster budgets, heat budget, explicit cash posture with regime-dependent hurdle commentary (T1/T2 only — cash advice must clear the same evidence bar). The portfolio-fit engine renders any candidate's marginal effect on all of these before sizing is finalized.

## 9.3 Swing module — the Setup Library

Swing trading is admitted **only** through named, versioned setup classes — e.g., post-earnings-announcement drift (well-documented in India), 52-week-high breakout with delivery confirmation, volatility-contraction continuation. Each setup carries: precise machine-checkable entry conditions; exit/stop discipline; a live base-rate table (N, hit rate, payoff distribution, by regime/cap band, after costs); a status lifecycle (**experimental → validated → live → decaying → retired**) driven by the hypothesis registry and ongoing forward performance; and expectancy math that must clear the after-tax/after-cost bar before a dossier may cite it. Discretionary swing trades outside the library are permitted (the human is the PM) but are *labeled unclassified* and tracked as their own cohort — over time the user learns whether their improvisations beat their library. Expected honest outcome: the library stays small; most candidate setups die in validation; the deaths are displayed as proudly as the survivors.

## 9.4 Taxes and costs as first-class physics (India-specific)

Round-trip delivery costs (STT both sides, stamp duty, exchange/SEBI charges, brokerage, GST) plus impact cost estimated from ADV participation and exchange-published impact-cost statistics: realistically ~0.3–0.6% for liquid large caps and multiples of that in mid/small caps. Layer the STCG(20%)/LTCG(12.5% above the annual exemption) cliff at 12 months and the conclusion is structural: **a marginal swing idea must clear a hurdle several points higher than the identical idea held past the cliff.** Every dossier computes breakeven hit-rate and shows the swing-vs-hold-tax comparison. The panel expects this feature alone to kill a majority of swing candidates — that is the feature working.

## 9.5 Execution planning (not execution)

EOD cadence decision support: staged-entry plans (tranche on confirmation), pre-committed stop and review levels written into the pre-registration record (Ulysses contracts — behavioral design, §11.5), event-calendar awareness (entering two days before results is a *choice* the dossier must flag), and gap-risk framing for stops (Indian circuit limits make "the stop will fill" an assumption, not a fact — say so). No order routing, ever (v1 boundary 7.1 stands).

---

# 10. Validation & Backtesting Doctrine

The objective is robustness, not historical CAGR. The doctrine is a **hierarchy** — cheap, high-sample methods validate components; expensive, low-sample methods are reserved for what survives.

## 10.1 The hierarchy

1. **Event studies** (per evidence type): cumulative abnormal returns vs. matched controls (sector/size-matched) around evidence events, PIT-safe, with regime/cap-band splits. Cheap, thousands of samples, the workhorse. This — not portfolio backtesting — is how evidence earns its T2 base-rate table.
2. **Cross-sectional information analysis** (per ranking signal): rank-IC by period with autocorrelation-robust errors, decay profiles, turnover implications, sector-neutralized variants.
3. **Portfolio-level walk-forward** (only for setup classes and allocation policies that survived 1–2): expanding/rolling windows, parameters frozen out-of-sample, all costs/taxes/slippage applied, capacity-realistic fills.
4. **Forward paper validation** — the final gate for everything: the system runs its dossiers forward in real time on the full universe before and alongside real capital. *The only backtest that cannot be overfit is the future.*

## 10.2 Bias controls (each a named test-harness invariant, not a hope)

Look-ahead: all studies run through the `as_of` API; a **leak-detector harness** perturbs knowledge_time and asserts result invariance/variance in the correct direction (§13.6). Survivorship: universe-as-of-date from index history + delisting registry; studies report dead-company coverage. Restatement: PIT grades propagate into every result caveat. Costs/taxes/slippage: mandatory, with pessimistic defaults; a result that only survives optimistic costs is recorded as a failure. Data snooping: §10.5.

## 10.3 Robustness matrix

Nothing is "validated" on a single aggregate number. Every study reports across: regimes × cap bands × sectors × sub-periods (including 2008, 2013 taper, 2016 demonetization, 2018 mid-cap crash, 2020 COVID, 2021–22 froth-and-unwind) × parameter neighborhoods (a signal that works only at lookback=12 but not 10 or 14 is noise) × execution-lag sensitivity (t+1 vs t+2). Fragility anywhere is reported *on the evidence object itself* as a caveat.

## 10.4 Regime analysis discipline

Regimes condition base rates but the number of independent regime episodes in the available history is single-digit — so: coarse regime definitions fixed *ex ante* (registered like any hypothesis), no regime-mining, and hierarchical shrinkage so thin regime cells borrow strength from the unconditional estimate rather than inventing regime-specific magic.

## 10.5 The Hypothesis Registry and multiple-testing control

Every hypothesis is registered **before** testing: motivation (mechanism, prior literature), exact specification, sample, success criteria. Results — especially nulls — are permanent records. Family-wise false discovery is controlled with Benjamini–Hochberg FDR across each research family, and headline performance claims are deflated for the number of trials (deflated-Sharpe-style reasoning). The registry does double duty: it is the platform's scientific conscience, and it is the single most credible artifact the project can show a quant interviewer — *a personal researcher with a registered-hypothesis discipline is rarer than one with a good backtest.*

> **Panel Challenge — ML researcher vs. Quant.** *ML:* pre-registration is too rigid for exploration; you'll strangle creativity. *Quant:* explore freely in a sandbox, but the rule is about *admission*: nothing enters the Evidence plane without a registered, FDR-disciplined study behind it. Exploration is cheap; belief is expensive. *Resolution:* two-tier research workflow — unregistered exploration is allowed and even logged informally, but T2 status requires registration and out-of-sample confirmation on data untouched during exploration.

---

# 11. The Adaptive Investor Model

The defining innovation the brief demands. Design principle: **model the investor with the same evidence discipline as a stock** — measured signatures, base rates, confidence intervals, and the subject's right to contest.

## 11.1 Dual representation

- **Stated policy** — an explicit, versioned Investment Policy Statement: horizon, drawdown tolerance, position/sector caps, tax preferences, liquidity needs, sizing rules, objectives. Editable only deliberately, with a diary note (policy churn is itself a measured behavior).
- **Revealed behavior** — measured from the decision ledger: what the user actually does, which the system compares against the stated policy, surfacing divergence *as information, never as silent override* (v1 §12.4's consent principle stands: the system proposes reconciliation; the user disposes).

## 11.2 Measured bias signatures (each an established construct with a statistical estimator, sample-size gated)

| Bias | Signature measured | Established basis |
|---|---|---|
| Disposition effect | Proportion of gains realized vs. losses realized; sell-hazard conditional on sign of P&L | Odean's PGR/PLR methodology |
| Overtrading / hot-hand | Trade intensity conditional on recent wins; turnover vs. stated policy | Barber & Odean |
| Loss-reaction | Behavior after drawdowns: freeze, revenge-size, or plan-adherence | Prospect-theoretic asymmetry |
| Recency/FOMO | Buy propensity conditional on trailing N-day runup of the bought stock | Extrapolative-beliefs literature |
| Home/sector bias | Concentration vs. opportunity set, persistent over rebalances | Familiarity bias |
| Sizing inconsistency | Dispersion of realized sizes vs. policy sizes, conditional on narrative excitement (journal sentiment) | — |
| Planning fallacy | Thesis review dates vs. actual review behavior; "temporary" positions' realized half-lives | — |
| Overconfidence | Calibration curve of the user's own elicited probabilities (§11.5) | Calibration literature |

Each signature reports with a confidence interval and activates only past a minimum sample (§17). The rendering surface is the **Behavioral Mirror**: descriptive, private, contestable, never gamified, never nagging — a mirror, not a nanny. (Ethics: all of this is self-surveillance by consent, local-only data; the model's contents are always fully inspectable — an opaque model of *the user* would be the platform's most corrosive possible hypocrisy.)

## 11.3 Decision quality ≠ outcome quality

The ledger scores **process** separately from **results**: was the dossier's evidence complete at decision time? were policy constraints honored? was sizing per framework? was the exit per plan or improvised? Good decisions with bad outcomes and bad decisions with good outcomes are both first-class categories — over years, the process/outcome correlation itself becomes a measured, humbling quantity. This distinction — standard in institutional post-mortems, absent in every retail product — is arguably the platform's philosophical center.

## 11.4 The Override & Counterfactual Ledger

Every divergence between dossier and action is logged with the user's stated reason, then scored at horizon: cumulative P&L of overrides vs. dossier-following, decomposed by override type. Simultaneously, standing counterfactual baselines run silently: the index, the equal-weight universe, "your portfolio with system sizing," "your portfolio with plan-adherent exits." Result: an evidence-based answer to *"where exactly do I add or destroy value relative to my own system?"* — selection? timing? sizing? exits? — the personal analog of institutional performance attribution.

## 11.5 Elicitation as behavioral instrument design

The journal and thesis flow *elicit* scoreable beliefs at decision time: probability estimates on invalidation triggers, expected holding period, "what would make me sell early." These feed the user's calibration curve (§11.2) and create Ulysses contracts (pre-committed stops/reviews that the future, emotional self must actively and visibly break rather than passively forget). The design challenge — eliciting honest probabilities from oneself without gaming — is a genuine open question (§20 Q6).

---

# 12. Self-Improvement Architecture

## 12.1 Claim scoring

Every T2/T3 claim carries scheduled scoring dates. Directional claims → Brier scores and calibration curves; distributional claims → realized-vs-predicted quantile checks (CRPS-flavored); invalidation triggers → did they fire before material drawdown (the *early-warning value* of triggers is itself measured). Scoring is automatic, append-only, and rendered in a Calibration Report the user is shown quarterly whether they like it or not.

## 12.2 Trust accounting

Per-engine, per-evidence-family ledgers keyed by (horizon, regime, cap band) with hierarchical empirical-Bayes shrinkage: thin cells display "insufficient evidence (n=7)" rather than a fake number; rich cells earn differentiated weights that flow — versioned — into §8.3 aggregation. Trust decays on staleness: an evidence family unvalidated on recent data loses weight autonomously. Models rot; the system assumes it.

## 12.3 Model cards & scheduled revalidation

Every learned or calibrated artifact (aggregation weight set, setup base-rate table, calibration model, extraction model) ships with a validation card: training/validation spans, OOS results, robustness matrix, known failure modes, expiry date. Expired artifacts revalidate or demote automatically. Drift monitors watch feature distributions and alert on regime novelty.

## 12.4 Thesis autopsies & the failure library

Every resolved thesis (confirmed/invalidated/abandoned) gets a structured autopsy: which assumption broke, was it knowable earlier, which evidence family was silent when it shouldn't have been, what trigger should have existed. Autopsies accumulate into the failure library that powers §7.3's reference-class postmortems — the system's institutional memory of its own mistakes, the compounding asset that makes year-five EquiSense categorically smarter than year-one EquiSense.

## 12.5 Knowledge compounding

The research corpus (journal, dossiers, autopsies, transcripts, annotations) is embedded for retrieval; every new dossier automatically surfaces the user's own prior contact with the name/sector/setup ("you wrote three notes on this in 2027; your last thesis here was invalidated by margin compression"). A personal Zettelkasten with an audit trail — memory as a feature, not a byproduct.

---

# 13. Engineering Blueprint

## 13.1 Macro-architecture

**Layered modular monolith** (planes of §4 as enforced package layers) — microservices rejected again with the same reasoning as v1 §16.3, now with more force: reproducibility (§13.5) is radically simpler in one process. Language: Python core (scientific ecosystem gravity); performance hot-spots isolated behind interfaces (a Rust/DuckDB escape hatch exists but is not budgeted — data volumes make it unlikely to be needed).

## 13.2 Plugin contracts

Engines, ingestion adapters, setup classes, and bias detectors are all plugins registered against small stable ABCs (`declare() → capabilities/data needs`, `emit(as_of) → [Evidence]`, `validate() → study spec`). The registry enforces the Evidence contract at the boundary — a defective plugin can be wrong, but it cannot be *unaccountable*. Replaceability is the 10-year survival property: every component listed in this document must be deletable without archaeology.

## 13.3 Reproducibility

`replay(dossier_id)` reproduces any dossier byte-for-byte from: snapshot_id (data manifest) + engine versions + config hash + seed. This is a *test*, run continuously on samples. Environment pinned (lockfiles, container definition); the analytical lake's immutability makes historical replays possible indefinitely.

## 13.4 The append-only decision ledger

Dossiers, claims, trades, overrides, elicited probabilities, policy versions: an append-only, hash-chained event log (cheap tamper-evidence — the pre-registration credibility mechanism, and incidentally a compelling artifact for any interviewer who asks "how do I know you didn't cherry-pick?"). Current-state views are projections; the log is the truth. This is event-sourcing applied where it actually earns its complexity — the epistemic record — and deliberately *not* applied to boring reference data.

## 13.5 Versioning discipline

Everything that can change carries a version: schemas (migrations), engines (semver; breaking = new major = new calibration cells), configs (hashed), models (cards, §12.3), the IPS (§11.1), universe definitions (time-series). Nothing versioned is ever mutated in place.

## 13.6 Testing tiers

(1) Golden-value tests — hand-verified numbers (exists from v1, extended to every new engine). (2) Property tests — accounting identities, adjustment-engine invariants (total return preserved across splits), monotonicity of aggregation. (3) **Time-travel tests** — the leak-detector harness: shift `knowledge_time` on synthetic filings and assert `as_of` outputs change exactly when they should; this single harness guards the platform's most valuable invariant. (4) Determinism tests — same snapshot+config ⇒ identical study results. (5) Grounding tests — the v1 AI-output validator, extended to span-citation checking for extractions. (6) Calibration self-tests — the scorer's math verified against synthetic claim streams with known properties.

## 13.7 Offline-first & operations

Everything batch, resumable, local-first: nightly EOD pipeline, weekly deep jobs (studies, revalidations), quarterly heavy jobs (calibration reports). No component requires connectivity except ingestion and optional LLM calls (which degrade gracefully — v1 behavior preserved). Backups: the lake and the ledger are the crown jewels; automated, tested restores (v1 §30's one justified over-investment, doubled down).

## 13.8 Named future bottlenecks

(a) **Calendar-time-bound PIT accumulation** — cannot be parallelized, only started early; (b) **decision-rate-bound personal sample sizes** — mitigated by paper dossiers on the full universe, but user-behavior models mature slowly by nature; (c) **single-maintainer attention** — the true existential risk; design response: everything resumable after months away, self-documenting dashboards, zero daily-care infrastructure; (d) **source death** — adapter isolation + raw-artifact archival; (e) **LLM cost/drift** — extraction outputs cached immutably; models swappable behind the grounding contract; (f) if the universe or history grows 10×, DuckDB-over-Parquet holds; the operational DB migrates SQLite→Postgres on a defined trigger (write contention or multi-device need), not speculatively.

---

# 14. ML Governance — where learning is allowed

The honest framing: at one investor, ~500 names, EOD cadence, this is a **statistics and epistemology project with ML garnishes**, not an ML project. Pretending otherwise is how it fails.

| Use | Status | Conditions |
|---|---|---|
| Calibration models (isotonic/Platt on claim outcomes) | **Core** | The centerpiece of §12; simple by design |
| Hierarchical/empirical-Bayes shrinkage of base rates & trust weights | **Core** | The correct answer to small samples everywhere |
| Gradient-boosted trees on interpretable engineered features, for *within-family* ranking | Permitted | Nested walk-forward; attribution (SHAP) mandatory in dossiers; validation card; loses to the simple rule unless it beats it out-of-sample by a registered margin |
| Embeddings for corpus retrieval | Permitted | Retrieval only; never generates claims |
| LLMs for narration & span-cited extraction | Permitted | Grounding validator; extraction accuracy audited on labeled samples; never originates numbers (v1 rule, unweakened) |
| Anomaly detection (statement/flow baselines) | Permitted | Statistical baseline first (v1 §13.3); ML variant must beat it OOS |
| End-to-end return prediction as decision input | **Forbidden** | Data-starved, unexplainable at T3, optimizes the wrong objective |
| RL for trading/sizing | **Forbidden** | Sample complexity is science fiction at this decision rate |
| Fine-tuning on personal data | **Forbidden** | n≈hundreds; would be performative learning — the exact thing this platform exists to never do |

Named ML risks (register): leakage through feature engineering (mitigated by the as_of API being the *only* data access path); hyperparameter overfitting via repeated OOS peeking (mitigated by registry + untouched holdout eras); silent target drift (costs/taxes change the effective label); SHAP over-interpretation (attributions ≠ mechanisms — dossiers must phrase them as associations); LLM extraction errors polluting downstream evidence (audited extraction accuracy with error bars propagated as caveats).

---

# 15. Consolidated Risk Register

**15.1 Finance assumptions, stated baldly.** (i) Indian mid/small-cap markets are semi-efficient with exploitable behavioral/structural pockets — justified by thinner analyst coverage, retail flow dominance, promoter information asymmetry; *this is a hypothesis the platform must test on itself, not an entitlement.* (ii) Documented factor premia (momentum notably robust in Indian studies; quality strong; value patchy post-2018) persist enough to matter after costs. (iii) Costs and impact are estimable within useful bounds. (iv) The user can act within a day (EOD sufficiency). (v) Equity-only, INR-denominated, unlevered. Each assumption has a named owner-metric in the meta-evaluation (§18) that can falsify it.

**15.2 Methodological weaknesses we accept knowingly.** Base rates from ≤25 years of usable Indian history straddle enormous structural change (pre/post-GST, UPI-era retail participation, derivatives-flow growth); regime cells are thin by construction; matched-control event studies in a 500-name universe have imperfect controls; the failure library will be biased toward *noticed* failures.

**15.3 The sample-size wall.** Personal decision counts mature over years. Everything gated in §17 exists because of this wall; the design crime would be pretending it isn't there.

**15.4 Data risks.** PIT gap before the archive matures; delisted-company fundamentals; transcript sparsity pre-2021; news archive licensing; per-stock FII/DII invisibility between quarterly patterns (only aggregates + bulk/block deals are daily); demerger adjustment complexity; source ToS/continuity.

**15.5 Research risks.** Momentum/PEAD/flow effects may be arbitraged away as Indian markets institutionalize — trust decay (§12.2) is the designed response, not a guarantee; the multiple-testing discipline may still undercount the garden of forking paths in exploratory work (two-tier workflow narrows, does not eliminate).

**15.6 Behavioral/product risks — the tool hurting its user.** *Rigor theater:* a beautiful dossier can inflate confidence beyond its evidence; countermeasures: mandatory "strongest case against," visible interval widths, calibration reports that show the user their own overconfidence. *Automation bias:* deference to the system where the override ledger shows the human is actually better; countermeasure: the ledger cuts both ways and is reviewed quarterly. *Goodharting oneself:* optimizing the mirror's metrics instead of wealth; countermeasure: mirror metrics are descriptive, never gamified, never targets. *Analysis paralysis:* thirteen evidence sections can rationalize never deciding; countermeasure: the abstain/act distinction is itself logged and scored — chronic abstention on later-validated dossiers is a measured cost, shown.

**15.7 The reflexivity limit.** A system that learns from its user's trades changes those trades, which changes the data. At personal scale this is manageable (we are not moving markets) but it means user-behavior base rates are non-stationary by design; the investor model must weight recent behavior and expect drift.

**15.8 Project risk.** Solo, multi-year, alongside study and life. Response: §17's gates each ship a standalone-valuable artifact; the platform is useful at every gate, not only at the end.

**15.9 Ten-year entropy.** Sources die, APIs rot, Python moves, motivation oscillates. Responses threaded through §13; the honest residual: this survives only if the maintainer keeps caring, and the design's best contribution is making resumption-after-absence cheap.

---

# 16. Rejected Alternatives (consolidated)

| Rejected | Where argued | One-line reason |
|---|---|---|
| Composite super-score | §8.1-A | Unfalsifiable, opaque, the category's central failure |
| End-to-end ML prediction core | §8.1-C, §14 | Data-starved; wrong objective; unexplainable |
| LLM multi-agent decision committee | §8.1-D | Eloquence-weighted, non-reproducible, unscoreable |
| Full automation / algorithmic execution | §8.2, §9.5 | Statistically unvalidatable at this decision rate; regulatory/liability surface; centaur is the honest architecture |
| Mean-variance optimizer as portfolio core | §9.2 | Error-maximizing at achievable estimation quality |
| Microservices / streaming / lakehouse stack | §6.2, §13.1 | Complexity without benefit at personal scale (v1 reasoning, reinforced) |
| Bitemporal DBMS dependency | §6.2 | Convention on boring storage achieves it |
| Intraday features (beyond schema headroom) | §6.3 | Different product; EOD sufficiency assumption stands |
| General news sentiment scoring | §6.3, §7.2 | Weak, noisy, licensing-fragile; scoped to entity-tagged, filing-adjacent events |
| Unversioned discretionary "gut" trades outside measurement | §9.3 | Permitted as actions, refused as unmeasured actions — everything enters the ledger |
| Options/derivatives coverage in this horizon | — | Depth-over-breadth; revisit only with CFA-progression trigger, per v1 §2.8 spirit |
| Social/community features, multi-tenancy | v1 §7.3/7.8 | Unchanged |
| Gamified behavioral nudging | §11.2 | Mirror, not nanny; gamification corrupts the measurement |

---

# 17. Roadmap as Validation Gates (not calendar)

Progress is gated by **evidence and sample sizes**, not dates. Each gate ships something independently valuable.

- **Gate 0 — done:** v1 measurement layer (T0/T1), tests, grounded narration, ledger, thesis objects.
- **Gate 1 — the time machine runs:** bitemporal ingestion live across core datasets; quality dashboards green ≥ one full quarter; corporate-action engine passing golden files; delisting/index-history registries populated. *Standalone value: the archive itself.*
- **Gate 2 — evidence earns tiers:** hypothesis registry operational; event-study harness validated; first evidence families graduate to T2 with base-rate tables (expected survivors: momentum family, PEAD, promoter-action family; expected deaths: most indicator folklore). *Value: a personal, honest "what works in India" compendium.*
- **Gate 3 — dossiers, on paper:** synthesis plane assembles full dossiers across the universe; 100% pre-registered; ≥ one quarter of paper decisions; sizing engine live in advisory display. *Value: the daily research workbench.*
- **Gate 4 — the loop closes:** claim scoring live; first calibration report at ≥50 expired claims; aggregation weights unlock from uniform priors at ≥150 scored claims per family (until then, uniform + visible "provisional"). *Value: the system now knows how good it is.*
- **Gate 5 — the mirror:** investor model activates per-signature as each crosses its minimum sample (≈100 logged decisions/overrides for the coarse signatures); counterfactual ledger reports. *Value: the user now knows where they add or destroy value.*
- **Gate 6 — conditional intelligence:** regime/cap-band-conditional weights unlock cell-by-cell at n≥30 with shrinkage; setup library items may reach "live"; adaptive elements operate under model cards and expiries. *Value: the platform is now, demonstrably and auditable-ly, learning.*

---

# 18. Meta-Evaluation — how EquiSense itself is judged

The platform must submit to its own epistemology. Standing success metrics, reported quarterly, with the null hypothesis stated: **"this user cannot beat the index after costs, and this system cannot help."**

1. **Calibration** — reliability diagrams of the system's confidence bands (the primary metric; a calibrated system that says "no edge" constantly is *succeeding*).
2. **Decision-quality trend** — bias signatures (§11.2) over time; plan-adherence; process scores.
3. **Regret ledger** — user P&L vs. index, equal-weight universe, and the §11.4 counterfactual portfolios, with significance honestly computed (and honestly wide).
4. **Trigger value** — fraction of material losses preceded by a fired invalidation trigger.
5. **Research integrity** — registry completeness; ratio of registered-to-reported studies (should be 1.0).

**The kill criterion, pre-committed:** if after three years of Gate-4+ operation the followed-dossier track record underperforms the index beyond costs with high confidence *and* the calibration report shows no improving trend, the platform's own standing recommendation becomes passive indexing — and EquiSense remains valuable as the instrument that proved it, which is more than any competing product would ever admit. Willingness to reach this conclusion is precisely what makes the platform research rather than marketing, and — the panel notes unanimously — precisely the property that makes it credible in an admissions essay or an interview room.

---

# 19. Portfolio & Career Narrative (explicit, per the brief)

The differentiating claims this blueprint supports, each backed by an inspectable artifact rather than an assertion: a registered-hypothesis research discipline with FDR control (registry export); a bitemporal PIT archive built solo (schema + leak-detector tests); a calibration-first decision system that scores itself (calibration reports); behavioral self-measurement with established estimators (the mirror); an evidence-synthesis architecture chosen over four rejected alternatives for stated statistical reasons (§8); engineering judgment that repeatedly chose boring over impressive and can defend it (§6.2, §13.1). For elite-finance audiences the strongest single narrative is §8.2 — *knowing what could and could not be statistically validated at personal scale, and placing the human exactly where validation is impossible* — because it demonstrates the rarest interview commodity: understanding the limits of one's own tooling.

# 20. Open Research Questions (genuinely unresolved)

1. Optimal shrinkage priors for Indian factor base rates given structural breaks — how much history is *too much*?
2. Does delivery-percentage carry information orthogonal to momentum and volume in mid-caps? (Promising, under-studied, cheaply testable at Gate 2.)
3. Can LLM-extracted concall tone-deltas be validated against subsequent guidance revisions/returns robustly enough for T2 admission — or do they remain a reading aid?
4. Ex-ante identifiable regime definitions: what is the minimal regime vocabulary that is both stable and decision-relevant?
5. Correlation-cluster estimation for evidence families with short overlapping histories — how to haircut without erasing real independent signal?
6. Honest self-elicitation: which probability-elicitation UX minimizes gaming when elicitor and subject are the same person?
7. Where exactly is the swing-trading after-tax frontier in post-2024 Indian tax law — does any validated setup class clear it in mid-caps at realistic impact costs?
8. What is the earliest statistically defensible point to let calibrated weights diverge from uniform — is 150 scored claims per family right, or does the answer itself need a study?

---

*End of blueprint. The next artifact is not code: it is Gate 1's ingestion-source survey and the first entries in the hypothesis registry.*
