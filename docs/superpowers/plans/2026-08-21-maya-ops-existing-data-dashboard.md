# Maya Ops Existing-Data Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformar `/ops/` em um painel operacional visual alimentado exclusivamente pelo trace Ops existente, preservando o canvas atual como detalhe e sem alterar o agente, sua instrumentação ou seus efeitos.

**Architecture:** Um reader SQLite estritamente read-only produz registros operacionais fechados para uma janela UTC máxima de 30 dias. Um módulo puro calcula indicadores, séries e distribuições; a FastAPI expõe um único GET autenticado com ETag; o frontend renderiza visão geral e canvas usando somente criação segura de DOM e os endpoints Ops atuais.

**Tech Stack:** Python 3.12, FastAPI, SQLite, dataclasses, pytest, JavaScript sem framework, HTML5 e CSS responsivo.

## Global Constraints

- Fonte única: `v2-ops-trace.sqlite3`, tabelas `executions`, `nodes` e `ops_schema`.
- Não alterar runtime Maya V2, prompts, skills, tools, adapters, instrumentação, providers, ingress, ledgers ou efeitos.
- Não importar, consultar nem executar `/home/ubuntu/chapada-leads-hermes`; não tocar no Agente V3.
- Não interpretar summaries, erros ou texto para inferir intenção, produto, receita, conversão, satisfação, etapa comercial ou motivo de handoff.
- Períodos fechados: `24h`, `7d`, `30d`; intervalo inclusivo `received_at >= generated_at - D` e `received_at <= generated_at`.
- Estados visíveis fechados: `pending`, `running`, `running_stale`, `completed`, `failed`, `manual_review`; completude fechada: `complete_trace`, `partial_trace`, `ledger_only`.
- Oito cards: execuções, leads distintos, em andamento, concluídas, falhas, revisão manual, taxa técnica de conclusão e duração média terminal.
- Marcos são presença técnica por execução distinta; retries e múltiplos nós não inflam contagens.
- Base vazia permanece vazia; taxas e médias sem amostra usam `null` na API e `—` na UI.
- API e UI permanecem read-only. Nenhum POST novo, PUT, PATCH, DELETE, replay, retry, reserva, pagamento, mensagem ou handoff.
- Nenhum deploy, restart ou efeito externo faz parte deste plano.
- Todos os comandos de teste usam `venv/bin/python -m pytest` dentro de `/home/ubuntu/agente-v2/.worktrees/maya-ops-existing-data-dashboard`.

## File Map

- Create `v2_ops/dashboard.py`: catálogo fechado dos períodos/marcos e cálculo puro do snapshot.
- Modify `v2_ops/store.py`: consulta read-only e temporal que entrega fatos existentes ao calculador.
- Modify `v2_ops/app.py`: serialização e endpoint autenticado `GET /ops/api/dashboard`.
- Replace `v2_ops/static/index.html`: shell visual com visão geral e detalhe do canvas.
- Replace `v2_ops/static/ops.js`: estado, fetch, filtros, renderização segura e preservação do canvas.
- Replace `v2_ops/static/ops.css`: linguagem visual aprovada e responsividade.
- Create `tests/test_v2_ops_dashboard.py`: fórmulas, janelas, deduplicação e ausência de inferência.
- Modify `tests/test_v2_ops_api.py`: autenticação, range, ETag, 503 e compatibilidade.
- Replace `tests/test_v2_ops_ui.py`: contrato estrutural e de ausência de efeitos do frontend.
- Modify `tests/test_v2_ops_no_effect_surface.py`: incluir o novo módulo na fronteira sem efeitos.
- Modify `docs/refactor/ACTIVE.md`: registrar evidência e estado da lane após cada commit verde.

---

### Task 1: Contrato e agregador puro do dashboard

**Files:**
- Create: `v2_ops/dashboard.py`
- Create: `tests/test_v2_ops_dashboard.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: `OpsExecutionView`, `OpsNodeView`, `NodeType`, `TraceCompleteness`.
- Produces: `DashboardRange.parse(value: str) -> DashboardRange`, `DashboardRecord`, `DashboardSnapshot`, `build_dashboard_snapshot(records, *, range_key, generated_at, table_limit=100)`.

- [ ] **Step 1: Write failing tests for closed ranges, formulas and empty state**

Create `tests/test_v2_ops_dashboard.py` with fixed UTC fixtures. Use exact expectations rather than snapshots:

```python
from datetime import datetime, timedelta, timezone

import pytest

from v2_ops.contracts import ExecutionStatus, NodeType, TraceCompleteness
from v2_ops.dashboard import (
    DashboardRange,
    DashboardRecord,
    build_dashboard_snapshot,
)
from v2_ops.store import OpsExecutionView

NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)


