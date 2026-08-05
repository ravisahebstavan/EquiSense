"""Append-only, hash-chained decision ledger (§10.1).

Every dossier is pre-registered here at creation: JSON-lines, each record
carrying the SHA-256 of the previous record — cheap tamper-evidence. Records
are never edited; a superseding dossier references its predecessor.

Claim scoring (§10.1): each dossier embeds a falsifiable claim — direction
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
# where the filesystem is ephemeral — README §3.1). Same hash chain either way.


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


def record_count() -> int:
    """How many records exist, WITHOUT reading or re-hashing them.

    The status page wants the size of the ledger, not proof of its integrity;
    conflating the two made every page load pay for a full O(n) verification.
    """
    if STORAGE == "db":
        from sqlalchemy import func, select
        with get_session() as s:
            return int(s.scalar(select(func.count(LedgerRecord.seq))) or 0)
    if not LEDGER_PATH.exists():
        return 0
    with LEDGER_PATH.open() as f:
        return sum(1 for line in f if line.strip())


def verify_chain(records: list[dict] | None = None) -> dict:
    """Recompute the hash chain; any edit to any historical line breaks it.

    `records` lets a caller that has already loaded the ledger pass it in rather
    than paying for a second full read — /api/live/ledger renders the tail AND
    verifies, and used to fetch the whole ledger twice to do it.

    Deliberately NOT memoised. Caching on (record count, last hash) looked like
    an easy win and silently destroyed the tamper detection: editing a field in
    record 0 changes neither the count nor the last record's stored hash, so a
    cached "intact" was returned for a corrupted ledger. A tamper-evidence check
    cannot be keyed on metadata the tamperer controls — the O(n) pass IS the
    feature. If this ever becomes a bottleneck the fix is to stop calling it on
    every page load, not to make it lie faster.
    """
    recs = read_all() if records is None else records
    prev = GENESIS
    for i, rec in enumerate(recs):
        # copy rather than pop: popping mutated the caller's records, stripping
        # "hash" from the very dicts /api/live/ledger then renders
        body = {k: v for k, v in rec.items() if k != "hash"}
        stored_hash = rec.get("hash")
        if body.get("prev_hash") != prev:
            return {"intact": False, "broken_at": i,
                    "reason": "prev_hash mismatch", "records": len(recs)}
        recomputed = hashlib.sha256(
            json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        if recomputed != stored_hash:
            return {"intact": False, "broken_at": i,
                    "reason": "content altered", "records": len(recs)}
        prev = stored_hash
    return {"intact": True, "records": len(recs)}


def register_dossier(dossier: dict) -> dict:
    """Pre-register a dossier and its falsifiable claim. Non-abstain verdicts
    get a scoreable claim; abstentions are registered too (abstention has a
    track record — chronic abstention on winners is a measured cost, §10.1)."""
    synth = dossier["synthesis"]
    horizon_days = dossier.get("claim_horizon_days", 126)
    score_after = (date.today() + timedelta(days=int(horizon_days * 1.5))).isoformat()
    if synth["verdict"] in ("long_candidate", "avoid_short_candidate"):
        from .research.learning import calibrated_magnitude, calibrated_probability
        p, p_basis = calibrated_probability(synth["net_score"])
        pred_mag, mag_basis = calibrated_magnitude(synth["net_score"], horizon_days)
        # interim checkpoint: an early read on the same claim, roughly a quarter
        # of the way to the horizon (min 21 trading days) — "T vs T+checkpoint"
        # tracking, scored well before the full claim matures (see
        # score_interim_checkpoints below)
        checkpoint_days = max(21, round(horizon_days / 4))
        claim = {
            "type": "directional_excess",
            "direction": 1 if synth["verdict"] == "long_candidate" else -1,
            "horizon_days": horizon_days,
            "checkpoint_days": checkpoint_days,
            "stated_probability": p,
            "probability_basis": p_basis,
            "predicted_excess_pct": pred_mag,
            "magnitude_basis": mag_basis,
            "benchmark": "universe_median_forward_return",
            "score_after": score_after,
            "entry_price": dossier["company"].get("price"),
        }
    else:
        # §10.2 (A10 fix): abstentions carry counterfactual claims —
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
            realized_pct = round(realized * 100, 2)
            predicted_pct = rec["claim"].get("predicted_excess_pct")
            forecast_error = (None if predicted_pct is None
                              else round(realized_pct - predicted_pct, 2))
            score_rec = _write({
                "kind": "score",
                "claim_type": "directional_excess",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "scores_dossier_hash": rec["hash"],
                "company": rec["company"]["ticker"],
                "realized_excess_pct": realized_pct,
                "hit": hit, "stated_probability": p, "brier": round(brier, 4),
                "predicted_excess_pct": predicted_pct,
                "forecast_error_pct": forecast_error,
                "abs_forecast_error_pct": None if forecast_error is None else abs(forecast_error),
            })
        results.append(score_rec)
    return {"scored": len(results), "results": results}


def score_interim_checkpoints(session: Session, as_of: date | None = None) -> dict:
    """Early read on open directional claims: at ~checkpoint_days (roughly a
    quarter of the way to the claim's horizon), compare realized-so-far excess
    against the horizon prediction pro-rated to the elapsed fraction. This is
    the literal 'prediction made at T for T+checkpoint, compared once T+
    checkpoint actually arrives' loop — a much earlier, more frequent signal
    than waiting for the full horizon (score_due_claims) to elapse, so drift
    from a stale regime or a broken thesis shows up fast instead of six
    months later. Never overwrites or substitutes for the final score."""
    as_of = as_of or date.today()
    records = read_all()
    checkpointed = {r["scores_dossier_hash"] for r in records if r["kind"] == "checkpoint"}
    tickers = {c.ticker: c.id for c in session.scalars(select(Company)).all()}
    results = []
    for rec in records:
        if rec["kind"] != "dossier" or not rec.get("claim"):
            continue
        claim = rec["claim"]
        if claim.get("type") != "directional_excess" or rec["hash"] in checkpointed:
            continue
        created = date.fromisoformat(rec["created_at"][:10])
        checkpoint_days = claim.get("checkpoint_days") or max(21, round(claim["horizon_days"] / 4))
        checkpoint_target = created + timedelta(days=int(checkpoint_days * 1.45))
        if as_of < checkpoint_target:
            continue
        cid = tickers.get(rec["company"]["ticker"])
        if cid is None:
            continue
        realized = _excess_return(session, cid, created, checkpoint_target, tickers.values())
        if realized is None:
            continue
        realized_pct = round(realized * 100, 2)
        predicted_pct = claim.get("predicted_excess_pct")
        expected_so_far_pct = (None if predicted_pct is None else
                               round(predicted_pct * (checkpoint_days / claim["horizon_days"]), 2))
        forecast_error = (None if expected_so_far_pct is None
                          else round(realized_pct - expected_so_far_pct, 2))
        direction = claim["direction"]
        on_track = (realized_pct > 0) == (direction > 0) if direction != 0 else None
        results.append(_write({
            "kind": "checkpoint",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scores_dossier_hash": rec["hash"],
            "company": rec["company"]["ticker"],
            "elapsed_days": checkpoint_days,
            "horizon_days": claim["horizon_days"],
            "realized_so_far_pct": realized_pct,
            "expected_so_far_pct": expected_so_far_pct,
            "forecast_error_pct": forecast_error,
            "on_track": on_track,
        }))
    return {"checkpointed": len(results), "results": results}


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
    """Reliability summary of all scored claims (§10.1), including the
    wrongful-abstention rate (§10.2). Honest even when thin."""
    scores = [r for r in read_all() if r["kind"] == "score"]
    directional = [s for s in scores if s.get("claim_type") != "abstention_counterfactual"
                   and s.get("hit") is not None]
    abstentions = [s for s in scores if s.get("claim_type") == "abstention_counterfactual"]
    checkpoints = [r for r in read_all() if r["kind"] == "checkpoint"]
    out = {"scored_claims": len(directional),
           "scored_abstentions": len(abstentions)}
    if directional:
        hits = sum(1 for s in directional if s["hit"])
        out.update(hit_rate=round(hits / len(directional), 3),
                   mean_stated_probability=round(
                       sum(s["stated_probability"] for s in directional) / len(directional), 3),
                   mean_brier=round(sum(s["brier"] for s in directional) / len(directional), 4))
        mag_errors = [s["abs_forecast_error_pct"] for s in directional
                     if s.get("abs_forecast_error_pct") is not None]
        if mag_errors:
            out.update(
                mean_abs_forecast_error_pct=round(sum(mag_errors) / len(mag_errors), 2),
                rmse_forecast_error_pct=round(
                    (sum(e ** 2 for e in mag_errors) / len(mag_errors)) ** 0.5, 2))
    if checkpoints:
        on_track = [c for c in checkpoints if c.get("on_track") is not None]
        if on_track:
            out["interim_on_track_rate"] = round(
                sum(1 for c in on_track if c["on_track"]) / len(on_track), 3)
        checkpoint_errors = [c["forecast_error_pct"] for c in checkpoints
                             if c.get("forecast_error_pct") is not None]
        if checkpoint_errors:
            out["mean_abs_interim_forecast_error_pct"] = round(
                sum(abs(e) for e in checkpoint_errors) / len(checkpoint_errors), 2)
        out["interim_checkpoints"] = len(checkpoints)
    if abstentions:
        wrongful = sum(1 for s in abstentions if s.get("wrongful_abstention"))
        out.update(wrongful_abstention_rate=round(wrongful / len(abstentions), 3),
                   wrongful_threshold_pct=WRONGFUL_ABSTENTION_PCT,
                   mean_abstained_excess_pct=round(
                       sum(s["realized_excess_pct"] for s in abstentions) / len(abstentions), 2))
    out["note"] = ("Calibrated when hit_rate ≈ mean_stated_probability, and when "
                   "mean_abs_forecast_error_pct trends down over time. Weights "
                   "unlock at ≥150 scored claims per family (N_eff-counted). "
                   "Interim checkpoints (~1/4 horizon) surface prediction drift "
                   "long before a claim's full horizon matures. Abstention now "
                   "has a price: the wrongful-abstention rate.")
    return out
