# Maya Ops Mockup-Fidelity Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the authenticated Maya Ops frontend so its layout, density and component quality closely match the approved mockup while rendering only the existing read-only operational payload.

**Architecture:** Keep the FastAPI application, SQLite reader, dashboard payload, authentication, ETag and SSE contracts unchanged. Replace the static application shell and presentation layer in bounded stages: semantic shell, executive components, analytics, operations table, and an execution drawer that reuses the existing canvas/inspector logic and async epochs. Qualify each stage with static DOM contracts, deterministic Chromium race tests, a real-browser smoke and immutable database checks.

**Tech Stack:** FastAPI static assets, semantic HTML5, CSS3, vanilla JavaScript/DOM/SVG, pytest, Playwright 1.55.0 Chromium in the pinned Docker image, SQLite read-only fixture.

## Global Constraints

- Work only in `/home/ubuntu/agente-v2/.worktrees/maya-ops-existing-data-dashboard` on `feature/maya-ops-existing-data-dashboard`.
- Start from design commit `37d82b1290661382f18dda713a1db20732cd99f8` or a descendant containing only reviewed work from this plan.
- Treat `docs/superpowers/specs/2026-08-22-maya-ops-mockup-fidelity-redesign.md` as the visual/interaction contract.
- Treat `docs/superpowers/specs/2026-08-21-maya-ops-existing-data-dashboard-design.md` as the authoritative data/safety contract.
- Use `/home/ubuntu/maya-dashboard-mockup/artifacts/dashboard-desktop.png`, `index.html`, and `styles.css` as the canonical visual reference; do not import `app.js` or its synthetic dataset.
- Render only the current authenticated `/ops/api/dashboard`, execution, node, full-content and SSE payloads.
- Do not add or infer revenue, conversion, interest, commercial stage, satisfaction, next step, conversation summary, handoff reason, trend versus a previous period, or demo data.
- Do not change Maya, prompts, skills, tools, ManyChat, providers, reservations, payments, handoffs, runtime, instrumentation, schema, writer, legacy dashboard or V3.
- Do not add PUT, PATCH, DELETE, effect POSTs, forms other than logout, or JavaScript fetch methods other than the default GET.
- Keep SQLite/DB/WAL/SHM unchanged by all dashboard requests and browser smokes.
- Preserve dashboard/detail epochs, independent Input/Output full epochs, stale-response suppression, ETag behavior and SSE error separation.
- Use safe DOM APIs; no `innerHTML` with API data.
- Browser evidence must use real Chromium; the final smoke must hard-fail when pinned browser capability is unavailable.
- Primary approval viewports are `1440 × 1000` and `390 × 844`.
- No build, deploy, restart or production smoke is part of this plan. Promotion requires a separate explicit authorization after final review.
- Each task ends with a focused green test set, `git diff --check`, an exact pathset check and one commit.

## File Structure

- `v2_ops/static/index.html` — semantic mockup-fidelity shell, real navigation, operations surface and execution drawer.
- `v2_ops/static/ops.css` — canonical tokens, layout, components, drawer, responsive rules and reduced-motion behavior.
- `v2_ops/static/ops.js` — current data flow plus safe component rendering, filters, refresh/menu/drawer interactions and race protection.
- `tests/test_v2_ops_ui.py` — closed DOM/control allowlist, forbidden-copy guards, JavaScript contract and deterministic Chromium interleavings.
- `tests/browser/ops_dashboard_smoke.py` — real app/browser qualification, screenshots, responsive assertions, drawer behavior and DB invariance evidence.
- `docs/refactor/ACTIVE.md` — bounded progress/evidence only.
- `docs/superpowers/reports/2026-08-22-maya-ops-mockup-fidelity-redesign-verification.md` — final immutable handoff report created in Task 7.

### Stable frontend interfaces

The completed implementation keeps or introduces these browser-facing functions:

```javascript
loadDashboard() -> Promise<void>
renderDashboard() -> void
renderKpis() -> void
renderExecutionSeries() -> void
renderStatusDistribution() -> void
renderTraceDistribution() -> void
renderMilestones() -> void
renderTopNodeTypes() -> void
filteredExecutions() -> ExecutionSummary[]
renderExecutionTable() -> void
openExecution(executionId) -> Promise<void>
closeExecutionDrawer({ restoreFocus = true } = {}) -> void
setExecutionDrawerOpen(open) -> void
clearDetail(executionId = "Execução") -> void
renderCanvas() -> void
selectNode(nodeId) -> void
loadFull(side) -> Promise<void>
refreshDashboard() -> Promise<void>
setMobileNavigationOpen(open) -> void
connectLive() -> void
```

No task renames the payload fields or changes the backend endpoint signatures.

---

### Task 1: Mockup-Fidelity Semantic Shell and Closed DOM Contract

**Files:**
- Modify: `v2_ops/static/index.html`
- Modify: `tests/test_v2_ops_ui.py:16-235`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: current IDs needed by `ops.js`, the approved visual structure, logout CSRF placeholder `{{CSRF}}`.
- Produces: stable IDs/classes for Tasks 2–6; a closed allowlist that permits only real controls.

- [ ] **Step 1: Replace the expected ID and control contracts in the test before changing HTML**

Use this exact expanded ID set:

```python
EXPECTED_IDS = {
    "app-sidebar", "sidebar-backdrop", "mobile-menu", "sidebar-close",
    "nav-overview", "nav-execution", "live-state", "source-health-state",
    "generated-at", "range-select", "refresh-dashboard", "operator-menu",
    "operator-popover", "dashboard-alert", "overview-view", "kpi-grid",
    "execution-series", "status-distribution", "trace-distribution",
    "milestones-chart", "top-node-types", "lead-search", "status-filter",
    "completeness-filter", "result-count", "operations-title", "empty-state",
    "execution-table-body", "execution-mobile-list", "drawer-backdrop",
    "execution-drawer", "drawer-close", "drawer-title", "drawer-lead",
    "drawer-status", "drawer-trace", "drawer-summary", "canvas-title",
    "execution-canvas", "edges", "nodes", "fit-canvas", "zoom-in",
    "zoom-out", "node-title", "input-panel", "input-summary", "input-full",
    "load-full-input", "output-panel", "output-summary", "output-full",
    "load-full-output", "metadata", "node-error",
}

EXPECTED_CONTROLS = (
    ("button", None, "submit"),
    ("button", "drawer-close", "button"),
    ("button", "fit-canvas", "button"),
    ("button", "load-full-input", "button"),
    ("button", "load-full-output", "button"),
    ("button", "mobile-menu", "button"),
    ("button", "nav-execution", "button"),
    ("button", "nav-overview", "button"),
    ("button", "operator-menu", "button"),
    ("button", "refresh-dashboard", "button"),
    ("button", "sidebar-close", "button"),
    ("button", "sidebar-backdrop", "button"),
    ("button", "zoom-in", "button"),
    ("button", "zoom-out", "button"),
    ("input", None, "hidden"),
    ("input", "lead-search", "text"),
    ("select", "completeness-filter", None),
    ("select", "range-select", None),
    ("select", "status-filter", None),
)
```

