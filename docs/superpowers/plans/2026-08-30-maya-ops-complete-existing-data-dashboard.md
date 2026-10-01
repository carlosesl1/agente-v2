# Maya Ops Complete Existing-Data Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformar o Maya Ops em um dashboard autenticado e estritamente read-only com Visão por lead, Execuções, Reservas, Pagamentos, Handoffs e exportações CSV, sem alterar o agente ou qualquer registro comercial.

**Architecture:** O serviço `v2-ops` continuará usando o store sanitizado de traces e ganhará um reader próprio para um catálogo fechado de SQLite comerciais montados em `/data/records:ro`. O reader materializa DTOs imutáveis e joins por identidade exata; a aplicação expõe somente projeções allowlisted e CSVs autenticados; o frontend vanilla usa seis views sem criar qualquer superfície de efeito. A publicação recria exclusivamente o serviço `v2-ops` e preserva a release `c228148e110a` como rollback.

**Tech Stack:** Python 3.12, stdlib `sqlite3`/`csv`/`dataclasses`, FastAPI 0.133.1, JavaScript e CSS sem framework, pytest, Playwright 1.55.0 em Chromium via Docker, Docker Compose.

## Global Constraints

- Fonte de produto: `docs/superpowers/specs/2026-08-30-maya-ops-complete-existing-data-dashboard-design.md`.
- Worktree obrigatório: `/home/ubuntu/agente-v2/.worktrees/maya-ops-existing-data-dashboard`; branch `feature/maya-ops-existing-data-dashboard`.
- Somente `v2-ops` pode mudar ou ser recriado; não editar nem reiniciar API, worker, router, agente, prompts, tools, providers, ManyChat ou V3.
- Os bancos comerciais são abertos somente com URI `mode=ro`, `PRAGMA query_only=ON`, catálogo fechado de arquivos/tabelas/colunas e sem seguir symlinks.
- O browser nunca recebe SQL, paths de banco, payload bruto de webhook, ciphertext, nonce, credenciais, tokens de claim/lease/fence, prompts ou URLs assinadas.
- Joins usam apenas IDs persistidos idênticos; são proibidos joins por horário, valor, telefone, similaridade textual ou inferência.
- Iniciação Stripe/link preparado não significa pagamento liquidado; ausência de settlement nunca vira `R$ 0,00` ou `Pago`.
- `booking_id` Bókun e `reservation_id` Cloudbeds só recebem esses rótulos quando presentes nas respectivas tabelas de auditoria.
- Nenhuma rota POST/PUT/PATCH/DELETE nova; login/logout continuam sendo os únicos POSTs.
- Listas públicas têm máximo de 200 registros e `truncated=true` quando aplicável; cache máximo de 2 segundos e ETag sobre o corpo público exato.
- CSV usa UTF-8 com BOM, RFC 4180, colunas fechadas e neutralização de valores iniciados por `=`, `+`, `-` ou `@`.
- O DOM permanece safe: sem handlers inline, `innerHTML`, `insertAdjacentHTML`, `eval`, URLs externas, estilos de dados inline ou controles de efeito.
- A timeline permanece `li.timeline-item > button.execution-step`, com `data-node-id`, círculo numerado e nome factual, ordem da API, seleção, Input/Output e fallback one-based; sem graph ou zoom.
- Chromium obrigatório: imagem exata `mcr.microsoft.com/playwright:v1.55.0-noble`, ID `sha256:09d59668831815b8b1e3862edae613fb450173d68ba2f4e3fc5fbb7be76e0e7d`, exatamente um worker, zero skips, `1440x1000` e `390x844`, zero `console.error`/page/request errors.
- Root filesystem e os dois mounts continuam read-only; nenhum arquivo SQLite/WAL/SHM pode mudar durante testes dark ou produção.
- TDD estrito por tarefa: teste falha pela ausência/erro exato, implementação mínima, suíte verde, commit e revisão independente.

---

## File Map

- Create `v2_ops/records.py`: catálogo fechado, conexões SQLite read-only, validação de schema, DTOs públicos, parsing estruturado e snapshot multi-store.
- Create `v2_ops/records_csv.py`: datasets CSV fechados e neutralização de fórmulas.
- Create `tests/v2_ops_records_fixture.py`: criador de fixtures SQLite reais usado pelos testes e pelo smoke; nunca importado por produção.
- Create `tests/test_v2_ops_records.py`: contratos do reader, joins, semântica comercial e invariância física.
- Create `tests/test_v2_ops_records_api.py`: auth, ETag/cache, API, CSV, limites e erros sanitizados.
- Modify `v2_ops/settings.py`: `records_path: Path | None` e `V2_OPS_RECORDS_PATH`.
- Modify `v2_ops/app.py`: injeção do reader, cache/epochs server-side, rotas GET e SSE combinada.
- Modify `v2_ops/static/index.html`: seis views semânticas, lead detail e botões GET de exportação.
- Modify `v2_ops/static/ops.js`: estado/navegação, renderização safe-DOM, drill-down por lead e concorrência.
- Modify `v2_ops/static/ops.css`: refino conservador, novas tabelas/cards e timeline mobile sem scroll interno.
- Modify `tests/test_v2_ops_ui.py`: inventário DOM fechado, JS adversarial e tokens CSS.
- Modify `tests/browser/ops_dashboard_smoke.py`: fixture comercial e navegação real desktop/mobile/download.
- Modify `deploy/v2-ops/compose.ops.yaml`, `deploy/v2-ops/env.example`, `Dockerfile.v2-ops`, `tests/test_v2_ops_deploy_contract.py`: segunda montagem read-only e imagem mínima.
- Modify `docs/refactor/ACTIVE.md` e `.superpowers/sdd/progress.md`: evidência e recuperação de progresso.

---

### Task 1: Settings, deploy contract and records fixture

**Files:**
- Modify: `v2_ops/settings.py`
- Modify: `deploy/v2-ops/compose.ops.yaml`
- Modify: `deploy/v2-ops/env.example`
- Modify: `Dockerfile.v2-ops`
- Modify: `tests/test_v2_ops_deploy_contract.py`
- Create: `tests/v2_ops_records_fixture.py`
- Test: `tests/test_v2_ops_settings.py`
- Test: `tests/test_v2_ops_deploy_contract.py`

