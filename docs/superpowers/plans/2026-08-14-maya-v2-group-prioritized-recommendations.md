# Maya V2 Group-Prioritized Recommendations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a period-based, informational activity-recommendation read that lets Maya rank semantically suitable tours while commercially prioritizing authenticated formed groups, without restricting ordinary Bókun consultation or reservation.

**Architecture:** Introduce `ReadKind.ACTIVITY_RECOMMENDATION` as a non-selectable, read-only model request over a bounded date interval and party. A dedicated recommendation adapter reads the group CSV once, discovers exact group/date candidates, adds bounded 4Ps and Pati-3d frequent candidates, asks the existing group-enriched Bókun adapter for executable availability, and returns one public observation without `offer_id` or reservation binding material. Maya remains the semantic owner of suitability and public ordering; ordinary `ReadKind.ACTIVITY` remains the only path to a selectable/reservable activity offer.

**Tech Stack:** Python 3.12, frozen dataclasses, existing V2 read contracts, `httpx`, pytest, Ruff, Docker Compose, existing Bókun/group adapters.

## Global Constraints

- V2 only. Do not edit or use Maya V3 artifacts.
- Suitability to the lead comes first; among suitable options, authenticated formed groups have commercial priority.
- A formed group is not a general eligibility requirement and must never close the ordinary catalog.
- Any catalog product, including products without groups and products other than 4Ps/Pati-3d, remains available through ordinary `ReadKind.ACTIVITY` consultation and reservation.
- Bókun remains authoritative for current availability, time, price, rate, category, quote, and executable binding.
- Recommendation observations are informational and non-selectable: no `offer_id`, `choice_ref`, private provider field, or reservation binding may enter the model-visible candidate list.
- A later selection/reservation always requires a fresh ordinary activity read.
- Maya alone interprets the complete customer message, profile, preferences, and suitability. Do not add keyword, substring, regex, alias, or deterministic natural-language routing.
- The group source remains worker-only and read-only. No raw row, source URL, customer/guide name, comment, or spreadsheet metadata enters model context.
- 4Ps and Pati-3d are frequent alternatives, not guaranteed departures and not a closed catalog.
- Pati-3d may be included only when its three calendar days fit wholly inside the requested interval.
- The existing solo-minimum-two exception remains unchanged and closed to its six configured products.
- Tests and qualification must execute no reservation, cart, payment, handoff, ManyChat delivery, or provider write.
- Do not deploy until focused tests, the canonical local workflow, immutable OCI binding, rollback preparation, and read-only smokes pass.

---

## File Structure

- `v2_contracts/providers.py` — owns the new closed recommendation request shape and canonical identity.
- `v2_adapters/bokun_groups.py` — owns bounded range discovery from one sanitized CSV fetch.
- `v2_adapters/group_enriched_activity.py` — applies an already sanitized group snapshot to one Bókun activity read, so recommendation fan-out never refetches the sheet.
- `v2_adapters/activity_recommendations.py` — new informational fan-out adapter; owns candidate planning, Bókun reads, deduplication, bounds, and public aggregation.
- `v2_adapters/hermes_model.py` — owns V8 parsing/wire instructions and Maya's semantic recommendation protocol.
- `v2_application/turn_executor.py` — keeps recommendation reads in the one read round and prevents recommendation evidence from becoming boundary selection authority.
- `v2_host/production.py` — composes the recommendation adapter from the same group source, policy, and group-enriched Bókun adapter used by ordinary activity reads.
- `tests/test_v2_activity_recommendation_contract.py` — request identity and parser/wire closure.
- `tests/test_v2_bokun_groups.py` — range candidate discovery and source failure behavior.
- `tests/test_v2_activity_recommendations.py` — fan-out, public aggregation, bounds, degradation, and catalog non-limitation.
- `tests/test_v2_hermes_model_adapter.py` — exact prompt/wire and non-selectability.
- `tests/test_v2_turn_executor.py` — one-round execution and fresh-read requirement.
- `tests/test_v2_production_composition.py` — productive wiring and worker-only source reuse.
- `tests/test_v2_group_recommendation_manychat.py` — no-effect, ManyChat-shaped natural conversation qualification harness.

---

### Task 1: Closed Recommendation Read Contract

**Files:**
- Modify: `v2_contracts/providers.py:27-271`
- Modify: `v2_adapters/hermes_model.py:712-776`
- Create: `tests/test_v2_activity_recommendation_contract.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`

