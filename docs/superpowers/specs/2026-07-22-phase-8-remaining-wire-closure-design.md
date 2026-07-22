# Fase 8 — Closure literal do wire restante da Task 1

**Status:** Proposed implementation refinement; non-executable until authenticated review and explicit human acceptance of its commit identity

**Date:** 2026-07-22

**Architectural authority:** `docs/superpowers/specs/2026-07-21-phase-8-operational-boundary-correction-design.md` at commit `2889e9ec08f466bbb16a30e4bb5c9a098daf54d3`

**Existing facts/reads closure:** `docs/superpowers/specs/2026-07-22-phase-8-facts-reads-wire-closure-design.md` at commit `6f638234a200a72178dac66705d739a4b597048f`

**Purpose:** Close only the field order, exact scalar/enum/nullability shapes, canonical codecs and hash domains still missing from Task 1. This document does not add an effect, capability, owner, table, state-machine state, allocation, gate or rollout authority.

---

## 1. Authority and stop conditions

This delta is a literal implementation refinement of the approved architecture. The approved architectural spec wins on every conflict. A conflict is a stop condition requiring a new architectural delta; it is never resolved by broadening a field or silently accepting another variant.

The contracts in this delta remain non-executable until all of the following refer to the same immutable candidate:

1. the companion fixture is byte-authenticated;
2. a focused AND review closes technical, gate and authority concerns;
3. Carlos Eduardo explicitly accepts the resulting commit identity;
4. the active plan and quarantine manifest record that identity without changing build, canary, conversation, E2E or rollout gates.

Implementation remains micro-unit RED/GREEN. A unit may implement only contracts already literalized in an accepted identity. Build, Docker, network, provider calls, delivery and live runtime mutations remain forbidden.

## 2. Global canonical encoding

Every contract uses canonical UTF-8 JSON:

- top-level object has exactly `schema`, `version`, `data`;
- keys are sorted lexicographically, separators are `,` and `:`, and no insignificant whitespace is emitted;
- duplicate keys, unknown keys, missing keys, floats, NaN/Infinity and bool-as-int are rejected;
- strings are Unicode NFKC and reject leading/trailing whitespace unless the field is explicitly canonical prose;
- `bytes` are RFC 4648 base64 with padding and strict round-trip re-encoding;
- datetimes are exact UTC and encode as `YYYY-MM-DDTHH:MM:SS.ffffffZ`;
- dates encode as `YYYY-MM-DD`;
- tuples encode as JSON arrays and are never accepted from mutable caller collections;
- a nested contract is embedded as its full decoded canonical envelope, never only as an untyped `data` object;
- all SHA-256 values are lowercase 64-hex; prefixed IDs use the exact prefix declared by the registry;
- nullable fields are JSON `null`; absence and null are never equivalent;
- canonical decode always re-encodes and demands byte identity.

Unless a contract defines a special artifact preimage, its hash is:

```text
SHA256(DOMAIN_ASCII || 0x00 || canonical_contract_bytes)
```

No secret, raw provider payload, raw user message, phone, e-mail, external token, credential, HMAC key, concrete filesystem path or reversible identifier may enter these public wire contracts.

## 3. Closed scalar vocabulary

The companion fixture owns these reusable scalar aliases:

```text
SHA256       = ^[0-9a-f]{64}$
OCI_DIGEST   = ^sha256:[0-9a-f]{64}$
UTC          = canonical UTC timestamp above
NONEMPTY     = exact str, NFKC, 1..256 bytes, no surrounding whitespace
ID_TOKEN     = ^[a-z0-9][a-z0-9._:-]{0,127}$
COUNT        = exact int >= 0, bool rejected
POSITIVE     = exact int >= 1, bool rejected
ORDINAL      = exact int >= 0, bool rejected
CANON_BYTES  = non-empty exact bytes whose contract-specific decoder re-encodes identically
```

A registry field cannot use `object`, free-form mapping, untagged union, arbitrary JSON or open string. Closed maps are represented as ordered tuples of typed rows.

## 4. Existing contracts not redefined

This delta does not redefine the already authenticated facts/reads registry or the implemented literals below:

- `SourceEventIdentity`, `ConversationProjection`, `ReservationExecutionProjection`;
- `MayaTurnRequest`, `MayaIntentClosure`, `MayaTurnClosure`, `TranscriptCommitment`;
- `TypedFact`, all accepted read requests/results/evidence and `ReadObservation`;
- `ReservationRelayBundle`, `SettlementRelayBundle`;
- `BehaviorStateSnapshot`, `ScenarioTerminalVerificationReceipt`.

Their accepted bytes/domains remain authoritative and collision tests must prove that no contract introduced here decodes under an existing domain.

## 5. Registry families and implementation placement

The literal registry and known-answer examples are stored in the companion fixture under three disjoint families:

1. `conversation_turn` — proposal, normalization, learning, reply chunks, turn receipt and capability policy;
2. `effects_receipts` — handoff/internal jobs, target/source ACKs, delivery, owner outcomes and exact allocations;
3. `qualification_gates` — E2E contracts/bindings, qualification lifecycle, memory preparation/reopen and finite conversation authorization.

Each registry entry contains exactly:

```text
name, fields, enums, schema, version, domain, hash_kind,
nullable_sets, invariants, known_answer
```

The `fields` array is ordered and each member is exactly `{name, type}`. `known_answer` contains `canonical_utf8` and its domain-separated `canonical_hash`; artifact-preimage contracts additionally contain `preimage_utf8` and `artifact_hash`.

Acceptance freezes every registry entry now, but implementation follows owner availability:

| Contract family | Implementation task |
|---|---:|
| common conversation values, facts/reads and `CapabilityPolicy` | Task 1 |
| `NormalizedToolProposal` | Task 2 |
| target operation/allocation installation receipts | Task 4 |
| `MayaTurnProposal` | Task 10 |
| `TurnReceipt`, `AdmissionAbortReceipt` and atomic boundary artifacts | Task 11 |
| command-relay receipts | Task 13 |
| handoff/learning internal-job contracts and receipts | Task 14 |
| provider/follow-up outcome receipts | Task 15 |
| public chunk/allocation/delivery receipts and finite conversation authorization | Task 16 |
| E2E contract, admission, effect-scan, transition and sealed bindings | Task 17 |
| cancellation, memory-preparation and reopen receipts | Task 18 |
| release/runtime `CapabilityPolicy` composition and graph binding | Task 19 |
| complete cross-family wire fixture and collision catalog | Task 21 |

This placement changes no dependency direction: a task must implement its accepted wire before its store or owner can persist it, and its focused RED must fail specifically because that contract is absent or non-conforming. No later task may change an accepted field, enum, schema or domain. The Task 1 closeout therefore requires the complete authenticated registry and fixture, but only the common Task 1 implementations; it does not create speculative owner-specific classes before their owner task. The final Task 21 composition proves that every registry entry has exactly one implementation before source F is frozen.

## 6. Facts/reads owner-acceptance bridge

This section corrects one literal mismatch without changing the facts/reads public union. The accepted facts/reads spec says the read owner atomically persists both the receipt and the owner evidence used by the projection, but its port returned only receipt bytes. The exact private verification record is therefore:

```text
ReadEvidenceRecord(
    receipt_bytes: CANON_BYTES(ReadEvidenceReceipt),
    source_evidence_bytes: CANON_BYTES,
)

SCHEMA = phase8-read-evidence-record
VERSION = 1
DOMAIN = phase8-read-evidence-record-v1
```

Both fields are standard-base64 in the envelope and are always non-null. The owner persists this record atomically with the classified read result. The capability-free lookup becomes exactly:

```text
ReadEvidenceStore.get(receipt_id: READ_EVIDENCE_ID)
  -> canonical_read_evidence_record_bytes | NotFound
```

It never receives providers, credentials, network, delivery or mutation ports. Acceptance strict-decodes the record and receipt, demands byte-identical re-encoding and supplied-receipt equality, then recomputes:

```text
source_evidence_hash =
  SHA256("phase8-read-source-evidence-v1" || 0x00 || source_evidence_bytes)
```

`source_evidence_bytes` is private owner material and never crosses into `ReadObservation`, reply text, public chunks, transcript commitments or Git evidence. Its adapter-specific strict decoder belongs to the owner that produced the record; unknown owner-evidence schema fails closed.

The parent has exactly one acceptance operation:

```text
accept_read_observation(
    observation: ReadObservation,
    *,
    aggregate_turn_id: ID_TOKEN,
    lead_key_hash: SHA256,
    source_event: SourceEventIdentity,
    projection_hash: SHA256,
    locale: LOCALE,
    frame_commitment_hash: SHA256,
    now: UTC,
    genesis_store: LegacyGenesisEvidenceStore,
    read_store: ReadEvidenceStore,
) -> ReadObservation
```

