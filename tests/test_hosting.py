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


def test_dashboard_does_not_ship_sparklines_it_cannot_draw():
    """The payload carried all 395 companies WITH 40-point sparklines — 471 KB
    to render 8 rows, a quarter of it arrays the view discards. The full ranking
    still ships (it is cheap and callers count on it); only the price history is
    limited to rows that can display one."""
    import inspect

    from equisense.api import services
    src = inspect.getsource(services.dashboard)
    assert "top: int" in src, "no bound on how many sparklines are serialised"
    assert 'r["spark"] = []' in src


def test_dashboard_still_returns_the_full_ranking():
    """Trimming the sparklines must not trim the ranking — counts and any
    future view depend on the complete list."""
    import inspect

    from equisense.api import services
    src = inspect.getsource(services.dashboard)
    assert '"ranked": ranked[' not in src, "the ranking itself was truncated"


def test_status_does_not_walk_the_hash_chain_by_default():
    """Chain verification is O(n) BY DESIGN — it re-hashes every record from
    genesis, and that is the feature (caching it on ledger metadata silently
    defeated tamper detection when tried). It just does not belong on every page
    load: it cost 5.8s of pure Python. Skipped by default, offered explicitly —
    the fix the ledger module's own docstring prescribes."""
    import inspect

    from equisense.api import status
    src = inspect.getsource(status.data_status)
    assert "verify_ledger" in src
    assert "record_count()" in src, "must report size without re-hashing"

    from equisense import ledger
    cnt = inspect.getsource(ledger.record_count)
    assert "read_all" not in cnt, "record_count must not read the payloads"


def test_status_still_verifies_when_asked():
    import inspect

    from equisense.api import status
    src = inspect.getsource(status.data_status)
    assert "ledger.verify_chain()" in src, "explicit verification path removed"
    assert "LEDGER CHAIN BROKEN" in src


def test_a_hosted_deployment_never_seeds_demo_data():
    """A hosted app that falls back to SQLite has lost its database. Seeding
    demo rows on top makes the failure INVISIBLE — the site renders, charts
    draw, and 9 fake companies read as real signals. That is worse than an error
    page. Observed live: the Vercel deployment served 54 rows across 9 companies
    while the real database held 1,005,145 bars across 501."""
    import inspect

    from equisense.api import app as A
    src = inspect.getsource(A)
    assert "IS_HOSTED_ENV" in src
    boot = inspect.getsource(A._startup_boot)
    assert "not IS_HOSTED_ENV" in boot, "a hosted fallback would still be seeded"


def test_startup_failure_cannot_take_down_the_site():
    """On serverless an exception during init is FUNCTION_INVOCATION_FAILED —
    the whole site 500s, including the pages that would explain why. A free
    Postgres tier that auto-suspends makes that routine, not an edge case. A
    site that loads and says "no database" beats one that will not load."""
    import inspect

    from equisense.api import app as A
    life = inspect.getsource(A.lifespan)
    assert "IS_HOSTED_ENV" in life, "a hosted boot must skip the database entirely"
    assert "STARTUP_ERROR" in life
    dep = inspect.getsource(A.db)
    assert "ensure_schema" in dep, "schema must be ensured lazily on first request"
    assert '_SCHEMA_READY["done"] = True' in dep, "must not retry on every request"


def test_schema_check_fast_paths_instead_of_migrating_every_cold_start():
    """The full path runs create_all over ~20 tables plus has_table and
    get_columns per soft migration plus index DDL — dozens of round trips at a
    few hundred milliseconds each against a database that must first be woken.
    Measured locally: 394ms full, 4.8ms fast path, an 82x difference, and on a
    network database the gap is far larger."""
    import inspect

    from equisense import db
    src = inspect.getsource(db.ensure_schema)
    assert "SCHEMA_VERSION" in src
    body = src[src.index('"""', src.index('"""') + 3):]   # skip the docstring
    assert "return" in body.split("create_all")[0], "no early exit before create_all"


def test_status_shouts_when_the_database_is_missing():
    """Without this the only symptom is implausibly small row counts, which read
    as ingestion lag rather than total loss of the database."""
    import inspect

    from equisense.api import status
    src = inspect.getsource(status.data_status)
    assert "CONFIGURATION ERROR" in src
    assert "DATABASE_URL" in src
    assert "warnings.insert(0" in src, "must be the FIRST warning, not buried"


