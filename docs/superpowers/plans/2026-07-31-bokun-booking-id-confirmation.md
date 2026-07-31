# Bókun Booking-ID Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confirm a Bókun reservation as soon as a successful submit returns one unambiguous reservation-level booking ID, so a later synchronous GET cannot erase the effect or block the final public reply.

**Architecture:** Keep all existing authenticated command, private binding, cart, checkout, fence, idempotency and submit checks. Change only the post-submit boundary in `BokunHTTPTransport._book_activity_v2()`: once `_booking_reference()` returns one valid conflict-free booking ID, return the canonical `confirmed` result immediately. The existing provider adapter, execution ledger and completion projector then persist `EFFECT_CONFIRMED` and project one final public result through their current interfaces. The separate legacy Bókun payload path remains unchanged.

**Tech Stack:** Python 3.11+, `httpx`, `pytest`, SQLite durable stores, Ruff, GitHub Actions.

## Global Constraints

- Exactly one Bókun submit per authorized command and zero submit retries.
- `2xx + one unambiguous reservation-level booking ID` is monotonic evidence of creation.
- Missing or conflicting booking IDs remain ambiguous after submit and must never become retryable.
- Do not expose raw booking IDs to model context; persist only the existing opaque provider-reference fingerprint in the ledger.
- Do not enable ManyChat, payment, cancellation, email or handoff.
- Do not change Cloudbeds behavior or the public Phase 7 tool catalog.
- Do not change `BokunHTTPTransport._book_activity()`, the separate legacy payload path.
- Do not issue a live provider write while qualifying this patch; use `httpx.MockTransport` and local durable stores.
- The historical booking `99079892` remains unchanged; no direct database patch is permitted.

---

### Task 1: Make booking ID the terminal Bókun write acknowledgment

**Files:**
- Modify: `tests/test_v2_bokun_write_transport.py`
- Modify: `v2_adapters/provider_http.py:1318-1352`

**Interfaces:**
- Consumes: `BokunHTTPTransport._booking_reference(payload: object) -> str | None` and the existing conflict-checking collector behind it.
- Produces: `BokunHTTPTransport.__call__("book_activity", payload, idempotency_key=key) -> {"status": "confirmed", "booking_id": str}` immediately after an accepted V2 submit response with one booking ID.

- [ ] **Step 1: Write the failing v2 transport test**

Rename `test_bokun_write_cart_checkout_submit_and_readback_are_one_fenced_call` to `test_bokun_v2_submit_booking_id_confirms_without_readback`. Keep its existing cart, checkout, submit-body and contact assertions. Delete the `call == 4` read-back response branch and replace it with an explicit guard after the submit branch:

```python
def test_bokun_v2_submit_booking_id_confirms_without_readback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        call = len(seen)
        if call == 3:
            return httpx.Response(
                200,
                request=request,
                json={"booking": {"bookingId": "booking-123", "status": "RESERVED"}},
            )
        raise AssertionError("booking ID confirmation must not depend on read-back")

    result = _transport(handler)(
        "book_activity",
        _dispatch_payload(),
        idempotency_key="idem:bokun-booking-id-confirmed",
    )

    assert result == {"status": "confirmed", "booking_id": "booking-123"}
    assert [request.method for request in seen] == ["POST", "GET", "POST"]
```

The unchanged `call == 1` and `call == 2` branches remain exactly as they are in the parent test.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
env -i HOME=/home/ubuntu PATH=/usr/bin:/bin \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_bokun_write_transport.py::test_bokun_v2_submit_booking_id_confirms_without_readback
```

Expected: FAIL because the current transport performs a fourth `GET /booking.json/booking/booking-123`.

- [ ] **Step 3: Implement the minimal production change**

In `_book_activity_v2()`, keep the existing submit status and booking-reference checks, then return immediately:

```python
booking_id = self._booking_reference(submit_payload)
if booking_id is None:
    if status in {400, 401, 403, 404, 405, 406, 415, 422} or (
        200 <= status < 300
        and isinstance(submit_payload, Mapping)
        and submit_payload.get("success") is False
    ):
        return {"status": "rejected"}
    raise ProviderHTTPError("Bókun write result is ambiguous")
