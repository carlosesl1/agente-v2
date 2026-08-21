from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Callable

import pytest

ROOT = Path(__file__).resolve().parents[1] / "v2_ops" / "static"
VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
EXPECTED_IDS = {
    "app-sidebar",
    "back-to-overview",
    "canvas-title",
    "completeness-filter",
    "dashboard-alert",
    "edges",
    "empty-state",
    "execution-canvas",
    "execution-mobile-list",
    "execution-series",
    "execution-table-body",
    "execution-view",
    "fit-canvas",
    "generated-at",
    "input-full",
    "input-panel",
    "input-summary",
    "kpi-grid",
    "lead-search",
    "live-state",
    "load-full-input",
    "load-full-output",
    "metadata",
    "milestones-chart",
    "nav-execution",
    "nav-overview",
    "node-error",
    "node-title",
    "nodes",
    "output-full",
    "output-panel",
    "output-summary",
    "overview-view",
    "range-select",
    "status-distribution",
    "status-filter",
    "top-node-types",
    "trace-distribution",
    "zoom-in",
    "zoom-out",
}
EXPECTED_CONTROLS = (
    ("button", None, "submit"),
    ("button", "back-to-overview", "button"),
    ("button", "fit-canvas", "button"),
    ("button", "load-full-input", "button"),
    ("button", "load-full-output", "button"),
    ("button", "nav-execution", "button"),
    ("button", "nav-overview", "button"),
    ("button", "zoom-in", "button"),
    ("button", "zoom-out", "button"),
    ("input", None, "hidden"),
    ("input", "lead-search", "text"),
    ("select", "completeness-filter", None),
    ("select", "range-select", None),
    ("select", "status-filter", None),
)


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    parent: Element | None = None
    children: list[Element] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def descendants(self, tag: str | None = None) -> list[Element]:
        found: list[Element] = []
        for child in self.children:
            if tag is None or child.tag == tag:
                found.append(child)
            found.extend(child.descendants(tag))
        return found

    def text(self) -> str:
        return "".join(self.text_parts) + "".join(child.text() for child in self.children)


class LocalDOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(element)
        if tag not in VOID_ELEMENTS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        assert len(self.stack) > 1 and self.stack[-1].tag == tag, f"unexpected </{tag}>"
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.stack[-1].text_parts.append(data)

    def finish(self) -> Element:
        self.close()
        assert self.stack == [self.root], "unclosed HTML elements"
        return self.root


def assets() -> tuple[str, str, str]:
    return tuple(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("index.html", "ops.js", "ops.css")
    )


def parse_html(html: str) -> Element:
    parser = LocalDOMParser()
    parser.feed(html)
    return parser.finish()


def elements_by_id(root: Element) -> dict[str, Element]:
    elements = root.descendants()
    ids = [element.attrs["id"] for element in elements if element.attrs.get("id")]
    assert len(ids) == len(set(ids)), "duplicate id"
    return {element.attrs["id"]: element for element in elements if element.attrs.get("id")}


def is_descendant(element: Element, ancestor: Element) -> bool:
    parent = element.parent
    while parent is not None:
        if parent is ancestor:
            return True
        parent = parent.parent
    return False


def control_type(element: Element) -> str | None:
    declared_type = element.attrs.get("type")
    if element.tag == "button":
        return (declared_type or "submit").casefold()
    if element.tag == "input":
        return (declared_type or "text").casefold()
    return declared_type.casefold() if declared_type is not None else None


