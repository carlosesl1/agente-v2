# Same-Turn Private Profile Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the semantically empty customer follow-up after valid name/email/country collection or correction by allowing the current turn to publish a fresh summary while preserving persist-first privacy and a hard no-reservation-command fence.

**Architecture:** Keep deterministic parent extraction and private SQLite persistence before the first model request. Distinguish a parent-authenticated private update from invalid input and model-only private facts; only the parent-authenticated path bypasses the acknowledgement-only transformation. Permit existing read/selection/reducer logic to produce a summary, but demote `confirm` proposals and assert zero reservation command/relay rows for every such source turn.

**Tech Stack:** Python 3.12, immutable dataclasses, SQLite boundary/private owners, pytest 9, Ruff, existing fake provider ports and `httpx.MockTransport` only.

## Global Constraints

- Work only in `/home/ubuntu/agente-v2/.worktrees/phase8-shadow-canary-rollout` on `maya-v2-operational-readiness`.
- Approved private continuation fields are exactly `full_name`, `email`, and `country_code`.
- `phone_e164` remains ManyChat-only, valid, and fresh.
- Persist accepted private facts before the first model request and expose field names only.
- Model-only private facts retain the conservative acknowledgement-only path.
- Invalid private input and conversational phone behavior remain unchanged.
- A parent-authenticated private update may publish a summary but must produce zero reservation command rows and zero command relays in that turn.
- Explicit handoff behavior remains unchanged.
- Keep exact PII out of public projection, model wire/state, artifacts, logs, evidence, `repr`, exceptions, and tracebacks.
- Use `PYTHONDONTWRITEBYTECODE=1` and `pytest -p no:cacheprovider`.
- No network, provider write, payment, ManyChat delivery/reset, deploy, reservation mutation, or rollout.
- Operational state remains `runtime=dark_read_only`, `kill_switch=true`, `post_budget_armed=false`, and `SAFE_FOR_BROAD_ROLLOUT=false`.

---

## File map

- `v2_application/turn_executor.py`: classify same-turn private updates, preserve invalid/model-only collection gates, demote confirmation, and enforce zero command/relay rows.
- `tests/test_v2_turn_executor.py`: focused privacy, crash/retry, invalid/model-only, no-command, and unchanged handoff witnesses.
- `tests/test_v2_split_origin_cloudbeds_e2e.py`: user-visible initial collection and valid correction journeys through same-turn summary and later confirmation.
- `docs/refactor/ACTIVE.md`: exact candidate evidence and closed operational gates after qualification.
- `software-development/bounded-software-task-execution/references/private-profile-and-sqlite-owner-boundaries.md`: reusable protocol amendment after the implementation proves green.

---

### Task 1: Initial collection produces a same-turn summary

**Files:**
- Modify: `tests/test_v2_split_origin_cloudbeds_e2e.py:49-293`
- Modify: `tests/test_v2_turn_executor.py:1340-1424,1608-1692`

**Interfaces:**
- Consumes: `V2TurnExecutor.execute(batch) -> V2TurnExecutionResult`, `SQLitePrivateCustomerFactStore.load(lead_id)`, `FakeAuditedModel.calls`.
- Produces: causal REDs for parent-authenticated collection continuation and PII containment.

- [ ] **Step 1: Rewrite the split-origin E2E input so one aggregate turn contains private and commercial facts**

Use one collection batch whose text contains labeled valid name/email/country plus lodging dates and party. Remove the separate `summary_batch`. Supply two model proposals for that same collection batch:

```python
ModelProposal(
    source_event_id=collect_batch.batch_id,
    intent="inform",
    reply_chunks=("Vou consultar.",),
    facts=(),
    read_requests=(read_request,),
    effect_proposals=(),
),
ModelProposal(
    source_event_id=collect_batch.batch_id,
    intent="select",
    reply_chunks=("Vou preparar o resumo.",),
    facts=summary_facts,
    read_requests=(),
    effect_proposals=(),
    target_offer_id="offer:" + "7" * 64,
),
```

Assert after `executor.execute(collect_batch)`:

```python
assert "Só para confirmar" in summary.reply_chunks[0]
assert summary.receipt.command_rows == ()
assert summary.receipt.relay_rows == ()
assert "Guardei esses dados" not in " ".join(summary.reply_chunks)
assert len(read_port.calls) == 1
```

Keep the later confirmation/replay and one-POST Cloudbeds assertions, updating proposal and call indexes to the combined turn.

- [ ] **Step 2: Update focused executor witnesses to require normal continuation rather than the canned acknowledgement**

Change the deterministic parent-collection fixtures to use an `inform` proposal when they are testing persistence/privacy only, then assert the proposal's normal safe text is returned and the canned text is absent. Keep exact private-store and public-surface assertions.

