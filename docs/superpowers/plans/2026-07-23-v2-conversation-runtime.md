# V2 Conversation Runtime Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect signed ManyChat ingress to one deterministic Phase 8/v8 conversation owner and the existing reservation/payment/public workers, then qualify lodging+Stripe, activity+Pix and package+Wise from webhook to completion using fake providers.

**Architecture:** Keep `SQLiteBoundaryStore` v8 as the only conversational owner. A capability-free Maya proposal supplies public facts, reads, selection, confirmation and reply text; deterministic application code joins private profile/provider bindings, drives the reservation-domain reducer, and atomically commits v8 artifacts/relays. Concrete workers relay canonical commands into the existing execution/followup stores and process the fixed seven-stage cycle.

**Tech Stack:** Python 3.12, dataclasses, SQLite STRICT/WAL, FastAPI, existing reservation boundary/domain/execution/followup packages, pytest, Docker.

## Global Constraints

- `v2_host` is the only production composition root.
- Do not import or execute the legacy agent, planner or cognitive runtime.
- Do not create a second conversation, reservation or financial ledger.
- Maya remains tool-free and receives public observations only.
- Provider IDs and customer profile values stay in private bindings.
- No model/provider call occurs inside a SQLite transaction.
- Every effect requires durable authorization, idempotency key, fence and receipt.
- Active handoff blocks commercial admission and pre-fence reservation dispatch.
- All real-effect gates remain false by default and no live effect is authorized.
- Use RED → GREEN → architecture guard → proportional regressions → diff review → functional commit → separate `ACTIVE.md` commit.

---

### Task 1: Productive model grammar and private customer profile

**Files:**
- Modify: `v2_contracts/model.py`
- Create: `v2_contracts/profile.py`
- Modify: `v2_application/turns.py`
- Create: `v2_adapters/manychat_profile.py`
- Create: `tests/test_v2_profile_and_model_grammar.py`

**Interfaces:**
- Produces: `PaymentMethodFact`, `PrivateCustomerBinding`, `CustomerProfileReadPort`, `ManyChatProfileAdapter`, `validate_productive_proposal(proposal: ModelProposal) -> ModelProposal`.
- Consumes: canonical `manychat:<subscriber_id>` lead IDs and a read-only transport callable.

- [ ] **Step 1: Write RED for productive proposal closure**

```python
def test_productive_proposal_rejects_effects_and_closes_payment_method():
    valid = ModelProposal(
        source_event_id="event:001",
        intent="inform",
        reply_chunks=("Recebi sua preferência de pagamento.",),
        facts=(ModelFact("payment_method", "pix"),),
        read_requests=(),
        effect_proposals=(),
    )
    assert validate_productive_proposal(valid) is valid
    with pytest.raises(InvalidModelProposal, match="effect proposals"):
        validate_productive_proposal(replace(valid, effect_proposals=(EffectProposal("write", {}),)))
    with pytest.raises(InvalidModelProposal, match="payment_method"):
        ModelFact("payment_method", "cash")
```

- [ ] **Step 2: Write RED for private profile isolation**

```python
def test_profile_adapter_returns_private_binding_without_public_serialization():
    binding = ManyChatProfileAdapter(FakeProfileTransport()).read("manychat:subscriber-001", now=NOW)
    assert binding.complete is True
    assert binding.binding_id.startswith("profile-binding:")
    assert "person@example.invalid" not in repr(binding)
    request = model_request_from(binding=binding)
    assert "person@example.invalid" not in repr(request)
```

- [ ] **Step 3: Run RED**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_profile_and_model_grammar.py
```

Expected: collection fails because `v2_contracts.profile` and the productive validator do not exist.

- [ ] **Step 4: Implement exact contracts and adapter**

```python
@dataclass(frozen=True, slots=True, repr=False)
class PrivateCustomerBinding:
    binding_id: str
    content_hash: str
    full_name: str
    email: str
    phone_e164: str
    country_code: str
    observed_at: datetime
    expires_at: datetime
    complete: bool

