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
- construção da wheel **candidata** antes da Task 23; a Task 21 pode produzir wheels
  temporárias somente em roots descartáveis para RED/GREEN de reproducibilidade, sem
  package identity, review ou promoção;
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
- runtime candidate novo: criado somente na Task 24, em repositório independente,
  root limpo e autenticado;
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
    test_patch_blob: str         # P, bytes Git binary patch canônicos
    test_patch_sha256: str
    test_patch_paths: tuple[str, ...]
    staged_tree: str             # S = apply(U, P)
    execution_root_manifest_sha256: str
    execution_root_absolute: str
    argv: tuple[str, ...]        # R
    cwd: str
    env_name_allowlist: tuple[str, ...]  # nomes apenas, nunca valores
    python_version: str
    tool_versions: tuple[tuple[str, str], ...]
    exit_code: int
    duration_ns: int
    counts: tuple[tuple[str, int], ...]
    output_sha256: str           # O
    output_bytes: int

@dataclass(frozen=True)
class ExecutionRootManifest:
    absolute_root: str
    root_kind: Literal["detached_worktree", "temporary_index"]
    git_dir_identity: str
    head_commit: str
    staged_tree: str
    patch_paths: tuple[str, ...]
    python_executable: str
    python_version: str
    tool_versions: tuple[tuple[str, str], ...]
    env_names: tuple[str, ...]
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

O store externo possui exatamente `coord.lock`, `.staging/` e `objects/`. Publisher e
scavenger usam a ordem global `coord.lock → owner.lock`; qualquer segundo nome de lock
é proibido e não há caminho owner→coord. Sob `coord.lock`, o publisher cria o path
`.staging/{secrets.token_hex(16)}`, cria
`owner.lock` e `object.tmp` com `openat(O_CREAT|O_EXCL|O_NOFOLLOW, 0600)`, retém o
owner flock e então libera coord. Escreve `object.tmp`, calcula hash/bytes, faz fsync,
reabre no-follow, rehasheia, executa `chmod 0400` e um **segundo fsync do inode após o
chmod**. Só então publica em `objects/{expected_sha256}` por rename/link no-replace e faz
directory fsync. Os únicos prefixos de staging recuperáveis são literalmente:

```text
S0 = {}
S1 = {owner.lock}
S2 = {owner.lock, object.tmp}
```

Sob coord, scavenger remove+dir-fsync S0; para S1/S2 exige owner lock livre. Staging
com publisher vivo não é removido. Objeto final existente é reaberto no-follow,
owner/mode/hash/bytes são verificados e somente igualdade exata é aceita. Objeto final
divergente, symlink, owner/mode incorreto, membro desconhecido, scanner failure ou
retenção não comprovada é `MANUAL_REVIEW`, nunca overwrite. Manifest externo fixa
path/hash/bytes/retention; reviewer reabre e rehasheia. Git recebe somente P e envelopes
sanitizados com U/P/S/R/O, contagens e conclusão; raw output permanece no store.

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
- suites pesadas somente na Task 22, uma vez por candidato terminal;
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
  maya.py                      # MayaTurnPort e child lifecycle
  kernel_adapter.py            # adapter puro proposal→kernel decision
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
  locks.py                     # MemoryPreparationExecutionLockFactory
  memory_preparation.py        # memory-preparation-v1 / 1 table + filesystem protocol
  reconciliation.py           # admission + MemoryPreparationRecoveryWorker
phase8_release/
  __init__.py
  red_provenance.py
  evidence_store.py
  candidate_pair.py
  graph_scan.py
  payload_manifest.py
  source_attestation.py
  build_input.py
  build_authorization.py       # GO_BUILD_ONCE + one-shot ledger/receipt
  oci_identity.py
  approval_manifest.py
  terminal_packet.py          # packet V acíclico e publicação object-by-object
  validator.py
scripts/
  validate_phase8_contracts.py
  phase8_prebuild_gate.py
  phase8_publish_oci.py
  phase8_terminal_gate.py
  phase8_terminal_packet.py
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
| 10 Command relay reserva/settlement + internal handoff/learning | 13–14 |
| 11 Delivery pública | 16 |
| 12 Factory/readiness | 19 e 24 |
| 13 Ingress/legacy poison | 20 e 24 |
| 14 Terminal upstream | 22 |
| 15 Wheel | 21 e 23 |
| 16 Runtime candidate | 24 |
| 17 Runtime F/E | 25 |
| 18 Release contract | 21 e 26 |

Tasks 15–18 detalham authorities, execution locks, public delivery e qualification que
fazem parte das obrigações transversais dos Slices 10–13.

## 3.1 Contratos TDD autocontidos das Tasks 0–21

Esta seção é parte normativa da seção **Files/Interfaces/Steps** das Tasks 0–21. Para
cada linha, o executor:

1. cria os selectors listados em P, sem production path;
2. executa no staged tree S o argv formado por
   `("python3", "-B", "-m", "unittest") + tuple(selectors_da_linha) + ("-v",)`;
3. exige exit não-zero pela causa/asserção literal da linha, e não por import/typo;
4. publica U/P/S/R/O no diretório E exato;
5. implementa somente os producers enumerados;
6. repete o mesmo argv até exit `0`, executa o blast radius literal e prova P
   byte-idêntico de S→F.

### Ownership de interfaces

| Task | Consumes | Produces; único owner inicial |
|---:|---|---|
| 0 | commit documental aprovado + quarantine manifest | `RedProvenance`, `ExecutionRootManifest`, `EvidenceArtifactStore`, `CandidatePair`, contract scanner |
| 1 | canonical JSON v7 | todos os DTOs/unions v8, `SourceEventIdentity`, `CapabilityPolicy`, `BehaviorStateSnapshot`, bindings/receipts/relay bundles |
| 2 | `NormalizedToolProposal`, `KernelDecision` | `ToolDispatch.normalize_proposal`, `verify_authorized` |
| 3 | DTOs v8 | Boundary-v8 schema/store, `commit_turn_v8`, semantic scan |
| 4 | allocations/relay bundles v8 | Phase5-v6/Phase6-v2 stores, reservation/settlement ingress e derived outcome receipts |
| 5 | clock/deadline ports | migration-ownership-v1, `LegacyWriteGuard`, `LeadExecutionLockFactory` |
| 6 | qualification DTOs | QualificationJournal e memory-preparation-v1 schema/stores |
| 7 | `LeadExecutionLockFactory` da Task 5 | factory endurecida + transaction/deadline helpers |
| 8 | DTOs/ToolDispatch | UDS frame codec, gateway e `TranscriptCommitment` |
| 9 | UDS da Task 8 | `AttemptRootManager`, scavenger e `MayaTurnPort` |
| 10 | proposal + ToolDispatch | `KernelPort`/adapter puro e binding/evidence checks |
| 11 | Tasks 3–10 | coordinator v8, genesis tri-state, admission/commit/ACK handshake |
| 12 | receipt/store da Task 11 | duplicate replay byte-idêntico + integrity scanner |
| 13 | command/bundles/stores | `BoundaryCommandRelayWorker`; reservation **e settlement** target ingress |
| 14 | internal union/lead locks | handoff/learning worker+reconciler+canceler e `InternalJobExecutionLockFactory` |
| 15 | target authorities | provider/follow-up lock factories, senders e capability-free reconcilers |
| 16 | public allocation DTO/store | public execution lock, sender e reconciler |
| 17 | journal + todos os owner receipts | install/admission/cutoff/effect scan/learning seal |
| 18 | Task 17 + target closures | cancellation/reopen, `MemoryPreparationExecutionLockFactory` e recovery worker |
| 19 | todos os nodes anteriores | `RuntimeGraphManifest`, readiness scan, behavior/policy digests |
| 20 | graph scanner | ingress universe literal e legacy/child capability poison |
| 21 | F/E candidates ainda mutáveis | wheel/release/prebuild/publish tooling e runners completos |
| 22 | producers 0–21 congelados | gate puro: source F, evidence-only E, terminal result e review request; nenhum novo producer funcional |

