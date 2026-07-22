# Phase 8 Facts and Reads Wire Closure Design

**Status:** Revised candidate design delta. Carlos Eduardo approved the delta scope for documentation on 2026-07-22. This revision addresses the focused reviews of commits `85d9f19cda1d606bb15374727b469af930d05707` and `f2522887f4edbccb55aff0a04861907f40c1698b`. It is not executable implementation authority until its new committed identity is reviewed and explicitly accepted.

**Scope:** Close only the Phase 8 wire for `TypedFact`, `ReservationExecutionProjection`, `ConversationProjection`, typed read requests, owner-verifiable read evidence, the sanitized read-result union, and `ReadObservation`.

**Base architectural authority:**

- approved Phase 8 design at commit `2889e9ec08f466bbb16a30e4bb5c9a098daf54d3`;
- approved replacement plan and Implementation Closure Registry at commit `2b60474bc441b872a150dc738f90084e06a4cc8e`;
- implementation baseline before this delta: `6b9d6963be86ec327bd5c71d20c76cf61643c3e8`.

This document refines missing literal wire details. It does not alter architectural invariants, effect ownership, capability boundaries, table universes, FSMs, cardinalities, build gates, canary gates, or rollout gates.

## 1. Goal

Create a closed, versioned, hostile-input-safe wire for facts and read results without breaking the internal Phase 7 `TypedFact(name, value)` constructor before the Phase 8 kernel and dispatch migration reaches its own task.

The result must guarantee:

1. no free-form fact crosses the v8 wire;
2. no raw provider response crosses the v8 wire;
3. legacy genesis absence remains distinct from legacy unavailability;
4. request, lead, projection, locale, subject, service, dates, party and result evidence cannot be swapped;
5. a caller cannot mark an unsafe result as safe for a public claim;
6. every v8 fact and observation binds to exactly one transcript frame commitment;
7. Phase 7 facts without a frame backlink remain internal-only and cannot serialize as v8;
8. `BoundaryState v8` and `ConversationProjection` have finite, non-recursive canonical bytes.

## 2. Non-goals

This delta does not define or modify:

- `NormalizedToolProposal`;
- `LearningProposal`;
- `PublicReplyChunk`;
- `MayaTurnProposal`;
- `TurnReceipt` or another effect receipt;
- `CapabilityPolicy` or deployment bindings;
- allocations, authorities, workers, relay execution, delivery, providers, network, Docker, build, canary, or rollout;
- durable Boundary schema v8;
- the catalog or authorization behavior of Task 2 `ToolDispatch`.

The owner evidence stores defined here are read-only verification surfaces for already-produced receipts. They do not grant provider, write, delivery, filesystem, network, or child-process capability. No runtime or provider repository is modified by this design.

## 3. Canonical wire and scalar grammar

### 3.1 Contract envelope and hash

Every contract introduced or refined here has:

- `SCHEMA: ClassVar[str]`;
- `VERSION: ClassVar[int] = 1`;
- `DOMAIN: ClassVar[str]`;
- `to_canonical_bytes() -> bytes`;
- `canonical_hash() -> str`.

Canonical bytes are UTF-8 JSON with exactly the top-level keys `schema`, `version`, and `data`, encoded with:

```python
json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
```

A canonical hash is exactly:

```text
SHA256(ASCII(DOMAIN) || 0x00 || to_canonical_bytes())
```

Strict decoding rejects:

- duplicate JSON keys;
- missing or unknown keys at every object level;
- `bool` where `int` is required;
- every `float`;
- mutable nested input;
- non-canonical date, datetime, decimal, locale, identifier or SHA-256 text;
- unknown enum values;
- bytes from a different schema/domain;
- a decoded value whose re-encoding is not byte-identical.

Tuples are encoded as JSON arrays and decoded only into exact tuples. Ordering is semantic and deterministic. A missing field and an empty tuple are never equivalent.

### 3.2 Exact bytes representation

Every Python `bytes` field is encoded as an RFC 4648 standard Base64 JSON string using `base64.b64encode`, including required `=` padding. URL-safe Base64 is forbidden.

Decoding uses `base64.b64decode(value, validate=True)` and then requires:

```text
base64.b64encode(decoded).decode("ascii") == value
```

An empty byte field is rejected unless its field definition explicitly permits empty bytes. No contract in this delta permits empty bytes.

### 3.3 Nested contract representation

A nested contract is represented as the complete decoded JSON envelope:

```json
{"data":{...},"schema":"...","version":1}
```

It is never represented as `data` alone, a Python object repr, or an untagged mapping. The parent encoder obtains this object only by strict UTF-8 decoding and duplicate-safe parsing of the nested contract's canonical bytes. The parent decoder reconstructs the nested contract, re-encodes it and requires byte equality.

A `bytes` field whose semantic content is another canonical contract remains a Base64 string and is additionally strict-decoded with the named owner codec. The field definition states the required owner type.

### 3.4 Exact scalar grammars

Unless a field definition narrows it further:

```text
SHA256              = ^[0-9a-f]{64}$
OPAQUE_ID            = ^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$
INTERNAL_ID          = ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$
OFFER_ID             = ^offer:[0-9a-f]{64}$
LOOKUP_ID            = ^lookup:[0-9a-f]{64}$
LOCALE               = ^[a-z]{2}(?:-[A-Z]{2})?$
CURRENCY             = ^[A-Z]{3}$
DATE                 = exact date.isoformat(), YYYY-MM-DD
UTC_DATETIME         = exact aware-UTC datetime.isoformat(), with +00:00
TIME                 = ^(?:[01][0-9]|2[0-3]):[0-5][0-9]$
POSITIVE_DECIMAL_2   = ^(?:0|[1-9][0-9]*)\.[0-9]{2}$ and Decimal(value) > 0
```

