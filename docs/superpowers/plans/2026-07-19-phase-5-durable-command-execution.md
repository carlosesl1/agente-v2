# Fase 5 — Comando e execução duráveis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar store SQLite transacional, ledger com lease/fencing, worker com um único dispatch possível, outcome/reconciliação tipados e outbox independente, sem qualquer integração live.

**Architecture:** `reservation_domain` continua autorizando e criando o comando imutável. O novo package `reservation_execution` persiste state/event/command/ledger/outbox em SQLite e coordena adapters/delivery ports exclusivamente injetados; DDL SQLite/PostgreSQL é gerado de um contrato comum, mas somente SQLite é executado.

**Tech Stack:** Python 3.12, `unittest`, `sqlite3`, `multiprocessing`, `dataclasses`, `Protocol`, SHA-256 e JSON canônico da biblioteca padrão; zero dependência externa.

## Global Constraints

- Base obrigatória: `a600d0b5ec403d9faefb7c8f4be00c918717709c` ou descendente limpo que contenha somente a abertura aprovada da Fase 5.
- Trabalhar somente em `/home/ubuntu/agente-v2`; `/home/ubuntu/chapada-leads-hermes` permanece estritamente somente leitura.
- Não executar Docker, PostgreSQL, Supabase, Redis, Hermes, LLM, ManyChat, provider, delivery, deploy, shadow, canary ou rollout live.
- Não criar transport ou adapter default; toda capacidade externa é explicitamente injetada.
- SQLite é a única persistência executável; bancos, WAL e SHM ficam em diretórios temporários e nunca entram no Git.
- O reducer continua único owner da autorização e do `ReservationCommand`.
- Ledger e outbox são tabelas, APIs e workers separados.
- `dispatch_slots_consumed <= 1`, `provider_calls <= 1` e `called_unknown_redispatches == 0` são invariantes de tolerância zero.
- Falha ou crash pós-fence nunca é retryable; progride para `called_unknown/manual_review`.
- Falha de outbox nunca muda ledger, outcome ou counters de dispatch.
- Fixtures/evidências são sintéticas e sanitizadas; nenhum payload bruto, PII real, segredo, banco ou log bruto é versionado.
- Toda função funcional nova começa por teste RED observado; cada RED gera evidência em `docs/refactor/evidence/phase-05/red-result-*.json`.
- Workload gate mínimo: 20.000 properties, 2.000 restart schedules, 50 corridas multiprocesso, seed `2026071905`.
- Job CI da Fase 5: timeout máximo de 15 minutos e RSS máximo observado de 256 MiB.
- Mutantes executam somente em cópias temporárias; casos sensíveis a hash rodam com `PYTHONHASHSEED=0,1,17`.
- Fases 0–4 permanecem gates regressivos.
- Fase 6 não é iniciada; rollout permanece `NO-GO`.

---

## File Structure

### Package funcional

- Create: `reservation_execution/__init__.py` — API pública fechada.
- Create: `reservation_execution/README.md` — ownership, limites e exemplos sem live I/O.
- Create: `reservation_execution/types.py` — enums/DTOs operacionais imutáveis.
- Create: `reservation_execution/adapter.py` — `ExecutionAdapter`, `PreparationFailure` e validação do boundary.
- Create: `reservation_execution/schema.py` — contrato declarativo e DDL determinístico.
- Create: `reservation_execution/sqlite_store.py` — UnitOfWork e operações de ledger/outbox.
- Create: `reservation_execution/projection.py` — outbox final determinística.
- Create: `reservation_execution/worker.py` — `CommandWorker`.
- Create: `reservation_execution/reconciliation.py` — recuperação sem adapter.
- Create: `reservation_execution/outbox.py` — `DeliveryPort` e `OutboxWorker`.
- Create: `reservation_execution/properties.py` — property runner operacional.

### Schema

- Create: `schemas/phase5/sqlite.sql` — DDL gerado e executado.
- Create: `schemas/phase5/postgresql.sql` — contrato gerado, não executado.

### Testes

- Create: `tests/phase5_helpers.py` — workflows/fakes sintéticos compartilhados.
- Create: `tests/test_phase5_domain_outcomes.py`.
- Create: `tests/test_phase5_types.py`.
- Create: `tests/test_phase5_schema.py`.
- Create: `tests/test_phase5_sqlite_store.py`.
- Create: `tests/test_phase5_claims.py`.
- Create: `tests/test_phase5_worker.py`.
- Create: `tests/test_phase5_reconciliation.py`.
- Create: `tests/test_phase5_outbox.py`.
- Create: `tests/test_phase5_fault_injection.py`.
- Create: `tests/test_phase5_concurrency.py`.
- Create: `tests/test_phase5_properties.py`.
- Create: `tests/test_phase5_mutation_runner.py`.

### Scripts, CI e evidência

- Create: `scripts/generate_phase5_schema.py`.
- Create: `scripts/run_phase5_properties.py`.
- Create: `scripts/run_phase5_faults.py`.
- Create: `scripts/run_phase5_mutations.py`.
- Create: `scripts/generate_phase5_manifest.py`.
- Create: `scripts/validate_phase5.py`.
- Create: `.github/workflows/phase5.yml`.
- Modify: `.gitignore` — manter exclusão explícita de SQLite/WAL/SHM.
- Modify: `reservation_domain/types.py` — evidência obrigatória por certainty.
- Modify: `reservation_domain/serialization.py` — roundtrip de outcome, se exposto.
- Modify: `reservation_domain/__init__.py` — exports estritamente necessários.
- Modify: `docs/refactor/06-risk-register.md` — R47–R53 conforme evidência.
- Modify: `docs/refactor/phases/phase-05-durable-command-execution.md` — execução/closeout.
- Modify: `README.md`, `docs/refactor/README.md`, `docs/refactor/evidence/README.md` — estado da fase.

---

### Task 1: Endurecer o contrato de `ExecutionOutcome`

**Files:**
- Modify: `reservation_domain/types.py:483-508`
- Modify: `reservation_domain/serialization.py:223-233`
- Modify: `reservation_domain/__init__.py`
- Create: `tests/test_phase5_domain_outcomes.py`
- Create: `docs/refactor/evidence/phase-05/red-result-domain-outcomes.json`

**Interfaces:**
- Consumes: `ExecutionCertainty`, `ExecutionOutcome`, `ReservationCommand`.
- Produces: `dumps_outcome(outcome: ExecutionOutcome) -> str`, `loads_outcome(raw: str) -> ExecutionOutcome`.

- [ ] **Step 1: Escrever testes RED para certainty/evidence e serializer hostil**

```python
from __future__ import annotations

import json
import unittest

from reservation_domain import (
    ExecutionCertainty,
    ExecutionOutcome,
    dumps_outcome,
    loads_outcome,
)


class Phase5DomainOutcomeTests(unittest.TestCase):
    def test_effect_confirmed_requires_reference_and_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence"):
            ExecutionOutcome(
                command_id="command:phase5:confirmed",
                certainty=ExecutionCertainty.EFFECT_CONFIRMED,
                normalized_status="confirmed",
                provider_reference="provider:synthetic:1",
                evidence=(),
            )

    def test_not_called_rejects_provider_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider_reference"):
            ExecutionOutcome(
                command_id="command:phase5:not-called",
                certainty=ExecutionCertainty.NOT_CALLED,
                normalized_status="not_called",
                provider_reference="provider:impossible",
                evidence=("a" * 64,),
            )

    def test_outcome_roundtrip_is_canonical(self) -> None:
        outcome = ExecutionOutcome(
            command_id="command:phase5:unknown",
            certainty=ExecutionCertainty.CALLED_UNKNOWN,
            normalized_status="response_lost",
            evidence=("b" * 64,),
        )
        raw = dumps_outcome(outcome)
        self.assertEqual(loads_outcome(raw), outcome)
        self.assertEqual(dumps_outcome(loads_outcome(raw)), raw)

    def test_outcome_loader_rejects_duplicate_keys_bool_and_unknown_fields(self) -> None:
        valid = json.loads(dumps_outcome(ExecutionOutcome(
            command_id="command:phase5:no-effect",
            certainty=ExecutionCertainty.CALLED_NO_EFFECT,
            normalized_status="declined",
            evidence=("c" * 64,),
        )))
        valid["data"]["unknown"] = True
        with self.assertRaises(ValueError):
            loads_outcome(json.dumps(valid))
        with self.assertRaises(ValueError):
            loads_outcome('{"schema_version":1,"schema_version":1,"type":"execution_outcome","data":{}}')
```

- [ ] **Step 2: Rodar RED e registrar evidência**

Run:

```bash
set +e
python3 -m unittest tests.test_phase5_domain_outcomes -v \
  >/tmp/phase5-red-domain-outcomes.out 2>&1
code=$?
set -e
test "$code" -ne 0
sha256sum /tmp/phase5-red-domain-outcomes.out
```

Expected: FAIL por `dumps_outcome` ausente e por evidence vazio ainda aceito.

Registrar JSON com `schema_version=1`, phase, command, nonzero exit code,
`expected_failure=true` e o SHA real do output.

- [ ] **Step 3: Implementar validação mínima**

```python
# reservation_domain/types.py — dentro de ExecutionOutcome.__post_init__
if self.certainty is ExecutionCertainty.EFFECT_CONFIRMED:
    if not self.provider_reference:
        raise ValueError("effect_confirmed requires provider_reference")
    if not normalized_evidence:
        raise ValueError("effect_confirmed requires evidence")
if (
    self.certainty is ExecutionCertainty.NOT_CALLED
    and self.provider_reference is not None
):
    raise ValueError("not_called forbids provider_reference")
```