- [ ] **Step 3: Run the two exact witnesses and verify causal RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_v2_split_origin_cloudbeds_e2e.py::test_split_origin_profile_reaches_one_monotonic_cloudbeds_post_and_replays_once \
  tests/test_v2_turn_executor.py::test_private_profile_collection_is_durable_collection_only_and_publicly_redacted
```

Expected: FAIL because the executor still returns `Obrigado. Guardei esses dados...`, suppresses the provider read/summary, or both. Collection must complete and reach assertions; import/fixture errors are not RED.

- [ ] **Step 4: Commit tests-only RED**

```bash
git add tests/test_v2_split_origin_cloudbeds_e2e.py tests/test_v2_turn_executor.py
git commit -m "test: require same-turn summary after private collection"
```

---

### Task 2: Permit only parent-authenticated continuation

**Files:**
- Modify: `v2_application/turn_executor.py:1026-1137,1531-1678,2024-2121,2285-2360`
- Test: `tests/test_v2_turn_executor.py`
- Test: `tests/test_v2_split_origin_cloudbeds_e2e.py`

**Interfaces:**
- Consumes: `collect_private_customer_facts(...)`, `turn_supplied_fact_names(...)`, `_persist_private_collection(...)`, `_collection_only_proposal(...)`.
- Produces: local booleans `parent_private_update_turn: bool` and `private_collection_only: bool`; helper `_private_update_no_command_proposal(...) -> ModelProposal`.

- [ ] **Step 1: Re-extract journaled names safely on retry**

At `_prepare` start, retain the authenticated journal names and include them in the closed expected-name tuple so a crash retry can deterministically recognize and sanitize the same parent-supplied value:

```python
journal_fact_names = self._private_customer_facts.turn_supplied_fact_names(
    batch.lead_id,
    batch.batch_id,
)
missing_fact_names = _expected_private_customer_fact_names(profile, private_facts)
expected_fact_names = tuple(
    name
    for name in ("full_name", "email", "country_code")
    if name in set((*missing_fact_names, *journal_fact_names))
)
private_collection = collect_private_customer_facts(
    batch.combined_text,
    expected_fact_names=expected_fact_names,
)
parent_private_update_turn = bool(private_collection.facts)
```

After persistence, keep `parent_private_update_turn=True`. Define the conservative gate as:

```python
private_collection_only = bool(
    private_collection.invalid_fact_names
    or journal_fact_names and not parent_private_update_turn
)
```

This preserves model-only crash/retry behavior while allowing a deterministically re-extracted parent update.

- [ ] **Step 2: Keep model-only and invalid facts collection-only**

Do not remove `_collection_only_proposal`. Continue setting `private_collection_only=True` when any validated model proposal contains private facts, invalid private facts, or conversational phone. Continue stripping private facts from the public reducer proposal through the existing helper.

Rename local `collection_only` uses consistently to `private_collection_only` so selection/confirmation review gates remain readable and unchanged for conservative paths.

- [ ] **Step 3: Demote `confirm` on a parent private-update turn before reads/reduction**

Add the exact helper:

```python
def _private_update_no_command_proposal(
    proposal: ModelProposal,
    *,
    pending_action: PendingCriticalActionContext | None,
    locale: str,
) -> ModelProposal:
    if proposal.intent != "confirm":
        return proposal
    pending = pending_action is not None
    reply = (
        "I updated your details. I’ll present a new summary before asking for confirmation."
        if locale.startswith("en")
        else "Atualizei seus dados. Vou apresentar um novo resumo antes de pedir confirmação."
    )
    return ModelProposal(
        source_event_id=proposal.source_event_id,
        intent="adjust" if pending else "inform",
        reply_chunks=(reply,),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        pending_disposition="revoke" if pending else None,
    )
```

Apply it to the final proposal only when `parent_private_update_turn` is true and `private_collection_only` is false, before profile-sensitive decision logic and reducer invocation.

- [ ] **Step 4: Add command/relay invariants after reduction and before prepared commit**

Immediately after `_execution_commands(decision.commands)`:

```python
if parent_private_update_turn and execution_commands:
    raise TurnExecutionError(
        "private customer update cannot authorize a reservation command"
    )
```

Immediately after `_command_rows` and `_command_relays`:

```python
if parent_private_update_turn and (command_rows or command_relays):
    raise TurnExecutionError(
        "private customer update cannot persist reservation effects"
    )
```

Error strings remain generic and contain no private values.

- [ ] **Step 5: Run focused GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_v2_split_origin_cloudbeds_e2e.py::test_split_origin_profile_reaches_one_monotonic_cloudbeds_post_and_replays_once \
  tests/test_v2_turn_executor.py::test_private_profile_collection_is_durable_collection_only_and_publicly_redacted \
  tests/test_v2_turn_executor.py::test_parent_collector_redacts_private_values_before_model_and_persists_first \
  tests/test_v2_turn_executor.py::test_private_collection_gate_survives_boundary_crash_and_retry \
  tests/test_v2_turn_executor.py::test_invalid_model_private_facts_are_collection_only_and_request_correction
```

