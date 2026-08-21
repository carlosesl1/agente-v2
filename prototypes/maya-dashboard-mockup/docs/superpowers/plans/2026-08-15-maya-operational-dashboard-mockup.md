# Maya Operational Dashboard Mockup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished, dependency-free, interactive dashboard mockup that combines Maya executive indicators with detailed operational visibility using only deterministic synthetic data.

**Architecture:** A standalone static artifact uses semantic HTML for layout, one focused CSS file for the branded responsive visual system, and one JavaScript file for deterministic mock data, filtering, navigation, charts, and the service-detail drawer. A Python `unittest` suite validates the artifact contract without adding a frontend build system; browser verification exercises the rendered result and interactions.

**Tech Stack:** HTML5, CSS3, vanilla JavaScript, inline SVG, Python 3 standard-library `unittest`, local static HTTP server, browser screenshot/inspection.

## Global Constraints

- Work only in `/home/ubuntu/maya-dashboard-mockup`.
- Do not modify `/home/ubuntu/agente-v2`, the Maya V2 runtime, the existing `/ops` dashboard, deployment files, containers, or production state.
- Use no database, production API, authentication, external business-data request, or effect action.
- Use only deterministic synthetic data and visibly label it `Dados demonstrativos`.
- Use no real customer names, identifiers, phone numbers, e-mails, conversations, reservation references, or payment links.
- Keep the artifact dependency-free: no npm project, frontend framework, remote chart library, or remote font requirement.
- Use the approved palette: `#245634`, `#173A27`, `#005A2A`, `#F4E8D7`, `#FFFCF7`, `#E4D5C0`, `#66766B`, `#E85F67`, `#2F7D4A`, `#327E8F`, `#C99135`, `#B7443E`, and `#A99C8A`.
- The dashboard remains observation-only; no button may imply reservation, payment, handoff, retry, send, or state mutation.
- Desktop is the primary approval surface, with non-breaking tablet and mobile layouts.
- Interactive controls require visible keyboard focus, meaningful accessible labels, Escape support for the drawer, and status text in addition to color.
- Every task ends with a focused test and a Git commit.

---

## File Structure

- `index.html` — semantic application shell, navigation, KPI/chart/table containers, drawer, and accessible labels.
- `styles.css` — approved tokens, component styling, responsive layouts, focus states, and print-safe/screenshot-safe presentation.
- `app.js` — immutable synthetic dataset, derived KPI/range calculations, local chart rendering, filters, navigation preview, refresh simulation, and drawer behavior.
- `tests/test_mockup_contract.py` — standard-library static contract tests for required content, palette, synthetic-data labeling, interactions, and forbidden production/effect surfaces.
- `README.md` — preview, verification, scope, and artifact limitations.
- `artifacts/dashboard-desktop.png` — final synthetic desktop screenshot produced during browser verification.
- `artifacts/dashboard-mobile.png` — final synthetic mobile screenshot produced during browser verification.

### Browser-facing interfaces

`app.js` owns these stable functions so tests and later maintainers can reason about behavior:

```javascript
getRangeModel(rangeKey) -> { kpis, volume, funnel }
filterServices({ query, status, interest }) -> Service[]
renderDashboard() -> void
renderKpis(rangeModel) -> void
renderCharts(rangeModel) -> void
renderServices(services) -> void
openServiceDrawer(serviceId) -> void
closeServiceDrawer() -> void
setActiveNavigation(section) -> void
simulateRefresh() -> void
```

Each synthetic `Service` has this closed shape:

```javascript
{
  id: "demo-001",
  leadLabel: "Lead demonstrativo 01",
  interest: "Passeio" | "Hospedagem" | "Pacote",
  stage: string,
  lastActivity: string,
  mayaStatus: "Ativo" | "Aguardando" | "Concluído" | "Falha",
  reservationStatus: "Não iniciada" | "Preparada" | "Confirmada" | "Falha",
  paymentStatus: "Não iniciado" | "Pendente" | "Pago" | "Falha",
  handoffStatus: "Não necessário" | "Triagem em andamento" | "Solicitado" | "Concluído",
  attention: "Normal" | "Atenção" | "Crítico",
  language: "PT" | "EN",
  summary: string,
  nextStep: string,
  reason: string | null,
  conversation: [{ speaker: "Lead" | "Maya", text: string }],
  journey: [{ label: string, status: "completed" | "active" | "pending" | "handoff" | "failed" | "skipped" }]
}
```