def test_writes_are_refused_on_ephemeral_hosted_storage():
    """The most expensive failure in this codebase, and it ran silently for the
    life of the deployment.

    Without DATABASE_URL a serverless box falls back to SQLite under /tmp, which
    is PER-INSTANCE and wiped between invocations. Every write appeared to
    succeed — refresh fetched genuine Yahoo data, paper trades were accepted,
    ledger entries were hash-chained — and the next request landed on a
    different instance that had never seen any of it. The observed symptom was a
    site reporting 54 price rows while refresh kept "working".

    It matters most for the LEDGER, which is the forward-testing record: losing
    it silently means nine months of calibration evidence never accumulates
    while appearing to.
    """
    import inspect

    from equisense.api import app as A
    src = inspect.getsource(A._reject_if_ephemeral)
    assert "IS_HOSTED_ENV" in src and "IS_SQLITE" in src
    assert "503" in src
    assert "refused rather" in src, "must say the write was refused, not lost"


def test_every_persisting_endpoint_is_guarded():
    """A write path that skips the guard reintroduces the silent loss."""
    import inspect

    from equisense.api import app as A
    src = inspect.getsource(A)
    assert src.count("_reject_if_ephemeral()") >= 4, (
        "refresh, refresh-stream, paper trades and the dossier ledger write "
        "must all refuse on ephemeral storage")


def test_a_missing_database_driver_cannot_make_the_app_unimportable():
    """create_engine imports the DBAPI eagerly, so a missing psycopg raises at
    MODULE IMPORT, before any handler exists. The whole site then 500s on every
    path including /favicon.ico, with no page left able to explain why — which
    is exactly what a deployment hit: `ModuleNotFoundError: No module named
    'psycopg'` while the requirement sat in requirements.txt, because the
    builder had skipped the extras-bracket syntax."""
    import inspect

    from equisense import db
    src = inspect.getsource(db)
    assert "ENGINE_ERROR" in src
    assert "_build_engine" in src
    head = src[:src.index("SessionLocal")]
    assert "try:" in head and "except Exception" in head, (
        "engine creation is unguarded; a missing driver kills the whole app")