Change `assert_dashboard_dom_contract()` so it asserts:

```python
overview = by_id["overview-view"]
drawer = by_id["execution-drawer"]
canvas = by_id["execution-canvas"]
assert overview.parent is not None and overview.parent.tag == "main"
assert drawer.tag == "aside"
assert drawer.attrs.get("aria-hidden") == "true"
assert "open" not in (drawer.attrs.get("class") or "").split()
assert is_descendant(canvas, drawer)
assert is_descendant(by_id["input-panel"], drawer)
assert is_descendant(by_id["output-panel"], drawer)
assert "hidden" in by_id["drawer-backdrop"].attrs
```

Keep the exact single logout form assertion and update the submit text to `Sair`.

- [ ] **Step 2: Add causal mutants for the drawer and forbidden controls**

Add mutants that must be rejected:

```python
(
    lambda html: html.replace('aria-hidden="true" id="execution-drawer"',
                              'aria-hidden="false" id="execution-drawer"'),
    "drawer must start closed",
),
(
    lambda html: html.replace('<div id="drawer-summary"',
                              '<button id="retry-execution" type="button">Retry</button><div id="drawer-summary"'),
    "unexpected control tag/id/type",
),
(
    lambda html: html.replace('<aside id="execution-drawer"',
                              '<template><aside id="execution-drawer"', 1)
                       .replace('</aside>\n  <script', '</aside></template>\n  <script', 1),
    "is inert",
),
```

The exact HTML substrings may be adjusted once the new shell is written, but each mutant must first be demonstrated to change the fixture and fail for its named causal reason.

- [ ] **Step 3: Run the focused DOM test and verify RED**

Run:

```bash
venv/bin/python -m pytest \
  tests/test_v2_ops_ui.py::test_dashboard_shell_dom_contract \
  tests/test_v2_ops_ui.py::test_dom_contract_rejects_causal_mutants -q
```

Expected: FAIL because the old shell has `execution-view`, lacks the new IDs and does not contain the drawer contract.

- [ ] **Step 4: Replace `index.html` with the approved semantic structure**

Use this document hierarchy and preserve the exact API-dependent IDs:

```html
<body>
  <div class="app-shell">
    <aside id="app-sidebar" class="sidebar" aria-label="Navegação principal">
      <div class="brand-lockup">
        <span class="brand-mark" aria-hidden="true"><span>M</span></span>
        <div><strong>Maya Ops</strong><small>Operação Maya V2</small></div>
      </div>
      <span class="readonly-chip"><i aria-hidden="true"></i>SOMENTE LEITURA</span>
      <button id="sidebar-close" class="sidebar-close" type="button" aria-label="Fechar navegação">×</button>
      <p class="nav-label">OPERAÇÃO</p>
      <nav class="main-nav" aria-label="Navegação operacional">
        <button id="nav-overview" class="nav-item active" type="button" aria-current="page"><span aria-hidden="true">⌂</span><span>Visão geral</span></button>
        <button id="nav-execution" class="nav-item" type="button" disabled><span aria-hidden="true">⌘</span><span>Execuções</span></button>
      </nav>
      <div class="sidebar-foot">
        <div class="source-health"><i class="online-dot" aria-hidden="true"></i><div><strong id="source-health-state">Conectando</strong><small>Fonte operacional</small></div></div>
        <p>Dados existentes · acesso read-only</p>
      </div>
    </aside>
    <button id="sidebar-backdrop" class="sidebar-backdrop" type="button" hidden aria-label="Fechar navegação"></button>
    <main class="main-content">
      <header class="dashboard-header">
        <div class="header-copy">
          <button id="mobile-menu" class="mobile-menu" type="button" aria-label="Abrir navegação">☰</button>
          <div><p class="eyebrow">OPERAÇÃO MAYA V2</p><h1>Painel operacional</h1><p>Execuções e traces já registrados.</p></div>
        </div>
        <div class="header-actions">
          <label class="range-control"><span>Período</span><select id="range-select"><option value="24h">24 horas</option><option value="7d" selected>7 dias</option><option value="30d">30 dias</option></select></label>
          <button id="refresh-dashboard" class="icon-button" type="button" aria-label="Atualizar painel"><span aria-hidden="true">↻</span><span>Atualizar</span></button>
          <button id="operator-menu" class="operator-button" type="button" aria-expanded="false" aria-controls="operator-popover"><span>OP</span><div><strong>Operador</strong><small>Conta autenticada</small></div></button>
          <div id="operator-popover" class="operator-popover" hidden>
            <form method="post" action="/ops/logout"><input type="hidden" name="csrf" value="{{CSRF}}"><button type="submit">Sair</button></form>
          </div>
        </div>
      </header>
      <section id="overview-view">
        <div class="welcome-row"><div><p class="eyebrow">VISÃO GERAL</p><h2>Atividade operacional</h2><p>Somente fatos persistidos no trace do V2.</p></div><div class="health-pill"><i class="online-dot" aria-hidden="true"></i><strong id="live-state" aria-live="polite">Conectando…</strong><small id="generated-at">Ainda não atualizado</small></div></div>
        <section id="dashboard-alert" class="alert" hidden aria-live="polite"></section>
        <section id="kpi-grid" class="kpi-grid" aria-label="Indicadores operacionais" aria-live="polite"></section>
        <section class="analytics-grid" aria-label="Indicadores analíticos">
          <article class="analytics-panel volume-panel"><div class="panel-heading"><div><p class="eyebrow">VOLUME</p><h2>Execuções no período</h2></div><span class="panel-meta">Baldes UTC</span></div><div id="execution-series" class="volume-chart"></div></article>
          <article class="analytics-panel"><div class="panel-heading"><div><p class="eyebrow">ESTADO</p><h2>Estados atuais</h2></div></div><div id="status-distribution"></div></article>
          <article class="analytics-panel"><div class="panel-heading"><div><p class="eyebrow">TRACE</p><h2>Completude do trace</h2></div></div><div id="trace-distribution"></div></article>
          <article class="analytics-panel"><div class="panel-heading"><div><p class="eyebrow">MARCOS</p><h2>Marcos registrados</h2></div><span class="panel-meta">Categorias sobrepostas</span></div><div id="milestones-chart"></div></article>
          <article class="analytics-panel wide"><div class="panel-heading"><div><p class="eyebrow">NÓS</p><h2>Nós registrados</h2></div></div><div id="top-node-types"></div></article>
        </section>
        <section class="operations-panel" aria-labelledby="operations-title">
          <div class="operations-head"><div><p class="eyebrow">OPERAÇÕES</p><h2 id="operations-title">Execuções recentes</h2><p>Registros do período selecionado.</p></div><span class="live-badge"><i class="pulse-dot" aria-hidden="true"></i>Atualização ao vivo</span></div>
          <div class="table-toolbar"><label class="search-box"><span aria-hidden="true">⌕</span><input id="lead-search" type="text" autocomplete="off" aria-label="Buscar Lead ID"></label><label>Estado<select id="status-filter"><option value="">Todos</option></select></label><label>Completude<select id="completeness-filter"><option value="">Todas</option></select></label><span id="result-count" class="result-count">0 execuções</span></div>
          <div id="empty-state" class="empty-state" hidden>Nenhuma execução registrada neste período.</div>
          <div class="table-wrap"><table><caption>Execuções operacionais no período selecionado</caption><thead><tr><th scope="col">Lead</th><th scope="col">Execução</th><th scope="col">Recebida</th><th scope="col">Duração</th><th scope="col">Estado</th><th scope="col">Trace</th><th scope="col">Nó atual</th><th scope="col">Nós</th><th scope="col">Marcos</th><th scope="col">Motivo terminal</th><th scope="col"><span class="sr-only">Detalhe</span></th></tr></thead><tbody id="execution-table-body"></tbody></table></div>
          <div id="execution-mobile-list" class="execution-mobile-list"></div>
        </section>
      </section>
    </main>
    <div id="drawer-backdrop" class="drawer-backdrop" hidden></div>
    <aside class="execution-drawer" aria-hidden="true" id="execution-drawer" aria-labelledby="drawer-title">
      <header class="drawer-header"><div><p class="eyebrow">EXECUÇÃO</p><h2 id="drawer-title">Execução</h2><p id="drawer-lead">Não registrado</p></div><button id="drawer-close" type="button" aria-label="Fechar detalhes da execução">×</button></header>
      <div class="drawer-content"><div class="drawer-status"><span id="drawer-status" class="status-chip">Não registrado</span><span id="drawer-trace" class="status-chip">Não registrado</span></div><div id="drawer-summary" class="fact-grid"></div>
        <div class="execution-detail"><section class="canvas-shell"><div class="canvas-toolbar"><strong id="canvas-title">Execução</strong><div><button id="zoom-out" type="button" aria-label="Reduzir zoom">−</button><button id="zoom-in" type="button" aria-label="Aumentar zoom">+</button><button id="fit-canvas" type="button">Fit</button></div></div><div id="execution-canvas" class="execution-canvas" tabindex="0" role="region" aria-label="Canvas da execução"><svg id="edges" aria-hidden="true"></svg><div id="nodes" class="nodes"></div></div></section>
          <aside class="inspector"><p class="eyebrow">INSPETOR</p><h2 id="node-title">Nenhum nó selecionado</h2><section id="input-panel" class="io-panel"><div class="panel-head"><strong>Input</strong><button id="load-full-input" type="button" hidden>Carregar permitido</button></div><pre id="input-summary">{}</pre><pre id="input-full" hidden></pre></section><section id="output-panel" class="io-panel"><div class="panel-head"><strong>Output</strong><button id="load-full-output" type="button" hidden>Carregar permitido</button></div><pre id="output-summary">{}</pre><pre id="output-full" hidden></pre></section><details><summary>Metadados técnicos</summary><pre id="metadata">{}</pre></details><details><summary>Erro sanitizado</summary><pre id="node-error">null</pre></details></aside>
        </div>
      </div>
    </aside>
  </div>
  <script src="/ops/static/ops.js" defer></script>
</body>
```