The operation is pure except for capability-free exact lookups. It returns the same immutable observation only after all checks pass:

1. strict request/result decode and byte-identical re-encode;
2. request hash, result hash and request/result equality matrix;
3. exact aggregate turn, lead, source event, projection and locale binding;
4. `now < request.deadline_at`, `now < result.evidence_receipt.expires_at` when the result has read evidence, and exact frame backlink;
5. exact owner-store lookup by receipt ID and all owner evidence/hash bindings;
6. genesis owner record verification for genesis variants;
7. facts catalog/order/backlinks and recomputed public-safety matrix;
8. no unresolved read, caller-selected safety, fabricated receipt or unknown owner schema.

`SanitizedOffer` and `SanitizedLookupResult` remain projections, not authorities. The read adapter owner constructs them only from the corresponding typed owner `OfferSnapshot`/`LookupResult`, derives offer IDs before provider references are removed, persists `ReadEvidenceRecord`, and returns the projection plus receipt. No public factory accepts caller-supplied `offer_id`, `lookup_id`, owner hash or disposition.

## 7. Composition fixture

The companion fixture top level is exactly:

```text
schema, version, encoding, scalar_aliases, enums, families,
owner_acceptance, known_answer_catalog
```

It contains every contract introduced by this delta exactly once, all closed enums, one valid known answer per contract and targeted invalid cases for each nullable/status matrix. Registry order is semantic and fixed; JSON object key order remains canonical lexicographic order. Missing or extra contract, field, enum, domain or example fails the Task 1 wire test.

The final `tests/fixtures/phase8_wire_contract_v8.json` is composed only after this delta is accepted. It embeds authenticated catalogs from:

- the accepted facts/reads fixture;
- the accepted registry fixture from this document;
- the already implemented conversation/effects/qualification contracts named in section 4.

Composition does not alter either source fixture and rejects duplicate schema/domain names.

## 8. Conversation and turn closure

The companion fixture is authoritative for the following exact field order, enums and known-answer bytes. Helper row shapes used below are closed inline:

```text
CommittedRowRef(row_id: ID_TOKEN, row_hash: SHA256)
CommittedArtifactRef(row_id: ID_TOKEN, canonical_bytes: CANON_BYTES, artifact_hash: SHA256)
CommittedPublicChunk(row_id: ID_TOKEN, ordinal: ORDINAL, canonical_bytes: CANON_BYTES(PublicReplyChunk), artifact_hash: SHA256)
CapabilityGrantRow(capability: Capability, disposition: CapabilityDisposition)
WorkerModeRow(worker: Worker, mode: WorkerMode)
```

### 8.1 `NormalizedToolProposal`

```text
NormalizedToolProposal(
    aggregate_turn_id: ID_TOKEN,
    request_id: ID_TOKEN,
    sequence: ORDINAL,
    tool_name: NormalizedCommandTool,
    arguments_type: NormalizedCommandArgumentsType,
    typed_arguments_json: CANON_BYTES(NormalizedCommandArgumentsPayload),
    request_hash: SHA256,
    frame_commitment_hash: SHA256,
)

SCHEMA = phase8-normalized-tool-proposal
VERSION = 1
DOMAIN = phase8-normalized-tool-proposal-v1
HASH_KIND = domain_hash
```

Closed enum references: `NormalizedCommandTool`, `NormalizedCommandArgumentsType`.

Invariants:
- constructed only by parent ToolDispatch.normalize_proposal.
- tool_name and arguments_type are a closed exact pair.
- typed_arguments_json decodes under that exact type with no unknown fields.
- request/frame/turn/sequence equal the accepted COMMAND transcript frame.
- contains no command authorization capability provider payload or secret.
- blocked/unmigrated read state-commit and alias names are rejected.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 8.2 `LearningProposal`

```text
LearningProposal(
    aggregate_turn_id: ID_TOKEN,
    request_id: ID_TOKEN,
    sequence: ORDINAL,
    claim: TypedFact,
    request_hash: SHA256,
    frame_commitment_hash: SHA256,
)

SCHEMA = phase8-learning-proposal
VERSION = 1
DOMAIN = phase8-learning-proposal-v1
HASH_KIND = domain_hash
```

Invariants:
- constructed by the parent after a LEARNING frame is validated.
- claim is one complete v8 TypedFact envelope with the same frame backlink.
- claim names/value variants remain the accepted closed TypedFact catalog.
- contains no raw text PII provider payload memory capability receipt or secret.
- no memory write occurs in-turn; any job is authorized and persisted only by the kernel/commit owner.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 8.3 `PublicReplyChunk`

```text
PublicReplyChunk(
    aggregate_turn_id: ID_TOKEN,
    ordinal: ORDINAL,
    text: PUBLIC_TEXT,
    source_closure_hash: SHA256,
)

SCHEMA = phase8-public-reply-chunk
VERSION = 1
DOMAIN = phase8-public-reply-chunk-v1
HASH_KIND = domain_hash
```

Invariants:
- constructed only by the deterministic parent splitter/guard.
- text is nonempty public-safe UTF-8 and contains no raw inbound/provider payload prompt capability credential secret or personal identifier.
- source_closure_hash resolves the exact accepted closure.
- ordinals in one proposal are unique and contiguous from zero.
- delivery uses the persisted UTF-8 bytes without regeneration translation concatenation or splitting.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 8.4 `MayaTurnProposal`

```text
MayaTurnProposal(
    aggregate_turn_id: ID_TOKEN,
    intent_closure: MayaIntentClosure,
    read_observations: tuple[ReadObservation,...],
    facts: tuple[TypedFact,...],
    normalized_tool_proposals: tuple[NormalizedToolProposal,...],
    learning_proposals: tuple[LearningProposal,...],
    public_reply_chunks: tuple[PublicReplyChunk,...],
    maya_turn_closure_hash: SHA256,
    final_transcript_commitment_hash: SHA256,
    final_seq: POSITIVE,
    final_transcript_mac: SHA256,
    runtime_graph_digest: SHA256,
    route: PublicRoute,
    reply_type: PublicReplyType,
)

SCHEMA = phase8-maya-turn-proposal
VERSION = 1
DOMAIN = phase8-maya-turn-proposal-v1
HASH_KIND = domain_hash
```

Closed enum references: `PublicRoute`, `PublicReplyType`.

Invariants:
- constructed only by the parent from the accepted child closure and parent-owned transcript artifacts.
- closure fields aggregate_turn_id route reply_type final_seq and hash bindings are equal.
- all child artifacts are ordered by transcript frame and have unique IDs/backlinks.
- public chunks have same turn and closure hash with contiguous ordinals.
- no_reply iff route/reply_type are no_reply and chunks are empty; handoff route iff handoff reply type.
- final transcript commitment is recomputable without the HMAC key; the MAC is opaque live proof only.
- contains no legacy ConversationIntent prompt raw payload capability token key credential or secret.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 8.5 `TurnReceipt`

```text
TurnReceipt(
    aggregate_turn_id: ID_TOKEN,
    event_hash: SHA256,
    source_events: nonempty tuple[SourceEventIdentity,...],
    maya_proposal_hash: SHA256,
    kernel_decision_hash: SHA256,
    read_observations: tuple[CommittedArtifactRef,...],
    committed_state_version: POSITIVE,
    committed_state_hash: SHA256,
    public_chunks: tuple[CommittedPublicChunk,...],
    command_rows: tuple[CommittedRowRef,...],
    relay_rows: tuple[CommittedRowRef,...],
    internal_outbox_rows: tuple[CommittedRowRef,...],
    uds_transcript_mac: SHA256,
    uds_final_seq: POSITIVE,
    structural_graph_digest: SHA256,
    capability_policy_digest: SHA256,
    effective_stage_binding_digest: SHA256,
    behavior_state_snapshot_digest: SHA256,
    qualification_id: ID_TOKEN|null,
    admission_sequence: POSITIVE|null,
    admission_revision: POSITIVE|null,
    commit_fence_token: POSITIVE|null,
    allocation_manifest_hash: SHA256|null,
    immutable_generation: POSITIVE|null,
    allocation_ids: nonempty tuple[ID_TOKEN,...]|null,
    committed_at: UTC,
    previous_turn_receipt_hash: SHA256|null,
    artifact_hash: SHA256,
)

SCHEMA = phase8-turn-receipt
VERSION = 1
DOMAIN = phase8-turn-receipt-v1
HASH_KIND = artifact_preimage
PREIMAGE_SCHEMA = phase8-turn-receipt-artifact-preimage
```

Nullable matrices: `[["qualification_id","admission_sequence","admission_revision","commit_fence_token","allocation_manifest_hash","immutable_generation","allocation_ids"],["previous_turn_receipt_hash"]]`.

