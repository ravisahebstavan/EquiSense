# EquiSense

An explainable, personalized equity-analysis workspace for the Indian market —
closer to a private analyst's workbench than a public screening tool. The full
founding design document is [PROJECT_DRAFT.md](PROJECT_DRAFT.md); this README
covers what is built and the engineering decisions that carry the product's
weight.

## Quick start (live data)

```bash
pip install -e ".[dev]"
python -m equisense.ingestion          # ~1 min: 10y NIFTY-50 prices+volume,
                                       # macro (NIFTY/VIX/INR/Brent), real annual
                                       # statements — free, keyless (Yahoo Finance)
uvicorn equisense.api.app:app --reload
# open http://localhost:8000
```

Then in the UI: **Research → Evidence Lab → Recompute studies** runs the
registered cross-sectional hypotheses against your stored history, and any
company page → **Generate dossier** builds a live, pre-registered decision
dossier. `POST /api/live/refresh` (or the Evidence Lab button) pulls
incremental prices/macro daily.

On first start the app seeds a **clearly-labeled demonstration dataset** (nine
Indian large/mid-caps with approximated financials, a demo portfolio, theses
and journal entries). Real personal data lives only in the gitignored local
SQLite database — decided at commit one, per §28.2 of the design doc.

Optional: set `ANTHROPIC_API_KEY` (see `.env.example`) to enable the AI
narration layer. Without it, every analytical feature still works — the AI
endpoints degrade gracefully instead of failing.

```bash
pytest   # engine correctness gate — hand-verified reference values
```

## Hosting it free (Neon + Vercel)

The app deploys end-to-end at zero cost: Vercel (serverless FastAPI + UI +
daily cron) with all persistent state — market DB, hash-chained ledger, raw
vault — in a free Neon Postgres. Token-gated, auto-bootstrapping, resumable.
Full path and your exact one-time steps: **[DEPLOYMENT.md](DEPLOYMENT.md)**.

## What it does (MVP scope, §8)

- **Financial analysis engine** — 25+ ratios in five families, ROIC vs WACC,
  DuPont decomposition, cash-flow quality (accruals, CFO/NI, capex intensity),
  Altman Z (with its calibration caveat surfaced, not buried), Piotroski
  F-Score, and manually curated peer comparison.
- **Reverse DCF, not price targets** — the valuation engine solves *backward*
  from the market price: what FCF growth is currently priced in, compared to
  the company's own history? Every assumption (risk-free rate, ERP, beta, tax,
  horizon, terminal growth) is exposed and editable in the UI.
- **Portfolio intelligence** — a transaction ledger (never mutable holdings),
  FIFO lots with India LTCG/STCG aging, correct XIRR, and four-axis
  concentration diagnostics including the quality-tier axis: how much of your
  capital sits in fundamentally fragile businesses.
- **Structural personalization** — the investor profile changes the ranking
  *function* and the card *ordering* (the lens system), not a color theme.
  The acceptance test is literal: same company, two profiles, different order
  — and it's enforced by a unit test.
- **Research memory** — structured theses with required falsifiable
  assumptions and invalidation triggers, a lifecycle
  (draft → active → confirmed/invalidated), a freeform journal with optional
  CFA-topic tags, and a watchlist that refuses entries without a rationale.
- **Grounded AI narration** — statement explanation, portfolio briefing, and
  a thesis-drafting assistant.

## Phase III — the research workstation

The frontend is now a complete operating interface for the research engine —
dark-first, keyboard-first, terminal-dense. Five areas:

- **Dashboard** — command center: regime vector, portfolio state + breaches,
  highest-attention names, weakest positions, thesis reviews due, model
  health (scored claims, base-rate records, hypothesis lifecycle counts,
  ledger chain), and every system warning.
- **Companies** — filterable, sortable universe; each company opens a tabbed
  workspace: Overview (lens-ordered cards, every number expands to its
  work), Dossier (live, pre-registered), **Memory** (every dossier ever
  issued, scored claims, theses, journal — institutional memory per name),
  and AI Desk (grounded narration + thesis drafting).
- **Portfolio** — institutional monitor: holdings with tax-lot aging, 126d
  correlation matrix, portfolio heat vs. budget, naive risk contribution,
  four-axis concentration, rule-breach diagnostics.
- **Research** — theses / journal / watchlist desk.
- **Lab** — hypothesis registry with lifecycle + admission caps, N_eff-gated
  base rates, calibration + hash-chained ledger browser, REG-001 runner, and
  a **Data Health** page (quality score decomposed, per-dataset freshness,
  vault stats, missing datasets listed visibly).

Always-on **status strip** (regime · data quality badge · price freshness ·
coverage · warnings) so staleness is never a mystery; **staged refresh
drawer** streams the pipeline live over SSE (downloading → validating →
running hypotheses → scoring → publishing); **command palette** (Ctrl+K or
`/`) with fuzzy company search and actions; `g d/c/p/r/l` navigation, `r` to
refresh.

## Phase II Wave R — rigor retrofit (PHASE2_ARCHITECTURE)

The system audited itself ([PHASE2_ARCHITECTURE.md](PHASE2_ARCHITECTURE.md) §1) and the
S1 findings are now fixed **and executed against live data**:

- **Raw vault** — content-addressed, immutable archive of provider payloads
  *before* normalization (`data/vault/`); the canonical store is now
  rebuildable, and every future fetch is preserved forever.
