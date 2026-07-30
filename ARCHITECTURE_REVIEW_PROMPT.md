# EquiSense — full architecture review request

Everything below the line is a self-contained brief. Paste it into another AI.
No repo access is needed.

---

You are reviewing the complete architecture of a working financial analysis and
decision-support platform. I want blunt, specific, technically-grounded
criticism — not encouragement. Assume I can handle "this is wrong, here is why."
Where you disagree with a design choice, say so directly and give the better
alternative.

# 1. What this is

**EquiSense** — a single-user equity research and decision-support platform for
the Indian market (NSE). It is a private tool for my own investing, not a
product and not a demo. I am about to start trading real money at small size.
Correctness dominates everything: a confident wrong number is worse than no
number.

**Stack.** Python monolith. FastAPI + SQLAlchemy 2.0 backend, vanilla-JS SPA
frontend (no framework), Postgres on Neon free tier (~0.5 GB), deployed on
Vercel serverless. Local dev runs the identical code against SQLite.

**Size.** ~15,700 lines application code, ~3,700 lines tests, 268 tests passing.

```
equisense/engine      4,903 LOC    pure computation, no I/O
equisense/api         3,605 LOC    FastAPI, 55 routes, orchestration only
equisense/research    1,752 LOC    measurement substrate
equisense/ingestion   1,437 LOC    data acquisition
equisense/ai            264 LOC    grounding validator + narration
equisense/seed          316 LOC    labelled demo data (local only)
web                   2,489 LOC    SPA
tests                 3,539 LOC    268 tests
```

**Dependencies, deliberately minimal:**
`fastapi>=0.110, sqlalchemy>=2.0, pydantic>=2.6, anthropic>=0.90,
yfinance>=0.2.40, pandas>=2.0, numpy>=1.26, psycopg[binary]>=3.1`

**No scipy, no statsmodels, no sklearn.** The deploy target is a size-capped
serverless bundle, so every statistical routine is implemented on stdlib +
numpy: exact Student-t via regularised incomplete beta (Lentz continued
fraction), normal CDF/inverse via `statistics.NormalDist`, ANOVA ICC, cluster
bootstrap, Cholesky via numpy.

# 2. Governing design commitments

Load-bearing. Challenge them if you think they are wrong, but they are
deliberate, not accidental.

1. **No price prediction.** No price targets, no buy/sell signals. Valuation is
   answered by a *reverse* DCF — "what FCF growth is the market currently
   pricing in?" — which is a computation about the present, not a forecast.
2. **Abstention is a first-class verdict** and is expected to be modal. Verdicts
   are: `long_candidate`, `avoid_short_candidate`, `abstain_no_edge`,
   `abstain_insufficient`, `abstain_disagreement`.
3. **The LLM never originates a number.** Every figure in AI narration is
   computed deterministically first and passed as structured context. A
   grounding validator extracts every numeric token from model output and
   verifies it exists in that context within rounding tolerance; violations
   trigger a corrective retry and are surfaced in the UI, never hidden.
4. **Explainability is structural.** The engine's unit of output is a `Metric`:
   `{key, label, value, unit, formula-with-numbers-substituted, inputs, period,
   family, caveat}`. "Show the work" renders what the engine already returned.
   Engines never return bare floats to the API layer.
5. **Influence is earned.** Every evidence family maps to a pre-registered
   hypothesis whose lifecycle status sets an *admission weight*. That weight
   scales the family's contribution AND caps the conviction band it can support.
6. **Every decision is pre-registered** in a hash-chained ledger before the
   outcome is known — direction, stated probability, predicted excess magnitude
   — then Brier-scored when the horizon expires.
7. **Data is derived or exchange-published, never hardcoded.** Universe,
   risk-free rate, sector classification all come from published files.

# 3. Full module map

## engine/ — pure functions, no I/O, fully unit-tested

