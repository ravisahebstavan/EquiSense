/* EquiSense workstation (Phase III) — the complete operating interface for
   the research engine. Dark-first, keyboard-first, evidence-native.
   Vanilla JS by design: one file, no build step, decade-maintainable. */
"use strict";

const app = document.getElementById("app");

/* ================================================================ utils */

const fmtN = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" :
    Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: 0 });
const fmtMoney = (v) => v === null || v === undefined ? "—" : "₹" + fmtN(v, 0);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const signed = (v, d = 2) => v == null ? "—" : (v >= 0 ? "+" : "") + fmtN(v, d);

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = JSON.stringify((await r.json()).detail); } catch { /* noop */ }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.status === 204 ? null : r.json();
}

/* Session cache: cheap, explicit, per-navigation invalidation. */
const cache = { companies: null, status: null, regime: null, profile: null };
async function getCompanies(force = false) {
  if (!cache.companies || force) cache.companies = await api("/companies");
  return cache.companies;
}

/* ====================================================== status strip */

function qClass(q) { return q >= 85 ? "ok" : q >= 60 ? "mid" : "bad"; }

async function refreshStatusStrip() {
  const el = document.getElementById("status-strip");
  try {
    const [st, rg] = await Promise.all([api("/live/status"), api("/live/regime")]);
    cache.status = st; cache.regime = rg;
    const p = st.datasets.prices;
    const warn = st.warnings.length
      ? `<span class="seg warn" title="${esc(st.warnings.join("\n"))}">⚠ ${st.warnings.length} warning${st.warnings.length > 1 ? "s" : ""}</span>`
      : `<span class="seg" style="color:var(--good-text)">✓ no warnings</span>`;
    el.innerHTML = `
      <span class="seg">Regime <strong>${esc(rg.label)}</strong>${rg.flags.length ? " · " + rg.flags.map(esc).join(" · ") : ""}</span>
      <span class="seg">Data <span class="q-badge ${qClass(st.quality_score)}"
        title="${esc(Object.entries(st.quality_components).map(([k, v]) => k + ": " + v).join("\n"))}">${st.quality_score}</span></span>
      <span class="seg">Prices <strong>${esc(p.latest)}</strong> (${p.staleness_days}d) · ${fmtN(p.rows, 0)} obs · ${p.companies} cos</span>
      <span class="seg">Coverage <strong>${esc(p.coverage)}</strong></span>
      <span class="seg">Provider ${esc(st.provider.split(" ")[0])}</span>
      ${warn}
      <span class="seg" style="margin-left:auto"><a href="#/lab/data">data health →</a></span>`;
  } catch (e) {
    el.innerHTML = `<span class="warn">status unavailable: ${esc(e.message)}</span>`;
  }
}

/* ==================================================== refresh drawer */

const drawer = document.getElementById("drawer");
const drawerBody = document.getElementById("drawer-body");
let refreshing = false;

const STAGE_LABEL = {
  bootstrap: "First-boot bootstrap", universe: "Syncing universe",
  downloading_prices: "Downloading prices", downloading_macro: "Downloading macro",
  fundamentals: "Fetching fundamentals", validating: "Validating",
  running_studies: "Running hypotheses", scoring_claims: "Scoring claims",
  publishing: "Publishing snapshot", pipeline: "Pipeline",
};

function renderStage(d) {
  const id = "st-" + d.stage;
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement("div");
    el.className = "stage"; el.id = id;
    drawerBody.appendChild(el);
  }
  const detail = Object.entries(d).filter(([k]) => !["stage", "status"].includes(k))
    .map(([k, v]) => `${k}: ${v}`).join(" · ");
  el.innerHTML =
    `<span class="chip ${d.status === "done" || d.status === "complete" ? "done" : d.status}">${esc(d.status)}</span>
     <span class="name">${esc(STAGE_LABEL[d.stage] || d.stage)}</span>
     <span class="detail">${esc(detail)}</span>`;
}

function finishRefresh(ok) {
  refreshing = false;
  refreshStatusStrip(); cache.companies = null;
  drawerBody.insertAdjacentHTML("beforeend",
    `<div class="sub" style="margin-top:10px">${ok
      ? "Done. Views reflect fresh data on next navigation."
      : "Stopped early — state is preserved and resumable: run Refresh again."}</div>`);
}

async function refreshViaPost() {
  // Fallback for hosts that buffer SSE (some serverless platforms):
  // same staged pipeline, results rendered at completion.
  drawerBody.insertAdjacentHTML("beforeend",
    '<div class="sub" id="post-note">Streaming unavailable here — running the full pipeline, results on completion (may take a few minutes)…</div>');
  try {
    const r = await api("/live/refresh", { method: "POST" });
    document.getElementById("post-note")?.remove();
    r.stages.forEach(renderStage);
    finishRefresh(r.ok);
  } catch (e) {
    drawerBody.insertAdjacentHTML("beforeend", `<div class="unavail">${esc(e.message)}</div>`);
    refreshing = false;
  }
}

function openRefreshDrawer() {
  drawer.hidden = false;
  if (refreshing) return;
  refreshing = true;
  drawerBody.innerHTML = "";
  let gotAny = false;
  const es = new EventSource("/api/live/refresh/stream");
  es.onmessage = (ev) => {
    gotAny = true;
    const d = JSON.parse(ev.data);
    renderStage(d);
    if (d.stage === "pipeline") { es.close(); finishRefresh(d.status === "complete"); }
  };
  es.onerror = () => {
    es.close();
    if (!gotAny) { refreshViaPost(); }           // SSE-hostile host → POST fallback
    else if (refreshing) { finishRefresh(false); } // stream cut mid-run → resumable
  };
}
document.getElementById("refresh-btn").addEventListener("click", openRefreshDrawer);
document.getElementById("drawer-close").addEventListener("click", () => drawer.hidden = true);

/* =================================================== command palette */

const palette = document.getElementById("palette");
const palInput = document.getElementById("palette-input");
const palList = document.getElementById("palette-list");
let palItems = [], palSel = 0;

const COMMANDS = [
  { label: "Go to Dashboard", k: "gd", run: () => location.hash = "#/dashboard" },
  { label: "Go to Companies", k: "gc", run: () => location.hash = "#/companies" },
  { label: "Go to Portfolio", k: "gp", run: () => location.hash = "#/portfolio" },
  { label: "Go to Research", k: "gr", run: () => location.hash = "#/research" },
  { label: "Go to Lab", k: "gl", run: () => location.hash = "#/lab" },
  { label: "Refresh live data (staged pipeline)", k: "R", run: openRefreshDrawer },
  { label: "Run studies (recompute base rates)", k: "", run: async () => { await api("/live/studies/run", { method: "POST" }); route(); } },
  { label: "Score due claims", k: "", run: async () => { await api("/live/score", { method: "POST" }); route(); } },
];

async function openPalette() {
  palette.hidden = false; palInput.value = ""; palSel = 0;
  await getCompanies().catch(() => []);
  renderPalette("");
  palInput.focus();
}
function closePalette() { palette.hidden = true; }

function renderPalette(q) {
  const needle = q.trim().toLowerCase();
  const cos = (cache.companies || [])
    .filter(c => !needle || c.ticker.toLowerCase().includes(needle) || c.name.toLowerCase().includes(needle))
    .slice(0, 8)
    .map(c => ({ label: `${c.ticker} — ${c.name}`, k: c.sector,
                 run: () => location.hash = `#/companies/${c.id}` }));
  const cmds = COMMANDS.filter(c => !needle || c.label.toLowerCase().includes(needle));
  palItems = [...cos, ...cmds];
  palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
  palList.innerHTML = palItems.map((it, i) =>
    `<div class="pal-item ${i === palSel ? "sel" : ""}" data-i="${i}">
       <span>${esc(it.label)}</span><span class="k">${esc(it.k || "")}</span></div>`).join("")
    || '<div class="pal-item sub">no matches</div>';
  palList.querySelectorAll(".pal-item[data-i]").forEach(el =>
    el.addEventListener("click", () => { closePalette(); palItems[+el.dataset.i].run(); }));
}
palInput.addEventListener("input", () => { palSel = 0; renderPalette(palInput.value); });
palInput.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { palSel = Math.min(palSel + 1, palItems.length - 1); renderPalette(palInput.value); e.preventDefault(); }
  else if (e.key === "ArrowUp") { palSel = Math.max(palSel - 1, 0); renderPalette(palInput.value); e.preventDefault(); }
  else if (e.key === "Enter" && palItems[palSel]) { closePalette(); palItems[palSel].run(); }
  else if (e.key === "Escape") closePalette();
});
palette.addEventListener("click", (e) => { if (e.target === palette) closePalette(); });
document.getElementById("palette-btn").addEventListener("click", openPalette);

