# Split-Origin Reservation Profile Authority — Implementation Plan

> **For agentic workers:** execute tasks in order with strict RED/GREEN discipline. Do not perform any real provider, ManyChat, payment, relay, deploy, or rollout effect.

**Goal:** Allow a reservation to use conversational full name/email/country fallbacks while preserving a fresh ManyChat-authenticated phone and keeping private values out of public/model/evidence artifacts.

**Architecture:** A dedicated SQLite private customer-fact owner persists canonical conversational facts and their source-turn provenance. A parent-owned resolver builds the effective reservation customer with conversation-first precedence for name/email/country, a ManyChat-only authenticated phone, and a source-aware split-origin digest. Collection turns are always command-free; only a later turn can create a summary, and a later contextual confirmation revalidates the exact selected customer material and all existing commercial terms.

**Tech stack:** Python 3.11+, immutable dataclasses, SQLite, pytest, Ruff, existing V2 boundary/domain stores.

## Global constraints

- Base SHA is exactly `1219ff2c12efa989f44f5caa7364363011ea281d` before functional edits.
- Phone is never accepted from conversational/model facts.
- Private values never enter public projection/artifacts or later model-state prompts.
- A current-turn private fact cannot create a summary or command.
- Provider tests use fake ports or `httpx.MockTransport` with `.invalid` hosts.
- No real network write, ManyChat delivery, relay, budget arming, deploy, promotion, or rollout.
- Update `docs/refactor/ACTIVE.md` after each green functional commit.

---

### Task 1: Activate split-origin authority

**Files:**
- Add: `docs/superpowers/specs/2026-08-02-split-origin-reservation-profile-authority-design.md`
- Add: `docs/superpowers/plans/2026-08-02-split-origin-reservation-profile-authority.md`
- Modify: `docs/refactor/ACTIVE.md`
- Modify: `docs/superpowers/plans/2026-08-01-conversational-country-profile-completion.md`

**Steps:**
1. Authenticate branch, worktree, base SHA, and clean status.
2. Record the new active spec/plan and mark the old ManyChat-exclusive name/email rule as superseded.
3. Run `git diff --check`.
4. Commit authority only.

---

### Task 2: Private canonical facts and durable owner

**Files:**
- Add: `v2_application/private_customer_facts.py`
- Add: `tests/test_v2_private_customer_facts.py`
- Modify: `v2_contracts/model.py`
- Modify: `tests/test_v2_profile_and_model_grammar.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True, repr=False)
class PrivateCustomerFactSnapshot:
    lead_id: str
    full_name: str | None
    email: str | None
    country_code: str | None
    content_hash: str
    source_turns: tuple[tuple[str, str], ...]

    @property
    def present_fact_names(self) -> tuple[str, ...]: ...

@dataclass(frozen=True, slots=True, repr=False)
class PrivateCustomerFactWriteResult:
    snapshot: PrivateCustomerFactSnapshot
    supplied_in_turn: tuple[str, ...]
    changed_in_turn: tuple[str, ...]
    replayed: bool

class SQLitePrivateCustomerFactStore:
    def __init__(self, path: Path): ...
    def load(self, lead_id: str) -> PrivateCustomerFactSnapshot: ...
    def persist_turn(
        self,
        *,
        lead_id: str,
        source_turn_id: str,
        source_event_hash: str,
        facts: tuple[ModelFact, ...],
        persisted_at: datetime,
    ) -> PrivateCustomerFactWriteResult: ...
    def close(self) -> None: ...
```

**RED tests:**
- canonical two-component Unicode name normalization without surname invention;
- invalid one-component conversational name rejected;
- canonical valid email and ISO country;
- phone outside the private-store catalog;
- private round-trip preserves exact effective values;
- same turn/event/payload is idempotent;
- same turn with divergent payload/event fails generically;
- retry after a simulated crash reports fields supplied by that same turn;
- repr/exceptions contain none of the fixture PII;
- only presence names have a public view.

**GREEN:** implement the minimum two-table SQLite owner and canonical validators. Ensure SQL errors are rethrown generically without values.

**Verification:** focused tests, Ruff, compile, `git diff --check`; commit functional task, then update `ACTIVE.md` in a separate control commit.