| Module | Contents |
|---|---|
| `types.py` | `Metric`, `StatementData` value objects; `safe_div`, `fmt` |
| `ratios.py` | ~25 ratios in 5 families; 3-way DuPont; ROIC. Average balance-sheet denominators when prior period available; effective tax rate bounded to [0, 0.60] with statutory 25.17% fallback |
| `quality.py` | Piotroski F-Score (availability-scaled, refuses to tier below 6 computable signals); Altman Z (1968); Altman **Z''-EM** (1995 emerging-market variant: no Sales/TA term, book equity, so it is price-invariant) |
| `valuation.py` | CAPM WACC; reverse DCF with Gordon-region guard; normalized 3-year base FCF; log-linear OLS growth with R²; **Vasicek-shrunk beta** estimated from weekly returns |
| `banking.py` | Financial-sector model: NIM, yield on assets, cost of funds, interest spread, cost/income, ROA, ROE, leverage, two-factor DuPont (ROA × leverage) |
| `technical.py` | 12-1 momentum, 52w-high distance, 200DMA + slope, realised vol, vol contraction, relative strength, volume anomaly, ADV, max drawdown |
| `novel.py` | Original composites: Momentum Quality Index, Cash Conviction Score, Fragility Index, P/E percentile vs own history, Trend-Value Tension quadrants, Participation Heat (delivery-aware), institutional flow |
| `derivatives.py` | Black-Scholes-Merton + Black-76 with full greeks; implied vol (Newton + bisection, with an identifiability gate); futures basis → implied financing rate; term structure; option-chain analytics (IV surface, 25Δ risk reversal, PCR, OI walls, max pain); multi-leg payoff; scenario margin estimate; SEBI F&O loss base rate |
| `montecarlo.py` | Portfolio VaR/CVaR under Gaussian / standardised Student-t / stationary block bootstrap; path-dependent drawdown touch probability; implied-growth distribution; SIP goal probability. Cholesky correlation, antithetic variates, nearest-PSD repair, MC standard errors |
| `crossasset.py` | Pearson correlation with Fisher-z CI; **stress-conditional correlation**; FDR-controlled lead-lag scan; driver betas; date-intersection alignment |
| `rates.py` | Risk-free rate **derived** from futures basis + index dividend yield; earnings-yield ERP sanity check |
| `regime.py` | Macro regime: NIFTY trend × VIX percentile, plus INR/crude flags |
| `evidence.py` | `Evidence` objects; cross-sectional percentile normalisation; admission control (the single enforcement point) |
| `synthesis.py` | Cluster aggregation → verdict. **The decision function.** |
| `sizing.py` | Vol-based risk-per-trade, conviction scaling, provisional haircut, position/heat/liquidity caps; India cost & tax physics |
| `portfolio.py` | FIFO lot matching, XIRR (dividend-aware), 4-axis concentration, tax-lot aging |
| `personalization.py` | Investor profile → ranking function and card ordering |

## research/ — the measurement substrate

| Module | Contents |
|---|---|
| `stats.py` | ANOVA ICC, Kish design effect, effective sample size, Liang-Zeger cluster-robust mean, exact Student-t (incomplete beta), Newey-West HAC, Benjamini-Hochberg FDR, Harvey-Liu-Zhu hurdle, Deflated Sharpe (Bailey/López de Prado), cluster bootstrap |
| `registry.py` | Pre-registered hypotheses, lifecycle status → admission caps |
| `base_rates.py` | Leakage-controlled cross-sectional event studies; feature builders; run-wide FDR |
| `backtest.py` | **Jegadeesh-Titman overlapping-tranche** backtest; vol-managed overlay (Barroso & Santa-Clara) |
| `learning.py` | Beta-Binomial cluster posteriors; gated probability + magnitude calibration from scored claims |
| `reg001.py` | A study asking whether the regime engine earns its own existence |

## ingestion/

| Module | Contents |
|---|---|
| `yahoo.py` | Prices (**both** nominal and total-return), dividends, annual statements, macro |
| `nse_archive.py` | NSE official archives: F&O bhavcopy, cash bhavcopy, MTO delivery, index closes, index constituents, bulk/block deals; health check; retention/pruning |
| `universe.py` | Universe resolved from NSE published constituents; NSE industry → internal sector map |
| `vault.py` | Content-addressed archive of raw provider payloads, with retention |

## api/ — orchestration only, all math in engine/