---

### Task 1: Branded Dashboard Shell and Static Contract

**Files:**
- Create: `index.html`
- Create: `styles.css`
- Create: `tests/test_mockup_contract.py`

**Interfaces:**
- Consumes: approved visual specification and palette.
- Produces: all stable DOM IDs and CSS tokens consumed by `app.js` in Task 2.

- [ ] **Step 1: Write the failing static contract test**

Create `tests/test_mockup_contract.py` with the initial contract:

```python
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MockupContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "styles.css").read_text(encoding="utf-8")

    def test_semantic_dashboard_regions_exist(self):
        for token in (
            'id="app-sidebar"',
            'id="dashboard-header"',
            'id="kpi-grid"',
            'id="commercial-funnel"',
            'id="service-volume-chart"',
            'id="interest-mix-chart"',
            'id="handoff-reasons-chart"',
            'id="services-table-body"',
            'id="service-drawer"',
        ):
            self.assertIn(token, self.html)

    def test_demo_label_and_read_only_copy_are_visible(self):
        self.assertIn("Dados demonstrativos", self.html)
        self.assertIn("Painel visual sem conexão com dados reais", self.html)

    def test_approved_palette_is_declared(self):
        for color in (
            "#245634", "#173A27", "#005A2A", "#F4E8D7",
            "#FFFCF7", "#E4D5C0", "#66766B", "#E85F67",
            "#2F7D4A", "#327E8F", "#C99135", "#B7443E",
            "#A99C8A",
        ):
            self.assertIn(color.lower(), self.css.lower())

    def test_accessible_controls_exist(self):
        self.assertRegex(self.html, r'<button[^>]+aria-label="Fechar detalhes do atendimento"')
        self.assertIn('aria-label="Buscar atendimento demonstrativo"', self.html)
        self.assertIn('aria-live="polite"', self.html)

    def test_responsive_breakpoints_exist(self):
        self.assertGreaterEqual(len(re.findall(r"@media", self.css)), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the causal RED**

Run:

```bash
python -m unittest -v tests/test_mockup_contract.py
```

Expected: `ERROR` because `index.html` and `styles.css` do not exist.

- [ ] **Step 3: Create the semantic HTML shell**

Create `index.html` with:

- `<aside id="app-sidebar">` containing the eight approved navigation entries as buttons with `data-section` values;
- `<header id="dashboard-header">` containing title, `Dados demonstrativos` badge, range selector, search, Maya online status, refresh control, and operator avatar;
- `<section id="kpi-grid" aria-live="polite">`;
- analytics cards containing `commercial-funnel`, `service-volume-chart`, `interest-mix-chart`, and `handoff-reasons-chart`;
- filters `status-filter` and `interest-filter`;
- desktop table with `services-table-body`;
- mobile list container `services-mobile-list`;
- `<aside id="service-drawer" aria-hidden="true">` with summary, facts, conversation, and `journey-list` containers;
- `<script src="app.js" defer></script>`;
- no inline production URLs, mutation forms, or external script/style references.

Use this exact document-level skeleton:

```html
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Mockup visual demonstrativo do dashboard operacional da Maya">
  <title>Maya · Dashboard operacional</title>
  <link rel="stylesheet" href="styles.css">
  <script src="app.js" defer></script>
</head>
<body>
  <div class="app-shell">
    <aside id="app-sidebar" class="sidebar" aria-label="Navegação principal"></aside>
    <main class="main-content">
      <header id="dashboard-header" class="dashboard-header"></header>
      <section id="kpi-grid" class="kpi-grid" aria-label="Resumo executivo" aria-live="polite"></section>
      <section class="analytics-grid" aria-label="Indicadores analíticos"></section>
      <section class="operations-panel" aria-labelledby="operations-title"></section>
    </main>
    <div id="drawer-backdrop" class="drawer-backdrop" hidden></div>
    <aside id="service-drawer" class="service-drawer" aria-hidden="true" aria-labelledby="drawer-title"></aside>
  </div>