class CustomerProfileReadPort(Protocol):
    def read(self, lead_id: str, *, now: datetime) -> PrivateCustomerBinding:
        raise NotImplementedError

def validate_productive_proposal(proposal: ModelProposal) -> ModelProposal:
    if proposal.effect_proposals:
        raise InvalidModelProposal("productive proposal rejects effect proposals")
    return proposal
```

`ManyChatProfileAdapter` validates exact closed transport fields, normalizes lead identity, computes binding/content hashes, never exposes PII in `repr`, and classifies missing required values as `complete=False` rather than inventing them.

- [ ] **Step 5: Run GREEN and ingress/model regressions**

```bash
uv run --no-project --with 'pytest>=8.0.0' --with 'fastapi>=0.115.0' --with 'httpx>=0.27.0' python -m pytest -q tests/test_v2_profile_and_model_grammar.py tests/test_v2_turns.py tests/test_v2_reads.py tests/test_v2_manychat_ingress.py
```

Expected: PASS.

- [ ] **Step 6: Run guard/lint and commit**

```bash
python scripts/check_fasttrack_boundaries.py
uvx ruff check v2_contracts/model.py v2_contracts/profile.py v2_application/turns.py v2_adapters/manychat_profile.py tests/test_v2_profile_and_model_grammar.py
git diff --check
git add v2_contracts v2_application/turns.py v2_adapters/manychat_profile.py tests/test_v2_profile_and_model_grammar.py
git commit -m "feat: close v2 model grammar and private profile"
```

---

### Task 2: Deterministic conversation reducer

**Files:**
- Create: `v2_application/conversation.py`
- Modify: `v2_application/reads.py`
- Modify: `v2_application/reservations.py`
- Create: `tests/test_v2_conversation_reducer.py`

**Interfaces:**
- Consumes: authenticated `BoundaryState`, productive `ModelProposal`, tuple of public `ReadObservation`, private provider bindings, `PrivateCustomerBinding`, UTC instant.
- Produces: `V2ConversationDecision(next_state, projection, commands, public_reply, handoff_request, receipt_requirements)`.

- [ ] **Step 1: Write RED for no-command states**

```python
def test_incomplete_profile_and_stale_confirmation_never_emit_command():
    incomplete = reducer.reduce(genesis(), proposal_for("confirm"), profile=INCOMPLETE, reads=READS, now=NOW)
    assert incomplete.commands == ()
    assert incomplete.public_reply.kind == "profile_completion"
    stale = reducer.reduce(summary_state(version=2), confirm(version=1), profile=COMPLETE, reads=READS, now=NOW)
    assert stale.commands == ()
```

- [ ] **Step 2: Write RED for authoritative single/package commands**

```python
def test_confirmed_summary_emits_domain_command_only():
    decision = drive_to_confirmation(service="lodging", payment_method="stripe")
    assert len(decision.commands) == 1
    assert type(decision.commands[0]) is ReservationCommand
    assert decision.commands[0].operation is ReservationOperation.RESERVE_LODGING

def test_package_has_one_summary_one_confirmation_and_two_allocated_components():
    decision = drive_to_confirmation(service="package", payment_method="wise")
    assert decision.commands[0].operation is ReservationOperation.RESERVE_PACKAGE
    assert len(ReservationAllocator().allocate(decision.commands[0]).commands) == 2
```

- [ ] **Step 3: Run RED**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_conversation_reducer.py
```

Expected: fails because `V2ConversationReducer` does not exist.

- [ ] **Step 4: Implement reducer as a pure state machine**

Use existing reservation-domain constructors/reducer for `StartSearch`, `LookupRecorded`, `OfferChosen`, `DraftRequested`, summary preparation and explicit confirmation. Build `CustomerFacts` only from `PrivateCustomerBinding`; build `EconomicTerms` only from the closed payment-method fact. Join public offer IDs to private bindings through `V2ReadCoordinator`; never accept model provider IDs. Represent profile prompt history by prior public receipt kind in boundary history, not a boolean field.