`app.py` (routes + auth gate), `services.py` (company/portfolio assembly),
`live.py` (dossier assembly), `snapshot.py` (one heavy pass → cached universe),
`candidates.py` (universe-wide screening + gates), `markets.py` (multi-asset),
`paper.py`, `autopilot.py`, `status.py`.

# 4. Database schema — 19 tables

```
companies (13)          id, ticker, name, sector, industry, exchange, cap_band,
                        peer_group, description, is_demo_data, is_financial,
                        is_index_member, last_seen_in_index

filing_periods (35)     company_id, period, fiscal_year, scope, filing_date,
                        restatement_version, is_latest, source, pit_grade,
                        revenue, gross_profit, ebitda, depreciation, ebit,
                        interest_expense, interest_income, net_interest_income,
                        pbt, tax_expense, net_income, total_assets,
                        current_assets, cash, inventory, receivables,
                        current_liabilities, payables, total_debt, total_equity,
                        retained_earnings, shares_outstanding, cfo, capex,
                        dividends_paid

price_observations (7)  company_id, obs_date, close (TOTAL-RETURN),
                        close_raw (NOMINAL), volume, dividend

macro_observations (5)  symbol, role, obs_date, close
index_observations (12) index_name, obs_date, OHLC, volume, turnover_cr,
                        pe, pb, div_yield
derivative_quotes (17)  trade_date, symbol, instrument_type, expiry, strike,
                        option_type, OHLC, settlement_price, underlying_price,
                        open_interest, change_in_oi, volume, lot_size
delivery_stats (7)      trade_date, symbol, series, traded_qty, delivered_qty,
                        delivery_pct

base_rates (31)         study_key, evidence_family, registry_ref, horizon_days,
                        regime_filter, n, n_eff, n_clusters, icc, design_effect,
                        cohort_breadth_pct, hit_rate, mean/median_excess_pct,
                        net_median_excess_pct, median_ci95_lo/hi_pct,
                        q25/q75_excess_pct, mean_se_pct, t_stat, df, p_value,
                        q_value, admissible, admissibility_reason,
                        multiplicity_verdict, survives_multiplicity, spec

ledger_records (4)      seq, kind, hash, payload          [hash-chained]
transactions (7)        company_id, side, quantity, price, trade_date, fees
paper_trades (8)        + dossier_hash
theses (11)             statement, assumptions, invalidation_triggers,
                        sizing_rationale, review_date, status, elaboration
journal_entries (6)     watchlist_items (4)    investor_profiles (16)
sector_attributes (6)   app_snapshots (4)
vault_blobs (2)         vault_fetches (7)
```

**Design notes.** Ratios are NEVER stored — always computed on read.
Portfolio state is derived from a transaction ledger, never a mutable holdings
row. Filings are versioned by filing date + restatement version and keep
standalone vs consolidated as separate scopes.

# 5. Data sources — all free, keyless, zero user setup

**Yahoo Finance (yfinance)** — 10y daily prices for the universe (fetched with
`auto_adjust=False, actions=True` so nominal close, total-return close, volume
and dividends all arrive in one batch request), annual statements, macro series
(`^NSEI, ^INDIAVIX, INR=X, BZ=F, GC=F, ^GSPC`). Unofficial endpoint.
Fundamentals are RESTATED, not point-in-time — everything derived from them is
flagged `pit_grade: reconstructed`.

**NSE official public archives** (`nsearchives.nseindia.com`) — published
archive FILES, one request per file per day:
- F&O bhavcopy: ~35,000 rows/day — every strike, both option types, settlement
  price, open interest, change in OI, underlying price, lot size
- Cash bhavcopy: ~3,000 instruments including ETFs and Sovereign Gold Bonds
- MTO file: delivery percentage per security
- Index close file: ~140 indices **with the exchange's own P/E, P/B and
  dividend yield**
- Index constituent lists (nifty50 … nifty500) with industry and ISIN
- Bulk/block deals: large trades with the counterparty NAMED