**Interfaces:**
- Produces enum member: `ReadKind.ACTIVITY_RECOMMENDATION = "activity_recommendation"`.
- Produces request fields on `ReadRequest`: `period_start: date | None`, `period_end: date | None`.
- Closed request shape: `kind`, `period_start`, `period_end`, `adults`, `children`; locale is parent-injected and optional in canonical V2 request state.
- Interval semantics: inclusive `period_start` and inclusive `period_end`, `period_start <= period_end`, maximum 14 calendar days, exact integer `adults >= 1`, exact integer `children >= 0`.
- All unrelated fields, including `product_id`, `activity_date`, `participants`, `offer_id`, lodging dates, and free query text, must be `None`.
- Produces V8 parser suffix: `activity-recommendation`.

- [ ] **Step 1: Write request-contract REDs**

Create tests equivalent to:

```python
REQUEST = ReadRequest(
    request_id="read:recommendation:one",
    kind=ReadKind.ACTIVITY_RECOMMENDATION,
    period_start=date(2026, 9, 10),
    period_end=date(2026, 9, 15),
    adults=2,
    children=0,
    locale="pt-BR",
)


def test_recommendation_request_is_closed_and_canonical() -> None:
    assert json.loads(REQUEST.to_canonical_bytes()) == {
        "adults": 2,
        "children": 0,
        "kind": "activity_recommendation",
        "locale": "pt-BR",
        "period_end": "2026-09-15",
        "period_start": "2026-09-10",
        "request_id": "read:recommendation:one",
    }
    assert replace(REQUEST, request_id="read:recommendation:two").query_hash() == REQUEST.query_hash()


@pytest.mark.parametrize(
    "changes",
    [
        {"period_end": date(2026, 9, 9)},
        {"period_end": date(2026, 9, 24)},
        {"adults": 0},
        {"children": -1},
        {"product_id": "product:tour-4ps"},
        {"activity_date": date(2026, 9, 10)},
        {"participants": 2},
    ],
)
def test_recommendation_request_rejects_open_or_invalid_shapes(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidReadRequest):
        replace(REQUEST, **changes)
```

- [ ] **Step 2: Run the contract selector and authenticate the RED**

Run:

```bash
uv run --extra runtime --extra dev python -m pytest -q tests/test_v2_activity_recommendation_contract.py
```

Expected: collection or assertion failure because `ACTIVITY_RECOMMENDATION`, `period_start`, and `period_end` do not exist.

- [ ] **Step 3: Implement the minimal closed request shape**

Add the enum member and optional fields. Extend `ReadRequest.__post_init__()`, `_require_none()`, and `to_canonical_bytes()` with the exact interval constraints above. Keep `query_hash()` behavior unchanged except that the new request naturally excludes only `request_id`; do not exclude party or interval.

- [ ] **Step 4: Write V8 parser/wire REDs**

Add a V8 proposal test:

```python
payload = _v8_bytes(
    read_requests=[
        {
            "kind": "activity_recommendation",
            "period_start": "2026-09-10",
            "period_end": "2026-09-15",
            "adults": 2,
            "children": 0,
        }
    ]
)
proposal = _proposal(payload, request)
assert proposal.read_requests[0].kind is ReadKind.ACTIVITY_RECOMMENDATION
assert proposal.read_requests[0].locale == request.locale
```

Also assert extra `product_id`, missing party, overlong interval, malformed dates, and unknown fields fail with `InvalidModelProposal`.

- [ ] **Step 5: Implement V8 decoding**

Extend `_v8_read_request()` with:

```python
elif kind is ReadKind.ACTIVITY_RECOMMENDATION:
    expected = {"kind", "period_start", "period_end", "adults", "children"}
    fields.update(
        period_start=_v8_date(value.get("period_start"), "period_start"),
        period_end=_v8_date(value.get("period_end"), "period_end"),
        adults=value.get("adults"),
        children=value.get("children"),
        locale=request.locale,
    )
```

Add `ReadKind.ACTIVITY_RECOMMENDATION: "activity-recommendation"` to the suffix map.

- [ ] **Step 6: Run focused GREEN and existing parser regression**

Run:

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_activity_recommendation_contract.py \
  tests/test_v2_hermes_model_adapter.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Run static boundary checks and commit**

Run:

```bash
uv tool run --from ruff==0.15.10 ruff check \
  v2_contracts/providers.py v2_adapters/hermes_model.py \
  tests/test_v2_activity_recommendation_contract.py tests/test_v2_hermes_model_adapter.py
git diff --check
```

Commit:

```bash
git add v2_contracts/providers.py v2_adapters/hermes_model.py \
  tests/test_v2_activity_recommendation_contract.py tests/test_v2_hermes_model_adapter.py
git commit -m "feat(v2): add activity recommendation read contract"
```

---

### Task 2: One-Fetch Group Range Discovery

**Files:**
- Modify: `v2_adapters/bokun_groups.py`
- Modify: `tests/test_v2_bokun_groups.py`

**Interfaces:**
- Produces frozen contract:

```python
@dataclass(frozen=True, slots=True)
class GroupDateCandidate:
    canonical_product_id: str
    activity_date: date
    group_participants: int
```

- Produces pure function:

```python
def discover_group_candidates(
    csv_bytes: bytes,
    *,
    policy: ActivityGroupPolicy,
    period_start: date,
    period_end: date,
    max_candidates: int = 24,
) -> tuple[GroupDateCandidate, ...]: ...
```

- Produces source method:

```python
def discover(
    self,
    *,
    period_start: date,
    period_end: date,
    max_candidates: int = 24,
) -> tuple[GroupDateCandidate, ...] | None: ...
```

`None` means source unavailable/invalid. An empty tuple means a valid sheet with no matching group in the interval.

- [ ] **Step 1: Write pure-discovery REDs**

Cover exact aliases, inherited blank dates, aggregation, inclusive range, deterministic sort `(activity_date, canonical_product_id)`, duplicate aggregation, outside-range exclusion, malformed/unknown rows ignored under the existing parser contract, and hard rejection of invalid bounds.

Use an assertion like:

```python
assert discover_group_candidates(
    csv_bytes,
    policy=policy,
    period_start=date(2026, 9, 10),
    period_end=date(2026, 9, 12),
) == (
    GroupDateCandidate("product:marimbus", date(2026, 9, 10), 3),
    GroupDateCandidate("product:tour-2ms", date(2026, 9, 12), 2),
)
```

- [ ] **Step 2: Run RED**

Run:

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_bokun_groups.py -k 'discover or range'
```

Expected: failure because the range contract does not exist.

- [ ] **Step 3: Implement one-pass pure discovery**

Reuse the existing validated CSV decoding, date inheritance, alias normalization, and participant parsing. Build an alias-to-product map from the already validated policy, aggregate by exact `(canonical_product_id, activity_date)`, and sort deterministically. Reject `max_candidates < 1`, periods over 14 days, reversed dates, and candidate overflow instead of silently truncating.

- [ ] **Step 4: Write source transport REDs**

Use an injected fetcher counter and assert:

```python
result = source.discover(period_start=start, period_end=end)
assert fetch_calls == 1
assert result == expected
```

Assert timeout, invalid CSV, redirect failure, oversized response, and overflow return `None` without leaking raw content.

- [ ] **Step 5: Implement `BokunGroupsSource.discover()`**

Call `_fetch()` exactly once, pass bytes to the pure function, and translate the source's existing handled transport/data exceptions to `None`. Do not loop through `lookup()` because that would refetch the sheet per product/date.

- [ ] **Step 6: Run full group-source GREEN**

Run:

```bash
uv run --extra runtime --extra dev python -m pytest -q tests/test_v2_bokun_groups.py
uv tool run --from ruff==0.15.10 ruff check \
  v2_adapters/bokun_groups.py tests/test_v2_bokun_groups.py