- [ ] **Step 5: Run GREEN and domain/property regressions**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_conversation_reducer.py tests/test_phase2_properties.py tests/test_v2_reservations.py tests/test_v2_recovery.py
```

Expected: PASS.

- [ ] **Step 6: Guard/lint/commit**

```bash
python scripts/check_fasttrack_boundaries.py
uvx ruff check v2_application/conversation.py v2_application/reads.py v2_application/reservations.py tests/test_v2_conversation_reducer.py
git diff --check
git add v2_application tests/test_v2_conversation_reducer.py
git commit -m "feat: add deterministic v2 conversation reducer"
```

---

### Task 3: Atomic v8 turn executor

**Files:**
- Create: `v2_application/turn_executor.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Create: `tests/test_v2_turn_executor.py`

**Interfaces:**
- Produces: `V2TurnExecutor.execute(batch, model, reads, profile, now) -> V2CommittedTurn`.
- Uses: `SQLiteBoundaryStore.commit_turn_v8` with exact artifacts, relays, internal jobs, public rows and `TurnReceipt`.

- [ ] **Step 1: Write RED for model-outside-transaction and exact replay**

```python
def test_turn_executor_calls_model_outside_transaction_and_replays_receipt():
    first = executor.execute(BATCH, now=NOW)
    second = executor.execute(BATCH, now=NOW + SECOND)
    assert first.receipt == second.receipt
    assert model.calls == 1
    assert store.turn_receipt_count(LEAD_ID, BATCH.batch_id) == 1
```

- [ ] **Step 2: Write RED for crash boundaries**

```python
def test_crash_after_commit_before_inbox_completion_reuses_turn():
    receipt = executor.execute(BATCH, fault="after_commit").receipt
    replay = executor.execute(BATCH).receipt
    assert replay == receipt
    assert store.internal_job_count(receipt.aggregate_turn_id) == EXPECTED_JOBS
```

- [ ] **Step 3: Run RED**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_turn_executor.py
```

- [ ] **Step 4: Implement the executor**

Load/acquire the boundary fence in a short transaction, call profile/read/model outside transactions, run the pure reducer, build canonical Phase 8 artifacts and one receipt, then call `commit_turn_v8` once. Add only read-only count/load methods required for idempotent replay; do not add a new table family.

- [ ] **Step 5: Run GREEN and v8 semantic regressions**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_turn_executor.py tests/test_phase8_boundary_schema_v8.py tests/test_phase8_boundary_semantic_scan.py
```

Expected: PASS.

- [ ] **Step 6: Guard/lint/commit**

```bash
python scripts/check_fasttrack_boundaries.py
uvx ruff check v2_application/turn_executor.py reservation_boundary/sqlite_store.py tests/test_v2_turn_executor.py
git diff --check
git add v2_application/turn_executor.py reservation_boundary/sqlite_store.py tests/test_v2_turn_executor.py
git commit -m "feat: commit v2 conversation turns atomically"
```

---

### Task 4: Inbox and relay workers

**Files:**
- Create: `v2_application/inbox_worker.py`
- Create: `v2_application/relay_worker.py`
- Modify: `v2_host/worker_main.py`
- Create: `tests/test_v2_inbox_relay_workers.py`

**Interfaces:**
- Produces: concrete `InboxTurnWorker.run_once(now)`, `BoundaryRelayWorker.run_once(now)`, and seven-stage `WorkerCycle` entries.
- Consumes: existing inbox leases, v8 internal jobs, execution/payment/public owner ports.

- [ ] **Step 1: Write RED for inbox replay and relay exactly once**

```python
def test_inbox_crash_after_turn_commit_and_relay_replays_without_duplicates():
    assert inbox_worker.run_once(now=NOW, fault="before_inbox_complete").value == "committed"
    assert inbox_worker.run_once(now=NOW + SECOND).value == "completed"
    assert relay_worker.run_once(now=NOW + TWO_SECONDS).value == "relayed"
    assert relay_worker.run_once(now=NOW + THREE_SECONDS).value == "idle"
    assert execution.command_count(COMMAND_ID) == 1
```

