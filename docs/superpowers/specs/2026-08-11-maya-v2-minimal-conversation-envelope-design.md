# Maya V2 Minimal Conversation Envelope Design

**Date:** 2026-08-11

**Status:** Direction approved by Carlos Eduardo; written specification awaiting review

**Base candidate:** `2dec8237357d6cdd24e123944233e46b31ac7311`

## 1. Objective

Make Maya responsible only for the work that requires understanding and serving the lead:

- understand the complete customer message in context;
- write the WhatsApp reply;
- extract facts that the customer actually supplied;
- ask for missing information;
- request commercial reads;
- identify the option the customer selected;
- classify the customer's semantic decision about a pending proposal.

The parent owns every mechanical value it already knows or can derive exactly. Maya must not copy protocol versions, event IDs, request IDs, locales, action tuples, summary versions, approval bases, effect placeholders, or duplicate customer-facing text into a second field.

This design fixes the real-conversation `NO-GO` without weakening fail-closed validation, moving natural-language interpretation into deterministic controller code, or letting the controller author Maya's public reply.

## 2. Confirmed failures

The immutable candidate passed deterministic suites but failed real-model conversations:

1. `vague_date` produced a valid customer question whose duplicate `clarification_question` field did not equal a `reply_chunk` byte for byte; its repair then copied the wrong `source_event_id`.
2. `same_day_invalid_range` produced the same duplicated-question mismatch; its repair then returned non-JSON text.
3. `activity_buracao` mechanically passed while Maya asserted that age 67 did not prevent or require additional confirmation, although the authenticated fake observation contained no age or suitability policy.

Identical retries sometimes passed. Therefore the defect is protocol and grounding variability, not the customer message and not an overly strict harness.

## 3. Design principles

1. **Maya owns semantics and prose.** The parent never interprets customer language and never writes, appends, replaces, masks, or canonicalizes Maya's customer-facing text.
2. **The parent owns mechanics and authority.** IDs, versions, bindings, capability checks, read identity, provider evidence, receipts, idempotency, persistence, and effects remain deterministic.
3. **Write information once.** A Maya-authored question appears once. An event identity appears only in the parent request. A pending summary binding appears only in authenticated parent state.
4. **Strict shape at generation time.** The provider should return the closed JSON shape rather than free-form text that is repaired after generation.
5. **No lexical controller.** No regex, substring, alias list, parser, keyword route, or stage route may infer facts, intent, clarification, selection, confirmation, handoff, safety, or suitability from customer or Maya text.
6. **One bounded correction.** Grounding conflicts may ask Maya to answer again once from exact typed evidence. The parent never substitutes its own reply.
7. **No data-based output intervention.** Customer-supplied values may be repeated and never trigger output scanning, correction, retry, rewriting, or failure.

## 4. Alternatives considered

### 4.1 More prompt instructions and more retries

Rejected. The prompt already states the duplicate-question and event-copy rules. More retries preserve the same unnecessary work, increase latency and cost, and only hide stochastic failure.

### 4.2 Parent normalization of malformed V7 output

Rejected. Copying `clarification_question` into `reply_chunks`, replacing a wrong event ID, or extracting JSON from Markdown would make an invalid model frame appear valid while retaining the redundant contract. It also creates additional controller transformations that are difficult to audit.

### 4.3 Selected: minimal semantic V8 wire

The model emits only semantic decisions and Maya-authored messages. The adapter adds parent-owned mechanics after strict decoding. Provider-level structured output prevents malformed transport where supported.

## 5. V8 model output

The model-facing response no longer contains `schema`, `source_event_id`, `effect_proposals`, `target_offer_id`, `target_offer_ids`, `confirmed_summary_version`, `confirmed_action_kinds`, `approval_basis`, `clarification_question`, read `request_id`, or read `locale`.

The complete V8 model output has eight top-level fields:

```json
{
  "intent": "inform|select|adjust|confirm|request_handoff",
  "reply_chunks": [
    {
      "text": "Maya-authored WhatsApp message",
      "expects_reply": false
    }
  ],
  "facts": [],
  "read_requests": [],
  "selected_choice_refs": [],
  "selection_requested": false,
  "pending_action_disposition": null,
  "passengers": []
}
```

### 5.1 Reply chunks