**Storage strategy under a 0.5 GB tier.** The deciding question is whether a
source returns HISTORY in one request:
- Yahoo `period="10y"` returns a decade for 50 tickers in one call → cacheable.
- NSE archives publish one file per DAY → a 750-day percentile history would be
  750 requests → must be accumulated.
- Option chains are fetched **live and never stored** (nothing studies
  historical OI or past IV surfaces; storing them was 23 MB of 58 MB).
- Delivery % and index valuation ARE accumulated — their value is the
  percentile-vs-own-history.
- Retention: derivatives 45d, delivery 400d, index 4000d, vault rolling window.
  Current usage 27 MB.

# 6. The decision pipeline, end to end

```
prices + statements + macro + NSE archives
   |
engines emit Metric objects (value + formula + inputs + caveats)
   |
Evidence objects:
   cluster ∈ {trend, value, quality, flow, macro, risk, portfolio}
   strength = cross-sectional PERCENTILE rank within the universe → [-1, +1]
   admission_weight = f(hypothesis lifecycle status)
   tier = T1 (context) or T2 (carries an admissible base rate)
   |
synthesis:
   within cluster : reliability-weighted mean of full-range strengths
   across clusters: weighted by cluster reliability × learned weight
   net_z = net_score / null_sd
   |
   where null_sd is the CLOSED-FORM sd of net_score for an uninformative name
   given that exact coverage. Under percentile normalisation an uninformative
   name's strengths are U(-1,1), so with C clusters holding m_c evidence each:
   
        Var(net) = (1 / 3C²) · Σ_c (1 / m_c)
   
   Verified against Monte-Carlo: C=5,m=3 → closed form 0.149 vs simulated
   0.149; C=3,m=1 → 0.333 vs 0.333.
   |
   |net_z| < 2.0                → abstain_no_edge
   fewer than 3 clusters        → abstain_insufficient
   cluster dispersion > 0.55    → abstain_disagreement
   otherwise                    → long_candidate / avoid_short_candidate
   conviction band = f(|net_z|, confidence) CAPPED by hypothesis maturity
   |
dossier → sizing (vol-based stop, position cap, heat cap, liquidity cap,
                  India round-trip costs, STCG/LTCG)
   |
pre-registered in the hash-chained ledger with direction, stated probability
and predicted excess magnitude → Brier-scored at horizon expiry
   |
scored outcomes → Beta-Binomial cluster posteriors → synthesis weights
                  (unlock only at ≥150 scored alignments per cluster)
```

# 7. Every decision constant

```
SYNTHESIS
  ABSTAIN_Z            2.0     |net_z| below this → abstain
  MIN_CLUSTERS         3       coverage floor
  MAX_DISPERSION       0.55    cluster stdev above this → abstain_disagreement
  CONVICTION_CEILING   reliability ≥1.00 → "high"; ≥0.60 → "moderate";
                       else → "low"
  CLUSTERS             trend, value, quality, flow, macro, risk, portfolio

ADMISSION CAPS (by hypothesis lifecycle status)
  registered 0.25 | computed 0.25 | registered-deferred 0.0 (SHADOW)
  shadow 0.0 | validated 0.60 | deployed 1.00 | weak 0.10
  rejected 0.0 | retired 0.0 | DEFAULT_UNVALIDATED 0.25

RESEARCH
  SAMPLING_DAYS               21      monthly sampling
  MIN_N_EFF                   30      admissibility power gate
  MIN_CLUSTERS                8       below this, cluster inference unreliable
  DEFAULT_ROUND_TRIP_COST_PCT 0.35
  BROAD_COHORT_PCT            40.0    above this → cross-sectionally undistinctive
  FDR_ALPHA                   0.05
  HLZ_T_HURDLE                3.0     Harvey-Liu-Zhu new-factor bar

SIZING (India, FY2026)
  RISK_PER_TRADE       0.0075   0.75% of book at the stop
  MAX_PORTFOLIO_HEAT   0.06     sum of open R ≤ 6%
  STOP_ATR_MULT        2.5      stop = 2.5 × daily vol
  ADV_PARTICIPATION    0.05     exit ≤5% of ADV/day
  provisional haircut  0.5      permanent while weights are uncalibrated
  STT 0.1%/side | stamp 0.015% buy | exchange 0.00297%/side | SEBI 0.0001%/side
  ROUND_TRIP_STATUTORY 0.221%
  STCG 20% (<12m) | LTCG 12.5% (>12m)

LEARNING GATES
  UNLOCK_N   150   scored alignments per cluster before weights unlock
  CAL_MIN     30   scored claims before probability calibration engages
  MAG_MIN     30   before magnitude calibration engages
  ALIGN_THRESHOLD 0.1

VALUATION
  TERMINAL_SPREAD_FLOOR 0.01    WACC must exceed terminal growth by ≥100bps
  NORMALIZATION_YEARS   3       base FCF averaging window
  GROWTH_FLOOR         -0.50    below this → refuse rather than pin
  BETA_PRIOR 1.0 | BETA_PRIOR_SD 0.30 | MAX_COST_OF_DEBT 0.25

MONTE CARLO
  DEFAULT_PATHS 20,000 | TRADING_DAYS 252 | stress quantile 0.20
CROSS-ASSET
  MIN_OBS 60 | STRESS_QUANTILE 0.20
```