`UTC_DATETIME` does not accept `Z`, a non-zero offset, naive time, or an alternate but equivalent textual form.

### 3.5 Normative known-answer fixture

`tests/fixtures/phase8_facts_reads_wire_v1.json` is a normative, evidence-only fixture committed with this revised design. It contains complete canonical UTF-8 text, standard-Base64 bytes and domain-separated hashes for:

- all three `TypedFact.value.kind` variants;
- `ReservationExecutionProjection` and `ConversationProjection`;
- all five `Phase8ToolReadRequest` tool/argument pairs and `LegacyGenesisReadRequest`;
- all three `LegacyGenesisReceipt.status` variants;
- all three `LegacyGenesisEvidenceRecord.status` matrices;
- both `ReadEvidenceDisposition` variants;
- every `SanitizedReadResult` variant, including all three lookup statuses;
- every `ReadObservationStatus` matrix row.
- the exact historical Phase 7 `chapada_commit_state` request with a two-field
  `TypedFact`.

An implementation that produces a different byte or hash is non-conforming even if it decodes to equal Python values.

Normative fixture identity for this revision:

```text
SHA-256 = fabdb3677cbd9d1b1157fd1cadcfb589bf8a5f1fb5a8cd827aff2a33a4395241
bytes = 208037
lines = 522
contract examples = 45
auxiliary preimages = 18
Phase 7 compatibility wires = 1
accepted sanitization probes = 3
rejected sanitization probes = 9
```

All fixture values are synthetic. No raw provider response, contact identity, credential or production payload is present.

## 4. TypedFact transition

### 4.1 Single-class compatibility strategy

There remains one Python class named `reservation_boundary.types.TypedFact`.

Its ordered fields become:

```text
TypedFact(name, value, frame_commitment_hash)
```

The constructor preserves Phase 7 source compatibility:

```python
frame_commitment_hash: str | None = None
```

A fact with `frame_commitment_hash is None` is a **legacy internal fact**. It may continue to flow through unchanged Phase 7 paths, but:

- `to_canonical_bytes()` rejects it;
- `canonical_hash()` rejects it;
- `ConversationProjection` rejects it;
- `ReadObservation.derived_facts` rejects it;
- the v8 serializer rejects it.

This compatibility exception is local to construction of the existing Phase 7 type. It is not a nullable v8 wire field. V8 canonical bytes always contain a lowercase SHA-256 `frame_commitment_hash`.

### 4.2 Fact catalog

The v8 catalog contains exactly six names and value variants:

| Fact name | Exact value type |
|---|---|
| `language` | `StringSlot` |
| `service` | `StringSlot` |
| `start_date` | `DateSlot` |
| `end_date` | `DateSlot` |
| `adults` | `IntegerSlot` |
| `children` | `IntegerSlot` |

No availability, offer, FAQ answer, description text, provider status, payment proof, reservation identity or arbitrary slot name is a v8 fact.

`service` is exactly `hostel` or `agency`. `language` satisfies `LOCALE`. `adults >= 1`; `children >= 0`; dates are exact `date`, never `datetime`.

Fact ordering is the table order above. Duplicate names are rejected.

### 4.3 V8 identity and data

```text
SCHEMA = phase8-typed-fact
VERSION = 1
DOMAIN = phase8-typed-fact-v1
```

Exact v8 data fields:

```text
name
value = {kind, value}
frame_commitment_hash
```

`kind` is exactly `string`, `integer` or `date`. Date values use `DATE`.

### 4.4 Exact Phase 7 wire compatibility

The Phase 7 public wire remains byte-identical for a legacy `TypedFact`:

```json
{"$type":"TypedFact","data":{"name":"language","value":{"$type":"StringSlot","data":{"value":"pt-BR"}}}}
```

The Phase 7 serializer/decoder has an explicit `TypedFact` compatibility branch rather than reflecting all dataclass fields:

- `frame_commitment_hash is None`: encode exactly the historical two-field object and decode with `None`;
- non-null `frame_commitment_hash`: reject Phase 7 serialization as a downgrade attempt;
- missing or extra fields in the historical object: reject;
- Phase 8 serialization: require non-null backlink and use only the v8 envelope from section 4.3.

This is a deliberate exception to generic dataclass reflection. It preserves the existing public v7 wire while preventing a v8 fact from being silently downgraded.

After changing `TypedFact`, the implementation must run these proportional regressions unconditionally, even if `serialization.py` bytes were not otherwise edited:

```text
tests.test_phase7_serialization
tests.test_phase7_types
tests.test_phase7_dispatch
```

`tests.test_phase7_serialization` gains an exact known-answer case for:

```text
ToolDispatchRequest(
  tool_name="chapada_commit_state",
  arguments=StateCommitArguments((TypedFact("language", StringSlot("pt-BR")),)),
  lead_key="lead-synthetic-001",
  event_id="event-synthetic-001",
  deadline=datetime(2026, 8, 1, 12, 5, tzinfo=timezone.utc),
)
```