Nenhuma task posterior pode introduzir producer atribuído a uma linha anterior sem
invalidar seu F/E e repetir o RED/review da task owner.

### Selectors, RED causal e blast radius

| Task | Selectors exatos em R | RED causal obrigatório | Blast radius após GREEN |
|---:|---|---|---|
| 0 | `tests.test_phase8_red_provenance.RedProvenanceTests.test_records_exact_patch_paths_execution_manifest_versions_duration_counts_without_env_values`; `tests.test_phase8_evidence_store.EvidenceStoreTests.test_scavenger_accepts_only_literal_s0_s1_s2_and_never_removes_live_publisher`; `tests.test_phase8_contract_lock.ContractLockTests.test_quarantined_interfaces_have_zero_active_owner` | tipos/módulos ausentes; falha deve nomear o producer, nunca collection error | os três módulos Phase8 + `scripts/validate_phase8_contracts.py` |
| 1 | `tests.test_phase8_conversation_types.ConversationTypesTests.test_closed_registry_matches_approved_spec_contract`; `tests.test_phase8_wire_v8.WireV8Tests.test_rejects_unknown_bool_float_mutable_and_cross_domain_bytes` | registry/type v8 ausente ou schema mismatch exato | quatro módulos Phase8 + `tests.test_phase7_serialization` + `tests.test_phase7_types` |
| 2 | `tests.test_phase8_tool_dispatch.ToolDispatchV8Tests.test_normalize_never_authorizes_and_verify_requires_exact_kernel_decision`; `tests.test_phase8_tool_dispatch.ToolDispatchV8Tests.test_handmade_or_stale_proposal_is_rejected` | método v8 ausente ou proposal→decision mismatch | módulo Phase8 + `tests.test_phase7_dispatch` |
| 3 | `tests.test_phase8_boundary_schema_v8.BoundarySchemaV8Tests.test_exact_eleven_table_universe_and_ddl_hash`; `tests.test_phase8_boundary_atomic_commit.BoundaryAtomicCommitTests.test_every_statement_fault_rolls_back_all_logical_rows` | universe ainda v7/6 ou `commit_turn_v8` ausente | três módulos Phase8 + Phase7 schema/store/serialization |
| 4 | `tests.test_phase8_phase5_v6.Phase5V6Tests.test_exact_eight_table_universe`; `tests.test_phase8_phase6_v2.Phase6V2Tests.test_exact_fourteen_table_universe`; `tests.test_phase8_target_ingress.TargetIngressTests.test_reservation_and_settlement_bind_preinstalled_allocation_atomically` | target schemas antigos/ingress ausente | quatro módulos Phase8 + Phase5/6 schema/store |
| 5 | `tests.test_phase8_migration_ownership.MigrationOwnershipTests.test_freeze_drains_old_epoch_permits_without_waiting_under_lead_lock`; `tests.test_phase8_legacy_write_guard.LegacyWriteGuardTests.test_revalidates_permit_before_dispatch_and_commit`; `tests.test_phase8_migration_contention.MigrationContentionTests.test_shared_lead_lock_serializes_register_freeze_and_release` | package/factory/guard ausentes | quatro módulos Phase8, `200` contention schedules |
| 6 | `tests.test_phase8_qualification_schema.QualificationSchemaTests.test_exact_five_table_universe`; `tests.test_phase8_memory_preparation_schema.MemoryPreparationSchemaTests.test_exact_one_table_and_closed_receipt_tuples` | roots/schemas ausentes | três módulos Phase8 + semantic scans |
| 7 | `tests.test_phase8_lead_lock.LeadLockTests.test_same_db_and_lead_contend_across_processes`; `tests.test_phase8_deadline_transaction.DeadlineTransactionTests.test_deadline_at_lock_begin_first_write_and_commit_changes_zero_rows` | factory não endurecida/deadline não reamostrada | dois módulos Phase8 + Phase7 store/coordinator |
| 8 | `tests.test_phase8_uds_frames.UdsFrameTests.test_rejects_length_duplicate_key_sequence_and_divergent_retry`; `tests.test_phase8_uds_peer_auth.UdsPeerAuthTests.test_rejects_wrong_uid_pid_group_and_second_connection`; `tests.test_phase8_uds_transcript.UdsTranscriptTests.test_terminal_commitment_recomputes_without_hmac_secret` | codec/gateway ausente | quatro módulos Phase8 + `2000` malformed frames |
| 9 | `tests.test_phase8_attempt_root.AttemptRootTests.test_publish_prefixes_are_s0_s1_s2_s3_then_active`; `tests.test_phase8_attempt_scavenger.AttemptScavengerTests.test_sigkill_restart_never_resumes_or_deletes_unknown_member`; `tests.test_phase8_maya_turn_port.MayaTurnPortTests.test_child_graph_has_only_minimal_uds_plugin` | attempt/Maya producer ausente | três módulos Phase8 + SIGKILL/os._exit/restart catalog |
| 10 | `tests.test_phase8_kernel_adapter.KernelAdapterTests.test_unresolved_read_fact_or_command_without_proposal_is_rejected`; `tests.test_phase8_kernel_ownership.KernelOwnershipTests.test_kernel_and_tooldispatch_are_only_authorizers` | kernel adapter ausente/legacy path reachable | dois módulos Phase8 + Phase7 dispatch/coordinator |
| 11 | `tests.test_phase8_coordinator_genesis.CoordinatorGenesisTests.test_unavailable_never_becomes_empty_genesis`; `tests.test_phase8_coordinator_commit.CoordinatorCommitTests.test_reply_receipt_relays_jobs_and_chunks_commit_atomically`; `tests.test_phase8_admission_handshake.AdmissionHandshakeTests.test_abort_and_commit_are_linearized_by_same_lead_lock` | coordinator v7 não satisfaz handshake | três módulos Phase8 + Phase7 coordinator/store |
| 12 | `tests.test_phase8_duplicate_replay.DuplicateReplayTests.test_restart_duplicate_returns_exact_persisted_bytes_with_all_ports_poisoned`; `tests.test_phase8_receipt_integrity.ReceiptIntegrityTests.test_missing_extra_or_divergent_child_blocks_replay_and_readiness` | duplicate chama port ou integrity scan ausente | dois módulos Phase8 + `2000` replay/restart properties |
| 13 | `tests.test_phase8_reservation_relay.ReservationRelayTests.test_target_commit_source_ack_recovers_same_receipt`; `tests.test_phase8_settlement_relay.SettlementRelayTests.test_settlement_is_command_relay_not_internal_job`; `tests.test_phase8_relay_target_ack.RelayTargetAckTests.test_stale_tuple_or_divergent_target_is_rejected_without_provider` | relay/settlement owner ausente | três módulos Phase8 + Phase5/6 store/replay faults |
| 14 | `tests.test_phase8_internal_jobs.InternalJobTests.test_union_is_exactly_handoff_or_learning`; `tests.test_phase8_internal_job_execution_lock.InternalJobLockTests.test_worker_reconciler_and_canceler_share_lock_through_target_ack`; `tests.test_phase8_learning_job.LearningJobTests.test_memory_and_receipt_commit_atomically` | settlement aceito no union ou factory ausente | quatro módulos Phase8 + Phase6 handoff + barrier faults |
| 15 | `tests.test_phase8_provider_execution_lock.ProviderLockTests.test_sender_rechecks_lease_deadline_and_allocation_under_shared_lock`; `tests.test_phase8_followup_delivery_lock.FollowupLockTests.test_fence_delivery_receipt_and_allocation_are_serialized`; `tests.test_phase8_capability_free_reconciler.CapabilityFreeReconcilerTests.test_reconciler_has_no_external_port` | lock factory/recheck ausente | três módulos Phase8 + Phase5/6 worker/reconciliation, `200` races/family |
| 16 | `tests.test_phase8_public_allocations.PublicAllocationTests.test_exact_manifest_precedes_ingress_and_close_blocks_late_bind`; `tests.test_phase8_public_delivery.PublicDeliveryTests.test_idempotency_key_binds_release_lead_target_channel_and_chunk`; `tests.test_phase8_public_reconciliation.PublicReconciliationTests.test_uncertain_fence_never_resends` | public authority/lock ausente | três módulos Phase8 + faults/restarts |
| 17 | `tests.test_phase8_qualification_install.QualificationInstallTests.test_open_requires_three_target_installation_receipts`; `tests.test_phase8_admission_cutoff.AdmissionCutoffTests.test_cutoff_and_membership_copy_are_one_transaction`; `tests.test_phase8_effect_scan.EffectScanTests.test_exact_owner_receipts_and_nonzero_provider_public_budget_required`; `tests.test_phase8_learning_seal.LearningSealTests.test_learning_claim_close_and_seal_use_target_commit_journal_ack` | controller path/status/receipt ausente | quatro módulos Phase8 + restart em cada transition |
| 18 | `tests.test_phase8_qualification_cancel.QualificationCancelTests.test_all_eight_origins_freeze_both_fsms_and_preserve_artifacts`; `tests.test_phase8_memory_prepare.MemoryPrepareTests.test_s0_to_s5_recovery_is_exact`; `tests.test_phase8_memory_abandon.MemoryAbandonTests.test_a0_to_a4_each_barrier_converges_with_zero_payload`; `tests.test_phase8_memory_recovery.MemoryRecoveryTests.test_worker_has_only_lookup_resume_ack_abandon_and_shared_lock`; `tests.test_phase8_qualification_reopen.QualificationReopenTests.test_new_epoch_root_ids_and_old_ack_rejection` | lock/recovery/cancel producer ausente | cinco módulos Phase8 + `2000` restarts por grammar catalog |
| 19 | `tests.test_phase8_runtime_graph_contract.RuntimeGraphContractTests.test_manifest_contains_every_worker_reconciler_canceler_lock_and_recovery_node`; `tests.test_phase8_readiness_contract.ReadinessContractTests.test_schema_root_lock_receipt_or_worker_mismatch_is_not_ready` | node/digest scanner ausente | dois módulos Phase8 + closed graph mutation catalog |
| 20 | `tests.test_phase8_ingress_universe.IngressUniverseTests.test_four_turn_ingresses_have_exact_source_identities_and_coordinator_owner`; `tests.test_phase8_legacy_poison.LegacyPoisonTests.test_every_mutator_is_coordinator_or_shared_migration_guarded`; `tests.test_phase8_child_capability_graph.ChildCapabilityGraphTests.test_child_cannot_import_legacy_provider_delivery_or_memory_writer` | alternate owner/import reachable | três módulos Phase8 + runtime inventory static scan |
| 21 | `tests.test_phase8_wheel_reproducibility.WheelReproducibilityTests.test_two_temporary_builds_are_byte_identical`; `tests.test_phase8_build_authorization.BuildAuthorizationTests.test_go_build_once_binds_all_inputs_destination_expiry_nonce_and_consumes_once`; `tests.test_phase8_publish_gate.PublishGateTests.test_malformed_stale_replay_or_non_loopback_calls_poison_runner_zero_times`; `tests.test_phase8_oci_identity.OciIdentityTests.test_single_arm64_child_and_rollback_config_rootfs_are_exact`; `tests.test_phase8_terminal_gate.TerminalGateTests.test_all_runners_catalogs_and_contract_validators_were_present_before_f`; `tests.test_phase8_terminal_gate.TerminalGateTests.test_e_is_direct_evidence_only_child_and_all_red_blobs_match_s_to_f`; `tests.test_phase8_terminal_packet.TerminalPacketTests.test_good_packet_is_acyclic_and_published_as_single_objects`; `tests.test_phase8_terminal_packet.TerminalPacketTests.test_self_reference_schema_hash_or_publication_mismatch_is_rejected` | release/terminal-gate/packet producers ausentes, validator sintético aceita F/E inválido ou packet aceita autorreferência/hash/publicação divergente | módulos wheel, wheel_reproducibility, payload_manifest, source_attestation, build_input, build_authorization, oci_identity, approval_manifest, publish_gate, terminal_gate e terminal_packet |

