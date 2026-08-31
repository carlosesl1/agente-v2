from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v2_ops.auth import hash_password
from v2_ops.contracts import ExecutionStatus, NodeType, OpsExecution, OpsNodeFinish, OpsNodeStart
from v2_ops.store import SQLiteOpsTraceWriter
from tests.v2_ops_records_fixture import write_records_fixture


ARTIFACTS = ROOT / "artifacts" / "ops-dashboard"
PLAYWRIGHT_CACHE = ARTIFACTS / "playwright"
DOCKER = Path("/usr/local/bin/docker")
IMAGE = "mcr.microsoft.com/playwright:v1.55.0-noble"
EXPECTED_IMAGE_ID = "sha256:09d59668831815b8b1e3862edae613fb450173d68ba2f4e3fc5fbb7be76e0e7d"
USERNAME = "ops-smoke"
PASSWORD = "ops-smoke-password"


def _smoke_bytecode_paths() -> tuple[Path, ...]:
    return tuple(sorted((ARTIFACTS / "__pycache__").glob("smoke_server*.pyc")))


def _records_fingerprint(root: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (
            str(path.relative_to(root)),
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(path for path in root.rglob("*") if path.is_file())
    )


def _write_fixture(path: Path, key: bytes, now: datetime) -> str:
    writer = SQLiteOpsTraceWriter(path, key)
    try:
        completed = OpsExecution("event-tech-completed", "lead-tech-completed", now - timedelta(hours=12))
        writer.write_execution(completed)
        node_fixtures = (
            (NodeType.MAYA_REQUEST, "request-tech-001", "output-tech-001"),
            (NodeType.PROVIDER_READ_REQUEST, "request-tech-002", "output-tech-002"),
            (NodeType.MAYA_RESPONSE, "request-tech-003", "output-tech-003"),
        )
        nodes = []
        for ordinal, (node_type, request_code, output_code) in enumerate(node_fixtures, 1):
            node = OpsNodeStart(
                execution_id=completed.execution_id,
                node_type=node_type,
                ordinal=ordinal,
                started_at=completed.received_at + timedelta(milliseconds=ordinal * 100),
                input_summary={"request_id": request_code, "message_hash": f"{ordinal}" * 64},
                input_full={"request_id": request_code, "fixture_code": f"input-tech-{ordinal:03d}"},
            )
            writer.start_node(node)
            writer.finish_node(OpsNodeFinish.from_start(
                node,
                status=ExecutionStatus.COMPLETED,
                completed_at=node.started_at + timedelta(milliseconds=100),
                output_summary={"status": "ok", "result_code": output_code},
                output_full={"status": "ok", "fixture_code": output_code},
            ))
            nodes.append(node)
        writer.write_execution(
            replace(
                completed,
                status=ExecutionStatus.COMPLETED,
                current_node_id=nodes[-1].node_id,
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
    await page.locator('#record-summary-grid .record-summary-card').first().waitFor();
    if (!(await page.locator('#app-sidebar .brand-lockup').isVisible())) throw new Error('desktop brand lockup missing');
    if (await page.locator('#kpi-grid .kpi-card').count() !== 8) throw new Error('expected exactly eight KPI cards');
    if (await page.locator('#kpi-grid .kpi-icon').count() !== 8) throw new Error('expected eight KPI icons');
    if (!(await page.locator('.welcome-row').isVisible())) throw new Error('welcome row missing');
    if (await page.locator('.analytics-panel').count() !== 5) throw new Error('expected five analytics panels');
    const recordsPayload = await page.evaluate(async () => {{
      const response = await fetch('/ops/api/records', {{credentials: 'same-origin'}});
      if (!response.ok) throw new Error(`records HTTP ${{response.status}}`);
      return response.json();
    }});
    if (await page.locator('#record-summary-grid .record-summary-card').count() !== 6) throw new Error('expected six factual record cards');
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
    const timelineContract = async viewport => page.evaluate(viewport => {{
      const timeline = document.querySelector('#execution-timeline');
      const steps = [...timeline.querySelectorAll(':scope > .timeline-item > .execution-step')];
      const markers = steps.map(step => step.querySelector('.step-marker').getBoundingClientRect());
      const names = steps.map(step => step.querySelector('.step-name').getBoundingClientRect());
      const timelineRect = timeline.getBoundingClientRect();
      const centerSpread = Math.max(...markers.map(rect => rect.left + rect.width / 2))
        - Math.min(...markers.map(rect => rect.left + rect.width / 2));
      return {{
        viewport,
        count: steps.length,
        numbers: steps.map(step => step.querySelector('.step-marker').textContent.trim()),
        names: steps.map(step => step.querySelector('.step-name').textContent.trim()),
        nodeIds: steps.map(step => step.getAttribute('data-node-id')),
        buttonTypes: steps.map(step => step.getAttribute('type')),
        currentCount: steps.filter(step => step.getAttribute('aria-current') === 'step').length,
        centerSpread,
        ordered: markers.every((rect, index) => index === 0 || rect.top > markers[index - 1].top),
        touchTargets: steps.every(step => step.getBoundingClientRect().height >= (viewport === 'mobile' ? 44 : 48)),
        contained: [...markers, ...names].every(rect => rect.left >= timelineRect.left - 1 && rect.right <= timelineRect.right + 1),
        horizontalOverflow: timeline.scrollWidth - timeline.clientWidth,
      }};
    }}, viewport);
    const assertTimeline = timeline => {{
      if (timeline.count !== 3) throw new Error(`timeline count ${{JSON.stringify(timeline)}}`);
      if (JSON.stringify(timeline.numbers) !== '["1","2","3"]') throw new Error(`timeline numbers ${{JSON.stringify(timeline)}}`);
      if (!timeline.names.every(name => name.length > 0)) throw new Error(`timeline names ${{JSON.stringify(timeline)}}`);
      if (!timeline.nodeIds.every(Boolean) || !timeline.buttonTypes.every(type => type === 'button')) throw new Error(`timeline semantics ${{JSON.stringify(timeline)}}`);
      if (timeline.currentCount !== 1 || timeline.centerSpread > 1 || !timeline.ordered || !timeline.touchTargets || !timeline.contained || timeline.horizontalOverflow > 1) {{
        throw new Error(`timeline geometry ${{JSON.stringify(timeline)}}`);
      }}
    }};
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
    await page.screenshot({{path: path.join(artifacts, 'overview-desktop-1440x1000.png'), fullPage: true, animations: 'allow', caret: 'initial'}});

    await page.click('#nav-leads');
    await page.locator('#lead-table-body > tr').first().waitFor();
    if (await page.locator('#lead-table-body > tr').count() !== recordsPayload.leads.length) throw new Error('lead list cardinality mismatch');
    if (!(await noOverflow())) throw new Error('desktop leads overflow');
    await page.screenshot({{path: path.join(artifacts, 'leads-desktop-1440x1000.png'), fullPage: true, animations: 'allow', caret: 'initial'}});
    const commercialLead = recordsPayload.leads.find(lead => lead.reservation_count > 0 || lead.fact_count > 0);
    if (!commercialLead) throw new Error('commercial fixture lead unavailable');
    const commercialRow = page.locator('#lead-table-body > tr').filter({{hasText: commercialLead.lead_id}});
    await commercialRow.locator('.lead-open').click();
    await page.locator('#lead-detail-title').waitFor();
    await page.waitForFunction(leadId => document.querySelector('#lead-detail-title')?.textContent === leadId, commercialLead.lead_id);
    if (!(await page.locator('#lead-detail').isVisible())) throw new Error('lead detail missing');
    if (!(await noOverflow())) throw new Error('desktop lead detail overflow');
    await page.screenshot({{path: path.join(artifacts, 'lead-detail-desktop-1440x1000.png'), fullPage: true, animations: 'allow', caret: 'initial'}});

    await page.click('#nav-reservations');
    if (await page.locator('#reservation-table-body > tr').count() !== recordsPayload.reservations.length) throw new Error('reservation cardinality mismatch');
    const confirmedReservation = recordsPayload.reservations.find(reservation => reservation.status_code === 'confirmed');
    if (confirmedReservation && !(await page.locator('#reservation-table-body').textContent()).includes(confirmedReservation.status_label)) throw new Error('confirmed reservation label missing');
    if (!(await noOverflow())) throw new Error('desktop reservations overflow');
    await page.screenshot({{path: path.join(artifacts, 'reservations-desktop-1440x1000.png'), fullPage: true, animations: 'allow', caret: 'initial'}});

    await page.click('#nav-payments');
    if (await page.locator('#payment-table-body > tr').count() !== recordsPayload.payments.length) throw new Error('payment cardinality mismatch');
    const unsettled = recordsPayload.payments.find(payment => payment.payment_link_prepared && !payment.settled);
    if (unsettled) {{
      const paymentText = await page.locator('#payment-table-body').textContent();
      if (!paymentText.includes(unsettled.status_label) || !paymentText.includes('Não registrado')) throw new Error('payment initiation promoted or missing');
    }}
    if (!(await noOverflow())) throw new Error('desktop payments overflow');
    await page.screenshot({{path: path.join(artifacts, 'payments-desktop-1440x1000.png'), fullPage: true, animations: 'allow', caret: 'initial'}});

    await page.click('#nav-handoffs');
    if (await page.locator('#handoff-table-body > tr').count() !== recordsPayload.handoffs.length) throw new Error('handoff cardinality mismatch');
    if (!recordsPayload.handoffs.length && !(await page.locator('#handoff-empty-state').isVisible())) throw new Error('factual handoff empty state missing');
    if (!(await noOverflow())) throw new Error('desktop handoffs overflow');
    await page.screenshot({{path: path.join(artifacts, 'handoffs-desktop-1440x1000.png'), fullPage: true, animations: 'allow', caret: 'initial'}});

    await page.click('#nav-execution');
    if (!(await page.locator('.operations-head').isVisible())) throw new Error('operations heading missing');
    await page.screenshot({{path: path.join(artifacts, 'executions-desktop-1440x1000.png'), fullPage: true, animations: 'allow', caret: 'initial'}});

    await page.selectOption('#range-select', '24h');
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
    await page.locator('#execution-timeline .execution-step').first().waitFor();
    if (!(await page.locator('#executions-view').isVisible())) throw new Error('execution context hidden behind desktop drawer');
    if (await page.locator('#execution-drawer').getAttribute('aria-hidden') !== 'false') throw new Error('desktop drawer aria state mismatch');
    if (await page.locator('#execution-timeline > .timeline-item > .execution-step').count() !== 3) throw new Error('timeline hierarchy/count mismatch');
    const firstStep = page.locator('#execution-timeline .execution-step').first();
    if (!(await firstStep.getAttribute('data-node-id'))) throw new Error('first step data-node-id missing');
    if (await firstStep.getAttribute('aria-current') !== 'step') throw new Error('first step not current');
    if (!(await page.locator('#input-panel').isVisible()) || !(await page.locator('#output-panel').isVisible())) throw new Error('Input/Output panels are not visible');
    if (!(await page.locator('#input-summary').textContent()).includes('request-tech-001')) throw new Error('Input summary mismatch');
    if (!(await page.locator('#output-summary').textContent()).includes('output-tech-001')) throw new Error('Output summary mismatch');
    const secondStep = page.locator('#execution-timeline .execution-step').nth(1);
    await secondStep.click();
    if (await secondStep.getAttribute('aria-current') !== 'step') throw new Error('second step not current');
    if (await page.locator('#execution-timeline [aria-current="step"]').count() !== 1) throw new Error('multiple current steps');
    if (!(await page.locator('#input-summary').textContent()).includes('request-tech-002')) throw new Error('second step input mismatch');
    if (!(await page.locator('#output-summary').textContent()).includes('output-tech-002')) throw new Error('second step output mismatch');
    if (!(await secondStep.evaluate(element => element === document.activeElement))) throw new Error('clicked step did not receive focus');
    console.log('timeline_selection=PASS');
    await page.click('#load-full-input');
    await page.click('#load-full-output');
    await page.waitForFunction(() => document.querySelector('#input-full')?.textContent.includes('input-tech'));
    await page.waitForFunction(() => document.querySelector('#output-full')?.textContent.includes('output-tech'));
    const desktopDrawer = await page.locator('#execution-drawer').evaluate(el => ({{
      width: el.getBoundingClientRect().width,
      viewport: window.innerWidth,
    }}));
    if (desktopDrawer.width < 700 || desktopDrawer.width > desktopDrawer.viewport * .92 + 1) throw new Error(`desktop drawer width mismatch: ${{JSON.stringify(desktopDrawer)}}`);
    assertTimeline(await timelineContract('desktop'));
    const fallbackNumbers = await page.evaluate(() => {{
      const ordinals = state.nodes.map(node => node.ordinal);
      const selectedNode = state.selectedNode;
      try {{
        state.nodes[0].ordinal = 0;
        state.nodes[1].ordinal = -1;
        state.nodes[2].ordinal = 1.5;
        renderTimeline();
        return [...document.querySelectorAll('#execution-timeline .step-marker')]
          .map(marker => marker.textContent.trim());
      }} finally {{
        state.nodes.forEach((node, index) => {{ node.ordinal = ordinals[index]; }});
        renderTimeline();
        selectNode(selectedNode);
      }}
    }});
    if (JSON.stringify(fallbackNumbers) !== '["1","2","3"]') throw new Error(`ordinal fallback mismatch: ${{JSON.stringify(fallbackNumbers)}}`);
    console.log('timeline_ordinal_fallback=PASS');
    if (!(await noOverflow())) throw new Error('desktop detail body overflow');
    await page.screenshot({{path: path.join(artifacts, 'drawer-desktop-1440x1000.png'), animations: 'allow', caret: 'initial'}});
    console.log('timeline_desktop=PASS');
    await page.keyboard.press('Escape');
    if (await page.locator('#execution-drawer').getAttribute('aria-hidden') !== 'true') throw new Error('Escape did not close desktop drawer');
    if (!(await desktopTrigger.evaluate(el => el === document.activeElement))) throw new Error('Escape did not restore desktop trigger focus');

    await page.setViewportSize({{width: 390, height: 844}});
    await page.waitForTimeout(100);
    await page.evaluate(() => setActiveView('overview', false));
    if (!(await noOverflow())) throw new Error('mobile body overflow');
    if (!(await page.locator('#mobile-menu').isVisible())) throw new Error('mobile menu missing');
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
    await page.screenshot({{path: path.join(artifacts, 'overview-mobile-390x844.png'), fullPage: true, animations: 'allow', caret: 'initial'}});
    await page.click('#mobile-menu');
    if (!(await page.locator('#app-sidebar').evaluate(el => el.classList.contains('mobile-open')))) throw new Error('mobile sidebar did not open');
    await page.locator('#sidebar-backdrop').click({{position: {{x: 380, y: 100}}}});
    await page.evaluate(() => setActiveView('executions', false));
    if (await page.locator('.operations-panel table').isVisible()) throw new Error('desktop table visible on mobile');
    if (!(await page.locator('#execution-mobile-list').isVisible())) throw new Error('mobile cards missing');
    const mobileCards = await page.locator('.execution-mobile-card').count();
    if (mobileCards !== 1) throw new Error(`mobile filtered card count mismatch: ${{mobileCards}}`);
    await page.screenshot({{path: path.join(artifacts, 'executions-mobile-390x844.png'), fullPage: true, animations: 'allow', caret: 'initial'}});
    const mobileTrigger = page.locator('.execution-mobile-card .execution-link');
    await mobileTrigger.click();
    await page.locator('#execution-timeline .execution-step').first().waitFor();
    if (await page.locator('#execution-drawer').getAttribute('aria-hidden') !== 'false') throw new Error('mobile drawer aria state mismatch');
    if (!(await page.locator('#execution-timeline').isVisible()) || !(await page.locator('#input-panel').isVisible()) || !(await page.locator('#output-panel').isVisible())) throw new Error('mobile timeline or inspector missing');
    await page.click('#load-full-input');
    await page.click('#load-full-output');
    await page.waitForFunction(() => document.querySelector('#input-full')?.textContent.includes('input-tech'));
    await page.waitForFunction(() => document.querySelector('#output-full')?.textContent.includes('output-tech'));
    const mobileDrawer = await page.evaluate(() => {{
      const drawer = document.querySelector('#execution-drawer').getBoundingClientRect();
      const timeline = document.querySelector('.canvas-shell').getBoundingClientRect();
      const inspector = document.querySelector('.inspector').getBoundingClientRect();
      return {{width: drawer.width, viewport: window.innerWidth, inspectorStacks: inspector.top >= timeline.bottom - 1}};
    }});
    if (Math.abs(mobileDrawer.width - mobileDrawer.viewport) > 1 || !mobileDrawer.inspectorStacks) throw new Error(`mobile drawer layout mismatch: ${{JSON.stringify(mobileDrawer)}}`);
    assertTimeline(await timelineContract('mobile'));
    if (!(await noOverflow())) throw new Error('mobile detail body overflow');
    await page.locator('#execution-timeline').evaluate(element => element.scrollIntoView({{block: 'start'}}));
    await page.screenshot({{path: path.join(artifacts, 'drawer-mobile-390x844.png'), animations: 'allow', caret: 'initial'}});
    console.log('timeline_mobile=PASS');
    await page.keyboard.press('Escape');
    if (!(await mobileTrigger.evaluate(el => el === document.activeElement))) throw new Error('Escape did not restore mobile trigger focus');

    await page.evaluate(() => {{
      setActiveView('leads', false);
      document.querySelector('#lead-detail').hidden = true;
    }});
    if (!(await noOverflow())) throw new Error('mobile leads overflow');
    await page.screenshot({{path: path.join(artifacts, 'leads-mobile-390x844.png'), fullPage: true, animations: 'allow', caret: 'initial'}});
    await page.evaluate(() => renderLeadDetail());
    if (!(await noOverflow())) throw new Error('mobile lead detail overflow');
    await page.screenshot({{path: path.join(artifacts, 'lead-detail-mobile-390x844.png'), fullPage: true, animations: 'allow', caret: 'initial'}});
    await page.evaluate(() => setActiveView('reservations', false));
    if (!(await page.locator('#reservation-mobile-list').isVisible()) || !(await noOverflow())) throw new Error('mobile reservations layout');
    await page.screenshot({{path: path.join(artifacts, 'reservations-mobile-390x844.png'), fullPage: true, animations: 'allow', caret: 'initial'}});
    await page.evaluate(() => setActiveView('payments', false));
    if (!(await page.locator('#payment-mobile-list').isVisible()) || !(await noOverflow())) throw new Error('mobile payments layout');
    await page.screenshot({{path: path.join(artifacts, 'payments-mobile-390x844.png'), fullPage: true, animations: 'allow', caret: 'initial'}});
    await page.evaluate(() => setActiveView('handoffs', false));
    if (!(await noOverflow())) throw new Error('mobile handoffs layout');
    await page.screenshot({{path: path.join(artifacts, 'handoffs-mobile-390x844.png'), fullPage: true, animations: 'allow', caret: 'initial'}});

    await page.waitForTimeout(100);
    if (errors.length) throw new Error(`page/console errors: ${{errors.join(' | ')}}`);
    if (failedRequests.length) throw new Error(`failed requests: ${{failedRequests.join(' | ')}}`);
    await context.close();
    console.log('browser_contract=PASS');
}});
"""


def main() -> None:
    screenshots = (
        ARTIFACTS / "overview-desktop-1440x1000.png",
        ARTIFACTS / "leads-desktop-1440x1000.png",
        ARTIFACTS / "lead-detail-desktop-1440x1000.png",
        ARTIFACTS / "executions-desktop-1440x1000.png",
        ARTIFACTS / "reservations-desktop-1440x1000.png",
        ARTIFACTS / "payments-desktop-1440x1000.png",
        ARTIFACTS / "handoffs-desktop-1440x1000.png",
        ARTIFACTS / "overview-mobile-390x844.png",
        ARTIFACTS / "leads-mobile-390x844.png",
        ARTIFACTS / "lead-detail-mobile-390x844.png",
        ARTIFACTS / "executions-mobile-390x844.png",
        ARTIFACTS / "reservations-mobile-390x844.png",
        ARTIFACTS / "payments-mobile-390x844.png",
        ARTIFACTS / "handoffs-mobile-390x844.png",
        ARTIFACTS / "drawer-desktop-1440x1000.png",
        ARTIFACTS / "drawer-mobile-390x844.png",
    )
    database = ARTIFACTS / "smoke.sqlite3"
    records_root = ARTIFACTS / "records"
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
        shutil.rmtree(records_root, ignore_errors=True)
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
        write_records_fixture(records_root)
        records_before = _records_fingerprint(records_root)
        password_hash = hash_password(PASSWORD, salt=b"s" * 16)
        server_module.write_text(
            "from pathlib import Path\n"
            "from v2_ops.app import create_ops_app\n"
            "from v2_ops.settings import OpsWebSettings\n"
            "from v2_ops.store import SQLiteOpsTraceReader\n"
            f"key = {key!r}\n"
            "database = Path('/artifacts/smoke.sqlite3')\n"
            "records = Path('/artifacts/records')\n"
            "settings = OpsWebSettings(\n"
            f"    username={USERNAME!r}, password_hash={password_hash!r},\n"
            "    session_key=b'k' * 32, trace_path=database, trace_key=key,\n"
            "    secure_cookie=False, release_sha='a' * 40,\n"
            "    image_digest='sha256:' + 'b' * 64, config_fingerprint='c' * 64,\n"
            "    records_path=records,\n"
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
        if _records_fingerprint(records_root) != records_before:
            raise RuntimeError("browser smoke mutated the commercial SQLite fixture")
        required_markers = (
            "browser_contract=PASS",
            "timeline_desktop=PASS",
            "timeline_mobile=PASS",
            "timeline_selection=PASS",
            "timeline_ordinal_fallback=PASS",
        )
        if (
            completed.returncode != 0
            or any(marker not in output for marker in required_markers)
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
        print("records_sqlite_invariance=PASS")
        for screenshot in screenshots:
            print(f"screenshot={screenshot}")
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
        try:
            shutil.rmtree(records_root, ignore_errors=False)
        except FileNotFoundError:
            pass
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
