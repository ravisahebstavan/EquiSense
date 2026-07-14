# EquiSense Phase II — Architecture for a Self-Improving Research Operating System

**Status:** Phase II redesign blueprint. Builds on [RESEARCH_BLUEPRINT.md](RESEARCH_BLUEPRINT.md) (v2, the epistemic constitution) and — critically — on the **implemented live system** (ingestion, engines, base-rate studies, synthesis, ledger), which now exists and can therefore be autopsied rather than imagined.
**Panel:** Principal Quant Researcher · Buy-side Analyst (CFA) · CIO · Principal Software Architect · ML Research Scientist · Behavioral Finance Researcher · Data Engineer · Financial Economist · Portfolio Construction Specialist · Research Methodology Expert.
**Scope:** Planning only. No implementation. Horizon: 5–10 years.

---

## 0. How this document relates to what exists

v2 established the constitution: the epistemic tiers (T0–T4), the centaur synthesis, pre-registration, calibration-gated learning, the kill criterion. **Nothing in Phase II repeals the constitution.** What Phase II does is:

1. **Audit the running system against its own constitution** (§1) — the implementation, built fast, violates its own blueprint in specific, fixable ways. Finding those violations is not embarrassment; it is the system working. A research platform that cannot detect its own methodological defects has no business scoring anyone else's.
2. **Redesign the subsystems where the audit found structural (not cosmetic) weakness** (§3–§12).
3. **Extend the horizon**: v2 planned to Gate 6 (~2–3 years); Phase II plans the decade (§15).

The reframe that organizes everything: EquiSense is not an application with features. It is a **research operating system** whose primitives are five object types — `Dataset`, `Hypothesis`, `Evidence`, `Claim`, `Decision` — and whose "features" are merely views over those objects. Anything proposed for the platform must state which primitive it creates, transforms, or scores. If it does none of those, it is decoration and is rejected (this test retires several v1-era features in §13.4).

---

# 1. Autopsy of the Implemented System

Every subsystem, judged against the v2 constitution. Severity: **S1** = corrupts inference (fix before trusting any output); **S2** = limits inference (fix within Phase II); **S3** = debt (fix opportunistically).

