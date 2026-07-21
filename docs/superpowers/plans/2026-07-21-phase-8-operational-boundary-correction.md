# Fase 8 — Correção da Fronteira Operacional — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans` to
> execute this plan task-by-task. Use `superpowers:test-driven-development` for every
> behavior change and `superpowers:verification-before-completion` before each gate.

**Goal:** implementar e autenticar a fronteira operacional descrita pela spec
`2026-07-21-phase-8-operational-boundary-correction-design.md`, produzir
`chapada-reservation-kernel==0.8.0`, fechar o runtime graph real e provar o release
contract **sem executar build OCI ou qualquer capability live**.

**Architecture:** o processo pai é o único owner do transcript, proposal, reducer,
autorização e commit; Maya roda em processo efêmero capability-free por UDS
autenticado. Estado, reply, receipts, relays, authorities e qualification são
owner-owned em roots SQLite novos, idempotentes e semanticamente escaneáveis. Source,
wheel, runtime e release contract avançam por identidades F/E imutáveis e reviews AND;
o plano termina numa decisão separada GO/NO-GO de build.

**Tech stack:** Python 3.12.13, stdlib, `unittest`, SQLite 3.46.1, Unix domain sockets,
`fcntl.flock`, canonical JSON, SHA-256/HMAC-SHA-256, Git, wheel, OCI Distribution
Specification e Docker/BuildKit somente depois de autorização futura.

---

## 0. Autoridade e escopo deste plano

### Identidade arquitetural aprovada

- commit: `2889e9ec08f466bbb16a30e4bb5c9a098daf54d3`;
- tree: `ed57032319d2319389412f4407b268e3d7b7a78c`;
- spec blob: `0e599670b4bc585b1665d932a84afcf3c4b57456`;
- spec SHA-256: `0f7486191e9963b3786a83cc7096c2af12a89905c5d92fcc27edf431367dcf60`;
- tamanho/linhas: `160392` bytes / `2872` linhas;
- revisão técnica: `Approved`, `Approved`, `Approved` no mesmo objeto;
- aprovação humana: Carlos aprovou o design em 2026-07-21.

A aprovação que permitiu escrever este arquivo cobre **somente plano/quarentena**.
Antes de executar a Task 0, o commit documental deste plano precisa de validators,
review AND e nova autorização explícita de Carlos para implementação.

### Fora do escopo executável

Este plano **não autoriza**:

- alteração de `/home/ubuntu/chapada-leads-hermes`;
- import, webhook, provider call, ManyChat send, pagamento, e-mail ou learning live;
- construção/publicação de wheel antes da Task 22;
- build OCI, dark canary, ingress, conversa, E2E, deploy, rollout ou rollback;
- usar candidate1/candidate2 como build context;
- promover por tag, image ID ou archive hash;
- transformar o teste humano de Carlos em teste automatizado.

Build e operações começam somente em novos runbooks após a Task 26 e após aprovações
independentes. Nada neste plano é autorização implícita para esses runbooks.

### Roots de trabalho

- source worktree: `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout`;
- repo source: `/home/ubuntu/agente-v2`;
- runtime operacional: `/home/ubuntu/chapada-leads-hermes` — sempre read-only;
- candidate1/candidate2: evidência histórica read-only;
- runtime candidate novo: criado somente na Task 23, em root limpo e autenticado;
- evidência raw: root privado externo ao Git, modo `0700`, definido no brief de cada
  execução e nunca inferido por default.

### Stop conditions globais

Pare sem corrigir silenciosamente quando houver:

1. HEAD/tree/worktree diferentes da identidade aprovada da task anterior;
2. DB v7 Boundary, Phase5-v5, Phase6-v1 ou table universe inesperado no root escolhido;
3. runtime/processo mutante legacy sem `migration-ownership-v1` compartilhado;
4. mixed mode sem guard completo; a única alternativa é cutover global quiescente;
5. lock root/DB device, inode ou mount divergentes entre os participantes;
6. duplicate não byte-idêntico, receipt órfão, child extra, ACK stale ou row incerta;
7. capability externa alcançável durante teste que exige zero efeito;
8. raw output, SQLite/WAL, PII, token, payload provider ou segredo prestes a entrar no
   Git;
9. timeout, summary ausente, `Needs fixes` ou review de outra identidade;
10. correção material após review — todas as lanes da identidade anterior viram zero.

---

## 1. Protocolo TDD/evidência obrigatório em toda task

### Identidades U/P/S/R/O

Cada RED é autenticado como:

```python
@dataclass(frozen=True)
class RedProvenance:
    unfixed_commit: str          # U
    unfixed_tree: str
    test_patch_sha256: str       # P
    staged_tree: str             # S = apply(U, P)
    argv: tuple[str, ...]        # R
    cwd: str
    env_allowlist: tuple[tuple[str, str], ...]
    exit_code: int
    output_sha256: str           # O
    output_bytes: int
```

Regras:

1. autenticar U limpo;
2. criar somente o patch de teste P;
3. aplicar P em worktree/index separado e autenticar S;
4. executar R exatamente uma vez no S;
5. publicar O no `EvidenceArtifactStore` privado por conteúdo;
6. implementar GREEN sem alterar os bytes de P;
7. provar que P em U ainda falha e P no candidato funcional passa;
8. qualquer mudança em P cria nova identidade e novo RED.

### EvidenceArtifactStore

O store externo usa `coord.lock → object.lock`, dirfd/no-follow, staging fechado,
`chmod 0400`, fsync de arquivo/diretório e rename-no-replace. Os únicos estados de
recuperação são:

```text
S0 = staging vazio/parcial validável
S1 = objeto final ausente + staging válido
S2 = objeto final presente com hash/tamanho/mode exatos
```

Objeto final divergente, symlink, owner/mode incorreto ou membro desconhecido é
`MANUAL_REVIEW`, nunca overwrite. Git recebe somente o envelope sanitizado com
hash/tamanho/contagens/conclusão.

### Candidatos F/E e reviews

- **F (functional):** source/test/fixture necessários à execução;
- **E (evidence child):** filho direto de F contendo somente envelopes/evidência
  sanitizada;
- E nasce em ref/worktree evidence-only separado. A linha funcional permanece em F;
  a task seguinte parte de F, nunca de E. E não é merged/cherry-picked na linha
  funcional;
- `tree(F)` precisa ser igual ao tree funcional usado no GREEN;
- E não altera testes, source, fixtures, lockfile nem bytes empacotáveis;
- review lanes recebem o mesmo `(F, E, package_identity)`;
- lanes: compatibilidade/implementabilidade, safety/crash/idempotência e
  TDD/evidence/release;
- gate é AND; não existe maioria.

### Matriz econômica

- durante cada task: RED focado + GREEN focado + regressão pelo blast radius;
- suites pesadas somente na Task 21, uma vez por candidato terminal;
- correção material depois do gate pesado invalida o candidato e reroda o gate todo;
- não rerodar suite pesada até “ficar verde”.

### Commit discipline

Cada task termina com dois commits quando houver evidence child. O primeiro stageia
exatamente os paths funcionais enumerados na seção **Files** daquela task, sem
`git add .`, e usa o título da task como capability no subject `feat(phase8): ...`.
Depois de autenticar F, um worktree/ref evidence-only separado cria o segundo commit,
stageia somente os envelopes sanitizados enumerados e usa
`test(phase8): attest ... evidence`. O branch funcional não avança para E. O executor
registra F, E e `parent(E)==F` no brief; nunca inventa SHA antecipadamente no plano.

Tasks puramente documentais usam `docs(phase8)`. Commits, closeout, merge, build e
rollout são decisões separadas.

---

## 2. Estrutura de arquivos alvo

### Source/kernel worktree

