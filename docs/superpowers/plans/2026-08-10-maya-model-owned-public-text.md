# Maya Model-Owned Public Text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Maya the sole author of every conversational `reply_chunks` value while keeping the controller authoritative for validation, privacy, observations, permissions, receipts, idempotency, and effects.

**Architecture:** The final accepted model frame supplies immutable public chunks. Controller branches may preserve those chunks, request one closed model-owned correction, or fail before public commit; they may never synthesize or replace prose. Canonical transactional material and asynchronous provider payloads remain parent-owned typed artifacts, but any conversational explanation of them must come from Maya.

**Tech Stack:** Python 3.12, frozen dataclasses, SQLite boundary store, pytest, Ruff, Hermes child adapter, JSON closed protocols.

## Global Constraints

- Starting implementation commit: `5e8208b2e901e90e1c6bf52496c02ae78ccc0d79`.
- Design authority: `docs/superpowers/specs/2026-08-10-maya-model-owned-public-text-design.md`.
- No deterministic NLU, date/person/country/intent extractor, regex interpretation, customer-text alias matching, or keyword trigger.
- The controller may accept, reject, persist, validate, execute, and request one model-owned correction; it may not write customer-facing Maya prose.
- A failed correction creates no public reply, command relay, channel delivery, or provider write.
- Existing Stripe, Wise, Pix, Cloudbeds, Bókun, handoff, idempotency, writer/reconciler, receipt, and replay gates remain closed and authoritative.
- Context remains at most four prior exchanges, nine wire messages including the current request, and 64 KiB aggregate dialogue bytes.
- Real-model tests use fake business providers and zero external effects.
- No deploy, restart, OCI publication, canary, promotion, or broad rollout.

---

### Task 1: Reproduce the private-update clarification loss

**Files:**
- Modify: `tests/test_v2_turn_executor.py`
- Read: `v2_application/turn_executor.py:878-926`

**Interfaces:**
- Consumes: existing `FakeAuditedModel`, `_executor`, `ModelFact`, and `ModelProposal` test helpers.
- Produces: `test_private_holder_update_preserves_exact_model_owned_clarification` as the causal gate.

- [ ] **Step 1: Add the exact failing regression**

Create a model proposal with private facts and a typed clarification that is also the only reply chunk:

```python
question = "Haverá alguma criança no grupo?"
model = FakeAuditedModel(
    proposals=(
        ModelProposal(
            source_event_id="event:holder-clarification",
            intent="inform",
            reply_chunks=(question,),
            clarification_question=question,
            facts=(
                ModelFact("full_name", "Bruno Exemplo"),
                ModelFact("email", "bruno@example.invalid"),
                ModelFact("country_code", "BR"),
            ),
            read_requests=(),
            effect_proposals=(),
        ),
    )
)
result = executor.execute(batch)
assert result.reply_chunks == (question,)
assert result.receipt.public_reply.chunks == (question,)
assert not result.receipt.commands
assert "bruno@example.invalid" not in result.receipt.to_canonical_bytes().decode("utf-8")
```

Also assert the private store contains the accepted private values and public/relay/effect tables remain empty.

- [ ] **Step 2: Witness RED**

Run:

```bash
pytest -q tests/test_v2_turn_executor.py::test_private_holder_update_preserves_exact_model_owned_clarification
```

Expected: FAIL showing the actual reply `Obrigado. Guardei esses dados para continuar a reserva.` instead of the typed question.

- [ ] **Step 3: Commit only the RED test**

```bash
git add tests/test_v2_turn_executor.py
git commit -m "test: expose controller replacement of Maya clarification"
```

---

### Task 2: Make typed clarification exact and parser-owned only by rejection

**Files:**
- Modify: `v2_adapters/hermes_model.py:515-656`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_profile_and_model_grammar.py`

**Interfaces:**
- Consumes: `ModelProposal.__post_init__`, which already requires `clarification_question` to be an exact member of `reply_chunks`.
- Produces: parser behavior that preserves decoded chunk order/text and rejects mismatched clarification instead of replacing chunks.

- [ ] **Step 1: Add parser RED tests**

Add one payload containing two model chunks and a matching second clarification. Assert both chunks survive in the same order. Add a second payload with a clarification absent from chunks and assert protocol repair is invoked; if repair is disabled, assert `InvalidModelProposal`.

```python
assert turn.proposal.reply_chunks == (
    "Dados recebidos sem repeti-los.",
    "Haverá alguma criança no grupo?",
)
assert turn.proposal.clarification_question == "Haverá alguma criança no grupo?"
```

- [ ] **Step 2: Witness RED**

Run:

```bash
pytest -q tests/test_v2_hermes_model_adapter.py -k 'clarification and (preserve or mismatch)'
```

Expected: the preservation test fails because `_proposal()` currently collapses `reply_chunks` to the question.

- [ ] **Step 3: Remove parser text replacement**

Delete:

```python
if type(clarification_question) is str and clarification_question:
    reply_chunks = (clarification_question,)
