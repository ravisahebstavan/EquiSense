"""Hosted-mode behavior: DB-backed ledger/vault (ephemeral-filesystem safe)
and the single-user token gate."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense.db import Base


@pytest.fixture
def mem_sessions(monkeypatch):
    """Fresh in-memory DB standing in for hosted Postgres; patches every
    get_session the storage backends resolve."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    SL = sessionmaker(bind=eng, expire_on_commit=False)
    import equisense.db as D
    import equisense.ledger as L
    monkeypatch.setattr(D, "get_session", lambda: SL())
    monkeypatch.setattr(L, "get_session", lambda: SL())
    return SL


def test_db_ledger_chain_survives_no_filesystem(mem_sessions, monkeypatch):
    import equisense.ledger as L
    monkeypatch.setattr(L, "STORAGE", "db")
    dossier = {"synthesis": {"verdict": "long_candidate", "net_score": 0.3,
                             "conviction_band": "low"},
               "company": {"ticker": "T1", "price": 100.0}}
    r1 = L.register_dossier(dossier)
    r2 = L.register_dossier({**dossier, "company": {"ticker": "T2", "price": 50.0}})
    assert r2["prev_hash"] == r1["hash"]          # chain continues across rows
    assert L.verify_chain() == {"intact": True, "records": 2}
    records = L.read_all()
    assert [r["company"]["ticker"] for r in records if r["kind"] == "dossier"] == ["T1", "T2"]


def test_db_vault_roundtrip(mem_sessions, monkeypatch):
    import equisense.ingestion.vault as V
    monkeypatch.setattr(V, "STORAGE", "db")
    h1 = V.store_raw({"a": 1}, "prov", "ep1")
    h2 = V.store_raw({"a": 1}, "prov", "ep2")   # identical content
    assert h1 == h2
    import json
    assert json.loads(V.read_raw(h1)) == {"a": 1}
    stats = V.vault_stats()
    assert stats["unique_blobs"] == 1 and stats["artifacts"] == 2
    assert stats["backend"] == "db"


def test_auth_gate(monkeypatch):
    from fastapi.testclient import TestClient
    from equisense.api.app import app
    monkeypatch.setenv("EQUISENSE_ACCESS_TOKEN", "s3cret")
    c = TestClient(app)  # no lifespan needed — middleware is independent
    assert c.get("/api/live/vault").status_code == 401
    assert "access token" in c.get("/").text            # login page, not the app
    ok = c.get("/api/live/vault", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    # query-param login sets the session cookie
    r = c.get("/?token=s3cret")
    assert r.status_code == 200
    assert c.cookies.get("eqs_token") == "s3cret"
    assert c.get("/api/live/vault").status_code == 200  # cookie now suffices


def test_auth_disabled_locally(monkeypatch):
    from fastapi.testclient import TestClient
    from equisense.api.app import app
    monkeypatch.delenv("EQUISENSE_ACCESS_TOKEN", raising=False)
    c = TestClient(app)
    assert c.get("/api/live/vault").status_code == 200  # frictionless dev