</body>
</html>
```

- [ ] **Step 4: Implement the visual system in CSS**

Create `styles.css` with:

- all approved colors as `:root` custom properties;
- warm light background and ivory cards;
- forest-green sidebar and active coral marker;
- responsive KPI grid (`repeat(4, minmax(0, 1fr))` desktop, two columns tablet, one/two columns mobile);
- two-column analytics layout desktop and stacked mobile layout;
- status-chip classes for success, active, pending, handoff, failed, and neutral;
- table hover/focus/selected states;
- fixed right drawer and backdrop;
- vertical journey timeline;
- `:focus-visible` rules with a high-contrast outline;
- `@media (max-width: 1100px)` and `@media (max-width: 720px)` rules;
- `prefers-reduced-motion` handling;
- mobile table-to-card transition.

Define the tokens exactly:

```css
:root {
  --brand-700: #245634;
  --brand-800: #173A27;
  --brand-600: #005A2A;
  --sand-100: #F4E8D7;
  --ivory-50: #FFFCF7;
  --sand-300: #E4D5C0;
  --text-muted: #66766B;
  --coral-500: #E85F67;
  --success-500: #2F7D4A;
  --info-500: #327E8F;
  --warning-500: #C99135;
  --danger-600: #B7443E;
  --neutral-500: #A99C8A;
  --shadow-soft: 0 12px 34px rgba(36, 86, 52, 0.08);
  --radius-card: 16px;
}
```

- [ ] **Step 5: Run the focused contract**

Run:

```bash
python -m unittest -v tests/test_mockup_contract.py
```

Expected: all initial tests `OK`.

- [ ] **Step 6: Validate markup basics and commit**

Run:

```bash
python - <<'PY'
from html.parser import HTMLParser
from pathlib import Path
class Parser(HTMLParser):
    pass
Parser().feed(Path("index.html").read_text(encoding="utf-8"))
print("html_parse=PASS")
PY
git diff --check
git add index.html styles.css tests/test_mockup_contract.py
git commit -m "feat: add branded Maya dashboard shell"
```

Expected: `html_parse=PASS`, clean diff check, commit succeeds.

---

### Task 2: Synthetic View Model, Charts, Filters, and Detail Drawer

**Files:**
- Create: `app.js`
- Modify: `tests/test_mockup_contract.py`

**Interfaces:**
- Consumes: stable DOM IDs and classes from Task 1.
- Produces: `getRangeModel`, `filterServices`, `renderDashboard`, `renderKpis`, `renderCharts`, `renderServices`, `openServiceDrawer`, `closeServiceDrawer`, `setActiveNavigation`, and `simulateRefresh`.

- [ ] **Step 1: Extend the contract tests before implementation**

Add these tests to `MockupContractTests` and load `app.js` in `setUpClass`:

```python
cls.js = (ROOT / "app.js").read_text(encoding="utf-8")


def test_synthetic_dataset_and_interaction_functions_exist(self):
    self.assertIn('const SERVICES = Object.freeze([', self.js)
    self.assertGreaterEqual(self.js.count('id: "demo-'), 12)
    for function_name in (
        "getRangeModel", "filterServices", "renderDashboard",
        "renderKpis", "renderCharts", "renderServices",
        "openServiceDrawer", "closeServiceDrawer",
        "setActiveNavigation", "simulateRefresh",
    ):
        self.assertRegex(self.js, rf"function\s+{function_name}\s*\(")


def test_dataset_covers_required_operational_states(self):
    for token in (
        'status: "completed"',
        'status: "active"',
        'status: "handoff"',
        'status: "failed"',
        'paymentStatus: "Pendente"',
        'reservationStatus: "Confirmada"',
    ):
        self.assertIn(token, self.js)


def test_mockup_has_no_business_network_or_effect_calls(self):
    lowered = self.js.lower()
    for forbidden in (
        "fetch(", "xmlhttprequest", "websocket", "eventsource",
        "payment_link", "request_handoff", "create_reservation",
        "send_message", "retry_execution",
    ):
        self.assertNotIn(forbidden, lowered)


def test_drawer_supports_escape_and_background_close(self):
    self.assertIn('event.key === "Escape"', self.js)
    self.assertIn('drawer-backdrop', self.js)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python -m unittest -v tests/test_mockup_contract.py
```

Expected: `ERROR` because `app.js` does not exist.

- [ ] **Step 3: Define deterministic synthetic data**

Create `app.js` and define:

```javascript
"use strict";

const JOURNEY_LABELS = Object.freeze([
  "Mensagem recebida",
  "Interpretação da Maya",
  "Consulta",
  "Opção apresentada",
  "Coleta de dados",
  "Confirmação",
  "Reserva",
  "Pagamento",
  "Entrega ou handoff",
]);