# 8. Hypothesis registry (pre-registration, in version control)

```
HYP-001 momentum_12_1_top_quintile            technical.trend           registered
HYP-002 near_52w_high                         technical.trend           registered
HYP-003 above_200dma                          technical.trend           registered
HYP-004 momentum_quality_top_quintile         novel.mqi                 registered
HYP-005 cash_conviction_score                 novel.ccs        registered-deferred
HYP-006 fragility_index                       novel.fragility  registered-deferred
HYP-007 participation_heat_top_decile         novel.crowding            registered
HYP-008 low_vol_quintile                      technical.vol             registered
HYP-009 vol_managed_momentum_overlay          meta.risk_management      registered
HYP-010 sector_relative_momentum_top_quintile technical.sector_momentum registered
HYP-011 low_max_effect_top_quintile           behavioral.max_effect     registered
HYP-012 delivery_percentile_accumulation      novel.delivery   registered-deferred
HYP-013 net_institutional_flow_direction      novel.institutional_flow
                                                               registered-deferred
HYP-014 bank_roa_quality                      banking.profitability
                                                               registered-deferred
REG-001 regime_conditioning_value             meta.regime               registered
```

Literature anchors: George & Hwang (2004) 52-week high; Moskowitz & Grinblatt
(1999) industry momentum; Bali, Cakici & Whitelaw (2011) MAX/lottery demand;
Barroso & Santa-Clara (2015) and Daniel & Moskowitz (2016) momentum crashes;
Sloan (1996) accruals; Piotroski (2000); Altman (1968, 1995).

Failed hypotheses stay in the registry permanently with `status="rejected"` —
deleting one would falsify the record. REG-001 is the regime engine being made
to justify itself: an earlier out-of-sample run showed regime conditioning added
**no measurable calibration value** (ΔBrier −0.0006).

# 9. Statistical machinery — the part I most want reviewed

Cross-sectional event studies here produce **doubly dependent** observations:
names selected on the same date share a market-wide shock, and h-day forward
windows sampled every 21 days overlap for ⌈h/21⌉ consecutive dates.

**Effective sample size.** Kish (1965) design effect with an intraclass
correlation **estimated from the data** by one-way random-effects ANOVA,
clustering on date blocks spanning the overlap horizon:

```
ICC = (MSB − MSW) / (MSB + (m₀ − 1)·MSW)      [m₀ = Kish, unbalanced]
deff = 1 + (m̄ − 1)·ICC
N_eff = N / deff
```

This replaced `N_eff = N × 21/h`, which corrected only serial overlap and
ignored same-date commonality, overstating independent information ~10×
(N=1080 → old estimate 360, honest 37).

**Significance.** Liang-Zeger (1986) cluster-robust SE on G−1 degrees of
freedom, exact Student-t p-value:

```
Var(x̄) = [G/(G−1)] · Σ_g (Σ_i (x_gi − x̄))² / N²
```

The sanity check that motivated this: a series with **true mean zero** and
strong within-date correlation shows t=2.53 under naive iid inference — and
t=0.47, p=0.64 cluster-robust. The old machinery would have published it.

