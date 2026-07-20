# Fase 7 — Migração das fronteiras — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use per-task review fan-outs. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer runner, plugin e executor consumirem contratos únicos do kernel por `LegacyStateImporter`, `TurnCoordinator`, `ToolDispatch` e `DecisionComparator`, com dual-read/single-write, sem capabilities live e sem tocar a árvore operacional.

**Architecture:** O package puro `reservation_boundary` vive no `agente-v2`; um wheel stdlib determinístico o entrega a uma réplica autenticada do runtime. O coordinator possui ordem/deadline/persistência, o dispatch traduz tools em reads ou comandos duráveis, e o comparator classifica divergências sem importar os reducers comparados. O runtime original é somente leitura; a integração é entregue como patch contra sua réplica.

**Tech Stack:** Python 3.12 stdlib, `dataclasses`, `enum`, `typing.Protocol`, `json`, `hashlib`, `sqlite3`, `zipfile`, `unittest`, `multiprocessing`; `pytest` apenas na réplica já provisionada; SQLite temporário executável e PostgreSQL somente DDL estático.

## Regime econômico vinculante

- Base da fase: `4169c6149f76e8bf4f30a26ee9d0bfbc43a58984`.
- Spec aprovada e corrigida: `580b1da3602308c16c8a45af694fe6c804ce7ffb`.
- Branch/worktree: `phase7-boundary-migration` em `.worktrees/phase7-boundary-migration`.
- Desenvolvimento: somente RED/GREEN focused e regressão pelo blast radius listado em cada tarefa.
- Nenhuma suíte integral, properties integrais, fault matrix integral, restarts, contention ou mutations durante Tasks 1–14.
- Cada RED registra comando, exit code, classe causal, SHA-256 e bytes; raw output fica em `/tmp`.
- Um commit pequeno ao final de cada tarefa; nenhuma revisão por tarefa.
- Runtime original `/home/ubuntu/chapada-leads-hermes`: estritamente somente leitura.
- Réplica durável local: `/home/ubuntu/workspace/agente-v2-phase7-runtime`.
- Nenhum LLM/provider, ManyChat, e-mail, Cloudbeds, Bókun, Wise, Stripe, Pix, Supabase, Redis, PostgreSQL, Docker, deploy, shadow live, canary ou rollout.
- SQLite somente `:memory:` ou diretório temporário.
- Rollout sempre `NO-GO`; `phase8_started=false`.
- Uma janela pesada por tree congelada: estágio local privado não sobreposto + um push remoto.
- Mudança material após congelamento cria novo candidato; correção documental/test-only repete somente gates afetados.

## Estrutura final

```text
pyproject.toml
reservation_boundary/
  __init__.py
  types.py
  serialization.py
  legacy_state.py
  schema.py
  sqlite_store.py
  coordinator.py
  dispatch.py
  shadow.py
  properties.py
  faults.py
schemas/phase7/
  sqlite.sql
  postgresql.sql
scripts/
  build_phase7_wheel.py
  capture_phase7_runtime.py
  generate_phase7_schema.py
  generate_phase7_manifest.py
  run_phase7_properties.py
  run_phase7_faults.py
  run_phase7_mutations.py
  validate_phase7.py
tests/
  phase7_helpers.py
  test_phase7_types.py
  test_phase7_serialization.py
  test_phase7_legacy_state.py
  test_phase7_schema.py
  test_phase7_sqlite_store.py
  test_phase7_coordinator.py
  test_phase7_dispatch.py
  test_phase7_shadow.py
  test_phase7_properties.py
  test_phase7_fault_injection.py
  test_phase7_mutation_runner.py
  test_phase7_package.py
  test_phase7_runtime_capture.py
  test_phase7_closeout.py
docs/refactor/phases/phase-07-boundary-migration.md
docs/refactor/evidence/phase-07/
.github/workflows/phase7.yml
```

A réplica adiciona/modifica somente os paths permitidos pela spec e gera
`docs/refactor/evidence/phase-07/runtime-integration.patch`.

---

### Task 1: Ativar a fase e registrar a entrada econômica

**Files:**
- Create: `docs/refactor/phases/phase-07-boundary-migration.md`
- Create: `docs/refactor/evidence/phase-07/README.md`
- Create: `docs/refactor/evidence/phase-07/entry-baseline.json`
- Create: `tests/test_phase7_closeout.py`
- Modify: `README.md`
- Modify: `docs/refactor/README.md`
- Modify: `docs/refactor/evidence/README.md`
- Modify: `docs/refactor/06-risk-register.md`

**Produces:** fase ativa, base/branch/spec pinadas, baseline 14/14 autenticado, regime econômico e `NO-GO` explícitos.

- [ ] **Step 1: Write entry-document RED**

```python
class Phase7EntryContractTests(unittest.TestCase):
    def test_entry_pins_base_spec_and_single_heavy_window(self) -> None:
        phase = read("docs/refactor/phases/phase-07-boundary-migration.md")
        self.assertIn("4169c6149", phase)
        self.assertIn("05c221186", phase)
        self.assertIn("uma janela", phase.casefold())
        self.assertIn("NO-GO", phase)
        self.assertIn("phase8_started=false", phase)

    def test_entry_evidence_is_real_and_focused(self) -> None:
        payload = read_json("docs/refactor/evidence/phase-07/entry-baseline.json")
        self.assertEqual(payload["focused_tests"], 14)
        self.assertEqual(payload["focused_failures"], 0)
        self.assertEqual(payload["phase6_validator"], "passed")
        self.assertEqual(payload["phase6_manifest"], "passed")
```

- [ ] **Step 2: Run RED and record it**

```bash
python3 -B -m unittest \
  tests.test_phase7_closeout.Phase7EntryContractTests -v \
  >/tmp/phase7-task1-red.out 2>&1
```

Expected: nonzero because phase/evidence files do not exist. Record the sanitized envelope in `red-results.json`; do not version raw output.

- [ ] **Step 3: Write phase/evidence entry docs**

`entry-baseline.json` must contain exact commit/tree, Python/SQLite versions, command, exit `0`, 14 tests, elapsed, output SHA-256, validator/manifest hashes and `runtime_original_status_entries: 80`. No timestamp is accepted without UTC `Z`.

- [ ] **Step 4: Run focused GREEN**

```bash
python3 -B -m unittest \
  tests.test_phase7_closeout.Phase7EntryContractTests -v
python3 -B scripts/validate_phase6.py >/tmp/phase7-task1-phase6-validator.json
python3 -B scripts/generate_phase6_manifest.py --check \
  >/tmp/phase7-task1-phase6-manifest.json
```

- [ ] **Step 5: Commit without review fan-out**

```bash
git add README.md docs/refactor tests/test_phase7_closeout.py
git diff --cached --check
git commit -m "docs(phase-7): activate boundary migration"
```

---

### Task 2: Wheel stdlib determinístico e distribuição fechada

**Files:**
- Create: `pyproject.toml`
- Create: `reservation_boundary/__init__.py` (somente `__version__` nesta tarefa)
- Create: `scripts/build_phase7_wheel.py`
- Create: `tests/test_phase7_package.py`
- Modify: `tests/test_phase7_closeout.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** `chapada_reservation_kernel-0.7.0-py3-none-any.whl` byte-reproducible, sem backend/download externo.

- [ ] **Step 1: Write wheel RED**

```python
class Phase7PackageTests(unittest.TestCase):
    def test_two_builds_are_byte_identical_and_closed(self) -> None:
        first = build_wheel(temp_dir("first"))
        second = build_wheel(temp_dir("second"))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with ZipFile(first) as wheel:
            names = wheel.namelist()
        self.assertTrue(all(allowed_wheel_path(name) for name in names))
        self.assertIn("chapada_reservation_kernel-0.7.0.dist-info/RECORD", names)

    def test_installed_wheel_imports_without_checkout_on_sys_path(self) -> None:
        installed = install_wheel_into_temp_target()
        result = smoke_import_with_checkout_removed(installed)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.payload["version"], "0.7.0")