const SERVICES = Object.freeze([
  // At least 12 complete Service objects using ids demo-001 ... demo-012.
]);
```

The twelve records must include at least:

1. active tour qualification;
2. active lodging consultation;
3. package with option selected;
4. awaiting confirmation;
5. confirmed reservation with pending card payment;
6. confirmed and paid reservation;
7. full-triage handoff for discount;
8. immediate handoff for explicit human request;
9. provider consultation failure;
10. payment-link operational failure;
11. English-language lodging lead;
12. completed no-handoff journey.

Every label begins with `Lead demonstrativo`; every conversation is fictional and short.

- [ ] **Step 4: Implement range models and KPI rendering**

Define immutable range configurations for `Hoje`, `7 dias`, and `30 dias`. `getRangeModel(rangeKey)` must return a cloned model containing:

- all eight KPI values and trends;
- five funnel stages;
- seven service-volume points;
- interest percentages;
- handoff-reason percentages.

`renderKpis(rangeModel)` must construct cards with DOM APIs, not `innerHTML` from external content. Each card includes label, value, trend label, sparkline SVG, and a semantic class.

- [ ] **Step 5: Implement dependency-free charts**

Implement:

- `renderFunnel()` as proportional horizontal stages;
- `renderVolumeChart()` as an inline SVG polyline/area chart;
- `renderInterestMix()` as an accessible CSS conic-gradient donut plus textual legend;
- `renderHandoffReasons()` as labeled horizontal bars.

Each chart must expose textual labels and values adjacent to the graphic so color is not the only signal.

- [ ] **Step 6: Implement service filtering and rendering**

`filterServices({ query, status, interest })` must:

- normalize query with `trim().toLocaleLowerCase("pt-BR")`;
- match only synthetic display fields;
- return all records when filters are blank;
- not mutate `SERVICES`.

`renderServices(services)` must render both:

- desktop table rows in `services-table-body`;
- mobile cards in `services-mobile-list`.

Each row/card has a button opening `openServiceDrawer(service.id)`. If no result remains, render a clear empty state in both views.

- [ ] **Step 7: Implement detail drawer behavior**

`openServiceDrawer(serviceId)` must:

- select the exact synthetic record;
- populate title, status, summary, next step, reason, facts, fictional conversation, and journey;
- map journey status to icon, class, and visible status label;
- set `aria-hidden="false"`;
- reveal backdrop;
- move focus to the close button;
- remember the triggering element for focus restoration.

`closeServiceDrawer()` must reverse those states and restore focus. Add:

```javascript
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeServiceDrawer();
});
```

The backdrop click closes the drawer.

- [ ] **Step 8: Implement simulated navigation, range, search, and refresh**

- `setActiveNavigation(section)` changes the active sidebar item and updates a small preview banner for non-overview sections; it never navigates to a production URL.
- Range changes call `getRangeModel()` and rerender KPIs/charts.
- Search and filters call `filterServices()` and `renderServices()`.
- `simulateRefresh()` changes the update label to `Atualizando…`, disables the button for 450ms, then displays the current local time and rerenders the current synthetic view.
- Initialization runs once on `DOMContentLoaded` through `renderDashboard()`.

- [ ] **Step 9: Run JavaScript and static checks**

Run:

```bash
node --check app.js
python -m unittest -v tests/test_mockup_contract.py
git diff --check
```

Expected: Node syntax check exits `0`; all Python contract tests pass; diff check is clean.

- [ ] **Step 10: Commit the interactive mockup**

Run:

```bash
git add app.js tests/test_mockup_contract.py index.html styles.css
git commit -m "feat: add interactive synthetic Maya operations view"
```

Expected: commit succeeds and working tree is clean.

---

### Task 3: Browser Qualification, Responsive Evidence, and Documentation

**Files:**
- Create: `README.md`
- Create: `artifacts/dashboard-desktop.png`
- Create: `artifacts/dashboard-mobile.png`
- Modify: `tests/test_mockup_contract.py`
- Modify if visual defects are found: `index.html`, `styles.css`, `app.js`

**Interfaces:**
- Consumes: complete static artifact from Tasks 1–2.
- Produces: verified standalone preview, screenshots, usage instructions, and final acceptance evidence.

- [ ] **Step 1: Add final scope/documentation tests**

Add:

```python
def test_readme_documents_preview_and_scope(self):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    self.assertIn("python -m http.server 8088", readme)
    self.assertIn("Dados demonstrativos", readme)
    self.assertIn("não acessa banco", readme.lower())
    self.assertIn("não executa reservas", readme.lower())