Its exact 384-byte wire and SHA-256
`fbcf8b1487fb0def2e188c3e02ed97b4cb0905df959b0d688535570e9e732243`
are stored at `phase7_compatibility.state_commit_request` in the normative
fixture. The test proves byte-identical two-field fact wire and round-trip. A
second case proves that the same request with a non-null frame backlink is
rejected by the v7 serializer.

## 5. Finite ConversationProjection

### 5.1 Fields and identity

Ordered fields:

```text
ConversationProjection(stage, desired_services, locale, facts,
                       reservation_execution_projection)
```

Identity:

```text
SCHEMA = phase8-conversation-projection
VERSION = 1
DOMAIN = phase8-conversation-projection-v1
```

`reservation_execution_projection` is exactly `ReservationExecutionProjection | None`, encoded as a full nested envelope or JSON `null`.

### 5.2 Closed stage and service universes

`ConversationStage` is exactly:

```text
recepcionista | hostel | agencia | fechamento | handoff | no_reply
```

`DesiredService` is exactly:

```text
hostel | agency
```

`desired_services` is an exact tuple, may be empty, contains no duplicates, and is ordered `hostel`, then `agency`.

`locale` satisfies `LOCALE`.

`facts` is an exact tuple of v8-wire-ready `TypedFact`. Every fact has a non-null frame backlink, names are unique, and ordering follows section 4.2. Zero facts are valid.

### 5.3 ReservationExecutionProjection

Ordered fields:

```text
ReservationExecutionProjection(
    reservation_relay_bundle_bytes,
    reservation_relay_bundle_hash,
)
```

Identity:

```text
SCHEMA = phase8-reservation-execution-projection
VERSION = 1
DOMAIN = phase8-reservation-execution-projection-v1
BUNDLE_BINDING_DOMAIN = phase8-reservation-execution-bundle-binding-v1
```

Rules:

1. `reservation_relay_bundle_bytes` is non-empty exact bytes encoded by section 3.2.
2. The bytes strict-decode to exact `reservation_boundary.effects.ReservationRelayBundle` and re-encode byte-identically.
3. `reservation_relay_bundle_hash` is exactly:

```text
SHA256(ASCII(BUNDLE_BINDING_DOMAIN) || 0x00 || reservation_relay_bundle_bytes)
```

4. The bundle carries the already-closed, non-recursive replay material: genesis state, exact Phase 5 event sequence, summary outboxes, expected final state/hash and command-ledger seed.
5. When embedding the projection into `BoundaryState`, the Boundary owner strict-decodes `bundle.expected_final_state` as an exact reservation-domain state and requires equality with `BoundaryState.workflow` plus equality of its owner semantic hash.
6. `BoundaryState.workflow is None` requires `reservation_execution_projection is None`; a non-null workflow requires it to be present.
7. No `BoundaryState`, `ConversationProjection`, raw provider response, open mapping, conversation text, token or arbitrary metadata appears inside this projection.

This breaks the recursive path. `BoundaryState v8` contains `ConversationProjection`, which contains only a non-recursive `ReservationRelayBundle`; the bundle never contains `BoundaryState` or `ConversationProjection`.

The settlement projection remains owned by Phase 6/payment fields already present in `BoundaryState`; it is not added to this delta.

## 6. Typed read requests and owner receipts

### 6.1 Lead identity formula

For every request, the parent derives:

```text
LEAD_KEY_DOMAIN = phase8-lead-key-v1
lead_key_hash = SHA256(ASCII(LEAD_KEY_DOMAIN) || 0x00 || UTF8(lead_key))
```

The raw lead key is never sent to the child or persisted in a sanitized result. Parent acceptance compares the hash to the current exact Boundary lead identity.

### 6.2 Phase8ToolReadRequest

Ordered fields:

```text
Phase8ToolReadRequest(
    tool_name,
    arguments,
    lead_key_hash,
    aggregate_turn_id,
    source_event,
    deadline_at,
    locale,
    projection_hash,
)
```

Identity:

```text
SCHEMA = phase8-tool-read-request
VERSION = 1
DOMAIN = phase8-tool-read-request-v1
```

`source_event` is a full nested `SourceEventIdentity`. `deadline_at` satisfies `UTC_DATETIME`; `locale` satisfies `LOCALE`; all hashes satisfy `SHA256`; IDs satisfy `OPAQUE_ID`.

`projection_hash` equals the canonical hash of the exact `ConversationProjection` admitted for the turn. Parent acceptance requires the request lead, turn, source event, deadline, locale and projection to equal the current turn bindings.

`tool_name` and the exact tagged `arguments` object use this matrix and no other pair:

| Tool | Argument tag | Exact data fields | Additional equality |
|---|---|---|---|
| `cerebro_consultar` | `FaqReadArguments` | `query`, `locale` | argument locale equals request locale |
| `cloudbeds_consultar_hospedagem_v2` | `LodgingReadArguments` | `check_in`, `check_out`, `adults`, `children` | dates/party become the lookup query |
| `cloudbeds_descrever_quartos` | `RoomDescriptionArguments` | `room_offer_id` | subject equals `room_offer_id` |
| `bokun_consultar_passeio_v2` | `ActivityReadArguments` | `activity_id`, `activity_date`, `participants` | lookup party is `(participants, 0)` |
| `bokun_consultar_descricao` | `ActivityDescriptionArguments` | `activity_id` | subject equals `activity_id` |

The canonical `arguments` JSON shape is exactly:

```json
{"data":{...},"type":"ExactArgumentClassName"}
```