Expected: PASS. The model-only crash/retry test must still return the conservative acknowledgement; deterministic parent tests must not.

- [ ] **Step 6: Commit minimal production GREEN**

```bash
git add v2_application/turn_executor.py tests/test_v2_split_origin_cloudbeds_e2e.py tests/test_v2_turn_executor.py
git commit -m "fix: continue after persisted private collection"
```

---

### Task 3: Valid correction replaces the summary in the same turn

**Files:**
- Modify: `tests/test_v2_split_origin_cloudbeds_e2e.py:296-423`
- Modify only if RED proves necessary: `v2_application/turn_executor.py`

**Interfaces:**
- Consumes: existing two-stage `inform + read` then `select` path while workflow is `AwaitingConfirmationState`.
- Produces: a new `AwaitingConfirmationState` bound to the corrected private snapshot and zero command/relay rows.

- [ ] **Step 1: Add valid-correction E2E**

Create `test_valid_private_correction_replaces_summary_in_same_turn`. Seed/persist original private facts, execute an initial summary turn, then execute a labeled correction batch such as `Nome completo: Pessoa Corrigida Silva` with two proposals:

```python
ModelProposal(
    source_event_id=correction_batch.batch_id,
    intent="inform",
    reply_chunks=("Vou atualizar.",),
    facts=(),
    read_requests=(correction_read_request,),
    effect_proposals=(),
),
ModelProposal(
    source_event_id=correction_batch.batch_id,
    intent="select",
    reply_chunks=("Vou preparar o resumo atualizado.",),
    facts=summary_facts,
    read_requests=(),
    effect_proposals=(),
    target_offer_id="offer:" + "7" * 64,
),
```

Assert:

```python
assert "Só para confirmar" in corrected.reply_chunks[0]
assert corrected.reply_chunks != original.reply_chunks
assert corrected.receipt.command_rows == ()
assert corrected.receipt.relay_rows == ()
assert private_store.load(lead_id).full_name == corrected_name
current = boundary.load_state(lead_id)
pending = executor._reducer.pending_action(current.state.workflow, locale="pt-BR")
assert pending is not None
assert pending.public_summary == corrected.reply_chunks[0]
assert pending.public_summary != original.reply_chunks[0]
```

Use private variable values only in private-store/command assertions, never public artifacts.

- [ ] **Step 2: Run RED/GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_v2_split_origin_cloudbeds_e2e.py::test_valid_private_correction_replaces_summary_in_same_turn \
  tests/test_v2_split_origin_cloudbeds_e2e.py::test_invalid_private_correction_after_summary_revokes_pending_confirmation
```

Expected before any additional production edit: valid correction may already PASS through the existing select reducer path; invalid correction must remain PASS. If valid correction fails causally, make only the smallest executor edit needed to allow the existing read/select path; do not change reducer command semantics.

- [ ] **Step 3: Add rogue-confirm and unchanged-handoff witnesses**

In `tests/test_v2_turn_executor.py`, add:

- parent-authenticated correction + model `confirm` while summary pending => old summary revoked/demoted, zero command/relay;
- parent-authenticated valid fact + explicit `request_handoff` => existing handoff job/reply remains present, zero reservation command/relay.

Run both exact selectors and require PASS.

- [ ] **Step 4: Commit correction safety**

```bash
git add tests/test_v2_split_origin_cloudbeds_e2e.py tests/test_v2_turn_executor.py v2_application/turn_executor.py
git commit -m "test: bind private corrections to fresh summaries"
```

Stage `v2_application/turn_executor.py` only if Step 2 or 3 required a production edit.

---

### Task 4: Replay, privacy, and proportional regression

**Files:**
- Modify: `tests/test_v2_turn_executor.py:1695-1763` only if assertions need adaptation
- Modify: `tests/test_v2_split_origin_cloudbeds_e2e.py`

**Interfaces:**
- Consumes: authenticated private turn journal and boundary turn receipt replay.
- Produces: evidence that crash/retry cannot escape the no-command fence or duplicate a committed summary/read.

- [ ] **Step 1: Add committed replay assertions**

Replay the same collection/correction batch after a successful same-turn summary and assert:

```python
replayed = executor.execute(batch)
assert replayed.replayed is True
assert replayed.receipt == first.receipt
assert replayed.reply_chunks == first.reply_chunks
assert read_port.calls == calls_after_first_commit
assert replayed.receipt.command_rows == ()
assert replayed.receipt.relay_rows == ()
```

- [ ] **Step 2: Preserve model-only crash/retry collection gate**

Keep `test_private_collection_gate_survives_boundary_crash_and_retry` as a model-only private-fact witness. Its eventual reply remains the deterministic acknowledgement and it produces zero commands/relays.

- [ ] **Step 3: Run proportional regression**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_v2_private_customer_facts.py \
  tests/test_v2_customer_collection.py \
  tests/test_v2_effective_customer_profile.py \
  tests/test_v2_conversation_reducer.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_split_origin_cloudbeds_e2e.py \
  tests/test_v2_cloudbeds_monotonic_e2e.py \
  tests/test_v2_cloudbeds_audit.py \
  tests/test_v2_bokun_write_transport.py \
  tests/test_v2_bokun_party_reads.py \
  tests/test_v2_payment_initiation.py \
  tests/test_v2_payment_evidence.py
```