```

Leave `ModelProposal` to accept an exact matching chunk or reject the frame. Do not add semantic matching, punctuation tests, regex, casefold, substring matching, or aliases.

- [ ] **Step 4: Run focused tests and commit**

```bash
pytest -q tests/test_v2_profile_and_model_grammar.py tests/test_v2_hermes_model_adapter.py
ruff check v2_adapters/hermes_model.py tests/test_v2_hermes_model_adapter.py

git add v2_adapters/hermes_model.py tests/test_v2_hermes_model_adapter.py tests/test_v2_profile_and_model_grammar.py
git commit -m "fix: preserve exact model reply chunks in parser"
```

---

### Task 3: Add a closed bounded public-reply correction request

**Files:**
- Modify: `v2_contracts/model.py:282-465`
- Modify: `v2_adapters/hermes_model.py`
- Modify: `scripts/phase8_hermes_child.py`
- Modify: `config/v2_luna_system_prompt.txt`
- Modify: `tests/test_v2_profile_and_model_grammar.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_phase8_hermes_child.py`

**Interfaces:**
- Produces: `PublicReplyCorrectionReason(str, Enum)` and `ModelRequest.public_reply_correction_reasons: tuple[PublicReplyCorrectionReason, ...] = ()`.
- Produces: one ordinary v7 model proposal written by Maya under a terminal correction protocol.
- Consumes later: executor branches use the reasons to request one correction without supplying parent prose.

- [ ] **Step 1: Add contract RED tests**

Use this closed enum:

```python
class PublicReplyCorrectionReason(str, Enum):
    PRIVATE_VALUE_EXPOSURE = "private_value_exposure"
    TYPED_CLARIFICATION_MISMATCH = "typed_clarification_mismatch"
    UNSUPPORTED_OBSERVATION_CLAIM = "unsupported_observation_claim"
    OPERATIONAL_STATUS_CONFLICT = "operational_status_conflict"
    READ_REMOVED_BY_AUTHORITY = "read_removed_by_authority"
    SELECTION_BINDING_FAILURE = "selection_binding_failure"
    ACTIVE_EXECUTION_CONFLICT = "active_execution_conflict"
    STALE_CONSULTATION_REUSE = "stale_consultation_reuse"
    INVALID_CONFIRMATION_REVIEW = "invalid_confirmation_review"
    RECURSIVE_READ_AFTER_OBSERVATION = "recursive_read_after_observation"
```

Assert the tuple is exact, unique, canonical by enum value, bounded to four reasons, and mutually exclusive with `progress_review_required`, `confirmation_review_required`, `selection_review_required`, and `recap_reuse_required`.

- [ ] **Step 2: Implement the closed contract**

Add to `ModelRequest`:

```python
public_reply_correction_reasons: tuple[PublicReplyCorrectionReason, ...] = ()
```

Validate exact enum instances, uniqueness, sorted canonical order, maximum four, and mutual exclusion with existing semantic review flags.

- [ ] **Step 3: Extend the private wire and child schema**

Serialize only reason enum values and the existing typed state/observations. Do not serialize private stored values, controller copy, or logs. Add a final high-salience prompt suffix:

```text
PUBLIC REPLY CORRECTION
The previous candidate could not be published for the listed closed reasons.
You, Maya, must write the corrected customer-facing reply.
Do not repeat private values. Do not request another read after observations.
Do not strengthen operational status beyond exact receipts.
Return one valid v2-model-proposal-v7 frame. The parent will not rewrite it.
```

- [ ] **Step 4: Prove one bounded attempt**

In adapter tests, make the first correction response invalid and assert there is no nested progress/confirmation/selection/correction call. The adapter may use its normal single protocol-repair frame, but the executor must never create a second correction request for the same turn.

- [ ] **Step 5: Run focused tests and commit**

```bash
pytest -q tests/test_v2_profile_and_model_grammar.py tests/test_v2_hermes_model_adapter.py tests/test_phase8_hermes_child.py
ruff check v2_contracts/model.py v2_adapters/hermes_model.py scripts/phase8_hermes_child.py

