# Phase 3 Lookup Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar adapters read-only Cloudbeds/Bókun que convertem responses HTTP sanitizadas em `LookupEvidence` e `OfferSnapshot` selecionáveis somente por `offer_id` opaco e fresco.

**Architecture:** Um package `reservation_lookup` recebe requests tipados e um `ReadTransport` obrigatório, constrói somente GETs, normaliza dois responses por provider e retorna `LookupResult` imutável. Identidade, seleção e revalidação são funções puras; rede real, auth, provider SDK e legado permanecem ausentes.

**Tech Stack:** Python 3.12 stdlib, `dataclasses`, `Protocol`, `unittest`, JSON sintético, SHA-256, GitHub Actions.

## Global Constraints

- Repositório: `/home/ubuntu/agente-v2`, branch `main`.
- Legado `/home/ubuntu/chapada-leads-hermes` é somente leitura e nunca importado.
- Nenhuma rede, credencial, provider live, write, Docker, banco ou deploy.
- Fixtures exclusivamente sintéticas/sanitizadas.
- TDD obrigatório: teste RED observado antes de cada implementação.
- `offer_id` exclui label/provenance e inclui todo campo executável.
- Somente `Party(adults, children)`; categorias adicionais falham, não são colapsadas.
- Validadores das Fases 0–2 permanecem verdes.

---

### Task 1: Tipos fechados, transport protocol e identidade opaca

**Files:**
- Create: `reservation_lookup/types.py`
- Create: `reservation_lookup/identity.py`
- Create: `reservation_lookup/__init__.py`
- Test: `tests/test_phase3_lookup_types.py`
- Evidence: `docs/refactor/evidence/phase-03/red-result-types.json`

**Interfaces:**
- Consumes: `reservation_domain.SearchQuery`, `LookupEvidence`, `OfferSnapshot`.
- Produces: `ProviderKind`, `ReadRequest`, `ReadResponse`, `ReadTransport`, `LookupFailure`, `LookupProvenance`, `LookupResult`, `CloudbedsLookupRequest`, `BokunLookupRequest`, `offer_id_for`, `lookup_id_for`, `snapshot_hash_for`.

- [ ] **Step 1: Write the failing tests**

Tests must instantiate exact immutable types and assert:

```python
request = ReadRequest(
    method="GET",
    path="/api/v1.3/getAvailableRoomTypes",
    query=(("adults", "2"), ("children", "0")),
)
self.assertEqual(request.method, "GET")
with self.assertRaises(ValueError):
    ReadRequest(method="POST", path="/write", query=())
with self.assertRaises(ValueError):
    ReadRequest(method="GET", path="https://provider.invalid/x", query=())
```

Create two `OfferSnapshot` values equal except for `public_label` and assert
`offer_id_for` is equal. Mutate each of provider ref, service, dates, time,
party, total, currency and availability independently; each resulting ID must
differ. Assert exchange order and JSON key order do not change
`snapshot_hash_for`, while swapping a response between endpoints changes it.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase3_lookup_types -v
```

Expected: non-zero, `ModuleNotFoundError: reservation_lookup`.

- [ ] **Step 3: Implement minimal closed types**

Required signatures:

```python
class ReadTransport(Protocol):
    def send(self, request: ReadRequest) -> ReadResponse: ...

@dataclass(frozen=True, slots=True)
class LookupResult:
    query: SearchQuery
    evidence: LookupEvidence
    provenance: LookupProvenance
    offers: tuple[OfferSnapshot, ...]
    failures: tuple[LookupFailure, ...] = ()