**Interfaces:**
- Consumes: `OpsWebSettings` e o Compose existentes.
- Produces: `OpsWebSettings.records_path: Path | None`; env `V2_OPS_RECORDS_PATH`; host env `V2_OPS_RECORDS_DATA_DIR`; fixture `write_records_fixture(root: Path) -> RecordsFixtureIdentity`.

- [ ] **Step 1: Write failing settings and deploy tests**

Adicionar em `tests/test_v2_ops_settings.py`:

```python
def test_records_path_is_optional_but_absolute_when_present(valid_env: dict[str, str]) -> None:
    without = OpsWebSettings.from_env(valid_env)
    assert without.records_path is None
    valid_env["V2_OPS_RECORDS_PATH"] = "/data/records"
    assert OpsWebSettings.from_env(valid_env).records_path == Path("/data/records")
    valid_env["V2_OPS_RECORDS_PATH"] = "records"
    with pytest.raises(ValueError, match="records path must be absolute"):
        OpsWebSettings.from_env(valid_env)
```

Atualizar `tests/test_v2_ops_deploy_contract.py` para exigir exatamente duas montagens e os dois nomes novos:

```python
assert set(service["volumes"]) == {
    "${V2_OPS_DATA_DIR:-./data}:/data/ops:ro",
    "${V2_OPS_RECORDS_DATA_DIR:?required}:/data/records:ro",
}
assert environment["V2_OPS_RECORDS_PATH"] == "/data/records"
assert "V2_OPS_RECORDS_DATA_DIR" in env_names
assert "COPY v2_ops /app/v2_ops" in dockerfile
for forbidden_copy in ("reservation_boundary", "reservation_domain", "reservation_execution", "v2_application", "v2_host"):
    assert f"COPY {forbidden_copy}" not in dockerfile
```

- [ ] **Step 2: Verify RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_settings.py tests/test_v2_ops_deploy_contract.py -q
```

Expected: FAIL porque `records_path`, `V2_OPS_RECORDS_PATH` e a segunda montagem ainda não existem.

- [ ] **Step 3: Add the closed settings contract**

Em `v2_ops/settings.py`:

```python
# Adicionar após config_fingerprint na dataclass:
records_path: Path | None = None

# Adicionar ao final de __post_init__:
if self.records_path is not None and (
    not isinstance(self.records_path, Path) or not self.records_path.is_absolute()
):
    raise ValueError("records path must be absolute when configured")

# Adicionar em from_env(), imediatamente após `source = os.environ if env is None else env`:
records_raw = source.get("V2_OPS_RECORDS_PATH", "")

# Adicionar como argumento do construtor retornado por from_env():
records_path=Path(records_raw) if records_raw else None,
```

Atualizar Compose/env conforme os valores exatos dos testes. `Dockerfile.v2-ops` mantém `COPY v2_ops /app/v2_ops`; apenas cria `/data/records` junto de `/data/ops`.

- [ ] **Step 4: Create real SQLite fixture writer**

Criar `tests/v2_ops_records_fixture.py` com uma API única:

```python
@dataclass(frozen=True, slots=True)
class RecordsFixtureIdentity:
    lead_id: str
    execution_id: str
    command_id: str
    workflow_id: str
    payment_id: str


def write_records_fixture(root: Path) -> RecordsFixtureIdentity:
    root.mkdir(parents=True, exist_ok=True)
    identity = RecordsFixtureIdentity(
        lead_id="manychat:lead-records-001",
        execution_id="event-records-001",
        command_id="command-records-001",
        workflow_id="workflow-records-001",
        payment_id="payment-records-001",
    )
    # Criar os nove arquivos comerciais allowlisted com schemas mínimos que reproduzem
    # exatamente as colunas autenticadas pelo store ativo. Inserir um lead,
    # diálogo, fatos, reserva confirmada com provider_reference genérica,
    # iniciação Stripe/link aceito e zero settlement/handoff.
    return identity
```

O helper usa somente `sqlite3`, `json.dumps(payload, sort_keys=True, separators=(",", ":"))` e timestamps UTC fixos. Ele não importa módulos de produção.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_settings.py tests/test_v2_ops_deploy_contract.py -q
venv/bin/python -m compileall -q v2_ops tests/v2_ops_records_fixture.py
```

Expected: PASS; compileall exit 0.

- [ ] **Step 6: Commit**

```bash
git add v2_ops/settings.py deploy/v2-ops/compose.ops.yaml deploy/v2-ops/env.example Dockerfile.v2-ops tests/test_v2_ops_settings.py tests/test_v2_ops_deploy_contract.py tests/v2_ops_records_fixture.py
git commit -m "feat(v2-ops): configure read-only records sources"
```

---

### Task 2: Closed multi-SQLite records reader and factual projection

**Files:**
- Create: `v2_ops/records.py`
- Create: `tests/test_v2_ops_records.py`
- Modify: `tests/v2_ops_records_fixture.py`

**Interfaces:**
- Consumes: `records_path` and fixture files from Task 1; trace execution summaries are supplied to `snapshot()` rather than re-read from the trace DB.
- Produces: `RecordsSourceError`; immutable DTOs `LeadSummary`, `LeadDetail`, `ReservationRecord`, `PaymentRecord`, `HandoffRecord`, `RecordsSnapshot`; `SQLiteRecordsReader(root: Path)`; `snapshot(*, executions: tuple[dict[str, object], ...], generated_at: datetime, limit: int = 200) -> RecordsSnapshot`; `lead_detail(lead_id: str, *, executions: tuple[dict[str, object], ...], generated_at: datetime) -> LeadDetail | None`; `change_token() -> str`.

- [ ] **Step 1: Write RED tests for path/schema/read-only guarantees**

Criar `tests/test_v2_ops_records.py` com:

```python
def test_reader_accepts_only_closed_root_files_and_query_only(tmp_path: Path) -> None:
    write_records_fixture(tmp_path)
    reader = SQLiteRecordsReader(tmp_path)
    snapshot = reader.snapshot(executions=(), generated_at=NOW)
    assert snapshot.generated_at == NOW
    assert reader.allowed_files == frozenset({
        "inbox.sqlite3", "v2-private-customer.sqlite3", "v2-boundary.sqlite3",
        "v2-execution.sqlite3", "v2-payment-initiation.sqlite3", "v2-followup.sqlite3",
        "v2-bokun-audit.sqlite3", "v2-cloudbeds-audit.sqlite3", "v2-public-outbox.sqlite3",
    })


def test_reader_rejects_symlink_and_schema_drift(tmp_path: Path) -> None:
    write_records_fixture(tmp_path)
    target = tmp_path / "inbox.sqlite3"
    target.rename(tmp_path / "inbox-real.sqlite3")
    target.symlink_to(tmp_path / "inbox-real.sqlite3")
    with pytest.raises(RecordsSourceError, match="records source unavailable"):
        SQLiteRecordsReader(tmp_path).snapshot(executions=(), generated_at=NOW)


def test_repeated_reads_preserve_each_db_wal_shm_byte_for_byte(tmp_path: Path) -> None:
    write_records_fixture(tmp_path)
    before = physical_snapshot(tmp_path)
    reader = SQLiteRecordsReader(tmp_path)
    for _ in range(3):
        reader.snapshot(executions=(), generated_at=NOW)
    assert physical_snapshot(tmp_path) == before
```

- [ ] **Step 2: Verify RED for the missing reader**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_records.py -k 'closed_root or symlink or byte_for_byte' -q
```

Expected: collection/import FAIL porque `v2_ops.records` não existe.

- [ ] **Step 3: Implement immutable contracts and physical reader boundary**

Criar em `v2_ops/records.py`:

```python
class RecordsSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LeadSummary:
    lead_id: str
    first_activity_at: datetime | None
    last_activity_at: datetime | None
    inbound_event_count: int
    dialogue_turn_count: int
    customer_fact_count: int
    execution_count: int
    reservation_count: int
    payment_initiation_count: int
    settled_payment_count: int
    handoff_count: int
    latest_execution_status: str | None


@dataclass(frozen=True, slots=True)
class RecordsSnapshot:
    generated_at: datetime
    leads: tuple[LeadSummary, ...]
    reservations: tuple[ReservationRecord, ...]
    payments: tuple[PaymentRecord, ...]
    handoffs: tuple[HandoffRecord, ...]
    truncated: bool


class SQLiteRecordsReader:
    _FILES = frozenset({
        "inbox.sqlite3",
        "v2-private-customer.sqlite3",
        "v2-boundary.sqlite3",
        "v2-execution.sqlite3",
        "v2-payment-initiation.sqlite3",
        "v2-followup.sqlite3",
        "v2-bokun-audit.sqlite3",
        "v2-cloudbeds-audit.sqlite3",
        "v2-public-outbox.sqlite3",
    })

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("records root must be an absolute pathlib.Path")
        self._root = root

    @property
    def allowed_files(self) -> frozenset[str]:
        return self._FILES

    @contextmanager
    def _connect(self, name: str) -> Iterator[sqlite3.Connection]:
        path = self._root / name
        if name not in self._FILES or path.is_symlink() or not path.is_file():
            raise RecordsSourceError("records source unavailable")
        if path.resolve().parent != self._root.resolve():
            raise RecordsSourceError("records source unavailable")
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise RecordsSourceError("records source unavailable")
            yield connection
        except (sqlite3.Error, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise RecordsSourceError("records source unavailable") from exc
        finally:
            connection.close()
```

Definir `_REQUIRED_COLUMNS` como mapping literal de arquivo → tabela → `frozenset[str]`; autenticar via `PRAGMA table_info(<nome literal>)` antes de qualquer SELECT. Todos os SQLs são constantes internas sem interpolação de entrada.

- [ ] **Step 4: Write RED semantic tests**

Adicionar testes:

```python
def test_snapshot_joins_only_exact_ids_and_labels_outcomes_conservatively(tmp_path: Path) -> None:
    identity = write_records_fixture(tmp_path)
    snapshot = SQLiteRecordsReader(tmp_path).snapshot(
        executions=({"lead_id": identity.lead_id, "execution_id": identity.execution_id,
                     "received_at": "2026-08-30T12:00:00Z", "status": "completed"},),
        generated_at=NOW,
    )
    reservation = snapshot.reservations[0]
    assert reservation.lead_id == identity.lead_id
    assert reservation.status_label == "Confirmada"
    assert reservation.provider_reference is not None
    assert reservation.bokun_booking_id is None
    assert reservation.cloudbeds_reservation_id is None


def test_completed_stripe_initiation_is_not_settlement(tmp_path: Path) -> None:
    identity = write_records_fixture(tmp_path)
    snapshot = SQLiteRecordsReader(tmp_path).snapshot(executions=(), generated_at=NOW)
    payment = next(item for item in snapshot.payments if item.kind == "initiation")
    assert payment.lead_id == identity.lead_id
    assert payment.status_label == "Link Stripe preparado"
    assert payment.amount_paid is None
    assert payment.settled_at is None
    assert not any(item.kind == "settlement" for item in snapshot.payments)


def test_similar_ids_never_join(tmp_path: Path) -> None:
    identity = write_records_fixture(tmp_path)
    inject_unlinked_payment(tmp_path, payment_id=identity.payment_id + "-other")
    snapshot = SQLiteRecordsReader(tmp_path).snapshot(executions=(), generated_at=NOW)
    unlinked = next(item for item in snapshot.payments if item.payment_id.endswith("-other"))
    assert unlinked.lead_id is None
```

- [ ] **Step 5: Verify semantic RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_records.py -k 'joins_only or initiation_is_not or similar_ids' -q
```

Expected: FAIL porque `snapshot()` ainda não projeta os contratos.

- [ ] **Step 6: Implement structured parsing and exact-ID joins**

Implementar helpers fechados:

```python
def _object_json(value: object) -> dict[str, object]:
    if type(value) is not str:
        raise RecordsSourceError("records source unavailable")
    parsed = json.loads(value)
    if type(parsed) is not dict:
        raise RecordsSourceError("records source unavailable")
    return parsed


def _exact_text(value: object) -> str | None:
    return value if type(value) is str and value != "" and "\x00" not in value else None


def _reservation_status(certainty: str | None, normalized_status: str | None, terminal: bool) -> str:
    if certainty == "effect_confirmed" and normalized_status == "confirmed":
        return "Confirmada"
    if terminal:
        return "Outcome registrado"
    return "Em preparação"
```

O `snapshot()` lê as tabelas allowlisted, cria índices em memória por `lead_id`, `command_id`, `workflow_id`, `payment_id` e `reservation_anchor_id`, e só une chaves por igualdade Python exata. Ordenar listas por timestamp UTC e ID como desempate. Aplicar `limit` após a ordenação e definir `truncated` se qualquer coleção exceder o limite.

- [ ] **Step 7: Close error and edge cases**

Adicionar e implementar testes para:

```python
@pytest.mark.parametrize("mutation", [drop_required_column, corrupt_structured_json])
def test_schema_or_json_failure_is_sanitized(tmp_path: Path, mutation: Callable[[Path], None]) -> None:
    write_records_fixture(tmp_path)
    mutation(tmp_path)
    with pytest.raises(RecordsSourceError) as failure:
        SQLiteRecordsReader(tmp_path).snapshot(executions=(), generated_at=NOW)
    assert str(failure.value) == "records source unavailable"


def test_all_history_union_includes_leads_from_each_authorized_source(tmp_path: Path) -> None:
    write_records_fixture(tmp_path)
    snapshot = SQLiteRecordsReader(tmp_path).snapshot(
        executions=({"lead_id": "trace-only", "execution_id": "trace-event",
                     "received_at": "2026-08-30T10:00:00Z", "status": "failed"},),
        generated_at=NOW,
    )
    assert {lead.lead_id for lead in snapshot.leads} >= {"trace-only", "manychat:lead-records-001"}
```

- [ ] **Step 8: Verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_records.py -q
venv/bin/python -m compileall -q v2_ops/records.py
```

Expected: PASS; compileall exit 0.

- [ ] **Step 9: Commit**

```bash
git add v2_ops/records.py tests/test_v2_ops_records.py tests/v2_ops_records_fixture.py
git commit -m "feat(v2-ops): project factual lead records read only"
```

---

### Task 3: Authenticated records API, ETag cache and CSV exports

**Files:**
- Create: `v2_ops/records_csv.py`
- Create: `tests/test_v2_ops_records_api.py`
- Modify: `v2_ops/app.py`
- Modify: `tests/test_v2_ops_api.py`

**Interfaces:**
- Consumes: `SQLiteRecordsReader`, DTOs and existing `SQLiteOpsTraceReader`.
- Produces: optional `records_reader` injection in `create_ops_app`; GET `/ops/api/records`, GET `/ops/api/leads/{lead_id}`, GET `/ops/api/exports/{dataset}.csv`; combined SSE fingerprint.

- [ ] **Step 1: Write API RED tests**

Criar `tests/test_v2_ops_records_api.py` com helper que usa ambos os readers reais e:

```python
def test_records_auth_precedes_parse_and_read(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SQLiteRecordsReader, "snapshot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read")))
    response = client.get("/ops/api/records?unknown=1")
    assert response.status_code == 401
    assert response.json() == {"status": "authentication_required"}