def test_requirements_name_the_postgres_driver_without_extras():
    """`psycopg[binary]` resolved to nothing on the deployment while every other
    package installed. Naming the binary wheel directly removes the extras
    resolution that failed."""
    from pathlib import Path
    req = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    # comments explain the fix and mention the broken form, so read the actual
    # requirement lines only
    lines = [ln.strip() for ln in req.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    assert any(ln.startswith("psycopg-binary") for ln in lines), \
        "the binary wheel is not named directly"
    assert not any("[" in ln for ln in lines if ln.startswith("psycopg")), \
        "extras syntax is what silently failed on the deployment"


def test_the_postgres_driver_is_a_base_dependency_not_an_extra():
    """It sat under [project.optional-dependencies] while the hosted build
    installed base deps only, so every other package landed and this one
    silently did not. The app then died at import the moment DATABASE_URL was
    set. A driver the deployment cannot boot without is not optional."""
    from pathlib import Path
    pt = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    # Parse by SECTION HEADER at line start, and read requirement strings only.
    # A substring search matched the explanatory comment above the dependency —
    # which quotes the very section name being searched for — and truncated the
    # slice before reaching it. The comment broke the test that guards it.
    lines, section, base = pt.splitlines(), None, []
    for ln in lines:
        st = ln.strip()
        if st.startswith("[") and st.endswith("]"):
            section = st
            continue
        if st.startswith("#") or not st:
            continue
        if section == "[project]" and st.startswith('"'):
            base.append(st)
    assert any("psycopg" in r for r in base), (
        f"the driver is still not in base dependencies: {base}")


def test_a_driver_failure_is_not_reported_as_a_missing_env_var():
    """Both conditions land on SQLite, so reporting both made the warnings
    contradict each other and pointed at the wrong fix."""
    import inspect

    from equisense.api import status
    src = inspect.getsource(status.data_status)
    assert 'not ENGINE_ERROR.get("detail")' in src
    assert "_has_url" in src


def test_the_storage_panel_reads_the_keys_the_endpoint_actually_returns():
    """The first version rendered "undefined" against a perfectly correct
    payload: it read the shape of storage_report() while the ROUTE wraps it as
    {"report": {...}}. Guarding that the panel exists was not enough — it has to
    read keys the endpoint really emits.

    So this calls the real function rather than grepping its source, which is
    what makes it able to catch a shape change at all.
    """
    from pathlib import Path

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import equisense.models  # noqa: F401 - registers tables
    from equisense.db import Base
    from equisense.ingestion.nse_archive import storage_report

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    report = storage_report(sessionmaker(bind=eng)())

    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text()
    block = js[js.index("async function renderDataExtras"):]
    block = block[:block.index("/* =====")]

    assert "store.report" in block, "the panel ignores the endpoint's wrapper"

    # storage_report is DIALECT-DEPENDENT: on-disk size is a Postgres-only
    # query, so this SQLite fixture yields row counts and retention only. Both
    # shapes must render — assuming the Postgres one unconditionally is what
    # produced "undefined" on the live site.
    assert set(report) >= {"rows", "retention"}, "the SQLite shape changed"
    for key in ("rows", "retention"):
        assert f"rep.{key}" in block, f"the panel never reads '{key}'"
    for key in ("total", "tables"):
        assert f"rep.{key}" in block, (
            f"the panel never reads '{key}', which Postgres does return")
    # and it must not read keys that never existed on either side
    for ghost in ("database_size", "largest_tables"):
        assert ghost not in report, f"'{ghost}' unexpectedly exists now"
        assert ghost not in block, f"panel still reads the invented key '{ghost}'"


def _status_db():
    """Tiny live universe + a graveyard of departed names, matching production:
    prices retained for the dead, fundamentals only ever for index members."""
    from datetime import date

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import equisense.models  # noqa: F401 - registers tables
    from equisense.db import Base
    from equisense.models import Company, FilingPeriod, PriceObservation

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    today = date.today()
    for i in range(10):
        c = Company(ticker=f"LIVE{i}", name=f"L{i}",
                    sector="Financials" if i < 2 else "IT",
                    is_financial=i < 2, is_index_member=True)
        s.add(c)
        s.flush()
        s.add(PriceObservation(company_id=c.id, obs_date=today, close=100.0, volume=1))
        if i >= 2:
            s.add(FilingPeriod(company_id=c.id, period="FY2026", fiscal_year=2026,
                               source="yahoo", scope="consolidated"))
    for i in range(400):
        c = Company(ticker=f"DEAD{i}", name=f"D{i}", sector="IT", is_index_member=False)
        s.add(c)
        s.flush()
        s.add(PriceObservation(company_id=c.id, obs_date=today, close=50.0, volume=1))
    s.commit()
    return s


def test_coverage_is_measured_against_the_live_universe_not_the_graveyard():
    """Departed constituents are retained ON PURPOSE (survivorship-corrected
    backtests need the dead names) and fundamentals are never fetched for them.
    Counting them in the denominator made production report 13.8% coverage
    while every live name was in fact covered — a gauge that can never reach
    green, which just teaches you to ignore the number."""
    from equisense.api.status import data_status

    st = data_status(_status_db())
    assert st["datasets"]["prices"]["companies"] == 410, "all names keep their history"
    assert st["datasets"]["prices"]["index_members"] == 10, "live universe is surfaced"
    assert st["quality_components"]["fundamental_coverage"] == 1.0, (
        "the live universe is fully covered, so coverage must read 1.0")


def test_status_shouts_when_a_hosted_deployment_has_no_access_token(monkeypatch):
    """The auth gate disables itself when EQUISENSE_ACCESS_TOKEN is unset — by
    design, so local dev stays frictionless. On a hosted deployment that same
    silence leaves the real portfolio, the ledger and every write endpoint open
    to anyone with the URL, and nothing anywhere says so."""
    from equisense.api import app as app_mod
    from equisense.api.status import data_status

    monkeypatch.delenv("EQUISENSE_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(app_mod, "IS_HOSTED_ENV", True)
    warnings = data_status(_status_db())["warnings"]
    assert warnings, "a hosted deployment with no token must warn"
    assert "ACCESS TOKEN" in warnings[0].upper(), (
        f"must be the FIRST warning, not buried; got {warnings[0]!r}")

    monkeypatch.setenv("EQUISENSE_ACCESS_TOKEN", "a-real-secret")
    assert not any("ACCESS TOKEN" in w.upper()
                   for w in data_status(_status_db())["warnings"]), (
        "must go quiet once the token is configured")


def test_ai_reports_a_missing_key_as_configuration_not_a_crash(monkeypatch):
    """The SDK's raw exception repr ("TypeError: Could not resolve
    authentication method...") was rendered straight into the UI, which reads
    as a crash and names nothing the user can act on."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from equisense.ai import narrator

    out = narrator._grounded_call("sys", {"x": 1}, "do it")
    assert out["available"] is False
    assert "ANTHROPIC_API_KEY" in out["reason"]
    assert "TypeError" not in out["reason"], "no raw exception reprs in the UI"
    assert out["context"] == {"x": 1}, "grounding context still returned"


def test_cron_does_irreplaceable_work_before_recomputable_work():
    """Measured on the real deployment: the cron ran 300s and was killed with a
    504 every day, committing nothing past the point of death. Because the
    recomputable work (prices, macro, base rates, snapshot) ran FIRST, the only
    stages that never happened were the ones that can never be recovered — the
    daily NSE archive and option surface publish one file per day and cannot be
    backfilled, and forecasts are what the entire learning loop unlocks from.
    Production proved it: 2 stored IV observations and zero scored claims.
    """
    import inspect

    from equisense.api.app import cron_refresh

    src = inspect.getsource(cron_refresh)
    order = {}
    for stage in ("vol_surface", "nse_archives", "forecasts",
                  "base_rate_records", "snapshot"):
        idx = src.find(f'stage("{stage}"')
        assert idx != -1, f"stage {stage!r} is no longer run by the cron"
        order[stage] = idx

    for irreplaceable in ("vol_surface", "nse_archives"):
        for recomputable in ("base_rate_records", "snapshot"):
            assert order[irreplaceable] < order[recomputable], (
                f"{irreplaceable} (cannot be backfilled) must run before "
                f"{recomputable} (recomputable any time)")

    # Acting on the book and protecting the database are not recomputable
    # tomorrow: a stop-loss that did not fire today did not fire, and retention
    # that did not run lets a 0.5 GB tier fill. Base rates are 10-year
    # statistics and yesterday's are not meaningfully worse, so they are the
    # correct thing to sacrifice when the budget runs short.
    for time_sensitive in ("autopilot", "pruned"):
        idx = src.find(f'stage("{time_sensitive}"')
        assert idx != -1, f"{time_sensitive} is no longer run by the cron"
        assert idx < order["base_rate_records"], (
            f"{time_sensitive} must run BEFORE base_rate_records — at 500 "
            "names studies cost 112s and pushed it past the budget, so the "
            "book stopped being managed to keep a statistic one day fresher")
    assert order["forecasts"] < order["base_rate_records"], (
        "registering forecasts is the learning loop; it cannot sit behind the "
        "most expensive recomputable stage")


def test_cron_refuses_to_start_work_it_cannot_finish():
    """A stage that starts near the platform's function limit gets killed
    mid-flight, so the cron must stop STARTING work well before it."""
    import inspect

    from equisense.api import app as app_mod

    assert app_mod.CRON_BUDGET_S < 300, (
        "budget must leave headroom under vercel.json maxDuration=300")
    src = inspect.getsource(app_mod.cron_refresh)
    assert "CRON_BUDGET_S" in src, "the budget must actually be enforced"
    assert "time budget exhausted" in src, "skipping must be reported, not silent"
    assert "s.commit()" in src, (
        "each stage must commit, or a later stall undoes everything before it")


def test_snapshot_does_not_load_the_whole_price_table():
    """The cron died at Vercel's 300s limit every day and stage timings put the
    whole cost here: everything else finished in 14.2s, then the snapshot ate
    the remaining ~285s.

    The snapshot only ever indexes these by a CURRENT index member, but the
    bulk loaders selected entire tables — on the real deployment that pulled
    all 1,005,395 price rows over a network Postgres to use the ~10% belonging
    to the 50 live constituents. Departed names must keep their history (the
    survivorship-corrected backtests read it through load_price_panel, which is
    separate and deliberately unfiltered), so the fix is to scope the read, not
    to delete the rows.
    """
    from datetime import date, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import equisense.models  # noqa: F401 - registers tables
    from equisense.api.snapshot import _bulk_prices
    from equisense.db import Base
    from equisense.models import Company, PriceObservation

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()

    live = Company(ticker="LIVE", name="Live", sector="IT", is_index_member=True)
    dead = Company(ticker="DEAD", name="Dead", sector="IT", is_index_member=False)
    s.add_all([live, dead])
    s.flush()
    start = date(2026, 1, 1)
    for i in range(80):
        d = start + timedelta(days=i)
        s.add(PriceObservation(company_id=live.id, obs_date=d, close=100.0, volume=1))
        s.add(PriceObservation(company_id=dead.id, obs_date=d, close=50.0, volume=1))
    s.commit()

    loaded = _bulk_prices(s)
    assert live.id in loaded, "the live universe must still be loaded in full"
    assert len(loaded[live.id][0]) == 80
    assert dead.id not in loaded, (
        "a departed constituent's price history must not be pulled into the "
        "snapshot — that read is what blew the cron's function budget")

    # and the history itself is untouched, because the backtests need the dead
    assert s.query(PriceObservation).filter_by(company_id=dead.id).count() == 80


def test_forecasts_are_registered_even_when_everything_abstains(monkeypatch):
    """The learning loop was structurally dead in production: 50 names scanned,
    0 long candidates, 0 forecasts registered, 0 scored claims — so every
    calibrated capability stayed provisional forever.

    register_daily_forecasts iterated `candidates`, which is deliberately
    long-only AND gate-filtered. On a universe where abstention is the modal
    correct output that list is routinely empty, so nothing was ever recorded —
    even though the ledger has full abstention-counterfactual machinery and the
    function's own docstring promises abstentions are registered too.
    """
    from equisense.api import autopilot

    reviewed = [
        {"id": 1, "ticker": "AAA", "verdict": "abstain", "net_score": -0.4,
         "conviction_band": "low", "data_suspect": False},
        {"id": 2, "ticker": "BBB", "verdict": "avoid_short_candidate",
         "net_score": -0.9, "conviction_band": "medium", "data_suspect": False},
        {"id": 3, "ticker": "CCC", "verdict": "abstain", "net_score": 0.1,
         "conviction_band": "low", "data_suspect": True},   # must be skipped
    ]
    # imported INSIDE the function, so it must be patched at its source module
    import equisense.api.candidates as cand_mod
    monkeypatch.setattr(cand_mod, "qualified_candidates",
                        lambda *a, **k: {"candidates": [], "reviewed": reviewed})

    built = []

    class _Co:
        def __init__(self, tid):
            self.id = tid

    monkeypatch.setattr(autopilot, "Company", _Co, raising=False)

    def fake_build(session, company):
        built.append(company.id)
        return {"synthesis": {"verdict": "abstain"},
                "ledger": {"hash": f"h{company.id}" * 8}}

    import equisense.api.live as live_mod
    import equisense.ledger as L
    monkeypatch.setattr(live_mod, "build_dossier", fake_build)
    monkeypatch.setattr(L, "read_all", lambda: [])

    class _S:
        def get(self, _model, tid):
            return _Co(tid)

    out = autopilot.register_daily_forecasts(_S(), top_n=6)

    tickers = [r["ticker"] for r in out["registered"]]
    assert "AAA" in tickers and "BBB" in tickers, (
        "abstain and avoid verdicts must still be registered — that is the "
        f"whole calibration record; got {out}")
    assert "CCC" not in tickers, "a data-suspect name must not become a claim"
    assert any("CCC" in s for s in out["skipped"])


def test_refresh_stream_registers_forecasts_like_the_cron_does():
    """Clicking Refresh ran the whole pipeline AND the autopilot but never
    recorded a prediction, so the calibration ledger only grew on days the
    server-side cron itself succeeded. While the cron was timing out that
    meant it never grew at all, and no amount of manual refreshing helped."""
    import inspect

    from equisense.api.status import refresh_stream

    src = inspect.getsource(refresh_stream)
    assert "register_daily_forecasts" in src, (
        "the manual refresh must register forecasts, exactly as the cron does")
    assert src.index("register_daily_forecasts") < src.index("score_due_claims"), (
        "forecasts are registered BEFORE scoring so a claim made today is in "
        "the chain and starts its horizon now")


def test_realign_touches_no_provider_and_recomputes_no_study():
    """Sync exists because Refresh is the wrong tool for "is what I'm looking
    at consistent with the database?". If it quietly grew into a second full
    pipeline it would be a multi-minute button wearing a seconds-long label,
    and people would stop pressing either one."""
    import inspect

    from equisense.api.app import live_realign

    src = inspect.getsource(live_realign)
    for forbidden in ("ingest_prices", "ingest_macro", "sync_universe",
                      "run_all_studies", "nse_backfill", "capture_vol_surface"):
        assert forbidden not in src, (
            f"realign must not {forbidden} — that is Refresh's job")
    for required in ("build_universe_snapshot", "register_daily_forecasts",
                     "score_due_claims", "run_autopilot"):
        assert required in src, f"realign must re-derive {required}"


def test_quote_poll_does_not_resync_the_universe_every_time():
    """Index membership changes on reshuffles, not on a five-minute timer.
    The quote poll re-fetched NSE's constituent CSV and re-upserted every
    company on EVERY call, for every open tab — most of the endpoint's latency,
    and a pointless repeated load on the exchange's published file."""
    import inspect

    from equisense.api.app import live_quotes

    src = inspect.getsource(live_quotes)
    assert "universe_sync_day" in src, "membership sync must be throttled per day"
    assert src.index("universe_sync_day") < src.index("sync_universe("), (
        "the throttle has to be checked BEFORE syncing, or it saves nothing")


def test_quote_polling_backs_off_when_the_exchange_is_closed():
    """Once NSE closes, today's bar is final: further polls re-download the
    same numbers, report 'updated 0, inserted 0', and burn a serverless
    invocation to redraw identical figures."""
    src = (__import__("pathlib").Path("web/app.js")).read_text()
    assert "QUOTE_INTERVAL_CLOSED_MS" in src and "QUOTE_INTERVAL_OPEN_MS" in src
    assert "setInterval(quoteLoop" not in src, (
        "a fixed interval cannot respect the exchange clock — quoteLoop must "
        "reschedule itself from cache.market.open")


def test_every_get_endpoint_actually_executes():
    """Reachable-from-the-UI is not the same as WORKS.

    /api/markets/transmission shipped with a NameError on its first line of
    real work — it referenced PriceObservation without importing it — and every
    existing guard passed, because they checked that the route was wired to a
    button, never that calling it returned anything. A 500 in production was
    the first feedback. This closes that gap: every parameterless GET is
    invoked, and anything that raises fails here instead of there.
    """
    from fastapi.routing import APIRoute
    from fastapi.testclient import TestClient

    from equisense.api.app import app

    # Endpoints that legitimately need an argument or mutate state are covered
    # by their own tests; this is the smoke net for the rest.
    SKIP = {"/api/live/refresh/stream"}

    broken = []
    with TestClient(app) as c:
        for r in app.routes:
            if not isinstance(r, APIRoute) or "GET" not in r.methods:
                continue
            if "{" in r.path or r.path in SKIP or not r.path.startswith("/api"):
                continue
            resp = c.get(r.path)
            if resp.status_code >= 500:
                broken.append(f"{r.path} -> {resp.status_code}")
    assert not broken, "GET endpoints raising server errors: " + "; ".join(broken)


def test_cron_refreshes_prices_incrementally_not_a_full_year_every_day():
    """Measured at 500 names: pulling a full year of bars daily cost 155s of a
    300s budget to obtain, on almost every name, a single new bar — and left
    nothing for the stages behind it, so retention stopped running.

    refresh_quotes sizes its window to the furthest-behind name. Names with NO
    history are a separate, bounded backfill job, because _refresh_period
    deliberately ignores them and a universe switch can introduce hundreds at
    once.
    """
    import inspect

    from equisense.api import app as app_mod

    src = inspect.getsource(app_mod.cron_refresh)
    assert "refresh_quotes(s, ids)" in src, (
        "the daily price stage must be an incremental refresh")
    assert "ingest_prices(s, ids, years=1)" not in src, (
        "a blanket one-year re-pull of the WHOLE universe per day does not "
        "scale with universe size (macro's years=1 is 11 series and is fine)")
    assert "BACKFILL_PER_RUN" in src, "new names must be backfilled in bounded chunks"
    assert app_mod.BACKFILL_PER_RUN <= 100


def test_frontend_stops_polling_a_hidden_tab_and_handles_session_expiry():
    """Two deployment-side failures that the engine tests cannot see.

    A hidden tab cannot show a price, so polling one spends a serverless
    invocation to update pixels nobody is looking at — and a user with the
    dashboard and the trading desk both open doubles it for no benefit.

    And the access cookie is 90 days, which guarantees it expires mid-session
    eventually. Left unhandled, api() throws "401: unauthorized" and it renders
    as cryptic red text on whichever panel happened to ask, so the app looks
    broken rather than logged out.
    """
    import pathlib

    src = pathlib.Path("web/app.js").read_text()

    assert "visibilitychange" in src, "polling must pause when the tab is hidden"
    assert "if (document.hidden) { quoteTimer = null; return; }" in src, (
        "scheduling must refuse to arm a timer for a hidden tab")
    assert "else if (!quoteTimer) quoteLoop();" in src, (
        "returning to the tab must refetch immediately, not wait out the "
        "remainder of an interval showing a stale price")

    i401 = src.find("r.status === 401")
    assert i401 != -1, "401 must be intercepted centrally in api()"
    window = src[i401:i401 + 400]
    assert "location.replace" in window, "an expired session must return to sign-in"

    html = pathlib.Path("web/index.html").read_text()
    assert 'async src="https://unpkg.com' in html, (
        "the charting CDN is used by one view and guarded at every call site; "
        "it must not sit between the user and time-to-interactive")


def test_snapshot_does_not_pull_ten_years_of_ohlc():
    """A free Postgres tier meters DATA TRANSFER, not just storage — a limit
    nothing in this codebase accounted for. At 500 names the snapshot query was
    the largest recurring egress in the system, and three of its eight columns
    (open/high/low) exist solely for a 21-day Yang-Zhang window validated over
    the trailing 260 bars. Pulling them across a decade was pure waste.

    The failure this prevents is not slowness: it is the database refusing
    connections mid-month because the transfer quota is exhausted, which takes
    the whole site down while storage sits at 41%.
    """
    import inspect

    from equisense.api import snapshot as snap

    src = inspect.getsource(snap._bulk_prices)
    assert "OHLC_WINDOW_DAYS" in src, "OHLC must be windowed, not full-history"
    assert snap.OHLC_WINDOW_DAYS <= 800, (
        "the window has to be comfortably smaller than the full history or it "
        "saves nothing")
    # the full-history query must NOT carry the intraday columns
    head = src[:src.find("OHLC tail")]
    for col in ("open_price", "high_price", "low_price"):
        assert col not in head, (
            f"{col} is still being pulled across the entire history")


def test_quote_refresh_prefetches_instead_of_querying_per_bar():
    """refresh_quotes issued a SELECT per bar per name to decide whether that
    bar needed writing. At 500 names over a five-day window that is ~2,500
    round trips, each returning a full entity — on a path that runs in the cron
    AND every five minutes while a tab is open.

    On a database that meters DATA TRANSFER, a hot loop pulling whole rows to
    discover that the common case needs no write at all is the most expensive
    way possible to do nothing.
    """
    import inspect

    from equisense.ingestion import yahoo

    src = inspect.getsource(yahoo.refresh_quotes)
    body = src[src.find("for t, sym in zip"):]
    assert "select(PriceObservation)" not in body, (
        "the per-bar SELECT is back inside the loop")
    assert "existing.get((cid, d.date()))" in body, (
        "existing bars must come from a single prefetched map")
    assert "PERIOD_DAYS" in src, "the prefetch must be bounded by the window"


def test_dashboard_reads_the_snapshot_not_raw_price_history():
    """The list views must never reconstruct signals from raw bars. They read
    ONE precomputed AppSnapshot row, which is what keeps a 500-name dashboard
    off the metered path entirely — the browser payload is Vercel egress, which
    is not the constrained resource."""
    import inspect

    from equisense.api import services

    src = inspect.getsource(services.dashboard)
    assert "get_universe" in src
    assert "PriceObservation" not in src, (
        "the dashboard must not touch the price table directly")


def test_base_rates_are_not_recomputed_every_single_day():
    """run_all_studies reloads the ENTIRE price table — unfiltered, because
    survivorship correction needs the delisted names — at roughly 60 MB a run.
    On a database that meters data transfer that is the single largest
    recurring cost in the system, and doing it daily bought a number that had
    not moved: these are ten-year statistics.
    """
    import inspect

    from equisense.api import app as app_mod
    from equisense.api.status import STALE_STUDY_DAYS

    src = inspect.getsource(app_mod.cron_refresh)
    assert "STALE_STUDY_DAYS" in src, "the studies stage must be gated on age"
    i = src.find("base_rate_records")
    assert "if _due:" in src[:i], "the gate has to precede the stage"
    assert STALE_STUDY_DAYS >= 7