/* keyboard: Ctrl+K / "/" palette; g+key nav; r refresh; Esc closes */
let pendingG = false;
document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
  if (typing) return;
  if (e.key === "/") { e.preventDefault(); openPalette(); return; }
  if (e.key === "Escape") { closePalette(); drawer.hidden = true; return; }
  if (pendingG) {
    pendingG = false;
    const map = { d: "dashboard", c: "companies", p: "portfolio", r: "research", l: "lab" };
    if (map[e.key]) location.hash = "#/" + map[e.key];
    return;
  }
  if (e.key === "g") { pendingG = true; setTimeout(() => pendingG = false, 800); return; }
  if (e.key === "r") openRefreshDrawer();
});

/* ============================================== shared renderers */

function metricRow(m) {
  const val = m.value === null ? "—" : fmtN(m.value, 2);
  const inputs = Object.entries(m.inputs || {})
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${typeof v === "number" ? fmtN(v, 4) : esc(v)}</td></tr>`).join("");
  return `<div class="metric">
    <div class="metric-row" data-toggle>
      <span class="m-label">${esc(m.label)}</span>
      <span class="m-value">${val}</span><span class="m-unit">${esc(m.unit)}</span>
      <span class="m-caret">▸ work</span>
    </div>
    <div class="metric-work">
      <div class="formula">${esc(m.formula || "")}</div>
      ${inputs ? `<table><tbody>${inputs}</tbody></table>` : ""}
      ${m.caveat ? `<div class="caveat">⚠ ${esc(m.caveat)}</div>` : ""}
      <div class="sub">Period: ${esc(m.period)} · deterministic engine (${esc(m.family)})</div>
    </div></div>`;
}
function wireToggles(root) {
  root.querySelectorAll("[data-toggle]").forEach(el =>
    el.addEventListener("click", () => el.parentElement.classList.toggle("open")));
}

function sparkline(title, series, unit = "") {
  const pts = series.filter(p => p.value !== null);
  if (pts.length < 2) return "";
  const vals = pts.map(p => p.value);
  const min = Math.min(...vals), max = Math.max(...vals), range = max - min || 1;
  const W = 160, H = 40, PAD = 3, step = (W - 2 * PAD) / (pts.length - 1);
  const xy = pts.map((p, i) => [PAD + i * step, H - PAD - ((p.value - min) / range) * (H - 2 * PAD)]);
  const poly = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = xy[xy.length - 1];
  const area = `${PAD},${H} ${poly} ${last[0].toFixed(1)},${H}`;
  return `<div class="spark" data-spark='${esc(JSON.stringify(pts.map(p => ({ p: p.period, v: p.value }))))}'>
    <div class="t">${esc(title)}</div><div class="v">${fmtN(vals[vals.length - 1], 1)}${esc(unit)}</div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <polygon class="area" points="${area}"></polygon>
      <polyline class="line" points="${poly}"></polyline>
      <circle class="dot" cx="${last[0]}" cy="${last[1]}" r="2.6"></circle>
    </svg><div class="tip"></div></div>`;
}

function microspark(values) {
  if (!values || values.length < 2) return "";
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const W = 84, H = 22, PAD = 2, step = (W - 2 * PAD) / (values.length - 1);
  const poly = values.map((v, i) =>
    `${(PAD + i * step).toFixed(1)},${(H - PAD - ((v - min) / range) * (H - 2 * PAD)).toFixed(1)}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  return `<svg class="microspark ${up ? "up" : "down"}" viewBox="0 0 ${W} ${H}"
    preserveAspectRatio="none"><polyline points="${poly}"></polyline></svg>`;
}

function skeleton(rows = 6) {
  return `<div class="panel">${Array.from({ length: rows }, (_, i) =>
    `<div class="skel" style="height:16px;margin:12px 0;width:${88 - (i * 7) % 30}%"></div>`).join("")}</div>`;
}
function wireSparks(root) {
  root.querySelectorAll(".spark").forEach(el => {
    const pts = JSON.parse(el.dataset.spark);
    const svg = el.querySelector("svg"), tip = el.querySelector(".tip");
    svg.addEventListener("mousemove", (e) => {
      const rect = svg.getBoundingClientRect();
      const i = Math.min(pts.length - 1, Math.max(0, Math.round((e.clientX - rect.left) / rect.width * (pts.length - 1))));
      tip.textContent = `${pts[i].p}: ${fmtN(pts[i].v, 1)}`;
      tip.style.display = "block";
      tip.style.left = Math.min(e.clientX - rect.left, rect.width - 70) + "px";
      tip.style.top = "-4px";
    });
    svg.addEventListener("mouseleave", () => tip.style.display = "none");
  });
}

function hbars(obj, cls = () => "") {
  return Object.entries(obj).map(([k, v]) => `
    <div class="hbar"><span class="lbl">${esc(k)}</span>
      <div class="track"><div class="fill ${cls(k, v)}" style="width:${Math.min(100, v)}%"></div></div>
      <span class="pct">${fmtN(v, 1)}%</span></div>`).join("");
}
function clusterBars(scores) {
  return Object.entries(scores).map(([k, v]) => {
    const pct = Math.min(50, Math.abs(v) * 50);
    const style = v >= 0 ? `left:50%;width:${pct}%` : `right:50%;width:${pct}%`;
    return `<div class="cluster-bar"><span class="lbl sub">${esc(k)}</span>
      <div class="track"><i class="mid"></i><i class="val ${v < 0 ? "neg" : ""}" style="${style}"></i></div>
      <span class="pct">${signed(v, 2)}</span></div>`;
  }).join("");
}

function aiBlock(id, label) {
  return `<div><button class="primary" data-ai="${id}">${esc(label)}</button><div id="ai-${id}"></div></div>`;
}
function renderAiResult(el, res) {
  if (!res.available) {
    el.innerHTML = `<div class="unavail">AI unavailable (${esc(res.reason || "no credentials")}).
      The deterministic numbers stand on their own.</div>`;
    return;
  }
  const g = res.grounding || {};
  const badge = g.grounded
    ? `<span class="ground-badge ok">✓ grounded — ${g.checked} numbers verified against context</span>`
    : `<span class="ground-badge bad">⚠ ungrounded: ${esc((g.violations || []).join(", "))}</span>`;
  let text = res.text;
  try { text = JSON.stringify(JSON.parse(res.text), null, 2); } catch { /* prose */ }
  el.innerHTML = `<div class="ai-out">${esc(text)}</div><div class="ai-meta">${badge}</div>
    <details class="ctx"><summary>Exact context sent to the model</summary>
      <pre>${esc(JSON.stringify(res.context, null, 2))}</pre></details>`;
}
function wireAi(root, handlers) {
  root.querySelectorAll("[data-ai]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const out = root.querySelector(`#ai-${btn.dataset.ai}`);
      btn.disabled = true;
      out.innerHTML = '<div class="loading">Narrating (grounded to engine output)…</div>';
      try { renderAiResult(out, await handlers[btn.dataset.ai]()); }
      catch (e) { out.innerHTML = `<div class="unavail">${esc(e.message)}</div>`; }
      finally { btn.disabled = false; }
    });
  });
}