```text
reservation_boundary/
  conversation.py              # projection, Maya DTOs, read/fact/proposal unions
  effects.py                   # reply chunks, relay/internal/public artifacts
  qualification.py             # bindings/allocations/qualification receipt DTOs
  uds_protocol.py              # frames, HMAC chain, peer/binding checks
  attempt_root.py              # staging/active grammar and scavenger
  locks.py                     # lead/internal/public lock factories
  relay.py                     # command/internal-job workers + reconcilers
  public_delivery.py           # public sender/reconciler
  runtime_graph.py             # graph manifest, readiness contracts
  ingress.py                   # ingress universe and poison scanner contracts
  types.py                     # existing boundary types plus stable exports
  serialization.py             # closed canonical wire registry
  schema.py                    # Boundary v8 / 11 tables
  sqlite_store.py              # atomic store, replay, authority and receipts
  dispatch.py                  # normalize_proposal + verify_authorized
  coordinator.py               # lead-lock/admission/Maya/kernel/commit handshake
reservation_execution/
  locks.py                     # ProviderExecutionLockFactory
  reconciliation.py            # capability-free fence reconciler
  schema.py                    # Phase5-v6 / 8 tables
  sqlite_store.py              # target ingress/authority/ledger atomic operations
  worker.py                    # provider sender under execution lock
reservation_followup/
  locks.py                     # FollowupDeliveryExecutionLockFactory
  reconciliation.py            # capability-free provider/delivery reconcilers
  schema.py                    # Phase6-v2 / 14 tables
  sqlite_store.py              # ingress, authority, outbox and closure operations
  workers.py                   # provider, internal and delivery workers
reservation_migration/
  __init__.py
  types.py
  schema.py                    # migration-ownership-v1 / 3 tables
  sqlite_store.py
  locks.py
  guard.py
  reconciliation.py
reservation_qualification/
  __init__.py
  types.py
  schema.py                    # journal / 5 tables
  sqlite_store.py
  controller.py
  admission.py
  effect_scan.py
  cancellation.py
  memory_preparation.py        # memory-preparation-v1 / 1 table + filesystem protocol
  reconciliation.py
phase8_release/
  __init__.py
  red_provenance.py
  evidence_store.py
  candidate_pair.py
  graph_scan.py
  payload_manifest.py
  source_attestation.py
  build_input.py
  oci_identity.py
  approval_manifest.py
  validator.py
scripts/
  validate_phase8_contracts.py
  phase8_prebuild_gate.py
  phase8_publish_oci.py
  run_phase8_properties.py
  run_phase8_faults.py
  run_phase8_restarts.py
  run_phase8_contention.py
  run_phase8_mutations.py
  build_phase8_wheel.py
```

`phase8_release` é tooling source-only e não entra na wheel. `reservation_migration`
e `reservation_qualification` entram na wheel 0.8.0.

### Runtime candidate novo, somente a partir da Task 23

```text
chapada_leads/
  runtime.py                   # única factory pública
  settings.py
  readiness.py
  lifespan.py
domain/
  phase8_maya_turn_port.py
  phase8_runtime_adapter.py
  phase8_behavior_snapshot.py
services/
  phase8_public_delivery.py
  phase8_worker_supervisor.py
.hermes/plugins/chapada_leads_boundary/
  __init__.py                  # cliente UDS mínimo, sem capability comercial
  plugin.yaml
tests/
  test_phase8_operational_wiring.py
  test_phase8_startup_lifespan.py
  test_phase8_runtime_graph.py
  test_phase8_capability_poison.py
  test_phase8_readiness.py
Dockerfile
pyproject.toml
uv.lock
```

---

## 3. Mapeamento spec → tasks

| Slice da spec | Tasks deste plano |
|---|---|
| 0 Contract replacement | 0 |
| 1 Types/wire | 1 |
| 2 ToolDispatch | 2 |
| 3 Schemas/stores | 3–6 |
| 4 Lock/transação | 7 |
| 5 UDS | 8 |
| 6 Maya subprocess | 9 |
| 7 Kernel adapter | 10 |
| 8 Coordinator/commit | 11 |
| 9 Duplicate/replay | 12 |
| 10 Relay reserva | 13 |
| 11 Handoff/settlement/learning | 14 |
| 12 Factory/readiness | 19 e 24 |
| 13 Ingress/legacy poison | 20 e 24 |
| 14 Terminal upstream | 22 |
| 15 Wheel | 21 e 23 |
| 16 Runtime candidate | 24 |
| 17 Runtime F/E | 25 |
| 18 Release contract | 21 e 26 |

Tasks 15–18 detalham authorities, execution locks, public delivery e qualification que
fazem parte das obrigações transversais dos Slices 10–13.

---

# Parte A — Contract lock e contratos puros

## Task 0: Fechar contract replacement e bootstrap de evidência

**Files:**

- Create: `phase8_release/__init__.py`
- Create: `phase8_release/red_provenance.py`
- Create: `phase8_release/evidence_store.py`
- Create: `phase8_release/candidate_pair.py`
- Create: `phase8_release/graph_scan.py`
- Create: `scripts/validate_phase8_contracts.py`
- Create: `tests/test_phase8_contract_lock.py`
- Create: `tests/test_phase8_red_provenance.py`
- Create: `tests/test_phase8_evidence_store.py`
- Modify: `.gitignore`

**RED tests exatos:**

- `ContractLockTests.test_quarantined_interfaces_have_zero_active_owner`;
- `ContractLockTests.test_runtime_source_and_build_context_are_disjoint`;
- `ContractLockTests.test_source_baseline_has_boundary_v7_phase5_v5_phase6_v1`;
- `RedProvenanceTests.test_staged_tree_is_exact_application_of_patch_to_unfixed_tree`;
- `RedProvenanceTests.test_green_candidate_cannot_change_red_patch_bytes`;
- `RedProvenanceTests.test_output_pointer_has_hash_size_command_and_environment`;
- `EvidenceStoreTests.test_concurrent_publish_same_bytes_returns_one_object`;
- `EvidenceStoreTests.test_divergent_existing_object_is_manual_review_not_overwritten`;
- `EvidenceStoreTests.test_restart_recovers_only_s0_s1_s2`.

**Steps:**

- [ ] Autenticar o commit documental aprovado, manifesto de quarentena e worktree
  limpa; interromper se o gate plano/quarentena não for AND + aprovação humana.
- [ ] Criar somente os três testes; capturar RED por U/P/S/R/O. Para este bootstrap,
  manter O dentro do root privado e publicá-lo imediatamente após o primeiro GREEN,
  preservando o hash calculado antes da implementação.
- [ ] Implementar `RedProvenance.from_run(...)`, `verify_red_replay(...)`,
  `EvidenceArtifactStore.publish(...)`, `recover(...)`, `CandidatePair.verify(...)` e
  `scan_for_quarantined_owners(...)` com fail-closed.
- [ ] Fazer o scanner provar ausência de owner ativo para todos os tokens do manifesto,
  sem escanear os próprios blobs históricos/manifesto/spec que os documentam.
- [ ] Executar:

```bash
python3 -B -m unittest \
  tests.test_phase8_contract_lock \
  tests.test_phase8_red_provenance \
  tests.test_phase8_evidence_store -v
python3 -B scripts/validate_phase8_contracts.py
```

  Esperado: `OK`; validator exit `0`; zero alteração em packages de runtime.
- [ ] Reaplicar P em U e provar RED; publicar envelopes sanitizados; criar F/E e obter
  review AND antes da Task 1.

## Task 1: Introduzir types/wire 0.8.0 fechados

**Files:**