```python
# reservation_domain/serialization.py

def dumps_outcome(outcome: ExecutionOutcome) -> str:
    if type(outcome) is not ExecutionOutcome:
        raise TypeError("outcome must be the exact ExecutionOutcome type")
    return _dumps(outcome, "execution_outcome")


def loads_outcome(raw: str) -> ExecutionOutcome:
    type_tag, data = _loads_payload(raw)
    if type_tag != "execution_outcome":
        raise ValueError(f"unknown outcome type: {type_tag}")
    return _decode_dataclass(ExecutionOutcome, data)
```

- [ ] **Step 4: Rodar GREEN e regressões do domínio**

Run:

```bash
python3 -m unittest \
  tests.test_phase5_domain_outcomes \
  tests.test_phase2_domain \
  tests.test_phase2_serialization \
  tests.test_phase4_replays -v
```

Expected: PASS.

- [ ] **Step 5: Atualizar manifesto/checksum da Fase 2 e validar 0–4**

Run:

```bash
python3 scripts/generate_phase2_matrix.py \
  --write docs/refactor/domain/phase2-state-event-matrix.md \
  --manifest docs/refactor/evidence/phase-02/domain-manifest.json >/dev/null
# atualizar somente hashes realmente alterados em phase-02/SHA256SUMS
python3 scripts/validate_phase2.py
python3 scripts/validate_phase4.py
```

Expected: ambos `status=ok`.

- [ ] **Step 6: Commit**

```bash
git add reservation_domain tests/test_phase5_domain_outcomes.py \
  docs/refactor/evidence/phase-05/red-result-domain-outcomes.json \
  docs/refactor/evidence/phase-02
git commit -m "feat(phase-5): harden execution outcome contract"
```

---

### Task 2: Criar DTOs operacionais e ports fechados

**Files:**
- Create: `reservation_execution/__init__.py`
- Create: `reservation_execution/README.md`
- Create: `reservation_execution/types.py`
- Create: `reservation_execution/adapter.py`
- Create: `tests/test_phase5_types.py`
- Create: `docs/refactor/evidence/phase-05/red-result-execution-types.json`

**Interfaces:**
- Produces: `LedgerStatus`, `OutboxStatus`, `OutboxKind`, `Lease`, `CommandClaim`, `DispatchRequest`, `DispatchPermit`, `OutboxMessage`, `DeliveryReceipt`, `PreparationDisposition`, `PreparationFailure`, `ExecutionAdapter`.

- [ ] **Step 1: Escrever testes RED do universo fechado**

```python
from dataclasses import fields
from datetime import datetime, timedelta, timezone
import inspect
import unittest

from reservation_execution import (
    CommandClaim,
    DispatchPermit,
    DispatchRequest,
    ExecutionAdapter,
    Lease,
    LedgerStatus,
    OutboxKind,
    OutboxMessage,
    OutboxStatus,
    PreparationDisposition,
)

T0 = datetime(2027, 1, 1, tzinfo=timezone.utc)


class Phase5ExecutionTypeTests(unittest.TestCase):
    def test_closed_enums_have_exact_values(self) -> None:
        self.assertEqual([x.value for x in LedgerStatus], [
            "queued", "preparing", "dispatch_fenced",
            "outcome_recorded", "manual_review",
        ])
        self.assertEqual([x.value for x in OutboxStatus], [
            "pending", "leased", "delivered",
        ])
        self.assertEqual(len(OutboxKind), 5)
        self.assertEqual([x.value for x in PreparationDisposition], [
            "requeued", "terminal_not_called",
        ])

    def test_lease_requires_exact_utc_positive_token_and_positive_ttl(self) -> None:
        lease = Lease(
            owner="worker:phase5:a",
            fencing_token=1,
            acquired_at=T0,
            expires_at=T0 + timedelta(seconds=30),
        )
        self.assertEqual(lease.expires_at, T0 + timedelta(seconds=30))
        for kwargs in (
            {"fencing_token": True},
            {"fencing_token": 0},
            {"expires_at": T0},
            {"acquired_at": T0.replace(tzinfo=None)},
        ):
            values = {
                "owner": "worker:phase5:a",
                "fencing_token": 1,
                "acquired_at": T0,
                "expires_at": T0 + timedelta(seconds=30),
            }
            values.update(kwargs)
            with self.assertRaises(ValueError):
                Lease(**values)

    def test_dispatch_permit_has_no_mutable_or_target_selection_fields(self) -> None:
        self.assertEqual(tuple(field.name for field in fields(DispatchPermit)), (
            "command_id", "lease", "dispatch_slot", "request_hash", "fenced_at",
        ))
        self.assertNotIn("provider_ref", inspect.signature(DispatchPermit).parameters)
        self.assertNotIn("offer_id", inspect.signature(DispatchPermit).parameters)

    def test_execution_adapter_is_protocol_only(self) -> None:
        self.assertTrue(getattr(ExecutionAdapter, "_is_protocol", False))
        self.assertEqual(set(ExecutionAdapter.__dict__).intersection({"prepare", "dispatch"}), {"prepare", "dispatch"})
```

- [ ] **Step 2: Rodar RED e registrar evidência**

Run: `python3 -m unittest tests.test_phase5_types -v`.

Expected: ERROR `ModuleNotFoundError: reservation_execution`.

- [ ] **Step 3: Implementar tipos exatos**

```python
class LedgerStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DISPATCH_FENCED = "dispatch_fenced"
    OUTCOME_RECORDED = "outcome_recorded"
    MANUAL_REVIEW = "manual_review"

class OutboxStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    DELIVERED = "delivered"

class PreparationDisposition(str, Enum):
    REQUEUED = "requeued"
    TERMINAL_NOT_CALLED = "terminal_not_called"
```

Implementar as dataclasses exatamente como a spec, com validações:

- tipos exatos (`bool` nunca vale como `int`);
- UTC canônico;
- IDs opacos no mesmo alfabeto do domínio;
- SHA-256 lowercase;
- JSON/payload canônico recomputado;
- `DispatchPermit.dispatch_slot == 1`;
- `OutboxMessage.payload_hash` igual ao hash do payload.

O contrato fechado adicional é:

- `DispatchRequest.from_command(command, canonical_payload)` copia identidade e
  operação somente do `ReservationCommand` e recompõe `payload_hash`;
- payloads são objetos JSON canônicos sem duplicate keys, `NaN` ou `Infinity`;
- `DeliveryReceipt` possui exatamente `message_id`, `delivery_reference`,
  `receipt_hash` e `delivered_at`;
- `receipt_hash` é recomposto do JSON canônico de `message_id`,
  `delivery_reference` e `delivered_at`.

Expandir o RED para verificar os universos exatos de campos de `CommandClaim`,
`DispatchRequest`, `DispatchPermit`, `OutboxMessage` e `DeliveryReceipt`, além de
payload/hash adulterado, JSON não canônico, root não-objeto e receipt divergente.

```python
@runtime_checkable
class ExecutionAdapter(Protocol):
    adapter_id: str
    adapter_version: int

    def prepare(self, command: ReservationCommand) -> DispatchRequest: ...
    def dispatch(
        self,
        request: DispatchRequest,
        *,
        idempotency_key: str,
    ) -> ExecutionOutcome: ...
```

- [ ] **Step 4: Documentar ausência de capacidade default**

`reservation_execution/README.md` deve afirmar explicitamente:

```text
Este package não abre rede, não lê env/auth, não escolhe provider e não possui
adapter/delivery default. Instanciar worker exige ports fornecidos pelo caller.
```

- [ ] **Step 5: Rodar GREEN**

Run:

```bash
python3 -m unittest tests.test_phase5_types -v
python3 -m compileall -q reservation_execution tests
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add reservation_execution tests/test_phase5_types.py \
  docs/refactor/evidence/phase-05/red-result-execution-types.json
git commit -m "feat(phase-5): add durable execution contracts"
```

---

### Task 3: Gerar schema SQLite/PostgreSQL de contrato comum

**Files:**
- Create: `reservation_execution/schema.py`
- Create: `schemas/phase5/sqlite.sql`
- Create: `schemas/phase5/postgresql.sql`
- Create: `scripts/generate_phase5_schema.py`
- Create: `tests/test_phase5_schema.py`
- Modify: `.gitignore`
- Create: `docs/refactor/evidence/phase-05/red-result-schema.json`

**Interfaces:**
- Produces: `SCHEMA_VERSION = 5`, `schema_contract()`, `render_sqlite()`, `render_postgresql()`, `schema_hash(dialect)`.

- [ ] **Step 1: Escrever RED para DDL e constraints**

```python
import sqlite3
import tempfile
from pathlib import Path
import unittest

from reservation_execution.schema import (
    SCHEMA_VERSION,
    render_postgresql,
    render_sqlite,
)

ROOT = Path(__file__).resolve().parents[1]


class Phase5SchemaTests(unittest.TestCase):
    def test_generated_sql_matches_tracked_artifacts(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 5)
        self.assertEqual(
            (ROOT / "schemas/phase5/sqlite.sql").read_text(),
            render_sqlite(),
        )
        self.assertEqual(
            (ROOT / "schemas/phase5/postgresql.sql").read_text(),
            render_postgresql(),
        )

    def test_sqlite_schema_executes_with_foreign_keys_and_checks(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(render_sqlite())
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertEqual(names, {
            "schema_migrations", "workflows", "domain_events",
            "reservation_commands", "execution_ledger", "outbox_messages",
        })

    def test_dispatch_and_idempotency_constraints_fail_closed(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(render_sqlite())
        now = "2027-01-01T00:00:00+00:00"

        def insert_workflow(suffix: str) -> str:
            workflow_id = f"workflow:schema:{suffix}"
            connection.execute(
                "INSERT INTO workflows "
                "(workflow_id, revision, state_type, state_json, state_hash, created_at, updated_at) "
                "VALUES (?, 0, 'collecting_trip_context', '{}', ?, ?, ?)",
                (workflow_id, "a" * 64, now, now),
            )
            return workflow_id

        def insert_command(suffix: str, workflow_id: str, idempotency_key: str) -> str:
            command_id = f"command:schema:{suffix}"
            connection.execute(
                "INSERT INTO reservation_commands "
                "(command_id, idempotency_key, workflow_id, draft_id, draft_version, "
                "subject_signature, operation, command_json, command_hash, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?, 'reserve_lodging', '{}', ?, ?)",
                (
                    command_id,
                    idempotency_key,
                    workflow_id,
                    f"draft:schema:{suffix}",
                    "b" * 64,
                    "c" * 64,
                    now,
                ),
            )
            return command_id

        first_workflow = insert_workflow("one")
        second_workflow = insert_workflow("two")
        first_command = insert_command("one", first_workflow, "idem:schema:shared")
        with self.assertRaises(sqlite3.IntegrityError):
            insert_command("two", second_workflow, "idem:schema:shared")

        connection.execute(
            "INSERT INTO execution_ledger "
            "(command_id, status, claim_owner, fencing_token, lease_acquired_at, "
            "lease_expires_at, claim_count, preparation_failures, "
            "dispatch_slots_consumed, dispatch_request_hash, dispatch_fenced_at, "
            "outcome_json, outcome_hash, updated_at) "
            "VALUES (?, 'queued', NULL, 0, NULL, NULL, 0, 0, 0, NULL, NULL, NULL, NULL, ?)",
            (first_command, now),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE execution_ledger SET dispatch_slots_consumed=2 WHERE command_id=?",
                (first_command,),
            )
```

