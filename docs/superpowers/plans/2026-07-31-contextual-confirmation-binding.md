# Contextual Confirmation Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an unequivocal semantic approval of the exact pending public summary produce a parent-bound `intent=confirm` without lexical phrase triggers.

**Architecture:** Add a narrow closed confirmation-review wire decoded by `HermesModelAdapter`. The reviewer emits only a semantic decision; on `approve`, the adapter constructs the exact confirmation assertion from `PendingCriticalActionContext`. The executor keeps all existing expiry, material-scope, refresh-read, reducer, fence, and commit guards.

**Tech Stack:** Python 3.12, frozen dataclasses/enums, Hermes tool-free child, SQLite-backed executor tests, pytest, Ruff.

## Global Constraints

- No word, phrase, regex, emoji, substring, locale-specific allowlist, or keyword list may authorize a critical effect.
- Only `approve` can become `intent=confirm`; every other or invalid result is non-authorizing.
- The parent copies summary version, action tuple, and contextual basis from the exact pending context.
- The narrow request contains no provider refs, offer IDs, subject signatures, private profile values, observations, or credentials.
- Existing expiry, material-scope, capability, refresh-read, replay, and commit guards remain authoritative.
- No provider, payment, handoff, delivery, or live customer effect is permitted during implementation or sandbox validation.

---

### Task 1: Closed contextual-review contract

**Files:**
- Create: `v2_contracts/confirmation_review.py`
- Create: `tests/test_v2_contextual_confirmation_review.py`

**Interfaces:**
- Produces: `ContextualConfirmationDecision(str, Enum)` with `APPROVE`, `REJECT`, `ADJUST`, `UNCERTAIN`.
- Produces: `ContextualConfirmationReview(source_event_id: str, decision: ContextualConfirmationDecision)`.

- [ ] **Step 1: Write the failing contract tests**

Test exact enum membership, exact dataclass field types, canonical source-event identity, and rejection of raw strings/unknown decisions/invalid IDs.

```python
def test_review_contract_is_closed_and_exact() -> None:
    review = ContextualConfirmationReview(
        source_event_id="batch:contextual-review-001",
        decision=ContextualConfirmationDecision.APPROVE,
    )
    assert review.decision is ContextualConfirmationDecision.APPROVE
    with pytest.raises(TypeError):
        replace(review, decision="approve")
```

- [ ] **Step 2: Verify RED**

Run:

```bash
python -m pytest -q tests/test_v2_contextual_confirmation_review.py
```

Expected: collection failure because `v2_contracts.confirmation_review` does not exist.

- [ ] **Step 3: Implement the exact enum and frozen dataclass**

Use the existing canonical identifier grammar (`^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$`) and exact-type checks; do not add text heuristics.

- [ ] **Step 4: Verify GREEN**

Run the focused selector and expect all contract tests to pass.

- [ ] **Step 5: Commit**

```bash
git add v2_contracts/confirmation_review.py tests/test_v2_contextual_confirmation_review.py
git commit -m "feat(v2): add contextual confirmation review contract"
```

### Task 2: Schema-neutral tool-free child

**Files:**
- Modify: `v2_host/hermes_child.py:46-53`
- Modify: `tests/test_v2_hermes_child.py:29-67`

**Interfaces:**
- Consumes: the supplied system prompt as the sole schema authority.
- Produces: one tool-free JSON object matching that supplied contract.

- [ ] **Step 1: Write a failing child test**

Capture the child command and assert its wrapper says “matching the supplied system contract” while containing no literal `v2-model-proposal-vN` schema.

- [ ] **Step 2: Verify RED**

Run:

```bash
python -m pytest -q tests/test_v2_hermes_child.py::test_child_wrapper_is_schema_neutral
```

Expected: failure because the wrapper currently appends `v2-model-proposal-v3`.

- [ ] **Step 3: Replace only the competing schema sentence**

Keep tool disabling, one-turn behavior, output extraction, size limits, and categorical stderr unchanged.

- [ ] **Step 4: Verify GREEN and child regressions**

Run all of `tests/test_v2_hermes_child.py`.

- [ ] **Step 5: Commit**

