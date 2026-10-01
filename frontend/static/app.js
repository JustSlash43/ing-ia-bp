// apiUrl "" = mismo origen (imagen única en Cloud Run); si no hay config, backend local.
const API = (window.APP_CONFIG?.apiUrl ?? "http://localhost:8000").replace(/\/$/, "");
// Sin este header, ngrok responde a los navegadores con una página HTML de aviso en vez del JSON.
const BASE_HEADERS = { "ngrok-skip-browser-warning": "1" };
const api = (path, options = {}) => fetch(`${API}${path}`, { ...options, headers: { ...BASE_HEADERS, ...options.headers } });
const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (v) => v == null ? "–" : `$${Number(v).toFixed(6)}`;
const int = (v) => v == null ? "–" : Number(v).toLocaleString("es");
const form = $("#query-form");
const caseResults = {};   // id -> { running, pass, actual, runs: [{ result, title }] }
let busy = false;
let selectedCase = null;  // caso cuyo resultado se está viendo
let followLive = true;    // false cuando el usuario abre un resultado anterior durante una ejecución

// ------------------------------------------------------------------ API

async function postQuery(body) {
  const started = performance.now();
  try {
    const res = await api("/api/v1/analysis/query", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    return { httpStatus: res.status, data, clientMs: performance.now() - started };
  } catch (e) {
    return { httpStatus: 0, data: { detail: `No se pudo conectar con ${API}: ${e.message}` }, clientMs: performance.now() - started };
  }
}

async function loadConfig() {
  try {
    const cfg = await (await api("/api/v1/config")).json();
    const l = cfg.limits;
    $("#api-info").textContent = `API ${API || window.location.origin} · modelo ${cfg.model} · precio $${cfg.pricing_usd_per_million.input}/$${cfg.pricing_usd_per_million.output} por 1M tokens (in/out)`;
    $("#limits").innerHTML = [
      `max_steps ${l.max_steps}`, `tokens/consulta ${int(l.max_tokens_budget)}`,
      `input ${int(l.max_input_tokens_budget)}`, `output ${int(l.max_output_tokens_budget)}`,
      `tokens/sesión ${int(l.session_token_budget)}`,
      `timeout ${l.timeout_seconds}s`, `delay tool ${l.tool_delay_seconds}s`, `loop ≥${l.loop_threshold}`,
    ].map((t) => `<span class="chip">${esc(t)}</span>`).join("");
  } catch {
    $("#api-info").textContent = `No se pudo conectar con la API en ${API}. ¿Está levantado el backend?`;
  }
}

async function loadSession(sessionId) {
  try {
    const s = await (await api(`/api/v1/sessions/${encodeURIComponent(sessionId)}/budget`)).json();
    $("#session").innerHTML = `<h3>Sesión ${esc(sessionId)}</h3>` +
      bar("Tokens de sesión", s.tokens_used, s.token_limit, `${int(s.tokens_remaining)} restantes`) +
      `<p class="muted small">${s.queries} consultas · costo acumulado ${usd(s.cost_usd)}</p>`;
  } catch { $("#session").innerHTML = ""; }
}

// ------------------------------------------------------------------ render

function bar(label, used, limit, note) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
  const cls = pct >= 100 ? "full" : pct >= 75 ? "high" : "";
  return `<div class="bar"><div class="bar-label"><span>${esc(label)}</span><span>${esc(note)}</span></div>
    <div class="bar-track"><div class="bar-fill ${cls}" style="width:${pct.toFixed(1)}%"></div></div></div>`;
}