def execution(
    execution_id: str,
    *,
    lead_id: str,
    received_at: datetime,
    status: str,
    completed_at: datetime | None = None,
    completeness: TraceCompleteness = TraceCompleteness.COMPLETE_TRACE,
) -> OpsExecutionView:
    stored = ExecutionStatus.RUNNING if status == "running_stale" else ExecutionStatus(status)
    return OpsExecutionView(
        execution_id=execution_id,
        lead_id=lead_id,
        received_at=received_at,
        completed_at=completed_at,
        status=status,
        stored_status=stored,
        trace_completeness=completeness,
        current_node_id=None,
        terminal_reason=None,
    )


def record(view: OpsExecutionView, *node_types: NodeType) -> DashboardRecord:
    return DashboardRecord(
        execution=view,
        node_types=tuple(node_types),
        current_node_type=node_types[-1] if node_types else None,
        node_count=len(node_types),
    )


def test_empty_snapshot_uses_zero_counts_and_null_rates() -> None:
    snapshot = build_dashboard_snapshot((), range_key=DashboardRange.D7, generated_at=NOW)
    assert snapshot.metrics == {
        "executions": 0,
        "distinct_leads": 0,
        "in_progress": 0,
        "completed": 0,
        "failed": 0,
        "manual_review": 0,
        "technical_completion_rate": None,
        "average_terminal_duration_ms": None,
    }
    assert len(snapshot.execution_series) == 7
    assert {point["count"] for point in snapshot.execution_series} == {0}
    assert snapshot.executions == ()


def test_metrics_use_received_at_window_and_exact_technical_mean() -> None:
    records = (
        record(execution("a", lead_id="lead-1", received_at=NOW - timedelta(days=1), status="completed", completed_at=NOW - timedelta(days=1) + timedelta(seconds=2))),
        record(execution("b", lead_id="lead-1", received_at=NOW - timedelta(hours=2), status="failed", completed_at=NOW - timedelta(hours=2) + timedelta(seconds=4))),
        record(execution("c", lead_id="lead-2", received_at=NOW, status="running_stale")),
    )
    snapshot = build_dashboard_snapshot(records, range_key=DashboardRange.D7, generated_at=NOW)
    assert snapshot.metrics["executions"] == 3
    assert snapshot.metrics["distinct_leads"] == 2
    assert snapshot.metrics["in_progress"] == 1
    assert snapshot.metrics["completed"] == 1
    assert snapshot.metrics["failed"] == 1
    assert snapshot.metrics["technical_completion_rate"] == pytest.approx(100 / 3)
    assert snapshot.metrics["average_terminal_duration_ms"] == 3000


def test_ranges_are_closed_and_boundaries_are_inclusive() -> None:
    assert [item.value for item in DashboardRange] == ["24h", "7d", "30d"]
    with pytest.raises(ValueError, match="range"):
        DashboardRange.parse("today")
    edge = record(execution("edge", lead_id="lead", received_at=NOW - timedelta(hours=24), status="pending"))
    older = record(execution("older", lead_id="lead", received_at=NOW - timedelta(hours=24, microseconds=1), status="pending"))
    snapshot = build_dashboard_snapshot((edge, older), range_key=DashboardRange.H24, generated_at=NOW)
    assert snapshot.metrics["executions"] == 1
```

Also add tests for all six visible statuses, all three completeness values, 24/7/30 zero-filled buckets, top-ten node ranking and stable ordering.

- [ ] **Step 2: Run the focused tests and prove RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_dashboard.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'v2_ops.dashboard'`.

- [ ] **Step 3: Implement the closed contracts and pure calculator**

Create `v2_ops/dashboard.py` with exact immutable types and catalogs:

```python
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from v2_ops.contracts import NodeType, TraceCompleteness
from v2_ops.store import OpsExecutionView


class DashboardRange(str, Enum):
    H24 = "24h"
    D7 = "7d"
    D30 = "30d"

    @classmethod
    def parse(cls, value: str) -> "DashboardRange":
        if type(value) is not str:
            raise ValueError("range is outside the closed catalog")
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError("range is outside the closed catalog") from exc

    @property
    def duration(self) -> timedelta:
        return {
            DashboardRange.H24: timedelta(hours=24),
            DashboardRange.D7: timedelta(days=7),
            DashboardRange.D30: timedelta(days=30),
        }[self]

    @property
    def buckets(self) -> int:
        return {DashboardRange.H24: 24, DashboardRange.D7: 7, DashboardRange.D30: 30}[self]

    @property
    def bucket_duration(self) -> timedelta:
        return timedelta(hours=1) if self is DashboardRange.H24 else timedelta(days=1)


RESERVATION_NODES = frozenset({
    NodeType.CLOUDBEDS_RESERVATION_REQUEST,
    NodeType.CLOUDBEDS_RESERVATION_RESPONSE,
    NodeType.BOKUN_BOOKING_REQUEST,
    NodeType.BOKUN_BOOKING_RESPONSE,
    NodeType.LEDGER_RESERVATION,
})
PAYMENT_NODES = frozenset({
    NodeType.STRIPE_PRODUCT, NodeType.STRIPE_PRICE, NodeType.STRIPE_PAYMENT_LINK,
    NodeType.PIX_INSTRUCTION, NodeType.WISE_INSTRUCTION, NodeType.SETTLEMENT,
    NodeType.STRIPE_RECONCILIATION, NodeType.LEDGER_PAYMENT,
})
DELIVERY_NODES = frozenset({
    NodeType.PUBLIC_OUTBOX, NodeType.MANYCHAT_DELIVERY_REQUEST,
    NodeType.MANYCHAT_DELIVERY_RESPONSE, NodeType.LEDGER_PUBLIC_OUTBOX,
})
HANDOFF_NODES = frozenset({NodeType.HANDOFF_REQUEST, NodeType.HANDOFF_DELIVERY})


@dataclass(frozen=True, slots=True)
class DashboardRecord:
    execution: OpsExecutionView
    node_types: tuple[NodeType, ...]
    current_node_type: NodeType | None
    node_count: int


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    generated_at: datetime
    range_key: DashboardRange
    metrics: dict[str, int | float | None]
    execution_series: tuple[dict[str, object], ...]
    status_distribution: tuple[dict[str, object], ...]
    trace_distribution: tuple[dict[str, object], ...]
    milestones: tuple[dict[str, object], ...]
    top_node_types: tuple[dict[str, object], ...]
    executions: tuple[dict[str, object], ...]
```

