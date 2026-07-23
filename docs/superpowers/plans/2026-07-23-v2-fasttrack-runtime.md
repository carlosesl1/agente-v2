# Agente V2 Fast-track Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is controller-inline; do not dispatch implementation subagents.

**Goal:** Entregar um host próprio do Agente V2 que atende no ManyChat, consulta hospedagem e passeios, cria reservas, conduz Stripe/Wise/Pix, executa pós-pagamento e conclui o atendimento, sem importar nem executar o agente legado.

**Architecture:** Um monólito modular em uma única imagem. `v2_host` é o único composition root; `v2_application` coordena o kernel `reservation_*`; `v2_adapters` implementa portas técnicas extraídas e versionadas; `v2_contracts` contém contratos neutros. API e workers são processos separados, usam a mesma store e nunca mantêm transação durante LLM ou provider.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Pydantic 2, HTTPX, SQLite WAL inicialmente, pytest/unittest existente, Hermes model adapter, ManyChat, Cloudbeds, Bókun, Stripe, Wise e validação Pix.

## Global Constraints

- A worktree obrigatória é `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout` na branch `phase8-shadow-canary-rollout`.
- A cadeia de autoridade é `AGENTS.md` → `docs/refactor/ACTIVE.md` → especificação ativa → este plano.
- `/home/ubuntu/chapada-leads-hermes` é somente leitura e nunca pode ser importado, executado como backend ou alterado pelo V2.
- O V2 possui um cérebro, um estado comercial e uma autoridade de side effects.
- A Maya interpreta e propõe; somente o kernel autoriza.
- Nenhum provider write ocorre dentro do request HTTP ou do turno da LLM.
- Nenhuma transação SQLite permanece aberta durante LLM, HTTP ou provider.
- Produto e opção usam IDs canônicos internos; nome público nunca autoriza write.
- Reserva, settlement e comunicação possuem ledgers/outboxes separados.
- Repetição só é automática quando há prova de `not_called`; `called_unknown` exige reconciliação ou handoff.
- SQLite é a primeira store, não um contrato da aplicação; ports permitem PostgreSQL posterior.
- Todos os serviços e métodos financeiros são implementados internamente em slices, mas a primeira ativação pública é única e só ocorre após Task 9.
- Provider writes, ManyChat público, deploy e rollout exigem autorizações separadas.
- O perfil de execução é controller-inline, RED/GREEN focado, regressão proporcional e uma única revisão integral no candidato final.

---

## 1. Autoridade de implementação

### 1.1 Grafo permitido

```text
v2_contracts
├── somente stdlib
├── sem provider SDK
└── sem imports internos

reservation_* (cápsula de kernel existente)

v2_application
├── v2_contracts
└── reservation_*

v2_adapters
└── v2_contracts

v2_host
├── v2_contracts
├── v2_application
├── v2_adapters
└── reservation_* somente no composition root
```

### 1.2 Prefixos legados proibidos nos pacotes novos

```python
LEGACY_PREFIXES = (
    "app",
    "cli",
    "chapada_leads",
    "config",
    "domain",
    "services",
    "tools",
)
```

Também são proibidos:

- literal `/home/ubuntu/chapada-leads-hermes` em código de produção;
- alteração de `sys.path`/`PYTHONPATH` para acessar legado;
- `docker exec` no container `chapada-leads-hermes`;
- subprocesso que execute módulos do legado;
- `importlib` dinâmico para contornar o guard;
- planner, agent runner, `LeadState`, operational orchestrator e tool executor genérico do legado.

### 1.3 Autoridade por responsabilidade

| Responsabilidade | Owner único |
|---|---|
| Linguagem natural | Maya via `ConversationModelPort` |
| Fatos/estado comercial | kernel V2 |
| Identidade do lead/evento | ingress V2 |
| Oferta escolhida | `OfferSnapshot` do kernel |
| Autorização de reserva | reducer V2 |
| Execução de reserva | worker V2 + adapter específico |
| Evidência financeira | workflow financeiro V2 |
| Settlement | worker V2 + adapter específico |
| Mensagem pública | public outbox V2 + ManyChat adapter |
| Handoff | workflow/outbox V2 |
| Configuração de rollout | `v2_host.settings` |

---

## 2. Estrutura final de arquivos

```text
v2_contracts/
  __init__.py
  channel.py
  model.py
  providers.py
  payments.py
  ports.py
v2_application/
  __init__.py
  inbox.py
  turns.py
  reads.py
  reservations.py
  payments.py
  completion.py
  workers.py
v2_adapters/
  __init__.py
  manychat.py
  hermes_model.py
  knowledge.py
  cloudbeds.py
  bokun.py
  stripe.py
  wise.py
  pix.py
v2_host/
  __init__.py
  settings.py
  composition.py
  app.py
  worker_main.py
scripts/
  check_fasttrack_boundaries.py
  run_v2_e2e.py
  validate_v2_fasttrack.py
tests/
  test_fasttrack_boundaries.py
  test_v2_manychat_ingress.py
  test_v2_turns.py
  test_v2_reads.py
  test_v2_reservations.py
  test_v2_payment_initiation.py
  test_v2_payment_evidence.py
  test_v2_completion.py
  test_v2_package_recovery.py
  test_v2_e2e.py
  fixtures/v2/
Dockerfile.v2
compose.v2.yaml
```

Arquivos existentes `reservation_*` são modificados apenas quando um contrato já provado do kernel precisa ser conectado. Não haverá renomeação ampla nem reorganização cosmética antes do primeiro E2E.

---

### Task 1: Control plane mecânico e pacotes do V2

**Files:**
- Create: `v2_contracts/__init__.py`
- Create: `v2_application/__init__.py`
- Create: `v2_adapters/__init__.py`
- Create: `v2_host/__init__.py`
- Create: `scripts/check_fasttrack_boundaries.py`
- Create: `tests/test_fasttrack_boundaries.py`
- Modify: `pyproject.toml`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: regras da seção 1 deste plano.
- Produces: `check_tree(root: Path) -> tuple[str, ...]`; quatro pacotes importáveis; gate obrigatório para todas as tasks seguintes.

- [ ] **Step 1: Escrever o teste RED do grafo e do legado proibido**

```python
# tests/test_fasttrack_boundaries.py
from pathlib import Path
import tempfile

from scripts.check_fasttrack_boundaries import check_tree


def test_current_tree_obeys_fasttrack_boundaries() -> None:
    assert check_tree(Path(__file__).parents[1]) == ()


def test_guard_rejects_legacy_import_and_cross_layer_import() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for package in ("v2_contracts", "v2_application", "v2_adapters", "v2_host"):
            (root / package).mkdir()
            (root / package / "__init__.py").write_text("", encoding="utf-8")
        (root / "v2_adapters" / "bad.py").write_text(
            "from services.manychat import ManyChatClient\n"
            "from v2_application.turns import V2TurnService\n",
            encoding="utf-8",
        )
        errors = check_tree(root)
        assert any("legacy prefix services" in item for item in errors)
        assert any("v2_adapters may not import v2_application" in item for item in errors)
```