git add v2_contracts/model.py v2_adapters/hermes_model.py scripts/phase8_hermes_child.py config/v2_luna_system_prompt.txt tests/test_v2_profile_and_model_grammar.py tests/test_v2_hermes_model_adapter.py tests/test_phase8_hermes_child.py
git commit -m "feat: add bounded model-owned public reply correction"
```

---

### Task 4: Remove deterministic adapter prose and fail closed

**Files:**
- Modify: `v2_adapters/hermes_model.py:685-742,873-974,1011-1097`
- Modify: `tests/test_v2_hermes_model_adapter.py`

**Interfaces:**
- Consumes: Task 3 correction request.
- Produces: no `_fallback_proposal`, no `_recursive_read_fallback`, and no `_proposal_from_confirmation_review` prose.

- [ ] **Step 1: Add RED tests for no deterministic prose**

Assert two invalid model frames cause `InvalidModelProposal` after the bounded protocol attempts, with audited failure frames retained but no `deterministic:protocol-fallback` session. Assert a recursive read after observations is rejected or corrected by Maya and never converted to deterministic prose.

- [ ] **Step 2: Make confirmation review model-authored**

Change the contextual confirmation review prompt to return a full `v2-model-proposal-v7` bound to the pending summary, including Maya-authored `reply_chunks`. Validate `intent`, `confirmed_summary_version`, `confirmed_action_kinds`, `approval_basis`, and `pending_disposition` structurally. Delete `_proposal_from_confirmation_review()`.

- [ ] **Step 3: Remove deterministic fallbacks**

Replace fallback construction with:

```python
raise InvalidModelProposal("model proposal remained invalid after bounded attempts")
```

For recursive reads after observations, treat the frame as invalid so the existing bounded protocol repair asks Maya to answer from observations with `read_requests=[]`; if it repeats the read, raise.

- [ ] **Step 4: Run and commit**

```bash
pytest -q tests/test_v2_hermes_model_adapter.py
ruff check v2_adapters/hermes_model.py tests/test_v2_hermes_model_adapter.py

git add v2_adapters/hermes_model.py tests/test_v2_hermes_model_adapter.py
git commit -m "fix: remove deterministic adapter replies"
```

---

### Task 5: Preserve Maya text through private updates, grounding, and executor guards

**Files:**
- Modify: `v2_application/turn_executor.py`
- Modify: `v2_application/public_reply.py`
- Modify: `v2_application/turn_plan.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_public_reply.py`
- Modify: `tests/test_v2_turn_plan.py`

**Interfaces:**
- Consumes: `public_reply_correction_reasons` from Task 3.
- Produces: final executor proposal whose chunks come from the latest successful model frame only.

- [ ] **Step 1: Convert the reproduced private-update branch**

For a safe typed clarification, partition/persist private facts and return the proposal with unchanged `reply_chunks` and `clarification_question`; clear only unauthorized structured fields:

```python
return replace(
    proposal,
    intent="inform",
    facts=proposal.facts,
    read_requests=(),
    effect_proposals=(),
    target_offer_id=None,
    target_offer_ids=(),
    selection_requested=False,
)
```

Do not pass `reply_chunks` or `clarification_question` to `replace()`.

- [ ] **Step 2: Add exact private-value exposure correction**

Check accepted private string values against candidate chunks as exact values, solely as a privacy boundary. If exposed, request one model-owned correction with `PRIVATE_VALUE_EXPOSURE`. After correction, repeat the exact leak check; a second exposure raises `TurnExecutionError` before commit. Never redact or substitute text.

- [ ] **Step 3: Make positive grounding text-preserving**

Change `apply_positive_grounding()` to validate exact proposal/observation types and return the proposal unchanged. Existing observation/state checks stay in reads and reducer. Update tests to assert byte-identical chunks rather than renderer output.

- [ ] **Step 4: Remove executor phrase generation**

Convert these functions to structural guards that preserve chunks or request correction:

```text
_selection_binding_failure_proposal
_active_execution_guard_proposal
_collection_only_proposal
_private_update_no_command_proposal
consultation reuse fallback
post-command execution replacement
```

The controller may change `intent`, facts, reads, targets, selection, confirmation fields, and effects to deny unauthorized behavior, but the resulting explanatory chunks must come from a model correction frame.

- [ ] **Step 5: Remove turn-plan phrase generation**

When `normalize_initial_commercial_plan()` cannot derive safe reads, clear the unbound selection fields without changing `reply_chunks`; add `READ_REMOVED_BY_AUTHORITY` to the later correction request.

- [ ] **Step 6: Run the causal and focused suites**

```bash
pytest -q \
  tests/test_v2_turn_executor.py::test_private_holder_update_preserves_exact_model_owned_clarification \
  tests/test_v2_turn_executor.py \
  tests/test_v2_public_reply.py \
  tests/test_v2_turn_plan.py