**Multiplicity.** Benjamini-Hochberg FDR at 5% across every primary study cell
in a run (regime sub-cells inherit the parent verdict rather than being counted
separately), plus the Harvey, Liu & Zhu (2016) |t| ≥ 3.0 hurdle for a newly
proposed factor.

**Backtest selection bias.** Deflated Sharpe Ratio (Bailey & López de Prado,
2014) discounting the observed Sharpe for the number of rules tried and for the
return series' own skew and kurtosis.

**Confidence intervals.** Cluster bootstrap resampling whole date cohorts — a
moving-block bootstrap over a flattened pick list keeps blocks *inside* one date
cohort and preserves no cross-sectional dependence, producing intervals ~3×
too narrow.

**Publication policy.** Records are ALWAYS written with an `admissible` flag and
a reason, never suppressed. A study that failed its power gate is still a
measurement, and hiding it would let absence be mistaken for "not studied".

# 10. Current live results (10 years, real NIFTY-50 data)

- **45 study cells: 28 admissible, 3 survive multiplicity control.**
  `above_200dma` shows t=4.82, q=0.003 — passing both FDR and HLZ — yet is
  correctly marked INADMISSIBLE because it selects 63% of the universe
  (cross-sectionally undistinctive by construction).
- `near_52w_high_126d` (t=3.16, q=0.023) and
  `sector_relative_momentum_126d` (t=3.05, q=0.023) survive everything.
- `momentum_12_1_126d` t=2.62, q=0.037 — passes FDR, fails the HLZ hurdle.
- Median excess returns are tiny (0.05% to 1.15%), mostly below the 0.35% cost
  model.
- **Backtest:** 276.7% total vs NIFTY 143.7% over ~9 years, 18.0% annualised,
  −22.2% max drawdown, 21.3%/month turnover, naive Sharpe 0.95 — but
  **Deflated Sharpe 0.88 < 0.95**: not distinguishable from the best of 8 lucky
  trials.
- **Portfolio VaR99 (21d):** Gaussian −6.69%, Student-t −6.67%, bootstrap of
  real history **−8.97%**. Drawdown touch probability over 1 year: −10% at 51%,
  −20% at 5.3%.
- **Market-implied risk-free 6.09%** (far NIFTY contract implies 4.886% carry +
  1.20% index dividend yield). ERP sanity check = **−1.26pp**: equities yield
  less than cash on trailing earnings.
- **Index valuation:** Nifty 50 P/E 20.7, Midcap 150 at 30.2, Smallcap 250 at
  34.0 → 1.64× small-cap premium.
- **Cross-asset stress-conditional correlation vs NIFTY:** gold +0.128 → **+0.203**
  in the worst 20% of market days (its correlation RISES in stress); TCS
  +0.448 → +0.152 (best in-stress diversifier); crude −0.097 → +0.045 (flips
  positive exactly when a hedge would matter).
- **Banking:** ICICI ROA 1.95% × 8.2× leverage = ROE 16.0%; SBI ROA 1.07% ×
  14.4× = ROE 15.4%. Near-identical ROE, entirely different businesses.
- **Institutional flow:** IIFL printed ₹1,486cr gross in one day for a net of
  −₹9.8cr (net/gross −0.007) — funds crossing stock with each other. Only 10 of
  33 symbols carried any directional claim.
- **Most dossiers abstain.**

# 11. Defects found and fixed (context for how battle-tested this is)

Every one was empirically reproduced against running code before being changed;
each has a regression test. Test count went 121 → 268.

**Statistical**
- N_eff ignored cross-sectional clustering → ~10× overstatement.
- Inference was naive iid → a true-zero-mean series read t=2.53.
- Bootstrap blocks sat inside one date cohort → CIs ~3× too narrow.
- **No multiple-testing control existed at all** across ~45 cells.

**Decision logic**
- Admission caps CLIPPED strength to 0.25, so with every hypothesis at
  "registered" both `conviction=="high"` (needed |net| ≥ 0.45) and
  `abstain_disagreement` (needed dispersion > 0.55, max reachable 0.25) were
  **unreachable dead branches**. Caps became a reliability weight plus a
  conviction ceiling; thresholds re-derived from the closed-form null.