Implement `build_dashboard_snapshot` so it:

1. rejects non-UTC `generated_at` and non-exact `DashboardRange`;
2. filters inclusively to `[generated_at - duration, generated_at]`;
3. computes the eight metrics exactly as the specification;
4. uses terminal statuses `completed`, `failed`, `manual_review` for mean duration;
5. emits all status/completeness categories even when zero;
6. deduplicates milestone presence with `set(record.node_types)`;
7. counts all node occurrences only for `top_node_types`;
8. emits at most 100 recent execution rows, sorted by `(received_at, execution_id)` descending;
9. includes only the table fields approved in section 7.3 of the spec.

Use `datetime` values internally; serialization stays in `app.py`.

- [ ] **Step 4: Complete milestone and no-inference tests**

Add a record containing repeated Stripe nodes and overlapping reservation, payment, delivery and handoff nodes. Assert each milestone equals one for that execution while top-node count preserves repetitions. Add a source scan assertion:

```python
def test_dashboard_module_has_no_commercial_or_text_inference() -> None:
    source = Path("v2_ops/dashboard.py").read_text(encoding="utf-8").casefold()
    for forbidden in ("input_summary", "output_summary", "chapada_leads", "revenue", "sentiment"):
        assert forbidden not in source
```

- [ ] **Step 5: Run GREEN and the boundary guard**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_dashboard.py tests/test_v2_ops_no_effect_surface.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Record evidence and commit**

Update the parallel lane in `docs/refactor/ACTIVE.md` with Task 1 command, exit code and commit intent, then:

```bash
git add v2_ops/dashboard.py tests/test_v2_ops_dashboard.py docs/refactor/ACTIVE.md
git diff --cached --check
git commit -m "feat(v2-ops): calculate existing-data dashboard"
```

---

### Task 2: Reader temporal e associação de nós

**Files:**
- Modify: `v2_ops/store.py`
- Modify: `tests/test_v2_ops_dashboard.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: `DashboardRange.duration`, existing schema, `_execution_view` stale derivation.
- Produces: `SQLiteOpsTraceReader.list_dashboard_records(*, start_at, end_at, now, stale_after) -> tuple[DashboardRecord, ...]`.

- [ ] **Step 1: Write failing integration tests against a real temporary SQLite**

Add tests that write executions/nodes with `SQLiteOpsTraceWriter`, then call:

```python
records = reader.list_dashboard_records(
    start_at=NOW - timedelta(days=7),
    end_at=NOW,
    now=NOW,
    stale_after=timedelta(minutes=5),
)
```

Assert:

- records outside the inclusive interval are absent;
- exact lower and upper boundaries are present;
- `running_stale` follows `_execution_view` behavior;
- `node_types`, `node_count` and `current_node_type` match persisted nodes;
- ordering is recent-first;
- no decryption/full payload read is required.

- [ ] **Step 2: Run RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_dashboard.py -k reader -q
```

Expected: failure because `list_dashboard_records` does not exist.

- [ ] **Step 3: Implement one read-only window query**

In `v2_ops/store.py`, import `DashboardRecord` locally inside the method to avoid a module-import cycle, validate both timestamps with `_require_utc`, require `start_at <= end_at == now`, then use one read connection:

```python
def list_dashboard_records(
    self,
    *,
    start_at: datetime,
    end_at: datetime,
    now: datetime,
    stale_after: timedelta = timedelta(minutes=5),
) -> tuple["DashboardRecord", ...]:
    from v2_ops.dashboard import DashboardRecord

    lower = _require_utc(start_at, "start_at")
    upper = _require_utc(end_at, "end_at")
    instant, threshold = self._time_inputs(now, stale_after)
    if lower > upper or upper != instant or upper - lower > timedelta(days=30):
        raise ValueError("dashboard window is outside the bounded range")
    try:
        with self._connect() as connection:
            execution_rows = connection.execute(
                "SELECT execution_id,lead_id,received_at,completed_at,status,"
                "trace_completeness,current_node_id,terminal_reason FROM executions "
                "WHERE received_at>=? AND received_at<=? "
                "ORDER BY received_at DESC,execution_id DESC",
                (format_utc_timestamp(lower), format_utc_timestamp(upper)),
            ).fetchall()
            node_rows = connection.execute(
                "SELECT n.execution_id,n.node_id,n.node_type "
                "FROM nodes n JOIN executions e ON e.execution_id=n.execution_id "
                "WHERE e.received_at>=? AND e.received_at<=? "
                "ORDER BY n.execution_id,n.ordinal,n.attempt,n.node_id",
                (format_utc_timestamp(lower), format_utc_timestamp(upper)),
            ).fetchall()
            grouped: dict[str, list[tuple[str, NodeType]]] = {}
            for row in node_rows:
                grouped.setdefault(row[0], []).append((row[1], NodeType(row[2])))
            result = []
            for row in execution_rows:
                execution = self._execution_view(connection, row, now=instant, stale_after=threshold)
                nodes = grouped.get(execution.execution_id, [])
                by_id = dict(nodes)
                result.append(DashboardRecord(
                    execution=execution,
                    node_types=tuple(node_type for _, node_type in nodes),
                    current_node_type=by_id.get(execution.current_node_id),
                    node_count=len(nodes),
                ))
            return tuple(result)
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise OpsTraceStoreError("operational trace store unavailable") from exc
```

Add a `TYPE_CHECKING` import for `DashboardRecord`; do not modify schema or writer methods.

- [ ] **Step 4: Prove reader calls do not mutate database bytes or row counts**

Use existing `database_bytes(path)` before/after with a WAL checkpoint performed before the baseline. Assert execution/node counts and file bytes remain unchanged after repeated reader calls.

- [ ] **Step 5: Run GREEN and store regression tests**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_dashboard.py tests/test_v2_ops_store.py tests/test_v2_ops_no_effect_surface.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Record evidence and commit**

```bash
git add v2_ops/store.py tests/test_v2_ops_dashboard.py docs/refactor/ACTIVE.md
git diff --cached --check
git commit -m "feat(v2-ops): read bounded dashboard windows"
```

---

### Task 3: Endpoint autenticado do dashboard

**Files:**
- Modify: `v2_ops/app.py`
- Modify: `tests/test_v2_ops_api.py`
- Modify: `tests/test_v2_ops_no_effect_surface.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: `DashboardRange.parse`, `SQLiteOpsTraceReader.list_dashboard_records`, `build_dashboard_snapshot`.
- Produces: authenticated `GET /ops/api/dashboard?range=24h|7d|30d` with ETag.

- [ ] **Step 1: Extend the API fixture with executions in multiple ranges**

Refactor `_build_client` only enough to accept a fixed `now` via monkeypatch of `v2_ops.app._now`. Add API tests:

```python
def test_dashboard_requires_session_and_rejects_open_range(tmp_path: Path) -> None:
    client, _, _ = _build_client(tmp_path)
    assert client.get("/ops/api/dashboard?range=7d").status_code == 401
    _login(client)
    invalid = client.get("/ops/api/dashboard?range=today")
    assert invalid.status_code == 422
    assert invalid.json() == {"status": "invalid_query"}


def test_dashboard_payload_is_real_bounded_and_etagged(tmp_path: Path) -> None:
    client, _, _ = _build_client(tmp_path)
    _login(client)
    response = client.get("/ops/api/dashboard?range=7d")
    assert response.status_code == 200
    payload = response.json()
    assert payload["range"] == "7d"
    assert set(payload) == {
        "generated_at", "range", "metrics", "execution_series",
        "status_distribution", "trace_distribution", "milestones",
        "top_node_types", "executions",
    }
    assert client.get(
        "/ops/api/dashboard?range=7d",
        headers={"If-None-Match": response.headers["etag"]},
    ).status_code == 304