Create the five analytics articles with IDs `execution-series`, `status-distribution`, `trace-distribution`, `milestones-chart`, and `top-node-types`. Preserve the current table columns and all Input/Output IDs. Do not add text matching any forbidden commercial claim.

- [ ] **Step 5: Run static contracts and close every allowlist mismatch**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py \
  -k 'shell_dom_contract or causal_mutants or accessibility_metadata or html_has_no_commercial_claims' -q
```

Expected: PASS. A new control or duplicate/inert ID must fail until explicitly represented in `EXPECTED_CONTROLS`/`EXPECTED_IDS`.

- [ ] **Step 6: Record scope and commit**

```bash
printf '%s\n' '- Mockup fidelity Task 1: semantic shell and closed drawer DOM contract.' >> docs/refactor/ACTIVE.md
git diff --check
git add v2_ops/static/index.html tests/test_v2_ops_ui.py docs/refactor/ACTIVE.md
test "$(git diff --cached --name-only | sort | tr '\n' ' ')" = "docs/refactor/ACTIVE.md tests/test_v2_ops_ui.py v2_ops/static/index.html "
git commit -m "feat(v2-ops): add mockup-fidelity dashboard shell"
```

Expected: one commit with exactly the three listed paths.

---

### Task 2: Executive Header, Safe Refresh and KPI Components

**Files:**
- Modify: `v2_ops/static/ops.js:1-126, loadDashboard/renderDashboard/listeners`
- Modify: `v2_ops/static/ops.css:1-160`
- Modify: `tests/test_v2_ops_ui.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: Task 1 shell IDs, unchanged dashboard payload and `execution_series`.
- Produces: `refreshDashboard()`, `setMobileNavigationOpen(open)`, operator popover behavior and polished `.kpi-card` children.

- [ ] **Step 1: Add failing JavaScript contract tests**

Add assertions:

```python
def test_mockup_fidelity_header_and_kpi_contract() -> None:
    html, js, css = assets()
    for token in (
        "brand-lockup", "readonly-chip", "welcome-row", "health-pill",
        "header-actions", "refresh-dashboard", "operator-popover",
    ):
        assert token in html
    for function_name in ("refreshDashboard", "setMobileNavigationOpen"):
        assert f"function {function_name}(" in js or f"async function {function_name}(" in js
    for token in ("kpi-top", "kpi-icon", "kpi-label", "kpi-value", "kpi-foot", "sparkline"):
        assert token in js
    assert 'setAttribute("aria-expanded"' in js
    assert "execution_series" in js
    assert "previous period" not in js.casefold()
    assert "período anterior" not in (html + js).casefold()
    for selector in (".brand-lockup", ".welcome-row", ".health-pill", ".kpi-icon", ".sparkline"):
        assert selector in css
```

- [ ] **Step 2: Verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py::test_mockup_fidelity_header_and_kpi_contract -q
```

Expected: FAIL because the current renderer creates only a label and value.

- [ ] **Step 3: Extend state and implement safe shell interactions**

Add only presentation state:

```javascript
const state = {
  // existing fields unchanged
  mobileNavigationOpen: false,
  operatorMenuOpen: false,
  dashboardLoading: false,
  drawerOpen: false,
  detailTrigger: null,
};

function setMobileNavigationOpen(open) {
  state.mobileNavigationOpen = Boolean(open);
  $("app-sidebar").classList.toggle("mobile-open", state.mobileNavigationOpen);
  $("sidebar-backdrop").hidden = !state.mobileNavigationOpen;
  $("mobile-menu").setAttribute("aria-expanded", String(state.mobileNavigationOpen));
}

