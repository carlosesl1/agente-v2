from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v2_ops.auth import hash_password
from v2_ops.contracts import ExecutionStatus, NodeType, OpsExecution, OpsNodeFinish, OpsNodeStart
from v2_ops.store import SQLiteOpsTraceWriter


ARTIFACTS = ROOT / "artifacts" / "ops-dashboard"
PLAYWRIGHT_CACHE = ARTIFACTS / "playwright"
DOCKER = Path("/usr/local/bin/docker")
IMAGE = "mcr.microsoft.com/playwright:v1.55.0-noble"
EXPECTED_IMAGE_ID = "sha256:09d59668831815b8b1e3862edae613fb450173d68ba2f4e3fc5fbb7be76e0e7d"
USERNAME = "ops-smoke"
PASSWORD = "ops-smoke-password"


def _write_fixture(path: Path, key: bytes, now: datetime) -> str:
    writer = SQLiteOpsTraceWriter(path, key)
    try:
        completed = OpsExecution("event-tech-completed", "lead-tech-completed", now - timedelta(hours=12))
        writer.write_execution(completed)
        node = OpsNodeStart(
            execution_id=completed.execution_id,
            node_type=NodeType.MAYA_REQUEST,
            ordinal=1,
            started_at=completed.received_at,
            input_summary={"request_id": "request-tech-001", "message_hash": "a" * 64},
            input_full={"request_id": "request-tech-001", "fixture_code": "input-tech"},
        )
        writer.start_node(node)
        writer.finish_node(
            OpsNodeFinish.from_start(
                node,
                status=ExecutionStatus.COMPLETED,
                completed_at=completed.received_at + timedelta(seconds=2),
                output_summary={"status": "ok", "result_code": "output-tech"},
                output_full={"status": "ok", "fixture_code": "output-tech"},
            )
        )
        writer.write_execution(
            replace(
                completed,
                status=ExecutionStatus.COMPLETED,
                current_node_id=node.node_id,
                completed_at=completed.received_at + timedelta(seconds=2),
                terminal_reason="technical_fixture_complete",
            )
        )
        writer.write_execution(
            OpsExecution("event-tech-pending", "lead-tech-pending", now - timedelta(days=3))
        )
        writer.write_execution(
            OpsExecution("event-tech-month", "lead-tech-month", now - timedelta(days=15))
        )
        return completed.execution_id
    finally:
        writer.close()