---

### Task 3: Parent-owned effective customer resolver

**Files:**
- Modify: `v2_application/conversation.py`
- Modify: `v2_contracts/profile.py`
- Modify: `reservation_domain/types.py` only if required to hide PII from repr
- Add/modify: `tests/test_v2_conversation_reducer.py`
- Modify: `tests/test_v2_profile_and_model_grammar.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True, repr=False)
class EffectiveCustomerResolution:
    customer: CustomerFacts | None
    missing_fields: tuple[str, ...]
    conflicting_fields: tuple[str, ...]


def resolve_effective_customer(
    profile: PrivateCustomerBinding,
    private_facts: PrivateCustomerFactSnapshot,
    projection: ConversationProjection,
    now: datetime,
    *,
    activity_party: Party | None = None,
) -> EffectiveCustomerResolution: ...
```

**RED tests:**
- fresh phone-only binding + private name/email/country resolves;
- one-word ManyChat name falls back to private full name;
- missing email falls back;
- valid persisted conversational name/email/country win over divergent valid ManyChat values;
- valid fresh ManyChat values supply only conversationally absent fields;
- no phone fallback even if a `ModelFact("phone_e164", ...)` exists elsewhere;
- absent/future/expired/invalid profile is not ready;
- split-origin `customer_ref` changes when selected customer material changes, but not when an unused ManyChat field changes;
- resolver/customer repr does not reveal PII.

**GREEN:** replace profile/projection-only `_customer` and readiness with the exact source-aware resolver. Preserve activity passenger checks. Bind confirmation reauthentication to binding identity, authenticated phone, effective selected fields/origins, and private snapshot rather than unrelated ManyChat metadata.

**Verification:** reducer/profile/customer tests plus reservation-domain serialization/signature regressions; static gates; commit + control update.

---

### Task 4: Collection-only turn protocol and public-artifact privacy

**Files:**
- Modify: `v2_application/turn_executor.py`
- Modify: `v2_application/conversation.py`
- Modify: `v2_contracts/model.py`
- Modify: `v2_adapters/hermes_model.py` and its versioned prompt
- Modify: `tests/test_v2_customer_collection.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_luna_prompt.py`

**RED tests:**
- private profile facts are split out before `_merge_projection`;
- a collection turn persists values but emits zero summary/command/relay/read-authority;
- a crash after private persistence and retry of the same batch remains collection-only;
- later request exposes only ordered presence markers and never stored values;
- `ConversationProjection`, `MayaTurnProposal`, typed-fact artifacts, receipt, public rows, logs, repr, and exception strings omit fixture PII;
- conversational phone proposal never enters private store or readiness;
- invalid private fields yield a natural non-technical correction request; valid divergence does not;
- model prompt asks only missing fields and never asks for an already authenticated phone.

**GREEN:**
1. Inject the private store into `V2TurnExecutor`.
2. Load private snapshot before model request.
3. Persist validated private facts after model validation.
4. Strip private reservation-profile values from the public proposal.
5. Force deterministic collection-only behavior when the current turn supplies a private field.
6. Compute presence markers and effective readiness from profile + snapshot.
7. Keep birth/gender/passenger behavior unchanged.

**Verification:** executor, customer collection, model adapter/prompt, boundary artifact graph and replay tests; static gates; commit + control update.

---

### Task 5: Runtime composition and physically separate owner

**Files:**
- Modify: `v2_host/settings.py`
- Modify: `v2_host/composition.py`
- Modify: `v2_host/production.py`
- Modify: `tests/test_v2_settings.py`
- Modify: `tests/test_v2_composition.py`
- Modify: `tests/test_v2_production_composition.py`

**RED tests:**
- `sqlite_paths["private_customer_facts"]` is deterministic and distinct;
- existing hardlink alias detection includes the new owner;
- worker owns exactly one private customer store and closes it;
- API role does not open the private owner;
- productive inbox worker receives the container-owned store;
- open failures close already-opened owners.

**GREEN:** wire the path/store into `V2Container` and `_build_inbox_worker`; update exact owner-count/readiness expectations.

**Verification:** settings/composition/production tests, hardlink guards, static checks; commit + control update.

