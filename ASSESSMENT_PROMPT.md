# Independent Assessment Request — EquiSense

You are being asked to critically assess a personal quantitative equity research
and decision-support system for Indian markets. **Real money is at stake**: the
owner intends to use its output alongside a family member's existing, manually
managed trading account. Please be adversarial rather than encouraging. The most
valuable thing you can do is find the reason a number below is wrong.

---

## 1. What it is

A single-user web application that ingests Indian equity data, computes
fundamental and technical signals, tests whether those signals actually predict
returns, and produces sized, caveated trade candidates. It is explicitly **not**
an execution system — it places no orders. It has a paper-trading account and a
hash-chained decision ledger for tracking its own forecasts.

Design stance the codebase enforces throughout:
- Absence of data is absence, never neutral evidence — a missing signal emits
  nothing rather than a zero.
- Every displayed number expands to its formula, inputs and caveats.
- Abstention is the modal, correct output. On a recent full scan: 395 companies
  evaluated, **2 candidates**.
- Backtest results are never allowed to set live weights. Cluster weights unlock
  only from *realised* scored predictions, which do not yet exist.

## 2. Stack and constraints

- **Backend**: Python 3.14, FastAPI, SQLAlchemy 2.0
- **Database**: Neon Postgres, free tier (512 MB hard ceiling; currently 196 MB)
- **Frontend**: vanilla JS SPA (~2,300 lines), no framework, no build step
- **Hosting**: Vercel serverless, `maxDuration: 300`
- **Deliberate constraint**: no scipy / statsmodels / sklearn. Everything
  statistical is stdlib + numpy, hand-implemented (incomplete beta function via
  Lentz continued fraction, Newey-West, Benjamini-Hochberg, etc.)
- **Data**: Yahoo Finance (prices, fundamentals, corporate actions) and NSE's
  official public archives (F&O bhavcopy, delivery %, index constituents). All
  free and keyless. No paid feeds.

**Data currently held**: ~1,005,000 daily price bars, 501 companies (Nifty 500
membership), 10 years (2016-08 → 2026-07), 962,565 bars carrying full OHLC.
Plus published index series (NIFTY 50 / 100 / 500 / Midcap 50 / Smallcap 250,
S&P 500), USD/INR, India VIX, Brent, gold.

## 3. Research methodology

Signals are registered as numbered hypotheses (HYP-001…020) with a
pre-registered specification, then tested three ways:

1. **Information Coefficient** — per-date cross-sectional Spearman rank
   correlation between signal and forward return (Fama-MacBeth), with
   Newey-West HAC standard errors for overlapping windows, and a
   *minimum detectable IC* computed for every cell so a null reads as "absent"
   or "unresolvable" rather than ambiguously either.
2. **Quantile portfolios** — top-minus-bottom spread, monotonicity across
   quantiles, basket turnover, factor autocorrelation, and annual return **net
   of India's statutory round trip** (STT, stamp, exchange, SEBI ≈ 0.215%).
   Reported both long-short (evaluation) and long-only (tradeable — single-stock
   shorting is unavailable in the NSE cash segment).
3. **Base rates** — forward excess return vs the same-date universe median for
   the top quantile, with cluster-robust inference, purging and embargo,
   Benjamini-Hochberg FDR, and the Harvey-Liu-Zhu |t| ≥ 3 hurdle.

## 4. What has been measured

**Universe expansion was the decisive experiment.** At 55 names the minimum
detectable IC was 0.0667 while every measured IC fell in 0.022–0.071 — so "no
signal passes" was a statement about *detection limit*, not about absence of
edge. At ~390 names per date the detection limit fell to 0.0092–0.0248:

| Hypothesis | 63d IC | t | 63d long-only net %/yr | Monotonicity | Turnover |
|---|---|---|---|---|---|
| HYP-001 momentum 12-1 | +0.067 | 3.16 | **+9.66%** | 1.00 | 0.22 |
| HYP-004 momentum quality | +0.065 | 3.38 | +7.29% | 1.00 | 0.24 |
| HYP-007 participation heat | +0.036 | 2.65 | +6.52% | 1.00 | 0.45 |
| HYP-010 sector rel. momentum | +0.022 | 1.40 | +6.01% | 0.40 | 0.45 |
| HYP-008 low volatility | −0.007 | −0.23 | −8.06% | −1.00 | 0.14 |
| HYP-011 MAX effect | −0.013 | −0.73 | −6.11% | −1.00 | 0.63 |

8 of 18 IC cells survive Benjamini-Hochberg FDR; 5 clear |t| ≥ 3. Base rates:
24 of 45 cells survive multiplicity control.

**Redundancy caveat**: HYP-001 ↔ HYP-004 correlate **0.970** cross-sectionally
(the same signal twice) and HYP-007 ↔ HYP-010 correlate 0.812. Between those two
groups ρ ≈ 0.31. So this is **two discoveries, not four**.

### Findings that changed conclusions

- **Mean vs median spreads.** HYP-008's mean top-minus-bottom spread is
  −18.6%/yr but its **median** spread is +2.3%/yr — the sign flips. The entire
  mean effect is a handful of enormous winners in one bucket. This also
  reconciles rank-IC (blind to fat tails) against mean spreads (dominated by
  them). A `tail_driven` flag now travels with every factor result.