def test_records_and_lead_detail_are_allowlisted_etagged_and_bounded(authenticated_client: TestClient) -> None:
    records = authenticated_client.get("/ops/api/records")
    assert records.status_code == 200
    assert set(records.json()) == {"generated_at", "leads", "reservations", "payments", "handoffs", "truncated"}
    assert authenticated_client.get("/ops/api/records", headers={"If-None-Match": records.headers["etag"]}).status_code == 304
    lead_id = records.json()["leads"][0]["lead_id"]
    detail = authenticated_client.get(f"/ops/api/leads/{quote(lead_id, safe='')}")
    assert detail.status_code == 200
    assert set(detail.json()) == {"lead"}
    assert "claim_token" not in detail.text and "ciphertext" not in detail.text


def test_unknown_query_dataset_and_source_failures_are_closed(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert authenticated_client.get("/ops/api/records?range=7d").status_code == 422
    assert authenticated_client.get("/ops/api/exports/unknown.csv").status_code == 422
    monkeypatch.setattr(SQLiteRecordsReader, "snapshot", lambda *a, **k: (_ for _ in ()).throw(RecordsSourceError("private cause")))
    response = authenticated_client.get("/ops/api/records")
    assert response.status_code == 503
    assert response.json() == {"status": "records_source_unavailable"}
    assert "private cause" not in response.text
```

- [ ] **Step 2: Verify API RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_records_api.py -q
```

Expected: FAIL/404 porque as rotas e injeção não existem.

- [ ] **Step 3: Add JSON projection and two-second records cache**

Modificar assinatura:

```python
def create_ops_app(
    settings: OpsWebSettings,
    *,
    reader: SQLiteOpsTraceReader | None = None,
    records_reader: SQLiteRecordsReader | None = None,
) -> FastAPI:
```

Construir `commercial_reader = records_reader or (SQLiteRecordsReader(settings.records_path) if settings.records_path else None)`. Extrair execuções all-history do trace por paginação interna limitada a 200 e projetar somente campos públicos necessários. Usar `records_cache: tuple[datetime, dict[str, object], RecordsSnapshot] | None` e `asyncio.Lock`; TTL exato de 2 segundos.

Rotas:

```python
@app.get("/ops/api/records")
async def records(request: Request) -> Response:
    authenticated = require_api(request)
    if type(authenticated) is not SessionClaims:
        return authenticated
    if request.query_params:
        return JSONResponse(status_code=422, content={"status": "invalid_query"})
    try:
        payload, _ = await records_snapshot()
    except RecordsSourceError:
        return JSONResponse(status_code=503, content={"status": "records_source_unavailable"})
    return _etag_response(payload, request)


@app.get("/ops/api/leads/{lead_id}")
async def lead(lead_id: str, request: Request) -> Response:
    authenticated = require_api(request)
    if type(authenticated) is not SessionClaims:
        return authenticated
    if request.query_params or not valid_public_id(lead_id):
        return JSONResponse(status_code=422, content={"status": "invalid_query"})
    try:
        _, snapshot = await records_snapshot()
        detail = commercial_reader.lead_detail(
            lead_id,
            executions=trace_execution_rows(),
            generated_at=snapshot.generated_at,
        )
    except RecordsSourceError:
        return JSONResponse(status_code=503, content={"status": "records_source_unavailable"})
    if detail is None:
        return JSONResponse(status_code=404, content={"status": "not_found"})
    return _etag_response({"lead": _public_value(detail)}, request)
```

- [ ] **Step 4: Write CSV RED tests**

```python
@pytest.mark.parametrize("dataset", ["leads", "executions", "reservations", "payments", "handoffs"])
def test_closed_csv_datasets_are_bom_prefixed_and_downloadable(authenticated_client: TestClient, dataset: str) -> None:
    response = authenticated_client.get(f"/ops/api/exports/{dataset}.csv")
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]


def test_csv_formula_cells_are_neutralized_and_lead_history_requires_id(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/ops/api/exports/lead-history.csv")
    assert response.status_code == 422
    response = authenticated_client.get("/ops/api/exports/lead-history.csv?lead_id=%3Ddanger")
    assert response.status_code in {200, 404}
    if response.status_code == 200:
        assert b"'=danger" in response.content
```

- [ ] **Step 5: Verify CSV RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_records_api.py -k csv -q
```

Expected: FAIL/404 porque o exporter não existe.

- [ ] **Step 6: Implement closed CSV exporter**

Criar `v2_ops/records_csv.py`:

```python
_DATASETS = frozenset({"leads", "executions", "reservations", "payments", "handoffs", "lead-history"})


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def render_csv(dataset: str, *, snapshot: RecordsSnapshot, lead: LeadDetail | None,
               executions: tuple[dict[str, object], ...]) -> bytes:
    if dataset not in _DATASETS:
        raise ValueError("invalid dataset")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_columns(dataset), extrasaction="raise", lineterminator="\r\n")
    writer.writeheader()
    for row in _rows(dataset, snapshot=snapshot, lead=lead, executions=executions):
        writer.writerow({key: _cell(row.get(key)) for key in _columns(dataset)})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