| # | Subsystem | Defect | Severity | Why it matters |
|---|---|---|---|---|
| A1 | Base-rate studies | **Overlapping-window sample inflation.** Monthly sampling with 63–126d horizons → adjacent episodes share up to ~5/6 of their outcome window. Reported N=1000 has an *effective* N closer to 150–250. Hit-rate CIs are far wider than the records imply, and the ≥30 publication gate is nearly meaningless under overlap. | **S1** | Every T2 record currently overstates its evidential weight. The constitution's core promise — honest sample sizes — is quietly broken. |
| A2 | Evidence layer | **Hand-picked strength scales.** `ev(..., scale=40)`, `scale=25`, `scale=4.5` — each divisor is an unvalidated constant deciding how loudly evidence speaks. This is the *weighted-score anti-pattern re-entering through the back door*, in the exact layer built to prevent it. | **S1** | The synthesis is only as honest as its inputs' magnitudes. Uniform cluster weights on top of arbitrary within-cluster scales is uniform theater. |
| A3 | Evidence admission | **Unvalidated novel metrics influence verdicts.** CCS and Fragility are registered as *deferred* hypotheses (HYP-005/006, untestable without PIT fundamentals) yet their strengths flow into the quality/risk clusters at full volume. Evidence is being consumed before it earned admission. | **S1** | Direct contradiction between the registry's own status field and the synthesis plane's behavior. |
| A4 | Universe & studies | **Survivorship: current constituents backfilled 10y.** Caveat-stamped (good) but unremediated: winners' history is overrepresented, so absolute base rates are optimistic. | **S1→S2** | Cross-sectional *rankings* partially survive; absolute levels don't. Caveats inform; they do not correct. |
| A5 | Benchmarking inside studies | **Universe-median counterfactual.** No sector/size matching; broad cohorts (e.g. above-200DMA ≈ half the universe) mechanically produce ~0 median excess, so some "null results" are artifacts of the control, not absence of signal. Studies also report **gross** of costs. | S2 | Weak controls can hide real effects and manufacture false nulls — the mirror image of data-mining. |
| A6 | Data plane | **Raw payloads are discarded.** Yahoo responses are normalized on ingest and thrown away — violating v2 §6.2's immutable raw vault. Re-normalization, audit, and provider-dispute resolution are impossible retroactively. Also: single provider, no reconciliation, no ingest-time anomaly gates (the blueprint specified them; the implementation skipped them), no PIT index-membership archive started. | **S1** (vault) / S2 (rest) | The archive's value compounds with calendar time; every month without raw archival is unrecoverable. |
| A7 | Fundamentals | Annual only; reconstructed (latest-known); no quarterly statements; no revision/restatement capture (schema supports it, source doesn't); banks/NBFCs excluded wholesale rather than served by a financial-sector schema (~30% of the universe analytically dark). | S2 | Quarterly cadence is where earnings-revision and PEAD evidence lives; the current layer cannot host them. |
| A8 | Regime engine | 2-state trend label + VIX percentile; INR/crude computed but unused in conditioning; regime definition never itself validated (does conditioning *improve* out-of-sample inference? untested). | S2 | Regime cells currently add partition noise without demonstrated conditioning value. |
| A9 | Synthesis constants | Abstention thresholds (0.12 net, 0.55 dispersion, 3-cluster floor) and the claim-probability map (0.5 + 0.25·\|net\|) are design guesses. Flagged provisional (good); still guesses. | S2 | Acceptable *only* because the calibration ledger will eventually grade them; the loop must actually close. |
| A10 | Ledger & scoring | Claims score against universe median, gross of costs; calendar≈trading-day fudge (×1.45); **abstentions carry no counterfactual score** — chronic wrongful abstention is invisible. Chain integrity itself: sound. | S2 | An unscoreable abstention is a free pass; v2 §15.6 explicitly forbids that. |
| A11 | Portfolio-fit | The cluster exists in the taxonomy and **no engine emits it** — dossiers evaluate candidates in a vacuum, violating "every investment evaluated in context of the existing portfolio." | S2 | The data to fix this (own price panel → correlations; own ledger → heat) is already stored. |
| A12 | Sizing | Portfolio heat hardcoded to zero (open positions not aggregated); impact model invented, uncalibrated; "Kelly" language over what is honestly a risk-budget rule. | S2/S3 | The shown-work discipline is right; two inputs to the work are stubs. |
| A13 | Macro | 4 series vs. the dozens needed for regime work (no CPI/WPI, repo, G-sec curve, gold, global indices, FII/DII flows). | S2 | The regime redesign (§6) is data-starved until this widens. |
| A14 | v1 remnants | Demo-seed path coexists with live data; AI narration doesn't consume live dossiers; lens/profile system unaware of evidence architecture. | S3 | Confusion debt, not inference debt. |

**The meta-finding:** the S1 defects share one root cause — *implementation moved faster than validation infrastructure*. Phase II's ordering principle is therefore: **no new evidence sources, engines, or scores until the measurement of existing ones is honest** (N_eff, admission caps, raw vault). Rigor first, reach second.

---

# 2. The Research OS Reframe

The five primitives and their contracts:

- **Dataset** — versioned, provenance-tracked, bitemporal, quality-gated. Everything downstream cites `(dataset, version, as_of)`.
- **Hypothesis** — the unit of belief (§4). Owns its lifecycle, validation history, and decay monitors. *Nothing becomes Evidence except through a Hypothesis.*
- **Evidence** — a Hypothesis's dated, per-entity emission, with tier, effective-sample-backed base rate, and admission status (§5).
- **Claim** — a falsifiable, pre-registered, expiring statement (dossier verdicts, regime assertions, base rates themselves). All claims are scoreable; all get scored.
- **Decision** — a human act (trade, pass, override, abstain-accept) linked to the Claims it consulted, with its own quality scoring separate from outcome.

Views over these primitives replace "features": a company page is a query over Evidence + Claims + Decisions for one entity; the Evidence Lab is a query over Hypotheses; the calibration report is a query over scored Claims; the investor model is a query over Decisions. **The system improves by accumulating better-scored objects, not by accumulating screens** — the brief's "improve through evidence rather than manually added features," made structural.

---

# 3. Data Plane v3

## 3.1 The architectural inversion: vault first, meaning later

Current flow (defective): fetch → normalize → store normalized → discard raw.
Phase II flow: **fetch → vault raw → normalize as replayable pure transform → canonical bitemporal store**, where:

- **Raw vault**: content-addressed, immutable, append-only blobs (`sha256`, provider, endpoint, fetch-time, license note). Cheap (tens of GB/decade). Every future dispute — "did the provider revise this? did our parser mangle it?" — is answerable forever. *This is the single highest-priority Phase II build*: its value is calendar-bound and every unvaulted month is gone.
- **Transforms**: versioned pure functions raw→canonical. A parser bug fix = new transform version + full replay; the canonical store is always reproducible from the vault (`canonical = f(vault, transform_version)`), which also makes the canonical store *disposable* — a profound simplification for schema evolution over a decade.
- **Canonical store**: bitemporal by convention (`event_time`, `knowledge_time`) with the `as_of` API as the **only** research-facing read path (leakage prevention as an access-control property, not a discipline).

## 3.2 Multi-provider truth and reconciliation

Single-provider dependence (A6) is the platform's largest operational risk. Design:

- **Provider registry** per dataset: ordered by authority (exchange/regulator > national statistical source > commercial aggregator > unofficial API). Yahoo demotes to *bootstrap provider*; exchange bhavcopies (official public downloads) become the price/volume/delivery authority; RBI/MOSPI data for macro; exchange filing feeds for statements and events.
- **Reconciliation rules**, per field class: prices — tolerance bands (≤10bps disagreement auto-resolves to authority; beyond → quarantine + human queue); fundamentals — exact-match expectation with restatement detection (a changed prior-period figure spawns a new `restatement_version`, never an overwrite — the schema already supports what the source pipeline must start doing); corporate actions — dual-source confirmation required before the adjuster consumes them (a missed or phantom split is the single worst data corruption available).
- **Provider health monitors**: freshness SLOs, schema-drift detectors, failure budgets; a dead provider triggers fallback, not silence.

## 3.3 Ingest-time quality gates (specified in v2, now non-optional)

Accounting identities; cross-source share-count reconciliation; return outlier vs. corporate-action cross-check (a −45% "return" with no action on file is a quarantine event, not a data point); staleness dashboards; **quarantine as the only failure mode** — bad data may delay research; it may never silently enter it.

## 3.4 Dataset build-out plan (full brief coverage, sequenced by inference value per effort)

| Wave | Datasets | Notes |
|---|---|---|
| **II.1** | Raw vault retrofit; exchange bhavcopy prices+**delivery %**; corporate-action feed (dual-source); PIT index-membership + delisting registry (starts the clock); macro widening: CPI, WPI, repo/reverse-repo, 10y & curve, gold, global indices, FII/DII daily flows | Delivery % is the highest-value free dataset not yet ingested — India-specific conviction signal |
| **II.2** | Quarterly results (exchange filings, archived at announcement → true PIT accrues); shareholding patterns (promoter/pledge/FII/MF/retail buckets); bulk/block deals; insider (SAST/PIT) disclosures; credit-rating actions | Unlocks: PEAD properly, earnings-revision family, ownership-flow family, distress events |
| **II.3** | Earnings-call transcripts (mandated disclosures, growing archive); annual reports (MD&A, auditor sections); MF monthly portfolios; F&O open interest / options positioning for derivative-listed names | Unlocks NLP evidence families + positioning evidence |
| **II.4** | Bank/NBFC canonical schema (NII, NIM, GNPA/NNPA, CAR, credit costs) — ends the financial-sector blackout | ~30% of universe re-illuminated |

**Alternative-data auditions** (each must pass: PIT-able? licensable? plausible mechanism? *incremental* information vs. existing families, measured as orthogonal IC?): transcripts **admit** (verifiable extractions, checkable against subsequent revisions); credit ratings **admit** (sparse, high-signal distress events); options positioning **audition** (public OI data; crowding/expectation evidence); analyst revisions **defer** (licensing wall at personal scale); news sentiment **reject** (crowded, weak, licensing quicksand — unchanged from v2); ESG **reject for this mandate** (no credible link to the decision quality this system optimizes); supply-chain **reject** (impractical at personal scale).

> **Panel Challenge — Data Engineer vs. CIO.** *CIO:* wave II.1 has no new alpha in it; why is plumbing first? *Data Engineer:* A1/A6 mean the platform currently cannot *measure* alpha honestly; new datasets would inherit the same broken measurement. *Resolution:* unanimous for rigor-first — with one concession to the CIO: delivery % rides in wave II.1 because it shares the bhavcopy pipeline being built anyway.

---

# 4. The Research Lifecycle Engine

The registry (currently a dict with four status strings) becomes the OS's process manager.

## 4.1 The Hypothesis object, full schema

`id · version · author · registered_at · mechanism` (a *causal story* — no mechanism, no registration: the cheapest and most effective anti-datamining filter) `· expected_sign/horizon/decay · assumptions[] · literature[] · spec` (exact, executable) `· data_requirements` (with PIT grades) `· validation_history[]` (every run: dataset version, N, **N_eff**, effect, CI, costs applied, regime splits, robustness grid) `· calibration_history[] · status · promotion/retirement criteria` (numeric, pre-stated — e.g., "retire if rolling 3y N_eff-adjusted IC CI includes 0 for 4 consecutive quarters") `· trials_ledger_ref` (every variant ever tried, for FDR accounting).

## 4.2 Lifecycle states and promotion gates

```
idea → registered → exploratory → confirmatory → shadow → deployed → monitoring ⇄ decayed → retired
```

- **exploratory**: free play on a designated exploration slice. Results are *inadmissible* as evidence, logged in the trials ledger (feeds FDR).
- **confirmatory**: one pre-specified test on data untouched during exploration (era holdout). BH-FDR across the family's trials ledger; effect must clear the *net-of-costs* bar with N_eff-honest CIs.
- **shadow**: emits Evidence into dossiers at **zero synthesis weight** (visible, non-influential) for ≥2 quarters — live-data plumbing errors surface before influence begins.
- **deployed**: full admission (strength caps per §5.2). **monitoring**: rolling decay detection (windowed IC with change-point alarms). **decayed/retired**: weight → 0; record permanent. Retired hypotheses are the failure library's raw material.

Existing hypotheses re-enter this ladder where they honestly sit: HYP-001/002/003/007/008 → *exploratory results, pending confirmatory re-run under N_eff + matched controls + costs*; HYP-004 (MQI) likewise; HYP-005/006 remain registered-deferred **and their live metrics drop to shadow status** (fixes A3).

---

# 5. Evidence & Synthesis v3

## 5.1 Fixing A2: normalization replaces hand scales

An engine never chooses its own volume. It emits **raw measurements**; the evidence layer converts to strength via a fixed, global convention: cross-sectional percentile within the universe as-of-date → centered to [−1, +1] (rank-based: robust to outliers and scale-free), with the *response shape* (linear vs. winsorized tails) a per-family parameter that is itself part of the hypothesis spec and validated with it. Hand divisors are abolished. One convention, uniformly applied, inspectable in one place.

## 5.2 Fixing A3: admission tiers with strength caps

| Validation status | Max |strength| into synthesis | Rendered as |
|---|---|---|---|
| shadow / registered-deferred | 0.00 | visible context, "not influencing verdict" |
| exploratory-passed only | 0.25 | "provisional evidence" |
| confirmatory-passed | 0.60 | "validated evidence" |
| deployed + surviving monitoring ≥1y | 1.00 | "established evidence" |

Influence is now *earned through the lifecycle*, mechanically. The synthesis plane enforces caps; engines cannot opt out.

## 5.3 Module roster (brief's list, mapped and judged)

Quality, Valuation, Growth, Momentum, Technical Structure, Financial Distress, Macro, Liquidity, Risk — **exist**; upgrade path is data waves + lifecycle re-validation. Earnings Revision — **new**, unlocked by wave II.2 (quarterly PIT results; revision = realized-vs-trailing-trajectory until analyst data is licensable). Capital Allocation — **new**: reinvestment-at-ROIC vs. buyback/dividend/hoard patterns, promoter-stake deltas as skin-in-the-game signals. Management — **narrowed to the measurable**: governance events (auditor changes, resignations, pledge spikes, RPT growth), commitment-vs-delivery from transcripts (stated guidance vs. outcomes — verifiable NLP); "management quality" as a vibe is rejected. Behavioural — ownership-flow evidence (retail-bucket changes, delivery %, MF adds) per v2's flow engine. Portfolio Fit — **built at last** (fixes A11): marginal heat, return-correlation to current book from the stored panel, factor-exposure delta, liquidity of the combined position.

## 5.4 Aggregation: the architecture comparison, revisited with implementation experience

| Candidate | Verdict | Reasoning |
|---|---|---|
| Uniform cluster weights (current) | Keep as **stage 0** | Honest under zero calibration data; now with §5.1–5.2 its inputs are no longer secretly weighted |
| Regularized learned model (logistic/GBT on evidence vectors → outcome) | **Reject as synthesizer** | Even pooled paper-dossiers yield low thousands of *overlapping* observations; a learned discriminator would memorize regime accidents. Retained only as a *diagnostic* (which families correlate with outcomes) |
| Full hierarchical Bayesian generative model | Reject (for now) | Right shape, wrong maintenance cost for one maintainer; revisit at year 5+ if claim volume justifies |
| **Empirical-Bayes staged aggregation** | **Adopt as stages 1–2** | Family hit-rates as Beta-Binomial posteriors shrunk toward 0.5 (prior strength = the skepticism dial, pre-registered); cluster weight = monotone function of posterior mean − 0.5, capped; correlation haircut from the *measured* inter-family correlation matrix (computable today from stored evidence panels — replacing hand-assigned clusters at stage 2). Simple, closed-form, small-sample-honest, fully inspectable |

Unlock schedule (extends v2 Gate 4): stage 0 → stage 1 at ≥150 scored claims/family (N_eff-counted); stage 1 → stage 2 (measured-correlation clustering) at ≥400. The synthesis output gains a mandatory **distributional statement** (base-rate-derived outcome IQR, not just a direction) — the CIO's requirement that sizing consume distributions, not points.

---

# 6. Regime Intelligence v2

## 6.1 The honesty constraint that governs everything here

Regime inference is macro-frequency: 25 years of index history contains perhaps 8–12 independent episodes. Therefore: few states (≤4), simple models, and — the Phase II addition — **the regime engine is itself a registered hypothesis** (`REG-001: does conditioning on discovered regimes improve out-of-sample calibration of base rates versus unconditional?`). If conditioning fails that test, regimes demote to descriptive dashboard context. The engine must justify its existence like any other hypothesis.

## 6.2 Two-layer design

- **Layer 1 — Observable state vector** (always-on, descriptive, no model): realized & implied vol percentiles, rate level/direction (repo, 10y), CPI trajectory, INR & crude trends, FII/DII flow regime, breadth (% above 200DMA), factor-spread momentum (value-vs-momentum leadership), earnings-cycle proxy (aggregate revision direction, post wave II.2). Rendered as the regime banner's replacement: a state *vector*, not a slogan.
- **Layer 2 — Latent state discovery**: Gaussian-mixture / HMM over a *small* subset (vol, rates direction, breadth — 3 dims max) on 25y of index-era data, K ∈ {2,3,4} selected by out-of-sample stability, with mandatory robustness: subsample re-fits must reproduce labelings (adjusted-Rand ≥ threshold, pre-registered); unstable → K reduces or Layer 2 ships nothing. Discovered states get *descriptive* names after inspection, never before.

## 6.3 Should engines behave differently per regime?

Only through the one sanctioned channel: **base-rate conditioning cells + synthesis weight cells with hierarchical shrinkage** (thin cells borrow from unconditional). Separate per-regime codepaths are rejected — they multiply surface area, fragment already-scarce samples, and make behavior unauditable. Sector leadership / factor rotation enter as evidence *inputs* (relative-strength family), not as regime switches.

---

# 7. The Learning & Calibration System

The loop, with every A-defect fix threaded in:

1. **Claim scoring v2** — costs and taxes applied to realized outcomes (fixes gross-of-cost grading); trading-day calendars (fixes the ×1.45 fudge); matched-control benchmarks where the claim implies one; **abstention counterfactuals**: every abstain-verdict tracks the forward outcome it declined, producing the wrongful-abstention rate (fixes A10) — abstention keeps its first-class status *and* acquires a price.
2. **Calibration** — reliability curves per claim family; once ≥100 scored claims/family, **isotonic recalibration** replaces the invented probability map of A9 (the map's parameters stop being guesses and become fitted, versioned, expiring artifacts with model cards).
3. **Trust accounting** — Beta-Binomial posteriors per (family × horizon × regime-cell), shrunk hierarchically; posteriors *are* the §5.4 weights. Trust decays on staleness and on change-point alarms from monitoring.
4. **Online learning verdict** — per-observation online updates **rejected**: non-stationary, tiny, overlapping data makes online learners chase noise. Adopted instead: *windowed refits on schedule + change-point-triggered early refits*, every refit producing a versioned artifact with validation card. Ensemble evolution = the posterior weights drifting under evidence — no genetic/meta-learning machinery, which at this scale is ritual.
5. **Thesis expiry** — hard: every thesis and claim carries a review-by date; unreviewed past date → auto-flagged stale and excluded from "active" views. Nothing is allowed to be quietly immortal.

---

# 8. Validation Standards v2

Additions to the v2 doctrine, each traceable to an autopsy finding:

- **Effective sample size is a mandatory reported field** (A1): non-overlapping episode counting or block-bootstrap CIs (block ≥ horizon length) on every study; the ≥30 publication gate re-based on N_eff. Every existing base-rate record is re-run under this standard before further use — the single most urgent research task in Phase II.
- **Matched controls** (A5): sector- and size-matched benchmarks as the default counterfactual; universe-median retained only as a secondary column. Cohort-construction sanity checks (a cohort covering >40% of the universe cannot claim cross-sectional distinction).
- **Net-of-cost columns everywhere** (A5): studies report gross *and* net at the standard cost model; promotion gates read the net column only.
- **Nested walk-forward** for any fitted parameter (isotonic maps, mixture models, response shapes): outer walk-forward for honest error, inner split for parameter selection; parameters frozen before each outer test era.
- **Leakage harness extension**: the existing future-price perturbation test generalizes to fundamentals (`knowledge_time` shifts must move results the right way) and becomes a CI-blocking test for every new feature builder.
- **Sensitivity & robustness grids** as promotion requirements: parameter neighborhoods, sub-era stability (including 2016, 2018, 2020, 2021–22 episodes), cap-band splits, execution-lag (t+1/t+2) — fragility anywhere is stamped on the hypothesis record.
- **Survivorship remediation** (A4): PIT membership archive forward (wave II.1); historical membership reconstruction from index-provider change records where obtainable; until then, studies dual-report "current-constituent" and (when reconstructible) "as-was" universes.

---

# 9. The Explainability Contract v2

The brief's twelve questions become **mandatory dossier fields with defined sources** — no field may be prose-only:

| Question | Source of answer |
|---|---|
| Why? | Evidence list, tiered & clustered, strengths shown |
| Supporting / contradicting evidence? | Split rendering; dissent named per cluster (exists) + **contradicting-evidence search** (the §7.3 v2 disconfirming checklist, now a required section) |
| Historical success of similar situations? | **Setup-similarity retrieval**: nearest historical evidence-vectors from the platform's own archive, with their realized outcomes — the outside view, automated |
| Known weaknesses? | Auto-compiled: N_eff warnings, pit_grade flags, missing clusters, fragility stamps from robustness grids |
| Confidence? | Decomposed computation (exists), now isotonic-calibrated once data permits |
| Alternative explanations? | Rule-generated rival readings (e.g., "momentum + volume surge is also consistent with crowding; heat score: X") |
| Missing information? | Coverage report (exists) + staleness map |
| Expected holding period? | The claim's horizon, from the base-rate table that motivated it |
| Catalysts? | Event calendar: results dates, corporate actions, rating reviews (wave II.2 data) |
| Invalidation triggers? | Auto-armed, machine-checkable conditions (price levels, statement thresholds, event types) — monitored, firing into the dashboard |
| Risk factors? | Risk-cluster evidence + portfolio-fit marginal risk |
| How much? | Sizing with shown work (exists), now consuming outcome *distributions* (§5.4) and real portfolio heat (fixes A12) |

---

# 10. Adaptive Investor Model

v2 §11's design survives Phase II review intact (it was never implemented, so there is nothing to autopsy — the panel notes the irony that the *unbuilt* subsystem is the only one without defects). Phase II adds exactly three things: (1) **strategy-preference learning** — which dossier archetypes (TVT quadrants, evidence signatures, horizons) the user acts on vs. ignores, feeding presentation ordering (never verdict changes); (2) the **override ledger wired to claim scoring** from day one of live decisions, so "human vs. machine, where?" accumulates from the first trade; (3) activation gates restated with N_eff discipline (bias estimators activate per-signature at their own minimum samples; premature psychology is noise dressed as insight). The mirror stays descriptive, contestable, local-only.

---

# 11. Portfolio Intelligence v2

- **Portfolio-fit engine** (fixes A11): every dossier gains marginal analytics — Δconcentration on all four axes, return-correlation of candidate to current book (from the stored panel; regime-instability caveat mandatory), Δfactor exposure (vs. long-short factor proxies constructed *from the platform's own universe*: momentum, value, quality, size), Δportfolio heat, combined-liquidity (days-to-exit book-wide).
- **Real heat** (fixes A12): open-position risk aggregated from the ledger's armed stops; sizing reads it.
- **Scenario & stress**: historical-episode replay (the book's current weights pushed through 2016/2018/2020/2021-22 windows), plus factor-shock grids (±2σ on each proxy). *Hypothetical* labeling per v1 §26 discipline.
- **Expected drawdown**: portfolio vol + historical drawdown distributions per composition profile — a distribution, never a point.
- **Opportunity cost**: the standing counterfactual portfolios (index, equal-weight, dossier-following, plan-adherent exits) from v2 §11.4, now including the abstention-counterfactual ledger (§7.1) — "what did saying no cost?" gets a number.
- **Taxation**: lot-aware LTCG/STCG cliffs in every sizing and every exit dossier (machinery exists; wiring completes).

# 12. Research Memory

Per-company **institutional memory view**: all dossiers ever issued (with scores), thesis lifecycle history, valuation-percentile history, governance event timeline, own-journal retrieval, and the **mistake taxonomy** — every scored failure tagged (thesis-wrong / timing-wrong / sizing-wrong / process-violation / bad-luck) per the v2 autopsy discipline, feeding the setup-similarity retrieval (§9). Implementation: views over the five primitives plus embeddings for text retrieval — memory is a *query*, not a new store, which is exactly why the OS reframe (§2) matters.

---

# 13. Engineering Architecture

## 13.1 Re-examined, not presumed

- **Layered monolith**: re-affirmed after genuine reconsideration — Phase II adds zero concurrent users, zero real-time constraints; the alternatives (services, queues) still price at complexity with no inference benefit. The monolith gains internal *process* separation only where crash-isolation matters: ingestion workers run as separate OS processes writing to the vault, so a parser segfault can never take the app down.
- **Storage**: SQLite remains operational-truth. The **trigger** for the Parquet+DuckDB research lake (specified in v2, deferred until needed): panel-query latency >5s or dataset >5GB — waves II.2/II.3 will trip it; the lake materializes *from the vault via transforms*, so adopting it is a replay, not a migration.
- **Plugin contracts**: engines/adapters/hypotheses register against small ABCs (v2 §13.2); Phase II adds the **admission enforcement point** — the synthesis plane reads validation status from the registry and applies §5.2 caps; an engine cannot self-promote.
- **Reproducibility**: `replay(dossier_id)` byte-identical from `(vault, transform versions, engine versions, config hash)` — now actually achievable *because* of the vault (it was aspirational while raw data was discarded).
- **Testing tiers**: golden values, property tests, time-travel/leakage harness (now CI-blocking), determinism, chain integrity, calibration self-tests — plus **N_eff correctness tests** (synthetic overlapping panels with known effective sizes).

## 13.2 Bottlenecks, named

(1) PIT accumulation — calendar-bound, unfixable except by starting waves II.1–II.2 now; (2) claim-scoring latency — horizons must elapse; mitigated by paper-dossier fan-out across the universe (hundreds of claims/quarter vs. dozens of trades); (3) single maintainer — mitigated by resumability, self-documenting dashboards, batch-everything; (4) provider mortality — vault + multi-provider + transform replay make any single death survivable; (5) the panel's candid #5: **motivation decay** — the strongest countermeasure is the system visibly getting smarter (calibration reports improving), which is precisely what the loop closure delivers.

## 13.4 Retired by the primitive test (§2)

Demo-seed path (replaced by live data + a synthetic *test* fixture set); AI narration as a standalone feature (re-scoped: narration of dossiers/claims only, grounding validator intact); the v1 "lens" cosmetic reordering (superseded by evidence-native presentation + investor-model preference learning); any dashboard element not backed by a scoreable object.

---

# 14. Competing Architectures — Consolidated Verdicts

| Decision | Chosen | Rejected (with reason) |
|---|---|---|
| Synthesis | Staged empirical-Bayes over validated evidence (§5.4) | Uniform-forever (wastes accumulated calibration); learned discriminator (overfits N_eff-tiny data); full hierarchical Bayes (maintenance cost now, revisit yr 5+); LLM judgment (unscoreable — unchanged from v2) |
| Regime | Observable vector + ≤4-state latent discovery, itself hypothesis-tested | Rule-only forever (never learns); rich HMMs (sample starvation); regime-specific codepaths (unauditable) |
| Data | Vault → transforms → bitemporal canonical, multi-provider | Normalize-and-discard (proven mistake, A6); commercial PIT feed as foundation (cost, single-point dependency; remains an opportunistic supplement) |
| Learning | Windowed refits + change-point triggers, Beta-Binomial trust | Per-observation online learning (noise-chasing); genetic/meta-ensembles (ritual at this scale) |
| Storage | SQLite + trigger-based Parquet/DuckDB lake | Lake-first now (premature); DBMS exotica (unchanged from v2) |
| Runtime | Layered monolith + crash-isolated ingest workers | Services/queues (unchanged); serverless (state-hostile) |

---

# 15. The 5–10 Year Roadmap (gates, not calendars)

**Wave R (Rigor) — before anything new:** raw vault live; N_eff re-run of all existing base rates (expect several current records to lose publishable status — that is the system telling the truth); §5.1 normalization + §5.2 caps deployed; abstention counterfactuals scoring. *Exit: every published number carries honest uncertainty.*

**Wave II.1–II.2 (Data):** per §3.4. *Exit: PIT clock running on membership, quarterly statements, ownership.*

**Wave S (Science):** confirmatory re-runs of HYP-001..008 under the v2 standards; earnings-revision and ownership-flow families through the lifecycle; REG-001 regime-conditioning test. *Exit: the first evidence families reach "deployed" honestly — or the registry records that none did, which is equally valid output.*

**Wave L (Learning):** claim volume from paper fan-out crosses calibration thresholds; isotonic maps replace invented probability curves; stage-1 weights unlock. *Exit: the confidence numbers mean something, demonstrably.*

**Wave P (Portfolio & Person):** portfolio-fit engine, real heat, scenario replay; investor-model signatures activate as decision counts permit. *Exit: every dossier is portfolio-contextual; the mirror has its first honest reflections.*

**Years 4–10:** transcripts/OI/ratings families mature; bank schema ends the financial blackout; stage-2 correlation-measured aggregation; latent-regime v2 with a decade of self-collected PIT data; the **meta-evaluation verdicts** (v2 §18) become answerable with statistical honesty — including, standing and undiminished, the kill criterion: if the system cannot demonstrate calibrated skill net of costs, its standing output becomes "index, and here is the instrument-grade proof," and the decade of infrastructure remains a genuinely novel research artifact either way.

# 16. Success Metrics & Open Questions

**The platform is succeeding iff** (quarterly, self-reported): calibration error shrinking; N_eff-honest evidence families surviving monitoring > families retired for decay *discovered late*; wrongful-abstention rate quantified and trending; user decision-quality signatures improving; the regret ledger honest. **Open questions carried forward** (v2 §20 plus): Q9 — what block length makes bootstrap CIs honest for 126d-overlap cohorts on ~2,500-day panels? Q10 — does delivery % survive a properly controlled, cost-adjusted confirmatory run? Q11 — is there any K for which Indian latent regimes are subsample-stable? Q12 — at what claim volume does stage-2 correlation estimation stop being noise? Q13 — can wrongful-abstention scoring avoid teaching the system to over-trade (Goodhart check on the fix itself)?

---

*End of Phase II blueprint. The first artifact is not a feature: it is the raw vault plus the N_eff re-run — the platform learning to distrust its own current numbers with statistical precision.*
