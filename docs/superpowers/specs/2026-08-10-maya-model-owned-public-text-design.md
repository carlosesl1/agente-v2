# Maya Model-Owned Public Text Design

**Date:** 2026-08-10

**Status:** Superseded in part by Carlos Eduardo on 2026-08-11

**Current authority:** Maya remains the sole author of customer-facing text. The 2026-08-11 decision cancels every privacy/PII output check or correction described below: voluntarily supplied data may be repeated, and privacy/PII may not alter, block, retry, redact, or fail a Maya reply. Privacy/PII work must not be started or recommended without an explicit current-chat request.

**Starting candidate:** `7123994605e934e1688b14b0489d3759252d65a8`

## 1. Problem

The V2 parent currently treats `reply_chunks` as mutable after Maya has produced a valid closed proposal. Multiple application and adapter branches replace those chunks with controller-authored phrases for grounding, selection failure, active execution, consultation reuse, confirmation review, deterministic fallback, and completion projection.

A real-model/fake-provider run proved the concrete failure:

1. Maya semantically assigned valid private holder facts.
2. Maya emitted the typed clarification `Haverá alguma criança no grupo?`.
3. `_private_update_no_command_proposal()` persisted the private facts, then replaced the model reply with `Obrigado. Guardei esses dados para continuar a reserva.`.
4. The customer never saw the question.
5. A later turn proceeded with `children=0` without an explicit answer.

This violates the active authority chain: Maya owns conversation semantics and text; the controller owns contracts, consistency, permissions, effects, observations, receipts, and commits.

## 2. Decision

Every customer-facing V2 text is model-owned.

Once a model proposal has been accepted as the final proposal for a turn, its `reply_chunks` are immutable through planning, structured-fact partition, reads, reduction, commit, replay, and delivery. Parent code may:

- reject a proposal;
- remove private structured facts from public state;
- deny reads, selections, confirmations, commands, relays, payments, handoffs, or delivery;
- attach accepted observations and exact operational status to a new model request;
- request one model-owned correction under a closed protocol;
- fail the turn closed when correction does not produce a valid proposal.

Parent code may not author, append, prepend, translate, canonicalize, redact, or replace customer-facing prose.

No deterministic fallback reply is delivered after model failure. Failure means no public turn commit and no channel delivery for that event.

## 3. Alternatives considered

### 3.1 Preserve only typed clarification

This would fix the reproduced defect while retaining parent-authored grounding and operational phrases. It has the smallest blast radius but does not satisfy the selected global rule.

### 3.2 Never rewrite; reject immediately

This gives strict authorship but makes recoverable protocol or grounding mistakes silent on the first failure.

### 3.3 Selected: model-owned finalization with one bounded correction

The controller never writes public prose. It provides typed accepted state and a closed correction reason to Maya. Maya writes the corrected public reply. One correction attempt is allowed; a second failure is terminal and non-delivering.

## 4. Public text authority contract

### 4.1 Immutable model text

`ModelProposal.reply_chunks` from the final accepted model frame are the exact chunks passed to the reducer and committed publicly.

The following are forbidden after final model acceptance:

- `replace(proposal, reply_chunks=...)` in controller/application code;
- constructing a replacement `ModelProposal` with controller-authored `reply_chunks`;
- grounding renderers that discard Maya's draft;
- reducer or completion projector selection of controller-authored customer text;
- adapter-generated confirmation, rejection, recursive-read, or deterministic fallback prose;
- adapter canonicalization that substitutes one reply chunk for another.

The parser may normalize JSON transport representation and reject invalid shape, but it may not change text bytes after parsing.

### 4.2 Typed clarification

`clarification_question` remains model-owned and must be byte-for-byte equal to one member of `reply_chunks`. The controller rejects mismatch; it does not search for a semantically similar chunk and does not rewrite either field.

A private-fact update does not weaken this rule. Private structured facts are partitioned and persisted, while the model's question remains unchanged.

### 4.3 Customer-supplied values