Task 22 é deliberadamente excluída desta matriz RED/GREEN: é um gate puro sobre
bytes já congelados. Os testes de `tests.test_phase8_terminal_gate` pertencem à Task
21 e usam fixtures sintéticos para provar que o validator rejeita parent errado,
child não evidence-only, runner/catalog ausente e P-path→S-blob→F-blob divergente.
Depois de F e E reais existirem, a Task 22 executa esse mesmo validator já congelado
em F com os dois commits explícitos; ela não cria novo P, selector ou U/P/S/R/O.

### Evidence child paths exatos

Para cada task `00`–`21`, a seção **Files** inclui implicitamente as ações abaixo no
ref/worktree E separado, sob o diretório enumerado na tabela:

```text
Create in E: red.patch
Create in E: red-provenance.json
Create in E: green-result.json
Create in E: candidate-pair.json
Create in E: SHA256SUMS
```

Task 22 não herda esses cinco artifacts RED/GREEN. Suas ações `Create in E` são
declaradas literalmente na própria Task 22.

```text
red.patch
red-provenance.json
green-result.json
candidate-pair.json
SHA256SUMS
```

| Task | Diretório E exato |
|---:|---|
| 0 | `docs/refactor/evidence/phase-08/tasks/task-00/` |
| 1 | `docs/refactor/evidence/phase-08/tasks/task-01/` |
| 2 | `docs/refactor/evidence/phase-08/tasks/task-02/` |
| 3 | `docs/refactor/evidence/phase-08/tasks/task-03/` |
| 4 | `docs/refactor/evidence/phase-08/tasks/task-04/` |
| 5 | `docs/refactor/evidence/phase-08/tasks/task-05/` |
| 6 | `docs/refactor/evidence/phase-08/tasks/task-06/` |
| 7 | `docs/refactor/evidence/phase-08/tasks/task-07/` |
| 8 | `docs/refactor/evidence/phase-08/tasks/task-08/` |
| 9 | `docs/refactor/evidence/phase-08/tasks/task-09/` |
| 10 | `docs/refactor/evidence/phase-08/tasks/task-10/` |
| 11 | `docs/refactor/evidence/phase-08/tasks/task-11/` |
| 12 | `docs/refactor/evidence/phase-08/tasks/task-12/` |
| 13 | `docs/refactor/evidence/phase-08/tasks/task-13/` |
| 14 | `docs/refactor/evidence/phase-08/tasks/task-14/` |
| 15 | `docs/refactor/evidence/phase-08/tasks/task-15/` |
| 16 | `docs/refactor/evidence/phase-08/tasks/task-16/` |
| 17 | `docs/refactor/evidence/phase-08/tasks/task-17/` |
| 18 | `docs/refactor/evidence/phase-08/tasks/task-18/` |
| 19 | `docs/refactor/evidence/phase-08/tasks/task-19/` |
| 20 | `docs/refactor/evidence/phase-08/tasks/task-20/` |
| 21 | `docs/refactor/evidence/phase-08/tasks/task-21/` |
| 22 | `docs/refactor/evidence/phase-08/tasks/task-22/` |