def assert_dashboard_dom_contract(html: str) -> None:
    root = parse_html(html)
    by_id = elements_by_id(root)
    assert set(by_id) == EXPECTED_IDS
    for element in by_id.values():
        parent = element.parent
        while parent is not None:
            assert parent.tag != "template", f"id={element.attrs['id']} is inert"
            parent = parent.parent

    overview = by_id["overview-view"]
    execution = by_id["execution-view"]
    assert overview.parent is execution.parent
    assert overview.parent is not None and overview.parent.tag == "main"
    assert overview.parent.children.index(overview) < overview.parent.children.index(execution)
    assert "hidden" not in overview.attrs
    assert "hidden" in execution.attrs

    canvas = by_id["execution-canvas"]
    inspector = next(element for element in root.descendants("aside") if "inspector" in (element.attrs.get("class") or "").split())
    assert is_descendant(canvas, execution), "canvas must descend from execution-view"
    assert is_descendant(inspector, execution), "inspector must descend from execution-view"

    input_panel = by_id["input-panel"]
    output_panel = by_id["output-panel"]
    assert input_panel.parent is output_panel.parent
    assert "hidden" not in input_panel.attrs, "hidden Input"
    assert "hidden" not in output_panel.attrs, "hidden Output"
    assert is_descendant(input_panel, inspector)
    assert is_descendant(output_panel, inspector)

    controls = [element for element in root.descendants() if element.tag in {"button", "input", "select"}]
    control_contract = Counter(
        (element.tag, element.attrs.get("id"), control_type(element))
        for element in controls
    )
    assert control_contract == Counter(EXPECTED_CONTROLS), "unexpected control tag/id/type"
    anonymous_controls = [element for element in controls if not element.attrs.get("id")]
    assert len(anonymous_controls) == 2

    forms = root.descendants("form")
    assert len(forms) == 1
    logout_form = forms[0]
    assert (logout_form.attrs.get("method") or "get").casefold() == "post"
    assert logout_form.attrs.get("action") == "/ops/logout"
    assert "formaction" not in logout_form.attrs
    csrf = [
        element
        for element in logout_form.descendants("input")
        if element.attrs.get("name") == "csrf"
    ]
    assert len(csrf) == 1
    assert csrf[0].attrs.get("type") == "hidden"
    assert csrf[0].attrs.get("value") == "{{CSRF}}"

    submit_controls = [
        element
        for element in controls
        if element.tag in {"button", "input"} and control_type(element) == "submit"
    ]
    assert len(submit_controls) == 1
    assert submit_controls[0].parent is logout_form
    assert submit_controls[0].text().strip() == "Logout"
    assert set(map(id, anonymous_controls)) == {id(csrf[0]), id(submit_controls[0])}
    assert not [element for element in controls if "formaction" in element.attrs]

    links = root.descendants("link")
    scripts = [element.attrs.get("src") for element in root.descendants("script")]
    assert len(links) == 1
    assert (links[0].attrs.get("rel") or "").casefold() == "stylesheet"
    assert links[0].attrs.get("href") == "/ops/static/ops.css"
    assert scripts == ["/ops/static/ops.js"]


def test_dashboard_shell_dom_contract() -> None:
    html, _, _ = assets()
    assert_dashboard_dom_contract(html)
    assert "SOMENTE LEITURA" in html
    assert "Dados demonstrativos" not in html


@pytest.mark.parametrize(
    ("mutant", "message"),
    (
        (
            lambda html: html.replace(
                '<section id="input-panel" class="io-panel">',
                '<section class="io-panel" hidden id="input-panel">',
            ),
            "hidden Input",
        ),
        (
            lambda html: html.replace(
                '          <aside class="inspector">',
                '        </div>\n      </section>\n      <aside class="inspector">',
                1,
            ).replace(
                '          </aside>\n        </div>\n      </section>',
                '      </aside>',
                1,
            ),
            "inspector must descend from execution-view",
        ),
        (
            lambda html: html.replace('id="generated-at"', 'id="range-select"'),
            "duplicate id",
        ),
        (
            lambda html: html.replace(
                '      <section id="overview-view">',
                '      <button type="submit">Excluir</button>\n      <section id="overview-view">',
            ),
            "unexpected control tag/id/type",
        ),
        (
            lambda html: html.replace(
                '<input id="lead-search" autocomplete="off">',
                '<input id="lead-search" type="submit" autocomplete="off">',
                1,
            ),
            "unexpected control tag/id/type",
        ),
    ),
)
def test_dom_contract_rejects_causal_mutants(
    mutant: Callable[[str], str], message: str
) -> None:
    html, _, _ = assets()
    mutated = mutant(html)
    assert mutated != html, f"mutant fixture did not apply: {message}"
    with pytest.raises(AssertionError, match=message):
        assert_dashboard_dom_contract(mutated)


def test_accessibility_metadata_is_explicit() -> None:
    html, _, _ = assets()
    root = parse_html(html)
    by_id = elements_by_id(root)

    table = root.descendants("table")
    assert len(table) == 1
    captions = table[0].descendants("caption")
    assert len(captions) == 1 and captions[0].text().strip()
    headers = table[0].descendants("th")
    assert headers and all(header.attrs.get("scope") == "col" for header in headers)

    canvas = by_id["execution-canvas"]
    assert canvas.attrs.get("role") == "region"
    assert canvas.attrs.get("aria-label") == "Canvas da execução"
    assert by_id["zoom-out"].attrs.get("aria-label") == "Reduzir zoom"
    assert by_id["zoom-in"].attrs.get("aria-label") == "Aumentar zoom"