function render({ httpStatus, data, clientMs }, title, nav = "") {
  const el = $("#result");
  if (httpStatus !== 200) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => `${d.loc?.join(".")}: ${d.msg}`).join("; ") : data.detail;
    el.innerHTML = `${nav}<div class="result-head"><h2>${esc(title || "Resultado")}</h2>
      <span class="badge error">HTTP ${httpStatus || "sin conexión"}</span></div>
      <div class="error-box">${esc(detail || "Error")}</div>${raw(data)}`;
    return;
  }
  const q = data.budget.query || {};
  const s = data.budget.session || {};
  const u = data.usage;
  el.innerHTML = `${nav}
    <div class="result-head">
      <h2 style="margin:0">${esc(title || "Resultado")}</h2>
      <span class="badge ${esc(data.status)}">${esc(data.status)}</span>
      ${data.stop_reason ? `<span class="chip">stop_reason: ${esc(data.stop_reason)}</span>` : ""}
      <span class="chip">${esc(data.analysis_id)}</span>
    </div>
    ${data.warning ? `<div class="warning">⚠ ${esc(data.warning)}</div>` : ""}
    <h3>Respuesta</h3>
    <div class="answer">${esc(data.answer || "(sin respuesta)")}</div>

    <div class="kpis">
      <div class="kpi"><b>${u.agent_steps}</b><span>pasos del agente</span></div>
      <div class="kpi"><b>${int(u.tokens_used)}</b><span>tokens (${int(u.input_tokens)} in / ${int(u.output_tokens)} out)</span></div>
      <div class="kpi"><b>${usd(u.cost_usd)}</b><span>costo de la consulta</span></div>
      <div class="kpi"><b>${u.elapsed_time.toFixed(2)}s</b><span>tiempo del agente (${(clientMs / 1000).toFixed(2)}s total)</span></div>
    </div>

    <h3>Presupuesto restante</h3>
    ${bar("Tokens de la consulta (total)", q.tokens_used, q.token_budget, `${int(q.tokens_remaining)} de ${int(q.token_budget)} restantes`)}
    ${bar("Tokens de input", q.input_tokens_used, q.input_token_budget, `${int(q.input_tokens_remaining)} de ${int(q.input_token_budget)} restantes`)}
    ${bar("Tokens de output", q.output_tokens_used, q.output_token_budget, `${int(q.output_tokens_remaining)} de ${int(q.output_token_budget)} restantes`)}
    ${bar("USD de la consulta", q.cost_usd, q.cost_budget_usd, `${usd(q.cost_remaining_usd)} de ${usd(q.cost_budget_usd)} restantes`)}
    ${s.token_limit ? bar("Tokens de la sesión", s.tokens_used, s.token_limit, `${int(s.tokens_remaining)} de ${int(s.token_limit)} restantes`) : ""}

    <h3>Métricas FinOps por paso</h3>
    ${stepsTable(data.steps)}

    <h3>Datos obtenidos (artifacts de las herramientas)</h3>
    ${dataTable(data.data)}

    <details><summary>Hallazgos de las reglas del dominio</summary>${raw(data.findings)}</details>
    <details><summary>Auditoría del orquestador (${data.audit_trail.length} nodos)</summary>${auditTable(data.audit_trail)}</details>
    <details><summary>Llamadas a herramientas (permitidas / bloqueadas)</summary>${raw({ tool_calls: data.agent.tool_calls, blocked_tool_calls: data.agent.blocked_tool_calls })}</details>
    <details><summary>JSON completo</summary>${raw(data)}</details>`;
}