`red.patch` é o blob P exato; `red-provenance.json` fecha U/P/S/R/O e retention;
`green-result.json` fecha o mesmo R em F e o blast radius; `candidate-pair.json` fecha
F/E, `parent(E)==F`, allowlists e P-path→S-blob→F-blob; `SHA256SUMS` cobre os quatro
artifacts anteriores. Nenhum desses diretórios recebe raw output, DB, WAL, socket,
token ou PII.

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
- Create: `tests/fixtures/phase8_wire_contract_v8.json`

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
`E2EScenarioContract`, `ProviderEffectOutcomeReceipt`,
`ScenarioTerminalVerificationReceipt`, `ExactEffectAllocationManifest` e os receipts
fechados de installation/closure. Também são obrigatórios
`QualificationCancelStartReceipt`, `QualificationCancelReceipt`,
`ReopenPreparationIntent`, `ReopenIntentAbandonStartReceipt`,
`ReopenIntentAbandonReceipt`, `MemoryPreparationReceipt`,
`MemoryPreparationAckReceipt`, `MemoryPreparationAbandonReceipt`,
`QualificationReopenReceipt`, `SourceEventIdentity`,
`LearningClaimsClosedReceipt`, `ConversationTestDispatchAuthorization`,
`CapabilityPolicy` e `BehaviorStateSnapshot`. `ReservationRelayBundle`,
`SettlementRelayBundle` e `HandoffRelayBundle` também pertencem ao wire fechado, com
owners de execução nas Tasks 13 e 14.

**Implementation Closure Registry — autoridade e limite:** a spec aprovada no commit
`2889e9e…` é autoridade única para invariantes arquiteturais e shapes descritivos. Ela
usa deliberadamente nomes descritivos e assinaturas elípticas em alguns pontos. O
registry literal abaixo é um **refinamento de implementação pertencente ao
plano**, necessário para tornar TDD, wire e ownership não ambíguos; ele não afirma
que esses nomes, field lists, assinaturas ou domains aparecem literalmente na spec
aprovada. Este refinamento não pode ampliar effects, capabilities, owners,
tabelas, cardinalidades, FSMs ou gates da spec. Conflito com qualquer invariante da
spec é stop condition e exige delta arquitetural, não interpretação local.

O registry só se torna autoridade executável depois de: (1) review AND 3/3 sobre o
mesmo commit do plano; e (2) aprovação humana explícita desse plano ou de uma futura
autorização de implementação que cite sua identidade. Até ambos existirem, é
candidate documental e Task 0 permanece bloqueada. O validator da Task 1 autentica
separadamente o commit/blob/hash da spec e o hash deste **Implementation Closure
Registry** no commit do plano; ele não tenta extrair do texto da spec nomes que a spec
não contém.

**Facts/reads closure authority:** Carlos Eduardo aprovou explicitamente em
2026-07-22 a identidade `6f638234a200a72178dac66705d739a4b597048f` como
autoridade executável do refinamento mínimo de facts/reads e autorizou sua
implementação em micro-unidades RED/GREEN. Essa identidade contém somente:

- spec delta `docs/superpowers/specs/2026-07-22-phase-8-facts-reads-wire-closure-design.md`,
  blob `3fea9b602042749fa0140f0de088f6c8cf5f981c`, SHA-256
  `f66f3aeb14ad47ad55d76dfa2b9155335d8c5b20765feb92d338bb9973bef2ef`;
- fixture normativa `tests/fixtures/phase8_facts_reads_wire_v1.json`, blob
  `05897324f75d7fd36cffb575699980c17eb16495`, SHA-256
  `fabdb3677cbd9d1b1157fd1cadcfb589bf8a5f1fb5a8cd827aff2a33a4395241`.

A rechecagem estática final desse commit encerrou F1–F5 (`Approved`) sem ampliar
effects, capabilities, owners de autorização, FSMs, tabelas ou gates. A autoridade
é restrita a `TypedFact`, `ReservationExecutionProjection`,
`ConversationProjection`, requests/receipts/evidence de reads, union sanitizada e
`ReadObservation`; qualquer outro contrato continua sujeito ao registry e aos gates
já aprovados neste plano.

Cada tipo possui `SCHEMA`, `VERSION`, `DOMAIN` e `to_canonical_bytes`; o registry e o
fixture independente comparam lista completa ordenada de campos, enums, nullability,
assinaturas e domains, rejeitando item ausente **ou extra**. Adicionar campo aberto ou
omitir receipt exige nova identidade/review/aceite do plano e, se alterar invariante,
delta arquitetural.

**Field registry mínimo literal:**

```text
SourceEventIdentity(source_event_id, source_event_hash)
ConversationProjection(stage, desired_services, locale, facts,
                       reservation_execution_projection)
MayaTurnRequest(boundary_state_bytes, state_version, state_hash,
                normalized_message, aggregate_turn_id, source_events,
                lead_key_hash, private_delivery_binding_hash, deadline_at,
                behavior_profile_fingerprint)
MayaIntentClosure(kind, selection, confirmation, handoff)
MayaTurnClosure(aggregate_turn_id, intent_closure, public_text, route, reply_type,
                final_seq, expected_prefix_mac, ephemeral_session_id,
                zero_requests_in_flight)
ReadObservation(request_bytes, request_hash, status, typed_result_bytes,
                result_hash, derived_facts, safe_for_public_claims,
                frame_commitment_hash)
TranscriptCommitment(direction, kind, sequence, request_id, request_hash,
                     response_hash, previous_frame_commitment)
BehaviorStateSnapshot(schema, version, memory_snapshot_hash)
CapabilityPolicy(capability_matrix, worker_modes, guard_semantics)
ReservationRelayBundle(genesis_state, phase5_events, summary_outboxes,
                       expected_final_state, expected_final_state_hash,
                       command_ledger_seed, qualification_id, scenario_id,
                       immutable_generation, allocation_id, artifact_hash)
SettlementRelayBundle(workflow_anchor, policy, payment_history, evidence,
                      payment_command, expected_final_state,
                      expected_final_state_hash, qualification_id, scenario_id,
                      immutable_generation, allocation_id, artifact_hash)
ScenarioTerminalVerificationReceipt(qualification_id, epoch, scenario_id,
                    scenario_contract_hash, cutoff_sequence, admitted_set_hash,
                    admitted_turn_receipt_aggregate_hash,
                    target_ingress_receipt_aggregate_hash,
                    provider_effect_outcome_aggregate_hash,
                    followup_delivery_receipt_aggregate_hash,
                    public_delivery_receipt_aggregate_hash,
                    compensation_receipt_aggregate_hash, final_state_hash,
                    final_economic_hash, allocation_manifest_hash,
                    exact_effect_budget_hash, previous_qualification_artifact_hash)
```

Os domínios literais são:

```text
RESERVATION_RELAY_DOMAIN = phase8-reservation-relay-bundle-v1
SETTLEMENT_RELAY_DOMAIN = phase8-settlement-relay-bundle-v1
SCENARIO_TERMINAL_VERIFICATION_DOMAIN = phase8-scenario-terminal-verification-v1
```

Nos dois relay bundles, os quatro campos E2E
`qualification_id|scenario_id|immutable_generation|allocation_id` são todos nulos ou
todos presentes. `phase5_events`, `summary_outboxes`, `payment_history` e `evidence`
são tuples ordenados de canonical bytes; vazio e ausente não são equivalentes.
`artifact_preimage_bytes()` serializa por canonical JSON, na ordem do registry, todos
os campos do bundle **exceto** `artifact_hash`; portanto exclui o campo artifact_hash
da própria preimage. `artifact_hash = SHA256(DOMAIN || 0x00 ||
artifact_preimage_bytes())`. `to_canonical_bytes()` inclui o `artifact_hash` já
calculado e o decoder sempre o recompõe e compara. `source_turn_receipt_hash` fica
somente na relay row/assinatura de ingress e é excluído do hash do bundle. No receipt terminal,
aggregate vazio usa o hash canônico do tuple vazio, nunca `None`; todos os aggregates
são rederivados das rows owner e `previous_qualification_artifact_hash` fecha o
backlink no journal.