It is a closed tagged value, not a top-level contract. Duplicate, missing or unknown keys; wrong tag/tool pair; bool-as-int; float; non-canonical date/locale/ID; or a sixth tool are rejected.

### 6.3 LegacyGenesisReadRequest

Ordered fields:

```text
LegacyGenesisReadRequest(
    lead_key_hash,
    aggregate_turn_id,
    source_event,
    deadline_at,
    legacy_source,
)
```

Identity:

```text
SCHEMA = phase8-legacy-genesis-read-request
VERSION = 1
DOMAIN = phase8-legacy-genesis-read-request-v1
```

`legacy_source` is exactly `chapada_leads_legacy_v1`. The other scalar rules are the same as section 6.2. Genesis is reachable only after owner-produced `StateNotFound`; it is not callable as a child tool.

### 6.4 ReadRequest union and request hash

`Phase8ReadRequest` is exactly:

```text
Phase8ToolReadRequest | LegacyGenesisReadRequest
```

`ReadObservation.request_bytes` strict-decodes to exactly one member.

```text
READ_REQUEST_DOMAIN = phase8-read-request-v1
request_hash = SHA256(ASCII(READ_REQUEST_DOMAIN) || 0x00 || request_bytes)
```

No arbitrary byte string can be authenticated merely by hashing it.

### 6.5 LegacyGenesisReceipt

Ordered fields:

```text
LegacyGenesisReceipt(
    receipt_id,
    request_hash,
    lead_key_hash,
    status,
    source_generation,
    source_watermark_hash,
    matched_row_count,
    source_snapshot_hash,
    projection_hash,
    failure_reason,
    failure_evidence_hash,
    completed_at,
)
```

Identity:

```text
SCHEMA = phase8-legacy-genesis-receipt
VERSION = 1
DOMAIN = phase8-legacy-genesis-receipt-v1
RECEIPT_ID_DOMAIN = phase8-legacy-genesis-receipt-id-v1
LEGACY_WATERMARK_DOMAIN = phase8-legacy-watermark-v1
LEGACY_SNAPSHOT_DOMAIN = phase8-legacy-snapshot-v1
LEGACY_FAILURE_DOMAIN = phase8-legacy-genesis-failure-v1
LEGACY_EVIDENCE_RECORD_DOMAIN = phase8-legacy-genesis-evidence-record-v1
```

`GenesisStatus` is exactly:

```text
found | proven_absent | unavailable
```

`LegacyUnavailableReason` is exactly:

```text
timeout | transport_error | malformed | unsupported_schema | identity_conflict
```

The exact matrix is:

| Status | generation/watermark | row count | snapshot/projection | failure reason/evidence |
|---|---|---:|---|---|
| `found` | both present | exactly `1` | both present | both null |
| `proven_absent` | both present | exactly `0` | both null | both null |
| `unavailable` | both null | null | both null | both present |

`source_generation` is an exact integer `>= 1`. `source_watermark_hash`, `source_snapshot_hash`, `projection_hash` and `failure_evidence_hash` satisfy `SHA256`. `completed_at` satisfies `UTC_DATETIME`.

For a successful source snapshot, the importer stores the canonical owner watermark bytes and derives:

```text
source_watermark_hash =
  SHA256(ASCII(LEGACY_WATERMARK_DOMAIN) || 0x00 || canonical_source_watermark_bytes)
```

The private watermark bytes are the canonical envelope:

```text
schema = phase8-legacy-source-watermark
version = 1
data = {
  source: "chapada_leads_legacy_v1",
  source_generation: exact integer >= 1,
  transaction_snapshot_id: OPAQUE_ID,
}
```

They are returned by owner-store verification and never supplied by the caller.

The receipt ID is derived, not caller-selected. Let `receipt_id_preimage` be the canonical envelope with schema `phase8-legacy-genesis-receipt-id-preimage`, version `1`, and the eleven fields after `receipt_id` in the same names/order. Then:

```text
receipt_id = "genesis:" +
  SHA256(ASCII(RECEIPT_ID_DOMAIN) || 0x00 || receipt_id_preimage)
```

For `found`, `canonical_legacy_snapshot_bytes` is the exact canonical envelope:

```text
schema = phase8-legacy-snapshot-evidence
version = 1
data = {
  source: "chapada_leads_legacy_v1",
  source_generation: exact integer >= 1,
  source_watermark_hash: SHA256,
  lead_key_hash: SHA256,
  matched_row_count: exactly 1,
  projection_bytes: non-empty standard-Base64 bytes,
  projection_hash: SHA256,
}
```

`projection_bytes` strict-decodes to one exact `ConversationProjection`,
re-encodes byte-identically, and its canonical hash equals `projection_hash`.
The other fields equal the receipt and request bindings. The snapshot hash is:

```text
source_snapshot_hash =
  SHA256(ASCII(LEGACY_SNAPSHOT_DOMAIN) || 0x00 || canonical_legacy_snapshot_bytes)
```

The receipt's `projection_hash` equals the snapshot-evidence projection hash.
The importer validates the normalized lead identity before constructing the
evidence and stores only `lead_key_hash`, never the raw lead key, in this
private codec.

For `unavailable`, `canonical_failure_evidence_bytes` is the exact canonical
envelope:

```text
schema = phase8-legacy-failure-evidence
version = 1
data = {
  source: "chapada_leads_legacy_v1",
  request_hash: SHA256,
  lead_key_hash: SHA256,
  failure_reason: LegacyUnavailableReason,
  attempt_count: exact integer >= 1,
  observed_at: UTC_DATETIME,
}
```