Os inserts usam somente identidades e valores sintéticos; o teste não omite
colunas do contrato de command ou ledger.

- [ ] **Step 2: Rodar RED e registrar evidência**

Expected: `reservation_execution.schema` ausente.

- [ ] **Step 3: Implementar contrato declarativo**

```python
@dataclass(frozen=True, slots=True)
class ColumnContract:
    name: str
    sqlite_type: str
    postgresql_type: str
    nullable: bool = False
    check: str | None = None

@dataclass(frozen=True, slots=True)
class TableContract:
    name: str
    columns: tuple[ColumnContract, ...]
    table_constraints: tuple[str, ...]
```

`schema_contract()` retorna exatamente seis `TableContract` na ordem:

```python
(
    schema_migrations_contract(),
    workflows_contract(),
    domain_events_contract(),
    reservation_commands_contract(),
    execution_ledger_contract(),
    outbox_messages_contract(),
)
```

Incluir todos os campos/PK/FK/UNIQUE/CHECK da spec. Renderers:

```python
def render_sqlite() -> str:
    return _render("sqlite", schema_contract())


def render_postgresql() -> str:
    return _render("postgresql", schema_contract())
```

Diferenças fechadas:

- SQLite: `TEXT`, `INTEGER`, sem enum nativo;
- PostgreSQL: `text`, `bigint`, `timestamptz`;
- ambos usam checks fechados para status/kind;
- ambos terminam com newline e ordem estável.

Aplicar literalmente a seção 8.7 da spec consolidada. Em particular:

- renderers produzem somente seis `CREATE TABLE`, sem DML/trigger/extension;
- `schema_hash(dialect)` hasheia o UTF-8 do SQL renderizado;
- hashes usam constraint portátil de 64 caracteres `[0-9a-f]`;
- timestamps SQLite usam `+00:00`; PostgreSQL usa `timestamptz`;
- operation/status/kind têm universos fechados;
- ledger e outbox têm as constraints cruzadas de lease, dispatch, outcome e
  receipt;
- FKs não usam cascade;
- PostgreSQL não é executado.

Expandir o RED para provar:

1. ordem e universo exatos das seis tabelas e suas colunas;
2. PKs, FKs, uniques e ausência de triggers no SQLite;
3. rejeição de hash malformado, operation/status/kind desconhecido, revision ou
   counters fora do intervalo e combinações cruzadas inválidas;
4. aceitação de `OUTCOME_RECORDED` com dispatch `0` e outcome presente
   (`not_called` pré-dispatch), além do caminho com dispatch `1`;
5. matriz `PENDING/LEASED/DELIVERED` da outbox;
6. renderer/hash determinísticos, seis statements e nenhuma DML;
7. DDL PostgreSQL contendo `bigint`/`timestamptz`, as mesmas identidades lógicas e
   nenhum marcador SQLite (`PRAGMA`, `AUTOINCREMENT`, `GLOB`).

- [ ] **Step 4: Gerar os dois SQLs**

```bash
python3 scripts/generate_phase5_schema.py \
  --sqlite schemas/phase5/sqlite.sql \
  --postgresql schemas/phase5/postgresql.sql
```

Expected: exit 0 e JSON stdout com hashes dos dois artefatos.

- [ ] **Step 5: Fortalecer `.gitignore`**

Manter as regras existentes e adicionar explicitamente:

```gitignore
*.db-wal
*.db-shm
*.sqlite-wal
*.sqlite-shm
```

- [ ] **Step 6: Rodar GREEN e drift check**

```bash
python3 -m unittest tests.test_phase5_schema -v
python3 scripts/generate_phase5_schema.py \
  --sqlite /tmp/phase5-sqlite.sql \
  --postgresql /tmp/phase5-postgresql.sql >/dev/null
diff -u schemas/phase5/sqlite.sql /tmp/phase5-sqlite.sql
diff -u schemas/phase5/postgresql.sql /tmp/phase5-postgresql.sql
```

Expected: PASS e diffs vazios.

- [ ] **Step 7: Commit**

```bash
git add reservation_execution/schema.py schemas/phase5 \
  scripts/generate_phase5_schema.py tests/test_phase5_schema.py .gitignore \
  docs/refactor/evidence/phase-05/red-result-schema.json
git commit -m "feat(phase-5): define portable execution schema"
```

---

### Task 4: Persistir workflow/evento com optimistic revision

**Files:**
- Create: `reservation_execution/sqlite_store.py`
- Create: `tests/phase5_helpers.py`
- Create: `tests/test_phase5_sqlite_store.py`
- Create: `docs/refactor/evidence/phase-05/red-result-store-core.json`

**Interfaces:**
- Produces: `SQLiteUnitOfWork.open`, `create_workflow`, `load_workflow`, `apply_event`, `PersistedTransition`, `ConcurrencyConflict`, `IdentityConflict`.

- [ ] **Step 1: Criar helper que parte de workflow vazio**

`tests/phase5_helpers.py` não pode importar outro módulo `tests.test_*`. Ele deve
usar adapters públicos Cloudbeds/Bókun com transportes sintéticos locais e expor:

```python
def workflow_events(
    provider: str,
    *,
    workflow_id: str,
) -> tuple[State, tuple[tuple[Event, tuple[OutboxMessage, ...]], ...]]:
    """Return revision-0 state and complete events through accepted confirmation."""


def persist_script(
    store: SQLiteUnitOfWork,
    workflow_id: str,
    script: tuple[tuple[Event, tuple[OutboxMessage, ...]], ...],
) -> tuple[PersistedTransition, ...]:
    results = []
    for event, outbox in script:
        state = store.load_workflow(workflow_id)
        results.append(store.apply_event(
            workflow_id,
            state.meta.revision,
            event,
            outbox=outbox,
        ))
    return tuple(results)


def database_counts(path: Path) -> tuple[int, int, int, int, int]:
    connection = sqlite3.connect(path)
    try:
        return tuple(connection.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0] for table in (
            "workflows", "domain_events", "reservation_commands",
            "execution_ledger", "outbox_messages",
        ))
    finally:
        connection.close()
```

A sequência exata é:

```text
new_workflow
StartSearch
LookupRecorded
OfferChosen
DraftRequested
SummaryRecorded + SUMMARY_PRESENTED outbox
ConfirmationReceived(ACCEPT)
```

Cloudbeds e Bókun precisam usar adapters read-only reais com fakes HTTP finais.

- [ ] **Step 2: Escrever RED do store core**

```python
class Phase5SQLiteStoreTests(unittest.TestCase):
    def test_create_close_reopen_and_load_exact_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phase5.db"
            initial, _ = workflow_events("cloudbeds", workflow_id="workflow:store:1")
            SQLiteUnitOfWork.open(path).create_workflow(initial)
            reopened = SQLiteUnitOfWork.open(path)
            self.assertEqual(reopened.load_workflow(initial.meta.workflow_id), initial)

    def test_apply_event_requires_expected_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phase5.db"
            initial, script = workflow_events("cloudbeds", workflow_id="workflow:store:revision")
            store = SQLiteUnitOfWork.open(path)
            store.create_workflow(initial)
            first_event, first_outbox = script[0]
            store.apply_event(initial.meta.workflow_id, 0, first_event, outbox=first_outbox)
            before = database_counts(path)
            stale_event, stale_outbox = script[1]
            with self.assertRaises(ConcurrencyConflict):
                store.apply_event(initial.meta.workflow_id, 0, stale_event, outbox=stale_outbox)
            self.assertEqual(database_counts(path), before)
            self.assertEqual(store.load_workflow(initial.meta.workflow_id).meta.revision, 1)

    def test_same_event_same_hash_is_idempotent_but_divergence_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phase5.db"
            initial, script = workflow_events("cloudbeds", workflow_id="workflow:store:duplicate")
            store = SQLiteUnitOfWork.open(path)
            store.create_workflow(initial)
            event, outbox = script[0]
            first = store.apply_event(initial.meta.workflow_id, 0, event, outbox=outbox)
            duplicate = store.apply_event(initial.meta.workflow_id, first.state.meta.revision, event, outbox=outbox)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(database_counts(path)[1], 1)
            conflict = replace(event, occurred_at=event.occurred_at + timedelta(seconds=1))
            with self.assertRaises(IdentityConflict):
                store.apply_event(initial.meta.workflow_id, first.state.meta.revision, conflict)
            self.assertEqual(database_counts(path)[1], 1)

    def test_tampered_state_hash_fails_before_reduce(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phase5.db"
            initial, _ = workflow_events("cloudbeds", workflow_id="workflow:store:tamper")
            store = SQLiteUnitOfWork.open(path)
            store.create_workflow(initial)
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE workflows SET state_json=? WHERE workflow_id=?",
                ("{}", initial.meta.workflow_id),
            )
            connection.commit()
            connection.close()
            with self.assertRaises(DataCorruption):
                store.load_workflow(initial.meta.workflow_id)
```