`MayaIntentClosure` não possui facts/tool/command. `CapabilityPolicy` não possui roots,
allowlist concreta ou percentual. O fixture fecha ainda: todos os artifact canonical
bytes/hash + frame backlink; bindings com os doze campos literais da spec; scenario/
allocation families, roles e parent tuples; receipt predecessor/backlink tuples; FSMs;
nullable all-null/all-present; e domain strings versionadas. O teste compara o fixture
com uma constante independente em cada módulo e falha se um símbolo referido neste
plano estiver sem owner, field list, enum list ou hash domain.

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
- [ ] Executar os quatro módulos focados e regressão
  `tests.test_phase7_serialization tests.test_phase7_types`; criar F/E e review AND.

## Task 2: Separar normalização de autorização em ToolDispatch

**Files:**

- Modify: `reservation_boundary/dispatch.py`
- Modify: `reservation_boundary/types.py`
- Create: `tests/test_phase8_tool_dispatch.py`
- Modify: `tests/test_phase7_dispatch.py`

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

**Ingress APIs exatas:**

```python
class SQLiteUnitOfWork:
    def accept_boundary_reservation(
        self,
        *,
        operation_id: str,
        source_turn_receipt_hash: str,
        bundle: ReservationRelayBundle,
    ) -> TargetOperationReceipt: ...

class SQLiteFollowupUnitOfWork:
    def accept_boundary_settlement(
        self,
        *,
        operation_id: str,
        source_turn_receipt_hash: str,
        bundle: SettlementRelayBundle,
    ) -> TargetOperationReceipt: ...
```

`operation_id` é o target operation ID domain-separated da relay row; os UoWs
recalculam `bundle.artifact_hash`, validam o backlink separado e retornam o receipt
owner byte-idêntico em duplicate. Eles nunca aceitam `dict|bytes` genérico no lugar do
tipo fechado.

**Steps:**

- [ ] RED: roots novos aceitos; v5/v1, migration extra ou universe inesperado são stop
  condition.
- [ ] Implementar instalação atômica `generation_header + manifest completo`,
  header-tombstone quando close vence install e states literais por header/allocation.
- [ ] Implementar as duas assinaturas acima e `accept_boundary_handoff` com seu
  `HandoffRelayBundle`: full replay, target receipt e allocation bind na mesma
  transaction; duplicate byte-idêntico, conflito terminal.
- [ ] Implementar pure derivations `derive_reservation_effect_receipt` e
  `derive_settlement_effect_receipt`; não criar segundo owner de outcome.
- [ ] Endurecer follow-up outboxes com slot 0/1, lease/deadline imutável e authority FK
  all-null/all-present.
- [ ] GREEN focado + regressão completa Phase5/6; F/E + AND.

## Task 5: migration-ownership-v1 e permits legacy

**Files:**

- Create: `reservation_boundary/locks.py`
- Create: `reservation_migration/__init__.py`
- Create: `reservation_migration/types.py`
- Create: `reservation_migration/schema.py`
- Create: `reservation_migration/sqlite_store.py`
- Create: `reservation_migration/locks.py`
- Create: `reservation_migration/guard.py`
- Create: `reservation_migration/reconciliation.py`
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

- [ ] Produzir `LeadExecutionLockFactory` nesta task, antes do primeiro contention
  probe. API: `acquire(ownership_db_identity, lead_hash, deadline, clock)` retorna
  context manager flock por dirfd/no-follow. Task 7 consome e endurece a mesma classe;
  não cria factory concorrente.
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

- Create: `reservation_qualification/__init__.py`
- Create: `reservation_qualification/types.py`
- Create: `reservation_qualification/schema.py`
- Create: `reservation_qualification/sqlite_store.py`
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

## Task 7: Endurecer lead lock deadline-aware e transaction curta

**Files:**

- Modify: `reservation_boundary/locks.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Create: `tests/test_phase8_lead_lock.py`
- Create: `tests/test_phase8_deadline_transaction.py`

**API:**

```python
class LeadExecutionLockFactory:
    def acquire(self, *, ownership_db_identity: str, lead_hash: str,
                deadline: datetime, clock: Clock) -> AbstractContextManager[None]: ...
```

**Consumes:** a factory mínima criada na Task 5. **Produces:** a mesma API com deadline,
path/device/inode/mount identity e crash semantics completas para Tasks 11, 17 e 18.

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

## Task 13: Reservation e settlement command relays com target ACK

**Files:**

- Create: `reservation_boundary/relay.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Modify: `reservation_execution/sqlite_store.py`
- Modify: `reservation_followup/sqlite_store.py`
- Create: `tests/test_phase8_reservation_relay.py`
- Create: `tests/test_phase8_settlement_relay.py`
- Create: `tests/test_phase8_relay_target_ack.py`

**Target ports e consumo exatos:**

```python
class ReservationIngressPort(Protocol):
    def lookup(
        self,
        *,
        operation_id: str,
        artifact_hash: str,
        source_turn_receipt_hash: str,
    ) -> OperationReceiptLookupResult: ...

    def accept(
        self,
        *,
        operation_id: str,
        source_turn_receipt_hash: str,
        bundle: ReservationRelayBundle,
    ) -> TargetOperationReceipt: ...

class SettlementIngressPort(Protocol):
    def lookup(
        self,
        *,
        operation_id: str,
        artifact_hash: str,
        source_turn_receipt_hash: str,
    ) -> OperationReceiptLookupResult: ...

    def accept(
        self,
        *,
        operation_id: str,
        source_turn_receipt_hash: str,
        bundle: SettlementRelayBundle,
    ) -> TargetOperationReceipt: ...
```

`BoundaryCommandRelayWorker` recebe somente boundary store, os dois ports tipados,
clock e policy. Ele escolhe o port pelo command kind fechado, passa exatamente o
bundle persistido e o backlink separado, valida o `TargetOperationReceipt` contra
operation/bundle/source tuple e então produz `BoundaryRelayReceipt` no source ACK.
Nenhum port aceita `BoundaryInternalJob`, provider ou generic callable.

**Steps:**

- [ ] RED pending/leased/acked/cancelled/manual_review FSM, full tuple lease CAS, stale
  ACK, preparation max 3 e target identity conflict.
- [ ] `BoundaryCommandRelayWorker` é owner 1:1 de **todo** `boundary_command`.
  `ReservationRelayBundle` contém genesis/eventos/summary outboxes/final state/
  command-ledger seed/allocation; `SettlementRelayBundle` contém
  anchor/policy/history/evidence/command/final state/allocation. Ambos carregam
  `artifact_hash`; a relay row carrega `source_turn_receipt_hash` fora do bundle hash.
- [ ] Implementar os ingresses fechados
  `SQLiteUnitOfWork.accept_boundary_reservation(...)` e
  `SQLiteFollowupUnitOfWork.accept_boundary_settlement(...)`; full replay, target
  receipt e allocation bind acontecem na mesma transaction e nunca chamam provider.
- [ ] Worker one-shot faz claim→prepare→target idempotent ingress→receipt validation→
  source ACK; nunca chama provider.
- [ ] Crash antes target libera/requeue; crash target-commit/source-ACK retorna mesmo
  target receipt e completa ACK; divergent target vai manual review.
- [ ] Closure cancela apenas pre-target; target receipt existente precisa reconcile/ACK.
- [ ] GREEN + faults/restarts; F/E + AND.

## Task 14: Internal jobs exclusivamente para handoff e learning

**Files:**