async function refreshDashboard() {
  if (state.dashboardLoading) return;
  state.dashboardLoading = true;
  $("refresh-dashboard").disabled = true;
  try {
    await loadDashboard();
  } finally {
    state.dashboardLoading = false;
    $("refresh-dashboard").disabled = false;
  }
}
```

Operator popover behavior must toggle `hidden` and `aria-expanded`, close on Escape/outside click, and never add a second form or request. Preserve startup ordering explicitly:

```javascript
document.addEventListener("DOMContentLoaded", () => {
  $("mobile-menu").addEventListener("click", () => setMobileNavigationOpen(true));
  $("sidebar-close").addEventListener("click", () => setMobileNavigationOpen(false));
  $("sidebar-backdrop").addEventListener("click", () => setMobileNavigationOpen(false));
  $("refresh-dashboard").addEventListener("click", () => refreshDashboard().catch(handleDashboardError));
  loadDashboard().catch(handleDashboardError);
  connectLive();
});
```

If the current script initializes without a `DOMContentLoaded` wrapper because it is deferred, keep that style but retain the same listener bindings followed by exactly one initial `loadDashboard()` and one `connectLive()` call.

- [ ] **Step 4: Replace KPI rendering with safe component construction**

Define closed presentation metadata without adding metrics:

```javascript
const KPI_DEFINITIONS = [
  ["executions", "Execuções", "◎", "Eventos recebidos no período", "neutral"],
  ["distinct_leads", "Leads distintos", "◇", "IDs distintos no período", "neutral"],
  ["in_progress", "Em andamento", "◌", "Pending, running e stale", "active"],
  ["completed", "Concluídas", "✓", "Conclusão técnica", "success"],
  ["failed", "Falhas", "!", "Estado técnico failed", "danger"],
  ["manual_review", "Revisão manual", "↗", "Estado manual_review", "warning"],
  ["technical_completion_rate", "Conclusão técnica", "%", "Concluídas sobre execuções", "success"],
  ["average_terminal_duration_ms", "Duração média terminal", "◷", "Apenas execuções terminais", "neutral"],
];
```

`renderKpis()` must create `article.kpi-card`, `.kpi-top`, `.kpi-icon`, `.kpi-label`, `.kpi-value`, `.kpi-foot` and a local SVG `.sparkline`. The SVG may use only `state.snapshot.execution_series`; its adjacent note must say `Execuções no período`. If no points exist, omit the SVG and retain the factual note.

- [ ] **Step 5: Apply the canonical shell/KPI CSS**

Port the reference values rather than approximating them:

```css
:root {
  --brand-700:#245634; --brand-800:#173a27; --brand-600:#005a2a;
  --sand-100:#f4e8d7; --ivory-50:#fffcf7; --sand-300:#e4d5c0;
  --text-muted:#66766b; --coral-500:#e85f67; --success-500:#2f7d4a;
  --info-500:#327e8f; --warning-500:#c99135; --danger-600:#b7443e;
  --neutral-500:#a99c8a; --sidebar-width:248px; --radius-card:16px;
  --shadow-soft:0 12px 34px rgba(36,86,52,.08);
}
.sidebar { width:var(--sidebar-width); padding:26px 18px 20px; background:var(--brand-700); }
.main-content { margin-left:var(--sidebar-width); padding:0 34px 30px; background:#f8f2e9; }
.dashboard-header { min-height:84px; margin:0 -34px; padding:14px 34px; background:rgba(255,252,247,.82); backdrop-filter:blur(12px); }
.kpi-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:13px; }
.kpi-card { min-height:132px; padding:17px; border:1px solid var(--sand-300); border-radius:var(--radius-card); background:var(--ivory-50); }
.kpi-icon { width:34px; height:34px; display:grid; place-items:center; border-radius:10px; }
.sparkline { width:66px; height:23px; overflow:visible; }
```

- [ ] **Step 6: Run focused tests and deterministic Chromium**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py \
  -k 'header_and_kpi or javascript_dashboard_contract or closed_state_cards or adversarial_interleavings' -q
```

Expected: all selected tests PASS; the deterministic Chromium suite remains `12 passed` at this stage.

- [ ] **Step 7: Commit exact paths**

```bash
printf '%s\n' '- Mockup fidelity Task 2: executive shell interactions and factual KPI components.' >> docs/refactor/ACTIVE.md
git diff --check
git add v2_ops/static/ops.js v2_ops/static/ops.css tests/test_v2_ops_ui.py docs/refactor/ACTIVE.md
git commit -m "feat(v2-ops): render executive mockup-fidelity KPIs"
```

---

### Task 3: High-Density Real Analytics

**Files:**
- Modify: `v2_ops/static/index.html` analytics headings/meta containers
- Modify: `v2_ops/static/ops.js:renderExecutionSeries/renderDistribution/renderMilestones/renderTopNodeTypes`
- Modify: `v2_ops/static/ops.css` analytics component rules
- Modify: `tests/test_v2_ops_ui.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: current `execution_series`, `status_distribution`, `trace_distribution`, `milestones`, and `top_node_types` arrays.
- Produces: line/area SVG, status donut, trace bars/donut, milestone bars and node ranking; no payload changes.

- [ ] **Step 1: Add a failing closed analytics test**

```python
def test_mockup_fidelity_analytics_are_closed_to_real_payload() -> None:
    html, js, css = assets()
    for title in (
        "Execuções no período", "Estados atuais", "Completude do trace",
        "Marcos registrados", "Nós registrados",
    ):
        assert title in html
    for function_name in (
        "renderStatusDistribution", "renderTraceDistribution",
        "renderMilestones", "renderTopNodeTypes",
    ):
        assert f"function {function_name}(" in js
    for token in ("chart-area", "chart-grid", "chart-point", "donut", "chart-legend", "bar-chart"):
        assert token in js or f".{token}" in css
    combined = (html + js).casefold()
    for forbidden in ("funil", "receita", "conversão", "interesse", "motivo de handoff"):
        assert forbidden not in combined
```

- [ ] **Step 2: Verify RED**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py::test_mockup_fidelity_analytics_are_closed_to_real_payload -q
```

Expected: FAIL because the existing analytics renderer uses generic bars and a plain polyline.

- [ ] **Step 3: Implement the line/area chart using safe SVG APIs**

`renderExecutionSeries()` must construct:

```javascript
const svg = document.createElementNS(SVG_NAMESPACE, "svg");
svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
svg.setAttribute("role", "img");
svg.setAttribute("aria-label", "Execuções registradas ao longo do período");
const grid = document.createElementNS(SVG_NAMESPACE, "g");
grid.setAttribute("class", "chart-grid");
const area = document.createElementNS(SVG_NAMESPACE, "path");
area.setAttribute("class", "chart-area");
const line = document.createElementNS(SVG_NAMESPACE, "polyline");
line.setAttribute("class", "chart-line");
```

Add grid lines, baseline labels, area path, line and circles with `<title>` children. Use `replaceChildren`; do not interpolate payload into HTML strings.

- [ ] **Step 4: Implement a real status donut with textual legend**

Create `renderStatusDistribution()` that computes percentages from the closed status array, sets a CSS custom property/string only from normalized numeric values, and renders adjacent legend rows with status labels and counts. The donut center displays total executions. If total is zero, render the existing empty-state component rather than a fabricated ring.

Use a closed color map:

```javascript
const STATUS_PRESENTATION = Object.freeze({
  pending: ["Pendente", "var(--warning-500)"],
  running: ["Em execução", "var(--info-500)"],
  running_stale: ["Execução atrasada", "var(--danger-600)"],
  completed: ["Concluída", "var(--success-500)"],
  failed: ["Falha", "var(--danger-600)"],
  manual_review: ["Revisão manual", "var(--coral-500)"],
});
```

