# Maya V2 Group-Enriched Activity Availability — Implementation Plan

> **Execution:** sequential TDD with one implementer and one reviewer per task. No provider writes in tests. Deployment only after all gates are green.

**Goal:** make every Maya V2 activity lookup a single runtime-composed Bókun + group-sheet result, and allow one-person booking on six minimum-two products only when a matching group and a currently valid Bókun group rate exist.

**Architecture:** add a read-only group source and a composite activity adapter around the existing Bókun adapter. Keep `ReadKind.ACTIVITY`, query hashes, and the private field catalog unchanged. Extend the authenticated public offer wire compatibly, and use the same composite resolver in the reservation worker to revalidate solo eligibility before any provider write.

**Source spec:** `docs/superpowers/specs/2026-08-13-maya-v2-group-enriched-availability.md`

---

## Global constraints

- V2 only; never touch `/home/ubuntu/agente-v3`.
- Maya receives no new tool and does not coordinate the spreadsheet call.
- Every activity read attempts both group lookup and Bókun lookup.
- Bókun remains authoritative for executable availability, price, quote, and private binding.
- For 2+ participants, group-source failure degrades to `group_status=unavailable` without hiding a valid Bókun offer.
- For exactly one participant on the six closed minimum-two products, missing/unavailable group evidence hides the offer and blocks private resolve.
- Solo group rates/category IDs must be present in current Bókun metadata/availability and validated through current quote/checkout; never patch a normal binding.
- Solo eligibility is re-read during private binding resolution before any reservation write.
- `ReadRequest.query_hash()` and the closed Bókun private field set remain unchanged.
- No raw rows, names, guide names, comments, source URL, or other spreadsheet content enters model context.
- Old `SanitizedOffer` v1 and `SanitizedLookupResult` records must remain byte-decodable and byte-reserializable.
- Tests must use fakes/fixtures; no live ManyChat messages and no provider writes.

## Task 1: Versioned policy and read-only group source

**Files:**
- Create: `config/v2_activity_group_policy.json`
- Create: `v2_adapters/bokun_groups.py`
- Create: `tests/test_v2_bokun_groups.py`

**RED tests:**

1. Policy loads aliases for exactly all canonical products in the active Bókun product map, plus a separate six-product solo-minimum-two section with expected provider/rate/category IDs.
2. Policy rejects missing/extra canonical products, duplicate normalized aliases, malformed IDs, product-map disagreement, and solo policies outside the closed six-product subset.
3. CSV parser inherits blank dates, handles ISO/Brazilian/month-name dates, aggregates matching participants, and returns `matched` only for exact normalized aliases and exact date.
4. Similar/substring product names do not match.
5. Empty or zero-participant rows do not establish a group.
6. Transport errors, malformed CSV, invalid headers, oversized responses, redirects, non-HTTPS URLs, and timeouts produce typed `unavailable` without raw content leakage.
7. Result objects contain only status, canonical product/date, and aggregate count.

**Implementation:**

- Add immutable policy/result contracts local to the adapter.
- Implement bounded HTTPS CSV fetch with injected client/fetcher for tests.
- Port only the proven date inheritance/normalization behavior from V1.
- Use equality against normalized closed aliases, not substring matching.
- Validate the complete alias policy against the active canonical→Bókun
  product map; validate the solo policy as a six-product subset.

**Gate:** `pytest -q tests/test_v2_bokun_groups.py`

**Commit:** `feat(v2): add closed group sheet read source`

## Task 2: Composite activity adapter and exact solo Bókun rate

**Files:**
- Create: `v2_adapters/group_enriched_activity.py`
- Modify: `v2_adapters/bokun.py`
- Modify: `v2_adapters/provider_http.py`
- Create: `tests/test_v2_group_enriched_activity.py`
- Modify: `tests/test_v2_bokun_party_reads.py`
- Modify: `tests/test_v2_bokun_write_transport.py` only if shared transport fixtures require it

**RED tests:**

1. Every `ACTIVITY` read invokes group source and Bókun once and returns one `ReadObservation` bound to the original request.
2. For 2+, matched/not-matched/unavailable group status enriches the same Bókun public payload while preserving original `offer_id`, private binding hash, price, and availability.
3. A Bókun failure remains a read failure even if a group matched.
4. For one participant on a restricted product:
   - no match/unavailable group returns a non-offer (`available=false`) without quote/write calls;
   - match uses exactly configured rate/category, only when they occur in current Bókun payload;
   - wrong/missing/ambiguous rate/category/currency/capacity fails closed;
   - quote/checkout rejection fails closed;
   - produced offer/binding comes from the validated solo fields.
5. One participant on a non-restricted product keeps normal Bókun behavior.
6. Composite `resolve()` rechecks group and Bókun; disappearance/unavailability blocks before reservation write.
7. For 2+, `resolve()` preserves existing normal behavior and does not make group evidence part of private fields.
8. `query_hash`, offer identity derivation, and private field catalog remain unchanged.

