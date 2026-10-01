# Maya Ops Vertical Step Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the execution drawer’s connected rectangular node canvas with a safe, responsive vertical timeline containing only numbered circles and factual step names while preserving selection and Input/Output inspection.

**Architecture:** Keep the existing node API, drawer state, async epochs, and inspector flow unchanged. Replace only the presentation boundary: static drawer markup becomes a timeline region, `renderCanvas()` is narrowed into `renderTimeline()`, and CSS changes from absolute graph positioning to a one-column step rail. Static contract tests prove the old graph cannot return; one pinned real-Chromium smoke proves geometry, interaction, accessibility, desktop/mobile containment, and console cleanliness.

**Tech Stack:** Vanilla HTML/CSS/JavaScript, Python 3.12, pytest, Playwright 1.55.0 in the pinned Chromium container, Docker Compose for reversible release.

## Global Constraints

- Source authority: `docs/superpowers/specs/2026-08-23-maya-ops-vertical-step-timeline-design.md` at commit `8df0477faccb3b93a69ac9cec5dcc3d76059ec0e`.
- Functional base: release commit `8fa907c593b667ae8fcc3e719c4c7d1968634cdc`; preserve all later documentation commits.
- The timeline contains exactly a numbered circle and the factual normalized `node_type` name per row; do not show attempt, status, duration, time, icon, or auxiliary copy.
- Use `node.ordinal` when it is a positive integer; otherwise use the one-based array position only as a visual fallback.
- Preserve the current nodes endpoint, first-node selection, Input/Output inspector, full-value loading, drawer close/focus restoration, detail epochs, EventSource singleton, `MediaQueryList('(max-width: 720px)')` singleton, authentication, and read-only SQLite behavior.
- No backend, API, database, agent-runtime, provider, V3, Lucide-subset, or dashboard-overview changes.
- No `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, inline handlers, or inline styles.
- Desktop qualification viewport: `1440×1000`; mobile qualification viewport: `390×844`.
- Real Chromium must use exactly one worker and produce zero skips, zero `console.error`, zero page errors, zero failed requests, and zero horizontal overflow.
- Do not version databases, WAL/SHM files, screenshots, browser caches, credentials, or generated smoke artifacts.
- A production cutover may recreate only `v2-ops`; root filesystem and `/data/ops` stay read-only, and rollback to `8fa907c593b667ae8fcc3e719c4c7d1968634cdc` must remain immediately available.

## File Map

- Modify `v2_ops/static/index.html`: replace graph/zoom markup with the accessible timeline region and list root.
- Modify `v2_ops/static/ops.js`: remove zoom state/listeners and graph positioning; safely render and select timeline steps.
- Modify `v2_ops/static/ops.css`: replace canvas graph rules with vertical rail, numbered marker, selected/focus states, and responsive containment.
- Modify `tests/test_v2_ops_ui.py`: update the exact DOM/interactive contract and add persistent anti-regression assertions for timeline-only rendering and safe semantics.
- Modify `tests/browser/ops_dashboard_smoke.py`: create multiple factual nodes and qualify geometry, interaction, inspector updates, touch targets, and screenshots in real Chromium.
- Create `.superpowers/sdd/vertical-timeline-task-1-report.md`: immutable RED/GREEN evidence for the static/rendering task; do not commit this controller scratch file.
- Create `.superpowers/sdd/vertical-timeline-task-2-report.md`: immutable browser qualification evidence; do not commit this controller scratch file.
- Create `.superpowers/sdd/vertical-timeline-review.md`: final read-only review verdict; do not commit this controller scratch file.

---

### Task 1: Replace graph markup and rendering with a safe vertical timeline

**Files:**
- Modify: `tests/test_v2_ops_ui.py:23-82, 541-543, 823-840, 1065-1122`
- Modify: `v2_ops/static/index.html:126-139`
- Modify: `v2_ops/static/ops.js:3-24, 779-874, 1039-1105`
- Modify: `v2_ops/static/ops.css:251-295, 378-484`
- Create untracked evidence: `.superpowers/sdd/vertical-timeline-task-1-report.md`

**Interfaces:**
- Consumes: existing `state.nodes: Array<NodeProjection>`, `state.selectedNode: string|null`, `selectNode(nodeId: string): void`, and `displayValue(value): string`.
- Produces: `renderTimeline(): void`; DOM root `ol#execution-timeline`; one `li.timeline-item > button.execution-step[data-node-id]` per node; button descendants `.step-marker` and `.step-name`; selected button attribute `aria-current="step"`.