```

Validate IDs with the same opaque-ID grammar as the domain, UTC timestamps,
SHA-256 lowercase, unique sorted offers, status/offer/failure consistency and
TTL through evidence.

Canonical offer projection:

```python
{
    "provider": provider.value,
    "provider_ref": offer.provider_ref,
    "service": offer.service.value,
    "start_date": offer.start_date.isoformat(),
    "end_date": offer.end_date.isoformat() if offer.end_date else None,
    "start_time": offer.start_time,
    "party": {"adults": offer.party.adults, "children": offer.party.children},
    "total": {"amount": format(offer.total.amount, "f"), "currency": offer.total.currency},
    "available": offer.available,
}
```

- [ ] **Step 4: Run GREEN and regressions**

```bash
python3 -m unittest tests.test_phase3_lookup_types -v
python3 -m unittest tests.test_phase2_domain tests.test_phase2_serialization -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add reservation_lookup tests/test_phase3_lookup_types.py docs/refactor/evidence/phase-03/red-result-types.json
git commit -m "feat(phase-3): add lookup identity contracts"
```

### Task 2: Cloudbeds request boundary and strict normalizer

**Files:**
- Create: `reservation_lookup/cloudbeds.py`
- Create: `tests/test_phase3_cloudbeds_adapter.py`
- Create: `tests/fixtures/phase3/cloudbeds/available-room-types.json`
- Create: `tests/fixtures/phase3/cloudbeds/rate-plans.json`
- Create: negative fixture variants under the same directory.
- Evidence: `docs/refactor/evidence/phase-03/red-result-cloudbeds.json`

**Interfaces:**
- Consumes: Task 1 types and `reservation_domain` value objects.
- Produces: `CloudbedsReadAdapter.lookup(request, observed_at, ttl)`.

- [ ] **Step 1: Write fixture transport and RED contract tests**

Test transport:

```python
class FixtureTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
    def send(self, request):
        self.requests.append(request)
        return self.responses.pop(0)
```

Assert exact request sequence and query, positive result, two response hashes,
no raw body field, deterministic total, private provider ref and opaque ID.
Negative tests: non-2xx, body not object, missing room ID/rate plan/daily rate,
partial stay, zero units, non-finite price, currency mismatch and unexpected
response count. Every structural/provider failure must produce `UNCERTAIN` and
zero offers; valid no-availability produces `NEGATIVE`.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase3_cloudbeds_adapter -v
```

Expected: import error for `reservation_lookup.cloudbeds`.

- [ ] **Step 3: Implement exact GET boundary**

Required request paths and query:

```python
("/api/v1.3/getAvailableRoomTypes", common_query)
("/api/v1.2/getRatePlans", common_query)
```

Normalize only canonical fixture fields. Require exactly one row per requested
night, all daily units positive, referenced rate plan present and one currency.
Build `provider_ref` as
`cloudbeds.property.<property_id>.room.<room_id>.rate.<rate_plan_id>` and compute the opaque ID after
constructing the offer without trusting any provider/LLM option ID.

- [ ] **Step 4: Run GREEN and provider-specific mutation probes**

```bash
python3 -m unittest tests.test_phase3_cloudbeds_adapter -v
```

Temporarily remove the rate-plan membership check and verify the corresponding
test fails; restore and re-run green.

- [ ] **Step 5: Commit**

```bash
git add reservation_lookup/cloudbeds.py tests/test_phase3_cloudbeds_adapter.py tests/fixtures/phase3/cloudbeds docs/refactor/evidence/phase-03/red-result-cloudbeds.json
git commit -m "feat(phase-3): normalize Cloudbeds offers"
```

### Task 3: Bókun request boundary and strict normalizer

**Files:**
- Create: `reservation_lookup/bokun.py`
- Create: `tests/test_phase3_bokun_adapter.py`
- Create: `tests/fixtures/phase3/bokun/activity.json`
- Create: `tests/fixtures/phase3/bokun/availabilities.json`
- Create: negative fixture variants under the same directory.
- Evidence: `docs/refactor/evidence/phase-03/red-result-bokun.json`

**Interfaces:**
- Consumes: Task 1 types and `reservation_domain` value objects.
- Produces: `BokunReadAdapter.lookup(request, observed_at, ttl)`.

- [ ] **Step 1: Write RED contract tests**

Assert exact GET order:

```text
/activity.json/913776?lang=pt_BR&currency=BRL
/activity.json/913776/availabilities?start=2026-10-11&end=2026-10-11&currency=BRL
```

The typed representation stores path and sorted query separately. Assert
metadata ID must equal internal `product_id`; title is presentation only;
provider ref includes product/start/rate IDs; options outside the date, sold
out, unavailable, no start ID/time, non-finite total or wrong currency are not
authorizable. HTTP/schema mismatch is `UNCERTAIN`; valid empty availability is
`NEGATIVE`.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase3_bokun_adapter -v
```

Expected: import error for `reservation_lookup.bokun`.

- [ ] **Step 3: Implement minimal Bókun adapter**

Only `product_id` from `BokunLookupRequest` builds the path. Never accept
`product_name`, label or provider-supplied option ID as authority. Build
`provider_ref` as
`bokun.product.<product>.start.<start>.rate.<rate>` and compute the opaque ID
from canonical offer fields.

- [ ] **Step 4: Run GREEN and mutation probe**

```bash
python3 -m unittest tests.test_phase3_bokun_adapter -v
```

Temporarily allow metadata ID mismatch, verify the test fails, restore and run
green.

- [ ] **Step 5: Commit**

```bash
git add reservation_lookup/bokun.py tests/test_phase3_bokun_adapter.py tests/fixtures/phase3/bokun docs/refactor/evidence/phase-03/red-result-bokun.json
git commit -m "feat(phase-3): normalize Bokun offers"
```

### Task 4: Seleção, revalidação e property gate bilateral

**Files:**
- Create: `reservation_lookup/selection.py`
- Create: `reservation_lookup/properties.py`
- Create: `tests/test_phase3_selection.py`
- Create: `tests/test_phase3_properties.py`
- Create: `scripts/run_phase3_properties.py`
- Evidence: `docs/refactor/evidence/phase-03/property-result.json`
- Evidence: `docs/refactor/evidence/phase-03/mutation-result.json`

**Interfaces:**
- Consumes: `LookupResult` from Tasks 2–3.
- Produces: `select_offer`, `revalidate_offer`, `SelectionRejected`,
  `Phase3PropertyReport`, `run_lookup_properties`.

- [ ] **Step 1: Write RED selection tests**

Assert:

```python
selected = select_offer(result, offer_id=result.offers[0].offer_id, at=NOW)
self.assertEqual(selected, result.offers[0])
```

And fail closed for label, `provider_ref`, random ID, duplicate IDs, negative,
uncertain and expired evidence. Revalidation preserves label-only changes and
rejects price/provider/date/time/party/currency/availability changes with
`offer_changed`.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_phase3_selection -v
```

Expected: import error for `reservation_lookup.selection`.

- [ ] **Step 3: Implement selection minimal**

No fuzzy matching, Unicode label normalization, index lookup or provider-ref
fallback is allowed. Match equality on `offer_id` only and require exactly one.

- [ ] **Step 4: Write and run property RED**

The generator runs at least 50,000 cases with fixed seed. Counters must include:

```text
label_equivalence_cases > 0
executable_mutation_cases > 0
expired_cases > 0
zero_match_cases > 0
multiple_match_cases > 0
false_authorizations = 0
missed_invalidations = 0
```

Run:

```bash
python3 -m unittest tests.test_phase3_properties -v
```

Expected: fail until the runner exists.

- [ ] **Step 5: Implement property runner and CLI gate**

Default CLI gate requires `--cases >= 50000`; lower loads require `--smoke`.
The oracle independently computes whether a mutation is presentation-only or
executable and asserts both authorization and invalidation directions.

- [ ] **Step 6: Execute full gate**

```bash
python3 scripts/run_phase3_properties.py \
  --cases 50000 --seed 20260718 \
  --write docs/refactor/evidence/phase-03/property-result.json
```

Expected: exit 0, all required counters positive, zero violations.

