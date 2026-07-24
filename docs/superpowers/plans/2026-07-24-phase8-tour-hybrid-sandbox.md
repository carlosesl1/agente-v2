# Phase 8 Tour and Hybrid Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the effect-denied Luna sandbox with real Bókun tour reads and one-turn Cloudbeds+Bókun hybrid grounding.

**Architecture:** Add a closed activity request/observation beside the existing lodging types, dispatch at most one request per kind through a no-shell child in the V2 read-only worker, and journal multiple observations atomically. Keep the model tool-free and preserve the existing singular observation API for one-read turns.

**Tech Stack:** Python 3.12, dataclasses, SQLite STRICT tables, subprocess without shell, Docker exec, V2 `httpx` transports, pytest/unittest.

## Global Constraints

- Default model remains `openai-codex / gpt-5.6-luna`.
- Bókun provider selection is exclusively `product:buracao`; names never resolve inside provider code.
- Model receives zero tools; reads are parent-owned.
- Runtime mode must be `dark_read_only` and all four effect gates false.
- No ManyChat send, provider write, reservation, payment, deploy, merge or rollout.
- Public replies and observations must not expose canonical/provider IDs or raw payloads.
- Provider calls remain outside SQLite write transactions.
- A failed turn persists no partial user, assistant, read or effect rows.

---

### Task 1: Closed activity contract

**Files:**
- Modify: `reservation_boundary/sandbox.py`
- Test: `tests/test_phase8_fasttrack_sandbox.py`

**Interfaces:**
- Produces: `ActivityAvailabilityReadRequest.from_mapping()`, `ActivityAvailabilityObservation.from_canonical_bytes()`, `SandboxReadRequest`, `SandboxReadObservation`.
- Consumes: existing `_iso_date`, `_bounded_int`, `_canonical_json`, `_text` validators.

- [ ] **Step 1: Write failing request and observation tests**

Add tests that parse only this exact request:

```python
{
    "kind": "activity_availability",
    "arguments": {
        "product_id": "product:buracao",
        "activity_date": "2026-08-05",
        "participants": 2,
    },
}
```

Reject names, unknown/extra fields, booleans as participant counts, malformed IDs and non-canonical dates. Add an observation fixture with `product_public_name`, `activity_date`, `participants`, `available`, canonical BRL price and `raw_provider_payload_returned=false`; reject any `product_id`, `bokun_product_id`, raw payload or malformed money.

- [ ] **Step 2: Verify RED**

Run:

```bash
/tmp/v2-ci-exact/bin/python -m pytest -q \
  tests/test_phase8_fasttrack_sandbox.py -k 'activity_contract'
```

Expected: fail because activity contract classes and parser dispatch do not exist.

- [ ] **Step 3: Implement minimum closed types**

In `reservation_boundary/sandbox.py`, add:

```python
_PRODUCT_ID_RE = re.compile(r"^product:[a-z0-9][a-z0-9._-]{0,127}$")

@dataclass(frozen=True, slots=True)
class ActivityAvailabilityReadRequest:
    product_id: str
    activity_date: str
    participants: int

@dataclass(frozen=True, slots=True)
class ActivityAvailabilityObservation:
    status: str
    activity_date: str
    participants: int
    product_public_name: str
    availability_confirmed: bool
    price_confirmed: bool
    total_amount: str | None
    currency: str | None
    public_summary: str
    raw_provider_payload_returned: bool = False
```

Dispatch read parsing by exact `kind`, cap requests at two and reject duplicate kinds.

- [ ] **Step 4: Verify GREEN and commit**

Run the focused selector, then:

```bash
git add reservation_boundary/sandbox.py tests/test_phase8_fasttrack_sandbox.py
git commit -m "feat(sandbox): add closed activity read contract"
```

---

### Task 2: Atomic multi-observation journal and read loop

**Files:**
- Modify: `reservation_boundary/sandbox.py`
- Test: `tests/test_phase8_fasttrack_sandbox.py`

**Interfaces:**
- Consumes: `tuple[SandboxReadRequest, ...]` and `tuple[SandboxReadObservation, ...]` from Task 1.
- Produces: `SandboxTurnResult.read_observations`, compatible `read_observation` property, `SQLiteSandboxStore.append_turn(..., observations=...)`.

- [ ] **Step 1: Write RED tests**

Add a hybrid response with one lodging and one activity request. Assert both reads happen outside a SQLite write transaction, both private observations enter one `READ_OBSERVATIONS` array, the final response cannot request another read, and exactly two rows persist under one ordinal. Add a migration fixture with the old `(session_id,ordinal)` table and prove initialization preserves its row while allowing a second kind.

- [ ] **Step 2: Verify RED**

Run:

```bash
/tmp/v2-ci-exact/bin/python -m pytest -q \
  tests/test_phase8_fasttrack_sandbox.py -k 'hybrid_read_loop or observation_migration'
```

Expected: fail because the current parser caps reads at one and the table key lacks kind.

- [ ] **Step 3: Implement minimum orchestration**

Validate every request before dispatch. Execute reads in request order outside transactions. Build:

```python
READ_OBSERVATIONS={
  "items": [
    {"kind": request.kind, "hash": observation.canonical_hash(),
     "observation": json.loads(observation.to_canonical_bytes())}
  ]
}
```

Persist all final observations in the same transaction as the public turn. Migrate the old table by rename/create/copy/drop inside one transaction. Preserve `result.read_observation` only when exactly one observation exists.

- [ ] **Step 4: Verify GREEN and commit**

Run focused tests and commit:

```bash
git add reservation_boundary/sandbox.py tests/test_phase8_fasttrack_sandbox.py
git commit -m "feat(sandbox): journal hybrid provider reads atomically"
```

---

### Task 3: Read-only V2 provider child

