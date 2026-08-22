"""REST key-value backend — the storage that has no connection to drop.

WHY THIS EXISTS
---------------
Every production outage this platform has had traced to the SQL layer, not the
logic: Neon's metered transfer exhausting, Supabase's pooler dropping prepared
statements, two cold-start instances racing `create_all` into a pg_type
UniqueViolation. None of it was about the data — the durable state is tiny and
single-user — and all of it was about holding a *connection* to a *relational*
server from an ephemeral serverless function.

A REST KV (Upstash Redis, which Vercel KV also resells) removes the entire
class: each call is one stateless HTTPS request with a bearer token. No pool, no
prepared-statement cache, no schema to build, no idle-in-transaction timeout,
nothing to wake. It is "direct API and nothing else" applied to persistence.

This module is the transport only — get/set/del of opaque strings. `docstore`
layers the JSON-document semantics the app speaks on top, and chooses this
backend over the SQL one via EQUISENSE_STORE.

Credentials (either naming works; Vercel KV and Upstash expose the same API):
  KV_REST_API_URL         / UPSTASH_REDIS_REST_URL
  KV_REST_API_TOKEN       / UPSTASH_REDIS_REST_TOKEN

With neither set, a LOCAL fallback is used so dev and tests never need the
network: an on-disk store under EQUISENSE_KV_DIR, or pure in-memory if that is
unset. The fallback is API-identical, so the same code path is exercised
offline as in production.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

_log = logging.getLogger("equisense.kv")

_REST_URL = (os.environ.get("KV_REST_API_URL")
             or os.environ.get("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
_REST_TOKEN = (os.environ.get("KV_REST_API_TOKEN")
               or os.environ.get("UPSTASH_REDIS_REST_TOKEN") or "")

# Network timeout. A KV read on the hot path must never hang a page load longer
# than the serve-stale budget; a few seconds is already pathological for a
# single REST round trip, so fail fast and let the caller fall back.
TIMEOUT_S = 6.0


def rest_configured() -> bool:
    return bool(_REST_URL and _REST_TOKEN)


def pg_configured() -> bool:
    """Postgres KV is available when the deployment has a hosted database. This is
    the zero-provisioning backend: it reuses DATABASE_URL, so the migration works
    with no Upstash account. Local/SQLite dev and tests (no DATABASE_URL) fall
    through to the file/memory store instead, keeping them offline and isolated."""
    return bool(os.environ.get("DATABASE_URL"))


def persistent() -> bool:
    """True when writes survive a serverless cold start — i.e. a REST KV or the
    Postgres table, but NOT the per-instance memory/file fallback. The price-panel
    switch keys off this: a panel with nowhere durable to live would read empty on
    every cold start."""
    return rest_configured() or pg_configured()


def backend_name() -> str:
    if rest_configured():
        return "rest_kv"
    if pg_configured():
        return "postgres_kv"
    return "local_dir" if os.environ.get("EQUISENSE_KV_DIR") else "memory"


# --------------------------------------------------------------- local fallback

_MEM: dict[str, str] = {}
_MEM_LOCK = threading.Lock()


def _local_dir() -> Optional[Path]:
    d = os.environ.get("EQUISENSE_KV_DIR")
    if not d:
        return None
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_name(key: str) -> str:
    # keys are our own short slugs, but never let one escape the dir
    return key.replace("/", "_").replace("..", "_")


def _local_get(key: str) -> Optional[str]:
    d = _local_dir()
    if d is None:
        with _MEM_LOCK:
            return _MEM.get(key)
    f = d / (_safe_name(key) + ".json")
    try:
        return f.read_text() if f.exists() else None
    except OSError:
        return None


def _local_set(key: str, value: str) -> None:
    d = _local_dir()
    if d is None:
        with _MEM_LOCK:
            _MEM[key] = value
        return
    (d / (_safe_name(key) + ".json")).write_text(value)


def _local_del(key: str) -> None:
    d = _local_dir()
    if d is None:
        with _MEM_LOCK:
            _MEM.pop(key, None)
        return
    f = d / (_safe_name(key) + ".json")
    try:
        f.unlink(missing_ok=True)
    except OSError:
        pass


def _reset_local_for_tests() -> None:
    with _MEM_LOCK:
        _MEM.clear()


# ------------------------------------------------------------- postgres backend

def _pg_get(key: str) -> Optional[str]:
    from sqlalchemy import text
    from .db import engine
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT value FROM kv_store WHERE key = :k"),
                               {"k": key}).first()
            return row[0] if row is not None else None
    except Exception as exc:                          # noqa: BLE001
        _log.warning("pg kv get %s failed: %s", key, exc)
        return None


def _pg_set(key: str, value: str) -> None:
    from sqlalchemy import text
    from .db import engine
    # Portable upsert: ON CONFLICT works on both Postgres and modern SQLite.
    stmt = ("INSERT INTO kv_store (key, value, updated_at) "
            "VALUES (:k, :v, CURRENT_TIMESTAMP) "
            "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = CURRENT_TIMESTAMP")
    with engine.begin() as conn:
        conn.execute(text(stmt), {"k": key, "v": value})


def _pg_del(key: str) -> None:
    from sqlalchemy import text
    from .db import engine
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM kv_store WHERE key = :k"), {"k": key})
    except Exception as exc:                          # noqa: BLE001
        _log.warning("pg kv del %s failed: %s", key, exc)


# ----------------------------------------------------------------- REST backend

def _command(args: list) -> object:
    """One Upstash REST command. Body is a JSON array [CMD, arg, ...]; the reply
    is {"result": ...} or {"error": ...}. Raises on transport or Redis error so
    a WRITE never silently vanishes — read-side callers catch and fall back."""
    import requests                                   # declared dep; imported lazily

    r = requests.post(_REST_URL, headers={"Authorization": f"Bearer {_REST_TOKEN}"},
                      data=json.dumps(args), timeout=TIMEOUT_S)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"KV {args[0]} failed: {body['error']}")
    return body.get("result")


# ------------------------------------------------------------------- public API

def get(key: str) -> Optional[str]:
    """Value for key, or None. Never raises: a read failure degrades to 'absent'
    so the caller can fall back to recompute rather than error a page."""
    if rest_configured():
        try:
            val = _command(["GET", key])
            return val if val is None else str(val)
        except Exception as exc:                      # noqa: BLE001
            _log.warning("kv get %s failed: %s", key, exc)
            return None
    if pg_configured():
        return _pg_get(key)
    return _local_get(key)


def set(key: str, value: str) -> None:               # noqa: A001 - mirrors Redis verb
    """Store value. RAISES on failure — a dropped write to user or ledger state
    is data loss, and the caller must see it rather than believe it persisted."""
    if rest_configured():
        _command(["SET", key, value])
    elif pg_configured():
        _pg_set(key, value)
    else:
        _local_set(key, value)


def delete(key: str) -> None:
    if rest_configured():
        try:
            _command(["DEL", key])
        except Exception as exc:                      # noqa: BLE001
            _log.warning("kv del %s failed: %s", key, exc)
    elif pg_configured():
        _pg_del(key)
    else:
        _local_del(key)


def ping() -> bool:
    """Liveness probe for the status surface. True if the backend answers."""
    if rest_configured():
        try:
            return _command(["PING"]) == "PONG"
        except Exception:                             # noqa: BLE001
            return False
    if pg_configured():
        try:
            _pg_get("__ping__")
            return True
        except Exception:                             # noqa: BLE001
            return False
    return True