- One or two chunks are allowed.
- Each chunk contains the text once and an `expects_reply` boolean.
- At most one chunk may have `expects_reply=true`.
- If present, it must be the final chunk.
- The adapter derives internal `clarification_question` from that final chunk's exact `text`.
- The parent does not require a second copy, inspect punctuation, or decide whether prose is a question.

Example:

```json
{
  "reply_chunks": [
    {
      "text": "Para hospedagem, a saída precisa ser posterior à entrada.",
      "expects_reply": false
    },
    {
      "text": "Qual será a data de saída?",
      "expects_reply": true
    }
  ]
}
```

### 5.2 Reads

Maya describes only the commercial read:

```json
{
  "kind": "lodging",
  "check_in": "2026-09-04",
  "check_out": "2026-09-06",
  "adults": 3,
  "children": 0
}
```

The parent derives:

- canonical `request_id` from the current source event and read kind;
- authoritative locale from the authenticated contact binding;
- request/observation hashes and provider binding.

Maya never copies an event ID into a read request.

### 5.3 Selection

Current observations expose bounded, ephemeral `choice_ref` values such as `lodging:1` and `activity:1`. Maya resolves the lead's natural choice to those references. The parent maps them to private `offer_id` values and performs every existing exact binding, freshness, party, price, currency, and capability check.

`selected_choice_refs` replaces the dual `target_offer_id`/`target_offer_ids` fields:

- zero values when there is no selection;
- one value for lodging or an activity;
- exactly two values, one per service, for a package.

Maya never needs to copy long opaque offer IDs.

### 5.4 Pending action

Maya returns only the semantic disposition:

```text
null
preserve
revoke
```

For `intent=confirm`, a live exact pending action must exist. The parent binds the confirmation to the already authenticated:

- summary version;
- exact action kinds;
- `contextual_reference` approval basis;
- expiry;
- subject projection;
- capability policy.

Maya does not reconstruct any of those values. Missing, expired, changed, ambiguous, or invalid confirmation remains non-authorizing.

### 5.5 Effects

Maya does not emit `effect_proposals`. The model has no tool or direct effect capability. Existing parent-owned planning derives possible commands only from an accepted intent plus exact authenticated state and then applies all current reservation, payment, handoff, outbox, idempotency, and worker gates.

## 6. Internal compatibility

The internal `ModelProposal` may continue to carry fields required by downstream application code during migration. The V8 adapter constructs those fields deterministically:

- `source_event_id = ModelRequest.source_event_id`;
- `clarification_question = last reply text` only when its model-authored flag is true;
- `effect_proposals = ()`;
- read IDs and locale from parent authority;
- target offer IDs from validated choice references;
- confirmation version/action/basis from the exact pending context.

This is structural projection, not natural-language interpretation or public-text rewriting.

Legacy V1-V7 decoders remain read-compatible for frozen fixtures and old authenticated frames. New productive model requests require V8. A legacy frame never gains V8 authority by normalization.

## 7. Structured generation

### 7.1 Capability probe

Before changing the child, run one isolated real-provider probe using the exact `openai-codex` transport and `gpt-5.6-luna` model with a strict Responses API JSON Schema. The probe must prove:

- valid V8 JSON is returned without Markdown;
- unknown keys are rejected by the provider contract;
- all required fields are present;
- the child still has zero tools and disposable state;
- no business provider or effect path exists.

### 7.2 Preferred transport

When supported, the child passes a strict `text.format` JSON Schema to the Responses API. Hermes transport support is narrow and explicit: permit and validate the `text` request field required for structured output rather than passing arbitrary body keys.

The child then canonicalizes the already-structured object and emits the existing authenticated result marker. It does not search for braces, strip code fences, run `json-repair`, or infer missing fields.

### 7.3 Unsupported-provider behavior

If the effective Codex endpoint rejects strict structured output, stop that implementation branch and preserve the probe. Do not silently fall back to permissive JSON extraction or increase retry count. The fallback design must be reviewed separately because it changes the transport guarantee.

## 8. Grounding without burdening Maya

Maya receives sanitized observations and answers naturally. A narrow tool-free reviewer—not Maya and not a lexical controller—checks only provider-backed public claims that can create material misinformation:

- price and availability;
- policy;
- operational status;
- safety, eligibility, and suitability.

The reviewer receives only:

- the current customer question;
- exact sanitized observations;
- Maya's proposed reply chunks;
- the closed claim categories relevant to that turn.