- [ ] **Step 3: Rodar RED e registrar evidência**

Expected: `SQLiteUnitOfWork` ausente.

- [ ] **Step 4: Implementar conexão e transação**

```python
@contextmanager
def _transaction(self):
    self._connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        self._connection.rollback()
        raise
    else:
        self._connection.commit()
```

Na abertura:

```python
connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
connection.execute("PRAGMA foreign_keys = ON")
connection.execute("PRAGMA journal_mode = WAL")
connection.execute("PRAGMA synchronous = FULL")
```

Validar migration version/hash antes de qualquer operação.

- [ ] **Step 5: Implementar `create_workflow`/`load_workflow`**

- serializar com `dumps_state`;
- hash SHA-256 do UTF-8;
- replay idêntico é no-op;
- ID com hash divergente levanta `IdentityConflict`;
- revision SQL precisa ser igual a `state.meta.revision`.

- [ ] **Step 6: Implementar `apply_event` sem command ainda**

A ordem dentro de `BEGIN IMMEDIATE` é:

```python
current = self._load_verified_state(workflow_id)
existing = self._event_by_id(event.event_id)
if existing:
    return self._resolve_duplicate(existing, event, current)
if current.meta.revision != expected_revision:
    raise ConcurrencyConflict(
        f"expected revision {expected_revision}, found {current.meta.revision}"
    )
transition = reduce(current, event)
self._insert_event(event, transition.state.meta.revision)
self._update_state_compare_and_swap(current, transition.state)
return PersistedTransition.from_domain(transition)
```

Se o reducer produzir command antes da Task 5, levantar erro interno e rollback;
não descartar silenciosamente.

- [ ] **Step 7: Rodar GREEN, restart e regressões**

```bash
python3 -m unittest tests.test_phase5_sqlite_store -v
python3 -m unittest tests.test_phase2_serialization tests.test_phase4_replays -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add reservation_execution/sqlite_store.py tests/phase5_helpers.py \
  tests/test_phase5_sqlite_store.py \
  docs/refactor/evidence/phase-05/red-result-store-core.json
git commit -m "feat(phase-5): persist workflows with optimistic revision"
```

---

### Task 5: Persistir resumo/outbox e command/ledger atomicamente

**Files:**
- Modify: `reservation_execution/sqlite_store.py`
- Create: `reservation_execution/projection.py`
- Expand: `tests/test_phase5_sqlite_store.py`
- Create: `docs/refactor/evidence/phase-05/red-result-atomic-command.json`

**Interfaces:**
- Consumes: `OutboxMessage`, `SummaryRecorded`, `Transition.commands`.
- Produces: `LedgerSnapshot`, `load_command`, `load_ledger`, `load_outbox`.

- [ ] **Step 1: Escrever RED para quatro objetos atômicos**

```python
class Phase5AtomicCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "phase5.db"
        self.store = SQLiteUnitOfWork.open(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_summary_requires_matching_outbox_in_same_transaction(self) -> None:
        store, path = self.store, self.path
        initial, script = workflow_events("cloudbeds", workflow_id="workflow:atomic:summary")
        store.create_workflow(initial)
        summary_index = next(
            index for index, (event, _) in enumerate(script)
            if isinstance(event, SummaryRecorded)
        )
        persist_script(store, initial.meta.workflow_id, script[:summary_index])
        before_state = store.load_workflow(initial.meta.workflow_id)
        before_counts = database_counts(path)
        event, _ = script[summary_index]
        with self.assertRaisesRegex(ValueError, "exactly one outbox"):
            store.apply_event(
                initial.meta.workflow_id,
                before_state.meta.revision,
                event,
                outbox=(),
            )
        self.assertEqual(store.load_workflow(initial.meta.workflow_id), before_state)
        self.assertEqual(database_counts(path), before_counts)

    def test_confirmation_persists_state_event_command_and_ledger(self) -> None:
        store, path = self.store, self.path
        initial, script = workflow_events("cloudbeds", workflow_id="workflow:atomic:command")
        store.create_workflow(initial)
        final = persist_script(store, initial.meta.workflow_id, script)[-1]
        self.assertIsInstance(final.state, ExecutionQueuedState)
        self.assertEqual(len(final.commands), 1)
        command = final.commands[0]
        self.assertEqual(store.load_command(command.command_id), command)
        self.assertEqual(store.load_ledger(command.command_id).status, LedgerStatus.QUEUED)
        self.assertEqual(database_counts(path)[2:4], (1, 1))

    def test_duplicate_confirmation_adds_no_command_or_ledger(self) -> None:
        store, path = self.store, self.path
        initial, script = workflow_events("cloudbeds", workflow_id="workflow:atomic:duplicate")
        store.create_workflow(initial)
        final = persist_script(store, initial.meta.workflow_id, script)[-1]
        event, outbox = script[-1]
        replay = store.apply_event(
            initial.meta.workflow_id,
            final.state.meta.revision,
            event,
            outbox=outbox,
        )
        self.assertTrue(replay.duplicate)
        self.assertEqual(database_counts(path)[2:4], (1, 1))

    def test_tampered_command_is_detected_without_state_change(self) -> None:
        store, path = self.store, self.path
        initial, script = workflow_events("cloudbeds", workflow_id="workflow:atomic:tamper")
        store.create_workflow(initial)
        final = persist_script(store, initial.meta.workflow_id, script)[-1]
        command = final.commands[0]
        before_state = store.load_workflow(initial.meta.workflow_id)
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE reservation_commands SET command_json=? WHERE command_id=?",
            ("{}", command.command_id),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(DataCorruption):
            store.load_command(command.command_id)
        self.assertEqual(store.load_workflow(initial.meta.workflow_id), before_state)
```

- [ ] **Step 2: Rodar RED e registrar evidência**

Expected: FAIL porque commands/outbox ainda não são inseridos.

- [ ] **Step 3: Implementar validação da summary outbox**

```python
def _validate_summary_outbox(event, outbox):
    if isinstance(event, SummaryRecorded):
        if len(outbox) != 1:
            raise ValueError("SummaryRecorded requires exactly one outbox message")
        message = outbox[0]
        if (
            message.kind is not OutboxKind.SUMMARY_PRESENTED
            or message.message_id != event.outbox_message_id
        ):
            raise IdentityConflict("summary outbox does not match event")
    elif outbox:
        raise ValueError("caller-provided outbox is only allowed for SummaryRecorded")
```

O payload/hash deve recompor o artefato da Fase 4 pelo helper público; não aceitar
texto solto divergente.

- [ ] **Step 4: Inserir command e ledger antes do commit**

```python
for command in transition.commands:
    self._insert_immutable_command(command)
    self._insert_ledger(command.command_id, LedgerStatus.QUEUED, now=event.occurred_at)
```

Exigir exatamente zero ou um command. O command vem exclusivamente do reducer.

- [ ] **Step 5: Provar rollback em cada statement com triggers**

Nos testes, criar TEMP triggers `RAISE(ABORT, 'fault:<name>')` em:

```text
domain_events BEFORE INSERT
workflows BEFORE UPDATE
reservation_commands BEFORE INSERT
execution_ledger BEFORE INSERT
outbox_messages BEFORE INSERT
```

Após cada abort, reabrir o banco e comparar counts/hashes com o baseline.

- [ ] **Step 6: Rodar GREEN**

```bash
python3 -m unittest tests.test_phase5_sqlite_store -v
```

Expected: PASS e nenhum arquivo DB tracked.

- [ ] **Step 7: Commit**

```bash
git add reservation_execution tests/test_phase5_sqlite_store.py \
  docs/refactor/evidence/phase-05/red-result-atomic-command.json
git commit -m "feat(phase-5): commit command ledger and outbox atomically"
```

---

### Task 6: Implementar claim, lease e fencing

**Files:**
- Modify: `reservation_execution/sqlite_store.py`
- Create: `tests/test_phase5_claims.py`
- Create: `docs/refactor/evidence/phase-05/red-result-claims.json`

**Interfaces:**
- Produces: `claim_command`, `renew_command_lease`, `release_preparation_failure`, `fence_dispatch`.

Em `tests/phase5_helpers.py`, `claim_fixture(test_case)` cria um DB temporário,
persiste até `execution_queued`, registra `store.close` e `temporary.cleanup` em
`test_case.addCleanup`, e retorna `(store, claim_at)`. `claim_at(now,
worker="worker:one")` chama `store.claim_command` com TTL fixo de 30 segundos.

- [ ] **Step 1: Escrever RED de claim/race/token**