def test_html_has_no_commercial_claims() -> None:
    html, _, _ = assets()
    lowered = html.casefold()
    for forbidden in (
        "receita",
        "conversão",
        "novos leads",
        "qualificados",
        "interesse",
        "reservas confirmadas",
        "pagamento pago",
        "retry",
        "replay",
    ):
        assert forbidden not in lowered


def test_existing_javascript_and_css_read_only_markers_are_preserved() -> None:
    html, js, css = assets()
    assert "EventSource" in js
    assert "textContent" in js
    assert "innerHTML" not in js
    assert "fetch(" in js
    assert "method:" not in js
    for forbidden in (
        "reserve",
        "charge",
        "payment link",
        "retry",
        "replay",
        "send message",
    ):
        assert forbidden not in (html + js).casefold()
    assert "@media" in css
    assert "input-panel" in css and "output-panel" in css


def test_javascript_dashboard_contract_is_safe_and_explicit() -> None:
    _, js, _ = assets()
    for name in (
        "loadDashboard",
        "renderKpis",
        "renderExecutionSeries",
        "renderDistribution",
        "renderMilestones",
        "renderTopNodeTypes",
        "renderExecutionTable",
        "filteredExecutions",
        "openExecution",
        "showOverview",
        "renderCanvas",
        "selectNode",
        "connectLive",
    ):
        assert f"function {name}(" in js or f"async function {name}(" in js

    assert "textContent" in js
    assert "document.createElement(" in js
    assert "document.createElementNS(" in js
    assert "replaceChildren" in js
    assert "setAttribute" in js
    assert "innerHTML" not in js
    assert "method:" not in js
    for forbidden in (
        "SERVICES",
        "RANGE_MODELS",
        "demo-",
        "payment_link",
        "request_handoff",
        "create_reservation",
    ):
        assert forbidden not in js


def test_javascript_uses_the_closed_state_cards_and_exact_filters() -> None:
    _, js, _ = assets()
    for field, initial in (
        ("range", '"7d"'),
        ("snapshot", "null"),
        ("selectedExecution", "null"),
        ("nodes", "[]"),
        ("selectedNode", "null"),
        ("statusFilter", '""'),
        ("completenessFilter", '""'),
        ("leadFilter", '""'),
        ("zoom", "1"),
        ("connected", "false"),
    ):
        assert f"{field}: {initial}" in js

    for key, label in (
        ("executions", "Execuções"),
        ("distinct_leads", "Leads distintos"),
        ("in_progress", "Em andamento"),
        ("completed", "Concluídas"),
        ("failed", "Falhas"),
        ("manual_review", "Revisão manual"),
        ("technical_completion_rate", "Conclusão técnica"),
        ("average_terminal_duration_ms", "Duração média terminal"),
    ):
        assert f'["{key}", "{label}"]' in js

    assert "execution.lead_id === state.leadFilter" in js
    assert "execution.status === state.statusFilter" in js
    assert "execution.trace_completeness === state.completenessFilter" in js


def test_javascript_preserves_detail_and_uses_sse_without_polling() -> None:
    _, js, _ = assets()
    assert "const requestedRange = state.range" in js
    assert 'getJSON(`/ops/api/dashboard?range=${encodeURIComponent(requestedRange)}`)' in js
    assert "epoch !== state.dashboardEpoch || requestedRange !== state.range" in js
    assert 'window.location.assign("/ops/login")' in js
    assert 'error.code = "source_unavailable"' in js
    assert 'new EventSource("/ops/api/events")' in js
    for event in ("ready", "change", "degraded"):
        assert f'addEventListener("{event}"' in js
    assert "setInterval" not in js
    assert "state.snapshot =" in js
    assert '$("overview-view").hidden = true' in js
    assert '$("execution-view").hidden = false' in js
    assert '$("overview-view").hidden = false' in js
    assert '$("execution-view").hidden = true' in js
    assert 'payload.value ?? { status: "not_recorded" }' in js
    for token in (
        "lead_id",
        "execution_id",
        "received_at",
        "duration_ms",
        "status",
        "trace_completeness",
        "current_node_type",
        "node_count",
        "has_reservation",
        "has_payment",
        "has_public_delivery",
        "has_handoff",
        "terminal_reason",
    ):
        assert token in js


