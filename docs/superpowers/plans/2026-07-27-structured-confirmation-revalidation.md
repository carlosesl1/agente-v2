# Structured Confirmation Revalidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a typed confirmation re-read the exact pending reservation draft in the same turn before any command can be emitted.

**Architecture:** Add one pure helper in `v2_application/turn_executor.py` that derives `ReadRequest` values from `AwaitingConfirmationState` and structured projection facts. The executor uses those reads only when the first validated proposal is a matching `confirm` with no model-provided reads; the reducer remains the final equality gate.

**Tech Stack:** Python 3.11+, dataclasses, existing Phase 8 contracts, pytest, Docker image build, Hermes Luna live prompt evaluation.

## Global Constraints

- No message text matching, keyword list, regex, or substring test may participate in the decision.
- A stale summary version must remain fail-closed without provider reads or commands.
- Provider observations must match the draft exactly through `_reads_bind_draft`.
- Cloudbeds, Bókun and Stripe writes remain disabled until the fixed candidate passes unit regression and live preflight.
- Stripe must stay in test mode.
- `uv.lock` is pre-existing, untracked, and must remain untouched.
- Do not merge `main` or expand a live allowlist.

---

### Task 1: Derive confirmation reads from authenticated draft state

**Files:**
- Modify: `tests/test_v2_turn_executor.py:870-1070`
- Modify: `v2_application/turn_executor.py:37-60,475-555`
- Modify: `config/v2_luna_system_prompt.txt:96-100`

**Interfaces:**
- Consumes: `AwaitingConfirmationState`, `ConversationProjection`, `ModelProposal`.
- Produces: `_confirmation_read_requests(state, projection, proposal) -> tuple[ReadRequest, ...]`.

- [ ] **Step 1: Change the existing confirmation integration test to reproduce the bug**

Replace the explicit first-round confirmation read proposal with the same `confirmation` proposal that has `read_requests=()`. Keep a second confirmation proposal for the post-read frame. Assert that the read port receives one derived request with the draft's exact dates and party:

```python
proposals = [
    selection_read_proposal,
    selection,
    confirmation,
    confirmation,
]
# ... execute both batches
assert len(read_port.calls) == 2
assert read_port.calls[-1].kind is ReadKind.LODGING
assert read_port.calls[-1].check_in == date(2026, 8, 10)
assert read_port.calls[-1].check_out == date(2026, 8, 12)
assert len(confirmed.receipt.command_rows) == 1
```

- [ ] **Step 2: Add fail-closed cases**

Add focused tests that reuse an awaiting confirmation state:

```python
def test_inform_does_not_derive_confirmation_read(...):
    # initial proposal is inform with no reads
    assert read_port.calls == []
    assert result.receipt.command_rows == ()


def test_stale_confirmation_version_does_not_derive_read(...):
    # confirmed_summary_version differs from draft.version
    assert read_port.calls == []
    assert result.receipt.command_rows == ()
```

- [ ] **Step 3: Run RED**

Run:

```bash
env -i PATH="$PATH" HOME="$HOME" \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/agent.json \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_turn_executor.py::test_confirmed_turn_commits_reservation_command_and_relay_atomically
```

Expected: FAIL because only three model proposals are consumed and no derived read occurs; the confirmation receipt has zero command rows.

- [ ] **Step 4: Implement the pure derivation helper**

Add exact imports and helper:

```python
from reservation_domain import (
    AwaitingConfirmationState,
    ReservationCommand,
    ServiceKind,
    dumps_command,
)
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest


def _confirmation_read_requests(
    state,
    projection: ConversationProjection,
    proposal: ModelProposal,
) -> tuple[ReadRequest, ...]:
    workflow = state.workflow
    if (
        type(workflow) is not AwaitingConfirmationState
        or proposal.intent != "confirm"
        or proposal.read_requests
        or proposal.confirmed_summary_version != workflow.draft.version
    ):
        return ()
    values = {fact.name: fact.value.value for fact in projection.facts}
    requests: list[ReadRequest] = []
    for component in workflow.draft.components:
        if component.service is ServiceKind.LODGING:
            if component.end_date is None:
                return ()
            requests.append(ReadRequest(
                request_id=f"{proposal.source_event_id}:confirm-read:lodging",
                kind=ReadKind.LODGING,
                check_in=component.start_date,
                check_out=component.end_date,
                adults=component.party.adults,
                children=component.party.children,
            ))
        elif component.service is ServiceKind.ACTIVITY:
            product_id = values.get("product_id")
            if type(product_id) is not str or not product_id:
                return ()
            requests.append(ReadRequest(
                request_id=f"{proposal.source_event_id}:confirm-read:activity",
                kind=ReadKind.ACTIVITY,
                product_id=product_id,
                activity_date=component.start_date,
                participants=component.party.adults,
            ))
        else:
            return ()
    return tuple(requests)
```

