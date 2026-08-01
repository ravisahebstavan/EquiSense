"""Data-trust surfaces (Phase III): dataset freshness, coverage, quality score,
staged streaming refresh, portfolio risk, and per-company research memory.

The rule this module serves: the user must never wonder whether they are
looking at stale information, and no backend capability may hide in JSON.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import ledger
from ..engine import technical
from ..models import (BaseRateRecord, Company, FilingPeriod, JournalEntry,
                      MacroObservation, PriceObservation, Thesis)
from ..research.registry import REGISTRY

STALE_PRICE_DAYS = 4      # > this (covers weekends+holiday) → warning
STALE_MACRO_DAYS = 6


def per_company_staleness(session: Session, max_lag_days: int = STALE_PRICE_DAYS
                          ) -> dict[str, int]:
    """{ticker: calendar days behind the universe} for names that stopped
    updating. Only index members count — a departed constituent is expected to
    go quiet and flagging it would be noise."""
    latest = session.scalar(select(func.max(PriceObservation.obs_date)))
    if latest is None:
        return {}
    rows = session.execute(
        select(Company.ticker, func.max(PriceObservation.obs_date))
        .join(PriceObservation, PriceObservation.company_id == Company.id)
        .where(Company.is_index_member == True)   # noqa: E712
        .group_by(Company.ticker)).all()
    out = {t: (latest - d).days for t, d in rows if (latest - d).days > max_lag_days}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def data_status(session: Session, verify_ledger: bool = False) -> dict:
    today = date.today()
    warnings: list[str] = []

    # ONE round trip for five aggregates. Each of these was a separate query,
    # and this endpoint issued 19 in total — none individually slow, but a
    # network database charges a few hundred milliseconds per ROUND TRIP, so the
    # query count was the latency. Scalar subqueries collapse them into a single
    # statement without changing a single number.
    n_companies, n_prices, latest_price, earliest_price, null_vol = session.execute(
        select(
            select(func.count(Company.id)).scalar_subquery(),
            select(func.count(PriceObservation.id)).scalar_subquery(),
            select(func.max(PriceObservation.obs_date)).scalar_subquery(),
            select(func.min(PriceObservation.obs_date)).scalar_subquery(),
            select(func.count(PriceObservation.id))
            .where(PriceObservation.volume.is_(None)).scalar_subquery(),
        )).one()
    price_stale = (today - latest_price).days if latest_price else None
    if price_stale is None:
        warnings.append("no price data ingested")
    elif price_stale > STALE_PRICE_DAYS:
        warnings.append(f"prices are {price_stale} days old — refresh recommended")

    # The global max above is a POOR freshness test on its own: one current name
    # makes the whole dataset read fresh while individual names sit frozen for
    # weeks. Measured per name, five Nifty-50 constituents were 17 sessions
    # behind and nothing in the system said so.
    stale_names = per_company_staleness(session)
    if stale_names:
        worst = ", ".join(f"{t} ({n}d)" for t, n in list(stale_names.items())[:5])
        warnings.append(
            f"{len(stale_names)} name(s) have stopped updating while the rest of "
            f"the universe moved on: {worst}"
            f"{' …' if len(stale_names) > 5 else ''}. They are excluded from the "
            "cross-sectional reference distribution until they refresh.")

    macro_rows = session.execute(
        select(MacroObservation.symbol, func.max(MacroObservation.obs_date),
               func.count(MacroObservation.id))
        .group_by(MacroObservation.symbol)).all()
    macro = [{"symbol": sym, "latest": str(latest), "rows": n,
              "staleness_days": (today - latest).days}
             for sym, latest, n in macro_rows]
    for m in macro:
        if m["staleness_days"] > STALE_MACRO_DAYS:
            warnings.append(f"macro series {m['symbol']} is {m['staleness_days']}d stale")

    n_filings = session.scalar(select(func.count(FilingPeriod.id))
                               .where(FilingPeriod.source == "yahoo"))
    filing_companies = session.scalar(
        select(func.count(func.distinct(FilingPeriod.company_id)))
        .where(FilingPeriod.source == "yahoo"))
    latest_fy = session.scalar(select(func.max(FilingPeriod.fiscal_year))
                               .where(FilingPeriod.source == "yahoo"))
    n_financial = session.scalar(select(func.count(Company.id))
                                 .where(Company.is_financial.is_(True)))

    br_count = session.scalar(select(func.count(BaseRateRecord.id)))
    br_computed = session.scalar(select(func.max(BaseRateRecord.computed_at)))
    studies_stale = bool(br_computed and latest_price
                         and br_computed.date() < latest_price)
    if br_count == 0:
        warnings.append("no base-rate studies computed — run studies in the Lab")
    elif studies_stale:
        warnings.append("base rates predate latest prices — recompute studies")

    from ..ingestion.vault import vault_stats
    vault = vault_stats()
    # Chain verification is O(n) BY DESIGN — it re-hashes every record from
    # genesis, and that is the feature, not an inefficiency to cache away
    # (caching it on ledger metadata silently defeated tamper detection when
    # tried). But it does not belong on every page load: it cost 5.8s of pure
    # Python here. Skipped by default and offered explicitly instead, which is
    # the fix the ledger module's own docstring prescribes.
    if verify_ledger:
        chain = ledger.verify_chain()
        if not chain.get("intact", False) and chain.get("records", 0) != 0:
            warnings.append("LEDGER CHAIN BROKEN — investigate immediately")
    else:
        chain = {"verified": False,
                 "records": ledger.record_count(),
                 "note": "hash chain not walked on this request — it is an O(n) "
                         "re-hash from genesis. Verify explicitly to check it."}

    # Quality score: decomposed, never a mystery number (Phase III rule)
    comp = {
        # Penalised by BREADTH of staleness as well as its age: a dataset where
        # 5% of names are frozen is not "fresh" merely because one name traded
        # today, which is exactly what the max-date-only score used to claim.
        "price_freshness": (0.0 if price_stale is None else
                            max(0.0, 1 - max(0, price_stale - 3) / 7)
                            * (1 - min(1.0, len(stale_names) / max(1, n_companies)))),
        "volume_completeness": 0.0 if not n_prices else 1 - (null_vol / n_prices),
        "fundamental_coverage": 0.0 if not n_companies else
        filing_companies / max(1, n_companies - n_financial),
        "studies_current": 0.0 if br_count == 0 else (0.6 if studies_stale else 1.0),
        "ledger_integrity": 1.0 if chain.get("intact", True) else 0.0,
    }
    quality = round(sum(comp.values()) / len(comp) * 100)

    return {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "provider": "yahoo (bootstrap tier — exchange sources planned, PHASE2 §3.2)",
        "quality_score": quality,
        "quality_components": {k: round(v, 3) for k, v in comp.items()},
        "warnings": warnings,
        "datasets": {
            "prices": {"rows": n_prices, "companies": n_companies,
                       "coverage": f"{earliest_price} → {latest_price}",
                       "latest": str(latest_price), "staleness_days": price_stale,
                       "null_volume_pct": round(null_vol / n_prices * 100, 2) if n_prices else None},
            "fundamentals": {"rows": n_filings, "companies_covered": filing_companies,
                             "latest_fy": latest_fy, "pit_grade": "reconstructed",
                             "financial_sector_excluded": n_financial},
            "macro": macro,
            "base_rates": {"records": br_count,
                           "computed_at": br_computed.isoformat() if br_computed else None,
                           "stale": studies_stale},
            "vault": vault,
            "ledger": chain,
        },
        "missing_datasets": ["delivery %", "shareholding pattern", "promoter pledges",
                             "insider trades", "quarterly statements",
                             "earnings calendar", "transcripts"],
        "registry": {status: sum(1 for h in REGISTRY.values() if h["status"] == status)
                     for status in sorted({h["status"] for h in REGISTRY.values()})},
    }


def refresh_stream(session: Session):
    """Generator of SSE events: every pipeline stage, visibly (Phase III).

    Bootstrap-aware (Vercel serverless: no background threads): an empty DB
    triggers the full 10-year ingest, fundamentals included, in per-chunk
    committed steps — if the platform's function limit cuts the stream, state
    is preserved and re-running the refresh resumes where it stopped.
    """
    def sse(stage: str, status: str, **detail):
        return ("data: " + json.dumps({"stage": stage, "status": status, **detail})
                + "\n\n")

    try:
        from ..ingestion.yahoo import (ingest_fundamentals, ingest_macro,
                                       ingest_prices, sync_universe)
        bootstrap = (session.scalar(select(func.count(PriceObservation.id))) or 0) == 0
        years = 10 if bootstrap else 1
        if bootstrap:
            yield sse("bootstrap", "running",
                      note="empty database — full 10y ingest; resumable if interrupted")

        yield sse("universe", "running")
        ids = sync_universe(session)
        yield sse("universe", "done", companies=len(ids))

        yield sse("downloading_prices", "running")
        n = ingest_prices(session, ids, years=years)
        yield sse("downloading_prices", "done", new_rows=n)

        yield sse("downloading_macro", "running")
        m = ingest_macro(session, years=years)
        yield sse("downloading_macro", "done", new_rows=m)

        needs_fundamentals = bootstrap or (
            session.scalar(select(func.count(FilingPeriod.id))
                           .where(FilingPeriod.source == "yahoo")) or 0) == 0
        if needs_fundamentals:
            tickers = list(ids)
            total = 0
            for i in range(0, len(tickers), 10):  # chunked: commits + progress
                chunk = {t: ids[t] for t in tickers[i:i + 10]}
                yield sse("fundamentals", "running",
                          progress=f"{min(i + 10, len(tickers))}/{len(tickers)}")
                total += ingest_fundamentals(session, chunk, pause=0.2)
            yield sse("fundamentals", "done", filings=total)

        yield sse("validating", "running")
        bad = session.scalar(select(func.count(PriceObservation.id))
                             .where(PriceObservation.close <= 0)) or 0
        yield sse("validating", "done", nonpositive_prices=bad)

        yield sse("running_studies", "running")
        from ..research.base_rates import run_all_studies
        rep = run_all_studies(session)
        yield sse("running_studies", "done", records=rep["records"])

        yield sse("scoring_claims", "running")
        scored = ledger.score_due_claims(session)
        checkpointed = ledger.score_interim_checkpoints(session)
        yield sse("scoring_claims", "done", scored=scored["scored"],
                  checkpoints_scored=checkpointed["checkpointed"])

        yield sse("publishing", "running")
        from .snapshot import build_universe_snapshot
        snap = build_universe_snapshot(session)  # views become single-row reads
        yield sse("publishing", "done", companies=len(snap["companies"]),
                  quality_score=data_status(session)["quality_score"])

        from .autopilot import get_config, run_autopilot
        if get_config(session)["enabled"]:
            yield sse("autopilot", "running")
            rep = run_autopilot(session)
            yield sse("autopilot", "done", entries=len(rep["entries"]),
                      exits=len(rep["exits"]))
        yield sse("pipeline", "complete")
    except Exception as e:  # surface, never swallow (failed refresh recovery)
        yield sse("pipeline", "failed", error=f"{type(e).__name__}: {e}")


def portfolio_risk(session: Session) -> dict:
    """Correlation matrix, heat, risk contribution, holding periods (Phase III
    portfolio monitor). Computed from the stored panel — no new data needed."""
    from .live import portfolio_state, _series
    book = portfolio_state(session)
    if not book["has_book"]:
        return {"has_book": False,
                "note": "No open positions — add transactions to activate the risk monitor."}
    companies = {c.id: c for c in session.scalars(select(Company)).all()}
    rets, vols, values = {}, {}, {}
    for cid, w in book["weights"].items():
        _, closes, _v = _series(session, cid)
        if len(closes) < 130:
            continue
        t = companies[cid].ticker
        rets[t] = [closes[i] / closes[i - 1] - 1
                   for i in range(len(closes) - 126, len(closes))]
        vols[t] = technical.realized_vol(closes).value
        values[t] = w

    tickers = sorted(rets)
    def corr(a, b):
        xa, xb = rets[a], rets[b]
        ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
        cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
        va = sum((x - ma) ** 2 for x in xa)
        vb = sum((y - mb) ** 2 for y in xb)
        return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None

    matrix = [[round(corr(a, b), 2) if a != b else 1.0 for b in tickers] for a in tickers]
    # naive risk contribution: weight × vol, normalized (stated as naive)
    contrib_raw = {t: values[t] * (vols[t] or 0) for t in tickers}
    total = sum(contrib_raw.values()) or 1
    return {
        "has_book": True,
        "as_of": date.today().isoformat(),
        "tickers": tickers,
        "correlation_matrix": matrix,
        "open_heat_pct": book["open_heat_pct"],
        "heat_budget_pct": 6.0,
        "weights_pct": {t: round(values[t] * 100, 1) for t in tickers},
        "vol_pct": {t: None if vols[t] is None else round(vols[t], 1) for t in tickers},
        "risk_contribution_pct": {t: round(v / total * 100, 1)
                                  for t, v in contrib_raw.items()},
        "caveats": ["126d Pearson correlations — regime-dependent, estimated with error",
                    "risk contribution = weight × vol, normalized (naive; no covariance credit)"],
    }


def company_memory(session: Session, company: Company) -> dict:
    """Everything the platform remembers about one name (Phase III research
    memory): theses, journal, every dossier ever issued, every scored claim."""
    theses = [t for t in session.scalars(
        select(Thesis).where(Thesis.company_id == company.id)
        .order_by(Thesis.created_at.desc())).all()]
    journal = session.scalars(
        select(JournalEntry).where(JournalEntry.company_id == company.id)
        .order_by(JournalEntry.created_at.desc())).all()
    records = ledger.read_all()
    dossiers = [r for r in records if r["kind"] == "dossier"
                and r["company"].get("ticker") == company.ticker]
    d_hashes = {d["hash"] for d in dossiers}
    scores = [r for r in records if r["kind"] == "score"
              and r.get("scores_dossier_hash") in d_hashes]
    checkpoints = [r for r in records if r["kind"] == "checkpoint"
                  and r.get("scores_dossier_hash") in d_hashes]
    return {
        "ticker": company.ticker,
        "theses": [{"id": t.id, "status": t.status, "statement": t.statement,
                    "created_at": t.created_at.isoformat(),
                    "review_date": t.review_date.isoformat() if t.review_date else None,
                    "assumptions": t.assumptions.splitlines(),
                    "invalidation_triggers": t.invalidation_triggers.splitlines()}
                   for t in theses],
        "journal": [{"id": j.id, "content": j.content, "cfa_topic": j.cfa_topic,
                     "created_at": j.created_at.isoformat()} for j in journal],
        "dossier_history": [{"created_at": d["created_at"], "verdict": d["verdict"],
                             "net_score": d["net_score"],
                             "conviction_band": d["conviction_band"],
                             "hash": d["hash"][:16],
                             "claim": d.get("claim")} for d in dossiers],
        "scored_claims": scores,
        "interim_checkpoints": checkpoints,
    }