BROWSER_SPEC = r"""
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const html = fs.readFileSync('/static/index.html', 'utf8')
  .replace('  <script src="/ops/static/ops.js" defer></script>\n', '');
const js = fs.readFileSync('/static/ops.js', 'utf8');

function snapshot(label, executions) {
  return {
    generated_at: '2026-08-21T12:00:00Z', range: label,
    metrics: {executions, distinct_leads: executions, in_progress: 0,
      completed: executions, failed: 0, manual_review: 0,
      technical_completion_rate: 100, average_terminal_duration_ms: 1000},
    execution_series: [],
    status_distribution: [{status: 'completed', count: executions}],
    trace_distribution: [{trace_completeness: 'complete_trace', count: executions}],
    milestones: [], top_node_types: [], executions: [],
  };
}

function nodes(executionId) {
  return {nodes: [{
    node_id: `node-${executionId}`, node_type: `type_${executionId}`,
    ordinal: 1, attempt: 1, status: 'completed',
    input_summary: {execution: executionId}, output_summary: {execution: executionId},
    technical_metadata: {execution: executionId}, error: null,
    has_full_input: true, has_full_output: true,
  }]};
}

async function setup(page) {
  await page.setContent(html);
  await page.evaluate(() => {
    window.__requests = [];
    window.fetch = (path, options) => new Promise((resolve, reject) => {
      window.__requests.push({path: String(path), options, resolve, reject});
    });
    window.__resolveRequest = (index, status, payload) => {
      window.__requests[index].resolve({
        status, ok: status >= 200 && status < 300,
        json: async () => payload,
      });
    };
    class DeterministicEventSource {
      constructor(path) {
        this.path = path;
        this.listeners = {};
        window.__eventSource = this;
      }
      addEventListener(name, callback) { this.listeners[name] = callback; }
      emit(name) { this.listeners[name](); }
      fail() { this.onerror(new Error('disconnected')); }
    }
    window.EventSource = DeterministicEventSource;
  });
  await page.addScriptTag({content: js});
}

async function resolveRequest(page, index, status, payload) {
  await page.evaluate(
    ([index, status, payload]) => window.__resolveRequest(index, status, payload),
    [index, status, payload],
  );
  await page.waitForTimeout(0);
}

async function paths(page) {
  return page.evaluate(() => window.__requests.map(request => request.path));
}

test('dashboard ignores inverted A/B ranges', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.selectOption('#range-select', '24h');
  await page.selectOption('#range-select', '30d');
  expect((await paths(page)).slice(1)).toEqual([
    '/ops/api/dashboard?range=24h', '/ops/api/dashboard?range=30d',
  ]);
  await resolveRequest(page, 2, 200, snapshot('manual-30d', 30));
  await resolveRequest(page, 1, 200, snapshot('stale-24h', 24));
  await expect(page.locator('#range-select')).toHaveValue('30d');
  await expect(page.locator('#kpi-grid strong').first()).toHaveText('30');
});

test('dashboard ignores an SSE load superseded by a manual range', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => window.__eventSource.emit('change'));
  await page.selectOption('#range-select', '24h');
  await resolveRequest(page, 2, 200, snapshot('manual-24h', 240));
  await resolveRequest(page, 1, 200, snapshot('stale-sse-7d', 7));
  await expect(page.locator('#range-select')).toHaveValue('24h');
  await expect(page.locator('#kpi-grid strong').first()).toHaveText('240');
});

test('detail commits inverted A/B responses atomically', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => { openExecution('A').catch(handleDashboardError); });
  await page.evaluate(() => { openExecution('B').catch(handleDashboardError); });
  await resolveRequest(page, 2, 200, nodes('B'));
  await resolveRequest(page, 1, 200, nodes('A'));
  await expect(page.locator('#canvas-title')).toHaveText('B');
  await expect(page.locator('#node-title')).toHaveText('type B');
  await expect(page.locator('#input-summary')).toContainText('"B"');
});

test('empty detail clears inspector and ignores obsolete full', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => { openExecution('B').catch(handleDashboardError); });
  await resolveRequest(page, 1, 200, nodes('B'));
  await page.click('#load-full-input');
  expect((await paths(page))[2]).toContain('/executions/B/nodes/node-B/full?side=input');
  await page.evaluate(() => { openExecution('EMPTY').catch(handleDashboardError); });
  await resolveRequest(page, 3, 200, {nodes: []});
  await resolveRequest(page, 2, 200, {value: {execution: 'B', secret: 'obsolete'}});
  await expect(page.locator('#canvas-title')).toHaveText('EMPTY');
  await expect(page.locator('#node-title')).toHaveText('Nenhum nó selecionado');
  await expect(page.locator('#input-summary')).toHaveText('{}');
  await expect(page.locator('#input-full')).toBeHidden();
  await expect(page.locator('#input-full')).not.toContainText('obsolete');
});

test('detail loading and 503 clear every prior detail field', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => { openExecution('A').catch(handleDashboardError); });
  await resolveRequest(page, 1, 200, nodes('A'));
  await page.evaluate(() => { openExecution('FAIL').catch(handleDashboardError); });
  await expect(page.locator('#canvas-title')).toHaveText('FAIL');
  await expect(page.locator('#node-title')).toHaveText('Nenhum nó selecionado');
  await expect(page.locator('#input-summary')).toHaveText('{}');
  await resolveRequest(page, 2, 503, {status: 'source_unavailable'});
  await expect(page.locator('#canvas-title')).toHaveText('FAIL');
  await expect(page.locator('#node-title')).toHaveText('Nenhum nó selecionado');
  await expect(page.locator('#metadata')).toHaveText('{}');
  await expect(page.locator('#node-error')).toHaveText('null');
  await expect(page.locator('#dashboard-alert')).toContainText('Fonte operacional indisponível');
});

test('SSE ready does not clear a dashboard 503', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 503, {status: 'source_unavailable'});
  await page.evaluate(() => window.__eventSource.emit('ready'));
  await expect(page.locator('#dashboard-alert')).toContainText('Fonte operacional indisponível');
});

test('dashboard success does not clear degraded live state', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => window.__eventSource.emit('degraded'));
  await page.evaluate(() => { loadDashboard().catch(handleDashboardError); });
  await resolveRequest(page, 1, 200, snapshot('recovered-dashboard', 2));
  await expect(page.locator('#dashboard-alert')).toContainText('Atualização ao vivo indisponível');
  await expect(page.locator('#live-state')).toHaveText('Desconectado');
  await page.evaluate(() => window.__eventSource.emit('ready'));
  await expect(page.locator('#dashboard-alert')).toBeHidden();
  await expect(page.locator('#live-state')).toHaveText('Ao vivo');
});

test('reconnect clears only the live error after dashboard success', async ({page}) => {
  await setup(page);
  await resolveRequest(page, 0, 200, snapshot('initial', 1));
  await page.evaluate(() => window.__eventSource.fail());
  await expect(page.locator('#dashboard-alert')).toContainText('Atualização ao vivo indisponível');
  await page.evaluate(() => window.__eventSource.emit('ready'));
  await expect(page.locator('#dashboard-alert')).toBeHidden();
});
"""


