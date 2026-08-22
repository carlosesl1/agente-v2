# Maya V2 Single-Agent Grounding Removal Design

**Date:** 2026-08-22

**Status:** Approved scope; written specification awaiting final review

**Base candidate:** `a123db7e99d76872aeb38968904898e644fd91d9`

## 1. Objective

Remove the material-grounding reviewer from the active Maya V2 runtime. Maya is the only semantic agent: she interprets the conversation, requests reads, consumes authenticated public observations, and writes the final customer-facing reply.

The parent remains deterministic. It validates closed schemas, current observation bindings, selection and confirmation contracts, command authorization, idempotency, persistence, and effects. It does not semantically judge, rewrite, or replace Maya's text.

## 2. Non-goals

- Do not remove deterministic contract or effect guards.
- Do not allow price, availability, links, reservations, payments, or operational status without the existing typed evidence and receipts.
- Do not add a lexical or regex-based public-text validator.
- Do not create a replacement reviewer, fallback agent, or hidden model call.
- Do not rewrite historical specs or plans; they remain immutable records of the prior decision.
- Do not change provider-write authorization or create any live reservation/payment during validation.

## 3. Single-agent architecture

The active flow is:

1. ManyChat ingress normalizes and debounces the customer message.
2. The parent builds one `ModelRequest` for Maya from the current message, committed dialogue, state, business clock, and typed state facts.
3. Maya either answers or requests exact provider reads.
4. The parent executes authorized read-only tools and returns sanitized public observations to Maya.
5. Maya writes the final V8 response using those observations.
6. The parent validates only structural and mechanical invariants and commits the result.
7. Existing outbox workers deliver committed public chunks and execute separately authorized commands.

A provider-read turn can contain more than one invocation of Maya because the first invocation asks for reads and the second consumes their results. These are continuations of the same Maya agent and contract. There is no second semantic persona and no post-answer model reviewer.

## 4. Information supplied to Maya

Maya continues to receive every sanitized `ReadObservation.public_payload`. The active system prompt must make the evidence contract explicit:

- `available=true` means available for the exact observed product, date, and party; `available=false` means unavailable for that scope.
- Price may be stated only when the corresponding observation is available and provides the authoritative final total under its existing typed price flags.
- Group metadata describes scheduling/group context and never overrides provider availability.
- Null `age_guidance` or `suitability_guidance` means the provider supplied no authenticated rule; Maya must describe uncertainty and ask relevant natural questions rather than assert suitability.
- Effects may be claimed only from exact command/provider receipts; a read or proposed action is not execution.
- Public links, provider status, booking status, and payment status remain receipt-bound.
- The agency card link policy charges the configured 20% deposit, while specific monetary values remain observation-bound.

These instructions belong to Maya's active prompt. They are not duplicated into a second model prompt.

## 5. Active code removal

Remove all active reviewer surfaces:

- `_GROUNDING_REVIEW_SYSTEM_PROMPT`;
- `_grounding_review_required`, `_grounding_review_wire`, `_grounding_decision`;
- `HermesModelAdapter._grounding_review` and `_apply_grounding_review`;
- the `complete_audited()` reviewer invocation;
- the `grounding-v1` child contract and its structured-output override;
- `GROUNDING_REVIEW_JSON_SCHEMA` and `grounding_review_request_overrides()`;
- `grounding_review_required` from Bókun public payloads;
- `UNSUPPORTED_OBSERVATION_CLAIM` if no other active path consumes it;
- active tests whose only purpose is the reviewer.

Keep the standard Maya V8 structured-output contract and the existing one-shot protocol repair for malformed Maya V8 output. Protocol repair is a retry of Maya's own schema contract, not a second semantic agent.

## 6. Historical artifacts

Existing historical specs and plans remain unchanged. New active documentation and tests must not instruct operators to repair or tune the removed reviewer. The operational recovery reference created for the reviewer must be retired or rewritten as a historical note so it cannot be followed as current procedure.

## 7. Failure behavior

After removal:

- malformed Maya V8 output still fails closed after the existing bounded protocol repair;
- unauthorized reads, stale choice references, invalid confirmations, and unauthorized effects still fail closed deterministically;
- no response can fail because another model disagrees semantically with Maya;
- no controller-authored public fallback is introduced;
- inbox retry remains reserved for actual model, transport, contract, persistence, or provider failures.

## 8. Verification

### 8.1 Static contract

Tests must prove:

- `HermesModelAdapter.complete_audited()` returns Maya's audited turn without invoking `--contract grounding-v1`;
- the child accepts only the Maya V8 response contract;
- the structured-output module exposes only Maya V8 overrides;
- Bókun activity descriptions do not emit `grounding_review_required`;
- the active source tree outside immutable historical docs contains no reviewer prompt, decision schema, runtime flag, or unsupported-observation correction reason.

### 8.2 Behavioral contract

Use copied production SQLite stores and the original authorized test event. Invoke only the inbox worker with provider writes and outbound delivery mechanically isolated. Require:

- the event reaches `processed` with a canonical receipt;
- the model subprocess transcript contains Maya calls only and no `grounding-v1` command;
- the final answer uses the exact authenticated Bókun availability and price observation;
- no reservation, payment, handoff, e-mail, or public delivery occurs in the isolated canary.

### 8.3 Regression and release gates

Run focused adapter/child/read tests, turn executor and inbox relay regressions, full project pytest, Ruff 0.15.10, compileall, `git diff --check`, and fast-track boundaries. Build one immutable OCI image whose revision label equals the committed SHA. Promote API, worker, and router only after the copied-state canary passes, then verify identical image digest/revision, health, restart count, OOM state, and clean synchronized Git.

## 9. Acceptance criteria

The change is complete only when:

1. No active path can launch a grounding reviewer.
2. Maya receives sufficient authenticated context to handle the affected availability, price, policy, and suitability cases herself.
3. The controller remains deterministic and does not inspect or rewrite natural-language truth.
4. Existing mechanical safety and effect authorization remain green.
5. A production-shaped copied-state canary proves one semantic agent end to end with zero external side effects.
6. Production runs the verified immutable image and the authorized test event can be processed without reviewer-related retry.