- [ ] **Step 5: Refine trace, milestones and node ranking**

Create dedicated renderers instead of routing all modules through one generic renderer. Each renderer must:

- use its exact payload field;
- normalize count with `Math.max(0, Number(value) || 0)`;
- display textual labels and counts;
- retain zero categories supplied by the API;
- use `emptyMessage()` only when the payload array itself is empty;
- never interpret summaries or errors.

- [ ] **Step 6: Port the analytics layout/styles from the reference**

Use:

```css
.analytics-grid { display:grid; grid-template-columns:1.18fr 1fr; gap:14px; margin-top:14px; }
.analytics-panel { min-height:285px; padding:19px; border:1px solid var(--sand-300); border-radius:var(--radius-card); background:var(--ivory-50); box-shadow:var(--shadow-soft); }
.analytics-panel.wide { grid-column:1 / -1; }
.panel-heading { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:16px; }
.volume-chart svg { width:100%; height:205px; overflow:visible; }
.chart-line { fill:none; stroke:var(--brand-600); stroke-width:3; }
.chart-area { fill:rgba(0,90,42,.10); }
.chart-point { fill:var(--ivory-50); stroke:var(--brand-600); stroke-width:2.5; }
```

Balance the five panels into two desktop rows without forcing equal empty heights on mobile.

- [ ] **Step 7: Extend deterministic browser fixtures with nonzero distributions**

Update `snapshot()` in `BROWSER_SPEC`:

```javascript
status_distribution: [
  {status:'completed', count:Math.max(0, executions - 1)},
  {status:'running', count:executions ? 1 : 0},
  {status:'failed', count:0},
],
trace_distribution: [
  {trace_completeness:'complete_trace', count:executions},
  {trace_completeness:'partial_trace', count:0},
  {trace_completeness:'ledger_only', count:0},
],
milestones: [
  {milestone:'reservation', count:1}, {milestone:'payment', count:1},
  {milestone:'public_delivery', count:0}, {milestone:'handoff', count:0},
],
top_node_types: [{node_type:'maya_request', count:executions}],
```

Add one Playwright test asserting the donut total and all five panel roots contain visible text derived from this fixture.

- [ ] **Step 8: Run and commit**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
venv/bin/python -m compileall -q v2_ops tests/test_v2_ops_ui.py
git diff --check
printf '%s\n' '- Mockup fidelity Task 3: high-density analytics over existing payload only.' >> docs/refactor/ACTIVE.md
git add v2_ops/static/index.html v2_ops/static/ops.js v2_ops/static/ops.css tests/test_v2_ops_ui.py docs/refactor/ACTIVE.md
git commit -m "feat(v2-ops): render polished real analytics"
```

Expected: full UI test file PASS and Chromium count increased by one.

---

### Task 4: Operational Table and Mobile Cards

**Files:**
- Modify: `v2_ops/static/index.html` operations section only
- Modify: `v2_ops/static/ops.js:filteredExecutions/renderExecutionTable`
- Modify: `v2_ops/static/ops.css` operations/table/mobile rules
- Modify: `tests/test_v2_ops_ui.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: current `ExecutionSummary` fields and exact filters.
- Produces: `.operations-head`, `.table-toolbar`, `#result-count`, factual status/trace/milestone badges, desktop rows and mobile cards that call `openExecution()`.

- [ ] **Step 1: Add failing table composition tests**

```python
def test_operations_surface_matches_mockup_without_new_fields() -> None:
    html, js, css = assets()
    for token in ("operations-head", "live-badge", "search-box", "result-count", "table-wrap"):
        assert token in html
    for token in ("lead-cell", "lead-avatar", "status-chip", "row-open", "service-mobile-top"):
        assert token in js
    assert '$("result-count").textContent' in js
    for field in (
        "lead_id", "execution_id", "received_at", "duration_ms", "status",
        "trace_completeness", "current_node_type", "node_count",
        "has_reservation", "has_payment", "has_public_delivery",
        "has_handoff", "terminal_reason",
    ):
        assert field in js
```

