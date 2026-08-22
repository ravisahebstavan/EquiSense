/* EquiSense workstation (Phase III) — the complete operating interface for
   the research engine. Dark-first, keyboard-first, evidence-native.
   Vanilla JS by design: one file, no build step, decade-maintainable. */
"use strict";

const app = document.getElementById("app");


/* ============================================================== theme */

function applyTheme(mode) {
  document.documentElement.setAttribute("data-theme", mode);
  document.getElementById("theme-btn").textContent = mode === "dark" ? "\u25d0" : "\u25d1";
  localStorage.setItem("eqs_theme", mode);
}
function initTheme() {
  const saved = localStorage.getItem("eqs_theme");
  applyTheme(saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
}
document.getElementById("theme-btn").addEventListener("click", () => {
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
});

/* ============================================================== toasts */

function toast(message, kind = "") {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => { el.classList.add("fade"); setTimeout(() => el.remove(), 260); }, 4200);
}

/* ------------------------------------------------------ shortcut help */

const SHORTCUTS = [
  ["Ctrl K", "Open command palette"], ["g d", "Go to Dashboard"],
  ["g c", "Go to Companies"], ["g p", "Go to Portfolio"], ["g t", "Go to Trading Desk"],
  ["g r", "Go to Research"], ["g s", "Go to Simulation Studio"], ["g l", "Go to Lab"],
  ["r", "Open refresh drawer"],
  ["/", "Also opens the command palette"],
  ["?", "This help"], ["Esc", "Close any overlay"],
];
function openHelp() {
  palette.hidden = true;
  const box = document.getElementById("help-overlay");
  box.hidden = false;
}
document.getElementById("help-btn").addEventListener("click", openHelp);

/* ================================================================ utils */

const fmtN = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" :
    Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: 0 });
const fmtMoney = (v) => v === null || v === undefined ? "—" : "₹" + fmtN(v, 0);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const signed = (v, d = 2) => v == null ? "—" : (v >= 0 ? "+" : "") + fmtN(v, d);

/* Every identifier shown to the user is rendered in frontend form: explicit
   labels first, then a generic snake_case → Title Case fallback. */
const LABELS = {
  long_candidate: "Long Candidate", avoid_short_candidate: "Avoid / Short Candidate",
  abstain_no_edge: "Abstain — No Edge Detected", abstain_disagreement: "Abstain — Engines Disagree",
  abstain_insufficient: "Abstain — Insufficient Data",
  trend: "Trend", value: "Value", quality: "Quality", flow: "Flow",
  macro: "Macro", risk: "Risk", portfolio: "Portfolio Fit",
  agreement: "Engine agreement", coverage: "Evidence coverage",
  base_rate_depth: "Base-rate depth (Nₑᶠᶠ)", calibration_history: "Calibration history",
  risk_budget: "Risk budget", position_cap: "Position cap",
  heat_room: "Heat headroom", liquidity_cap: "Liquidity cap",
  directional_excess: "Directional call", abstention_counterfactual: "Abstention (counterfactual)",
  dossier: "Dossier issued", score: "Claim scored", paper_trade: "Paper fill",
  paper_reset: "Paper account reset",
  registered: "Registered", "registered-deferred": "Registered (deferred)",
  computed: "Computed", validated: "Validated", deployed: "Deployed",
  rejected: "Rejected", retired: "Retired",
  scored_claims: "Scored directional claims", scored_abstentions: "Scored abstentions",
  hit_rate: "Hit rate", mean_stated_probability: "Mean stated probability",
  mean_brier: "Mean Brier score", wrongful_abstention_rate: "Wrongful-abstention rate",
  wrongful_threshold_pct: "Wrongful threshold", mean_abstained_excess_pct: "Mean abstained excess",
  conditioning_helps: "Conditioning helps", no_measurable_value: "No measurable value",
  conditioning_hurts: "Conditioning hurts", insufficient_data: "Insufficient data",
  mean_abs_forecast_error_pct: "Mean abs. forecast error (magnitude)",
  rmse_forecast_error_pct: "RMSE forecast error (magnitude)",
  interim_on_track_rate: "Interim checkpoints on-track rate",
  mean_abs_interim_forecast_error_pct: "Mean abs. interim forecast error",
  interim_checkpoints: "Interim checkpoints scored", checkpoint: "Interim checkpoint",
  predicted_excess_pct: "Predicted excess return", forecast_error_pct: "Forecast error",
};
const human = (s) => LABELS[s] ?? String(s ?? "")
  .replaceAll("_", " ").replace(/\b[a-z]/g, c => c.toUpperCase());

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    /* An expired or cleared token is a SESSION event, not a data error. Left as
       a thrown "401: unauthorized" it surfaces as cryptic red text on whatever
       panel happened to ask — and the 90-day cookie guarantees this eventually
       happens mid-session. Reloading hands the request back to the server,
       which serves the login page for a non-API path. */
    if (r.status === 401) {
      stopQuotes();
      document.body.innerHTML =
        '<div style="font:15px system-ui;padding:40px">Session expired — returning to sign-in…</div>';
      location.replace("/");
      return new Promise(() => {});      // never resolves; the page is leaving
    }
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

let autoRefreshing = false;

function isEditingForm() {
  const t = document.activeElement?.tagName;
  return t === "INPUT" || t === "TEXTAREA" || t === "SELECT";
}

async function maybeAutoRefresh(st) {
  /* Stay-fresh policy: if prices are stale beyond a weekend and we haven't
     auto-refreshed in the last 6 hours, run the pipeline silently in the
     background — no clicks required. (A daily server-side cron does the same
     on the hosted deployment.) */
  const stale = st.datasets.prices.staleness_days;
  const last = parseInt(localStorage.getItem("eqs_auto_refresh") || "0");
  if (stale === null || stale <= 2 || refreshing || autoRefreshing) return;
  if (Date.now() - last < 6 * 3600 * 1000) return;
  autoRefreshing = true;
  localStorage.setItem("eqs_auto_refresh", String(Date.now()));
  try {
    await api("/live/refresh", { method: "POST" });
    cache.companies = null;
    await refreshStatusStrip();
    if (!isEditingForm()) route();  // never yank the DOM out from under an in-progress edit
  } catch { /* surfaced via status warnings on next poll */ }
  finally { autoRefreshing = false; }
}