function renderDossier(d) {
  const s = d.synthesis;
  const evid = d.evidence.map(e => `
    <div class="evi ${e.direction === "shadow" ? "" : e.direction}"
         style="${e.direction === "shadow" ? "opacity:.6;border-left-style:dashed" : ""}">
      <span class="tier ${e.tier}">${e.tier}</span>
      ${e.direction === "shadow" ? '<span class="tier" style="color:var(--serious);border-color:var(--serious)">SHADOW</span>' : ""}
      <strong>${esc(e.engine)}</strong> · ${esc(e.statement)}
      <span class="sub">(${esc(e.cluster)}, ${signed(e.strength, 2)})</span>
      ${e.base_rate ? `<div class="base-rate">📊 [${esc(e.base_rate.registry_ref)}, regime=${esc(e.base_rate.regime)}]
        N<sub>eff</sub>=${e.base_rate.n_eff ?? "?"} (N=${e.base_rate.n}) · hit ${(e.base_rate.hit_rate * 100).toFixed(0)}%
        · median ${signed(e.base_rate.median_excess_pct)}% (net ${signed(e.base_rate.net_median_excess_pct)}%)
        · ${e.base_rate.horizon_days}d · IQR [${e.base_rate.iqr_excess_pct}]%</div>` : ""}
      ${(e.caveats || []).filter(Boolean).map(c => `<div class="sub">⚠ ${esc(c)}</div>`).join("")}
    </div>`).join("");
  const conf = s.confidence;
  const sizing = d.sizing ? `
    <h2 style="margin-top:14px">Sizing (advisory, work shown)</h2>
    <div class="tiles">
      <div class="tile"><div class="label">Recommended</div><div class="value">${fmtMoney(d.sizing.recommended_value)}</div>
        <div class="sub">${d.sizing.recommended_shares} sh · ${fmtN(d.sizing.pct_of_book, 1)}% of book</div></div>
      <div class="tile"><div class="label">Stop distance</div><div class="value">${fmtN(d.sizing.stop_distance_pct, 1)}%</div>
        <div class="sub">risk ${fmtMoney(d.sizing.risk_at_stop)}</div></div>
      <div class="tile"><div class="label">Binding constraint</div><div class="value" style="font-size:14px">${esc(d.sizing.binding_constraint)}</div></div>
    </div>
    <div class="score-detail">
      ${Object.entries(d.sizing.working).map(([k, v]) =>
        `<span style="display:inline-block;margin:1px 14px 1px 0">${esc(k.replaceAll("_", " "))}:
         <strong>${typeof v === "number" ? fmtN(v, 2) : esc(v)}</strong></span>`).join("")}
      <div style="margin-top:5px">⚠ ${esc(d.sizing.caveat)}</div></div>
    <h2 style="margin-top:12px">Costs & taxes (India)</h2>
    <div class="score-detail">Round trip ≈ ${d.costs_taxes.round_trip_cost_pct}%
      (statutory ${d.costs_taxes.statutory_pct}% + impact ${d.costs_taxes.impact_estimate_pct}%) ·
      ${esc(d.costs_taxes.applicable_tax)} · breakeven ${d.costs_taxes.breakeven_gross_move_pct}%
      ${d.costs_taxes.ltcg_cliff_note ? "<br>⚠ " + esc(d.costs_taxes.ltcg_cliff_note) : ""}</div>` : "";
  return `
    <div class="verdict-banner ${s.verdict}">
      <span class="verdict ${s.verdict}">${esc(s.verdict.replaceAll("_", " "))}</span>
      <span>net ${signed(s.net_score, 3)} · ${esc(s.conviction_band)} conviction · dispersion ${s.dispersion}</span>
      <span class="sub">as of ${esc(d.as_of)} · regime ${esc(d.regime.label)} (context)</span>
    </div>
    ${s.dissent.length ? `<div class="breach" style="margin-top:6px">${s.dissent.map(esc).join("<br>")}</div>` : ""}
    <div class="sub" style="margin:6px 0">${s.notes.map(esc).join(" ")}</div>
    <h2 style="margin-top:8px">Cluster scores (uniform provisional weights)</h2>
    ${clusterBars(s.cluster_scores)}
    <div class="score-detail" style="margin-top:8px">Confidence ${conf.score} —
      ${esc(JSON.stringify(conf.components))}<br>${esc(conf.label)}</div>
    ${d.trend_value_tension?.inputs?.quadrant
      ? `<div class="breach" style="color:var(--series-3);margin-top:8px">TVT: ${esc(d.trend_value_tension.inputs.quadrant)}</div>` : ""}
    <h2 style="margin-top:12px">Evidence (${d.evidence.length})</h2>${evid}
    ${sizing}
    <h2 style="margin-top:12px">What am I missing?</h2>
    <div class="score-detail">
      Clusters missing: ${s.coverage.clusters_missing.join(", ") || "none"} ·
      shadow items: ${d.missing_information.shadow_evidence}<br>
      Not ingested: ${d.missing_information.not_ingested.join(", ")}<br>
      ${esc(d.missing_information.note)}</div>
    <div class="hashline" style="margin-top:8px">Pre-registered: ${esc(d.ledger.hash)} ·
      claim: ${esc(d.ledger.claim.type)}${d.ledger.claim.stated_probability
        ? `, P(hit)=${d.ledger.claim.stated_probability}` : ""} ·
      scores after ${esc(d.ledger.claim.score_after)}</div>`;
}

/* ============================================================ dashboard */

async function viewDashboard() {
  const [d, pf, cal, br, led] = await Promise.all([
    api("/dashboard"), api("/portfolio"), api("/live/calibration"),
    api("/live/base-rates"), api("/live/ledger")]);
  const st = cache.status || await api("/live/status");
  const rg = cache.regime || await api("/live/regime");

  const top = d.ranked.slice(0, 8).map(r => `
    <tr class="clickable" data-company="${r.id}">
      <td><strong>${esc(r.ticker)}</strong></td><td>${esc(r.sector)}</td>
      <td>${microspark(r.spark)}</td>
      <td>${r.held ? '<span class="chip held">held</span>' : ""}${r.watched ? '<span class="chip">watch</span>' : ""}</td>
      <td class="num">${fmtMoney(r.price)}</td>
      <td class="num">${r.signals.f_score ?? "—"}</td>
      <td class="num">${fmtN(r.signals.pe, 1)}</td>
      <td><span class="priority-bar"><i style="width:${r.priority.score}%"></i></span>${r.priority.score}</td>
    </tr>`).join("");

  const weakest = pf.holdings.filter(h => h.unrealized_pnl < 0 || h.quality_tier === "low")
    .sort((a, b) => a.unrealized_pnl - b.unrealized_pnl).slice(0, 5);
  const reviews = d.thesis_reviews_due;
  const regCounts = st.registry;
  const recent = led.records.slice(-6).reverse();

  app.innerHTML = `
    <h1>Command Center</h1>
    <div class="dash-grid" style="margin-top:10px">
      <div class="panel span4"><h2>Market Regime (context)</h2>
        ${rg.components.map(c => `<div class="metric-row" style="cursor:default">
          <span class="m-label">${esc(c.label)}</span>
          <span class="m-value">${fmtN(c.value, 1)}</span><span class="m-unit">${esc(c.unit)}</span></div>`).join("")}
        ${rg.flags.map(f => `<div class="breach">⚠ ${esc(f)}</div>`).join("")}
        <div class="sub" style="margin-top:6px">REG-001: conditioning showed no OOS value — regime is descriptive.</div>
      </div>
      <div class="panel span4"><h2>Portfolio</h2>
        ${pf.holdings.length ? `
        <div class="tiles" style="grid-template-columns:1fr 1fr">
          <div class="tile"><div class="label">Value</div><div class="value">${fmtMoney(pf.total_value)}</div></div>
          <div class="tile"><div class="label">XIRR</div>
            <div class="value ${(pf.xirr?.value ?? 0) >= 0 ? "pos" : "neg"}">${fmtN(pf.xirr?.value, 1)}%</div></div>
        </div>
        ${pf.profile_limit_breaches.slice(0, 3).map(b => `<div class="breach">⚠ ${esc(b)}</div>`).join("")
          || '<div class="sub">No profile-limit breaches.</div>'}
        <div class="sub" style="margin-top:4px"><a href="#/portfolio">full monitor →</a></div>`
        : `<div class="empty">No transactions yet — the monitor activates with your first
           recorded trade.</div>
           <div style="text-align:center"><a href="#/portfolio">record a transaction →</a></div>`}
      </div>
      <div class="panel span4"><h2>System & Model Health</h2>
        <div class="metric-row" style="cursor:default"><span class="m-label">Data quality</span>
          <span class="m-value"><span class="q-badge ${qClass(st.quality_score)}">${st.quality_score}</span></span></div>
        <div class="metric-row" style="cursor:default"><span class="m-label">Scored claims / abstentions</span>
          <span class="m-value">${cal.scored_claims ?? 0} / ${cal.scored_abstentions ?? 0}</span></div>
        <div class="metric-row" style="cursor:default"><span class="m-label">Base-rate records</span>
          <span class="m-value">${br.records.length}</span></div>
        <div class="metric-row" style="cursor:default"><span class="m-label">Hypotheses</span>
          <span class="m-value">${Object.entries(regCounts).map(([k, v]) => `${v} ${k}`).join(" · ")}</span></div>
        <div class="metric-row" style="cursor:default"><span class="m-label">Ledger chain</span>
          <span class="m-value">${led.chain.intact ? "✓ intact" : "⚠ BROKEN"} (${led.chain.records})</span></div>
        ${st.warnings.map(w => `<div class="breach">⚠ ${esc(w)}</div>`).join("")}
      </div>
      <div class="panel span8"><h2>Highest attention (your profile's ordering)</h2>
        <div class="tablewrap"><table><thead><tr><th>Ticker</th><th>Sector</th><th>1y</th><th></th>
          <th class="num">Price</th><th class="num">F</th><th class="num">P/E</th><th>Priority</th></tr></thead>
          <tbody>${top}</tbody></table></div>
        <div class="sub" style="margin-top:6px"><a href="#/companies">full universe →</a></div>
      </div>
      <div class="panel span4"><h2>Needs your attention</h2>
        ${reviews.length ? reviews.map(t => `<div class="breach">Thesis review due (${esc(t.review_date)}):
          <strong>${esc(t.ticker)}</strong> — ${esc(t.statement.slice(0, 90))}…</div>`).join("")
          : '<div class="sub">No thesis reviews due.</div>'}
        <h2 style="margin-top:12px">Weakest positions</h2>
        ${weakest.length ? weakest.map(h => `<div class="metric-row" style="cursor:default">
            <span class="m-label">${esc(h.ticker)} ${h.quality_tier ? `<span class="chip ${h.quality_tier}">${h.quality_tier}</span>` : ""}</span>
            <span class="m-value" style="color:${h.unrealized_pnl >= 0 ? "inherit" : "var(--critical)"}">${fmtMoney(h.unrealized_pnl)}</span></div>`).join("")
          : '<div class="sub">None flagged.</div>'}
      </div>
      <div class="panel span12"><h2>Recent ledger activity (pre-registered, hash-chained)</h2>
        ${recent.map(r => `<div class="metric-row" style="cursor:default">
          <span class="m-label">${esc(r.kind)} · ${esc(r.company?.ticker || r.company || "")} ·
            ${esc((r.verdict || r.claim_type || "").replaceAll("_", " "))}</span>
          <span class="sub">${esc(r.created_at?.slice(0, 16))}</span>
          <span class="hashline">${esc((r.hash || "").slice(0, 12))}</span></div>`).join("")
          || '<div class="sub">Ledger empty — generate a dossier.</div>'}
      </div>
    </div>`;
  app.querySelectorAll("tr[data-company]").forEach(tr =>
    tr.addEventListener("click", () => location.hash = `#/companies/${tr.dataset.company}`));
}