- [ ] **Step 1: Add a causal static timeline contract that fails against the graph**

Add this helper and focused test to `tests/test_v2_ops_ui.py`:

```python
def assert_vertical_timeline_contract(html: str, js: str, css: str) -> None:
    root = parse_html(html)
    by_id = elements_by_id(root)
    timeline = by_id["execution-timeline"]
    assert timeline.tag == "ol"
    assert timeline.attrs.get("aria-label") == "Passos da execução"
    assert timeline.attrs.get("role") is None

    for removed_id in ("execution-canvas", "edges", "fit-canvas", "zoom-in", "zoom-out"):
        assert removed_id not in by_id

    render = javascript_function_body(js, "renderTimeline")
    assert 'document.createElement("li")' in render
    assert 'item.className = "timeline-item"' in render
    assert 'document.createElement("button")' in render
    assert 'element.className = "execution-step"' in render
    assert 'element.setAttribute("aria-current", "step")' in render
    assert 'marker.className = "step-marker"' in render
    assert 'name.className = "step-name"' in render
    assert "marker.textContent" in render
    assert "name.textContent" in render
    assert "node.node_type" in render
    assert "node.ordinal" in render
    assert "node.attempt" not in render
    assert "node.status" not in render
    assert "document.createElementNS" not in render
    assert ".style." not in render

    assert "function nodePosition(" not in js
    assert "function renderCanvas(" not in js
    assert "state.zoom" not in js
    assert '$("edges")' not in js
    assert '$("zoom-in")' not in js
    assert '$("zoom-out")' not in js
    assert '$("fit-canvas")' not in js

    for selector in (
        '.execution-timeline', '.timeline-item', '.execution-step', '.step-marker', '.step-name',
        '.execution-step[aria-current="step"]', ".execution-step:focus-visible",
    ):
        assert selector in css
    for removed_selector in ("#edges", ".edge {", ".nodes {", ".node {"):
        assert removed_selector not in css


def test_execution_detail_uses_numbered_vertical_timeline_only() -> None:
    html, js, css = assets()
    assert_vertical_timeline_contract(html, js, css)
```

Update `EXPECTED_IDS`, `EXPECTED_CONTROLS`, `test_accessibility_metadata_is_explicit`, and the closed state assertion so they remove the five graph/zoom IDs, add `execution-timeline`, expect a list region rather than a focusable canvas, and no longer require `zoom: 1`.

- [ ] **Step 2: Run the focused test and capture the expected RED**

Run:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  HERMES_LEADS_AGENT_CONFIG_PATH="$PWD/config/agent.yaml" \
  venv/bin/python -m pytest -q \
  tests/test_v2_ops_ui.py::test_execution_detail_uses_numbered_vertical_timeline_only
```

Expected: exit `1`; FAIL because `execution-timeline`/`renderTimeline` do not exist and the old graph IDs still exist. Save command, exit code, failure class, and SHA-256 of raw output in `.superpowers/sdd/vertical-timeline-task-1-report.md`.

- [ ] **Step 3: Replace the drawer graph markup with the timeline root**

Replace `index.html:127-130` with:

```html
<section class="canvas-shell timeline-shell">
  <div class="canvas-toolbar"><strong id="canvas-title">Execução</strong></div>
  <ol id="execution-timeline" class="execution-timeline" aria-label="Passos da execução"></ol>