Expected: all selected tests PASS, zero warnings/errors.

- [ ] **Step 4: Run focused static gates and commit tests**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m ruff check \
  v2_application/turn_executor.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_split_origin_cloudbeds_e2e.py
git diff --check
git add tests/test_v2_turn_executor.py tests/test_v2_split_origin_cloudbeds_e2e.py
git commit -m "test: prove same-turn private continuation replay"
```

---

### Task 5: Full qualification, documentation, and exact-SHA review

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Patch skill reference: `bounded-software-task-execution/references/private-profile-and-sqlite-owner-boundaries.md`

**Interfaces:**
- Consumes: frozen functional HEAD and complete local evidence.
- Produces: clean final candidate SHA/tree, closed operational state, and independent verdict.

- [ ] **Step 1: Run the official local gate**

```bash
env -i \
  HOME=/home/ubuntu \
  PATH=/home/ubuntu/chapada-leads-hermes/venv/bin:/usr/bin:/bin \
  PYTHONDONTWRITEBYTECODE=1 \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/leads_agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q -p no:cacheprovider \
    --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_project_metadata_declares_closed_distribution \
    --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_two_builds_are_byte_identical_closed_and_self_hashing \
    --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_installed_wheel_imports_without_checkout_on_sys_path \
    --deselect=tests/test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only \
    --deselect=tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_manifest_is_deterministic_current_and_covers_runtime_patch \
    --deselect=tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts \
    --deselect=tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed
```

Expected: zero failures, exactly seven historical deselections.

- [ ] **Step 2: Run repository static gates**

```bash
PY=/home/ubuntu/chapada-leads-hermes/venv/bin/python
$PY -m ruff check v2_adapters v2_application v2_contracts v2_host
$PY -m ruff check reservation_boundary/schema.py reservation_boundary/sqlite_store.py
$PY scripts/check_fasttrack_boundaries.py
PYTHONDONTWRITEBYTECODE=1 $PY -m compileall -q \
  reservation_boundary reservation_confirmation reservation_domain \
  reservation_execution reservation_followup reservation_lookup \
  v2_adapters v2_application v2_contracts v2_host
git diff --check
```

Expected: all green.

- [ ] **Step 3: Update reusable protocol and ACTIVE ledger**

Patch the private-profile reference to state:

- parent-authenticated valid name/email/country may continue to summary in the same turn;
- invalid, phone, and model-only private facts remain conservative collection-only;
- same-turn private update cannot authorize reservation command/relay;
- crash/retry must reauthenticate journal and deterministic redaction;
- explicit handoff remains governed by its existing policy.

Record exact focused/proportional/full counts, commits, SHA/tree, clean status, and closed operational gates in `docs/refactor/ACTIVE.md`.

- [ ] **Step 4: Commit the candidate ledger**

```bash
git add docs/refactor/ACTIVE.md
git commit -m "docs: freeze same-turn private continuation candidate"
git diff --check
printf 'HEAD='; git rev-parse HEAD
printf 'TREE='; git rev-parse HEAD^{tree}
test -z "$(git status --short)"
```

- [ ] **Step 5: Obtain independent read-only verdict**

Review only the new base-to-HEAD delta. Require authentication of exact HEAD/tree/clean status and causal review of:

- same-turn collection summary;
- same-turn valid correction/new summary;
- model-only and invalid paths unchanged;
- zero commands/relays on private-update turn;
- later confirmation and exactly-once Cloudbeds behavior;
- PII containment;
- docs/code consistency.

A timeout is not approval. Push and CI remain blocked until `VERDICT: CLEAR` on the exact final SHA.

---

## Plan self-review

- Spec coverage: initial collection, valid correction, invalid/model-only paths, no-command fence, replay, privacy, handoff non-regression, provider/payment regressions, operational gates, and exact-SHA review are each mapped to a task.
- Type consistency: all named production types/functions exist in the authenticated baseline; the only new helper has one exact signature and is consumed in Task 2.
- Scope: no reducer redesign, schema change, provider transport change, payment change, or deployment work is included.