ruff check v2_application/turn_executor.py v2_application/public_reply.py v2_application/turn_plan.py
```

Expected: the causal test is green and no focused assertion expects controller-authored prose.

- [ ] **Step 7: Commit**

```bash
git add v2_application/turn_executor.py v2_application/public_reply.py v2_application/turn_plan.py tests/test_v2_turn_executor.py tests/test_v2_public_reply.py tests/test_v2_turn_plan.py
git commit -m "fix: keep Maya text immutable through turn execution"
```

---

### Task 6: Make reducer decisions non-textual

**Files:**
- Modify: `v2_application/conversation.py`
- Modify: `tests/test_v2_conversation_reducer.py`
- Modify: `tests/test_v2_turn_executor.py`

**Interfaces:**
- Consumes: a final corrected `ModelProposal` whose text already reflects typed state.
- Produces: `V2ConversationDecision.public_reply.chunks == proposal.reply_chunks` for every synchronous branch.

- [ ] **Step 1: Add table-driven RED coverage for every reducer branch**

For handoff guard, post-command guard, incomplete profile, preserved/revoked summary, denied/stale/expired confirmation, refresh mismatch, command authorization, package summary, and single-offer summary, use unique Maya sentinels. Assert:

```python
assert decision.public_reply.chunks == proposal.reply_chunks
```

Separately assert commands, transitions, receipt requirements, canonical critical context, and idempotency are unchanged.

- [ ] **Step 2: Replace controller prose with proposal chunks**

Introduce one strict helper:

```python
def _model_owned_reply(kind: str, proposal: ModelProposal) -> ConversationReply:
    if not proposal.reply_chunks:
        raise ConversationReductionError("final model proposal requires public reply chunks")
    return ConversationReply(kind, proposal.reply_chunks)
```

Use it in every reducer return that currently calls a copy helper or embeds a literal. Delete unused conversational copy helpers. Keep `critical_context.public_summary` as canonical typed material for hashes and confirmation binding, but do not substitute it for Maya chunks.

- [ ] **Step 3: Bind transactional summary material independently of prose**

Persist/hash the canonical critical material exactly as before. Add an assertion in executor preparation that the model correction request received the canonical pending action context before Maya's final summary wording was committed. Confirmation remains bound to summary version, action kinds, approval basis, and canonical subject signature—not to free prose alone.

- [ ] **Step 4: Run reducer/domain gates and commit**

```bash
pytest -q tests/test_v2_conversation_reducer.py tests/test_v2_turn_executor.py -k 'reducer or confirmation or summary or post_command'
ruff check v2_application/conversation.py

git add v2_application/conversation.py tests/test_v2_conversation_reducer.py tests/test_v2_turn_executor.py
git commit -m "refactor: keep reducer decisions separate from Maya prose"
```

---

### Task 7: Separate asynchronous system artifacts from Maya conversation

**Files:**
- Modify: `v2_application/completion.py`
- Modify: `v2_application/completion_projector.py`
- Modify: `tests/test_v2_completion.py`
- Modify: `tests/test_v2_completion_projector.py`

**Interfaces:**
- Produces: an explicit `PublicMessageAuthor` enum with `MAYA` and `AUTHENTICATED_SYSTEM`.
- Preserves: exact provider-issued URLs/Pix instructions and receipt-confirmed completion notices without representing them as Maya-authored conversation.

- [ ] **Step 1: Add authorship to outbox rows**

Define:

```python
class PublicMessageAuthor(str, Enum):
    MAYA = "maya"
    AUTHENTICATED_SYSTEM = "authenticated_system"
```

Require every `PublicReply` to declare an author. Synchronous executor rows use `MAYA`; completion projector rows use `AUTHENTICATED_SYSTEM`.

- [ ] **Step 2: Prove the completion projector does not rewrite a Maya frame**

Tests must show completion projection is sourced only from confirmed ledger receipts or provider-issued payment payloads, has no `ModelProposal`, and is explicitly authored as `AUTHENTICATED_SYSTEM`. Preserve exact payment URLs and provider public instructions.

- [ ] **Step 3: Run and commit**

```bash
pytest -q tests -k 'completion or public_outbox'
ruff check v2_application/completion.py v2_application/completion_projector.py