```bash
git add v2_host/hermes_child.py tests/test_v2_hermes_child.py
git commit -m "fix(v2): make model child schema neutral"
```

### Task 3: Narrow review wire and parent-owned approval mapping

**Files:**
- Modify: `v2_adapters/hermes_model.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Test: `tests/test_v2_contextual_confirmation_review.py`

**Interfaces:**
- Produces: `_confirmation_review_wire(request: ModelRequest, system_prompt: str) -> bytes`.
- Produces: `_confirmation_review(payload: bytes, source_event_id: str) -> ContextualConfirmationReview`.
- Produces: `_proposal_from_confirmation_review(request: ModelRequest, review: ContextualConfirmationReview) -> ModelProposal`.
- `HermesModelAdapter.complete_audited()` dispatches `confirmation_review_required=True` through schema `v2-contextual-confirmation-review-v1`.

- [ ] **Step 1: Write failing parser and privacy tests**

Cover exact three-key schema, duplicate keys, wrong schema/source, unknown decision, and a minimal request wire. Assert serialized review input excludes `state_facts`, profile markers/values, passenger status, observations, provider/offer/subject identifiers, and credentials.

- [ ] **Step 2: Write failing parent-binding tests**

For `approve`, assert the resulting proposal has exactly:

```python
assert proposal.intent == "confirm"
assert proposal.confirmed_summary_version == pending.summary_version
assert proposal.confirmed_action_kinds == pending.action_kinds
assert proposal.approval_basis is ApprovalBasis.CONTEXTUAL_REFERENCE
assert proposal.facts == proposal.read_requests == proposal.effect_proposals == ()
assert proposal.passengers == ()
```

For `reject` and `adjust`, assert non-authorizing `adjust` with `pending_disposition="revoke"`. For `uncertain`, assert non-authorizing `inform`. None may inherit model-supplied binding because the review wire has no such fields.

- [ ] **Step 3: Verify RED**

Run the new tests and confirm failures are missing narrow-wire/parser/mapping functions.

- [ ] **Step 4: Implement the dedicated system contract and parser**

The system contract defines semantic entailment of the complete message against the complete public summary and the four decisions. It must contain no runtime lexical allowlist and no fixed approval phrase table.

- [ ] **Step 5: Route audited completion through the narrow protocol**

General requests retain the existing V6 proposal path. Confirmation-review requests use a dedicated original wire, dedicated protocol-repair prompt, and parser. Successful `approve` is mapped to a normal bound `ModelProposal`; malformed/failed attempts use the existing deterministic non-authorizing fallback.

- [ ] **Step 6: Verify GREEN and adapter regressions**

Run:

```bash
python -m pytest -q tests/test_v2_contextual_confirmation_review.py tests/test_v2_hermes_model_adapter.py
```

- [ ] **Step 7: Commit**

```bash
git add v2_adapters/hermes_model.py tests/test_v2_hermes_model_adapter.py tests/test_v2_contextual_confirmation_review.py
git commit -m "fix(v2): bind semantic confirmation reviews in parent"
```

### Task 4: Executor integration and distinct review identity

**Files:**
- Modify: `v2_application/turn_executor.py:1368-1410`
- Modify: `tests/test_v2_turn_executor.py`

**Interfaces:**
- Consumes: a parent-bound `ModelProposal` from the narrow adapter review.
- Produces: a distinct `_opaque("model-confirmation-review", ...)` request identity.
- Preserves: `_critical_confirmation_bound`, `_confirmation_read_requests`, reducer confirmation checks, atomic command/relay commit, and replay.

- [ ] **Step 1: Add the exact failing canary regression**

Build a pending lodging summary and make the first general proposal return `inform`. The review call returns the parent-bound `confirm`. Use the canary wording as a witness but never inspect it lexically in production:

```text
Sim, confirmo exatamente esse resumo. Pode fazer a reserva agora.
```

Assert one reservation command, one relay, refresh read, and replay without duplicate command. Assert first and review `request_id` values differ.

- [ ] **Step 2: Add fail-closed executor cases**

Parameterize non-authorizing review outcomes: question, hesitation, refusal, postponement, condition, scope narrowing, and material changes. Feed the typed outcomes from the fake model, then assert zero commands/relays.

- [ ] **Step 3: Verify RED**

The distinct-identity assertion must fail against current `replace(request, ...)`, and the narrow integration fixture must fail before Task 3 is wired.

- [ ] **Step 4: Assign a distinct deterministic review request ID**

Change only the review request identity and keep the source event, pending context, profile markers, projection and frame audit chain intact.

- [ ] **Step 5: Verify GREEN**

Run the exact new selectors plus existing critical confirmation expiry/scope/read/replay selectors.

- [ ] **Step 6: Commit**

```bash
git add v2_application/turn_executor.py tests/test_v2_turn_executor.py
git commit -m "fix(v2): execute bound contextual confirmations"
```

### Task 5: Prompt and adversarial no-magic-phrase gate

**Files:**
- Modify: `tests/test_v2_luna_prompt.py`
- Modify: `tests/test_v2_contextual_confirmation_review.py`
- Create: `scripts/validate_contextual_confirmation_model.py`
- Create: `tests/test_validate_contextual_confirmation_model.py`

**Interfaces:**
- Produces: a sandbox-only classifier validator that accepts a tool-free command/config and emits aggregate counts/hashes, never customer text.
- The validator has no provider/read/write/delivery/handoff/payment imports or capabilities.

- [ ] **Step 1: Add static and adversarial failing gates**

Statically reject production helpers that classify approval via phrase/keyword/regex tables. Add synthetic PT/EN cases with structurally diverse approvals and non-approvals. Expected decisions are explicit fixture data, not runtime matching logic.

- [ ] **Step 2: Verify RED**

The validator module and static gate do not yet exist.

- [ ] **Step 3: Implement the sandbox validator**

Run each synthetic public case through `HermesModelAdapter` with `confirmation_review_required=True`. Verify schema, source binding, decision, no tools, and no side-effect imports. Output only case count, pass count, and hashes.

- [ ] **Step 4: Verify GREEN offline**

Run validator unit tests with a fake tool-free child.

- [ ] **Step 5: Run real-model sandbox validation**

Use the installed isolated child and the configured model only after deterministic tests pass. No provider settings or state DBs are supplied. Require every unequivocal approval and every adversarial non-approval to match its expected typed decision.

- [ ] **Step 6: Commit**

```bash
git add tests/test_v2_luna_prompt.py tests/test_v2_contextual_confirmation_review.py scripts/validate_contextual_confirmation_model.py tests/test_validate_contextual_confirmation_model.py
git commit -m "test(v2): gate contextual confirmation semantics"
```

### Task 6: Frozen-candidate verification and review

**Files:**
- Verify all modified files; no new production behavior in this task.

**Interfaces:**
- Produces: exact SHA/tree, clean worktree, focused/causal/full gate evidence, and independent read-only review.

- [ ] **Step 1: Run focused tests**

```bash
python -m pytest -q \
  tests/test_v2_contextual_confirmation_review.py \
  tests/test_v2_hermes_child.py \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_critical_actions.py \
  tests/test_v2_conversation_reducer.py
```

- [ ] **Step 2: Run blast-radius regressions**

Include package confirmations, commercial surface, turns, production composition, Cloudbeds/Bókun transport, reservations, relay and completion projector.

- [ ] **Step 3: Run static checks**

Run Ruff on changed Python files, `scripts/check_fasttrack_boundaries.py`, AST capability scan for the sandbox validator, and `git diff --check`.

- [ ] **Step 4: Run the repository suite once on the frozen candidate**

Use the clean environment and the seven already-declared historical deselections. Report it explicitly as a suite with deselections, not a full zero-exclusion suite.

- [ ] **Step 5: Obtain independent read-only review**

Require the reviewer to reproduce the canary regression, inspect the exact decision wire and privacy boundary, and probe false positives for questions/conditions/material changes. Any Critical/Important finding reopens a named RED.

- [ ] **Step 6: Re-anchor and publish only after approval**

Verify exact HEAD/tree/blobs, clean worktree, push the branch, wait for exact-SHA CI `test`, `image`, and `gate`, and authenticate OCI revision label. Do not run a live provider canary without new explicit authorization.
