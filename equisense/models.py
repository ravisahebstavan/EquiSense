"""ORM entities (PROJECT_DRAFT §15.2).

Design decisions carried through from the draft:
- FilingPeriod is versioned by filing date and restatement version (§14.4) and
  keeps standalone vs consolidated as first-class scopes (§10.1).
- Ratios are NEVER stored — always computed on read from filings (§15.3).
- Portfolio state is a transaction ledger, not mutable holdings (§11.1).
- Thesis is a structured object with falsifiable assumptions and invalidation
  triggers, not a prose blob (§23.1).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Integer,
                        LargeBinary, String, Text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    sector: Mapped[str] = mapped_column(String(60))
    industry: Mapped[str] = mapped_column(String(80), default="")
    exchange: Mapped[str] = mapped_column(String(10), default="NSE")  # §16.4: market id from day one
    cap_band: Mapped[str] = mapped_column(String(10), default="large")  # large | mid | small
    peer_group: Mapped[str] = mapped_column(String(60), default="")     # manually curated (§10.8)
    description: Mapped[str] = mapped_column(Text, default="")
    is_demo_data: Mapped[bool] = mapped_column(Boolean, default=False)
    is_financial: Mapped[bool] = mapped_column(Boolean, default=False)  # banks/NBFC: statement engines skip
    # Index membership, refreshed from NSE's published constituent list on every
    # universe sync. A company that LEAVES the index is deactivated, never
    # deleted: its price history stays (deleting it would manufacture exactly the
    # survivorship bias the research plane warns about on every base-rate record)
    # but it drops out of the live analytical universe, so it no longer
    # contaminates cross-sectional percentile ranking or new study cohorts.
    is_index_member: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_in_index: Mapped[date | None] = mapped_column(Date, nullable=True)

    filings: Mapped[list["FilingPeriod"]] = relationship(back_populates="company")


class FilingPeriod(Base):
    """One fiscal period × scope × restatement version (§14.4, §10.1).
    Canonical line items as typed columns (₹ crore; shares in crore)."""
    __tablename__ = "filing_periods"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    period: Mapped[str] = mapped_column(String(10))        # "FY2025"
    fiscal_year: Mapped[int] = mapped_column(Integer)
    scope: Mapped[str] = mapped_column(String(15), default="consolidated")
    filing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    restatement_version: Mapped[int] = mapped_column(Integer, default=1)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")     # manual | demo | yahoo
    pit_grade: Mapped[str] = mapped_column(String(20), default="reconstructed")  # archived | reconstructed (§6.1)

    revenue: Mapped[float | None] = mapped_column(Float)
    gross_profit: Mapped[float | None] = mapped_column(Float)
    ebitda: Mapped[float | None] = mapped_column(Float)
    depreciation: Mapped[float | None] = mapped_column(Float)
    ebit: Mapped[float | None] = mapped_column(Float)
    interest_expense: Mapped[float | None] = mapped_column(Float)
    # Banking-specific: a bank's revenue engine is the interest spread, which the
    # industrial schema has nowhere to put. Null for non-financials.
    interest_income: Mapped[float | None] = mapped_column(Float)
    net_interest_income: Mapped[float | None] = mapped_column(Float)
    pbt: Mapped[float | None] = mapped_column(Float)
    tax_expense: Mapped[float | None] = mapped_column(Float)
    net_income: Mapped[float | None] = mapped_column(Float)
    total_assets: Mapped[float | None] = mapped_column(Float)
    current_assets: Mapped[float | None] = mapped_column(Float)
    cash: Mapped[float | None] = mapped_column(Float)
    inventory: Mapped[float | None] = mapped_column(Float)
    receivables: Mapped[float | None] = mapped_column(Float)
    current_liabilities: Mapped[float | None] = mapped_column(Float)
    payables: Mapped[float | None] = mapped_column(Float)
    total_debt: Mapped[float | None] = mapped_column(Float)
    total_equity: Mapped[float | None] = mapped_column(Float)
    retained_earnings: Mapped[float | None] = mapped_column(Float)
    shares_outstanding: Mapped[float | None] = mapped_column(Float)
    cfo: Mapped[float | None] = mapped_column(Float)
    capex: Mapped[float | None] = mapped_column(Float)
    dividends_paid: Mapped[float | None] = mapped_column(Float)

    company: Mapped[Company] = relationship(back_populates="filings")


class PriceObservation(Base):
    __tablename__ = "price_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    obs_date: Mapped[date] = mapped_column(Date, index=True)
    # TOTAL-RETURN series: adjusted for splits AND dividends. Correct for every
    # return/momentum/volatility/correlation computation.
    close: Mapped[float] = mapped_column(Float)                    # ₹ per share
    # NOMINAL series: adjusted for splits/bonuses ONLY — the price actually
    # traded that day. Required wherever a price meets a per-share accounting
    # figure, because filing EPS/BVPS are nominal. Dividing a dividend-adjusted
    # close by a nominal EPS deflates historical P/E and makes a valuation
    # percentile read systematically "expensive". Nullable: rows ingested before
    # this split are total-return only and must not silently masquerade as
    # nominal (see PriceObservation usage notes in ingestion/yahoo.py).
    close_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Intraday range, NOMINAL scale (same convention as close_raw). Range-based
    # volatility estimators are ~6x more efficient than close-to-close for the
    # same window, and volatility feeds the stop distance and therefore the
    # position size — so estimator error here costs money directly.
    open_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)  # shares traded
    # Cash dividend per share with this EX-date (0/None on ordinary days).
    # Required for money-weighted return: a dividend is a real cash inflow, and
    # omitting it understates XIRR by roughly the yield, every year.
    dividend: Mapped[float | None] = mapped_column(Float, nullable=True)


class MacroObservation(Base):
    """Macro/reference series (NIFTY, India VIX, USDINR, Brent) — regime inputs."""
    __tablename__ = "macro_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    role: Mapped[str] = mapped_column(String(20))  # index | vix | currency | commodity
    obs_date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[float] = mapped_column(Float)


class VolSurfaceObservation(Base):
    """Daily summary of one underlying's option surface — six numbers, not the
    35,000-row chain.

    The full chain is fetched live and discarded because nothing studies
    historical open interest. But the SUMMARY is different: an implied-volatility
    time series is the only way to measure the variance risk premium (implied vol
    versus subsequently realised vol), which is among the most robustly
    documented effects in finance and, unlike stock-selection alpha, is
    harvestable by a retail trader. At ~6 floats per underlying per day the cost
    is negligible and it cannot be backfilled — NSE publishes one file per day —
    so capture has to start before the study can ever run.
    """
    __tablename__ = "vol_surface_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    obs_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    expiry: Mapped[date] = mapped_column(Date)
    days_to_expiry: Mapped[int] = mapped_column(Integer)
    underlying: Mapped[float | None] = mapped_column(Float)
    atm_iv_pct: Mapped[float | None] = mapped_column(Float)
    skew_25d_pct: Mapped[float | None] = mapped_column(Float)   # put IV − call IV
    put_call_ratio_oi: Mapped[float | None] = mapped_column(Float)
    total_oi: Mapped[float | None] = mapped_column(Float)
    iv_points_solved: Mapped[int | None] = mapped_column(Integer)


class BaseRateRecord(Base):
    """Cached T2 base-rate study result (RESEARCH_BLUEPRINT §7.1, §10.1).
    Computed from the platform's own stored price history — never asserted."""
    __tablename__ = "base_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    study_key: Mapped[str] = mapped_column(String(60), index=True)   # e.g. "momentum_12_1_top_quintile"
    evidence_family: Mapped[str] = mapped_column(String(40))
    registry_ref: Mapped[str] = mapped_column(String(60))            # hypothesis registry id
    horizon_days: Mapped[int] = mapped_column(Integer)
    regime_filter: Mapped[str] = mapped_column(String(30), default="all")
    n: Mapped[int] = mapped_column(Integer)
    n_eff: Mapped[int | None] = mapped_column(Integer)               # design-effect corrected (Wave S)
    n_clusters: Mapped[int | None] = mapped_column(Integer)          # independent date blocks
    icc: Mapped[float | None] = mapped_column(Float)                 # estimated intraclass correlation
    design_effect: Mapped[float | None] = mapped_column(Float)       # Kish variance inflation
    cohort_breadth_pct: Mapped[float | None] = mapped_column(Float)  # avg % of universe selected (A5)
    net_median_excess_pct: Mapped[float | None] = mapped_column(Float)  # after round-trip cost model
    median_ci95_lo_pct: Mapped[float | None] = mapped_column(Float)     # cluster bootstrap (Wave S)
    median_ci95_hi_pct: Mapped[float | None] = mapped_column(Float)
    # cluster-robust inference + multiple-testing control (Wave S)
    mean_se_pct: Mapped[float | None] = mapped_column(Float)         # Liang–Zeger cluster-robust SE
    t_stat: Mapped[float | None] = mapped_column(Float)
    df: Mapped[int | None] = mapped_column(Integer)                  # G−1, not N−1
    p_value: Mapped[float | None] = mapped_column(Float)             # exact Student-t
    q_value: Mapped[float | None] = mapped_column(Float)             # Benjamini–Hochberg FDR
    admissible: Mapped[bool] = mapped_column(Boolean, default=False)
    admissibility_reason: Mapped[str | None] = mapped_column(Text)
    multiplicity_verdict: Mapped[str | None] = mapped_column(Text)
    survives_multiplicity: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_rate: Mapped[float | None] = mapped_column(Float)            # P(excess return > 0)
    mean_excess_pct: Mapped[float | None] = mapped_column(Float)
    median_excess_pct: Mapped[float | None] = mapped_column(Float)
    q25_excess_pct: Mapped[float | None] = mapped_column(Float)
    q75_excess_pct: Mapped[float | None] = mapped_column(Float)
    spec: Mapped[str] = mapped_column(Text, default="")              # JSON of exact study spec
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SectorAttribute(Base):
    """Extensible attributes for sector-specific KPIs (§15.1) — e.g. ARPOB,
    bed occupancy — without a schema explosion."""
    __tablename__ = "sector_attributes"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    period: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(60))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="")


