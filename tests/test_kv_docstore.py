"""KV transport + document-store facade.

These lock the contract the Postgres → REST KV migration rests on: the facade
must behave identically whichever backend is selected, and the KV transport's
local fallback must be API-identical to the REST one so offline tests exercise
the real code path.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense import docstore, kv
from equisense.db import Base


@pytest.fixture
def db_session():
    import equisense.models  # noqa: F401 - registers tables before create_all
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


@pytest.fixture(autouse=True)
def _clean_kv(monkeypatch):
    monkeypatch.delenv("EQUISENSE_KV_DIR", raising=False)
    monkeypatch.delenv("KV_REST_API_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    kv._reset_local_for_tests()
    yield
    kv._reset_local_for_tests()


def test_local_kv_roundtrips_and_deletes():
    assert kv.get("missing") is None
    kv.set("a", "hello")
    assert kv.get("a") == "hello"
    kv.delete("a")
    assert kv.get("a") is None


def test_local_kv_persists_to_a_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUISENSE_KV_DIR", str(tmp_path))
    kv.set("k", "v")
    # a fresh in-memory map still sees it, because it is on disk
    kv._reset_local_for_tests()
    assert kv.get("k") == "v"
    assert kv.backend_name() == "local_dir"


def test_docstore_kv_backend_roundtrips(monkeypatch):
    monkeypatch.setenv("EQUISENSE_STORE", "kv")
    assert docstore.backend() == "kv"
    docstore.put(None, "cfg", json.dumps({"x": 1}), as_of="2026-08-01")
    doc = docstore.get(None, "cfg")
    assert doc is not None
    assert json.loads(doc.payload) == {"x": 1}
    assert doc.as_of == "2026-08-01"
    docstore.delete(None, "cfg")
    assert docstore.get(None, "cfg") is None


def test_docstore_defaults_to_db_backend(monkeypatch):
    monkeypatch.delenv("EQUISENSE_STORE", raising=False)
    assert docstore.backend() == "db"


def test_docstore_db_backend_uses_app_snapshots(db_session, monkeypatch):
    """The default path must remain the app_snapshots table, byte-for-byte."""
    monkeypatch.delenv("EQUISENSE_STORE", raising=False)
    docstore.put(db_session, "widget", json.dumps({"n": 7}), as_of="2026-08-02")
    from equisense.models import AppSnapshot
    row = db_session.get(AppSnapshot, "widget")
    assert row is not None and json.loads(row.payload) == {"n": 7}
    assert row.as_of == "2026-08-02"
    doc = docstore.get(db_session, "widget")
    assert json.loads(doc.payload) == {"n": 7}
