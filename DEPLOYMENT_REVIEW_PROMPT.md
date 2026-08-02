# External Review Request — EquiSense (Live Vercel Deployment)

Paste this whole document into AI Studio (or any capable model). Give it the
deployment URL and, if the access token is set, a working link. Ask it to work
through Sections 5–8 in order.

---

## 0. How to brief the reviewer

> You are conducting an adversarial external review of a live, deployed
> quantitative equity system for Indian markets. Real family capital is intended
> to be deployed alongside its output. Your job is to find the reason a number
> is wrong, not to encourage. Where you cannot verify a claim from what you are
> given, say so explicitly rather than assuming it holds.

**Deployment URL:** `<PASTE VERCEL URL>`
**Access:** single-user token gate; append `?token=<TOKEN>` if `EQUISENSE_ACCESS_TOKEN` is set.

---

## 1. What the system is

A single-user web application that ingests Indian equity data, computes
fundamental and technical signals, tests whether those signals predict returns,
and produces sized, caveated candidates. **It places no orders.** It runs a
paper-trading account and a SHA-256 hash-chained decision ledger that records
its own forecasts before outcomes are known.

Enforced design stances, which the reviewer should test rather than trust:
- Absence of data emits nothing, never a neutral zero.
- Abstention is the modal, correct output.
- Backtest results are **never** allowed to set live weights; cluster weights
  unlock only from realised scored forecasts, of which there are currently ~2.

## 2. Stack and hard constraints

| | |
|---|---|
| Backend | Python 3.14, FastAPI, SQLAlchemy 2.0 |
| Database | Neon Postgres, **free tier**, 512 MB ceiling, ~196 MB used |
| Frontend | vanilla JS SPA, ~2,300 lines, no framework, no build step |
| Hosting | Vercel serverless, `maxDuration: 300`, cron 13:30 UTC weekdays |
| Deliberate | **no scipy / statsmodels / sklearn** — all statistics hand-rolled on stdlib + numpy |
| Data | Yahoo Finance + NSE public archives. Free, keyless, no paid feeds. |

**Data held:** ~1,005,000 daily bars, 501 companies (Nifty 500), 2016-08 →
2026-07, 962,565 with full OHLC. Published index series (NIFTY 50/100/500,
Midcap 50, Smallcap 250, S&P 500), USD/INR, India VIX, Brent, gold. Plus a
point-in-time listing table of 3,428 symbols reconstructed from 116 monthly NSE
bhavcopies.

## 3. Endpoints worth exercising

```
GET /api/live/status              data-trust surface, quality decomposition, warnings
GET /api/live/candidates          the actual decision output (slow: scans ~395 names)
GET /api/live/ic                  information coefficient per signal, cached
GET /api/live/factor-portfolio    what each factor PAYS, net of costs
GET /api/live/base-rates          base-rate records with multiplicity control
GET /api/backtest/strategy        the headline backtest (add ?refresh=true to recompute)
GET /api/live/ledger              hash-chained forecast ledger + chain verification
GET /api/live/calibration         forecast scoring state
GET /api/storage                  Neon headroom, largest tables
GET /api/markets/sources          upstream source reachability
```

UI routes: `#/dashboard`, `#/companies`, `#/portfolio`, `#/trading`,
`#/research`, `#/markets`, `#/lab`. The Lab carries Hypotheses, Base Rates,
Signal IC, **Factor P&L**, Calibration & Ledger, Backtest, Data Health.

## 4. Measured results, with the caveats attached

### 4a. The headline, and why it is not the number to quote

Composite rule: top-3 by percentile composite of (12-1 momentum, momentum
quality, trend vs 200DMA, inverted vol, inverted crowding); 63-day hold as 3
overlapping monthly tranches; 0.35% round-trip cost on actual turnover.

| | Value |
|---|---|
| Annualized, net of costs | **32.3%** |
| After 20% STCG | **25.8%** |
| NIFTY over same span | 12.18% |
| Max drawdown | −21.3% |
| Turnover | ~30%/month (~370%/yr) |
| CPCV, 15 OOS paths | min +14.5%, median +26.8%, max +53.1%, **0 paths lose money** |
| Deflated Sharpe | passes to ~50 trials, fails by 75 |

### 4a-bis. Basket width — the concentration fix, measured

A three-stock book puts 33.3% in each name; one promoter default or forensic
audit in an Indian mid-cap gaps down in consecutive lower circuits with no exit.
Measured across N=3..30:

| N | Ann % | After 20% STCG | Max DD | Sharpe | CPCV min | Per-position | −50% shock costs |
|---|---|---|---|---|---|---|---|
| 3 | 32.30 | 25.84 | −21.31 | 1.45 | 20.99 | 33.3% | 16.7% |
| 5 | 33.93 | 27.14 | −20.04 | 1.56 | 21.48 | 20.0% | 10.0% |
| 10 | 29.40 | 23.52 | −19.99 | 1.40 | 16.05 | 10.0% | 5.0% |
| **15 (default)** | **29.75** | **23.80** | −20.59 | 1.40 | 15.26 | **6.7%** | **3.3%** |
| 20 | 28.37 | 22.69 | −21.19 | 1.37 | 13.97 | 5.0% | 2.5% |

The CAGR is nearly flat while catastrophe exposure falls fivefold, so the
default is now N=15. Turnover also FALLS with N (30% → 25%), reducing cost and
tax churn. **What widening does not buy: max drawdown is flat near −20% at
every N**, because those drawdowns are market-wide rather than idiosyncratic.
Diversification here protects against the single-name disaster, not the market.

### 4b. The finding that undercuts it

The backtest holds `nlargest(3)` **unconditionally** — always invested, never
cash. The live screen **abstains**: 0 long candidates out of 395 on the current
universe. Adding an absolute quality bar and costing idle cash at a 5.5% swept
yield:

| Quality bar | Annualized | After tax | Invested fraction |
|---|---|---|---|
| none | 32.30% | 25.84% | 0.98 |
| 0.85 | 8.76% | 7.01% | 0.15 |
| 0.90 | 6.03% | 4.82% | 0.01 |
| 0.95 | 5.54% | 4.43% | 0.00 |

**The backtest and the live system measure opposite strategies.** This is the
central unresolved question and the reviewer should attack it first.

### 4c. Survivorship, quantified and partially corrected

- Panel is today's index membership backfilled; it held **1 of 771** names that
  stopped trading over the decade.
- Recovered 142 of those 771 from the price provider and re-ran: **33.07% →
  34.01%**. Drawdown deepened, hit rate fell, return held.
- Zero-out stress test (mark suspended names to ₹0.00, 55,535 cells): **34.81%**.
  Verified mechanically that **zero held-through-death windows exist** — the
  composite ranks a dying name out of the top-3 long before it dies.
- Reconstructed equal-weight basket returns 24.67%/yr vs the published NIFTY 500
  at 12.33% — a **+12.34pp/yr gap** from survivorship plus equal-weighting.
  Absolute levels from this panel are therefore meaningless; only excess is used.

### 4d. Signals, measured three ways

At ~390 names/date the minimum detectable IC is 0.0092–0.0248 (it was 0.0667 at
55 names, which is why an earlier "no edge" result was a power artefact).

| Hypothesis | 63d IC | t | Long-only net %/yr | Monotonicity | Turnover |
|---|---|---|---|---|---|
| HYP-001 momentum 12-1 | +0.067 | 3.16 | **+9.66%** | 1.00 | 0.22 |
| HYP-004 momentum quality | +0.065 | 3.38 | +7.29% | 1.00 | 0.24 |
| HYP-007 participation heat | +0.036 | 2.65 | +6.52% | 1.00 | 0.45 |
| HYP-010 sector rel. momentum | +0.022 | 1.40 | +6.01% | 0.40 | 0.45 |
| HYP-008 low volatility | −0.007 | −0.23 | −8.06% | −1.00 | 0.14 |
| HYP-011 MAX effect | −0.013 | −0.73 | −6.11% | −1.00 | 0.63 |

HYP-001 ↔ HYP-004 correlate **0.970** and HYP-007 ↔ HYP-010 **0.812**, so this
is **two discoveries, not four**. HYP-008/011 are demoted to `weak` (cap 0.10);
both flag `tail_driven`, meaning mean and median spreads disagree in sign.

### 4e. Refutations recorded, so they are not re-litigated

- **HYP-017** momentum confirmed by news gaps: +1.30%/yr vs +14.79% plain.
  REFUTED — the filter destroyed the cross-sectional ranking. Its **control**
  (momentum *without* confirmation, +9.54%) beating the treatment is what
  identified it as a construction failure rather than a null.
- **HYP-020** momentum inside a high-news universe: in-sample IC +0.093 vs
  +0.067, spread t=4.08, +25.9%/yr net, robust to 10× costs. Walk-forward OOS:
  **+0.060 vs +0.062 for plain momentum**. The advantage vanished. Registered
  REFUTED_OUT_OF_SAMPLE.

### 4f. Benchmark reality

Real index series, 2016-08 → 2026-08, no survivorship bias:

| Index | CAGR | Vol | Max DD |
|---|---|---|---|
| NIFTY 50 | 10.94% | 16.2% | −38.4% |
| NIFTY 500 | 12.33% | 16.3% | −38.3% |
| NIFTY Midcap 50 | 17.20% | 20.8% | −49.1% |
| S&P 500 (USD) | 13.19% | 18.1% | −33.9% |
| **S&P 500 (in INR)** | **17.08%** | 19.4% | **−30.2%** |

The rupee depreciated 3.68%/yr. An Indian investor holding the S&P earned the
dollar return plus that.

## 5. Live deployment checks (do these against the URL)

1. **Does it load and function?** Walk all seven routes. Note anything that
   renders blank, throws, or shows a spinner indefinitely.
2. **Latency.** Time each endpoint in §3. Vercel's `maxDuration` is 300s.
   `/api/live/candidates` scans ~395 names. Does anything approach the limit?
3. **Honesty of the data-trust surface.** `/api/live/status` decomposes a
   quality score and lists warnings. Do the warnings match what the other
   endpoints show? Is anything stale being presented as current?
4. **Staleness marking.** Frozen prices are badged `Nd stale` in the dashboard,
   company list and company header. Is any price shown unbadged that shouldn't
   be? (Five Nifty-50 names were once 13 sessions behind with nothing saying so.)
5. **Ledger integrity.** `/api/live/ledger` returns the chain plus a
   verification. Does it verify? Note that `/api/live/status` deliberately does
   **not** walk the chain by default (`?verify_ledger=true` forces it) because
   it is an O(n) re-hash.
6. **Caveat propagation.** Open a company dossier. Every evidence item should
   carry its cluster, strength, admission weight (`counts at ×0.25`) and any
   caveat from its own factor study. Are tail-driven signals labelled?
7. **Storage.** `/api/storage` against a 512 MB ceiling. How much runway?

## 6. The questions that matter most

Answer these directly and plainly.

1. **§4b is the crux.** The backtest is always-invested; the live system
   abstains. Which strategy should be run, and does the 32% figure have any
   bearing on the abstaining system? If not, what *is* the expected return of
   what is actually deployed?
2. **Is the momentum result believable?** Attack the survivorship defence
   (§4c), the two-not-four reduction (§4d), the Deflated Sharpe trial count, and
   the in-sample framing.
3. **Given §4f, is active management justified at all here?** A passive
   S&P-500-in-INR allocation returned 17.08% with a *smaller* drawdown (−30.2%).
   The N=15 active book returns 23.80% after STCG with −20.6% drawdown and 6.7%
   per-position weight. That is a ~6.7pp after-tax premium at comparable
   drawdown — but with key-person dependency on a custom stack, a free-tier
   database, monthly execution load, and zero forward track record. Is that
   premium worth it? Say so plainly if not.
4. **What is the single highest-value missing capability?** Historical *index
   membership* is the known candidate (bhavcopy gives listed universe, not index
   membership). Is there something better?
5. **Where is the statistical reasoning wrong?** Every estimator is hand-rolled
   without scipy — incomplete beta via Lentz continued fraction, Newey-West,
   Benjamini-Hochberg, Deflated Sharpe, CPCV with purge and embargo. Assume bugs
   exist; say where you would look first.
6. **What would you require before real capital is deployed?**

## 7. Known-weak areas — probe these hardest

- **No forward track record.** ~2 registered forecasts, 0 scored. Calibration
  gates at 30 scored claims; learned weights at 150 per family. Everything is
  historical.
- **Fundamentals are not point-in-time.** Yahoo serves latest-known figures, so
  all fundamental signals carry `pit_grade="reconstructed"` and look-ahead risk.
  Price history *is* PIT-safe.
- **Search not fully accounted.** FDR is applied within the IC family, but the
  search *over strategy constructions* is only partly captured by walk-forward
  and the DSR sensitivity sweep.
- **Costs are statutory only** (0.35% round trip). Market impact modelled as
  square-root of participation; bid-ask crossing excluded from headline figures.
- **Circuit-lock exits** are measured (0.17% of 881,512 bars close at low *and*
  at a standard limit; ~0.54 expected blocked exits across the backtest) but not
  yet enforced in the fill engine.

## 8. Deliverable requested from the reviewer

1. A ranked list of defects, most severe first, each with the concrete failure
   scenario it produces.
2. A direct answer to §6.1 and §6.3 — no hedging.
3. One recommendation: deploy capital, paper-trade further, or abandon the
   active approach for passive. With the reasoning.
