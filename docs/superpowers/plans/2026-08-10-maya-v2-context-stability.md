# Maya V2 Context Stability Implementation Plan

> Execute in `/home/ubuntu/agente-v2/.worktrees/maya-v2-context-stability`. Use strict TDD. No deploy or real business-provider effect.

**Goal:** Give Maya bounded private dialogue, one model-owned semantic progress review, exact handoff lifecycle context, and granular typed-question grounding so she advances journeys without deterministic NLU or keyword triggers.

**Architecture:** Extend the private customer owner with a four-exchange integrity-bound journal, project it through `ModelRequest`, upgrade the model protocol to v7 with `clarification_question`, replace boolean handoff state with the exact durable status, and remove prose classification from positive grounding. Trigger one progress review solely from closed proposal structure.

**Tech stack:** Python dataclasses, SQLite STRICT tables, Hermes child subprocess, pytest, Ruff, Docker/Compose.

---

## Task 1 — Freeze authority and baseline

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- Create: `docs/superpowers/specs/2026-08-10-maya-v2-context-stability-design.md`
- Create: `docs/superpowers/plans/2026-08-10-maya-v2-context-stability.md`

1. Verify branch derives from `9d3be1a...` and is clean.
2. Run focused baseline tests for executor, adapter, public reply, private owner, handoff, and child.
3. Commit only control/design documents.

## Task 2 — RED: bounded private dialogue contract and owner

**Files:**
- Modify: `tests/test_v2_private_customer_facts.py`
- Modify: `tests/test_v2_model_contracts.py`
- Modify: `v2_contracts/model.py`
- Modify: `v2_application/private_customer_facts.py`

1. Add failing contract tests for `ConversationExchange`: exact types, max four exchanges, per-field/aggregate byte bounds, oldest-to-newest tuple, and `repr` redaction.
2. Add failing store tests for exact-idempotent record/load, divergent replay rejection, `0600`, integrity tamper detection, oldest-row pruning, and restart persistence.
3. Implement the contract and STRICT table with domain-separated hash.
4. Run only these tests until green and commit.

## Task 3 — RED: context transport and commit/replay repair

**Files:**
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_hermes_child.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `v2_adapters/hermes_model.py`
- Modify: `v2_host/hermes_child.py`
- Modify: `scripts/phase8_hermes_child.py`
- Modify: `v2_application/turn_executor.py`

1. Add failing tests proving four exchanges become alternating private model messages before the untouched current request.
2. Add child tests rejecting non-alternating, oversized, or more-than-four histories.
3. Add executor tests proving the next turn receives the prior committed customer/reply exchange and that replay repairs an idempotent missing dialogue row without a model/provider call.
4. Implement load into every model request and record after commit/replay.
5. Verify artifacts and `repr` do not contain raw dialogue; commit.

## Task 4 — RED: v7 typed clarification and nonlexical grounding

**Files:**
- Modify: `tests/test_v2_model_contracts.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_public_reply.py`
- Modify: `v2_contracts/model.py`
- Modify: `v2_adapters/hermes_model.py`
- Modify: `v2_application/public_reply.py`
- Modify: `config/v2_luna_system_prompt.txt`

1. Add failing parser/contract tests for exact `v2-model-proposal-v7` and `clarification_question`.
2. Add a causal grounding test: an untrusted positive claim plus a typed holder question must produce deterministic accepted observation text plus the exact question.
3. Add a source guard test proving `public_reply.py` has no `re`, keyword marker, substring, or natural-language contradiction classifier.
4. Implement v7, always-render positive typed observations, and append only the typed question.
5. Keep non-positive paths unchanged; commit.

## Task 5 — RED: exact receipt-aware handoff status

**Files:**
- Modify: `tests/test_v2_model_contracts.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_luna_prompt.py`
- Modify: `v2_contracts/model.py`
- Modify: `v2_adapters/hermes_model.py`
- Modify: `v2_application/turn_executor.py`
- Modify: `config/v2_luna_system_prompt.txt`

1. Add failing tests rejecting boolean handoff and accepting only the exact status catalog.
2. Add executor tests for pending versus acknowledged lifecycle projection.
3. Add prompt/wire tests forbidding “human already monitoring” semantics for pending/acknowledged states.
4. Replace `handoff_active` with `handoff_status=current.state.handoff.status.value`.
5. Run handoff lifecycle regressions and commit.

## Task 6 — RED: one model-owned progress review and final salience

**Files:**
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_hermes_model_adapter.py`
- Modify: `tests/test_v2_luna_prompt.py`
- Modify: `v2_contracts/model.py`
- Modify: `v2_adapters/hermes_model.py`
- Modify: `v2_application/turn_executor.py`
- Modify: `config/v2_luna_system_prompt.txt`

1. Add a failing test where the first proposal is `inform` with prose only; assert exactly one review request with unchanged current message/context and `progress_review_required=True`.
2. Assert the second model proposal owns facts and lodging read; parent invents none.
3. Assert a typed clarification prevents review, a second empty answer does not loop, and pending/active/handoff paths do not trigger it.
4. Add adapter/prompt tests proving `TURN COMPLETION PRIORITY` is the final suffix and contains no keyword allowlist.
5. Implement the structural gate and mutually exclusive review request.
6. Ensure package post-read selection remains within the existing three-completion budget; commit.

## Task 7 — Focused and full verification

1. Run focused suites for model contracts, private store, adapter, child, public reply, executor, handoff, and E2E journeys.
2. Run source scans for customer-text regex/substring/keyword decision branches added by this phase.
3. Run full clean-env pytest, Ruff, compileall, boundary checks, Compose config, Actionlint, and repository CI scripts.
4. Fix root causes with new REDs; never weaken tests or guards.
5. Commit the verified implementation.

## Task 8 — Independent review and controlled conversations

1. Request independent code review of the final diff for privacy, replay/idempotency, provider authority, and absence of deterministic NLU.
2. Build a new immutable OCI labeled with the exact successor commit.
3. Run repeated tool-free real-Hermes conversations with fake/no business providers for:
   - English foreign lodging request;
   - first-frame no-op recovery and next-turn continuity;
   - ambiguous reservation holder with preserved question;
   - discount handoff pending without false human receipt;
   - package choice through summary/confirmation boundaries;
   - replay/idempotency and PII artifact scans.
4. Require repeated stability and zero external effects/receipts.
5. Freeze SHA/tree/OCI and write a qualification report. Do not promote or deploy.