Invariants:
- artifact_hash is derived from the preimage excluding artifact_hash; previous_turn_receipt_hash remains inside the preimage.
- source events are nonempty ordered and source_event_id-unique.
- read observations include owner row id exact canonical bytes and domain hash.
- public chunk rows include row id ordinal exact canonical bytes and domain hash; ordinals are contiguous from zero.
- command relay and internal rows prove only atomic row persistence, never target ACK provider outcome or delivery.
- qualification/admission/allocation fields are all-null or all-present and allocation IDs are unique.
- duplicate event/turn returns byte-identical receipt and creates no rows.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 8.6 `CapabilityPolicy`

```text
CapabilityPolicy(
    capability_matrix: tuple[CapabilityGrantRow,...],
    worker_modes: tuple[WorkerModeRow,...],
    guard_semantics: tuple[GuardSemantic,...],
)

SCHEMA = phase8-capability-policy
VERSION = 1
DOMAIN = phase8-capability-policy-v1
HASH_KIND = domain_hash
```

Closed enum references: `Capability`, `CapabilityDisposition`, `Worker`, `WorkerMode`, `GuardSemantic`.

Invariants:
- capability_matrix has exactly one row per Capability in enum order.
- worker_modes has exactly one row per Worker in enum order.
- guard_semantics contains each closed semantic exactly once in enum order.
- provider write followup delivery public delivery and learning write are independent.
- contains no stage root path concrete allowlist cardinality percentage traffic split secret or credential.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 8.7 Closed enum values

`Capability` is exactly:

```text
legacy_read | maya_inference | provider_read | turn_commit | relay_enqueue | provider_write | followup_delivery | public_delivery | learning_write
```

`CapabilityDisposition` is exactly:

```text
denied | read_only | propose_only | execute
```

`GuardSemantic` is exactly:

```text
fail_closed | deadline_bounded | idempotency_required | lease_fenced | owner_checked
```

`NormalizedCommandArgumentsType` is exactly:

```text
lodging_reservation | activity_reservation | lodging_payment | activity_payment
```

`NormalizedCommandTool` is exactly:

```text
cloudbeds_criar_reserva_v2 | bokun_agendar_passeio_v2 | cloudbeds_lancar_pagamento_confirmar_reserva | bokun_lancar_pagamento_confirmar_reserva
```

`PublicReplyType` is exactly:

```text
ask_more | qualify | answer | handoff | no_reply
```

`PublicRoute` is exactly:

```text
recepcionista | hostel | agencia | fechamento | handoff | no_reply
```

`Worker` is exactly:

```text
turn_coordinator | command_relay_worker | internal_job_worker | provider_effect_worker | followup_delivery_worker | public_delivery_worker | learning_worker | reconciliation_worker | qualification_controller
```

`WorkerMode` is exactly:

```text
disabled | shadow | active
```

## 9. Qualification and gate closure

These contracts close future code paths but do not open any operational gate. Human conversation, E2E, rollout and closeout remain separate decisions. Helper rows are closed inline:

```text
DeterministicTurnIdentity(aggregate_turn_id: ID_TOKEN, source_event_ids: nonempty tuple[ID_TOKEN,...], source_event_hashes: nonempty tuple[SHA256,...])
KindCount(kind: closed family enum, count: COUNT)
```

Qualification and admission states are distinct. Every transition is full-tuple CAS with a canonical receipt. The only run chain is `installing -> open -> qualifying -> effects_verified -> learning_drained -> memory_sealed -> transition_recorded -> qualified`; cancellation uses `frozen -> cancelled|manual_review`. Admission is only `installing -> open -> qualifying`, or `frozen -> cancelled|manual_review`.

### 9.1 `E2EScenarioContract`

```text
E2EScenarioContract(
    scenario_id: ID_TOKEN,
    turn_identities: nonempty tuple[DeterministicTurnIdentity,...],
    lead_key_hash: SHA256,
    target_hash: SHA256,
    channel_hash: SHA256,
    allowlist_digest: SHA256,
    provider_scopes: nonempty tuple[ProviderScope,...],
    workflow_scopes: nonempty tuple[WorkflowScope,...],
    effect_scopes: nonempty tuple[EffectScope,...],
    window_start: UTC,
    window_end: UTC,
    expected_command_counts: tuple[KindCount[ExpectedCommandKind],...],
    expected_relay_counts: tuple[KindCount[ExpectedRelayKind],...],
    expected_target_ingress_counts: tuple[KindCount[TargetIngressKind],...],
    expected_provider_outcome_counts: tuple[KindCount[ProviderOutcomeKind],...],
    expected_followup_delivery_counts: tuple[KindCount[FollowupDeliveryKind],...],
    expected_public_chunk_count: COUNT,
    expected_public_delivery_count: COUNT,
    expected_compensation_count: COUNT,
    expected_cancellation_receipt_count: COUNT,
    expected_final_state_hash: SHA256,
    expected_final_economic_hash: SHA256,
    external_effect_budget: POSITIVE,
)

SCHEMA = phase8-e2e-scenario-contract
VERSION = 1
DOMAIN = phase8-e2e-scenario-contract-v1
HASH_KIND = domain_hash
```

Closed enum references: `ProviderScope`, `WorkflowScope`, `EffectScope`, `ExpectedCommandKind`, `ExpectedRelayKind`, `TargetIngressKind`, `ProviderOutcomeKind`, `FollowupDeliveryKind`.

Invariants:
- turn identities and all kind-count rows are ordered and key-unique.
- window_end is later than window_start.
- external_effect_budget equals the exact sum of terminal external calls/deliveries expected by the scenario.
- the scenario contains at least one provider outcome or is rejected by its parent contract.
- provider/workflow/effect scopes are subsets of the parent qualification contract.
- all target channel and lead identities are hashes; no raw contact appears.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.2 `E2EQualificationContract`

```text
E2EQualificationContract(
    release_child_manifest_digest: OCI_DIGEST,
    runtime_graph_digest: SHA256,
    capability_policy_digest: SHA256,
    behavior_state_snapshot_digest_at_admission: SHA256,
    provider_scopes: nonempty tuple[ProviderScope,...],
    workflow_scopes: nonempty tuple[WorkflowScope,...],
    effect_scopes: nonempty tuple[EffectScope,...],
    allowlist_digest: SHA256,
    allowlist_cardinality: POSITIVE,
    traffic_stage: TrafficStage,
    state_root_class: StateRootClass,
    instance_constraints: nonempty tuple[ID_TOKEN,...],
    admission_epoch: POSITIVE,
    scenarios: nonempty tuple[E2EScenarioContract,...],
)

SCHEMA = phase8-e2e-qualification-contract
VERSION = 1
DOMAIN = phase8-e2e-qualification-contract-v1
HASH_KIND = domain_hash
```

Closed enum references: `ProviderScope`, `WorkflowScope`, `EffectScope`, `TrafficStage`, `StateRootClass`.

Invariants:
- traffic_stage is canary_e2e and state_root_class is ephemeral_canary.
- scenario IDs are unique and scenarios are ordered by scenario_id.
- at least one expected provider outcome and one public delivery exist across scenarios.
- global external budget equals the exact sum of scenario budgets and cannot be zero.
- qualification_id is derived after hashing this contract from contract hash release graph policy and admission epoch.
- no path secret raw target or mutable tag is present.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.3 `E2EEffectAuthorizationBinding`

```text
E2EEffectAuthorizationBinding(
    release_child_manifest_digest: OCI_DIGEST,
    runtime_graph_digest: SHA256,
    capability_policy_digest: SHA256,
    runtime_role: RuntimeRole,
    provider_scopes: nonempty tuple[ProviderScope,...],
    workflow_scopes: nonempty tuple[WorkflowScope,...],
    effect_scopes: nonempty tuple[EffectScope,...],
    qualification_id: ID_TOKEN,
    qualification_contract_hash: SHA256,
    allowlist_digest: SHA256,
    allowlist_cardinality: POSITIVE,
    traffic_stage: TrafficStage,
    state_root_class: StateRootClass,
    instance_constraints: nonempty tuple[ID_TOKEN,...],
    admission_epoch: POSITIVE,
)

SCHEMA = phase8-e2e-effect-authorization-binding
VERSION = 1
DOMAIN = phase8-e2e-effect-authorization-binding-v1
HASH_KIND = domain_hash
```

Closed enum references: `RuntimeRole`, `ProviderScope`, `WorkflowScope`, `EffectScope`, `TrafficStage`, `StateRootClass`.

Invariants:
- runtime_role is canary_e2e traffic_stage is canary_e2e and root class is ephemeral_canary.
- all stable fields equal the qualification contract.
- behavior snapshot is deliberately absent.
- allocation manifests reference only this stable binding hash.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.4 `EffectiveE2EDeploymentBinding`

