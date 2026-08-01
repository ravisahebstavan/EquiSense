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
    # Base.metadata only knows tables whose module has been imported, so models
    # must be imported BEFORE create_all — otherwise ledger_records/vault_blobs
    # simply are not created. In a full-suite run another test imports models
    # first and this passes by accident; in isolation it failed with
    # "no such table: ledger_records". ensure_schema() does the same import for
    # the same reason.
    import equisense.models  # noqa: F401 - registers tables on Base.metadata

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

# --------------------------------------------------- markets UI surface

def test_markets_endpoints_are_registered():
    """The Markets view calls these; a rename would break the page silently."""
    from equisense.api.app import app
    paths = {r.path for r in app.routes}
    for p in ("/api/markets/derivatives/{symbol}", "/api/markets/simulate",
              "/api/markets/relationships", "/api/markets/valuation",
              "/api/markets/rates", "/api/markets/flow",
              "/api/markets/sources", "/api/markets/position-risk",
              "/api/markets/delivery/{ticker}"):
        assert p in paths, f"{p} missing — the Markets view would 404"


def test_markets_view_is_wired_into_the_frontend():
    """Nav entry, route branch and every render function must all exist, or the
    engines behind them are unreachable from the site — which is the definition
    of dead weight."""
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "web"
    js = (web / "app.js").read_text()
    html = (web / "index.html").read_text()
    assert 'data-route="markets"' in html
    assert 'name === "markets"' in js
    for fn in ("viewMarkets", "renderDerivatives", "renderMarketRisk",
               "renderRelations", "renderValuationRegime", "renderFlow"):
        assert f"function {fn}" in js, f"{fn} not defined"


def test_markets_view_uses_real_helpers_not_invented_ones():
    """Guards a class of bug that only shows at runtime: calling a helper or
    CSS class that does not exist renders a blank panel with no error."""
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "web"
    js = (web / "app.js").read_text()
    css = (web / "style.css").read_text()
    block = js[js.index("markets view"):js.index("routing */", js.index("markets view"))]
    import re
    # every helper the block calls must be defined in the file
    for helper in ("fmtN", "esc", "signed", "skeleton", "api"):
        assert re.search(rf"(const|function) {helper}\b", js), f"{helper} undefined"
    assert "fmt(" not in block.replace("fmtN(", ""), "fmt() does not exist; it is fmtN()"
    # every class the block uses must be styled somewhere
    used = set(re.findall(r'class="([a-z0-9 _-]+)"', block))
    names = {n for grp in used for n in grp.split()}
    for n in names:
        assert f".{n}" in css or n in ("primary",), f'CSS class "{n}" is not styled'


def test_ic_panel_is_defined_not_just_called():
    """Caught a real silent failure: a string-replace anchored on the wrong
    function signature inserted the CALL but not the DEFINITION, leaving a tab
    that throws at runtime and renders nothing."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    assert "async function renderIcPanel(body)" in js, "definition missing"
    assert 'section === "ic"' in js, "route branch missing"
    assert '"ic", "Signal IC"' in js, "tab entry missing"


def test_every_view_function_called_in_the_router_exists():
    """Generalises the above: any render/view function the router dispatches to
    must actually be defined."""
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    called = set(re.findall(r"await ((?:view|render)[A-Za-z]+)\(", js))
    defined = set(re.findall(r"(?:async )?function ((?:view|render)[A-Za-z]+)\(", js))
    missing = called - defined
    assert not missing, f"called but never defined: {sorted(missing)}"


def test_ic_endpoints_are_registered():
    from equisense.api.app import app
    paths = {r.path for r in app.routes}
    assert "/api/live/ic" in paths
    assert "/api/live/ic/run" in paths


def test_factor_panel_is_defined_routed_and_tabbed():
    """Same guard as the IC panel, for the same reason: a string-replace that
    inserts the CALL but not the DEFINITION leaves a tab that throws at runtime
    and renders a blank page with no server-side error to notice."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    assert "async function renderFactorPanel(body)" in js, "definition missing"
    assert 'section === "factors"' in js, "route branch missing"
    assert '"factors", "Factor P&L"' in js, "tab entry missing"