```python
class Phase5ClaimTests(unittest.TestCase):
    def test_first_claim_transitions_state_and_increments_token(self) -> None:
        store, claim_at = claim_fixture(self)
        claim = store.claim_command(
            worker_id="worker:one", now=T0, lease_ttl=timedelta(seconds=30)
        )
        self.assertEqual(claim.lease.fencing_token, 1)
        self.assertEqual(store.load_ledger(claim.command.command_id).claim_count, 1)
        self.assertIsInstance(store.load_workflow(claim.command.workflow_id), ExecutingState)

    def test_second_worker_cannot_claim_live_lease(self) -> None:
        store, claim_at = claim_fixture(self)
        claim_at(T0, worker="worker:one")
        self.assertIsNone(store.claim_command(
            worker_id="worker:two", now=T0 + timedelta(seconds=1),
            lease_ttl=timedelta(seconds=30),
        ))

    def test_expired_pre_dispatch_lease_is_recoverable_with_new_token(self) -> None:
        store, claim_at = claim_fixture(self)
        first = claim_at(T0)
        second = claim_at(T0 + timedelta(seconds=31), worker="worker:two")
        self.assertEqual(second.lease.fencing_token, first.lease.fencing_token + 1)

    def test_stale_token_cannot_fence_or_release(self) -> None:
        store, claim_at = claim_fixture(self)
        first = claim_at(T0, worker="worker:one")
        claim_at(T0 + timedelta(seconds=31), worker="worker:two")
        request = DispatchRequest.from_command(first.command, dumps_command(first.command))
        with self.assertRaises(StaleLease):
            store.fence_dispatch(first, request, now=T0 + timedelta(seconds=32))
        failure = PreparationFailure(
            reason="synthetic_preparation_failure",
            retryable=True,
            evidence=("d" * 64,),
        )
        with self.assertRaises(StaleLease):
            store.release_preparation_failure(first, failure, now=T0 + timedelta(seconds=32))

    def test_exactly_one_dispatch_permit_can_exist(self) -> None:
        store, claim_at = claim_fixture(self)
        claim = claim_at(T0, worker="worker:one")
        request = DispatchRequest.from_command(claim.command, dumps_command(claim.command))
        permit = store.fence_dispatch(claim, request, now=T0 + timedelta(seconds=2))
        self.assertEqual(permit.dispatch_slot, 1)
        with self.assertRaises(DispatchAlreadyFenced):
            store.fence_dispatch(claim, request, now=T0 + timedelta(seconds=3))
```

- [ ] **Step 2: Rodar RED e registrar evidência**

Expected: APIs ausentes.

- [ ] **Step 3: Implementar claim transacional**

Seleção elegível:

```sql
status IN ('queued','preparing')
AND dispatch_slots_consumed = 0
AND (claim_owner IS NULL OR lease_expires_at <= :now)
```

No mesmo `BEGIN IMMEDIATE`:

- CAS do ledger;
- incrementa fencing token e claim count;
- aplica `ExecutionStarted` somente se workflow está `execution_queued`;
- se já está `executing`, não cria evento novo;
- retorna command verificado por hash.

- [ ] **Step 4: Implementar falha de preparação**

```python
if failure.retryable and preparation_failures < MAX_PREPARATION_FAILURES:
    # status QUEUED, limpa lease, preserva state Executing e command
    return PreparationDisposition.REQUEUED
# cria outcome NOT_CALLED, aplica ExecutionFinished, outbox final, limpa lease
return PreparationDisposition.TERMINAL_NOT_CALLED
```

Evidence hashes são persistidos; texto de exception não é.

- [ ] **Step 5: Implementar fence com CAS**

```sql
UPDATE execution_ledger
SET status='dispatch_fenced',
    dispatch_slots_consumed=1,
    dispatch_request_hash=:request_hash,
    dispatch_fenced_at=:now,
    updated_at=:now
WHERE command_id=:command_id
  AND status='preparing'
  AND fencing_token=:token
  AND claim_owner=:owner
  AND lease_expires_at>:now
  AND dispatch_slots_consumed=0
```

Exigir `rowcount == 1`.

- [ ] **Step 6: Rodar GREEN**

```bash
python3 -m unittest tests.test_phase5_claims -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add reservation_execution/sqlite_store.py tests/test_phase5_claims.py \
  docs/refactor/evidence/phase-05/red-result-claims.json
git commit -m "feat(phase-5): claim commands with durable fencing"
```

---

### Task 7: Implementar worker e outcome projection

**Files:**
- Create: `reservation_execution/worker.py`
- Complete: `reservation_execution/projection.py`
- Modify: `reservation_execution/sqlite_store.py`
- Create: `tests/test_phase5_worker.py`
- Create: `docs/refactor/evidence/phase-05/red-result-worker.json`

**Interfaces:**
- Produces: `CommandWorker.run_once(now) -> WorkerResult`, `record_outcome`, `project_outcome_outbox`.

- [ ] **Step 1: Criar fake scripted sem rede**

Em `tests/phase5_helpers.py`:

```python
class ScriptedExecutionAdapter:
    adapter_id = "scripted-execution"
    adapter_version = 1

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.prepare_calls = 0
        self.dispatch_calls = 0

    def prepare(self, command):
        self.prepare_calls += 1
        payload = dumps_command(command)
        return DispatchRequest.from_command(command, payload)

    def dispatch(self, request, *, idempotency_key):
        self.dispatch_calls += 1
        action = self.outcomes.pop(0)
        if isinstance(action, Exception):
            raise action
        return action
```

O helper `worker_fixture(test_case, action)` persiste um workflow até
`execution_queued`, registra `test_case.addCleanup(temporary.cleanup)`, constrói
`ScriptedExecutionAdapter([action])` e retorna
`(store, worker, adapter, workflow_id, command_id)`.

- [ ] **Step 2: Escrever RED para quatro certainties e exception**

```python
class Phase5WorkerTests(unittest.TestCase):
    def test_effect_confirmed_calls_dispatch_once_and_persists_success(self) -> None:
        store, worker, adapter, workflow_id, command_id = worker_fixture(
            self, ExecutionCertainty.EFFECT_CONFIRMED
        )
        result = worker.run_once(now=T0)
        self.assertEqual(adapter.dispatch_calls, 1)
        self.assertIsInstance(store.load_workflow(workflow_id), SucceededState)
        self.assertEqual(store.load_ledger(command_id).dispatch_slots_consumed, 1)
        self.assertEqual(store.outbox_count(command_id), 1)

    def test_called_no_effect_is_terminal_without_retry(self) -> None:
        store, worker, adapter, workflow_id, _ = worker_fixture(
            self, ExecutionCertainty.CALLED_NO_EFFECT
        )
        worker.run_once(now=T0)
        self.assertIsInstance(store.load_workflow(workflow_id), FailedNoEffectState)
        self.assertTrue(worker.run_once(now=T0 + timedelta(minutes=1)).idle)
        self.assertEqual(adapter.dispatch_calls, 1)

    def test_called_unknown_goes_to_manual_review_without_redispatch(self) -> None:
        store, worker, adapter, workflow_id, _ = worker_fixture(
            self, ExecutionCertainty.CALLED_UNKNOWN
        )
        worker.run_once(now=T0)
        self.assertIsInstance(store.load_workflow(workflow_id), ManualReviewState)
        self.assertTrue(worker.run_once(now=T0 + timedelta(minutes=1)).idle)
        self.assertEqual(adapter.dispatch_calls, 1)

    def test_exception_after_fence_becomes_unknown_and_second_run_never_dispatches(self) -> None:
        store, worker, adapter, workflow_id, _ = worker_fixture(
            self, RuntimeError("synthetic dispatch failure")
        )
        worker.run_once(now=T0)
        worker.run_once(now=T0 + timedelta(minutes=1))
        self.assertEqual(adapter.dispatch_calls, 1)
        self.assertIsInstance(store.load_workflow(workflow_id), ManualReviewState)

    def test_dispatch_returning_not_called_is_contract_violation_promoted_to_unknown(self) -> None:
        store, worker, adapter, workflow_id, command_id = worker_fixture(
            self, ExecutionCertainty.NOT_CALLED
        )
        worker.run_once(now=T0)
        state = store.load_workflow(workflow_id)
        self.assertIsInstance(state, ManualReviewState)
        self.assertEqual(state.command.command_id, command_id)
        self.assertEqual(state.outcome.certainty, ExecutionCertainty.CALLED_UNKNOWN)
        self.assertEqual(adapter.dispatch_calls, 1)
```

- [ ] **Step 3: Rodar RED e registrar evidência**

Expected: `CommandWorker` ausente.

- [ ] **Step 4: Implementar projeção fechada**

```python
_TEMPLATE_BY_CERTAINTY = {
    ExecutionCertainty.EFFECT_CONFIRMED: (
        OutboxKind.EXECUTION_SUCCEEDED,
        "reservation.execution.succeeded.v1",
    ),
    ExecutionCertainty.NOT_CALLED: (
        OutboxKind.EXECUTION_NOT_CALLED,
        "reservation.execution.not_called.v1",
    ),
    ExecutionCertainty.CALLED_NO_EFFECT: (
        OutboxKind.EXECUTION_FAILED_NO_EFFECT,
        "reservation.execution.no_effect.v1",
    ),
    ExecutionCertainty.CALLED_UNKNOWN: (
        OutboxKind.EXECUTION_MANUAL_REVIEW,
        "reservation.execution.manual_review.v1",
    ),
}
```

IDs/idempotency são SHA-256 derivados de command ID + certainty + template.
Payload público canônico não inclui `provider_ref`, `offer_id`, auth ou texto raw.

- [ ] **Step 5: Implementar `record_outcome`**

Dentro de uma transação:

- validar permit/token/request hash;
- rejeitar outcome de outro command;
- rejeitar outcome divergente já persistido;
- aplicar `ExecutionFinished`;
- se unknown, aplicar `ManualReviewRequested` antes do commit;
- persistir outcome hash, state/eventos e outbox;
- status final `OUTCOME_RECORDED` ou `MANUAL_REVIEW`;
- limpar lease;
- nunca chamar delivery.

- [ ] **Step 6: Implementar `CommandWorker.run_once`**

```python
def run_once(self, *, now: datetime) -> WorkerResult:
    claim = self._store.claim_command(
        worker_id=self._worker_id,
        now=now,
        lease_ttl=self._lease_ttl,
    )
    if claim is None:
        return WorkerResult.idle()
    try:
        request = self._adapter.prepare(claim.command)
    except PreparationFailure as failure:
        return WorkerResult.from_preparation(
            self._store.release_preparation_failure(claim, failure, now=now)
        )
    permit = self._store.fence_dispatch(claim, request, now=now)
    try:
        outcome = self._adapter.dispatch(
            request,
            idempotency_key=claim.command.idempotency_key,
        )
    except Exception:
        outcome = claim.command.outcome(
            certainty=ExecutionCertainty.CALLED_UNKNOWN,
            normalized_status="dispatch_exception",
            evidence=(permit.request_hash,),
        )
    if outcome.certainty is ExecutionCertainty.NOT_CALLED:
        outcome = claim.command.outcome(
            certainty=ExecutionCertainty.CALLED_UNKNOWN,
            normalized_status="invalid_post_fence_not_called",
            evidence=(permit.request_hash,),
        )
    return WorkerResult.completed(
        self._store.record_outcome(permit, outcome, now=now)
    )
```