- Create: `reservation_boundary/conversation.py`
- Create: `reservation_boundary/effects.py`
- Create: `reservation_boundary/qualification.py`
- Modify: `reservation_boundary/types.py`
- Modify: `reservation_boundary/serialization.py`
- Modify: `reservation_boundary/__init__.py`
- Modify: `pyproject.toml`
- Create: `tests/test_phase8_conversation_types.py`
- Create: `tests/test_phase8_effect_types.py`
- Create: `tests/test_phase8_qualification_types.py`
- Create: `tests/test_phase8_wire_v8.py`

**Dataclasses frozen/unions obrigatórios:** `ConversationProjection`,
`FoundSnapshot|ProvenAbsent|LegacyUnavailable`, `MayaTurnRequest`,
`MayaIntentClosure`, `MayaTurnClosure`, `MayaTurnProposal`,
`TranscriptCommitment`, `ReadObservation`, `TypedFact`,
`NormalizedToolProposal`, `LearningProposal`, `PublicReplyChunk`, `TurnReceipt`,
`BoundaryInternalJob`, `BoundaryRelayReceipt`, `TargetOperationReceipt`,
`OperationReceiptLookupResult` (`NOT_FOUND|RECEIPT|DIVERGENT`),
`PublicDeliveryReceipt`, `AdmissionAbortReceipt`, `InternalJobClosureReceipt`,
`ChildAllocationUnusedReceipt`, `EffectiveE2EDeploymentBinding`,
`E2EEffectAuthorizationBinding`, `SealedCanaryQualificationBinding`,
`BehaviorTransitionReceipt`, `RolloutAuthorization`,
`ProductionInitialDeploymentBinding`, `E2EQualificationContract`,
`E2EScenarioContract`, `ProviderEffectOutcomeReceipt`, o receipt fechado de
verificação terminal de cenário, `ExactEffectAllocationManifest` e os receipts
fechados de installation/closure. Também são obrigatórios
`QualificationCancelStartReceipt`, `QualificationCancelReceipt`,
`ReopenPreparationIntent`, `ReopenIntentAbandonStartReceipt`,
`ReopenIntentAbandonReceipt`, `MemoryPreparationReceipt`,
`MemoryPreparationAckReceipt`, `MemoryPreparationAbandonReceipt` e
`QualificationReopenReceipt`. Os campos e domínios de hash são exatamente os da spec
aprovada; adicionar campo aberto ou omitir receipt exige delta arquitetural, não
decisão local do implementador.

**Steps:**

- [ ] RED: strict types rejeitam bool-as-int, float, duplicate JSON keys, unknown key,
  mutable nested object, raw provider payload, free-form fact/tool/status e artifact
  fora de ordem.
- [ ] Bump project version para `0.8.0`; não construir wheel.
- [ ] Implementar dataclasses frozen, enums fechados, deep detach/freeze e canonical
  serializer com schema/version/domain separation para cada hash.
- [ ] `MayaIntentClosure` não pode conter facts/tool/command; `ReadObservation` aceita
  somente union sanitizado; `TurnReceipt` separa `artifact_hash` de backlink.
- [ ] Round-trip hostil de todo tipo e cross-type/domain collision tests.
- [ ] Executar os quatro módulos focados e regressão `tests.test_serialization`,
  `tests.test_boundary_types`; criar F/E e review AND.

## Task 2: Separar normalização de autorização em ToolDispatch

**Files:**

- Modify: `reservation_boundary/dispatch.py`
- Modify: `reservation_boundary/types.py`
- Create: `tests/test_phase8_tool_dispatch.py`
- Modify: `tests/test_dispatch.py`

**API:**

```python
class ToolDispatch:
    def normalize_proposal(
        self,
        *,
        tool_name: str,
        typed_arguments_json: bytes,
        transcript_binding: str,
    ) -> NormalizedToolProposal: ...

    def verify_authorized(
        self,
        *,
        proposal: NormalizedToolProposal,
        state: BoundaryState,
        decision: KernelDecision,
    ) -> AuthorizedDispatch: ...
```

**Steps:**

- [ ] RED: normalização nunca cria command; verify rejeita command sem proposal,
  proposal extra, alias divergente, offer/version/evidence stale e handmade DTO.
- [ ] Preservar catálogo fechado de 13 tools e `BLOCKED_UNMIGRATED` sem executar
  provider.
- [ ] Implementar bijeção proposal→decision→command e hashes de argumentos canônicos.
- [ ] AST/import test prova zero provider/ManyChat/network em `dispatch.py`.
- [ ] GREEN focado + regressão dispatch/kernel; F/E + AND.

---

# Parte B — Roots, schemas e stores owner-owned

## Task 3: Boundary v8 com onze tabelas e commit atômico

**Files:**

- Modify: `reservation_boundary/schema.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Modify: `reservation_boundary/serialization.py`
- Create: `tests/test_phase8_boundary_schema_v8.py`
- Create: `tests/test_phase8_boundary_atomic_commit.py`
- Create: `tests/test_phase8_boundary_semantic_scan.py`

**Table universe literal:**

```python
BOUNDARY_V8_TABLES = {
    "boundary_state", "boundary_events", "boundary_event_sources",
    "boundary_turn_artifacts", "boundary_commands", "boundary_command_relays",
    "boundary_outbox", "boundary_public_outbox", "boundary_dispatch_authority",
    "legacy_import_claims", "decision_comparisons",
}
```

**Steps:**

- [ ] RED: root vazio cria exatamente as onze tables; v7, table/trigger/index extra,
  FK/check divergente ou DDL hash errado falham startup.
- [ ] Implementar `commit_turn_v8(...)` em uma `BEGIN IMMEDIATE` curta contendo state,
  event/sources, artifacts, receipt, commands, relays, internal jobs, public chunks e
  allocation CAS.
- [ ] Persistir artifact payload hash sem backlink; preencher
  `source_turn_receipt_hash` depois do receipt hash no mesmo commit.
- [ ] Fault injection antes/depois de cada statement e antes de COMMIT: rollback deixa
  zero mudança lógica.
- [ ] Semantic scan bilateral recompõe counts/hashes/transcript/proposal/decision e
  rejeita row ausente, órfã, extra ou divergente.
- [ ] GREEN focado + regressão boundary; F/E + AND.

## Task 4: Phase5-v6 e Phase6-v2 com ingress/authorities exatas

**Files:**

- Modify: `reservation_execution/schema.py`
- Modify: `reservation_execution/sqlite_store.py`
- Modify: `reservation_followup/schema.py`
- Modify: `reservation_followup/sqlite_store.py`
- Create: `tests/test_phase8_phase5_v6.py`
- Create: `tests/test_phase8_phase6_v2.py`
- Create: `tests/test_phase8_target_ingress.py`
- Create: `tests/test_phase8_effect_authority.py`

**Universos:**

```text
Phase5-v6: seis tables v5 + reservation_boundary_ingress_receipts
                         + reservation_e2e_effect_authority = 8
Phase6-v2: onze nomes v1 endurecidos + handoff_boundary_ingress_receipts
                                   + payment_boundary_ingress_receipts
                                   + followup_e2e_effect_authority = 14