```text
EffectiveE2EDeploymentBinding(
    release_child_manifest_digest: OCI_DIGEST,
    runtime_graph_digest: SHA256,
    capability_policy_digest: SHA256,
    behavior_state_snapshot_digest: SHA256,
    runtime_role: RuntimeRole,
    provider_scopes: nonempty tuple[ProviderScope,...],
    workflow_scopes: nonempty tuple[WorkflowScope,...],
    effect_scopes: nonempty tuple[EffectScope,...],
    qualification_id: ID_TOKEN,
    qualification_contract_hash: SHA256,
    allowlist_digest: SHA256,
    allowlist_cardinality: POSITIVE,
    traffic_stage: TrafficStage,
    state_root_class: StateRootClass,
    instance_id: ID_TOKEN,
    admission_epoch: POSITIVE,
)

SCHEMA = phase8-effective-e2e-deployment-binding
VERSION = 1
DOMAIN = phase8-effective-e2e-deployment-binding-v1
HASH_KIND = domain_hash
```

Closed enum references: `RuntimeRole`, `ProviderScope`, `WorkflowScope`, `EffectScope`, `TrafficStage`, `StateRootClass`.

Invariants:
- all stable fields project exactly the E2EEffectAuthorizationBinding.
- only behavior_state_snapshot_digest may advance and only through a valid LearningReceipt.
- runtime_role traffic stage and root class are canary_e2e canary_e2e ephemeral_canary.
- instance_id satisfies the authorization binding constraints.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.5 `LearningClaimsClosedReceipt`

```text
LearningClaimsClosedReceipt(
    operation_id: ID_TOKEN,
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    cutoff_sequence: COUNT,
    admitted_set_hash: SHA256,
    closed_claim_count: COUNT,
    closed_claim_aggregate_hash: SHA256,
    completed_at: UTC,
    previous_qualification_artifact_hash: SHA256|null,
)

SCHEMA = phase8-learning-claims-closed-receipt
VERSION = 1
DOMAIN = phase8-learning-claims-closed-receipt-v1
HASH_KIND = domain_hash
```

Nullable/status matrices: `[["previous_qualification_artifact_hash"]]`.

Invariants:
- operation_id is stable for qualification and duplicate returns byte-identical bytes.
- the memory authority closes normal claims before effects scan.
- zero claims is count zero plus the canonical empty aggregate hash, never omission.
- the journal persists this owner receipt before effects verification.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.6 `BehaviorTransitionReceipt`

```text
BehaviorTransitionReceipt(
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    admitted_set_hash: SHA256,
    learning_claims_closed_receipt_hash: SHA256,
    learning_receipt_aggregate_hash: SHA256,
    before_behavior_snapshot_digest: SHA256,
    sealed_behavior_snapshot_digest: SHA256,
    memory_seal_receipt_hash: SHA256,
    recorded_at: UTC,
    previous_qualification_artifact_hash: SHA256,
)

SCHEMA = phase8-behavior-transition-receipt
VERSION = 1
DOMAIN = phase8-behavior-transition-receipt-v1
HASH_KIND = domain_hash
```

Invariants:
- constructed only after EFFECTS_VERIFIED LEARNING_DRAINED and MEMORY_SEALED.
- zero learning uses the canonical empty aggregate and before equals sealed digest.
- nonzero learning advances only through owner LearningReceipts included in the aggregate.
- duplicate transition bytes are deterministic and journal-CAS bound.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.7 `SealedCanaryQualificationBinding`

```text
SealedCanaryQualificationBinding(
    release_child_manifest_digest: OCI_DIGEST,
    runtime_graph_digest: SHA256,
    capability_policy_digest: SHA256,
    provider_scopes: nonempty tuple[ProviderScope,...],
    workflow_scopes: nonempty tuple[WorkflowScope,...],
    effect_scopes: nonempty tuple[EffectScope,...],
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    cutoff_sequence: COUNT,
    admitted_set_hash: SHA256,
    qualification_contract_hash: SHA256,
    scenario_count: POSITIVE,
    allocation_manifest_hash: SHA256,
    immutable_generation_aggregate_hash: SHA256,
    allocation_installation_receipt_aggregate_hash: SHA256,
    terminal_allocation_ledger_aggregate_hash: SHA256,
    scenario_terminal_receipt_aggregate_hash: SHA256,
    effective_e2e_binding_aggregate_hash: SHA256,
    sealed_behavior_snapshot_digest: SHA256,
    behavior_transition_receipt_hash: SHA256,
    state_root_class: StateRootClass,
    release_image_attestation_hash: SHA256,
    container_binding_attestation_hash: SHA256,
)

SCHEMA = phase8-sealed-canary-qualification-binding
VERSION = 1
DOMAIN = phase8-sealed-canary-qualification-binding-v1
HASH_KIND = domain_hash
```

Closed enum references: `ProviderScope`, `WorkflowScope`, `EffectScope`, `StateRootClass`.

Invariants:
- created only after all qualification transition receipts and exact bilateral scans are terminal.
- state_root_class is ephemeral_canary and scenario_count equals the nonempty contract.
- aggregates are recomputed from ordered owner receipts and rows, never caller-supplied evidence.
- does not pretend to be the effective turn binding.
- same immutable release child digest and scopes are preserved.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.8 `RolloutAuthorization`

```text
RolloutAuthorization(
    authorization_id: ID_TOKEN,
    sealed_qualification_binding_hash: SHA256,
    target_role: RuntimeRole,
    release_child_manifest_digest: OCI_DIGEST,
    runtime_graph_digest: SHA256,
    capability_policy_digest: SHA256,
    sealed_behavior_snapshot_digest: SHA256,
    behavior_transition_receipt_hash: SHA256,
    provider_scopes: nonempty tuple[ProviderScope,...],
    workflow_scopes: nonempty tuple[WorkflowScope,...],
    effect_scopes: nonempty tuple[EffectScope,...],
    allowlist_digest: SHA256,
    allowlist_cardinality: POSITIVE,
    traffic_stage: TrafficStage,
    state_root_class: StateRootClass,
    instance_constraints: nonempty tuple[ID_TOKEN,...],
    not_before: UTC,
    expires_at: UTC,
    approver_identity_hash: SHA256,
    authorization_request_hash: SHA256,
)

SCHEMA = phase8-rollout-authorization
VERSION = 1
DOMAIN = phase8-rollout-authorization-v1
HASH_KIND = domain_hash
```

Closed enum references: `RuntimeRole`, `ProviderScope`, `WorkflowScope`, `EffectScope`, `TrafficStage`, `StateRootClass`.

Invariants:
- created in the qualification journal only from QUALIFIED by full-tuple CAS against cancellation.
- target role root class and traffic stage are production_initial persistent_production rollout_initial.
- release graph policy behavior transition scopes and sealed binding match exactly.
- window is finite and current; approver identity is hashed.
- this authorization is separate from build conversation E2E and closeout decisions.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.9 `ProductionInitialDeploymentBinding`

```text
ProductionInitialDeploymentBinding(
    sealed_qualification_binding_hash: SHA256,
    rollout_authorization_hash: SHA256,
    release_child_manifest_digest: OCI_DIGEST,
    runtime_graph_digest: SHA256,
    capability_policy_digest: SHA256,
    sealed_behavior_snapshot_digest: SHA256,
    behavior_transition_receipt_hash: SHA256,
    provider_scopes: nonempty tuple[ProviderScope,...],
    workflow_scopes: nonempty tuple[WorkflowScope,...],
    effect_scopes: nonempty tuple[EffectScope,...],
    runtime_role: RuntimeRole,
    allowlist_digest: SHA256,
    allowlist_cardinality: POSITIVE,
    traffic_stage: TrafficStage,
    state_root_class: StateRootClass,
    instance_id: ID_TOKEN,
    cloned_memory_snapshot_digest: SHA256,
)

SCHEMA = phase8-production-initial-deployment-binding
VERSION = 1
DOMAIN = phase8-production-initial-deployment-binding-v1
HASH_KIND = domain_hash
```

Closed enum references: `RuntimeRole`, `ProviderScope`, `WorkflowScope`, `EffectScope`, `TrafficStage`, `StateRootClass`.

Invariants:
- derived only by derive_production_initial_binding from the sealed qualification and rollout authorization.
- role transition is sealed_canary_qualification to production_initial and root transition is ephemeral_canary to persistent_production.
- all release graph policy behavior transition scope allowlist and stage values are equal.
- cloned memory snapshot is byte-identical to the sealed snapshot and instance satisfies constraints.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.10 `QualificationCancelStartReceipt`

