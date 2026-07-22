# Phase 8 Facts and Reads Wire Closure Design

**Status:** Candidate design delta approved by Carlos Eduardo for documentation on 2026-07-22. It is not executable implementation authority until its committed identity is reviewed and explicitly accepted.

**Scope:** Close only the Phase 8 wire for `TypedFact`, `ConversationProjection`, the sanitized read-result union, and `ReadObservation`.

**Base architectural authority:**

- approved Phase 8 design at commit `2889e9ec08f466bbb16a30e4bb5c9a098daf54d3`;
- approved replacement plan and Implementation Closure Registry at commit `2b60474bc441b872a150dc738f90084e06a4cc8e`;
- current implementation baseline before this delta: `6b9d6963be86ec327bd5c71d20c76cf61643c3e8`.

This document refines missing literal wire details. It does not alter the architectural invariants, effect ownership, capability boundaries, table universes, FSMs, cardinalities, build gates, canary gates, or rollout gates of the approved Phase 8 design.

## 1. Goal

Create a closed, versioned, hostile-input-safe wire for facts and read results without breaking the internal Phase 7 `TypedFact(name, value)` constructor before the Phase 8 kernel and dispatch migration reaches its own task.

The result must guarantee:

1. no free-form fact crosses the v8 wire;
2. no raw provider response crosses the v8 wire;
3. legacy genesis absence remains distinct from legacy unavailability;
4. a caller cannot mark an unsafe result as safe for a public claim;
5. every v8 fact and observation binds to exactly one transcript frame commitment;
6. Phase 7 facts without a frame backlink remain internal-only and cannot serialize as v8.

## 2. Non-goals

This delta does not define or modify:

- `NormalizedToolProposal`;
- `LearningProposal`;
- `PublicReplyChunk`;
- `MayaTurnProposal`;
- `TurnReceipt` or any other receipt;
- `CapabilityPolicy` or deployment bindings;
- allocations, authorities, workers, relay execution, delivery, providers, network, Docker, build, canary, or rollout;
- durable Boundary schema v8;
- the catalog or authorization behavior of Task 2 `ToolDispatch`.

No runtime or provider repository is modified by this design.

## 3. Canonical wire rules

Every type introduced or refined here has:

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

A canonical hash is:

```text
SHA256(DOMAIN || 0x00 || to_canonical_bytes())
```

Strict decoding rejects:

- duplicate JSON keys;
- missing or unknown keys at every object level;
- `bool` where `int` is required;
- every `float`;
- mutable nested input;
- non-canonical date, datetime, decimal, locale, identifier, or SHA-256 text;
- unknown enum values;
- bytes from a different schema/domain;
- a decoded value whose re-encoding is not byte-identical.

Tuples are encoded as JSON arrays but decoded only into exact tuples. Ordering is semantic and deterministic. A missing field and an empty tuple are never equivalent.

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

- `to_canonical_bytes()` must reject it;
- `canonical_hash()` must reject it;
- `ConversationProjection` must reject it;
- `ReadObservation.derived_facts` must reject it;
- the v8 serializer must reject it.

This compatibility exception is temporary and local to construction of the existing Phase 7 type. It is not a nullable v8 wire field. In v8 canonical bytes, `frame_commitment_hash` is always present and is a lowercase SHA-256.

### 4.2 Fact catalog

The v8 catalog contains exactly six names and exact value variants:

| Fact name | Exact value type |
|---|---|
| `language` | `StringSlot` |
| `service` | `StringSlot` |
| `start_date` | `DateSlot` |
| `end_date` | `DateSlot` |
| `adults` | `IntegerSlot` |
| `children` | `IntegerSlot` |

No availability, offer, FAQ answer, description text, provider status, payment proof, reservation identity, or arbitrary slot name is a v8 fact in this delta. Those values remain in sanitized typed read results or in later owner-specific contracts.

`service` is restricted to the exact values `hostel` and `agency`. `language` must satisfy the existing canonical locale rule. `adults >= 1`; `children >= 0`; dates are exact `date`, never `datetime`.

V8 fact ordering is the table order above. Duplicate names are rejected.

### 4.3 TypedFact wire identity