git add v2_application/completion.py v2_application/completion_projector.py tests/test_v2_completion.py tests/test_v2_completion_projector.py
git commit -m "feat: label authenticated system notifications explicitly"
```

---

### Task 8: Add a global source boundary against controller prose mutation

**Files:**
- Create: `tests/test_v2_model_text_authority.py`
- Modify: any false-positive-exempt test helper paths only when mechanically necessary.

**Interfaces:**
- Produces: AST regression that prevents future post-model `reply_chunks` mutation.

- [ ] **Step 1: Add the AST authority test**

Parse `v2_application`, `v2_adapters`, and synchronous boundary modules. Reject:

- `replace(..., reply_chunks=...)` outside a named model-frame carry function;
- `ModelProposal(..., reply_chunks=(<literal>, ...))` outside tests;
- `ConversationReply(..., (<literal>, ...))` in synchronous reducer/executor code;
- clarification canonicalization that assigns to `reply_chunks` after decode.

Allow only model response decoding, unchanged transport, private test fakes, and `AUTHENTICATED_SYSTEM` completion payload construction.

- [ ] **Step 2: Add causal dynamic sentinels**

For each old transformation site, assert the final receipt chunks equal a unique model-frame sentinel or the corrected model-frame sentinel. Verify no parent phrase from the starting candidate appears.

- [ ] **Step 3: Run and commit**

```bash
pytest -q tests/test_v2_model_text_authority.py

git add tests/test_v2_model_text_authority.py
git commit -m "test: forbid controller-authored Maya replies"
```

---

### Task 9: Canonical verification and isolated real-model qualification

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Create outside Git: a new evidence directory named with the final short SHA.
- Reuse and copy, rather than mutate in place: `/home/ubuntu/maya-v2-random-conversations-7123994/run_random_conversations.py`.

**Interfaces:**
- Consumes: all preceding commits.
- Produces: a frozen commit/tree/image identity, canonical tests, repeated isolated real-model evidence, manual review, and a separate rollout decision.

- [ ] **Step 1: Update active authority**

Point `docs/refactor/ACTIVE.md` to the new design and this plan. Record the causal RED, approved global authorship rule, zero-deploy constraint, and exact next gate.

- [ ] **Step 2: Run static and focused gates**

```bash
git diff --check
python -m compileall -q v2_contracts v2_application v2_adapters reservation_boundary scripts
ruff check \
  v2_contracts/model.py \
  v2_adapters/hermes_model.py \
  v2_application/turn_executor.py \
  v2_application/public_reply.py \
  v2_application/turn_plan.py \
  v2_application/conversation.py \
  v2_application/completion.py \
  v2_application/completion_projector.py
pytest -q \
  tests/test_v2_profile_and_model_grammar.py \
  tests/test_v2_hermes_model_adapter.py \
  tests/test_v2_public_reply.py \
  tests/test_v2_turn_plan.py \
  tests/test_v2_turn_executor.py \
  tests/test_v2_model_text_authority.py
```

- [ ] **Step 3: Run the canonical full suite from a clean environment**

Use the repository's explicit V2 config path and canonical clean-environment pytest command. Expected: exit code `0`; record exact passed/deselected/warning/subtest counts.

- [ ] **Step 4: Audit source mechanically**

AST-scan controller/application code for `reply_chunks=` mutations, literal `ConversationReply` prose, customer-text regex, `.casefold()` routing, keyword triggers, aliases, and extractors. Manually classify every match; no semantic-language branch may remain.

- [ ] **Step 5: Build and validate a local image**

Build a new local image from the exact commit. Verify default non-root UID/GID, read-only filesystem, `--network none`, exact Git SHA/tree, and no mutable candidate mount. Do not push it.

- [ ] **Step 6: Repeat the holder scenario three times**

Use fresh private/boundary journals per repetition, real Hermes model, fake read providers, zero writers, zero senders, and zero external network except the isolated model child. Require the exact typed question to appear in final public chunks and require no provider read until children is explicitly answered.

- [ ] **Step 7: Run and manually review the broader matrix**

Run all prior multi-turn scenarios with a new evidence path. Compare each final model frame with each committed public reply byte-for-byte. Require zero parent rewrite, zero deterministic fallback, zero real business provider calls, and zero external effects.

- [ ] **Step 8: Freeze and report without rollout**

Commit all green changes, record SHA/tree/image ID/checksums, ensure worktree and temporary-auth cleanup, and report `SAFE_FOR_CONTROLLED_CANARY` and `SAFE_FOR_BROAD_ROLLOUT` separately. Do not deploy or promote.