git diff --check
```

Expected: all tests and static checks pass.

- [ ] **Step 7: Commit**

```bash
git add v2_adapters/bokun_groups.py tests/test_v2_bokun_groups.py
git commit -m "feat(v2): discover formed groups across a stay period"
```

---

### Task 3: Bounded Informational Recommendation Adapter

**Files:**
- Create: `v2_adapters/activity_recommendations.py`
- Create: `tests/test_v2_activity_recommendations.py`
- Modify: `v2_adapters/group_enriched_activity.py`
- Modify: `tests/test_v2_group_enriched_activity.py`
- Modify: `v2_application/reads.py` only if a dedicated informational-port protocol is needed; otherwise retain `ReadPort`.

**Interfaces:**
- Consumes `BokunGroupsSource.discover()`, `ActivityGroupPolicy`, and a new source-free `GroupEnrichedActivityReadAdapter.read_with_group_context()` method.
- Produces on the existing activity adapter:

```python
def read_with_group_context(
    self,
    request: ReadRequest,
    *,
    group: GroupLookupResult,
) -> ReadObservation: ...
```

`read()` remains the ordinary entry point: it performs one exact `groups.lookup(...)` and delegates to `read_with_group_context()`. The new method performs no group-source I/O; it applies the same solo policy and Bókun resolution using the supplied sanitized result.
- Produces:

```python
class ActivityRecommendationReadAdapter:
    def __init__(
        self,
        *,
        groups: BokunGroupsSource,
        policy: ActivityGroupPolicy,
        activity: GroupEnrichedActivityReadAdapter,
        clock: object,
        ttl: timedelta,
        max_group_candidates: int = 24,
        max_provider_reads: int = 64,
    ) -> None: ...

    def read(self, request: ReadRequest) -> ReadObservation: ...
```

- Public payload schema:

```json
{
  "recommendation_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "party": {"adults": 2, "children": 0},
  "group_source_status": "available | unavailable",
  "candidates": [
    {
      "product_id": "product:marimbus",
      "product_public_name": "Marimbus",
      "activity_date": "YYYY-MM-DD",
      "duration_days": 1,
      "total_amount": "300.00",
      "currency": "BRL",
      "group_status": "matched | not_matched | unavailable",
      "existing_group": true,
      "frequent_alternative": false
    }
  ],
  "candidate_count": 1
}
```

- Candidate entries must not contain `offer_id`, `choice_ref`, `private_binding_hash`, start/rate/category IDs, source URL, raw row, names, comments, or guide data.
- Frequent candidate identities are exactly `product:tour-4ps` and `product:pati-3d`.
- Candidate planning rules:
  - include each exact formed-group product/date discovered in the interval;
  - include 4Ps for each interval date until the provider-read cap;
  - include Pati-3d start dates only where `start + 2 days <= period_end`;
  - deduplicate exact `(product_id, activity_date)` pairs;
  - formed-group candidates are planned before frequent candidates;
  - a provider-read cap overflow fails the recommendation read before provider calls; it does not silently omit formed-group candidates.

- [ ] **Step 1: Write candidate-planning REDs**

Test a pure private helper or public behavior showing:

```python
planned = plan_recommendation_candidates(...)
assert planned == (
    CandidateQuery("product:marimbus", date(2026, 9, 10), source="formed_group"),
    CandidateQuery("product:tour-4ps", date(2026, 9, 10), source="frequent"),
    CandidateQuery("product:pati-3d", date(2026, 9, 10), source="frequent"),
    ...
)
```

Cover deduplication where 4Ps/Pati already has a group, Pati window exclusion, deterministic order, 14-day interval maximum, and max-provider-read rejection before any Bókun call.

- [ ] **Step 2: Run planning RED**

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_activity_recommendations.py -k planning
```

Expected: import/attribute failure for the missing adapter/planner.

- [ ] **Step 3: Implement frozen `CandidateQuery` and planner**

Implement exact validation and deterministic planning. The planner must not inspect customer prose or decide suitability.

- [ ] **Step 4: Write source-free group-context REDs**

Extend `tests/test_v2_group_enriched_activity.py` to call `read_with_group_context()` with matched, not-matched, and unavailable `GroupLookupResult` values. Assert it applies the existing solo gates and Bókun authority exactly as `read()` does while the fake group source receives zero calls. Keep the existing `read()` tests proving one lookup for ordinary activity consultation.

- [ ] **Step 5: Extract the source-free activity path**

Move the current post-lookup logic from `GroupEnrichedActivityReadAdapter.read()` into `read_with_group_context()`. Make `read()` only validate the activity request, obtain the exact sanitized lookup result, and delegate. Do not duplicate the solo policy or Bókun resolution.

- [ ] **Step 6: Write composed-recommendation REDs**

Use fake group discovery and a fake existing activity adapter. Assert:

1. group discovery occurs once before any Bókun read;
2. every planned candidate uses one source-free group-context activity read with exact party/date/product;
3. unavailable Bókun candidates are omitted;
4. group-source `None` still yields available 4Ps/Pati candidates marked `group_status=unavailable` and never claims a group;
5. a candidate Bókun error is isolated to that candidate only when the error is a definitive read-unavailable outcome; structural/binding errors fail the aggregate read;
6. two or more participants without groups remain present when Bókun says available;
7. restricted solo candidates without matching groups are omitted by the existing group-enriched adapter;
8. no output key is selectable or private;
9. private binding hash is an aggregate evidence hash, not any candidate's executable binding;
10. the returned observation binds `request.canonical_hash()`.

