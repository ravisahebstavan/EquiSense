"""Append-only, hash-chained decision ledger (RESEARCH_BLUEPRINT §13.4).

Every dossier is pre-registered here at creation: JSON-lines, each record
carrying the SHA-256 of the previous record — cheap tamper-evidence. Records
are never edited; a superseding dossier references its predecessor.

Claim scoring (§12.1): each dossier embeds a falsifiable claim — direction
over a horizon vs. the universe median. Once the horizon expires, score()
computes the realized outcome from the price store and Brier-scores the
stated probability. Scores are appended to the same chain.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import DATA_DIR, STORAGE, get_session
from .models import Company, LedgerRecord, PriceObservation

LEDGER_PATH = Path(DATA_DIR) / "ledger" / "dossiers.jsonl"
GENESIS = "equisense-genesis"
WRONGFUL_ABSTENTION_PCT = 5.0  # abstained and forward excess beat this → wrongful (§7.1)

# Storage backend: "file" (JSONL, local default) or "db" (hosted deployments,
# where the filesystem is ephemeral — DEPLOYMENT.md). Same hash chain either way.


def _last_hash() -> str:
    records = read_all()
    return records[-1]["hash"] if records else GENESIS


def _write(record: dict) -> dict:
    record["prev_hash"] = _last_hash()
    payload = json.dumps(record, sort_keys=True, default=str)
    record["hash"] = hashlib.sha256(payload.encode()).hexdigest()
    if STORAGE == "db":
        with get_session() as s:
            s.add(LedgerRecord(kind=record.get("kind", "?"), hash=record["hash"],
                               payload=json.dumps(record, default=str)))
            s.commit()
    else:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER_PATH.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    return record


def read_all() -> list[dict]:
    if STORAGE == "db":
        from sqlalchemy import select
        with get_session() as s:
            rows = s.scalars(select(LedgerRecord).order_by(LedgerRecord.seq)).all()
        return [json.loads(r.payload) for r in rows]
    if not LEDGER_PATH.exists():
        return []
    with LEDGER_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def verify_chain() -> dict:
    """Recompute the hash chain; any edit to any historical line breaks it."""
    prev = GENESIS
    for i, rec in enumerate(read_all()):
        stored_hash = rec.pop("hash")
        if rec.get("prev_hash") != prev:
            return {"intact": False, "broken_at": i, "reason": "prev_hash mismatch"}
        recomputed = hashlib.sha256(
            json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()
        if recomputed != stored_hash:
            return {"intact": False, "broken_at": i, "reason": "content altered"}
        prev = stored_hash
    return {"intact": True, "records": len(read_all())}


def register_dossier(dossier: dict) -> dict:
    """Pre-register a dossier and its falsifiable claim. Non-abstain verdicts
    get a scoreable claim; abstentions are registered too (abstention has a
    track record — chronic abstention on winners is a measured cost, §15.6)."""
    synth = dossier["synthesis"]
    horizon_days = dossier.get("claim_horizon_days", 126)
    score_after = (date.today() + timedelta(days=int(horizon_days * 1.5))).isoformat()
    if synth["verdict"] in ("long_candidate", "avoid_short_candidate"):
        from .research.learning import calibrated_probability
        p, p_basis = calibrated_probability(synth["net_score"])
        claim = {
            "type": "directional_excess",
            "direction": 1 if synth["verdict"] == "long_candidate" else -1,
            "horizon_days": horizon_days,
            "stated_probability": p,
            "probability_basis": p_basis,
            "benchmark": "universe_median_forward_return",
            "score_after": score_after,
            "entry_price": dossier["company"].get("price"),
        }
    else:
        # PHASE2 §7.1 (A10 fix): abstentions carry counterfactual claims —
        # "I declined; what would a long have returned?" Chronic wrongful
        # abstention becomes measurable instead of a free pass.
        claim = {
            "type": "abstention_counterfactual",
            "direction": 0,
            "horizon_days": horizon_days,
            "stated_probability": None,
            "benchmark": "universe_median_forward_return",
            "score_after": score_after,
            "entry_price": dossier["company"].get("price"),
        }
    record = {
        "kind": "dossier",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "company": dossier["company"],
        "verdict": synth["verdict"],
        "net_score": synth["net_score"],
        "conviction_band": synth["conviction_band"],
        "cluster_scores": synth.get("cluster_scores", {}),  # learning attribution
        "claim": claim,
        "dossier_sha256": hashlib.sha256(
            json.dumps(dossier, sort_keys=True, default=str).encode()).hexdigest(),
    }
    return _write(record)


def _stated_probability(synth: dict) -> float:
    """Map net score to a stated hit probability, anchored at 0.5. Deliberately
    timid until calibration exists — the ledger will tell us if even this is
    overconfident."""
    return round(min(0.65, 0.5 + abs(synth["net_score"]) * 0.25), 3)


def score_due_claims(session: Session, as_of: date | None = None) -> dict:
    """Score every unscored dossier claim whose horizon has elapsed, from the
    price store. Appends 'score' records to the chain."""
    as_of = as_of or date.today()
    records = read_all()
    scored_ids = {r["scores_dossier_hash"] for r in records if r["kind"] == "score"}
    tickers = {c.ticker: c.id for c in session.scalars(select(Company)).all()}
    results = []
    for rec in records:
        if rec["kind"] != "dossier" or not rec.get("claim"):
            continue
        if rec["hash"] in scored_ids:
            continue
        created = date.fromisoformat(rec["created_at"][:10])
        horizon = rec["claim"]["horizon_days"]
        target = created + timedelta(days=int(horizon * 1.45))  # calendar ≈ trading days
        if as_of < target:
            continue
        cid = tickers.get(rec["company"]["ticker"])
        if cid is None:
            continue
        realized = _excess_return(session, cid, created, target, tickers.values())
        if realized is None:
            continue
        direction = rec["claim"]["direction"]
        if direction == 0:  # abstention counterfactual (A10)
            score_rec = _write({
                "kind": "score",
                "claim_type": "abstention_counterfactual",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "scores_dossier_hash": rec["hash"],
                "company": rec["company"]["ticker"],
                "realized_excess_pct": round(realized * 100, 2),
                "wrongful_abstention": realized * 100 > WRONGFUL_ABSTENTION_PCT,
                "hit": None, "stated_probability": None, "brier": None,
            })
        else:
            hit = (realized > 0) == (direction > 0)
            p = rec["claim"]["stated_probability"]
            brier = (p - (1.0 if hit else 0.0)) ** 2
            score_rec = _write({
                "kind": "score",
                "claim_type": "directional_excess",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "scores_dossier_hash": rec["hash"],
                "company": rec["company"]["ticker"],
                "realized_excess_pct": round(realized * 100, 2),
                "hit": hit, "stated_probability": p, "brier": round(brier, 4),
            })
        results.append(score_rec)
    return {"scored": len(results), "results": results}


def _excess_return(session: Session, cid: int, start: date, end: date,
                   all_ids) -> float | None:
    def px(company_id: int, on: date) -> float | None:
        row = session.scalars(select(PriceObservation)
                              .where(PriceObservation.company_id == company_id,
                                     PriceObservation.obs_date <= on)
                              .order_by(PriceObservation.obs_date.desc())).first()
        return row.close if row else None

    p0, p1 = px(cid, start), px(cid, end)
    if not p0 or not p1:
        return None
    stock_ret = p1 / p0 - 1
    peer_rets = []
    for other in all_ids:
        q0, q1 = px(other, start), px(other, end)
        if q0 and q1:
            peer_rets.append(q1 / q0 - 1)
    if not peer_rets:
        return None
    peer_rets.sort()
    median = peer_rets[len(peer_rets) // 2]
    return stock_ret - median


def calibration_report() -> dict:
    """Reliability summary of all scored claims (§12.1), including the
    wrongful-abstention rate (PHASE2 §7.1). Honest even when thin."""
    scores = [r for r in read_all() if r["kind"] == "score"]
    directional = [s for s in scores if s.get("claim_type") != "abstention_counterfactual"
                   and s.get("hit") is not None]
    abstentions = [s for s in scores if s.get("claim_type") == "abstention_counterfactual"]
    out = {"scored_claims": len(directional),
           "scored_abstentions": len(abstentions)}
    if directional:
        hits = sum(1 for s in directional if s["hit"])
        out.update(hit_rate=round(hits / len(directional), 3),
                   mean_stated_probability=round(
                       sum(s["stated_probability"] for s in directional) / len(directional), 3),
                   mean_brier=round(sum(s["brier"] for s in directional) / len(directional), 4))
    if abstentions:
        wrongful = sum(1 for s in abstentions if s.get("wrongful_abstention"))
        out.update(wrongful_abstention_rate=round(wrongful / len(abstentions), 3),
                   wrongful_threshold_pct=WRONGFUL_ABSTENTION_PCT,
                   mean_abstained_excess_pct=round(
                       sum(s["realized_excess_pct"] for s in abstentions) / len(abstentions), 2))
    out["note"] = ("Calibrated when hit_rate ≈ mean_stated_probability. Weights "
                   "unlock at ≥150 scored claims per family (N_eff-counted). "
                   "Abstention now has a price: the wrongful-abstention rate.")
    return out