`request_hash`, `lead_key_hash`, `failure_reason` and `observed_at` equal the
bound request/receipt. The failure hash is:

```text
failure_evidence_hash =
  SHA256(ASCII(LEGACY_FAILURE_DOMAIN) || 0x00 || canonical_failure_evidence_bytes)
```

Failure evidence contains only classified metadata, never exception text,
credentials, URLs, request/response bodies or raw provider payloads.

For `proven_absent`, the legacy owner performs one exact-key scan in a consistent source snapshot, records source generation/watermark and persists the zero-row receipt before returning it. `None`, timeout, malformed data, unsupported schema or an exception cannot create this status.

### 6.6 Owner verification for genesis

`LegacyGenesisReceipt` is deterministic evidence but acceptance also proves
owner provenance. The owner stores the exact canonical envelope:

```text
LegacyGenesisEvidenceRecord(
    receipt_bytes,
    source_watermark_bytes,
    source_snapshot_bytes,
    failure_evidence_bytes,
)

SCHEMA = phase8-legacy-genesis-evidence-record
VERSION = 1
DOMAIN = phase8-legacy-genesis-evidence-record-v1
```

All four fields are standard-Base64 bytes or JSON `null`. `receipt_bytes` is
always non-null and strict-decodes to `LegacyGenesisReceipt`. The remaining
field matrix is exact:

| Receipt status | watermark bytes | snapshot bytes | failure bytes |
|---|---|---|---|
| `found` | present | present | null |
| `proven_absent` | present | null | null |
| `unavailable` | null | null | present |

The importer owner exposes only this capability-free verification port:

```text
LegacyGenesisEvidenceStore.get(receipt_id)
  -> canonical_evidence_record_bytes | NotFound
```

The store record is persisted atomically with the classified scan outcome.
Parent acceptance requires:

1. exact `receipt_id` lookup;
2. strict decoding and byte-identical re-encoding of the evidence record and
   every present nested byte field;
3. byte equality between `record.receipt_bytes` and the supplied receipt;
4. canonical receipt hash/ID/formula validation;
5. recomputation of every present watermark, snapshot and failure hash from
   the exact owner bytes;
6. equality of source generation, watermark, request, lead, status and failure
   bindings with the current request/receipt;
7. for `found`, strict projection decode plus equality with the supplied
   projection and its hash;
8. exact receipt and evidence-record status matrices.

A syntactically valid fabricated receipt or preimage not present
byte-identically in the owner store is rejected before Boundary commit.

### 6.7 Public read sanitization policy

The exact policy ID is `public-read-v1`. Its canonical policy manifest is:

```json
{"forbidden_patterns":{"br_phone":"(?<![0-9])(?:\\+?55[\\s.-]?)?(?:\\(?[1-9][0-9]\\)?[\\s.-]?)?(?:9[0-9]{4}|[2-8][0-9]{3})[\\s.-]?[0-9]{4}(?![0-9])","control":"[\\u0000-\\u0008\\u000b-\\u001f\\u007f]","cpf":"(?<![0-9])(?:[0-9]{3}[.\\s-]?){2}[0-9]{3}[-.\\s]?[0-9]{2}(?![0-9])","e164":"(?<![0-9])\\+[1-9][0-9]{7,14}(?![0-9])","email":"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]{1,64}@[A-Z0-9.-]+\\.[A-Z]{2,63}(?![A-Z0-9._%+-])","html":"<[A-Za-z!/][^>]*>","markdown_link":"!?\\[[^\\]]*\\]\\([^)]+\\)","pan":"(?<![0-9])(?:[0-9][ -]?){12,18}[0-9](?![0-9])","provider_ref":"(?:cloudbeds\\.property\\.|bokun\\.product\\.)","random_payment_key":"(?i)(?<![0-9A-F])[0-9A-F]{8}-[0-9A-F]{4}-[1-5][0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}(?![0-9A-F])","secret_marker":"(?i)\\b(?:api[_-]?key|access[_-]?token|bearer)\\b\\s*[:=]","url":"(?i)(?:https?://|www\\.)\\S+"},"limits":{"knowledge_codepoints":4096,"label_codepoints":256},"normalization":{"double_ascii_space":"forbidden","line_ending":"LF","surrounding_whitespace":"forbidden","tab":"forbidden","unicode":"NFKC"},"schema":"phase8-public-read-sanitization-policy","version":1}
```

```text
POLICY_DOMAIN = phase8-public-read-sanitization-policy-v1
policy_hash = 2a3f36953a7d1020df4d3d5f2471df8767be4f99f23413f9effc38d03ac7b637
```

The hash is `SHA256(ASCII(POLICY_DOMAIN) || 0x00 || UTF8(canonical_policy_manifest))`.

A conforming text is already Unicode NFKC, uses only LF line endings, has no
leading/trailing whitespace, tab or double ASCII space, respects its code-point
limit and matches none of the twelve forbidden regexes. The literal personal
identifier coverage is: e-mail, E.164 phone, Brazilian local/national phone
(mobile or landline, compact or punctuated), CPF, payment-card PAN and random
UUID-format payment key. CNPJ remains a business identifier and is not treated
as personal data by this text policy. Empty text is rejected. The policy is
reject-only: it never deletes or rewrites a matched substring and then calls
the remainder safe.

