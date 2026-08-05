# EquiSense

An explainable, personalized equity-research workstation for the Indian market —
closer to a private analyst's workbench than a public screening tool.

This is the **single design document** for the project. It replaces the earlier
set (`PROJECT_DRAFT`, `RESEARCH_BLUEPRINT`, `PHASE2_ARCHITECTURE`, `DEPLOYMENT`
and the external-review prompts). Source docstrings cite it by section number as
`§N` — for example `§6.2` is the valuation engine.

---

## Contents

| § | Section |
|---|---|
| 1 | [Mandate and epistemic contract](#1-mandate-and-epistemic-contract) |
| 2 | [Quick start](#2-quick-start) |
| 3 | [Deployment and operations](#3-deployment-and-operations) |
| 4 | [Repository layout](#4-repository-layout) |
| 5 | [Data plane](#5-data-plane) |
| 6 | [Analysis engines](#6-analysis-engines) |
| 7 | [Evidence and synthesis](#7-evidence-and-synthesis) |
| 8 | [Decision plane](#8-decision-plane) |
| 9 | [Research lifecycle](#9-research-lifecycle) |
| 10 | [Ledger, learning and calibration](#10-ledger-learning-and-calibration) |
| 11 | [Regime intelligence](#11-regime-intelligence) |
| 12 | [Validation and backtesting](#12-validation-and-backtesting) |
| 13 | [AI narration](#13-ai-narration) |
| 14 | [API and web layer](#14-api-and-web-layer) |
| 15 | [Testing](#15-testing) |
| 16 | [Boundaries held on purpose](#16-boundaries-held-on-purpose) |
| 17 | [Operating notes](#17-operating-notes) |

---

## 1. Mandate and epistemic contract

EquiSense exists to make one person's investment reasoning **explicit, testable
and improvable**. It is not a screener, not a robo-advisor, and not a signal
service.

Four commitments carry the product's weight. They are enforced in code and
tested, not aspirational:

1. **The LLM never originates a number** (§13.2). Every figure in an AI
   narration is computed by the deterministic engine first and passed in as
   structured context. A grounding validator extracts every numeric token from
   the model's output and verifies it exists in that context; violations trigger
   a corrective retry and are surfaced, never hidden.

2. **No fortune-telling.** No price targets, no buy/sell signals. "Is this
   cheap?" is answered by a reverse DCF (§6.2) — market-implied growth versus
   delivered growth — which is a statement about the present, not a forecast.

3. **Explainability is architectural.** The engine's unit of output is a
   `Metric`: value + formula with numbers filled in + raw inputs + caveats.
   "Show the work" in the UI renders what the engine already returned; it is not
   a retrofitted tooltip. Published methodologies (Altman 1968, Piotroski 2000)
   are used precisely so they can be checked independently.

4. **Boring, provable infrastructure** (§14). One FastAPI app, pure-function
   computation modules unit-tested against hand-computed values. No
   microservices, no queues, no vector DB — at single-user scale that complexity
   buys nothing.

**The honesty constraint.** The system is required to report weak or absent
edges rather than manufacture them. Real results it publishes about itself: 12-1
momentum shows ~zero unconditional edge in this mega-cap universe; after overlap
correction and round-trip costs, every price-only study's net median excess is
≤ 0; and REG-001 found regime conditioning had *no measurable* out-of-sample
calibration value. Those are the methodology working, not failures to hide.

---

## 2. Quick start

```bash
pip install -e ".[dev]"
uvicorn equisense.api.app:app --reload
# open http://localhost:8000
```

A fresh database is empty. Click **⟳ Refresh** (top right) to run the full
bootstrap: universe → 10y prices → macro → fundamentals → validation →
hypotheses → scoring → publish. It is chunk-committed and resumable — if it is
interrupted, click Refresh again and it continues where it stopped.

```bash
pytest    # engine correctness gate (§15)
```

Optional: set `ANTHROPIC_API_KEY` to enable AI narration (§13). Without it every
analytical feature still works; the AI endpoints degrade with a clear
configuration message rather than failing.

---

## 3. Deployment and operations

### 3.1 Architecture

Two external services, both free tier:

```
┌─────────────────────────────────┐      ┌──────────────────────────────┐
│  VERCEL (Hobby)                 │      │  NEON (Postgres)             │
│  FastAPI + UI as a serverless   │──────│  0.5 GB · no expiry          │
│  function, + one daily cron     │      │  prices · filings · macro    │
└───────────────┬─────────────────┘      │  base rates · LEDGER · VAULT │
                │ outbound HTTPS         └──────────────────────────────┘
      Yahoo Finance + NSE archives (keyless)   optional: Anthropic API
```

Vercel's filesystem is ephemeral and read-only, but EquiSense's substance is
persistent state — the market database, the hash-chained ledger, the raw vault.
With `DATABASE_URL` set, all of it lives in Neon (`EQUISENSE_STORAGE=db` engages
automatically) and the Vercel side is stateless and disposable. Local
development is unchanged (SQLite + files).

### 3.2 One-time setup

1. **Neon** — create a project (region `ap-southeast-1` is closest to India) and
   copy the connection string.
2. **Vercel** — import the repo. `vercel.json` and `requirements.txt` are read
   automatically; there are no build settings to change.
3. Set **environment variables** (Production):

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Neon connection string |
   | `EQUISENSE_ACCESS_TOKEN` | a strong secret — this is your login |
   | `CRON_SECRET` | **the same value**, so Vercel Cron passes the auth gate |
   | `ANTHROPIC_API_KEY` | *optional*, enables AI narration |

4. Deploy, open the site, enter the token, click **⟳ Refresh**.

Environment variables only take effect on a **new deployment** — after adding or
changing one, redeploy.

### 3.3 Secrets, auth and access control

Single-user token gate, implemented as middleware in `equisense/api/app.py`.
It accepts `Authorization: Bearer <token>`, a `?token=` query parameter (which
then sets a 90-day cookie), or the `eqs_token` cookie.

**The gate disables itself when `EQUISENSE_ACCESS_TOKEN` is unset.** That is
deliberate, so local development stays frictionless — but on a hosted
deployment the same silence leaves the portfolio, the ledger and every write
endpoint open to anyone with the URL. `data_status()` therefore raises a
`SECURITY: NO ACCESS TOKEN SET` warning as the *first* warning whenever the app
detects a hosted environment with no token configured.

Personal data never enters the repo: real holdings live only in the database,
and `.env` and `data/` are gitignored.

**Write protection.** A hosted deployment with no `DATABASE_URL` falls back to
per-instance SQLite under `/tmp`, which is wiped between invocations. Every
write would *appear* to succeed while silently vanishing — the most expensive
failure mode in the codebase, because the ledger is the forward-testing record.
Writes are refused outright in that state rather than accepted and lost.

### 3.4 The daily cron

One Vercel cron job (`vercel.json`) calls `GET /api/cron/refresh` on weekdays.
Hobby allows a single cron and the function ceiling is `maxDuration: 300`.

Stages run in order of **irreplaceability**, and each commits on its own:

| Order | Stage | Why here |
|---|---|---|
| 1 | universe | cheap; everything downstream needs the ids |
| 2 | vol_surface, nse_archives | **cannot be backfilled** — one file per day |
| 3 | prices, macro | recoverable, but the learning loop needs them |
| 4 | forecasts, scoring | the learning loop (§10.2) |
| 5 | snapshot, base rates, prune, autopilot | fully recomputable any time |

This ordering is not cosmetic. It previously ran the other way round, so when
the function hit its limit the only work that never happened was the work that
could never be recovered. The cron also stops *starting* new work at
`CRON_BUDGET_S` rather than being killed mid-flight, and logs each stage's
duration — on a timeout the JSON response never arrives, so the platform log is
the only record of where the time went.

### 3.5 Known constraints

| Constraint | Effect | Handling |
|---|---|---|
| Serverless time limit (300s) | a long stage can exhaust it | staged, budgeted, per-stage commits (§3.4) |
| Cold starts / Neon auto-suspend | first request after idle is slow (~28s worst case) | startup does no database work on a hosted host; failures are carried to endpoints, which report them |
| Yahoo throttles cloud IPs | occasional refresh failures | surfaced in the drawer; adapters isolated for provider swaps |
| Vercel Hobby cron is best-effort | refresh time may drift | fine for end-of-day data |
| Corporate actions rename tickers | a symbol stops resolving | exchange-derived universe self-heals; `YAHOO_SYMBOL_OVERRIDES` covers the pinned fallback |

---

## 4. Repository layout

```
equisense/
  engine/        pure computation: ratios, quality, valuation, portfolio,
                 personalization, sizing, regime, technical, novel, crossasset
                 — no I/O, no web, fully unit-tested
  research/      hypothesis registry, base-rate studies, backtests, learning
  ingestion/     Yahoo + NSE archive adapters, universe definition, raw vault
  ai/            grounding validator + narration orchestration (no financial logic)
  api/           FastAPI app, services, snapshot builder, markets, autopilot
  models.py      SQLAlchemy entities (§5.4)
  ledger.py      append-only hash-chained decision ledger (§10.1)
web/             dense analytical UI (single-page, vanilla JS)
tests/           hand-verified reference values (§15)
```

---

## 5. Data plane

### 5.1 Universe definition

The analytical universe is **bounded and exchange-derived**: NSE publishes index
membership as a free keyless CSV, so constituents, industry classification and
ISINs come from the exchange itself and survive index reshuffles without anyone
editing code. `equisense/ingestion/universe.py` keeps a pinned NIFTY-50 map as
an offline fallback, and falling back is **reported, never silent** — analysing
a stale membership list means holding names the index dropped and missing the
ones it added.

Departed constituents are **deactivated, not deleted** (`is_index_member`).
Their price history stays, because deleting it would manufacture exactly the
survivorship bias every base-rate record is caveated for; clearing the flag
removes them from the live cross-section so they stop shifting percentile ranks.

Consequence to remember: the database holds far more names than the live
universe. Anything measuring *coverage* or building the live snapshot must scope
to index members, or it reports nonsense and reads data it does not use.

### 5.2 Sources and ingestion

Free and keyless by design. Yahoo Finance for prices, macro series and annual
statements; NSE published archives for delivery percentages, index valuation and
option-chain summaries. Source death is a *when*, not an *if*, so each provider
is isolated behind an adapter and nothing outside `ingestion/` knows Yahoo
exists.

Prices are stored in **both conventions per bar**, because they are not
interchangeable:

- `close` — total-return (splits *and* dividends adjusted): the correct basis
  for returns, momentum, volatility, correlation.
- `close_raw` — nominal (splits only): the price that actually traded, required
  wherever a price meets a per-share accounting figure.

Storing only the total-return close back-deflates historical prices, which made
historical P/E read systematically cheap and put a standing bearish tilt on the
value cluster.

Fundamentals are flagged `pit_grade: reconstructed` — Yahoo serves latest-known,
not point-in-time, figures. The honest label travels with every downstream
number. Prices do not restate, so price history is point-in-time safe.

### 5.3 The raw vault

Content-addressed, immutable archive of provider payloads captured **before**
normalization. The canonical store is therefore rebuildable, and a parsing bug
found later can be re-run against what the provider actually said rather than
against what was inferred at the time. Vault growth is capped by a rolling
retention window.

### 5.4 Storage model

SQLAlchemy entities in `equisense/models.py`. Notable choices: filing-date
versioned statements (restatements are versions, not overwrites), an immutable
transaction ledger rather than mutable holdings, and structured theses with
required falsifiable assumptions.

Schema changes are applied by `ensure_schema()` — `create_all` for new tables
plus additive column migrations, dialect-portable across SQLite and Postgres.

### 5.5 Exchange archives

NSE publishes delivery percentage, index valuation (P/E, P/B, dividend yield)
and the option-chain surface as **one file per day**, with no history endpoint.
These cannot be backfilled: a missed day is a permanent hole, not a delay. This
is why they are captured first in the cron (§3.4) and why the variance-risk-
premium study (HYP-015) is registered-deferred until the series matures.

### 5.6 Data quality as a subsystem

`/api/live/status` decomposes a quality score into named components — price
freshness (penalised by *breadth* of staleness, not just age), volume
completeness, fundamental coverage, studies currency, ledger integrity — and
never reports a mystery number.

Staleness is measured in **trading sessions against the universe's own
calendar**, not calendar days, so the Indian holiday calendar cannot make a
healthy name look frozen. Per-name staleness is tracked separately from the
dataset maximum, because one current name would otherwise make the whole dataset
read fresh while individual names sat weeks behind.

---

## 6. Analysis engines

All engines are pure functions over `StatementData` and price series, returning
`Metric` objects (value + formula + inputs + caveats). No I/O, no web, callable
from a notebook.

### 6.1 Ratio engine

25+ ratios in five families: profitability (including DuPont decomposition and
ROIC), leverage, liquidity, efficiency and per-share. Banks and NBFCs are
flagged `is_financial` and skipped by statement engines — bank statements do not
fit the industrial canonical schema, and that is stated rather than silently
computed wrong.

### 6.2 Valuation — reverse DCF

Solves *backward* from the market price: what FCF growth is currently priced in?
Every assumption (risk-free rate, ERP, beta, tax, horizon, terminal growth) is
exposed and editable in the UI. WACC is estimated from market data rather than
hardcoded. The output is explicitly labeled as a statement about present
expectations, not a forecast.

The companion **Expectations Gap** compares implied growth against the company's
own delivered growth.

### 6.3 Cash-flow quality and distress

Accruals ratio, CFO/net income, capex intensity, capex/depreciation, free cash
flow. Piotroski F-Score (9 binary signals, each shown) and Altman Z — the latter
carrying its calibration caveat visibly, since it was fitted on 1968 US
manufacturing and is directional at best for Indian services businesses.

### 6.4 Portfolio intelligence

A transaction ledger, never mutable holdings. FIFO tax lots with India
LTCG/STCG aging, correct money-weighted XIRR (dividends included), and
four-axis concentration diagnostics — including a quality-tier axis answering
how much capital sits in fundamentally fragile businesses. Unmatched sells are
surfaced rather than absorbed.

Risk monitoring adds a 126-day correlation matrix, portfolio heat against
budget, and naive risk contribution.

### 6.5 Cross-asset relationships

Conditional correlation between the book and macro factors (index, currency,
crude, gold), computed per regime rather than unconditionally.

### 6.6 Personalization

The investor profile changes the ranking *function* and the card *ordering*
(the lens system), not a colour theme. The acceptance test is literal and
enforced by a unit test: same company, two profiles, different order.

### 6.7 Proprietary analytics

Each is itself a registered hypothesis subject to the full validation gauntlet
(§9): **Momentum Quality Index** (vol-scaled, persistence-weighted momentum),
**Cash Conviction Score**, **Fragility Index**, **Expectations Gap**,
**Trend–Value Tension** quadrants, **Participation Heat** (volume-surge ×
extension, flagging late-crowd entries), and range-based (Yang–Zhang) volatility
for sizing.

---

## 7. Evidence and synthesis

### 7.1 The Evidence object

The platform's core contract. Every engine emits typed Evidence carrying:
statement, direction, strength, cluster, tier, the base rate it appeals to, and
its caveats. Synthesis consumes only Evidence — no engine reaches around it.

### 7.2 Percentile normalization

Hand-picked strength scales are abolished. Evidence strength is the
**cross-sectional percentile rank within the universe**, mid-ranked so that ties
no longer read as maximum conviction. This makes strengths comparable across
engines that measure different things in different units.

### 7.3 Admission tiers

Influence is **earned**, mechanically, through the hypothesis lifecycle:

| Status | Cap on \|strength\| |
|---|---|
| registered / computed | ±0.25 (provisional) |
| registered-deferred | 0 — renders as **SHADOW** |
| validated | ±0.60 |
| deployed | ±1.00 |

Deferred hypotheses appear in every dossier and aggregate into none: visible,
uninfluential.

### 7.4 Aggregation

Mechanical, monotone and inspectable. Cluster weights stay **uniform and
provisional** until enough scored claims accumulate per cluster, at which point
learned weights unlock (§10.2). The gate counts *independent* cycles, not raw
overlapping claims. Synthesis surfaces dissent by name and treats **abstain** as
a first-class verdict.

### 7.5 The decision dossier

The only recommendation artifact. Carries verdict, net score, per-cluster
scores, confidence with components, every piece of evidence with its base rate
and caveats, sizing with shown work, cost and tax physics, and the pre-registered
claim it is committing to. Every dossier is written to the ledger (§10.1) before
it is shown.

---

## 8. Decision plane

### 8.1 Sizing

Advisory, with the work shown. Volatility-based stops, heat and liquidity caps,
a concentration gate that flags and demotes correlated picks rather than
silently taking both, and a permanent 0.5 provisional haircut while weights are
unlearned. Missing sizing inputs must never *enlarge* a position.

Every gate and every binding constraint is named in the output.

### 8.2 Paper account and autopilot

A paper book at real, executable prices, benchmarked against NIFTY 500 (not
NIFTY 50 — using a 50-name cap-weighted index would credit a broad strategy with
a size premium it did not earn). Autopilot trades this book on policy: entries
take top qualified candidates within caps; exits fire on stop breach, time, or
verdict flip. Every action *and every skip* is reasoned and ledger-chained.

### 8.3 Costs and taxes as physics

India-specific and first-class: STT and statutory charges, impact estimate,
round-trip cost, breakeven gross move, and the STCG/LTCG 12-month cliff. A
strategy that does not clear its own costs is reported as not clearing them.

---

## 9. Research lifecycle

### 9.1 Base-rate studies

Pre-registered hypotheses run as leakage-controlled cross-sectional event
studies against the platform's own stored history, publishing N, N_eff, hit
rate, median excess, net-of-cost median, 95% CI and IQR per regime cell.

**N_eff everywhere**: overlapping episodes are overlap-corrected, and
publication is gated on N_eff ≥ 30, not raw N. Thin cells are suppressed. A
survivorship caveat is stamped on every record.

### 9.2 Hypothesis registry

`equisense/research/registry.py` — hypotheses are pre-registered **in code**,
with name, family, motivation (with literature citation where one exists), and a
falsifiable spec. Failures become permanent records; nothing is quietly dropped.
The registry drives the admission caps in §7.3.

---

## 10. Ledger, learning and calibration

### 10.1 The hash-chained decision ledger

Append-only and tamper-evident. Every dossier and every paper fill is
pre-registered as a hash-chained record carrying a **direction**, a stated
**probability**, and a predicted excess-return **magnitude** — all fixed before
the outcome is known.

Storage is file-backed locally and database-backed on hosted deployments where
the filesystem is ephemeral; the chain is identical either way. Verification is
a read, never a mutation, and is not performed on every status call.

### 10.2 Learning and calibration

The loop that makes the system improve:

1. A dossier registers a claim with a horizon.
2. An **interim checkpoint** fires roughly a quarter of the way to the horizon,
   comparing realized-so-far against the pro-rated prediction — so drift shows
   up early instead of six months later.
3. At horizon the claim is scored against realized universe-relative returns:
   direction (hit / Brier) **and** magnitude (predicted vs realized, forecast
   error).
4. Scored outcomes update per-cluster Beta-Binomial posteriors, refit the
   probability calibration, and refit the magnitude calibration.
5. Influence unlocks only past pre-registered sample gates (§7.3).

Abstentions are scored too: chronic abstention on winners is a measured cost,
reported as a wrongful-abstention rate.

Forecasts are registered **autonomously by the daily cron**, not only when a
human clicks "Generate dossier". Without that the calibration ledger sits at
zero indefinitely and every weight stays provisional forever — which is exactly
what happened while the cron was timing out (§3.4).

---

## 11. Regime intelligence

A description of *present conditions* used to condition historical base rates —
explicitly **not** a market-timing forecast. Components (NIFTY vs 200DMA, India
VIX percentile, USD/INR and Brent trend) each show their formula and inputs.

The regime engine is required to justify its own existence. **REG-001** splits
momentum episodes at the median date, fits conditional and unconditional hit
rates on the first half, and Brier-scores both on the second. Result on live
data: ΔBrier −0.00025, verdict *no measurable value* — so regime conditioning is
demoted to descriptive context and base rates serve unconditional cells until a
better regime definition passes. Recorded in the registry, not buried.

---

## 12. Validation and backtesting

Standards applied to the platform's own strategy, not just to hypotheses:

- **Overlapping tranches** run as genuinely non-overlapping steps of one
  investable portfolio, so the reported period count is real rather than a
  correction.
- **Deflated Sharpe** (Bailey & López de Prado) for the number of rules tried —
  read `deflated_sharpe`, never `sharpe_naive`.
- **Survivorship correction** using a point-in-time listed universe rebuilt from
  NSE bhavcopies, plus a delisting stress test: the return must survive adding
  the dead.
- **CPCV** (combinatorial purged cross-validation) where the sample supports it.
- **Drawdown measured daily**, not on rebalance steps.
- **Net of costs** always (§8.3).
- **Information Coefficient** reported alongside its detection limit, so "no
  measurable signal" is distinguishable from "underpowered test".

---

## 13. AI narration

### 13.1 Narration

Three surfaces: statement explanation, portfolio briefing, and a thesis-drafting
assistant that requires the user's own angle first — the thesis stays the
user's reasoning. Entirely optional; without `ANTHROPIC_API_KEY` the endpoints
return `available: false` with a configuration message, and every analytical
feature continues to work.

### 13.2 The grounding contract

The hard rule from §1, mechanised. The validator extracts every numeric token
from model output and checks it against the supplied context with rounding
tolerance. A violation triggers one corrective retry; the result and the exact
context the model received are both shown in the UI. The model may explain
numbers, never invent them.

---

## 14. API and web layer

One FastAPI application (`equisense/api/app.py`) serving both the JSON API and
the static UI. Services assemble engine output; engines never import web code.

**Serverless adaptations** that matter:

- Startup does **no** database work on a hosted host — waking a suspended free
  Postgres inside the init window produced `FUNCTION_INVOCATION_FAILED` and took
  the whole site down. Startup is best-effort and failures are carried to
  endpoints, which already know how to report a broken database. A site that
  loads and says "no database" beats one that will not load.
- Heavy reads go through a **published universe snapshot** (`app_snapshots`), so
  views are single-row reads. The snapshot rebuilds when prices are newer than
  it, guarded by a throttled freshness probe.
- Query *count* is the latency over a network Postgres, not query cost —
  aggregates are collapsed into single statements with scalar subqueries.
- The dashboard payload ships sparklines only for rows that can display one.

The UI is a dense single-page workstation: Dashboard (command center), Companies
(tabbed workspace per name, including per-company Memory), Portfolio, Trading
desk, Research (theses / journal / watchlist), Markets (derivatives, variance
premium, Monte Carlo risk, cross-asset, valuation regime, institutional flow),
and Lab (hypotheses, base rates, signal IC, factor P&L, calibration + ledger
browser, backtest, data health). Always-on status strip, SSE refresh drawer,
command palette (Ctrl+K), `g d/c/p/r/l` navigation.

---

## 15. Testing

The correctness gate: engine tests must pass before the AI layer is allowed to
describe any number.

Reference values in `tests/` are **hand-computed** from fixed synthetic
statements, so a test failure means the engine changed, not that a fixture
drifted. Beyond unit correctness the suite covers hosted-mode behaviour
(ephemeral-storage write refusal, auth gate, missing-driver handling), frontend/
backend contract alignment (every route reachable from the UI or explicitly
exempt; panels read keys the endpoints actually return), and regression tests
for defects found in production — each written to fail against the old code.

```bash
pytest -q
```

---

## 16. Boundaries held on purpose

No order execution. No price prediction. No social or copy-trading features. No
intraday or tick data. No news sentiment. No mobile app. No multi-tenancy.

Each is a deliberate scope decision, not a backlog item: they either invite
fortune-telling, require infrastructure that dwarfs the analytical value at
single-user scale, or turn a reasoning tool into a signal service.

---

## 17. Operating notes

**Data looks stale.** Check the status strip and `/api/live/status` warnings
first — freshness, per-name staleness and configuration errors are all reported
there. Click ⟳ Refresh, or wait for the daily cron (§3.4).

**Everything reads zero after deploying.** Almost always a missing
`DATABASE_URL`: the app fell back to ephemeral SQLite. `data_status()` says so
explicitly as its first warning.

**The site is public.** `EQUISENSE_ACCESS_TOKEN` is unset (§3.3). Set it and
`CRON_SECRET` to the same value, then redeploy.

**Calibration never progresses.** Scored claims only accumulate if forecasts are
registered daily (§10.2), which is the cron's job. If the cron is failing, the
learning loop is dead while everything else looks healthy — check the Vercel
cron logs for timeouts and the per-stage durations the cron logs.

**A ticker stopped updating.** Likely a corporate action renaming the symbol.
The exchange-derived universe self-heals when its NSE fetch succeeds;
`YAHOO_SYMBOL_OVERRIDES` in `ingestion/universe.py` covers the pinned fallback.