- [ ] **Step 2: Executar o RED**

Run:

```bash
python -m pytest -q tests/test_fasttrack_boundaries.py
```

Expected: FAIL por ausência de `scripts.check_fasttrack_boundaries`.

- [ ] **Step 3: Implementar scanner AST fail-closed**

O scanner deve:

```python
NEW_PACKAGES = ("v2_contracts", "v2_application", "v2_adapters", "v2_host")
ALLOWED_INTERNAL = {
    "v2_contracts": frozenset(),
    "v2_application": frozenset({"v2_contracts", "reservation_domain", "reservation_lookup", "reservation_confirmation", "reservation_execution", "reservation_followup", "reservation_boundary"}),
    "v2_adapters": frozenset({"v2_contracts"}),
    "v2_host": frozenset({"v2_contracts", "v2_application", "v2_adapters", "reservation_domain", "reservation_lookup", "reservation_confirmation", "reservation_execution", "reservation_followup", "reservation_boundary"}),
}
LEGACY_PREFIXES = frozenset({"app", "cli", "chapada_leads", "config", "domain", "services", "tools"})
FORBIDDEN_LITERALS = (
    "/home/ubuntu/chapada-leads-hermes",
    "chapada-leads-hermes",
    "PYTHONPATH",
)
```

`check_tree` percorre somente `.py` dos quatro pacotes, rejeita erro de parse, imports estáticos/dinâmicos fora do grafo e literais proibidos. A CLI imprime cada erro e retorna 1; árvore válida imprime `fasttrack-boundaries: OK` e retorna 0.

- [ ] **Step 4: Registrar pacotes e dependências de runtime**

Modificar `pyproject.toml` para preservar os seis pacotes históricos em `[tool.phase7-wheel]`, registrar os quatro pacotes novos em `[tool.v2-fasttrack].packages` e adicionar:

```toml
[project.optional-dependencies]
runtime = [
  "fastapi>=0.115.0",
  "uvicorn>=0.30.0",
  "pydantic>=2.8.0",
  "httpx>=0.27.0",
  "PyYAML>=6.0.0",
  "cryptography>=42.0.0",
]
dev = ["pytest>=8.0.0"]
```

- [ ] **Step 5: Executar GREEN e regressão de pacote**

Run:

```bash
uv run --with pytest python -m pytest -q tests/test_fasttrack_boundaries.py
python scripts/check_fasttrack_boundaries.py
python -m py_compile v2_contracts/*.py v2_application/*.py v2_adapters/*.py v2_host/*.py scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: todos exit 0; o teste prova que `[tool.phase7-wheel]` permanece com os seis pacotes históricos; o guard imprime `fasttrack-boundaries: OK`. `tests/test_phase7_package.py` não é gate deste fast-track porque já falha no commit-base `8f73ee8b4bf40d6ea458a7fac3394aab756c1d88`: seu builder exige metadata `0.7.0`, enquanto a branch já declarava `0.8.0` antes da Task 1.

- [ ] **Step 6: Atualizar controle e commitar**

Após os gates verdes, criar o commit funcional:

```bash
git add AGENTS.md pyproject.toml docs/refactor docs/superpowers v2_contracts v2_application v2_adapters v2_host scripts/check_fasttrack_boundaries.py tests/test_fasttrack_boundaries.py
git commit -m "chore: establish v2 fasttrack control plane"
```

Depois registrar em `ACTIVE.md` o SHA real desse commit, marcar Task 1 `DONE`, mover `NEXT` para Task 2 e criar um segundo commit somente de controle:

```bash
git add docs/refactor/ACTIVE.md
git commit -m "docs: advance v2 fasttrack to task 2"
```

---

### Task 2: ManyChat ingress autenticado e inbox durável

**Files:**
- Create: `v2_contracts/channel.py`
- Create: `v2_application/inbox.py`
- Create: `v2_adapters/manychat.py`
- Create: `v2_host/settings.py`
- Create: `v2_host/app.py`
- Create: `tests/test_v2_manychat_ingress.py`
- Create: `tests/fixtures/v2/manychat_text.json`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: pacotes e guard da Task 1.
- Produces: `InboundEvent`, `InboundBatch`, `SQLiteInbox.accept`, `SQLiteInbox.claim_ready`, `parse_manychat_payload`, endpoint `POST /webhook/manychat`.

Contratos exatos:

```python
@dataclass(frozen=True, slots=True)
class InboundEvent:
    event_id: str
    lead_id: str
    subscriber_id: str
    conversation_id: str
    text: str
    media_url: str | None
    media_type: str | None
    occurred_at: datetime
    payload_hash: str

@dataclass(frozen=True, slots=True)
class InboundBatch:
    batch_id: str
    lead_id: str
    subscriber_id: str
    events: tuple[InboundEvent, ...]
    combined_text: str