/* ============================================================ companies */

let coSort = { key: "priority", dir: -1 };

async function viewCompanies() {
  const list = await getCompanies();
  const q = (document.getElementById("co-filter")?.value || "").toLowerCase();
  const rows = list
    .filter(c => !q || c.ticker.toLowerCase().includes(q) || c.name.toLowerCase().includes(q) || c.sector.toLowerCase().includes(q))
    .sort((a, b) => {
      const g = (x) => coSort.key === "priority" ? x.priority.score : (x.signals[coSort.key] ?? x[coSort.key] ?? -1e9);
      return (g(a) > g(b) ? 1 : -1) * coSort.dir;
    })
    .map(c => `
    <tr class="clickable" data-company="${c.id}">
      <td><strong>${esc(c.ticker)}</strong><div class="sub">${esc(c.name)}</div></td>
      <td>${esc(c.sector)}</td>
      <td>${microspark(c.spark)}</td>
      <td>${c.held ? '<span class="chip held">held</span>' : ""}${c.watched ? '<span class="chip">watch</span>' : ""}</td>
      <td class="num">${fmtMoney(c.price)}</td>
      <td class="num">${c.signals.f_score ?? "—"}/9</td>
      <td>${c.signals.z_zone ? `<span class="chip ${c.signals.z_zone}">${c.signals.z_zone}</span>` : "—"}</td>
      <td class="num">${fmtN(c.signals.roic_pct, 1)}</td>
      <td class="num">${fmtN(c.signals.revenue_cagr_pct, 1)}</td>
      <td class="num">${fmtN(c.signals.pe, 1)}</td>
      <td class="num">${fmtN(c.signals.implied_growth_gap_pct, 1)}</td>
      <td><span class="priority-bar"><i style="width:${c.priority.score}%"></i></span>${c.priority.score}</td>
    </tr>`).join("");

  const TH = (label, key, num = true) =>
    `<th class="${num ? "num" : ""} clickable" data-sort="${key}">${label}${coSort.key === key ? (coSort.dir > 0 ? " ↑" : " ↓") : ""}</th>`;

  app.innerHTML = `
    <h1>Universe <span class="sub">${list.length} companies · bounded by design</span></h1>
    <div class="filterbar" style="margin-top:8px">
      <input id="co-filter" placeholder="Filter ticker / name / sector…" value="${esc(q)}">
      <span class="sub">click headers to sort · click a row to open the workspace</span>
    </div>
    <div class="panel"><div class="tablewrap"><table>
      <thead><tr><th>Company</th><th>Sector</th><th>1y</th><th></th>
        ${TH("Price", "price")}${TH("F", "f_score")}<th>Z</th>${TH("ROIC%", "roic_pct")}
        ${TH("Rev CAGR", "revenue_cagr_pct")}${TH("P/E", "pe")}${TH("Exp gap*", "implied_growth_gap_pct")}
        ${TH("Priority", "priority")}</tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="sub" style="margin-top:6px">* market-implied FCF growth − delivered FCF CAGR (pp).</div>
    </div>`;
  app.querySelectorAll("tr[data-company]").forEach(tr =>
    tr.addEventListener("click", () => location.hash = `#/companies/${tr.dataset.company}`));
  app.querySelectorAll("th[data-sort]").forEach(th =>
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      coSort = { key: k, dir: coSort.key === k ? -coSort.dir : -1 };
      viewCompanies();
    }));
  const f = document.getElementById("co-filter");
  f.addEventListener("input", () => viewCompanies());
  f.focus();
  f.setSelectionRange(q.length, q.length);
}

/* ===================================================== company workspace */

function valuationCard(card, companyId) {
  const a = card.extras.assumptions || {}, w = a.wacc || {};
  return `${card.metrics.map(metricRow).join("")}
    <div class="sub" style="margin:6px 0">Every assumption is editable — test your own view.</div>
    <div class="frm" id="val-form">
      <div><label>Risk-free rate</label><input name="risk_free_rate" type="number" step="0.001" value="${w.risk_free_rate ?? 0.07}"></div>
      <div><label>Equity risk premium</label><input name="equity_risk_premium" type="number" step="0.001" value="${w.equity_risk_premium ?? 0.065}"></div>
      <div><label>Beta</label><input name="beta" type="number" step="0.05" value="${w.beta ?? 1.0}"></div>
      <div><label>Tax rate</label><input name="tax_rate" type="number" step="0.001" value="${w.tax_rate ?? 0.2517}"></div>
      <div><label>Horizon (y)</label><input name="horizon_years" type="number" step="1" value="${a.horizon_years ?? 10}"></div>
      <div><label>Terminal growth</label><input name="terminal_growth" type="number" step="0.005" value="${a.terminal_growth ?? 0.04}"></div>
    </div>
    <button class="primary" id="recompute-val">Recompute implied growth</button>
    <div id="val-result"></div>`;
}

async function viewCompanyDetail(id, tab = "overview") {
  const d = await api(`/companies/${id}`);
  const c = d.company;
  const tabs = [["overview", "Overview"], ["dossier", "Dossier"], ["memory", "Memory"], ["ai", "AI Desk"]];

  app.innerHTML = `
    <h1>${esc(c.name)} <span class="sub">${esc(c.ticker)} · ${esc(c.sector)} · ${fmtMoney(c.price)}
      · ${esc(d.period)}</span></h1>
    <div class="sub">${esc(c.description)}</div>
    <div class="tabs">${tabs.map(([k, l]) =>
      `<button class="${k === tab ? "active" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>
    <div id="tab-body">${skeleton(6)}</div>`;
  app.querySelectorAll("[data-tab]").forEach(b =>
    b.addEventListener("click", () => location.hash = `#/companies/${id}/${b.dataset.tab}`));

  const body = document.getElementById("tab-body");
  if (tab === "overview") renderCompanyOverview(body, d, id);
  else if (tab === "dossier") renderCompanyDossier(body, id);
  else if (tab === "memory") renderCompanyMemory(body, id);
  else if (tab === "ai") renderCompanyAi(body, id);
}

