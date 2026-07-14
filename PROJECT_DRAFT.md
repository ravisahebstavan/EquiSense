# EquiSense
## Product Requirements Document & Strategic Architecture
### An AI-Powered Personal Investment Intelligence Platform for the Indian Equity Market

**Document status:** Founding design document (v1.0)
**Prepared by:** Founding design team — Principal PM, CFA Charterholder, Equity Research Analyst, Portfolio Manager, Credit Risk Analyst, Financial Data Scientist, ML Researcher, Software Architect, UX Designer, Startup CTO, Senior Data Engineer, Business Analyst
**Scope:** Full product definition, from first principles, for a solo-built, multi-year platform

---

## How to read this document

Every major section ends with a **Cross-Functional Challenge** block — this is the "founding team" interrogating the preceding decision from the seven analytical and three engineering/product lenses named above. Where the team disagreed, both views are given, and a resolution is stated with reasoning, not just asserted. Where a feature or idea was rejected, the rejection is recorded with the same rigor as an acceptance — a PRD that only records what was built is a PRD that hides its own reasoning.

Numbering follows the 37 required sections plus a final synthesis. This is long by design: you asked for a document precise enough that another engineering team could build the product without ambiguity, and for challenge rather than brainstorming. Brevity would have been the wrong optimization here.

---

# 1. Vision

**EquiSense is the analytical exoskeleton for a single serious investor.**

Not a dashboard that displays data about companies — a system that thinks *with* you about companies, remembers what you believe about them, tracks whether reality is confirming or contradicting those beliefs, and gets sharper as your own financial literacy (via the CFA curriculum) gets sharper.

The test for every feature in this document is a single question: **"Would a working equity analyst, given this feature, make a measurably better decision, faster, with a clearer record of why?"** If the honest answer is "it would look impressive in a demo but change nothing about the decision," it does not belong in the product.

Three horizons define the vision:

- **Year 1**: A daily-use personal workspace that replaces your spreadsheets or notes for tracking companies you own or watch, with a defensible analytical core (ratio engine, DCF, quality scoring) and a research journal that captures your own reasoning over time.
- **Year 2–3**: A personalization engine that has learned your investment philosophy well enough that its prioritization, alerts, and AI commentary feel individually tailored rather than generically financial. At this point the CFA curriculum (L2/L3) starts feeding new analytical modules directly — fixed income analytics, derivatives-aware portfolio risk, alternative investments framing.
- **Year 3+**: A platform mature enough that it is a legitimate answer to "what have you built" in an interview room, a master's admissions committee, or a conversation about joining/running Maruti Hospicare's capital allocation function — because it demonstrates you can translate financial theory into working analytical software, not just pass exams about it.

**Vision anti-statement, deliberately:** EquiSense is not trying to be Screener.in, Tickertape, or Moneycontrol with an AI layer bolted on. Those products win on breadth (every listed company, every possible ratio, every derivative screen). EquiSense wins on **depth of reasoning about a bounded universe of companies the user actually cares about**. This is a fundamentally different product shape, not a feature-competitive one, and every downstream decision in this document follows from that distinction.

---

# 2. Product Philosophy

Ten commitments, stated as constraints the team must actively defend against scope creep, not aspirations:

**2.1 — Reasoning over data.** Any number on screen must be traceable to *why it matters for this decision*, not just *how it was computed*. A ROIC of 18% means nothing without "and here is what that implies for reinvestment economics relative to WACC." Data-only screens are commodity; reasoning is the product.

**2.2 — Personalization is structural, not cosmetic.** A "risk tolerance: moderate" dropdown that doesn't change any downstream computation is decoration. Personalization must alter what the system *shows first*, *how it scores*, and *what it flags* — provably, not just thematically (dark mode is not personalization).

**2.3 — Explainability is a hard requirement, not a nice-to-have.** Every AI-generated output must expose its inputs and reasoning chain. If a feature cannot be explained, it does not ship, regardless of how good it looks in a demo.

**2.4 — No fortune-telling.** No price targets framed as predictions, no "AI says BUY," no probability-of-return outputs disguised as forecasts. This is a philosophical line as much as a legal-safety one: apps that promise prediction train users toward exactly the behavior (chasing signals) that destroys long-term returns. See Section 13 for the full AI capability boundary.

**2.5 — Bounded universe over breadth.** V1 supports a few hundred companies you would plausibly watch or hold, not all ~5,000 NSE/BSE-listed entities. Depth of analysis on a curated universe beats shallow analysis on everything. (Section 16 details data-acquisition consequences of this choice.)

**2.6 — Simplicity is the default; complexity must be earned.** Every architectural decision in Sections 15–18 explicitly states the simpler alternative that was rejected and why. If a simpler design gets 95% of the value, it wins, even if the more complex design is more "interesting" to build.

**2.7 — The user's own reasoning is a first-class data object.** Investment theses, journal entries, and rule violations are not metadata bolted onto price charts — they are the primary content the platform exists to organize. Section 22–23 elevate this to a core engine, not a notes feature.

**2.8 — Learning-linked evolution.** The roadmap (Section 9) is explicitly sequenced against CFA curriculum progression, not against arbitrary sprints, because the stated purpose of the product is partly pedagogical and this sequencing is a genuine differentiator for admissions and resume narratives — it shows *applied* learning, which is rare.

**2.9 — India-first, not India-only-forever.** Architecture must not assume BSE/NSE-only data models are permanent, but no international abstraction should be built speculatively in V1. "Designed so it could extend" is different from "built to support multiple markets now" — the former costs almost nothing; the latter costs months. (Section 16.)

**2.10 — This is software you will actually open every day.** If, six months in, you're not opening EquiSense before you open a spreadsheet, the product has failed regardless of its GitHub stars. Daily-use friction is a first-order design constraint, tested against explicitly in the UX section (Section 18).

### Cross-Functional Challenge