At the executor read boundary:

```python
read_requests = first_proposal.read_requests or _confirmation_read_requests(
    current.state,
    projection,
    first_proposal,
)
derived_confirmation_reads = bool(read_requests) and not first_proposal.read_requests
```

After the second model call, preserve the already-authenticated confirmation closure if the second proposal changes intent:

```python
if derived_confirmation_reads and proposal.intent != "confirm":
    proposal = replace(
        first_proposal,
        reply_chunks=proposal.reply_chunks,
        read_requests=(),
    )
```

- [ ] **Step 5: Clarify the model protocol**

Add under `RESUMO E CONFIRMAÇÃO`:

```text
- Em intent=confirm, se state_facts contêm a consulta completa, peça a consulta atual no primeiro frame do mesmo turno; após observations, repita confirm com a mesma confirmed_summary_version e read_requests=[].
```

- [ ] **Step 6: Run GREEN and focused regression**

Run:

```bash
env -i PATH="$PATH" HOME="$HOME" \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/agent.json \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_turn_executor.py tests/test_v2_conversation_reducer.py
```

Expected: all selected tests pass, zero warnings/errors.

- [ ] **Step 7: Commit**

```bash
git add v2_application/turn_executor.py config/v2_luna_system_prompt.txt tests/test_v2_turn_executor.py
git commit -m "fix: revalidate pending draft on confirmation"
```

### Task 2: Prove the fix before and after real provider writes

**Files:**
- Create outside repo: `/home/ubuntu/workspace/maya-v2-real-whatsapp-stripe-*/evidence/*`
- Do not modify tracked source during this task.

**Interfaces:**
- Consumes: fixed local Docker image and isolated E2E state stores.
- Produces: preflight transcript, reservation references, Stripe test links, provider read-back evidence, checksums.

- [ ] **Step 1: Run the clean project regression**

Run the repository's established clean-environment command with explicit config path and verify zero failures. Keep `uv.lock` untracked and unchanged.

- [ ] **Step 2: Build a local immutable test image**

```bash
docker build -t maya-v2-real-e2e:<commit-sha> .
docker image inspect maya-v2-real-e2e:<commit-sha> --format '{{.Id}}'
```

Record the image ID and use that exact image for every subsequent turn.

- [ ] **Step 3: Re-run hostel and 4Ps preflight with all writes closed**

For each scenario, require:

```text
all pre-confirmation turns: command_rows == 0
final confirmation: command_rows == 1
provider reservation rows: 0
Stripe offers: 0
handoff rows: 0
```

- [ ] **Step 4: Execute exactly two real test workflows**

Open only Cloudbeds write, Bókun write and Stripe link gates in the isolated harness. Keep ManyChat delivery, handoff, Pix and Wise closed. Use:

```text
Hostel: 2026-11-10 to 2026-11-12, one adult, cheapest available shared room.
Agency: Roteiro 4Ps, 2026-11-11, one adult.
Customer: Marina Teste Novembro / maya.qa.20260727@example.com / synthetic phone.
Stripe: test mode only; do not complete payment.
```

- [ ] **Step 5: Read back every effect**

Verify each reservation through its provider API. Query Stripe test payment links and map each URL to its `plink_...` ID. Assert:

```text
hostel amount = exact reservation total (100%)
agency amount = exact 20% deposit, ROUND_HALF_UP
all payment links active and livemode=false
no duplicate provider references
```

- [ ] **Step 6: Freeze evidence and report cancellation identifiers**

Write transcript and result JSON files, generate SHA-256 checksums, and report:

```text
Cloudbeds reservation ID
Bókun booking ID / confirmation code if available
V2 command ID, workflow ID, draft ID and idempotency key
Stripe payment ID, link URL and plink ID
Dates, product/room and amounts
```

Do not cancel the reservations unless Carlos asks; he requested the IDs so he can cancel them.