class InvestorProfileRow(Base):
    __tablename__ = "investor_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    horizon: Mapped[str] = mapped_column(String(10), default="long")
    horizon_target_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_tolerance: Mapped[str] = mapped_column(String(15), default="moderate")
    style: Mapped[float] = mapped_column(Float, default=50.0)
    dividend_preference: Mapped[float] = mapped_column(Float, default=20.0)
    quality_emphasis: Mapped[float] = mapped_column(Float, default=60.0)
    sector_preferences: Mapped[str] = mapped_column(Text, default="")   # comma-separated
    sector_exclusions: Mapped[str] = mapped_column(Text, default="")
    max_position_pct: Mapped[float] = mapped_column(Float, default=10.0)
    max_sector_pct: Mapped[float] = mapped_column(Float, default=30.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=25.0)
    preferred_lens: Mapped[str] = mapped_column(String(20), default="balanced")
    rules: Mapped[str] = mapped_column(Text, default="")                # newline-separated


class TransactionRow(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    trade_date: Mapped[date] = mapped_column(Date)
    fees: Mapped[float] = mapped_column(Float, default=0.0)


class PaperTrade(Base):
    """Paper-trading account fills (the live validation loop): executed at the
    latest EOD close, optionally linked to the dossier that motivated them —
    every fill is also pre-registered in the hash-chained ledger."""
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    side: Mapped[str] = mapped_column(String(4))          # buy | sell
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)           # fill = latest EOD close
    trade_date: Mapped[date] = mapped_column(Date)
    dossier_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Thesis(Base):
    """Structured, falsifiable thesis (§23.1) with lifecycle (§23.2)."""
    __tablename__ = "theses"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    assumptions: Mapped[str] = mapped_column(Text)            # newline-separated, falsifiable
    invalidation_triggers: Mapped[str] = mapped_column(Text)  # newline-separated
    sizing_rationale: Mapped[str] = mapped_column(Text, default="")
    review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft | active | under_review | confirmed | invalidated | closed
    elaboration: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    thesis_id: Mapped[int | None] = mapped_column(ForeignKey("theses.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    cfa_topic: Mapped[str] = mapped_column(String(80), default="")  # §22 learning-linked tag
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSnapshot(Base):
    """Precomputed view cache (serverless performance): heavy universe-wide
    computations run ONCE per data refresh and are stored as JSON, so page
    loads are a single-row fetch instead of hundreds of queries over the
    network. `as_of` ties freshness to the latest price date."""
    __tablename__ = "app_snapshots"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    as_of: Mapped[str] = mapped_column(String(20))
    payload: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)


class LedgerRecord(Base):
    """DB backend for the append-only, hash-chained decision ledger — used on
    hosted deployments where the filesystem is ephemeral (DEPLOYMENT.md).
    `payload` is the full JSON record including hash and prev_hash; `seq`
    preserves chain order."""
    __tablename__ = "ledger_records"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text)


