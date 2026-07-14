"""Raw payload vault (PHASE2_ARCHITECTURE §3.1 — the A6 fix).

Content-addressed, immutable, append-only archive of data as received from
providers, stored BEFORE normalization. The canonical store becomes
disposable: it can always be rebuilt as f(vault, transform_version).

Layout: data/vault/<sha256[:2]>/<sha256>.json.gz + data/vault/index.jsonl
(one line per artifact: hash, provider, endpoint, fetched_at, meta).

Honesty note: the current provider adapter receives parsed DataFrames from
its client library, not HTTP bytes — so "raw" here means "as handed to us,
serialized losslessly, before any EquiSense transform." Capturing true wire
bytes is a provider-adapter upgrade recorded in the blueprint, not silently
claimed here.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import DATA_DIR, STORAGE

VAULT_DIR = Path(DATA_DIR) / "vault"
INDEX_PATH = VAULT_DIR / "index.jsonl"
# STORAGE == "db" → blobs live in vault_blobs/vault_fetches tables (hosted
# deployments have ephemeral filesystems — DEPLOYMENT.md).


def _serialize(payload: Any) -> bytes:
    """Lossless, deterministic serialization of provider payloads."""
    if hasattr(payload, "to_json"):  # pandas objects
        body = payload.to_json(orient="split", date_format="iso")
    else:
        body = json.dumps(payload, sort_keys=True, default=str)
    return body.encode()


def store_raw(payload: Any, provider: str, endpoint: str,
              meta: dict | None = None, session=None) -> str:
    """Vault a payload; returns its content hash. Idempotent — identical
    content vaults once (a re-fetch of unchanged data costs an index line,
    not a blob). In db-storage mode, pass the caller's `session` so the vault
    write joins the caller's transaction instead of opening a competing
    connection (SQLite would deadlock; Postgres would merely be untidy)."""
    raw = _serialize(payload)
    digest = hashlib.sha256(raw).hexdigest()
    if STORAGE == "db":
        from ..db import get_session
        from ..models import VaultBlob, VaultFetch
        own = session is None
        s = get_session() if own else session
        if s.get(VaultBlob, digest) is None:
            s.add(VaultBlob(hash=digest, blob=gzip.compress(raw)))
        s.add(VaultFetch(hash=digest, provider=provider, endpoint=endpoint,
                         nbytes=len(raw), meta=json.dumps(meta or {})))
        if own:
            s.commit()
            s.close()
        else:
            s.flush()  # caller's commit persists it atomically with the ingest
        return digest
    blob = VAULT_DIR / digest[:2] / f"{digest}.json.gz"
    if not blob.exists():
        blob.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(blob, "wb") as f:
            f.write(raw)
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("a") as f:
        f.write(json.dumps({
            "hash": digest, "provider": provider, "endpoint": endpoint,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "bytes": len(raw), "meta": meta or {},
        }) + "\n")
    return digest


def read_raw(digest: str) -> bytes:
    if STORAGE == "db":
        from ..db import get_session
        from ..models import VaultBlob
        with get_session() as s:
            row = s.get(VaultBlob, digest)
        if row is None:
            raise KeyError(digest)
        return gzip.decompress(row.blob)
    blob = VAULT_DIR / digest[:2] / f"{digest}.json.gz"
    with gzip.open(blob, "rb") as f:
        return f.read()


def vault_stats() -> dict:
    if STORAGE == "db":
        from sqlalchemy import func, select
        from ..db import get_session
        from ..models import VaultBlob, VaultFetch
        with get_session() as s:
            artifacts = s.scalar(select(func.count(VaultFetch.id))) or 0
            blobs = s.scalar(select(func.count(VaultBlob.hash))) or 0
            nbytes = s.scalar(select(func.coalesce(func.sum(VaultFetch.nbytes), 0)))
            providers = sorted(x[0] for x in s.execute(
                select(VaultFetch.provider).distinct()).all())
        return {"artifacts": artifacts, "unique_blobs": blobs, "bytes": int(nbytes),
                "providers": providers, "backend": "db"}
    if not INDEX_PATH.exists():
        return {"artifacts": 0, "unique_blobs": 0, "bytes": 0}
    entries = [json.loads(l) for l in INDEX_PATH.open() if l.strip()]
    blobs = {e["hash"] for e in entries}
    return {"artifacts": len(entries), "unique_blobs": len(blobs),
            "bytes": sum(e["bytes"] for e in entries),
            "providers": sorted({e["provider"] for e in entries})}