- [ ] **Step 2: Write RED that no stage is noop/fallback**

```python
def test_worker_composition_has_seven_concrete_stages(container):
    cycle = build_worker_cycle(container, providers=FAKES)
    assert tuple(cycle.workers) == tuple(WorkerQueue)
    assert all(type(worker).__name__ not in {"NoopWorker", "FallbackWorker"} for worker in cycle.workers.values())
```

- [ ] **Step 3: Run RED**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_inbox_relay_workers.py
```

- [ ] **Step 4: Implement concrete workers**

Complete inbox claims only after a committed/replayed receipt. Relay canonical boundary command/relay/public rows using deterministic target IDs and existing store APIs. Store attempts/leases in the v8 internal outbox rows. Refuse `build_worker_cycle` if any provider or stage is absent.

- [ ] **Step 5: Run GREEN and worker regressions**

```bash
uv run --no-project --with 'pytest>=8.0.0' python -m pytest -q tests/test_v2_inbox_relay_workers.py tests/test_v2_worker_main.py tests/test_v2_completion.py tests/test_phase5_reconciliation.py tests/test_phase6_reconciliation.py
```

- [ ] **Step 6: Guard/lint/commit**

```bash
python scripts/check_fasttrack_boundaries.py
uvx ruff check v2_application/inbox_worker.py v2_application/relay_worker.py v2_host/worker_main.py tests/test_v2_inbox_relay_workers.py
git diff --check
git add v2_application v2_host/worker_main.py tests/test_v2_inbox_relay_workers.py
git commit -m "feat: compose durable v2 inbox and relay workers"
```

---

### Task 5: Financial webhooks, receipt correlation and complete host

**Files:**
- Modify: `v2_host/settings.py`
- Modify: `v2_host/app.py`
- Modify: `v2_host/api_main.py`
- Modify: `v2_host/composition.py`
- Modify: `v2_host/worker_main.py`
- Modify: `compose.v2.yaml`
- Create: `tests/test_v2_complete_host.py`

**Interfaces:**
- Produces: authenticated provider-specific webhook routes, `build_worker_cycle`, API/worker roles in one image, and receipt-derived completion query.

- [ ] **Step 1: Write RED for authenticated evidence**

```python
def test_financial_webhook_verifies_before_persist_and_replays():
    unauthorized = client.post("/webhook/payments/stripe", content=BODY)
    first = client.post("/webhook/payments/stripe", headers=SIGNED, content=BODY)
    replay = client.post("/webhook/payments/stripe", headers=SIGNED, content=BODY)
    assert unauthorized.status_code == 401
    assert first.status_code == 202
    assert replay.status_code == 200
    assert followup.evidence_count(EVIDENCE_ID) == 1
```

- [ ] **Step 2: Write RED for owner counts/readiness**

```python
def test_api_and_worker_roles_have_least_privilege_and_concrete_readiness():
    assert api.owner_counts() == API_OWNER_COUNTS
    assert worker.owner_counts() == WORKER_OWNER_COUNTS
    assert worker.readiness().status == "ready"
    assert settings.all_real_effect_gates_closed