```

**Steps:**

- [ ] RED: roots novos aceitos; v5/v1, migration extra ou universe inesperado são stop
  condition.
- [ ] Implementar instalação atômica `generation_header + manifest completo`,
  header-tombstone quando close vence install e states literais por header/allocation.
- [ ] Implementar `accept_boundary_reservation`, `accept_boundary_handoff` e
  `accept_boundary_settlement`: full replay, target receipt e allocation bind na mesma
  transaction; duplicate byte-idêntico, conflito terminal.
- [ ] Implementar pure derivations `derive_reservation_effect_receipt` e
  `derive_settlement_effect_receipt`; não criar segundo owner de outcome.
- [ ] Endurecer follow-up outboxes com slot 0/1, lease/deadline imutável e authority FK
  all-null/all-present.
- [ ] GREEN focado + regressão completa Phase5/6; F/E + AND.

## Task 5: migration-ownership-v1 e permits legacy

**Files:**

- Create package: `reservation_migration/`
- Modify: `pyproject.toml`
- Create: `tests/test_phase8_migration_schema.py`
- Create: `tests/test_phase8_migration_ownership.py`
- Create: `tests/test_phase8_legacy_write_guard.py`
- Create: `tests/test_phase8_migration_contention.py`

**Table universe:**

```python
MIGRATION_OWNERSHIP_V1_TABLES = {
    "migration_owners", "migration_permits", "migration_transitions"
}
```

**Steps:**

- [ ] RED owner FSM `legacy_owned→freezing→frozen→boundary_owned`, release dedicado e
  `manual_review`; transition revision/hash chain precisa ser contígua.
- [ ] Implementar register/acquire/complete/begin/finish/finalize/release por full-tuple
  CAS, active count exato e operation receipt idempotente.
- [ ] Contention probe pausa writer com permit, executa begin freeze, conclui writer e
  prova que frozen só publica depois de active=0.
- [ ] `LegacyWriteGuard` exige permit no read/preparation, imediatamente antes de
  external dispatch e no commit local; permit incerto não expira por idade.
- [ ] Graph/import scanner exige guard em webhook, debounce/flush, Stripe, Wise,
  image/actions e callbacks; enquanto runtime legacy não o possuir, mixed mode fica
  NO-GO.
- [ ] GREEN + 200 contentions focadas; F/E + AND.

## Task 6: QualificationJournal e memory-preparation-v1

**Files:**

- Create package: `reservation_qualification/`
- Modify: `pyproject.toml`
- Create: `tests/test_phase8_qualification_schema.py`
- Create: `tests/test_phase8_qualification_store.py`
- Create: `tests/test_phase8_memory_preparation_schema.py`

**Universos:**

```python
QUALIFICATION_TABLES = {
    "qualification_admission_state", "qualification_admissions",
    "qualification_runs", "qualification_scenarios", "qualification_artifacts",
}
MEMORY_PREPARATION_TABLES = {"memory_preparation_operations"}
```

**Steps:**

- [ ] RED para checks all-null/all-present, memberships append-only, active count,
  statuses run/admission distintos, revision monotônica e artifact IDs globais.
- [ ] Implementar roots novos/exatos, DDL hashes e semantic scan bilateral.
- [ ] `reopen_intent_state` aceita somente `NULL|PREPARING|ABANDONING|ABANDONED|COMMITTED`;
  apenas um intent ativo por old qualification/epoch.
- [ ] Memory operation state aceita
  `PREPARING|PREPARED|ABANDONING|ABANDONED|ACKED|MANUAL_REVIEW` com tuple de receipts
  exata.
- [ ] Root antigo ou table extra falha readiness; nenhuma migration automática.
- [ ] GREEN focado; F/E + AND.

---

# Parte C — Turno autenticado e commit/replay

## Task 7: Lead lock deadline-aware e transaction curta

**Files:**

- Create: `reservation_boundary/locks.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Create: `tests/test_phase8_lead_lock.py`
- Create: `tests/test_phase8_deadline_transaction.py`

**API:**

```python
class LeadExecutionLockFactory:
    def acquire(self, *, ownership_db_identity: str, lead_hash: str,
                deadline: datetime, clock: Clock) -> AbstractContextManager[None]: ...
```

**Steps:**

- [ ] RED flock contention, crash FD release, symlink/path escape, divergent inode/mount
  e deadline antes/acquisition/BEGIN/first-write/COMMIT.
- [ ] Implementar path domain-separated, dirfd/no-follow, `LOCK_NB`, clock injetável e
  polling limitado.
- [ ] Busy timeout sempre menor que remaining budget; nenhuma legacy/remote I/O sob
  `BEGIN IMMEDIATE`.
- [ ] Provar DB logicamente inalterado nos quatro deadlines; WAL/SHM físico não conta
  como row mutation.
- [ ] GREEN + blast radius store/coordinator; F/E + AND.

## Task 8: UDS autenticado, transcript e tool gateway

**Files:**

- Create: `reservation_boundary/uds_protocol.py`
- Create: `tests/test_phase8_uds_frames.py`
- Create: `tests/test_phase8_uds_peer_auth.py`
- Create: `tests/test_phase8_uds_transcript.py`
- Create: `tests/test_phase8_tool_gateway.py`

**Protocol:**

```text
canonical JSON + 4-byte big-endian length
single connection; global monotonic sequence
SO_PEERCRED same UID + spawned process group
HMAC chain keyed by per-turn random capability
READ | STATE_COMMIT | LEARNING | COMMAND | FINAL
```

**Steps:**

- [ ] RED broken length, duplicate key, seq gap/replay, request divergence, second
  connection, peer PID/UID mismatch, expired deadline, unknown tool e late frame.
- [ ] Implement frame parser with size budget and canonical bytes; request cache only
  para duplicate byte-idêntico.
- [ ] Gateway acumula observations/facts/proposals/learning; nenhum COMMAND chama
  provider e nenhuma STATE_COMMIT/LEARNING escreve estado.
- [ ] FINAL valida prefix MAC, zero request in-flight e marker stdout fixo; parent
  acrescenta FINAL em domínio separado e produz terminal commitment sem segredo.
- [ ] Persistable transcript contém somente metadata/hashes/artifacts sanitizados.
- [ ] GREEN + 2,000 malformed frame properties; F/E + AND.

## Task 9: Attempt root crash-safe e MayaTurnPort efêmero

**Files:**

- Create: `reservation_boundary/attempt_root.py`
- Create: `reservation_boundary/maya.py`
- Create: `tests/test_phase8_attempt_root.py`
- Create: `tests/test_phase8_attempt_scavenger.py`
- Create: `tests/test_phase8_maya_turn_port.py`

**Attempt grammar:**

```text
staging S0={} → S1={owner.lock} → S2={owner.lock,attempt.meta.tmp}
→ S3={owner.lock,attempt.meta} → active/ published
lock order: coord.lock → owner.lock
```

**Steps:**

- [ ] RED crash em mkdir/lock/temp/meta/rename/socket/spawn/final/cleanup e unknown
  member/symlink.
- [ ] Implementar `AttemptRootManager.create`, `cleanup` e capability-free `scavenge`
  com rename-no-replace, fsync e allowlist de members.
- [ ] AST/import gate rejeita qualquer fallback JSONL, HTTP localhost,
  global/thread-local ou outro canal que contorne a conexão UDS autenticada.
- [ ] `MayaTurnPort.run` cria home/session novos de projection autenticada, plugin mínimo
  e env allowlist; nunca retoma sessão e sempre destrói attempt root.
- [ ] Proibir transitivamente terminal/file/web/generic-memory/cron, legacy tools,
  provider SDKs e senders no child module graph.
- [ ] SIGKILL/os._exit/restart probes convergem a zero órfão ou manual review; F/E +
  AND.

## Task 10: Kernel adapter reduz proposal completo

**Files:**

- Create: `reservation_boundary/kernel_adapter.py`
- Modify: `reservation_boundary/coordinator.py`
- Create: `tests/test_phase8_kernel_adapter.py`
- Create: `tests/test_phase8_kernel_ownership.py`

**API:**

```python
class KernelPort(Protocol):
    def reduce(self, state: BoundaryState, proposal: MayaTurnProposal) -> KernelDecision: ...
```

**Steps:**

- [ ] RED command sem proposal, fact não reduzido, unresolved read, stale selection,
  reply sem closure binding, manual DTO e plugin attempting authorization.
- [ ] Adaptar facts/reads para reducers canônicos e chamar ToolDispatch apenas nas duas
  fases previstas.
- [ ] Reusar os bindings canônicos de package/confirmation/payment; claim sem evidence
  ou binding econômico divergente falha antes de command/outbox.