```

Adicionar rota `exports`; somente `lead-history` aceita exatamente `lead_id`; os demais rejeitam qualquer query.

- [ ] **Step 7: Extend SSE fingerprint without a second EventSource**

Atualizar `/ops/api/events`: computar `records_reader.change_token()` quando configurado e concatená-lo ao fingerprint técnico. Capturar `RecordsSourceError` como evento `degraded` com `records_source_unavailable`, sem encerrar o stream. Não criar endpoint SSE adicional.

- [ ] **Step 8: Verify GREEN and route matrix**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_records_api.py tests/test_v2_ops_api.py -q
venv/bin/python - <<'PY'
from v2_ops.app import create_ops_app
# Os testes da suíte autenticam a matriz; compileall garante imports.
print('records_api_contract=PASS')
PY
venv/bin/python -m compileall -q v2_ops
```

Expected: PASS and `records_api_contract=PASS`.

- [ ] **Step 9: Commit**

```bash
git add v2_ops/app.py v2_ops/records_csv.py tests/test_v2_ops_records_api.py tests/test_v2_ops_api.py
git commit -m "feat(v2-ops): expose factual records and CSV exports"
```

---

### Task 4: Semantic six-view shell and closed DOM contract

**Files:**
- Modify: `v2_ops/static/index.html`
- Modify: `tests/test_v2_ops_ui.py`

**Interfaces:**
- Consumes: existing dashboard and drawer IDs.
- Produces: nav buttons `nav-overview`, `nav-leads`, `nav-executions`, `nav-reservations`, `nav-payments`, `nav-handoffs`; views with matching `*-view`; lead detail region; GET export links/buttons; preserved drawer.

- [ ] **Step 1: Write DOM RED assertions and survivor mutants**

Atualizar o parser contract para exigir:

```python
expected_navigation = (
    ("nav-overview", "overview-view"),
    ("nav-leads", "leads-view"),
    ("nav-executions", "executions-view"),
    ("nav-reservations", "reservations-view"),
    ("nav-payments", "payments-view"),
    ("nav-handoffs", "handoffs-view"),
)
for control_id, view_id in expected_navigation:
    control = by_id[control_id]
    assert control.tag == "button" and control.attrs.get("type") == "button"
    assert control.attrs.get("data-view") == view_id
    assert by_id[view_id].tag == "section"
assert by_id["nav-overview"].attrs.get("aria-current") == "page"
assert sum(node.attrs.get("aria-current") == "page" for node in root.descendants()) == 1
assert is_descendant(by_id["lead-detail"], by_id["leads-view"])
assert is_descendant(by_id["execution-drawer"], by_id["app-shell"])
```

Adicionar mutantes que removem uma view, duplicam `aria-current`, transformam nav em link externo ou export em POST; cada mutante deve falhar pela asserção correspondente.

- [ ] **Step 2: Verify DOM RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -k 'shell_dom_contract or dom_contract_rejects' -q
```

Expected: FAIL porque cinco views ainda não existem e Execuções está desabilitado.

- [ ] **Step 3: Build semantic HTML shell**

No `index.html`, preservar cabeçalho/sidebar/drawer e criar seções irmãs:

```html
<button id="nav-leads" class="nav-item" type="button" data-view="leads-view">
  <svg class="nav-icon" aria-hidden="true"><use href="#icon-users"></use></svg>
  <span>Leads</span>
</button>

<section id="leads-view" class="app-view" aria-labelledby="leads-title" hidden>
  <header class="view-heading">
    <div><p class="eyebrow">Histórico factual</p><h1 id="leads-title">Leads</h1></div>
    <a id="export-leads" class="export-link" href="/ops/api/exports/leads.csv" download>Exportar CSV</a>
  </header>
  <div id="leads-list" class="records-list" aria-live="polite"></div>
  <article id="lead-detail" class="lead-detail" hidden aria-labelledby="lead-detail-title">
    <button id="close-lead-detail" type="button">Voltar aos leads</button>
    <h2 id="lead-detail-title">Visão do lead</h2>
    <div id="lead-detail-content"></div>
  </article>
</section>
```

Repetir estrutura semântica específica para Execuções, Reservas, Pagamentos e Handoffs. Reusar a tabela/cards de execuções dentro de `executions-view`; remover esse bloco de `overview-view` sem alterar seus IDs internos.

- [ ] **Step 4: Preserve closed interaction inventory**

Atualizar a allowlist exata de controles/form/actions. Exportações são GET same-origin com atributo `download`; login/logout permanecem os únicos submits. Proibir novos anchors sem `download`/destino fechado.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -k 'shell_dom_contract or dom_contract_rejects or vertical_timeline' -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add v2_ops/static/index.html tests/test_v2_ops_ui.py
git commit -m "feat(v2-ops): add semantic operational views"
```

---

### Task 5: Safe-DOM navigation, lead drill-down and records rendering