---

### Task 6: End-to-end reservation, confirmation, Cloudbeds payload, and replay

**Files:**
- Add: `tests/test_v2_split_origin_profile_e2e.py`
- Modify as needed: existing focused Cloudbeds/replay/privacy tests only

**Fake E2E sequence:**
1. ManyChat binding has authenticated phone only.
2. Natural collection message supplies full name, email, country.
3. Assert private persistence and zero command/relay/provider call in that turn.
4. Later selection creates one frozen summary using the effective customer.
5. Later contextual natural confirmation creates exactly one command.
6. Replay confirmation/restart does not create another command or relay.
7. Fake Cloudbeds `httpx.MockTransport` receives exact effective first/last name, email, authenticated phone, and country.
8. Accepted 2xx + one principal `reservationID` confirms monotonically; GET audit remains separate.

**Counterexamples:**
- one-word ManyChat name fallback;
- missing email fallback;
- divergent valid ManyChat/conversation values use the conversational fields;
- mutation of an unused ManyChat name/email/country field does not revoke confirmation;
- expired/missing phone;
- invalid private fields;
- any customer change after summary rejects old confirmation until a new summary;
- no payment rows/effects;
- no PII in public/evidence artifacts.

**Verification:** focused E2E + Cloudbeds transport/audit + Bókun + completion/outbox/payment separation + replay; static gates; commit + control update.

---

### Task 7: Final qualification, independent review, publication, and CI

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Add/update: `.superpowers/sdd/` task reports as needed

**Steps:**
1. Authenticate exact clean candidate SHA/tree/branch.
2. Run focused profile/conversation/reducer/executor/Cloudbeds/replay/privacy tests.
3. Run the repository's exact complete clean CI command with the seven documented historical deselections.
4. Run Ruff, compileall, `fasttrack-boundaries`, manifest/checksum validators, `git diff --check`, and secret/PII scans.
5. Obtain independent read-only review on the exact SHA; any material reproducible finding creates a new RED and invalidates the candidate.
6. Only after local green and review approval, push the exact SHA.
7. Authenticate GitHub CI `test`, `image`, `gate` as completed/success for the same SHA.
8. Authenticate the OCI digest for the same candidate if CI publishes one.
9. Report SHA, tree, test/subtest counts, CI run/jobs, OCI digest, review verdict, and effect ledger (`0` real POST/messages/reservations/payments).
10. Stop at readiness gate. Do not deploy, open relay, arm write budget, or roll out.

---

### Task 8: Superseding conversation-first authority correction

**Authority:** Carlos explicitly superseded fail-closed ManyChat/conversation divergence on 2026-08-03. Name, email, and country supplied explicitly by the lead win; phone remains ManyChat-only.

**Files:**
- Modify: `v2_application/conversation.py`
- Modify: `v2_application/turn_executor.py`
- Modify: `tests/test_v2_effective_customer_profile.py`
- Modify: `tests/test_v2_split_origin_cloudbeds_e2e.py`
- Modify focused reducer/executor tests only if a causal witness requires them
- Modify: this spec/plan and `docs/refactor/ACTIVE.md`

**RED witnesses:**
1. Valid persisted conversational name/email/country diverge from valid fresh ManyChat values; resolver must be ready with the conversational values and no conflicts.
2. With conversational overrides frozen into a summary, a refresh changing only unused ManyChat name/email/country metadata must still permit confirmation.
3. Changing binding identity or authenticated phone still blocks confirmation with zero command/relay.
4. If a conversational field is absent, changing the selected ManyChat value remains material and requires a new summary.
5. Cloudbeds fake payload receives the conversational name/email/country and ManyChat phone exactly once; no payment effect.

**GREEN:** implement field-level source selection, a source-aware opaque customer reference, and source-aware profile material reauthentication. Remove divergence conflicts only for the approved three fields; preserve invalid-input, phone, freshness, exactly-once, payment-separation, privacy, and provider-effect guards.

**Verification:** focused resolver/executor/E2E RED→GREEN; proportional profile/Cloudbeds/replay/privacy suite; exact official regression command; Ruff, boundaries, compileall, and `git diff --check`; independent review on the final SHA before push/CI.