- [ ] Kernel produz commands/internal jobs/reply decision sem I/O; AST/import scan
  proíbe subprocess/socket/provider/delivery.
- [ ] Metamorphic tests alteram label/locale/order irrelevante sem alterar identity;
  economic/version change precisa alterar decisão.
- [ ] GREEN + regressão kernel/reducers; F/E + AND.

## Task 11: Coordinator, genesis e commit/receipt atômico

**Files:**

- Modify: `reservation_boundary/coordinator.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Modify: `reservation_boundary/types.py`
- Create: `tests/test_phase8_coordinator_genesis.py`
- Create: `tests/test_phase8_coordinator_commit.py`
- Create: `tests/test_phase8_admission_handshake.py`

**Closed genesis result:**

```python
LegacyGenesisResult = FoundSnapshot | ProvenAbsent | LegacyUnavailable
```

**Steps:**

- [ ] RED: unavailable/timeout/malformed nunca cria empty lead; legacy reader só é
  chamado em `StateNotFound`; state existente nunca toca legacy.
- [ ] Implementar ordem lock→duplicate→freeze/drain→admission fence→Maya→kernel→short
  transaction→ownership finalize→journal ACK→unlock.
- [ ] Pausar mutator legacy durante Maya e alterar snapshot A→B; a releitura sob freeze
  precisa abortar sem gênese, import claim, boundary row ou efeito.
- [ ] E2E mantém lead lock de `admitted→commit_fenced` até receipt ACK; normal failure
  antes de boundary commit persiste abort receipt pelo mesmo reconciler path.
- [ ] Commit insere reply chunks/receipt/commands/relays/jobs numa transaction; deadline
  reamostrada imediatamente antes de COMMIT.
- [ ] Fault injection em cada boundary e crash commit-before-ACK; restart converge para
  receipt ACK ou zero-scan abort, nunca ambos.
- [ ] GREEN + regressão coordinator/store; F/E + AND.

## Task 12: Duplicate replay byte-idêntico e integrity scan

**Files:**

- Modify: `reservation_boundary/sqlite_store.py`
- Modify: `reservation_boundary/coordinator.py`
- Create: `tests/test_phase8_duplicate_replay.py`
- Create: `tests/test_phase8_receipt_integrity.py`

**Steps:**

- [ ] RED duplicate após restart com ports poison: Maya, legacy, read, kernel e dispatch
  não podem ser chamados.
- [ ] Implementar lookup por todos os source IDs/hashes; qualquer subset/mismatch é
  identity conflict/corruption.
- [ ] Replay retorna bytes persistidos de `TurnReceipt` e chunks ordenados; não rerender,
  rechunk ou renormaliza.
- [ ] Semantic scan recompõe child IDs/hashes/counts e backlinks bidirecionalmente.
- [ ] Corromper cada child/backlink/count/hash em cópia temporária precisa bloquear
  readiness e duplicate.
- [ ] GREEN + 2,000 duplicate/restart properties; F/E + AND.

---

# Parte D — Relays, effects e locks externos

## Task 13: Reservation command relay e target ACK

**Files:**

- Create: `reservation_boundary/relay.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Modify: `reservation_execution/sqlite_store.py`
- Create: `tests/test_phase8_reservation_relay.py`
- Create: `tests/test_phase8_relay_target_ack.py`

**Steps:**

- [ ] RED pending/leased/acked/cancelled/manual_review FSM, full tuple lease CAS, stale
  ACK, preparation max 3 e target identity conflict.
- [ ] Implementar bundle com genesis, eventos contíguos, summary outboxes, final state,
  command/ledger seed e allocation binding.
- [ ] Worker one-shot faz claim→prepare→target idempotent ingress→receipt validation→
  source ACK; nunca chama provider.
- [ ] Crash antes target libera/requeue; crash target-commit/source-ACK retorna mesmo
  target receipt e completa ACK; divergent target vai manual review.
- [ ] Closure cancela apenas pre-target; target receipt existente precisa reconcile/ACK.
- [ ] GREEN + faults/restarts; F/E + AND.

## Task 14: Internal jobs para handoff, settlement e learning

**Files:**

- Modify: `reservation_boundary/relay.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Modify: `reservation_followup/sqlite_store.py`
- Modify: `reservation_followup/workers.py`
- Create: `tests/test_phase8_internal_jobs.py`
- Create: `tests/test_phase8_handoff_ingress.py`
- Create: `tests/test_phase8_settlement_ingress.py`
- Create: `tests/test_phase8_learning_job.py`

**Steps:**

- [ ] RED union fechado por kind, operation ID determinístico, target lookup
  side-effect-free e `NOT_FOUND` somente após zero-scan completo.
- [ ] Worker/reconciler/canceler compartilham `InternalJobExecutionLockFactory` por
  boundary DB + job; lock cobre lookup→target commit→source ACK.
- [ ] Handoff/settlement ingresses persistem full replay/receipts/allocations; learning
  aplica `expected_version/hash` e receipt na mesma memory transaction.
- [ ] Outcome parcial/órfão/uncerto termina manual review; reconciler não recebe
  capability genérica de mutation.
- [ ] Pause probes em lookup, target commit e source ACK contra canceler; stale worker
  faz zero primeira mutation após terminalização.
- [ ] GREEN + regressão handoff/payment/followup; F/E + AND.

## Task 15: Provider e follow-up execution locks

**Files:**

- Create: `reservation_execution/locks.py`
- Create: `reservation_execution/reconciliation.py`
- Modify: `reservation_execution/worker.py`
- Create: `reservation_followup/locks.py`
- Create: `reservation_followup/reconciliation.py`
- Modify: `reservation_followup/workers.py`
- Create: `tests/test_phase8_provider_execution_lock.py`
- Create: `tests/test_phase8_followup_delivery_lock.py`
- Create: `tests/test_phase8_capability_free_reconciler.py`

**Steps:**

- [ ] RED sender-vs-reconciler/canceler races; pause antes/depois lock, lease/deadline
  expiry, crash pós-dispatch/pré-receipt e mismatched lock inode.
- [ ] Sender reamostra ledger/allocation/owner/token/lease/deadline sob lock
  imediatamente antes do port; mantém lock pela call e outcome transaction.
- [ ] Reconciler recebe somente store/clock/mesma lock factory, nunca provider,
  credentials ou delivery port.
- [ ] Expiry sob lock produz zero call; chamada já iniciada sob lock é irrevogável para
  aquele attempt e crash posterior termina unknown/manual review sem retry.
- [ ] Follow-up fence faz outbox slot `0→1` e authority `bound→dispatch_fenced` na mesma
  transaction; receipt/outbox/authority terminalizam juntos.
- [ ] GREEN + 200 races por família; F/E + AND.

## Task 16: Public delivery authority, worker e reconciler

**Files:**

- Create: `reservation_boundary/public_delivery.py`
- Modify: `reservation_boundary/locks.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Create: `tests/test_phase8_public_allocations.py`
- Create: `tests/test_phase8_public_delivery.py`
- Create: `tests/test_phase8_public_reconciliation.py`

**Steps:**

- [ ] RED conversation-test budget finito e E2E allocations exatas; row/ordinal/chunk
  extra precisa abortar antes de commit/dispatch.
- [ ] Instalar header + manifest completo antes de ingress; close-before-install cria
  header tombstone e bloqueia late install.
- [ ] Turn commit liga chunk↔allocation; fence cria dispatch slot; sender revalida
  policy/binding/recipient/channel/lease/deadline sob public execution lock.
- [ ] Idempotency key pública inclui release+lead+target+channel; chunks formam uma
  cadeia predecessor/successor e prefixo parcial bloqueia successors.