**Financial**
- Effective tax rate unbounded → a refund year produced NOPAT > EBIT and ROIC
  of 35.5%.
- ROE/ROA/ROIC/turnover used period-end, not average, balances.
- Piotroski counted MISSING signals as FAILED → the same improving company
  scored 9/9 with full data and 6/9 when sparse, was tiered down, and that tier
  fed the portfolio's "fundamentally fragile" concentration axis.
- Reverse DCF had no WACC ≤ terminal-growth guard → reported "implied growth
  −17.9%" with no caveat, in a region where the Gordon model is undefined.
- Base FCF was a single noisy year → normalising moved implied growth 10.9pp.
- Growth anchor was endpoint CAGR → 4.6% vs 16.4% on an unchanged 10% trend.
- MQI's persistence term used raw up-day fraction, so a SMOOTH decline scored
  as weaker evidence than a choppy one — inverted. Present in all three copies
  (engine, study feature builder, backtest).
- Grounding validator's blanket ×100 expansion let fabricated "1352" validate
  because 13.52 was in context.
- `compute_wacc` spread its assumptions dict last, overwriting the DERIVED cost
  of debt with None — so the one input most likely to be estimated was the one
  never shown.
- Prices stored only the total-return series; dividing it by nominal filing EPS
  deflated historical P/E and made the valuation percentile read systematically
  "expensive", putting a standing bearish tilt on the value cluster.
- XIRR omitted dividends entirely.
- Backtest charged a full round trip every month for a 63-day hold (~3× cost)
  and faked the equity curve with `(1+r)^(21/h)`.

**Infrastructure**
- NSE's edge silently STALLS on a long descriptive User-Agent — every archive
  fetch returned empty with no error, indistinguishable from a quiet market.
- `BOOLEAN DEFAULT 0` is valid SQLite and rejected by Postgres, aborting schema
  migration on every hosted cold start.
- Departed index constituents were never deactivated — 6 stale names (one with
  no price data at all) sat in the cross-section shifting every percentile.
- Index derivatives were filtered out by an equity-ticker filter: 24k rows
  ingested, NIFTY chain still reported "no data".
- Cross-asset alignment was positional, not by date — different holiday
  calendars meant pairing Monday's rupee move with Tuesday's equity move;
  RELIANCE~NIFTY read +0.05 instead of +0.69.
- Implied vol returned the initial guess 0.25 AS a solved IV for deep-ITM
  options where price is flat in sigma.
- Two tests passed only by accident of ordering.

# 12. Known limitations

Do not spend the response re-telling me these. I want what I have **missed**.

- Fundamentals are restated, not point-in-time. Every fundamental hypothesis is
  therefore *deferred* rather than tested, because testing on restated figures
  is look-ahead.
- Universe is current index constituents backfilled — survivorship-tilted.
  Caveated on every base-rate record.
- Bank asset quality (GNPA/NNPA/PCR), capital adequacy (CAR/CET1) and funding
  mix (CASA) are unavailable from free sources. The banking engine therefore
  REFUSES to emit a quality composite rather than emit a misleading one.
- Delivery % and institutional flow are measured but unvalidated — the archives
  publish one file per day and history cannot be backfilled.
- No intraday data. No options data beyond EOD settlement prices.
- Single user, single process. No queue, no cache layer beyond snapshots.
- ~50 names in the active universe (expandable to 500 via NSE constituent
  lists), ~10 years of daily history.

# 13. What I want from you

Be concrete, prioritised and willing to say "delete this". For each point: what
is wrong or missing, why it matters for DECISION QUALITY, and specifically what
to do.

**1. Statistical soundness.** Is the inference actually correct?
   (a) Is an ANOVA-estimated ICC on date blocks the right dependence model, or
   should this be Driscoll-Kraay, or Fama-MacBeth with Newey-West?
   (b) Is BH-FDR across heterogeneous hypothesis families defensible, or should
   families be controlled separately?
   (c) The closed-form null for `net_z` assumes independence ACROSS clusters,
   which is optimistic since signals correlate. How wrong is that, and what is
   the right correction — empirical cross-sectional dispersion, an eigenvalue
   correction, something else?
   (d) Is the design-effect approach adequate when both dependence dimensions
   are present simultaneously?