**Files:**
- Modify: `v2_ops/static/ops.js`
- Modify: `tests/test_v2_ops_ui.py`

**Interfaces:**
- Consumes: `/ops/api/records`, `/ops/api/leads/{lead_id}`, existing dashboard/detail endpoints and Task 4 DOM.
- Produces: `activateView(viewId)`, `loadRecords()`, `renderLeads()`, `openLead(leadId)`, `renderReservations()`, `renderPayments()`, `renderHandoffs()`; a single EventSource; independent `recordsEpoch` and `leadEpoch`.

- [ ] **Step 1: Write JS contract RED tests**

Adicionar asserts estáticos:

```python
for required in (
    "function activateView(", "async function loadRecords(", "function renderLeads(",
    "async function openLead(", "function renderReservations(",
    "function renderPayments(", "function renderHandoffs(",
):
    assert required in javascript
assert javascript.count("new EventSource(") == 1
for forbidden in ("innerHTML", "insertAdjacentHTML", "eval(", "fetch(\"http"):
    assert forbidden not in javascript
```

Adicionar cenários Chromium adversariais no `BROWSER_SPEC` embutido:

```javascript
test('superseded records and lead responses cannot overwrite current view', async ({ page }) => {
  // Fulfill /records A depois de B e /leads/A depois de /leads/B.
  // Exigir que somente B permaneça renderizado e nenhum erro superseded apareça.
});
```

- [ ] **Step 2: Verify JS RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -k 'javascript_contract or adversarial_interleavings' -q
```

Expected: FAIL porque as funções/epochs não existem. Se a imagem Playwright não estiver presente, executar primeiro o preflight da Task 7; não converter em skip.

- [ ] **Step 3: Extend state and navigation**

Em `ops.js`:

```javascript
const state = {
  activeView: "overview-view",
  recordsSnapshot: null,
  selectedLead: null,
  recordsEpoch: 0,
  leadEpoch: 0,
  recordsError: null,
};

function activateView(viewId) {
  document.querySelectorAll(".app-view").forEach(view => { view.hidden = view.id !== viewId; });
  document.querySelectorAll("[data-view]").forEach(control => {
    if (control.dataset.view === viewId) control.setAttribute("aria-current", "page");
    else control.removeAttribute("aria-current");
  });
  state.activeView = viewId;
  if (window.matchMedia("(max-width: 820px)").matches) closeSidebar({ restoreFocus: false });
}
```

Usar o `MediaQueryList` existente; não criar outro listener de breakpoint.

- [ ] **Step 4: Implement safe factual renderers**

Criar helpers `element(tag, className, text)` usando `document.createElement` e `textContent`. Renderizar:

- Leads: ID, datas, contagens factuais e estado técnico mais recente.
- Lead detail: fatos/origem, perfil estruturado, turnos, reservas/pagamentos vinculados e execuções.
- Reservas: status factual, IDs, componentes, valor/moeda e outcome.
- Pagamentos: dois agrupamentos com headings `Iniciações` e `Liquidações`; `Lead não vinculado` quando `lead_id === null`.
- Handoffs: registros ou texto exato `Nenhum handoff registrado`.

Nenhum renderer cria claims derivadas. Todo botão de execução chama `openExecution(executionId)` existente; botões de lead chamam `openLead`.

- [ ] **Step 5: Add independent fetch epochs and retained snapshots**

```javascript
async function loadRecords() {
  const epoch = ++state.recordsEpoch;
  try {
    const payload = await getJSON("/ops/api/records");
    if (epoch !== state.recordsEpoch) return;
    state.recordsSnapshot = payload;
    state.recordsError = null;
    renderRecords();
  } catch (error) {
    if (epoch !== state.recordsEpoch) return;
    state.recordsError = normalizedMessage(error);
    renderRecordsAlert();
  }
}

async function openLead(leadId) {
  const epoch = ++state.leadEpoch;
  clearLeadDetail();
  const payload = await getJSON(`/ops/api/leads/${encodeURIComponent(leadId)}`);
  if (epoch !== state.leadEpoch) return;
  state.selectedLead = payload.lead;
  renderLeadDetail(payload.lead);
}
```

Refresh manual e evento SSE chamam `Promise.allSettled([loadDashboard(), loadRecords()])`, sem criar concorrência duplicada enquanto o refresh atual está pendente.

- [ ] **Step 6: Verify GREEN and old interaction preservation**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
venv/bin/python -m pytest tests/test_v2_ops_api.py tests/test_v2_ops_records_api.py -q
```

Expected: PASS; exatamente um EventSource e nenhuma regressão de Input/Output/timeline.

- [ ] **Step 7: Commit**

```bash
git add v2_ops/static/ops.js tests/test_v2_ops_ui.py
git commit -m "feat(v2-ops): render factual lead operations"
```

---

### Task 6: Conservative visual refinement and responsive records surfaces

**Files:**
- Modify: `v2_ops/static/ops.css`
- Modify: `tests/test_v2_ops_ui.py`

**Interfaces:**
- Consumes: approved refinement spec and Task 4/5 classes.
- Produces: accessible six-view desktop/mobile layout, selected timeline row, natural drawer scroll.

- [ ] **Step 1: Write CSS token and behavior RED tests**

Adicionar asserts causais:

```python
assert "--border-strong:" in css
assert "--surface-raised:" in css
assert re.search(r"\.execution-step\[aria-current=['\"]step['\"]\][^{]*\{[^}]*background:", css, re.S)
assert re.search(r"\.fact-chip[^}]*font-size:\s*(?:1[0-9]|0\.[89][0-9]*)", css, re.S)
mobile = css[css.index("@media (max-width: 820px)"):]
assert not re.search(r"#execution-timeline[^}]*max-height", mobile, re.S)
assert not re.search(r"#execution-timeline[^}]*overflow-y:\s*auto", mobile, re.S)
```

Criar mutantes que removem o fundo selecionado e recolocam `max-height` mobile; ambos devem falhar.