- [ ] Reconciler capability-free nunca envia; fence incerto vira manual review.
- [ ] Delivery receipt terminaliza public row + allocation na mesma transaction;
  duplicate retorna bytes idênticos.
- [ ] GREEN + faults/restarts; F/E + AND.

---

# Parte E — Qualification, cancellation e reopen

## Task 17: Instalar qualification, admitir, fechar cutoff e selar

**Files:**

- Modify: `reservation_qualification/controller.py`
- Modify: `reservation_qualification/admission.py`
- Modify: `reservation_qualification/effect_scan.py`
- Modify: `reservation_qualification/sqlite_store.py`
- Create: `tests/test_phase8_qualification_install.py`
- Create: `tests/test_phase8_admission_cutoff.py`
- Create: `tests/test_phase8_effect_scan.py`
- Create: `tests/test_phase8_learning_seal.py`

**Steps:**

- [ ] RED contrato exige cenário não vazio, pelo menos um provider terminal e uma
  public delivery terminal; read-only/zero-budget não qualifica.
- [ ] Criar run + scenarios em `INSTALLING`; instalar manifests nos três roots com
  operation IDs estáveis e journal ACK; somente então CAS conjunto para `OPEN`.
- [ ] Admission membership digest exclui status/revision/timestamps; lead lock protege
  fence/commit/ACK e reconciler abort.
- [ ] `OPEN→QUALIFYING` congela cutoff + admitted-set na mesma transaction e fecha
  learning claims normais.
- [ ] Scan rederiva target ingresses, provider outcomes e deliveries owner-owned;
  cardinalidade ausente/extra ou estado não terminal falha.
- [ ] Avançar por CAS/receipts
  `EFFECTS_VERIFIED→LEARNING_DRAINED→MEMORY_SEALED→TRANSITION_RECORDED→QUALIFIED`;
  seal duplicate byte-idêntico e zero learning explícito.
- [ ] Restart em cada transição converge sem repetir efeito; F/E + AND.

## Task 18: Cancelar em oito estados, abandonar preparation e reabrir

**Files:**

- Modify: `reservation_qualification/cancellation.py`
- Modify: `reservation_qualification/memory_preparation.py`
- Modify: `reservation_qualification/reconciliation.py`
- Modify: `reservation_qualification/sqlite_store.py`
- Create: `tests/test_phase8_qualification_cancel.py`
- Create: `tests/test_phase8_memory_prepare.py`
- Create: `tests/test_phase8_memory_abandon.py`
- Create: `tests/test_phase8_qualification_reopen.py`

**States de origem obrigatórios:**

```python
CANCELLABLE_RUN_STATES = (
    "INSTALLING", "OPEN", "QUALIFYING", "EFFECTS_VERIFIED",
    "LEARNING_DRAINED", "MEMORY_SEALED", "TRANSITION_RECORDED", "QUALIFIED",
)
```

**Steps:**

- [ ] `begin_cancel_qualification` persiste start receipt e faz run+admission→FROZEN
  atomicamente; preservar seal/transition/binding já emitidos.
- [ ] Drenar admissions sob lead locks; fechar root generations, relays/internal jobs,
  parent outcomes, children, follow-up/public rows na ordem normativa.
- [ ] CAS final `CANCELLED` exige active=0, memberships terminais, zero manual review e
  closure receipts bilaterais completos.
- [ ] Reopen reserva journal intent **antes** do clone; operation/attempt/root/artifact
  IDs nunca são reutilizados.
- [ ] Memory prepare usa `PREPARING→PREPARED→ACKED`, sem transaction aberta durante
  clone/fsync.
- [ ] Abandon usa journal start→target `ABANDONING`→rename tombstone→cleanup/fsync/
  zero-scan→target `ABANDONED`→journal `ABANDONED`; `ABANDONED` com payload residual é
  manual review.
- [ ] Fault probe nos sete checkpoints A0–A4 e restart no pair target terminal/journal
  intermediário; convergir ao mesmo receipt bilateral.
- [ ] Reopen cria epoch+1/new qualification/new root; old ACK/receipt/allocation é
  rejeitado para sempre.
- [ ] Depois do target prepare, uma transaction do journal faz o CAS old
  `CANCELLED`→new `INSTALLING`, cria run/scenarios/reopen receipt e ACKa preparation;
  target installs idempotentes continuam até `OPEN` antes de admitir.
- [ ] GREEN em todos os oito origins + 2,000 restarts; F/E + AND.

---

# Parte F — Composition/readiness upstream e gate terminal

## Task 19: RuntimeGraphManifest e composition contract upstream

**Files:**

- Create: `reservation_boundary/runtime_graph.py`
- Create: `tests/test_phase8_runtime_graph_contract.py`
- Create: `tests/test_phase8_readiness_contract.py`

**Graph mínimo:**

```text
settings/role/roots; boundary store; lead/ownership/internal/provider/followup/public
locks; ownership reconciler; Maya+UDS+attempt scavenger; kernel; relays; provider and
delivery senders/reconcilers; learning/memory preparation; qualification; coordinator;
routes/lifespan
```

**Steps:**

- [ ] RED qualquer node ausente/`None`, classe/version/hash divergente, lock root
  mismatch, behavior snapshot inválido ou plugin graph extra.
- [ ] Implementar manifest canônico de classes/versions/wheel placeholder/profile/
  config/skills/plugin/catalog/adapters/workers; learned memory fica em snapshot
  separado.
- [ ] Readiness recompõe schemas, roots, locks, transcript/receipts e source/target
  ACKs; capability policy não substitui graph node.
- [ ] Definir stable `E2EEffectAuthorizationBinding` sem behavior digest e effective
  turn binding com behavior digest versionável por LearningReceipt.
- [ ] GREEN + graph mutation catalog; F/E + AND.

## Task 20: Ingress universe e legacy poison contracts

**Files:**

- Create: `reservation_boundary/ingress.py`
- Modify: `phase8_release/graph_scan.py`
- Create: `tests/test_phase8_ingress_universe.py`
- Create: `tests/test_phase8_legacy_poison.py`
- Create: `tests/test_phase8_child_capability_graph.py`

**Steps:**

- [ ] Enumerar todos os ingress/event/job/callback mutantes do runtime observado;
  classifier literal exige owner `coordinator` ou `legacy_guarded`.
- [ ] RED webhook/debounce/action/callback/job alternativo que alcança write sem owner;
  route/import/plugin entrypoint desconhecido falha.
- [ ] Poison modules explodem se child importar legacy tools, provider SDK, delivery,
  terminal/file/web/memory writer ou alternative plugin.
- [ ] Provar que runtime live só foi lido; não instalar guard nem alterar processos.
- [ ] Se inventário provar mutator não guardado, registrar mixed mode NO-GO sem tentar
  resolver nesta task.
- [ ] GREEN + F/E + AND.

## Task 21: Implementar package/release tooling antes de congelar source F

**Files:**

- Create: `scripts/build_phase8_wheel.py`
- Create: `scripts/phase8_prebuild_gate.py`
- Create: `scripts/phase8_publish_oci.py`
- Create: `scripts/run_phase8_properties.py`
- Create: `scripts/run_phase8_faults.py`
- Create: `scripts/run_phase8_restarts.py`
- Create: `scripts/run_phase8_contention.py`
- Create: `scripts/run_phase8_mutations.py`
- Modify: `phase8_release/candidate_pair.py`
- Create: `phase8_release/payload_manifest.py`
- Create: `phase8_release/source_attestation.py`
- Create: `phase8_release/build_input.py`
- Create: `phase8_release/oci_identity.py`
- Create: `phase8_release/approval_manifest.py`
- Create: `phase8_release/validator.py`
- Create: `tests/test_phase8_wheel.py`
- Create: `tests/test_phase8_wheel_reproducibility.py`
- Create: `tests/test_phase8_payload_manifest.py`
- Create: `tests/test_phase8_source_attestation.py`
- Create: `tests/test_phase8_build_input.py`
- Create: `tests/test_phase8_oci_identity.py`
- Create: `tests/test_phase8_approval_manifest.py`
- Create: `tests/test_phase8_publish_gate.py`
- Create: `tests/test_phase8_terminal_gate.py`