```

Add a failing-reader test asserting exact `503 {"status":"source_unavailable"}` and route-matrix assertions proving only login/logout are POST.

- [ ] **Step 2: Run RED**

```bash
venv/bin/python -m pytest tests/test_v2_ops_api.py -k dashboard -q
```

Expected: 404 for the missing endpoint.

- [ ] **Step 3: Add serializer and GET route**

In `v2_ops/app.py`, import the dashboard interfaces and add:

```python
@app.get("/ops/api/dashboard")
async def dashboard(request: Request, range: str = "7d") -> Response:
    if type(require_api(request)) is not SessionClaims:
        return require_api(request)
    try:
        range_key = DashboardRange.parse(range)
        generated_at = _now()
        records = trace_reader.list_dashboard_records(
            start_at=generated_at - range_key.duration,
            end_at=generated_at,
            now=generated_at,
            stale_after=settings.stale_after,
        )
        snapshot = build_dashboard_snapshot(
            records,
            range_key=range_key,
            generated_at=generated_at,
        )
    except ValueError:
        return JSONResponse(status_code=422, content={"status": "invalid_query"})
    except OpsTraceStoreError:
        return JSONResponse(status_code=503, content={"status": "source_unavailable"})
    return _etag_response(_dashboard(snapshot), request)
```

Implement `_dashboard` using `_json_value` and exact public keys. Do not expose dataclass internals or enums directly.

- [ ] **Step 4: Run API and no-effect suites**

```bash
venv/bin/python -m pytest tests/test_v2_ops_api.py tests/test_v2_ops_no_effect_surface.py -q
```

Expected: all selected tests pass and route matrix contains no additional write method.

- [ ] **Step 5: Record evidence and commit**

```bash
git add v2_ops/app.py tests/test_v2_ops_api.py tests/test_v2_ops_no_effect_surface.py docs/refactor/ACTIVE.md
git diff --cached --check
git commit -m "feat(v2-ops): expose readonly dashboard snapshot"
```

---

### Task 4: Estrutura HTML da visão geral e detalhe

**Files:**
- Replace: `v2_ops/static/index.html`
- Replace: `tests/test_v2_ops_ui.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: IDs usados pelo JavaScript da Task 5.
- Produces: duas visões internas `overview-view` e `execution-view`, sem nova rota web.

- [ ] **Step 1: Write the failing UI structure contract**

Replace `tests/test_v2_ops_ui.py` with assertions for:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "v2_ops" / "static"


def assets() -> tuple[str, str, str]:
    return tuple((ROOT / name).read_text(encoding="utf-8") for name in ("index.html", "ops.js", "ops.css"))


def test_dashboard_shell_and_canvas_detail_exist() -> None:
    html, _, _ = assets()
    for token in (
        'id="overview-view"', 'id="execution-view"', 'id="range-select"',
        'id="kpi-grid"', 'id="execution-series"', 'id="status-distribution"',
        'id="trace-distribution"', 'id="milestones-chart"',
        'id="top-node-types"', 'id="execution-table-body"',
        'id="execution-mobile-list"', 'id="empty-state"',
        'id="execution-canvas"', 'id="input-panel"', 'id="output-panel"',
        'id="back-to-overview"',
    ):
        assert token in html
    assert "SOMENTE LEITURA" in html
    assert "Dados demonstrativos" not in html


def test_html_has_no_commercial_claims_or_write_controls() -> None:
    html, _, _ = assets()
    lowered = html.casefold()
    for forbidden in (
        "receita", "conversão", "novos leads", "qualificados", "interesse",
        "reservas confirmadas", "pagamento pago", "retry", "replay",
    ):
        assert forbidden not in lowered
```

Keep the existing assertions for Input/Output simultaneous visibility and all canvas controls.

- [ ] **Step 2: Run RED**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
```

Expected: missing overview IDs.

- [ ] **Step 3: Replace HTML with semantic shell**

Build the page in this order:

```html
<body>
  <div class="app-shell">
    <aside id="app-sidebar" class="app-sidebar">
      <strong>Maya Ops</strong>
      <span class="readonly">SOMENTE LEITURA</span>
      <nav aria-label="Navegação operacional">
        <button id="nav-overview" type="button" aria-current="page">Visão geral</button>
        <button id="nav-execution" type="button" disabled>Execução</button>
      </nav>
    </aside>
    <main class="main-content">
      <header class="dashboard-header">
        <div><p class="eyebrow">OPERAÇÃO MAYA V2</p><h1>Painel operacional</h1></div>
        <div><span id="live-state" aria-live="polite">Conectando…</span><form method="post" action="/ops/logout"><input type="hidden" name="csrf" value="{{CSRF}}"><button>Logout</button></form></div>
      </header>
      <section id="overview-view">
        <div class="overview-toolbar">
          <label>Período <select id="range-select">
            <option value="24h">Últimas 24 horas</option>
            <option value="7d" selected>Últimos 7 dias</option>
            <option value="30d">Últimos 30 dias</option>
          </select></label>
          <span id="generated-at">Ainda não atualizado</span>
        </div>
        <section id="dashboard-alert" class="alert" hidden aria-live="polite"></section>
        <section id="kpi-grid" class="kpi-grid" aria-label="Indicadores operacionais"></section>
        <section class="analytics-grid">
          <article><h2>Execuções no período</h2><div id="execution-series"></div></article>
          <article><h2>Estados atuais</h2><div id="status-distribution"></div></article>
          <article><h2>Completude do trace</h2><div id="trace-distribution"></div></article>
          <article><h2>Marcos registrados</h2><div id="milestones-chart"></div></article>
          <article><h2>Nós registrados</h2><div id="top-node-types"></div></article>
        </section>
        <section class="operations-panel">
          <div class="table-toolbar">
            <label>Lead ID <input id="lead-search" autocomplete="off"></label>
            <label>Estado <select id="status-filter"><option value="">Todos</option></select></label>
            <label>Completude <select id="completeness-filter"><option value="">Todas</option></select></label>
          </div>
          <div id="empty-state" hidden>Nenhuma execução registrada neste período.</div>
          <table><thead><tr><th>Lead</th><th>Execução</th><th>Recebida</th><th>Duração</th><th>Estado</th><th>Trace</th><th>Nó atual</th><th>Nós</th><th>Marcos</th><th>Motivo terminal</th></tr></thead><tbody id="execution-table-body"></tbody></table>
          <div id="execution-mobile-list" class="execution-mobile-list"></div>
        </section>
      </section>
      <section id="execution-view" hidden>
        <button id="back-to-overview" type="button">Voltar à visão geral</button>
        <div class="execution-detail">
          <section class="canvas-shell"><div class="canvas-toolbar"><strong id="canvas-title">Execução</strong><div><button id="zoom-out" type="button">−</button><button id="zoom-in" type="button">+</button><button id="fit-canvas" type="button">Fit</button></div></div><div id="execution-canvas" tabindex="0"><svg id="edges" aria-hidden="true"></svg><div id="nodes"></div></div></section>
          <aside class="inspector"><h2 id="node-title">Nenhum nó selecionado</h2><section id="input-panel"><strong>Input</strong><button id="load-full-input" type="button" hidden>Carregar permitido</button><pre id="input-summary">{}</pre><pre id="input-full" hidden></pre></section><section id="output-panel"><strong>Output</strong><button id="load-full-output" type="button" hidden>Carregar permitido</button><pre id="output-summary">{}</pre><pre id="output-full" hidden></pre></section><details><summary>Metadados técnicos</summary><pre id="metadata">{}</pre></details><details><summary>Erro sanitizado</summary><pre id="node-error">null</pre></details></aside>
        </div>
      </section>
    </main>
  </div>
  <script src="/ops/static/ops.js" defer></script>
</body>
```

The execution view must preserve `execution-canvas`, `edges`, `nodes`, `node-title`, summaries, full-content buttons, metadata, error, zoom and fit controls. Use `<template>` only for static decoration; no synthetic data in HTML.

- [ ] **Step 4: Run GREEN and API harness regression**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py tests/test_v2_ops_api.py::test_authenticated_list_detail_nodes_full_and_etag_are_read_only -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Record evidence and commit**

```bash
git add v2_ops/static/index.html tests/test_v2_ops_ui.py docs/refactor/ACTIVE.md
git diff --cached --check
git commit -m "feat(v2-ops): add operational dashboard shell"
```

---

### Task 5: Frontend real, filtros e preservação do canvas

**Files:**
- Replace: `v2_ops/static/ops.js`
- Modify: `tests/test_v2_ops_ui.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: `GET /ops/api/dashboard`, existing execution/nodes/full APIs, SSE `/ops/api/events`.
- Produces: DOM rendering, exact Lead ID/status/completeness filters, overview/detail navigation.

- [ ] **Step 1: Write failing static contracts for safe rendering and state**

Add assertions that `ops.js` contains the named functions:

```python
for name in (
    "loadDashboard", "renderKpis", "renderExecutionSeries",
    "renderDistribution", "renderMilestones", "renderTopNodeTypes",
    "renderExecutionTable", "filteredExecutions", "openExecution",
    "showOverview", "renderCanvas", "selectNode", "connectLive",
):
    assert f"function {name}(" in js or f"async function {name}(" in js
```

Assert:

```python
assert "textContent" in js
assert "innerHTML" not in js
assert 'method:' not in js
for forbidden in ("SERVICES", "RANGE_MODELS", "demo-", "payment_link", "request_handoff", "create_reservation"):
    assert forbidden not in js
```

- [ ] **Step 2: Run RED**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
```

Expected: required dashboard functions missing.

- [ ] **Step 3: Implement state and fetch semantics**

Use one explicit state object:

```javascript
const state = {
  range: "7d",
  snapshot: null,
  selectedExecution: null,
  nodes: [],
  selectedNode: null,
  statusFilter: "",
  completenessFilter: "",
  leadFilter: "",
  zoom: 1,
  connected: false,
};
```

Implement `getJSON` so `401` redirects to `/ops/login`, `503` throws a typed `source_unavailable` error, and successful JSON is returned. All rendering must use `document.createElement`, `textContent`, `replaceChildren`, `setAttribute` and safe style values derived from numeric API fields.

- [ ] **Step 4: Implement the eight cards and five chart regions**

Define exact card metadata in code, not data:

```javascript
const KPI_DEFINITIONS = [
  ["executions", "Execuções"],
  ["distinct_leads", "Leads distintos"],
  ["in_progress", "Em andamento"],
  ["completed", "Concluídas"],
  ["failed", "Falhas"],
  ["manual_review", "Revisão manual"],
  ["technical_completion_rate", "Conclusão técnica"],
  ["average_terminal_duration_ms", "Duração média terminal"],
];
```

Format null rates/durations as `—`; completion rate to one decimal plus `%`; duration in ms below 1s and seconds otherwise. Render:

- execution series as an accessible SVG created with `createElementNS`;
- status and trace distributions as labeled bars;
- milestones as overlapping categories with copy `execuções com marco`;
- top node types as ranked bars;
- zero state without inserting fake points or rows.

- [ ] **Step 5: Implement table filtering and mobile cards**

`filteredExecutions()` must apply:

- exact `lead_id === leadFilter` when non-empty;
- exact status when selected;
- exact trace completeness when selected.

Render identical facts in table/mobile card: lead ID, shortened execution ID with `title` full value, received timestamp, duration or `Não registrado`, status, completeness, current node type or `Não registrado`, node count, four booleans and terminal reason or `Não registrado`.

- [ ] **Step 6: Preserve and integrate the current canvas**

`openExecution(executionId)` switches views, fetches nodes, keeps `renderCanvas`, `selectNode`, `loadFull`, zoom and fit. `showOverview()` restores filters/range because it does not replace `state`. Full content absent displays `{status: "not_recorded"}`.

- [ ] **Step 7: Wire SSE and degraded state**

On `ready`, mark live. On `change`, reload the current dashboard range and refresh the open execution if one exists. On `degraded` or network error, keep the previous snapshot, show `Desconectado` and an alert. Do not retain the old two-second polling interval; SSE is already part of the existing deployed contract.

- [ ] **Step 8: Run GREEN and no-write scans**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py tests/test_v2_ops_api.py tests/test_v2_ops_no_effect_surface.py -q
```

Expected: all selected tests pass; no frontend write method or effect token.

- [ ] **Step 9: Record evidence and commit**

```bash
git add v2_ops/static/ops.js tests/test_v2_ops_ui.py docs/refactor/ACTIVE.md
git diff --cached --check
git commit -m "feat(v2-ops): render real operational metrics"
```

---

### Task 6: Visual aprovado, responsividade e browser contract

**Files:**
- Replace: `v2_ops/static/ops.css`
- Modify: `tests/test_v2_ops_ui.py`
- Create: `tests/browser/ops_dashboard_smoke.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: semantic IDs/classes from Tasks 4–5.
- Produces: desktop/mobile layout and executable browser smoke against a temporary local app.

- [ ] **Step 1: Add failing CSS contract**

Assert the approved palette and responsive structure:

```python
for color in ("#245634", "#173a27", "#f4e8d7", "#fffcf7", "#e4d5c0", "#66766b", "#e85f67"):
    assert color in css.casefold()
assert css.count("@media") >= 2
for token in (".kpi-grid", ".analytics-grid", ".operations-panel", ".execution-detail", ".execution-mobile-list"):
    assert token in css
```

- [ ] **Step 2: Run RED**

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
```

Expected: palette/layout assertions fail against the old dark CSS.

- [ ] **Step 3: Implement the approved visual system**

Define readable CSS variables and preserve accessible focus/reduced motion:

```css
:root {
  --brand-700:#245634;
  --brand-800:#173a27;
  --sand-100:#f4e8d7;
  --ivory-50:#fffcf7;
  --sand-300:#e4d5c0;
  --muted:#66766b;
  --coral:#e85f67;
  --success:#2f7d4a;
  --info:#327e8f;
  --warning:#c99135;
  --danger:#b7443e;
  --sidebar-width:248px;
}
```

Desktop: fixed sidebar, four-column KPI grid, two-column analytics, full table. At `max-width:1100px`: two KPI columns and one analytics column. At `max-width:720px`: off-canvas/compact sidebar, table hidden, mobile cards visible, full-width execution detail, no horizontal page overflow. Preserve canvas internal scrolling.

- [ ] **Step 4: Build a real browser smoke script**

Create `tests/browser/ops_dashboard_smoke.py` using Playwright only when invoked explicitly. The script must:

1. create a temporary Ops DB via writer with synthetic technical identifiers only;
2. start `uvicorn` on localhost with the temporary app;
3. log in through the real form;
4. verify eight KPI cards and real fixture counts;
5. change range and filters;
6. open one execution and verify canvas/input/output;
7. assert no page/console errors;
8. check desktop `1440x1000` and mobile `390x844` have no body overflow;
9. write screenshots under ignored `artifacts/ops-dashboard/`;
10. terminate the local server in `finally`.

No provider, ManyChat, reservation, payment or handoff adapter is imported or called.

- [ ] **Step 5: Run CSS tests and browser smoke**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
venv/bin/python tests/browser/ops_dashboard_smoke.py
```