The normative fixture carries three accepted probes and nine rejected probes.
The rejected set includes `(75) 99999-9999`, `75999999999`, `99999-9999`,
`3333-4444`, `+55 (75) 99999-9999`, compact E.164, CPF, PAN and a UUID-format
payment key. Every implementation compiles the exact manifest expressions and
must reproduce every probe outcome before it can emit `public_safe`.

This policy is applied to `answer_text` and every `public_label`. Dates, prices, IDs and other structured claim fields remain typed and are not inferred from text.

### 6.8 ReadEvidenceReceipt

Ordered fields:

```text
ReadEvidenceReceipt(
    receipt_id,
    request_hash,
    result_content_hash,
    source_evidence_hash,
    policy_id,
    policy_hash,
    disposition,
    observed_at,
    expires_at,
)
```

Identity:

```text
SCHEMA = phase8-read-evidence-receipt
VERSION = 1
DOMAIN = phase8-read-evidence-receipt-v1
RECEIPT_ID_DOMAIN = phase8-read-evidence-receipt-id-v1
SOURCE_EVIDENCE_DOMAIN = phase8-read-source-evidence-v1
RESULT_CONTENT_DOMAIN = phase8-read-result-content-v1
```

`ReadEvidenceDisposition` is exactly:

```text
public_safe | private_only
```

`policy_id` and `policy_hash` equal section 6.7. `observed_at` and `expires_at` satisfy `UTC_DATETIME`, with `expires_at > observed_at`.

```text
source_evidence_hash =
  SHA256(ASCII(SOURCE_EVIDENCE_DOMAIN) || 0x00 || canonical_owner_evidence_bytes)

result_content_hash =
  SHA256(ASCII(RESULT_CONTENT_DOMAIN) || 0x00 || result_content_preimage_bytes)
```

`result_content_preimage_bytes` is each result's canonical envelope with schema suffixed `-content-preimage`, version `1`, and all result data fields except `evidence_receipt`.

The receipt ID is `"read-evidence:" +` the SHA-256 of `RECEIPT_ID_DOMAIN || 0x00 ||` its canonical ID preimage containing all fields after `receipt_id`.

The read-adapter owner exposes:

```text
ReadEvidenceStore.get(receipt_id) -> canonical_receipt_bytes | NotFound
```

The owner atomically persists the receipt with the canonical owner evidence bytes used by the projection. Parent acceptance requires byte identity, all formulas, request/result binding, current time before `expires_at`, and exact policy ID/hash. A caller-created receipt absent from the store is rejected.

`public_safe` means both that the literal policy passed and that the typed claim is backed by the owner evidence bound to the request. `private_only` means the sanitized content may be shown to Maya but cannot support a public claim.

## 7. Sanitized read-result union

`SanitizedReadResult` is exactly:

```text
FoundSnapshot |
ProvenAbsent |
LegacyUnavailable |
SanitizedKnowledgeResult |
SanitizedLookupResult
```

Every nested contract uses its complete envelope under section 3.3.

### 7.1 Genesis variants

```text
FoundSnapshot(genesis_receipt, projection)
ProvenAbsent(genesis_receipt)
LegacyUnavailable(genesis_receipt)
```

Identities:

```text
phase8-found-snapshot / phase8-found-snapshot-v1
phase8-proven-absent / phase8-proven-absent-v1
phase8-legacy-unavailable / phase8-legacy-unavailable-v1
```

Rules:

- `genesis_receipt` is a full nested `LegacyGenesisReceipt` with matching status;
- `FoundSnapshot.projection` is a full nested `ConversationProjection` whose hash equals the receipt projection hash;
- all three require successful section 6.6 owner verification;
- none is safe for a public commercial claim;
- `LegacyUnavailable` is never equal or convertible to `ProvenAbsent`.

### 7.2 SanitizedKnowledgeResult

Ordered fields:

```text
SanitizedKnowledgeResult(
    request_hash,
    source,
    subject_id,
    locale,
    answer_text,
    evidence_receipt,
)
```

Identity:

```text
SCHEMA = phase8-sanitized-knowledge-result
VERSION = 1
DOMAIN = phase8-sanitized-knowledge-result-v1
```

`KnowledgeSource` is exactly:

```text
faq | lodging_description | activity_description
```

Rules:

- `faq` requires `subject_id is None`;
- description sources require `subject_id` matching `INTERNAL_ID`;
- `locale` satisfies `LOCALE`;
- `answer_text` satisfies section 6.7 and the 4096-code-point limit;
- `evidence_receipt` is a full nested `ReadEvidenceReceipt` whose request/content hashes match;
- no provider body, provider reference, URL response, untyped metadata or arbitrary nested JSON is carried.

### 7.3 SanitizedOffer

Ordered fields:

```text
SanitizedOffer(offer_id, service, public_label, start_date, end_date,
               start_time, adults, children, total_amount, currency)
```

Identity:

```text
SCHEMA = phase8-sanitized-offer
VERSION = 1
DOMAIN = phase8-sanitized-offer-v1
```

`ReadService` is exactly:

```text
lodging | activity
```

Rules:

- `offer_id` satisfies `OFFER_ID` and equals `reservation_lookup.identity.offer_id_for` on the owner `OfferSnapshot` before `provider_ref` is stripped;
- `public_label` satisfies section 6.7 and the 256-code-point limit;
- lodging requires `end_date > start_date`;
- activity requires `end_date is None`;
- `start_time` is null or satisfies `TIME`;
- `adults >= 1`, `children >= 0` and both reject bool;
- `total_amount` satisfies `POSITIVE_DECIMAL_2`;
- `currency` satisfies `CURRENCY`;
- `provider_ref`, raw provider payload and transport metadata are excluded.