</section>
```

Do not add an empty-state sentence: a node-less execution remains an empty factual list and the inspector remains cleared.

- [ ] **Step 4: Implement the minimal safe renderer**

In `ops.js`, remove `zoom` from `state`, delete `nodePosition`, replace `renderCanvas` with this shape, and retain the existing click-to-`selectNode` behavior:

```javascript
function renderTimeline() {
  const timeline = $("execution-timeline");
  timeline.replaceChildren();
  state.nodes.forEach((node, index) => {
    const ordinal = Number(node.ordinal);
    const stepNumber = Number.isInteger(ordinal) && ordinal > 0 ? ordinal : index + 1;
    const selected = node.node_id === state.selectedNode;
    const item = document.createElement("li");
    item.className = "timeline-item";
    const element = document.createElement("button");
    element.type = "button";
    element.className = "execution-step";
    element.dataset.nodeId = node.node_id;
    if (selected) element.setAttribute("aria-current", "step");

    const marker = document.createElement("span");
    marker.className = "step-marker";
    marker.textContent = String(stepNumber);
    const name = document.createElement("span");
    name.className = "step-name";
    name.textContent = displayValue(node.node_type).replaceAll("_", " ");

    element.append(marker, name);
    element.addEventListener("click", () => selectNode(node.node_id));
    item.append(element);
    timeline.append(item);
  });
}
```

Then:

- change `clearDetail()` to clear `#execution-timeline` and remove the `#edges` operation;
- change `refreshOpenExecution()` to call `renderTimeline()`;
- change `selectNode()` to iterate `.execution-step`, toggle the `selected` class, set `aria-current="step"` on the selected button, and remove it from every other button;
- delete all three zoom listeners at the end of the file;
- preserve `clearInspector()`, all epoch checks, `loadFull()`, drawer state, startup, and EventSource byte-for-byte unless a selector rename is necessary.

- [ ] **Step 5: Replace graph CSS with the vertical rail**

Replace `ops.css:253-287` with rules equivalent to:

```css
.timeline-shell { overflow: hidden; }
.canvas-toolbar { display: flex; align-items: center; min-height: 58px; padding: 10px 14px; border-bottom: 1px solid var(--sand-300); }
.canvas-toolbar strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.execution-timeline { width: 100%; max-height: min(58vh, 620px); min-height: 300px; margin: 0; padding: 20px 18px; overflow-y: auto; overflow-x: hidden; list-style: none; background: #fbf6ed; }
.timeline-item { position: relative; margin: 0; padding: 0; }
.execution-step { display: grid; width: 100%; min-height: 52px; grid-template-columns: 36px minmax(0, 1fr); align-items: center; gap: 12px; border: 0; background: transparent; color: var(--ink); padding: 4px 8px; text-align: left; }
.timeline-item:not(:last-child)::after { content: ""; position: absolute; z-index: 0; top: 40px; bottom: -12px; left: 25px; width: 2px; background: var(--sand-300); }
.step-marker { position: relative; z-index: 1; display: grid; width: 36px; height: 36px; place-items: center; border: 2px solid var(--sand-300); border-radius: 50%; background: var(--ivory-50); color: var(--brand-700); font-size: 12px; font-weight: 800; }
.step-name { min-width: 0; color: var(--brand-800); font-size: 12px; font-weight: 700; overflow-wrap: normal; word-break: normal; }
.execution-step:hover .step-marker, .execution-step[aria-current="step"] .step-marker { border-color: var(--brand-700); background: var(--brand-700); color: var(--ivory-50); }
.execution-step[aria-current="step"] .step-name { color: var(--brand-700); }
.execution-step:focus-visible { outline: 3px solid rgba(36, 86, 52, .28); outline-offset: -2px; border-radius: 10px; }
```

At the mobile breakpoint, remove the fixed canvas heights and require `.execution-timeline { max-height: min(48vh, 390px); min-height: 220px; padding: 16px 10px; }` plus `.execution-step { min-height: 48px; }`. The connector must remain behind markers and must not extend after the last step.