**Steps:**

- [ ] RED wheel package list/metadata/RECORD/source mapping, duas builds idênticas,
  isolated import e ausência de source-only tooling no member universe.
- [ ] Implementar builder determinístico `0.8.0` que aceita output dir explícito e
  `SOURCE_DATE_EPOCH`, mas não executá-lo para produzir a wheel candidata nesta task.
- [ ] RED auto-hash/cycle, unlisted `COPY`, symlink/hardlink/device/path escape,
  uid/gid/mode/mtime/order não canônicos, OCI descriptor extra/wrong platform,
  tag-only reference e instance fields no release manifest comum.
- [ ] Implementar approval manifest externo, payload-context manifest fechado,
  source attestation acíclica, canonical tar/build-input identity e OCI
  index/child/config/layer validator. Nenhum módulo abre Docker/registry.
- [ ] Implementar `phase8_publish_oci.py` com runner injetado e gate que recusa qualquer
  chamada sem build authorization bytes, build-input identity e destino
  registry/repository explícitos; os testes usam poison runner e zero rede.
- [ ] Criar também todos os runners e o terminal-gate test que a Task 22 executará;
  counters/catalogs são literais e independentes do source sob teste.
- [ ] Executar somente testes unitários focados de package/release tooling com fakes e
  poison transports; GREEN + blast radius; F/E task-local + AND.
- [ ] Stop condition: qualquer builder, validator, runner ou teste funcional ausente
  depois desta task impede congelar source F. Nunca adicioná-lo retrospectivamente em
  source E, na wheel ou na Task 26.

## Task 22: Congelar source F/E e executar o único gate pesado upstream

**Files:**

- No functional source/test/builder changes
- Create only sanitized envelopes under `docs/refactor/evidence/phase-08/` in E

**Steps:**

- [ ] Congelar source functional F limpo já contendo implementação 0.8.0, package
  builder, release controller, runners e todos os testes. Qualquer alteração posterior
  exige novo F e invalida este gate.
- [ ] Executar uma única vez no F:

```bash
python3 -B -m unittest discover -s tests -v
python3 -B scripts/run_phase8_properties.py --cases 20000
python3 -B scripts/run_phase8_faults.py --schedules 64
python3 -B scripts/run_phase8_restarts.py --cases 2000
python3 -B scripts/run_phase8_contention.py --cases 200
python3 -B scripts/run_phase8_mutations.py --catalog phase8-v1
python3 -B scripts/validate_phase8_contracts.py
```

- [ ] Esperado: todos exit `0`; counters obrigatórios positivos; cada mutante do
  catálogo fechado morto; raw outputs apenas no private store.
- [ ] A fault suite inclui EvidenceArtifactStore publisher vivo, SIGKILL/power-loss,
  chmod→fsync→publish→dir-fsync e scavenger S0/S1/S2 sob lock order coord→owner.
- [ ] Criar E terminal como filho direto de F consolidando os envelopes aprovados de
  todas as tasks, hashes, counts e conclusions; verificar que E não altera bytes
  funcionais/empacotáveis nem incorpora os evidence-child commits intermediários.
- [ ] Enviar o mesmo `(F,E)` às três lanes. Timeout/ausência/Needs fixes = NO-GO.
- [ ] Correção material reroda todos os comandos e todas as lanes; sem exceção.

---

# Parte G — Wheel e runtime candidate real

## Task 23: Construir e autenticar wheel 0.8.0 sem alterar source F/E

**Files:**

- No repository changes
- External outputs under a private release root only

**Steps:**

- [ ] Reautenticar `parent(E)==F`, allowlist evidence-only e package version 0.8.0;
  qualquer builder/test ausente invalida F/E em vez de ser criado aqui.
- [ ] Extrair/usar exatamente o builder de F; produzir duas wheels em dirs externos
  limpos com `SOURCE_DATE_EPOCH` derivado do commit.
- [ ] Exigir bytes SHA-256 idênticos, metadata `0.8.0`, package universe fechado e zero
  `phase8_release`, test/evidence/SQLite/raw output na wheel.
- [ ] Instalar cada wheel em dois targets stdlib limpos, importar todos os packages e
  executar smoke contract fora do checkout.
- [ ] Criar package identity canônica; review AND no mesmo `(F,E,wheel)`.
- [ ] Não criar commit, não vendorizar em candidate existente e não construir OCI.

## Task 24: Criar runtime candidate3 limpo e fechar wiring operacional

**Files in the new runtime candidate:**

- Create: `chapada_leads/runtime.py`
- Create: `chapada_leads/readiness.py`
- Create: `chapada_leads/lifespan.py`
- Modify: `app.py`
- Modify: `chapada_leads/app.py`
- Modify: `chapada_leads/api/context.py`
- Modify: `chapada_leads/api/lifespan.py`
- Modify: `chapada_leads/api/use_cases/health.py`
- Modify: `chapada_leads/api/routers/health.py`
- Modify: `chapada_leads/api/routers/manychat.py`
- Modify: `chapada_leads/api/routers/payments.py`
- Modify: `chapada_leads/api/routers/actions.py`
- Create: `domain/phase8_maya_turn_port.py`
- Create: `domain/phase8_runtime_adapter.py`
- Create: `domain/phase8_behavior_snapshot.py`
- Modify: `domain/turn_coordinator_adapter.py`
- Replace: `domain/tool_dispatch_adapter.py`
- Create: `services/phase8_public_delivery.py`
- Create: `services/phase8_worker_supervisor.py`
- Create: `scripts/run_phase8_runtime_gate.py`
- Create: `.hermes/plugins/chapada_leads_boundary/__init__.py`
- Create: `.hermes/plugins/chapada_leads_boundary/plugin.yaml`
- Modify: `hermes_profiles/leads/skills/chapada-leads/skills-manifest.json`
- Create: five runtime test modules listed in Section 2
- Create: `tests/test_phase8_boundary_plugin.py`
- Create: `tests/test_phase8_runtime_ingress.py`
- Create: `tests/test_phase8_runtime_worker_supervision.py`
- Modify: `tests/test_app_shadow_webhook.py`
- Modify: `tests/test_phase7_turn_coordinator_adapter.py`
- Modify: `Dockerfile`, `pyproject.toml`, `uv.lock`

**Canonical factory:**

```text
uvicorn chapada_leads.runtime:create_app --factory
```

**Steps:**

- [ ] Criar `/home/ubuntu/workspace/agente-v2-phase8-runtime-candidate3` a partir do
  commit runtime Phase7 `183fb41d645e1bb04e237c986988309a28e42b34`, tree
  `e546e9d88093c09a245502bcca3d119e2e450672`, usando o Git object store da réplica
  sanitizada e nunca o runtime live. Candidate1 e o candidate2 experimental/sujo
  permanecem somente leitura e inelegíveis.
- [ ] O comando de criação é fixo e só pode ser executado após autenticar que o path
  não existe e que o base object é o commit/tree acima:

```bash
git -C /home/ubuntu/workspace/agente-v2-phase8-runtime-candidate1 worktree add \
  -b phase8-operational-runtime-candidate3 \
  /home/ubuntu/workspace/agente-v2-phase8-runtime-candidate3 \
  183fb41d645e1bb04e237c986988309a28e42b34
```
- [ ] RED boot real da factory encontra o adapter obrigatório ausente no predecessor e
  prova zero import/write live.