- Modify: `reservation_boundary/relay.py`
- Modify: `reservation_boundary/locks.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Modify: `reservation_followup/sqlite_store.py`
- Modify: `reservation_followup/workers.py`
- Create: `tests/test_phase8_internal_jobs.py`
- Create: `tests/test_phase8_internal_job_execution_lock.py`
- Create: `tests/test_phase8_handoff_ingress.py`
- Create: `tests/test_phase8_learning_job.py`

**Steps:**

- [ ] RED prova que `BoundaryInternalJob` aceita somente
  `HandoffRelayBundle|LearningProposal`; settlement internal job é schema error e
  permanece command relay da Task 13. Operation ID é determinístico, target lookup
  side-effect-free e `NOT_FOUND` somente após zero-scan completo.
- [ ] Implementar `InternalJobExecutionLockFactory.acquire(boundary_db_identity,
  boundary_job_id, deadline, clock)` em
  `internal-target/{boundary_db_identity}/{boundary_job_id}.lock`, por dirfd/no-follow.
- [ ] Worker/reconciler/canceler compartilham `InternalJobExecutionLockFactory` por
  boundary DB + job; lock cobre lookup→target commit→source ACK.
- [ ] Handoff ingress persiste full replay/receipts/allocations; learning aplica
  `expected_version/hash` e receipt na mesma memory transaction.
- [ ] Outcome parcial/órfão/uncerto termina manual review; reconciler não recebe
  capability genérica de mutation.
- [ ] Pause probes em lookup, target commit e source ACK contra canceler; stale worker
  faz zero primeira mutation após terminalização.
- [ ] GREEN + regressão handoff/payment/followup; F/E + AND.

## Task 15: Provider e follow-up execution locks

**Files:**

- Create: `reservation_execution/locks.py`
- Modify: `reservation_execution/reconciliation.py`
- Modify: `reservation_execution/worker.py`
- Create: `reservation_followup/locks.py`
- Modify: `reservation_followup/reconciliation.py`
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

- Create: `reservation_qualification/controller.py`
- Create: `reservation_qualification/admission.py`
- Create: `reservation_qualification/effect_scan.py`
- Modify: `reservation_qualification/sqlite_store.py`
- Create: `tests/test_phase8_qualification_install.py`
- Create: `tests/test_phase8_admission_cutoff.py`
- Create: `tests/test_phase8_effect_scan.py`
- Create: `tests/test_phase8_learning_seal.py`

**Terminal scenario verification API:**

```python
class ScenarioEffectScanner:
    def verify_terminal_scenario(
        self,
        *,
        qualification_id: str,
        epoch: int,
        scenario_id: str,
        expected_contract_hash: str,
        expected_allocation_manifest_hash: str,
        previous_qualification_artifact_hash: str,
    ) -> ScenarioTerminalVerificationReceipt: ...

class SQLiteQualificationStore:
    def commit_scenario_terminal_verification(
        self,
        *,
        receipt: ScenarioTerminalVerificationReceipt,
        expected_run_revision: int,
    ) -> ScenarioTerminalVerificationReceipt: ...
```

O scanner recebe ports read-only fechados para boundary, Phase5, Phase6 e public
delivery, carrega o `E2EScenarioContract` e o `ExactEffectAllocationManifest`
persistidos pelo tuple informado e rederiva todos os aggregates. Nenhum aggregate ou
owner receipt é aceito do caller. O store persiste cenário + qualification artifact
na mesma transaction; duplicate do mesmo receipt retorna bytes idênticos e qualquer
contract/allocation/revision/backlink divergente é conflito terminal.

**Steps:**

- [ ] RED contrato exige cenário não vazio, pelo menos um provider terminal e uma
  public delivery terminal; read-only/zero-budget não qualifica.
- [ ] Criar run + scenarios em `INSTALLING`; instalar manifests nos três roots com
  operation IDs estáveis e journal ACK; somente então CAS conjunto para `OPEN`.
- [ ] Admission membership digest exclui status/revision/timestamps; lead lock protege
  fence/commit/ACK e reconciler abort.
- [ ] `OPEN→QUALIFYING` congela cutoff + admitted-set na mesma transaction e fecha
  learning claims normais pela operação idempotente que retorna
  `LearningClaimsClosedReceipt`; target-commit/journal-ACK é retomável e o receipt fica
  em `qualification_artifacts` antes de effects scan.
- [ ] `ScenarioEffectScanner.verify_terminal_scenario(...)` rederiva target ingresses,
  provider outcomes e deliveries owner-owned; cardinalidade ausente/extra ou estado
  não terminal falha. O controller persiste o
  `ScenarioTerminalVerificationReceipt` pela assinatura fechada acima antes de
  avançar a run.
- [ ] Avançar por CAS/receipts
  `EFFECTS_VERIFIED→LEARNING_DRAINED→MEMORY_SEALED→TRANSITION_RECORDED→QUALIFIED`;
  seal duplicate byte-idêntico e zero learning explícito.
- [ ] Restart em cada transição converge sem repetir efeito; F/E + AND.

## Task 18: Cancelar em oito estados, abandonar preparation e reabrir

**Files:**

- Create: `reservation_qualification/cancellation.py`
- Create: `reservation_qualification/locks.py`
- Create: `reservation_qualification/memory_preparation.py`
- Create: `reservation_qualification/reconciliation.py`
- Modify: `reservation_qualification/sqlite_store.py`
- Create: `tests/test_phase8_qualification_cancel.py`
- Create: `tests/test_phase8_memory_prepare.py`
- Create: `tests/test_phase8_memory_abandon.py`
- Create: `tests/test_phase8_memory_recovery.py`
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
- [ ] Implementar `MemoryPreparationExecutionLockFactory.acquire(operation_id,
  deadline, clock)` e `MemoryPreparationRecoveryWorker` com ports exclusivamente
  `lookup|resume_exact|ack|abandon`. Controller, recovery e canceler resolvem o mesmo
  registry DB, payload root e path/device/inode/mount do lock; mismatch falha startup.
- [ ] Memory prepare usa `PREPARING→PREPARED→ACKED`, sem transaction aberta durante
  clone/fsync.
- [ ] Abandon usa journal start→target `ABANDONING`→rename tombstone→cleanup/fsync/
  zero-scan→target `ABANDONED`→journal `ABANDONED`; `ABANDONED` com payload residual é
  manual review.
- [ ] Fault matrix cobre preparação `S0–S5` e abandono `A0–A4`, com barreiras em
  journal `ABANDONING`, target `ABANDONING`, antes/depois do rename, antes/depois de
  **cada** directory fsync, no meio do unlink recursivo, depois do zero-scan, target
  `ABANDONED` e antes/depois do journal `ABANDONED`. Restart repetido converge ao mesmo
  receipt bilateral; `ABANDONED` sempre possui zero payload.
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
- [ ] O graph literal inclui `InternalJobExecutionLockFactory`,
  `MemoryPreparationExecutionLockFactory`, `MemoryPreparationRecoveryWorker`,
  provider/follow-up/public lock factories e todos os cancelers/reconcilers que
  compartilham esses inodes.
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
- Create: `scripts/phase8_terminal_gate.py`
- Create: `scripts/run_phase8_properties.py`
- Create: `scripts/run_phase8_faults.py`
- Create: `scripts/run_phase8_restarts.py`
- Create: `scripts/run_phase8_contention.py`
- Create: `scripts/run_phase8_mutations.py`
- Modify: `phase8_release/candidate_pair.py`
- Create: `phase8_release/payload_manifest.py`
- Create: `phase8_release/source_attestation.py`
- Create: `phase8_release/build_input.py`
- Create: `phase8_release/build_authorization.py`
- Create: `phase8_release/oci_identity.py`
- Create: `phase8_release/approval_manifest.py`
- Create: `phase8_release/terminal_packet.py`
- Create: `phase8_release/validator.py`
- Create: `scripts/phase8_terminal_packet.py`
- Create: `tests/test_phase8_wheel.py`
- Create: `tests/test_phase8_wheel_reproducibility.py`
- Create: `tests/test_phase8_payload_manifest.py`
- Create: `tests/test_phase8_source_attestation.py`
- Create: `tests/test_phase8_build_input.py`
- Create: `tests/test_phase8_build_authorization.py`
- Create: `tests/test_phase8_oci_identity.py`
- Create: `tests/test_phase8_approval_manifest.py`
- Create: `tests/test_phase8_publish_gate.py`
- Create: `tests/test_phase8_terminal_gate.py`
- Create: `tests/test_phase8_terminal_packet.py`
- Create: `tests/fixtures/phase8_terminal_packet_v1.json`

**Producer do packet V, congelado antes de F:**

```python
@dataclass(frozen=True)
class TerminalPacketPublicationReceipt:
    source_f: str
    evidence_e: str
    terminal_result_sha256: str
    candidate_pair_sha256: str
    review_request_sha256: str
    packet_manifest_sha256: str  # identidade V
    sha256sums_object_sha256: str