function renderCompanyOverview(body, d, id) {
  const cardsHtml = d.card_order.map(key => {
    const card = d.cards[key];
    if (!card) return "";
    let inner;
    if (key === "peer_comparison") {
      inner = `<div class="tablewrap"><table><thead><tr><th>Peer</th><th class="num">Rev (cr)</th>
        <th class="num">CAGR%</th><th class="num">OpM%</th><th class="num">ROIC%</th>
        <th class="num">P/E</th><th class="num">EV/EBITDA</th><th class="num">F</th><th>Z</th></tr></thead>
        <tbody>${card.table.map(r => `
          <tr class="${r.is_self ? "self-row" : "clickable"}" data-company="${r.id}">
            <td><strong>${esc(r.ticker)}</strong></td><td class="num">${fmtN(r.revenue, 0)}</td>
            <td class="num">${fmtN(r.revenue_cagr_pct, 1)}</td><td class="num">${fmtN(r.operating_margin_pct, 1)}</td>
            <td class="num">${fmtN(r.roic_pct, 1)}</td><td class="num">${fmtN(r.pe, 1)}</td>
            <td class="num">${fmtN(r.ev_ebitda, 1)}</td><td class="num">${r.f_score ?? "—"}</td>
            <td>${r.z_zone ? `<span class="chip ${r.z_zone}">${r.z_zone}</span>` : "—"}</td></tr>`).join("")}
        </tbody></table></div><div class="sub" style="margin-top:6px">Peer set manually curated.</div>`;
    } else if (key === "valuation") {
      inner = valuationCard(card, id);
    } else if (key === "growth_trends") {
      const e = card.extras;
      inner = `<div class="tiles">
          <div class="tile"><div class="label">Revenue CAGR ${esc(e.window)}</div><div class="value">${fmtN(e.revenue_cagr_pct, 1)}%</div></div>
          <div class="tile"><div class="label">Net income CAGR</div><div class="value">${fmtN(e.net_income_cagr_pct, 1)}%</div></div>
        </div>
        <div class="sparks">
          ${sparkline("Revenue (cr)", d.trends.revenue)}${sparkline("Net income (cr)", d.trends.net_income)}
          ${sparkline("Op margin %", d.trends.operating_margin, "%")}${sparkline("ROIC %", d.trends.roic, "%")}
          ${sparkline("FCF (cr)", d.trends.fcf)}${sparkline("EPS ₹", d.trends.eps)}
        </div>`;
    } else {
      const extras = [];
      if (card.extras?.z_zone) extras.push(`Z zone: <span class="chip ${card.extras.z_zone}">${card.extras.z_zone}</span>`);
      if (card.extras?.quality_tier) extras.push(`Quality: <span class="chip ${card.extras.quality_tier}">${card.extras.quality_tier}</span>`);
      if (card.extras?.payout_ratio_pct != null) extras.push(`Payout: <strong>${fmtN(card.extras.payout_ratio_pct, 1)}%</strong>`);
      inner = (extras.length ? `<div style="margin-bottom:6px">${extras.join(" · ")}</div>` : "")
        + card.metrics.map(metricRow).join("");
    }
    return `<div class="panel"><h2>${esc(card.title)}</h2>${inner}</div>`;
  }).join("");

  const sectorAttrs = d.sector_attributes.length
    ? `<div class="panel"><h2>Sector KPIs</h2>${d.sector_attributes.map(a =>
        `<div class="metric-row" style="cursor:default"><span class="m-label">${esc(a.name)}</span>
         <span class="m-value">${fmtN(a.value, 1)}</span><span class="m-unit">${esc(a.unit)}</span></div>`).join("")}</div>` : "";

  body.innerHTML = `<div class="sub" style="margin-bottom:8px">Cards ordered by your lens.
    Every number expands to its formula, inputs, and caveats.</div>${cardsHtml}${sectorAttrs}`;
  wireToggles(body); wireSparks(body);
  body.querySelectorAll("tr.clickable[data-company]").forEach(tr =>
    tr.addEventListener("click", () => location.hash = `#/companies/${tr.dataset.company}`));
  const btn = body.querySelector("#recompute-val");
  if (btn) btn.addEventListener("click", async () => {
    const payload = {};
    body.querySelectorAll("#val-form input").forEach(i => payload[i.name] = parseFloat(i.value));
    btn.disabled = true;
    try {
      const r = await api(`/companies/${id}/valuation`, { method: "POST", body: JSON.stringify(payload) });
      const target = body.querySelector("#val-result");
      target.innerHTML = `<div class="panel" style="margin-top:10px">
        ${[r.implied_growth, r.wacc, r.historical_fcf_cagr].filter(Boolean).map(metricRow).join("")}</div>`;
      wireToggles(target);
    } finally { btn.disabled = false; }
  });
}

async function renderCompanyDossier(body, id) {
  body.innerHTML = `<div class="panel">
    <div class="sub">Runs every engine on live data, applies admission caps, attaches N_eff-honest
      base rates, and pre-registers a scoreable claim in the hash-chained ledger.</div>
    <button class="primary" id="gen-dossier" style="margin-top:8px">Generate dossier</button>
    <div id="dossier-out"></div></div>`;
  document.getElementById("gen-dossier").addEventListener("click", async (e) => {
    const out = document.getElementById("dossier-out");
    e.target.disabled = true;
    out.innerHTML = '<div class="loading">Running engines on live data…</div>';
    try { out.innerHTML = renderDossier(await api(`/live/dossier/${id}`, { method: "POST" })); }
    catch (err) { out.innerHTML = `<div class="unavail">${esc(err.message)}</div>`; }
    finally { e.target.disabled = false; }
  });
}

async function renderCompanyMemory(body, id) {
  const m = await api(`/companies/${id}/memory`);
  body.innerHTML = `
    <div class="grid2">
      <div class="panel"><h2>Dossier history (${m.dossier_history.length})</h2>
        ${m.dossier_history.map(h => `<div class="metric-row" style="cursor:default">
          <span class="m-label">${esc(h.created_at.slice(0, 16))} ·
            <span class="verdict ${esc(h.verdict)}" style="font-size:11px;padding:0 7px">${esc(h.verdict.replaceAll("_", " "))}</span>
            net ${signed(h.net_score, 2)}</span>
          <span class="hashline">${esc(h.hash)}</span></div>`).join("")
          || '<div class="sub">No dossiers issued yet for this name.</div>'}
        <h2 style="margin-top:12px">Scored claims (${m.scored_claims.length})</h2>
        ${m.scored_claims.map(s => `<div class="metric-row" style="cursor:default">
          <span class="m-label">${esc(s.claim_type || "claim")} → realized ${signed(s.realized_excess_pct)}%</span>
          <span class="m-value">${s.hit === null ? (s.wrongful_abstention ? "wrongful abstention" : "abstention ok")
            : (s.hit ? "hit" : "miss")}</span></div>`).join("")
          || '<div class="sub">No claims have reached their horizon yet.</div>'}
      </div>
      <div class="panel"><h2>Theses (${m.theses.length})</h2>
        ${m.theses.map(t => `<div class="thesis"><h3><span class="chip ${t.status}">${esc(t.status)}</span>
          ${t.review_date ? `<span class="sub">review ${esc(t.review_date)}</span>` : ""}</h3>
          <div>${esc(t.statement)}</div>
          <ul class="trg">${t.invalidation_triggers.map(a => `<li>${esc(a)}</li>`).join("")}</ul></div>`).join("")
          || '<div class="sub">No theses for this name.</div>'}
        <h2 style="margin-top:12px">Journal (${m.journal.length})</h2>
        ${m.journal.map(j => `<div class="journal-entry">
          <div class="meta">${esc(j.created_at.slice(0, 10))}${j.cfa_topic ? " · 📚 " + esc(j.cfa_topic) : ""}</div>
          <div>${esc(j.content)}</div></div>`).join("") || '<div class="sub">No entries.</div>'}
      </div>
    </div>`;
}