Capturar somente `Exception` ao redor de `dispatch`, para classificar falha
retornada pelo adapter como unknown; `KeyboardInterrupt`, `SystemExit` e morte do
processo não são engolidos e serão tratados pelo reconciler após expiração.

- [ ] **Step 7: Rodar GREEN**

```bash
python3 -m unittest tests.test_phase5_worker -v
```

Expected: PASS, `dispatch_calls == 1` em todos os schedules pós-fence.

- [ ] **Step 8: Commit**

```bash
git add reservation_execution tests/test_phase5_worker.py \
  docs/refactor/evidence/phase-05/red-result-worker.json
git commit -m "feat(phase-5): execute one fenced dispatch"
```

---

### Task 8: Implementar reconciler sem capacidade de dispatch

**Files:**
- Create: `reservation_execution/reconciliation.py`
- Modify: `reservation_execution/sqlite_store.py`
- Create: `tests/test_phase5_reconciliation.py`
- Create: `docs/refactor/evidence/phase-05/red-result-reconciliation.json`

**Interfaces:**
- Produces: `Reconciler.run_once(now) -> ReconciliationResult`.

Em `tests/phase5_helpers.py`, `queued_store_fixture(test_case)` retorna
`(store, path, workflow_id, command_id)` já persistido em `execution_queued`.
`fenced_store_fixture(test_case, now)` deriva desse fixture, faz claim, prepare e
fence sem chamar dispatch e devolve a mesma tupla. Ambos registram cleanup no
`test_case`.

- [ ] **Step 1: Escrever RED das janelas de crash**

```python
class Phase5ReconciliationTests(unittest.TestCase):
    def test_expired_pre_dispatch_claim_returns_to_queue(self) -> None:
        store, path, workflow_id, _ = queued_store_fixture(self)
        first = store.claim_command(
            worker_id="worker:one", now=T0, lease_ttl=timedelta(seconds=30)
        )
        result = Reconciler(store).run_once(now=T0 + timedelta(seconds=31))
        second = store.claim_command(
            worker_id="worker:two",
            now=T0 + timedelta(seconds=32),
            lease_ttl=timedelta(seconds=30),
        )
        self.assertEqual(result.pre_dispatch_released, 1)
        self.assertEqual(second.command.command_id, first.command.command_id)
        self.assertEqual(second.lease.fencing_token, first.lease.fencing_token + 1)

    def test_expired_post_fence_claim_becomes_unknown_without_adapter(self) -> None:
        store, path, workflow_id, _ = fenced_store_fixture(self, now=T0)
        reconciler = Reconciler(store)
        result = reconciler.run_once(now=T0 + timedelta(minutes=2))
        self.assertEqual(result.called_unknown, 1)
        self.assertIsInstance(store.load_workflow(workflow_id), ManualReviewState)

    def test_impossible_outcome_state_pair_fails_closed(self) -> None:
        store, path, workflow_id, command_id = queued_store_fixture(self)
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE execution_ledger SET status='outcome_recorded' WHERE command_id=?",
            (command_id,),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(DataCorruption):
            Reconciler(store).run_once(now=T0 + timedelta(minutes=2))

    def test_reconciler_public_constructor_accepts_no_adapter(self) -> None:
        self.assertNotIn("adapter", inspect.signature(Reconciler).parameters)
```

- [ ] **Step 2: Rodar RED e registrar evidência**

Expected: module ausente.

- [ ] **Step 3: Implementar queries de recuperação**

`Reconciler` pode chamar somente APIs locais do store:

```python
class Reconciler:
    def __init__(self, store: SQLiteUnitOfWork):
        self._store = store

    def run_once(self, *, now: datetime) -> ReconciliationResult:
        self._store.assert_execution_consistency()
        released = self._store.release_expired_pre_dispatch(now=now)
        unknown = self._store.mark_expired_fenced_unknown(now=now)
        self._store.assert_execution_consistency()
        return ReconciliationResult(
            pre_dispatch_released=released,
            called_unknown=unknown,
        )
```

Nenhum método recebe request, permit ou callback externo.

- [ ] **Step 4: Rodar GREEN e source scan**

```bash
python3 -m unittest tests.test_phase5_reconciliation -v
python3 - <<'PY'
from pathlib import Path
text=Path('reservation_execution/reconciliation.py').read_text()
for forbidden in ('adapter', 'dispatch(', 'socket', 'requests', 'http'):
    assert forbidden not in text, forbidden
PY
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reservation_execution/reconciliation.py \
  reservation_execution/sqlite_store.py tests/test_phase5_reconciliation.py \
  docs/refactor/evidence/phase-05/red-result-reconciliation.json
git commit -m "feat(phase-5): reconcile crashes without redispatch"
```

---

### Task 9: Implementar outbox independente

**Files:**
- Create: `reservation_execution/outbox.py`
- Modify: `reservation_execution/sqlite_store.py`
- Create: `tests/test_phase5_outbox.py`
- Create: `docs/refactor/evidence/phase-05/red-result-outbox.json`

**Interfaces:**
- Produces: `DeliveryPort`, `OutboxClaim`, `OutboxWorker.run_once`, store methods `claim_outbox`, `complete_outbox`, `release_outbox`.

- [ ] **Step 1: Escrever fake delivery e RED**

```python
class ScriptedDeliveryPort:
    delivery_id = "scripted-delivery"
    delivery_version = 1

    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0

    def deliver(self, message):
        self.calls += 1
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action(message)
        return action
```

O helper `outbox_fixture(test_case, actions)` executa previamente um command
sintético até outcome terminal, registra cleanup do tempdir e devolve
`(store, worker, delivery, command_id, message_id)`. A função
`successful_receipt(*, delivered_at: datetime)` retorna uma closure que chama
`receipt_for(message, delivered_at=delivered_at)`.

```python
class Phase5OutboxTests(unittest.TestCase):
    def test_delivery_marks_receipt_without_touching_ledger(self) -> None:
        store, worker, delivery, command_id, message_id = outbox_fixture(self, [
            successful_receipt(delivered_at=T0)
        ])
        before = store.load_ledger(command_id)
        worker.run_once(now=T0)
        self.assertEqual(store.load_ledger(command_id), before)
        self.assertEqual(store.load_outbox(message_id).status, OutboxStatus.DELIVERED)
        self.assertEqual(delivery.calls, 1)

    def test_delivery_failure_releases_only_message(self) -> None:
        store, worker, delivery, command_id, message_id = outbox_fixture(self, [
            RuntimeError("synthetic delivery failure")
        ])
        before = store.load_ledger(command_id)
        worker.run_once(now=T0)
        self.assertEqual(store.load_ledger(command_id), before)
        self.assertEqual(store.load_outbox(message_id).status, OutboxStatus.PENDING)
        self.assertEqual(delivery.calls, 1)

    def test_expired_outbox_lease_is_reclaimable_with_new_token(self) -> None:
        store, _, _, _, _ = outbox_fixture(self, [])
        first = store.claim_outbox(
            worker_id="delivery:one", now=T0, lease_ttl=timedelta(seconds=30)
        )
        second = store.claim_outbox(
            worker_id="delivery:two",
            now=T0 + timedelta(seconds=31),
            lease_ttl=timedelta(seconds=30),
        )
        self.assertEqual(second.message.message_id, first.message.message_id)
        self.assertEqual(second.lease.fencing_token, first.lease.fencing_token + 1)

    def test_stale_delivery_token_cannot_mark_delivered(self) -> None:
        store, _, _, _, _ = outbox_fixture(self, [])
        first = store.claim_outbox(
            worker_id="delivery:one", now=T0, lease_ttl=timedelta(seconds=30)
        )
        store.claim_outbox(
            worker_id="delivery:two",
            now=T0 + timedelta(seconds=31),
            lease_ttl=timedelta(seconds=30),
        )
        receipt = receipt_for(first.message, delivered_at=T0 + timedelta(seconds=32))
        with self.assertRaises(StaleLease):
            store.complete_outbox(first, receipt, now=receipt.delivered_at)

    def test_duplicate_receipt_is_idempotent_but_divergent_receipt_conflicts(self) -> None:
        store, _, _, _, _ = outbox_fixture(self, [])
        claim = store.claim_outbox(
            worker_id="delivery:one", now=T0, lease_ttl=timedelta(seconds=30)
        )
        receipt = receipt_for(claim.message, delivered_at=T0 + timedelta(seconds=1))
        store.complete_outbox(claim, receipt, now=receipt.delivered_at)
        store.complete_outbox(claim, receipt, now=receipt.delivered_at)
        divergent = replace(receipt, receipt_hash="f" * 64)
        with self.assertRaises(IdentityConflict):
            store.complete_outbox(claim, divergent, now=receipt.delivered_at)
```

- [ ] **Step 2: Rodar RED e registrar evidência**

Expected: `OutboxWorker` ausente.

- [ ] **Step 3: Implementar claim separado**

Query elegível somente em `outbox_messages`; nunca fazer join/update em ledger.
Fencing token da outbox é independente do command fencing token.

- [ ] **Step 4: Implementar worker**

```python
def run_once(self, *, now: datetime) -> OutboxWorkerResult:
    claim = self._store.claim_outbox(
        worker_id=self._worker_id,
        now=now,
        lease_ttl=self._lease_ttl,
    )
    if claim is None:
        return OutboxWorkerResult.idle()
    try:
        receipt = self._delivery.deliver(claim.message)
    except Exception:
        self._store.release_outbox(claim, now=now)
        return OutboxWorkerResult.retryable_failure(claim.message.message_id)
    self._store.complete_outbox(claim, receipt, now=now)
    return OutboxWorkerResult.delivered(claim.message.message_id)
```

