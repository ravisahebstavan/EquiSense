"""JSON-document persistence, backend-agnostic.

The app keeps a handful of small keyed JSON documents — the universe snapshot,
the strategy-backtest cache, the momentum-risk blob, IC and factor studies, the
paper-trading config, the autopilot config and last-run marker. Every one is a
`key -> (as_of, payload)` row and nothing more; none needs a relational query.

Historically they lived in the `app_snapshots` table, read through the ORM. This
facade lifts them off that assumption so the SAME call sites work against either
backend, chosen once by EQUISENSE_STORE:

    db  (default) — the app_snapshots table, exactly as before. Zero behaviour
                    change; the SQL path is untouched until you opt out of it.
    kv            — the REST KV (equisense.kv). No connection, no schema, no
                    pooler — the storage that cannot drop a connection because
                    it never holds one.

This is Phase 1 of the Postgres → REST KV migration: the document layer moves
first because it is already document-shaped and fully regenerable, so a KV
misconfiguration can only cost a recompute, never data. User state and the
hash-chained ledger migrate in later phases behind the same switch.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

from . import kv


@dataclass
class Doc:
    payload: str                       # the JSON string the caller stored
    as_of: str                         # freshness stamp (a date/str the caller set)


def backend() -> str:
    return "kv" if os.environ.get("EQUISENSE_STORE", "db").lower() == "kv" else "db"


# KV keys are namespaced so the document layer can share one KV database with the
# later user/ledger phases without collision.
def _k(key: str) -> str:
    return f"doc:{key}"


def get(session, key: str) -> Optional[Doc]:
    """The stored document, or None if absent. `session` is accepted for a
    uniform signature and used only by the db backend."""
    if backend() == "kv":
        raw = kv.get(_k(key))
        if raw is None:
            return None
        try:
            obj = json.loads(raw)
            return Doc(payload=obj["payload"], as_of=obj.get("as_of", ""))
        except (ValueError, KeyError, TypeError):
            return None
    from .models import AppSnapshot
    row = session.get(AppSnapshot, key)
    return None if row is None else Doc(payload=row.payload, as_of=row.as_of)


def put(session, key: str, payload: str, as_of: Optional[str] = None) -> None:
    """Create or overwrite the document. `as_of` defaults to today. Propagates a
    write failure (KV.set raises) so a lost write is never mistaken for success."""
    stamp = as_of or str(date.today())
    if backend() == "kv":
        kv.set(_k(key), json.dumps({"as_of": stamp, "payload": payload}))
        return
    from .models import AppSnapshot
    row = session.get(AppSnapshot, key)
    if row is None:
        session.add(AppSnapshot(key=key, as_of=stamp, payload=payload))
    else:
        row.as_of = stamp
        row.payload = payload
    session.commit()


def delete(session, key: str) -> None:
    if backend() == "kv":
        kv.delete(_k(key))
        return
    from .models import AppSnapshot
    row = session.get(AppSnapshot, key)
    if row is not None:
        session.delete(row)
        session.commit()