- [ ] **Step 7: Implement `ActivityRecommendationReadAdapter.read()`**

For each planned query, construct:

```python
ReadRequest(
    request_id=f"{request.request_id}:candidate:{index}",
    kind=ReadKind.ACTIVITY,
    product_id=query.product_id,
    activity_date=query.activity_date,
    adults=request.adults,
    children=request.children,
    locale=request.locale,
)
```

Create one in-memory map from discovered `(product_id, activity_date)` pairs to sanitized `GroupLookupResult` values. For every planned candidate, pass `matched` when the exact pair exists, `not_matched` when discovery succeeded without that pair, and `unavailable` when discovery returned `None`. Call `read_with_group_context()`; never call `activity.read()` inside recommendation fan-out. Project only the closed public fields. Set `frequent_alternative=True` only for 4Ps/Pati entries introduced or recognized as frequent. Compute the aggregate `private_binding_hash` from request hash plus ordered candidate observation hashes using a new domain string `v2-activity-recommendation-evidence-v1`; never use it as a reservable binding.

- [ ] **Step 8: Prove ordinary catalog paths remain unchanged**

Add regression tests that directly call the existing `GroupEnrichedActivityReadAdapter` for:

- an arbitrary non-frequent product without a group and 2+ participants;
- an explicitly requested product absent from recommendation candidates;
- a normal binding resolution after a fresh ordinary read.

Assert the recommendation adapter is not in those call paths.

- [ ] **Step 9: Run adapter GREEN and affected regressions**

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_activity_recommendations.py \
  tests/test_v2_group_enriched_activity.py \
  tests/test_v2_bokun_party_reads.py \
  tests/test_v2_reads.py
uv tool run --from ruff==0.15.10 ruff check \
  v2_adapters/activity_recommendations.py v2_adapters/group_enriched_activity.py \
  tests/test_v2_activity_recommendations.py tests/test_v2_group_enriched_activity.py
git diff --check
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add v2_adapters/activity_recommendations.py \
  v2_adapters/group_enriched_activity.py \
  tests/test_v2_activity_recommendations.py \
  tests/test_v2_group_enriched_activity.py \
  v2_application/reads.py
git commit -m "feat(v2): compose bounded activity recommendations"
```

If `v2_application/reads.py` was not changed, omit it from `git add`.

---

### Task 4: Maya Recommendation Semantics and Non-Selectable Turn Flow

**Files:**
- Modify: `v2_adapters/hermes_model.py:165-265,320-410,712-839`
- Modify: `v2_application/turn_executor.py:1989-2165,2515-2532`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_turn_executor.py`

**Interfaces:**
- Adds prompt constant `_ACTIVITY_RECOMMENDATION_SYSTEM_SUFFIX`.
- Recommendation observation remains in `ModelRequest.observations` for the post-read semantic pass.
- Recommendation candidate entries never become choice refs because they contain no `offer_id`.
- `boundary_reads` continues to include only `ReadKind.LODGING` and `ReadKind.ACTIVITY`; recommendation evidence is never persisted as selectable availability authority.

- [ ] **Step 1: Write prompt-contract REDs**

Assert the exact system prompt contains all of these rules:

- when the lead supplies a period and requests suggestions, emit one `activity_recommendation` read in the initial frame;
- after observation, suitability comes before commercial priority;
- among suitable candidates, `group_status=matched` ranks first;
- a clearly unsuitable grouped tour must not outrank a suitable ungrouped tour;
- 4Ps/Pati-3d are frequent alternatives, not guaranteed groups/departures;
- normally present at most three useful options, but do not state the catalog is limited;
- explicit later requests for another tour use an ordinary fresh `activity` read;
- recommendation candidates cannot be selected or reserved directly;
- no second read may be emitted after observations in the same turn.

Also assert no prompt language tells the model to choose by list position, keyword, or deterministic score.