- [ ] Vendorizar exatamente a wheel aprovada; lockfile fixa a wheel por hash.
- [ ] Construir graph completo da Task 19, todos os workers/reconcilers/scavengers
  supervisionados, nenhuma dependency obrigatória `None`.
- [ ] Remover import-time global app e segundo composition graph; `create_app()` de
  produção não aceita adapter opcional, e runtime adapter não recebe callback de
  enqueue público pós-commit.
- [ ] Child Maya recebe somente plugin mínimo/UDS e model transport credential; runtime
  parent possui providers/delivery mas policies default-deny.
- [ ] Startup falha se root/schema/lock/mount/behavior/plugin/graph divergente;
  readiness só abre depois de semantic scans e attempt scavenge.
- [ ] `/health/live` mede processo/event loop; `/health/ready` fica 503 até graph e
  workers obrigatórios saudáveis. Morte de worker torna unready/fail-stop; shutdown
  fecha admission/readiness antes de drenar workers e stores.
- [ ] Matriz parametrizada cobre `/webhook/manychat`, `flush-ready`, `flush-contact` e
  auto-flush de lifespan. Cada caminho produz um aggregate receipt; duplicate consulta
  receipt sem Maya/read/kernel. Stripe/Wise/actions diretas ficam capability-closed.
- [ ] Dark/e2e/prod usam mesmas classes/factory; diferenças são role/policy/stage
  binding autenticados, não graph omission.
- [ ] Implementar o role gate com transports poison para provar graph completo e zero
  capability nos modos deny-all; o script precisa existir em runtime F antes da Task
  25.
- [ ] Testar startup/lifespan/shutdown com fakes capability-poison; não abrir rede nem
  chamar provider/ManyChat.
- [ ] F/E task review; candidate1/candidate2 permanecem intocados.

## Task 25: Congelar runtime F/E e revisar composição real

**Files:**

- No functional source/runtime/test/script changes
- Create sanitized runtime evidence envelopes outside executable payload and in E

**Steps:**

- [ ] Congelar runtime F limpo incluindo wheel, `uv.lock`, profile/config/skills/plugin,
  graph inventory, Dockerfile e tests.
- [ ] Executar a suite integral uma única vez em runtime F, em ambiente sem
  capabilities, com raw output no private evidence root:

```bash
uv run --frozen python -B -m pytest -q -p no:cacheprovider \
  --basetemp=/tmp/phase8-runtime-full
```

- [ ] Instalar produção offline em venv externo sem dev dependencies e executar
  `python -I` para provar versão `0.8.0` e imports da wheel/`chapada_leads.runtime`,
  nunca do checkout. Importar factory não inicia app/provider/worker.
- [ ] Executar os três graph/role gates adicionais, sem omitir classes:

```bash
uv run --frozen python -B scripts/run_phase8_runtime_gate.py --role dark --deny-all-effects
uv run --frozen python -B scripts/run_phase8_runtime_gate.py --role canary-e2e --deny-all-effects
uv run --frozen python -B scripts/run_phase8_runtime_gate.py --role production --deny-all-effects
```

- [ ] Esperado: graph/class digests iguais; somente role/policy/binding diferem;
  startup/readiness/shutdown verdes; external call counters zero.
- [ ] Criar runtime E filho direto, provar zero mudança no payload F.
- [ ] Reautenticar fingerprints de source F/E, runtime F/E, wheel, runtime live
  read-only e changed-path universe; drift de live exige reconciliação explícita.
- [ ] Review AND do mesmo `(runtime F,E,wheel,source F,E)`; qualquer fix material volta
  à task owner e invalida todas as lanes.

---

# Parte H — Release contract pré-build

## Task 26: Executar o release contract pré-build sem alterar candidatos

**Files:**

- No source F/E, wheel or runtime F/E changes
- External content-addressed prebuild objects and one sanitized decision envelope

**Canonical chain:**

```text
source F/E + wheel + runtime F/E
→ combined approval manifest externo
→ payload-context manifest
→ source attestation
→ canonical tar/build-input identity
→ expected OCI index/child/config/layer contract
```

**Steps:**

- [ ] Reautenticar source F/E, wheel/package review e runtime F/E. Se o release
  controller/test não estava em source F, invalidar F/E e voltar à Task 21.
- [ ] Executar exatamente os tests/controller extraídos de source F contra as
  identidades aprovadas; não corrigir código, teste, schema, plano ou runtime nesta
  task.
- [ ] Executar `scripts/phase8_prebuild_gate.py` de source F em root privado com
  argumentos fully explicit; `phase8_publish_oci.py` é apenas autenticado/poison-tested
  e não é chamado nesta task.
- [ ] Construir um único approval manifest combinado externo; payload manifest fecha
  membros e Dockerfile reachability; source attestation liga payload, F/E e wheel sem
  auto-hash.
- [ ] Produzir tar canônico externo com path order, uid/gid zero, modes/mtime/PAX
  literais e rejeição de membro não manifestado.
- [ ] Validar o contrato OCI esperado: index com exatamente um child executável
  `linux/arm64`, config/layers por digest e instance attestation separada. Não contatar
  daemon ou registry.
- [ ] Poison context com arquivo não listado/secret/DB/WAL precisa falhar antes de
  qualquer builder; review AND recebe o mesmo conjunto imutável de identities e
  prebuild object hashes.
- [ ] Produzir somente um relatório **GO/NO-GO de build**. O resultado GO não executa
  build; Carlos precisa autorizar um novo runbook com registry/repository/retention e
  credenciais já definidos fora do Git.

---

## 4. Gates pós-plano — deliberadamente não executáveis aqui

Após Task 26, criar planos/runbooks separados e pedir autorização própria, nesta ordem:

1. **Build único:** tar canônico fechado → OCI index/child/config/layers; autenticar
   registry e retenção de rollback.
2. **Dark canary:** mesmo child digest, state roots isolados, reads reais, zero write e
   zero delivery.
3. **Ingress isolado:** contato autorizado único, webhook/session/reply provenance,
   provider/public/payment effects ainda fechados.
4. **Conversa humana:** avisar Carlos; ele testa Maya naturalmente e decide o gate.
5. **E2E:** autorização fixa contato/workflow/provider/janela/alocação e único efeito;
   confirmar receipts source/target/provider/delivery e cancelamento.
6. **Rollout:** 1%→5%→25%→100% pelo mesmo child digest, cada estágio com GO próprio.
7. **Closeout e Fase 9:** decisões separadas; rollback nunca desfaz efeito comercial.

Nenhum desses passos recebe comando executável, credential, ID privado ou default neste
arquivo porque esses valores ainda não foram autorizados nem autenticados.

---

## 5. Verificação do próprio plano antes do Slice 0

Executar somente no commit documental candidato:

```bash
python3 -B -m unittest tests.test_phase8_entry -v
git diff --check
git status --short -uall
```

Além disso, um validator documental precisa provar:

- spec aprovada com commit/tree/blob/SHA-256/bytes/linhas exatos;
- plano substituto com hash/tamanho/linhas ligados ao manifesto de quarentena;
- banners `HISTORICAL-NON-EXECUTABLE` nos dois arquivos históricos executáveis;
- nove identidades históricas preservadas no manifesto;
- zero token/interface quarentenado nos active authority paths;
- ADR, página, índice, validation/rollout, risk register e evidence README coerentes;
- `implementation_authorized=false`, `build_authorized=false`, `rollout=NO-GO`;
- worktree limpa e review AND no mesmo commit documental.

Depois da revisão técnica do plano, apresentar a Carlos:

- identidade imutável do commit documental;
- resumo das 27 tasks e limite até GO/NO-GO de build;
- findings/restrições das lanes;
- pedido explícito de autorização para **Task 0 apenas** ou para uma faixa de tasks
  claramente enumerada.

Sem essa resposta, a execução permanece bloqueada.