async function refreshStatusStrip() {
  const el = document.getElementById("status-strip");
  try {
    const [st, rg] = await Promise.all([api("/live/status"), api("/live/regime")]);
    /* Research and Lab don't move with quotes, but they DO go stale the moment
       the pipeline lands new prices or recomputes studies. Fingerprint what
       they actually render off, and silently redraw only when it changes —
       otherwise those pages showed yesterday's studies until a manual reload,
       which on a page whose whole job is data trust is the worst place to be
       quietly wrong. */
    const stamp = [st.datasets.prices.latest, st.datasets.prices.rows,
                   st.datasets.base_rates.computed_at, st.datasets.ledger.records].join("|");
    const moved = cache.dataStamp !== undefined && cache.dataStamp !== stamp;
    cache.dataStamp = stamp;
    cache.status = st; cache.regime = rg;
    if (moved && !LIVE_VIEWS.has(currentView()) && !isEditingForm()) route({ silent: true });
    maybeAutoRefresh(st);
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
      ${cache.market ? `<span class="seg">Market <strong>${cache.market.open ? "OPEN" : "closed"}</strong> · ${esc(cache.market.ist)} IST</span>` : ""}
      ${cache.quotesAt ? `<span class="seg" title="${cache.market && cache.market.open
          ? "Live quotes pulled every 5 minutes while this page is open; the view re-renders on each pull."
          : "The exchange is closed, so today's close is final and further polling cannot change it. Quotes resume at 09:15 IST."}">Quotes
        <strong>${cache.quotesAt.toLocaleTimeString()}</strong>${cache.market && !cache.market.open
          ? ' <span class="sub">· session closed, prices final</span>' : ""}</span>` : ""}
      ${autoRefreshing ? '<span class="seg" style="color:var(--accent)">⟳ auto-refreshing…</span>' : ""}
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
  running_studies: "Running hypotheses",
  registering_forecasts: "Registering forecasts", scoring_claims: "Scoring claims",
  publishing: "Publishing snapshot", autopilot: "Autopilot", pipeline: "Pipeline",
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

/* Sync ≠ Refresh. Refresh re-ingests and re-runs every hypothesis (minutes,
   hits providers). Sync only re-derives what the stored data already implies —
   snapshot, today's forecasts, due scoring, autopilot — which is the honest
   answer to "is what I'm looking at consistent with the database?" and takes
   seconds. Separated because reaching for a multi-minute ingest to fix a stale
   panel is how people learn not to press the button at all. */
document.getElementById("sync-btn").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  if (btn.disabled) return;
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "⟳ Syncing…";
  try {
    const r = await api("/live/realign", { method: "POST" });
    const bits = [`${r.snapshot_companies} companies`];
    if (r.forecasts_registered) bits.push(`${r.forecasts_registered} forecast(s)`);
    if (r.claims_scored) bits.push(`${r.claims_scored} claim(s) scored`);
    if (r.checkpoints_scored) bits.push(`${r.checkpoints_scored} checkpoint(s)`);
    if (r.autopilot) bits.push(`autopilot ${r.autopilot.entries}↑ ${r.autopilot.exits}↓`);
    if (r.forecasts_error) bits.push(`forecasts failed: ${r.forecasts_error}`);
    toast("Synced — " + bits.join(" · "));
    cache.companies = null;
    await refreshStatusStrip();
    if (!isEditingForm()) await route({ silent: true });
  } catch (err) {
    toast("Sync failed: " + err.message);
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
});
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
  { label: "Go to Trading Desk", k: "gt", run: () => location.hash = "#/trading" },
  { label: "Edit Investor Profile & Limits", k: "", run: () => location.hash = "#/portfolio/profile" },
  { label: "Go to Research", k: "gr", run: () => location.hash = "#/research" },
  { label: "Go to Lab", k: "gl", run: () => location.hash = "#/lab" },
  { label: "Go to Markets (derivatives, risk, cross-asset)", k: "gm", run: () => location.hash = "#/markets" },
  { label: "Go to Simulation Studio (Monte Carlo, VaR, backtest)", k: "gs", run: () => location.hash = "#/simulation" },
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
  if (e.key === "Escape") { closePalette(); drawer.hidden = true;
    document.getElementById("help-overlay").hidden = true; return; }
  if (e.key === "?") { e.preventDefault(); openHelp(); return; }
  if (pendingG) {
    pendingG = false;
    const map = { d: "dashboard", c: "companies", p: "portfolio", t: "trading", r: "research", l: "lab", m: "markets", s: "simulation" };
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
      <span class="sub">(${esc(human(e.cluster))} · strength ${signed(e.strength, 2)}${
        e.admission_weight != null && e.admission_weight < 1
          ? ` · counts at ×${fmtN(e.admission_weight, 2)}` : ""})</span>
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
      <div class="tile"><div class="label">Binding constraint</div><div class="value" style="font-size:14px">${esc(human(d.sizing.binding_constraint))}</div></div>
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
  const executeBtn = (d.synthesis.verdict === "long_candidate" && d.sizing
    && d.sizing.recommended_shares > 0)
    ? `<button class="primary" id="paper-exec" data-shares="${d.sizing.recommended_shares}"
         data-hash="${esc(d.ledger.hash)}">
         ▶ Execute in paper account (${d.sizing.recommended_shares} sh)</button>
       <span class="sub" id="paper-exec-msg"></span>` : "";
  return `
    <div class="verdict-banner ${s.verdict}">
      <span class="verdict ${s.verdict}">${esc(human(s.verdict))}</span>
      <span>net ${signed(s.net_score, 3)} · ${esc(human(s.conviction_band))} conviction · dispersion ${s.dispersion}</span>
      <span class="sub">as of ${esc(d.as_of)} · regime ${esc(d.regime.label)} (context)</span>
      ${executeBtn}
    </div>
    ${s.dissent.length ? `<div class="breach" style="margin-top:6px">${s.dissent.map(esc).join("<br>")}</div>` : ""}
    <div class="sub" style="margin:6px 0">${s.notes.map(esc).join(" ")}</div>
    <h2 style="margin-top:8px">Cluster scores (uniform provisional weights)</h2>
    ${clusterBars(Object.fromEntries(Object.entries(s.cluster_scores).map(([k, v]) => [human(k), v])))}
    <div class="score-detail" style="margin-top:8px">
      <strong>Confidence ${conf.score}</strong> —
      ${Object.entries(conf.components).map(([k, v]) =>
        `<span style="display:inline-block;margin:1px 14px 1px 0">${esc(human(k))}: <strong>${fmtN(v, 2)}</strong></span>`).join("")}
      <div style="margin-top:3px">${esc(conf.label)}</div></div>
    ${d.trend_value_tension?.inputs?.quadrant
      ? `<div class="breach" style="color:var(--series-3);margin-top:8px">TVT: ${esc(d.trend_value_tension.inputs.quadrant)}</div>` : ""}
    <h2 style="margin-top:12px">Evidence (${d.evidence.length})</h2>${evid}
    ${sizing}
    <h2 style="margin-top:12px">What am I missing?</h2>
    <div class="score-detail">
      Evidence groups without data: ${s.coverage.clusters_missing.map(human).join(", ") || "none"} ·
      shadow items: ${d.missing_information.shadow_evidence}<br>
      Not yet ingested: ${d.missing_information.not_ingested.join(", ")}<br>
      ${esc(d.missing_information.note)}</div>
    <div class="hashline" style="margin-top:8px">Pre-registered: ${esc(d.ledger.hash)} ·
      claim: ${esc(human(d.ledger.claim.type))}${d.ledger.claim.stated_probability
        ? `, P(hit)=${d.ledger.claim.stated_probability}` : ""} ·
      scores after ${esc(d.ledger.claim.score_after)}</div>`;
}


/* ------------------------------------------------------- interactive chart */

function renderPriceChart(containerId, rows, height = 320) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (typeof LightweightCharts === "undefined" || !rows || rows.length < 5) {
    el.innerHTML = sparkline("Price", rows.map(r => ({ period: r.time, value: r.value })), "");
    return; // graceful fallback — CDN blocked or thin data, never a blank panel
  }
  el.innerHTML = "";
  const styles = getComputedStyle(document.documentElement);
  const cssvar = (name) => styles.getPropertyValue(name).trim();
  const chart = LightweightCharts.createChart(el, {
    width: el.clientWidth, height,
    layout: { background: { color: "transparent" }, textColor: cssvar("--ink-2"),
             fontSize: 11, fontFamily: "system-ui" },
    grid: { vertLines: { color: cssvar("--grid") }, horzLines: { color: cssvar("--grid") } },
    rightPriceScale: { borderColor: cssvar("--grid") },
    timeScale: { borderColor: cssvar("--grid") },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  const series = chart.addAreaSeries({
    lineColor: cssvar("--series-1"), topColor: "rgba(57,135,229,0.28)",
    bottomColor: "rgba(57,135,229,0)", lineWidth: 2,
  });
  series.setData(rows);
  chart.timeScale().fitContent();
  const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
  ro.observe(el);
  el._chart = chart;
  return chart;
}

function chartRangeButtons(containerId, allRows) {
  const ranges = [["3M", 63], ["6M", 126], ["1Y", 252], ["3Y", 756], ["All", allRows.length]];
  return `<div class="chart-range">${ranges.map(([label, n], i) =>
    `<button data-range="${n}" class="${i === 2 ? "active" : ""}">${label}</button>`).join("")}</div>
    <div class="pricechart-wrap"><div id="${containerId}" class="pricechart"></div></div>`;
}
function wireChartRanges(root, containerId, allRows) {
  root.querySelectorAll("[data-range]").forEach(btn => {
    btn.addEventListener("click", () => {
      root.querySelectorAll("[data-range]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const n = parseInt(btn.dataset.range);
      renderPriceChart(containerId, allRows.slice(-n));
    });
  });
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
      <td class="num">${fmtMoney(r.price)}${staleBadge(r)}
        <div class="sub" style="color:${(r.chg_1d_pct ?? 0) >= 0 ? "var(--good-text)" : "var(--critical)"}">${signed(r.chg_1d_pct, 2)}%</div></td>
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
        ${pf.data_integrity && !pf.data_integrity.ok
          ? `<div class="breach">⚠ Ledger integrity: ${esc(pf.data_integrity.warning)}</div>` : ""}
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
          <span class="m-label">${esc(human(r.kind))} · ${esc(r.company?.ticker || r.company || "")} ·
            ${esc(human(r.verdict || r.claim_type || ""))}</span>
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
      <td class="num">${fmtMoney(c.price)}${staleBadge(c)}
        <div class="sub" style="color:${(c.chg_1d_pct ?? 0) >= 0 ? "var(--good-text)" : "var(--critical)"}">${signed(c.chg_1d_pct, 2)}%</div></td>
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
  if (d.error) {
    app.innerHTML = `<div class="panel"><div class="empty">${esc(d.error)}</div></div>`;
    return;
  }
  const c = d.company;
  const tabs = [["overview", "Overview"], ["dossier", "Dossier"], ["memory", "Memory"], ["ai", "AI Desk"]];

  app.innerHTML = `
    <h1>${esc(c.name)} <span class="sub">${esc(c.ticker)} · ${esc(c.sector)} · ${fmtMoney(c.price)}${staleBadge(c)}
      · ${esc(d.period)}</span></h1>
    <div class="sub">${esc(c.description)}</div>
    <div class="tabs">${tabs.map(([k, l]) =>
      `<button class="${k === tab ? "active" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>
    <div id="tab-body">${skeleton(6)}</div>`;
  app.querySelectorAll("[data-tab]").forEach(b =>
    b.addEventListener("click", () => location.hash = `#/companies/${id}/${b.dataset.tab}`));

  const body = document.getElementById("tab-body");
  if (tab === "overview") {
    renderCompanyOverview(body, d, id);
    // Delivery % is India-specific and was fully built but unreachable from the
    // UI. It separates real ownership transfer from intraday churn, which no
    // price or volume series can distinguish.
    const slot = document.createElement("div");
    slot.id = "delivery-slot";
    body.appendChild(slot);
    renderDeliveryPanel(slot, c.ticker);
  }
  else if (tab === "dossier") renderCompanyDossier(body, id);
  else if (tab === "memory") renderCompanyMemory(body, id);
  else if (tab === "ai") renderCompanyAi(body, id);
}

async function renderDeliveryPanel(host, ticker) {
  if (!host || !ticker) return;
  const d = await api(`/markets/delivery/${encodeURIComponent(ticker)}`).catch(() => null);
  if (!d || !d.available) {
    host.innerHTML = `<div class="panel"><h2>Delivery %</h2>
      <div class="sub">${esc((d && d.reason) || "No delivery data for this name yet. " +
        "The NSE archive yields one file per trading day, so this series accumulates " +
        "forward and cannot be backfilled.")}</div></div>`;
    return;
  }
  const hi = d.delivery_pct >= d.mean_delivery_pct;
  const spark = (d.history || []).map(h => h.delivery_pct);
  const max = Math.max(...spark, 1), min = Math.min(...spark, 0);
  const bars = spark.map(v => {
    const pct = max > min ? (v - min) / (max - min) * 100 : 50;
    return `<div style="flex:1;display:flex;align-items:flex-end;height:38px">
      <div style="width:100%;height:${Math.max(4, pct)}%;background:var(--series-1);opacity:.75"></div></div>`;
  }).join("");
  host.innerHTML = `
    <div class="panel"><h2>Delivery % — accumulation vs churn</h2>
      <div class="tiles" style="grid-template-columns:repeat(3,1fr)">
        <div class="tile"><div class="label">Latest (${esc(d.as_of)})</div>
          <div class="value ${hi ? "pos" : "neg"}">${fmtN(d.delivery_pct, 1)}%</div></div>
        <div class="tile"><div class="label">Own mean</div>
          <div class="value">${fmtN(d.mean_delivery_pct, 1)}%</div></div>
        <div class="tile"><div class="label">Percentile vs own history</div>
          <div class="value">${fmtN(d.percentile_vs_own_history, 0)}</div></div>
      </div>
      <div style="display:flex;gap:2px;margin-top:8px">${bars}</div>
      <div class="sub" style="margin-top:6px">
        Share of traded volume actually taken to demat rather than squared off
        intraday — the one free measure that separates real ownership transfer
        from churn. Ranked against this stock's OWN history, because the normal
        level differs enormously between a large-cap and a retail-heavy small-cap.
        ${d.observations} observation${d.observations === 1 ? "" : "s"} so far;
        the series accumulates forward and cannot be backfilled.</div>
    </div>`;
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
    Every number expands to its formula, inputs, and caveats.</div>
    <div class="panel"><h2>Price</h2><div id="price-chart-panel">${skeleton(3)}</div></div>
    ${cardsHtml}${sectorAttrs}`;
  wireToggles(body); wireSparks(body);
  api(`/companies/${id}/prices`).then(rows => {
    const panel = document.getElementById("price-chart-panel");
    if (!panel) return;
    panel.innerHTML = chartRangeButtons("price-chart", rows);
    renderPriceChart("price-chart", rows.slice(-252));
    wireChartRanges(panel, "price-chart", rows);
  }).catch(() => {
    const panel = document.getElementById("price-chart-panel");
    if (panel) panel.innerHTML = '<div class="empty">Price history unavailable.</div>';
  });
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
    out.innerHTML = skeleton(6);
    try {
      out.innerHTML = renderDossier(await api(`/live/dossier/${id}`, { method: "POST" }));
      const exec = out.querySelector("#paper-exec");
      if (exec) exec.addEventListener("click", async () => {
        exec.disabled = true;
        const msg = out.querySelector("#paper-exec-msg");
        try {
          const r = await api("/paper/trade", { method: "POST", body: JSON.stringify({
            company_id: id, side: "buy",
            quantity: parseInt(exec.dataset.shares),
            dossier_hash: exec.dataset.hash })});
          msg.innerHTML = `filled ${r.quantity} @ ₹${fmtN(r.fill_price, 1)} —
            <a href="#/trading">view trading desk</a>`;
        } catch (err) { msg.textContent = err.message; exec.disabled = false; }
      });
    }
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
          <span class="m-label">${esc(human(s.claim_type || "claim"))} → realized ${signed(s.realized_excess_pct)}%</span>
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

async function viewPortfolio(sub = "real") {
  if (sub === "paper") { location.hash = "#/trading"; return; }
  const tabs = [["real", "Real Book"], ["profile", "Profile & Limits"]];
  app.innerHTML = `
    <h1>Portfolio Desk</h1>
    <div class="tabs">${tabs.map(([k, l]) =>
      `<button class="${k === sub ? "active" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>
    <div id="pf-body">${skeleton(5)}</div>`;
  app.querySelectorAll("[data-tab]").forEach(b =>
    b.addEventListener("click", () => location.hash = `#/portfolio/${b.dataset.tab}`));
  const body = document.getElementById("pf-body");
  if (sub === "profile") return renderProfileEditor(body);
  return renderRealBook(body);
}

async function renderRealBook(body) {
  const [p, risk, companies, txns] = await Promise.all([
    api("/portfolio"), api("/live/portfolio-risk"), getCompanies(),
    api("/transactions")]);

  const txnList = txns.length ? `<div class="panel"><h2>Transaction ledger (source of truth)</h2>
    <div class="tablewrap"><table><thead><tr><th>Date</th><th>Company</th><th>Side</th>
      <th class="num">Qty</th><th class="num">Price</th><th class="num">Fees</th><th></th></tr></thead>
      <tbody>${txns.map(t => `<tr><td>${esc(t.trade_date)}</td><td><strong>${esc(t.ticker)}</strong></td>
        <td><span class="chip ${t.side === "buy" ? "active" : "invalidated"}">${t.side}</span></td>
        <td class="num">${fmtN(t.quantity, 0)}</td><td class="num">${fmtN(t.price, 2)}</td>
        <td class="num">${fmtN(t.fees, 2)}</td>
        <td><button data-deltx="${t.id}" style="font-size:11px">delete</button></td></tr>`).join("")}
      </tbody></table></div></div>` : "";

  if (!p.holdings.length) {
    body.innerHTML = `
      <div class="panel">
        <div class="empty">No open positions. Record a trade below — positions, P&amp;L, XIRR,
          concentration, correlation and portfolio heat all derive from the transaction ledger.
          Prefer risk-free experimentation? Use the <a href="#/portfolio/paper">Paper Trading</a> desk.</div>
      </div>
      ${txnFormHtml(companies)}${txnList}`;
    wireTxnForm(() => viewPortfolio("real"));
    body.querySelectorAll("[data-deltx]").forEach(b => b.addEventListener("click", async () => {
      await api(`/transactions/${b.dataset.deltx}`, { method: "DELETE" }); toast("Transaction deleted."); viewPortfolio("real");
    }));
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
  body.innerHTML = `
    <div class="sub" style="margin-bottom:8px">As of ${esc(p.as_of)} · derived from the transaction ledger</div>
    <div class="tiles">
      <div class="tile"><div class="label">Value</div><div class="value">${fmtMoney(p.total_value)}</div></div>
      <div class="tile"><div class="label">Invested</div><div class="value">${fmtMoney(p.total_invested)}</div></div>
      <div class="tile"><div class="label">Unrealized</div><div class="value ${p.unrealized_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(p.unrealized_pnl)}</div></div>
      <div class="tile"><div class="label">Realized</div><div class="value ${p.realized_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(p.realized_pnl)}</div></div>
      <div class="tile"><div class="label">XIRR</div><div class="value ${(x.value ?? 0) >= 0 ? "pos" : "neg"}">${fmtN(x.value, 2)}%</div></div>
    </div>
    ${p.data_integrity && !p.data_integrity.ok ? `<div class="panel"><h2>⚠ Ledger integrity</h2>
      <div class="breach">${esc(p.data_integrity.warning)}</div>
      <div class="tablewrap"><table><thead><tr><th>Company</th>
        <th class="num">Sold with no matching lot</th></tr></thead><tbody>
        ${Object.entries(p.data_integrity.unmatched_sells).map(([t, q]) =>
          `<tr><td>${esc(t)}</td><td class="num">${fmtN(q, 4)}</td></tr>`).join("")}
      </tbody></table></div>
      <div class="sub">Every figure on this page is derived from the ledger, so
        correct the entries before reading anything below as accurate. A fully
        oversold name does not appear in Holdings at all — its quantity nets to
        zero — which is why this check exists separately.</div></div>` : ""}
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
    ${txnFormHtml(companies)}${txnList}
    <div class="panel">${aiBlock("pfnarrate", "Brief me on this book (grounded)")}</div>`;
  body.querySelectorAll("tr[data-company]").forEach(tr =>
    tr.addEventListener("click", () => location.hash = `#/companies/${tr.dataset.company}`));
  wireTxnForm(() => viewPortfolio("real"));
  body.querySelectorAll("[data-deltx]").forEach(b => b.addEventListener("click", async () => {
    await api(`/transactions/${b.dataset.deltx}`, { method: "DELETE" }); toast("Transaction deleted."); viewPortfolio("real");
  }));
  wireAi(body, { pfnarrate: () => api("/ai/narrate/portfolio", { method: "POST" }) });
}

/* --------------------------------------------------------- paper trading */

function equityChart(curve, benchmark) {
  if (!curve || curve.length < 2) return "";
  const vals = curve.map(p => p.equity);
  const min = Math.min(...vals), max = Math.max(...vals), range = max - min || 1;
  const W = 900, H = 130, PAD = 4, step = (W - 2 * PAD) / (vals.length - 1);
  const xy = vals.map((v, i) => [PAD + i * step, H - PAD - ((v - min) / range) * (H - 2 * PAD)]);
  const poly = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${PAD},${H} ${poly} ${xy[xy.length - 1][0].toFixed(1)},${H}`;
  return `<div class="spark" style="padding:10px 12px 6px">
    <div class="t">Account equity — ${esc(curve[0].date)} → ${esc(curve[curve.length - 1].date)}</div>
    <div class="v">${fmtMoney(vals[vals.length - 1])}
      <span class="sub" style="font-weight:400">· window ${fmtMoney(min)} – ${fmtMoney(max)}</span></div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:110px">
      <polygon class="area" points="${area}"></polygon>
      <polyline class="line" points="${poly}"></polyline>
    </svg></div>`;
}

function renderApReport(r) {
  if (!r) return '<div class="empty">No autopilot runs yet.</div>';
  const li = (arr, cls) => arr.map(x => `<div class="${cls}" style="margin:2px 0">
    ${typeof x === "string" ? esc(x)
      : esc(`${x.ticker}: ${x.quantity} sh @ ₹${fmtN(x.fill_price, 1)} — ${x.reason}`)}</div>`).join("");
  return `<div class="score-detail"><strong>Last run ${esc((r.ran_at || "").slice(0, 16))}</strong> ·
    ${r.entries.length} entries · ${r.exits.length} exits · ${r.skipped.length} skips<br>
    ${li(r.entries, "sub")}${li(r.exits, "breach")}
    <details class="ctx"><summary>Skips (with reasons)</summary>${li(r.skipped, "sub")}</details></div>`;
}

async function viewTrading() {
  const [a, companies, cands, learn, ap] = await Promise.all([
    api("/paper"), getCompanies(), api("/live/candidates"), api("/live/learning"),
    api("/autopilot")]);
  const body = app;
  const opts = companies.map(c => `<option value="${c.id}" data-price="${c.price}">${esc(c.ticker)} — ${esc(c.name)}</option>`).join("");
  const alpha = a.alpha_pct;
  const mkt = cache.market;

  const candRows = cands.candidates.length ? cands.candidates.map((c, i) => `
    <div class="evi ${c.tradable ? "long" : ""}" style="${c.tradable ? "" : "opacity:.75"}">
      <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
        <strong style="font-size:14px">${i + 1}. ${esc(c.ticker)}</strong>
        <span class="sub">${esc(c.name)} · ${esc(c.sector)} · ₹${fmtN(c.price, 1)}</span>
        <span class="chip ${c.conviction_band}">${esc(human(c.conviction_band))} conviction</span>
        <span class="sub">net ${signed(c.net_score)} · dispersion ${c.dispersion}</span>
        ${c.tradable ? `<button class="primary" data-exec-cid="${c.id}"
          data-exec-qty="${c.sizing.shares}" style="margin-left:auto">
          ▶ Buy ${c.sizing.shares} sh (${fmtMoney(c.sizing.value)})</button>`
          : '<span class="chip closed" style="margin-left:auto">not tradable</span>'}
      </div>
      <div class="sub" style="margin-top:4px">Why: ${c.drivers.map(esc).join(" · ")}</div>
      ${c.dissent.length ? `<div class="breach">${c.dissent.map(esc).join(" · ")}</div>` : ""}
      <div class="sub">Stop ${fmtN(c.sizing.stop_distance_pct, 1)}% ·
        sized by ${esc(human(c.sizing.binding))} ·
        round trip ≈ ${c.round_trip_cost_pct}% · breakeven move ${c.breakeven_move_pct}%</div>
      ${c.gates.map(g => `<div class="sub" style="color:${g.startsWith("failed") ? "var(--critical)" : "var(--serious)"}">⚠ ${esc(g)}</div>`).join("")}
    </div>`).join("")
    : `<div class="empty">No trade clears every gate right now — in the current
       ${esc(cands.regime)} regime, standing aside IS the defensible position.
       The system only shows trades it can justify end to end.</div>`;

  const posts = learn.cluster_posteriors;
  const learnBars = Object.keys(posts).length
    ? Object.entries(posts).map(([k, p]) => `
      <div class="hbar"><span class="lbl">${esc(human(k))} (${p.wins}/${p.n})</span>
        <div class="track"><div class="fill ${p.posterior_mean >= 0.5 ? "q-high" : "q-low"}"
          style="width:${p.posterior_mean * 100}%"></div></div>
        <span class="pct">${(p.posterior_mean * 100).toFixed(0)}%</span></div>`).join("")
    : '<div class="empty">No scored outcomes yet — posteriors populate as claims reach their horizons.</div>';

  const outcomes = learn.recent_outcomes.length ? learn.recent_outcomes.map(o => `
    <div class="metric-row" style="cursor:default">
      <span class="m-label">${esc(o.company)} · ${esc(human(o.verdict))} →
        realized ${signed(o.realized_excess_pct)}%${o.predicted_excess_pct != null
          ? ` (predicted ${signed(o.predicted_excess_pct)}%, error ${signed(o.forecast_error_pct)}pp)` : ""}</span>
      <span class="m-value" style="color:${o.hit ? "var(--good-text)" : "var(--critical)"}">
        ${o.hit ? "hit" : "miss"}</span>
      <span class="m-unit">B ${fmtN(o.brier, 2)}</span></div>`).join("")
    : '<div class="empty">Outcomes appear here as claim horizons expire (126 trading days).</div>';

  const checkpoints = (learn.recent_checkpoints || []).length ? learn.recent_checkpoints.map(c => `
    <div class="metric-row" style="cursor:default">
      <span class="m-label">${esc(c.company)} · T+${c.elapsed_days}d of ${c.horizon_days}d →
        realized-so-far ${signed(c.realized_so_far_pct)}%
        vs expected ${c.expected_so_far_pct == null ? "—" : signed(c.expected_so_far_pct) + "%"}</span>
      <span class="m-value" style="color:${c.on_track ? "var(--good-text)" : "var(--critical)"}">
        ${c.on_track == null ? "—" : c.on_track ? "on track" : "off track"}</span>
      <span class="m-unit">err ${c.forecast_error_pct == null ? "—" : signed(c.forecast_error_pct) + "pp"}</span></div>`).join("")
    : '<div class="empty">Interim checkpoints fire ~1/4 of the way through a claim\'s horizon — an early read on prediction drift, well before the full claim matures.</div>';

  body.innerHTML = `
    <h1>Trading Desk
      <span class="sub">${mkt ? `market ${mkt.open ? "OPEN" : "closed"} · ${esc(mkt.ist)} IST · ` : ""}
      prices refresh every 5 min while this site is open (≤15 min exchange delay) ·
      paper account, real executable prices</span></h1>
    <div class="sub" style="margin:4px 0 10px">Everything below is real-data grounded:
      candidates are reasoned across the full universe (patterns + fundamentals + risk,
      percentile-weighted, admission-capped), gated on liquidity and after-tax costs, sized
      for this account, and every fill is pre-registered in the tamper-evident ledger —
      trades you could place at a real broker as shown.</div>
    <div class="tiles">
      <div class="tile"><div class="label">Equity</div><div class="value">${fmtMoney(a.equity)}</div></div>
      <div class="tile"><div class="label">Cash</div><div class="value">${fmtMoney(a.cash)}</div></div>
      <div class="tile"><div class="label">Positions</div><div class="value">${fmtMoney(a.positions_value)}</div></div>
      <div class="tile"><div class="label">Total return</div>
        <div class="value ${(a.total_return_pct ?? 0) >= 0 ? "pos" : "neg"}">${signed(a.total_return_pct)}%</div></div>
      <div class="tile"><div class="label">Alpha vs ${esc((a.benchmark || {}).index || "NIFTY 500")}</div>
        <div class="value ${(alpha ?? 0) >= 0 ? "pos" : "neg"}">${alpha == null ? "—" : signed(alpha) + "%"}</div>
        <div class="sub">${a.benchmark
          ? "same cashflows in " + esc(a.benchmark.index || "the index") + ": "
            + signed(a.benchmark.total_return_pct) + "%"
            + (a.benchmark.fell_back ? " (NIFTY 500 unavailable — fell back)" : "")
          : "trade to activate"}</div>
        ${a.alpha_vs_nifty50_pct != null ? `<div class="sub">vs NIFTY 50:
          <strong class="${a.alpha_vs_nifty50_pct >= 0 ? "pos" : "neg"}">${
          signed(a.alpha_vs_nifty50_pct)}%</strong> — the narrower index is easier
          to beat, so the gap between these two is size premium, not skill</div>` : ""}</div>
    </div>
    ${a.curve ? `<div class="panel">${equityChart(a.curve, a.benchmark)}
      ${a.alpha_note ? `<div class="sub" style="margin-top:8px">${esc(a.alpha_note)}</div>` : ""}</div>` : ""}

    <div class="panel"><h2>Autopilot — the system trades this book on policy</h2>
      <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
        <button class="${ap.config.enabled ? "" : "primary"}" id="ap-toggle">
          ${ap.config.enabled ? "Disable autopilot" : "Enable autopilot"}</button>
        <span class="chip ${ap.config.enabled ? "active" : "closed"}">
          ${ap.config.enabled ? "ENABLED — runs after every data refresh" : "disabled"}</span>
        <button id="ap-run">Run once now</button>
      </div>
      <div class="frm" style="margin-top:10px">
        <div><label>Max new positions / run</label><input id="ap-max" type="number" min="0" max="10" value="${ap.config.max_new_per_run}"></div>
        <div><label>Max open positions</label><input id="ap-open" type="number" min="1" max="20" value="${ap.config.max_open_positions}"></div>
        <div><label>Cash reserve %</label><input id="ap-cash" type="number" min="0" max="90" value="${ap.config.cash_reserve_pct}"></div>
        <div><label>Forecasts / day</label>
          <input id="ap-forecasts" type="number" min="0" max="100" value="${ap.config.daily_forecasts ?? 25}"></div>
        <div><label>Time exit (days)</label><input id="ap-days" type="number" min="30" max="730" value="${ap.config.time_exit_days}"></div>
      </div>
      <button id="ap-save" style="margin-top:2px">Save policy</button>
      <span class="sub" id="ap-msg" style="margin-left:8px"></span>
      <div id="ap-report" style="margin-top:10px">${renderApReport(ap.last_run)}</div>
      <div class="sub" style="margin-top:6px">Exits fire on stop breach (2.5× daily vol below
        avg cost), time (claim horizon), or verdict flip; entries take top qualified candidates
        within policy caps. Every action and every skip is reasoned, ledger-chained, and feeds
        the learning loop via full dossiers.</div>
    </div>

    <div class="panel"><h2>Qualified trade candidates — ${cands.scanned} companies reasoned,
      ${cands.verdict_counts.abstain} abstained</h2>
      <div class="sub" style="margin-bottom:8px">As of ${esc(cands.as_of)} ·
        regime ${esc(cands.regime)} · weights: ${esc(cands.weights_status)}</div>
      ${candRows}
      <div class="sub" style="margin-top:8px">${esc(cands.discipline_note)}</div>
    </div>

    <div class="grid2">
      <div class="panel"><h2>Open positions</h2>
        ${a.positions.length ? `<div class="tablewrap"><table><thead><tr><th>Company</th>
          <th class="num">Qty</th><th class="num">Avg</th><th class="num">Price</th>
          <th class="num">Value</th><th class="num">Unrealized</th><th></th></tr></thead><tbody>
          ${a.positions.map(p => `<tr><td><strong>${esc(p.ticker)}</strong>
            <div class="sub">${esc(p.sector)}</div></td>
            <td class="num">${fmtN(p.quantity, 0)}</td><td class="num">${fmtN(p.avg_cost, 1)}</td>
            <td class="num">${fmtN(p.price, 1)}</td><td class="num">${fmtMoney(p.value)}</td>
            <td class="num" style="color:${p.unrealized_pnl >= 0 ? "var(--good-text)" : "var(--critical)"}">${fmtMoney(p.unrealized_pnl)}</td>
            <td><button data-close="${p.company_id}" data-qty="${p.quantity}" style="font-size:11px">close</button></td>
          </tr>`).join("")}</tbody></table></div>`
          : '<div class="empty">No open paper positions — execute a qualified candidate above, or place a manual trade.</div>'}
        <h2 style="margin-top:14px">Manual trade</h2>
        <div class="frm">
          <div><label>Company</label><select id="pp-company">${opts}</select></div>
          <div><label>Side</label><select id="pp-side"><option value="buy">Buy</option><option value="sell">Sell</option></select></div>
          <div><label>Quantity</label><input id="pp-qty" type="number" min="1" step="1" placeholder="10"></div>
        </div>
        <div class="sub" id="pp-price-hint" style="margin-bottom:8px"></div>
        <button class="primary" id="pp-save">Execute at latest price</button>
        <div id="pp-msg" class="sub" style="margin-top:6px"></div>
      </div>
      <div class="panel"><h2>How the system learns from its trades</h2>
        <div class="sub" style="margin-bottom:8px">${esc(learn.how_it_learns)}</div>
        <h2>Cluster reliability posteriors (Beta-Binomial)</h2>
        ${learnBars}
        <div class="sub" style="margin:6px 0 10px">Weights: ${esc(learn.weights_status)} ·
          unlock gate: ${learn.unlock_n} scored alignments per cluster ·
          ${esc(learn.calibration_note)}</div>
        <h2>Recent scored outcomes (${learn.scored_claims} total)</h2>
        ${outcomes}
        ${learn.mean_abs_forecast_error_pct != null ? `<div class="sub" style="margin-top:4px">
          Mean absolute forecast error (magnitude): ${learn.mean_abs_forecast_error_pct}pp ·
          ${esc(learn.magnitude_calibration_note)}</div>` : ""}
        <h2 style="margin-top:12px">Prediction tracking — T vs T+checkpoint</h2>
        <div class="sub" style="margin-bottom:8px">Every claim is scored twice: an interim
          checkpoint partway through its horizon (predicted-so-far vs realized-so-far), then
          the full outcome at maturity. This is the model checking its own work continuously,
          not just once every six months.</div>
        ${checkpoints}
        <h2 style="margin-top:12px">Account</h2>
        <div class="frm"><div><label>Reset with starting cash (₹)</label>
          <input id="pp-cash" type="number" value="1000000" step="10000"></div></div>
        <button id="pp-reset">Reset paper account</button>
      </div>
    </div>
    <div class="panel"><h2>Fill history (${a.trades.length})</h2>
      ${a.trades.length ? `<div class="tablewrap"><table><thead><tr><th>Date</th><th>Company</th>
        <th>Side</th><th class="num">Qty</th><th class="num">Fill</th><th>From dossier</th></tr></thead><tbody>
        ${a.trades.map(t => `<tr><td>${esc(t.date)}</td><td><strong>${esc(t.ticker)}</strong></td>
          <td><span class="chip ${t.side === "buy" ? "active" : "invalidated"}">${t.side}</span></td>
          <td class="num">${fmtN(t.quantity, 0)}</td><td class="num">${fmtN(t.price, 1)}</td>
          <td class="hashline">${esc(t.from_dossier || "manual")}</td></tr>`).join("")}
        </tbody></table></div>` : '<div class="empty">No fills yet.</div>'}
    </div>`;

  document.getElementById("ap-toggle").addEventListener("click", async () => {
    const updated = await api("/autopilot", { method: "PUT", body: JSON.stringify({ enabled: !ap.config.enabled }) });
    toast(updated.enabled ? "Autopilot enabled." : "Autopilot disabled.", "ok");
    viewTrading();
  });
  document.getElementById("ap-save").addEventListener("click", async () => {
    await api("/autopilot", { method: "PUT", body: JSON.stringify({
      max_new_per_run: parseInt(document.getElementById("ap-max").value),
      max_open_positions: parseInt(document.getElementById("ap-open").value),
      cash_reserve_pct: parseFloat(document.getElementById("ap-cash").value),
      time_exit_days: parseInt(document.getElementById("ap-days").value),
      daily_forecasts: parseInt(document.getElementById("ap-forecasts").value) }) });
    toast("Autopilot policy saved.", "ok");
  });
  document.getElementById("ap-run").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Running…";
    try {
      const r = await api("/autopilot/run", { method: "POST" });
      document.getElementById("ap-report").innerHTML = renderApReport(r);
      viewTrading();
    } catch (err) { document.getElementById("ap-msg").textContent = err.message; e.target.disabled = false; }
  });

  body.querySelectorAll("[data-exec-cid]").forEach(b => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await api("/paper/trade", { method: "POST", body: JSON.stringify({
        company_id: parseInt(b.dataset.execCid), side: "buy",
        quantity: parseInt(b.dataset.execQty) })});
      toast(`Filled: bought ${r.quantity} ${r.ticker} @ ₹${fmtN(r.fill_price, 1)}`, "ok");
      viewTrading();
    } catch (e) { toast("Order rejected: " + e.message, "err"); b.disabled = false; }
  }));
  const hint = () => {
    const sel = document.getElementById("pp-company");
    const price = sel.selectedOptions[0]?.dataset.price;
    const qty = parseFloat(document.getElementById("pp-qty").value) || 0;
    document.getElementById("pp-price-hint").textContent =
      price ? `Last close ₹${fmtN(parseFloat(price), 1)}` +
        (qty ? ` · order value ≈ ${fmtMoney(qty * parseFloat(price))}` : "") : "";
  };
  document.getElementById("pp-company").addEventListener("change", hint);
  document.getElementById("pp-qty").addEventListener("input", hint);
  hint();

  document.getElementById("pp-save").addEventListener("click", async () => {
    const msg = document.getElementById("pp-msg");
    try {
      const r = await api("/paper/trade", { method: "POST", body: JSON.stringify({
        company_id: parseInt(document.getElementById("pp-company").value),
        side: document.getElementById("pp-side").value,
        quantity: parseFloat(document.getElementById("pp-qty").value),
      })});
      toast(`Filled: ${r.side} ${r.quantity} ${r.ticker} @ ₹${fmtN(r.fill_price, 1)}`, "ok");
      viewTrading();
    } catch (e) { msg.textContent = "Rejected: " + e.message; toast("Order rejected: " + e.message, "err"); }
  });
  body.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", async () => {
    const r = await api("/paper/trade", { method: "POST", body: JSON.stringify({
      company_id: parseInt(b.dataset.close), side: "sell",
      quantity: parseFloat(b.dataset.qty) })});
    toast(`Closed: sold ${r.quantity} ${r.ticker} @ ₹${fmtN(r.fill_price, 1)}`, "ok");
    viewTrading();
  }));
  document.getElementById("pp-reset").addEventListener("click", async (e) => {
    if (!confirm("Reset the paper account? All fills are wiped (the ledger keeps the reset record).")) return;
    await api("/paper/reset", { method: "POST", body: JSON.stringify({
      starting_cash: parseFloat(document.getElementById("pp-cash").value) || 1000000 })});
    toast("Paper account reset.", "ok");
    viewTrading();
  });
}

/* ------------------------------------------------------- profile editor */

async function renderProfileEditor(body) {
  const p = await api("/profile");
  const sel = (id, options, current) => `<select id="${id}">${options.map(o =>
    `<option value="${o}" ${o === current ? "selected" : ""}>${human(o)}</option>`).join("")}</select>`;
  const num = (id, val, step = 1) => `<input id="${id}" type="number" value="${val}" step="${step}">`;
  body.innerHTML = `
    <div class="sub" style="margin-bottom:8px">Your stated policy — it drives dashboard ordering,
      the lens system, sizing limits and rule-breach diagnostics. Changes apply immediately.</div>
    <div class="panel"><h2>Investor profile</h2>
      <div class="frm">
        <div><label>Horizon</label>${sel("pr-horizon", ["short", "medium", "long"], p.horizon)}</div>
        <div><label>Risk tolerance</label>${sel("pr-risk", ["conservative", "moderate", "aggressive"], p.risk_tolerance)}</div>
        <div><label>Style (0 value ↔ 100 growth)</label>${num("pr-style", p.style)}</div>
        <div><label>Dividend preference (0–100)</label>${num("pr-div", p.dividend_preference)}</div>
        <div><label>Quality emphasis (0–100)</label>${num("pr-qual", p.quality_emphasis)}</div>
        <div><label>Preferred lens</label>${sel("pr-lens", ["balanced", "quality_first", "growth_first", "income_first", "value_first"], p.preferred_lens)}</div>
      </div>
      <h2 style="margin-top:14px">Hard limits (drive breach diagnostics & sizing)</h2>
      <div class="frm">
        <div><label>Max single position %</label>${num("pr-maxpos", p.max_position_pct, 0.5)}</div>
        <div><label>Max sector %</label>${num("pr-maxsec", p.max_sector_pct, 1)}</div>
        <div><label>Max acceptable drawdown %</label>${num("pr-maxdd", p.max_drawdown_pct, 1)}</div>
      </div>
      <h2 style="margin-top:14px">Sector preferences</h2>
      <div class="frm">
        <div><label>Prefer (comma-separated)</label><input id="pr-prefs" value="${esc(p.sector_preferences.join(", "))}"></div>
        <div><label>Exclude (comma-separated)</label><input id="pr-excl" value="${esc(p.sector_exclusions.join(", "))}"></div>
      </div>
      <h2 style="margin-top:14px">Written investment rules</h2>
      <textarea id="pr-rules" rows="4">${esc(p.rules.join("\n"))}</textarea>
      <div style="margin-top:10px"><button class="primary" id="pr-save">Save profile</button>
        <span class="sub" id="pr-msg" style="margin-left:10px"></span></div>
    </div>`;
  document.getElementById("pr-save").addEventListener("click", async () => {
    const msg = document.getElementById("pr-msg");
    try {
      await api("/profile", { method: "PUT", body: JSON.stringify({
        horizon: document.getElementById("pr-horizon").value,
        risk_tolerance: document.getElementById("pr-risk").value,
        style: parseFloat(document.getElementById("pr-style").value),
        dividend_preference: parseFloat(document.getElementById("pr-div").value),
        quality_emphasis: parseFloat(document.getElementById("pr-qual").value),
        preferred_lens: document.getElementById("pr-lens").value,
        max_position_pct: parseFloat(document.getElementById("pr-maxpos").value),
        max_sector_pct: parseFloat(document.getElementById("pr-maxsec").value),
        max_drawdown_pct: parseFloat(document.getElementById("pr-maxdd").value),
        sector_preferences: document.getElementById("pr-prefs").value.split(",").map(s => s.trim()).filter(Boolean),
        sector_exclusions: document.getElementById("pr-excl").value.split(",").map(s => s.trim()).filter(Boolean),
        rules: document.getElementById("pr-rules").value.split("\n").filter(Boolean),
      })});
      toast("Profile saved — rankings and limits updated.", "ok");
      document.getElementById("lens").value = document.getElementById("pr-lens").value;
    } catch (e) { msg.textContent = "Rejected: " + e.message; }
  });
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
      toast("Removed from watchlist."); viewResearch();
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


/* ------------------------------------------------- signal information coefficient */
/* Does each signal actually predict? Every result carries its own DETECTION
   LIMIT, because a null IC is ambiguous without one: "the signal is absent" and
   "the cross-section is too narrow to resolve it" call for opposite responses. */

async function renderIcPanel(body) {
  const d = await api("/live/ic");
  if (!d.computable) {
    body.innerHTML = `<div class="panel"><strong>Information Coefficient not computed.</strong>
      <div class="sub" style="margin-top:6px">${esc(d.reason || "")}</div>
      <button id="ic-run" class="primary" style="margin-top:10px">Run IC studies</button><div class="sub" style="margin-top:8px">These studies are a full pass over the price panel and take several minutes — a serverless request will time out before they finish. Run <code>python -m equisense.research all</code> locally against the same database; the results cache to the same place this page reads.</div></div>`;
    const b = document.getElementById("ic-run");
    if (b) b.addEventListener("click", async () => {
      b.disabled = true; b.textContent = "Running…";
      await api("/live/ic/run", { method: "POST" });
      renderIcPanel(body);
    });
    return;
  }
  const rows = Object.entries(d.signals).map(([hyp, sg]) => {
    if (!sg.computable)
      return `<tr><td>${esc(hyp)}</td><td colspan="6" class="sub">${esc(sg.reason || "")}</td></tr>`;
    const h = sg.best_horizon, e = sg.by_horizon[h];
    if (!e || !e.computable)
      return `<tr><td>${esc(hyp)}</td><td colspan="6" class="sub">${esc((e || {}).reason || "")}</td></tr>`;
    const passes = Math.abs(e.t_stat || 0) >= 2;
    const label = passes ? "PASS" : (e.underpowered ? "underpowered" : "no edge");
    return `<tr><td>${esc(hyp)}</td><td class="num">${h}d</td>
      <td class="num">${signed(e.mean_ic, 4)}</td>
      <td class="num">${signed(e.t_stat)}</td>
      <td class="num sub">${fmtN(e.minimum_detectable_ic, 4)}</td>
      <td class="num sub">${e.dates}</td>
      <td style="color:${passes ? "var(--good-text)" : "var(--muted)"}">${label}</td></tr>`;
  }).join("");
  body.innerHTML = `
    <div class="panel"><h3>Information Coefficient — does each signal actually predict?</h3>
      <div class="sub" style="margin-bottom:8px">${d.universe} names · ${d.history_days} days ·
        per-date cross-sectional rank correlation (Fama-MacBeth), Newey-West t for
        overlapping windows</div>
      <div class="tablewrap"><table><thead><tr><th>Hypothesis</th><th>Horizon</th>
        <th>Mean IC</th><th>t</th><th>Min detectable</th><th>Dates</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <div class="sub" style="margin-top:10px;padding:8px 10px;border-left:2px solid var(--accent)">
        <strong>Passing IC t-test: ${d.passing_ic_t_test.length ? esc(d.passing_ic_t_test.join(", ")) : "NONE"}.</strong>
        Read every null against the detection limit beside it. Where |IC| sits below
        that limit the study could not have resolved the effect even if it were real —
        widening the cross-section is what changes that, not abandoning the signal.</div>
      <div class="sub" style="margin-top:8px">${esc(d.note || "")}</div>
      <button id="ic-rerun" style="margin-top:10px">Recompute</button></div>`;
  const b = document.getElementById("ic-rerun");
  if (b) b.addEventListener("click", async () => {
    b.disabled = true; b.textContent = "Running…";
    await api("/live/ic/run", { method: "POST" });
    renderIcPanel(body);
  });
}

async function renderFactorPanel(body) {
  const d = await api("/live/factor-portfolio");
  if (!d.computable) {
    body.innerHTML = `<div class="panel"><strong>Factor P&L not computed.</strong>
      <div class="sub" style="margin-top:6px">${esc(d.reason || "")}</div>
      <button id="fp-run" class="primary" style="margin-top:10px">Run factor studies</button><div class="sub" style="margin-top:8px">These studies are a full pass over the price panel and take several minutes — a serverless request will time out before they finish. Run <code>python -m equisense.research all</code> locally against the same database; the results cache to the same place this page reads.</div></div>`;
    const b = document.getElementById("fp-run");
    if (b) b.addEventListener("click", async () => {
      b.disabled = true; b.textContent = "Running…";
      await api("/live/factor-portfolio/run", { method: "POST" });
      renderFactorPanel(body);
    });
    return;
  }
  const rows = Object.entries(d.signals).map(([hyp, sg]) => {
    if (!sg.computable)
      return `<tr><td>${esc(hyp)}</td><td colspan="8" class="sub">${esc(sg.reason || "")}</td></tr>`;
    // show the horizon with the best TRADEABLE (long-only) net return
    let bh = null, best = null;
    for (const [h, v] of Object.entries(sg.by_horizon)) {
      const lo = v.long_only || {};
      if (!lo.computable) continue;
      if (best === null || (lo.net_annual_pct || 0) > (best.long_only.net_annual_pct || 0)) {
        best = v; bh = h;
      }
    }
    if (!best) return `<tr><td>${esc(hyp)}</td><td colspan="8" class="sub">no computable horizon</td></tr>`;
    const lo = best.long_only, ls = best.long_short;
    const ok = (lo.net_annual_pct || 0) > 0 && Math.abs(lo.t_stat || 0) >= 2 && !ls.tail_driven;
    const ac = (sg.autocorrelation || {}).mean_autocorrelation;
    return `<tr><td>${esc(hyp)}</td><td class="num">${esc(bh)}d</td>
      <td class="num ${(lo.net_annual_pct || 0) >= 0 ? "pos" : "neg"}"><strong>${signed(lo.net_annual_pct, 2)}%</strong></td>
      <td class="num">${signed(lo.t_stat)}</td>
      <td class="num sub">${signed(ls.net_annual_pct, 2)}%</td>
      <td class="num sub">${fmtN(ls.monotonicity, 2)}</td>
      <td class="num sub">${fmtN(ls.turnover_per_rebalance, 2)}</td>
      <td class="num sub">${fmtN(ac, 2)}</td>
      <td style="color:${ok ? "var(--good-text)" : "var(--muted)"}">
        ${ls.tail_driven ? "TAIL-DRIVEN" : (ok ? "tradeable" : "no edge")}</td></tr>`;
  }).join("");
  body.innerHTML = `
    <div class="panel"><h3>Factor P&amp;L — what each signal actually pays</h3>
      <div class="sub" style="margin-bottom:8px">${d.universe} names · ${d.history_days} days ·
        quantile portfolios rebalanced monthly, net of the India statutory round trip</div>
      <div class="tablewrap"><table><thead><tr><th>Hypothesis</th><th>Horizon</th>
        <th>Long-only net %/yr</th><th>t</th><th>Long-short net</th><th>Monotone</th>
        <th>Turnover</th><th>Autocorr</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <div class="sub" style="margin-top:10px;padding:8px 10px;border-left:2px solid var(--accent)">
        <strong>Read the long-only column.</strong> Single-stock shorting is not
        available in the NSE cash segment, so the long-short spread is a
        factor-evaluation number, not a tradeable one. A TAIL-DRIVEN flag means the
        mean spread and the median spread disagree — the effect lives in a few
        extreme names rather than the typical one, and a small book cannot
        concentrate into it.</div>
      <div class="sub" style="margin-top:8px">${esc(d.note || "")}</div>
      <button id="fp-rerun" style="margin-top:10px">Recompute</button></div>`;
  const b = document.getElementById("fp-rerun");
  if (b) b.addEventListener("click", async () => {
    b.disabled = true; b.textContent = "Running…";
    await api("/live/factor-portfolio/run", { method: "POST" });
    renderFactorPanel(body);
  });
}

/* ================================================================== lab */

async function viewLab(section = "hypotheses") {
  const tabs = [["hypotheses", "Hypotheses"], ["baserates", "Base Rates"],
                ["ic", "Signal IC"], ["factors", "Factor P&L"],
                ["calibration", "Calibration & Ledger"], ["backtest", "Backtest"],
                ["data", "Data Health"]];
  app.innerHTML = `
    <h1>Research Laboratory</h1>
    <div class="tabs">${tabs.map(([k, l]) =>
      `<button class="${k === section ? "active" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>
    <div id="lab-body">${skeleton(6)}</div>`;
  app.querySelectorAll("[data-tab]").forEach(b =>
    b.addEventListener("click", () => location.hash = `#/lab/${b.dataset.tab}`));
  const body = document.getElementById("lab-body");

  if (section === "ic") { await renderIcPanel(body); return; }
  if (section === "factors") { await renderFactorPanel(body); return; }

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
        out.innerHTML = r.verdict === "insufficient_data" ? `<div class="score-detail" style="margin-top:8px">
          <strong>Verdict: ${esc(human(r.verdict))}</strong><br>
          Only ${r.episodes} momentum-top-quintile episodes so far — need at least 200
          before a train/test split is meaningful.</div>` : `<div class="score-detail" style="margin-top:8px">
          <strong>Verdict: ${esc(human(r.verdict))}</strong> — ${esc(r.consequence || "")}<br>
          Out-of-sample Brier: unconditional ${r.brier_unconditional_oos} vs
          regime-conditioned ${r.brier_conditional_oos}
          (improvement ${signed(r.improvement, 5)})<br>
          Training hit rates: overall ${(r.train_hit_rate_unconditional * 100).toFixed(1)}%
          ${Object.entries(r.train_hit_rate_by_regime || {}).map(([k, v]) =>
            `· ${esc(human(k))} ${(v * 100).toFixed(1)}%`).join(" ")}<br>
          Episodes: ${r.train_episodes} train / ${r.test_episodes} test
          (effective test sample ${r.test_n_eff})<br>
          <span class="sub">${(r.caveats || []).map(esc).join(" · ")}</span></div>`;
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
        <th class="num">Hit</th><th class="num">Median</th><th class="num">Net</th><th class="num">95% CI</th><th class="num">IQR</th></tr></thead>
        <tbody>${br.records.map(r => `
          <tr><td>${esc(r.study_key)}</td><td>${esc(r.registry_ref)}</td><td>${esc(r.regime)}</td>
            <td class="num">${r.n_eff ?? "—"}</td><td class="num sub">${r.n}</td>
            <td class="num">${(r.hit_rate * 100).toFixed(0)}%</td>
            <td class="num">${signed(r.median_excess_pct)}%</td>
            <td class="num" style="color:${(r.net_median_excess_pct ?? 0) > 0 ? "var(--good-text)" : "var(--critical)"}">
              ${r.net_median_excess_pct == null ? "—" : signed(r.net_median_excess_pct) + "%"}</td>
            <td class="num sub">${r.ci95 ? "[" + r.ci95 + "]%" : "—"}</td>
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
          ${Object.entries(cal).filter(([k]) => k !== "note").map(([k, v]) =>
            `<div class="metric-row" style="cursor:default"><span class="m-label">${esc(human(k))}</span>
             <span class="m-value">${typeof v === "number" ? fmtN(v, 3) : esc(v)}</span></div>`).join("")}
          <div class="sub" style="margin-top:6px">${esc(cal.note || "")}</div>
          <div style="margin-top:8px"><button class="primary" id="score-claims">Score due claims</button></div>
        </div>
        <div class="panel"><h2>Decision ledger (hash-chained, append-only)</h2>
          <div class="hashline">Chain: ${led.chain.intact ? "✓ intact" : "⚠ BROKEN"} · ${led.chain.records ?? 0} records</div>
          <div style="margin-top:8px">${led.records.slice(-20).reverse().map(r => `
            <div class="metric-row" style="cursor:default">
              <span class="m-label">${esc(human(r.kind))} · ${esc(r.company?.ticker || r.company || "")}
                · ${esc(human(r.verdict || r.claim_type || ""))}</span>
              <span class="sub">${esc((r.created_at || "").slice(0, 16))}</span>
              <span class="hashline">${esc((r.hash || "").slice(0, 12))}</span></div>`).join("")}</div>
        </div>
      </div>`;
    document.getElementById("score-claims").addEventListener("click", async (e) => {
      e.target.disabled = true; await api("/live/score", { method: "POST" }); viewLab("calibration");
    });
    const uniBtn = document.getElementById("uni-save");
    if (uniBtn) uniBtn.addEventListener("click", async () => {
      const msg = document.getElementById("uni-msg");
      uniBtn.disabled = true; msg.textContent = "re-syncing membership…";
      try {
        const r = await api("/universe", { method: "PUT", body: JSON.stringify({
          index_key: document.getElementById("uni-index").value })});
        msg.textContent = `Universe is now ${r.index_key} — ${r.live_members} live members. ${r.note}`;
        toast(`Universe set to ${r.index_key} (${r.live_members} names)`);
      } catch (e) { msg.textContent = "Failed: " + e.message; }
      finally { uniBtn.disabled = false; }
    });
  } else if (section === "backtest") {
    const bt = await api("/backtest/strategy");
    body.innerHTML = (bt.error || !bt.vol_managed_overlay)
      ? `<div class="panel"><div class="empty">${esc(bt.error || "Backtest cache outdated — click Recompute.")}</div>
         <button id="bt-refresh" style="margin-top:8px">Recompute</button></div>` : `
      <div class="panel"><h2>Strategy backtest — the live price-cluster rule, simulated</h2>
        <div class="sub" style="margin-bottom:8px">${esc(bt.spec)}</div>
        <div class="tiles">
          <div class="tile"><div class="label">Annualized (net)</div>
            <div class="value ${bt.annualized_net_pct >= 0 ? "pos" : "neg"}">${signed(bt.annualized_net_pct)}%</div>
            <div class="sub">NIFTY same windows: ${signed(bt.nifty_annualized_pct)}%</div></div>
          <div class="tile"><div class="label">Hit rate</div><div class="value">${(bt.hit_rate * 100).toFixed(0)}%</div></div>
          <div class="tile"><div class="label">Mean period (net)</div><div class="value">${signed(bt.mean_period_return_net_pct)}%</div>
            <div class="sub">median 95% CI [${bt.median_ci95_pct}]% (block bootstrap)</div></div>
          <div class="tile"><div class="label">Effective sample</div><div class="value">${bt.n_eff}</div>
            <div class="sub">${bt.periods} overlapping periods</div></div>
          <div class="tile"><div class="label">Naive Sharpe</div><div class="value">${fmtN(bt.sharpe_naive, 2)}</div></div>
        </div>
        ${bt.curve && bt.curve.length > 1 ? equityChart(
          bt.curve.map(p => ({ date: p.date, equity: p.strategy })), null) : ""}
        <div class="sub" style="margin-top:8px">${bt.caveats.map(esc).join(" · ")}</div>
        <div style="margin-top:10px"><button id="bt-refresh">Recompute</button></div>
      </div>
      <div class="panel"><h2>Volatility-managed overlay (HYP-009)</h2>
        <div class="sub" style="margin-bottom:8px">${esc(bt.vol_managed_overlay.citation)} —
          scales exposure by target vol (${bt.vol_managed_overlay.target_annual_vol_pct}%) ÷
          the STRATEGY'S OWN trailing ${bt.vol_managed_overlay.lookback_periods}-period realized
          vol, bounded [${bt.vol_managed_overlay.scale_bounds}]×. Distinct from stock-level
          scaling (MQI) — this targets the portfolio's own vol, the actual
          Barroso-Santa-Clara mechanism.</div>
        <div class="grid2">
          <div class="tile"><div class="label">Baseline (equal-weight)</div>
            <div class="value">Sharpe ${fmtN(bt.vol_managed_overlay.baseline.sharpe_naive, 2)}</div>
            <div class="sub">worst period ${signed(bt.vol_managed_overlay.baseline.worst_period_pct)}% ·
              illustrative max DD ${signed(bt.vol_managed_overlay.baseline.max_drawdown_display_pct)}%</div></div>
          <div class="tile"><div class="label">Vol-managed</div>
            <div class="value">Sharpe ${fmtN(bt.vol_managed_overlay.vol_managed.sharpe_naive, 2)}</div>
            <div class="sub">worst period ${signed(bt.vol_managed_overlay.vol_managed.worst_period_pct)}% ·
              illustrative max DD ${signed(bt.vol_managed_overlay.vol_managed.max_drawdown_display_pct)}%</div></div>
        </div>
        <div class="score-detail">Current scale factor: <strong>${bt.vol_managed_overlay.current_scale_factor}×</strong>
          (mean across sample: ${bt.vol_managed_overlay.mean_scale_factor}×)<br>
          <strong>${esc(human(bt.vol_managed_overlay.verdict))}</strong> — measured on this sample,
          not asserted.</div>
      </div>`;
    const btn = document.getElementById("bt-refresh");
    if (btn) btn.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "computing…";
      await api("/backtest/strategy?refresh=true"); viewLab("backtest");
    });
  } else if (section === "data") {
    const [st, uni, reads] = await Promise.all([
      api("/live/status"), api("/universe"), api("/storage/reads")]);
    const p = st.datasets.prices, f = st.datasets.fundamentals;
    body.innerHTML = `
      <div class="panel"><h2>Analytical universe</h2>
        <div class="sub" style="margin-bottom:8px">Universe size is the binding constraint on
          learning. Claims come from names: a 50-name cross-section produces them roughly ten
          times slower than a 500-name one, and gives percentile ranking only 50 points to rank
          against — which makes every "top quintile" a 10-name bet.</div>
        <div class="frm">
          <div><label>NSE index</label><select id="uni-index">
            ${uni.choices.map(k => `<option value="${esc(k)}" ${k === uni.index_key ? "selected" : ""}>${esc(k)}</option>`).join("")}
          </select></div>
          <div><label>Live members</label><input value="${uni.live_members}" disabled></div>
          <div><label>Companies with history</label><input value="${uni.companies_known}" disabled></div>
        </div>
        <button class="primary" id="uni-save">Apply universe</button>
        <span class="sub" style="margin-left:10px">${esc(uni.note)}</span>
        <div id="uni-msg" class="sub" style="margin-top:6px"></div>
      </div>
      <div class="panel"><h2>Database reads this process
        <span class="sub">${reads.rows_total.toLocaleString()} rows ≈ ${reads.approx_mb} MB</span></h2>
        <div class="sub" style="margin-bottom:8px">A free Postgres tier meters DATA TRANSFER,
          not just storage. This deployment was taken down by an exhausted transfer quota while
          disk sat at 41%, and nothing could say which read was responsible — every cost here had
          been reasoned about in rows and seconds, never in bytes off the database.</div>
        ${Object.entries(reads.rows_by_source || {}).map(([k, v]) => `
          <div class="metric-row" style="cursor:default">
            <span class="m-label">${esc(k)}</span>
            <span class="m-value">${v.toLocaleString()}</span>
            <span class="m-unit">rows</span></div>`).join("")
          || '<div class="empty">No heavy reads yet in this process.</div>'}
        <div class="sub" style="margin-top:6px">${esc(reads.note)}</div>
      </div>
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
          ${(() => {
            /* Completeness is NOT staleness and the difference is the point: a
               name missing three weeks from the middle of 2024 is current today,
               so it reads perfectly fresh while every return computed across the
               gap is wrong. Same for a bar with no intraday range — the bar is
               there, and Yang-Zhang quietly falls back to a 6x noisier
               estimator that sets the stop distance. Neither is visible
               anywhere else on this page. */
            const c = p.coverage_detail;
            if (!c || c.error || !c.sessions) return "";
            return `<div class="metric-row" style="cursor:default"><span class="m-label">Series completeness</span>
              <span class="m-value">${c.names_with_gaps}</span><span class="m-unit">names with gaps</span></div>
            <div class="sub" style="margin:0 0 6px 2px">${fmtN(c.missing_sessions, 0)} missing sessions across
              ${c.names} names · ${c.sessions} exchange sessions · intraday range on
              ${c.ohlc_complete_pct}% of bars</div>
            ${(c.worst || []).filter(w => w.missing_sessions > 0 || w.ohlc_pct < 98).slice(0, 5)
              .map(w => `<div class="sub" style="margin:0 0 2px 10px">${esc(w.ticker)} —
                ${w.missing_sessions} missing · OHLC ${w.ohlc_pct}%</div>`).join("")}`;
          })()}
          ${(() => {
            const pb = p.panel;
            if (!pb || pb.error || !pb.blobs) return "";
            const core = pb.blobs.prices_core || {};
            if (!core.present) return `<div class="sub" style="margin:0 0 6px 2px">
              Columnar panel not built — studies read the full price table.</div>`;
            return `<div class="metric-row" style="cursor:default"><span class="m-label">Columnar panel
              ${pb.fresh ? "" : "<b>(STALE — studies fall back to the table)</b>"}</span>
              <span class="m-value">${pb.total_mb}</span><span class="m-unit">MB</span></div>
            <div class="sub" style="margin:0 0 6px 2px">${core.rows} sessions × ${core.cols} names ·
              as of ${esc(core.as_of)} · the same bars column-major, which is what keeps every study
              off the metered path</div>`;
          })()}
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
      </div>
      <div id="data-extra">${skeleton(2)}</div>`;
    renderDataExtras(document.getElementById("data-extra"));
  }
}


/* A price that stopped updating must never be displayed as if it were today's.
   The universe keeps stale names visible on purpose (the user may hold one) and
   excludes them from the cross-sectional reference distribution — but without a
   marker here, a frozen quote reads exactly like a live one. Five Nifty-50
   names were sitting 13 trading sessions behind when this was added. */
function staleBadge(item) {
  const n = item && item.stale_sessions;
  if (!n) return "";
  return `<span class="stale-badge" title="Last price is ${n} trading session${
    n === 1 ? "" : "s"} behind the rest of the universe — excluded from ranking">
    ${n}d stale</span>`;
}

async function renderDataExtras(host) {
  if (!host) return;
  // Storage and source reachability were reachable only as raw JSON. The rule
  // this module serves is that no backend capability hides in JSON, and these
  // two were the exceptions: Neon's free tier is a hard 512 MB ceiling, and
  // every archive fetch fails CLOSED, so an unreachable source is otherwise
  // indistinguishable from a quiet market day.
  const [store, src] = await Promise.all([
    api("/storage").catch(() => null),
    api("/markets/sources").catch(() => null),
  ]);
  // The endpoint WRAPS the report: {"report": {...}}. The first version of this
  // panel read the shape of storage_report() directly and rendered "undefined"
  // against a payload that was perfectly correct — the bug was reading the
  // function's return type instead of the route's.
  const rep = (store && store.report) || store || null;
  let storePanel = "";
  if (rep && !rep.total) {
    // storage_report is DIALECT-DEPENDENT: on-disk size is a Postgres-only
    // query, so SQLite returns row counts and retention alone. Render what
    // exists instead of printing "undefined" where a size should be.
    storePanel = `
      <div class="panel"><h2>Storage</h2>
        <div class="sub">On-disk size is a Postgres-only measurement, so it is
          unavailable on this database. Row counts and the retention policy
          still apply.</div>
        ${Object.keys(rep.rows || {}).length ? `<div class="tablewrap"><table>
          <thead><tr><th>Table</th><th class="num">Rows</th></tr></thead><tbody>
          ${Object.entries(rep.rows).map(([k, v]) =>
            `<tr><td>${esc(k)}</td><td class="num">${fmtN(v, 0)}</td></tr>`).join("")}
        </tbody></table></div>` : ""}
        <div class="sub" style="margin-top:6px">Retention:
          ${Object.entries(rep.retention || {}).map(([k, v]) =>
            `${esc(k.replace(/_/g, " "))} ${v}d`).join(" · ") || "not configured"}.</div>
      </div>`;
  } else if (rep && rep.total) {
    const pct = rep.used_pct != null ? rep.used_pct
      : (rep.total_bytes && rep.free_tier_bytes
         ? rep.total_bytes / rep.free_tier_bytes * 100 : 0);
    const tone = pct > 85 ? "neg" : (pct > 65 ? "" : "pos");
    const ceiling = rep.free_tier_bytes
      ? (rep.free_tier_bytes / 1e6).toFixed(0) + " MB" : "the free tier";
    storePanel = `
      <div class="panel"><h2>Storage — Neon free tier</h2>
        <div class="tiles" style="grid-template-columns:1fr 1fr">
          <div class="tile"><div class="label">Used</div>
            <div class="value ${tone}">${esc(String(rep.total))}</div></div>
          <div class="tile"><div class="label">Of ${esc(ceiling)}</div>
            <div class="value ${tone}">${fmtN(pct, 1)}%</div></div>
        </div>
        <div class="tablewrap"><table><thead><tr><th>Table</th>
          <th class="num">Rows</th><th class="num">Size</th>
          <th>If lost</th></tr></thead><tbody>
          ${(rep.tables || []).map(t => `<tr><td>${esc(t.table)}</td>
            <td class="num">${fmtN(t.rows, 0)}</td>
            <td class="num">${esc(String(t.size))}</td>
            <td class="sub">${esc(t.class || "")}</td></tr>`).join("")}
        </tbody></table></div>
        <div class="sub" style="margin-top:6px">Tables are classed by whether
          they can be rebuilt. Pruning starts with the largest REFETCHABLE table
          and never with an accumulated series, because a series published once
          per day cannot be recovered after deletion — the row is gone for good.</div>
      </div>`;
  }
  let srcPanel = "";
  if (src) {
    const checks = Object.entries(src.checks || {});
    srcPanel = `
      <div class="panel"><h2>Data sources — reachability</h2>
        <div class="metric-row" style="cursor:default"><span class="m-label">${esc(src.source || "")}</span>
          <span class="m-value ${src.healthy ? "pos" : "neg"}">${src.healthy ? "healthy" : "DEGRADED"}</span></div>
        ${checks.map(([name, c]) => `<div class="metric-row" style="cursor:default">
          <span class="m-label">${esc(name.replace(/_/g, " "))}</span>
          <span class="m-value ${c.ok ? "pos" : "neg"}">${c.ok ? "ok" : "FAIL"}</span>
          <span class="m-unit">${c.rows != null ? fmtN(c.rows, 0) + " rows" : ""}</span></div>`).join("")}
        <div class="sub" style="margin-top:6px">${esc(src.note || "")}</div>
      </div>`;
  }
  host.innerHTML = (storePanel || srcPanel)
    ? `<div class="grid2">${storePanel}${srcPanel}</div>`
    : `<div class="sub">Storage and source probes unavailable.</div>`;
}

/* ========================================================= markets view */
/* Surfaces the multi-asset engines: derivatives (live, unstored), the
   market-implied risk-free rate, cross-asset stress correlation, Monte Carlo
   portfolio risk, and disclosed institutional flow. Every panel renders the
   engine's own caveat rather than a cleaned-up summary of it. */

const MARKET_TABS = [
  ["derivatives", "Derivatives"], ["vrp", "Variance Premium"],
  ["risk", "Risk (Monte Carlo)"],
  ["relations", "Cross-Asset"], ["valuation", "Valuation Regime"],
  ["flow", "Institutional Flow"], ["events", "Event Calendar"],
  ["transmission", "Transmission"],
];

async function viewMarkets(tab) {
  const nav = MARKET_TABS.map(([k, label]) =>
    `<button class="${k === tab ? "active" : ""}" data-tab="${k}">${label}</button>`).join("");
  app.innerHTML = `<h1>Markets</h1>
    <div class="sub" style="margin:-6px 0 14px">Derivatives, cross-asset behaviour and
      simulated risk — all from free exchange-published data.</div>
    <div class="tabs" id="mk-tabs">${nav}</div>
    <div id="mk-body">${skeleton(4)}</div>`;
  document.querySelectorAll("#mk-tabs [data-tab]").forEach(b =>
    b.addEventListener("click", () => location.hash = `#/markets/${b.dataset.tab}`));
  const body = document.getElementById("mk-body");
  try {
    if (tab === "transmission") await renderTransmission(body);
    else if (tab === "events") await renderEvents(body);
    else if (tab === "vrp") await renderVrp(body);
    else if (tab === "risk") await renderMarketRisk(body);
    else if (tab === "relations") await renderRelations(body);
    else if (tab === "valuation") await renderValuationRegime(body);
    else if (tab === "flow") await renderFlow(body);
    else await renderDerivatives(body);
  } catch (e) {
    body.innerHTML = `<div class="panel">Could not load: ${esc(e.message)}</div>`;
  }
}

function unavailable(d, what) {
  return `<div class="panel"><strong>${what} unavailable.</strong>
    <div class="sub" style="margin-top:6px">${esc(d.reason || "no data")}</div>
    ${d.hint ? `<div class="sub">${esc(d.hint)}</div>` : ""}</div>`;
}

async function renderDerivatives(body) {
  const sym = (document.getElementById("mk-sym") || {}).value || "NIFTY";
  const d = await api(`/markets/derivatives/${encodeURIComponent(sym)}`);
  if (!d.available) { body.innerHTML = unavailable(d, "Derivatives"); return; }
  const ts = d.term_structure || {}, oc = d.option_chain || {};
  const curve = (ts.contracts || []).map(c => `<tr>
      <td>${esc(c.expiry)}</td><td class="num">${c.days_to_expiry}</td>
      <td class="num">${fmtN(c.futures, 2)}</td>
      <td class="num">${signed(c.implied_rate_pct)}%</td>
      <td class="num sub">${signed(c.basis_pct)}%</td></tr>`).join("");
  const smile = (oc.iv_surface || []).filter(r => r.iv_pct != null);
  const puts = smile.filter(r => r.kind === "put").sort((a, b) => a.strike - b.strike);
  const step = Math.max(1, Math.floor(puts.length / 10));
  const smileRows = puts.filter((_, i) => i % step === 0).map(r => `<tr>
      <td class="num">${fmtN(r.strike, 0)}</td>
      <td class="num">${fmtN(r.iv_pct, 2)}%</td>
      <td class="num sub">${fmtN(r.delta, 3)}</td></tr>`).join("");
  body.innerHTML = `
    <div class="panel"><div style="display:flex;gap:8px;align-items:center">
      <input id="mk-sym" value="${esc(sym)}" style="width:150px" placeholder="NIFTY">
      <button id="mk-go" class="primary">Load</button>
      <span class="sub" style="margin-left:auto">${esc(d.source || "")} · ${esc(d.trade_date)}</span>
    </div></div>
    <div class="grid2">
      <div class="panel"><h3>Futures term structure</h3>
        <div class="tablewrap"><table><thead><tr><th>Expiry</th><th>DTE</th><th>Futures</th>
          <th>Implied rate</th><th>Basis</th></tr></thead><tbody>${curve || ""}</tbody></table></div>
        ${ts.calendar_spread ? `<div class="sub" style="margin-top:8px">
          Annualised roll cost ${signed(ts.calendar_spread.annualised_roll_pct)}% —
          ${esc(ts.calendar_spread.interpretation)}</div>` : ""}
        <div class="sub">${esc(ts.curve_shape || "")}</div></div>
      <div class="panel"><h3>Option chain</h3>
        ${oc.computable === false ? `<div class="sub">${esc(oc.reason || "")}</div>` : `
        <div class="tiles">
          <div class="tile"><div class="label">Expiry</div><div class="value">${esc(oc.expiry || "")}</div>
            <div class="sub">${oc.days_to_expiry}d</div></div>
          <div class="tile"><div class="label">ATM IV</div><div class="value">${fmtN(oc.atm_iv_pct, 2)}%</div>
            <div class="sub">${oc.iv_points_solved} strikes solved</div></div>
          <div class="tile"><div class="label">25Δ skew</div><div class="value">${signed(oc.skew_25d_risk_reversal_pct)}</div>
            <div class="sub">put IV − call IV</div></div>
          <div class="tile"><div class="label">PCR (OI)</div><div class="value">${fmtN(oc.put_call_ratio_oi, 3)}</div>
            <div class="sub">max pain ${fmtN(oc.max_pain_strike, 0)}</div></div>
        </div>
        <div class="tablewrap"><table style="margin-top:10px"><thead><tr><th>Put strike</th><th>IV</th>
          <th>Delta</th></tr></thead><tbody>${smileRows}</tbody></table></div>
        <div class="sub" style="margin-top:8px">${esc((oc.methodology || {}).pcr_caveat || "")}</div>
        <div class="sub">${esc((oc.methodology || {}).max_pain_caveat || "")}</div>`}
      </div></div>`;
  const go = document.getElementById("mk-go");
  if (go) go.addEventListener("click", () => renderDerivatives(body));
}

const VERDICT_STYLE = {
  confirmed:      ["var(--good-text)", "confirmed"],
  contradicted:   ["var(--critical)", "CONTRADICTED"],
  not_detectable: ["var(--muted)", "not detectable"],
  negligible:     ["var(--muted)", "negligible"],
  underpowered:   ["var(--muted)", "underpowered"],
};

function transmissionLink(l, depth) {
  const [colour, label] = VERDICT_STYLE[l.verdict] || ["var(--muted)", l.verdict];
  const imp = l.implied;
  return `
    <div class="evi" style="margin-left:${depth * 18}px;border-left-color:${colour}">
      <span class="tier" style="color:${colour};border-color:${colour}">${esc(label)}</span>
      <strong>${esc(l.ticker || l.label)}</strong>
      ${l.beta != null ? `<span class="sub"> · β ${signed(l.beta, 3)}` +
        (l.explains_pct != null ? ` · explains ${l.explains_pct}% of variance` : "") +
        ` · n=${l.observations}${l.ci95 ? ` · r95 [${l.ci95[0]}, ${l.ci95[1]}]` : ""}</span>` : ""}
      <div class="sub" style="margin-top:3px">${esc(l.mechanism || "")}</div>
      <div class="sub" style="color:${colour}">${esc(l.why || "")}</div>
      ${imp && l.verdict === "confirmed" ? `<div class="base-rate">
        driver moved ${signed(imp.driver_move_pct)}% → implied ${signed(imp.implied_pct)}%
        (${esc(imp.confidence)} confidence) · ${esc(imp.caveat)}</div>` : ""}
    </div>`;
}

async function renderTransmission(body) {
  const driver = cache.txDriver || "BZ=F";
  body.innerHTML = skeleton(5);
  const d = await api(`/markets/transmission?driver=${encodeURIComponent(driver)}`);
  const picker = (d.drivers || []).map(([k, label]) =>
    `<option value="${esc(k)}" ${k === driver ? "selected" : ""}>${esc(label)}</option>`).join("");
  if (!d.available) {
    body.innerHTML = `<div class="panel"><select id="tx-driver">${picker}</select>
      <div class="unavail" style="margin-top:8px">${esc(d.reason || "unavailable")}</div></div>`;
  } else {
    const s = d.summary;
    body.innerHTML = `
      <div class="panel">
        <div class="frm"><div><label>Macro driver</label>
          <select id="tx-driver">${picker}</select></div></div>
        <h2 style="margin-top:10px">${esc(d.driver_label)}
          ${d.driver_move_pct != null ? `<span class="sub">observed ${signed(d.driver_move_pct)}% over ~3 months</span>` : ""}</h2>
        <div class="tiles">
          <div class="tile"><div class="label">Channels declared</div><div class="value">${s.channels_declared}</div></div>
          <div class="tile"><div class="label">Confirmed</div><div class="value" style="color:var(--good-text)">${s.confirmed}</div></div>
          <div class="tile"><div class="label">Contradicted</div><div class="value" style="color:var(--critical)">${s.contradicted}</div></div>
          <div class="tile"><div class="label">Not detectable</div><div class="value">${s.not_detectable}</div></div>
        </div>
        <div class="sub" style="margin-top:8px">${esc(d.reading)}</div>
      </div>
      <div class="panel"><h2>1 · Macro → market</h2>
        ${d.market_links.map(l => transmissionLink(l, 0)).join("") || '<div class="empty">No market-level channel declared.</div>'}
      </div>
      <div class="panel"><h2>2 · Macro → sector</h2>
        ${d.sector_links.map(l => transmissionLink(l, 1)).join("") || '<div class="empty">No sector channel declared for this driver.</div>'}
      </div>
      <div class="panel"><h2>3 · Sector → security</h2>
        <div class="sub" style="margin-bottom:8px">Only names whose SECTOR link was confirmed are
          measured here. Testing every name against every driver regardless would guarantee
          false positives — with 50 names something always looks significant.</div>
        ${d.security_links.map(l => transmissionLink(l, 2)).join("")
          || '<div class="empty">No sector channel was confirmed, so the chain stops before individual names.</div>'}
      </div>`;
  }
  const sel = document.getElementById("tx-driver");
  if (sel) sel.addEventListener("change", () => {
    cache.txDriver = sel.value;
    renderTransmission(body);
  });
}

async function renderEvents(body) {
  const d = await api("/markets/events");
  if (!d.available) {
    body.innerHTML = `<div class="panel"><div class="unavail">Event calendar unavailable —
      ${esc(d.reason || "the exchange feed could not be reached")}.</div>
      <div class="sub" style="margin-top:6px">This is not a statement that there is no
      event risk; it means the calendar could not be read.</div></div>`;
    return;
  }
  const row = (e, showDir) => `
    <div class="metric-row" style="cursor:default">
      <span class="m-label"><strong>${esc(e.ticker)}</strong>${showDir && e.direction
        ? ` <span class="chip ${e.direction === "short" ? "grey" : "active"}">${esc(e.direction)} ${Math.abs(e.quantity)}</span>` : ""}
        · ${esc(e.purpose || "scheduled event")}
        <div class="sub">${esc(e.detail || "")}</div></span>
      <span class="m-value" style="color:${e.days_away <= 7 ? "var(--critical)" : "inherit"}">
        ${esc(e.date)}</span>
      <span class="m-unit">${e.days_away}d</span></div>`;

  body.innerHTML = `
    <div class="panel"><h2>Open positions with a scheduled event</h2>
      <div class="sub" style="margin-bottom:8px">Momentum and valuation read identically
        the day before a result and the day after — nothing in the evidence stack can see
        a results date. These are the positions currently carrying one.</div>
      ${d.held_with_events.length ? d.held_with_events.map(e => row(e, true)).join("")
        : '<div class="empty">No open position has a published event ahead of it.</div>'}
    </div>
    <div class="panel"><h2>Next ${d.upcoming.length} across the exchange
      <span class="sub">(${d.symbols} symbols scheduled)</span></h2>
      ${d.upcoming.map(e => row(e, false)).join("")}
      <div class="sub" style="margin-top:8px">${esc(d.note || "")}</div>
    </div>`;
}

async function renderVrp(body) {
  const d = await api("/markets/vrp?symbol=NIFTY&horizon_days=21");
  const cap = `<button id="vrp-capture" style="margin-top:10px">Capture today's surface</button>`;
  if (!d.computable) {
    body.innerHTML = `<div class="panel"><h3>Variance risk premium — accumulating</h3>
      <div class="tiles">
        <div class="tile"><div class="label">IV observations</div>
          <div class="value">${d.iv_observations_stored ?? 0}</div>
          <div class="sub">need ~40</div></div>
        <div class="tile"><div class="label">Price history</div>
          <div class="value">${(d.close_observations ?? 0).toLocaleString()}</div>
          <div class="sub">realised-vol side ready</div></div>
      </div>
      <div class="sub" style="margin-top:10px;padding:8px 10px;border-left:2px solid var(--accent)">
        ${esc(d.reason || "")}</div>
      <div class="sub" style="margin-top:8px">${esc(d.hypothesis || "")}</div>
      ${cap}</div>`;
  } else {
    const rows = (d.recent || []).map(r => `<tr><td>${esc(r.date)}</td>
      <td class="num">${fmtN(r.implied_pct, 2)}%</td>
      <td class="num">${fmtN(r.realised_pct, 2)}%</td>
      <td class="num" style="color:${r.premium_pp > 0 ? "var(--good-text)" : "var(--critical)"}">
        ${signed(r.premium_pp)}pp</td></tr>`).join("");
    body.innerHTML = `<div class="panel"><h3>Variance risk premium — implied vs subsequently realised</h3>
      <div class="tiles">
        <div class="tile"><div class="label">Mean premium</div>
          <div class="value">${signed(d.mean_premium_pp)}pp</div>
          <div class="sub">t = ${signed(d.t_stat)}</div></div>
        <div class="tile"><div class="label">Positive</div>
          <div class="value">${fmtN(d.share_positive_pct, 1)}%</div>
          <div class="sub">of ${d.observations} observations</div></div>
        <div class="tile"><div class="label">Worst observation</div>
          <div class="value" style="color:var(--critical)">${signed((d.worst_observation || {}).premium_pp)}pp</div>
          <div class="sub">${esc((d.worst_observation || {}).date || "")}</div></div>
      </div>
      <div class="sub" style="margin-top:10px">${esc(d.verdict || "")}</div>
      <div class="sub" style="margin-top:10px;padding:8px 10px;border-left:2px solid var(--critical)">
        ${esc(d.risk_warning || "")}</div>
      <div class="tablewrap" style="margin-top:10px"><table><thead><tr><th>Date</th>
        <th>Implied</th><th>Realised</th><th>Premium</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <div class="sub" style="margin-top:8px">${esc(d.method || "")} · ${esc(d.citation || "")}</div>
      ${cap}</div>`;
  }
  const b = document.getElementById("vrp-capture");
  if (b) b.addEventListener("click", async () => {
    b.disabled = true; b.textContent = "Capturing…";
    await api("/markets/capture-surface", { method: "POST" });
    renderVrp(body);
  });
}

async function renderMarketRisk(body) {
  const d = await api("/markets/simulate?horizon_days=21&paths=15000");
  if (!d.available) { body.innerHTML = unavailable(d, "Simulation"); return; }
  const models = d.risk.models || {};
  const rows = Object.values(models).map(m => `<tr>
      <td>${esc(m.model)}</td>
      <td class="num">${fmtN(m.volatility_pct, 2)}%</td>
      <td class="num">${fmtN(m.var_95_pct, 2)}%</td>
      <td class="num" style="color:var(--critical)">${fmtN(m.var_99_pct, 2)}%</td>
      <td class="num" style="color:var(--critical)">${fmtN(m.cvar_99_pct, 2)}%</td>
      <td class="num sub">${fmtN(m.excess_kurtosis, 2)}</td></tr>`).join("");
  const dd = d.drawdown;
  const touch = dd ? Object.entries(dd.touch_probability_pct).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td class="num">${fmtN(v, 1)}%</td></tr>`).join("") : "";
  body.innerHTML = `
    <div class="panel"><h3>Value at Risk / Expected Shortfall — ${d.risk.horizon_days} trading days</h3>
      <div class="sub" style="margin-bottom:8px">Basis: ${esc(d.basis)} ·
        ${d.risk.n_paths.toLocaleString()} paths · annualised vol ${fmtN(d.risk.annualised_vol_pct, 2)}%</div>
      <div class="tablewrap"><table><thead><tr><th>Model</th><th>Vol</th><th>VaR 95</th>
        <th>VaR 99</th><th>CVaR 99</th><th>Excess kurt</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <div class="sub" style="margin-top:10px;padding:8px 10px;border-left:2px solid var(--accent)">${esc(d.risk.interpretation)}</div>
      <div class="sub" style="margin-top:8px">${esc((d.risk.method || {}).es_note || "")}</div>
      <div class="sub">${esc((d.risk.method || {}).bootstrap || "")}</div></div>
    ${dd ? `<div class="grid2">
      <div class="panel"><h3>Probability of TOUCHING a drawdown (1 year)</h3>
        <div class="tablewrap"><table><thead><tr><th>Level</th><th>Probability</th></tr></thead>
          <tbody>${touch}</tbody></table></div>
        <div class="sub" style="margin-top:8px">${esc(dd.note)}</div></div>
      <div class="panel"><h3>Path outcomes</h3><div class="tiles">
        <div class="tile"><div class="label">Median max DD</div>
          <div class="value">${fmtN(dd.median_max_drawdown_pct, 1)}%</div></div>
        <div class="tile"><div class="label">95th pctile DD</div>
          <div class="value">${fmtN(dd.p95_max_drawdown_pct, 1)}%</div></div>
        <div class="tile"><div class="label">Terminal p05</div>
          <div class="value">${fmtN(dd.terminal_return_pct.p05, 1)}%</div></div>
        <div class="tile"><div class="label">Terminal median</div>
          <div class="value">${fmtN(dd.terminal_return_pct.median, 1)}%</div></div>
      </div></div></div>` : ""}`;
}

async function renderRelations(body) {
  const d = await api("/markets/relationships?lookback=900");
  if (!d.available) { body.innerHTML = unavailable(d, "Relationship map"); return; }
  const stress = Object.entries(d.stress_conditional || {})
    .sort((a, b) => (b[1].gap || 0) - (a[1].gap || 0))
    .map(([k, v]) => `<tr><td>${esc(k)}</td>
      <td class="num">${signed(v.unconditional_r)}</td>
      <td class="num">${signed(v.stress_r)}</td>
      <td class="num" style="color:${(v.gap || 0) > 0.05 ? "var(--critical)" : "var(--good-text)"}">
        ${signed(v.gap)}</td></tr>`).join("");
  const pairs = (d.pairs || []).slice(0, 12).map(p => `<tr>
      <td>${esc(p.a)} ~ ${esc(p.b)}</td><td class="num">${signed(p.r)}</td>
      <td class="num sub">[${p.ci95.map(x => fmtN(x, 2)).join(", ")}]</td>
      <td class="num sub">${p.q_value == null ? "—" : fmtN(p.q_value, 3)}</td>
      <td>${p.survives_fdr ? "✓" : "—"}</td></tr>`).join("");
  body.innerHTML = `
    <div class="panel"><h3>Correlation in stress vs on average</h3>
      <div class="sub" style="margin-bottom:8px">${d.common_dates} common dates ·
        the stress column is the number a diversification claim actually rests on</div>
      <div class="tablewrap"><table><thead><tr><th>Asset</th><th>Unconditional ρ</th>
        <th>ρ in worst 20% of market days</th><th>Gap</th></tr></thead>
        <tbody>${stress}</tbody></table></div>
      <div class="sub" style="margin-top:10px;padding:8px 10px;border-left:2px solid var(--accent)">A POSITIVE gap means correlation
        rises exactly when diversification is supposed to pay.</div></div>
    <div class="panel"><h3>Pairwise correlations (FDR-controlled)</h3>
      <div class="sub" style="margin-bottom:8px">${d.pairs_tested} pairs tested,
        ${d.pairs_surviving_fdr} survive multiple-testing control</div>
      <div class="tablewrap"><table><thead><tr><th>Pair</th><th>ρ</th><th>95% CI</th>
        <th>q</th><th>FDR</th></tr></thead><tbody>${pairs}</tbody></table></div>
      <div class="sub" style="margin-top:8px">${esc(d.note || "")}</div></div>`;
}

async function renderValuationRegime(body) {
  const [v, r] = await Promise.all([api("/markets/valuation"), api("/markets/rates")]);
  const iv = v.index || {}, seg = v.segments || {};
  const segRows = ["large", "midcap", "smallcap"].filter(k => seg[k]).map(k => `<tr>
      <td>${esc(seg[k].index)}</td><td class="num">${fmtN(seg[k].pe, 2)}</td>
      <td class="num sub">${fmtN(seg[k].pe_percentile, 0)}th pctile</td></tr>`).join("");
  body.innerHTML = `
    <div class="grid2">
      <div class="panel"><h3>Index valuation vs own history</h3>
        ${iv.available ? `<div class="tiles">
          <div class="tile"><div class="label">${esc(iv.index)} P/E</div>
            <div class="value">${fmtN(iv.pe, 2)}</div>
            <div class="sub">${fmtN(iv.pe_percentile_vs_own_history, 0)}th percentile</div></div>
          <div class="tile"><div class="label">P/B</div><div class="value">${fmtN(iv.pb, 2)}</div></div>
          <div class="tile"><div class="label">Dividend yield</div>
            <div class="value">${fmtN(iv.div_yield, 2)}%</div></div>
        </div>
        <div class="sub" style="margin-top:10px;padding:8px 10px;border-left:2px solid var(--accent)">${esc(iv.reading || "")}</div>
        <div class="tablewrap"><table style="margin-top:10px"><thead><tr><th>Segment</th>
          <th>P/E</th><th>vs own history</th></tr></thead><tbody>${segRows}</tbody></table></div>
        ${seg.smallcap_premium_x ? `<div class="sub" style="margin-top:8px">
          Small-cap premium ${fmtN(seg.smallcap_premium_x, 2)}× — ${esc(seg.reading || "")}</div>` : ""}
        <div class="sub" style="margin-top:8px">${esc(iv.caveat || "")}</div>`
        : `<div class="sub">${esc(iv.reason || "no index valuation history")}</div>`}
      </div>
      <div class="panel"><h3>Market-implied rates</h3>
        ${r.available ? `<div class="tiles">
          <div class="tile"><div class="label">Risk-free (derived)</div>
            <div class="value">${fmtN(r.risk_free.value, 2)}%</div>
            <div class="sub">not hardcoded</div></div>
          <div class="tile"><div class="label">ERP sanity check</div>
            <div class="value">${signed(r.erp_check.value)}pp</div>
            <div class="sub">earnings yield − rf</div></div>
        </div>
        <div class="sub" style="margin-top:10px">${esc(r.risk_free.formula || "")}</div>
        <div class="sub" style="margin-top:8px">${esc(r.source || "")}</div>
        <div class="sub" style="margin-top:10px;padding:8px 10px;border-left:2px solid var(--accent)">${esc(r.erp_check.caveat || "")}</div>`
        : `<div class="sub">${esc(r.reason || "rate not derivable")}</div>`}
      </div></div>`;
}

async function renderFlow(body) {
  const d = await api("/markets/flow");
  if (!d.available) { body.innerHTML = unavailable(d, "Institutional flow"); return; }
  const row = r => `<tr><td>${esc(r.symbol)}${r.in_universe ? " ★" : ""}</td>
      <td class="num">${r.deals}</td>
      <td class="num sub">₹${fmtN(r.gross_value_cr, 1)}cr</td>
      <td class="num" style="color:${r.net_value_cr > 0 ? "var(--good-text)" : "var(--critical)"}">
        ₹${signed(r.net_value_cr)}cr</td>
      <td class="num">${r.net_to_gross_ratio == null ? "—" : signed(r.net_to_gross_ratio)}</td>
      <td class="num sub">${r.days_of_adv == null ? "—" : signed(r.days_of_adv) + "d"}</td></tr>`;
  body.innerHTML = `
    <div class="panel"><h3>Directional flow — net is at least half of gross</h3>
      <div class="sub" style="margin-bottom:8px">${esc(d.trade_date)} ·
        ${d.total_deals} disclosed deals across ${d.symbols} symbols</div>
      <div class="tablewrap"><table><thead><tr><th>Symbol</th><th>Deals</th><th>Gross</th>
        <th>Net</th><th>Net/gross</th><th>Days of ADV</th></tr></thead>
        <tbody>${(d.directional || []).map(row).join("") ||
          `<tr><td colspan="6" class="sub">Nothing directional today — every name was
           funds crossing stock with each other.</td></tr>`}</tbody></table></div></div>
    <div class="panel"><h3>Largest by absolute net</h3>
      <div class="tablewrap"><table><thead><tr><th>Symbol</th><th>Deals</th><th>Gross</th>
        <th>Net</th><th>Net/gross</th><th>Days of ADV</th></tr></thead>
        <tbody>${(d.by_net_value || []).map(row).join("")}</tbody></table></div>
      <div class="sub" style="margin-top:10px;padding:8px 10px;border-left:2px solid var(--accent)">${esc(d.note || "")}</div></div>`;
}

/* ========================================================= simulation studio

   Every pixel here is bound to the Monte Carlo engine's OWN output — the
   bootstrap of real history, three tail models, quantile envelopes and a
   handful of actual sample paths (montecarlo.py). The animation shows those
   real paths settling into the cone and the histogram they summarise; nothing
   is invented for effect. Pure SVG, no chart library, theme-aware via CSS vars. */

const SIM = { horizon: 63, paths: 20000, running: false, data: null, bt: null };
const MODEL_STYLE = {
  gaussian: { c: "var(--series-1)", label: "Gaussian" },
  student_t: { c: "var(--series-3)", label: "Student-t (fat tails)" },
  bootstrap: { c: "var(--series-2)", label: "Bootstrap of real history" },
};

/* map a value in [lo,hi] to a pixel in [a,b] */
const _lin = (v, lo, hi, a, b) => a + (hi === lo ? 0.5 : (v - lo) / (hi - lo)) * (b - a);

function simFanSVG(fan, samples) {
  const W = 720, H = 340, L = 46, R = 14, T = 14, B = 26;
  const steps = fan.steps, n = steps.length;
  const flat = [...fan.p05, ...fan.p95, ...samples.flat()];
  let lo = Math.min(...flat), hi = Math.max(...flat);
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const X = (i) => _lin(i, 0, n - 1, L, W - R);
  const Y = (v) => _lin(v, lo, hi, H - B, T);
  const bandPath = (top, bot) => {
    const up = top.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`);
    const dn = bot.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).reverse();
    return `M${up.join(" L")} L${dn.join(" L")} Z`;
  };
  const line = (arr) => arr.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const spaghetti = samples.map((p, i) =>
    `<polyline class="sim-path" style="animation-delay:${(i * 14)}ms"
       points="${line(p)}"/>`).join("");
  // zero reference + a few gridlines with % labels
  const ticks = 4, gl = [];
  for (let t = 0; t <= ticks; t++) {
    const v = lo + (hi - lo) * t / ticks, y = Y(v);
    gl.push(`<line x1="${L}" y1="${y.toFixed(1)}" x2="${W - R}" y2="${y.toFixed(1)}" class="sim-grid"/>
      <text x="${L - 6}" y="${(y + 3).toFixed(1)}" class="sim-axis" text-anchor="end">${signed(v, 0)}%</text>`);
  }
  const zeroY = Y(0);
  return `<svg viewBox="0 0 ${W} ${H}" class="sim-svg" preserveAspectRatio="xMidYMid meet">
    ${gl.join("")}
    <line x1="${L}" y1="${zeroY.toFixed(1)}" x2="${W - R}" y2="${zeroY.toFixed(1)}" class="sim-zero"/>
    <path class="sim-band outer" d="${bandPath(fan.p95, fan.p05)}"/>
    <path class="sim-band inner" d="${bandPath(fan.p75, fan.p25)}"/>
    <polyline class="sim-median" points="${line(fan.p50)}"/>
    <g class="sim-spaghetti">${spaghetti}</g>
    <text x="${L}" y="${H - 8}" class="sim-axis">today</text>
    <text x="${W - R}" y="${H - 8}" class="sim-axis" text-anchor="end">+${steps[n - 1]}d</text>
  </svg>`;
}

function simHistSVG(dist) {
  const W = 720, H = 220, L = 46, R = 14, T = 12, B = 26;
  const centers = dist.bin_centers_pct, models = dist.models;
  const all = Object.values(models).flat();
  const hi = Math.max(...all) || 1, lo = Math.min(...centers), hic = Math.max(...centers);
  const X = (v) => _lin(v, lo, hic, L, W - R);
  const Y = (d) => _lin(d, 0, hi, H - B, T);
  const area = (arr, cls, style) => {
    const pts = arr.map((d, i) => `${X(centers[i]).toFixed(1)},${Y(d).toFixed(1)}`);
    return `<polyline class="sim-hist ${cls}" style="${style}" points="${pts.join(" ")}"/>`;
  };
  const zeroX = X(0);
  const layers = Object.entries(models).map(([m, arr]) =>
    area(arr, m, `stroke:${MODEL_STYLE[m].c}`)).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="sim-svg" preserveAspectRatio="xMidYMid meet">
    <line x1="${zeroX.toFixed(1)}" y1="${T}" x2="${zeroX.toFixed(1)}" y2="${H - B}" class="sim-zero"/>
    <text x="${zeroX.toFixed(1)}" y="${H - 8}" class="sim-axis" text-anchor="middle">0%</text>
    <text x="${L}" y="${H - 8}" class="sim-axis">${signed(lo, 0)}%</text>
    <text x="${W - R}" y="${H - 8}" class="sim-axis" text-anchor="end">${signed(hic, 0)}%</text>
    ${layers}
  </svg>`;
}

function simEquitySVG(curve) {
  const W = 720, H = 240, L = 46, R = 14, T = 12, B = 22;
  const S = curve.map(p => p.strategy), N = curve.map(p => p.nifty);
  const lo = Math.min(...S, ...N), hi = Math.max(...S, ...N), n = curve.length;
  const X = (i) => _lin(i, 0, n - 1, L, W - R), Y = (v) => _lin(v, lo, hi, H - B, T);
  const line = (arr) => arr.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const y100 = Y(100);
  return `<svg viewBox="0 0 ${W} ${H}" class="sim-svg" preserveAspectRatio="xMidYMid meet">
    <line x1="${L}" y1="${y100.toFixed(1)}" x2="${W - R}" y2="${y100.toFixed(1)}" class="sim-zero"/>
    <polyline class="sim-eq nifty" points="${line(N)}"/>
    <polyline class="sim-eq strat" points="${line(S)}"/>
    <text x="${L}" y="${(y100 - 5).toFixed(1)}" class="sim-axis">base 100</text>
  </svg>`;
}

function simRiskTable(models, gap) {
  const rows = ["gaussian", "student_t", "bootstrap"].filter(m => models[m]);
  const cell = (m, k, d = 2) => signed(models[m][k], d);
  const tr = (m) => {
    const st = MODEL_STYLE[m];
    return `<tr><td><span class="sim-dot" style="background:${st.c}"></span>${st.label}</td>
      <td>${cell(m, "mean_return_pct")}%</td><td>${signed(models[m].volatility_pct, 1)}%</td>
      <td class="neg">${cell(m, "var_95_pct")}%</td><td class="neg">${cell(m, "var_99_pct")}%</td>
      <td class="neg">${cell(m, "cvar_95_pct")}%</td><td class="neg">${cell(m, "cvar_99_pct")}%</td>
      <td>${fmtN(models[m].skew, 2)}</td><td>${fmtN(models[m].excess_kurtosis, 2)}</td></tr>`;
  };
  return `<div class="tablewrap"><table class="sim-table"><thead><tr>
    <th>Return model</th><th>Mean</th><th>Vol</th><th>VaR 95</th><th>VaR 99</th>
    <th>CVaR 95</th><th>CVaR 99</th><th>Skew</th><th>Ex. kurt</th></tr></thead>
    <tbody>${rows.map(tr).join("")}</tbody></table></div>
    <div class="sub" style="margin-top:8px">Tail-model gap at 99%:
      <strong>${signed(gap, 2)} pts</strong> — the amount the Gaussian assumption
      under- or over-states the loss the bootstrap of real history actually shows.</div>`;
}

function simDrawdownBars(dd) {
  if (!dd) return `<div class="sub">Drawdown path simulation unavailable for this book.</div>`;
  const tp = dd.touch_probability_pct;
  const bars = Object.entries(tp).map(([lvl, pct]) =>
    `<div class="sim-bar-row"><span class="sim-bar-lbl">${lvl}</span>
      <div class="sim-bar-track"><div class="sim-bar-fill" style="width:${Math.min(100, pct)}%"></div></div>
      <span class="sim-bar-val">${fmtN(pct, 1)}%</span></div>`).join("");
  return `${bars}<div class="sub" style="margin-top:8px">Probability of EVER touching each
    drawdown over ${dd.horizon_days} sessions (path-dependent — what triggers a stop or a
    margin call), median max drawdown <strong>${signed(dd.median_max_drawdown_pct, 1)}%</strong>,
    worst simulated <strong>${signed(dd.worst_max_drawdown_pct, 1)}%</strong>.</div>`;
}

function simControls() {
  const hz = [[21, "1M"], [63, "3M"], [126, "6M"], [252, "1Y"]];
  const pa = [[10000, "10k"], [20000, "20k"], [40000, "40k"]];
  const btn = (v, lbl, key) =>
    `<button data-sim="${key}" data-v="${v}" class="${SIM[key] === v ? "active" : ""}">${lbl}</button>`;
  return `<div class="sim-controls">
    <div class="sim-ctl"><label>Horizon</label><div class="segbtns">
      ${hz.map(([v, l]) => btn(v, l, "horizon")).join("")}</div></div>
    <div class="sim-ctl"><label>Paths</label><div class="segbtns">
      ${pa.map(([v, l]) => btn(v, l, "paths")).join("")}</div></div>
    <button id="sim-run" class="primary">${SIM.running ? "Simulating…" : "⟳ Run simulation"}</button>
  </div>`;
}

async function viewSimulation() {
  document.title = "EquiSense · Simulation Studio";
  app.innerHTML = `<div class="viewhead"><h1>Simulation Studio</h1>
    <div class="sub">Monte Carlo on your actual book — three tail models over a
    bootstrap of real history. VaR is a point; the distribution is the truth.</div></div>
    ${simControls()}
    <div id="sim-body">${skeleton(6)}</div>`;
  wireSimControls();
  await runSimulation();
}

function wireSimControls() {
  app.querySelectorAll("[data-sim]").forEach(b => b.addEventListener("click", () => {
    SIM[b.dataset.sim] = parseInt(b.dataset.v);
    app.querySelectorAll(`[data-sim="${b.dataset.sim}"]`).forEach(x => x.classList.remove("active"));
    b.classList.add("active");
  }));
  const run = document.getElementById("sim-run");
  if (run) run.addEventListener("click", runSimulation);
}

async function runSimulation() {
  const body = document.getElementById("sim-body");
  const runBtn = document.getElementById("sim-run");
  if (SIM.running) return;
  SIM.running = true;
  if (runBtn) { runBtn.disabled = true; runBtn.textContent = "Simulating…"; }
  if (body) body.innerHTML = `<div class="panel sim-loading">
    <div class="sim-pulse"></div>Drawing ${fmtN(SIM.paths, 0)} paths over ${SIM.horizon} sessions…</div>`;
  try {
    const [sim, bt] = await Promise.all([
      api(`/markets/simulate?horizon_days=${SIM.horizon}&paths=${SIM.paths}&detail=1`),
      api(`/backtest/strategy`).catch(() => null),
    ]);
    SIM.data = sim; SIM.bt = bt;
    renderSimulation(sim, bt);
  } catch (e) {
    if (body) body.innerHTML = `<div class="panel"><strong>Simulation unavailable:</strong>
      ${esc(e.message)}</div>`;
  } finally {
    SIM.running = false;
    if (runBtn) { runBtn.disabled = false; runBtn.textContent = "⟳ Run simulation"; }
  }
}

function renderSimulation(sim, bt) {
  const body = document.getElementById("sim-body");
  if (!body) return;
  if (!sim || !sim.available) {
    body.innerHTML = `<div class="panel"><strong>No simulable book yet.</strong>
      <div class="sub" style="margin-top:6px">${esc((sim && sim.reason) ||
      "Add positions on the Trading desk, or the live universe proxy needs more history.")}</div></div>`;
    return;
  }
  const risk = sim.risk, legend = Object.entries(MODEL_STYLE).map(([m, s]) =>
    `<span class="sim-leg"><span class="sim-dot" style="background:${s.c}"></span>${s.label}</span>`).join("");
  const fan = risk.fan, dist = risk.distribution, samples = sim.risk.sample_paths || [];

  body.innerHTML = `
    <div class="panel sim-hero">
      <div class="panel-head"><h3>Forward path simulation</h3>
        <span class="sub">${esc(sim.basis)} · ${fmtN(risk.n_paths, 0)} paths · ${risk.horizon_days} sessions</span></div>
      ${fan ? simFanSVG(fan, samples) : `<div class="sub">Path fan unavailable.</div>`}
      <div class="sim-legend"><span class="sim-leg"><span class="sim-swatch band"></span>5–95% cone</span>
        <span class="sim-leg"><span class="sim-swatch band inner"></span>25–75%</span>
        <span class="sim-leg"><span class="sim-swatch median"></span>median</span>
        <span class="sim-leg"><span class="sim-swatch thread"></span>sample paths</span></div>
    </div>

    <div class="grid2">
      <div class="panel"><div class="panel-head"><h3>Terminal outcome distribution</h3></div>
        ${dist ? simHistSVG(dist) : ""}
        <div class="sim-legend">${legend}</div>
        <div class="sub" style="margin-top:6px">${esc(risk.interpretation || "")}</div></div>
      <div class="panel"><div class="panel-head"><h3>Drawdown — probability of touching</h3></div>
        ${simDrawdownBars(sim.drawdown)}</div>
    </div>

    <div class="panel"><div class="panel-head"><h3>Risk under three tail models</h3>
      <span class="sub">${esc(risk.correlation_estimator || "")}</span></div>
      ${simRiskTable(risk.models, risk.tail_model_gap_99_pct)}</div>

    ${bt ? `<div class="panel"><div class="panel-head"><h3>Strategy backtest — investable equity curve</h3>
      <span class="sub">vs NIFTY, base 100</span></div>
      ${simEquitySVG(bt.curve)}
      <div class="sim-legend">
        <span class="sim-leg"><span class="sim-swatch median"></span>Strategy</span>
        <span class="sim-leg"><span class="sim-swatch thread"></span>NIFTY</span></div>
      <div class="sim-stats">
        ${simStat("Total return", signed(bt.total_return_pct, 1) + "%")}
        ${simStat("NIFTY", signed(bt.nifty_total_return_pct, 1) + "%")}
        ${simStat("Annualised (net)", signed(bt.annualized_net_pct, 1) + "%")}
        ${simStat("Deflated Sharpe", fmtN(bt.deflated_sharpe, 2))}
        ${simStat("Hit rate", fmtN((bt.hit_rate || 0) * 100, 0) + "%")}
      </div>
      <div class="sub" style="margin-top:8px">${esc((bt.caveats || []).slice(-1)[0] || "")}</div></div>` : ""}
  `;
  requestAnimationFrame(() => body.querySelectorAll(".sim-svg").forEach(s => s.classList.add("in")));
}

function simStat(label, value) {
  return `<div class="sim-stat"><div class="sim-stat-v">${value}</div>
    <div class="sim-stat-l">${esc(label)}</div></div>`;
}

/* ============================================================== routing */

/* Views whose numbers move with live quotes. Lab and Research render study
   output that only changes when the PIPELINE runs, so re-rendering them on a
   quote tick would refetch heavy endpoints and fight the user's scroll to
   redraw identical figures. They refresh when the data behind them actually
   changes instead — see refreshStatusStrip. */
const LIVE_VIEWS = new Set(["dashboard", "companies", "portfolio", "trading", "markets"]);

function currentView() {
  return (location.hash.replace(/^#\//, "") || "dashboard").split("/")[0];
}

async function route(opts) {
  /* opts.silent: redraw in place for an automatic refresh — no skeleton flash,
     scroll position preserved. Called with a hashchange Event for real
     navigation, where `silent` is simply absent. */
  const silent = !!(opts && opts.silent === true);
  const parts = (location.hash.replace(/^#\//, "") || "dashboard").split("/");
  const [name, arg, sub] = parts;
  const scrollY = window.scrollY;
  document.querySelectorAll("#nav a").forEach(a =>
    a.classList.toggle("active", a.dataset.route === name));
  if (!silent) {
    app.innerHTML = `<div class="skel" style="height:22px;width:220px;margin:4px 0 16px"></div>
      ${skeleton(5)}<div class="grid2">${skeleton(4)}${skeleton(4)}</div>`;
  }
  try {
    if (name === "companies" && arg) await viewCompanyDetail(parseInt(arg), sub || "overview");
    else if (name === "companies") await viewCompanies();
    else if (name === "portfolio") await viewPortfolio(arg || "real");
    else if (name === "trading") await viewTrading();
    else if (name === "research") await viewResearch();
    else if (name === "lab") await viewLab(arg || "hypotheses");
    else if (name === "markets") await viewMarkets(arg || "derivatives");
    else if (name === "simulation") await viewSimulation();
    else await viewDashboard();
  } catch (e) {
    app.innerHTML = `<div class="panel"><strong>Error:</strong> ${esc(e.message)}
      <div class="sub" style="margin-top:6px">If this is a data problem, the
      <a href="#/lab/data">data health page</a> is the place to start.</div></div>`;
  }
  if (silent) window.scrollTo(0, scrollY);
}

async function quoteLoop() {
  try {
    const q = await api("/live/quotes", { method: "POST" });
    cache.market = q.market;
    cache.quotes = q.prices;
    cache.quotesAt = new Date();
    /* Re-render the market-facing views, not just the trading desk. Quotes
       were being fetched every 5 minutes on every page and then thrown into
       the cache while the view kept showing the numbers it rendered on load —
       so the site looked frozen everywhere except one tab. Silent redraw, so
       an automatic refresh never flashes a skeleton or moves the scroll, and
       never yanks the DOM out from under an in-progress edit. */
    if (LIVE_VIEWS.has(currentView()) && !isEditingForm()) await route({ silent: true });
    refreshStatusStrip();
  } catch { /* quotes are best-effort; status strip reports staleness */ }
  finally { scheduleQuotes(); }
}

/* Poll on the exchange's clock, not a fixed timer. Once NSE closes, today's
   bar is final: every further poll re-downloads the same numbers, reports
   "updated 0, inserted 0", and spends a serverless invocation to redraw
   identical figures. Back off to a slow heartbeat that still catches the
   post-close settle and the next open. */
const QUOTE_INTERVAL_OPEN_MS = 5 * 60 * 1000;
const QUOTE_INTERVAL_CLOSED_MS = 30 * 60 * 1000;
let quoteTimer = null;

function stopQuotes() { clearTimeout(quoteTimer); quoteTimer = null; }

function scheduleQuotes() {
  clearTimeout(quoteTimer);
  /* A hidden tab cannot show a price, so polling one spends a serverless
     invocation to update pixels nobody is looking at — and a user with the
     dashboard and trading desk both open doubles that for no benefit. Polling
     resumes on focus with an IMMEDIATE fetch, so returning to the tab never
     shows a stale number while waiting out the rest of an interval. */
  if (document.hidden) { quoteTimer = null; return; }
  const open = !!(cache.market && cache.market.open);
  quoteTimer = setTimeout(quoteLoop,
    open ? QUOTE_INTERVAL_OPEN_MS : QUOTE_INTERVAL_CLOSED_MS);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopQuotes();
  else if (!quoteTimer) quoteLoop();     // refetch at once, then reschedule
});

document.getElementById("help-grid").innerHTML = SHORTCUTS.map(([k, d]) =>
  `<span>${k.split(" ").map(p => `<kbd>${p}</kbd>`).join(" ")}</span><span>${d}</span>`).join("");
document.getElementById("help-overlay").addEventListener("click", (e) => {
  if (e.target.id === "help-overlay") e.target.hidden = true;
});
initTheme();

window.addEventListener("hashchange", route);
refreshStatusStrip();
setInterval(refreshStatusStrip, 120000);
quoteLoop();   // self-reschedules on the exchange clock (see scheduleQuotes)
route();