function renderCompanyAi(body, id) {
  body.innerHTML = `<div class="panel stack">
    ${aiBlock("narrate", "Explain this company (grounded)")}
    <div style="margin-top:14px">
      <input id="th-angle" placeholder="Your thesis angle, in your own words…" style="width:60%">
      <button data-ai="thdraft">Draft thesis skeleton</button>
      <div id="ai-thdraft"></div>
    </div></div>`;
  wireAi(body, {
    narrate: () => api(`/ai/narrate/company/${id}`, { method: "POST" }),
    thdraft: () => {
      const angle = document.getElementById("th-angle").value.trim();
      if (angle.length < 5) return Promise.resolve({ available: false, reason: "give the assistant your angle first — the thesis stays yours" });
      return api(`/ai/thesis-draft/${id}`, { method: "POST", body: JSON.stringify({ user_angle: angle }) });
    },
  });
}

/* ============================================================ portfolio */

function corrCell(v) {
  if (v === null) return "<td>—</td>";
  const alpha = Math.min(1, Math.abs(v));
  const bg = v >= 0.999 ? "var(--surface-2)" :
    `color-mix(in srgb, ${v >= 0 ? "var(--pos-cell)" : "var(--neg-cell)"} ${Math.round(alpha * 85)}%, var(--surface))`;
  return `<td><span style="background:${bg}">${v.toFixed(2)}</span></td>`;
}

function txnFormHtml(companies) {
  const opts = companies.map(c => `<option value="${c.id}">${esc(c.ticker)} — ${esc(c.name)}</option>`).join("");
  const today = new Date().toISOString().slice(0, 10);
  return `<div class="panel"><h2>Record transaction</h2>
    <div class="frm">
      <div><label>Company</label><select id="tx-company">${opts}</select></div>
      <div><label>Side</label><select id="tx-side"><option value="buy">Buy</option><option value="sell">Sell</option></select></div>
      <div><label>Quantity</label><input id="tx-qty" type="number" min="1" step="1" placeholder="10"></div>
      <div><label>Price (₹/share)</label><input id="tx-price" type="number" min="0.05" step="0.05" placeholder="1234.50"></div>
      <div><label>Trade date</label><input id="tx-date" type="date" value="${today}"></div>
      <div><label>Fees (₹)</label><input id="tx-fees" type="number" min="0" step="0.01" value="0"></div>
    </div>
    <button class="primary" id="tx-save">Record</button>
    <span class="sub" style="margin-left:10px">The ledger is the source of truth — positions, XIRR,
      heat and tax lots all derive from it.</span>
    <div id="tx-msg" class="sub" style="margin-top:6px"></div></div>`;
}

function wireTxnForm(onSaved) {
  document.getElementById("tx-save").addEventListener("click", async () => {
    const msg = document.getElementById("tx-msg");
    try {
      await api("/transactions", { method: "POST", body: JSON.stringify({
        company_id: parseInt(document.getElementById("tx-company").value),
        side: document.getElementById("tx-side").value,
        quantity: parseFloat(document.getElementById("tx-qty").value),
        price: parseFloat(document.getElementById("tx-price").value),
        trade_date: document.getElementById("tx-date").value,
        fees: parseFloat(document.getElementById("tx-fees").value || "0"),
      })});
      onSaved();
    } catch (e) { msg.textContent = "Rejected: " + e.message; }
  });
}

async function viewPortfolio() {
  const [p, risk, companies] = await Promise.all([
    api("/portfolio"), api("/live/portfolio-risk"), getCompanies()]);

  if (!p.holdings.length) {
    app.innerHTML = `
      <h1>Portfolio Monitor</h1>
      <div class="panel" style="margin-top:12px">
        <div class="empty">No transactions recorded yet. Record your first trade below —
          positions, P&amp;L, XIRR, concentration, correlation and portfolio heat all
          derive from the transaction ledger.</div>
      </div>
      ${txnFormHtml(companies)}`;
    wireTxnForm(viewPortfolio);
    return;
  }

  const holdings = p.holdings.map(h => `
    <tr class="clickable" data-company="${h.company_id}">
      <td><strong>${esc(h.ticker)}</strong><div class="sub">${esc(h.sector)}</div></td>
      <td class="num">${fmtN(h.quantity, 0)}</td><td class="num">${fmtN(h.avg_cost, 0)}</td>
      <td class="num">${fmtN(h.price, 0)}</td><td class="num">${fmtMoney(h.value)}</td>
      <td class="num" style="color:${h.unrealized_pnl >= 0 ? "var(--good-text)" : "var(--critical)"}">${fmtMoney(h.unrealized_pnl)}</td>
      <td class="num">${fmtN(h.xirr_pct, 1)}%</td><td class="num">${fmtN(h.weight_pct, 1)}%</td>
      <td>${h.quality_tier ? `<span class="chip ${h.quality_tier}">${h.quality_tier}</span>` : "—"}</td>
      <td class="sub">${h.lots.map(l => l.is_long_term ? "LT" : `${l.days_to_long_term}d→LT`).join(", ")}</td>
    </tr>`).join("");

  const riskPanel = risk.has_book ? `
    <div class="grid2">
      <div class="panel"><h2>Correlation matrix (126d)</h2>
        <div class="tablewrap"><table class="corr"><thead><tr><th></th>
          ${risk.tickers.map(t => `<th>${esc(t)}</th>`).join("")}</tr></thead><tbody>
          ${risk.tickers.map((t, i) => `<tr><th>${esc(t)}</th>
            ${risk.correlation_matrix[i].map(corrCell).join("")}</tr>`).join("")}
        </tbody></table></div>
        <div class="sub" style="margin-top:6px">${risk.caveats.map(esc).join(" · ")}</div>
      </div>
      <div class="panel"><h2>Risk</h2>
        <div class="tiles" style="grid-template-columns:1fr 1fr">
          <div class="tile"><div class="label">Portfolio heat</div>
            <div class="value ${risk.open_heat_pct > risk.heat_budget_pct ? "neg" : ""}">${fmtN(risk.open_heat_pct, 1)}%</div>
            <div class="sub">budget ${risk.heat_budget_pct}%</div></div>
        </div>
        <h2 style="margin-top:8px">Risk contribution (weight × vol, naive)</h2>
        ${hbars(risk.risk_contribution_pct)}
        <h2 style="margin-top:8px">Realized vol (ann.)</h2>
        ${hbars(risk.vol_pct)}
      </div>
    </div>` : `<div class="panel"><div class="sub">${esc(risk.note || "No open positions.")}</div></div>`;

  const x = p.xirr || {};
  app.innerHTML = `
    <h1>Portfolio Monitor <span class="sub">as of ${esc(p.as_of)} · derived from the transaction ledger</span></h1>
    <div class="tiles" style="margin-top:10px">
      <div class="tile"><div class="label">Value</div><div class="value">${fmtMoney(p.total_value)}</div></div>
      <div class="tile"><div class="label">Invested</div><div class="value">${fmtMoney(p.total_invested)}</div></div>
      <div class="tile"><div class="label">Unrealized</div><div class="value ${p.unrealized_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(p.unrealized_pnl)}</div></div>
      <div class="tile"><div class="label">Realized</div><div class="value ${p.realized_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(p.realized_pnl)}</div></div>
      <div class="tile"><div class="label">XIRR</div><div class="value ${(x.value ?? 0) >= 0 ? "pos" : "neg"}">${fmtN(x.value, 2)}%</div></div>
    </div>
    ${p.profile_limit_breaches.length ? `<div class="panel"><h2>Conflicts with your stated rules</h2>
      ${p.profile_limit_breaches.map(b => `<div class="breach">⚠ ${esc(b)}</div>`).join("")}
      <div class="sub">Diagnostics only — EquiSense never suggests trades.</div></div>` : ""}
    <div class="panel"><h2>Holdings</h2><div class="tablewrap">
      <table><thead><tr><th>Company</th><th class="num">Qty</th><th class="num">Avg</th>
        <th class="num">Price</th><th class="num">Value</th><th class="num">Unrl P&L</th>
        <th class="num">XIRR%</th><th class="num">Wt</th><th>Q</th><th>Tax lots</th></tr></thead>
      <tbody>${holdings}</tbody></table></div></div>
    ${riskPanel}
    <div class="grid2">
      <div class="panel"><h2>Concentration — position</h2>${hbars(p.concentration.by_position)}</div>
      <div class="panel"><h2>Concentration — sector</h2>${hbars(p.concentration.by_sector)}</div>
      <div class="panel"><h2>Concentration — cap band</h2>${hbars(p.concentration.by_cap_band)}</div>
      <div class="panel"><h2>Concentration — quality tier</h2>
        ${hbars(p.concentration.by_quality_tier, (k) => "q-" + k)}
        <div class="sub" style="margin-top:6px">Capital in fundamentally fragile businesses — the axis most tools omit.</div></div>
    </div>
    ${txnFormHtml(companies)}
    <div class="panel">${aiBlock("pfnarrate", "Brief me on this book (grounded)")}</div>`;
  app.querySelectorAll("tr[data-company]").forEach(tr =>
    tr.addEventListener("click", () => location.hash = `#/companies/${tr.dataset.company}`));
  wireTxnForm(viewPortfolio);
  wireAi(app, { pfnarrate: () => api("/ai/narrate/portfolio", { method: "POST" }) });
}

