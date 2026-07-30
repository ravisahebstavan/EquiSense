# EquiSense — architecture review request

Copy everything below the line into another AI. It is written to be
self-contained: no repo access needed.

---

You are reviewing the architecture of a working financial analysis platform.
I want blunt, specific, technically-grounded criticism and recommendations —
not encouragement. Assume I can handle "this part is wrong, here is why".

## What it is

**EquiSense** — a single-user equity research and decision-support platform for
the Indian market (NSE). It is a private tool for my own investing, not a
product. I am about to start trading real money, small size. Correctness matters
more than features; a wrong number that looks confident is worse than no number.

It is a Python monolith: FastAPI + SQLAlchemy backend, vanilla-JS frontend,
Postgres (Neon free tier), deployed on Vercel. ~15,700 lines of application code
plus ~3,500 lines of tests (268 tests). Dependencies deliberately minimal:
`fastapi, sqlalchemy, pydantic, anthropic, yfinance, pandas, numpy, psycopg`.
**No scipy, statsmodels or sklearn** — the deploy target is a size-capped
serverless bundle, so all statistics are implemented on stdlib + numpy.

## Governing design commitments

These are load-bearing. Tell me if any are wrong, but understand they are
deliberate:

1. **No price prediction, ever.** No price targets, no buy/sell signals. The
   valuation question is answered by a *reverse* DCF ("what growth is the market
   pricing in?"), which is a computation about the present, not a forecast.
2. **Abstention is a first-class verdict** and is expected to be the modal
   output. A name that shows no edge should say so.
3. **The LLM never originates a number.** Every figure in AI narration is
   computed deterministically first and passed in as context; a grounding
   validator extracts numeric tokens from model output and verifies each exists
   in the supplied context. Violations trigger a retry and are surfaced.
4. **Explainability is structural, not a tooltip.** The engine's unit of output
   is a `Metric`: value + formula-with-numbers-substituted + raw inputs +
   caveats. "Show the work" renders what the engine already returned.
5. **Influence is earned.** Every signal family maps to a pre-registered
   hypothesis with a lifecycle status; status sets an *admission weight* that
   caps how much that family can contribute and how much conviction it can
   support. Unvalidated families are capped; deferred ones render as SHADOW
   (visible, aggregating to nothing).
6. **Every decision is pre-registered in a hash-chained ledger** before the
   outcome is known — direction, stated probability, predicted excess magnitude
   — and Brier-scored when the horizon expires.

## Architecture

```
equisense/
  engine/        4,900 LOC — PURE computation, no I/O, unit-tested
    types.py           Metric + StatementData value objects
    ratios.py          ~25 ratios, DuPont, ROIC (average balances, clamped tax)
    quality.py         Piotroski F, Altman Z (1968) + Z''-EM variant
    valuation.py       WACC, reverse DCF, log-OLS growth, Vasicek beta
    banking.py         financial-sector model (NIM, spread, ROA x leverage)
    technical.py       12-1 momentum, 200DMA, vol, RS, volume anomaly
    novel.py           original composites: MQI, Cash Conviction, Fragility,
                       P/E percentile, Trend-Value Tension, crowding
    derivatives.py     Black-76/BSM + greeks, implied vol, futures basis,
                       option-chain analytics, scenario margin
    montecarlo.py      VaR/CVaR under Gaussian/Student-t/block-bootstrap,
                       drawdown paths, implied-growth distribution, SIP goals
    crossasset.py      correlation w/ Fisher-z CI, STRESS-CONDITIONAL
                       correlation, FDR-controlled lead-lag, driver betas
    rates.py           risk-free DERIVED from futures basis + index div yield
    regime.py          macro regime classification (trend x vol)
    evidence.py        typed Evidence objects + admission control
    synthesis.py       cluster aggregation -> verdict (the decision function)
    sizing.py          vol-based sizing, India cost/tax physics (STT, LTCG)
    portfolio.py       FIFO lots, XIRR (dividend-aware), concentration
    personalization.py profile -> ranking function + card ordering

  research/      1,750 LOC — the measurement substrate
    stats.py           ICC/design-effect N_eff, cluster-robust SE (Liang-Zeger),
                       exact Student-t, Benjamini-Hochberg FDR, Harvey-Liu-Zhu
                       hurdle, Deflated Sharpe (Bailey/Lopez de Prado),
                       cluster bootstrap, Newey-West
    registry.py        pre-registered hypotheses + lifecycle -> admission caps
    base_rates.py      leakage-controlled cross-sectional event studies
    backtest.py        Jegadeesh-Titman overlapping-tranche backtest
    learning.py        Beta-Binomial cluster posteriors, gated calibration
    reg001.py          study asking whether the regime engine earns its place

  ingestion/     1,440 LOC
    yahoo.py           prices (nominal AND total-return), fundamentals, macro
    nse_archive.py     NSE official archives: F&O bhavcopy, cash bhavcopy,
                       delivery %, index closes w/ P/E, index constituents
    universe.py        universe resolved from NSE published constituents
    vault.py           content-addressed archive of raw provider payloads

  api/           3,600 LOC — FastAPI, 50 routes, orchestration only
  ai/              260 LOC — grounding validator + narration
  web/           2,490 LOC — vanilla JS SPA, no framework
```

**19 tables.** Notable: `filing_periods` (versioned by filing date +
restatement), `price_observations` (BOTH `close` = total-return and `close_raw` =
nominal), `transactions` (ledger, never mutable holdings), `ledger_records`
(hash-chained), `base_rates` (study results with full inference decomposition),
`derivative_quotes`, `delivery_stats`, `index_observations`.

## Data sources — all free, keyless, no user setup

- **Yahoo Finance** (yfinance): 10y daily prices for the universe, annual
  statements, macro series, dividends. Unofficial; fundamentals are RESTATED,
  not point-in-time, and everything derived from them is flagged
  `pit_grade: reconstructed`.
- **NSE official public archives** (`nsearchives.nseindia.com`): F&O bhavcopy
  (~35k rows/day: every strike, settlement price, open interest, lot size),
  cash bhavcopy (~3,000 instruments), MTO file (delivery % per security),
  index close file (~140 indices WITH the exchange's own P/E, P/B, dividend
  yield), index constituent lists. These are published archive FILES, fetched
  once per file per day.

Storage is sized for a 0.5 GB free tier: option chains are fetched LIVE and
never stored (nothing studies historical OI, and it was 23 MB of 58 MB), while
delivery % and index valuation ARE accumulated because their value is the
percentile-vs-own-history and the exchange publishes one file per day.

## The decision pipeline

```
prices + statements + macro + NSE archives
        |
   engines emit Metrics (value + formula + inputs + caveats)
        |
   Evidence objects: cluster (trend/value/quality/flow/risk/portfolio),
   strength = cross-sectional PERCENTILE within the universe [-1,+1],
   admission_weight = f(hypothesis lifecycle status)
        |
   synthesis: reliability-weighted mean within cluster, then across clusters
   net_z = net_score / null_sd, where null_sd is the CLOSED-FORM standard
   deviation of net_score for an uninformative name given that exact coverage:
        Var(net) = (1 / 3C^2) * sum_c (1 / m_c)
   |net_z| < 2.0 -> abstain. Conviction band is separately CAPPED by the
   maturity of the strongest hypothesis behind the evidence.
        |
   dossier -> sizing (vol-based stop, heat cap, liquidity cap, India costs)
        |
   pre-registered in the hash-chained ledger, Brier-scored at horizon
```

## Statistical machinery (the part I most want reviewed)

Cross-sectional event studies produce doubly-dependent observations: names
picked on the same date share a market shock, and h-day forward windows sampled
every 21 days overlap. So:

- **N_eff** uses a Kish design effect with an intraclass correlation
  **estimated from the data** (one-way random-effects ANOVA), clustering on date
  blocks spanning the overlap horizon. This replaced `N x 21/h`, which corrected
  only serial overlap and overstated independent information ~10x.
- **Significance** uses a Liang-Zeger cluster-robust SE on G-1 degrees of
  freedom with an exact Student-t p-value. Sanity check that motivated it: a
  series with TRUE mean zero and strong within-date correlation shows t=2.53
  under naive iid inference and t=0.47 (p=0.64) cluster-robust.
- **Multiplicity**: Benjamini-Hochberg FDR across every study cell in a run,
  plus the Harvey-Liu-Zhu |t| >= 3 hurdle for a newly proposed factor.
- **Backtests**: Deflated Sharpe (Bailey & Lopez de Prado) discounting for the
  number of rules tried and for return skew/kurtosis.
- **Confidence intervals**: cluster bootstrap resampling whole date cohorts.

Records are always published with an `admissible` flag rather than suppressed —
a study that failed its power gate is still a measurement.

## Current live results (10y NIFTY-50, real data)

- 45 study cells: 28 admissible, **3 survive multiplicity control**.
  `above_200dma` has t=4.82, q=0.003 but is correctly INADMISSIBLE because it
  selects 63% of the universe (cross-sectionally undistinctive).
- Backtest: 276.7% total vs NIFTY 143.7%, 18.0% annualised, -22.2% max
  drawdown, 21.3%/month turnover, naive Sharpe 0.95 — but **Deflated Sharpe
  0.88 < 0.95**, i.e. not distinguishable from the best of 8 lucky trials.
- Portfolio VaR99: Gaussian -6.69% vs bootstrap of real history -8.97%.
- Market-implied risk-free 6.09% (from futures basis + index dividend yield);
  ERP sanity check = **-1.26pp**, i.e. equities yield less than cash on
  trailing earnings.
- Gold's correlation to Indian equity **rises** in stress (+0.128 -> +0.203).
- Most dossiers abstain.

## Known limitations I am already aware of

Do not spend the response re-telling me these; I want to know what I have
*missed*.

- Fundamentals are restated, not point-in-time. Any fundamental hypothesis is
  therefore deferred rather than tested (look-ahead risk).
- Universe is current index constituents backfilled — survivorship-tilted.
  Caveated on every base-rate record.
- Bank asset quality (GNPA/NNPA/PCR), capital adequacy (CAR/CET1) and funding
  mix (CASA) are unavailable from free sources. The banking engine therefore
  REFUSES to emit a quality composite.
- Delivery % and institutional flow are measured but not yet validated — their
  hypotheses are registered-deferred because the archives publish one file per
  day and history cannot be backfilled.
- No intraday data. No options data other than EOD settlement.
- Single user, single process, no queue, no cache layer beyond snapshots.

## What I want from you

Please be concrete and prioritised. For each point, say what is wrong or
missing, why it matters for DECISION QUALITY, and what specifically to do.

1. **Statistical soundness.** Is the inference actually correct? Specific
   worries: (a) is an ANOVA-estimated ICC on date blocks the right dependence
   model here, or should this be a full panel/Driscoll-Kraay or a
   Fama-MacBeth-with-Newey-West treatment? (b) is applying BH-FDR across
   heterogeneous hypothesis families defensible, or should families be treated
   separately? (c) is the closed-form null for `net_z` (which assumes
   independence across clusters) too permissive given signals are correlated,
   and what is the right correction?

2. **The synthesis function.** Aggregating percentile strengths into a
   reliability-weighted mean and thresholding on a standardised score — is that
   a defensible decision rule, or is there a materially better formulation
   (Bayesian posterior over expected excess return, a proper scoring rule,
   stacking)? What breaks first as more signals are added?

3. **What is structurally missing** from an Indian-market decision system built
   on free data. Which absent input would most change decision quality?

4. **Free, keyless, machine-readable data sources I have not named** — Indian or
   global — that would materially improve this. Especially: point-in-time
   fundamentals, bank asset quality, index-constituent history, sector
   classification, or an Indian yield curve.

5. **Model additions worth the complexity.** I have Monte Carlo, Black-76,
   cluster-robust inference. What else genuinely earns its place — regime
   switching, factor models, Kelly/risk-budgeting, hierarchical risk parity,
   copulas for tail dependence, Bayesian shrinkage of cross-sectional signals?
   Say which are cargo-cult at single-user scale.

6. **Failure modes.** How would this system most plausibly mislead someone into
   losing money? Where is it most likely to be confidently wrong?

7. **Architecture and scale.** Anything structurally wrong with a pure-Python
   monolith on serverless + Postgres for this workload? What breaks first?

8. **State-of-the-art quantitative finance — what does the top of the field
   actually do that I am not doing?**

   Go as deep and as current as you can. I want the real frontier, not a
   textbook summary. Cover at least:

   - **Signal construction and combination.** What do serious systematic shops
     use beyond cross-sectional percentile ranking? Orthogonalisation against
     known factors, signal decay/half-life estimation, information-coefficient
     analysis, ensemble methods, non-linear combination — and where each is
     genuinely superior versus where it is complexity theatre.
   - **Portfolio construction.** Mean-variance is fragile; what actually
     survives contact with reality? Hierarchical Risk Parity, Black-Litterman,
     robust/shrinkage covariance (Ledoit-Wolf, nonlinear shrinkage),
     Nested Clustered Optimisation, risk budgeting, transaction-cost-aware
     optimisation with turnover penalties.
   - **Risk modelling.** Beyond historical/parametric VaR: EVT for tails,
     copulas for dependence structure, GARCH-family volatility forecasting,
     realised-volatility estimators from OHLC data (Garman-Klass, Yang-Zhang),
     regime-switching (Markov / HMM), conditional / DCC correlation, expected
     shortfall backtesting (Acerbi-Szekely).
   - **Backtesting rigour.** Combinatorially Purged Cross-Validation, purging
     and embargo, Probability of Backtest Overfitting, the Deflated Sharpe
     framework (I have the basic version), walk-forward with proper leakage
     control, meta-labelling and triple-barrier labelling.
   - **Execution and microstructure.** Realistic cost models: square-root
     market impact, Almgren-Chriss, implementation shortfall, participation
     limits — and how much of this matters for a retail-size Indian book.
   - **Derivatives.** Beyond Black-76: volatility surface construction and
     arbitrage-free fitting (SVI/SSVI), local vs stochastic volatility, greeks
     under a skew, variance risk premium as a tradeable signal, and what is
     realistically extractable from EOD settlement data alone.
   - **Modern approaches worth taking seriously**: Bayesian hierarchical models
     for cross-sectional signals, Gaussian processes, gradient boosting on
     tabular financial data with proper CV, causal inference for factor claims,
     conformal prediction for calibrated intervals, and where machine learning
     is actively harmful at this data scale.

   For each: name the technique, the canonical reference, what it would concretely
   improve HERE given my data constraints (EOD only, free sources, ~50-500 names,
   ~10 years, no point-in-time fundamentals), and an honest verdict on whether a
   single-user system should implement it or deliberately skip it. Be explicit
   about which of these are genuinely used by good practitioners versus which are
   academically fashionable but operationally useless.

Rank your recommendations by expected impact on decision quality per unit of
implementation effort. If something I have built is over-engineered relative to
its value, say so and say what to delete.

Where you recommend a technique, give enough specificity that I could implement
it: the estimator, the pitfalls, and the diagnostic that tells me whether it is
working. Assume I will actually build what you recommend, so do not recommend
anything you would not stake your own money on.