```text
QualificationCancelStartReceipt(
    cancel_operation_id: ID_TOKEN,
    request_hash: SHA256,
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    origin_run_status: QualificationRunStatus,
    origin_run_revision: POSITIVE,
    origin_admission_status: AdmissionState,
    origin_admission_revision: POSITIVE,
    cutoff_sequence: COUNT|null,
    admitted_set_hash: SHA256|null,
    memory_seal_receipt_hash: SHA256|null,
    behavior_transition_receipt_hash: SHA256|null,
    sealed_qualification_binding_hash: SHA256|null,
    run_frozen_revision: POSITIVE,
    admission_frozen_revision: POSITIVE,
    started_at: UTC,
    previous_qualification_artifact_hash: SHA256|null,
)

SCHEMA = phase8-qualification-cancel-start-receipt
VERSION = 1
DOMAIN = phase8-qualification-cancel-start-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `QualificationRunStatus`, `AdmissionState`.

Nullable/status matrices: `[["cutoff_sequence","admitted_set_hash"],["memory_seal_receipt_hash"],["behavior_transition_receipt_hash"],["sealed_qualification_binding_hash"],["previous_qualification_artifact_hash"]]`.

Invariants:
- same transaction persists this receipt and freezes run plus admission.
- origin status is one of installing through qualified, never frozen cancelled or manual_review.
- terminal artifact hashes are preserved according to the origin status and are never deleted.
- duplicate operation/request returns identical bytes; divergent request fails.
- eligible rollout authorization/deployment blocks this cancellation path.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.11 `QualificationCancelReceipt`

```text
QualificationCancelReceipt(
    cancel_operation_id: ID_TOKEN,
    cancel_start_receipt_hash: SHA256,
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    origin_run_status: QualificationRunStatus,
    origin_run_revision: POSITIVE,
    origin_admission_status: AdmissionState,
    origin_admission_revision: POSITIVE,
    terminal_run_revision: POSITIVE,
    terminal_admission_revision: POSITIVE,
    active_count: COUNT,
    terminal_membership_aggregate_hash: SHA256,
    admitted_set_terminal_hash: SHA256,
    allocation_closure_aggregate_hash: SHA256,
    internal_job_closure_aggregate_hash: SHA256,
    provider_followup_public_closure_aggregate_hash: SHA256,
    completed_at: UTC,
    previous_qualification_artifact_hash: SHA256,
)

SCHEMA = phase8-qualification-cancel-receipt
VERSION = 1
DOMAIN = phase8-qualification-cancel-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `QualificationRunStatus`, `AdmissionState`.

Invariants:
- active_count is exactly zero and all memberships are aborted or turn_receipt_committed.
- all allocation internal provider followup and public closure receipts are owner-verified and terminal.
- run and admission move together from frozen to cancelled in one transaction.
- manual review or uncertain effect blocks this receipt.
- all old qualification ACKs are rejected after this receipt.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.12 `ReopenPreparationIntent`

```text
ReopenPreparationIntent(
    reopen_operation_id: ID_TOKEN,
    old_qualification_id: ID_TOKEN,
    old_epoch: POSITIVE,
    cancel_receipt_hash: SHA256,
    new_qualification_id: ID_TOKEN,
    new_epoch: POSITIVE,
    new_contract_hash: SHA256,
    new_release_child_manifest_digest: OCI_DIGEST,
    new_runtime_graph_digest: SHA256,
    new_capability_policy_digest: SHA256,
    memory_source_snapshot_kind: MemorySourceSnapshotKind,
    memory_source_snapshot_hash: SHA256,
    attempt: POSITIVE,
    request_hash: SHA256,
    state: ReopenIntentState,
    created_at: UTC,
    previous_qualification_artifact_hash: SHA256,
)

SCHEMA = phase8-reopen-preparation-intent
VERSION = 1
DOMAIN = phase8-reopen-preparation-intent-v1
HASH_KIND = domain_hash
```

Closed enum references: `MemorySourceSnapshotKind`, `ReopenIntentState`.

Invariants:
- old run and singleton are cancelled terminal and old allocations/jobs/effects are bilaterally closed.
- new_epoch equals old_epoch plus one and IDs/hashes are derived from the new tuple.
- state is preparing; one active intent exists per old qualification/epoch.
- same request retries the exact intent; divergence fails before memory authority.
- attempt advances only after an abandoned intent is terminal on both sides.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.13 `MemoryPreparationReceipt`

```text
MemoryPreparationReceipt(
    operation_id: ID_TOKEN,
    reopen_operation_id: ID_TOKEN,
    request_hash: SHA256,
    memory_source_snapshot_kind: MemorySourceSnapshotKind,
    memory_source_snapshot_hash: SHA256,
    new_qualification_id: ID_TOKEN,
    new_epoch: POSITIVE,
    root_identity_hash: SHA256,
    prepared_content_hash: SHA256,
    state: MemoryPreparationState,
    prepared_at: UTC,
)

SCHEMA = phase8-memory-preparation-receipt
VERSION = 1
DOMAIN = phase8-memory-preparation-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `MemorySourceSnapshotKind`, `MemoryPreparationState`.

Invariants:
- owner target reaches prepared only after rename-no-replace chmod and required fsyncs.
- root identity is path-independent and binds device/inode/class evidence through a private owner record.
- prepared_content_hash equals the selected baseline or sealed snapshot hash.
- state is prepared; duplicate returns identical bytes.
- no SQLite transaction remains open during clone or filesystem publication.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.14 `MemoryPreparationAckReceipt`

```text
MemoryPreparationAckReceipt(
    operation_id: ID_TOKEN,
    preparation_receipt_hash: SHA256,
    qualification_reopen_receipt_hash: SHA256,
    new_qualification_id: ID_TOKEN,
    new_epoch: POSITIVE,
    state: MemoryPreparationState,
    acked_at: UTC,
)

SCHEMA = phase8-memory-preparation-ack-receipt
VERSION = 1
DOMAIN = phase8-memory-preparation-ack-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `MemoryPreparationState`.

Invariants:
- state is acked and target CAS is prepared to acked.
- journal must persist the same receipt before the new run may open.
- target-commit/journal-ack retries return byte-identical bytes.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.15 `ReopenIntentAbandonStartReceipt`

```text
ReopenIntentAbandonStartReceipt(
    abandon_operation_id: ID_TOKEN,
    request_hash: SHA256,
    reopen_intent_hash: SHA256,
    old_qualification_id: ID_TOKEN,
    old_epoch: POSITIVE,
    attempt: POSITIVE,
    memory_preparation_operation_id: ID_TOKEN,
    previous_intent_state: ReopenIntentState,
    started_at: UTC,
)

SCHEMA = phase8-reopen-intent-abandon-start-receipt
VERSION = 1
DOMAIN = phase8-reopen-intent-abandon-start-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `ReopenIntentState`.

Invariants:
- old run remains cancelled with zero reopen receipt.
- same execution lock is held through target and journal terminal abandonment.
- previous intent state is preparing and journal CAS moves it to abandoning.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.16 `MemoryPreparationAbandonReceipt`

```text
MemoryPreparationAbandonReceipt(
    memory_preparation_operation_id: ID_TOKEN,
    abandon_operation_id: ID_TOKEN,
    abandon_start_receipt_hash: SHA256,
    target_predecessor_state: MemoryPreparationPredecessor,
    zero_scan_hash: SHA256,
    tombstone_identity_hash: SHA256,
    state: MemoryPreparationState,
    completed_at: UTC,
)

SCHEMA = phase8-memory-preparation-abandon-receipt
VERSION = 1
DOMAIN = phase8-memory-preparation-abandon-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `MemoryPreparationPredecessor`, `MemoryPreparationState`.

Invariants:
- target enters abandoning before any filesystem mutation.
- state is abandoned only after deterministic tombstone cleanup and exact zero scan.
- not_found predecessor is allowed only with zero row/temp/final/tombstone proof.
- abandoned never coexists with payload; divergence enters manual_review without further delete.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.17 `ReopenIntentAbandonReceipt`

```text
ReopenIntentAbandonReceipt(
    abandon_operation_id: ID_TOKEN,
    abandon_start_receipt_hash: SHA256,
    memory_preparation_abandon_receipt_hash: SHA256,
    old_qualification_id: ID_TOKEN,
    old_epoch: POSITIVE,
    attempt: POSITIVE,
    state: ReopenIntentState,
    completed_at: UTC,
)

SCHEMA = phase8-reopen-intent-abandon-receipt
VERSION = 1
DOMAIN = phase8-reopen-intent-abandon-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `ReopenIntentState`.

Invariants:
- journal moves abandoning to abandoned only after owner target abandonment receipt is verified.
- same hash is persisted in journal and duplicate is byte-identical.
- only then may a later monotonic attempt be reserved.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.18 `QualificationReopenReceipt`

```text
QualificationReopenReceipt(
    reopen_operation_id: ID_TOKEN,
    reopen_intent_hash: SHA256,
    old_qualification_id: ID_TOKEN,
    old_epoch: POSITIVE,
    cancel_receipt_hash: SHA256,
    new_qualification_id: ID_TOKEN,
    new_epoch: POSITIVE,
    new_contract_hash: SHA256,
    memory_preparation_receipt_hash: SHA256,
    new_run_hash: SHA256,
    new_scenario_aggregate_hash: SHA256,
    singleton_predecessor_revision: POSITIVE,
    singleton_installing_revision: POSITIVE,
    reopened_at: UTC,
    previous_qualification_artifact_hash: SHA256,
)