- [ ] **Step 7: Mutation test in temporary copies**

Apply and kill all eight spec mutations. Save exact test, exit code and
`killed=true` for each in `mutation-result.json`.

- [ ] **Step 8: Commit**

```bash
git add reservation_lookup/selection.py reservation_lookup/properties.py tests/test_phase3_selection.py tests/test_phase3_properties.py scripts/run_phase3_properties.py docs/refactor/evidence/phase-03/property-result.json docs/refactor/evidence/phase-03/mutation-result.json
git commit -m "test(phase-3): prove opaque offer selection"
```

### Task 5: Manifest, validator, CI, adversarial review and closeout

**Files:**
- Create: `scripts/generate_phase3_manifest.py`
- Create: `scripts/validate_phase3.py`
- Create: `.github/workflows/phase3.yml`
- Create: `docs/refactor/evidence/phase-03/README.md`
- Create: `docs/refactor/evidence/phase-03/source-map.json`
- Create: `docs/refactor/evidence/phase-03/fixture-manifest.json`
- Create: `docs/refactor/evidence/phase-03/adversarial-review.md`
- Create: `docs/refactor/evidence/phase-03/validation-result.json`
- Create: `docs/refactor/evidence/phase-03/SHA256SUMS`
- Modify: `docs/refactor/06-risk-register.md`
- Modify: phase/index docs at closeout.

**Interfaces:**
- Consumes: all Phase 3 artifacts.
- Produces: reproducible local/CI gate and formal phase closure.

- [ ] **Step 1: Implement manifest generator**

Hash package, fixtures and source-map inputs. Manifest must record fixture
counts per provider, no absolute source paths, and exact 50k property
configuration.

- [ ] **Step 2: Implement validator**

Require tracked/staged files, exact property counters, eight killed mutants,
fixture hashes, source-map symbols, no secret/PII, valid relative links and
forbidden import/call scan. Run Phase 0–2 validators first.

- [ ] **Step 3: Add CI**

Workflow order:

```text
validate phases 0–2
regenerate manifest/diff
all unittest
50k property gate/regenerate/diff
validate phase 3
compileall
diff check
```

- [ ] **Step 4: Run adversarial review**

Review independently:

1. request/boundary and sanitization;
2. identity/selection/invalidation;
3. fixtures/property false-green and purity.

Reproduce every actionable finding with RED before changing implementation.
Document blocked/timeout reviewers as non-evidence.

- [ ] **Step 5: Final local gate**

```bash
python3 scripts/validate_phase0.py
PHASE1_LEGACY_SOURCE=/path-not-present-in-ci python3 scripts/validate_phase1.py
python3 scripts/validate_phase2.py
python3 scripts/validate_phase3.py
python3 -m unittest discover -s tests -v
python3 -m compileall -q reservation_domain reservation_lookup characterization scripts tests
git diff --check
```

Also confirm legacy HEAD, status count and status hash unchanged.

- [ ] **Step 6: Publish implementation and verify CI**

```bash
git add .
git diff --cached --check
git commit -m "feat(phase-3): add read-only lookup adapters"
git push origin main
```

Wait for Phase 0–3 workflows on the implementation SHA; all must be success.

- [ ] **Step 7: Close phase in docs and publish closeout**

Mark every deliverable complete, no active phase, Phase 4 eligible but not
started, rollout `NO-GO`. Commit:

```bash
git commit -am "docs(phase-3): close lookup adapters delivery"
git push origin main
git fetch origin main
```

Verify:

```text
HEAD == origin/main == git ls-remote origin refs/heads/main
working tree clean
Phase 0–3 CI success on final SHA
```

## Self-review

- Spec coverage: every design component maps to Tasks 1–5.
- Placeholders: none; every artifact, command and interface has an exact path.
- Type consistency: adapters return `LookupResult`; selection consumes it;
  property/validator consume the same stable counters.
- Scope: no live transport, auth, writes, catalog, confirmation, persistence or
  rollout was added.