Expected: pytest passes; browser smoke prints `ops_dashboard_smoke=PASS`, and both viewport screenshots exist.

If Playwright Chromium is absent, install only its local browser binary with:

```bash
venv/bin/python -m playwright install chromium
```

Then rerun the smoke. Do not replace browser evidence with static inspection.

- [ ] **Step 6: Record evidence and commit**

```bash
git add v2_ops/static/ops.css tests/test_v2_ops_ui.py tests/browser/ops_dashboard_smoke.py docs/refactor/ACTIVE.md
git diff --cached --check
git commit -m "feat(v2-ops): apply approved dashboard visual"
```

Do not add generated screenshots to Git.

---

### Task 7: Full regression, immutable-scope audit and handoff

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Create: `docs/superpowers/reports/2026-08-21-maya-ops-existing-data-dashboard-verification.md`

**Interfaces:**
- Consumes: all previous task commits.
- Produces: auditable verification report and implementation-ready branch; no deploy.

- [ ] **Step 1: Run the complete V2 Ops suite fresh**

```bash
venv/bin/python -m pytest tests/test_v2_ops_*.py -q
```

Expected: zero failures/errors/skips that hide required behavior. Record exact passed count and duration from real output.

- [ ] **Step 2: Run repository boundary and diff audits**

```bash
git diff --check acfd5d6c1f7ecfef2bebf875a8e5a4dac37da265..HEAD
git diff --name-only acfd5d6c1f7ecfef2bebf875a8e5a4dac37da265..HEAD
venv/bin/python -m pytest tests/test_v2_ops_no_effect_surface.py tests/test_v2_ops_deploy_contract.py -q
```

The changed-file list must be limited to:

```text
v2_ops/dashboard.py
v2_ops/store.py
v2_ops/app.py
v2_ops/static/index.html
v2_ops/static/ops.js
v2_ops/static/ops.css
tests/test_v2_ops_dashboard.py
tests/test_v2_ops_api.py
tests/test_v2_ops_ui.py
tests/test_v2_ops_no_effect_surface.py
tests/browser/ops_dashboard_smoke.py
docs/refactor/ACTIVE.md
docs/superpowers/specs/2026-08-21-maya-ops-existing-data-dashboard-design.md
docs/superpowers/plans/2026-08-21-maya-ops-existing-data-dashboard.md
docs/superpowers/reports/2026-08-21-maya-ops-existing-data-dashboard-verification.md
```

Any agent runtime, prompt, skill, adapter, provider, deployment or V3 path is a blocker; revert it before proceeding.

- [ ] **Step 3: Prove the API surface remains read-only**

Run a focused script/test that inspects the `.routes` attribute of the `FastAPI` object returned by `create_ops_app(settings, reader=reader)` and asserts:

```python
approved_posts = {"/ops/login", "/ops/logout"}
assert {route.path for route in app.routes if "POST" in (route.methods or set())} == approved_posts
assert not any(method in (route.methods or set()) for route in app.routes for method in {"PUT", "PATCH", "DELETE"})
```

Also call every new dashboard GET against a temporary DB and compare execution/node row counts before/after.

- [ ] **Step 4: Re-run the browser smoke from the final tree**

```bash
venv/bin/python tests/browser/ops_dashboard_smoke.py
```

Expected: `ops_dashboard_smoke=PASS`, zero page/console errors and zero body overflow at both viewports.

- [ ] **Step 5: Write the verification report with actual evidence**

Create `docs/superpowers/reports/2026-08-21-maya-ops-existing-data-dashboard-verification.md` containing:

- branch, base and final HEAD;
- exact changed-file list;
- each command, exit code, pass count and duration;
- screenshot paths and SHA-256 hashes;
- API route-matrix result;
- database before/after immutability result;
- explicit confirmation that no agent/instrumentation/provider/deploy file changed;
- residual risks: metrics reflect only records currently present in Ops and carry no commercial meaning;
- decision `IMPLEMENTATION VERIFIED — NOT DEPLOYED` only if every gate passed.

Do not write anticipated counts or hashes; copy only fresh tool output.

- [ ] **Step 6: Update ACTIVE and commit verification evidence**

```bash
git add docs/refactor/ACTIVE.md docs/superpowers/reports/2026-08-21-maya-ops-existing-data-dashboard-verification.md
git diff --cached --check
git commit -m "docs(v2-ops): verify existing-data dashboard"
```

- [ ] **Step 7: Final post-commit verification**

```bash
git status --short --branch
git show -s --format='COMMIT=%H%nTREE=%T%nSUBJECT=%s'
venv/bin/python -m pytest tests/test_v2_ops_*.py -q
```

Expected: clean worktree and full V2 Ops suite green. Report the actual commit and evidence to Carlos. Stop before push, PR, image build, deploy, restart or production smoke; each requires separate authorization.