SCHEMA = phase8-qualification-reopen-receipt
VERSION = 1
DOMAIN = phase8-qualification-reopen-receipt-v1
HASH_KIND = domain_hash
```

Invariants:
- new_epoch is old_epoch plus one and new IDs derive from accepted inputs.
- same journal transaction validates intent/preparation inserts new run/scenarios moves singleton cancelled to installing and commits this receipt.
- old intent moves preparing to committed.
- old ACK/install receipts never bind the new tuple.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.19 `ConversationTestDispatchAuthorization`

```text
ConversationTestDispatchAuthorization(
    authorization_id: ID_TOKEN,
    release_child_manifest_digest: OCI_DIGEST,
    runtime_graph_digest: SHA256,
    capability_policy_digest: SHA256,
    behavior_state_snapshot_digest: SHA256,
    runtime_role: RuntimeRole,
    recipient_hash: SHA256,
    target_hash: SHA256,
    channel_hash: SHA256,
    allowlist_digest: SHA256,
    allowlist_cardinality: POSITIVE,
    traffic_stage: TrafficStage,
    state_root_class: StateRootClass,
    instance_id: ID_TOKEN,
    immutable_generation: POSITIVE,
    public_allocation_ids: nonempty tuple[ID_TOKEN,...],
    opened_effect_scopes: tuple[EffectScope,...],
    closed_effect_scopes: nonempty tuple[EffectScope,...],
    memory_root_binding_hash: SHA256,
    state_root_binding_hash: SHA256,
    session_root_binding_hash: SHA256,
    outbox_zero_scan_hash: SHA256,
    not_before: UTC,
    expires_at: UTC,
    approver_identity_hash: SHA256,
    authorization_request_hash: SHA256,
)

SCHEMA = phase8-conversation-test-dispatch-authorization
VERSION = 1
DOMAIN = phase8-conversation-test-dispatch-authorization-v1
HASH_KIND = domain_hash
```

Closed enum references: `RuntimeRole`, `TrafficStage`, `StateRootClass`, `EffectScope`.

Invariants:
- runtime role and traffic stage are conversation_test; root class is ephemeral_canary.
- allowlist cardinality is exactly one and recipient target channel equal the single allowed contact.
- opened scopes are exactly public_delivery; provider command relay payment handoff and followup delivery are closed.
- public allocation IDs are finite unique preinstalled and each call consumes one; unused rows close terminally.
- reads remain read-only and learning may target only the isolated canary memory binding.
- state session outbox zero scans and clean baseline are authenticated before issuance.
- finite window is re-sampled under the public delivery execution lock.
- does not authorize E2E provider effects rollout or automated conversation.

The exact known-answer `data`, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under this contract name.

### 9.20 Closed qualification enum values

`AdmissionMembershipStatus` is exactly:

```text
admitted | commit_fenced | turn_receipt_committed | aborted | manual_review
```

`AdmissionState` is exactly:

```text
installing | open | qualifying | frozen | cancelled | manual_review
```

`EffectScope` is exactly:

```text
reservation_provider | payment_provider | command_relay | handoff_delivery | payment_delivery | public_delivery | learning_write
```

`ExpectedCommandKind` is exactly:

```text
reservation | settlement
```

`ExpectedRelayKind` is exactly:

```text
reservation | settlement
```

`FollowupDeliveryKind` is exactly:

```text
handoff | payment
```

`MemoryPreparationPredecessor` is exactly:

```text
not_found | preparing | prepared
```

`MemoryPreparationState` is exactly:

```text
preparing | prepared | acked | abandoning | abandoned | manual_review
```

`MemorySourceSnapshotKind` is exactly:

```text
sealed_snapshot | authenticated_baseline
```

`ProviderOutcomeKind` is exactly:

```text
reservation_provider | payment_provider
```

`ProviderScope` is exactly:

```text
cloudbeds | bokun
```

`QualificationRunStatus` is exactly:

```text
installing | open | qualifying | effects_verified | learning_drained | memory_sealed | transition_recorded | qualified | frozen | cancelled | manual_review
```

`ReopenIntentState` is exactly:

```text
preparing | abandoning | abandoned | committed
```

`RuntimeRole` is exactly:

```text
canary_e2e | sealed_canary_qualification | conversation_test | production_initial
```

`StateRootClass` is exactly:

```text
ephemeral_canary | persistent_production
```

`TargetIngressKind` is exactly:

```text
reservation | settlement | handoff
```

`TrafficStage` is exactly:

```text
canary_e2e | conversation_test | rollout_initial
```

`WorkflowScope` is exactly:

```text
lodging_reservation | activity_reservation | lodging_payment | activity_payment | handoff
```

## 10. Effects, receipts and exact allocation closure

Every identity in this section is an **explicit proposed implementation refinement** because the architectural authority fixed the invariant and owner semantics but did not contain a literal field registry. These decisions do not claim to pre-exist in commit `2889e9e…`; they become executable only through the acceptance procedure in section 1.

No type below adds a capability. Receipts are evidence emitted by their existing owner. `EffectAllocationRow`, `AllocationInstallationReceipt` and `AllocationGenerationClosureReceipt` are the three strictly necessary names assigned here to the architecture’s previously unnamed allocation row and installation/closure receipts. `ChildAllocationUnusedReceipt` keeps the architectural name; there is no broader invented child-decision receipt.

### 10.1 `HandoffRelayBundle`

```text
HandoffRelayBundle(
    handoff_request_hash: SHA256,
    handoff_policy_hash: SHA256,
    sanitized_history_hashes: tuple[SHA256,...],
    expected_target_binding_hash: SHA256,
    artifact_hash: SHA256,
)

SCHEMA = phase8-handoff-relay-bundle
VERSION = 1
DOMAIN = phase8-handoff-relay-bundle-v1
HASH_KIND = artifact_preimage
PREIMAGE_SCHEMA = phase8-handoff-relay-bundle-preimage
```

Invariants:
- contains only hashes of the closed request, policy, public-safe history and expected target binding; no raw message or PII.
- artifact_hash is the domain hash of the canonical envelope with artifact_hash omitted.
- source_turn_receipt_hash is deliberately excluded and belongs to BoundaryInternalJob.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `HandoffRelayBundle`.

### 10.2 `BoundaryInternalJob`

```text
BoundaryInternalJob(
    job_id: ID_TOKEN,
    job_kind: InternalJobKind,
    artifact_bytes: CANON_BYTES(HandoffRelayBundle|LearningProposal),
    artifact_hash: SHA256,
    source_turn_receipt_hash: SHA256,
    qualification_id: ID_TOKEN|null,
    epoch: POSITIVE|null,
    target_operation_id: SHA256,
    deadline_at: UTC,
)

SCHEMA = phase8-boundary-internal-job
VERSION = 1
DOMAIN = phase8-boundary-internal-job-v1
HASH_KIND = domain_hash
```

Closed enum references: `InternalJobKind`.

Closed nullable/status matrix:

- `qualification_id is null`: null = `qualification_id, epoch`; present = `none`.
- `qualification_id is present`: null = `none`; present = `qualification_id, epoch`.

Invariants:
- job_kind selects exactly one strict artifact decoder: handoff or learning.
- artifact_hash is recomputed from artifact_bytes; source receipt is a separate backlink.
- qualification_id and epoch are all-null or all-present.
- target_operation_id is derived with phase8-internal-target-v1 from kind, qualification tuple, job ID, artifact hash and source receipt hash.
- settlement is not a member and the contract carries no provider, delivery or memory-write capability.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `BoundaryInternalJob`.

### 10.3 `TargetOperationReceipt`

```text
TargetOperationReceipt(
    operation_id: SHA256,
    job_kind: InternalJobKind,
    artifact_hash: SHA256,
    source_turn_receipt_hash: SHA256,
    target_commit_hash: SHA256,
    target_result_hash: SHA256,
    committed_at: UTC,
)

SCHEMA = phase8-target-operation-receipt
VERSION = 1
DOMAIN = phase8-target-operation-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `InternalJobKind`.

Invariants:
- constructed and persisted atomically by the handoff or learning target owner.
- operation, artifact and source backlink must equal the accepted BoundaryInternalJob.
- duplicate exact operation returns byte-identical receipt; any conflicting tuple is divergent.
- contains no target payload, raw memory, PII, provider reference or capability.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `TargetOperationReceipt`.

### 10.4 `OperationReceiptLookupResult`