class AcceptDisposition(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
```

- [ ] **Step 1: Escrever RED para autenticação, duplicata e isolamento por lead**

```python
# tests/test_v2_manychat_ingress.py

def test_webhook_accepts_once_and_never_calls_turn_inline(client, inbox) -> None:
    first = client.post("/webhook/manychat", headers={"X-V2-Webhook-Secret": "test-secret"}, json=TEXT_PAYLOAD)
    duplicate = client.post("/webhook/manychat", headers={"X-V2-Webhook-Secret": "test-secret"}, json=TEXT_PAYLOAD)
    assert first.status_code == 202
    assert first.json()["status"] == "accepted"
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert inbox.pending_count() == 1


def test_same_event_id_with_different_payload_is_conflict(client) -> None:
    client.post("/webhook/manychat", headers=AUTH, json=TEXT_PAYLOAD)
    changed = {**TEXT_PAYLOAD, "text": "conteúdo divergente"}
    response = client.post("/webhook/manychat", headers=AUTH, json=changed)
    assert response.status_code == 409
```

- [ ] **Step 2: Executar RED**

```bash
uv run --no-project --with 'fastapi>=0.115.0' --with 'httpx>=0.27.0' --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_manychat_ingress.py
```

Expected: FAIL por contratos/host ausentes.

- [ ] **Step 3: Implementar parser e inbox**

`parse_manychat_payload(payload, received_at)` aceita os aliases sanitizados comprovados pela fixture, deriva `lead_id` de identidade canônica e calcula SHA-256 sobre JSON canônico. `SQLiteInbox.accept` executa `BEGIN IMMEDIATE`, insere evento uma vez e diferencia duplicata idêntica de conflito. `claim_ready` agrupa eventos do mesmo lead em ordem após a quiet window e os marca com lease, sem processar turno.

Schema mínimo:

```sql
CREATE TABLE inbound_events (
  event_id TEXT PRIMARY KEY,
  lead_id TEXT NOT NULL,
  subscriber_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  payload BLOB NOT NULL,
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','claimed','processed','manual_review')),
  claim_token TEXT,
  claim_expires_at TEXT
) STRICT;
CREATE INDEX inbound_events_lead_status ON inbound_events(lead_id,status,occurred_at,event_id);
```

- [ ] **Step 4: Implementar host mínimo**

`V2Settings.from_env()` exige segredo do webhook e caminho absoluto da store. `create_app(settings, inbox)` valida `X-V2-Webhook-Secret` com `hmac.compare_digest`, limita corpo, faz parse e retorna apenas `accepted`, `duplicate` ou `conflict`. Não chama modelo, provider ou ManyChat outbound.

- [ ] **Step 5: Executar GREEN e guards**

```bash
uv run --no-project --with 'fastapi>=0.115.0' --with 'httpx>=0.27.0' --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_manychat_ingress.py tests/test_fasttrack_boundaries.py
python scripts/check_fasttrack_boundaries.py
python -m py_compile v2_contracts/channel.py v2_application/inbox.py v2_adapters/manychat.py v2_host/settings.py v2_host/app.py
git diff --check
```

Expected: exit 0.

- [ ] **Step 6: Atualizar controle e commitar**

```bash
git add v2_contracts v2_application v2_adapters v2_host tests/test_v2_manychat_ingress.py tests/fixtures/v2 docs/superpowers/plans/2026-07-23-v2-fasttrack-runtime.md
git commit -m "feat: add durable v2 manychat ingress"
```

Depois registrar em `ACTIVE.md` o SHA do commit funcional, marcar Task 2 `DONE`, mover `NEXT` para Task 3 e criar o commit de controle `docs: advance v2 fasttrack to task 3`.

---

### Task 3: Turno canônico, Maya e consultas completas

**Files:**
- Create: `v2_contracts/model.py`
- Create: `v2_contracts/providers.py`
- Create: `v2_contracts/ports.py`
- Create: `v2_application/turns.py`
- Create: `v2_application/reads.py`
- Create: `v2_adapters/hermes_model.py`
- Create: `v2_adapters/knowledge.py`
- Create: `v2_adapters/cloudbeds.py`
- Create: `v2_adapters/bokun.py`
- Create: `tests/test_v2_turns.py`
- Create: `tests/test_v2_reads.py`
- Modify: `reservation_boundary/coordinator.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Modify: `tests/test_phase7_coordinator.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: `InboundBatch`, kernel `reservation_*`, sandbox conversacional já validado.
- Produces: `ConversationModelPort.complete(ModelRequest) -> ModelProposal`; `ReadPort.read(ReadRequest) -> ReadObservation`; `V2TurnService.handle(batch) -> TurnResult`.

Novos ports não expõem secrets nem payload cru:

```python
class ConversationModelPort(Protocol):
    def complete(self, request: ModelRequest) -> ModelProposal: ...

class ReadPort(Protocol):
    def read(self, request: ReadRequest) -> ReadObservation: ...
```

`ReadRequest.kind` é união fechada de `knowledge`, `lodging`, `activity`, `room_description`, `activity_description`. Cada observation contém `request_hash`, `provider`, `observed_at`, `expires_at`, `public_payload` e `private_binding_hash`.

- [ ] **Step 1: Escrever RED que prova modelo/provider fora da transação**

```python
# tests/test_v2_turns.py

def test_model_and_read_run_without_open_sqlite_transaction(runtime) -> None:
    runtime.model.on_call = lambda: assert_not_in_transaction(runtime.connection)
    runtime.reads.on_call = lambda: assert_not_in_transaction(runtime.connection)
    result = runtime.turns.handle(LODGING_BATCH)
    assert result.reply_chunks
    assert runtime.store.turn_receipt_count(LODGING_BATCH.batch_id) == 1
```

Adicionar em `test_phase7_coordinator.py` uma regressão equivalente que falha no código atual porque `intent.interpret` ocorre dentro de `turn_transaction`.

- [ ] **Step 2: Executar RED focado**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_phase7_coordinator.py::Phase7CoordinatorTests::test_intent_runs_outside_turn_transaction tests/test_v2_turns.py
```

Expected: a regressão do coordinator falha antes da correção.

- [ ] **Step 3: Separar preparo, chamada externa e commit**

Alterar `TurnCoordinator.coordinate` para manter o lead lock, mas usar duas transações curtas:

```text
claim lead lock
→ tx A: dedupe, load/create genesis, acquire fence, snapshot request
→ fecha tx A
→ intent/model fora de transação
→ reads fora de transação, no máximo um ciclo por kind permitido
→ kernel reduce em memória
→ tx B: revalida event/version/fence/deadline, commit atômico
→ fecha tx B
```

Adicionar `create_genesis(lead_key, claimed_at)` ao store. O caminho produtivo não chama `LegacyReaderPort`; migração legada fica disponível somente por comando administrativo explícito e fora de `V2TurnService`.

- [ ] **Step 4: Extrair o adapter do modelo sem capabilities de provider**

Mover o protocolo já provado em `reservation_boundary.sandbox` para os contratos neutros sem alterar sua gramática. `HermesModelAdapter` pode inicialmente executar o child Hermes isolado, mas nunca o container/runtime `chapada-leads-hermes`. O prompt fornece somente knowledge sanitizado e schemas fechados. Resposta inválida não persiste turno parcial.

- [ ] **Step 5: Implementar reads diretos e versionados**

Extrair somente clientes técnicos das fontes de referência:

```text
services/cerebro.py + tools/cerebro_tools.py
services/cloudbeds.py + tools/cloudbeds_tools.py + tools/cloudbeds_v2_tools.py
services/bokun.py + tools/bokun_tools.py + tools/bokun_v2_tools.py
```

Antes de copiar comportamento, registrar SHA-256 dos arquivos lidos em `docs/refactor/extraction-evidence/task3-read-adapters.md`. Os adapters novos usam transports HTTP injetados/stdlib e contratos V2; não importam os módulos acima. Cloudbeds e Bókun retornam opções canônicas com IDs privados apenas no binding interno. Bókun recebe exclusivamente `tour_product_id` canônico. Os caminhos reais encontrados e qualquer caminho previsto mas ausente devem constar nessa evidência, sem presumir arquivos.

- [ ] **Step 6: Provar conversa completa somente leitura**

Casos em `tests/test_v2_reads.py`:

```python
def test_lodging_read_binds_dates_occupancy_price_and_private_offer_id(reads) -> None:
    observation = reads.read(LODGING_REQUEST)
    assert observation.request_hash == LODGING_REQUEST.canonical_hash()
    assert observation.public_payload["total_amount"] == "480.00"
    assert observation.private_binding_hash == EXPECTED_LODGING_BINDING


def test_activity_read_requires_canonical_product_id(reads) -> None:
    with pytest.raises(InvalidReadRequest, match="canonical product"):
        reads.read(replace(ACTIVITY_REQUEST, product_id="Buracão"))


def test_knowledge_read_cannot_return_provider_credentials(reads) -> None:
    observation = reads.read(KNOWLEDGE_REQUEST)
    public = json.dumps(observation.public_payload, sort_keys=True)
    assert "token" not in public.lower()
    assert "secret" not in public.lower()


def test_stale_observation_cannot_authorize_selection(runtime) -> None:
    stale = replace(LODGING_OBSERVATION, expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(StaleObservation):
        runtime.reads.accept(stale, now=NOW)


def test_model_cannot_mix_read_and_effect_proposal(runtime) -> None:
    runtime.model.next = MIXED_READ_AND_EFFECT_PROPOSAL
    with pytest.raises(InvalidModelProposal):
        runtime.turns.handle(LODGING_BATCH)
    assert runtime.provider_log == []
```

Run:

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_phase7_coordinator.py tests/test_v2_turns.py tests/test_v2_reads.py tests/test_phase8_fasttrack_sandbox.py
python scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: exit 0. Milestone interno: HTTP local conversa com providers fake e reads reais, com todos os writes/delivery fechados.

- [ ] **Step 7: Atualizar controle e commitar**

Criar primeiro o commit funcional:

```bash
git add reservation_boundary v2_contracts v2_application v2_adapters tests docs/refactor/extraction-evidence docs/superpowers/plans
git commit -m "feat: connect canonical v2 turns and provider reads"
```

Depois registrar esse SHA em `ACTIVE.md`, mover `NEXT` para Task 4 e criar o commit de controle separado, sem código funcional.

---

### Task 4: Reservas duráveis Cloudbeds e Bókun

**Files:**
- Create: `v2_application/reservations.py`
- Create: `tests/test_v2_reservations.py`
- Modify: `v2_contracts/providers.py`
- Modify: `v2_adapters/cloudbeds.py`
- Modify: `v2_adapters/bokun.py`
- Create: `v2_application/workers.py`
- Modify: `reservation_execution/adapter.py`
- Modify: `reservation_execution/worker.py`
- Modify: `reservation_boundary/dispatch.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: `ReservationCommand`, `OfferSnapshot`, workers/ledgers existentes.
- Produces: `CloudbedsReservationPort.execute(ProviderDispatchPermit) -> ProviderExecutionResult`; `BokunReservationPort.execute(ProviderDispatchPermit) -> ProviderExecutionResult`; a camada `v2_application` converte o resultado neutro em `ExecutionOutcome`; worker único por comando.

- [ ] **Step 1: Escrever RED dos dois providers e pacote sem split inseguro**

```python
# tests/test_v2_reservations.py

def test_confirmed_lodging_queues_one_cloudbeds_command(runtime) -> None:
    runtime.confirm(LODGING_SUMMARY)
    commands = runtime.store.reservation_commands(LODGING_WORKFLOW_ID)
    assert [(item.provider, item.operation) for item in commands] == [("cloudbeds", "reserve_lodging")]


def test_confirmed_activity_queues_one_bokun_command(runtime) -> None:
    runtime.confirm(ACTIVITY_SUMMARY)
    commands = runtime.store.reservation_commands(ACTIVITY_WORKFLOW_ID)
    assert [(item.provider, item.operation) for item in commands] == [("bokun", "book_activity")]


def test_duplicate_worker_claim_calls_provider_once(runtime) -> None:
    runtime.queue(LODGING_COMMAND)
    assert runtime.reservation_worker.run_once(now=NOW).disposition.value == "effect_confirmed"
    assert runtime.reservation_worker.run_once(now=NOW).disposition.value == "idle"
    assert runtime.provider_log == [LODGING_COMMAND.command_id]


def test_timeout_after_dispatch_becomes_called_unknown_without_retry(runtime) -> None:
    runtime.cloudbeds.raise_after_dispatch = TimeoutError()
    runtime.queue(LODGING_COMMAND)
    assert runtime.reservation_worker.run_once(now=NOW).disposition.value == "manual_review"
    assert runtime.reservation_worker.run_once(now=NOW).disposition.value == "idle"
    assert len(runtime.provider_log) == 1


def test_package_confirmation_queues_two_component_commands_atomically(runtime) -> None:
    runtime.confirm(PACKAGE_SUMMARY)
    commands = runtime.store.reservation_commands(PACKAGE_WORKFLOW_ID)
    assert {item.provider for item in commands} == {"cloudbeds", "bokun"}
    assert len(commands) == 2


def test_model_supplied_provider_payload_is_rejected(runtime) -> None:
    with pytest.raises(DispatchRejected):
        runtime.dispatch(MODEL_FORGED_PROVIDER_PAYLOAD)
    assert runtime.provider_log == []
```

- [ ] **Step 2: Executar RED**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_reservations.py
```

Expected: FAIL porque os adapters de write e composição não existem.

- [ ] **Step 3: Implementar adapters específicos**

Cloudbeds deriva payload da option vinculada e dados canônicos do hóspede; Bókun deriva cart e submit da option vinculada. Ambos exigem `ProviderDispatchPermit`, idempotency key do command ledger e write gate por provider. O método retorna somente:

O port técnico retorna `ProviderExecutionResult` neutro; `v2_application` o converte para o `ExecutionOutcome` canônico existente. Ambos aceitam exatamente as certezas `NOT_CALLED`, `CALLED_NO_EFFECT`, `CALLED_UNKNOWN` ou `EFFECT_CONFIRMED`, com request hash do command, payload hash derivado, provider reference fingerprint e evidência de claim validados pelos construtores.

Erro antes de dispatch pode liberar claim conforme orçamento finito. Qualquer timeout/erro após fence registra `CALLED_UNKNOWN` e encerra auto-retry.

- [ ] **Step 4: Fechar package allocation**

Uma confirmação combinada chama `ReservationAllocator.expand_commands(...)` antes do commit e cria dois commands na mesma tupla da decisão atômica, um por componente. Cada command possui provider, ledger, fence e outcome próprios. Se o segundo falhar, o primeiro não é repetido; package progress deriva dos dois outcomes e pode exigir compensação/handoff.

- [ ] **Step 5: Executar GREEN e regressões de execução**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_reservations.py tests/test_phase5_worker.py tests/test_phase5_reconciliation.py tests/test_phase8_tool_dispatch.py
python scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: exit 0, incluindo assert de `provider_calls <= 1`.

- [ ] **Step 6: Atualizar controle e commitar**

Criar primeiro o commit funcional:

```bash
git add v2_contracts v2_application v2_adapters reservation_execution tests docs/superpowers/plans
git commit -m "feat: execute v2 lodging and activity reservations"
```

Depois registrar esse SHA em `ACTIVE.md`, mover `NEXT` para Task 5 e criar o commit de controle separado, sem código funcional.

---

### Task 5: Iniciação financeira Stripe, Wise e Pix

**Files:**
- Create: `v2_contracts/payments.py`
- Create: `v2_application/payments.py`
- Create: `v2_adapters/stripe.py`
- Create: `v2_adapters/wise.py`
- Create: `v2_adapters/pix.py`
- Create: `tests/test_v2_payment_initiation.py`
- Modify: `reservation_followup/payment.py`
- Modify: `reservation_boundary/dispatch.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: reserva `EFFECT_CONFIRMED`, política comercial e `BusinessUnit`.
- Produces: `PaymentObligation`, `PaymentMethodOffer`, links/instruções públicas e outbox, sem declarar settlement.

Contrato econômico:

```python
@dataclass(frozen=True, slots=True)
class PaymentObligation:
    payment_id: str
    reservation_anchor_id: str
    business_unit: BusinessUnit
    amount_minor: int
    currency: str
    due_kind: DueKind
    economic_version: int
    receiver_profile_id: str
```

- [ ] **Step 1: Escrever RED da separação por unidade e método**

```python
# tests/test_v2_payment_initiation.py

def test_hostel_stripe_uses_only_hostel_account_and_anchor(runtime) -> None:
    link = runtime.payments.initiate(HOSTEL_STRIPE_OBLIGATION)
    assert link.account_profile_id == "hostel"
    assert link.reservation_anchor_id == HOSTEL_ANCHOR_ID


def test_agency_stripe_uses_only_agency_account_and_anchor(runtime) -> None:
    link = runtime.payments.initiate(AGENCY_STRIPE_OBLIGATION)
    assert link.account_profile_id == "agency"
    assert link.reservation_anchor_id == AGENCY_ANCHOR_ID


def test_wise_instruction_contains_no_unverified_payment_claim(runtime) -> None:
    instruction = runtime.payments.initiate(HOSTEL_WISE_OBLIGATION)
    assert instruction.settled is False
    assert "confirmado" not in instruction.public_text.lower()


def test_pix_instruction_comes_from_authorized_knowledge_profile(runtime) -> None:
    instruction = runtime.payments.initiate(AGENCY_PIX_OBLIGATION)
    assert instruction.receiver_profile_id == AGENCY_PIX_OBLIGATION.receiver_profile_id
    assert instruction.public_text == runtime.knowledge.pix_instruction("agency")


def test_foreign_guest_due_at_checkin_completes_without_payment_link(runtime) -> None:
    outcome = runtime.payments.plan(FOREIGN_GUEST_RESERVATION)
    assert outcome.obligation.due_kind.value == "due_at_checkin"
    assert outcome.payment_effects == ()


def test_method_change_preserves_reservation_and_economic_version(runtime) -> None:
    changed = runtime.payments.change_method(HOSTEL_PIX_PAYMENT, "wise")
    assert changed.reservation_anchor_id == HOSTEL_PIX_PAYMENT.reservation_anchor_id
    assert changed.economic_version == HOSTEL_PIX_PAYMENT.economic_version


def test_economic_change_increments_only_financial_version(runtime) -> None:
    changed = runtime.payments.change_amount(HOSTEL_PIX_PAYMENT, amount_minor=30000)
    assert changed.reservation_anchor_id == HOSTEL_PIX_PAYMENT.reservation_anchor_id
    assert changed.economic_version == HOSTEL_PIX_PAYMENT.economic_version + 1
```

- [ ] **Step 2: Executar RED**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_payment_initiation.py
```

Expected: FAIL por adapters/contratos ausentes e pelo ledger de iniciação ainda inexistente.

- [ ] **Step 3: Implementar Stripe link como efeito durável**

Extrair comportamento técnico de `services/stripe_client.py` e testes sanitizados, registrando o hash em `docs/refactor/extraction-evidence/task5-payment-initiation.md`. O worker recebe obrigação já autorizada, escolhe conta por `BusinessUnit`, cria metadata mínima vinculada a `payment_id`, `anchor_id`, `amount_minor`, `currency` e `economic_version`, e grava receipt antes da mensagem pública. Link nunca é gerado por texto da Maya.

- [ ] **Step 4: Implementar Wise expectation e Pix instructions**

Wise registra expectativa vinculada sem fazer settlement. Pix retorna somente instrução da autoridade knowledge, receiver profile e valor da obrigação. Stripe, Wise e Pix usam tipos discriminados diferentes; nenhuma função aceita `dict[str, Any]` como contrato público.

- [ ] **Step 5: Manter catálogo histórico bloqueado e compor somente o caminho V2**

Os tools Stripe de `reservation_boundary/dispatch.py` permanecem `BLOCKED_UNMIGRATED`: eles pertencem à interface histórica e não representam o novo command de iniciação. O V2 usa exclusivamente `v2_application.payments`, seu ledger `queue → claim → fence → receipt/manual_review` e os adapters tipados. Wise verification continua bloqueado até Task 6; instrução Wise é separada da verificação.

- [ ] **Step 6: Executar GREEN e regressões financeiras**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_payment_initiation.py tests/test_phase6_payment.py tests/test_phase6_payment_outbox.py tests/test_phase8_tool_dispatch.py
python scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: exit 0.

- [ ] **Step 7: Atualizar controle e commitar**

Criar primeiro o commit funcional:

```bash
git add v2_contracts v2_application v2_adapters tests docs/refactor/extraction-evidence docs/superpowers/plans
git commit -m "feat: initiate v2 stripe wise and pix payments"
```

Depois registrar esse SHA em `ACTIVE.md`, mover `NEXT` para Task 6 e criar o commit de controle separado, sem código funcional.

---

### Task 6: Evidência financeira, claims e settlement

**Files:**
- Create: `tests/test_v2_payment_evidence.py`
- Modify: `v2_contracts/payments.py`
- Modify: `v2_application/payments.py`
- Modify: `v2_adapters/stripe.py`
- Modify: `v2_adapters/wise.py`
- Modify: `v2_adapters/pix.py`
- Modify: `v2_host/app.py`
- Modify: `v2_application/workers.py`
- Modify: `reservation_followup/payment.py`
- Modify: `reservation_followup/sqlite_store.py`
- Modify: `reservation_followup/workers.py`
- Modify: `reservation_boundary/dispatch.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: `PaymentEvidenceRecorded` já produzida por adapter de assinatura/prova verificado; `PaymentObligation` persistida.
- Produces: claim global; `PaymentSettlementCommand`; `SettlementOutcome`. Os endpoints de bytes brutos apenas compõem esses adapters no `v2_host` da Task 9, sem segundo ledger.

- [ ] **Step 1: Escrever RED para binding, claim global, replay e timeout pós-fence**

```python
# tests/test_v2_payment_evidence.py

def test_stripe_signed_event_matches_exact_obligation_once(runtime) -> None:
    first = runtime.stripe_ingress.accept(STRIPE_EVENT_BYTES, STRIPE_SIGNATURE)
    second = runtime.stripe_ingress.accept(STRIPE_EVENT_BYTES, STRIPE_SIGNATURE)
    assert first.disposition.value == "accepted"
    assert second.disposition.value == "duplicate"
    assert runtime.store.payment_claim_count(STRIPE_CLAIM_KEY) == 1


def test_stripe_duplicate_is_noop_and_divergent_reuse_is_conflict(runtime) -> None:
    runtime.stripe_ingress.accept(STRIPE_EVENT_BYTES, STRIPE_SIGNATURE)
    with pytest.raises(EvidenceConflict):
        runtime.stripe_ingress.accept(DIVERGENT_STRIPE_EVENT_BYTES, DIVERGENT_STRIPE_SIGNATURE)


def test_wise_signed_credit_requires_unique_target_amount_currency_receiver(runtime) -> None:
    evidence = runtime.wise_ingress.accept(WISE_EVENT_BYTES, WISE_SIGNATURE)
    assert evidence.payment_id == HOSTEL_WISE_OBLIGATION.payment_id
    assert evidence.amount_minor == HOSTEL_WISE_OBLIGATION.amount_minor


def test_wise_ambiguous_credit_goes_to_manual_review(runtime) -> None:
    runtime.store.add_obligation(SECOND_MATCHING_WISE_OBLIGATION)
    result = runtime.wise_ingress.accept(WISE_EVENT_BYTES, WISE_SIGNATURE)
    assert result.disposition.value == "manual_review"


def test_pix_requires_exact_receiver_amount_status_and_nonplaceholder_e2e(runtime) -> None:
    with pytest.raises(InvalidPaymentEvidence):
        runtime.pix_ingress.accept(replace(PIX_PROOF, e2e_id="000000"))


def test_pix_replay_against_another_target_is_rejected(runtime) -> None:
    runtime.pix_ingress.accept(PIX_PROOF)
    with pytest.raises(EvidenceConflict):
        runtime.pix_ingress.accept(replace(PIX_PROOF, payment_id=OTHER_PAYMENT_ID))


def test_pix_keeps_bank_settlement_confirmed_false(runtime) -> None:
    evidence = runtime.pix_ingress.accept(PIX_PROOF)
    assert evidence.visual_evidence_accepted is True
    assert evidence.bank_settlement_confirmed is False


def test_settlement_timeout_after_fence_never_redispatches(runtime) -> None:
    runtime.settlement.raise_after_dispatch = TimeoutError()
    runtime.queue_settlement(STRIPE_SETTLEMENT_COMMAND)
    assert runtime.settlement_worker.run_once(now=NOW).disposition.value == "manual_review"
    assert runtime.settlement_worker.run_once(now=NOW).disposition.value == "idle"
    assert len(runtime.settlement_log) == 1
```

- [ ] **Step 2: Executar RED**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_payment_evidence.py
```

Expected: FAIL porque a fachada V2 ainda não delega ao claim global da Fase 6.

- [ ] **Step 3: Compor evidência verificada sem duplicar validadores**

`V2PaymentEvidenceGateway` aceita somente `PaymentEvidenceRecorded` já verificada pelos contratos discriminados maduros (`VerifiedStripeEvent`, `VerifiedWiseCredit`, `PixVisualEvidence`) e delega atomicamente a `SQLiteFollowupUnitOfWork.claim_payment_evidence`. Assinatura sobre bytes brutos e extração Pix continuam nos adapters técnicos auditados; as rotas abaixo serão composition-only na Task 9:

```text
POST /webhook/stripe/{business_unit}
POST /webhook/wise/{business_unit}
POST /webhook/pix-proof
```

Nenhuma rota executa settlement inline e não existe um segundo ledger V2 de evidência.

- [ ] **Step 4: Implementar claims e settlement worker**

A store adquire claim global antes de enfileirar settlement. O worker prepara request, fence permanentemente, chama exatamente uma vez e grava outcome. `DISPATCHED_UNKNOWN` sempre exige reconciliação/handoff. Para Pix aceito:

```python
visual_evidence_accepted = True
bank_settlement_confirmed = False
```

A projeção pública usa “comprovante validado e pagamento aceito para processamento”, nunca confirmação bancária.

- [ ] **Step 5: Manter tool histórico bloqueado e executar GREEN**

`wise_verificar_pagamento` permanece bloqueado no catálogo histórico: o V2 usa o gateway de evidência e o settlement worker tipado da Fase 6, não o executor genérico de tools. Run:

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_payment_evidence.py tests/test_phase6_payment_claims.py tests/test_phase6_payment_worker.py tests/test_phase8_phase6_v2.py
python scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: exit 0.

- [ ] **Step 6: Atualizar controle e commitar**

Criar o commit funcional da fachada/integração e, depois dos gates, registrar seu SHA em `ACTIVE.md`, mover `NEXT` para Task 7 e criar um commit de controle separado, sem código funcional.

---

### Task 7: Pós-pagamento, mensagem pública e conclusão

**Files:**
- Create: `v2_application/completion.py`
- Create: `tests/test_v2_completion.py`
- Modify: `v2_adapters/manychat.py`
- Modify: `v2_application/workers.py`
- Create: `v2_host/worker_main.py`
- Modify: `reservation_followup/handoff.py`
- Modify: `reservation_followup/workers.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: reservation/payment outcomes canônicos.
- Produces: public outbox, efeitos pós-pagamento, delivery receipts e estado `completed`.

- [ ] **Step 1: Escrever RED de separação entre settlement e delivery**

```python
# tests/test_v2_completion.py

def test_payment_settlement_enqueues_required_post_payment_effects_once(runtime) -> None:
    runtime.record_settlement(SETTLED_OUTCOME)
    runtime.record_settlement(SETTLED_OUTCOME)
    assert runtime.store.post_payment_effect_count(PAYMENT_ID) == EXPECTED_REQUIRED_EFFECTS


def test_manychat_failure_does_not_repeat_reservation_or_settlement(runtime) -> None:
    runtime.manychat.fail_before_send = True
    runtime.delivery_worker.run_once(now=NOW)
    assert len(runtime.reservation_log) == 1
    assert len(runtime.settlement_log) == 1


def test_delivery_receipt_marks_only_public_outbox_delivered(runtime) -> None:
    result = runtime.delivery_worker.run_once(now=NOW)
    assert result.disposition.value == "delivered"
    assert runtime.store.public_outbox_status(PUBLIC_MESSAGE_ID) == "delivered"
    assert runtime.store.payment_status(PAYMENT_ID) == "settled"


def test_optional_email_failure_does_not_block_customer_completion(runtime) -> None:
    runtime.email.fail_before_send = True
    runtime.run_followups()
    assert runtime.completion.evaluate(WORKFLOW_ID).value == "completed"


def test_bokun_form_is_required_only_for_applicable_activity(runtime) -> None:
    assert "bokun_form" in runtime.completion.required_receipts(ACTIVITY_WORKFLOW)
    assert "bokun_form" not in runtime.completion.required_receipts(LODGING_WORKFLOW)


def test_completed_requires_all_required_receipts(runtime) -> None:
    runtime.store.remove_receipt(REQUIRED_PUBLIC_RECEIPT_ID)
    assert runtime.completion.evaluate(WORKFLOW_ID).value == "pending"


def test_new_message_after_completed_starts_followup_without_erasing_history(runtime) -> None:
    before = runtime.store.history(WORKFLOW_ID)
    next_workflow = runtime.turns.handle(FOLLOWUP_BATCH)
    assert runtime.store.history(WORKFLOW_ID) == before
    assert next_workflow.workflow_id != WORKFLOW_ID
```

- [ ] **Step 2: Executar RED**

```bash
python -m pytest -q tests/test_v2_completion.py
```

Expected: FAIL por completion policy e ManyChat delivery port ausentes.

- [ ] **Step 3: Implementar projection e outboxes**

`CompletionPolicy.evaluate(workflow)` retorna `PENDING`, `COMPLETED` ou `MANUAL_REVIEW` a partir de receipts, nunca do texto da Maya. `PublicReply` contém chunks públicos e chave idempotente `release_id:lead_id:message_id:channel`. Worker ManyChat grava receipt por chunk; falha antes de send permite retry, resultado incerto bloqueia resend automático.

- [ ] **Step 4: Implementar processo de workers**

`v2_host.worker_main` executa lotes pequenos e independentes nesta ordem:

```text
inbox → reservation → payment initiation → settlement → post-payment → public delivery → reconciliation
```

Cada `run_once` processa no máximo um claim por fila. Ausência de trabalho retorna `idle`; erro de uma fila não abre capability de outra.

- [ ] **Step 5: Executar GREEN e regressões de outbox**

```bash
python -m pytest -q tests/test_v2_completion.py tests/test_phase5_outbox.py tests/test_phase6_handoff_worker.py tests/test_phase6_payment_outbox.py tests/test_phase8_followup_outbox_v2.py
python scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: exit 0.

- [ ] **Step 6: Atualizar controle e commitar**

```bash
git add v2_application v2_adapters v2_host reservation_followup tests docs/refactor/ACTIVE.md
git commit -m "feat: complete v2 post payment and public delivery"
```

---

### Task 8: Pacote combinado, recuperação e handoff excepcional

**Files:**
- Create: `tests/test_v2_package_recovery.py`
- Modify: `v2_application/turns.py`
- Modify: `v2_application/reservations.py`
- Modify: `v2_application/payments.py`
- Modify: `v2_application/completion.py`
- Modify: `v2_application/workers.py`
- Modify: `v2_host/worker_main.py`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Consumes: todos os workflows das Tasks 2–7.
- Produces: package progress derivado, reconciliação sem redispatch e handoff durável para exceções.

- [ ] **Step 1: Escrever matriz RED de falhas compostas**

```python
# tests/test_v2_package_recovery.py

def test_package_uses_one_summary_one_confirmation_two_reservation_ledgers(runtime) -> None:
    runtime.confirm(PACKAGE_SUMMARY)
    assert runtime.store.summary_count(PACKAGE_WORKFLOW_ID) == 1
    assert runtime.store.confirmation_count(PACKAGE_WORKFLOW_ID) == 1
    assert runtime.store.reservation_ledger_count(PACKAGE_WORKFLOW_ID) == 2


def test_hostel_success_agency_unknown_never_repeats_hostel(runtime) -> None:
    runtime.run_package(HOSTEL_CONFIRMED_AGENCY_UNKNOWN)
    runtime.reconciler.run_once(now=NOW)
    assert runtime.provider_log.count(HOSTEL_COMMAND_ID) == 1
    assert runtime.store.package_status(PACKAGE_WORKFLOW_ID) == "manual_review"


def test_two_business_units_never_share_receiver_or_financial_claim(runtime) -> None:
    obligations = runtime.store.payment_obligations(PACKAGE_WORKFLOW_ID)
    assert obligations[0].receiver_profile_id != obligations[1].receiver_profile_id
    assert obligations[0].claim_namespace != obligations[1].claim_namespace


def test_restart_before_fence_requeues_safely(runtime) -> None:
    runtime.crash_before_fence(LODGING_COMMAND)
    reopened = runtime.reopen()
    assert reopened.reservation_worker.run_once(now=AFTER_LEASE).disposition.value == "effect_confirmed"
    assert len(reopened.provider_log) == 1


def test_restart_after_fence_reconciles_without_provider_call(runtime) -> None:
    runtime.crash_after_fence(LODGING_COMMAND)
    reopened = runtime.reopen()
    reopened.reconciler.run_once(now=AFTER_LEASE)
    assert reopened.provider_log == []
    assert reopened.store.command_status(LODGING_COMMAND.command_id) == "manual_review"


def test_discount_request_creates_one_handoff_and_no_write(runtime) -> None:
    runtime.turns.handle(DISCOUNT_REQUEST_BATCH)
    assert runtime.store.handoff_count(WORKFLOW_ID) == 1
    assert runtime.provider_log == []


def test_active_handoff_blocks_all_commercial_effects(runtime) -> None:
    runtime.activate_handoff(WORKFLOW_ID)
    with pytest.raises(ActiveHandoff):
        runtime.dispatch(LODGING_COMMAND)
    assert runtime.provider_log == []


def test_handoff_acknowledgement_is_public_safe_and_idempotent(runtime) -> None:
    runtime.activate_handoff(WORKFLOW_ID)
    runtime.activate_handoff(WORKFLOW_ID)
    messages = runtime.store.public_messages(WORKFLOW_ID)
    assert len(messages) == 1
    assert "internal" not in messages[0].text.lower()
```

- [ ] **Step 2: Executar RED**

```bash
python -m pytest -q tests/test_v2_package_recovery.py
```

Expected: FAIL nos gaps de package/handoff/restart.

- [ ] **Step 3: Implementar package progress derivado**

Não persistir flags paralelas como `hostel_done` ou `agency_done`. Derivar progresso de component commands, outcomes, obligations e required receipts. Conclusão ocorre somente quando todos os componentes requeridos atingirem terminal válido.

- [ ] **Step 4: Implementar reconciler capability-free**

Pré-fence expirado pode liberar claim. Pós-fence sem outcome consulta apenas receipts/read-back seguros e nunca chama create/settlement novamente. Incerteza preservada cria um handoff único e bloqueia effects subsequentes.

- [ ] **Step 5: Executar GREEN e regressões de crash**

```bash
python -m pytest -q tests/test_v2_package_recovery.py tests/test_phase5_reconciliation.py tests/test_phase6_reconciliation.py tests/test_phase6_concurrency.py
python scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: exit 0.

- [ ] **Step 6: Atualizar controle e commitar**

```bash
git add v2_application v2_host tests docs/refactor/ACTIVE.md
git commit -m "feat: close v2 package recovery and handoff"
```

---

### Task 9: Composition root, E2E, imagem e qualificação

**Files:**
- Create: `v2_host/composition.py`
- Create: `scripts/run_v2_e2e.py`
- Create: `scripts/validate_v2_fasttrack.py`
- Create: `tests/test_v2_e2e.py`
- Create: `Dockerfile.v2`
- Create: `compose.v2.yaml`
- Modify: `v2_host/app.py`
- Modify: `v2_host/settings.py`
- Modify: `v2_host/worker_main.py`
- Modify: `docs/refactor/ACTIVE.md`
- Modify: `docs/refactor/README.md`

**Interfaces:**
- Consumes: todos os contratos e adapters concluídos.
- Produces: `build_container(settings) -> V2Container`; API/worker executáveis; suite E2E; imagem única pronta para dark canary.

- [ ] **Step 1: Escrever RED do composition root e cenários E2E**

```python
# tests/test_v2_e2e.py

def test_production_composition_has_exactly_one_owner_per_port(settings) -> None:
    container = build_container(settings)
    assert container.owner_counts() == EXPECTED_OWNER_COUNTS


def test_lodging_stripe_reaches_completed_with_fake_providers(e2e) -> None:
    report = e2e.run("lodging_stripe")
    assert report.terminal_state == "completed"
    assert report.provider_call_counts == {"cloudbeds.reserve": 1, "stripe.link": 1, "cloudbeds.settle": 1}


def test_activity_pix_reaches_completed_without_bank_confirmation_claim(e2e) -> None:
    report = e2e.run("activity_pix")
    assert report.terminal_state == "completed"
    assert report.visual_evidence_accepted is True
    assert report.bank_settlement_confirmed is False


def test_package_wise_reaches_completed_with_separate_business_units(e2e) -> None:
    report = e2e.run("package_wise")
    assert report.terminal_state == "completed"
    assert report.business_units == ("hostel", "agency")
    assert len(set(report.financial_claims)) == 2


def test_provider_writes_closed_means_zero_external_calls(e2e_closed) -> None:
    report = e2e_closed.run("lodging_stripe")
    assert report.external_provider_calls == 0


def test_public_delivery_closed_means_zero_manychat_calls(e2e_closed) -> None:
    report = e2e_closed.run("activity_pix")
    assert report.manychat_calls == 0


def test_no_legacy_module_or_path_is_loaded(settings) -> None:
    before = set(sys.modules)
    build_container(settings)
    loaded = set(sys.modules) - before
    assert not any(name.split(".")[0] in LEGACY_PREFIXES for name in loaded)
    assert "chapada-leads-hermes" not in "\n".join(sys.path)
```

- [ ] **Step 2: Executar RED**

```bash
python -m pytest -q tests/test_v2_e2e.py
```

Expected: FAIL porque composition root e runner não existem.

- [ ] **Step 3: Implementar composition root único**

`build_container` cria exatamente uma instância de store, inbox, kernel service, model, read adapters, provider adapters e workers. Settings são fail-closed:

```python
provider_reads_enabled: bool = False
cloudbeds_writes_enabled: bool = False
bokun_writes_enabled: bool = False
stripe_writes_enabled: bool = False
settlement_writes_enabled: bool = False
manychat_delivery_enabled: bool = False
live_allowlist: tuple[str, ...] = ()
```

Não existe fallback para planner/agente legado. Config inválida impede startup.

- [ ] **Step 4: Implementar runner E2E determinístico**

`run_v2_e2e.py` cria store temporária, inicia API in-process, injeta providers fake com append-only call logs e executa três cenários obrigatórios:

```text
S1 lodging + Stripe
S2 activity + Pix
S3 package + Wise
```

Relatório JSON contém cenário, estado terminal, command IDs, provider call counts, claims, receipts e zero secrets/PII. Exit 1 para qualquer cenário incompleto ou efeito duplicado.

- [ ] **Step 5: Implementar imagem e processos separados**

`Dockerfile.v2` instala o wheel/runtime no Python 3.12 e possui entrypoints explícitos:

```text
python -m uvicorn v2_host.app:create_default_app --factory --host 0.0.0.0 --port 8788
python -m v2_host.worker_main
```

`compose.v2.yaml` usa a mesma imagem para `v2-api` e `v2-worker`, um volume de store compartilhado e gates todos falsos. Não monta `/home/ubuntu/chapada-leads-hermes`.

- [ ] **Step 6: Executar gate completo local**

Run:

```bash
python scripts/check_fasttrack_boundaries.py
python -m pytest -q
python scripts/run_v2_e2e.py --providers fake --writes disabled --delivery disabled
python scripts/validate_v2_fasttrack.py
python -m compileall -q v2_contracts v2_application v2_adapters v2_host reservation_domain reservation_lookup reservation_confirmation reservation_execution reservation_followup reservation_boundary
DOCKER_BUILDKIT=1 docker build -f Dockerfile.v2 -t agente-v2:fasttrack-candidate .
docker run --rm agente-v2:fasttrack-candidate python scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: todos exit 0; E2E mostra 3/3 cenários completos em fake mode; call logs mostram zero chamadas externas com gates fechados.

- [ ] **Step 7: Revisão única do candidato congelado**

Após commit funcional, revisar apenas o diff desde o commit da Task 1 com foco em:

- imports/caminhos legados;
- capability reachability;
- transações durante chamadas externas;
- idempotência e fences;
- cross-business-unit financeiro;
- mensagem otimista sem receipt;
- secrets/PII;
- defaults de live gates.

Achado material recebe teste RED, correção, regressão focada e nova execução do gate completo uma vez no candidato corrigido.

- [ ] **Step 8: Atualizar controle e commitar**

```bash
git add v2_host scripts/run_v2_e2e.py scripts/validate_v2_fasttrack.py tests/test_v2_e2e.py Dockerfile.v2 compose.v2.yaml docs/refactor
git commit -m "feat: assemble and qualify v2 fasttrack runtime"
```

Em `ACTIVE.md`, marcar Tasks 1–9 concluídas, registrar o commit candidato e definir `NEXT = AWAIT_WRITE_AUTHORIZATION`. Não habilitar nenhum gate real.

- [ ] **Step 9: Gates externos separados, cada um com autorização própria**

A sequência posterior é fixa:

```text
local fake E2E
→ dark canary com reads reais e writes/delivery fechados
→ autorização de um write Cloudbeds/Bókun controlado
→ read-back e reconciliação
→ autorização de pagamento controlado
→ autorização ManyChat apenas para Carlos
→ três conversas humanas completas
→ decisão de rollout
```

Cada seta exige evidência do mesmo commit/imagem e autorização explícita. Falha ou incerteza fecha o gate seguinte.

---

## 3. Definition of Done do produto

O plano termina somente quando o mesmo candidato prova:

1. ManyChat ingress autenticado, idempotente e durável;
2. conversa multi-turno com Maya sem provider capability;
3. FAQ/Cérebro sem invenção;
4. Cloudbeds read + reserva;
5. Bókun read + reserva por ID canônico;
6. pacote com uma confirmação e dois ledgers;
7. Stripe por unidade de negócio e webhook assinado;
8. Wise com match único, assinatura e claim;
9. Pix visual com receiver/valor/status/E2E e sem falsa confirmação bancária;
10. settlement provider específico sem redispatch incerto;
11. pós-pagamento e ManyChat por outbox;
12. conclusão por receipts;
13. handoff único em exceções;
14. restart/reconciliation sem repetir efeito;
15. zero import, execução ou estado do agente legado;
16. defaults de todos os efeitos reais fechados;
17. três cenários E2E verdes;
18. teste humano allowlisted aprovado antes de rollout.

## 4. Itens deliberadamente posteriores ao primeiro candidato

Estes itens não bloqueiam o fast-track porque os ports já deixam a evolução aberta:

- trocar SQLite por PostgreSQL;
- separar API e workers em imagens diferentes;
- substituir adapter Hermes child por endpoint/model service dedicado;
- remover ciclos internos históricos da cápsula `reservation_*`;
- API bancária Pix;
- remover código histórico das Fases 0–7;
- autoscaling e filas externas.

Nenhum desses itens pode ser usado como justificativa para furar os contratos do plano atual.