def test_factor_panel_uses_real_helpers_and_styled_classes():
    """A helper or CSS class that does not exist renders a blank panel silently."""
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "web"
    js = (web / "app.js").read_text()
    css = (web / "style.css").read_text()
    start = js.index("async function renderFactorPanel(body)")
    block = js[start:js.index("/* ============", start)]
    for helper in ("fmtN", "esc", "signed", "api"):
        assert re.search(rf"(const|function) {helper}\b", js), f"{helper} undefined"
        # and if the block calls it, it must be the real name
    assert "fmt(" not in block.replace("fmtN(", ""), "fmt() does not exist; it is fmtN()"
    used = set(re.findall(r'class="([a-z0-9 _&;-]+)"', block))
    names = {n for grp in used for n in grp.split()}
    for n in names:
        assert f".{n}" in css or n in ("primary", "pos", "neg"), \
            f'CSS class "{n}" is not styled'


def test_factor_portfolio_endpoints_are_registered():
    from equisense.api.app import app
    paths = {r.path for r in app.routes}
    assert "/api/live/factor-portfolio" in paths
    assert "/api/live/factor-portfolio/run" in paths


def test_research_cli_exposes_every_heavy_study():
    """The Lab's run buttons POST to endpoints that do minutes of work, which a
    serverless request cannot survive. The CLI is the supported path, so it has
    to cover all three studies and cache to the SAME keys the read endpoints
    look at — otherwise running it locally would leave the site still empty."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "equisense" / "research" / "__main__.py").read_text()
    for study in ("ic", "factors", "baserates"):
        assert f'"{study}"' in src, f"{study} missing from the CLI"
    assert '"ic_studies"' in src, "IC results must cache where GET /live/ic reads"
    assert '"factor_studies"' in src, "factor results must cache where GET reads"


def test_lab_run_buttons_warn_about_the_serverless_timeout():
    """A button that always fails on the deployed site is worse than no button;
    the guidance has to travel with it."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    assert js.count("python -m equisense.research all") == 2, (
        "both the IC and factor run buttons need the CLI fallback note")
    assert "time out" in js


def test_every_api_route_is_reachable_from_the_ui_or_explicitly_exempt():
    """The rule this repo states in status.py: 'no backend capability may hide
    in JSON.' An endpoint nothing calls is either dead weight or a feature the
    user cannot reach — both are defects. Exemptions are listed explicitly so
    adding one is a deliberate act, not an oversight."""
    import re
    from pathlib import Path

    from equisense.api.app import app

    # machine-to-machine or streaming endpoints with no UI surface
    EXEMPT = {
        "/api/cron/refresh",            # scheduler entry point
        "/api/live/refresh/stream",     # SSE, driven by the refresh button
        "/api/live/vault",              # same payload as /live/status datasets.vault
        "/api/markets/position-risk",   # POST calculator, invoked from markets view
    }
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    calls = set(re.findall(r'api\(\s*[`"\']([^`"\'?]+)', js))
    calls |= {re.sub(r"\$\{[^}]+\}", "*", t.split("?")[0])
              for t in re.findall(r'api\(\s*`([^`]+)`', js)}
    called = {c if c.startswith("/api") else "/api" + c for c in calls}

    missing = []
    for r in app.routes:
        p = getattr(r, "path", "")
        if not p.startswith("/api") or p in EXEMPT:
            continue
        norm = re.sub(r"\{[^}]+\}", "*", p)
        if not any(norm.rstrip("/") == c.rstrip("/") for c in called):
            missing.append(p)
    assert not missing, f"backend capabilities with no UI: {sorted(missing)}"


def test_data_health_surfaces_storage_and_source_reachability():
    """Neon's free tier is a hard 512 MB ceiling, and every archive fetch fails
    CLOSED — so an unreachable source looks exactly like a quiet market day.
    Both were reachable only as raw JSON."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    assert "async function renderDataExtras(host)" in js
    assert '"/storage"' in js and '"/markets/sources"' in js
    assert "renderDataExtras(" in js.split("async function renderDataExtras")[0], \
        "defined but never called"


def test_company_page_shows_delivery_percentage():
    """Built, tested, and completely unreachable from the UI until now."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    assert "async function renderDeliveryPanel(host, ticker)" in js
    assert "/markets/delivery/" in js
    assert "renderDeliveryPanel(slot" in js, "defined but never called"