**2. The synthesis function.** Aggregating percentile strengths into a
   reliability-weighted mean and thresholding a standardised score — is that
   defensible, or is there a materially better formulation? A Bayesian posterior
   over expected excess return? A proper scoring rule? Stacking? What breaks
   first as signal count grows?

**3. What is structurally missing** from an Indian-market decision system built
   on free EOD data. Which single absent input would most change decision
   quality?

**4. Free, keyless, machine-readable data sources I have not named** — Indian or
   global. Especially: point-in-time fundamentals, bank asset quality,
   historical index constituents, an Indian yield curve, corporate actions,
   promoter pledging, shareholding patterns.

**5. Model additions worth the complexity** — and which are cargo-cult at
   single-user scale.

**6. Failure modes.** How would this system most plausibly mislead someone into
   losing money? Where is it most likely to be confidently wrong? Attack the
   design.

**7. Architecture and scale.** Anything structurally wrong with a pure-Python
   monolith on serverless + Postgres for this workload? What breaks first as the
   universe goes 50 → 500 names?

**8. State-of-the-art quantitative finance — what does the top of the field
   actually do that I am not doing?**

   Go deep and current. I want the real frontier, not a textbook summary.

   - **Signal construction and combination.** Beyond cross-sectional percentile
     ranking: orthogonalisation against known factors, signal decay/half-life
     estimation, information-coefficient analysis, ensembling, non-linear
     combination. Where each is genuinely superior versus complexity theatre.
   - **Portfolio construction.** Mean-variance is fragile; what survives
     contact with reality? Hierarchical Risk Parity, Black-Litterman,
     shrinkage covariance (Ledoit-Wolf, nonlinear shrinkage), Nested Clustered
     Optimisation, risk budgeting, turnover-penalised optimisation.
   - **Risk modelling.** Beyond historical/parametric VaR: EVT for tails,
     copulas for dependence, GARCH-family forecasting, OHLC realised-volatility
     estimators (Garman-Klass, Yang-Zhang, Rogers-Satchell), regime switching
     (Markov/HMM), DCC conditional correlation, expected-shortfall backtesting
     (Acerbi-Székely).
   - **Backtesting rigour.** Combinatorially Purged Cross-Validation, purging
     and embargo, Probability of Backtest Overfitting, walk-forward with proper
     leakage control, triple-barrier labelling, meta-labelling.
   - **Execution and microstructure.** Square-root market impact,
     Almgren-Chriss, implementation shortfall, participation limits — and how
     much of this matters for a retail-size Indian book.
   - **Derivatives.** Beyond Black-76: arbitrage-free volatility surface fitting
     (SVI/SSVI), local vs stochastic volatility, greeks under skew, the variance
     risk premium as a tradeable signal, and what is realistically extractable
     from EOD settlement data alone.
   - **Modern approaches worth taking seriously**: Bayesian hierarchical models
     for cross-sectional signals, Gaussian processes, gradient boosting on
     tabular financial data with proper CV, causal inference for factor claims,
     conformal prediction for calibrated intervals — and where ML is actively
     harmful at this data scale.

   For each: name the technique, the canonical reference, what it would
   concretely improve HERE given my constraints (EOD only, free sources, 50–500
   names, ~10 years, no point-in-time fundamentals), and an honest verdict on
   whether a single-user system should implement it or deliberately skip it. Be
   explicit about which are genuinely used by good practitioners versus which
   are academically fashionable but operationally useless.

---

Rank everything by expected impact on decision quality per unit of
implementation effort. If something I have built is over-engineered relative to
its value, say so and say what to delete.

Where you recommend a technique, give enough specificity that I could implement
it: the estimator, the pitfalls, and the diagnostic that tells me whether it is
working. Assume I will actually build what you recommend — so do not recommend
anything you would not stake your own money on.