def _require_browser_capability() -> None:
    if not DOCKER.is_file():
        raise RuntimeError(f"browser capability missing: Docker not found at {DOCKER}")
    inspected = subprocess.run(
        [str(DOCKER), "image", "inspect", "--format", "{{.Id}}", IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    actual_id = inspected.stdout.strip()
    if inspected.returncode != 0 or actual_id != EXPECTED_IMAGE_ID:
        raise RuntimeError(
            "browser capability missing: pinned Playwright image unavailable or digest mismatch; "
            f"expected {EXPECTED_IMAGE_ID}, got {actual_id or inspected.stderr.strip()}"
        )
    package = PLAYWRIGHT_CACHE / "node_modules" / "playwright" / "package.json"
    if not package.is_file() or '"version": "1.55.0"' not in package.read_text(encoding="utf-8"):
        raise RuntimeError(
            "browser capability missing: cached playwright@1.55.0 package unavailable at "
            f"{PLAYWRIGHT_CACHE}; provision it once with the pinned container before running smoke"
        )


def _browser_spec(base_url: str, execution_id: str) -> str:
    return f"""
const {{ chromium }} = require('/pw/node_modules/playwright');
const path = require('path');
const baseURL = {base_url!r};
const expectedExecution = {execution_id!r};
const artifacts = '/artifacts';

(async () => {{
  const browser = await chromium.launch({{headless: true}});
  const errors = [];
  const failedRequests = [];
  try {{
    const context = await browser.newContext({{viewport: {{width: 1440, height: 1000}}}});
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(`pageerror: ${{error.message}}`));
    page.on('console', message => {{ if (message.type() === 'error') errors.push(`console: ${{message.text()}}`); }});
    page.on('requestfailed', request => failedRequests.push(`${{request.method()}} ${{request.url()}}: ${{request.failure()?.errorText}}`));

    let loginReady = false;
    for (let attempt = 0; attempt < 60; attempt += 1) {{
      try {{
        await page.goto(`${{baseURL}}/ops/login`, {{waitUntil: 'domcontentloaded', timeout: 1000}});
        loginReady = true;
        break;
      }} catch (error) {{
        await page.waitForTimeout(100);
      }}
    }}
    if (!loginReady) throw new Error('local uvicorn did not become ready');
    failedRequests.length = 0;
    await page.locator('input[name="username"]').fill({USERNAME!r});
    await page.locator('input[name="password"]').fill({PASSWORD!r});
    await Promise.all([
      page.waitForURL(`${{baseURL}}/ops/`),
      page.locator('button[type="submit"]').click(),
    ]);
    await page.locator('#kpi-grid .kpi-card').first().waitFor();
    if (!(await page.locator('#app-sidebar .brand-lockup').isVisible())) throw new Error('desktop brand lockup missing');
    if (await page.locator('#kpi-grid .kpi-card').count() !== 8) throw new Error('expected exactly eight KPI cards');
    if (await page.locator('#kpi-grid .kpi-icon').count() !== 8) throw new Error('expected eight KPI icons');
    if (!(await page.locator('.welcome-row').isVisible())) throw new Error('welcome row missing');
    if (await page.locator('.analytics-panel').count() !== 5) throw new Error('expected five analytics panels');
    if (!(await page.locator('.operations-head').isVisible())) throw new Error('operations heading missing');
    const cards = await page.locator('#kpi-grid .kpi-card').allTextContents();
    const expectedCards = [
      ['Execuções', '2'], ['Leads distintos', '2'], ['Em andamento', '1'],
      ['Concluídas', '1'], ['Falhas', '0'], ['Revisão manual', '0'],
      ['Conclusão técnica', '50.0%'], ['Duração média terminal', '2.0 s'],
    ];
    for (const [label, value] of expectedCards) {{
      if (!cards.some(card => card.includes(label) && card.includes(value))) throw new Error(`missing KPI ${{label}}=${{value}}`);
    }}
    const noOverflow = async () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth && document.body.scrollWidth <= document.body.clientWidth);
    const desktopLayout = await page.evaluate(() => ({{
      noOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth && document.body.scrollWidth <= document.body.clientWidth,
      sidebarWidth: Math.round(document.querySelector('#app-sidebar').getBoundingClientRect().width),
      kpiColumns: getComputedStyle(document.querySelector('#kpi-grid')).gridTemplateColumns.split(' ').length,
      analyticsRowOccupancies: Array.from([...document.querySelectorAll('.analytics-panel')]
        .reduce((rows, panel) => {{
          const top = Math.round(panel.getBoundingClientRect().top);
          rows.set(top, (rows.get(top) || 0) + 1);
          return rows;
        }}, new Map())
        .entries())
        .sort(([left], [right]) => left - right)
        .map(([, occupancy]) => occupancy),
    }}));
    if (!desktopLayout.noOverflow || desktopLayout.sidebarWidth < 230 || desktopLayout.kpiColumns !== 4 || JSON.stringify(desktopLayout.analyticsRowOccupancies) !== '[2,3]') throw new Error(JSON.stringify(desktopLayout));
    await page.screenshot({{path: path.join(artifacts, 'desktop-1440x1000.png'), fullPage: true}});

    await page.selectOption('#range-select', '24h');
    await page.locator('#kpi-grid .kpi-card strong').first().waitFor();
    await page.waitForFunction(() => document.querySelector('#kpi-grid .kpi-card strong')?.textContent === '1');
    await page.selectOption('#range-select', '30d');
    await page.waitForFunction(() => document.querySelector('#kpi-grid .kpi-card strong')?.textContent === '3');
    await page.selectOption('#status-filter', 'completed');
    if (await page.locator('#execution-table-body tr').count() !== 1) throw new Error('status filter count mismatch');
    await page.fill('#lead-search', 'lead-tech-completed');
    if (await page.locator('#execution-table-body tr').count() !== 1) throw new Error('lead filter count mismatch');
    const title = await page.locator('#execution-table-body .execution-link').getAttribute('title');
    if (title !== expectedExecution) throw new Error(`unexpected execution ${{title}}`);
    const desktopTrigger = page.locator('#execution-table-body .execution-link');
    await desktopTrigger.click();
    await page.locator('#nodes .node').first().waitFor();
    if (!(await page.locator('#overview-view').isVisible())) throw new Error('overview hidden behind desktop drawer');
    if (await page.locator('#execution-drawer').getAttribute('aria-hidden') !== 'false') throw new Error('desktop drawer aria state mismatch');
    if (!(await page.locator('#execution-canvas').isVisible())) throw new Error('canvas is not visible');
    if (!(await page.locator('#input-panel').isVisible()) || !(await page.locator('#output-panel').isVisible())) throw new Error('Input/Output panels are not visible');
    if (!(await page.locator('#input-summary').textContent()).includes('request-tech-001')) throw new Error('Input summary mismatch');
    if (!(await page.locator('#output-summary').textContent()).includes('output-tech')) throw new Error('Output summary mismatch');
    await page.click('#load-full-input');
    await page.click('#load-full-output');
    await page.waitForFunction(() => document.querySelector('#input-full')?.textContent.includes('input-tech'));
    await page.waitForFunction(() => document.querySelector('#output-full')?.textContent.includes('output-tech'));
    const desktopDrawer = await page.locator('#execution-drawer').evaluate(el => ({{
      width: el.getBoundingClientRect().width,
      viewport: window.innerWidth,
    }}));
    if (desktopDrawer.width < 700 || desktopDrawer.width > desktopDrawer.viewport * .92 + 1) throw new Error(`desktop drawer width mismatch: ${{JSON.stringify(desktopDrawer)}}`);
    const canvasScroll = await page.evaluate(() => {{ const el = document.querySelector('#execution-canvas'); return getComputedStyle(el).overflowX === 'auto' && el.scrollWidth > el.clientWidth; }});
    if (!canvasScroll) throw new Error('canvas does not preserve internal horizontal scroll');
    if (!(await noOverflow())) throw new Error('desktop detail body overflow');
    await page.screenshot({{path: path.join(artifacts, 'drawer-desktop-1440x1000.png')}});
    await page.keyboard.press('Escape');
    if (await page.locator('#execution-drawer').getAttribute('aria-hidden') !== 'true') throw new Error('Escape did not close desktop drawer');
    if (!(await desktopTrigger.evaluate(el => el === document.activeElement))) throw new Error('Escape did not restore desktop trigger focus');

    await page.setViewportSize({{width: 390, height: 844}});
    await page.waitForTimeout(100);
    if (!(await noOverflow())) throw new Error('mobile body overflow');
    if (!(await page.locator('#mobile-menu').isVisible())) throw new Error('mobile menu missing');
    if (await page.locator('.operations-panel table').isVisible()) throw new Error('desktop table visible on mobile');
    if (!(await page.locator('#execution-mobile-list').isVisible())) throw new Error('mobile cards missing');
    const mobileLayout = await page.evaluate(() => ({{
      kpiColumns: getComputedStyle(document.querySelector('#kpi-grid')).gridTemplateColumns.split(' ').length,
      sidebarPosition: getComputedStyle(document.querySelector('#app-sidebar')).position,
      closedSidebarRight: Math.round(document.querySelector('#app-sidebar').getBoundingClientRect().right),
      kpiContentContained: [...document.querySelectorAll('#kpi-grid .kpi-card')].every(card => {{
        const cardRect = card.getBoundingClientRect();
        const expectedSelectors = [
          '.kpi-foot', '.kpi-series', '.sparkline', '.kpi-label', '.kpi-value',
          '.kpi-foot > span', '.kpi-series > span',
        ];
        return card.scrollWidth <= card.clientWidth && expectedSelectors.every(selector => {{
          const elements = card.querySelectorAll(selector);
          if (elements.length !== 1) return false;
          const elementRect = elements[0].getBoundingClientRect();
          return elementRect.left >= cardRect.left - 1 && elementRect.right <= cardRect.right + 1
            && elementRect.top >= cardRect.top - 1 && elementRect.bottom <= cardRect.bottom + 1;
        }});
      }}),
    }}));
    if (mobileLayout.kpiColumns !== 2 || mobileLayout.sidebarPosition !== 'fixed' || mobileLayout.closedSidebarRight > 0 || !mobileLayout.kpiContentContained) throw new Error(`mobile layout mismatch: ${{JSON.stringify(mobileLayout)}}`);
    await page.click('#mobile-menu');
    if (!(await page.locator('#app-sidebar').evaluate(el => el.classList.contains('mobile-open')))) throw new Error('mobile sidebar did not open');
    await page.locator('#sidebar-backdrop').click({{position: {{x: 380, y: 100}}}});
    const mobileCards = await page.locator('.execution-mobile-card').count();
    if (mobileCards !== 1) throw new Error(`mobile filtered card count mismatch: ${{mobileCards}}`);
    await page.screenshot({{path: path.join(artifacts, 'mobile-390x844.png'), fullPage: true}});
    const mobileTrigger = page.locator('.execution-mobile-card .execution-link');
    await mobileTrigger.click();
    await page.locator('#nodes .node').first().waitFor();
    if (await page.locator('#execution-drawer').getAttribute('aria-hidden') !== 'false') throw new Error('mobile drawer aria state mismatch');
    if (!(await page.locator('#execution-canvas').isVisible()) || !(await page.locator('#input-panel').isVisible()) || !(await page.locator('#output-panel').isVisible())) throw new Error('mobile canvas or inspector missing');
    await page.click('#load-full-input');
    await page.click('#load-full-output');
    await page.waitForFunction(() => document.querySelector('#input-full')?.textContent.includes('input-tech'));
    await page.waitForFunction(() => document.querySelector('#output-full')?.textContent.includes('output-tech'));
    const mobileDrawer = await page.evaluate(() => {{
      const drawer = document.querySelector('#execution-drawer').getBoundingClientRect();
      const canvas = document.querySelector('.canvas-shell').getBoundingClientRect();
      const inspector = document.querySelector('.inspector').getBoundingClientRect();
      return {{width: drawer.width, viewport: window.innerWidth, inspectorStacks: inspector.top >= canvas.bottom - 1}};
    }});
    if (Math.abs(mobileDrawer.width - mobileDrawer.viewport) > 1 || !mobileDrawer.inspectorStacks) throw new Error(`mobile drawer layout mismatch: ${{JSON.stringify(mobileDrawer)}}`);
    if (!(await noOverflow())) throw new Error('mobile detail body overflow');
    await page.screenshot({{path: path.join(artifacts, 'drawer-mobile-390x844.png')}});
    await page.keyboard.press('Escape');
    if (!(await mobileTrigger.evaluate(el => el === document.activeElement))) throw new Error('Escape did not restore mobile trigger focus');

    await page.waitForTimeout(100);
    if (errors.length) throw new Error(`page/console errors: ${{errors.join(' | ')}}`);
    if (failedRequests.length) throw new Error(`failed requests: ${{failedRequests.join(' | ')}}`);
    await context.close();
    console.log('browser_contract=PASS');
  }} finally {{
    await browser.close();
  }}
}})().catch(error => {{ console.error(error.stack || error); process.exit(1); }});
"""


def main() -> None:
    screenshots = (
        ARTIFACTS / "desktop-1440x1000.png",
        ARTIFACTS / "mobile-390x844.png",
        ARTIFACTS / "drawer-desktop-1440x1000.png",
        ARTIFACTS / "drawer-mobile-390x844.png",
    )
    database = ARTIFACTS / "smoke.sqlite3"
    spec = ARTIFACTS / "smoke.js"
    server_module = ARTIFACTS / "smoke_server.py"
    generated = (
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        spec,
        server_module,
    )
    container = f"ops-dashboard-smoke-{os.getpid()}"
    browser_capability_ready = False
    try:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        for screenshot in screenshots:
            screenshot.unlink(missing_ok=True)
        for path in generated:
            path.unlink(missing_ok=True)
        _require_browser_capability()
        browser_capability_ready = True

        key = b"t" * 32
        now = datetime.now(timezone.utc).replace(microsecond=0)
        execution_id = _write_fixture(database, key, now)
        password_hash = hash_password(PASSWORD, salt=b"s" * 16)
        server_module.write_text(
            "from pathlib import Path\n"
            "from v2_ops.app import create_ops_app\n"
            "from v2_ops.settings import OpsWebSettings\n"
            "from v2_ops.store import SQLiteOpsTraceReader\n"
            f"key = {key!r}\n"
            "database = Path('/artifacts/smoke.sqlite3')\n"
            "settings = OpsWebSettings(\n"
            f"    username={USERNAME!r}, password_hash={password_hash!r},\n"
            "    session_key=b'k' * 32, trace_path=database, trace_key=key,\n"
            "    secure_cookie=False, release_sha='a' * 40,\n"
            "    image_digest='sha256:' + 'b' * 64, config_fingerprint='c' * 64,\n"
            ")\n"
            "app = create_ops_app(settings, reader=SQLiteOpsTraceReader(database, key))\n",
            encoding="utf-8",
        )
        spec.write_text(_browser_spec("http://127.0.0.1:18765", execution_id), encoding="utf-8")
        command = (
            "set -eu; server=''; "
            "cleanup() { if [ -n \"$server\" ]; then kill \"$server\" 2>/dev/null || true; "
            "wait \"$server\" 2>/dev/null || true; fi; }; trap cleanup EXIT INT TERM; "
            "PYTHONPATH=/repo:/venv/lib/python3.12/site-packages "
            "python3 -m uvicorn smoke_server:app --host 127.0.0.1 --port 18765 "
            "--log-level warning --no-access-log & server=$!; node smoke.js"
        )
        completed = subprocess.run(
            [
                str(DOCKER), "run", "--rm", "--name", container,
                "-v", f"{PLAYWRIGHT_CACHE}:/pw:ro",
                "-v", f"{ROOT}:/repo:ro",
                "-v", f"{ROOT / 'venv'}:/venv:ro",
                "-v", f"{ARTIFACTS}:/artifacts",
                "-w", "/artifacts", IMAGE, "sh", "-lc", command,
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        output = completed.stdout + completed.stderr
        if completed.returncode != 0 or "browser_contract=PASS" not in output:
            raise RuntimeError(f"real browser smoke failed (exit={completed.returncode}):\n{output}")
        for screenshot in screenshots:
            if not screenshot.is_file() or screenshot.stat().st_size == 0:
                raise RuntimeError(f"browser smoke did not produce screenshot: {screenshot}")
        print("ops_dashboard_smoke=PASS")
        print(f"desktop_screenshot={screenshots[0]}")
        print(f"mobile_screenshot={screenshots[1]}")
        print(f"drawer_desktop_screenshot={screenshots[2]}")
        print(f"drawer_mobile_screenshot={screenshots[3]}")
    finally:
        primary_failure = sys.exc_info()[0] is not None
        cleanup_error: OSError | None = None
        if browser_capability_ready:
            try:
                subprocess.run(
                    [str(DOCKER), "rm", "-f", container],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as exc:
                if not primary_failure:
                    cleanup_error = exc
        for path in generated:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                if not primary_failure and cleanup_error is None:
                    cleanup_error = exc
        if primary_failure or cleanup_error is not None:
            for screenshot in screenshots:
                try:
                    screenshot.unlink(missing_ok=True)
                except OSError:
                    if not primary_failure and cleanup_error is None:
                        raise
        if cleanup_error is not None:
            raise cleanup_error


if __name__ == "__main__":
    main()