class VaultBlob(Base):
    """DB backend for the raw vault: content-addressed immutable payloads."""
    __tablename__ = "vault_blobs"
    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    blob: Mapped[bytes] = mapped_column(LargeBinary)


class VaultFetch(Base):
    __tablename__ = "vault_fetches"
    id: Mapped[int] = mapped_column(primary_key=True)
    hash: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    endpoint: Mapped[str] = mapped_column(String(200))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    nbytes: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[str] = mapped_column(Text, default="{}")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), unique=True)
    rationale: Mapped[str] = mapped_column(Text)  # REQUIRED at add-time (§21)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------- NSE archives
# Tables backed by NSE's official public EOD archive files. These are the free,
# keyless, exchange-published source — materially better than the unofficial
# quote API for anything derivative or delivery related, and they are the only
# free route to a real Indian option chain with open interest.

class DerivativeQuote(Base):
    """One F&O contract's EOD bar from the NSE F&O bhavcopy.

    Covers index futures/options (IDF/IDO) and stock futures/options (STF/STO):
    ~35k rows per trading day. `settlement_price` is the exchange's own mark and
    is what implied volatility should be solved from — `close` can be stale or
    zero for untraded strikes, of which there are many.
    """
    __tablename__ = "derivative_quotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)   # TckrSymb
    instrument_type: Mapped[str] = mapped_column(String(6))       # IDF|IDO|STF|STO
    expiry: Mapped[date] = mapped_column(Date, index=True)
    strike: Mapped[float | None] = mapped_column(Float)           # None for futures
    option_type: Mapped[str | None] = mapped_column(String(2))    # CE|PE, None for futures
    open_price: Mapped[float | None] = mapped_column(Float)
    high_price: Mapped[float | None] = mapped_column(Float)
    low_price: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    settlement_price: Mapped[float | None] = mapped_column(Float)
    underlying_price: Mapped[float | None] = mapped_column(Float)
    open_interest: Mapped[float | None] = mapped_column(Float)
    change_in_oi: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    lot_size: Mapped[int | None] = mapped_column(Integer)