- **CFA Charterholder / PM view**: Commitment 2.4 (no fortune-telling) is right, but there's a risk of over-correcting into a system so cautious it becomes useless for the actual decision investors need help with: *timing of capital deployment*. The resolution (Section 13) is to support **valuation-relative-to-history and peers** (is this cheap or expensive right now, and why) without ever crossing into *price prediction*. Cheapness is a measurable, explainable, backward- and structurally-grounded fact; a price target is a forecast. That line is defensible and will be maintained throughout.
- **Software Architect / CTO view**: 2.6 and 2.9 are in tension — "simple by default" pulls toward hardcoding Indian-market assumptions everywhere; "not India-only-forever" pulls toward abstraction. Resolution: abstain from a generic "Market" interface layer in V1 (that's speculative complexity), but *do* isolate all India-specific constants (trading calendar, filing formats, currency, tax treatment) into a small number of named modules rather than scattering them through the codebase. This is "designed so it could extend" at near-zero cost, versus a full multi-market abstraction, which would be actual, unjustified cost.
- **UX Designer view**: 2.10 should be treated as the tie-breaker whenever two philosophically-defensible options conflict. A feature that is analytically pure but adds friction to daily use loses to a feature that is slightly less rigorous but effortless to use daily, provided it doesn't violate 2.3 (explainability) or 2.4 (no fortune-telling).

---

# 3. Problems Being Solved

Stated precisely, because vague problem statements produce vague products.

**Problem 1 — Fragmented workspace.** Financial statement analysis lives in Excel, valuation models live in a different spreadsheet, watchlists live in a broker app or Tickertape, and reasoning about *why* a stock was bought lives in scattered notes or nowhere. There is no single place where data, analysis, and reasoning about a position converge.

**Problem 2 — Screeners answer "what" but not "why" or "so what."** Screener.in and Tickertape are excellent at "show me all companies with ROCE > 20% and D/E < 0.5." They are not designed to answer "given *my* stated philosophy and *my* existing portfolio, does adding this company make sense, and what would have to be true for this thesis to break?"

**Problem 3 — No persistent memory of one's own reasoning.** Most investors (including sophisticated ones) cannot answer "why did I buy this, and has that reason changed?" six months after a purchase. Decision quality degrades not from lack of data but from lack of a forcing function to record and revisit the *decision rationale itself.*

**Problem 4 — Generic risk framing.** "Risk" in most retail tools means volatility or beta. It rarely means "what specific things, if they happened, would prove me wrong" (thesis-level risk) or "how correlated are my actual holdings during a drawdown" (portfolio-structural risk). Both are addressable with rigor; neither is offered off-the-shelf.

**Problem 5 — CFA-level financial literacy has no natural application surface.** Learning DCF mechanics, ROIC decomposition, or distress-prediction models (Altman Z-score, Merton distance-to-default) in the abstract, via CFA study, doesn't build durable intuition the way applying them to real companies you actually hold does. There's no existing tool designed to be a live application layer for curriculum concepts as you learn them.

**Problem 6 — Indian-market-specific gaps in existing tools.** Promoter pledging, related-party transactions, corporate governance red flags specific to Indian listed-company structures (family holding patterns, standalone-vs-consolidated distortions), and India-specific valuation context (peer sets that make sense only within Indian sector structures) are inconsistently or shallowly covered by both Indian tools (breadth-focused) and global tools (India-blind).

### Cross-Functional Challenge

- **Business Analyst view**: Six problems is the right number — enough to justify a real product, not so many that the MVP becomes unfocused. Problems 1, 2, and 3 are must-solve-in-V1 (they define daily-use value). Problems 4 and 5 are legitimate but can be phased (Section 9). Problem 6 is a durable differentiator versus Screener/Tickertape and should inform the financial-analysis engine (Section 10) from day one, even if the *feature surface* for it comes later — the underlying data model must capture promoter pledge %, RPT flags, and consolidated-vs-standalone deltas from the first data pipeline, or retrofitting later is expensive.
- **Credit Risk Analyst view**: Problem 4's "portfolio-structural risk during a drawdown" is frequently promised and rarely delivered credibly by retail tools because true stress correlation requires either (a) a long, clean price history per holding, or (b) factor-model proxies. For a bounded universe of a few hundred Indian large/mid-caps this is tractable with (a); it would not be tractable with a much larger universe. This is a direct argument reinforcing Commitment 2.5 (bounded universe).

---

# 4. User Personas

Because "personalization" without concrete personas is a slogan, four personas are defined. Persona 1 is you, explicitly, and is the one the MVP must nail; the other three exist to stress-test that the product generalizes rather than becoming an unshippable personal script.

### Persona 1 — "The Builder-Investor" (primary; this is the founder's own profile)
A recent STEM graduate with strong technical ability, mid-way through CFA Level 1, managing a personal portfolio and involved in the capital-allocation conversation for a family-controlled listed company (healthcare sector). Time-rich in analytical depth, time-poor in operational overhead (does not want to babysit a trading terminal). Wants long-horizon, fundamentals-first investing with occasional tactical overlays informed by CFA-level rigor (e.g., understanding when a "cheap" stock is a value trap via credit and quality signals). Highest-value features: financial analysis engine, thesis/journal system, personalized watchlist prioritization, and depth on the specific companies they hold or are evaluating (including MHPL itself, and its listed peers/comparables in Indian healthcare).

### Persona 2 — "The Working Professional Investor"
Mid-career professional, SIP-and-direct-equity investor, financially literate but not formally trained (no CFA/MBA), 30–60 minutes a week for investing decisions. Wants clarity, not complexity — the same underlying rigor as Persona 1, but surfaced with far less jargon and far more "so what should I do" framing. Stress-tests whether the explainability layer (Section 19) can serve two very different sophistication levels without becoming two different products.

### Persona 3 — "The Quality-Focused Long-Term Holder"
Older investor, capital preservation and dividend income focus, low portfolio turnover, high sensitivity to governance and balance-sheet risk, low interest in growth narratives. Stress-tests whether personalization (Section 12) genuinely reorders priorities — this persona should see debt covenant risk and promoter pledging before they see revenue growth charts, the inverse of what a growth-focused persona sees.

### Persona 4 — "The Sector Specialist"
An investor (could be a professional in a specific industry, e.g., healthcare, like family exposure to MHPL suggests) who wants deep, comparative, sector-specific analysis rather than broad-market coverage — heavy use of peer comparison, sector-specific KPIs (hospital occupancy, ARPOB in healthcare; same-store sales in retail, etc.), and less interest in generic screening. Stress-tests whether the platform's data model can hold sector-specific metrics without a schema explosion (Section 15 addresses this via an extensible-attributes pattern, not per-sector tables).

### Cross-Functional Challenge

- **UX Designer view**: The real risk with four personas is designing a UI that tries to please all of them simultaneously and pleases none. Resolution: **one underlying data and analysis model, but a "lens" system at the UI layer** — the same company page can be rendered with a "quality/governance-first" lens (Persona 3) or a "growth narrative-first" lens (Persona 2) by reordering, not duplicating, the same underlying cards. This is detailed in Section 18.
- **Principal PM view**: Persona 1 is the only persona the MVP is *built for*; Personas 2–4 exist purely as design guardrails to prevent the product from becoming an unlabeled personal script that can't be described coherently to an admissions committee or a GitHub visitor. Do not build features for Personas 2–4 in V1 — only make sure V1's architecture doesn't foreclose serving them later.

---

# 5. Competitive Analysis

| Product | Core strength | Core weakness relative to EquiSense's mission | Lesson to take |
|---|---|---|---|
| **Screener.in** | Best-in-class raw fundamental data depth for Indian equities; a genuine industry-respected tool; extremely fast custom screening query language | No personalization, no memory of user reasoning, no explainability layer beyond raw numbers, no portfolio-level intelligence | Data depth and query speed are table stakes for credibility with a serious Indian investor audience — EquiSense must not be worse than Screener at the numbers, even though it competes on a different axis |
| **Tickertape** | Polished consumer UX, "smallcase"-style thematic investing, clean mobile-first design, PMS/smallcase integration | Optimized for discovery and thematic browsing, not deep single-analyst workflows; AI features (if any) are shallow; no real thesis/journal concept | UX polish matters even for an analytically deep tool — professional-grade does not mean visually dense or ugly |
| **Moneycontrol** | Breadth (every listed company, every news item), incumbency, free | Cluttered, ad-driven, no personalization, no coherent analytical philosophy, "everything for everyone" | Breadth-for-everyone is a trap EquiSense must explicitly avoid (Commitment 2.5) |
| **Simplywall.st** (global, some India coverage) | Genuinely good visual explainability ("snowflake" scores, visual breakdowns of value/future/past/health/dividend) | Score methodology is a black box in practice despite visual friendliness; not built around personal thesis-tracking; India coverage is shallow versus domestic tools | Visual explainability done well is a differentiator worth emulating in spirit — Section 19 borrows the *principle* (multi-axis visual scoring) but commits to fully transparent, inspectable methodology, which Simplywall.st does not offer |
| **Traditional Excel/Google Sheets models** | Total flexibility, the current status quo for most serious individual investors including the founder | Doesn't scale across companies, no historical versioning of "what I believed and why," no automated data refresh, brittle | This is the actual incumbent EquiSense has to beat for Persona 1 — not the branded competitors. If EquiSense isn't clearly better than "a well-built personal spreadsheet," it has failed regardless of how it compares to Tickertape |
| **Bloomberg Terminal / FactSet** (aspirational reference, not a real competitor at this scale) | Institutional-grade analytics, deep fixed income/derivatives coverage, real-time everything | Cost, complexity, and scope utterly inappropriate for a personal/individual tool | The *aesthetic and rigor* (dense, data-forward, no wasted chrome) is worth referencing in the UX section as a design-language ancestor, without pretending to replicate institutional data coverage |

**Positioning statement:** *EquiSense is not trying to out-Screener Screener on breadth, nor out-Tickertape Tickertape on consumer polish. It occupies the position no current Indian product occupies: a personalized, explainable, memory-carrying analytical workspace for a bounded set of companies one investor actually cares about — closer in spirit to a private analyst's workbench than a public screening tool.*

### Cross-Functional Challenge

- **Equity Research Analyst view**: The comparison to a "well-built personal spreadsheet" (not the branded apps) as the real incumbent is the single most important insight in this section, and it must discipline every later architectural decision. A spreadsheet has near-zero marginal cost to add a new ratio; EquiSense's ratio/analysis engine must not feel slower or more rigid than that baseline, or the switching cost won't be worth it even with better UX.
- **Financial Data Scientist view**: Simplywall.st's "black box in practice despite visual friendliness" critique is worth internalizing precisely because it's the failure mode EquiSense is most likely to fall into if the Explainability Strategy (Section 19) is treated as a UI-polish task rather than an architectural constraint on the scoring engine itself (Section 10, Section 13). Explainability must be designed into the scoring math, not retrofitted as a tooltip.

---

# 6. Feature Inventory

This is the master feature list. Each entry carries the required attributes. Complexity is rated Low / Medium / High / Very High relative to a solo, AI-assisted developer's realistic throughput — not relative to an enterprise engineering team. Phases reference the roadmap in Section 9 (P0 = MVP, P1 = Months 4–8, P2 = Months 9–14, P3 = Months 15–18+).

## 6.1 Financial Analysis Engine Features

| Feature | Purpose | Business value | User value | Dependencies | Complexity | Phase | MVP? |
|---|---|---|---|---|---|---|---|
| Standardized financial statement ingestion (IS/BS/CFS, standalone + consolidated) | Normalize heterogeneous filing data into one schema | Foundation for every other analytical feature; without this nothing else works | Consistent numbers across companies without manual spreadsheet re-entry | Data acquisition pipeline (Sec. 16) | High | P0 | **Yes** |
| Core ratio engine (liquidity, leverage, profitability, efficiency, per-share) | Compute the ~25-30 ratios that form the backbone of fundamental analysis | Reusable computation layer every downstream feature depends on | Instant, correct, comparable ratios without manual Excel formulas | Statement ingestion | Medium | P0 | **Yes** |
| Multi-year trend view per metric | Show trajectory, not snapshot | Trajectory is more decision-relevant than a point-in-time number | Answers "is this improving or deteriorating" at a glance | Ratio engine | Low | P0 | **Yes** |
| ROIC / ROIC-vs-WACC reinvestment analysis | Answer "does growth here create or destroy value" | Core CFA-curriculum-aligned analytical lens; differentiates from generic screeners | Directly informs quality-of-growth judgment | Ratio engine, cost-of-capital estimation | Medium | P0 | **Yes** |
| Cash flow quality analysis (accruals ratio, CFO/NI, capex intensity) | Detect earnings manipulation risk and capital intensity reality | Protects against the single most common fundamental-analysis failure mode (trusting reported earnings) | Builds real analytical skill, not just number-watching | Statement ingestion | Medium | P0 | **Yes** |
| Reverse DCF / implied-growth valuation | Show what growth rate is *priced in*, rather than predicting a "fair value" | Reframes valuation as a falsifiable, explainable question rather than a black-box "fair value" number | Directly supports Commitment 2.4 (no price targets) while still being genuinely useful for valuation judgment | Ratio engine, forecast assumptions from user | Medium-High | P0 | **Yes** |
| Traditional multi-scenario DCF (as a secondary, clearly-labeled-as-assumption-driven tool) | Support explicit, user-owned valuation modeling | Some users/CFA topics require a "forward" DCF, not just reverse | Educational value tied directly to CFA equity valuation curriculum | Reverse DCF infrastructure | Medium | P1 | No |
| Altman Z-score / distress indicators | Flag balance-sheet distress risk | Direct, well-validated academic grounding; avoids "AI black box" criticism because methodology is fully public | Protects capital — arguably higher value than any "upside" feature | Ratio engine | Low-Medium | P0 | **Yes** |
| Piotroski F-Score (or equivalent quality composite) | Cheap, transparent, well-validated quality signal | Extremely high value-to-effort ratio; fully explainable by construction | Fast quality triage across watchlist | Ratio engine | Low | P0 | **Yes** |
| Peer/sector comparison tables | Contextualize a company's numbers against real comparables | Numbers without peer context are frequently misleading (e.g., "high" margin in a structurally high-margin sector) | Essential for Persona 4 (Sector Specialist) and for MHPL/healthcare-peer analysis specifically | Sector taxonomy, statement ingestion for peer set | Medium | P0 | **Yes** |
| Related-party transaction & promoter pledge tracking | Surface India-specific governance risk factors | Direct differentiator vs. both domestic and global tools (Problem 6) | Protects against a very real, very Indian-market-specific risk class | Filing-level data extraction (harder than headline financials) | High | P1 | No |
| Segment-level reporting breakdown | Understand revenue/profit composition below the consolidated level | Needed for diversified holding companies and conglomerates | Deeper diagnostic capability for complex businesses | Statement ingestion, segment data availability | Medium-High | P2 | No |
| Sector-specific KPI tracking (extensible attributes) | Support metrics that only make sense per-industry (ARPOB, same-store sales, etc.) | Serves Persona 4 without a schema explosion | Enables genuinely sector-literate analysis, a rare feature | Extensible data schema (Sec. 15) | Medium | P2 | No |

## 6.2 Portfolio Intelligence Engine Features

| Feature | Purpose | Business value | User value | Dependencies | Complexity | Phase | MVP? |
|---|---|---|---|---|---|---|---|
| Portfolio holdings tracker (manual entry + optional broker statement import) | Single source of truth for current positions | Foundation for all portfolio-level analytics | Removes need for a separate spreadsheet | None (core data model) | Low-Medium | P0 | **Yes** |
| Position-level P&L and XIRR | Standard, correct performance measurement | Table stakes; must be correct, not approximate | Trustworthy performance tracking | Holdings tracker, price history | Low | P0 | **Yes** |
| Sector/market-cap/style concentration diagnostics | Reveal unintended concentration risk | Most retail portfolios are more concentrated than the holder realizes | Directly actionable diversification insight | Holdings tracker, company classification data | Low-Medium | P0 | **Yes** |
| Correlation & drawdown co-movement analysis across holdings | Quantify true diversification (or lack of it) during stress | Addresses Problem 4 with real rigor, not a vague "risk score" | Answers "if the market drops 20%, how correlated is my actual pain" | Price history (2+ years per holding), holdings tracker | Medium-High | P1 | No |
| Rebalancing / drift-from-target diagnostics | Compare actual allocation to stated target allocation | Operationalizes the user's own stated strategy (personalization) | Turns personalization settings into an actionable output, not just a profile field | Personalization engine, holdings tracker | Medium | P1 | No |
| Dividend income tracking & forward income projection | Track and project income-oriented outcomes | High-value for Persona 3 specifically | Directly useful, low computational complexity | Holdings tracker, dividend history data | Low | P1 | No |
| Tax-aware position aging (LTCG/STCG threshold tracking, India-specific) | Prevent avoidable tax-inefficient decisions | Genuinely useful, India-specific, low-competition feature space | Concrete money-saving utility | Holdings tracker with purchase-date lots | Medium | P1 | No |
| Portfolio-level quality/distress aggregate score | Roll up company-level quality scores to a portfolio view | Extends Section 6.1's quality scoring to the portfolio level | "How risky is my portfolio as a whole" in one view | Company quality scores, holdings tracker | Low | P2 | No |
| What-if position sizing simulator (pre-trade) | Model impact of a prospective buy/sell on portfolio composition before acting | Supports disciplined, rule-based capital allocation | Directly ties into the user's stated investment rules (personalization) | Holdings tracker, personalization engine | Medium | P2 | No |

## 6.3 Personalization Engine Features

| Feature | Purpose | Business value | User value | Dependencies | Complexity | Phase | MVP? |
|---|---|---|---|---|---|---|---|
| Investor profile capture (horizon, risk tolerance, sector preference, valuation philosophy, rules) | Structured representation of the user's stated philosophy | This is the spine of the entire "personalization" pillar of the product | Makes the platform feel individually built, not generic | None (core data model) | Low-Medium | P0 | **Yes** |
| Profile-driven re-ranking of watchlist/dashboard priority | Make the profile *do* something visible daily | Prevents personalization from being decorative (Commitment 2.2) | The single most important proof-point that personalization is real | Investor profile, quality/ratio engine | Medium | P0 | **Yes** |
| Rule-violation flagging (e.g., "you said max position size 8%, this trade breaches that") | Operationalize the user's own stated discipline | Directly supports better decisions, the core mission | Prevents the single most common failure mode in individual investing: rule drift | Investor profile, holdings tracker | Medium | P1 | No |
| Adaptive learning from user behavior (e.g., consistently ignoring high-dividend flags implies low actual dividend preference) | Refine the profile from revealed, not just stated, preference | Differentiates from a static "preferences" settings page | Reduces the burden of manually maintaining an accurate profile | Sufficient behavioral/usage history | High | P2 | No |
| "Lens" system — same data, reordered/reframed by persona-style profile (growth-first vs. quality-first vs. income-first) | Serve very different investor styles from one data model | Prevents schema/UI explosion across personas | Makes the tool feel bespoke without being bespoke-built | Investor profile, UI component architecture | Medium | P1 | No |

## 6.4 AI Capability Features
*(Full philosophy and boundary discussion in Section 13; this table is the inventory only.)*

| Feature | Purpose | Business value | User value | Dependencies | Complexity | Phase | MVP? |
|---|---|---|---|---|---|---|---|
| Financial statement narrative interpretation ("explain this quarter's numbers in plain language, with the actual figures cited") | Translate ratio-engine output into analyst-style prose | Directly differentiates from raw-number tools | Saves real time; genuinely explains rather than just restates | Ratio engine, LLM integration | Medium | P0 | **Yes** |
| Investment thesis generation assistant (drafts a structured thesis from user inputs + company data, for the user to edit) | Reduce blank-page problem in thesis writing | Supports the Research Journal / Thesis system (Sec. 22–23), a core differentiator | Encourages the *habit* of thesis-writing by lowering the activation cost | Financial analysis engine, thesis data model | Medium | P0 | **Yes** |
| Natural language querying over the user's own data ("which of my holdings have declining ROIC for 2+ years") | Let the user ask questions instead of building filters manually | High perceived-intelligence-to-effort ratio | Fast, flexible access to one's own portfolio/watchlist data | Structured data model, query translation layer | Medium-High | P1 | No |
| Company quality scoring (composite, fully explainable) | Single defensible "how good is this business" signal | Anchors the dashboard's default sort order | Fast triage across a watchlist | Ratio engine, weighting methodology | Medium | P0 | **Yes** |
| Peer comparison narrative ("how does this company's margin trajectory compare to its three closest peers, and why might that be") | Automate the contextualization step analysts do manually | Genuinely useful synthesis step, not just data restatement | Saves the single most time-consuming manual research step | Peer comparison data, LLM integration | Medium | P1 | No |
| Portfolio diagnostic narrative (concentration, correlation, and rule-adherence summarized in prose) | Turn Section 6.2's numeric diagnostics into a readable weekly/monthly briefing | High retention value — a reason to open the app on a cadence | Feels like a real analyst reviewing your book periodically | Portfolio intelligence engine | Medium | P1 | No |
| Anomaly detection (unusual jump in receivables, sudden margin change, unusual promoter pledge increase, etc.) | Surface what deserves attention without manual monitoring | High value-to-effort; statistically groundable, fully explainable | Directly prevents the "I didn't notice until it was too late" failure mode | Statement ingestion, statistical baselining | Medium-High | P1 | No |
| Distress/quality-decline early-warning (Z-score trend + qualitative flags combined) | Extend static distress scoring into a trend-aware warning | Meaningfully more useful than a single Z-score snapshot | Concrete downside protection | Distress scoring, historical trend data | Medium | P2 | No |
| Scenario simulation ("if revenue growth slows to X% and margins compress by Y bps, what happens to key ratios and implied valuation") | Support structured "what would have to be true" thinking | This is the *correct*, non-prediction way to do "what if" analysis (Sec. 26) | Directly builds the analytical habit CFA curriculum teaches | Reverse DCF/valuation engine | Medium-High | P1 | No |
| Research assistant / knowledge retrieval over uploaded filings, notes, and saved articles | Let the user query their own accumulated research corpus | Genuinely valuable as the user's research library grows over years | Turns the platform into a permanent knowledge base, increasing switching cost (in a healthy way) | Document ingestion, retrieval infrastructure | High | P2 | No |
| Watchlist prioritization ranking ("given your profile and current watchlist, here's what deserves attention this week") | Combat watchlist rot (items added and never revisited) | Directly ties personalization + AI into a recurring, high-retention action | Concrete answer to "what should I look at today" | Personalization engine, quality scoring, anomaly detection | Medium | P1 | No |

## 6.5 Research Journal, Thesis Management, Watchlists, Alerts

| Feature | Purpose | Business value | User value | Dependencies | Complexity | Phase | MVP? |
|---|---|---|---|---|---|---|---|
| Structured investment thesis object (thesis statement, key assumptions, invalidation triggers, target review date) | Force explicit, falsifiable reasoning at time of decision | This is arguably the single highest-differentiation feature in the entire product | Directly solves Problem 3 (no memory of one's own reasoning) | Core data model | Medium | P0 | **Yes** |
| Freeform research journal (dated notes, linkable to companies) | Lower-friction capture for reasoning that isn't yet thesis-ready | Cheap to build, high daily-use value | Natural home for CFA-study-linked notes on real companies | Core data model | Low | P0 | **Yes** |
| Watchlist with manual + AI-assisted prioritization | Track companies not yet owned | Table stakes for any investment tool, but tied into personalization here | Central daily-use screen | Company data, personalization | Low | P0 | **Yes** |
| Thesis invalidation alerting (system checks stated triggers against new data and flags breaches) | Close the loop between thesis and reality | This operationalizes Problem 3 fully — not just recording a thesis but *checking* it | The most "wow" feature for a sophisticated user, and fully explainable by construction | Thesis object, statement ingestion, alerting infrastructure | Medium-High | P1 | No |
| Price/valuation-metric alerts (e.g., "P/E below 5-year median") | Standard, expected utility feature | Table stakes | Straightforward, expected utility | Price/ratio history | Low | P1 | No |
| Governance/event alerts (credit rating change, auditor resignation, promoter pledge change) | Surface high-signal, low-frequency events | High value, India-specific, addresses Problem 6 | Protects against being blindsided by material governance events | Filing-level data extraction | Medium-High | P2 | No |

### Cross-Functional Challenge

- **Principal PM view**: The MVP column above is the real decision this section produces. It is intentionally narrow: standardized ingestion, ratio engine, trend view, ROIC/reinvestment analysis, cash flow quality, reverse DCF, Z-score, F-Score, peer comparison, portfolio tracker with P&L/XIRR and concentration diagnostics, investor profile with profile-driven re-ranking, statement narrative interpretation, thesis generation assistant, quality scoring, thesis object, research journal, and watchlist. That is already a substantial build. Everything else is explicitly *not* MVP, and Section 8 defends this cut in detail.
- **Startup CTO view**: The riskiest MVP item from a build-complexity standpoint is **standardized statement ingestion** — everything else is downstream of it, and Indian filing data is genuinely messy (inconsistent XBRL quality, standalone/consolidated ambiguity, restatements). This should be the first thing built and de-risked, even before UI, because if this doesn't work reliably the rest of the roadmap doesn't matter. See Section 14 and 16.
- **ML Researcher view**: Note that nothing in the P0/MVP list requires a trained model — Z-score, F-Score, ratio engine, and reverse DCF are all closed-form/rules-based. The only "ML-flavored" P0 items (narrative interpretation, thesis generation assistant) are LLM-orchestration tasks, not trained-model tasks. This is deliberate: it means the MVP has zero training-data or model-ops risk. True ML (anomaly detection baselining, adaptive personalization) is correctly deferred to P1/P2 where there's enough historical usage data to make it meaningful rather than performative.

---

# 7. Product Boundaries — What Should Intentionally NOT Be Built

Stated as firmly as the feature inventory, because an unbounded product is not a product.

**7.1 — No brokerage or order execution, ever.** This is a decision-support tool. The moment it can place a trade, it inherits an entirely different regulatory, security, and liability surface (SEBI broker/RIA registration considerations, custody of funds, KYC), which is fundamentally incompatible with a solo-built personal/portfolio project. If broker integration ever happens, it is **read-only statement import**, never order placement.

**7.2 — No price prediction, target price generation, or buy/sell signals**, as already established in Commitment 2.4. This is worth restating as a boundary because it will be the single most common well-intentioned feature request ("just show a fair value estimate") that must be declined or carefully reframed (see reverse-DCF framing in Section 6.1, which answers the same underlying curiosity without crossing the line).

**7.3 — No social/community features (comment sections, public thesis sharing, follower counts).** This is a personal analytical workspace, not a social platform. Social features would multiply moderation, abuse, and privacy surface area for a product that has exactly one intended user for the foreseeable future, and they add zero analytical value per Commitment 2.1.

**7.4 — No real-time tick-by-tick market data or intraday trading dashboards.** This is a long-term fundamentals platform (Commitment 2.9's "designed so it could extend" does not extend to intraday trading — that's a different product entirely). End-of-day data is sufficient for every feature in Section 6 and dramatically simplifies data infrastructure (Section 16).

**7.5 — No attempt to cover all ~5,000+ NSE/BSE-listed companies in V1.** Directly from Commitment 2.5. A bounded universe (a few hundred companies: current holdings, active watchlist, index constituents of 1–2 relevant indices, and sector peer sets for whatever's being analyzed) is sufficient and dramatically reduces data-acquisition and data-quality burden.

**7.6 — No generic news aggregation or sentiment-from-news-headlines features.** This is a crowded, low-differentiation space dominated by Moneycontrol-style products, and headline sentiment is a weak, noisy signal that risks violating Commitment 2.4's spirit (it's adjacent to "AI says buy" if not handled with extreme care). If news relevance ever gets built, it is scoped tightly to *filing-level and regulatory events* (Section 6.5's governance alerts), not general news sentiment.

**7.7 — No mobile-native app in V1.** A responsive web application is sufficient for a daily-use personal analytical tool used primarily during considered decision-making (not on-the-go glancing, which is what mobile-native optimizes for). Building and maintaining a separate mobile app is a multi-year commitment inappropriate for a solo builder at this stage. (Revisit only if usage patterns genuinely demand it — Section 31.)

**7.8 — No attempt at multi-user/team features, sharing, or permissions systems** beyond what's needed for the user's own single account. This is not a SaaS product in V1; multi-tenancy is speculative complexity with zero current user demand (Commitment 2.6).

**7.9 — No fixed-income, derivatives, or alternatives coverage until the CFA curriculum and the founder's own need justify it.** The product starts as an *equity* intelligence platform. Expanding asset-class coverage prematurely would dilute the depth-over-breadth positioning (Section 5) before equity depth is even fully realized.

**7.10 — No attempt to build proprietary data feeds, web-scraping infrastructure at hostile scale, or anything that risks ToS violations with exchanges or data providers.** Data acquisition (Section 14) must stay within clearly licensed or explicitly-permitted-for-personal-use boundaries. This is both an ethical and a practical constraint — a project whose foundation is a fragile, adversarial scraping pipeline is not "industry-grade," it's a liability.

### Cross-Functional Challenge

- **CFA Charterholder / PM view**: 7.2 will be the hardest boundary to hold in practice, because users (and the founder, six months in) will keep wanting "just tell me if it's cheap." The resolution already built into Section 6.1 (reverse DCF: "what growth is priced in") is the correct way to satisfy that curiosity without violating the boundary, and it should be pointed to explicitly whenever this pressure arises rather than re-litigated each time.
- **Startup CTO view**: 7.10 is easy to state and easy to violate accidentally via convenience libraries or unofficial scrapers. The Data Acquisition Strategy (Section 14) must name specific, permitted sources up front so this boundary has a concrete enforcement mechanism, not just a principle.

---

# 8. MVP Definition

**The MVP is defined as: a single investor can add a company, see a full standardized financial analysis (ratios, trends, ROIC/reinvestment, cash flow quality, reverse DCF, Z-score, F-score, peer comparison) generated from real filing data; can log a structured investment thesis and freeform journal entries against it; can track their actual portfolio holdings with correct P&L, XIRR, and concentration diagnostics; can set an investor profile that visibly reorders what the dashboard prioritizes; and can ask the AI layer to explain any of the above in plain language, fully citing the underlying numbers.**

This is deliberately not a trivial MVP. It is, however, a *coherent* one: every element in it directly serves the "replace my spreadsheets" and "demonstrate real analytical + engineering ability" goals simultaneously, and nothing in it depends on features from later phases.

**What is explicitly excluded from MVP** (with justification, since every exclusion here was a genuine candidate in Section 6):

- **Correlation/drawdown co-movement analysis** — genuinely valuable but requires 2+ years of clean price history per holding and is computationally/conceptually heavier than concentration diagnostics, which deliver most of the same "am I over-concentrated" insight more simply. Correct sequencing, not lack of value.
- **Rule-violation flagging and thesis invalidation alerting** — both depend on the thesis object and profile existing first *and* being used for a while to have real triggers to check against. Building the checking mechanism before there's meaningful history to check would be premature.
- **Related-party transaction / promoter pledge tracking** — high value (Problem 6) but the hardest data-extraction problem in the entire inventory (it requires parsing filing text/tables, not just standardized financial statements). Correctly sequenced after the core statement pipeline is proven reliable.
- **Natural language querying, anomaly detection, scenario simulation** — all genuinely valuable AI capabilities, but all depend on either (a) a meaningful corpus of the user's own data existing first, or (b) statistical baselines that need historical data to be non-arbitrary. Building them against an empty/thin dataset would produce unconvincing demos.
- **Any broker integration, even read-only** — not because it lacks value, but because manual entry is sufficient to prove the core loop, and broker statement formats are a genuine integration project better tackled once the core product's value is already proven.

**MVP success criteria** (concrete, falsifiable):
1. The founder personally stops opening the Colgate-Palmolive-style Jupyter analysis notebook and the portfolio spreadsheet for day-to-day company review within 4–6 weeks of MVP completion.
2. At least 15–20 real companies (the founder's actual holdings + active watchlist, likely including MHPL and its healthcare peer set) are fully modeled with correct, verified financials.
3. At least 5 structured investment theses exist with genuine invalidation criteria, not placeholder text.
4. The reverse-DCF and Z-score/F-score outputs are manually spot-checked against a known-correct source (e.g., a manually built spreadsheet for 2-3 companies) and match within acceptable rounding tolerance.

### Cross-Functional Challenge

- **Software Architect view**: Success criterion 1 is the most important one in this entire document — it's a behavioral, not a feature-completion, criterion. A PM discipline worth enforcing throughout the build: if a feature is completed but criterion 1 still isn't true, something in the MVP scope was wrong (either missing or over-built elsewhere), and scope should be revisited rather than proceeding blindly to Phase 1.
- **Financial Data Scientist view**: Success criterion 4 (manual spot-check against a hand-built spreadsheet) is non-negotiable and should happen *before* any AI narrative layer is trusted to describe the numbers. An eloquent AI explanation of a wrong ROIC calculation is worse than no explanation at all — it launders a data error into false confidence. This validation step belongs in the testing strategy (Section 29) as a hard gate, not an afterthought.

---

# 9. Multi-Year Roadmap

Sequenced against realistic solo-build velocity and, per Commitment 2.8, against CFA curriculum progression where that alignment is genuine (not forced).

## Phase 0 — MVP (Months 1–3)
**Theme: Prove the core loop works and is trustworthy.**
Standardized statement ingestion for a bounded universe (~15-30 companies) → ratio engine → trend views → ROIC/reinvestment analysis → cash flow quality → reverse DCF → Z-score/F-score → peer comparison tables → portfolio holdings tracker with P&L/XIRR and concentration diagnostics → investor profile with visible dashboard re-ranking → statement narrative interpretation (AI) → thesis generation assistant (AI) → structured thesis object → freeform journal → watchlist.
**CFA alignment**: This phase draws almost entirely on CFA Level 1 Financial Statement Analysis, Equity Investments, and Corporate Issuers material — directly reinforcing what's being studied *right now*, which is the strongest possible alignment case in the whole roadmap.

## Phase 1 — Depth & Personalization Proof (Months 4–8)
**Theme: Make personalization structurally real, and extend portfolio intelligence.**
Correlation/drawdown co-movement analysis → rebalancing/drift diagnostics → dividend income tracking → tax-aware position aging → rule-violation flagging → thesis invalidation alerting → natural language querying over own data → peer comparison narrative (AI) → portfolio diagnostic narrative (AI) → anomaly detection → scenario simulation → price/valuation alerts → traditional multi-scenario DCF → "lens" system for persona-style reframing.
**CFA alignment**: Roughly coincides with Level 1 completion and Level 2 start — Level 2's much heavier emphasis on valuation (equity, fixed income intro) and quantitative methods maps directly onto the scenario simulation and multi-scenario DCF work.

## Phase 2 — Governance Depth & India-Specific Differentiation (Months 9–14)
**Theme: Build the features that make this specifically an Indian-market-literate tool, not a generic one.**
Related-party transaction & promoter pledge tracking → governance/event alerts → segment-level reporting breakdown → sector-specific KPI tracking via extensible attributes → portfolio-level quality/distress aggregate score → what-if position sizing simulator → research assistant/knowledge retrieval over uploaded filings and notes.
**CFA alignment**: Level 2's Financial Statement Analysis (more advanced: intercorporate investments, multinational operations) and Corporate Issuers (governance, ESG) map directly onto the RPT/pledge tracking and governance alert work.

## Phase 3 — Maturity, Polish, and Long-Horizon Differentiators (Months 15–18+)
**Theme: The features that require enough historical usage data or maturity to be genuinely good rather than performative.**
Adaptive learning from revealed user behavior → distress/quality-decline early-warning trend analysis → deeper scenario/stress-testing tied to portfolio-level correlation data → expanded universe coverage if justified by real usage → potential (careful, boundary-respecting) read-only broker statement import → open-source packaging and documentation polish for public release (Section 32) → admissions-cycle-timed case-study writeups (Section 34).
**CFA alignment**: Level 2 completion / Level 3 start — Level 3's portfolio management emphasis (IPS construction, asset allocation, private wealth) is the natural conceptual home for the adaptive personalization and portfolio-level stress-testing work, making this the most sophisticated phase and appropriately the last one.

### Cross-Functional Challenge

- **Principal PM view**: The CFA alignment is presented as a genuine sequencing constraint, not decoration, because it solves a real prioritization problem: when in doubt about what to build next, "what CFA topic am I studying right now" is a better tiebreaker than arbitrary sprint planning for a project whose explicit purpose (per Commitment 2.8) includes demonstrating applied learning.
- **Startup CTO view**: Phase boundaries should be treated as soft, not hard, gates. If Phase 0 takes 4 months instead of 3 because statement ingestion is harder than expected (the Startup CTO's Section 6.1 flag), that's an acceptable and expected outcome — extending Phase 0 is far better than rushing into Phase 1 on top of an unreliable ingestion layer.

---

# 10. Financial Analysis Engine

This is the analytical core of the product and the section where "industry-grade" is most directly tested — a professional equity analyst must recognize every number here as methodologically sound, not simplified-for-consumers.

## 10.1 Statement Standardization Layer
Raw filings (standalone and consolidated Income Statement, Balance Sheet, Cash Flow Statement) are mapped into one internal canonical schema per statement type, regardless of source-format quirks (different filers use different line-item labels for economically identical items — this normalization is the single hardest and most important data-engineering task in the product; see Section 14 and 16).

Key design decision: **store both standalone and consolidated figures as first-class, separately-queryable data**, never collapse them into one "the" number. For holding-company or conglomerate structures (directly relevant given family involvement with MHPL), the standalone-vs-consolidated gap is itself analytically meaningful and must never be silently resolved by picking one.

## 10.2 Ratio Engine
Organized into five families, each computed from the canonical schema so ratio definitions are centralized in exactly one place (never recomputed ad hoc per feature — this single-source-of-truth design is what prevents the "different screens show slightly different numbers" bug class that plagues hand-rolled spreadsheets):

- **Liquidity**: current ratio, quick ratio, cash ratio, working capital trend
- **Leverage/Solvency**: debt/equity, net debt/EBITDA, interest coverage, debt/EBITDA trend, off-balance-sheet lease adjustment where material
- **Profitability**: gross/operating/net margin, ROE (with DuPont 3-way and 5-way decomposition), ROA, ROIC
- **Efficiency**: asset turnover, inventory days, receivable days, payable days, cash conversion cycle
- **Per-share & valuation-adjacent**: EPS (basic/diluted), book value per share, P/E, P/B, EV/EBITDA, dividend yield — all computed, none predicted

Every ratio output carries: the formula used (visible on demand), the raw inputs (linked back to source filing line items), and the multi-year trend, not just the current value. This is the concrete implementation of Commitment 2.1 (reasoning over data) and Commitment 2.3 (explainability) at the most granular level of the product.

## 10.3 ROIC vs. WACC Reinvestment Analysis
Computes ROIC (NOPAT / invested capital, with explicit, inspectable treatment of operating leases and goodwill) against an estimated WACC (CAPM-based cost of equity using a reasonable beta source and equity risk premium, weighted with post-tax cost of debt from actual interest expense and debt levels). The output is explicitly framed as **"this company's growth is currently value-accretive / value-neutral / value-destructive,"** with the WACC estimate's own assumptions fully exposed and user-adjustable (this matters enormously for explainability — WACC estimates are inherently assumption-sensitive, and hiding that sensitivity would be methodologically dishonest).

## 10.4 Cash Flow Quality Analysis
Computes the accruals ratio ((Net Income − CFO) / Total Assets), CFO/Net Income ratio over multiple years, and capex intensity (capex/revenue, capex/depreciation) to flag potential earnings-quality concerns. This is deliberately one of the highest-priority MVP features because it is the single most common way unsophisticated fundamental analysis gets fooled — trusting a clean-looking income statement without checking whether cash actually followed the reported profit.

## 10.5 Reverse DCF (Implied Expectations Valuation)
Rather than producing a "fair value" (a forecast, and therefore prohibited under Commitment 2.4), this engine solves *backward* from the current market price: **given the current price, what perpetual growth rate / near-term growth trajectory would have to be true to justify it, under a stated set of margin and reinvestment assumptions?** This is then compared against the company's own historical growth rates and the growth rates of its peer set, producing a fully explainable, non-predictive answer to "is the market pricing in something reasonable or something aggressive."

## 10.6 Traditional Multi-Scenario DCF (Phase 1)
A conventional forward DCF is *also* offered, but explicitly labeled at every touchpoint as "assumption-driven, not a prediction" — the user sets growth/margin/discount-rate assumptions explicitly (no hidden defaults presented as "the" forecast), and the output is a *range* driven by scenario toggles (Section 26), never a single point estimate presented as an answer.

## 10.7 Distress & Quality Scoring
**Altman Z-score** (or the appropriate India-adapted variant, given differences in typical capital structure and the private/public split relevant to Indian markets) for distress risk, and a **Piotroski F-Score**-style composite (9 binary fundamental-improvement signals) for quality triage. Both are chosen specifically because they are **fully public, peer-reviewed, well-validated methodologies** — this is a deliberate explainability and credibility decision: a proprietary "EquiSense Quality Score" invented from scratch would face justified skepticism about arbitrary weighting, whereas these scores' methodology is independently verifiable by anyone (including an admissions committee or interviewer) who wants to check.

## 10.8 Peer/Sector Comparison
Every company is mapped to a sector taxonomy (starting from a standard classification like NSE's own industry classification, not an invented one) and to a manually curated peer set (automated peer-matching by SIC/industry code alone is unreliable for a market with as much conglomerate/diversified-holding complexity as India's — manual curation, at bounded-universe scale, is the higher-quality choice per Commitment 2.6's "simpler is better if it gets 95% of the value").

### Cross-Functional Challenge

- **CFA Charterholder view**: Section 10.3's WACC-assumption transparency is the correct call and should be treated as a template for the whole product: *any* time an estimate requires an assumption a reasonable analyst could disagree with (equity risk premium, terminal growth rate, beta source), the assumption must be visible and editable, never silently baked in. This is what separates "explainable" from "shows its work sometimes."
- **Equity Research Analyst view**: 10.8's decision to manually curate peer sets rather than automate via industry codes is correct for Indian markets specifically — SIC/GICS-style codes badly misclassify Indian conglomerates and diversified holding companies (again, directly relevant to a healthcare company with potential adjacent business lines). This manual curation cost is bounded and acceptable precisely because Commitment 2.5 keeps the universe small.
- **Credit Risk Analyst view**: The standard Altman Z-score was calibrated on U.S. manufacturing firms; using it uncritically on Indian companies (especially services/financial/asset-light businesses) without at least flagging the model's original calibration context would be methodologically sloppy for a document that claims industry-grade rigor. The build must either use a documented India/EM-adapted variant or, at minimum, clearly caveat the score's applicability per sector — this is a testing-strategy item (Section 29), not just a modeling footnote.

---

# 11. Portfolio Intelligence Engine

## 11.1 Holdings & Transaction Model
Portfolio state is derived from a **transaction ledger** (buy/sell lots with date, quantity, price, fees), never stored as a single mutable "current holding" — this is the correct design because it's the only representation that supports XIRR, tax-lot-aware LTCG/STCG tracking (Phase 1), and historical "what did my portfolio look like on date X" queries, all from one underlying data model rather than three separate ones.

## 11.2 Performance Measurement
XIRR is the primary money-weighted return measure (correct for a portfolio with irregular cash flows from periodic buying, which is the realistic pattern for an individual investor, versus time-weighted return which is more appropriate for professionally-managed funds with controlled cash flow timing). Absolute and CAGR-based views are secondary/supplementary, always clearly labeled so they're never confused with each other.

## 11.3 Concentration Diagnostics
Computed along four axes simultaneously: single-position weight, sector weight, market-cap-band weight (large/mid/small), and — distinctively — **quality-score-band weight** (what fraction of the portfolio sits in the platform's own high/medium/low quality-score tiers, from Section 10.7). This last axis is a genuine differentiator: most portfolio tools show sector/stock concentration but very few show "how much of your capital sits in fundamentally fragile businesses," which is arguably more decision-relevant.

## 11.4 Correlation & Drawdown Co-Movement (Phase 1)
Using 2+ years of daily/weekly price history per holding, computes pairwise correlation and, more usefully, **conditional correlation during the worst historical drawdown periods** (correlations often spike toward 1 exactly when diversification is needed most — an unconditional correlation matrix can be dangerously misleading, and a genuinely rigorous tool must show the conditional version, not just the average).

## 11.5 Rebalancing & Drift Diagnostics (Phase 1)
Compares actual allocation against the user's stated target allocation (from the personalization profile, Section 12) along the same axes as 11.3, surfacing drift beyond a user-configurable tolerance band — deliberately *not* auto-suggesting trades (that would edge toward advisory/signal territory prohibited under Section 7.2's spirit), only surfacing the diagnostic fact of drift and letting the user decide.

## 11.6 Tax-Aware Position Aging (Phase 1, India-specific)
Tracks each tax lot's holding period against India's LTCG/STCG thresholds for listed equity, surfacing "X days until this lot becomes long-term" as a concrete, actionable, India-specific utility feature with essentially no direct competitor doing this well at this level of care.

### Cross-Functional Challenge

- **Portfolio Manager view**: 11.4's insistence on *conditional* (stress-period) correlation rather than unconditional average correlation is the single most professionally-credible detail in this section — it's exactly the distinction a professional risk desk cares about and a retail tool almost never implements correctly. It should be highlighted in any resume/portfolio narrative about this project specifically.
- **Software Architect view**: 11.1's transaction-ledger-as-source-of-truth design is the correct foundational choice and must not be compromised for MVP convenience (e.g., "just store current quantity for now, add lots later") — retrofitting lot-level tracking onto a system that started with aggregate holdings is a substantial, avoidable rework. Build it lot-based from day one even though XIRR/tax-lot features are Phase 1, because the data model is P0.

---

# 12. Personalization Engine

This is the engine that most directly separates EquiSense from Screener/Tickertape/Moneycontrol, so it receives the most architectural scrutiny in the document.

## 12.1 Investor Profile Data Model
Captures, as structured (not freeform-text) fields wherever a structured representation is actionable:

- **Investment horizon** (short/medium/long, with an actual target year, not just a label)
- **Risk tolerance** (stated, distinct from *revealed* tolerance derived from behavior in Phase 2)
- **Sector preferences and exclusions** (positive interest list and explicit avoid list — both matter equally)
- **Growth vs. value orientation** (represented as a position on a spectrum, not a binary, since most real investors are not purely one or the other)
- **Dividend preference** (income-seeking weight, from indifferent to primary objective)
- **Diversification targets** (max single-position weight, max sector weight — concrete percentages, not vague statements)
- **Maximum acceptable drawdown** (a real, stated number — this becomes the reference point for 11.3's diagnostics)
- **Preferred valuation lens** (e.g., prioritizes ROIC/reinvestment framing vs. prioritizes dividend yield vs. prioritizes asset-based value — this directly drives which cards surface first, per 12.2)
- **Explicit investment rules** (freeform but structured-enough-to-check statements, e.g., "never buy above 3x book value," "always maintain 10% cash," feeding directly into Phase 1's rule-violation flagging)

## 12.2 Profile-Driven Re-Ranking (the "does personalization actually do anything" proof point)
The dashboard and watchlist default sort order and card prioritization are computed as a **weighted function of the investor profile against each company's attributes** — a income-oriented profile surfaces dividend consistency and payout sustainability cards above growth/reinvestment cards for the *same* company; a growth/value-agnostic-but-quality-focused profile (Persona 3) surfaces Z-score/F-score and governance flags first. This must be demonstrably visible: viewing the same company under two different saved profile configurations should produce a different card order, not just different color accents. This is the concrete build-test for Commitment 2.2.

## 12.3 Rule-Violation Flagging (Phase 1)
Parses the structured components of the "explicit investment rules" field and checks candidate/existing positions against them mechanically wherever the rule is checkable (position-size rules, valuation-multiple ceilings, sector-exposure caps) — explicitly *not* attempting NLP-based enforcement of vague freeform rules in Phase 1, since a false rule-violation flag (or a false all-clear) is worse than no flag at all when the rule wasn't actually machine-checkable to begin with.

## 12.4 Adaptive/Revealed-Preference Learning (Phase 2)
Only attempted once sufficient usage history exists (per Commitment stated in Section 6.3): if a user's *stated* profile says "dividend-indifferent" but they consistently open, favorite, or act on dividend-flagged content, the system can surface a gentle prompt to reconcile stated vs. revealed preference — never silently overriding the stated profile, always as a suggested update the user confirms. Silent behavioral override would violate Commitment 2.3 (explainability) by making the personalization opaque to the very person it's personalizing for.

### Cross-Functional Challenge

- **UX Designer view**: 12.2's requirement that two profiles produce visibly different card ordering (not just visual accents) is the correct bar, and should be a literal acceptance-test screenshot comparison during QA — "same company, two profiles, side by side, does the ordering differ" is a simple, brutal, honest test for whether personalization is real or decorative.
- **Business Analyst view**: 12.1's separation of "stated" and eventually "revealed" preference (12.4) is a genuinely sophisticated design choice that mirrors how actual wealth management IPS (Investment Policy Statement) processes work — this is worth calling out explicitly in any admissions or resume narrative, since it demonstrates awareness of a real practitioner concept (stated vs. revealed preference, common in behavioral finance) rather than a naive settings page.
- **ML Researcher view**: 12.4 is correctly gated behind "sufficient usage history exists" rather than attempted from day one — with only weeks of data, any revealed-preference inference would be statistical noise dressed up as insight, which would actively damage trust in the personalization engine's credibility right when it matters most (early usage, when the user is deciding whether to trust the tool at all).

---

# 13. AI Capabilities

## 13.1 The Governing Test
Every AI feature in this product must pass a single test before it is allowed to exist: **"If a skeptical CFA charterholder asked 'how did it arrive at this, and can I verify it myself,' is there a genuine, complete answer?"** If the honest answer involves any version of "trust the model," the feature does not ship. This is stricter than most consumer AI products hold themselves to, deliberately — it is the direct implementation of Commitment 2.3 and 2.4 combined.

## 13.2 The Architecture Behind Every AI Feature: Retrieval/Computation-Grounded Generation, Never Free-Form Generation
This is the single most important technical decision in the AI capability set, so it is stated explicitly rather than left implicit: **the LLM is never the source of any number.** Every number that appears in an AI-generated explanation was computed by the deterministic Financial Analysis Engine (Section 10) or Portfolio Intelligence Engine (Section 11) *first*, and the LLM's only job is to **explain, contextualize, and narrate numbers it is handed**, with those numbers passed into its context explicitly (not retrieved from its own training data or "remembered," since the LLM has no reliable memory of a specific Indian mid-cap's Q3 FY26 receivable days). This single architectural rule is what makes explainability (Section 19) tractable at all — without it, "explainability" would mean auditing an opaque model's free-form claims, which is not actually achievable.

## 13.3 Capability-by-Capability Boundary Definition

| Capability | What it does | What it explicitly does NOT do | Why the line is there |
|---|---|---|---|
| Financial statement narrative interpretation | Takes ratio-engine output (already computed) and produces analyst-style prose explaining the trend and its likely drivers | Does not introduce any number not already computed by the engine; does not speculate about future performance | Keeps the LLM in a narration role, never a computation or forecasting role |
| Investment thesis generation assistant | Drafts a structured thesis skeleton from company data + user-stated rationale fragments, for the user to edit and own | Does not present the draft as "the" correct thesis, or imply the AI "recommends" the position | The thesis must remain the *user's* reasoning, with AI as a drafting aid, or the entire Research Journal concept (Section 22-23) becomes compromised |
| Company quality scoring | A **fully rules-based** composite (Piotroski-style + engine-computed sub-scores), not an LLM output at all | Is not, and must never be marketed or built as, an "AI opinion" of quality | Deliberately keeping this rules-based (not LLM-generated) is itself a design decision in service of Commitment 2.3 — some things are better solved by not using an LLM |
| Peer comparison narrative | Synthesizes already-computed peer-set comparison tables into readable prose | Does not fabricate peer relationships not already curated in the peer-set data (Section 10.8) | Prevents hallucinated "comparable companies" that were never actually vetted |
| Portfolio diagnostic narrative | Narrates already-computed concentration/correlation/rule-adherence diagnostics | Does not recommend specific trades to fix a diagnosed issue | Recommending trades would cross into advisory territory (Section 7.2's spirit) even without a price target attached |
| Natural language querying | Translates a user's plain-language question into a query against the user's own structured data (holdings, ratios, thesis objects), then returns the *actual query result* | Does not answer from the LLM's general knowledge about markets; if the question can't be mapped to a real query, it says so rather than guessing | Prevents the single most dangerous LLM failure mode (confident fabrication) in the highest-trust-required feature |
| Anomaly detection | Statistical baselining (e.g., z-score of quarter-over-quarter change vs. historical volatility of that same metric) flags outliers; LLM narrates *why the flagged metric looks unusual*, using the statistical output as input | The anomaly flag itself is never LLM-generated — it's a statistical computation | Keeps the actual detection mechanism auditable and reproducible, independent of any LLM's non-determinism |
| Scenario simulation | User sets explicit assumption changes (e.g., "-300bps margin, -5% revenue growth"); engine recomputes ratios/valuation deterministically; LLM narrates the resulting implications | Never auto-generates "likely" scenarios framed as predictions; scenarios are always user-initiated hypotheticals | This is the precise mechanism that satisfies "what if" curiosity without violating Commitment 2.4 |
| Research assistant / knowledge retrieval | Retrieval-augmented search over the user's own uploaded filings/notes, returning grounded excerpts with source citations | Does not answer questions by synthesizing outside/general knowledge about the company beyond what's in the user's own corpus, unless explicitly asked to and clearly labeled as doing so | Keeps the research assistant's authority scoped to what it can actually prove it retrieved |
| Watchlist prioritization | Combines profile weights (Section 12.2), quality scores (rules-based), and anomaly flags (statistical) into a ranked list, with the ranking *rationale* narrated by the LLM | The ranking itself is a deterministic weighted function; the LLM explains an already-computed ranking, it doesn't decide the ranking via free-form judgment | Same "narration, not computation" principle applied to the highest-visibility AI-touched feature |

## 13.4 Prompt/Context Construction Discipline
Every LLM call in the system follows a consistent pattern: **structured data in → constrained narration out**, with the system prompt for each capability explicitly instructing the model to only reference figures present in the supplied context, to flag (not silently omit) any data gaps, and to never state a number it wasn't given. Outputs should be treated, engineeringly, the same way a citation-required search response is treated elsewhere in this ecosystem — narrated in the model's own words, but never allowed to assert a fact untethered from a supplied source.

## 13.5 What Happens When the User Asks for Something Across the Line
The product must have a **designed refusal/reframe behavior**, not just an absence of the feature: if a user asks "what will this stock be worth in a year," the correct product behavior is not silence but an explicit reframe — surfacing the reverse-DCF's "here's what growth rate is currently priced in, and here's how that compares to history" as the legitimate version of the underlying question. This turns a boundary (Section 7.2) into a teaching moment rather than a dead end, which matters enormously for daily-use satisfaction (Commitment 2.10) — a tool that just refuses without redirecting feels broken, not principled.

### Cross-Functional Challenge

- **ML Researcher view**: 13.2's "LLM never originates a number" rule is the correct architecture and is achievable with today's tooling (structured context injection, function/tool-calling to fetch the deterministic engine outputs, constrained generation) without needing any custom model training. This should be treated as non-negotiable throughout implementation — it is the single point of failure for the entire "no fortune-telling" commitment if violated even once.
- **CFA Charterholder / PM view**: Section 13.5 is an underrated but important product decision. Simply refusing "what will this be worth" requests without redirecting to the reverse-DCF framing would make the product feel less capable than a naive competitor that just gives a (bad) fair-value number, even though EquiSense's approach is more rigorous. The redirect *is* the feature — it must be built with real UX care (Section 18), not treated as an edge-case error path.
- **Software Architect / CTO view**: The "narration, not computation" pattern also has a major, non-obvious *engineering* benefit worth flagging: it means the correctness of every user-facing number can be unit-tested against the deterministic engine (Section 29) completely independently of LLM behavior, and the LLM's non-determinism is confined entirely to prose style, never to factual correctness. This is what makes the AI layer testable at all — an architecture where the LLM could originate numbers would make rigorous testing nearly impossible.

---

# 14. Data Acquisition Strategy

## 14.1 The Core Constraint
Per Product Boundary 7.10, data acquisition must stay within licensed, ToS-compliant, or explicitly personal-use-permitted sources. This rules out adversarial scraping of brokers' or aggregators' proprietary UIs, and it should be treated as a hard constraint even though it's the "harder" path, because a project whose data foundation is legally fragile cannot be the industry-grade, defensible flagship project the brief asks for.

## 14.2 Layered Source Strategy (for a bounded universe of a few hundred companies)

- **Primary financials**: Company filings directly from stock exchange filing systems (BSE/NSE corporate filing sections) and/or the company's own investor-relations pages — annual reports, quarterly results (both standalone and consolidated), which are public regulatory disclosures, not scraped from a third party's proprietary aggregation.
- **Structured financial data where available**: India's regulatory XBRL filings, where machine-readable structured data exists, reduce parsing burden significantly versus PDF extraction — prioritize XBRL-available companies for the initial bounded universe precisely because this de-risks the hardest engineering problem (Section 6.1's flag).
- **Price history**: End-of-day price/volume data (per Boundary 7.4, no intraday needed) via any of the widely available free/low-cost EOD data APIs that explicitly license redistribution/personal use — this is a commodity data category with several legitimate options and should not be the place any ToS risk is taken.
- **Corporate actions / dividends / splits**: Same EOD data source family, or exchange corporate-action disclosure feeds directly.
- **Sector/peer classification**: NSE's own published industry classification as the taxonomy backbone (Section 10.8), supplemented by manual curation for peer-set refinement.
- **Governance-specific data (Phase 2)**: Promoter shareholding pattern and pledge disclosures are themselves regular, mandated exchange filings (shareholding pattern disclosures), not obscure or scraped data — this is a genuine and legitimate data source, just one requiring more parsing sophistication (structured table extraction from filing documents) than headline financials.

## 14.3 Update Cadence
Quarterly-result-driven for financial statements (matching actual filing cadence — there is no value in "real-time" financial statement data since fundamentals don't change between filings), daily batch for EOD price data, and event-driven (checked on a reasonable polling cadence, e.g., daily) for corporate actions and governance filings. This cadence directly reflects Boundary 7.4's "no intraday" decision and keeps infrastructure cost and complexity low (Commitment 2.6).

## 14.4 Data Quality & Restatement Handling
Filings get restated (prior-period figures revised in a later filing). The ingestion pipeline must **version statement data by filing date, not silently overwrite prior-period numbers**, so historical analysis reflects what was actually reported at the time where that distinction matters (e.g., checking "was this anomaly visible at the time" for the eventual anomaly-detection feature) while also surfacing the latest-known-correct figures by default for current analysis. This is a nuanced but important design point that a naive "just store the latest numbers" pipeline would get wrong.

### Cross-Functional Challenge

- **Senior Data Engineer view**: 14.4's filing-versioning requirement is easy to skip under time pressure ("just overwrite with latest") and expensive to retrofit later (every downstream table would need a migration). It must be part of the initial schema design (Section 15), not added later — this is exactly the kind of decision that's cheap now and very expensive in six months.
- **Startup CTO view**: 14.2's prioritization of XBRL-available companies for the initial bounded universe is a pragma­tic sequencing choice worth stating explicitly to the user: the *first* 15-30 companies chosen for the MVP universe should be chosen partly by "is clean structured filing data available" not purely by "which companies do I hold or want to watch" — a slight compromise on Persona 1 authenticity in exchange for dramatically lower Phase 0 risk. If a genuinely-cared-about holding lacks clean XBRL data, it can be added via more manual/semi-manual extraction without blocking the rest of the MVP.

---

# 15. Data Architecture

## 15.1 Core Design Principle: Extensible Attributes Over Schema Explosion
Given Persona 4's sector-specific KPI need (Section 4) and the general reality that different sectors care about different metrics, the schema uses a **canonical core (statements, ratios, prices, holdings, theses) plus a typed extensible-attributes pattern** for sector-specific or long-tail metrics (e.g., ARPOB for healthcare, same-store sales for retail) — rather than either (a) a rigid schema that can't accommodate new metric types without a migration, or (b) an unstructured schema-less blob that loses queryability. This directly resolves the tension flagged in Persona 4's stress-test (Section 4).

## 15.2 Core Entities (conceptual, not physical schema — that's an implementation-phase task)

- **Company** (identity, sector taxonomy mapping, listing details)
- **FilingPeriod** (fiscal period + filing date + standalone/consolidated flag + restatement version, per 14.4)
- **StatementLineItem** (canonical line item, value, linked to FilingPeriod and Company)
- **ComputedRatio** (ratio type, value, linked FilingPeriod — always derived, never manually entered, to preserve single-source-of-truth per Section 10.2)
- **PriceObservation** (EOD price/volume, date, Company)
- **InvestorProfile** (the structured personalization fields from Section 12.1)
- **Holding / TransactionLot** (per Section 11.1's ledger design)
- **Thesis** (structured thesis object, Section 22-23)
- **JournalEntry** (freeform, linkable to Company and/or Thesis)
- **WatchlistItem** (Company + user-added context + AI-computed priority score, recomputed not stored-stale)
- **Alert** (type, trigger condition, status, linked entity)
- **SectorAttribute** (the extensible-attribute entity from 15.1: Company + attribute name + value + unit + period)

## 15.3 Derived-vs-Stored Data Discipline
Anything computable from more fundamental data (ratios from statements, XIRR from transaction lots, concentration % from holdings) is **computed on read or cached-and-invalidated, never stored as an independently-editable field.** This is the direct schema-level enforcement of Commitment "single source of truth" already established in Section 10.2 — it prevents an entire bug class where a ratio silently drifts from what its inputs would actually produce.

### Cross-Functional Challenge

- **Financial Data Scientist view**: 15.3 must be weighed against real performance needs — recomputing every ratio on every page load for a rich company page is likely fine at this scale (a few hundred companies, single user), but portfolio-level aggregates (correlation matrices, Section 11.4) may warrant caching with explicit, visible invalidation logic (recompute on new price data arrival) rather than pure on-read computation. This is a legitimate exception to 15.3's spirit, not a violation of it, provided the cache is demonstrably always consistent with its inputs.
- **Software Architect view**: 15.1's extensible-attribute pattern is the correct generalized solution and should be resisted from over-engineering into a fully generic EAV (entity-attribute-value) schema for *everything* — it should be scoped specifically to sector-specific long-tail metrics, while the core financial schema (15.2's StatementLineItem, ComputedRatio) stays a normal, strongly-typed relational structure. A fully generic EAV-everywhere schema is exactly the kind of "interesting but unjustified" complexity Commitment 2.6 warns against.

---

# 16. System Architecture

## 16.1 Governing Architectural Decision: Boring, Provable Infrastructure
Per Commitment 2.6, the architecture explicitly rejects distributed-systems complexity (no microservices, no message queues, no Kubernetes, no separate real-time streaming layer) in favor of a **single well-structured application with a relational database**, because at the scale this product actually operates (single user, a few hundred companies, quarterly-cadence data updates, daily batch price updates) that complexity would deliver zero real benefit and substantial maintenance burden for a solo builder. This is stated explicitly here because "industry-grade" is easy to misread as "as complex as a large company's stack" — the correct reading, per the brief's own philosophy, is "as rigorous and correct as a large company's stack, at the appropriate scale for this problem."

## 16.2 High-Level Components

- **Ingestion layer**: Scheduled jobs (not a real-time streaming system — batch is entirely sufficient per 14.3) that pull from the licensed data sources (Section 14), parse/normalize into the canonical schema (Section 15), and version by filing date (14.4).
- **Core relational data store**: Holds all entities from Section 15.2. A single well-normalized relational database is sufficient — no need for a separate analytical/OLAP store at this data volume (a few hundred companies × ~20 years of quarterly data × ~40 line items is a dataset trivially handled by any mainstream relational database on modest hardware).
- **Computation layer**: The Financial Analysis Engine (Section 10) and Portfolio Intelligence Engine (Section 11), implemented as a well-tested library of pure functions operating on data pulled from the core store — deliberately decoupled from the web/API layer so these functions can be unit-tested in complete isolation (Section 29) and, notably, reused directly in a notebook/script context for the founder's own ad hoc analysis, mirroring the existing Colgate-Palmolive Jupyter workflow rather than replacing its *flexibility*, only its *manual repetition*.
- **AI orchestration layer**: A thin layer responsible exclusively for constructing grounded context (Section 13.4) from computation-layer outputs and calling the LLM API — this layer contains no financial logic of its own, only prompt construction, response validation (checking the model didn't introduce ungrounded numbers), and formatting.
- **Application/API layer**: Serves the web UI, handles the investor profile and personalization re-ranking logic (Section 12.2), and orchestrates calls to the computation and AI layers.
- **Web frontend**: A single responsive web application (per Boundary 7.7, no separate mobile-native app), structured around the "lens" system (Section 12.2, Section 18).

## 16.3 Why Not [Common Over-Engineering Temptations], Stated Explicitly

- **Why not microservices**: There is one user and one deployment target; splitting the computation engine, AI layer, and API into separate deployed services would only add network calls, deployment complexity, and debugging surface with zero corresponding scalability benefit at this usage level. Modular *code* organization (Section 17) delivers the same maintainability benefit without the operational cost.
- **Why not a NoSQL/document store for the core data**: The data is fundamentally relational (statements belong to companies belong to sectors; holdings reference companies and transaction lots; theses reference companies) — forcing this into a document model would mean reimplementing joins in application code for no benefit, purely to look more "modern."
- **Why not a dedicated vector database (initially)**: The Phase 2 research assistant / knowledge retrieval feature (Section 6.4) needs retrieval over the user's own documents, but at the realistic corpus size for one user's own filings/notes over a few years, an embedded/lightweight vector search capability within the existing data store is entirely sufficient — a separate dedicated vector database service is premature infrastructure for this scale.
- **Why not a real-time streaming architecture**: Already covered by 14.3/16.1 — there is no real-time data source in this product's scope (Boundary 7.4), so there is nothing for a streaming architecture to stream.

## 16.4 International Expansion Readiness (Without Building It Now)
Per Commitment 2.9, the *only* concrete architectural provision made for eventual multi-market expansion is: (a) Company entities carry an explicit market/exchange identifier field from day one (cost: one column), and (b) India-specific business logic (trading calendar, tax-lot rules, filing-format parsing) lives in clearly named, isolated modules rather than being scattered inline through general code (cost: normal good code hygiene, not extra architecture). Nothing else — no abstract "MarketAdapter" interface, no speculative multi-currency handling — is built now, because per the "why not" analysis above, that would be complexity built for a future that may not arrive, violating Commitment 2.6.

### Cross-Functional Challenge

- **Startup CTO view**: 16.1's rejection of microservices/streaming/NoSQL is the single most important architectural stance in the document for a solo builder, and it should be defended vigorously against the natural temptation (especially for a portfolio project meant to impress) to over-architect for resume-keyword reasons ("built with Kafka and Kubernetes"). The correct resume narrative is the *opposite*: "correctly identified that this problem didn't need distributed-systems complexity, and built boring, provable infrastructure instead" is a more sophisticated signal to a technical interviewer than a needlessly complex stack, because it demonstrates judgment, not just tool familiarity.
- **Senior Data Engineer view**: 16.3's vector-database reasoning is correct for Phase 2's scale but should be revisited (not pre-committed against forever) if the research-assistant corpus grows substantially larger than initially expected (e.g., if it eventually indexes not just the user's own notes but a large library of annual reports) — this is exactly the kind of decision that should be made with real data on corpus size, not speculatively now.

---

# 17. Module Decomposition

Reflecting 16.2's components into buildable, independently-testable modules:

1. **`ingestion`** — source-specific fetchers/parsers, each normalizing into the canonical schema; isolated per source so a change in one filing format doesn't ripple elsewhere.
2. **`schema/core-data`** — the canonical entity definitions and persistence layer (Section 15.2).
3. **`engine/statement-analysis`** — ratio engine, ROIC/WACC, cash flow quality, distress/quality scoring (Section 10.1–10.4, 10.7). Pure functions, extensively unit-tested (Section 29), zero dependency on the web layer.
4. **`engine/valuation`** — reverse DCF and traditional multi-scenario DCF (Section 10.5–10.6). Separated from statement-analysis because valuation assumptions (WACC, growth) are a distinct concern from historical-statement computation.
5. **`engine/peer-comparison`** — sector taxonomy mapping and peer-set computation (Section 10.8).
6. **`engine/portfolio`** — holdings ledger, XIRR, concentration diagnostics, correlation/drawdown, rebalancing drift (Section 11).
7. **`engine/personalization`** — investor profile model and profile-driven re-ranking function (Section 12.1–12.2); depends on outputs from modules 3–6 but contains no statement/portfolio computation logic itself, only weighting/ranking logic.
8. **`ai-orchestration`** — grounded-context construction and LLM calls for every AI capability in Section 13; depends on modules 3–7's outputs as its only source of "facts," never computes facts itself.
9. **`research-journal`** — thesis object, journal entries, invalidation-trigger checking (Section 22-23); depends on module 3 for the data it checks triggers against.
10. **`alerts`** — alert definitions and evaluation scheduling (Section 24); a thin orchestration layer over modules 3, 6, 7, and 9's outputs.
11. **`api`** — the application/API layer tying modules together for the frontend.
12. **`web`** — the frontend, structured around the lens system (Section 18).

### Cross-Functional Challenge

- **Software Architect view**: The dependency direction stated for modules 7–10 (personalization, AI, research-journal, alerts all *depend on* but never *duplicate* the computation in modules 3–6) is the single most important rule to enforce during actual implementation — the most common way this kind of system degrades over time is a "quick" duplicate ratio calculation sneaking into the AI-orchestration or alerts layer for convenience, silently reintroducing the "numbers don't match across screens" bug class Section 10.2 was designed to prevent. Code review discipline (even solo, self-review) should explicitly check for this.

---

# 18. UX and Navigation

## 18.1 Design Language
Dense, data-forward, unapologetically analytical — closer in spirit to a professional research terminal than a consumer fintech app (Section 5's Bloomberg/FactSet reference point), but with genuinely good information hierarchy and typography, not "dense" as an excuse for cluttered. Every screen should look like something a working equity analyst would not be embarrassed to have open during a client call.

## 18.2 Primary Navigation Structure
Four top-level areas, deliberately not more:
1. **Dashboard** — personalization-driven daily entry point (Section 12.2); the profile-weighted watchlist priority, active thesis-review reminders, and any triggered alerts live here.
2. **Companies** — the bounded universe; company detail pages hold the full Financial Analysis Engine output (Section 10), organized via the lens system (18.3).
3. **Portfolio** — holdings, performance, concentration/correlation diagnostics (Section 11).
4. **Research** — thesis library, journal, and (Phase 2) the knowledge-retrieval research assistant (Section 22–23).

## 18.3 The Lens System (resolving the Persona 4-vs-others tension from Section 4)
A company detail page is composed of the same underlying data cards (statement trends, ROIC/reinvestment, cash flow quality, distress/quality scores, peer comparison, valuation) but the **default ordering and visual emphasis** of those cards is computed from the active investor profile (Section 12.2) — a single toggle/setting, not a separate page or duplicated view. This is the direct UI implementation of Commitment 2.2, and its correctness is tested exactly as described in Section 12's Cross-Functional Challenge (same company, different profile, visibly different ordering).

## 18.4 Progressive Disclosure for Explainability
Every number is a **first-class, inspectable object**: a compact default view, with an expandable "show the work" affordance that reveals the formula, the raw inputs (linked to source filing line items), and — for AI-narrated content — the exact structured context that was passed to the model (Section 13.4). This is not an optional debug view; it is a core, always-available UX pattern, because Commitment 2.3 requires it to be always available, not hidden behind a developer mode.

## 18.5 Friction Budget for Daily Use
Per Commitment 2.10, the product tracks (informally, by the founder's own honest self-assessment during Phase 0/1) a simple question every week: *"did I open EquiSense before I opened a spreadsheet this week, and if not, why not?"* Any recurring "why not" answer is a genuine UX or feature-gap signal that should reprioritize the next sprint, ahead of anything in the Section 9 roadmap — daily-use friction discovered empirically outranks a pre-written roadmap.

### Cross-Functional Challenge

- **UX Designer view**: 18.2's four-area navigation is deliberately minimal; the temptation to add a fifth top-level area for every new Section-6 feature category (e.g., a separate "Alerts" tab, a separate "AI Assistant" tab) should be resisted — alerts surface *within* Dashboard, AI capabilities surface *within* whichever area they're contextually relevant to (statement narration on Companies, portfolio narration on Portfolio), not as a separate destination. A growing feature list should deepen these four areas, not multiply top-level navigation.
- **Principal PM view**: 18.5 is the most important operational discipline in this whole section — it converts Commitment 2.10 from an aspiration into an actual weekly checkpoint with a concrete trigger for reprioritization, which is the only way a solo builder avoids the common failure mode of building impressive features nobody (including themselves) actually uses day to day.

---

# 19. Explainability Strategy

## 19.1 Three Layers of Explainability, All Required Simultaneously
1. **Computational transparency** (Section 18.4): every number traces to its formula and raw inputs.
2. **Methodological transparency**: every scoring/composite methodology (Z-score, F-score, quality composite, WACC estimate) is either a published, externally-verifiable methodology (Section 10.7's deliberate choice) or, where a genuinely original composite is used (e.g., the profile-driven re-ranking weights in Section 12.2), its weighting logic is itself displayed, not just its output.
3. **AI-narration grounding transparency**: per Section 13.4, the exact structured facts fed to the LLM are inspectable, so any AI-generated sentence can be checked against its actual source data, not just trusted on the model's authority.

## 19.2 The "Show the Work" Interaction Pattern
Concretely, this means every card in the Section 18.3 lens system supports an expand action revealing: the formula (with the filled-in numbers, not just the abstract formula), the source filing period and line items referenced, and, where an assumption was required (WACC components, growth-rate scenario inputs), those assumptions displayed as editable fields, not fixed text — letting the user immediately test their own alternative assumption rather than just reading someone else's.

## 19.3 Explicit Non-Goals for Explainability
Explainability does not mean exposing raw model internals, LLM chain-of-thought, or engineering implementation details to the user — it means exposing the **financial reasoning chain**: what was measured, how, from what source, under what assumptions. This distinction matters because "full transparency" taken too literally (e.g., surfacing raw LLM token-level reasoning) would add noise without adding financially meaningful trust, diluting focus from the transparency that actually matters.

### Cross-Functional Challenge

- **Equity Research Analyst view**: 19.2's requirement that assumption fields be genuinely editable (not just displayed) is what separates real explainability from cosmetic transparency — a WACC breakdown that shows "Equity Risk Premium: 6.5%" as static text still leaves the user unable to check "what if I think it's 7%," whereas an editable field answers that immediately. This should be treated as a strict UI requirement wherever an assumption exists, not a nice-to-have enhancement.

---

# 20. Portfolio Analytics
*(Consolidating and cross-referencing Section 11's engine into concrete analytics deliverables, to satisfy this as its own required section without duplicating content.)*

The Portfolio area (Section 18.2) surfaces, at minimum: position-level and portfolio-level XIRR (11.2), the four-axis concentration view including the distinctive quality-band axis (11.3), conditional stress-period correlation (11.4, Phase 1), rebalancing drift against stated targets (11.5, Phase 1), and dividend income tracking with forward projection based on currently-held positions' own disclosed policy (not predicted growth — a projection from *current* known dividend rates is a computation, a projection assuming future dividend *growth* would edge toward forecasting and should be avoided or extremely clearly caveated as a scenario, per Commitment 2.4).

### Cross-Functional Challenge
- **Portfolio Manager view**: The dividend "forward projection" caveat above is a real and easy-to-miss violation risk — a naive feature spec would happily project "expected future dividend income assuming 8% growth," which is a forecast wearing a portfolio-analytics costume. The correct version projects only from currently known/declared rates, explicitly flagging that it assumes no change, which keeps it a computation rather than a prediction.

---

# 21. Watchlists

Beyond the Section 6.5 inventory: a watchlist item is not merely "a company I'm tracking" but carries **why it's being tracked** (a short required rationale field at add-time — even one sentence) precisely because an unlabeled watchlist accumulates rot (Section 6.4's "watchlist prioritization" motivation) — six months later, "why did I add this" is exactly as important a question as "why did I buy this" (Problem 3), and the product should treat watchlist entries with nearly the same reasoning-capture discipline as full theses, just lighter-weight.

### Cross-Functional Challenge
- **Business Analyst view**: Requiring a rationale at add-time adds minor friction but is exactly the kind of "small forcing function, large downstream value" design choice this product should favor throughout — it costs the user five seconds and saves them from an unlabeled, meaningless list later. This is a good template for evaluating other potential friction-additions: does this small friction *now* prevent a larger information-loss *later*?

---

# 22. Research Journal

The freeform journal (Section 6.5) is deliberately kept lightweight and always available — a dated entry, optionally linked to one or more companies and/or an existing thesis, with no required structure. Its purpose is to capture the *messy, exploratory* stage of reasoning (reading a set of results, forming an initial impression, noting a CFA-curriculum concept just learned and wanting to apply it to a real company) that precedes a thesis being ready to formalize. Journal entries are also the natural home for genuinely educational content — e.g., "just learned DuPont 5-way decomposition in CFA L1, applying it to [Company] here" — directly serving Commitment 2.8's learning-linked evolution in a way that's visible in the product itself, not just in the roadmap document.

### Cross-Functional Challenge
- **CFA Charterholder view**: This CFA-linked journaling use case should be actively designed for (e.g., an optional "linked CFA topic" tag on journal entries) rather than left as an incidental possibility — over a multi-year CFA journey, a journal that can answer "show me everything I wrote while studying Fixed Income" becomes a uniquely personal and valuable artifact, and a genuinely distinctive thing to show an admissions committee.

---

# 23. Investment Thesis Management

## 23.1 The Structured Thesis Object (elaborating Section 6.5's core P0 feature)
A thesis is not a paragraph of prose — it is a structured object with, at minimum: a clear thesis statement, the **specific, falsifiable assumptions** the thesis depends on (e.g., "management successfully expands into two new states within 18 months," not "the company will do well"), explicit **invalidation triggers** (what observable fact would prove this wrong), a stated review date, and a position-sizing rationale linking back to the investor profile's rules (Section 12.1). Freeform elaboration/prose is supported *underneath* this structure, not instead of it — the structure is what makes Phase 1's invalidation alerting (Section 6.5) mechanically possible at all.

## 23.2 Thesis Lifecycle
Draft → Active → (Under Review, if an invalidation trigger fires or the review date passes) → Resolved (Confirmed / Invalidated / Closed-for-other-reason, e.g., position sold for portfolio-construction reasons unrelated to the thesis itself). This lifecycle is itself a valuable, reviewable history over time — "how often have my theses actually held up" is a uniquely powerful piece of self-knowledge for an investor, and the product should make that historical thesis-accuracy record easily viewable (a natural Phase 2/3 analytics feature building on this data once enough theses have resolved).

### Cross-Functional Challenge
- **CFA Charterholder / PM view**: 23.1's requirement that assumptions be *falsifiable*, not just stated, is the single hardest-to-enforce but most valuable discipline in the entire product — most retail "investment theses" (even sophisticated investors') fail exactly here, stating a belief rather than a testable claim. The thesis-creation UI (both manual and AI-assisted, Section 13.3) should actively prompt for falsifiability ("what would have to happen for this to be wrong?") as a required field, not an optional nicety, because a thesis without a real invalidation condition can never actually be checked, which defeats the entire purpose of Section 6.5's invalidation alerting feature.

---

# 24. Alerts and Monitoring

Per Section 6.5's inventory: thesis invalidation alerts (Phase 1, the highest-value alert type given Section 23), price/valuation-metric alerts (Phase 1, table-stakes), and governance/event alerts (Phase 2, India-specific differentiator per Problem 6). All alerts share one underlying model — a trigger condition, an evaluation cadence (matching the underlying data's own update cadence per Section 14.3, not artificially real-time), and a notification surfaced on the Dashboard (Section 18.2), never as an intrusive/separate channel (no push notifications, SMS, etc. — this is a considered-decision tool, not an urgency-driven trading app, and the notification design should reflect that philosophy explicitly).

### Cross-Functional Challenge
- **Software Architect view**: The shared underlying alert model (one trigger/evaluation/notification structure across all three alert types) is the correct design — it means Module 10 (`alerts`, Section 17) stays a genuinely thin orchestration layer rather than growing three parallel, duplicated alert subsystems as new alert types are added over the roadmap.

---

# 25. Risk Management Tools

Distinct from Section 11's portfolio-level risk analytics, this section covers company- and decision-level risk tools: distress scoring (Section 10.7) at the company level, and — the more novel contribution — **explicit "pre-mortem" support built into the thesis object** (Section 23.1's invalidation triggers are, in effect, a structured pre-mortem: "what would have to be true for this to fail" asked *before* the position is taken, not after). This reframes "risk management" away from a generic volatility number and toward the CFA-aligned, genuinely more useful practice of structured downside-scenario thinking at the point of decision.

### Cross-Functional Challenge
- **Credit Risk Analyst view**: This section's framing deliberately avoids introducing a redundant, separate "risk score" widget — company-level risk is already fully covered by Section 10.7 (distress/quality) and portfolio-level risk by Section 11 (concentration/correlation); the marginal value of a *third*, generic "risk" number here would be low and would risk contradicting or duplicating the other two, undermining the single-source-of-truth principle (Section 10.2, Section 15.3) that the rest of the product carefully protects.

---

# 26. Scenario Analysis

Elaborating Section 6.4's scenario simulation feature: the user sets explicit hypothetical changes to key assumptions (revenue growth rate, margin trajectory, working-capital efficiency, discount rate) via the same editable-assumption UI pattern established in Section 19.2, and the deterministic engine (Section 10) recomputes every downstream ratio, the reverse-DCF implied-growth comparison, and (Phase 1) the traditional DCF's valuation range under the new assumptions — with the LLM narrating *implications*, never generating the scenario itself unprompted (Section 13.3's boundary). This is explicitly a **user-initiated hypothetical tool**, not a forecasting feature, and every scenario output should be visually and textually labeled as such (e.g., a persistent "Hypothetical Scenario" banner) to prevent any ambiguity with Commitment 2.4.

### Cross-Functional Challenge
- **ML Researcher view**: There is a genuine and recurring temptation to make scenario generation "smarter" by having the AI *suggest* plausible scenarios (e.g., "based on historical volatility, here are three scenarios worth testing") — this is defensible (suggesting hypotheticals to test is different from predicting outcomes) but sits close enough to the Commitment 2.4 line that it should be built, if at all, only in Phase 2+ after the pure user-initiated version has proven its value, and only with extremely careful framing ("scenarios worth stress-testing," never "likely scenarios").

---

# 27. Performance Analytics
*(Cross-referencing Section 11.2 as the canonical source, to avoid duplicating a second performance-measurement system.)*

Beyond position/portfolio XIRR (11.2), Phase 2/3 performance analytics should include **thesis-level performance attribution** — a genuinely distinctive feature: linking realized returns back to the original thesis (Section 23) to answer "when my theses were right, was I right about the *reason*, or right for a different reason than I thought?" This is a substantially more sophisticated performance-analytics question than any competitor product asks, and it's only possible because of the structured-thesis data model built in Phase 0 — a direct payoff of Section 8's MVP prioritization decision to build the thesis object early even though invalidation-checking itself is deferred to Phase 1.

### Cross-Functional Challenge
- **Portfolio Manager view**: This thesis-level attribution feature is worth flagging as one of the most professionally credible ideas in the entire document — real institutional post-mortem/attribution processes ask exactly this "right for the right reasons" question, and almost no retail tool attempts it because almost no retail tool captures structured theses at all. This is a direct, compounding payoff of Commitment 2.7 (user's reasoning as a first-class data object).

---

# 28. Security and Privacy Considerations

## 28.1 What's Actually Sensitive Here
Realistically, for a single-user personal tool, the sensitive data is: actual portfolio holdings and position sizes (financially sensitive), the investor profile (personally revealing of risk tolerance and strategy), and journal/thesis content (potentially reveals non-public reasoning about, among other things, a family-controlled listed company's prospects — this last point deserves specific care given the MHPL context, since journal entries analyzing a company the user has insider-adjacent proximity to should never be exposed publicly even in an open-source/portfolio-showcase context).

## 28.2 Concrete Practices
- Credentials for any external data API and, if ever added, broker read-only integration (Boundary 7.1) are held only in environment-level secrets, never committed to source control — an especially important discipline *because* this is also intended as a public GitHub project (Section 32); a leaked API key in a public repo's git history is a common, embarrassing, avoidable failure.
- If the open-sourced version of the codebase (Section 32) is the same codebase running the founder's real personal data, the repository must exclude actual portfolio/thesis/journal content entirely (via `.gitignore`'d local data, seed/demo data for the public repo, never real data committed) — this is a structural decision that must be made from the *first* commit, not retrofitted before a public release, because scrubbing sensitive data from git history after the fact is difficult and error-prone.
- Standard web-application hygiene (authenticated access even for a single-user app, encrypted data at rest and in transit, no sensitive data in URL query parameters) applies regardless of scale — "it's just for me" is not a reason to skip baseline security practice, especially for a project explicitly meant to demonstrate engineering maturity.

## 28.3 What NOT to Over-Build
No enterprise-grade security theater (no SOC2-style audit logging, no complex RBAC system) is warranted for a genuinely single-user application — per Commitment 2.6, security effort should be proportional to actual risk, and actual risk here is "don't leak my portfolio data or API keys," not "defend against a nation-state APT."

### Cross-Functional Challenge
- **Startup CTO view**: 28.2's "public repo must never contain real personal/financial data, decided from commit one" is the single most important operational discipline in this section — the single most common way personal financial side-projects become embarrassing or actively harmful when open-sourced is exactly this failure, and it is entirely preventable with a five-minute decision made before the first line of code, versus a painful history-rewrite later.

---

# 29. Testing Strategy

## 29.1 The Non-Negotiable Gate: Deterministic Engine Correctness
Per Section 8's MVP success criterion 4 and Section 13's "narration, not computation" architecture, the Financial Analysis Engine and Portfolio Intelligence Engine (Section 10, 11) require **rigorous unit testing against hand-verified reference values** — for a small set of real companies, every ratio, the Z-score, F-score, and reverse-DCF output should be independently computed by hand (or in a trusted spreadsheet) at least once and checked to match within acceptable tolerance, before the AI narration layer is ever allowed to describe that engine's output. This is the direct testing-strategy implementation of the Financial Data Scientist's Section 8 challenge.

## 29.2 Testing Layers
- **Unit tests** on every pure function in the computation layer (Section 16.2) — the majority of test coverage should live here, since this is where correctness matters most and where testing is cheapest (pure functions, no I/O, no LLM non-determinism).
- **Data-quality tests** on the ingestion layer — schema validation, plausibility checks (e.g., flag if a ratio comes out wildly outside a sane range, which usually indicates a parsing error rather than a real business result) run automatically on every new filing ingested, not just at build time.
- **AI-output validation tests** — not testing the LLM's prose quality (subjective, low-value to test rigorously) but testing the *grounding discipline* from Section 13.4: does the AI orchestration layer's output ever contain a number not present in the supplied context? This is checkable programmatically (extract numbers from the AI output, verify each exists in the input context) and should be a real, automated test, not a manual spot-check, precisely because it's the load-bearing guarantee behind Commitment 2.3/2.4.
- **UX acceptance checks** — the Section 12's "does personalization visibly change ordering" test and similar behavioral acceptance criteria, run manually at each phase boundary given solo-project scale (full automated UI testing infrastructure would be disproportionate effort here per Commitment 2.6).

### Cross-Functional Challenge
- **Financial Data Scientist view**: 29.2's "AI-output validation test" (programmatically checking the AI never states an ungrounded number) is a genuinely important and somewhat unusual test to build explicitly, and it's worth calling out that this is *more* rigorous than what most consumer AI products do — most treat "the LLM said something reasonable-sounding" as sufficient, whereas this product treats "the LLM said something *verifiably grounded*" as the actual bar, tested automatically.

---

# 30. Deployment Strategy

Given single-user scale and solo-maintenance reality (Commitment 2.6 applied to operations, not just architecture): a single, modest-tier deployment (one application server, one database instance) is entirely sufficient — no auto-scaling infrastructure, no multi-region deployment, no complex CI/CD pipeline beyond a straightforward build-test-deploy sequence. Scheduled ingestion jobs (Section 16.2) run on a simple cron-equivalent scheduler, not a dedicated orchestration platform. This should be treated as a deliberate, stated choice in any resume/portfolio narrative — "I correctly right-sized deployment infrastructure to actual load" is a better engineering-judgment signal than an over-provisioned, resume-padded deployment that doesn't reflect genuine scale reasoning.

### Cross-Functional Challenge
- **Startup CTO view**: The one area worth slightly over-investing in, relative to strict "just enough" scaling, is **backup/data-durability practice** for the core relational store (Section 16.2) — losing years of accumulated thesis/journal/portfolio history (Commitment 2.7's central data asset) to a preventable data-loss incident would be a uniquely painful, avoidable failure for a project whose entire value compounds over years. Regular automated backups are cheap insurance and should not be cut even under a minimal-infrastructure philosophy.

---

# 31. Long-Term Maintainability

## 31.1 The Real Maintainability Risk for a Solo, Multi-Year Project
Not "will the code rot" in the conventional software-engineering sense, but **"will the founder still understand and want to extend this in year 3"** — which argues for the module boundaries in Section 17 being genuinely clean (so a given module can be revisited after months away without needing to re-understand the whole system), and for documentation that captures *why* decisions were made (this document itself, kept living and updated, is the primary maintainability artifact, more so than inline code comments).

## 31.2 Revisit Triggers
Certain V1 decisions should be explicitly revisited only if a concrete trigger occurs, not on a fixed schedule: reconsider mobile-native (Boundary 7.7) only if actual usage data shows frequent on-the-go, glance-style access attempts on mobile web that a native app would meaningfully improve; reconsider the bounded-universe size (Commitment 2.5) only if the founder's actual watchlist/holdings genuinely outgrow it; reconsider vector-database infrastructure (Section 16.3) only if the research-assistant corpus grows large enough that the lightweight approach measurably degrades.

### Cross-Functional Challenge
- **Principal PM view**: 31.2's "revisit only on concrete trigger, not on schedule" discipline is what prevents a multi-year roadmap (Section 9) from becoming a source of scope creep pressure in its own right — the roadmap describes a plausible path, but every phase's actual feature set should still be re-validated against real usage at the time, not built on autopilot because "the roadmap said so."

---

# 32. Open-Source Strategy

Per Section 28.2, the public repository ships with seed/demo data (a handful of real, already-public companies' data, clearly labeled as demonstration content) rather than the founder's actual portfolio/thesis content. The README and repository structure should foreground exactly the things Sections 10, 13, and 16 argue are the project's genuine differentiators — the "narration never originates a number" architecture (13.2), the reverse-DCF's non-predictive valuation framing (10.5), the explicit "why not microservices" reasoning (16.3) — because for a technical audience (recruiters, admissions committees with technical reviewers, other developers), *documented engineering judgment* is more differentiating than feature count. A README that reads like a feature list undersells this project; a README that reads like an engineering design rationale sells it correctly.

### Cross-Functional Challenge
- **Business Analyst view**: The open-source release should be timed deliberately (Phase 3, per Section 9) rather than rushed early — releasing before the MVP's core loop is genuinely proven (Section 8's success criteria) risks a public repository that looks unfinished or, worse, that has to walk back an early architectural choice publicly. Prove it privately first; open it once the foundational bet (Sections 10, 13, 16) has held up under real personal use.

---

# 33. Resume Value

Stated directly, since the brief asks for it explicitly: this project's resume value does not come primarily from "built a fintech app" (a crowded, generic claim) but from the *specific, defensible engineering and financial-reasoning decisions* documented throughout this PRD — correctly scoping infrastructure to actual need (Section 16.3), building an AI system with a hard, testable non-hallucination guarantee (Section 13, 29.2), choosing well-validated public methodologies over invented black-box scores for credibility (Section 10.7), and designing personalization that's provably structural rather than cosmetic (Section 12.2's ordering test). A technical interviewer who reads this document's Cross-Functional Challenge sections will see evidence of judgment under trade-offs, which is a rarer and more valuable signal than a long feature list.

### Cross-Functional Challenge
- **Principal PM view**: The single best resume artifact this project can produce is not the deployed app itself but a well-written **engineering-decisions writeup** (distinct from this internal PRD) that walks through 3-4 of the most defensible trade-off decisions above in narrative form — this is a far higher-leverage deliverable for a resume/interview context than polishing additional features, and should be explicitly scheduled (Phase 3, alongside Section 32's open-source release) rather than treated as an afterthought.

---

# 34. Master's Admissions Value

For Finance / Business Analytics / Financial Engineering / Quantitative Finance admissions specifically, this project's value proposition is: **demonstrated ability to translate CFA-curriculum theory into working, correctly-implemented analytical software**, which is a genuinely rare combination (most finance-admissions portfolios show either pure academic/exam credentials or pure technical projects, rarely both fused together with this level of integration). The CFA-alignment sequencing in Section 9 is not just a development-planning convenience — it is the literal narrative arc for an admissions essay: "as I learned X in the CFA curriculum, I built Y to apply it to real Indian companies, and here's what I learned that the curriculum alone couldn't teach me" is a compelling, differentiated, and entirely true story if the roadmap is actually followed with genuine care rather than treated as decoration.

### Cross-Functional Challenge
- **CFA Charterholder / PM view**: This value is only real if the CFA-alignment claims in Section 9 are genuinely substantive, not retrofitted — meaning the founder should actually be able to point to specific curriculum readings that directly informed specific engine decisions (e.g., "the ROIC-vs-WACC framing in Section 10.3 directly reflects [specific CFA L1 reading]"). A vague, after-the-fact claim of alignment would be easily seen through by a knowledgeable admissions reviewer or interviewer; a specific, traceable one is a genuine differentiator.

---

# 35. Future Monetization Opportunities

Stated cautiously and explicitly deferred, since Boundary 7.8 correctly rules out multi-tenant/SaaS features in V1: **if** this platform's core loop proves genuinely valuable over 1-2 years of personal use, the most defensible future monetization path (should it ever be pursued) would be a very narrow, opt-in productization of the *methodology and tooling* (e.g., a licensed/subscription version for other individual investors who want the same rigor), never a path that compromises the "no fortune-telling, no signals" philosophy (Commitment 2.4) that is the product's actual credibility foundation — a pivot toward signal-selling or "AI picks" to monetize faster would destroy the exact thing that makes this project intellectually and professionally valuable in the first place.

### Cross-Functional Challenge
- **Business Analyst view**: This section is intentionally the shortest and least developed in the document, and that is itself the correct call — spending significant design effort on a hypothetical multi-year-out monetization path before the core single-user product has even proven itself (Section 8) would be a direct violation of Commitment 2.6's "don't build for speculative futures" principle applied to business strategy, not just software architecture.

---

# 36. Risks and Trade-offs

Stated plainly, without softening, because a PRD that only lists strengths is not a serious document:

| Risk | Why it's real | Mitigation already built into this design |
|---|---|---|
| Data ingestion (Section 14, 16) is harder and more time-consuming than expected, delaying everything downstream | Indian filing data quality is genuinely inconsistent; this is the single most-flagged risk across multiple Cross-Functional Challenges in this document | Phase 0 explicitly prioritizes XBRL-available companies (14.2) to de-risk this first, before building anything downstream; Section 9's phase boundaries are explicitly soft (Section 9's CTO challenge) |
| Solo-builder time constraints mean the roadmap (Section 9) is optimistic | One person, alongside CFA study and other commitments (per the founder's actual context), has limited hours; an 18-month roadmap for this scope is ambitious | Every phase is scoped to be independently valuable and shippable on its own (Section 8's MVP is coherent standalone); slipping a phase doesn't strand the project in an unusable state |
| The AI-narration architecture (Section 13.2) is more complex to implement correctly than a naive "just call the LLM" approach | Grounded-context construction and output validation (Section 29.2) is real engineering work, not a thin wrapper | This is treated as core P0 scope, not an add-on, precisely because it's load-bearing for the entire "no fortune-telling, fully explainable" positioning — if this is under-invested, the product's central credibility claim is compromised |
| Personalization (Section 12) risks staying cosmetic despite the design intent, if the re-ranking function ends up simplistic in practice | It's easy to *say* "profile-driven re-ranking" and ship something that barely changes ordering | Section 12.2 and Section 18.3's Cross-Functional Challenge both specify a concrete, brutal acceptance test (visibly different card order for two different profiles) precisely to catch this failure mode before it ships |
| The bounded-universe decision (Commitment 2.5) may feel limiting if the founder's genuine interests outgrow it faster than expected | A genuinely curious investor's watchlist can grow quickly | Section 31.2 names this as an explicit, concrete revisit trigger, not a permanent ceiling |
| The multi-persona "founding team" framing of this document, while useful for rigor, could tempt building features for Personas 2-4 prematurely | Section 4 explicitly warns against this, but the temptation is real once features are designed in detail | Section 4's Cross-Functional Challenge and Section 8's MVP scope both explicitly restrict Phase 0-1 build effort to Persona 1 only |

### Cross-Functional Challenge
- **Startup CTO view**: Of all risks listed, the first (data ingestion difficulty) is the one most likely to actually derail the timeline, and it's worth stating even more bluntly here than elsewhere in the document: if, after a genuine, focused effort, standardized statement ingestion for even the initial small universe isn't reliable, the entire roadmap should pause and this should be treated as a first-order problem to solve in isolation (perhaps with a smaller universe than even the 15-30 company target) rather than proceeding to build analytical features on top of a shaky foundation.

---

# 37. Critical Review of the Entire Design

Taking the harshest plausible outside view, deliberately, as a closing discipline:

**Is this over-scoped for a solo builder?** Partially, yes — the full Section 6 feature inventory across all four phases is a genuinely large body of work. This is why Section 8's MVP definition and Section 9's phase-by-phase sequencing exist as hard discipline mechanisms: the *document* describes the full multi-year vision (as explicitly requested), but the *build plan* is meant to be followed phase-by-phase with real willingness to let later phases slip or be re-scoped (Section 31.2), not treated as a fixed contract.

**Is the "no fortune-telling" positioning going to feel limiting to the founder's own future self?** Possibly, at some point — there will be moments where "just tell me if this is a buy" feels like the natural thing to want from the tool. The design's answer (Section 13.5's reframe-not-refuse pattern, the reverse-DCF's implied-expectations framing) is a genuine, considered answer to this tension, not a dodge, but it should be revisited honestly if, after real usage, it turns out to under-serve genuine decision needs rather than just feeling occasionally restrictive.

**Is the multi-persona framing (Section 4) actually load-bearing, or decorative?** It is load-bearing specifically as a *design guardrail* (preventing the product from becoming an undescribable personal script) and as a *narrative device* for this document's requested rigor, but it must not be mistaken for a real multi-user product plan — Boundary 7.8 exists precisely to keep this honest.

**Is the CFA-alignment story (Sections 2.8, 9, 34) genuine or convenient?** This is the single biggest execution risk to the document's own credibility, flagged directly in Section 34's Cross-Functional Challenge: the alignment must be *lived*, not asserted after the fact. If phases are built without genuine reference to what's actually being studied at the time, the entire admissions/resume narrative around this becomes hollow, and a good interviewer or admissions reviewer will find the seams.

**What would make this project fail, honestly?** Not lack of features — the feature inventory (Section 6) is generous. Failure looks like: (a) the founder stops using it personally within the first few months (violating the Section 8 success criteria and Section 18.5's friction-budget discipline), in which case no amount of further feature-building matters; or (b) the data-ingestion foundation (Section 14, 16) never becomes reliable enough to trust, undermining every downstream analytical claim; or (c) scope creep from the ambitious Section 6/9 vision causes Phase 0 to never actually ship a usable MVP. All three failure modes are explicitly guarded against in this document (Section 8's behavioral success criteria; Section 14's XBRL-first sequencing; Section 8's hard MVP-exclusion list) — but a guard on paper is only as good as the discipline to actually follow it once building begins.

---

# Closing Synthesis: The Four Required Deliverables

## Features to remove because they add complexity without sufficient value
- **Real-time/intraday data and dashboards** (Boundary 7.4) — no feature in Section 6 requires it, and it would substantially complicate infrastructure (Section 16) for zero decision-quality benefit given the platform's long-horizon philosophy.
- **Social/community features** (Boundary 7.3) — pure complexity and risk surface for a single-intended-user product with zero corresponding analytical value.
- **A separate, generic "risk score" widget** (Section 25's Cross-Functional Challenge) — redundant with, and risks contradicting, the distress/quality scoring (10.7) and portfolio concentration/correlation analytics (11) that already cover this need with more rigor.
- **Fully generic EAV-everywhere data schema** (Section 15's Cross-Functional Challenge) — the extensible-attributes pattern should be scoped narrowly to sector-specific long-tail metrics, not applied to the core financial schema, where it would sacrifice type-safety and query performance for no real benefit.
- **A separate mobile-native app in V1** (Boundary 7.7) — a multi-year maintenance commitment with no clear evidence yet that it's needed for this considered-decision, not glance-based, use case.

## Features that are deceptively simple but provide exceptional value
- **The required watchlist-add rationale field** (Section 21) — one sentence of friction, prevents months of accumulated, meaningless list rot.
- **Filing-date versioning of restated statement data** (Section 14.4) — a "boring" data-modeling decision that's cheap now, expensive to retrofit, and quietly enables historically-honest analysis later.
- **The falsifiable-assumptions requirement in the thesis object** (Section 23.1) — a small UI prompt ("what would have to be true for this to be wrong?") that is the entire mechanism making the platform's most differentiated feature (invalidation alerting, thesis-level performance attribution) possible at all.
- **Editable assumption fields wherever an estimate exists** (Section 19.2) — the difference between decorative and real explainability, at essentially zero extra engineering cost over static display.
- **Conditional (stress-period), not unconditional, correlation** (Section 11.4) — the same computational family as a naive correlation matrix, but professionally far more meaningful, for negligible extra implementation cost.

## The smallest possible MVP that still feels premium
Standardized statement ingestion (for ~15-20 real, XBRL-clean companies) → core ratio engine with multi-year trends → ROIC/WACC reinvestment framing → cash flow quality flags → reverse DCF → Z-score + F-score → peer comparison → portfolio holdings ledger with correct XIRR and four-axis concentration diagnostics → investor profile with genuinely-tested profile-driven re-ranking → AI statement narration and thesis-drafting assistance, both fully grounded per Section 13.2 → the structured, falsifiable-assumption thesis object → freeform journal → watchlist with required rationale field. This is "small" only relative to the full Section 6 inventory — it is a real, coherent, daily-usable product, and every element in it is there because removing it would break the coherence of what remains, not because it was cheap to build.

## The ideal development order over the next 12-18 months
1. **Weeks 1-4**: Data ingestion pipeline and canonical schema (Sections 14, 15) for a small, XBRL-prioritized seed set of companies — de-risk the hardest problem first, before any UI exists.
2. **Weeks 5-8**: Core ratio engine, ROIC/WACC, cash flow quality, Z-score/F-score (Section 10.1-10.4, 10.7) as a tested, standalone computation library — validated by hand against 2-3 companies (Section 8's success criterion 4) before anything else touches it.
3. **Weeks 9-11**: Reverse DCF and peer comparison (Section 10.5, 10.8); portfolio holdings ledger, XIRR, concentration diagnostics (Section 11.1-11.3).
4. **Weeks 12-13**: Investor profile data model and profile-driven re-ranking (Section 12.1-12.2), tested against the explicit ordering-difference acceptance test.
5. **Weeks 14-16**: AI orchestration layer (Section 13.2, 13.4) — statement narration and thesis-drafting assistant, built with the grounding-validation tests (Section 29.2) from the start, not retrofitted.
6. **Weeks 17-18**: Structured thesis object, freeform journal, watchlist with rationale field (Sections 21-23) — the Research area (Section 18.2) of the UI.
7. **Weeks 19-20+**: UI polish across the lens system (Section 18.3), explainability "show the work" interactions (Section 19.2), and the Section 8 MVP success-criteria review — at which point the founder should genuinely be living in this tool daily before Phase 1 (Section 9) begins.

This order is deliberately back-loaded on UI and front-loaded on data correctness and computation, because per this entire document's repeated emphasis: a beautiful interface over wrong numbers is worse than a plain interface over numbers that are actually, verifiably correct — and correctness, not polish, is what an equity analyst, an admissions committee, and the founder's own future self will actually be checking.