- [ ] **Step 6: Run the focused contract GREEN, then the complete static UI suite**

Run:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  HERMES_LEADS_AGENT_CONFIG_PATH="$PWD/config/agent.yaml" \
  venv/bin/python -m pytest -q \
  tests/test_v2_ops_ui.py::test_execution_detail_uses_numbered_vertical_timeline_only
```

Expected: `1 passed`.

Then run:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  HERMES_LEADS_AGENT_CONFIG_PATH="$PWD/config/agent.yaml" \
  venv/bin/python -m pytest -q tests/test_v2_ops_ui.py
```

Expected: all tests pass, with no warning introduced by this task. Record exact counts and output hashes in the task report.

- [ ] **Step 7: Verify scope and commit Task 1**

Run:

```bash
git diff --check
git diff --name-only
git grep -nE 'nodePosition|renderCanvas|zoom-in|zoom-out|fit-canvas|id="edges"|class="edge"' -- \
  v2_ops/static/index.html v2_ops/static/ops.js v2_ops/static/ops.css
```

Expected: `git diff --check` passes; changed versioned paths are exactly the three static assets plus `tests/test_v2_ops_ui.py`; the final `git grep` returns exit `1` with no matches.

Commit:

```bash
git add v2_ops/static/index.html v2_ops/static/ops.js v2_ops/static/ops.css tests/test_v2_ops_ui.py
git commit -m "feat(v2-ops): restore vertical execution timeline"
```

---

### Task 2: Qualify timeline geometry and inspector interaction in real Chromium

**Files:**
- Modify: `tests/browser/ops_dashboard_smoke.py:28-72, 130-370`
- Test: `tests/test_v2_ops_ui.py`
- Create untracked evidence: `.superpowers/sdd/vertical-timeline-task-2-report.md`

**Interfaces:**
- Consumes: Task 1 DOM contract `#execution-timeline > button.execution-step`, `.step-marker`, `.step-name`, and `aria-current="step"`.
- Produces: browser witnesses `timeline_desktop=PASS`, `timeline_mobile=PASS`, `timeline_selection=PASS`; fresh desktop/mobile drawer screenshots; unchanged `browser_contract=PASS`, `chromium_workers=1`, and `chromium_skips=0` markers.

- [ ] **Step 1: Extend the browser fixture to contain three factual ordered steps**

Refactor `_write_fixture()` so the completed execution receives three `OpsNodeStart`/`OpsNodeFinish` pairs with ordinals `1`, `2`, and `3`, distinct `node_type` values already accepted by the contract, and summaries containing `request-tech-001`, `request-tech-002`, and `request-tech-003`. Return the same execution ID.

Use the exact existing enum members `NodeType.MAYA_REQUEST`, `NodeType.PROVIDER_READ_REQUEST`, and `NodeType.MAYA_RESPONSE`; do not add or modify enum members. Use a local tuple and the existing writer API rather than mocks:

```python
node_fixtures = (
    (NodeType.MAYA_REQUEST, "request-tech-001", "output-tech-001"),
    (NodeType.PROVIDER_READ_REQUEST, "request-tech-002", "output-tech-002"),
    (NodeType.MAYA_RESPONSE, "request-tech-003", "output-tech-003"),
)
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
```

- [ ] **Step 2: Replace the obsolete horizontal-scroll assertion with a causal timeline geometry witness**

In `_browser_spec()`, after opening the desktop drawer, replace `#nodes .node` and `canvasScroll` checks with:

```javascript
await page.locator('#execution-timeline .execution-step').first().waitFor();
const timelineContract = async viewport => page.evaluate(viewport => {
  const timeline = document.querySelector('#execution-timeline');
  const steps = [...timeline.querySelectorAll(':scope > .timeline-item > .execution-step')];
  const markers = steps.map(step => step.querySelector('.step-marker').getBoundingClientRect());
  const names = steps.map(step => step.querySelector('.step-name').getBoundingClientRect());
  const timelineRect = timeline.getBoundingClientRect();
  const centerSpread = Math.max(...markers.map(rect => rect.left + rect.width / 2))
    - Math.min(...markers.map(rect => rect.left + rect.width / 2));
  return {
    viewport,
    count: steps.length,
    numbers: steps.map(step => step.querySelector('.step-marker').textContent.trim()),
    names: steps.map(step => step.querySelector('.step-name').textContent.trim()),
    currentCount: steps.filter(step => step.getAttribute('aria-current') === 'step').length,
    centerSpread,
    ordered: markers.every((rect, index) => index === 0 || rect.top > markers[index - 1].top),
    touchTargets: steps.every(step => step.getBoundingClientRect().height >= (viewport === 'mobile' ? 44 : 48)),
    contained: [...markers, ...names].every(rect => rect.left >= timelineRect.left - 1 && rect.right <= timelineRect.right + 1),
    horizontalOverflow: timeline.scrollWidth - timeline.clientWidth,
  };
}, viewport);
```

Add assertions for exactly three steps, numbers `['1','2','3']`, nonempty factual names, one current step, `centerSpread <= 1`, increasing vertical positions, touch targets, containment, and horizontal overflow `<= 1`.

- [ ] **Step 3: Prove click selection updates both timeline state and inspector**

Before changing production code, add this browser expectation immediately after the initial first-step assertions:

```javascript
const secondStep = page.locator('#execution-timeline .execution-step').nth(1);
await secondStep.click();
if (await secondStep.getAttribute('aria-current') !== 'step') throw new Error('second step not current');
if (await page.locator('#execution-timeline [aria-current="step"]').count() !== 1) throw new Error('multiple current steps');
if (!(await page.locator('#input-summary').textContent()).includes('request-tech-002')) throw new Error('second step input mismatch');
if (!(await page.locator('#output-summary').textContent()).includes('output-tech-002')) throw new Error('second step output mismatch');
console.log('timeline_selection=PASS');
```

Run the smoke before completing all selector and fixture updates. Expected RED: exit `1` from missing `.execution-step`/multi-step geometry against Task 1’s pre-browser test state or from the obsolete graph selectors. Preserve the causal output hash in the task report.

- [ ] **Step 4: Complete desktop and mobile browser qualification**

Update all drawer selectors from `#nodes .node`/`#execution-canvas` to `#execution-timeline .execution-step`/`#execution-timeline`. Assert the geometry witness once at desktop `1440×1000` and once at mobile `390×844`, logging:

```javascript
console.log('timeline_desktop=PASS');
console.log('timeline_mobile=PASS');
```

Retain existing checks for:

- drawer visibility and width;
- Input/Output visibility and full payload loading;
- mobile inspector stacking;
- `Escape` close and trigger focus restoration;
- body overflow;
- page errors, all `console.error`, and failed requests;
- one worker and zero skips;
- fresh desktop/mobile drawer screenshots.

- [ ] **Step 5: Run real Chromium GREEN and inspect both drawer screenshots**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python tests/browser/ops_dashboard_smoke.py
```

Expected output includes:

```text
Running 1 test using 1 worker
browser_contract=PASS
timeline_desktop=PASS
timeline_mobile=PASS
timeline_selection=PASS
1 passed
ops_dashboard_smoke=PASS
chromium_workers=1
chromium_skips=0
```

Expected screenshots:

- `artifacts/ops-dashboard/drawer-desktop-1440x1000.png`
- `artifacts/ops-dashboard/drawer-mobile-390x844.png`

Inspect both images and reject the candidate if circles are not vertically aligned, names are not beside markers, a connector extends past the final marker, rectangular graph cards remain, focus/selection is ambiguous, the inspector overlaps the timeline, or any horizontal clipping appears. Screenshots remain untracked.

- [ ] **Step 6: Run static regression, hygiene, and cleanup gates**

Run:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  HERMES_LEADS_AGENT_CONFIG_PATH="$PWD/config/agent.yaml" \
  venv/bin/python -m pytest -q tests/test_v2_ops_ui.py
python -m py_compile tests/browser/ops_dashboard_smoke.py tests/test_v2_ops_ui.py
git diff --check
test ! -e artifacts/ops-dashboard/test-results
test -z "$(docker ps -aq --filter name=ops-dashboard-smoke-)"
```