**Files:**
- Create: `scripts/phase8_v2_provider_read_child.py`
- Modify: `reservation_boundary/sandbox.py`
- Test: `tests/test_phase8_fasttrack_sandbox.py`

**Interfaces:**
- Produces: `V2ProviderDockerRead.read(request: SandboxReadRequest) -> SandboxReadObservation`.
- Consumes: `V2_CLOUDBEDS_*`, `V2_BOKUN_*`, `V2_BOKUN_PRODUCT_MAP_JSON`, and existing V2 HTTP read transports inside the worker container.

- [ ] **Step 1: Write RED child and adapter tests**

Prove the child rejects non-dark runtime and any true effect gate, strips Cloudbeds/Bókun private IDs, binds returned dates/participant counts, and emits only `PHASE8_V2_READ_RESULT\0` plus canonical DTO. Prove the adapter invokes:

```python
("docker", "exec", "-i", worker_container, "/usr/local/bin/python", "-c", child_source)
```

without `sh` or `bash`, and fails closed on a missing marker, nonzero exit or request mismatch.

- [ ] **Step 2: Verify RED**

Run:

```bash
/tmp/v2-ci-exact/bin/python -m pytest -q \
  tests/test_phase8_fasttrack_sandbox.py -k 'v2_provider_child or v2_provider_adapter'
```

Expected: fail because the child and adapter do not exist.

- [ ] **Step 3: Implement the child and adapter**

The child must check:

```python
assert os.environ["V2_RUNTIME_MODE"] == "dark_read_only"
for name in (
    "V2_ENABLE_CLOUDBEDS_WRITES",
    "V2_ENABLE_BOKUN_WRITES",
    "V2_ENABLE_STRIPE_LINKS",
    "V2_ENABLE_MANYCHAT_DELIVERY",
):
    assert os.environ.get(name, "false").casefold() in {"false", "0"}
```

Then instantiate only `CloudbedsHTTPTransport` or `BokunHTTPTransport`, sanitize their closed result, and emit no credentials or provider IDs.

- [ ] **Step 4: Verify GREEN and commit**

Run focused tests, `py_compile`, Ruff and commit:

```bash
git add scripts/phase8_v2_provider_read_child.py \
  reservation_boundary/sandbox.py tests/test_phase8_fasttrack_sandbox.py
git commit -m "feat(sandbox): read tours through V2 dark worker"
```

---

### Task 4: Reusable source runner

**Files:**
- Modify: `scripts/run_phase8_sandbox.py`
- Test: `tests/test_phase8_fasttrack_sandbox.py`

**Interfaces:**
- Consumes: `V2ProviderDockerRead` and internal catalog knowledge.
- Produces: interactive and `--message` runner capable of lodging, tour and hybrid reads.

- [ ] **Step 1: Write RED runner tests**

Assert `_knowledge(None)` contains a private closed catalog mapping Buracão to `product:buracao`, that the notice requires provider observations for both services, and that the default read container is `agente-v2-digest-canary-169a67c-worker` with `V2ProviderDockerRead`.

- [ ] **Step 2: Verify RED**

Run the focused runner selector and observe the old Cloudbeds-only default fail.

- [ ] **Step 3: Implement minimum runner change**

Replace `--cloudbeds-container` with `--read-worker-container`, instantiate `V2ProviderDockerRead`, and print one sanitized stderr line per observation kind/hash. Keep the Luna default centralized.

- [ ] **Step 4: Verify GREEN and commit**

Run the sandbox suite and commit:

```bash
git add scripts/run_phase8_sandbox.py tests/test_phase8_fasttrack_sandbox.py
git commit -m "feat(sandbox): enable tour and hybrid source runs"
```

---

### Task 5: Real Luna smokes and human gate

**Files:**
- Create outside Git: `~/.local/share/agente-v2-phase8-evidence/sandbox/<timestamp>-tour-hybrid-luna/`
- No production file changes.

**Interfaces:**
- Consumes: committed source runner, live V2 read-only worker and Luna model.
- Produces: two private journals, sanitized `smoke.json`, `report.md`, checksums and a human-test command.

- [ ] **Step 1: Preflight**

Instantiate the real Luna child and assert zero tool names/objects. Snapshot 12 productive command/workflow/outbox/payment table counts and require zero. Confirm worker readiness and all effect gates false.

- [ ] **Step 2: Run tour smoke**

Use the exact three-turn script from the design. Allow one identical retry only for a protocol-invalid, provably non-persisted turn. Require Bókun status `ok`, date/participants binding, no IDs in public reply and blocked booking/payment/delivery proposals.

- [ ] **Step 3: Run hybrid smoke**

Use the exact hybrid script. Require exactly one lodging and one activity observation under the same accepted turn, public response grounded in both, and all requested effects blocked.

- [ ] **Step 4: Verify no effects and regression**

Recount all productive tables, inspect logs for errors/delivery markers, run:

```bash
/tmp/v2-ci-exact/bin/python -m pytest -q tests/test_phase8_fasttrack_sandbox.py
/tmp/v2-ci-exact/bin/python -B -m py_compile \
  reservation_boundary/sandbox.py scripts/run_phase8_sandbox.py \
  scripts/phase8_v2_provider_read_child.py scripts/phase8_hermes_child.py
/tmp/v2-ci-exact/bin/ruff check reservation_boundary/sandbox.py \
  scripts/run_phase8_sandbox.py scripts/phase8_v2_provider_read_child.py \
  tests/test_phase8_fasttrack_sandbox.py
git diff --check
```

- [ ] **Step 5: Publish branch and stop at human gate**

Commit only source/test changes, push normally, wait for exact-SHA CI, and report the private command Carlos can use. Do not simulate his conversation and do not enter controlled writes.