- **N_eff everywhere** — base rates carry overlap-corrected effective sample
  sizes; publication is gated on N_eff ≥ 30, not raw N; synthesis confidence
  reads N_eff. The honest re-grading: after overlap correction **and** the
  round-trip cost model, every price-only study's net median excess is ≤ 0 —
  the platform reports that it has found no deployable edge in these families
  on mega-caps, which is the methodology working.
- **Percentile normalization** — hand-picked strength scales abolished;
  evidence strength = cross-sectional percentile rank within the universe.
- **Admission caps** — influence is earned: exploratory families cap at
  ±0.25; deferred hypotheses (Cash Conviction, Fragility) render as **SHADOW**
  — visible in every dossier, aggregated into none.
- **Abstention counterfactuals** — abstain verdicts register scoreable claims;
  the calibration report now includes a wrongful-abstention rate.
- **Portfolio-fit + real heat** — candidates are scored against the actual
  book (63d correlation, live portfolio heat feeds sizing).
- **REG-001 executed** — the regime engine was made to justify its own
  existence: out-of-sample, regime conditioning showed *no measurable
  calibration value* (ΔBrier −0.0006). Recorded in the registry; a
  walk-forward confirmatory rerun decides its fate.

## The live research layer (RESEARCH_BLUEPRINT v2)

Built on the v1 measurement substrate, all running on **real market data**:

- **Ingestion plane** — 10 years of daily prices+volume for the NIFTY-50
  universe, macro conditioning series, and real annual statements (flagged
  `pit_grade: reconstructed` — Yahoo serves restated figures; the honest label
  travels with every downstream number).
- **Hypothesis registry + base-rate studies** — pre-registered hypotheses
  (`equisense/research/registry.py`) run as leakage-controlled cross-sectional
  event studies against the platform's own stored history, publishing N / hit
  rate / median excess / IQR per regime cell, with thin cells (<30) suppressed
  and a survivorship caveat stamped on every record. The honest headline from
  the first run: 12-1 momentum shows ~zero unconditional edge in this mega-cap
  universe — the platform reports weak edges instead of manufacturing them.
- **Novel proprietary analytics** — Momentum Quality Index (vol-scaled,
  persistence-weighted momentum), Cash Conviction Score, Fragility Index,
  Expectations Gap (reverse-DCF implied vs delivered growth), Trend–Value
  Tension quadrants, Participation Heat — each fully documented, each itself a
  registered hypothesis subject to the same validation gauntlet.
- **Evidence → synthesis → dossier** — engines emit typed, clustered Evidence;
  synthesis is mechanical (uniform provisional weights until ≥150 scored
  claims unlock learned ones), surfaces dissent by name, and abstains as a
  first-class verdict. Dossiers carry sizing with shown work (vol-based stops,
  heat/liquidity caps, a permanent 0.5 provisional haircut) and India's actual
  cost/tax physics (STT, impact, the STCG/LTCG 12-month cliff).
- **Hash-chained decision ledger** — every dossier is pre-registered
  (tamper-evident JSONL); non-abstain verdicts carry a scoreable claim that is
  Brier-scored against realized universe-relative returns once its horizon
  expires, feeding the calibration report.

## The four decisions that matter (§33)

1. **The LLM never originates a number (§13.2).** Every figure in an AI
   narration was computed by the deterministic engine first and passed in as
   structured context. This is *tested programmatically*: a grounding
   validator extracts every numeric token from the model's output and verifies
   it exists in the supplied context (with rounding tolerance); violations
   trigger a corrective retry and are surfaced in the UI, never hidden. The UI
   also shows the exact context the model received.

2. **No fortune-telling (§2.4, §10.5).** No price targets, no buy/sell
   signals. "Is this cheap?" is answered by the reverse DCF — market-implied
   growth vs. delivered growth — which is a computation about the present,
   not a forecast, and its output is labeled accordingly in code.

3. **Explainability is architectural (§18.4, §19).** The engine's unit of
   output is a `Metric`: value + formula-with-numbers-filled-in + raw inputs +
   caveats. "Show the work" in the UI is a rendering of what the engine
   already returns, not a retrofitted tooltip. Scoring methodologies are
   published ones (Altman 1968, Piotroski 2000) precisely so they can be
   independently checked.

4. **Boring, provable infrastructure (§16).** One FastAPI app, one SQLite
   file, pure-function computation modules that are unit-tested against
   hand-computed values and reusable from a notebook. No microservices, no
   queues, no vector DB — at single-user scale that complexity would deliver
   zero benefit. Correctness gates: the engine test suite must pass before the
   AI layer is allowed to describe any number (§29.1).

## Layout

```
equisense/
  engine/        pure computation: ratios, quality, valuation, portfolio,
                 personalization — no I/O, no web, fully unit-tested
  models.py      SQLAlchemy entities (filing-date versioned statements §14.4,
                 transaction ledger §11.1, structured thesis §23.1)
  ai/            grounding validator + narration orchestration (no financial logic)
  api/           FastAPI app + services that assemble engine output
  seed/          labeled demo data
web/             dense analytical UI: Dashboard / Companies / Portfolio / Research
tests/           hand-verified reference values (§29.1 gate)
```

## Boundaries held on purpose (§7)

No order execution, no price prediction, no social features, no intraday data,
no news sentiment, no mobile app, no multi-tenancy. See the design doc for the
reasoning behind each.