Expected: complete static suite PASS, Python compilation PASS, no whitespace errors, no Playwright result residue, and no smoke container. Record exact test counts and image hashes in the task report.

- [ ] **Step 7: Commit Task 2**

Confirm the only new versioned path in Task 2 is `tests/browser/ops_dashboard_smoke.py` and that generated artifacts remain untracked/ignored, then commit:

```bash
git add tests/browser/ops_dashboard_smoke.py
git commit -m "test(v2-ops): qualify vertical execution timeline"
```

---

### Task 3: Final review, reversible release, and public verification

**Files:**
- Read: `docs/superpowers/specs/2026-08-23-maya-ops-vertical-step-timeline-design.md`
- Read: `docs/superpowers/plans/2026-08-23-maya-ops-vertical-step-timeline.md`
- Read: Task 1 and Task 2 immutable diffs/reports
- Create untracked review: `.superpowers/sdd/vertical-timeline-review.md`
- Update operational files only after cutover: `/home/ubuntu/workspace/agente-v2-ops-deploy/deployment.json`, release-specific Compose/env/archive/evidence files, and active pointer copies.

**Interfaces:**
- Consumes: frozen clean candidate SHA containing Tasks 1-2, green static suite, green pinned Chromium smoke, and approved read-only review.
- Produces: pushed exact remote SHA; release image `agente-v2-ops:<12-char-sha>`; public `/ops` with authenticated timeline smoke; rollback manifest pointing to `8fa907c593b667ae8fcc3e719c4c7d1968634cdc`.

- [ ] **Step 1: Freeze and authenticate the complete candidate**

Run:

```bash
git show -s --format='HEAD=%H%nPARENT=%P%nTREE=%T%nSUBJECT=%s' HEAD
git status --short --branch
git diff --check
git diff 8df0477faccb3b93a69ac9cec5dcc3d76059ec0e..HEAD -- \
  v2_ops/static/index.html v2_ops/static/ops.js v2_ops/static/ops.css \
  tests/test_v2_ops_ui.py tests/browser/ops_dashboard_smoke.py
```

Expected: clean index/worktree; exact five-file functional/test pathset; no backend/API/config/database changes.

- [ ] **Step 2: Run the final focused gates once against the frozen SHA**

Run the clean-environment static suite and pinned browser smoke exactly as in Tasks 1-2. Then run:

```bash
python -m compileall -q v2_ops tests/test_v2_ops_ui.py tests/browser/ops_dashboard_smoke.py
git diff --check
test -z "$(git status --porcelain=v1)"
```

Expected: all gates PASS; frozen SHA and tree remain unchanged after verification.

- [ ] **Step 3: Obtain an independent read-only review**

Create an immutable review package from `8df0477f...` to the frozen candidate. The reviewer must explicitly adjudicate:

- circles, numbers, names, rail, selected state, focus, mobile touch target, and overflow;
- removal of rectangles, SVG edges, absolute positioning, and zoom;
- safe DOM and no inline style;
- preserved node API, first selection, Input/Output, full-value loading, epochs, drawer close/focus, startup, EventSource, MediaQuery, authentication, and read-only contracts;
- no V3/backend/database drift;
- browser proof uses one worker and zero skips.

Require final tokens `Conformidade: APPROVE|REJECT` and `Qualidade: APPROVE|REJECT`, with Critical/Important/Minor counts. Do not build or deploy with either verdict rejected or any Critical/Important concern open.