- [ ] **Step 2: Run prompt RED**

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_hermes_model_adapter.py -k recommendation
```

Expected: failure because the recommendation protocol is absent.

- [ ] **Step 3: Implement the semantic prompt suffix**

Add a concise highest-salience suffix that states:

```text
ACTIVITY RECOMMENDATION PROTOCOL:
- You alone decide semantic suitability from the complete message, known facts, and observations.
- Suitability comes first. Among suitable candidates, present matched formed groups before suitable candidates without a formed group.
- Never recommend a clearly unsuitable activity merely because it has a group.
- 4Ps and Pati 3 days are frequent alternatives only; never claim a formed group or confirmed departure without current evidence.
- Recommendation candidates are informational. Never select their list position or treat them as reservation authority. A customer choice requires a fresh ordinary activity read.
- The recommendation set does not limit the Bókun catalog. If the customer asks about another known product, request that ordinary activity read.
```

Wire it into productive request construction without adding a skill or tool.

- [ ] **Step 4: Write model-wire non-selectability REDs**

Construct a recommendation observation with candidate product IDs and assert `_request_wire()`:

- preserves the public recommendation fields;
- adds no `choice_ref`;
- contains no `offer_id` or private fields;
- permits a post-read `inform` response;
- rejects `selected_choice_refs=["activity:1"]` because no such current choice exists.

- [ ] **Step 5: Write turn-executor REDs**

Use a scripted fake model and fake read port:

1. first frame emits exactly one recommendation read;
2. executor calls it once and sends its one observation to the second model frame;
3. second frame emits an informational recommendation and no new reads/effects;
4. `boundary_reads` receives no recommendation artifact;
5. a later separate turn that chooses a product emits a normal `ReadKind.ACTIVITY` request before any selection;
6. recommendation history cannot authorize confirmation or reservation;
7. ordinary arbitrary-product reads still execute.

- [ ] **Step 6: Implement minimal turn support**

The existing read loop should accept the new kind automatically through `V2ReadService`. Make only the explicit updates required for locale propagation, recommendation observation handling, and tests. Keep the boundary-read filter unchanged and add an explanatory invariant comment/test rather than broadening it.

- [ ] **Step 7: Run focused GREEN**

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_activity_recommendation_contract.py \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_turn_executor.py
uv tool run --from ruff==0.15.10 ruff check \
  v2_adapters/hermes_model.py v2_application/turn_executor.py \
  tests/test_v2_hermes_model_adapter.py tests/test_v2_turn_executor.py
git diff --check
```

- [ ] **Step 8: Commit**

```bash
git add v2_adapters/hermes_model.py v2_application/turn_executor.py \
  tests/test_v2_hermes_model_adapter.py tests/test_v2_turn_executor.py
git commit -m "feat(v2): teach Maya group-prioritized recommendations"
```

---

### Task 5: Productive Composition and Worker Isolation

**Files:**
- Modify: `v2_host/production.py`
- Modify: `tests/test_v2_production_composition.py`
- Modify: `tests/test_v2_group_runtime_packaging.py` only if packaging assertions need the new module.

**Interfaces:**
- `build_read_service(settings)` maps `ReadKind.ACTIVITY_RECOMMENDATION` to one `ActivityRecommendationReadAdapter`.
- That adapter reuses the already composed worker-only `BokunGroupsSource`, `ActivityGroupPolicy`, and `GroupEnrichedActivityReadAdapter` instances.
- API/router continue without the group URL and recommendation provider graph.

- [ ] **Step 1: Write production-composition REDs**

Assert:

```python
reads = build_read_service(worker_settings)
recommendations = reads._ports[ReadKind.ACTIVITY_RECOMMENDATION]
activity = reads._ports[ReadKind.ACTIVITY]
assert type(recommendations).__name__ == "ActivityRecommendationReadAdapter"
assert recommendations._activity is activity
assert recommendations._groups is activity._groups
assert recommendations._policy is activity._policy
```

Also assert missing worker group source still fails composition, while API-role settings do not retain or expose the source URL.

- [ ] **Step 2: Run production RED**

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_production_composition.py -k recommendation
```

Expected: missing recommendation port/adapter.

- [ ] **Step 3: Implement productive wiring**

Refactor `_group_enriched_activity_adapter()` only as much as needed to return/reuse its three dependencies without creating duplicate group fetchers or policy instances. Register the new read kind in worker composition. Do not add it to reservation binding resolution.

- [ ] **Step 4: Verify packaging and repr safety**

Assert the OCI source copy includes `v2_adapters/activity_recommendations.py` through the existing package copy and that `repr(reads)` contains type names only, never the sheet URL.

- [ ] **Step 5: Run composition GREEN**

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_settings.py \
  tests/test_v2_production_composition.py \
  tests/test_v2_group_runtime_packaging.py \
  tests/test_v2_reads.py
uv tool run --from ruff==0.15.10 ruff check \
  v2_host/production.py tests/test_v2_production_composition.py
git diff --check
```

