"""EquiSense API + web app (PROJECT_DRAFT §16.2 application layer).

Run:  uvicorn equisense.api.app:app --reload
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import narrator
from ..db import Base, engine, get_session
from ..engine import valuation
from ..models import (Company, InvestorProfileRow, JournalEntry, Thesis,
                      TransactionRow, WatchlistItem)
from ..seed import seed
from . import services

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs per cold start on serverless — must stay fast and idempotent.
    Live data bootstrap is NOT done here (serverless kills background
    threads): the refresh stream is bootstrap-aware instead (status.py)."""
    from ..db import IS_SQLITE, ensure_schema
    ensure_schema()
    with get_session() as s:
        if IS_SQLITE and os.environ.get("EQUISENSE_AUTO_INGEST") != "1":
            seed(s)  # demo data only for local SQLite dev — never into a hosted DB
        if not s.scalars(select(InvestorProfileRow)).first():
            s.add(InvestorProfileRow(name="default", is_active=True))
            s.commit()
    yield


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
    """Single-user token gate (PROJECT_DRAFT §28.2: authenticated access even
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


def db():
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
    """Editable assumptions for the reverse DCF (§19.2)."""
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
    assumptions: list[str] = Field(min_length=1)          # falsifiable, §23.1
    invalidation_triggers: list[str] = Field(min_length=1)  # required, §23.1
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
    rationale: str = Field(min_length=10)  # required rationale at add-time (§21)


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
    """Recompute the reverse DCF under user-edited assumptions (§19.2)."""
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
    ids = sync_universe(s)
    result = refresh_quotes(s, ids)
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
    _get_company(s, company_id)
    from ..models import PriceObservation
    rows = s.execute(
        select(PriceObservation.obs_date, PriceObservation.close)
        .where(PriceObservation.company_id == company_id)
        .order_by(PriceObservation.obs_date.desc()).limit(days)).all()
    return [{"time": str(d), "value": round(c, 2)} for d, c in reversed(rows)]


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
    """Statement narrative (§13.3). The full grounded context is returned so
    the UI can show exactly what the model was given (§19.1 layer 3)."""
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


@app.post("/api/live/studies/run")
def live_run_studies(s: Session = Depends(db)):
    from ..research.base_rates import run_all_studies
    return run_all_studies(s)


@app.post("/api/live/refresh")
def live_refresh(s: Session = Depends(db)):
    """Full staged pipeline WITHOUT streaming — the fallback for hosts that
    buffer SSE. Returns every stage event the stream would have emitted."""
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
    return {"records": ledger.read_all()[-50:], "chain": ledger.verify_chain()}


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
    """Run REG-001: the regime engine justifying its own existence (PHASE2 §6.1)."""
    from ..research.reg001 import run_reg001
    return run_reg001(s)


@app.get("/api/live/vault")
def live_vault():
    from ..ingestion.vault import vault_stats
    return vault_stats()


@app.get("/api/live/status")
def live_status(s: Session = Depends(db)):
    """Data-trust surface: freshness, coverage, quality score, warnings."""
    from .status import data_status
    return data_status(s)


@app.get("/api/live/refresh/stream")
def live_refresh_stream(s: Session = Depends(db)):
    """Staged pipeline refresh as Server-Sent Events — everything visible."""
    from fastapi.responses import StreamingResponse
    from .status import refresh_stream
    return StreamingResponse(refresh_stream(s), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/live/portfolio-risk")
def live_portfolio_risk(s: Session = Depends(db)):
    from .status import portfolio_risk
    return portfolio_risk(s)


@app.get("/api/cron/refresh")
def cron_refresh(s: Session = Depends(db)):
    """Daily keep-fresh for Vercel Cron (GET; Vercel sends
    `Authorization: Bearer $CRON_SECRET`, which the auth gate accepts when
    CRON_SECRET equals EQUISENSE_ACCESS_TOKEN). Incremental prices + macro,
    then recompute studies and score due claims."""
    from ..ingestion.yahoo import ingest_macro, ingest_prices, sync_universe
    from ..research.base_rates import run_all_studies
    from .. import ledger as L
    from .snapshot import build_universe_snapshot
    ids = sync_universe(s)
    prices = ingest_prices(s, ids, years=1)
    macro = ingest_macro(s, years=1)
    studies = run_all_studies(s)["records"]
    scored = L.score_due_claims(s)["scored"]
    checkpointed = L.score_interim_checkpoints(s)["checkpointed"]
    snap = build_universe_snapshot(s)
    from .autopilot import get_config, run_autopilot
    auto = run_autopilot(s) if get_config(s)["enabled"] else None
    return {"price_rows": prices, "macro_rows": macro,
            "base_rate_records": studies, "claims_scored": scored,
            "checkpoints_scored": checkpointed,
            "snapshot_companies": len(snap["companies"]),
            "autopilot": None if auto is None else
            {"entries": len(auto["entries"]), "exits": len(auto["exits"])}}


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