- [ ] **Step 2: Verify RED**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py::test_operations_surface_matches_mockup_without_new_fields -q
```

Expected: FAIL on missing mockup table component classes.

- [ ] **Step 3: Refactor row/card construction into factual helpers**

Add closed helpers:

```javascript
function executionStatusPresentation(status) {
  return Object.freeze({
    pending: ["Pendente", "pending"], running: ["Em execução", "active"],
    running_stale: ["Execução atrasada", "failed"], completed: ["Concluída", "completed"],
    failed: ["Falha", "failed"], manual_review: ["Revisão manual", "handoff"],
  })[status] ?? [displayValue(status), "neutral"];
}
function tracePresentation(value) {
  return Object.freeze({
    complete_trace: ["Trace completo", "completed"],
    partial_trace: ["Trace parcial", "pending"],
    ledger_only: ["Somente ledger", "neutral"],
  })[value] ?? [displayValue(value), "neutral"];
}
function milestoneLabels(execution) {
  return [
    execution.has_reservation && "Reserva",
    execution.has_payment && "Pagamento",
    execution.has_public_delivery && "Entrega",
    execution.has_handoff && "Handoff",
  ].filter(Boolean);
}
function makeExecutionOpenButton(execution, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "execution-link row-open";
  button.title = execution.execution_id;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", (event) => openExecution(execution.execution_id, event.currentTarget));
  return button;
}
```

Keep exact equality filters from the current contract. `leadFilter` remains an exact Lead ID match after trimming; do not silently change it to fuzzy/substring semantics.

- [ ] **Step 4: Render the desktop table in the mockup hierarchy**

Each Lead cell must contain a deterministic initials/avatar token derived only from the displayed Lead ID and the full Lead ID text. Each status and trace value uses a textual chip. Milestones render labels or `Nenhum registrado`; no icon may imply confirmation/payment settlement.

Set result copy with:

```javascript
const executions = filteredExecutions();
$("result-count").textContent = `${executions.length} ${executions.length === 1 ? "execução" : "execuções"}`;
```

- [ ] **Step 5: Render mobile cards from the same execution list**

Construct `.execution-mobile-card` with:

- lead ID and execution ID;
- status/trace chips;
- received timestamp;
- node count;
- milestone text;
- one detail button.

The desktop and mobile renderers must use the same `executions` array so filters cannot diverge.

- [ ] **Step 6: Port operations CSS from the reference**

```css
.operations-panel { margin-top:14px; border:1px solid var(--sand-300); border-radius:var(--radius-card); background:var(--ivory-50); box-shadow:var(--shadow-soft); overflow:hidden; }
.operations-head { display:flex; justify-content:space-between; align-items:center; gap:20px; padding:20px 21px 15px; }
.table-toolbar { display:flex; align-items:center; gap:9px; padding:11px 20px; border-block:1px solid #eee4d6; background:#fdf9f3; }
.search-box { flex:1; max-width:310px; display:flex; align-items:center; border:1px solid var(--sand-300); border-radius:9px; background:#fff; }
.table-wrap { overflow-x:auto; }
th { padding:11px 14px; font-size:8px; letter-spacing:.07em; text-transform:uppercase; }
td { padding:13px 14px; border-top:1px solid #f0e8dd; font-size:10px; }
.status-chip { display:inline-flex; gap:5px; padding:4px 7px; border-radius:20px; font-size:8px; font-weight:800; }
```

- [ ] **Step 7: Add real DOM interaction assertions**

Extend deterministic Playwright to assert:

```javascript
await expect(page.locator('#result-count')).toHaveText('1 execução');
await expect(page.locator('#execution-table-body .status-chip')).toContainText('Concluída');
await page.setViewportSize({width:390, height:844});
await expect(page.locator('.execution-mobile-card')).toHaveCount(1);
await expect(page.locator('.operations-panel table')).toBeHidden();
```

- [ ] **Step 8: Run and commit**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
git diff --check
printf '%s\n' '- Mockup fidelity Task 4: polished operations table and mobile cards.' >> docs/refactor/ACTIVE.md
git add v2_ops/static/index.html v2_ops/static/ops.js v2_ops/static/ops.css tests/test_v2_ops_ui.py docs/refactor/ACTIVE.md
git commit -m "feat(v2-ops): refine execution operations surface"
```

---

### Task 5: Execution Drawer with Existing Canvas and Race Guarantees

**Files:**
- Modify: `v2_ops/static/ops.js:detail state, clearDetail/openExecution/showOverview, listeners`
- Modify: `v2_ops/static/ops.css:drawer/canvas/inspector rules`
- Modify: `tests/test_v2_ops_ui.py:BROWSER_SPEC`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: Task 1 drawer DOM, Task 4 open buttons, current node/full endpoints and existing epochs.
- Produces: `setExecutionDrawerOpen(open)`, `closeExecutionDrawer(options)`, factual drawer summary and the existing canvas/inspector inside a context-preserving overlay.

- [ ] **Step 1: Update static and browser contracts to require drawer semantics**

Replace old view-switch assertions with:

```python
assert '$("execution-drawer").classList.toggle("open", state.drawerOpen)' in js
assert '$("execution-drawer").setAttribute("aria-hidden", String(!state.drawerOpen))' in js
assert '$("drawer-backdrop").hidden = !state.drawerOpen' in js
assert 'document.body.classList.toggle("drawer-open", state.drawerOpen)' in js
assert "closeExecutionDrawer" in js
assert "detailTrigger" in js
assert 'event.key === "Escape"' in js
for token in ("dashboardEpoch", "detailEpoch", "fullEpoch"):
    assert token in js
assert "state.fullEpoch.input" in js
assert "state.fullEpoch.output" in js
```

Add a deterministic Chromium test:

```javascript
test('execution opens as drawer and restores overview context and focus', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshotWithExecution('initial', 'A'));
  const trigger = page.locator('#execution-table-body .execution-link');
  await trigger.focus();
  await trigger.click();
  await resolveRequest(page, 1, 200, nodes('A'));
  await expect(page.locator('#execution-drawer')).toHaveAttribute('aria-hidden', 'false');
  await expect(page.locator('#overview-view')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#execution-drawer')).toHaveAttribute('aria-hidden', 'true');
  await expect(trigger).toBeFocused();
});
```

- [ ] **Step 2: Verify RED without weakening existing race tests**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
```

Expected: FAIL on old `execution-view` behavior and the new drawer test; the existing 12 adversarial scenarios must remain in the spec.

- [ ] **Step 3: Implement drawer open/close state**

```javascript
function clearDetail(executionId = "Execução") {
  state.selectedExecution = executionId === "Execução" ? null : executionId;
  state.nodes = [];
  state.selectedNode = null;
  $("canvas-title").textContent = executionId;
  $("nodes").replaceChildren();
  $("edges").replaceChildren();
  clearInspector();
}

function setExecutionDrawerOpen(open) {
  state.drawerOpen = Boolean(open);
  $("execution-drawer").classList.toggle("open", state.drawerOpen);
  $("execution-drawer").setAttribute("aria-hidden", String(!state.drawerOpen));
  $("drawer-backdrop").hidden = !state.drawerOpen;
  document.body.classList.toggle("drawer-open", state.drawerOpen);
}

function closeExecutionDrawer({restoreFocus = true} = {}) {
  state.detailEpoch += 1;
  state.fullEpoch.input += 1;
  state.fullEpoch.output += 1;
  setExecutionDrawerOpen(false);
  clearDetail();
  const trigger = state.detailTrigger;
  state.detailTrigger = null;
  if (restoreFocus && trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
}
```

`openExecution(executionId, trigger = document.activeElement)` stores the trigger, increments existing epochs, calls `clearDetail(executionId)` before its `await`, opens the drawer immediately, and commits nodes only if the existing epoch/execution guards still match. On a current successful response it must execute the existing rendering sequence exactly once:

```javascript
state.nodes = payload.nodes;
renderCanvas();
if (state.nodes.length) selectNode(state.nodes[0].node_id);
```

Keep the existing `loadFull(side)` function and bind `#load-full-input`/`#load-full-output` to `loadFull("input")` and `loadFull("output")`; drawer migration must not merge the two `fullEpoch` channels.

- [ ] **Step 4: Render factual drawer summary**

When opening from the current dashboard snapshot, locate the exact `execution_id` and populate only:

```javascript
$("drawer-title").textContent = shortExecutionId(execution.execution_id);
$("drawer-lead").textContent = displayValue(execution.lead_id);
$("drawer-summary").replaceChildren(
  fact("Recebida", formatTimestamp(execution.received_at)),
  fact("Duração", formatDuration(execution.duration_ms)),
  fact("Nó atual", displayValue(execution.current_node_type)),
  fact("Nós", displayValue(execution.node_count)),
  fact("Motivo terminal", displayValue(execution.terminal_reason)),
  fact("Marcos", milestoneLabels(execution).join(", ") || "Nenhum registrado"),
);
```

No response missing from the snapshot, use identifiers already supplied to `openExecution`; do not request or infer extra summary fields.

- [ ] **Step 5: Preserve and adapt every adversarial race test**

Update selectors/expectations only where the drawer changes visibility. Do not remove scenarios for:

- inverted dashboard ranges;
- SSE superseded by manual range;
- superseded dashboard errors;
- inverted A/B details;
- obsolete full after empty detail;
- 503 clearing previous detail;
- SSE detail refresh then 503;
- independent Input/Output completion orders;
- superseded detail/full errors;
- dashboard and live error separation.

Add two new scenarios:

1. close drawer while detail request is pending, then resolve it; drawer and inspector remain closed/clear;
2. close drawer while full request is pending, reopen another execution, then resolve obsolete full; no old content or alert appears.

The expected Chromium count becomes 15 or greater: the original 12, the drawer/focus test, and the two close-while-pending tests.

- [ ] **Step 6: Implement wide drawer and responsive canvas CSS**

```css
body.drawer-open { overflow:hidden; }
.drawer-backdrop { position:fixed; z-index:49; inset:0; background:rgba(23,58,39,.28); backdrop-filter:blur(2px); }
.execution-drawer { position:fixed; z-index:50; inset:0 0 0 auto; width:min(960px,92vw); display:flex; flex-direction:column; background:var(--ivory-50); box-shadow:-20px 0 55px rgba(23,58,39,.16); transform:translateX(105%); transition:transform .28s ease; }
.execution-drawer.open { transform:translateX(0); }
.drawer-content { min-height:0; padding:18px 22px 28px; overflow-y:auto; }
.execution-detail { display:grid; grid-template-columns:minmax(0,1fr) minmax(300px,360px); gap:16px; }
.execution-canvas { height:min(58vh,620px); min-height:420px; overflow:auto; }
```

At `max-width:720px`, set drawer width to `100vw`, stack `.execution-detail`, and cap canvas height so Input/Output remain reachable by vertical scrolling.

- [ ] **Step 7: Run the full UI gate and commit**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
venv/bin/python -m compileall -q v2_ops tests/test_v2_ops_ui.py
git diff --check
printf '%s\n' '- Mockup fidelity Task 5: context-preserving execution drawer with race guarantees.' >> docs/refactor/ACTIVE.md
git add v2_ops/static/ops.js v2_ops/static/ops.css tests/test_v2_ops_ui.py docs/refactor/ACTIVE.md
git commit -m "feat(v2-ops): move execution detail into safe drawer"
```

Expected: every static/UI/Chromium test PASS; no original race scenario deleted.

---

### Task 6: Responsive Visual Qualification and Real-Browser Evidence

**Files:**
- Modify: `v2_ops/static/ops.css`
- Modify: `tests/browser/ops_dashboard_smoke.py`
- Modify if browser evidence exposes a defect: `v2_ops/static/index.html`, `v2_ops/static/ops.js`, `tests/test_v2_ops_ui.py`
- Modify: `docs/refactor/ACTIVE.md`
- Generate ignored: `artifacts/ops-dashboard/desktop-1440x1000.png`
- Generate ignored: `artifacts/ops-dashboard/mobile-390x844.png`
- Generate ignored: `artifacts/ops-dashboard/drawer-desktop-1440x1000.png`
- Generate ignored: `artifacts/ops-dashboard/drawer-mobile-390x844.png`

**Interfaces:**
- Consumes: completed static UI, real app fixture and pinned Playwright capability.
- Produces: hard-failing real-browser smoke with four screenshots and visual/layout assertions.

- [ ] **Step 1: Extend screenshot declarations before UI fixes**

Set:

```python
screenshots = (
    ARTIFACTS / "desktop-1440x1000.png",
    ARTIFACTS / "mobile-390x844.png",
    ARTIFACTS / "drawer-desktop-1440x1000.png",
    ARTIFACTS / "drawer-mobile-390x844.png",
)
```

Delete every path before the run and verify each exists and has nonzero size afterward.

- [ ] **Step 2: Add browser assertions that causally fail against the pre-redesign layout**

After login at `1440 × 1000`, assert:

```javascript
if (!(await page.locator('#app-sidebar .brand-lockup').isVisible())) throw new Error('desktop brand lockup missing');
if (await page.locator('#kpi-grid .kpi-card').count() !== 8) throw new Error('expected exactly eight KPI cards');
if (await page.locator('#kpi-grid .kpi-icon').count() !== 8) throw new Error('expected eight KPI icons');
if (!(await page.locator('.welcome-row').isVisible())) throw new Error('welcome row missing');
if (await page.locator('.analytics-panel').count() !== 5) throw new Error('expected five analytics panels');
if (!(await page.locator('.operations-head').isVisible())) throw new Error('operations heading missing');
```

Measure computed layout:

```javascript
const desktopLayout = await page.evaluate(() => ({
  noOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth && document.body.scrollWidth <= document.body.clientWidth,
  sidebarWidth: Math.round(document.querySelector('#app-sidebar').getBoundingClientRect().width),
  kpiColumns: getComputedStyle(document.querySelector('#kpi-grid')).gridTemplateColumns.split(' ').length,
}));
if (!desktopLayout.noOverflow || desktopLayout.sidebarWidth < 230 || desktopLayout.kpiColumns !== 4) throw new Error(JSON.stringify(desktopLayout));
```

- [ ] **Step 3: Assert drawer behavior in the real app**

Open the completed execution and assert:

- overview remains visible;
- drawer `aria-hidden=false`;
- canvas, Input and Output are visible;
- full input/output load successfully;
- drawer width is between 700px and 92% of viewport on desktop;
- document has no horizontal overflow;
- Escape closes and restores focus.

Capture `drawer-desktop-1440x1000.png` before closing.

- [ ] **Step 4: Assert the mobile navigation, cards and full-screen drawer**

At `390 × 844`:

```javascript
await page.setViewportSize({width:390,height:844});
if (!(await page.locator('#mobile-menu').isVisible())) throw new Error('mobile menu missing');
if (await page.locator('.operations-panel table').isVisible()) throw new Error('desktop table visible on mobile');
if (!(await page.locator('#execution-mobile-list').isVisible())) throw new Error('mobile cards missing');
await page.click('#mobile-menu');
if (!(await page.locator('#app-sidebar').evaluate(el => el.classList.contains('mobile-open')))) throw new Error('mobile sidebar did not open');
await page.click('#sidebar-backdrop');
```

Capture the mobile overview, open a mobile execution, assert drawer width equals viewport within one pixel, canvas and inspector stack, then capture the mobile drawer.

- [ ] **Step 5: Run RED against the first Task 6 test edit**

```bash
venv/bin/python tests/browser/ops_dashboard_smoke.py
```

Expected before final CSS fixes: FAIL on at least one causal visual/layout assertion, never skip.

- [ ] **Step 6: Iterate CSS only on observed defects**

Fix concrete browser failures while preserving:

- `248px` desktop sidebar reference;
- 4-column KPI desktop and 2-column mobile;
- off-canvas mobile sidebar;
- five balanced analytics panels;
- table-to-card transition;
- internal canvas scrolling;
- document-level no-overflow;
- reduced motion.

Do not change payload or add visual filler to satisfy screenshot density.

- [ ] **Step 7: Run complete visual/browser gates**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
venv/bin/python tests/browser/ops_dashboard_smoke.py
venv/bin/python -m compileall -q v2_ops tests/test_v2_ops_ui.py tests/browser/ops_dashboard_smoke.py
git diff --check
```

Expected:

```text
all tests passed
ops_dashboard_smoke=PASS
```

All four screenshot files must exist and be nonempty.

- [ ] **Step 8: Inspect screenshots against the canonical reference**

Inspect these side by side:

- `/home/ubuntu/maya-dashboard-mockup/artifacts/dashboard-desktop.png`;
- `artifacts/ops-dashboard/desktop-1440x1000.png`;
- `artifacts/ops-dashboard/mobile-390x844.png`;
- both drawer captures.

Block the task for any concrete defect in sidebar/header proportions, KPI hierarchy, panel density, table finish, clipping, overlap, unreadable type, drawer sizing or mobile overflow. Differences caused by omitted commercial modules are expected and must be documented.

- [ ] **Step 9: Commit only versioned implementation/test paths**

```bash
printf '%s\n' '- Mockup fidelity Task 6: real-browser desktop/mobile/drawer qualification PASS.' >> docs/refactor/ACTIVE.md
git diff --check
git add v2_ops/static/ops.css tests/browser/ops_dashboard_smoke.py docs/refactor/ACTIVE.md
# Add index.html, ops.js or test_v2_ops_ui.py only if this task made a browser-proven fix there.
git status --short
git commit -m "test(v2-ops): qualify mockup-fidelity dashboard"
```

Confirm screenshots and browser caches remain ignored and uncommitted.

---

### Task 7: Full Regression, Immutable Audit and Handoff Without Deploy

**Files:**
- Create: `docs/superpowers/reports/2026-08-22-maya-ops-mockup-fidelity-redesign-verification.md`
- Modify: `docs/refactor/ACTIVE.md`
- Do not modify implementation or tests during the final evidence commit.

**Interfaces:**
- Consumes: all Task 1–6 commits and generated browser evidence.
- Produces: reproducible final report, exact pathset, DB invariance proof and `IMPLEMENTATION VERIFIED — NOT DEPLOYED` decision.

- [ ] **Step 1: Capture immutable candidate identity and allowed pathset**

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse HEAD^{tree}
git diff --name-only 37d82b1290661382f18dda713a1db20732cd99f8..HEAD | sort
```

Allowed implementation pathset:

```text
docs/refactor/ACTIVE.md
tests/browser/ops_dashboard_smoke.py
tests/test_v2_ops_ui.py
v2_ops/static/index.html
v2_ops/static/ops.css
v2_ops/static/ops.js
```

The final report file may be added after this audit. Any backend, agent, runtime, instrument, provider, deploy or V3 path blocks release.

- [ ] **Step 2: Run the full clean-environment suite**

Use the project’s validated audit command with explicit config path:

```bash
env -i \
  PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  HERMES_LEADS_AGENT_CONFIG_PATH="$PWD/config/agent.yaml" \
  venv/bin/python -m pytest -q
```

Expected: all tests PASS with zero skips accepted as browser evidence. Record exact count, duration and warnings.

- [ ] **Step 3: Run focused no-effect and browser gates**

```bash
venv/bin/python -m pytest \
  tests/test_v2_ops_no_effect_surface.py \
  tests/test_v2_ops_deploy_contract.py -q
venv/bin/python tests/browser/ops_dashboard_smoke.py
```

Expected: both commands PASS and smoke prints all screenshot paths.

- [ ] **Step 4: Prove route surface remains read-only**

Run an introspection script against `create_ops_app` and record:

```text
POST /ops/login
POST /ops/logout
```

Assert there are no PUT/PATCH/DELETE routes and no other POST route.

- [ ] **Step 5: Prove DB/WAL/SHM invariance across all new GETs**

Create a temporary fixture using `SQLiteOpsTraceWriter`, close the writer, record individual SHA-256/size/mtime/existence for DB, WAL and SHM, then perform authenticated GETs for:

- dashboard `24h`, `7d`, `30d`;
- matching ETag revalidations (`304`);
- one execution;
- its node list;
- one permitted full Input and Output;
- events connection startup if the harness supports bounded SSE.

Record the same file metadata after all reads and assert byte-for-byte equality for every existing file and no newly created WAL/SHM.

- [ ] **Step 6: Verify visual evidence and forbidden content**

```bash
sha256sum artifacts/ops-dashboard/desktop-1440x1000.png \
  artifacts/ops-dashboard/mobile-390x844.png \
  artifacts/ops-dashboard/drawer-desktop-1440x1000.png \
  artifacts/ops-dashboard/drawer-mobile-390x844.png
git check-ignore -v artifacts/ops-dashboard/*.png
python - <<'PY'
from pathlib import Path
text = ''.join((Path('v2_ops/static') / name).read_text() for name in ('index.html','ops.js'))
for forbidden in ('Dados demonstrativos','Receita potencial','Taxa de conversão','Novos leads','Qualificados'):
    assert forbidden.casefold() not in text.casefold(), forbidden
print('forbidden_content=PASS')
PY
```

- [ ] **Step 7: Write the final verification report**

The report must contain:

- base/spec commit and final candidate SHA/tree;
- exact six-path implementation diff plus report/ACTIVE documentation paths;
- full test outputs;
- deterministic Chromium count;
- hard-failing real-browser smoke result;
- four screenshot hashes and viewport labels;
- visual comparison verdict and intentional data-driven differences;
- route matrix;
- DB/WAL/SHM invariance evidence;
- forbidden-content/no-demo evidence;
- statement that backend/API, agent, instrumentation and effects did not change;
- rollback release currently deployed;
- explicit decision `IMPLEMENTATION VERIFIED — NOT DEPLOYED`.

- [ ] **Step 8: Commit documentation only**

```bash
git diff --check
git add docs/refactor/ACTIVE.md \
  docs/superpowers/reports/2026-08-22-maya-ops-mockup-fidelity-redesign-verification.md
paths=$(git diff --cached --name-only | sort)
test "$paths" = "docs/refactor/ACTIVE.md
docs/superpowers/reports/2026-08-22-maya-ops-mockup-fidelity-redesign-verification.md"
git commit -m "docs(v2-ops): verify mockup-fidelity redesign"
```

- [ ] **Step 9: Run final post-commit verification and independent review**

```bash
git status --short --branch
git diff --check 37d82b1290661382f18dda713a1db20732cd99f8..HEAD
venv/bin/python -m pytest -q
venv/bin/python tests/browser/ops_dashboard_smoke.py
```

Dispatch an independent reviewer over the full diff from `37d82b1...` to final HEAD. Require separate verdicts for specification compliance and quality/correctness. Do not declare completion until all Critical, Important and Minor findings are either fixed and re-reviewed or explicitly rejected with evidence.

- [ ] **Step 10: Stop before promotion**

Do not push, build, deploy, restart or access the production dashboard as part of this task. Present the candidate SHA, evidence and screenshots to the user, then request separate publication authorization.

---

## Final Acceptance Matrix

| Requirement | Implemented/Tested in |
|---|---|
| Mockup architecture rather than palette-only styling | Tasks 1, 2, 6 |
| Only real navigation destinations | Task 1 |
| Eight factual KPIs with icons/context | Task 2 |
| No previous-period trend invention | Tasks 2, 7 |
| Five real analytic modules | Task 3 |
| Refined operations table and mobile cards | Task 4 |
| Context-preserving execution drawer | Task 5 |
| Canvas, Input, Output and full-content policy | Tasks 5, 6 |
| Existing async race guarantees | Tasks 2, 5, 7 |
| Desktop/mobile no-overflow | Task 6 |
| Safe DOM and closed controls | Tasks 1–5 |
| No commercial/demo content | Tasks 1–4, 7 |
| Read-only DB/API invariance | Task 7 |
| Chromium real, no skip as evidence | Tasks 5–7 |
| No agent/runtime/provider/V3 changes | Task 7 |
| No deployment without separate authorization | Task 7 |
