"""EquiSense API + web app (§14 application layer).

Run:  uvicorn equisense.api.app:app --reload
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai import narrator
from ..db import Base, engine, get_session
from ..engine import valuation
from ..models import (Company, InvestorProfileRow, JournalEntry, Thesis,
                      TransactionRow, WatchlistItem)
from . import services

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"

_log = logging.getLogger("equisense.api")

# Serverless platforms set these. Used only to decide whether a SQLite fallback
# is a development convenience or a production misconfiguration.
IS_HOSTED_ENV = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV")
                     or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs per cold start on serverless — must stay fast and idempotent.
    Live data bootstrap is NOT done here (serverless kills background
    threads): the refresh stream is bootstrap-aware instead (status.py)."""
    # NOTHING here may raise. On a serverless host an exception during init is
    # FUNCTION_INVOCATION_FAILED — the entire site 500s, including the pages that
    # would have explained why. A free-tier Postgres that auto-suspends after a
    # few minutes idle makes this a routine event, not an edge case: the cold
    # start lands on a sleeping database, create_all blocks waking it, and the
    # whole deployment goes dark.
    #
    # So startup is best-effort and the failure is carried to the endpoints,
    # which already know how to report a broken database. A site that loads and
    # says "no database" is strictly better than one that will not load at all.
    # Measured against the real deployment: a suspended Neon free-tier instance
    # takes ~28 SECONDS to accept its first connection, and the schema work on
    # top is only ~3s of that. No amount of optimising the migration helps,
    # because the cost is waking the database, not the DDL.
    #
    # So a hosted app does NO database work during init. The first real request
    # pays the wake cost inside its own generous budget (maxDuration 300)
    # instead of inside the function's much shorter init window, where blocking
    # produced FUNCTION_INVOCATION_FAILED and took the entire site down.
    #
    # Local development still boots eagerly: SQLite has no wake cost, and having
    # the schema and default profile ready on first run is worth the
    # milliseconds.
    if not IS_HOSTED_ENV:
        try:
            _startup_boot()
        except Exception as exc:                   # noqa: BLE001
            STARTUP_ERROR["detail"] = f"{type(exc).__name__}: {exc}"
    yield


STARTUP_ERROR: dict = {"detail": None}


def _startup_boot() -> None:
    """Schema + default profile. Split out so lifespan can guard it."""
    from ..db import IS_SQLITE, ensure_schema
    ensure_schema()
    with get_session() as s:
        # Seeding is disabled entirely — real data only, everywhere (owner's
        # standing instruction). A fresh database stays empty until live data is
        # fetched; nothing synthetic is ever written, so no fabricated number can
        # be mistaken for a real signal on any surface.
        if not s.scalars(select(InvestorProfileRow)).first():
            s.add(InvestorProfileRow(name="default", is_active=True))
            s.commit()


app = FastAPI(title="EquiSense", version="0.1.0", lifespan=lifespan)