```text
SCHEMA = phase8-typed-fact
VERSION = 1
DOMAIN = phase8-typed-fact-v1
```

The exact v8 data fields are:

```text
name
value = {kind, value}
frame_commitment_hash
```

The closed `kind` values are `string`, `integer`, and `date`. Date values use canonical ISO `YYYY-MM-DD`.

## 5. ConversationProjection

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

### 5.2 Closed stage and service universes

`ConversationStage` contains exactly:

```text
recepcionista | hostel | agencia | fechamento | handoff | no_reply
```

`DesiredService` contains exactly:

```text
hostel | agency
```

`desired_services` is an exact tuple, may be empty, contains no duplicates, and is normalized in the fixed order `hostel`, then `agency`.

`locale` uses the existing `xx` or `xx-YY` canonical rule.

`facts` is an exact tuple of v8-wire-ready `TypedFact` values. Every fact has a non-null frame commitment hash, names are unique, and ordering follows the fact catalog. A projection may contain zero facts.

### 5.3 Reservation execution projection

`reservation_execution_projection` is exact non-empty `bytes` containing the canonical wire JSON of an exact `BoundaryState` accepted by the current owner serializer.

Validation is:

1. decode with the owner `reservation_boundary.serialization` decoder into exact `BoundaryState`;
2. reject unknown schema/type/field, duplicate key, unsupported union member, and non-canonical scalar;
3. re-encode with the same owner serializer;
4. require byte equality with the supplied bytes.

This field never accepts `ReadResponse.body`, provider JSON, a generic mapping, or a caller-created projection dict. It follows the owner serializer as Boundary moves from v7 to v8; its field shape does not change merely because the durable Boundary schema version advances in Task 3.

The field may contain private owner state required for the private Maya turn. It is not a public reply artifact and is never copied into a `LearningProposal`.

## 6. Sanitized read-result union

`SanitizedReadResult` is the closed union:

```text
FoundSnapshot |
ProvenAbsent |
LegacyUnavailable |
SanitizedKnowledgeResult |
SanitizedLookupResult
```

Every variant has independent schema/domain identity and byte-identical strict round-trip.

### 6.1 FoundSnapshot

Ordered fields:

```text
FoundSnapshot(lead_key_hash, projection_bytes, projection_hash,
              source_snapshot_hash)
```

Identity:

```text
SCHEMA = phase8-found-snapshot
DOMAIN = phase8-found-snapshot-v1
```

Rules:

- all three hash fields are lowercase SHA-256;
- `projection_bytes` decodes to exact `ConversationProjection`;
- `projection_hash` equals the projection canonical hash;
- `source_snapshot_hash` authenticates the legacy source snapshot and is not substituted by `projection_hash`.

### 6.2 ProvenAbsent

Ordered fields:

```text
ProvenAbsent(lead_key_hash, zero_scan_hash)
```

Identity:

```text
SCHEMA = phase8-proven-absent
DOMAIN = phase8-proven-absent-v1
```

Both fields are lowercase SHA-256. `ProvenAbsent` can be constructed only from an owner-produced zero-scan receipt; a simple `None`, missing row, timeout, malformed snapshot, or exception cannot produce it.

### 6.3 LegacyUnavailable

Ordered fields:

```text
LegacyUnavailable(lead_key_hash, reason, failure_hash)
```

Identity:

```text
SCHEMA = phase8-legacy-unavailable
DOMAIN = phase8-legacy-unavailable-v1
```

`LegacyUnavailableReason` contains exactly:

```text
timeout | transport_error | malformed | unsupported_schema | identity_conflict
```

Both hashes are lowercase SHA-256. This variant is never equivalent to `ProvenAbsent` and is never safe for a public claim.

### 6.4 SanitizedKnowledgeResult

Ordered fields:

```text
SanitizedKnowledgeResult(source, subject_id, locale, answer_text,
                         evidence_hash)
```

Identity:

```text
SCHEMA = phase8-sanitized-knowledge-result
DOMAIN = phase8-sanitized-knowledge-result-v1
```

`KnowledgeSource` contains exactly:

```text
faq | lodging_description | activity_description
```

Rules:

- `faq` requires `subject_id is None`;
- description sources require a canonical non-empty internal `subject_id`;
- `locale` is canonical;
- `answer_text` is exact non-empty text, has no forbidden control characters, and is at most 4096 Unicode code points;
- `evidence_hash` is lowercase SHA-256;
- no provider payload, URL fetch response, untyped metadata, or arbitrary nested JSON is carried.

### 6.5 SanitizedOffer

Ordered fields:

```text
SanitizedOffer(offer_id, service, public_label, start_date, end_date,
               start_time, adults, children, total_amount, currency)
```

Identity:

```text
SCHEMA = phase8-sanitized-offer
DOMAIN = phase8-sanitized-offer-v1
```

Rules:

- `service` is `hostel` or `agency`;
- `offer_id` is the canonical internal opaque offer ID;
- `public_label` is normalized non-empty text, at most 256 code points;
- hostel requires `end_date > start_date`;
- agency requires `end_date is None`;
- `start_time` is `None` or canonical `HH:MM`;
- `adults >= 1`, `children >= 0`;
- `total_amount` is a positive canonical two-decimal string;
- `currency` is three uppercase ASCII letters;
- `provider_ref`, raw provider payload, and lookup transport metadata are excluded.

### 6.6 SanitizedLookupResult

Ordered fields:

```text
SanitizedLookupResult(service, status, query_signature, lookup_id,
                      observed_at, expires_at, snapshot_hash, offers,
                      failure_codes)
```

Identity:

```text
SCHEMA = phase8-sanitized-lookup-result
DOMAIN = phase8-sanitized-lookup-result-v1
```

`SanitizedLookupStatus` contains exactly:

```text
positive | negative | uncertain
```

Rules:

- `service` is `hostel` or `agency`;
- query, lookup, and snapshot identities are canonical and lower-level evidence remains owner-authenticated;
- `observed_at` and `expires_at` are exact canonical UTC datetimes and `expires_at > observed_at`;
- `offers` is an exact tuple of `SanitizedOffer`, sorted by `offer_id`, with unique IDs and service matching the result;
- `failure_codes` is an exact sorted tuple of unique closed identifiers;
- positive: at least one offer and zero failure codes;
- negative: zero offers and zero failure codes;
- uncertain: zero offers and at least one failure code.

The adapter from existing `reservation_lookup.LookupResult` revalidates the source result and copies only these fields. It never serializes `ReadResponse.body`, provider request/response bodies, or `provider_ref`.

## 7. ReadObservation

### 7.1 Fields and identity