class ListingWindow(Base):
    """When each NSE symbol was actually tradeable — the point-in-time universe.

    Every backtest here runs on TODAY'S index membership backfilled, which means
    a name that was listed in 2018 and later delisted is absent entirely. That
    is survivorship bias, and it inflates every absolute return the panel
    produces (the reconstructed equal-weight basket returns 24.67%/yr against
    the published NIFTY 500's 12.33%).

    Storing one row per SYMBOL rather than per symbol-day is what makes this
    affordable on a 512 MB tier: ~3,400 rows instead of ~5 million. First and
    last sighting plus the sample count is enough to answer the only question
    the studies need — "was this name tradeable on date D?" — to within the
    sampling interval.
    """
    __tablename__ = "listing_windows"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    first_seen: Mapped[date] = mapped_column(Date, index=True)
    last_seen: Mapped[date] = mapped_column(Date, index=True)
    sessions_sampled: Mapped[int] = mapped_column(Integer, default=0)
    # True once last_seen falls short of the newest sampled session: the symbol
    # stopped trading. This is the population the panel is missing.
    is_delisted: Mapped[bool] = mapped_column(Boolean, default=False)
    in_panel: Mapped[bool] = mapped_column(Boolean, default=False)


class DeliveryStat(Base):
    """Security-wise delivery position from the NSE MTO file.

    Delivery percentage is the closest free proxy to "was this real
    accumulation or intraday churn", and engine/novel.py's crowding proxy
    documents its absence as a limitation. It is not absent — it is published
    daily by the exchange.
    """
    __tablename__ = "delivery_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    series: Mapped[str] = mapped_column(String(4))
    traded_qty: Mapped[float] = mapped_column(Float)
    delivered_qty: Mapped[float] = mapped_column(Float)
    delivery_pct: Mapped[float] = mapped_column(Float)


class IndexObservation(Base):
    """Daily EOD bar for every NSE index, WITH the exchange's own valuation
    metrics (P/E, P/B, dividend yield) — ~141 indices per day.

    Index-level P/E history is a genuine market-valuation input that the
    platform previously had no access to: it makes "is the market itself
    expensive versus its own history" answerable on the same percentile
    footing as the single-stock version.
    """
    __tablename__ = "index_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    index_name: Mapped[str] = mapped_column(String(60), index=True)
    obs_date: Mapped[date] = mapped_column(Date, index=True)
    open_value: Mapped[float | None] = mapped_column(Float)
    high_value: Mapped[float | None] = mapped_column(Float)
    low_value: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    turnover_cr: Mapped[float | None] = mapped_column(Float)
    pe: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    div_yield: Mapped[float | None] = mapped_column(Float)