/* ============================================================= research */

async function viewResearch() {
  const [theses, journal, watch, companies] = await Promise.all([
    api("/theses"), api("/journal"), api("/watchlist"), getCompanies()]);
  const opts = companies.map(c => `<option value="${c.id}">${esc(c.ticker)} — ${esc(c.name)}</option>`).join("");

  app.innerHTML = `
    <h1>Research Desk</h1>
    <div class="grid2" style="margin-top:10px">
      <div>
        <div class="panel"><h2>Theses — structured, falsifiable, lifecycle-tracked</h2>
          ${theses.map(t => `<div class="thesis">
            <h3>${esc(t.ticker)} <span class="chip ${t.status}">${esc(t.status)}</span>
              ${t.review_date ? `<span class="sub">review ${esc(t.review_date)}</span>` : ""}</h3>
            <div>${esc(t.statement)}</div>
            <div class="sub" style="margin-top:4px">Falsifiable assumptions:</div>
            <ul>${t.assumptions.map(a => `<li>${esc(a)}</li>`).join("")}</ul>
            <div class="sub">Invalidation triggers:</div>
            <ul class="trg">${t.invalidation_triggers.map(a => `<li>${esc(a)}</li>`).join("")}</ul>
            <div>${["active", "under_review", "confirmed", "invalidated", "closed"]
              .filter(sx => sx !== t.status)
              .map(sx => `<button data-thesis="${t.id}" data-status="${sx}">${sx.replace("_", " ")}</button>`).join(" ")}</div>
          </div>`).join("") || '<div class="sub">No theses yet.</div>'}
        </div>
        <div class="panel stack"><h2>New thesis</h2>
          <select id="th-company">${opts}</select>
          <textarea id="th-statement" rows="2" placeholder="Thesis statement (a business claim, not a price claim)"></textarea>
          <textarea id="th-assumptions" rows="3" placeholder="Falsifiable assumptions — one per line, with numbers/dates"></textarea>
          <textarea id="th-triggers" rows="3" placeholder="Invalidation triggers — what observable fact proves this wrong? (required)"></textarea>
          <input id="th-review" type="date">
          <button class="primary" id="th-save">Save thesis</button>
          <div id="th-msg" class="sub"></div>
        </div>
      </div>
      <div>
        <div class="panel"><h2>Watchlist — rationale required</h2>
          ${watch.map(w => `<div class="journal-entry">
            <div class="meta"><a href="#/companies/${w.company_id}">${esc(w.ticker)}</a> · ${esc(w.added_at.slice(0, 10))}
              <button style="float:right;font-size:11px" data-unwatch="${w.id}">remove</button></div>
            <div>${esc(w.rationale)}</div></div>`).join("") || '<div class="sub">Empty.</div>'}
          <div class="stack" style="margin-top:10px">
            <select id="w-company">${opts}</select>
            <input id="w-rationale" placeholder="Why track this? (required — future-you will ask)">
            <button class="primary" id="w-add">Add</button><div id="w-msg" class="sub"></div>
          </div>
        </div>
        <div class="panel"><h2>Journal</h2>
          <div class="stack">
            <textarea id="j-content" rows="3" placeholder="Dated note — messy thinking welcome"></textarea>
            <div class="frm">
              <div><label>Company (optional)</label><select id="j-company"><option value="">—</option>${opts}</select></div>
              <div><label>CFA topic (optional)</label><input id="j-topic" placeholder="e.g. FSA — DuPont"></div>
            </div>
            <button class="primary" id="j-add">Add entry</button>
          </div>
          <div style="margin-top:10px">${journal.map(j => `<div class="journal-entry">
            <div class="meta">${esc(j.created_at.slice(0, 10))} ${j.ticker ? "· " + esc(j.ticker) : ""}
              ${j.cfa_topic ? "· 📚 " + esc(j.cfa_topic) : ""}</div>
            <div>${esc(j.content)}</div></div>`).join("") || '<div class="sub">No entries.</div>'}</div>
        </div>
      </div>
    </div>`;

  app.querySelectorAll("[data-thesis]").forEach(btn =>
    btn.addEventListener("click", async () => {
      await api(`/theses/${btn.dataset.thesis}/status`,
        { method: "PATCH", body: JSON.stringify({ status: btn.dataset.status }) });
      viewResearch();
    }));
  app.querySelectorAll("[data-unwatch]").forEach(btn =>
    btn.addEventListener("click", async () => {
      await api(`/watchlist/${btn.dataset.unwatch}`, { method: "DELETE" });
      viewResearch();
    }));
  document.getElementById("th-save").addEventListener("click", async () => {
    try {
      await api("/theses", { method: "POST", body: JSON.stringify({
        company_id: parseInt(document.getElementById("th-company").value),
        statement: document.getElementById("th-statement").value,
        assumptions: document.getElementById("th-assumptions").value.split("\n").filter(Boolean),
        invalidation_triggers: document.getElementById("th-triggers").value.split("\n").filter(Boolean),
        review_date: document.getElementById("th-review").value || null,
      })});
      viewResearch();
    } catch (e) { document.getElementById("th-msg").textContent =
      "Rejected: needs a statement, ≥1 falsifiable assumption, ≥1 trigger. " + e.message; }
  });
  document.getElementById("w-add").addEventListener("click", async () => {
    try {
      await api("/watchlist", { method: "POST", body: JSON.stringify({
        company_id: parseInt(document.getElementById("w-company").value),
        rationale: document.getElementById("w-rationale").value,
      })});
      viewResearch();
    } catch (e) { document.getElementById("w-msg").textContent =
      "Rejected: rationale required (≥10 chars) — that's the point. " + e.message; }
  });
  document.getElementById("j-add").addEventListener("click", async () => {
    const content = document.getElementById("j-content").value.trim();
    if (!content) return;
    await api("/journal", { method: "POST", body: JSON.stringify({
      content,
      company_id: document.getElementById("j-company").value ? parseInt(document.getElementById("j-company").value) : null,
      cfa_topic: document.getElementById("j-topic").value,
    })});
    viewResearch();
  });
}

/* ================================================================== lab */