- **Information features were refuted.** Overnight gaps were used as a free
  proxy for news arrival (post-earnings-announcement drift). Momentum
  *confirmed* by news returned +1.30%/yr vs +14.79% for plain momentum — the
  filter destroyed the cross-sectional ranking. The **control** (momentum
  *without* confirmation, +9.54%) beating the treatment is what identified this
  as a construction failure rather than a null.
- **A variant that looked outstanding died out of sample.** Momentum restricted
  to a high-news universe: in-sample IC +0.093 vs +0.067, spread t = 4.08,
  +25.9%/yr net, robust even at 10× assumed costs. Walk-forward with purging and
  embargo: **OOS IC +0.060 against +0.062 for plain momentum on all names.** The
  entire advantage vanished — it was the product of searching three universe
  constructions. Registered as REFUTED_OUT_OF_SAMPLE so it is not re-litigated.

## 5. Known limitations — please probe these hardest

1. **Survivorship bias, quantified.** The price panel is *today's* Nifty 500
   membership backfilled 10 years, retaining exactly **one** departed
   constituent. The reconstructed equal-weight basket returns 24.67%/yr against
   the published NIFTY 500's 12.33% — a **+12.34%/yr gap**. Therefore all
   *absolute* returns from this panel are meaningless; only *excess over the
   same reconstructed basket* is used, on the argument that the bias largely
   cancels in the difference.
   *Partial defence*: momentum selects names that rose and survivorship retains
   names that rose, so cancellation is not automatic. A split-sample test found
   the excess is **larger in the recent, less-biased half** (+11.06%/yr
   2021-26 vs +8.41%/yr 2016-21) — the opposite of the artefact signature.
   **Is that test sufficient? What would you demand instead?**
2. **No forward track record.** Every number above is historical. The system has
   made zero scored predictions. Its calibration ledger and learned cluster
   weights are gated behind realised outcomes and remain locked.
3. **In-sample, and searched.** Several constructions were tried. FDR is applied
   within the IC family, but the *search over constructions* is only partly
   accounted for by the walk-forward check.
4. **Fundamentals are not point-in-time.** Yahoo serves latest-known figures, so
   all fundamental signals are flagged `pit_grade="reconstructed"` and carry
   look-ahead risk. Price history is PIT-safe.
5. **Costs are statutory only.** Market impact and bid-ask crossing are excluded
   from the headline net figures, though a sensitivity test showed the momentum
   result survives a 10× cost assumption.
6. **Benchmark sensitivity.** Excess is measured against the equal-weighted
   universe. Against NIFTY 50 the same strategy would show ~23%/yr, of which
   roughly 13pp is size premium rather than skill.

## 6. A relevant comparison the system produced

Over 2016-08 → 2026-08, real index series, no survivorship bias:

| Index | CAGR | Vol | Max DD |
|---|---|---|---|
| NIFTY 50 | 10.94% | 16.2% | −38.4% |
| NIFTY 500 | 12.33% | 16.3% | −38.3% |
| NIFTY Midcap 50 | 17.20% | 20.8% | −49.1% |
| S&P 500 (USD) | 13.19% | 18.1% | −33.9% |
| **S&P 500 (in INR)** | **17.08%** | 19.4% | **−30.2%** |

The rupee depreciated 3.68%/yr over the period. **Given a ~+9.7%/yr excess with
real execution risk versus a passive S&P-500-in-INR allocation that delivered
17.08% with a smaller drawdown — is the active strategy actually justified?**
Please answer this directly.

## 7. Engineering quality context

386 tests pass. The codebase has a documented habit of encoding *why* a fix
exists, including reverted ones. Representative defects found and fixed:

- Tie handling in cross-sectional percentiles gave every member of a tied group
  the group's top rank — an all-identical universe returned +1.0, maximum
  bullish conviction from a signal carrying no information.
- Price staleness was measured as `max(obs_date)` over the whole table, so one
  current name made the dataset read fresh while five Nifty-50 constituents sat
  frozen 13 trading sessions behind.
- A memoisation of the ledger's tamper-detection check silently defeated it
  (editing record 0 changes neither the record count nor the last hash). Caught
  by an existing test, reverted, and documented.
- The candidate endpoint re-read the entire ledger once per company scanned:
  396 full reads, 361 of its 668 seconds. Now 24 queries total.

## 8. What to assess

1. **Is the momentum result believable?** Attack the survivorship defence, the
   benchmark choice, the multiple-testing treatment, and the in-sample framing.
2. **Is the two-discoveries-not-four reduction handled correctly** in a system
   that aggregates evidence across correlated clusters?
3. **Given §6, is any of this worth doing** versus a passive allocation? Say so
   plainly if not.
4. **What is the single highest-value missing capability?** Historical index
   membership is the known candidate — is there something better?
5. **What would you require before risking real money on this?**
6. **Where is the statistical reasoning wrong?** The implementations are
   hand-rolled; assume bugs exist and say where you would look.