- [ ] **Step 2: Verify CSS RED**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -k 'visual or css or timeline' -q
```

Expected: FAIL nos tokens/comportamentos ainda ausentes.

- [ ] **Step 3: Implement exact conservative palette refinement**

No `:root`, adicionar sem mudar a marca:

```css
--border-strong: #cdbda8;
--surface-raised: #fffdf9;
--surface-selected: #edf6ee;
--text-secondary: #52635a;
```

Aplicar:

```css
.execution-step[aria-current="step"] {
  background: var(--surface-selected);
  border-color: color-mix(in srgb, var(--brand-600) 35%, var(--border-strong));
}
.execution-step:hover { background: #f6f1e8; }
.execution-step:focus-visible { outline: 3px solid color-mix(in srgb, var(--brand-600) 35%, transparent); }
.fact-chip, .status-chip { font-size: 12px; line-height: 1.35; }
```

Se `color-mix` não estiver no baseline Chromium, usar cores hex estáticas equivalentes no teste e CSS.

- [ ] **Step 4: Style new views and mobile natural flow**

Implementar `records-table`, `record-card`, `lead-detail`, `record-section`, `empty-state`, `split-records`, com tabela desktop e cards mobile. Em `@media (max-width: 820px)`, `#execution-timeline` usa `max-height: none; overflow: visible`; o drawer scrolla em seu container externo. Touch targets ≥44px; sem overflow horizontal.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
venv/bin/python -m pytest tests/test_v2_ops_ui.py -q
```

Expected: PASS antes do smoke visual real.

- [ ] **Step 6: Commit**

```bash
git add v2_ops/static/ops.css tests/test_v2_ops_ui.py
git commit -m "style(v2-ops): refine operational hierarchy and mobile flow"
```

---

### Task 7: Real Chromium fixture, navigation and CSV qualification

**Files:**
- Modify: `tests/browser/ops_dashboard_smoke.py`
- Modify: `tests/v2_ops_records_fixture.py`
- Modify: `tests/test_v2_ops_ui.py`

**Interfaces:**
- Consumes: complete local app and pinned Playwright container.
- Produces: one-worker zero-skip desktop/mobile smoke and ignored screenshots.

- [ ] **Step 1: Provision and authenticate the pinned browser capability**

Run:

```bash
docker pull mcr.microsoft.com/playwright:v1.55.0-noble
docker image inspect --format '{{.Id}}' mcr.microsoft.com/playwright:v1.55.0-noble
```

Expected exact ID:

```text
sha256:09d59668831815b8b1e3862edae613fb450173d68ba2f4e3fc5fbb7be76e0e7d
```

Se o registry devolver outro ID, parar a qualificação e não alterar o valor esperado sem auditar a imagem.

- [ ] **Step 2: Write/extend smoke assertions before product changes**

O fixture server cria trace + records e passa `records_path`. O Playwright deve:

```javascript
const nav = ["nav-overview", "nav-leads", "nav-executions", "nav-reservations", "nav-payments", "nav-handoffs"];
for (const id of nav) {
  await page.locator(`#${id}`).click();
  if (await page.locator('[aria-current="page"]').count() !== 1) throw new Error(`aria-current ${id}`);
}
await page.locator('#nav-leads').click();
await page.locator('[data-lead-id="manychat:lead-records-001"]').click();
await page.locator('[data-open-execution="event-tech-completed"]').click();
// Provar timeline, seleção e Input/Output.
await page.locator('#nav-reservations').click();
await expect(page.locator('#reservations-view')).toContainText('Confirmada');
await expect(page.locator('#reservations-view')).not.toContainText('ID Bókun registrado');
await page.locator('#nav-payments').click();
await expect(page.locator('#payments-view')).toContainText('Link Stripe preparado');
await expect(page.locator('#payments-view')).not.toContainText('Pagamento liquidado');
await page.locator('#nav-handoffs').click();
await expect(page.locator('#handoffs-view')).toContainText('Nenhum handoff registrado');
```

Capturar download de Leads e validar BOM/cabeçalho. No mobile, validar `timeline.scrollHeight === timeline.clientHeight` ou `overflowY !== 'auto'` e scroll natural do drawer.

- [ ] **Step 3: Verify RED**

Run:

```bash
venv/bin/python -m pytest tests/browser/ops_dashboard_smoke.py -q
```

Expected: FAIL enquanto o smoke server/seletores ainda não recebem records; a falha precisa citar a view/fixture ausente, não capability.

- [ ] **Step 4: Wire the records fixture into the real smoke server**

No script gerado, configurar:

```python
settings = OpsWebSettings(
    username=USERNAME,
    password_hash=PASSWORD_HASH,
    session_key=SESSION_KEY,
    trace_path=Path("/data/ops.sqlite3"),
    trace_key=TRACE_KEY,
    secure_cookie=False,
    records_path=Path("/records"),
)
app = create_ops_app(
    settings,
    reader=SQLiteOpsTraceReader(Path("/data/ops.sqlite3"), TRACE_KEY),
    records_reader=SQLiteRecordsReader(Path("/records")),
)
```

Montar o diretório inteiro read-only no container de teste. Derivar cardinalidades a partir de `/ops/api/records`, não de constantes de produção.

- [ ] **Step 5: Run Chromium GREEN**

Run:

```bash
venv/bin/python -m pytest tests/browser/ops_dashboard_smoke.py -q
```

Expected: `1 passed`, um worker, zero skips, desktop/mobile/download/timeline PASS.

- [ ] **Step 6: Inspect screenshots and fix only causal findings by TDD**

Carregar as capturas `1440x1000` e `390x844`. Qualquer defeito encontrado exige primeiro uma asserção geométrica/DOM que falhe, depois correção CSS/JS e rerun do smoke. Verificar contraste, wrapping, alinhamento, densidade, seleção, drawer e ausência de corte.

- [ ] **Step 7: Commit**

```bash
git add tests/browser/ops_dashboard_smoke.py tests/v2_ops_records_fixture.py tests/test_v2_ops_ui.py v2_ops/static/ops.css v2_ops/static/ops.js
git commit -m "test(v2-ops): qualify complete dashboard in Chromium"
```

---

### Task 8: Whole-branch verification, independent review and reversible service-only release

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Modify: `.superpowers/sdd/progress.md` (ignored operational ledger)
- Create outside Git: release Compose/env/manifest/evidence under `/home/ubuntu/workspace/agente-v2-ops-deploy/`

**Interfaces:**
- Consumes: frozen candidate commit/tree and active deployment `c228148e110a`.
- Produces: immutable `agente-v2-ops:<candidate12>`, dark/public evidence, active pointers and rollback receipt.

- [ ] **Step 1: Run complete clean test/static gate**

Run:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 PYTHONPATH=. venv/bin/python -m pytest tests/test_v2_ops_*.py -q
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 PYTHONPATH=. venv/bin/python -m pytest tests/browser/ops_dashboard_smoke.py -q
venv/bin/python -m compileall -q v2_ops tests
git diff --check
git status --short
```

Expected: zero failures/skips, browser `1 passed`, compileall/diff exit 0, somente arquivos esperados.

- [ ] **Step 2: Prove closed routes and no-effect boundary**

Executar script que instancia `create_ops_app` e exige:

```python
methods = {(route.path, tuple(sorted(route.methods or ()))) for route in app.routes}
assert not any(set(route.methods or ()) & {"PUT", "PATCH", "DELETE"} for route in app.routes)
assert {path for path, methods in methods if "POST" in methods} == {"/ops/login", "/ops/logout"}
```

Comparar SHA-256/size de todos os DB/WAL/SHM fixtures antes/depois de todas as rotas GET.

- [ ] **Step 3: Generate immutable review package and obtain two verdicts**

Gerar pacote de diff desde `c228148e110a6b0f7aeb5ca43758db85e4b64f3e` até HEAD. Revisão independente deve retornar:

```text
Spec compliance: APPROVE
Code quality: APPROVE
Critical: 0
Important: 0
```

Corrigir qualquer Critical/Important por RED→GREEN e repetir revisão/gates.

- [ ] **Step 4: Freeze candidate and build exact OCI image**

Commitar apenas após todos os gates; autenticar:

```bash
CANDIDATE=$(git rev-parse HEAD)
TREE=$(git rev-parse HEAD^{tree})
SHORT=${CANDIDATE:0:12}
docker build -f Dockerfile.v2-ops \
  --build-arg VCS_REF="$CANDIDATE" \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t "agente-v2-ops:$SHORT" .
docker image inspect "agente-v2-ops:$SHORT" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

Expected: label equals full candidate SHA.

- [ ] **Step 5: Create and authenticate rollback artifacts before mutation**

Preservar compose/env/image ID/container ID/start time da release ativa `c228148e110a`; exportar a imagem ativa se não existir archive validado; criar manifesto com SHA-256. Validar que rollback Compose aponta para `agente-v2-ops:c228148e110a` e seu env atual antes de publicar qualquer ponteiro candidato.

- [ ] **Step 6: Run hardened dark smoke**

Renderizar Compose candidato sem imprimir segredos. Iniciar container temporário sem Traefik, com:

- rootfs `--read-only`;
- `/data/ops:ro`;
- raiz GA ativa em `/data/records:ro`;
- tmpfs `/tmp`;
- env exato derivado do arquivo ativo + `V2_OPS_RECORDS_PATH=/data/records`.

Provar:

```text
health=200
login/auth=PASS
records/leads/reservations/payments/handoffs/exports=PASS
rootfs_readonly=PASS
ops_mount_rw=false
records_mount_rw=false
db_wal_shm_invariance=PASS
runtime_modules_absent=PASS
v2_ops_records_import=PASS
error_lines=0
```

Remover somente o container dark.

- [ ] **Step 7: Validate service-only Compose drift**

Comparar Compose renderizado ativo/candidato. Allowlist única:

- `v2-ops.image`;
- adição de `V2_OPS_RECORDS_PATH`;
- adição de `${V2_OPS_RECORDS_DATA_DIR}:/data/records:ro`;
- labels de release/config esperadas.

Todos os demais campos devem ser idênticos. Capturar IDs/start times de `agente-v2-ga-api`, `agente-v2-ga-worker` e `agente-v2-ga-router`.

- [ ] **Step 8: Cut over only `v2-ops` with rollback trap**

Usar Compose candidato e:

```bash
docker compose --env-file "$CANDIDATE_ENV" -f "$CANDIDATE_COMPOSE" up -d --no-deps --force-recreate v2-ops
```

Se health, mount, API, log ou smoke falhar, executar imediatamente o Compose/env rollback com o mesmo comando restrito a `v2-ops`.

- [ ] **Step 9: Public authenticated API-derived smoke**

Na URL `https://hermes.chapadabackpackers.com/ops`:

- health 200 e `/ops` 307;
- login real sem imprimir credenciais;
- release SHA/digest/config correspondem ao candidato;
- derivar contagens de `/ops/api/records`;
- navegar seis views desktop/mobile;
- abrir lead, execução, timeline/Input/Output;
- baixar CSV;
- zero console/page/request errors;
- screenshots finais.

Revalidar DB/WAL/SHM invariance e logs sem erro.

- [ ] **Step 10: Prove untouched GA services and publish pointers**

Exigir IDs/start times de API/worker/router idênticos ao baseline, zero restarts/OOM e health aplicável. Só então atualizar atomically `compose.yaml`, env pointer, `deployment.json` e manifesto de rollback. Registrar candidate SHA/tree/image ID, timestamps, testes e checksums em `docs/refactor/ACTIVE.md` e relatório operacional ignorado.

- [ ] **Step 11: Final fresh verification**

Run novamente:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 PYTHONPATH=. venv/bin/python -m pytest tests/test_v2_ops_*.py -q
venv/bin/python -m pytest tests/browser/ops_dashboard_smoke.py -q
curl -fsS https://hermes.chapadabackpackers.com/ops/healthz
```

Expected: zero failures/skips; browser `1 passed`; health JSON `{"status":"alive","mode":"read_only"}`.

---

## Plan Self-Review

- Spec coverage: Tasks 1–3 cobrem fontes/read-only/API/CSV; Tasks 4–6 cobrem seis views, drill-down e refino; Task 7 cobre Chromium; Task 8 cobre revisão, build, dark smoke, cutover e rollback.
- Type consistency: `OpsWebSettings.records_path`, `SQLiteRecordsReader`, `RecordsSnapshot`, `LeadDetail` e `render_csv` têm um único nome/signature em todo o plano.
- Source boundary: somente os nove arquivos comerciais raiz e o trace dedicado; sem sandbox, provider ou módulos V3.
- Semantic boundary: reserva confirmada usa certainty/status; iniciação/link não vira liquidação; IDs de provider permanecem específicos.
- Placeholder scan: nenhum passo depende de decisão futura ou implementação “similar”; valores fechados, comandos e resultados esperados estão definidos.