async function viewLab(section = "hypotheses") {
  const tabs = [["hypotheses", "Hypotheses"], ["baserates", "Base Rates"],
                ["calibration", "Calibration & Ledger"], ["data", "Data Health"]];
  app.innerHTML = `
    <h1>Research Laboratory</h1>
    <div class="tabs">${tabs.map(([k, l]) =>
      `<button class="${k === section ? "active" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>
    <div id="lab-body">${skeleton(6)}</div>`;
  app.querySelectorAll("[data-tab]").forEach(b =>
    b.addEventListener("click", () => location.hash = `#/lab/${b.dataset.tab}`));
  const body = document.getElementById("lab-body");

  if (section === "hypotheses") {
    const br = await api("/live/base-rates");
    body.innerHTML = `<div class="panel">
      <h2>Hypothesis registry — pre-registered in code; failures are permanent records</h2>
      ${Object.entries(br.registry).map(([id, h]) => `
        <div class="journal-entry">
          <div class="meta">${esc(id)} · <span class="chip ${h.status.includes("deferred") ? "grey" : "active"}">${esc(h.status)}</span>
            · family ${esc(h.family)}</div>
          <strong>${esc(h.name)}</strong> — ${esc(h.motivation)}
          <div class="sub" style="margin-top:3px">Spec: ${esc(h.spec)}</div>
        </div>`).join("")}
      <div class="sub" style="margin-top:8px">Admission caps: registered/computed → ±0.25 (provisional) ·
        registered-deferred → SHADOW (0) · validated → ±0.60 · deployed → ±1.00.
        Influence is earned through the lifecycle, mechanically.</div>
    </div>
    <div class="panel"><h2>REG-001 — the regime engine on trial</h2>
      <button class="primary" id="run-reg001">Run REG-001</button><div id="reg001-out"></div></div>`;
    document.getElementById("run-reg001").addEventListener("click", async (e) => {
      e.target.disabled = true;
      const out = document.getElementById("reg001-out");
      out.innerHTML = '<div class="loading">Splitting a decade of momentum episodes…</div>';
      try {
        const r = await api("/live/reg001", { method: "POST" });
        out.innerHTML = `<div class="score-detail" style="margin-top:8px">
          <strong>Verdict: ${esc(r.verdict)}</strong> — ${esc(r.consequence || "")}<br>
          ${esc(JSON.stringify(r, null, 1))}</div>`;
      } catch (err) { out.innerHTML = `<div class="unavail">${esc(err.message)}</div>`; }
      finally { e.target.disabled = false; }
    });
  } else if (section === "baserates") {
    const br = await api("/live/base-rates");
    body.innerHTML = `<div class="panel">
      <h2>Base rates — N<sub>eff</sub>-gated, net-of-cost, from this platform's own studies</h2>
      <div class="sub">Survivorship caveat on every record: universe = current constituents backfilled.</div>
      <div class="tablewrap" style="margin-top:8px"><table><thead><tr>
        <th>Study</th><th>Hyp</th><th>Regime</th><th class="num">N<sub>eff</sub></th><th class="num">N</th>
        <th class="num">Hit</th><th class="num">Median</th><th class="num">Net</th><th class="num">IQR</th></tr></thead>
        <tbody>${br.records.map(r => `
          <tr><td>${esc(r.study_key)}</td><td>${esc(r.registry_ref)}</td><td>${esc(r.regime)}</td>
            <td class="num">${r.n_eff ?? "—"}</td><td class="num sub">${r.n}</td>
            <td class="num">${(r.hit_rate * 100).toFixed(0)}%</td>
            <td class="num">${signed(r.median_excess_pct)}%</td>
            <td class="num" style="color:${(r.net_median_excess_pct ?? 0) > 0 ? "var(--good-text)" : "var(--critical)"}">
              ${r.net_median_excess_pct == null ? "—" : signed(r.net_median_excess_pct) + "%"}</td>
            <td class="num">[${r.iqr}]%</td></tr>`).join("")}
        </tbody></table></div>
      <div style="margin-top:10px"><button class="primary" id="run-studies">Recompute studies</button></div>
    </div>`;
    document.getElementById("run-studies").addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "computing…";
      await api("/live/studies/run", { method: "POST" }); viewLab("baserates");
    });
  } else if (section === "calibration") {
    const [cal, led] = await Promise.all([api("/live/calibration"), api("/live/ledger")]);
    body.innerHTML = `
      <div class="grid2">
        <div class="panel"><h2>Calibration ledger</h2>
          <div class="score-detail">${esc(JSON.stringify(cal, null, 1))}</div>
          <div style="margin-top:8px"><button class="primary" id="score-claims">Score due claims</button></div>
        </div>
        <div class="panel"><h2>Decision ledger (hash-chained, append-only)</h2>
          <div class="hashline">Chain: ${led.chain.intact ? "✓ intact" : "⚠ BROKEN"} · ${led.chain.records ?? 0} records</div>
          <div style="margin-top:8px">${led.records.slice(-20).reverse().map(r => `
            <div class="metric-row" style="cursor:default">
              <span class="m-label">${esc(r.kind)} · ${esc(r.company?.ticker || r.company || "")}
                · ${esc((r.verdict || r.claim_type || "").replaceAll("_", " "))}</span>
              <span class="sub">${esc((r.created_at || "").slice(0, 16))}</span>
              <span class="hashline">${esc((r.hash || "").slice(0, 12))}</span></div>`).join("")}</div>
        </div>
      </div>`;
    document.getElementById("score-claims").addEventListener("click", async (e) => {
      e.target.disabled = true; await api("/live/score", { method: "POST" }); viewLab("calibration");
    });
  } else if (section === "data") {
    const st = await api("/live/status");
    const p = st.datasets.prices, f = st.datasets.fundamentals;
    body.innerHTML = `
      <div class="grid2">
        <div class="panel"><h2>Data quality — decomposed, never a mystery number</h2>
          <div class="tiles" style="grid-template-columns:1fr 1fr">
            <div class="tile"><div class="label">Quality score</div>
              <div class="value"><span class="q-badge ${qClass(st.quality_score)}">${st.quality_score}</span></div></div>
            <div class="tile"><div class="label">Provider</div><div class="value" style="font-size:13px">${esc(st.provider)}</div></div>
          </div>
          ${hbars(Object.fromEntries(Object.entries(st.quality_components).map(([k, v]) => [k, v * 100])))}
          ${st.warnings.map(w => `<div class="breach">⚠ ${esc(w)}</div>`).join("")
            || '<div class="sub" style="margin-top:6px">No warnings.</div>'}
        </div>
        <div class="panel"><h2>Datasets</h2>
          <div class="metric-row" style="cursor:default"><span class="m-label">Prices</span>
            <span class="m-value">${fmtN(p.rows, 0)}</span><span class="m-unit">rows</span></div>
          <div class="sub" style="margin:0 0 6px 2px">${esc(p.coverage)} · ${p.companies} companies ·
            null volume ${p.null_volume_pct}% · ${p.staleness_days}d stale</div>
          <div class="metric-row" style="cursor:default"><span class="m-label">Fundamentals (pit_grade: ${esc(f.pit_grade)})</span>
            <span class="m-value">${f.rows}</span><span class="m-unit">filings</span></div>
          <div class="sub" style="margin:0 0 6px 2px">${f.companies_covered} companies · latest FY${f.latest_fy} ·
            ${f.financial_sector_excluded} financials excluded by design</div>
          <div class="metric-row" style="cursor:default"><span class="m-label">Vault (immutable raw archive)</span>
            <span class="m-value">${st.datasets.vault.unique_blobs}</span><span class="m-unit">blobs</span></div>
          <div class="sub" style="margin:0 0 6px 2px">${((st.datasets.vault.bytes || 0) / 1048576).toFixed(1)} MB ·
            ${st.datasets.vault.artifacts} fetches recorded</div>
          <h2 style="margin-top:10px">Macro series</h2>
          ${st.datasets.macro.map(m => `<div class="metric-row" style="cursor:default">
            <span class="m-label">${esc(m.symbol)}</span>
            <span class="m-value">${esc(m.latest)}</span><span class="m-unit">${m.staleness_days}d</span></div>`).join("")}
          <h2 style="margin-top:10px">Missing datasets (visible, not ignored)</h2>
          <div class="sub">${st.missing_datasets.join(" · ")}</div>
        </div>
      </div>`;
  }
}

/* ============================================================== routing */

async function route() {
  const parts = (location.hash.replace(/^#\//, "") || "dashboard").split("/");
  const [name, arg, sub] = parts;
  document.querySelectorAll("#nav a").forEach(a =>
    a.classList.toggle("active", a.dataset.route === name));
  app.innerHTML = `<div class="skel" style="height:22px;width:220px;margin:4px 0 16px"></div>
    ${skeleton(5)}<div class="grid2">${skeleton(4)}${skeleton(4)}</div>`;
  try {
    if (name === "companies" && arg) await viewCompanyDetail(parseInt(arg), sub || "overview");
    else if (name === "companies") await viewCompanies();
    else if (name === "portfolio") await viewPortfolio();
    else if (name === "research") await viewResearch();
    else if (name === "lab") await viewLab(arg || "hypotheses");
    else await viewDashboard();
  } catch (e) {
    app.innerHTML = `<div class="panel"><strong>Error:</strong> ${esc(e.message)}
      <div class="sub" style="margin-top:6px">If this is a data problem, the
      <a href="#/lab/data">data health page</a> is the place to start.</div></div>`;
  }
}

window.addEventListener("hashchange", route);
refreshStatusStrip();
setInterval(refreshStatusStrip, 120000);
route();