**Implementation:**

- Add explicit optional booking selection to Bókun read/resolve internals, closed to exact configured rate/category.
- Validate rate/category against current metadata and availability before quote.
- Composite adapter calls group source first, always calls Bókun for normal reads, and applies the failure matrix from the spec.
- For restricted solo misses, perform the Bókun availability read without opening quote/checkout and return unavailable to the bridge.
- Composite resolver repeats group lookup and uses the exact solo selection before returning a binding.

**Gate:**

`pytest -q tests/test_v2_group_enriched_activity.py tests/test_v2_bokun_party_reads.py tests/test_v2_bokun_write_transport.py`

**Commit:** `feat(v2): compose group evidence with bokun activity reads`

## Task 3: Authenticated public wire and consultation history

**Files:**
- Modify: `reservation_boundary/reads.py`
- Modify: `v2_application/read_bridge.py`
- Modify: `v2_application/turn_executor.py`
- Modify: `v2_adapters/hermes_model.py` only if its closed JSON validation needs explicit fields
- Modify: `tests/test_v2_read_bridge.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Add/modify focused serialization tests owning `SanitizedOffer`

**RED tests:**

1. New activity offer wire carries exact `group_status`, `existing_group`, `group_participants`, and `solo_group_booking` invariants.
2. Lodging rejects group fields.
3. Contradictory group fields fail closed.
4. A frozen v1 offer/result fixture decodes and reserializes byte-identically.
5. Current model observation contains group fields as part of the same offer/result, not as a second tool result.
6. Consultation history retains the same fields on the next turn.
7. No spreadsheet raw field or source URL appears in model JSON.

**Implementation:**

- Add a v2 `SanitizedOffer` wire with a decoder preserving v1 wire identity.
- Populate fields only from the composite adapter's closed payload.
- Copy closed activity group context into public consultation history.
- Keep lookup/result hashes correctly bound to the new serialized content.

**Gate:**

`pytest -q tests/test_v2_read_bridge.py tests/test_v2_turn_executor.py tests/test_v2_hermes_model_adapter.py tests/test_phase8_serialization.py`

**Commit:** `feat(v2): carry group context through authenticated offer wire`

## Task 4: Settings and productive composition

**Files:**
- Modify: `v2_host/settings.py`
- Modify: `v2_host/production.py`
- Modify: `tests/test_v2_settings.py`
- Modify: `tests/test_v2_production_composition.py`
- Modify: deploy env/example files discovered in the active V2 deploy only after checking ownership
- Modify: operational docs/reference as needed

**RED tests:**

1. Worker parses `V2_BOKUN_GROUPS_SHEET_CSV_URL`; API role never retains it.
2. Productive worker fails closed for missing/non-HTTPS source or policy/product-map disagreement.
3. `ReadKind.ACTIVITY` is mapped to `GroupEnrichedActivityReadAdapter`; description stays directly Bókun.
4. Reservation worker uses the same group-enriched activity resolver for `ServiceKind.ACTIVITY`.
5. Reconciliation probe handles `group_status` without requiring a group match for its two-person probe.
6. `repr` and settings serialization do not disclose the URL.

**Implementation:**

- Add worker-only group source setting and bounded timeout.
- Load and validate the versioned policy once during composition.
- Build the composite adapter for both conversational reads and reservation binding resolution.
- Keep API/router isolated from provider configuration.

**Gate:**

`pytest -q tests/test_v2_settings.py tests/test_v2_production_composition.py tests/test_v2_reads.py`

**Commit:** `feat(v2): wire group-enriched activity runtime`

## Task 5: Verification, review, and deployment

1. Run all focused gates above from a clean environment.
2. Run the repository canonical suite documented in `AGENTS.md`/active refactor docs; compare known baseline deselections only when still applicable to this base.
3. Run static/syntax checks.
4. Review complete branch diff for spec compliance, private binding safety, settings isolation, and no V3 changes.
5. Commit any review corrections and rerun covering tests.
6. Inspect active deployment paths and current image/health without writes.
7. Add worker-only group URL from the historical source without printing it.
8. Build immutable candidate image and verify its digest/SHA bindings.
9. Deploy with rollback metadata; do not reactivate any retired session.
10. Verify API/router/worker readiness and run read-only smoke:
    - normal 2+ matched or not-matched composed read;
    - group source failure behavior via isolated fixture/config, not by breaking production;
    - restricted solo matched if a real future group exists, otherwise verify fail-closed through deterministic integration fixture;
    - no ManyChat outbound test message.
11. Confirm V3 remained untouched.

**Final evidence:** commit SHA, image digest, container health, focused/canonical test counts, smoke result, and rollback handle.