### 7.4 SanitizedLookupResult

Ordered fields:

```text
SanitizedLookupResult(
    request_hash,
    service,
    status,
    query_signature,
    lookup_id,
    observed_at,
    expires_at,
    snapshot_hash,
    offers,
    failure_codes,
    evidence_receipt,
)
```

Identity:

```text
SCHEMA = phase8-sanitized-lookup-result
VERSION = 1
DOMAIN = phase8-sanitized-lookup-result-v1
```

`SanitizedLookupStatus` is exactly:

```text
positive | negative | uncertain
```

`LookupFailureCode` is exactly the current adapter output universe:

```text
transport_error | http_error | schema_error
```

Rules:

- `query_signature` satisfies `SHA256` and equals `SearchQuery.signature`;
- `lookup_id` satisfies `LOOKUP_ID` and equals `reservation_lookup.identity.lookup_id_for` on owner evidence;
- `snapshot_hash` equals the exact `LookupProvenance.snapshot_hash`;
- UTC times satisfy `expires_at > observed_at`;
- `offers` is an exact tuple sorted by `offer_id`, with unique IDs and service matching the result;
- `failure_codes` is an exact sorted tuple of unique enum members;
- positive: at least one offer and zero failure codes;
- negative: zero offers and zero failure codes;
- uncertain: zero offers and at least one failure code;
- the exact current `LookupResult` is fully revalidated before projection;
- `evidence_receipt` binds the source `LookupResult`, request and projected content;
- `provider_ref`, `LookupFailure.detail`, request/response bodies and transport paths do not cross the projection.

### 7.5 Tool/request/result equality matrix

Parent acceptance applies this exact matrix:

| Request | Required result | Required equalities |
|---|---|---|
| FAQ | knowledge/`faq` | request hash; locale; null subject |
| lodging availability | lookup/`lodging` | request hash; check-in/start date; check-out/end date; adults; children; null start time; query signature |
| room description | knowledge/`lodging_description` | request hash; locale; subject = room offer ID |
| activity availability | lookup/`activity` | request hash; activity date/start date; null end date/time; adults = participants; children = 0; query signature |
| activity description | knowledge/`activity_description` | request hash; locale; subject = activity ID |
| legacy genesis | one genesis variant | request hash and lead hash through owner receipt; found projection hash |

A result from another tool, lead, locale, subject, service, date, party, query, projection or request is rejected even if all individual hashes are syntactically valid.

## 8. ReadObservation

### 8.1 Fields and identity

Ordered fields remain the approved registry fields:

```text
ReadObservation(request_bytes, request_hash, status, typed_result_bytes,
                result_hash, derived_facts, safe_for_public_claims,
                frame_commitment_hash)
```

Identity:

```text
SCHEMA = phase8-read-observation
VERSION = 1
DOMAIN = phase8-read-observation-v1
```

`request_bytes` and `typed_result_bytes` use section 3.2. The first strict-decodes to one `Phase8ReadRequest`; the second to one `SanitizedReadResult`. Both are re-encoded byte-identically.

`request_hash` uses section 6.4. `result_hash` equals the nested result's domain-separated canonical hash, not an unscoped hash of arbitrary bytes.

`frame_commitment_hash` satisfies `SHA256` and points to the accepted response frame, never to the request being hashed and never to itself.

`derived_facts` is an exact catalog-ordered tuple of v8 `TypedFact`. Names are unique and every fact frame hash equals the observation frame hash.

### 8.2 Status and public-safety matrix

`ReadObservationStatus` is exactly:

```text
found_snapshot |
proven_absent |
legacy_unavailable |
answered |
positive |
negative |
uncertain
```

The exact matrix is:

| Result | Status | `safe_for_public_claims` |
|---|---|---|
| `FoundSnapshot` | `found_snapshot` | `False` |
| `ProvenAbsent` | `proven_absent` | `False` |
| `LegacyUnavailable` | `legacy_unavailable` | `False` |
| knowledge + receipt `public_safe` | `answered` | `True` |
| knowledge + receipt `private_only` | `answered` | `False` |
| positive lookup + receipt `public_safe` | `positive` | `True` |
| positive lookup + receipt `private_only` | `positive` | `False` |
| negative lookup + receipt `public_safe` | `negative` | `True` |
| negative lookup + receipt `private_only` | `negative` | `False` |
| uncertain lookup | `uncertain` | `False` |

Parent `accept_read_observation(...)` recomputes this matrix after:

1. strict request/result decoding;
2. current turn/lead/projection/source/deadline validation;
3. section 7.5 equality checks;
4. exact owner-store receipt verification;
5. policy/content/result hash checks;
6. evidence freshness at acceptance time;
7. frame and derived-fact binding.

The public-safety bool is rejected if caller-selected differently. A datatype constructor alone does not confer owner provenance or permission to make a public claim.

`ProvenAbsent` says only that no legacy genesis existed in the bound source snapshot. It is not a commercial availability claim.

## 9. Data flow

### 9.1 Existing-state turn

1. Parent loads exact `BoundaryState`.
2. Parent verifies its non-recursive relay-backed `ConversationProjection`.
3. Parent constructs a private Maya request from the exact projection and current turn bindings.
4. Every accepted state-commit fact receives the accepted response-frame commitment.
5. A legacy fact without a frame hash cannot cross the v8 path.

### 9.2 Legacy genesis