def test_javascript_runs_adversarial_interleavings_in_real_chromium() -> None:
    docker_probe = subprocess.run(
        ["docker", "image", "inspect", "mcr.microsoft.com/playwright:v1.55.0-noble"],
        capture_output=True,
        text=True,
        check=False,
    )
    if docker_probe.returncode != 0:
        pytest.skip("Playwright Chromium container is unavailable")
    with tempfile.TemporaryDirectory(prefix="ops-browser-") as directory:
        os.chmod(directory, 0o755)
        spec = Path(directory) / "ops.spec.js"
        spec.write_text(BROWSER_SPEC, encoding="utf-8")
        spec.chmod(0o644)
        container = f"ops-browser-{os.getpid()}"
        started = subprocess.run(
            [
                "docker", "run", "--rm", "-d", "--name", container,
                "mcr.microsoft.com/playwright:v1.55.0-noble", "sleep", "300",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert started.returncode == 0, started.stdout + started.stderr
        prepared = subprocess.run(
            ["docker", "exec", container, "mkdir", "-p", "/work", "/static"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        try:
            for source, target in (
                (spec, "/work/ops.spec.js"),
                (ROOT / "index.html", "/static/index.html"),
                (ROOT / "ops.js", "/static/ops.js"),
            ):
                copied = subprocess.run(
                    ["docker", "cp", str(source), f"{container}:{target}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert copied.returncode == 0, copied.stdout + copied.stderr
            installed = subprocess.run(
                [
                    "docker", "exec", "-w", "/work", container,
                    "npm", "install", "--no-save", "@playwright/test@1.55.0",
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            assert installed.returncode == 0, installed.stdout + installed.stderr
            completed = subprocess.run(
                [
                    "docker", "exec", "-w", "/work", container,
                    "./node_modules/.bin/playwright", "test", "ops.spec.js",
                    "--reporter=line", "--workers=1",
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", container],
                capture_output=True,
                text=True,
                check=False,
            )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "8 passed" in output, output