def test_no_remote_assets_or_production_endpoints(self):
    combined = (self.html + self.css + self.js).lower()
    for forbidden in (
        "https://", "http://", "leads-hermes.chapadabackpackers.com",
        "hermes.chapadabackpackers.com/ops", "/ops/api/",
    ):
        self.assertNotIn(forbidden, combined)
```

- [ ] **Step 2: Run the focused RED**

Run:

```bash
python -m unittest -v tests/test_mockup_contract.py
```

Expected: failure because `README.md` is absent.

- [ ] **Step 3: Write README**

Create `README.md` with exactly these sections:

- `# Maya Operational Dashboard Mockup`
- `## Preview local`
- `## Verificação`
- `## Escopo e segurança`
- `## Dados demonstrativos`
- `## Próxima fase após aprovação`

Document:

```bash
cd /home/ubuntu/maya-dashboard-mockup
python -m http.server 8088
# open http://127.0.0.1:8088/
```

State explicitly that the artifact:

- uses deterministic synthetic data;
- does not access a database;
- does not call a production API;
- does not execute reservations, payments, handoffs, messages, retries, or any other effect;
- is not the existing production `/ops` implementation;
- requires a separate approved integration specification before real data is linked.

- [ ] **Step 4: Start a tracked local preview server**

Run:

```bash
python -m http.server 8088 --bind 127.0.0.1
```

Start it through the tracked background-process mechanism with completion notification disabled because it is a long-lived preview server. Verify readiness with:

```bash
curl -fsS http://127.0.0.1:8088/ >/dev/null
printf 'preview_ready=PASS\n'
```

Expected: `preview_ready=PASS`.

- [ ] **Step 5: Inspect desktop rendering in a browser**

At viewport `1440 × 1050`, verify:

- sidebar and brand hierarchy;
- eight KPI cards visible without overlap;
- funnel and charts readable;
- operational table aligned;
- demo badge visible;
- no horizontal page overflow;
- drawer opens from a selected service;
- completed, active, handoff, and failed journey states are visible;
- search/filter changes the record list;
- refresh timestamp changes;
- Escape closes the drawer.

Save a screenshot to `artifacts/dashboard-desktop.png` using only synthetic data.

- [ ] **Step 6: Inspect mobile rendering in a browser**

At viewport `390 × 844`, verify:

- compact navigation does not cover content;
- KPI cards remain readable;
- charts stack without clipping;
- desktop table is replaced by mobile cards;
- drawer uses the viewport width;
- journey is vertical;
- all controls remain keyboard/touch reachable;
- no horizontal overflow.

Save a screenshot to `artifacts/dashboard-mobile.png`.

- [ ] **Step 7: Fix only observed visual defects and rerun checks**

If browser inspection finds a defect, make the smallest correction in `index.html`, `styles.css`, or `app.js`. Then run:

```bash
node --check app.js
python -m unittest -v tests/test_mockup_contract.py
git diff --check
```

Expected: all checks pass. Repeat the affected viewport inspection and replace only the stale screenshot.

- [ ] **Step 8: Run final artifact verification**

Run:

```bash
set -euo pipefail
node --check app.js
python -m unittest -v tests/test_mockup_contract.py
python - <<'PY'
from pathlib import Path
required = [
    "index.html", "styles.css", "app.js", "README.md",
    "artifacts/dashboard-desktop.png", "artifacts/dashboard-mobile.png",
]
for name in required:
    path = Path(name)
    assert path.is_file() and path.stat().st_size > 0, name
print("artifact_bundle=PASS")
PY
git diff --check
```

Expected: JavaScript valid, all contract tests pass, `artifact_bundle=PASS`, and clean diff check.

- [ ] **Step 9: Commit final evidence and documentation**

Run:

```bash
git add README.md artifacts/ tests/test_mockup_contract.py index.html styles.css app.js
git commit -m "docs: qualify Maya dashboard visual mockup"
git status --short --branch
```

Expected: commit succeeds; working tree is clean.

- [ ] **Step 10: Deliver the approval artifact**

Report:

- absolute project path;
- preview URL while the local server is running;
- screenshot paths;
- tests and browser checks performed;
- explicit confirmation that no real data or effect capability was used;
- the visual decisions that need user approval before a real-data integration specification.

Do not claim that real data is connected or that production `/ops` was changed.