- [ ] **Step 5: Rodar GREEN e invariant snapshot**

```bash
python3 -m unittest tests.test_phase5_outbox -v
```

Cada teste de delivery deve comparar ledger JSON/hash antes/depois.

- [ ] **Step 6: Commit**

```bash
git add reservation_execution tests/test_phase5_outbox.py \
  docs/refactor/evidence/phase-05/red-result-outbox.json
git commit -m "feat(phase-5): deliver outbox independently"
```

---

### Task 10: Fault injection, restart e concorrência multiprocesso

**Files:**
- Create: `tests/test_phase5_fault_injection.py`
- Create: `tests/test_phase5_concurrency.py`
- Create: `scripts/run_phase5_faults.py`
- Create: `docs/refactor/evidence/phase-05/red-result-faults.json`
- Later generate: `docs/refactor/evidence/phase-05/fault-matrix.json`
- Later generate: `docs/refactor/evidence/phase-05/concurrency-result.json`
- Later generate: `docs/refactor/evidence/phase-05/restart-result.json`

**Interfaces:**
- Produces: deterministic fault runner envelope, no runtime API.

- [ ] **Step 1: Escrever matriz fechada dos 17 fault points**

```python
FAULT_POINTS = (
    "before_event",
    "after_event_before_state",
    "after_state_before_command",
    "after_command_before_ledger",
    "after_ledger_before_commit",
    "after_commit_before_claim",
    "after_claim_before_prepare",
    "during_prepare",
    "after_prepare_before_fence",
    "after_fence_before_dispatch",
    "during_dispatch",
    "after_dispatch_before_outcome",
    "after_outcome_before_state",
    "after_state_before_outbox",
    "after_outbox_before_commit",
    "during_delivery",
    "after_delivery_before_receipt",
)
```

Teste exige igualdade exata com o manifesto/result.

- [ ] **Step 2: Escrever RED para rollback/restart**

Para pontos dentro de transação, usar TEMP triggers ou conexão interrompida e
reabrir o DB. Para pontos entre transações, executar child process que termina
com `os._exit(91)` em uma fronteira controlada pelo scripted port/process harness.

Assertions por schedule:

```python
self.assertLessEqual(snapshot.command_count, 1)
self.assertLessEqual(snapshot.dispatch_slots_consumed, 1)
self.assertLessEqual(call_log.provider_calls, 1)
self.assertEqual(snapshot.partial_transactions, 0)
self.assertEqual(snapshot.called_unknown_redispatches, 0)
```

- [ ] **Step 3: Escrever RED de corrida multiprocesso**

Dois processos abrem o mesmo path e aguardam uma `multiprocessing.Barrier` antes
de `claim_command`. Ambos reportam por Queue; exatamente um recebe claim/token.
Repetir analogamente para outbox.

O fake de provider escreve uma linha por dispatch de modo append-only:

```python
fd = os.open(call_log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
try:
    os.write(fd, (command_id + "\n").encode("utf-8"))
finally:
    os.close(fd)
```

A contagem final precisa ser <= 1.

- [ ] **Step 4: Rodar RED e registrar evidência**

```bash
python3 -m unittest \
  tests.test_phase5_fault_injection \
  tests.test_phase5_concurrency -v
```

Expected: FAIL por runner/harness ausente.

- [ ] **Step 5: Implementar runner determinístico**

CLI:

```text
python3 scripts/run_phase5_faults.py
  --seed 2026071905
  --restart-schedules 2000
  --contention-rounds 50
  --write-fault-matrix PATH
  --write-restart PATH
  --write-concurrency PATH
```

Modo gate rejeita valores menores; `--smoke` permite `17/8/2` somente para unit.

- [ ] **Step 6: Rodar GREEN smoke**

```bash
python3 -m unittest \
  tests.test_phase5_fault_injection \
  tests.test_phase5_concurrency -v
python3 scripts/run_phase5_faults.py \
  --seed 2026071905 \
  --restart-schedules 8 \
  --contention-rounds 2 \
  --smoke \
  --write-fault-matrix /tmp/phase5-faults.json \
  --write-restart /tmp/phase5-restarts.json \
  --write-concurrency /tmp/phase5-concurrency.json
```

Expected: PASS e zero safety violations.

- [ ] **Step 7: Commit**

```bash
git add tests/test_phase5_fault_injection.py tests/test_phase5_concurrency.py \
  scripts/run_phase5_faults.py \
  docs/refactor/evidence/phase-05/red-result-faults.json
git commit -m "test(phase-5): inject crash and concurrency faults"
```

---

### Task 11: Properties operacionais e mutation catalog

**Files:**
- Create: `reservation_execution/properties.py`
- Create: `scripts/run_phase5_properties.py`
- Create: `scripts/run_phase5_mutations.py`
- Create: `tests/test_phase5_properties.py`
- Create: `tests/test_phase5_mutation_runner.py`
- Create: `docs/refactor/evidence/phase-05/red-result-properties.json`
- Create: `docs/refactor/evidence/phase-05/red-result-mutations.json`

**Interfaces:**
- Produces: `Phase5PropertyReport`, `run_phase5_properties(cases, seed)`, `MUTANTS`, `run_mutants`.

- [ ] **Step 1: Escrever RED do relatório bilateral**

```python
class Phase5PropertyTests(unittest.TestCase):
    def test_smoke_covers_both_providers_and_all_outcomes(self) -> None:
        report = run_phase5_properties(cases=160, seed=2026071905)
        self.assertEqual(report.cloudbeds_cases + report.bokun_cases, 160)
        self.assertEqual(set(report.outcome_counts), {
            "not_called", "called_no_effect", "effect_confirmed", "called_unknown",
        })
        for field in (
            "authorized_commands", "terminal_commands", "summary_outboxes",
            "final_outboxes", "expired_lease_recoveries", "stale_token_rejections",
            "post_fence_unknowns", "manual_reviews", "delivery_retries",
            "duplicate_probes", "conflict_probes",
        ):
            self.assertGreater(getattr(report, field), 0, field)
        for field in (
            "unauthorized_commands", "second_commands", "second_dispatch_slots",
            "second_provider_calls", "unknown_redispatches", "outbox_provider_retries",
            "partial_transactions", "stale_token_writes", "missing_terminals",
            "unexpected_exceptions",
        ):
            self.assertEqual(getattr(report, field), 0, field)
        self.assertTrue(report.passed)
```

Cada caso começa em `new_workflow`, passa pelo adapter read-only e Fase 4; proibido
injetar `ExecutionQueuedState` diretamente.

- [ ] **Step 2: Escrever RED do modo gate**

CLI sem `--smoke` rejeita `cases < 20_000`. Envelope esperado:

```json
{
  "schema_version": 1,
  "phase": "phase-05-durable-command-execution",
  "mode": "gate",
  "configuration": {
    "cases": 20000,
    "minimum_gate_cases": 20000,
    "seed": 2026071905
  },
  "result": "passed",
  "report": {}
}
```

- [ ] **Step 3: Rodar RED e registrar evidência**

Expected: API ausente.

- [ ] **Step 4: Implementar gerador e oráculo**

Distribuição determinística por `index % 8`:

```text
0 effect_confirmed
1 called_no_effect
2 called_unknown direto
3 dispatch exception -> unknown
4 retryable prepare then confirmed
5 definitive prepare -> not_called
6 outbox failure then delivery success
7 duplicate/conflict/stale lease probes
```

Alternar providers e usar tempfile DB por shard; nenhum DB no repositório.

- [ ] **Step 5: Definir catálogo com pelo menos 20 mutantes**

`MUTANTS` deve conter, no mínimo, nomes exatos:

```text
remove_optimistic_revision
accept_divergent_event_hash
commit_command_outside_transaction
remove_unique_idempotency
allow_second_dispatch_slot
ignore_fencing_token
recover_post_fence_as_retry
post_fence_exception_as_not_called
allow_not_called_from_dispatch
redispatch_called_unknown
outbox_failure_requeues_command
mark_delivered_without_receipt
accept_divergent_outcome
accept_tampered_command_hash
accept_tampered_state_hash
skip_manual_review
allow_effect_without_evidence
allow_not_called_provider_reference
reduce_property_gate
remove_required_fault_point
```

Cada mutante contém `path`, `old`, `new`, `test`. Runner copia repo para temp,
exclui `.git`, bancos/caches e exige target count 1.

- [ ] **Step 6: Testar hash-seed e working tree**

```python
for seed in ("0", "1", "17"):
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = seed
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase5_mutations.py",
            "--only",
            "remove_required_fault_point",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    self.assertEqual(completed.returncode, 0, completed.stderr)
    report = json.loads(completed.stdout)
    self.assertTrue(report["all_killed"])
    self.assertEqual(report["mutants"][0]["exit_code"], 1)
```

Digest antes/depois cobre `reservation_domain`, `reservation_execution`,
`scripts` e `tests`.

- [ ] **Step 7: Rodar GREEN smoke**

```bash
python3 -m unittest tests.test_phase5_properties tests.test_phase5_mutation_runner -v
python3 scripts/run_phase5_properties.py \
  --cases 160 --seed 2026071905 --smoke \
  --write /tmp/phase5-property-smoke.json >/dev/null
python3 scripts/run_phase5_mutations.py \
  --only allow_second_dispatch_slot >/tmp/phase5-mutant-smoke.json
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add reservation_execution/properties.py scripts/run_phase5_properties.py \
  scripts/run_phase5_mutations.py tests/test_phase5_properties.py \
  tests/test_phase5_mutation_runner.py \
  docs/refactor/evidence/phase-05/red-result-properties.json \
  docs/refactor/evidence/phase-05/red-result-mutations.json
git commit -m "test(phase-5): add operational properties and mutations"
```