return {"status": "confirmed", "booking_id": booking_id}
```

Delete only the synchronous booking GET and strict read-back block from `_book_activity_v2()`. Do not change `_book_activity()`, `_booking_reference()`, cart validation, checkout economics, submit status handling or idempotency headers.

- [ ] **Step 4: Run the new test and verify GREEN**

Expected: `1 passed`, three provider calls and no booking GET.

- [ ] **Step 5: Run the complete Bókun transport file**

```bash
env -i HOME=/home/ubuntu PATH=/usr/bin:/bin \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_bokun_write_transport.py
```

Rename `test_bokun_multi_passenger_cart_checkout_submit_and_readback` to `test_bokun_multi_passenger_cart_checkout_submit_by_booking_id`, remove its fourth-call response, assert `len(seen) == 3`, and keep all 2-adult + 1-child cart/checkout/submit assertions. Update parameterized V2 success-path expectations in the same mechanical way. Keep dedicated `_validate_booking_readback_v2()` unit tests intact because the validator may be reused by a future read-only auditor. Ambiguous/missing/conflicting booking-ID tests must remain fail-closed with one submit and no retry.

- [ ] **Step 6: Commit the transport behavior**

```bash
git add v2_adapters/provider_http.py tests/test_v2_bokun_write_transport.py
git commit -m "fix(v2): confirm Bokun submit by booking id"
```

---

### Task 2: Prove durable effect and public completion

**Files:**
- Modify: `tests/test_v2_reservations.py`
- Modify: `tests/test_v2_completion_projector.py`

**Interfaces:**
- Consumes: `BokunReservationPort.execute(ProviderDispatchPermit) -> ProviderExecutionResult`, `V2ReservationWorker.run_once(now: datetime) -> V2WorkerResult`, and `CompletionProjector.run_once(now: datetime) -> CompletionProjectionResult`.
- Produces: one durable `ExecutionCertainty.EFFECT_CONFIRMED` outcome with an opaque provider-reference fingerprint and one idempotent public confirmation chunk.

- [ ] **Step 1: Add a provider-port classification test**

Construct `BokunReservationPort` with a callable transport that returns:

```python
{"status": "confirmed", "booking_id": "booking-123"}
```

Execute an exact Bókun `ProviderDispatchPermit` and assert:

```python
assert result.certainty is ProviderCertainty.EFFECT_CONFIRMED
assert result.normalized_status == "confirmed"
assert result.provider_reference_fingerprint == hashlib.sha256(b"booking-123").hexdigest()
```

This test must not expose the raw booking ID in the execution ledger.

- [ ] **Step 2: Run the selector**

Expected: PASS on the parent because the provider-port mapping already recognizes canonical confirmed results. This is a characterization test, not the RED; Task 1 owns the behavior change.

- [ ] **Step 3: Add a worker replay assertion for the confirmed Bókun result**

Use a local SQLite execution store and a Bókun reservation adapter/port. Run the worker twice and assert:

```python
assert first.disposition is V2WorkerDisposition.EFFECT_CONFIRMED
assert second.disposition is V2WorkerDisposition.IDLE
assert ledger.dispatch_slots_consumed == 1
assert state.outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED
assert state.outcome.provider_reference.startswith("provider:bokun:")
assert "booking-123" not in dumps_outcome(state.outcome)
```

The provider callable must be invoked exactly once.

- [ ] **Step 4: Run the worker selector**

Expected: PASS with one durable fence slot, one provider call and no replay.

- [ ] **Step 5: Prove final public projection remains exactly once**

Add `test_single_activity_confirmation_enters_public_outbox_once` using `_stores(tmp_path)`, the first command from `ReservationAllocator().allocate(_package_command()).commands`, `_persist`, `_finish_next(execution, now=NOW + timedelta(seconds=1), certainty=ExecutionCertainty.EFFECT_CONFIRMED)`, `outcome.run_once(now=NOW + timedelta(seconds=2))`, and `_completion`. Assert:

```python
assert first.inserted == 1
assert replay.inserted == 0
assert texts == ("Seu passeio foi confirmado.",)
```

Delivery remains local/pending; do not enable ManyChat or payment.

- [ ] **Step 6: Run the focused durable journey**

```bash
env -i HOME=/home/ubuntu PATH=/usr/bin:/bin \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_bokun_write_transport.py \
  tests/test_v2_reservations.py \
  tests/test_v2_outcome_projector.py \
  tests/test_v2_completion_projector.py \
  tests/test_v2_workers.py