```text
OperationReceiptLookupResult(
    status: OperationLookupStatus,
    operation_id: SHA256,
    artifact_hash: SHA256,
    source_turn_receipt_hash: SHA256,
    receipt_bytes: CANON_BYTES(TargetOperationReceipt)|null,
    evidence_hash: SHA256,
)

SCHEMA = phase8-operation-receipt-lookup-result
VERSION = 1
DOMAIN = phase8-operation-receipt-lookup-result-v1
HASH_KIND = domain_hash
```

Closed enum references: `OperationLookupStatus`.

Closed nullable/status matrix:

- `status=receipt`: null = `none`; present = `receipt_bytes`.
- `status=not_found|divergent`: null = `receipt_bytes`; present = `none`.

Invariants:
- not_found requires receipt_bytes null and evidence_hash of a complete target zero-scan.
- receipt requires strict receipt bytes and evidence_hash equal the decoded receipt domain hash.
- divergent requires receipt_bytes null and evidence_hash of owner divergence evidence.
- lookup is capability-free and side-effect-free.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `OperationReceiptLookupResult`.

### 10.5 `BoundaryRelayReceipt`

```text
BoundaryRelayReceipt(
    source_job_id: ID_TOKEN,
    operation_id: SHA256,
    artifact_hash: SHA256,
    source_turn_receipt_hash: SHA256,
    target_receipt_hash: SHA256,
    source_predecessor_status: InternalJobSourcePredecessor,
    source_status: InternalJobSourceTerminal,
    acked_at: UTC,
)

SCHEMA = phase8-boundary-relay-receipt
VERSION = 1
DOMAIN = phase8-boundary-relay-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `InternalJobSourcePredecessor`, `InternalJobSourceTerminal`.

Invariants:
- created only after strict validation of TargetOperationReceipt.
- source status is acked and predecessor is pending or leased.
- source ACK uses the exact operation, artifact, source receipt and target receipt tuple.
- receipt authorizes no provider or delivery effect.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `BoundaryRelayReceipt`.

### 10.6 `InternalJobClosureReceipt`

```text
InternalJobClosureReceipt(
    source_job_id: ID_TOKEN,
    operation_id: SHA256,
    job_kind: InternalJobKind,
    artifact_hash: SHA256,
    source_turn_receipt_hash: SHA256,
    lookup_status: OperationLookupTerminalAbsence,
    target_zero_scan_hash: SHA256,
    source_predecessor_status: InternalJobSourcePredecessor,
    source_status: InternalJobSourceTerminal,
    closed_at: UTC,
)

SCHEMA = phase8-internal-job-closure-receipt
VERSION = 1
DOMAIN = phase8-internal-job-closure-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `InternalJobKind`, `OperationLookupTerminalAbsence`, `InternalJobSourcePredecessor`, `InternalJobSourceTerminal`.

Invariants:
- published under the same execution lock only after a complete not_found lookup.
- source status is cancelled; divergent, unavailable or uncertain target state cannot create this receipt.
- source CAS and receipt persistence are one boundary transaction.
- a closed job can never call handoff or mutate memory later.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `InternalJobClosureReceipt`.

### 10.7 `PublicDeliveryReceipt`

```text
PublicDeliveryReceipt(
    public_row_id: ID_TOKEN,
    aggregate_turn_id: ID_TOKEN,
    chunk_ordinal: ORDINAL,
    allocation_id: ID_TOKEN,
    immutable_generation: POSITIVE,
    idempotency_key_hash: SHA256,
    artifact_hash: SHA256,
    target_binding_hash: SHA256,
    provider_receipt_hash: SHA256,
    result: PublicDeliveryResult,
    delivered_at: UTC,
)

SCHEMA = phase8-public-delivery-receipt
VERSION = 1
DOMAIN = phase8-public-delivery-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `PublicDeliveryResult`.

Invariants:
- one receipt per exact public row and external call.
- result is delivered and commits dispatch_fenced to terminal under the public execution lock.
- allocation and immutable generation are re-sampled immediately before the call and consumed in the same terminal CAS.
- contains no public text, recipient, channel identifier, provider reference or capability.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `PublicDeliveryReceipt`.

### 10.8 `AdmissionAbortReceipt`

```text
AdmissionAbortReceipt(
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    admission_sequence: POSITIVE,
    aggregate_turn_id: ID_TOKEN,
    admission_revision: POSITIVE|null,
    commit_fence_token: SHA256|null,
    owner_instance_id: ID_TOKEN|null,
    boundary_preimage_version: COUNT,
    boundary_preimage_hash: SHA256,
    zero_scan_hash: SHA256,
    predecessor_status: AdmissionAbortPredecessor,
    final_status: AdmissionAbortTerminal,
    aborted_at: UTC,
)

SCHEMA = phase8-admission-abort-receipt
VERSION = 1
DOMAIN = phase8-admission-abort-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `AdmissionAbortPredecessor`, `AdmissionAbortTerminal`.

Closed nullable/status matrix:

- `predecessor_status=admitted`: null = `admission_revision, commit_fence_token, owner_instance_id`; present = `none`.
- `predecessor_status=commit_fenced`: null = `none`; present = `admission_revision, commit_fence_token, owner_instance_id`.

Invariants:
- admitted predecessor requires revision, token and owner all null; commit_fenced requires all present.
- same lead lock proves byte-identical boundary preimage and zero event, receipt, child, target-ingress or consumed allocation.
- admission CAS and receipt persistence are atomic; the membership row is never deleted.
- any divergence or uncertain effect enters manual review instead of abort.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `AdmissionAbortReceipt`.

### 10.9 `ProviderEffectOutcomeReceipt`

```text
ProviderEffectOutcomeReceipt(
    effect_id: ID_TOKEN,
    effect_family: ProviderEffectFamily,
    effect_role: ProviderEffectRole,
    parent_effect_id: ID_TOKEN|null,
    command_id: ID_TOKEN,
    command_hash: SHA256,
    target_ingress_receipt_hash: SHA256,
    provider_operation_hash: SHA256,
    idempotency_key_hash: SHA256,
    outcome_shape: ProviderOutcomeShape,
    before_state_hash: SHA256|null,
    after_state_hash: SHA256|null,
    economic_hash: SHA256|null,
    terminal_result: ProviderTerminalResult,
    source_row_hashes: nonempty tuple[SHA256,...],
    owner_result_hash: SHA256,
    completed_at: UTC,
)

SCHEMA = phase8-provider-effect-outcome-receipt
VERSION = 1
DOMAIN = phase8-provider-effect-outcome-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `ProviderEffectFamily`, `ProviderEffectRole`, `ProviderOutcomeShape`, `ProviderTerminalResult`.

Closed nullable/status matrix:

- `effect_role=primary`: null = `parent_effect_id`; present = `none`.
- `effect_role=compensation`: null = `none`; present = `parent_effect_id`.
- `outcome_shape=state_transition`: null = `economic_hash`; present = `before_state_hash, after_state_hash`.
- `outcome_shape=economic`: null = `before_state_hash, after_state_hash`; present = `economic_hash`.

Invariants:
- reservation is derived only from reservation owner rows; settlement only from payment owner rows.
- state_transition requires before and after hashes with economic null; economic requires economic hash with before and after null.
- primary requires parent null; compensation requires parent effect ID.
- journal re-derives from byte-identical terminal owner rows and never trusts worker-supplied receipt bytes.
- receipt is evidence only and creates no second ledger or effect capability.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `ProviderEffectOutcomeReceipt`.

### 10.10 `EffectAllocationRow`

```text
EffectAllocationRow(
    row_kind: AllocationRowKind,
    installation_target: InstallationTarget,
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    scenario_id: ID_TOKEN,
    contract_hash: SHA256,
    effect_authorization_binding_hash: SHA256,
    generation_id: ID_TOKEN,
    allocation_id: ID_TOKEN,
    allocation_ordinal: ORDINAL,
    effect_family: EffectFamily,
    effect_kind: EffectKind,
    effect_role: AllocationEffectRole,
    effect_scope_hash: SHA256,
    workflow_scope_hash: SHA256|null,
    channel_scope_hash: SHA256|null,
    target_binding_hash: SHA256,
    message_ordinal: ORDINAL|null,
    activation_parent_kind: ActivationParentKind,
    activation_parent_id: ID_TOKEN|null,
    activation_parent_hash: SHA256|null,
    initial_state: AllocationInitialState,
)