It returns a closed decision bound to the proposal and observation digests:

```json
{
  "decision": "accept|unsupported_claim",
  "unsupported_categories": []
}
```

It cannot write customer text, add facts, request reads, choose offers, confirm, hand off, or authorize effects.

When it returns `unsupported_claim`, Maya receives one correction request containing the exact observations and closed reason categories. Maya authors a new reply. A second invalid result fails before public persistence or delivery.

This review is never invoked because a reply contains customer-supplied data.

## 9. Buracão behavior

Activity-description DTOs represent unknown evidence explicitly:

```json
{
  "product_id": "product:buracao",
  "public_name": "Cachoeira do Buracão",
  "description": "...",
  "age_guidance": null,
  "suitability_guidance": null
}
```

With null guidance, Maya may state that the available information does not confirm an age rule and may ask about relevant mobility or health constraints. She may not conclude that age does or does not prevent the tour, that no additional confirmation is necessary, or that the participant is suitable.

The reviewer enforces this evidence boundary semantically. The parent does not scan phrases such as “não impede” or maintain an age keyword list.

## 10. Failure and persistence semantics

A transport-invalid, schema-invalid, grounding-invalid, or correction-invalid response causes:

- no public reply row for that turn;
- no command or relay row;
- no channel delivery row;
- no business provider write;
- no parent-authored fallback reply;
- a categorical failure artifact containing only reason enums and commitments.

Earlier committed turns remain valid. Exact-event retry remains idempotent. Customer facts accepted by their existing typed owner do not become an output gate.

## 11. Implementation slices

Implementation is deliberately split into short causal slices:

1. V8 reply chunks and parent-owned source-event binding.
2. Parent-owned read identity and locale.
3. Parent-owned confirmation binding and removal of model effect placeholders.
4. Choice references and offer-ID projection.
5. Provider structured-output probe and transport support.
6. Narrow grounding reviewer and Buracão evidence DTO.
7. Prompt reduction after the mechanics have left the model contract.

Each slice starts with one failing behavior test, introduces only the minimum production change, runs its focused gate, and receives a checkpoint before the next slice. No unrelated refactor is included.

## 12. Verification

### 12.1 Deterministic RED/GREEN

Tests must prove:

- V8 has no model-authored source/event/request IDs;
- a customer question is authored once and projected without text changes;
- invalid reply-chunk flags fail closed;
- read IDs and locale come from exact parent authority;
- confirmation binding comes only from an exact live pending action;
- no model field can widen action kinds or alter summary version;
- choice refs map only to current authenticated observations;
- malformed structured output causes no persistence;
- an unsupported Buracão age/suitability assertion receives one Maya-owned correction;
- correction failure produces zero public/effect rows;
- customer-supplied values trigger no reviewer or correction;
- legacy V1-V7 fixtures remain decodable but cannot be emitted productively.

### 12.2 One expensive gate per immutable SHA

After focused and canonical tests are green, run one real-model/fake-provider qualification gate on an immutable commit/tree:

- exact `vague_date` message: **10/10**;
- exact `same_day_invalid_range` message: **10/10**;
- `activity_buracao`: **10/10**, with every final claim grounded in its observation;
- complete historical Matrices A and B;
- zero protocol repair frames for copied IDs or duplicated questions;
- zero malformed JSON frames;
- zero deterministic fallback;
- zero real business provider calls;
- zero external effects;
- exact Maya-authored final chunks preserved.

Any non-passing attempt remains evidence and keeps the candidate `NO-GO`. Later passes do not erase it.

## 13. Acceptance criteria

The successor is eligible for conversational `GO` only when:

1. Maya's productive output contains only the eight semantic/conversational fields defined in Section 5.
2. No opaque parent-known identifier or confirmation authority value is copied by Maya.
3. No customer-facing text is duplicated across protocol fields.
4. The controller performs no natural-language interpretation and authors no Maya reply.
5. Structured generation prevents free-form non-JSON output on the effective provider path.
6. Material provider-backed claims pass the closed semantic grounding review.
7. The historical failing conversations meet the exact stability gate in Section 12.2.
8. Existing canonical transactional, provider, receipt, replay, idempotency, and zero-effect tests remain green.
9. No push, deploy, restart, canary, provider write, or rollout occurs without separate explicit authorization.