class TerminalVerificationPacketBuilder:
    def build_and_publish(
        self,
        *,
        source_root: Path,
        source_f: str,
        evidence_root: Path,
        evidence_e: str,
        terminal_result_bytes: bytes,
        review_criteria_bytes: bytes,
        staging_root: Path,
        store: EvidenceArtifactStore,
    ) -> TerminalPacketPublicationReceipt: ...
```

O builder autentica `parent(E)==F` e a allowlist integral de E, valida JSON estrito e
schemas fechados, cria `candidate-pair.json` e `review-request.json`, e exige que
nenhum membro contenha `V` ou placeholder de V. `packet-manifest.json` lista, em ordem
canônica, somente os três objetos normativos `terminal-result.json`,
`candidate-pair.json` e `review-request.json`, cada um com `path|sha256|bytes`.

`V = SHA256(packet-manifest.json)` sobre os bytes canônicos crus. Isso coincide com o
contrato existente `objects/{expected_sha256}` do `EvidenceArtifactStore`; não há
directory publication especial. O builder publica como **objetos individuais** os
três membros e depois `packet-manifest.json`, sempre por
`store.publish(payload, expected_sha256=SHA256(payload))`. `SHA256SUMS` é derivado
depois de V, cobre os três membros + manifest, é publicado como quinto objeto
independente e não integra `packet-manifest.json` nem a preimage de V. O receipt é
escrito apenas no output privado do CLI e referencia V + hash do objeto SHA256SUMS.

O CLI `scripts/phase8_terminal_packet.py` aceita somente:

```text
--source-root --source-f --evidence-root --evidence-e --terminal-result
--review-criteria --staging-root --store-root --output-receipt
```

Ele deriva o parent/diff via Git read-only, instancia `EvidenceArtifactStore` e chama
`build_and_publish`.
Nenhum dos dois producers possui port de rede, Docker, registry, build ou runtime.

**R exato do producer V, executado ainda na Task 21:**

```bash
python3 -B -m unittest \
  tests.test_phase8_terminal_packet.TerminalPacketTests.test_good_packet_is_acyclic_and_published_as_single_objects \
  tests.test_phase8_terminal_packet.TerminalPacketTests.test_self_reference_schema_hash_or_publication_mismatch_is_rejected \
  -v
```

No primeiro run, ambos falham por ausência de `TerminalVerificationPacketBuilder` e
do CLI, nunca por import/typo. O mesmo argv precisa terminar `OK` antes do F task-local
da Task 21; fixture, P e resultados entram no U/P/S/R/O dessa task.

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
- [ ] Implementar schema canônico `BuildOnceAuthorization` com campos fechados:
  `schema=phase8-build-once-v1`, `decision=GO_BUILD_ONCE`, authorization ID/nonce,
  issued/not-before/expires-at, explicit human-approval receipt hash, source F/E,
  runtime F/E, wheel hash/bytes, combined approval-manifest hash, payload-manifest
  hash, source-attestation hash, build-input identity, context-tar hash/bytes,
  `platform=linux/arm64`, loopback registry host+port, repository, immutable-retention
  policy hash e expected child count `1`.
- [ ] `PrebuildDecision` da Task 26 aceita somente
  `GO_BUILD_ONCE_ELIGIBLE|NO_GO` e **não** é autorização operacional.
  `BuildAuthorizationStore.consume_once(...)` usa release lock e ledger externo
  `available→consuming→consumed|manual_review`; malformed, stale, future, expired,
  wrong identity/platform/destination, nonce duplicado, replay e crash incerto fazem
  zero runner call e nunca reabrem uma autorização.
- [ ] Implementar `phase8_publish_oci.py` com runner injetado; ele aceita somente os
  bytes canônicos acima, reautentica todos os inputs e consome uma vez. Registry port
  exige loopback, immutable policy e release lock; Delete, tag overwrite e garbage
  collection são proibidos durante release/rollback eligibility. Após cada operação,
  reconsulta e
  revalida index/child/config/layers. Rollback import só é elegível após igualdade
  exata do config digest e RootFS/layers da imagem live autenticada. Testes usam fake
  registry/builder ou poison runner e zero rede.
- [ ] Criar também todos os runners, `phase8_terminal_gate.py` e os testes do validator
  com pares F/E sintéticos. Os testes provam parent direto, allowlist evidence-only,
  presença pré-F de runners/catalogs/validators e P-path→S-blob→F-blob; não dependem
  do F/E real que será criado na Task 22. Counters/catalogs são literais e
  independentes do source sob teste.
- [ ] RED/GREEN dos dois selectors `TerminalPacketTests` usa fixture independente e
  store temporário real. O caso verde recompõe V, todos os objetos e o receipt; casos
  hostis cobrem unknown/duplicate key, member extra/ausente/fora de ordem,
  `review-request` contendo V, manifest contendo próprio digest/V, hash/size
  divergente, parent/diff E divergente, publish no-replace/inode mismatch e
  `SHA256SUMS` inserido indevidamente na preimage de V.
- [ ] Executar somente testes unitários focados de package/release tooling com fakes e
  poison transports; GREEN + blast radius; F/E task-local + AND.
- [ ] Stop condition: qualquer builder, validator, runner ou teste funcional ausente
  depois desta task impede congelar source F. Nunca adicioná-lo retrospectivamente em
  source E, na wheel ou na Task 26.

## Task 22: Congelar source F/E e executar o único gate pesado upstream

**Files:**

- No changes in F: functional source, tests, builders, runners e validators
- Create in E: `docs/refactor/evidence/phase-08/tasks/task-22/gate-input-manifest.json`
- Create in E: `docs/refactor/evidence/phase-08/tasks/task-22/heavy-gate-result.json`
- Create in E: `docs/refactor/evidence/phase-08/tasks/task-22/SHA256SUMS`
- Create outside Git/E under private terminal-verification staging:
  `terminal-result.json`, `candidate-pair.json`, `review-request.json`,
  `packet-manifest.json`, `SHA256SUMS`

Task 22 é um gate puro: não cria novo U/P/S/R/O, `red.patch`, selector ou producer
funcional. `gate-input-manifest.json` autentica F, argv, runner/catalog digests e
private-output roots; `heavy-gate-result.json` contém apenas exit codes, counts,
durations e hashes sanitizados. E contém somente evidência disponível antes de sua
própria identidade; nenhum arquivo em E pode nomear E ou depender de resultado obtido
depois de congelá-lo.

**Allowlist integral e exclusiva de `F→E`:**

```python
TASK_EVIDENCE_NAMES = frozenset({
    "red.patch",
    "red-provenance.json",
    "green-result.json",
    "candidate-pair.json",
    "SHA256SUMS",
})
TERMINAL_E_PATHS = frozenset(
    f"docs/refactor/evidence/phase-08/tasks/task-{task:02d}/{name}"
    for task in range(22)
    for name in TASK_EVIDENCE_NAMES
) | frozenset({
    "docs/refactor/evidence/phase-08/tasks/task-22/gate-input-manifest.json",
    "docs/refactor/evidence/phase-08/tasks/task-22/heavy-gate-result.json",
    "docs/refactor/evidence/phase-08/tasks/task-22/SHA256SUMS",
})
```

O validator calcula `actual_diff_paths` de `git diff-tree --name-only F E` e exige
literalmente `actual_diff_paths == TERMINAL_E_PATHS`. Exige ainda `parent(E)==F`,
zero rename/copy/typechange/submodule e nenhum outro path, inclusive índices, READMEs,
manifests globais ou evidence-child commits. Cada path task-00..21 precisa ser o
envelope final já aprovado daquela task; byte/hash divergente do registry de tasks é
erro, não nova evidência permitida.

Depois de E existir, o validator produz um **terminal-verification packet V**
armazenado fora de E no `EvidenceArtifactStore` privado/content-addressed. Seus
membros têm contratos acíclicos:

- `terminal-result.json`: inputs F/E, argv/validator hash e conclusão;
- `candidate-pair.json`: F, E, `parent(E)==F` e allowlist do diff;
- `review-request.json`: F, E, hashes dos dois membros anteriores e critérios das
  lanes; não contém V;
- `packet-manifest.json`: lista ordenada `(path, sha256, bytes)` dos três membros;
- `SHA256SUMS`: cobre os quatro arquivos anteriores.

`packet-manifest.json` não inclui o próprio digest nem o digest V. Seus bytes já são
canonical JSON e `V = SHA256(packet-manifest.json)` sem prefixo adicional, exatamente
como definido pelo producer da Task 21. O store publica cada membro como objeto
individual; o objeto `packet-manifest.json` fica em `objects/V`. `SHA256SUMS` é o
quinto objeto derivado, não é membro normativo e não altera V. Assim F, E e V são
identidades distintas e nenhuma é autorreferente.

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
- [ ] Criar E terminal como filho direto de F contendo **exatamente**
  `TERMINAL_E_PATHS`: os cinco envelopes finais já aprovados de cada task 00–21 e os
  três arquivos task-22. Provar igualdade de set, bytes/hashes do registry e zero
  path adicional; então congelar E definitivamente.
- [ ] Depois de criar E, executar a partir do checkout ainda fixado em F o validator
  externo já congelado na Task 21, com roots e commits explícitos:

```bash
python3 -B "$SOURCE_F_ROOT/scripts/phase8_terminal_gate.py" \
  --source-root "$SOURCE_F_ROOT" \
  --source-f "$F" \
  --evidence-root "$EVIDENCE_E_ROOT" \
  --evidence-e "$E" \
  --output "$PRIVATE_RESULT_ROOT/terminal-result.json"