```

- [ ] **Step 3: Run RED**

```bash
uv run --no-project --with 'pytest>=8.0.0' --with 'fastapi>=0.115.0' --with 'httpx>=0.27.0' python -m pytest -q tests/test_v2_complete_host.py
```

- [ ] **Step 4: Implement strict webhook and role composition**

Use provider verification ports before normalized evidence creation. Open short-lived followup UOWs in API request scope. Build worker ports from settings only when all required fake/real configurations are exact. Add worker service to compose with the same image and a different module command. `/readyz` reports role-specific store authentication and closed gate map.

- [ ] **Step 5: Run GREEN and host regressions**

```bash
uv run --no-project --with 'pytest>=8.0.0' --with 'fastapi>=0.115.0' --with 'httpx>=0.27.0' python -m pytest -q tests/test_v2_complete_host.py tests/test_v2_composition.py tests/test_v2_manychat_ingress.py tests/test_v2_payment_evidence.py
```

- [ ] **Step 6: Guard/lint/commit**

```bash
python scripts/check_fasttrack_boundaries.py
uvx ruff check v2_host tests/test_v2_complete_host.py
git diff --check
git add v2_host compose.v2.yaml tests/test_v2_complete_host.py
git commit -m "feat: complete v2 api and worker composition"
```

---

### Task 6: Signed-webhook E2E, image and final gate

**Files:**
- Modify: `tests/test_v2_e2e.py`
- Modify: `scripts/run_v2_e2e.py`
- Modify: `Dockerfile.v2`
- Modify: `docs/refactor/extraction-evidence/task9-host-checkpoint.md`
- Modify: `docs/refactor/ACTIVE.md`

**Interfaces:**
- Produces: three mandatory webhook-to-completion fake-provider scenarios and final Task 9 evidence.

- [ ] **Step 1: Replace direct-store qualification with signed webhook scenarios**

Each test posts a signed ManyChat payload, runs the concrete seven-stage cycle to idle, posts verified settlement evidence when applicable, runs to idle again, and asserts:

```python
assert runtime.completed(workflow_id) is True
assert runtime.provider_call_counts == EXPECTED_ONE_PER_IDEMPOTENCY_KEY
assert runtime.public_delivery_count == EXPECTED_CHUNKS
assert runtime.owner_counts == EXPECTED_OWNER_COUNTS
assert runtime.settings.all_real_effect_gates_closed is True
```

Keep exact scenarios: lodging+Stripe, activity+Pix, package+Wise.

- [ ] **Step 2: Run the three E2Es on host**

```bash
uv run --no-project --with 'pytest>=8.0.0' --with 'fastapi>=0.115.0' --with 'httpx>=0.27.0' python scripts/run_v2_e2e.py
```

Expected JSON: `status=passed`, `providers=fake_only`, `real_effects=false`.

- [ ] **Step 3: Run repository-wide final suite and static gates**

```bash
uv run --no-project --with 'pytest>=8.0.0' --with 'fastapi>=0.115.0' --with 'httpx>=0.27.0' --with 'pydantic>=2.8.0' python -m pytest -q
python scripts/check_fasttrack_boundaries.py
python -m compileall -q v2_contracts v2_application v2_adapters v2_host
uvx ruff check v2_contracts v2_application v2_adapters v2_host tests/test_v2_*.py
git diff --check
```

Expected: all green. `tests/test_phase7_package.py` remains excluded only if it still asserts historical metadata `0.7.0`; record the exact reason and run every other test.

- [ ] **Step 4: Build and qualify the image**

```bash
docker build -f Dockerfile.v2 -t agente-v2:task9-final .
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --cap-drop ALL --security-opt no-new-privileges \
  --entrypoint python agente-v2:task9-final scripts/run_v2_e2e.py
```

Expected: image build guard green and runner `status=passed` as UID 10001.

- [ ] **Step 5: Review and commit functional candidate**

```bash
git diff --check
git add v2_contracts v2_application v2_adapters v2_host tests scripts Dockerfile.v2 compose.v2.yaml docs/refactor/extraction-evidence
git commit -m "feat: complete standalone v2 agent runtime"
```

- [ ] **Step 6: Record Task 9 in a separate control commit**

Update `docs/refactor/ACTIVE.md` with the functional SHA, set Task 9 `DONE`, remove `NEXT`, retain rollout `NO-GO`, then:

```bash
git add docs/refactor/ACTIVE.md
git commit -m "docs: complete v2 fasttrack implementation"
```

No push, merge, deploy, restart, real provider call or public ManyChat message is part of this plan.