```

Expected: all pass; no external network calls.

- [ ] **Step 7: Commit durable/public tests**

```bash
git add tests/test_v2_reservations.py tests/test_v2_completion_projector.py
git commit -m "test(v2): prove booking id completion journey"
```

---

### Task 3: Qualify and freeze the release candidate

**Files:**
- Modify: `docs/refactor/ACTIVE.md` with the final SHA and evidence after all gates pass.

**Interfaces:**
- Consumes: immutable Git SHA produced by Tasks 1–2.
- Produces: one clean reviewed candidate suitable for deployment qualification, not an automatic production rollout.

- [ ] **Step 1: Run the broader causal suite**

```bash
env -i HOME=/home/ubuntu PATH=/usr/bin:/bin \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_bokun_write_transport.py \
  tests/test_v2_bokun_party_reads.py \
  tests/test_v2_reservations.py \
  tests/test_v2_workers.py \
  tests/test_v2_outcome_projector.py \
  tests/test_v2_completion_projector.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_conversation_reducer.py \
  tests/test_v2_critical_actions.py \
  tests/test_v2_read_bridge.py \
  tests/test_v2_production_composition.py
```

Expected: all pass.

- [ ] **Step 2: Run static and architecture gates**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m ruff check \
  v2_adapters/provider_http.py tests/test_v2_bokun_write_transport.py \
  tests/test_v2_reservations.py tests/test_v2_completion_projector.py
python3 scripts/check_fasttrack_boundaries.py
git diff --check
```

Expected: Ruff clean, `fasttrack-boundaries: OK`, diff check exit `0`.

- [ ] **Step 3: Run the official full test gate**

Run the complete pytest command used by CI, with only the seven established package/closeout deselections. Expected: no new failures compared with candidate `16d4d90`; preserve the exact output artifact privately.

- [ ] **Step 4: Update active-candidate documentation**

Record the final behavior, focused/full test counts, static gates and explicit statement: no live submit was executed for this patch. Do not include PII, raw booking response bodies, credentials or customer data.

- [ ] **Step 5: Commit and authenticate the final candidate**

```bash
git add docs/refactor/ACTIVE.md
git commit -m "docs(v2): freeze booking id confirmation candidate"
git rev-parse HEAD
git rev-parse HEAD^{tree}
test -z "$(git status --porcelain=v1)"
```

- [ ] **Step 6: Request independent terminal review**

Review the exact parent-to-candidate delta. Require an explicit `APPROVE` or findings by severity, focused tests, Ruff, boundary scan, diff check and PII/secret scan. Any code change after review invalidates the verdict.

- [ ] **Step 7: Publish and require exact-SHA CI**

Push only after local qualification and review. Wait for GitHub Actions to report `success` on the exact candidate SHA before calling it deployable.

- [ ] **Step 8: Report release state honestly**

Distinguish:

- code/CI/review ready;
- deployed dark canary;
- controlled canary with effects closed;
- customer traffic enabled.

Do not claim customers are live unless a separately authorized deployment and routing change were actually executed and verified.