- [ ] **Step 4: Push the exact candidate and build a labeled image**

After APPROVE/APPROVE:

```bash
SHA=$(git rev-parse HEAD)
git push --dry-run origin HEAD:feature/maya-ops-existing-data-dashboard
git push origin HEAD:feature/maya-ops-existing-data-dashboard
test "$(git ls-remote origin refs/heads/feature/maya-ops-existing-data-dashboard | cut -f1)" = "$SHA"
docker build --pull=false \
  --label org.opencontainers.image.revision="$SHA" \
  --label org.opencontainers.image.source="feature/maya-ops-existing-data-dashboard" \
  -t "agente-v2-ops:${SHA:0:12}" .
```

Expected: remote SHA equals local SHA and image label equals the same full SHA.

- [ ] **Step 5: Run a dark authenticated smoke with data invariance**

Launch the candidate image under a temporary unique name with:

- `--read-only`;
- `--cap-drop ALL`;
- `--security-opt no-new-privileges:true`;
- tmpfs `/tmp`;
- the live `/data/ops` source mounted `:ro`;
- a mechanically derived temporary env file whose only release differences are image, image digest, and release SHA.

Before and after login, assets, `/ops/api/release`, ranges `24h/7d/30d`, execution nodes, and full-value reads, compare size, mtime-ns, and SHA-256 for every data file. Expected: health/read-only PASS, authenticated endpoints PASS, exact release SHA PASS, data fingerprint byte-identical, zero `500/503/traceback/exception`, and temporary container removed.

- [ ] **Step 6: Perform a reversible `v2-ops`-only cutover**

Create release-specific Compose/env files and a rollback manifest retaining:

- rollback SHA `8fa907c593b667ae8fcc3e719c4c7d1968634cdc`;
- rollback image `agente-v2-ops:8fa907c593b6`;
- its Compose/env files and image archive.

Authenticate the Compose delta allowlist, then run only:

```bash
docker compose --env-file "/home/ubuntu/workspace/agente-v2-ops-deploy/v2-ops-${SHA:0:12}.env" \
  -f "/home/ubuntu/workspace/agente-v2-ops-deploy/compose-${SHA:0:12}.yaml" \
  up -d --no-deps --force-recreate v2-ops
```

A shell trap must restore the `8fa907c593b6` Compose/env pair if candidate health, image ID, revision label, read-only rootfs, read-only data mount, or public health fails. Snapshot API/worker/router IDs and start times before cutover and require byte-identical values afterward.

- [ ] **Step 7: Run public authenticated Chromium and update pointers only after PASS**

Use the pinned Playwright `1.55.0` image and cached package to log into `https://hermes.chapadabackpackers.com/ops`, open a real execution, and repeat the Task 2 geometry/selection contract at `1440×1000` and `390×844`. Require exact candidate release SHA, local Lucide assets, one worker, zero skips, zero console/page/request errors, and fresh public drawer screenshots.

Only after public PASS:

- atomically update active `compose.yaml`, `v2-ops.env`, and `deployment.json`;
- retain release-specific files, image archive/checksum, public evidence/checksums, and rollback manifest;
- verify no temporary smoke containers remain;
- verify recent candidate logs have zero `500/503/traceback/exception` lines;
- verify API, worker, and router IDs/start times remain unchanged.

Expected terminal evidence:

```text
public_timeline_desktop=PASS
public_timeline_mobile=PASS
public_timeline_selection=PASS
public_chromium=PASS
chromium_workers=1
chromium_skips=0
public_data_invariance=PASS
rollback_ready=8fa907c593b6
```

- [ ] **Step 8: Record completion without committing operational secrets or artifacts**

Update only the untracked SDD progress/report with candidate SHA, tree, review tokens, test counts, image ID, deployment timestamp, public evidence hashes, and rollback handle. Confirm `git status --porcelain=v1` remains empty and no env, credentials, databases, screenshots, caches, or deployment artifacts entered Git.