1. Parent reaches genesis only after owner-produced `StateNotFound`.
2. Importer performs one classified scan and persists `LegacyGenesisReceipt` in its owner store.
3. Exactly one genesis result variant is built from that receipt.
4. Parent accepts it only after byte-identical owner-store lookup.
5. Unavailable, timeout, malformed and identity conflict never become empty genesis.

### 9.3 FAQ and description read

1. Parent accepts exact `Phase8ToolReadRequest` through the gateway.
2. Read adapter obtains owner evidence and projects text under section 6.7.
3. Adapter atomically persists `ReadEvidenceReceipt` with its canonical evidence.
4. Parent constructs `SanitizedKnowledgeResult` and accepts its observation through section 8.2.
5. Only a verified `public_safe` receipt may support public text.

### 9.4 Availability read

1. Parent accepts exact typed request.
2. Adapter produces and fully validates exact existing `LookupResult`.
3. Projection removes `provider_ref`, failure detail and raw transport bodies.
4. Adapter persists an evidence receipt binding request, source result and sanitized content.
5. Positive/negative claims require a fresh `public_safe` receipt; uncertain is always private-only.

## 10. Error handling

All mismatches fail before durable turn commit:

- unknown fact name, wrong slot type, legacy fact entering v8 or v8 fact downgrade;
- duplicate fact or service;
- recursive, malformed or state-divergent reservation execution projection;
- non-canonical Base64 or nested envelope;
- arbitrary request bytes or wrong tool/argument pair;
- result schema not in the union;
- request/result/frame/content/evidence hash mismatch;
- request/result equality-matrix mismatch;
- fabricated or missing owner receipt;
- stale evidence;
- caller-selected public-safety mismatch;
- PII, URL, markup, provider ref, secret marker or forbidden control in public text;
- raw provider body, failure detail or transport metadata in a sanitized result;
- unknown failure code;
- duplicate/unknown JSON key, bool-as-int, float or cross-domain bytes.

Failure creates no state, command, relay, internal job, public chunk, delivery, provider call or memory write.

## 11. Test design

Implementation remains split into small RED/GREEN units.

### Unit A — TypedFact compatibility and v8 wire

Focused selectors prove:

- historical two-argument construction;
- exact v7 two-field wire and v8 downgrade rejection;
- exact six-name catalog/value mapping;
- frame backlink required for v8;
- bool-as-int, free-form name, wrong slot, duplicate and cross-domain rejection.

After the implementation bytes change, run unconditionally:

```text
tests.test_phase7_serialization
tests.test_phase7_types
tests.test_phase7_dispatch
```

Do not rerun them again unless relevant bytes change.

### Unit B — finite projections

Focused selectors prove:

- exact fields/enums/order;
- strict relay-bundle decode and binding hash;
- parent workflow equals bundle expected final state;
- null/workflow matrix;
- absence of `BoundaryState` or `ConversationProjection` inside the bundle;
- service/fact duplicates and hostile nested input rejection.

### Unit C — typed request union

Focused selectors prove:

- five exact tool/argument pairs plus genesis;
- lead/projection/source/deadline/locale bindings;
- arbitrary bytes, sixth tool, wrong pair and non-canonical nested value rejection.

### Unit D — genesis receipt and tri-state

Focused selectors prove:

- exact receipt ID/hash formulas and status matrix;
- owner-store byte identity;
- found lead/projection binding;
- zero-row proof cannot be fabricated from `None`;
- malformed/timeout cannot become absence;
- unavailable remains distinct.

### Unit E — sanitization receipt and knowledge results

Focused selectors prove:

- exact policy manifest/hash;
- owner-store byte identity, request/content/evidence binding and freshness;
- FAQ/description subject and locale matrix;
- PII, URL, markup, provider ref, secret marker and evidence swapping rejection;
- `private_only` cannot become publicly safe.

### Unit F — lookup projection

Focused selectors prove:

- existing `LookupResult` revalidation;
- query/lookup/offer formulas and request equality;
- exact three-code failure catalog and status cardinalities;
- raw body/provider ref/failure detail exclusion;
- positive/negative/uncertain public-safety matrix.

### Unit G — ReadObservation

Focused selectors prove:

- standard-Base64 request/result encoding;
- exact union decode and known-answer hashes;
- owner verification before acceptance;
- fixed status/public-safety matrix;
- derived facts share the response frame;
- lead/locale/subject/service/date/party/query/evidence swapping rejection.

### Unit H — fixture and registry

The implementation must reproduce `tests/fixtures/phase8_facts_reads_wire_v1.json` byte-for-byte and then add the contracts to the complete Task 1 wire v8 fixture. Unknown, missing or extra contract/member fails closed.

No heavy suite runs during these units. The complete Task 1 module set and proportional Phase 7 regressions run once on the later frozen Task 1 candidate, except the mandatory three-module regression immediately after `TypedFact` changes.

## 12. Documentation and execution gate

This revised delta becomes executable only after:

1. its new commit, tree, blob and companion fixture hashes are recorded;
2. a focused recheck authenticates only residual F3/F4 against that exact
   identity, preserves the prior closure of F1/F2/F5, and reports no open
   Critical/Important finding;
3. Carlos explicitly accepts that exact identity for implementation;
4. the approved replacement plan records the identity as the facts/reads closure authority without changing any architectural invariant.

Until all four conditions hold, no implementation of these contracts is authorized. Already committed literal Task 1 micro-units remain unchanged.