```

- [ ] **Step 2: Run causal RED**

```bash
python3 -B -m unittest tests.test_phase7_package -v \
  >/tmp/phase7-task2-red.out 2>&1
```

Expected: missing builder/metadata.

- [ ] **Step 3: Declare closed package metadata**

```toml
[project]
name = "chapada-reservation-kernel"
version = "0.7.0"
requires-python = ">=3.12"
dependencies = []

[tool.phase7-wheel]
packages = [
  "reservation_domain",
  "reservation_lookup",
  "reservation_confirmation",
  "reservation_execution",
  "reservation_followup",
  "reservation_boundary",
]
```

Do not add `[build-system]`; `scripts/build_phase7_wheel.py` is the sole builder.

- [ ] **Step 4: Implement deterministic wheel writer**

```python
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
PACKAGES = (
    "reservation_domain",
    "reservation_lookup",
    "reservation_confirmation",
    "reservation_execution",
    "reservation_followup",
    "reservation_boundary",
)


def write_member(archive: ZipFile, name: str, payload: bytes) -> None:
    info = ZipInfo(name, ZIP_TIME)
    info.external_attr = 0o100644 << 16
    info.compress_type = ZIP_DEFLATED
    archive.writestr(info, payload)
```

Sort source paths, normalize LF, reject symlinks/non-`.py`, compute URL-safe SHA-256 `RECORD`, and ensure `RECORD` itself has empty hash/size.

Create `reservation_boundary/__init__.py` with only
`__version__ = "0.7.0"`; no domain type or behavior is implemented before the
Task 3 RED.

- [ ] **Step 5: Run focused GREEN and smoke install**

```bash
python3 -B -m unittest tests.test_phase7_package -v
python3 -B scripts/build_phase7_wheel.py --output-dir /tmp/phase7-wheel-a
python3 -B scripts/build_phase7_wheel.py --output-dir /tmp/phase7-wheel-b
sha256sum /tmp/phase7-wheel-{a,b}/*.whl
python3 -m pip install --no-index --no-deps \
  --target /tmp/phase7-wheel-install /tmp/phase7-wheel-a/*.whl
```

Expected: equal SHA-256 and import only from target.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml reservation_boundary/__init__.py \
  scripts/build_phase7_wheel.py \
  tests/test_phase7_package.py tests/test_phase7_closeout.py \
  docs/refactor/evidence/phase-07/red-results.json
git commit -m "build(phase-7): add deterministic kernel wheel"
```

---

### Task 3: Tipos fechados da fronteira

**Files:**
- Modify: `reservation_boundary/__init__.py`
- Create: `reservation_boundary/types.py`
- Create: `tests/phase7_helpers.py`
- Create: `tests/test_phase7_types.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** enums/dataclasses da spec com exact-type, UTC, identidade opaca e unions discriminadas.

- [ ] **Step 1: Write exact-type RED**

```python
class Phase7TypeTests(unittest.TestCase):
    def test_bool_never_passes_as_version_or_fencing_token(self) -> None:
        with self.assertRaises(ValueError):
            boundary_state(version=True)
        with self.assertRaises(ValueError):
            turn_lease(fencing_token=True)

    def test_confirm_requires_exact_target_draft_version(self) -> None:
        with self.assertRaises(ValueError):
            conversation_intent(kind=CONFIRM, target_draft_version=None)

    def test_choose_offer_requires_opaque_offer_id(self) -> None:
        with self.assertRaises(ValueError):
            conversation_intent(kind=CHOOSE_OFFER, offer_id="Quarto casal")

    def test_boundary_commit_must_equal_reducer_outputs(self) -> None:
        with self.assertRaises(ValueError):
            boundary_commit(commands=(different_command(),))
```

Add table-driven tests for every enum member, duplicate event/payment/command/outbox IDs, naive datetime, unsupported schema, illegal field combinations and mutable input aliasing.

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_types -v \
  >/tmp/phase7-task3-red.out 2>&1
```

Expected: import failure for absent `reservation_boundary.types`/public symbols;
the package bootstrap itself already exists from Task 2.

- [ ] **Step 3: Implement exact public contracts**

Implement the spec names exactly: `ImportDisposition`, `DispatchKind`,
`DivergenceSeverity`, `ConversationIntentKind`, `ImportReason`, `TurnPlanReason`,
`CommandMigrationDisposition`, `BoundaryCommand` as the exact union
`ReservationCommand | PaymentSettlementCommand`, `TypedFact` variants,
`ToolArguments` variants, `LegacyLeadSnapshot`, `ImportResult`,
`ConversationIntent`, `BoundaryState`, `NormalizedMessage`, `IntentRequest`,
`ToolDispatchRequest`, `KernelDecision`, `TurnLease`, `VersionedBoundaryState`,
`BoundaryCommit`, `TurnEnvelope`, `TurnPlan`.

Use `@dataclass(frozen=True, slots=True)` and exact checks such as
`type(value) is int`, `type(value) is str` and `type(value) is datetime`.

- [ ] **Step 4: Run focused GREEN and direct domain regression**

```bash
python3 -B -m unittest tests.test_phase7_types -v
python3 -B -m unittest tests.test_phase2_domain tests.test_phase5_types \
  tests.test_phase6_types -v
```

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary tests/phase7_helpers.py \
  tests/test_phase7_types.py docs/refactor/evidence/phase-07/red-results.json
git commit -m "feat(phase-7): add closed boundary contracts"
```

---

### Task 4: Wire JSON fechado e semantic hashes

**Files:**
- Create: `reservation_boundary/serialization.py`
- Create: `tests/test_phase7_serialization.py`
- Modify: `reservation_boundary/__init__.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** `to_wire_json`, `from_wire_json`, `semantic_hash` e round-trip byte-estável para todos os contratos públicos.

- [ ] **Step 1: Write hostile decoder RED**

```python
class Phase7SerializationTests(unittest.TestCase):
    def test_duplicate_unknown_missing_and_bool_as_int_fail_closed(self) -> None:
        hostile = (
            '{"schema_version":1,"schema_version":1}',
            canonical_json_with_unknown_key(),
            canonical_json_missing("lead_key"),
            canonical_json_with("version", True),
        )
        for payload in hostile:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                from_wire_json(payload, LegacyLeadSnapshot)

    def test_round_trip_is_byte_stable_for_every_public_contract(self) -> None:
        for value in public_contract_examples():
            encoded = to_wire_json(value)
            self.assertEqual(to_wire_json(from_wire_json(encoded, type(value))), encoded)
```

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_serialization -v \
  >/tmp/phase7-task4-red.out 2>&1
```

- [ ] **Step 3: Implement closed registry and canonical codec**

```python
PUBLIC_TYPES = {
    "LegacyLeadSnapshot": LegacyLeadSnapshot,
    "ImportResult": ImportResult,
    "BoundaryState": BoundaryState,
    "ConversationIntent": ConversationIntent,
    "IntentRequest": IntentRequest,
    "ToolDispatchRequest": ToolDispatchRequest,
    "KernelDecision": KernelDecision,
    "TurnLease": TurnLease,
    "VersionedBoundaryState": VersionedBoundaryState,
    "BoundaryCommit": BoundaryCommit,
    "TurnEnvelope": TurnEnvelope,
    "TurnPlan": TurnPlan,
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
```

Reject subclass instances, unknown tags, unknown schema, nonfinite values, noncanonical decimal/date/datetime and unsorted semantically unordered collections.

- [ ] **Step 4: Run GREEN and wire regressions**

```bash
python3 -B -m unittest tests.test_phase7_serialization -v
python3 -B -m unittest tests.test_phase2_serialization tests.test_phase6_types -v
```

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary tests/test_phase7_serialization.py \
  docs/refactor/evidence/phase-07/red-results.json
git commit -m "feat(phase-7): add closed boundary wire format"
```

---

### Task 5: LegacyStateImporter — identidade e estados seguros

**Files:**
- Create: `reservation_boundary/legacy_state.py`
- Create: `tests/test_phase7_legacy_state.py`
- Modify: `reservation_boundary/__init__.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** `import_legacy_state(snapshot) -> ImportResult` para collecting/handoff/manual-review/rejected, sem inferência.

- [ ] **Step 1: Write identity/no-inference RED**

```python
class Phase7LegacyStateTests(unittest.TestCase):
    def test_public_name_never_reconstructs_offer_or_product_id(self) -> None:
        result = import_legacy_state(snapshot(
            stage="fechamento",
            collected_slots=(text_fact("room_name", "Suíte casal"),),
        ))
        self.assertIs(result.disposition, ImportDisposition.MANUAL_REVIEW)
        self.assertIsNone(result.boundary_state.reservation.selected_offer)

    def test_conflicting_canonical_identity_is_rejected(self) -> None:
        result = import_legacy_state(snapshot_with_conflicting_offer_ids())
        self.assertIs(result.disposition, ImportDisposition.REJECTED)
        self.assertIs(result.reason, ImportReason.CONFLICTING_IDENTITY)

    def test_collecting_state_migrates_without_authorization(self) -> None:
        result = import_legacy_state(collecting_snapshot())
        self.assertIs(result.disposition, ImportDisposition.MIGRATED)
        self.assertEqual(result.boundary_state.reservation.phase, WorkflowPhase.COLLECTING)
        self.assertEqual(result.boundary_state.commands, ())
```

Add exact stage matrix, unknown schema, invalid lead key, duplicate subjects, free-text metadata, handoff terminal precedence and deterministic fingerprint tests.

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_legacy_state -v \
  >/tmp/phase7-task5-red.out 2>&1
```

- [ ] **Step 3: Implement staged importer**

Implementation order:

```python
def import_legacy_state(snapshot: LegacyLeadSnapshot) -> ImportResult:
    validate_snapshot_schema_and_identity(snapshot)
    reservation = import_collecting_or_selected_state(snapshot)
    handoff = import_handoff_state(snapshot)
    payments = import_payment_states(snapshot, reservation)
    return classify_import(snapshot, reservation, handoff, payments)
```

A first implementation supports safe collecting and handoff. Selected/confirmed/payment paths return manual review until Task 6; they never silently downgrade to collecting.

- [ ] **Step 4: Run focused GREEN**

```bash
python3 -B -m unittest tests.test_phase7_legacy_state -v
python3 -B -m unittest tests.test_phase2_domain tests.test_phase6_handoff -v
```

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary tests/test_phase7_legacy_state.py \
  docs/refactor/evidence/phase-07/red-results.json
git commit -m "feat(phase-7): import safe legacy boundary state"
```

---

### Task 6: LegacyStateImporter — seleção, confirmação e pagamentos

**Files:**
- Modify: `reservation_boundary/legacy_state.py`
- Modify: `tests/test_phase7_legacy_state.py`
- Modify: `tests/phase7_helpers.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** migração fechada de lookup/selection/draft/confirmation/outcome/payment somente com evidência recomponível.

- [ ] **Step 1: Write advanced-state RED**

```python
class Phase7LegacyAdvancedStateTests(unittest.TestCase):
    def test_selected_offer_requires_provenance_ttl_and_snapshot_hash(self) -> None:
        result = import_legacy_state(selected_snapshot_without_provenance())
        self.assertIs(result.disposition, ImportDisposition.MANUAL_REVIEW)

    def test_confirmation_requires_matching_draft_signature_and_rendered_hash(self) -> None:
        result = import_legacy_state(confirmation_with_mismatched_summary_hash())
        self.assertIs(result.disposition, ImportDisposition.MANUAL_REVIEW)

    def test_paid_ref_requires_effect_confirmed_anchor(self) -> None:
        result = import_legacy_state(paid_snapshot_without_confirmed_outcome())
        self.assertIs(result.disposition, ImportDisposition.MANUAL_REVIEW)
        self.assertEqual(result.boundary_state.payments, ())
```

Add lodging/activity/package matrix, current-vs-stale confirmation, called_unknown, duplicate target, method-specific payment evidence and terminal handoff precedence.

- [ ] **Step 2: Run RED for only advanced cases**

```bash
python3 -B -m unittest \
  tests.test_phase7_legacy_state.Phase7LegacyAdvancedStateTests -v \
  >/tmp/phase7-task6-red.out 2>&1
```

- [ ] **Step 3: Implement evidence-backed mapping**

Reuse canonical functions from prior phases: `select_offer`, `subject_signature`, `rendered_summary_hash`, `command_identity`, `ConfirmedReservationAnchor`, `PaymentSubject`. Never duplicate their formulas.

- [ ] **Step 4: Run GREEN and affected regressions**

```bash
python3 -B -m unittest tests.test_phase7_legacy_state -v
python3 -B -m unittest tests.test_phase3_selection tests.test_phase4_replays \
  tests.test_phase5_domain_outcomes tests.test_phase6_payment -v
```

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary/legacy_state.py tests/test_phase7_legacy_state.py \
  tests/phase7_helpers.py docs/refactor/evidence/phase-07/red-results.json
git commit -m "feat(phase-7): migrate evidenced active workflows"
```

---

### Task 7: Schema e store single-write

**Files:**
- Create: `reservation_boundary/schema.py`
- Create: `reservation_boundary/sqlite_store.py`
- Create: `schemas/phase7/sqlite.sql`
- Create: `schemas/phase7/postgresql.sql`
- Create: `scripts/generate_phase7_schema.py`
- Create: `tests/test_phase7_schema.py`
- Create: `tests/test_phase7_sqlite_store.py`
- Modify: `reservation_boundary/__init__.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** seis tabelas STRICT, CAS/fencing, gênese única, event dedupe e commit atômico de state/command/outbox.

- [ ] **Step 1: Write schema/store RED**

```python
class Phase7SingleWriteStoreTests(unittest.TestCase):
    def test_legacy_port_has_no_write_surface(self) -> None:
        self.assertNotIn("write", dir(LegacyStateReadPort))
        self.assertNotIn("upsert", dir(LegacyStateReadPort))

    def test_genesis_is_single_winner_and_never_dual_writes(self) -> None:
        first = store.import_genesis(import_claim("lead-1", fingerprint="a"))
        second = store.import_genesis(import_claim("lead-1", fingerprint="a"))
        self.assertEqual(first.state_hash, second.state_hash)
        self.assertEqual(store.legacy_write_count, 0)

    def test_state_command_and_outbox_commit_atomically(self) -> None:
        inject_failure("after_command_insert")
        with self.assertRaises(InjectedFailure):
            store.commit(boundary_commit())
        self.assertEqual(store.rows_for_lead("lead-1"), empty_rows())
```

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_schema tests.test_phase7_sqlite_store -v \
  >/tmp/phase7-task7-red.out 2>&1
```

- [ ] **Step 3: Implement DDL and store**

Tables exactly:

```text
boundary_state
boundary_events
boundary_commands
boundary_outbox
legacy_import_claims
decision_comparisons
```

SQLite uses `STRICT`, foreign keys, unique `(lead_key,event_id)`, unique command/outbox IDs, version checks and `BEGIN IMMEDIATE`. PostgreSQL DDL is generated but never executed.

- [ ] **Step 4: Run GREEN and direct storage regressions**

```bash
python3 -B scripts/generate_phase7_schema.py --write
python3 -B scripts/generate_phase7_schema.py --check
python3 -B -m unittest tests.test_phase7_schema tests.test_phase7_sqlite_store -v
python3 -B -m unittest tests.test_phase5_sqlite_store \
  tests.test_phase6_sqlite_store -v
```

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary schemas/phase7 scripts/generate_phase7_schema.py \
  tests/test_phase7_schema.py tests/test_phase7_sqlite_store.py \
  docs/refactor/evidence/phase-07/red-results.json
git commit -m "feat(phase-7): add fenced single-write boundary store"
```

---

### Task 8: TurnCoordinator puro

**Files:**
- Create: `reservation_boundary/coordinator.py`
- Create: `tests/test_phase7_coordinator.py`
- Modify: `reservation_boundary/__init__.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** uma ordem de turno: validate → claim → dedupe → load/import → intent → reducer → atomic commit → persisted plan.

- [ ] **Step 1: Write orchestration RED**

```python
class Phase7CoordinatorTests(unittest.TestCase):
    def test_persists_state_command_and_outbox_before_return(self) -> None:
        trace = []
        plan = coordinator(trace_ports(trace)).coordinate(turn_envelope())
        self.assertEqual(trace, [
            "claim", "load_new", "load_legacy", "import_genesis",
            "intent", "reduce", "commit",
        ])
        self.assertTrue(plan.persisted)

    def test_expired_deadline_has_zero_writes_and_zero_intent_calls(self) -> None:
        result = coordinator().coordinate(expired_envelope())
        self.assertIs(result.reason, TurnPlanReason.DEADLINE_EXPIRED)
        self.assertEqual(fake_store.write_count, 0)
        self.assertEqual(fake_intent.calls, 0)

    def test_cas_loser_reloads_only_new_state(self) -> None:
        coordinator(conflicting_genesis_ports()).coordinate(turn_envelope())
        self.assertEqual(legacy_reader.calls, 1)
        self.assertEqual(new_store.loads, 2)
```

Add duplicate event, invalid intent, manual review, rejected import, post-commit replay and command mismatch tests.

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_coordinator -v \
  >/tmp/phase7-task8-red.out 2>&1
```

- [ ] **Step 3: Implement ports and coordinator**

```python
class TurnCoordinator:
    def __init__(self, *, lock, store, legacy_reader, importer, intent, kernel, clock):
        self.lock = lock
        self.store = store
        self.legacy_reader = legacy_reader
        self.importer = importer
        self.intent = intent
        self.kernel = kernel
        self.clock = clock

    def coordinate(self, envelope: TurnEnvelope) -> TurnPlan:
        validate_deadline(envelope, self.clock.now())
        lease = self.lock.claim(
            lead_key=envelope.lead_key,
            event_id=envelope.event_id,
            now=self.clock.now(),
        )
        current = self.store.load(envelope.lead_key)
        if current is None:
            current = self._import_once(envelope, lease)
        intent_request = IntentRequest(
            event_id=envelope.event_id,
            state=current.state,
            message=envelope.message,
            deadline_at=envelope.deadline_at,
        )
        intent = self.intent.interpret(intent_request)
        decision = self.kernel.reduce(current.state, intent)
        persisted = self.store.commit(commit_from(decision, lease, envelope))
        return plan_from(persisted, decision)
```

No provider calls, no filesystem/env/process import and no public reply composition.

- [ ] **Step 4: Run GREEN and blast-radius regressions**

```bash
python3 -B -m unittest tests.test_phase7_coordinator -v
python3 -B -m unittest tests.test_phase2_domain tests.test_phase5_claims \
  tests.test_phase6_payment_claims -v
```

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary/coordinator.py reservation_boundary/__init__.py \
  tests/test_phase7_coordinator.py \
  docs/refactor/evidence/phase-07/red-results.json
git commit -m "feat(phase-7): coordinate durable boundary turns"
```

---

### Task 9: ToolDispatch único e catálogo fechado

**Files:**
- Create: `reservation_boundary/dispatch.py`
- Create: `tests/test_phase7_dispatch.py`
- Modify: `reservation_boundary/__init__.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** classificação única de tool como read/command/state_commit, aliases
sem escalada, quatro writes convertidos em commands Fases 5/6 e três writes
bloqueados em manual review, nunca provider calls.

- [ ] **Step 1: Write classification/authorization RED**

```python
class Phase7DispatchTests(unittest.TestCase):
    def test_unknown_and_alias_category_escalation_fail_closed(self) -> None:
        with self.assertRaises(DispatchRejected):
            dispatch.dispatch(tool_request("unknown_tool"))
        with self.assertRaises(DispatchRejected):
            dispatch.dispatch(alias_request("availability", pretending="write"))

    def test_write_tool_emits_command_without_provider_call(self) -> None:
        result = dispatch.dispatch(confirmed_lodging_write_request())
        self.assertIs(result.kind, DispatchKind.COMMAND)
        self.assertEqual(result.commands, (expected_reservation_command(),))
        self.assertEqual(provider.calls, 0)

    def test_only_four_writes_have_durable_command_owners(self) -> None:
        self.assertEqual(
            command_migration_counts(),
            {"reservation": 2, "payment_settlement": 2, "blocked_unmigrated": 3},
        )

    def test_unmigrated_writes_require_manual_review_without_executor(self) -> None:
        for name in (
            "wise_verificar_pagamento",
            "cloudbeds_gerar_link_pagamento_stripe",
            "bokun_gerar_link_pagamento_stripe",
        ):
            with self.subTest(name=name):
                result = dispatch.dispatch(valid_request_for(name))
                self.assertIs(result.reason, TurnPlanReason.MANUAL_REVIEW)
                self.assertEqual(result.commands, ())
                self.assertEqual(provider.calls, 0)

    def test_llm_confirmation_boolean_cannot_authorize(self) -> None:
        with self.assertRaises(DispatchRejected):
            dispatch.dispatch(write_request(arguments={"confirmed": True}))
```

Add each catalog entry, exact args union, read cache, stale revalidation, package atomicity, state commit whitelist, deadline and called_unknown tests.

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_dispatch -v \
  >/tmp/phase7-task9-red.out 2>&1
```

- [ ] **Step 3: Implement literal catalog and ports**

```python
CATALOG: Mapping[str, ToolContract] = MappingProxyType({
    "cerebro_consultar": ToolContract(
        kind=DispatchKind.READ,
        arguments_type=FaqReadArguments,
    ),
    "cloudbeds_consultar_hospedagem_v2": ToolContract(
        kind=DispatchKind.READ,
        arguments_type=LodgingReadArguments,
    ),
    "cloudbeds_descrever_quartos": ToolContract(
        kind=DispatchKind.READ,
        arguments_type=RoomDescriptionReadArguments,
    ),
    "bokun_consultar_passeio_v2": ToolContract(
        kind=DispatchKind.READ,
        arguments_type=ActivityReadArguments,
    ),
    "bokun_consultar_descricao": ToolContract(
        kind=DispatchKind.READ,
        arguments_type=ActivityDescriptionReadArguments,
    ),
    "cloudbeds_criar_reserva_v2": ToolContract(
        kind=DispatchKind.COMMAND,
        arguments_type=LodgingReservationCommandArguments,
        command_migration=CommandMigrationDisposition.RESERVATION,
    ),
    "bokun_agendar_passeio_v2": ToolContract(
        kind=DispatchKind.COMMAND,
        arguments_type=ActivityReservationCommandArguments,
        command_migration=CommandMigrationDisposition.RESERVATION,
    ),
    "cloudbeds_lancar_pagamento_confirmar_reserva": ToolContract(
        kind=DispatchKind.COMMAND,
        arguments_type=LodgingPaymentCommandArguments,
        command_migration=CommandMigrationDisposition.PAYMENT_SETTLEMENT,
    ),
    "bokun_lancar_pagamento_confirmar_reserva": ToolContract(
        kind=DispatchKind.COMMAND,
        arguments_type=ActivityPaymentCommandArguments,
        command_migration=CommandMigrationDisposition.PAYMENT_SETTLEMENT,
    ),
    "wise_verificar_pagamento": ToolContract(
        kind=DispatchKind.COMMAND,
        arguments_type=WiseVerificationCommandArguments,
        command_migration=CommandMigrationDisposition.BLOCKED_UNMIGRATED,
    ),
    "cloudbeds_gerar_link_pagamento_stripe": ToolContract(
        kind=DispatchKind.COMMAND,
        arguments_type=LodgingPaymentLinkCommandArguments,
        command_migration=CommandMigrationDisposition.BLOCKED_UNMIGRATED,
    ),
    "bokun_gerar_link_pagamento_stripe": ToolContract(
        kind=DispatchKind.COMMAND,
        arguments_type=ActivityPaymentLinkCommandArguments,
        command_migration=CommandMigrationDisposition.BLOCKED_UNMIGRATED,
    ),
    "chapada_commit_state": ToolContract(
        kind=DispatchKind.STATE_COMMIT,
        arguments_type=StateCommitArguments,
    ),
})
```

These are the exact 13 v2-active names observed in
`domain/chapada_native_tools.py` at runtime base
`57408d8b2040399bc25ee7957505208079458884`. Task 12 must
derive the same set independently from the runtime schema manifest; extra or
missing names block closeout. Provider write adapters are not accepted as
constructor args. The two reservation writes map to `ReservationCommand`; the
two payment-registration writes require verified evidence/current financial
confirmation and map to `PaymentSettlementCommand`; Wise verification and both
Stripe-link generators return `BLOCKED_UNMIGRATED` + manual review with zero
executor calls.

- [ ] **Step 4: Run GREEN and affected regressions**

```bash
python3 -B -m unittest tests.test_phase7_dispatch -v
python3 -B -m unittest tests.test_phase3_bokun_adapter \
  tests.test_phase3_cloudbeds_adapter tests.test_phase5_domain_outcomes -v
```

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary/dispatch.py reservation_boundary/__init__.py \
  tests/test_phase7_dispatch.py docs/refactor/evidence/phase-07/red-results.json
git commit -m "feat(phase-7): centralize typed tool dispatch"
```

---

### Task 10: DecisionComparator independente

**Files:**
- Create: `reservation_boundary/shadow.py`
- Create: `tests/test_phase7_shadow.py`
- Modify: `reservation_boundary/__init__.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** comparação determinística old/new com severidade fechada e totais reconstruíveis.

- [ ] **Step 1: Write severity-oracle RED**

```python
class Phase7ShadowTests(unittest.TestCase):
    def test_authorization_identity_and_certainty_divergences_are_critical(self) -> None:
        for field in (
            "command_identities", "subject_signature", "effect_certainties",
            "handoff_required", "claim_evidence",
        ):
            old, new = observations_differing_only(field)
            self.assertIs(compare(old, new).severity, DivergenceSeverity.CRITICAL)

    def test_copy_only_difference_is_noncritical(self) -> None:
        result = compare(observation(copy_hash="a"), observation(copy_hash="b"))
        self.assertIs(result.severity, DivergenceSeverity.NONCRITICAL)

    def test_comparator_source_does_not_import_old_or_new_reducers(self) -> None:
        self.assertEqual(forbidden_comparator_imports(), [])
```

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_shadow -v \
  >/tmp/phase7-task10-red.out 2>&1
```

- [ ] **Step 3: Implement literal field policy**

```python
CRITICAL_FIELDS = frozenset({
    "handoff_required",
    "subject_signature",
    "command_identities",
    "dispatch_kinds",
    "effect_certainties",
    "claim_evidence",
    "persistence_order",
})
NONCRITICAL_FIELDS = frozenset({"route_label", "copy_hash", "diagnostic_tags"})


def compare(old: DecisionObservation, new: DecisionObservation) -> DecisionComparison:
    changed = exact_changed_fields(old, new)
    severity = classify_from_closed_sets(changed)
    return DecisionComparison(
        old_hash=semantic_hash(old),
        new_hash=semantic_hash(new),
        changed_fields=tuple(sorted(changed)),
        severity=severity,
    )
```

Reject observations with unknown fields/types and summaries whose totals do not equal rows.

- [ ] **Step 4: Run GREEN**

```bash
python3 -B -m unittest tests.test_phase7_shadow -v
python3 -B -m unittest tests.test_phase4_replays tests.test_phase6_properties -v
```

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary/shadow.py reservation_boundary/__init__.py \
  tests/test_phase7_shadow.py docs/refactor/evidence/phase-07/red-results.json
git commit -m "feat(phase-7): classify boundary decision divergence"
```

---

### Task 11: Properties, faults, restarts, contention e mutations — harness only

**Files:**
- Create: `reservation_boundary/properties.py`
- Create: `reservation_boundary/faults.py`
- Create: `scripts/run_phase7_properties.py`
- Create: `scripts/run_phase7_faults.py`
- Create: `scripts/run_phase7_mutations.py`
- Create: `tests/test_phase7_properties.py`
- Create: `tests/test_phase7_fault_injection.py`
- Create: `tests/test_phase7_mutation_runner.py`
- Modify: `reservation_boundary/__init__.py`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** runners determinísticos configuráveis. Durante desenvolvimento, somente contagens focused mínimas; contagens integrais ficam bloqueadas ao CI congelado.

- [ ] **Step 1: Write harness-integrity RED**

```python
class Phase7PropertyHarnessTests(unittest.TestCase):
    def test_small_run_is_deterministic_and_reconstructs_totals(self) -> None:
        first = run_property_sequences(seed=2026072007, cases=25)
        second = run_property_sequences(seed=2026072007, cases=25)
        self.assertEqual(first, second)
        self.assertEqual(first.total, len(first.rows))

    def test_integral_counts_require_frozen_candidate_environment(self) -> None:
        with self.assertRaises(RuntimeError):
            run_property_sequences(seed=2026072007, cases=20_000)

class Phase7MutationHarnessTests(unittest.TestCase):
    def test_catalog_is_closed_and_each_mutant_has_one_owner(self) -> None:
        self.assertEqual(len(MUTANTS), 12)
        self.assertEqual(len({m.name for m in MUTANTS}), 12)
```

Mutants cover ID inference, dual-write, bool-as-int, stale confirmation, command-in-turn, alias escalation, deadline write, CAS bypass, comparator downgrade, plugin business guard, process execution and duplicate JSON.

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_properties \
  tests.test_phase7_fault_injection tests.test_phase7_mutation_runner -v \
  >/tmp/phase7-task11-red.out 2>&1
```

- [ ] **Step 3: Implement configurable deterministic harnesses**

Integral constants:

```python
PROPERTY_SEED = 2026072007
PROPERTY_CASES = 20_000
RESTART_SCHEDULES = 2_000
CONTENTION_DOMAINS = ("genesis", "event", "command", "outbox")
CONTENTION_ROUNDS_PER_DOMAIN = 50
MUTANT_COUNT = 12
```

Integral mode requires `PHASE7_FROZEN_TREE` equal to `git write-tree`; tests mock this guard rather than setting it globally.

- [ ] **Step 4: Run only focused harness sizes**

```bash
python3 -B -m unittest tests.test_phase7_properties \
  tests.test_phase7_fault_injection tests.test_phase7_mutation_runner -v
python3 -B scripts/run_phase7_properties.py --cases 100 --seed 2026072007 \
  --output /tmp/phase7-task11-properties.json
python3 -B scripts/run_phase7_faults.py --focused \
  --output /tmp/phase7-task11-faults.json
python3 -B scripts/run_phase7_mutations.py --focused \
  --output /tmp/phase7-task11-mutations.json
```

Do not run integral flags.

- [ ] **Step 5: Commit**

```bash
git add reservation_boundary/properties.py reservation_boundary/faults.py \
  reservation_boundary/__init__.py scripts/run_phase7_properties.py \
  scripts/run_phase7_faults.py scripts/run_phase7_mutations.py \
  tests/test_phase7_properties.py tests/test_phase7_fault_injection.py \
  tests/test_phase7_mutation_runner.py \
  docs/refactor/evidence/phase-07/red-results.json
git commit -m "test(phase-7): add bounded boundary adversarial harnesses"
```

---

### Task 12: Captura segura e manifest da réplica do runtime

**Files:**
- Create: `scripts/capture_phase7_runtime.py`
- Create: `tests/test_phase7_runtime_capture.py`
- Create: `docs/refactor/evidence/phase-07/runtime-contract-manifest.json`
- Modify: `docs/refactor/evidence/phase-07/red-results.json`

**Produces:** clone local independente, base+tracked patch+untracked allowlist autenticados, sem alterar source.

- [ ] **Step 1: Write synthetic-repository RED**

```python
class Phase7RuntimeCaptureTests(unittest.TestCase):
    def test_capture_reconstructs_tracked_and_allowlisted_untracked_without_source_drift(self) -> None:
        source = synthetic_dirty_runtime()
        before = source_fingerprint(source)
        result = capture(source, output=temp_path("replica"))
        self.assertEqual(source_fingerprint(source), before)
        self.assertEqual(result.reconstructed_tree, expected_tree(source))

    def test_capture_rejects_secret_pii_db_log_symlink_and_unallowlisted_path(self) -> None:
        for hostile in hostile_runtime_inputs():
            with self.subTest(hostile=hostile), self.assertRaises(CaptureRejected):
                capture(hostile.source, output=temp_path("replica"))
```

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_runtime_capture -v \
  >/tmp/phase7-task12-red.out 2>&1
```

- [ ] **Step 3: Implement local-only capture**

Algorithm:

```text
assert source HEAD == 57408d8b2040399bc25ee7957505208079458884
fingerprint source HEAD/tree/status/tracked diff/untracked allowlist
create git clone --no-local into output
write tracked binary diff to /tmp and git apply in clone
copy only allowlisted untracked regular files after scan
assert reconstructed tracked/untracked hashes
commit local synthetic baseline inside clone
fingerprint source again and require equality
write sanitized manifest; never copy raw diff into agente-v2
```

Use subprocess argument arrays, no shell interpolation, output path refusal if it exists, and local git identity scoped to the clone.

- [ ] **Step 4: Run focused synthetic GREEN**

```bash
python3 -B -m unittest tests.test_phase7_runtime_capture -v
```

- [ ] **Step 5: Capture the real source once, without running tests**

```bash
python3 -B scripts/capture_phase7_runtime.py \
  --source /home/ubuntu/chapada-leads-hermes \
  --output /home/ubuntu/workspace/agente-v2-phase7-runtime \
  --manifest docs/refactor/evidence/phase-07/runtime-source-manifest.json
```

Verify source still has exact HEAD/tree/status/diff/untracked fingerprints. This is capture, not heavy validation.

- [ ] **Step 6: Generate schema/tool contract manifest from the replica**

The manifest contains only tool names, categories, JSON schemas, function signatures, source hashes and counts. Reject descriptions/defaults containing secrets/PII.

- [ ] **Step 7: Commit scripts/tests/manifests**

```bash
git add scripts/capture_phase7_runtime.py tests/test_phase7_runtime_capture.py \
  docs/refactor/evidence/phase-07/runtime-source-manifest.json \
  docs/refactor/evidence/phase-07/runtime-contract-manifest.json \
  docs/refactor/evidence/phase-07/red-results.json
git commit -m "build(phase-7): authenticate isolated runtime replica"
```

---

### Task 13: Runtime adapter — TurnCoordinator e app boundary

**Primary workspace:** `/home/ubuntu/workspace/agente-v2-phase7-runtime`

**Files in replica:**
- Create: `domain/turn_coordinator_adapter.py`
- Create: `tests/test_phase7_turn_coordinator_adapter.py`
- Modify: `app.py`

**Files in agente-v2:** nenhum nesta tarefa; o commit durável ocorre na réplica e
é empacotado junto com a Task 14 no integration patch.

**Produces:** `_process_event` delegates state/import/order/persistence to a thin adapter while old route is test-only oracle.

- [ ] **Step 1: Install the current wheel into an isolated target**

```bash
python3 -B scripts/build_phase7_wheel.py --output-dir /tmp/phase7-task13-wheel
install_target=$(mktemp -d /tmp/phase7-task13-install.XXXXXX)
printf '%s\n' "$install_target" > /tmp/phase7-task13-install.path
python3 -m pip install --no-index --no-deps \
  --target "$install_target" /tmp/phase7-task13-wheel/*.whl
```

No pre-existing directory is removed. Subsequent commands read the exact target
from `/tmp/phase7-task13-install.path`.

- [ ] **Step 2: Write adapter RED in the replica**

Create four tests with exact assertions:

1. `test_process_event_persists_before_public_delivery`: trace must be
   `claim, load, intent, reduce, commit, enqueue`; delivery before commit fails.
2. `test_legacy_state_is_read_once_and_never_written`: one legacy read, zero
   legacy writes, one typed genesis.
3. `test_nonmigrable_state_handoffs_without_public_fact_claim`: manual review,
   zero commands and no availability/price/reservation claim.
4. `test_deadline_expiry_has_zero_tool_provider_and_manychat_calls`: all three
   injected call counters remain zero.

- [ ] **Step 3: Run only adapter RED**

```bash
install_target=$(python3 -c 'from pathlib import Path; print(Path("/tmp/phase7-task13-install.path").read_text().strip())')
env -i \
  PATH=/usr/local/bin:/usr/bin:/bin \
  HOME=/tmp \
  PYTHONPATH="$install_target:/home/ubuntu/workspace/agente-v2-phase7-runtime" \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/workspace/agente-v2-phase7-runtime/config/leads_agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_phase7_turn_coordinator_adapter.py \
  >/tmp/phase7-task13-red.out 2>&1
```

Expected: missing adapter/new delegation.

- [ ] **Step 4: Implement thin adapter and app seam**

`app.py` may select old path only under an injected test oracle; no env feature flag activates live behavior. The adapter receives ports explicitly and exposes no provider method.

- [ ] **Step 5: Run focused GREEN and app blast radius**

```bash
install_target=$(python3 -c 'from pathlib import Path; print(Path("/tmp/phase7-task13-install.path").read_text().strip())')
env -i \
  PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp \
  PYTHONPATH="$install_target:/home/ubuntu/workspace/agente-v2-phase7-runtime" \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/workspace/agente-v2-phase7-runtime/config/leads_agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_phase7_turn_coordinator_adapter.py \
  tests/test_app_llm_central_webhook.py tests/test_app_shadow_webhook.py \
  tests/test_manychat_single_confirmation_flow.py
```

Do not run the full runtime suite.

- [ ] **Step 6: Commit only inside the isolated clone**

```bash
git add app.py domain/turn_coordinator_adapter.py \
  tests/test_phase7_turn_coordinator_adapter.py
git commit -m "refactor(phase-7): route turns through boundary coordinator"
```

No push. Source runtime must remain unchanged.

---

### Task 14: Runtime adapter — dispatch, runner, executor e plugin fino

**Primary workspace:** `/home/ubuntu/workspace/agente-v2-phase7-runtime`

**Files in replica:**
- Create: `domain/tool_dispatch_adapter.py`
- Create: `tests/test_phase7_tool_dispatch_adapter.py`
- Create: `tests/test_phase7_runtime_boundary.py`
- Modify: `domain/hermes_native_runner.py`
- Modify: `domain/tool_executor.py`
- Modify: `domain/chapada_native_tools.py`
- Modify: `.hermes/plugins/chapada_leads_tools/__init__.py`

**Files in agente-v2:**
- Create/update: `docs/refactor/evidence/phase-07/runtime-integration.patch`

**Produces:** runner apenas transporta intent/tool results, plugin apenas registra/marshals, executor fica atrás do dispatch, e nenhuma dessas superfícies autoriza side effects.

- [ ] **Step 1: Write unique-ownership RED**

Create eight tests with exact assertions:

1. every plugin handler reaches one `dispatch_native_tool` symbol;
2. runner AST has zero owners for budget, confirmation, retry or provider write;
3. executor is reachable only behind typed dispatch;
4. two reservation + two settlement tools yield their exact durable command and
   zero provider calls in-turn;
5. Wise verification + two Stripe-link tools produce `BLOCKED_UNMIGRATED`,
   manual review and zero executor/provider calls;
6. aliases cannot change runtime category;
7. plugin/runner/executor import no kernel business helper;
8. pure kernel call graph has zero env/auth/network/process capabilities, and
   old oracle path has no route to ManyChat/provider send.

- [ ] **Step 2: Run focused RED**

```bash
install_target=$(python3 -c 'from pathlib import Path; print(Path("/tmp/phase7-task13-install.path").read_text().strip())')
env -i \
  PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp \
  PYTHONPATH="$install_target:/home/ubuntu/workspace/agente-v2-phase7-runtime" \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/workspace/agente-v2-phase7-runtime/config/leads_agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_phase7_tool_dispatch_adapter.py tests/test_phase7_runtime_boundary.py \
  >/tmp/phase7-task14-red.out 2>&1
```

- [ ] **Step 3: Implement thin transport seams**

Rules:

```text
plugin.register -> schemas + one handler factory
handler -> dispatch_native_tool(request)
dispatch adapter -> reservation_boundary.ToolDispatch
READ -> existing read adapter
COMMAND -> ReservationCommand, PaymentSettlementCommand or BLOCKED_UNMIGRATED
STATE_COMMIT -> typed intent/facts
runner -> forwards result; no own budget/confirmation/retry
executor -> provider adapter invoked only by permitted worker/read path
```

Do not delete old code blindly; isolate it behind test-only oracle and prove no production call path reaches it.

- [ ] **Step 4: Run focused GREEN and direct runtime regressions**

```bash
install_target=$(python3 -c 'from pathlib import Path; print(Path("/tmp/phase7-task13-install.path").read_text().strip())')
env -i \
  PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp \
  PYTHONPATH="$install_target:/home/ubuntu/workspace/agente-v2-phase7-runtime" \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/workspace/agente-v2-phase7-runtime/config/leads_agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_phase7_tool_dispatch_adapter.py tests/test_phase7_runtime_boundary.py \
  tests/test_hermes_native_runner.py tests/test_tool_executor.py \
  tests/test_chapada_native_tools.py
```

- [ ] **Step 5: Commit in replica and generate integration-only patch**

```bash
git add domain/hermes_native_runner.py domain/tool_executor.py \
  domain/chapada_native_tools.py domain/tool_dispatch_adapter.py \
  .hermes/plugins/chapada_leads_tools/__init__.py \
  tests/test_phase7_tool_dispatch_adapter.py tests/test_phase7_runtime_boundary.py
git commit -m "refactor(phase-7): centralize runtime tool dispatch"
baseline=$(python3 -c 'import json; print(json.load(open("/home/ubuntu/agente-v2/.worktrees/phase7-boundary-migration/docs/refactor/evidence/phase-07/runtime-source-manifest.json"))["synthetic_baseline_commit"])')
git diff --binary "$baseline"..HEAD \
  > /home/ubuntu/agente-v2/.worktrees/phase7-boundary-migration/docs/refactor/evidence/phase-07/runtime-integration.patch
```

- [ ] **Step 6: Verify patch apply/reverse in a second disposable clone**

Run `git apply --check`, `git apply`, compare tree, `git apply --reverse --check`, reverse, compare baseline. Do not run the full suite.

- [ ] **Step 7: Commit patch/evidence in agente-v2**

```bash
git add docs/refactor/evidence/phase-07/runtime-integration.patch \
  docs/refactor/evidence/phase-07/runtime-source-manifest.json \
  docs/refactor/evidence/phase-07/red-results.json
git commit -m "refactor(phase-7): integrate isolated runtime boundaries"
```

---

### Task 15: Closeout validator, manifests, workflow e frozen-candidate preflight

**Files:**
- Create: `scripts/generate_phase7_manifest.py`
- Create: `scripts/validate_phase7.py`
- Create: `.github/workflows/phase7.yml`
- Create/update: evidence artifacts listed by spec
- Modify: `tests/test_phase7_closeout.py`
- Modify: `README.md`
- Modify: `docs/refactor/README.md`
- Modify: `docs/refactor/evidence/README.md`
- Modify: `docs/refactor/evidence/phase-07/README.md`
- Modify: `docs/refactor/phases/phase-07-boundary-migration.md`
- Modify: `docs/refactor/06-risk-register.md`

**Produces:** validator independente, manifests fechados, workflow branch-only e candidato pronto para congelamento; não roda a janela pesada ainda.

- [ ] **Step 1: Write closeout RED**

```python
class Phase7CloseoutContractTests(unittest.TestCase):
    def test_workflow_triggers_only_frozen_phase7_branch(self) -> None:
        workflow = load_yaml_without_bool_coercion(".github/workflows/phase7.yml")
        self.assertEqual(
            workflow["on"]["push"]["branches"],
            ["phase7-boundary-migration"],
        )
        self.assertNotIn("pull_request", workflow["on"])
        self.assertNotIn("main", workflow["on"]["push"]["branches"])
```

Add four mutation-style tests that:

1. inject each live/process capability and require validator failure;
2. alter runtime source or integration patch SHA and require failure;
3. insert synthetic/missing remote IDs into `ci-result.json` and require failure;
4. change `NO-GO` or `phase8_started=false` and require failure.

- [ ] **Step 2: Run RED**

```bash
python3 -B -m unittest tests.test_phase7_closeout -v \
  >/tmp/phase7-task15-red.out 2>&1
```

- [ ] **Step 3: Implement independent validator**

Validator must parse JSON with duplicate-key rejection, reconstruct totals from rows, inspect AST/call graph, verify wheel/patch/manifests, reject unexpected evidence files, authenticate exact runtime source fingerprints, and require `ci-result.json` only with real remote IDs/URLs after publication.

- [ ] **Step 4: Implement branch-only workflow**

```yaml
name: phase-7-boundary-migration

on:
  push:
    branches: [phase7-boundary-migration]
  workflow_dispatch:
```

Jobs: `static-validation`, `full-suite`, `boundary-properties-faults`, `package-runtime-contract`, aggregate `phase7-gate` with exact `needs` and no `if: always()`.

Integral runners receive `PHASE7_FROZEN_TREE` from `git write-tree`; no local development command sets it.

- [ ] **Step 5: Run only focused closeout/static checks**

```bash
python3 -B -m unittest tests.test_phase7_closeout -v
python3 -B scripts/generate_phase7_manifest.py --write
python3 -B scripts/generate_phase7_manifest.py --check
python3 -B scripts/validate_phase7.py --allow-ci-pending \
  >/tmp/phase7-task15-validator.json
python3 -B -m compileall -q reservation_boundary scripts tests
```

Do not run full suite or integral runners.

- [ ] **Step 6: Commit candidate-preflight artifacts**

```bash
git add .github/workflows/phase7.yml README.md docs/refactor \
  scripts/generate_phase7_manifest.py scripts/validate_phase7.py \
  tests/test_phase7_closeout.py
git diff --cached --check
git commit -m "test(phase-7): close boundary migration gates"
```

---

### Task 16: Freeze, validate once, review once, publish once and close

**No functional edits are permitted after Step 2 without declaring a new candidate.**

- [ ] **Step 1: Focused pre-freeze gate**

```bash
python3 -B -m unittest \
  tests.test_phase7_types tests.test_phase7_serialization \
  tests.test_phase7_legacy_state tests.test_phase7_schema \
  tests.test_phase7_sqlite_store tests.test_phase7_coordinator \
  tests.test_phase7_dispatch tests.test_phase7_shadow \
  tests.test_phase7_properties tests.test_phase7_fault_injection \
  tests.test_phase7_mutation_runner tests.test_phase7_package \
  tests.test_phase7_runtime_capture tests.test_phase7_closeout -v
python3 -B scripts/generate_phase7_manifest.py --check
python3 -B scripts/validate_phase7.py --allow-ci-pending
```

This is focused Phase 7 coverage, not the full historical suite or integral workloads.

- [ ] **Step 2: Freeze and authenticate candidate**

```bash
test -z "$(git status --porcelain)"
commit=$(git rev-parse HEAD)
tree=$(git rev-parse HEAD^{tree})
index_tree=$(git write-tree)
test "$tree" = "$index_tree"
git diff --binary 4169c6149f76e8bf4f30a26ee9d0bfbc43a58984..HEAD \
  > /tmp/phase7-candidate.diff
sha256sum /tmp/phase7-candidate.diff
```

Record commit/tree/package SHA/bytes/paths. Do not push yet.

- [ ] **Step 3: Run the local private heavy stage exactly once**

Build/install the frozen wheel, then:

```bash
wheel_dir=$(mktemp -d /tmp/phase7-frozen-wheel.XXXXXX)
install_target=$(mktemp -d /tmp/phase7-frozen-install.XXXXXX)
python3 -B scripts/build_phase7_wheel.py --output-dir "$wheel_dir"
python3 -m pip install --no-index --no-deps \
  --target "$install_target" "$wheel_dir"/*.whl
env -i \
  PATH=/usr/local/bin:/usr/bin:/bin \
  HOME=/tmp \
  PYTHONPATH="$install_target:/home/ubuntu/workspace/agente-v2-phase7-runtime" \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/workspace/agente-v2-phase7-runtime/config/leads_agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  >/tmp/phase7-runtime-full.out 2>&1
```

Also apply/reverse patch in the second clone, run adapter integration tests from installed wheel and authenticate original runtime before/after. Record `runtime-validation-result.json` and `performance-result.json`; raw output remains `/tmp`.

Do not run the `agente-v2` full suite or integral properties locally.

- [ ] **Step 4: Regenerate manifests without changing functional tree**

Evidence files may update. Commit them, then freeze a final evidence tree. If this changes only evidence/docs/manifests, rerun focused validator/manifest only; do not repeat runtime full suite.

- [ ] **Step 5: One terminal review batch with new information only**

Dispatch exactly three non-overlapping reviews against the same commit/tree/package:

1. identity/import/dual-read-single-write;
2. coordinator/dispatch/deadline/side-effect boundary;
3. runtime replica/provenance/patch/comparator/CI/claims.

Each reviewer must authenticate artifacts and report Critical/Important/Minor. Timeout, missing summary or `Needs fixes` counts as zero. Do not redispatch an identical lane against unchanged tree/evidence.

- [ ] **Step 6: Reconcile findings economically**

- Critical/Important functional fix: new candidate; rerun only affected focused/local gate, then one new terminal review for invalidated lanes.
- Test/evidence/doc fix: rerun only focused validator/manifest and invalidated review lane.
- No findings: proceed without another local test run.

- [ ] **Step 7: Publish branch once and let `phase7.yml` be the sole remote heavy stage**

```bash
GIT_SSH_COMMAND='ssh -i /home/ubuntu/.ssh/agente_v2_github_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new' \
  git push -u origin phase7-boundary-migration
```

Do not amend/push again while the run is in progress. Poll GitHub API for this exact head SHA. Require exactly one successful `phase-7-boundary-migration` run and all four material jobs plus `phase7-gate` successful.

- [ ] **Step 8: Create and validate real `ci-result.json`**

Include run/job IDs, URLs, start/end, head SHA, workflow path and conclusions. Run:

```bash
python3 -B scripts/validate_phase7.py
python3 -B scripts/generate_phase7_manifest.py --write
python3 -B scripts/generate_phase7_manifest.py --check
```

Commit closeout evidence with `[skip ci]`; push branch once more only if workflow trigger is guarded to ignore `[skip ci]`. Confirm zero new run for that docs-only commit.

- [ ] **Step 9: Merge identical approved tree into main without a second heavy cycle**

From `/home/ubuntu/agente-v2`:

```bash
git checkout main
git merge --no-ff phase7-boundary-migration \
  -m "merge(phase-7): integrate approved boundary migration [skip ci]"
test "$(git rev-parse main^{tree})" = \
  "$(git rev-parse phase7-boundary-migration^{tree})"
GIT_SSH_COMMAND='ssh -i /home/ubuntu/.ssh/agente_v2_github_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new' \
  git push origin main
```

Confirm no second heavy run was created and local/origin/remote main match.

- [ ] **Step 10: Final closeout claims**

Record:

- implementation and merge commits/trees;
- wheel and integration patch hashes;
- local private stage once;
- remote heavy stage once;
- terminal review verdicts;
- runtime original unchanged;
- capabilities live zero;
- PostgreSQL not executed;
- rollout `NO-GO`;
- `phase8_started=false`.

Do not start Phase 8.

## Final verification matrix

| Surface | Development | Frozen local | Frozen remote |
|---|---|---|---|
| Boundary unit tests | focused per task | focused aggregate | full suite |
| Historical agente-v2 suites | blast radius only | not run | workflow jobs |
| Properties/faults/mutations | focused small counts | not run | integral once |
| Runtime adapter tests | focused | installed-wheel/full runtime once | contract manifest only |
| Runtime full suite | never | once | never |
| Review | none per task | one terminal batch | no duplicate review |
| Live capabilities | never | never | never |

## Plan approval gate

Implementation starts only after Carlos approves this plan. Approval authorizes Tasks 1–16 under the constraints above; it does not authorize touching the operational runtime, using live capabilities, deploying, rolling out, or starting Phase 8.