Structured customer facts may retain their existing typed owner, but this ownership never controls Maya's prose. The parent performs no exact-value, substring, component, regex, semantic, or corpus comparison between customer data and `reply_chunks`/`clarification_question`.

Maya may repeat any voluntarily supplied name, name component, e-mail, phone, country, birth date, gender, or passenger value. Such repetition causes no correction request, redaction, masking, retry, response suppression, or fail-closed path.

## 5. Model-owned correction protocol

Introduce a closed correction context on `ModelRequest` rather than a controller reply:

```python
public_reply_correction: PublicReplyCorrection | None
```

The correction contains only typed, bounded information:

- digest of the rejected proposal;
- one or more closed reason enums;
- accepted public facts;
- accepted read observations;
- exact pending action, active execution, critical outcome, and handoff status already available to the model;
- whether reads, selection, confirmation, effects, passengers, or private updates are permitted in the correction frame.

Closed reasons cover structural or authority failures, including:

- `typed_clarification_mismatch`;
- `unsupported_observation_claim`;
- `operational_status_conflict`;
- `read_removed_by_authority`;
- `selection_binding_failure`;
- `active_execution_conflict`;
- `stale_consultation_reuse`;
- `invalid_confirmation_review`;
- `recursive_read_after_observation`.

The correction frame uses the same `v2-model-proposal-v7` contract. It must preserve `source_event_id`, produce one or two non-empty Maya-authored chunks, emit no unauthorized structured action, and conform to the supplied accepted typed state.

There is at most one correction frame for a public-text problem. Existing protocol repair and semantic progress review budgets must be consolidated so nested helper calls cannot reopen another correction budget.

## 6. Typed evidence and unsupported claims

The parent still owns factual authority. It never infers semantics from the customer message.

### 6.1 Provider reads

After accepted reads, Maya receives the exact sanitized `ReadObservation` objects and writes the final availability/price/description reply. `apply_positive_grounding()` no longer renders text.

The final proposal must have `read_requests=()` after observations. Structured facts, offer bindings, amounts, currencies, dates, availability, and product names remain checked against accepted observations where the existing contract exposes them.

Because prose is model-owned, a narrow model-owned public-reply review performs semantic consistency checking against the same typed observations. It returns either:

- `accept` bound to the proposal digest; or
- one corrected `v2-model-proposal-v7` directly.

The reviewer has no tools and cannot authorize effects. An invalid reviewer result fails closed.

### 6.2 Operational status

Handoff, command execution, reservation/payment outcomes, confirmation state, and delivery receipts remain exact typed fields. Maya writes the wording. The reviewer checks that the wording does not strengthen the authenticated status. The controller does not translate status into prose.

### 6.3 Transactional system artifacts

Canonical reservation summaries, provider-issued payment links/Pix instructions, and receipt-confirmed completion notices are not Maya conversational prose. They are separate `authenticated_system` artifacts with explicit authorship. The controller may construct these only from canonical domain material or exact provider/receipt payloads; it may not present them as Maya-authored chunks or silently replace a Maya frame with them.

When a turn produces both a Maya explanation and a canonical transactional artifact, both are preserved as distinct authored outputs. Confirmation binds only to the authenticated system summary version, action kinds, subject signature, and canonical material. Maya cannot change economic or operational material through prose.

## 7. Controller transformations

The implementation removes or converts all customer-text transformations found in the starting candidate:

- `v2_application/public_reply.py` positive grounding replacement;
- `_selection_binding_failure_proposal()` text replacement;
- `_active_execution_guard_proposal()` text replacement;
- `_collection_only_proposal()` text generation;
- `_private_update_no_command_proposal()` text generation;
- consultation-reuse fallback text;
- regressive post-command text replacement;
- `turn_plan.py` reply replacement;
- generic reducer replies used instead of Maya text;
- completion projector customer text;
- Hermes adapter confirmation-review prose;
- deterministic protocol fallback prose;
- recursive-read fallback prose;
- parser clarification canonicalization that changes reply chunks.

Each branch becomes one of:

1. preserve the final Maya chunks unchanged;
2. ask Maya once for a closed correction using exact typed state;
3. fail before public commit/delivery.

## 8. Safety invariants

This change does not authorize any new effect.

The following remain deterministic and parent-owned:

- schema parsing and exact enums;
- source-event binding;
- typed fact validation;
- private/public ownership and persistence;
- read allowlists, batch limits, freshness, request/observation hashes, and recursive-read closure;
- offer binding and selection consistency;
- pending-summary version/action binding;
- command capability and idempotency;
- payment, reservation, handoff, relay, outbox, writer/reconciler, and delivery gates;
- exact receipts and lifecycle status;
- transaction boundaries and replay;
- zero-effect laboratory isolation.

The controller may deny behavior without explaining it in its own prose. Maya is the only component that explains behavior to the customer.

## 9. Failure semantics

A public-text correction failure causes:

- no public reply rows;
- no command or relay rows;
- no channel outbox/delivery row;
- no provider write;
- no deterministic fallback text;
- a categorical technical failure artifact containing only reason enums and commitments;
- exact idempotent retry behavior for the same inbound event.

Customer facts already accepted from the original message remain in their typed owner under the existing crash/retry contract; they do not become a conversational progress gate and may also appear in Maya-authored public text.

## 10. Testing strategy

### 10.1 Causal RED

Add an executor test reproducing the exact shape:

- original message identifies a synthetic holder and provides private holder facts;
- Maya emits typed private facts plus `clarification_question="Haverá alguma criança no grupo?"`;
- no read/effect is proposed;
- expected final public reply is exactly Maya's question;
- customer facts are persisted by their typed owner while Maya's exact question remains public;
- no command, relay, or external effect exists.

The test must fail on the starting candidate because the final reply is the controller's generic collection text.

### 10.2 Global authorship tests

For every former rewrite branch, use a unique Maya-authored sentinel and prove either:

- the exact sentinel is committed unchanged; or
- the branch invokes exactly one model-owned correction and commits exactly the corrected model chunks.

Add an AST/source boundary test forbidding application/adapter assignment of customer-facing `reply_chunks` outside:

- decoding a model response;
- carrying unchanged existing chunks;
- test fakes.

### 10.3 Safety regressions

Prove:

- voluntarily supplied customer data in Maya text triggers no correction, redaction, retry, or failure;
- a failed correction creates no public/command/relay/delivery rows;
- observations remain required for price/availability claims;
- pending handoff cannot be described as human receipt;
- active execution cannot create a duplicate command;
- selection failure cannot silently select another offer;
- invalid confirmation cannot execute;
- replay returns the exact committed Maya chunks;
- no controller-side customer-message regex, keyword, alias, or trigger is introduced.

### 10.4 Real-model qualification

Repeat the holder scenario in at least three fresh isolated journals, then run the broader fake-provider matrix. Qualification requires:

- typed clarification appears byte-for-byte in the public reply;
- no unanswered party field is defaulted into a provider read;
- no parent-authored public text is observed;
- no deterministic fallback;
- zero business provider writes and external effects;
- manual review of every final reply.

## 11. Migration and compatibility

The proposal wire remains v7 unless the correction contract requires a version bump. Durable historical receipts remain readable. New turns use model-owned text authority. No historical public reply is rewritten.

No deployment, restart, image publication, canary, promotion, provider write, or broad rollout is part of this implementation phase.

## 12. Acceptance criteria

The successor is acceptable only when:

1. the reproduced private-holder clarification test passes after a witnessed RED;
2. every post-model customer-text rewrite is removed or converted to bounded model-owned correction;
3. accepted final `reply_chunks` are byte-identical from final model frame through commit and replay;
4. no privacy/PII gate, parser, corpus, prompt instruction, or correction reason can affect Maya text;
5. focused and full regression gates pass;
6. repeated real-model/fake-provider qualification passes with manual review;
7. the candidate is frozen under a new commit/tree/image identity;
8. rollout remains separately blocked until explicit authorization.