```

  Esperado: exit `0`; `parent(E)==F`; diff E evidence-only; todos os runners,
  catalogs, validators e tests já presentes em F; cada P-path→S-blob→F-blob e
  envelope task-00..21 íntegro; task-22 contém somente a allowlist acima. O script
  escreve raw output apenas no private result root e nunca altera E. Falha não
  autoriza editar F/E: exige novo ciclo a partir da task owner e novo F.
- [ ] Com o resultado privado verde, construir os cinco arquivos do packet V, validar
  e publicar invocando somente o producer já congelado em F:

```bash
(
  cd "$SOURCE_F_ROOT"
  python3 -B scripts/phase8_terminal_packet.py \
    --source-root "$SOURCE_F_ROOT" \
    --source-f "$F" \
    --evidence-root "$EVIDENCE_E_ROOT" \
    --evidence-e "$E" \
    --terminal-result "$PRIVATE_RESULT_ROOT/terminal-result.json" \
    --review-criteria "$PRIVATE_INPUT_ROOT/review-criteria.json" \
    --staging-root "$PRIVATE_PACKET_STAGING_ROOT" \
    --store-root "$PRIVATE_EVIDENCE_OBJECT_STORE" \
    --output-receipt "$PRIVATE_RESULT_ROOT/packet-publication-receipt.json"
)
```

  Esperado: exit `0`; receipt autentica V, os cinco objetos e o mesmo F/E; reabrir F/E
  apenas para leitura. Não copiar membro de V para E e não amend/recommit E.
- [ ] Enviar o mesmo tuple imutável `(F,E,V)` às três lanes. Cada summary precisa autenticar
  os três digests. Timeout/ausência/Needs fixes = NO-GO.
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

- [ ] Criar `/home/ubuntu/workspace/agente-v2-phase8-runtime-candidate3` como
  **repositório Git independente** a partir do
  commit runtime Phase7 `183fb41d645e1bb04e237c986988309a28e42b34`, tree
  `e546e9d88093c09a245502bcca3d119e2e450672`, lendo a réplica sanitizada e nunca o
  runtime live. Candidate1 e o candidate2 experimental/sujo
  permanecem somente leitura e inelegíveis.
- [ ] Antes e depois, hashear `for-each-ref`, `worktree list --porcelain`, HEAD/tree e
  status do candidate1; qualquer drift falha. É proibido criar linked worktree, branch,
  ref ou checkout no repositório histórico. O comando de criação é fixo:

```bash
test ! -e /home/ubuntu/workspace/agente-v2-phase8-runtime-candidate3
git clone --no-local --no-checkout \
  /home/ubuntu/workspace/agente-v2-phase8-runtime-candidate1 \
  /home/ubuntu/workspace/agente-v2-phase8-runtime-candidate3
git -C /home/ubuntu/workspace/agente-v2-phase8-runtime-candidate3 \
  checkout --detach 183fb41d645e1bb04e237c986988309a28e42b34
test "$(git -C /home/ubuntu/workspace/agente-v2-phase8-runtime-candidate3 \
  rev-parse HEAD^{tree})" = e546e9d88093c09a245502bcca3d119e2e450672
git -C /home/ubuntu/workspace/agente-v2-phase8-runtime-candidate3 \
  switch -c phase8-operational-runtime-candidate3
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

- [ ] Hashear o wheelhouse/cache externo antes da instalação e executar o argv literal
  abaixo; produção não instala dev dependencies nem usa rede/checkout:

```bash
RT3=/home/ubuntu/workspace/agente-v2-phase8-runtime-candidate3
PROD_ROOT="$REL/runtime/prod-import"
UV_CACHE_DIR="$REL/runtime/offline-uv-cache"
UV_PROJECT_ENVIRONMENT="$PROD_ROOT/venv" \
UV_CACHE_DIR="$UV_CACHE_DIR" \
uv sync --project "$RT3" --frozen --no-dev --offline
cd "$PROD_ROOT"
"$PROD_ROOT/venv/bin/python" -I -c \
'import reservation_boundary, chapada_leads.runtime; print(
reservation_boundary.__version__,
reservation_boundary.__file__,
chapada_leads.runtime.__file__
)'
```

  Esperado: `0.8.0`; ambos os `__file__` resolvem no venv/runtime F autenticado, nunca
  no source checkout; importar a factory não inicia app, provider ou worker.
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
- [ ] Produzir somente `PrebuildDecision(decision=
  GO_BUILD_ONCE_ELIGIBLE|NO_GO, ...)`. `GO_BUILD_ONCE_ELIGIBLE` não é aceito pelo
  publisher e não executa build. Carlos precisa autorizar um novo runbook; esse gate
  posterior cria `BuildOnceAuthorization(decision=GO_BUILD_ONCE, ...)` com
  registry/repository/retention, nonce e expiração exatos, fora do Git.

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
- dez identidades históricas/substituídas preservadas no manifesto, incluindo o entry
  test exigido pela spec;
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