Ordered fields remain the approved plan fields:

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
READ_REQUEST_DOMAIN = phase8-read-request-v1
```

`request_hash` is:

```text
SHA256(READ_REQUEST_DOMAIN || 0x00 || request_bytes)
```

`typed_result_bytes` must strict-decode to exactly one `SanitizedReadResult` variant. `result_hash` equals that variant's canonical hash, not an unscoped hash of arbitrary bytes.

`frame_commitment_hash` is lowercase SHA-256.

`derived_facts` is an exact tuple of v8-wire-ready `TypedFact` values. Names are unique, catalog-ordered, and every fact's `frame_commitment_hash` equals the observation's `frame_commitment_hash`.

### 7.2 Status enum and derived matrix

`ReadObservationStatus` contains exactly:

```text
found_snapshot |
proven_absent |
legacy_unavailable |
answered |
positive |
negative |
uncertain
```

The type and status matrix is fixed:

| Result variant | Required status | Required `safe_for_public_claims` |
|---|---|---:|
| `FoundSnapshot` | `found_snapshot` | `False` |
| `ProvenAbsent` | `proven_absent` | `False` |
| `LegacyUnavailable` | `legacy_unavailable` | `False` |
| `SanitizedKnowledgeResult` | `answered` | `True` |
| `SanitizedLookupResult(status=positive)` | `positive` | `True` |
| `SanitizedLookupResult(status=negative)` | `negative` | `True` |
| `SanitizedLookupResult(status=uncertain)` | `uncertain` | `False` |

The constructor recomputes and validates this matrix. A caller cannot override it.

`ProvenAbsent` means only that no legacy genesis exists for the bound lead. It is not a commercial availability claim.

## 8. Data flow

### 8.1 Existing-state turn

1. Parent loads exact `BoundaryState`.
2. Parent creates `ConversationProjection` from typed owner state.
3. Parent sends only projection canonical bytes in the private Maya request.
4. Every accepted state-commit fact receives the current frame commitment hash.
5. Legacy Phase 7 facts without a frame hash cannot cross this path.

### 8.2 Legacy genesis read

1. Parent performs the owner-controlled lookup/zero scan.
2. Exactly one of `FoundSnapshot`, `ProvenAbsent`, or `LegacyUnavailable` is constructed.
3. Parent wraps the variant in `ReadObservation`.
4. Unavailable or malformed input never creates empty genesis.

### 8.3 FAQ and description read

1. Parent executes only the read adapter.
2. Adapter projects the response into `SanitizedKnowledgeResult`.
3. Parent persists a canonical `ReadObservation(status=answered, safe=True)`.
4. No generic provider/body mapping survives the projection.

### 8.4 Availability read

1. Parent validates exact existing `LookupResult`.
2. Adapter creates `SanitizedLookupResult` and strips `provider_ref` and transport bodies.
3. Observation status/safety is derived from the lookup status.
4. Only positive and proven negative availability are public-safe; uncertain is not.

## 9. Error handling

All mismatches fail before durable turn commit:

- unknown fact name or wrong slot type;
- legacy fact entering v8 wire;
- duplicate fact name;
- malformed/non-canonical Boundary projection;
- read-result schema not in the union;
- result bytes/hash mismatch;
- request bytes/hash mismatch;
- observation status/result mismatch;
- caller-selected public safety mismatch;
- fact/observation frame mismatch;
- raw provider payload or provider reference in a sanitized result;
- duplicate/unknown JSON key or cross-domain bytes.

Failure does not create state, command, relay, internal job, public chunk, delivery, provider call, or memory write.

## 10. Test design

Implementation remains split into small RED/GREEN units.

### Unit A — TypedFact compatibility and v8 wire

Focused selectors prove:

- existing Phase 7 two-argument construction still succeeds;
- legacy facts cannot serialize as v8;
- exact six-name catalog and value mapping;
- frame backlink required for v8;
- bool-as-int, free-form name, wrong slot type, duplicate and cross-domain rejection.

Blast radius: new selectors plus `tests.test_phase7_types` and `tests.test_phase7_dispatch` only if relevant bytes in `types.py` or `dispatch.py` changed.

### Unit B — ConversationProjection

Focused selectors prove:

- exact fields/enums/order;
- strict BoundaryState canonical round-trip;
- service/fact duplicates and unknown stage rejection;
- nested mutable/raw mapping rejection.

Blast radius: projection selectors plus Phase 7 serialization only if serializer bytes changed.

### Unit C — legacy tri-state

Focused selectors prove:

- `LegacyUnavailable != ProvenAbsent`;
- source/projection/zero-scan/failure hashes bind;
- malformed/timeout cannot produce absence;
- exact schema/domain round-trip.

### Unit D — knowledge and lookup results

Focused selectors prove:

- five READ tools map into only the two sanitized families;
- raw body/provider ref never appears;
- status cardinalities and time bounds;
- hostile nested input and unknown enum rejection.

### Unit E — ReadObservation

Focused selectors prove:

- request/result/frame hashes;
- exact union decode;
- fixed status/public-safety matrix;
- derived facts share the frame;
- no free-form status/fact/result;
- cross-domain and byte-divergent retry rejection.

No heavy suite runs during these units. The four Task 1 modules and Phase 7 serialization/types regression run only on the later complete frozen Task 1 candidate, as required by the approved plan.

## 11. Documentation and execution gate

This delta becomes executable only after:

1. commit identity and blob hash are recorded;
2. focused review authenticates this exact commit and reports no Critical/Important finding;
3. Carlos explicitly accepts that identity for implementation;
4. the approved replacement plan records this delta identity as the facts/reads closure authority without changing any architectural invariant.

Until all four conditions hold, no implementation of the contracts in this delta is authorized. The already committed literal Task 1 micro-units remain unchanged.