- [ ] **Step 6: Commit**

```bash
git add v2_host/production.py tests/test_v2_production_composition.py \
  tests/test_v2_group_runtime_packaging.py
git commit -m "feat(v2): wire activity recommendations into worker runtime"
```

Omit unchanged files from the commit.

---

### Task 6: ManyChat-Shaped No-Effect Conversation Qualification

**Files:**
- Create: `tests/test_v2_group_recommendation_manychat.py`
- Modify: existing fake conversation helpers only if they are already the owner of reusable ManyChat-shaped payload construction.

**Interfaces:**
- Uses the actual V2 turn/model request boundary with scripted model frames and fake read ports for deterministic CI.
- Adds a separate real-model shadow harness outside Git for final qualification; it uses the deployed/candidate image with live read credentials but mechanically closes every write/send gate.

- [ ] **Step 1: Write deterministic conversational acceptance REDs**

Cover at least these natural customer messages and scripted semantic outcomes:

1. period + preference, two suitable candidates, one matched group → matched suitable candidate is mentioned first;
2. grouped strenuous candidate versus suitable ungrouped candidate → suitable ungrouped candidate is first;
3. no groups → Bókun-available options are still recommended;
4. group source unavailable → no group claim, normal recommendation continues;
5. explicit request for an arbitrary non-frequent product → ordinary activity read is emitted;
6. 4Ps without a group → may be described as frequent, never as confirmed group/departure;
7. Pati-3d without a three-day window → absent from candidate observation/recommendation;
8. solo restricted product without group → absent/unavailable under existing policy;
9. two-person same product without group → remains available;
10. customer chooses a recommendation → next turn performs a fresh ordinary activity read.

Every scenario asserts zero commands, relays, provider writes, payments, handoffs, and ManyChat deliveries.

- [ ] **Step 2: Run deterministic RED/GREEN cycle**

Run before implementation completion to observe causal failures, then after Tasks 1-5:

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_group_recommendation_manychat.py
```

Expected final result: all scenarios pass with zero effects.

- [ ] **Step 3: Add anti-limitation regressions**

Explicitly assert that the recommendation response does not contain language equivalent to “only these tours can be booked” and that a subsequent arbitrary catalog product read reaches the ordinary activity port.

Do this with exact scripted reply text in deterministic tests; do not build a controller text scanner in production.

- [ ] **Step 4: Run affected behavioral gate**

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_group_recommendation_manychat.py \
  tests/test_v2_activity_recommendations.py \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_group_enriched_activity.py
```

- [ ] **Step 5: Commit deterministic qualification**

```bash
git add tests/test_v2_group_recommendation_manychat.py
git commit -m "test(v2): qualify group-prioritized recommendation conversations"
```

Add any shared helper only if changed and covered.

---

### Task 7: Frozen Candidate, Review, OCI, and Read-Only Rollout

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Create outside Git: immutable review packet, live-read smoke scripts, rollout receipt, and rollback backup under `/home/ubuntu/workspace/agente-v2-canary-deploy/`.
- Do not modify V3, provider state, or customer communication state.

**Interfaces:**
- Functional candidate is one exact SHA/tree with clean status.
- OCI label `org.opencontainers.image.revision` equals that SHA.
- Rollout receipt records exact SHA, tree, digest, compose/env paths, test counts, smoke results, and previous rollback identity.

- [ ] **Step 1: Run every focused gate once on the complete candidate**

```bash
uv run --extra runtime --extra dev python -m pytest -q \
  tests/test_v2_activity_recommendation_contract.py \
  tests/test_v2_bokun_groups.py \
  tests/test_v2_activity_recommendations.py \
  tests/test_v2_group_enriched_activity.py \
  tests/test_v2_bokun_party_reads.py \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_settings.py \
  tests/test_v2_production_composition.py \
  tests/test_v2_group_runtime_packaging.py \
  tests/test_v2_group_recommendation_manychat.py
```

Expected: all pass.

- [ ] **Step 2: Run canonical and static gates**

Run the exact canonical workflow command/deselections from `.github/workflows/phase8.yml`, then:

```bash
uv tool run --from ruff==0.15.10 ruff check v2_adapters v2_application v2_contracts v2_host deploy
python -m compileall -q reservation_boundary reservation_domain reservation_execution \
  reservation_followup v2_adapters v2_application v2_contracts v2_host tests
git diff --check
git status --short --branch
```

Expected: canonical suite and static gates pass; worktree is clean after final commit.

- [ ] **Step 3: Obtain bounded read-only reviews**

Review exact SHA/tree in two scopes:

1. contract/model semantics and proof that suitability precedes group priority without catalog limitation;
2. provider fan-out, bounds, source failure, non-selectability, and reservation safety.

Any Critical/Important finding requires a named RED, corrective commit, affected gate rerun, and exact-SHA re-review.

- [ ] **Step 4: Build one immutable OCI candidate**

Build with `Dockerfile.v2`, label exact SHA, verify `/app/config/v2_activity_group_policy.json`, import the new adapter, and prove API/router do not receive `V2_BOKUN_GROUPS_SHEET_CSV_URL` while worker does.

- [ ] **Step 5: Run isolated live read-only smokes**

With all write/send/payment/handoff gates forced closed, execute:

1. a period with a real formed-group candidate and prove one sheet fetch plus Bókun GETs;
2. a no-group period and prove ordinary Bókun candidates still return;
3. 4Ps frequent candidate without false group claim;
4. Pati-3d window inclusion/exclusion;
5. arbitrary ordinary product read outside the recommendation set;
6. selection follow-up requiring fresh ordinary activity read;
7. zero POST/cart/reservation/payment/ManyChat calls.

Do not disclose the group URL or raw payloads in output.

- [ ] **Step 6: Run real-model ManyChat-shaped shadow qualification**

Use unique fake lead IDs, native Maya V2 model boundary, live provider reads, and no outbound delivery. Capture Maya replies and read kinds for the acceptance scenarios from Task 6. Manual semantic review must confirm:

- suitable options precede unsuitable grouped options;
- matched groups precede equally suitable non-groups;
- Maya does not limit the catalog;
- Maya does not claim a group/confirmed departure without evidence;
- no internal provider/tool/schema terminology appears;
- zero external effects.

- [ ] **Step 7: Prepare rollback and deploy only after all gates are green**

Back up current compose/env pointers and SQLite databases consistently, record current image digest/SHA, render immutable candidate compose/env, confirm pending queues are zero, then recreate API/worker/router with automatic rollback on failed health/identity checks.

- [ ] **Step 8: Post-deploy verification**

Verify exact image digest, SHA, worker heartbeat, API/router readiness, zero critical log markers, zero pending queues, worker-only group source, and repeat one positive and one no-group recommendation smoke with writes closed.

- [ ] **Step 9: Publish receipt and update active authority**

Write a public sanitized rollout receipt, update `docs/refactor/ACTIVE.md` with final SHA/tree/gates/rollback handle, commit documentation, push the branch using the V2 deploy key, and verify remote SHA equals local SHA.

---

## Plan Self-Review

### Spec coverage

- Suitability before formed groups: Tasks 4 and 6.
- Formed groups before equally suitable alternatives: Tasks 2-4 and 6.
- 4Ps/Pati-3d frequent alternatives: Task 3.
- Pati three-day fit: Tasks 3 and 6.
- Arbitrary tours and tours without groups remain consultable/reservable: Tasks 3, 4, and 6.
- No direct recommendation selection: Tasks 3 and 4.
- Fresh ordinary read before selection/reservation: Tasks 4 and 6.
- Bókun authority and solo-minimum-two policy preserved: Tasks 3 and 7.
- Worker-only sheet and no raw leakage: Tasks 2, 3, 5, and 7.
- Natural ManyChat-shaped qualification with no effects: Tasks 6 and 7.

### Type consistency

- `period_start`/`period_end` are inclusive exact `date` fields in every task.
- `GroupDateCandidate` is source evidence; `CandidateQuery` is adapter planning state; neither is model-visible as a private DTO.
- Recommendation public candidates use `product_id` only as a stable catalog reference and omit `offer_id`; ordinary activity reads remain the only selectable offer producer.
- `ActivityRecommendationReadAdapter` implements the existing `read(ReadRequest) -> ReadObservation` port shape.

### Scope check

This remains one coherent subsystem: an informational recommendation read plus Maya's semantic handling. It does not alter reservation execution, payment, handoff, V3, or the existing solo-minimum-two policy.
