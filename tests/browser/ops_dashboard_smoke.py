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


def _smoke_bytecode_paths() -> tuple[Path, ...]:
    return tuple(sorted((ARTIFACTS / "__pycache__").glob("smoke_server*.pyc")))


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
const {{ test }} = require('/pw/node_modules/@playwright/test');
const path = require('path');
const baseURL = {base_url!r};
const expectedExecution = {execution_id!r};
const artifacts = '/artifacts';

test('desktop and mobile operational dashboard geometry', async ({{browser}}) => {{
  const errors = [];
  const failedRequests = [];
    const context = await browser.newContext({{viewport: {{width: 1440, height: 1000}}}});
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(`pageerror: ${{error.message}}`));
    page.on('console', message => {{
      if (message.type() !== 'error') return;
      const location = message.location();
      const detail = `console: ${{message.text()}} @ ${{JSON.stringify(location)}}`;
      errors.push(detail);
    }});
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
    const geometryContract = async viewport => page.evaluate(viewport => {{
      const tolerance = 1;
      const controls = [...document.querySelectorAll('#range-select, #refresh-dashboard, #operator-menu')];
      const controlRects = controls.map(node => node.getBoundingClientRect());
      const heights = controlRects.map(rect => rect.height);
      const headerRect = document.querySelector('.dashboard-header').getBoundingClientRect();
      const controlTopSpread = Math.max(...controlRects.map(rect => rect.top)) - Math.min(...controlRects.map(rect => rect.top));
      const mobileMenuRect = document.querySelector('#mobile-menu').getBoundingClientRect();
      const compactMobileHeader = viewport !== 'mobile-390x844' || (
        headerRect.height <= 85 && controlTopSpread <= tolerance
        && Math.abs(mobileMenuRect.height - 48) <= tolerance
        && Math.abs(mobileMenuRect.top - controlRects[0].top) <= tolerance
      );
      const healthIndicatorCount = document.querySelectorAll('.health-pill .online-dot').length;
      const cards = [...document.querySelectorAll('#kpi-grid > .kpi-card')];
      const rowHeights = [...cards.reduce((rows, card) => {{
        const rect = card.getBoundingClientRect();
        const top = Math.round(rect.top);
        rows.set(top, [...(rows.get(top) || []), rect.height]);
        return rows;
      }}, new Map()).values()];
      const rowsEqual = rowHeights.every(row => Math.max(...row) - Math.min(...row) <= tolerance);
      const selectors = [
        '.kpi-top', '.kpi-value', '.kpi-foot', '.kpi-foot > span',
        '.kpi-series', '.kpi-series > span', '.sparkline',
      ];
      const kpiChecks = cards.map((card, index) => {{
        const cardRect = card.getBoundingClientRect();
        const description = card.querySelector('.kpi-foot > span');
        const series = card.querySelector('.kpi-series');
        const descriptionRange = document.createRange();
        descriptionRange.selectNodeContents(description);
        const descriptionRect = descriptionRange.getBoundingClientRect();
        const seriesRect = series.getBoundingClientRect();
        const verticalOverlap = descriptionRect.top < seriesRect.bottom - tolerance
          && seriesRect.top < descriptionRect.bottom - tolerance;
        const siblingsDoNotOverlap = !verticalOverlap || descriptionRect.right <= seriesRect.left + tolerance;
        const contained = selectors.every(selector => {{
          const elements = card.querySelectorAll(selector);
          if (elements.length !== 1) return false;
          const rect = elements[0].getBoundingClientRect();
          return rect.left >= cardRect.left - tolerance && rect.right <= cardRect.right + tolerance
            && rect.top >= cardRect.top - tolerance && rect.bottom <= cardRect.bottom + tolerance;
        }});
        return {{index, scrollContained: card.scrollWidth <= card.clientWidth, siblingsDoNotOverlap, contained}};
      }});
      const kpiGeometry = cards.length === 8 && rowsEqual
        && kpiChecks.every(check => check.scrollContained && check.siblingsDoNotOverlap && check.contained);
      const labelsDoNotStackSingleWords = [...document.querySelectorAll('.kpi-series > span')].every(label => {{
        const textNode = [...label.childNodes].find(node => node.nodeType === Node.TEXT_NODE);
        if (!textNode) return false;
        const words = [...textNode.textContent.matchAll(/\\S+/g)];
        const lines = [];
        for (const word of words) {{
          const range = document.createRange();
          range.setStart(textNode, word.index);
          range.setEnd(textNode, word.index + word[0].length);
          const rect = range.getBoundingClientRect();
          const line = lines.find(item => Math.abs(item.top - rect.top) <= tolerance);
          if (line) line.words += 1;
          else lines.push({{top: rect.top, words: 1}});
        }}
        return !lines.some((line, index) => line.words === 1 && lines[index + 1]?.words === 1);
      }});
      return {{
        viewport,
        headerGeometry: heights.length === 3
          && heights.every(height => Math.abs(height - 48) <= tolerance)
          && controlTopSpread <= tolerance,
        compactMobileHeader,
        headerHeight: headerRect.height,
        controlTopSpread,
        controlTops: controlRects.map(rect => rect.top),
        controlLefts: controlRects.map(rect => rect.left),
        headerActions: (() => {{
          const node = document.querySelector('.header-actions');
          const rect = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          return {{top: rect.top, left: rect.left, width: rect.width, height: rect.height, flexDirection: style.flexDirection, alignItems: style.alignItems}};
        }})(),
        rangeControl: (() => {{
          const rect = document.querySelector('.range-control').getBoundingClientRect();
          const style = getComputedStyle(document.querySelector('.range-control'));
          return {{top: rect.top, left: rect.left, width: rect.width, height: rect.height, gridTemplateRows: style.gridTemplateRows}};
        }})(),
        mobileMenuHeight: mobileMenuRect.height,
        heights,
        healthIndicatorCount,
        kpiGeometry,
        kpiFailures: kpiChecks.filter(check => !check.scrollContained || !check.siblingsDoNotOverlap || !check.contained),
        labelsDoNotStackSingleWords,
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }};
    }}, viewport);
    const assertGeometry = geometry => {{
      if (!geometry.headerGeometry || !geometry.compactMobileHeader) throw new Error(`headerGeometry ${{JSON.stringify(geometry)}}`);
      if (geometry.healthIndicatorCount !== 1) throw new Error(`healthIndicatorCount ${{JSON.stringify(geometry)}}`);
      if (!geometry.kpiGeometry || !geometry.labelsDoNotStackSingleWords) throw new Error(`kpiGeometry ${{JSON.stringify(geometry)}}`);
      if (geometry.horizontalOverflow > 1) throw new Error(`horizontalOverflow ${{JSON.stringify(geometry)}}`);
      console.log(`geometry_${{geometry.viewport}}=PASS`);
    }};
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
    assertGeometry(await geometryContract('desktop-1440x1000'));
    await page.screenshot({{path: path.join(artifacts, 'desktop-1440x1000.png'), fullPage: true, animations: 'allow', caret: 'initial'}});

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
    await page.screenshot({{path: path.join(artifacts, 'drawer-desktop-1440x1000.png'), animations: 'allow', caret: 'initial'}});
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
    assertGeometry(await geometryContract('mobile-390x844'));
    await page.click('#mobile-menu');
    if (!(await page.locator('#app-sidebar').evaluate(el => el.classList.contains('mobile-open')))) throw new Error('mobile sidebar did not open');
    await page.locator('#sidebar-backdrop').click({{position: {{x: 380, y: 100}}}});
    const mobileCards = await page.locator('.execution-mobile-card').count();
    if (mobileCards !== 1) throw new Error(`mobile filtered card count mismatch: ${{mobileCards}}`);
    await page.screenshot({{path: path.join(artifacts, 'mobile-390x844.png'), fullPage: true, animations: 'allow', caret: 'initial'}});
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
    await page.screenshot({{path: path.join(artifacts, 'drawer-mobile-390x844.png'), animations: 'allow', caret: 'initial'}});
    await page.keyboard.press('Escape');
    if (!(await mobileTrigger.evaluate(el => el === document.activeElement))) throw new Error('Escape did not restore mobile trigger focus');

    await page.waitForTimeout(100);
    if (errors.length) throw new Error(`page/console errors: ${{errors.join(' | ')}}`);
    if (failedRequests.length) throw new Error(`failed requests: ${{failedRequests.join(' | ')}}`);
    await context.close();
    console.log('browser_contract=PASS');
}});
"""


def main() -> None:
    screenshots = (
        ARTIFACTS / "desktop-1440x1000.png",
        ARTIFACTS / "mobile-390x844.png",
        ARTIFACTS / "drawer-desktop-1440x1000.png",
        ARTIFACTS / "drawer-mobile-390x844.png",
    )
    database = ARTIFACTS / "smoke.sqlite3"
    spec = ARTIFACTS / "smoke.spec.js"
    server_module = ARTIFACTS / "smoke_server.py"
    generated = (
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        ARTIFACTS / "smoke.js",
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
        stale_bytecode = _smoke_bytecode_paths()
        if stale_bytecode:
            raise RuntimeError(f"stale smoke server bytecode exists: {stale_bytecode}")
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
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/repo:/venv/lib/python3.12/site-packages "
            "python3 -m uvicorn smoke_server:app --host 127.0.0.1 --port 18765 "
            "--log-level warning --no-access-log & server=$!; "
            "/pw/node_modules/.bin/playwright test smoke.spec.js --reporter=line --workers=1 "
            "--output=/tmp/ops-smoke-results"
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
        if (
            completed.returncode != 0
            or "browser_contract=PASS" not in output
            or "Running 1 test using 1 worker" not in output
            or "1 passed" not in output
            or "skipped" in output.casefold()
        ):
            raise RuntimeError(f"real browser smoke failed (exit={completed.returncode}):\n{output}")
        residual_bytecode = _smoke_bytecode_paths()
        if residual_bytecode:
            raise RuntimeError(f"smoke server left bytecode: {residual_bytecode}")
        for screenshot in screenshots:
            if not screenshot.is_file() or screenshot.stat().st_size == 0:
                raise RuntimeError(f"browser smoke did not produce screenshot: {screenshot}")
        print(output.strip())
        print("ops_dashboard_smoke=PASS")
        print("chromium_workers=1")
        print("chromium_skips=0")
        print(f"desktop_screenshot={screenshots[0]}")
        print(f"mobile_screenshot={screenshots[1]}")
        print(f"drawer_desktop_screenshot={screenshots[2]}")
        print(f"drawer_mobile_screenshot={screenshots[3]}")
    finally:
        primary_failure = sys.exc_info()[0] is not None
        cleanup_error: OSError | RuntimeError | None = None
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
        residual_bytecode = _smoke_bytecode_paths()
        if residual_bytecode and not primary_failure and cleanup_error is None:
            cleanup_error = RuntimeError(f"smoke server left bytecode: {residual_bytecode}")
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