function stepsTable(steps) {
  if (!steps.length) return `<p class="muted">No se ejecutaron pasos.</p>`;
  const rows = steps.map((s) => `<tr>
    <td class="num">${s.step_number}</td><td>${esc((s.tool_called || ["— respuesta final"]).join(", "))}</td>
    <td class="num">${int(s.tokens_used)}</td><td class="num">${int(s.input_tokens)} / ${int(s.output_tokens)}</td>
    <td class="num">${int(s.cumulative_tokens)}</td>
    <td class="num">${usd(s.step_cost)}</td><td class="num">${usd(s.cumulative_cost)}</td>
    <td class="num">${int(s.remaining_tokens)}</td><td class="num">${usd(s.remaining_budget_usd)}</td>
    <td class="num">${s.llm_latency_ms} / ${s.tool_latency_ms}</td><td class="num">${s.elapsed_time.toFixed(2)}s</td></tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>
    <th class="num">step</th><th>tool_called</th><th class="num">tokens_used</th><th class="num">in / out</th>
    <th class="num">acumulado</th>
    <th class="num">costo paso</th><th class="num">cumulative_cost</th><th class="num">tokens rest.</th>
    <th class="num">USD rest.</th><th class="num">ms LLM / tool</th><th class="num">elapsed_time</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

function dataTable(data) {
  const rows = [
    ...data.searches.map((s) => `<tr><td>search_financial_data</td><td>${esc(s.requested_metric)} · ${esc(s.requested_period)}</td>
      <td>${s.found ? `${Number(s.value).toLocaleString("es")} ${esc(s.unit)} ${esc(s.currency)}` : `<span class="verdict fail">${esc(s.error)}</span>`}</td></tr>`),
    ...data.ratios.map((r) => `<tr><td>calculate_ratio</td><td>${esc(r.numerator)} / ${esc(r.denominator)}</td>
      <td>${r.result ?? `<span class="verdict fail">${esc(r.error)}</span>`}</td></tr>`),
  ];
  if (!rows.length) return `<p class="muted">Sin datos.</p>`;
  return `<div class="table-wrap"><table><thead><tr><th>herramienta</th><th>entrada</th><th>resultado</th></tr></thead>
    <tbody>${rows.join("")}</tbody></table></div>`;
}

function auditTable(audit) {
  const rows = audit.map((a) => `<tr><td class="num">${a.step}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td>
    <td class="num">${a.detail?.latency_ms ?? "–"} ms</td></tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th>nodo</th><th>acción</th><th class="num">latencia</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

const raw = (obj) => `<pre>${esc(JSON.stringify(obj, null, 2))}</pre>`;

// ------------------------------------------------------------------ casos de prueba

function expectLabel(e) {
  if (e.status === "http_422") return "HTTP 422";
  const reasons = e.stopReasons.filter(Boolean);
  return reasons.length ? `${e.status} · ${reasons.join(" | ")}` : e.status;
}

function evaluate(c, result) {
  const e = c.expect;
  if (e.status === "http_422") return result.httpStatus === 422;
  return result.httpStatus === 200 && result.data.status === e.status && e.stopReasons.includes(result.data.stop_reason ?? null);
}

function actualLabel(result) {
  if (result.httpStatus !== 200) return `HTTP ${result.httpStatus || "–"}`;
  return result.data.stop_reason ? `${result.data.status} · ${result.data.stop_reason}` : result.data.status;
}

function renderCases() {
  $("#cases").innerHTML = window.TEST_CASES.map((c) => {
    const r = caseResults[c.id];
    const verdict = r ? `<span class="verdict ${r.pass ? "pass" : "fail"}">${r.pass ? "✓" : "✗"} ${esc(r.actual)}</span>` : "";
    const title = `${esc(c.title)} ${c.llm ? '<span class="tag">LLM</span>' : ""}`;
    const titleEl = r?.runs?.length
      ? `<button class="link case-title" data-view="${c.id}" title="Ver el resultado">${title}</button>`
      : `<span class="case-title">${title}</span>`;
    return `<li class="case ${selectedCase === c.id ? "selected" : ""}">
      <div class="case-top">${titleEl}
        <button class="ghost" data-case="${c.id}" ${busy ? "disabled" : ""}>${r?.running ? "Ejecutando…" : "Ejecutar"}</button></div>
      <p>${esc(c.description)}</p>
      <div class="case-foot"><span class="expect">Esperado: ${esc(expectLabel(c.expect))}</span>${verdict}</div>
    </li>`;
  }).join("");
}

async function runCase(c) {
  const sessionId = `T-${c.id}-${Date.now().toString(36)}`;  // sesión aislada por caso
  const body = { analyst_id: form.elements.analyst_id.value || "A01", session_id: sessionId, ...c.request };
  const runs = [];
  caseResults[c.id] = { running: true, runs };
  renderCases();
  let result;
  for (let i = 1; i <= (c.repeatUntil ? c.maxRuns : 1); i++) {
    result = await postQuery(body);
    runs.push({ result, title: c.repeatUntil ? `${c.title} (consulta ${i})` : c.title });
    if (followLive) showCase(c.id);
    if (!c.repeatUntil || result.httpStatus !== 200 || result.data.stop_reason === c.repeatUntil) break;
  }
  caseResults[c.id] = { pass: evaluate(c, result), actual: actualLabel(result), runs };
  renderCases();
  if (result.httpStatus === 200 && followLive) loadSession(sessionId);
}

function showCase(id, index) {
  const { runs } = caseResults[id];
  const i = index ?? runs.length - 1;
  const nav = runs.length > 1
    ? `<div class="run-nav">${runs.map((_, n) => `<button class="ghost ${n === i ? "active" : ""}" data-case-id="${id}" data-run="${n}">Consulta ${n + 1}</button>`).join("")}</div>`
    : "";
  selectedCase = id;
  render(runs[i].result, runs[i].title, nav);
  renderCases();
}

async function withBusy(fn) {
  busy = true; followLive = true; $("#submit").disabled = true; $("#run-all").disabled = true; renderCases();
  try { await fn(); } finally { busy = false; $("#submit").disabled = false; $("#run-all").disabled = false; renderCases(); }
}

// ------------------------------------------------------------------ eventos

form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const body = {
    analyst_id: form.elements.analyst_id.value, session_id: form.elements.session_id.value,
    query: form.elements.query.value, max_budget_usd: Number(form.elements.max_budget_usd.value),
  };
  withBusy(async () => {
    $("#result").innerHTML = `<div class="empty">Analizando… (cada búsqueda tarda ~2 s)</div>`;
    selectedCase = null;
    render(await postQuery(body), "Consulta manual");
    loadSession(body.session_id);
  });
});

$("#new-session").addEventListener("click", () => {
  form.elements.session_id.value = `S-${Date.now().toString(36).toUpperCase()}`;
  loadSession(form.elements.session_id.value);
});
form.elements.session_id.addEventListener("change", () => loadSession(form.elements.session_id.value));

$("#cases").addEventListener("click", (ev) => {
  const view = ev.target.closest("button[data-view]")?.dataset.view;
  if (view) {  // reabrir un resultado anterior; durante una ejecución, deja de seguir el caso en curso
    followLive = !busy;
    showCase(view);
    return;
  }
  const id = ev.target.closest("button[data-case]")?.dataset.case;
  if (id && !busy) withBusy(() => runCase(window.TEST_CASES.find((c) => c.id === id)));
});

$("#result").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-run]");
  if (btn) showCase(btn.dataset.caseId, Number(btn.dataset.run));
});

$("#run-all").addEventListener("click", () => withBusy(async () => {
  for (const c of window.TEST_CASES.filter((c) => !c.repeatUntil)) await runCase(c);
}));

loadConfig();
loadSession(form.elements.session_id.value);
renderCases();