---

### Task 12: Gates integrais, evidência, validator, CI e closeout

**Files:**
- Create: `scripts/generate_phase5_manifest.py`
- Create: `scripts/validate_phase5.py`
- Create: `.github/workflows/phase5.yml`
- Create/Update: `docs/refactor/evidence/phase-05/*`
- Modify: `docs/refactor/evidence/phase-05/README.md`
- Modify: `docs/refactor/phases/phase-05-durable-command-execution.md`
- Modify: `docs/refactor/06-risk-register.md`
- Modify: `README.md`, `docs/refactor/README.md`, `docs/refactor/evidence/README.md`

**Interfaces:**
- Produces: reproducible manifests, validation JSON, CI gate and terminal closeout.

- [ ] **Step 1: Rodar suíte completa fresca**

```bash
python3 -m unittest discover -s tests -v \
  >/tmp/phase5-unittest.out 2>&1
```

Capturar exit code, count, elapsed, RSS e output SHA-256 em
`validation-result.json`. Não copiar output bruto.

- [ ] **Step 2: Rodar properties gate**

```bash
python3 scripts/run_phase5_properties.py \
  --cases 20000 \
  --seed 2026071905 \
  --write docs/refactor/evidence/phase-05/property-result.json \
  >/tmp/phase5-properties.stdout
```

Expected: exit 0, zero counters de safety failure.

- [ ] **Step 3: Rodar fault/restart/concurrency gate**

```bash
python3 scripts/run_phase5_faults.py \
  --seed 2026071905 \
  --restart-schedules 2000 \
  --contention-rounds 50 \
  --write-fault-matrix docs/refactor/evidence/phase-05/fault-matrix.json \
  --write-restart docs/refactor/evidence/phase-05/restart-result.json \
  --write-concurrency docs/refactor/evidence/phase-05/concurrency-result.json
```

Expected: 17/17 fault points, 2.000 restarts, 50 races, zero violations.

- [ ] **Step 4: Rodar mutation gate completo**

```bash
python3 scripts/run_phase5_mutations.py \
  --write docs/refactor/evidence/phase-05/mutation-result.json
```

Expected: todos mortos e working tree funcional inalterada exceto JSON esperado.

- [ ] **Step 5: Gerar schema/package manifests e SHA256SUMS**

Manifestos:

```text
schema-manifest.json
package-manifest.json
performance-result.json
SHA256SUMS
```

`schema-manifest` inclui bytes/hash dos dois SQLs e `postgresql_executed=false`.
`package-manifest` inclui todos os `.py` de `reservation_execution`.
`SHA256SUMS` inclui código, testes, scripts, SQLs, docs/evidências e workflows.

- [ ] **Step 6: Implementar validator fechado**

`validate_phase5.py` deve falhar se:

- arquivo obrigatório não está tracked/staged;
- validators 0–4 não estão `ok`;
- DDL/manifests divergem do gerador;
- workload/seed/fault points/races/restarts estão abaixo do mínimo;
- qualquer safety counter não é zero;
- mutantes não correspondem exatamente ao catálogo;
- SQLite/WAL/SHM/log aparece tracked ou em evidência;
- package importa HTTP/SDK/env/auth/subprocess fora dos scripts;
- reconciler contém adapter/dispatch;
- outbox API altera ledger;
- Postgres/Docker/Supabase/provider/delivery live é alegado;
- hashes, links, PII/secret scan ou diff check falham.

- [ ] **Step 7: Criar workflow CI**

`.github/workflows/phase5.yml`:

```yaml
name: phase-5-durable-execution
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  durable-command-execution:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - name: Validate previous phases
        env:
          PHASE1_LEGACY_SOURCE: /path-not-present-in-ci
        run: |
          python3 scripts/validate_phase0.py
          python3 scripts/validate_phase1.py
          python3 scripts/validate_phase2.py
          python3 scripts/validate_phase3.py
          python3 scripts/validate_phase4.py
      - name: Regenerate schemas and manifests
        run: |
          python3 scripts/generate_phase5_schema.py --sqlite /tmp/sqlite.sql --postgresql /tmp/postgresql.sql
          diff -u schemas/phase5/sqlite.sql /tmp/sqlite.sql
          diff -u schemas/phase5/postgresql.sql /tmp/postgresql.sql
          python3 scripts/generate_phase5_manifest.py --check
      - name: Run all tests
        run: python3 -m unittest discover -s tests -v
      - name: Run operational properties
        run: |
          python3 scripts/run_phase5_properties.py --cases 20000 --seed 2026071905 --write docs/refactor/evidence/phase-05/property-result.json
          git diff --exit-code -- docs/refactor/evidence/phase-05/property-result.json
      - name: Run restart race and fault gates
        run: |
          python3 scripts/run_phase5_faults.py --seed 2026071905 --restart-schedules 2000 --contention-rounds 50 --write-fault-matrix docs/refactor/evidence/phase-05/fault-matrix.json --write-restart docs/refactor/evidence/phase-05/restart-result.json --write-concurrency docs/refactor/evidence/phase-05/concurrency-result.json
          git diff --exit-code -- docs/refactor/evidence/phase-05/fault-matrix.json docs/refactor/evidence/phase-05/restart-result.json docs/refactor/evidence/phase-05/concurrency-result.json
      - name: Kill mutation catalog
        run: |
          python3 scripts/run_phase5_mutations.py --write docs/refactor/evidence/phase-05/mutation-result.json
          git diff --exit-code -- docs/refactor/evidence/phase-05/mutation-result.json
      - name: Validate Phase 5
        env:
          PHASE1_LEGACY_SOURCE: /path-not-present-in-ci
        run: python3 scripts/validate_phase5.py
      - name: Compile and whitespace
        run: |
          python3 -m compileall -q reservation_domain reservation_lookup reservation_confirmation reservation_execution characterization scripts tests
          git diff --check
```

Os runners omitem timestamps e duração dos JSONs determinísticos. O CI regenera
os artefatos versionados e exige diff vazio; métricas não determinísticas ficam
somente em `performance-result.json`, produzidas no closeout local.

- [ ] **Step 8: Revisão adversarial inline**

Responder e registrar em `adversarial-review.md`:

1. Existe caminho de command sem confirmação?
2. Existe commit parcial state/command/ledger?
3. Token antigo consegue fence/outcome/receipt?
4. Crash pós-fence consegue redispatch?
5. `dispatch` pode retornar `not_called` e requeue?
6. Unknown chega a manual review?
7. Falha de outbox altera ledger/provider count?
8. DB/hash/event/command/outcome adulterado falha antes de uso?
9. Properties começam em `new_workflow` e atravessam ambos adapters?
10. Mutantes são materiais, determinísticos e temporários?
11. PostgreSQL não executado está declarado sem overclaim?
12. Há qualquer rede/runtime/default adapter?

Qualquer Critical/Important reabre TDD antes do closeout.

- [ ] **Step 9: Validar tudo após a última edição**

```bash
git add -A
git diff --cached --check
python3 scripts/validate_phase0.py
PHASE1_LEGACY_SOURCE=/path-not-present-in-ci python3 scripts/validate_phase1.py
python3 scripts/validate_phase2.py
python3 scripts/validate_phase3.py
python3 scripts/validate_phase4.py
python3 scripts/validate_phase5.py
python3 -m compileall -q reservation_domain reservation_lookup reservation_confirmation reservation_execution characterization scripts tests
```

Expected: zero failures.

- [ ] **Step 10: Commit de implementação e push**

```bash
git commit -m "feat(phase-5): complete durable command execution"
git push origin main
git fetch origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
test "$(git rev-parse HEAD)" = "$(git ls-remote origin refs/heads/main | cut -f1)"
```

Acompanhar todos os workflows do SHA; qualquer failure é blocker e gera RED/RCA.

- [ ] **Step 11: Commit documental de closeout**

Somente após CI verde do commit de implementação:

- gravar IDs/URLs em `ci-result.json`;
- atualizar fase/status/riscos;
- declarar Fase 6 não iniciada e rollout `NO-GO`;
- regenerar sums/manifests;
- validar novamente;
- commit `docs(phase-5): close verified durable execution`;
- push e acompanhar também o CI do SHA terminal.

- [ ] **Step 12: Prova terminal**

```bash
git fetch origin main
local_sha=$(git rev-parse HEAD)
origin_sha=$(git rev-parse origin/main)
remote_sha=$(git ls-remote origin refs/heads/main | cut -f1)
test "$local_sha" = "$origin_sha"
test "$local_sha" = "$remote_sha"
test -z "$(git status --porcelain)"
```

Relatório final contém SHA terminal, tests/properties/faults/mutations, seis
workflows relevantes, fingerprint do legado somente leitura, Fase 6 não
iniciada e rollout `NO-GO`.

---

## Plan Self-Review Checklist

- [x] Cada requisito da spec está coberto por uma Task.
- [x] Nenhuma Task cria adapter/default transport/live capability.
- [x] `release_preparation_failure` terminal não exige `DispatchPermit`.
- [x] Summary outbox é obrigatória e atômica com `SummaryRecorded`.
- [x] Command/ledger vêm somente do reducer e da mesma transação.
- [x] Pós-fence nunca requeue/redispatch.
- [x] Reconciler não aceita adapter.
- [x] Outbox worker não altera ledger.
- [x] 20.000/2.000/50/seed estão consistentes em tests, CLI, evidence, validator e CI.
- [x] Catálogo tem pelo menos 20 mutantes materiais.
- [x] SQLs são gerados do contrato comum e somente SQLite é executado.
- [x] Fases 0–4 permanecem verdes após qualquer mudança compartilhada.
- [x] Nenhum placeholder, assinatura indefinida ou nome divergente permanece.
- [x] Fase 6 e rollout continuam bloqueados.