SCHEMA = phase8-effect-allocation-row
VERSION = 1
DOMAIN = phase8-effect-allocation-row-v1
HASH_KIND = domain_hash
```

Closed enum references: `AllocationRowKind`, `InstallationTarget`, `EffectFamily`, `EffectKind`, `AllocationEffectRole`, `ActivationParentKind`, `AllocationInitialState`.

Closed nullable/status matrix:

- `activation_parent_kind=none`: null = `activation_parent_id, activation_parent_hash`; present = `none`.
- `activation_parent_kind=provider_allocation|internal_target_operation`: null = `none`; present = `activation_parent_id, activation_parent_hash`.

Invariants:
- field combinations follow the closed family/kind/target/scope/parent matrix in the design document.
- root allocations have no parent; compensation and payment delivery reference provider allocation; handoff delivery references internal target operation.
- public delivery alone has message ordinal and channel binding with no workflow scope.
- all identity and scope fields are immutable; historical generations are never rewritten.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `EffectAllocationRow`.

### 10.11 `ExactEffectAllocationManifest`

```text
ExactEffectAllocationManifest(
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    contract_hash: SHA256,
    effect_authorization_binding_hash: SHA256,
    rows: nonempty tuple[EffectAllocationRow,...],
    allocation_count: POSITIVE,
)

SCHEMA = phase8-exact-effect-allocation-manifest
VERSION = 1
DOMAIN = phase8-exact-effect-allocation-manifest-v1
HASH_KIND = domain_hash
```

Invariants:
- one row exists for every and only effect allowed by the qualification contract.
- rows are canonically ordered by target, scenario, generation, ordinal and allocation ID.
- allocation IDs and target-generation allocation tuples are unique and every parent row precedes its child.
- allocation_count equals the number of rows and no header or later write can expand budget.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `ExactEffectAllocationManifest`.

### 10.12 `AllocationInstallationReceipt`

```text
AllocationInstallationReceipt(
    operation_id: SHA256,
    installation_target: InstallationTarget,
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    contract_hash: SHA256,
    effect_authorization_binding_hash: SHA256,
    manifest_hash: SHA256,
    generation_ids: nonempty tuple[ID_TOKEN,...],
    installed_row_hashes: nonempty tuple[SHA256,...],
    allocation_count: POSITIVE,
    installed_allocation_aggregate_hash: SHA256,
    header_state: InstallationHeaderState,
    status: InstallationStatus,
    installed_at: UTC,
)

SCHEMA = phase8-allocation-installation-receipt
VERSION = 1
DOMAIN = phase8-allocation-installation-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `InstallationTarget`, `InstallationHeaderState`, `InstallationStatus`.

Invariants:
- exactly one byte-identical receipt per installation target is required before admission opens.
- header and every target row install atomically; partial install emits no receipt.
- generation IDs and row hashes are canonical and allocation count equals installed row count.
- late install against a closed tombstone or divergent tuple fails without receipt.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `AllocationInstallationReceipt`.

### 10.13 `ChildAllocationUnusedReceipt`

```text
ChildAllocationUnusedReceipt(
    operation_id: SHA256,
    installation_target: ChildInstallationTarget,
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    scenario_id: ID_TOKEN,
    contract_hash: SHA256,
    effect_authorization_binding_hash: SHA256,
    manifest_hash: SHA256,
    generation_id: ID_TOKEN,
    child_allocation_id: ID_TOKEN,
    child_allocation_hash: SHA256,
    activation_parent_kind: ChildActivationParentKind,
    activation_parent_id: ID_TOKEN,
    activation_parent_hash: SHA256,
    parent_evidence_kind: ParentEvidenceKind,
    parent_evidence_hash: SHA256,
    parent_disposition: ParentDisposition,
    decision: ChildUnusedDecision,
    before_state: ChildUnusedBeforeState,
    after_state: ChildUnusedAfterState,
    decided_at: UTC,
)

SCHEMA = phase8-child-allocation-unused-receipt
VERSION = 1
DOMAIN = phase8-child-allocation-unused-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `ChildInstallationTarget`, `ChildActivationParentKind`, `ParentEvidenceKind`, `ParentDisposition`, `ChildUnusedDecision`, `ChildUnusedBeforeState`, `ChildUnusedAfterState`.

Invariants:
- created only in the same target-local reducer transaction that verifies the exact terminal activation-parent evidence.
- parent disposition is does_not_activate_child, decision is unused and state moves available to closed.
- provider parent accepts only provider outcome evidence; internal target accepts target receipt or internal closure evidence.
- after this receipt the child can never bind or fence.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `ChildAllocationUnusedReceipt`.

### 10.14 `AllocationGenerationClosureReceipt`

```text
AllocationGenerationClosureReceipt(
    operation_id: SHA256,
    installation_target: InstallationTarget,
    qualification_id: ID_TOKEN,
    epoch: POSITIVE,
    scenario_id: ID_TOKEN,
    contract_hash: SHA256,
    effect_authorization_binding_hash: SHA256,
    manifest_hash: SHA256,
    generation_id: ID_TOKEN,
    closure_mode: GenerationClosureMode,
    installation_receipt_hash: SHA256|null,
    begin_state: GenerationBeginState,
    intermediate_state: GenerationIntermediateState,
    final_state: GenerationFinalState,
    allocation_count: COUNT,
    terminal_count: COUNT,
    closed_count: COUNT,
    parent_terminal_receipt_hashes: tuple[SHA256,...],
    child_unused_receipt_hashes: tuple[SHA256,...],
    missing_child_decision_count: COUNT,
    final_allocation_aggregate_hash: SHA256,
    closed_at: UTC,
)

SCHEMA = phase8-allocation-generation-closure-receipt
VERSION = 1
DOMAIN = phase8-allocation-generation-closure-receipt-v1
HASH_KIND = domain_hash
```

Closed enum references: `InstallationTarget`, `GenerationClosureMode`, `GenerationBeginState`, `GenerationIntermediateState`, `GenerationFinalState`.

Closed nullable/status matrix:

- `closure_mode=installed_generation`: null = `none`; present = `installation_receipt_hash`.
- `closure_mode=preinstall_tombstone`: null = `installation_receipt_hash`; present = `none`.

Invariants:
- installed mode requires installation receipt and open to closing to closed; tombstone mode requires null receipt and absent to not_applicable to closed.
- installed allocation count equals terminal plus closed counts; every child decision is accounted and missing count is zero.
- tombstone mode has all counts zero and both receipt-hash lists empty.
- manual-review rows or uncertain fenced effects block closure.

The exact known-answer data, canonical bytes and hash are frozen in `tests/fixtures/phase8_remaining_wire_registry_v1.json` under `AllocationGenerationClosureReceipt`.

### 10.15 Closed effects enum values

`ActivationParentKind` is exactly:

```text
none | provider_allocation | internal_target_operation
```

`AdmissionAbortPredecessor` is exactly:

```text
admitted | commit_fenced
```

`AdmissionAbortTerminal` is exactly:

```text
aborted
```

`AllocationEffectRole` is exactly:

```text
primary | compensation | none
```

`AllocationInitialState` is exactly:

```text
available
```

`AllocationRowKind` is exactly:

```text
allocation
```

`ChildActivationParentKind` is exactly:

```text
provider_allocation | internal_target_operation
```

`ChildInstallationTarget` is exactly:

```text
reservation_e2e_effect_authority | followup_e2e_effect_authority
```

`ChildUnusedAfterState` is exactly:

```text
closed
```

`ChildUnusedBeforeState` is exactly:

```text
available
```

`ChildUnusedDecision` is exactly:

```text
unused
```

`EffectFamily` is exactly:

```text
reservation | payment | handoff_delivery | payment_delivery | public_delivery
```

`EffectKind` is exactly:

```text
provider_primary | provider_compensation | external_message | public_chunk
```

`GenerationBeginState` is exactly:

```text
open | absent
```

`GenerationClosureMode` is exactly:

```text
installed_generation | preinstall_tombstone
```

`GenerationFinalState` is exactly:

```text
closed
```

`GenerationIntermediateState` is exactly:

```text
closing | not_applicable
```

`InstallationHeaderState` is exactly:

```text
open
```

`InstallationStatus` is exactly:

```text
installed
```

`InstallationTarget` is exactly:

```text
boundary_dispatch_authority | reservation_e2e_effect_authority | followup_e2e_effect_authority
```

`InternalJobKind` is exactly:

```text
handoff | learning
```

`InternalJobSourcePredecessor` is exactly:

```text
pending | leased
```

`InternalJobSourceTerminal` is exactly:

```text
acked | cancelled
```

`OperationLookupStatus` is exactly:

```text
not_found | receipt | divergent
```

`OperationLookupTerminalAbsence` is exactly:

```text
not_found
```

`ParentDisposition` is exactly:

```text
does_not_activate_child
```

`ParentEvidenceKind` is exactly:

```text
provider_effect_outcome | target_operation_receipt | internal_job_closure_receipt
```

`ProviderEffectFamily` is exactly:

```text
reservation | settlement
```

`ProviderEffectRole` is exactly:

```text
primary | compensation
```

`ProviderOutcomeShape` is exactly:

```text
state_transition | economic
```

`ProviderTerminalResult` is exactly:

```text
succeeded | failed
```

`PublicDeliveryResult` is exactly:

```text
delivered
```