_LOGIN_HTML = """<!DOCTYPE html><html><head><title>EquiSense</title><style>
body{background:#0d0d0d;color:#fff;font:15px system-ui;display:flex;align-items:center;
justify-content:center;height:100vh;margin:0}form{background:#1a1a19;padding:28px;
border-radius:10px;border:1px solid #383835}input{font:inherit;padding:8px 10px;
border-radius:6px;border:1px solid #383835;background:#0d0d0d;color:#fff;width:260px}
button{font:inherit;margin-left:8px;padding:8px 14px;border-radius:6px;border:none;
background:#3987e5;color:#fff;cursor:pointer}</style></head><body>
<form onsubmit="location='/?token='+encodeURIComponent(document.getElementById('t').value);return false">
<div style="font-weight:700;margin-bottom:10px">Equi<span style="color:#3987e5">Sense</span> — access token</div>
<input id="t" type="password" placeholder="EQUISENSE_ACCESS_TOKEN" autofocus><button>Enter</button>
</form></body></html>"""


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Single-user token gate (§3.3: authenticated access even
    for a personal app). Enabled only when EQUISENSE_ACCESS_TOKEN is set —
    local development stays frictionless."""
    token = os.environ.get("EQUISENSE_ACCESS_TOKEN")
    if not token:
        return await call_next(request)
    supplied = (request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                or request.query_params.get("token")
                or request.cookies.get("eqs_token"))
    if supplied != token:
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return HTMLResponse(_LOGIN_HTML, status_code=401)
    response = await call_next(request)
    if request.query_params.get("token") == token:
        response.set_cookie("eqs_token", token, httponly=True, samesite="lax",
                            max_age=90 * 24 * 3600)
    return response


def _reject_if_ephemeral() -> None:
    """Refuse writes on a hosted deployment with no real database.

    Without DATABASE_URL a serverless box falls back to SQLite under /tmp, which
    is PER-INSTANCE and wiped between invocations. Every write still appears to
    succeed: the refresh fetches genuine Yahoo data, the paper trade is
    accepted, the ledger entry is hash-chained — and the next request lands on a
    different instance that has never seen any of it.

    That is the most expensive failure in this codebase, because the ledger is
    the forward-testing record. Losing it silently means the nine months of
    calibration evidence never accumulates while appearing to. Better to refuse
    the write outright.
    """
    from ..db import IS_SQLITE
    if IS_HOSTED_ENV and IS_SQLITE:
        raise HTTPException(503, (
            "No database configured. This deployment is running on ephemeral "
            "per-instance storage, so anything written here is discarded on the "
            "next request — including paper trades and the hash-chained forecast "
            "ledger. Set DATABASE_URL and redeploy. The write was refused rather "
            "than silently lost."))


_SCHEMA_READY = {"done": False}


def db():
    # Deferred schema check: the first request per process pays for it, inside
    # its own 300s budget rather than the init window. Guarded so it runs once
    # and never blocks a warm invocation.
    if not _SCHEMA_READY["done"]:
        _SCHEMA_READY["done"] = True               # set first: a failure must not
        try:                                       # retry on every request
            from ..db import ensure_schema
            ensure_schema()
        except Exception as exc:                   # noqa: BLE001
            STARTUP_ERROR["detail"] = f"{type(exc).__name__}: {exc}"
    s = get_session()
    try:
        yield s
    finally:
        s.close()


# ------------------------------------------------------------------ schemas

class ProfileUpdate(BaseModel):
    horizon: Optional[str] = None
    horizon_target_year: Optional[int] = None
    risk_tolerance: Optional[str] = None
    style: Optional[float] = Field(None, ge=0, le=100)
    dividend_preference: Optional[float] = Field(None, ge=0, le=100)
    quality_emphasis: Optional[float] = Field(None, ge=0, le=100)
    sector_preferences: Optional[list[str]] = None
    sector_exclusions: Optional[list[str]] = None
    max_position_pct: Optional[float] = Field(None, gt=0, le=100)
    max_sector_pct: Optional[float] = Field(None, gt=0, le=100)
    max_drawdown_pct: Optional[float] = Field(None, gt=0, le=100)
    preferred_lens: Optional[str] = None
    rules: Optional[list[str]] = None


class ValuationAssumptions(BaseModel):
    """Editable assumptions for the reverse DCF (§14)."""
    risk_free_rate: float = Field(0.070, ge=0, le=0.25)
    equity_risk_premium: float = Field(0.065, ge=0, le=0.20)
    beta: float = Field(1.0, ge=0.1, le=3.0)
    tax_rate: float = Field(0.2517, ge=0, le=0.6)
    cost_of_debt: Optional[float] = Field(None, ge=0, le=0.4)
    horizon_years: int = Field(10, ge=3, le=25)
    terminal_growth: float = Field(0.04, ge=0, le=0.08)

    def to_engine(self) -> valuation.ReverseDcfAssumptions:
        return valuation.ReverseDcfAssumptions(
            horizon_years=self.horizon_years, terminal_growth=self.terminal_growth,
            wacc=valuation.WaccAssumptions(
                risk_free_rate=self.risk_free_rate,
                equity_risk_premium=self.equity_risk_premium,
                beta=self.beta, tax_rate=self.tax_rate,
                cost_of_debt=self.cost_of_debt))


class TransactionIn(BaseModel):
    company_id: int
    side: str = Field(pattern="^(buy|sell)$")
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    trade_date: date
    fees: float = Field(0.0, ge=0)


class ThesisIn(BaseModel):
    company_id: int
    statement: str = Field(min_length=10)
    assumptions: list[str] = Field(min_length=1)          # falsifiable, §14
    invalidation_triggers: list[str] = Field(min_length=1)  # required, §14
    sizing_rationale: str = ""
    review_date: Optional[date] = None
    elaboration: str = ""


class ThesisStatusUpdate(BaseModel):
    status: str = Field(pattern="^(draft|active|under_review|confirmed|invalidated|closed)$")


class JournalIn(BaseModel):
    content: str = Field(min_length=3)
    company_id: Optional[int] = None
    thesis_id: Optional[int] = None
    cfa_topic: str = ""


class WatchlistIn(BaseModel):
    company_id: int
    rationale: str = Field(min_length=10)  # required rationale at add-time (§14)


class ThesisDraftRequest(BaseModel):
    user_angle: str = Field(min_length=5,
                            description="Your own rationale fragments, in your words")


# ---------------------------------------------------------------- companies

def _get_company(s: Session, company_id: int) -> Company:
    c = s.get(Company, company_id)
    if not c:
        raise HTTPException(404, "company not found")
    return c


@app.get("/api/companies")
def list_companies(s: Session = Depends(db)):
    profile = services.active_profile(s)
    return services.dashboard(s, profile)["ranked"]


@app.get("/api/companies/{company_id}")
def company_detail(company_id: int, s: Session = Depends(db)):
    c = _get_company(s, company_id)
    return services.company_analysis(s, c, services.active_profile(s))


@app.post("/api/companies/{company_id}/valuation")
def company_valuation(company_id: int, assumptions: ValuationAssumptions,
                      s: Session = Depends(db)):
    """Recompute the reverse DCF under user-edited assumptions (§14)."""
    c = _get_company(s, company_id)
    stmts = services.latest_statements(s, company_id)
    price = services.latest_price(s, company_id)
    if not stmts or price is None:
        raise HTTPException(422, "insufficient data")
    result = valuation.reverse_dcf(stmts[-1], price, assumptions.to_engine())
    hist = valuation.historical_fcf_cagr(stmts)
    return {"implied_growth": result["implied_growth"].to_dict(),
            "wacc": result["wacc"].to_dict(),
            "historical_fcf_cagr": hist.to_dict() if hist else None,
            "assumptions": result["assumptions"],
            "enterprise_value": result["enterprise_value"],
            "base_fcf": result["base_fcf"]}


# ------------------------------------------------------------------ profile

@app.get("/api/profile")
def get_profile(s: Session = Depends(db)):
    return services.active_profile(s).to_dict()


@app.put("/api/profile")
def update_profile(update: ProfileUpdate, s: Session = Depends(db)):
    row = s.scalars(select(InvestorProfileRow)
                    .where(InvestorProfileRow.is_active.is_(True))).first()
    if row is None:
        raise HTTPException(404, "no active profile")
    data = update.model_dump(exclude_none=True)
    for k, v in data.items():
        if k in ("sector_preferences", "sector_exclusions"):
            v = ",".join(v)
        elif k == "rules":
            v = "\n".join(v)
        setattr(row, k, v)
    s.commit()
    return services.profile_from_row(row).to_dict()


# ---------------------------------------------------------------- dashboard

@app.get("/api/dashboard")
def get_dashboard(s: Session = Depends(db)):
    return services.dashboard(s, services.active_profile(s))


# ---------------------------------------------------------------- portfolio

@app.get("/api/portfolio")
def get_portfolio(s: Session = Depends(db)):
    return services.portfolio_view(s, services.active_profile(s))


@app.get("/api/transactions")
def list_transactions(s: Session = Depends(db)):
    rows = s.scalars(select(TransactionRow).order_by(TransactionRow.trade_date.desc())).all()
    companies = {c.id: c.ticker for c in s.scalars(select(Company)).all()}
    return [{"id": r.id, "ticker": companies.get(r.company_id), "company_id": r.company_id,
             "side": r.side, "quantity": r.quantity, "price": r.price,
             "trade_date": r.trade_date.isoformat(), "fees": r.fees} for r in rows]


@app.post("/api/transactions", status_code=201)
def add_transaction(t: TransactionIn, s: Session = Depends(db)):
    _get_company(s, t.company_id)
    row = TransactionRow(**t.model_dump())
    s.add(row)
    s.commit()
    return {"id": row.id}


@app.delete("/api/transactions/{txn_id}", status_code=204)
def delete_transaction(txn_id: int, s: Session = Depends(db)):
    row = s.get(TransactionRow, txn_id)
    if row:
        s.delete(row)
        s.commit()


# ------------------------------------------------------------ paper trading

class PaperTradeIn(BaseModel):
    company_id: int
    side: str = Field(pattern="^(buy|sell)$")
    quantity: float = Field(gt=0)
    dossier_hash: Optional[str] = None


class PaperResetIn(BaseModel):
    starting_cash: float = Field(1_000_000.0, gt=0)


@app.get("/api/paper")
def paper_account(s: Session = Depends(db)):
    from .paper import account
    return account(s)


@app.post("/api/paper/trade", status_code=201)
def paper_trade(t: PaperTradeIn, s: Session = Depends(db)):
    _reject_if_ephemeral()
    from .paper import place_trade
    try:
        return place_trade(s, t.company_id, t.side, t.quantity, t.dossier_hash)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/paper/reset")
def paper_reset(r: PaperResetIn, s: Session = Depends(db)):
    from .paper import reset_account
    return reset_account(s, r.starting_cash)


# ---------------------------------------------------- trading desk (live)

@app.post("/api/live/quotes")
def live_quotes(s: Session = Depends(db)):
    """Near-live price upsert (today's running bar) + market clock — called
    on a timer while the site is open so fills stay executable-real."""
    from ..ingestion.yahoo import market_open_ist, refresh_quotes, sync_universe
    from ..models import AppSnapshot

    # Index membership changes on reshuffles, not on a five-minute timer. This
    # re-fetched NSE's constituent CSV and re-upserted every company on EVERY
    # quote poll — for every open tab — which is most of the call's latency and
    # a pointless load on the exchange's file. Once a day is the real cadence.
    _sync_key = "universe_sync_day"
    _row = s.get(AppSnapshot, _sync_key)
    _today = str(date.today())
    if _row is None or _row.as_of != _today:
        ids = sync_universe(s)
        if _row is None:
            s.add(AppSnapshot(key=_sync_key, as_of=_today, payload="{}"))
        else:
            _row.as_of = _today
        s.commit()
    else:
        ids = {c.ticker: c.id for c in s.scalars(
            select(Company).where(Company.is_index_member.is_(True))).all()}
    result = refresh_quotes(s, ids)
    # New bars landed, so the cached snapshot and its freshness probe are both
    # stale. Without this the refresh button could appear to do nothing for up
    # to FRESHNESS_PROBE_TTL_S.
    from .snapshot import invalidate_universe_cache
    invalidate_universe_cache()
    result["market"] = market_open_ist()
    return result


@app.get("/api/live/candidates")
def live_candidates(s: Session = Depends(db)):
    """Universe-wide reasoning → the short list of defensible trades, sized
    for the paper account, every gate shown."""
    from .candidates import qualified_candidates
    from .paper import account
    acct = account(s, include_curve=False)
    return qualified_candidates(s, book_value=acct["equity"], cash=acct["cash"])


@app.get("/api/live/learning")
def live_learning():
    """The self-refinement state: cluster posteriors, calibration, outcomes."""
    from ..research.learning import learning_state
    return learning_state()


# ------------------------------------------------------------- autopilot

class AutopilotConfigIn(BaseModel):
    enabled: Optional[bool] = None
    max_new_per_run: Optional[int] = Field(None, ge=0, le=10)
    max_open_positions: Optional[int] = Field(None, ge=1, le=20)
    cash_reserve_pct: Optional[float] = Field(None, ge=0, le=90)
    time_exit_days: Optional[int] = Field(None, ge=30, le=730)
    # Upper bound is a storage decision, not a statistical one: every claim is a
    # permanent ledger record in a 0.5 GB database.
    daily_forecasts: Optional[int] = Field(None, ge=0, le=100)


@app.get("/api/autopilot")
def autopilot_state(s: Session = Depends(db)):
    from .autopilot import get_config, last_run
    return {"config": get_config(s), "last_run": last_run(s)}


@app.put("/api/autopilot")
def autopilot_config(c: AutopilotConfigIn, s: Session = Depends(db)):
    from .autopilot import set_config
    return set_config(s, c.model_dump(exclude_none=True))


@app.post("/api/autopilot/run")
def autopilot_run(s: Session = Depends(db)):
    from .autopilot import run_autopilot
    return run_autopilot(s, force=True)


# -------------------------------------------------------------- backtest

@app.get("/api/backtest/strategy")
def backtest_strategy(refresh: bool = False, s: Session = Depends(db)):
    from ..research.backtest import cached_strategy_backtest
    return cached_strategy_backtest(s, refresh=refresh)


@app.get("/api/companies/{company_id}/prices")
def company_prices(company_id: int, days: int = 1250, s: Session = Depends(db)):
    """Daily close history for the interactive chart."""
    company = _get_company(s, company_id)
    from ..models import PriceObservation
    rows = s.execute(
        select(PriceObservation.obs_date, PriceObservation.close)
        .where(PriceObservation.company_id == company_id)
        .order_by(PriceObservation.obs_date.desc()).limit(days)).all()
    if rows:
        return [{"time": str(d), "value": round(c, 2)} for d, c in reversed(rows)]
    # Live mode: no stored bars, so the chart reads the warm live series (the
    # snapshot already fetched it) rather than rendering an empty panel.
    try:
        from .snapshot import live_data_enabled
        if live_data_enabled():
            from ..ingestion import live_provider
            series = live_provider.get_series(company.ticker)
            if series and series[0]:
                d_c = list(zip(series[0], series[1]))[-days:]
                return [{"time": str(d), "value": round(c, 2)} for d, c in d_c]
    except Exception:                                  # noqa: BLE001
        pass
    return []


# ------------------------------------------------------------------- theses

def _thesis_dict(t: Thesis, ticker: str) -> dict:
    return {"id": t.id, "company_id": t.company_id, "ticker": ticker,
            "statement": t.statement,
            "assumptions": t.assumptions.splitlines(),
            "invalidation_triggers": t.invalidation_triggers.splitlines(),
            "sizing_rationale": t.sizing_rationale,
            "review_date": t.review_date.isoformat() if t.review_date else None,
            "status": t.status, "elaboration": t.elaboration,
            "created_at": t.created_at.isoformat()}


@app.get("/api/theses")
def list_theses(s: Session = Depends(db)):
    companies = {c.id: c.ticker for c in s.scalars(select(Company)).all()}
    return [_thesis_dict(t, companies.get(t.company_id, "?"))
            for t in s.scalars(select(Thesis).order_by(Thesis.created_at.desc())).all()]


@app.post("/api/theses", status_code=201)
def create_thesis(t: ThesisIn, s: Session = Depends(db)):
    c = _get_company(s, t.company_id)
    row = Thesis(company_id=t.company_id, statement=t.statement,
                 assumptions="\n".join(t.assumptions),
                 invalidation_triggers="\n".join(t.invalidation_triggers),
                 sizing_rationale=t.sizing_rationale, review_date=t.review_date,
                 elaboration=t.elaboration, status="draft")
    s.add(row)
    s.commit()
    return _thesis_dict(row, c.ticker)


@app.patch("/api/theses/{thesis_id}/status")
def update_thesis_status(thesis_id: int, u: ThesisStatusUpdate, s: Session = Depends(db)):
    t = s.get(Thesis, thesis_id)
    if not t:
        raise HTTPException(404, "thesis not found")
    t.status = u.status
    s.commit()
    c = s.get(Company, t.company_id)
    return _thesis_dict(t, c.ticker if c else "?")


# ------------------------------------------------------------------ journal

@app.get("/api/journal")
def list_journal(s: Session = Depends(db)):
    companies = {c.id: c.ticker for c in s.scalars(select(Company)).all()}
    return [{"id": j.id, "content": j.content, "cfa_topic": j.cfa_topic,
             "ticker": companies.get(j.company_id) if j.company_id else None,
             "company_id": j.company_id, "thesis_id": j.thesis_id,
             "created_at": j.created_at.isoformat()}
            for j in s.scalars(select(JournalEntry)
                               .order_by(JournalEntry.created_at.desc())).all()]


@app.post("/api/journal", status_code=201)
def add_journal(j: JournalIn, s: Session = Depends(db)):
    row = JournalEntry(**j.model_dump())
    s.add(row)
    s.commit()
    return {"id": row.id}


# ---------------------------------------------------------------- watchlist

@app.get("/api/watchlist")
def list_watchlist(s: Session = Depends(db)):
    companies = {c.id: c for c in s.scalars(select(Company)).all()}
    return [{"id": w.id, "company_id": w.company_id,
             "ticker": companies[w.company_id].ticker,
             "name": companies[w.company_id].name,
             "rationale": w.rationale, "added_at": w.added_at.isoformat()}
            for w in s.scalars(select(WatchlistItem)).all()]


@app.post("/api/watchlist", status_code=201)
def add_watchlist(w: WatchlistIn, s: Session = Depends(db)):
    _get_company(s, w.company_id)
    existing = s.scalars(select(WatchlistItem)
                         .where(WatchlistItem.company_id == w.company_id)).first()
    if existing:
        raise HTTPException(409, "already on watchlist")
    row = WatchlistItem(company_id=w.company_id, rationale=w.rationale)
    s.add(row)
    s.commit()
    return {"id": row.id}


@app.delete("/api/watchlist/{item_id}", status_code=204)
def remove_watchlist(item_id: int, s: Session = Depends(db)):
    row = s.get(WatchlistItem, item_id)
    if row:
        s.delete(row)
        s.commit()


# ----------------------------------------------------------------------- AI

@app.post("/api/ai/narrate/company/{company_id}")
def ai_narrate_company(company_id: int, s: Session = Depends(db)):
    """Statement narrative (§14). The full grounded context is returned so
    the UI can show exactly what the model was given (§14 layer 3)."""
    c = _get_company(s, company_id)
    analysis = services.company_analysis(s, c, services.active_profile(s))
    context = {
        "company": analysis["company"], "period": analysis["period"],
        "cards": {k: v for k, v in analysis["cards"].items() if k != "peer_comparison"},
        "trends": analysis["trends"],
        "note": "Demo dataset — figures are approximations for product demonstration."
        if c.is_demo_data else None,
    }
    return narrator.narrate_statements(context)


@app.post("/api/ai/narrate/portfolio")
def ai_narrate_portfolio(s: Session = Depends(db)):
    profile = services.active_profile(s)
    view = services.portfolio_view(s, profile)
    context = {"portfolio": view, "profile": profile.to_dict()}
    return narrator.narrate_portfolio(context)


@app.post("/api/ai/thesis-draft/{company_id}")
def ai_thesis_draft(company_id: int, req: ThesisDraftRequest, s: Session = Depends(db)):
    c = _get_company(s, company_id)
    analysis = services.company_analysis(s, c, services.active_profile(s))
    context = {"company": analysis["company"], "period": analysis["period"],
               "cards": {k: v for k, v in analysis["cards"].items()},
               "trends": analysis["trends"]}
    return narrator.draft_thesis(context, req.user_angle)


# ---------------------------------------------------------------- live (v2)

@app.get("/api/live/regime")
def live_regime(s: Session = Depends(db)):
    from .live import current_regime
    return current_regime(s)


@app.post("/api/live/dossier/{company_id}")
def live_dossier(company_id: int, s: Session = Depends(db)):
    """Build, pre-register (hash-chained), and return a full decision dossier."""
    _reject_if_ephemeral()
    from .live import build_dossier
    c = _get_company(s, company_id)
    profile = services.active_profile(s)
    view = services.portfolio_view(s, profile)
    book = view["total_value"] or 1_000_000
    return build_dossier(s, c, book_value=book,
                         max_position_pct=profile.max_position_pct)


@app.get("/api/live/base-rates")
def live_base_rates(s: Session = Depends(db)):
    from ..models import BaseRateRecord
    from ..research.registry import REGISTRY
    rows = s.scalars(select(BaseRateRecord).order_by(BaseRateRecord.study_key)).all()
    return {"registry": REGISTRY,
            "inference_note": (
                "N_eff is design-effect corrected: N observations collapse to "
                "n_clusters independent date blocks at the estimated ICC. "
                "t/p are cluster-robust (Liang–Zeger) on G−1 df; q is "
                "Benjamini–Hochberg FDR across the run. A cell with "
                "admissible=false was measured but is not usable as evidence."),
            "records": [{
                "study_key": r.study_key, "registry_ref": r.registry_ref,
                "regime": r.regime_filter, "horizon_days": r.horizon_days,
                "n": r.n, "n_eff": r.n_eff, "hit_rate": round(r.hit_rate, 3),
                "n_clusters": r.n_clusters, "icc": r.icc,
                "design_effect": r.design_effect,
                "median_excess_pct": round(r.median_excess_pct, 2),
                "net_median_excess_pct": None if r.net_median_excess_pct is None
                else round(r.net_median_excess_pct, 2),
                "cohort_breadth_pct": r.cohort_breadth_pct,
                "ci95": None if r.median_ci95_lo_pct is None else f"{r.median_ci95_lo_pct}, {r.median_ci95_hi_pct}",
                "iqr": [round(r.q25_excess_pct, 2), round(r.q75_excess_pct, 2)],
                "t_stat": r.t_stat, "p_value": r.p_value, "q_value": r.q_value,
                "admissible": bool(r.admissible),
                "admissibility_reason": r.admissibility_reason,
                "multiplicity_verdict": r.multiplicity_verdict,
                "survives_multiplicity": bool(r.survives_multiplicity),
                "computed_at": r.computed_at.isoformat(),
            } for r in rows]}


@app.get("/api/live/ic")
def live_ic(s: Session = Depends(db)):
    """Information Coefficient per signal, with each result's own detection
    limit — so a null reads as 'absent' or 'unresolvable' rather than
    ambiguously either."""
    from ..research.ic import run_ic_studies
    from ..models import AppSnapshot
    import json
    row = s.get(AppSnapshot, "ic_studies")
    if row is not None:
        try:
            return json.loads(row.payload)
        except Exception:                          # noqa: BLE001
            pass
    return {"computable": False,
            "reason": "not yet computed — POST /api/live/ic/run"}


@app.post("/api/live/ic/run")
def live_ic_run(s: Session = Depends(db)):
    """Recompute IC across the stored history. Cached because it is a full
    pass over the price panel."""
    import json
    from datetime import date as _date

    from ..models import AppSnapshot
    from ..research.ic import run_ic_studies
    out = run_ic_studies(s)
    row = s.get(AppSnapshot, "ic_studies")
    if row is None:
        row = AppSnapshot(key="ic_studies", as_of=str(_date.today()), payload="")
        s.add(row)
    row.payload = json.dumps(out)
    row.as_of = str(_date.today())
    s.commit()
    return out


@app.get("/api/live/factor-portfolio")
def live_factor_portfolio(s: Session = Depends(db)):
    """What each factor PAYS: quantile spreads, turnover, and return net of the
    India round trip — long-only (tradeable) alongside long-short (evaluation)."""
    import json

    from ..models import AppSnapshot
    row = s.get(AppSnapshot, "factor_studies")
    if row is not None:
        try:
            return json.loads(row.payload)
        except Exception:                          # noqa: BLE001
            pass
    return {"computable": False,
            "reason": "not yet computed — POST /api/live/factor-portfolio/run"}


@app.post("/api/live/factor-portfolio/run")
def live_factor_portfolio_run(s: Session = Depends(db)):
    """Recompute and cache. A full pass over the price panel, like the IC run."""
    import json
    from datetime import date as _date

    from ..models import AppSnapshot
    from ..research.factor_portfolio import run_factor_studies
    out = run_factor_studies(s)
    row = s.get(AppSnapshot, "factor_studies")
    if row is None:
        row = AppSnapshot(key="factor_studies", as_of=str(_date.today()), payload="")
        s.add(row)
    row.payload = json.dumps(out, default=str)
    row.as_of = str(_date.today())
    s.commit()
    return out


@app.post("/api/live/studies/run")
def live_run_studies(s: Session = Depends(db)):
    from ..research.base_rates import run_all_studies
    return run_all_studies(s)


@app.post("/api/live/realign")
def live_realign(s: Session = Depends(db)):
    """Re-derive everything the stored data implies, WITHOUT re-ingesting it.

    The full refresh downloads a decade of prices and recomputes every
    hypothesis — minutes of work, and the wrong tool when the question is
    simply "is what I am looking at consistent with what the database already
    knows?". Derived state can drift behind stored state for ordinary reasons:
    a snapshot built before the last quote poll, forecasts not yet registered
    today, claims whose horizon has since expired, an autopilot that has not
    seen the newest marks.

    So this rebuilds the published snapshot, registers today's forecasts,
    scores whatever is now due, and lets the autopilot act on the current
    book — the alignment pass, not the data cycle. Seconds, not minutes, and
    it touches no external provider.
    """
    _reject_if_ephemeral()
    from .. import ledger as L
    from .autopilot import get_config, register_daily_forecasts, run_autopilot
    from .snapshot import rebuild_universe_snapshot

    out: dict = {}
    snap = rebuild_universe_snapshot(s)
    s.commit()
    out["snapshot_companies"] = len(snap["companies"])
    try:
        fc = register_daily_forecasts(s)
        s.commit()
        out["forecasts_registered"] = len(fc.get("registered", []))
        out["forecasts_skipped"] = len(fc.get("skipped", []))
    except Exception as exc:                           # noqa: BLE001
        s.rollback()
        out["forecasts_error"] = f"{type(exc).__name__}: {exc}"[:160]
    out["claims_scored"] = L.score_due_claims(s)["scored"]
    out["checkpoints_scored"] = L.score_interim_checkpoints(s)["checkpointed"]
    s.commit()
    if get_config(s)["enabled"]:
        rep = run_autopilot(s)
        s.commit()
        out["autopilot"] = {"entries": len(rep["entries"]), "exits": len(rep["exits"])}
    out["note"] = ("Derived state realigned from stored data — no provider was "
                   "contacted and no study was recomputed. Use Refresh for that.")
    return out


@app.post("/api/live/refresh")
def live_refresh(s: Session = Depends(db)):
    """Full staged pipeline WITHOUT streaming — the fallback for hosts that
    buffer SSE. Returns every stage event the stream would have emitted."""
    _reject_if_ephemeral()
    import json as _json
    from .status import refresh_stream
    stages = [_json.loads(ev[5:].strip()) for ev in refresh_stream(s)
              if ev.startswith("data:")]
    return {"stages": stages,
            "ok": any(st.get("stage") == "pipeline" and st.get("status") == "complete"
                      for st in stages)}


@app.get("/api/live/ledger")
def live_ledger():
    from .. import ledger
    # one read serves both the listing and the verification
    records = ledger.read_all()
    return {"records": records[-50:], "chain": ledger.verify_chain(records)}


@app.post("/api/live/score")
def live_score(s: Session = Depends(db)):
    from .. import ledger
    due = ledger.score_due_claims(s)
    checkpoints = ledger.score_interim_checkpoints(s)
    return {**due, "checkpoints_scored": checkpoints["checkpointed"]}


@app.get("/api/live/calibration")
def live_calibration():
    from .. import ledger
    return ledger.calibration_report()


@app.post("/api/live/reg001")
def live_reg001(s: Session = Depends(db)):
    """Run REG-001: the regime engine justifying its own existence (§11)."""
    from ..research.reg001 import run_reg001
    return run_reg001(s)


@app.get("/api/live/vault")
def live_vault():
    from ..ingestion.vault import vault_stats
    return vault_stats()


@app.get("/api/live/status")
def live_status(s: Session = Depends(db), verify_ledger: bool = False):
    """Data-trust surface: freshness, coverage, quality score, warnings.

    `verify_ledger` walks the hash chain, which is an O(n) re-hash from genesis
    and cost 5.8s of pure Python on every page load when it was unconditional.
    Off by default; the Data Health panel offers it as an explicit action.
    """
    from .status import data_status
    return data_status(s, verify_ledger=verify_ledger)


@app.get("/api/live/refresh/stream")
def live_refresh_stream(s: Session = Depends(db)):
    """Staged pipeline refresh as Server-Sent Events — everything visible."""
    _reject_if_ephemeral()
    from fastapi.responses import StreamingResponse
    from .status import refresh_stream
    return StreamingResponse(refresh_stream(s), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/live/portfolio-risk")
def live_portfolio_risk(s: Session = Depends(db)):
    from .status import portfolio_risk
    return portfolio_risk(s)


# The cron must finish INSIDE the platform's function limit (vercel.json
# maxDuration). Measured against the real deployment, the full pipeline ran
# 300s and was killed with a 504 — every day, silently, committing nothing
# past the point of death. Leave real headroom: a stage started at 239s can
# still overrun, so the budget is what we refuse to START new work after.
CRON_BUDGET_S = 210.0

# New names are backfilled a bounded number per run. A universe switch can
# introduce hundreds at once and a 10y pull for all of them would consume the
# whole function budget; spreading it over consecutive runs costs days of
# completeness on names the system has never held, and costs nothing on the
# ones it has.
BACKFILL_PER_RUN = 40

# Names re-pulled per run to close gaps and fill missing intraday range. Smaller
# than the backfill budget because repair is never urgent and always competes
# with captures that cannot be backfilled at all (§3.4).
REPAIR_PER_RUN = 12


def _priced_ids(session: Session) -> set[int]:
    """Company ids that already have at least one stored bar."""
    from ..models import PriceObservation
    return set(session.scalars(
        select(PriceObservation.company_id).distinct()).all())


@app.get("/api/cron/refresh")
def cron_refresh(s: Session = Depends(db)):
    """Daily keep-fresh for Vercel Cron (GET; Vercel sends
    `Authorization: Bearer $CRON_SECRET`, which the auth gate accepts when
    CRON_SECRET equals EQUISENSE_ACCESS_TOKEN).

    Stages run in order of IRREPLACEABILITY, not convenience, and each commits
    on its own. This ordering is the whole point:

    The daily NSE archive and the option surface publish one file per day and
    cannot be backfilled — a missed day is a permanent hole. Registering
    forecasts is what lets the system learn at all. Prices, macro, base rates
    and the snapshot are all fully recomputable from scratch at any time.

    Previously the recomputable work ran FIRST and the irreplaceable work last,
    so when the function hit its limit the only things that never happened were
    the ones that could never be recovered. Production showed it exactly: 2
    stored IV observations, zero scored claims, and base rates frozen for days.
    """
    import time as _time

    from ..ingestion.yahoo import (ingest_macro, ingest_prices, refresh_quotes,
                                   sync_universe)
    from ..research.base_rates import run_all_studies
    from .. import ledger as L
    from ..ingestion.nse_archive import backfill as nse_backfill, prune
    from .autopilot import get_config, register_daily_forecasts, run_autopilot
    from .markets import capture_vol_surface
    from .snapshot import live_data_enabled, rebuild_universe_snapshot

    t0 = _time.monotonic()
    out: dict = {}
    live = live_data_enabled()

    def stage(name: str, fn):
        """Run one stage, commit it, and never let it take down the rest.

        Skipping on budget is reported, not silent: a cron that quietly does
        half its work is how this failure survived for weeks.

        Timings are LOGGED, not just returned, because the response never
        arrives when the platform kills the function — the logs are the only
        record of which stage was holding the budget.
        """
        elapsed = _time.monotonic() - t0
        if elapsed > CRON_BUDGET_S:
            _log.warning("cron: SKIP %s (%.1fs elapsed, budget %.0fs)",
                         name, elapsed, CRON_BUDGET_S)
            out[name] = {"skipped": f"time budget exhausted at {elapsed:.0f}s"}
            return None
        _log.info("cron: start %s (%.1fs elapsed)", name, elapsed)
        t_stage = _time.monotonic()
        try:
            result = fn()
            s.commit()          # durable per stage, so a later stall cannot undo it
            _log.info("cron: done %s in %.1fs", name, _time.monotonic() - t_stage)
            out[name] = result
            out.setdefault("timings_s", {})[name] = round(_time.monotonic() - t_stage, 1)
            return result
        except Exception as exc:               # noqa: BLE001 - never block the cron
            s.rollback()
            _log.warning("cron: FAIL %s after %.1fs: %s",
                         name, _time.monotonic() - t_stage, exc)
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:160]}
            return None

    def dstage(name: str, fn):
        """A bulk-DATA stage. In live mode the market is fetched from Yahoo on
        demand and nothing bulk is stored, so these stages — price/OHLC/macro
        ingestion, archives, the columnar panel — are not just unnecessary but
        actively harmful: every write spends the metered transfer this whole
        rearchitecture exists to stop. Skipped and reported, never silent."""
        if live:
            out[name] = {"skipped": "live mode — market data is fetched live, "
                                    "not stored (no Neon writes)"}
            return None
        return stage(name, fn)

    # 1. Universe first: everything downstream needs the ids, and it is cheap.
    ids = stage("universe", lambda: sync_universe(s)) or {}
    out["universe"] = {"companies": len(ids)}

    # 2. NON-BACKFILLABLE captures, before anything that merely refetches.
    dstage("vol_surface", lambda: capture_vol_surface(s))
    if ids:
        dstage("nse_archives", lambda: nse_backfill(s, days=4, symbols=list(ids)))

    # 3. Prices and macro — recoverable, but the learning loop below needs them.
    #
    # Incremental, not a blanket re-pull. This downloaded a FULL YEAR of bars
    # for every name daily; at 50 names that was affordable and at 500 it cost
    # 155s of a 300s budget to obtain, on almost every name, one new bar.
    # refresh_quotes sizes its window to the furthest-behind name instead, so a
    # current universe costs a few days of data rather than a year.
    #
    # Names with NO history are a different job: _refresh_period deliberately
    # ignores them (sizing the window to a new listing would drag the whole
    # batch to 10y), so they are backfilled separately and in bounded chunks —
    # a universe switch can introduce hundreds at once, and none of them are
    # worth losing the rest of the pipeline over.
    if ids:
        dstage("price_rows", lambda: refresh_quotes(s, ids))
        missing = [t for t, cid in ids.items() if cid not in _priced_ids(s)]
        if missing:
            chunk = dict(list({t: ids[t] for t in missing}.items())[:BACKFILL_PER_RUN])
            dstage("backfilled_names", lambda: {
                "names": len(chunk), "remaining": len(missing) - len(chunk),
                "rows": ingest_prices(s, chunk, years=10)})
        else:
            # Nothing new to bootstrap, so spend the same budget repairing what
            # is already there. Staleness cannot see either fault this fixes: a
            # hole in the MIDDLE of a series leaves the latest bar current, and
            # a bar missing its intraday range is a bar all the same. Both are
            # silent, and both corrupt the volatility that sets position size.
            # Bounded and worst-first, because a hole that has survived a year
            # survives another day and must never cost the irreplaceable
            # captures above it.
            from ..ingestion.coverage import names_needing_repair
            hurt = names_needing_repair(s, limit=REPAIR_PER_RUN)
            if hurt:
                dstage("repaired_names", lambda: {
                    "names": hurt,
                    "rows": ingest_prices(s, {t: ids[t] for t in hurt if t in ids},
                                          years=10)})
    dstage("macro_rows", lambda: ingest_macro(s, years=1))

    # The columnar panel, rebuilt once here so that every study, IC run, factor
    # fit and backtest below reads one compressed blob instead of the whole
    # price table. That read is the largest recurring data transfer in the
    # system and is what exhausted the tier's quota (§5.3).
    from ..panel import build_panels
    dstage("panel", lambda: build_panels(s))

    # 4. The learning loop. Every gated capability unlocks from realised
    #    forecasts, and a forecast only exists once a dossier is registered.
    #    Registered BEFORE scoring so a claim made today starts its horizon now.
    forecasts = stage("forecasts", lambda: register_daily_forecasts(s))
    if isinstance(forecasts, dict) and "registered" in forecasts:
        out["forecasts"] = {"registered": len(forecasts["registered"])}
    stage("claims_scored", lambda: L.score_due_claims(s)["scored"])
    stage("checkpoints_scored", lambda: L.score_interim_checkpoints(s)["checkpointed"])

    # 5. The snapshot, which everything below reads.
    snap = stage("snapshot", lambda: rebuild_universe_snapshot(s))
    if isinstance(snap, dict) and "companies" in snap:
        out["snapshot"] = {"companies": len(snap["companies"])}

    # 6. Acting on the live book, and protecting the database. Both sit AHEAD
    #    of base rates because neither is recomputable tomorrow: a stop-loss or
    #    time exit that does not fire today did not fire, and retention that
    #    does not run lets a 0.5 GB tier fill. Measured at 500 names, studies
    #    took 112s and pushed both of these past the budget — the book stopped
    #    being managed so that a statistic could be one day fresher.
    if get_config(s)["enabled"]:
        auto = stage("autopilot", lambda: run_autopilot(s))
        if isinstance(auto, dict):
            out["autopilot"] = {"entries": len(auto["entries"]),
                                "exits": len(auto["exits"])}
    dstage("pruned", lambda: prune(s))

    # 7. The most expensive stage and the most recomputable one, so it is both
    #    the correct thing to sacrifice on a heavy day AND the wrong thing to
    #    repeat daily. It reloads the ENTIRE price table — unfiltered, because
    #    survivorship correction needs the delisted names — which on a database
    #    that meters data transfer is the single largest recurring cost in the
    #    system, roughly 60 MB a run. Base rates are ten-year statistics: doing
    #    that every day bought a number that had not moved.
    #
    #    In live mode there is no stored panel to reload at all: the deep,
    #    survivorship-corrected history these studies need is precisely the bulk
    #    data the rearchitecture stopped keeping. So the daily cron does not run
    #    them — the last computed records (small JSON, persisted) are served, and
    #    a refresh is an explicit on-demand job (/api/live/studies/run) that pays
    #    the one-off deep fetch when the user asks for it.
    from ..models import BaseRateRecord
    from .status import STALE_STUDY_DAYS
    if live:
        out["base_rate_records"] = {
            "skipped": "live mode — base-rate studies need a deep, survivorship-"
                       "corrected historical panel that is not stored. Last "
                       "computed records are served; refresh on demand."}
    else:
        _computed = s.scalar(select(func.max(BaseRateRecord.computed_at)))
        _due = (_computed is None
                or (date.today() - _computed.date()).days >= STALE_STUDY_DAYS)
        if _due:
            stage("base_rate_records", lambda: run_all_studies(s)["records"])
        else:
            age = (date.today() - _computed.date()).days
            out["base_rate_records"] = {
                "skipped": f"studies are {age}d old; recomputed every "
                           f"{STALE_STUDY_DAYS}d. Ten-year statistics do not move "
                           "on one session, and the full-panel reload is the "
                           "largest recurring data transfer in the system."}

    out["elapsed_s"] = round(_time.monotonic() - t0, 1)
    out["budget_s"] = CRON_BUDGET_S
    # What this run actually pulled out of the database. The quota that took the
    # site down was exhausted invisibly, because nothing anywhere reported the
    # volume of a read — only its duration.
    from ..db import rows_read_report
    out["data_read"] = rows_read_report(reset=True)
    return out


@app.get("/api/companies/{company_id}/memory")
def company_memory_view(company_id: int, s: Session = Depends(db)):
    from .status import company_memory
    return company_memory(s, _get_company(s, company_id))


# ------------------------------------------------------- markets (multi-asset)
# Derivatives, flow and valuation-regime endpoints. Each returns
# {"available": false, "reason": ...} rather than raising when the underlying
# dataset has not been ingested yet — an empty dataset is an ordinary state and
# the UI has to be able to say which one it is.


@app.get("/api/markets/derivatives/{symbol}")
def markets_derivatives(symbol: str, live: bool = True, s: Session = Depends(db)):
    """Futures term structure (implied financing rate) + option chain with a
    solved IV surface, 25-delta skew, PCR and OI structure."""
    from .markets import derivative_snapshot
    return derivative_snapshot(s, symbol, live=live)


class PositionLeg(BaseModel):
    kind: str = Field(description="call | put | future")
    strike: float = 0.0
    quantity: int = Field(description="lots; negative is short")
    premium: float = 0.0


class PositionRiskRequest(BaseModel):
    symbol: str = "NIFTY"
    account_equity: float = Field(default=100_000.0, gt=0)
    legs: list[PositionLeg]


@app.post("/api/markets/position-risk")
def markets_position_risk(req: PositionRiskRequest, s: Session = Depends(db)):
    """Net greeks, expiry payoff, scenario margin estimate and the leverage
    reality-check (with SEBI's published individual F&O loss rate attached)."""
    from .markets import position_risk
    if not req.legs:
        raise HTTPException(400, "at least one leg is required")
    return position_risk(s, req.symbol, [l.model_dump() for l in req.legs],
                         req.account_equity)


@app.get("/api/markets/delivery/{ticker}")
def markets_delivery(ticker: str, s: Session = Depends(db)):
    """Delivery percentage vs the stock's own history — real accumulation
    versus intraday churn, from NSE's published MTO file."""
    from .markets import delivery_profile
    return delivery_profile(s, ticker)


@app.get("/api/markets/valuation")
def markets_valuation(index: str = "Nifty 50", s: Session = Depends(db)):
    """Index P/E vs its own history, plus the large/mid/small multiple spread."""
    from .markets import market_valuation, valuation_spread
    return {"index": market_valuation(s, index), "segments": valuation_spread(s)}


@app.get("/api/markets/simulate")
def markets_simulate(horizon_days: int = 21, paths: int = 20000,
                     s: Session = Depends(db)):
    """Monte Carlo VaR / Expected Shortfall / drawdown on the actual book,
    under Gaussian, Student-t AND a bootstrap of real history."""
    from .markets import portfolio_simulation
    return portfolio_simulation(s, horizon_days=horizon_days,
                                n_paths=max(1000, min(paths, 60000)))


@app.get("/api/markets/rates")
def markets_rates(s: Session = Depends(db)):
    """Risk-free rate implied by the futures basis plus the exchange's own index
    dividend yield, and an earnings-yield ERP sanity check. Replaces a hardcoded
    7.0% that fed every discounted valuation."""
    from .markets import market_rates
    return market_rates(s)


@app.get("/api/markets/relationships")
def markets_relationships(lookback: int = 900, s: Session = Depends(db)):
    """Cross-asset correlation map with STRESS-CONDITIONAL correlations —
    what still diversifies in a drawdown, which is when it matters. All pairs
    are FDR-controlled."""
    from .markets import relationships
    return relationships(s, lookback=max(120, min(lookback, 2500)))


@app.get("/api/markets/flow")
def markets_flow(ticker: str | None = None, s: Session = Depends(db)):
    """Net disclosed institutional activity from NSE bulk/block deals — NET
    direction scaled by liquidity, never gross value."""
    from .markets import institutional_flow
    return institutional_flow(s, ticker)


@app.get("/api/storage")
def storage_view(universe_size: int = 50, s: Session = Depends(db)):
    """Storage headroom, classified by REPLACEABILITY rather than size.

    The operationally important fact: the largest table is also the most
    disposable (one Yahoo request restores a decade), while the data that
    genuinely cannot be rebuilt is a rounding error on the total."""
    from ..ingestion.retention import projected_headroom, storage_report
    return {"report": storage_report(s),
            "projection": projected_headroom(s, max(10, min(universe_size, 2000)))}


class UniverseIn(BaseModel):
    index_key: str


@app.get("/api/storage/reads")
def storage_reads():
    """How many rows this process has pulled per source, since it started.

    Exists because the deployment was taken down by an exhausted DATA TRANSFER
    quota while disk sat at 41%, and nothing could say which read was
    responsible: every cost in this system had been reasoned about in rows and
    seconds, never in bytes off the database. Self-reported by the loaders, so
    it needs no extension and no privileges.
    """
    from ..db import rows_read_report
    return rows_read_report()


@app.get("/api/universe")
def universe_state(s: Session = Depends(db)):
    """Which NSE index defines the live analytical universe, and the options.

    Universe size is the binding constraint on this system's ability to learn.
    Claims come from names; a 50-name cross-section produces them roughly ten
    times slower than a 500-name one, and gives percentile normalisation only
    50 points to rank against — which makes every "top quintile" a 10-name bet.
    """
    from ..ingestion.universe import INDEX_CAP_BAND
    from ..ingestion.yahoo import active_index_key
    live = s.scalar(select(func.count()).select_from(Company)
                    .where(Company.is_index_member.is_(True))) or 0
    known = s.scalar(select(func.count()).select_from(Company)) or 0
    return {"index_key": active_index_key(s),
            "choices": sorted(INDEX_CAP_BAND),
            "live_members": live, "companies_known": known,
            "note": ("Changing this takes effect on the next universe sync "
                     "(Sync or Refresh). Departed names keep their price "
                     "history — they leave the live cross-section, they are "
                     "never deleted.")}


@app.put("/api/universe")
def universe_set(body: UniverseIn, s: Session = Depends(db)):
    _reject_if_ephemeral()
    from ..ingestion.yahoo import set_index_key, sync_universe
    try:
        key = set_index_key(s, body.index_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    ids = sync_universe(s, index_key=key)
    from .snapshot import invalidate_universe_cache
    invalidate_universe_cache()
    return {"index_key": key, "live_members": len(ids),
            "note": "Universe re-synced. Run Refresh to ingest any new names' history."}


@app.get("/api/markets/transmission")
def markets_transmission(driver: str = "BZ=F", window: int = 260,
                         s: Session = Depends(db)):
    """Macro → market → sector → security, one measured link at a time (§6.5).

    Answers the question every other engine here skips: a macro variable just
    moved — by what PATH does that reach a price, and does this platform's own
    data actually show the path exists?
    """
    from datetime import timedelta

    from ..engine.crossasset import align_on_dates, returns_from_dated_closes
    from ..engine.transmission import build_chain, drivers
    from ..models import MacroObservation, PriceObservation

    since = date.today() - timedelta(days=int(window * 1.6))

    macro_rows = s.execute(
        select(MacroObservation.symbol, MacroObservation.obs_date,
               MacroObservation.close)
        .where(MacroObservation.obs_date >= since)).all()
    by_symbol: dict[str, list] = {}
    for sym, d_, c in macro_rows:
        by_symbol.setdefault(sym, []).append((d_, c))
    if driver not in by_symbol:
        return {"available": False,
                "reason": f"no stored history for {driver}",
                "drivers": drivers()}

    driver_closes = sorted(by_symbol[driver])
    move_pct = None
    if len(driver_closes) > 63 and driver_closes[-64][1]:
        move_pct = (driver_closes[-1][1] / driver_closes[-64][1] - 1.0) * 100

    # Sector baskets are equal-weighted from the LIVE universe: a departed name
    # in the basket would measure a sector this book can no longer hold.
    from .snapshot import _live_ids
    px = s.execute(
        select(PriceObservation.company_id, PriceObservation.obs_date,
               PriceObservation.close)
        .where(PriceObservation.company_id.in_(_live_ids()),
               PriceObservation.obs_date >= since)).all()
    meta = {c.id: (c.sector, c.ticker) for c in s.scalars(
        select(Company).where(Company.is_index_member.is_(True))).all()}
    per_name: dict[str, list] = {}
    for cid, d_, c in px:
        m = meta.get(cid)
        if m:
            per_name.setdefault(m[1], []).append((d_, c))

    # EVERYTHING stays date-keyed until the final intersection. Equity bars and
    # macro bars come from different providers with different holiday calendars,
    # so pairing them by position pairs Monday's rupee move with Tuesday's
    # equity move — the failure crossasset._align documents, and the reason
    # NIFTY Bank measured a beta of 0.02 against the financials basket it is
    # definitionally made of.
    name_rets = {t: returns_from_dated_closes(v) for t, v in per_name.items()
                 if len(v) > 60}
    name_sector = {m[1]: m[0] for m in meta.values()}

    # Equal weight means the mean of the members trading THAT DAY, not a
    # running pairwise average — that would weight the last name added 1/2, the
    # one before it 1/4, and so on.
    sector_dated: dict[str, dict] = {}
    for tkr, rets in name_rets.items():
        sec = name_sector.get(tkr)
        if not sec:
            continue
        bucket = sector_dated.setdefault(sec, {})
        for d_, r in rets.items():
            bucket.setdefault(d_, []).append(r)
    sector_dated = {sec: {d_: sum(v) / len(v) for d_, v in days.items()}
                    for sec, days in sector_dated.items()}

    to_align = {"__driver__": returns_from_dated_closes(driver_closes),
                "__market__": returns_from_dated_closes(
                    sorted(by_symbol.get("^NSEI", [])))}
    to_align.update({f"sector::{k}": v for k, v in sector_dated.items()})
    to_align.update({f"name::{k}": v for k, v in name_rets.items()})
    aligned = align_on_dates({k: v for k, v in to_align.items() if v})

    chain = build_chain(
        driver, aligned.get("__driver__", []), move_pct,
        aligned.get("__market__", []),
        {k[len("sector::"):]: v for k, v in aligned.items() if k.startswith("sector::")},
        {k[len("name::"):]: v for k, v in aligned.items() if k.startswith("name::")},
        name_sector)
    chain["drivers"] = drivers()
    chain["aligned_observations"] = len(aligned.get("__driver__", []))
    chain["window_days"] = window
    return chain


@app.get("/api/markets/events")
def markets_events(s: Session = Depends(db)):
    """Scheduled corporate events for names this book actually cares about.

    Live from NSE's published calendar, never stored (§5.2). Annotated onto the
    open paper positions, because holding into a result you did not know was
    coming is a risk the evidence stack cannot see: momentum and valuation read
    identically the day before earnings and the day after.
    """
    from ..ingestion.nse_events import fetch_event_calendar
    from .paper import account

    cal = fetch_event_calendar()
    events = cal.get("events") or {}
    held = []
    if cal.get("available"):
        for p in account(s, include_curve=False)["positions"]:
            evs = events.get(p["ticker"].upper())
            if evs:
                held.append({"ticker": p["ticker"], "direction": p["direction"],
                             "quantity": p["quantity"], **evs[0]})
        held.sort(key=lambda e: e["days_away"])
    upcoming = sorted(
        ({"ticker": t, **evs[0]} for t, evs in events.items() if evs),
        key=lambda e: e["days_away"])[:60]
    return {"available": cal.get("available"), "reason": cal.get("reason"),
            "note": cal.get("note"), "symbols": cal.get("symbols", 0),
            "held_with_events": held, "upcoming": upcoming}


@app.get("/api/markets/vrp")
def markets_vrp(symbol: str = "NIFTY", horizon_days: int = 21,
                s: Session = Depends(db)):
    """Variance risk premium: implied volatility versus the volatility that
    subsequently realised. Not testable until the IV series has accumulated —
    it publishes one file per day and cannot be backfilled."""
    from .markets import variance_premium
    return variance_premium(s, symbol, horizon_days)


@app.post("/api/markets/capture-surface")
def markets_capture_surface(s: Session = Depends(db)):
    """Persist today's option-surface summary (six floats per expiry)."""
    from .markets import capture_vol_surface
    return capture_vol_surface(s)


@app.get("/api/markets/sources")
def markets_sources():
    """Data-source reachability. Exists because every archive fetch fails closed
    (returns empty), so an unreachable source is otherwise indistinguishable
    from a quiet market day."""
    from ..ingestion.nse_archive import health_check
    return health_check()


# ---------------------------------------------------------------- static UI

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")
