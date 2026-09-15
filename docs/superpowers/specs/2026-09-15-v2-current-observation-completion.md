# Maya V2 Current-Observation Completion Design

**Date:** 2026-09-15
**Authorization:** Carlos approved freeing disk space and proceeding with the previously proposed correction and controlled retest.

## Problem

A real isolated-contact conversation produced a Cloudbeds lodging observation with `status=positive`, `safe_for_public_claims=true`, and an exact BRL total. The second Maya frame received that observation but answered that the price was not yet confirmed. The same authenticated total was recalled correctly in later turns. Provider read, acceptance, persistence, continuity, delivery, and effect isolation all passed.

## Scope

Correct only the V2 Maya response behavior after current-turn provider observations. Do not change Cloudbeds/Bókun adapters, read acceptance, public payloads, controller text handling, reservation/payment authority, V3, legacy, or Maya Ops.

## Considered approaches

### 1. Edit only the base commercial prompt

Smallest diff, but the relevant instruction competes with the complete long-lived prompt in both pre-read and post-read frames. The failed runtime already had broad instructions to use observations and show final totals, so another distant sentence would provide weak causal separation.

### 2. Add a dynamic post-observation completion suffix — selected

When and only when `ModelRequest.observations` is non-empty, append a concise highest-salience contract to the existing Maya system envelope. It identifies the request as the post-read answer frame and requires Maya to answer from every relevant current public observation, state positive availability and exact total/currency when present, preserve useful public labels/date/time/group evidence, and avoid contradicting an exact current result. The model remains the sole semantic interpreter and prose author.

### 3. Parse or review Maya's reply in the controller — rejected

A lexical/parser gate would duplicate semantic ownership and violate the no-regex/no-keyword architecture. A second LLM reviewer would create a competing semantic authority. A deterministic rewriter would violate byte-exact Maya authorship. None is introduced.

## Architecture and data flow

1. The first Maya frame interprets the complete customer message and emits a typed provider read.
2. The controller validates and dispatches the existing read exactly once.
3. The provider returns an authenticated `ReadObservation` with public payload and private binding separated as today.
4. `_request_wire()` projects the public observation into the second Maya frame.
5. Because `request.observations` is non-empty, `_request_wire()` appends the current-observation completion suffix after the general completion rules.
6. Maya authors the final V8 response; the controller validates schema, authority and effects without inspecting or rewriting the prose.

## Behavioral contract

- The suffix is absent from frames with no current observations.
- The suffix is present exactly once when current observations exist.
- A relevant positive offer with an exact `total_amount` and `currency` must be answered with that exact total and currency, not described as unconfirmed.
- Availability, public label, dates, start time and formed-group status are used when present and relevant.
- Negative/unknown observations are reported faithfully; missing fields are never invented.
- No recursive provider read is emitted after observations.
- No effect, selection or confirmation authority is broadened.

## Testing

- RED/GREEN wire tests prove conditional suffix presence and absence while proving the exact `600.00 BRL` payload reaches the model unchanged.
- Focused adapter and turn-executor suites prove no second semantic model and no controller prose rewrite.
- Canonical clean-environment pytest, Ruff, compileall, diff check and repository boundary guard qualify the commit.
- The immutable image receives a direct real-model post-observation causal smoke.
- The isolated WhatsApp contact repeats the lodging date-change query once with a fresh operation ID. Read, final response, delivery, no duplicate and zero commercial effects are reconciled before any GA promotion.

## Rollout and rollback

Build a new immutable image from the final commit. Preserve the current Terra/high image and runtime authority as rollback. Promote the isolated test runtime first. Promote GA only after the exact image passes the model and WhatsApp gates. Any failed or ambiguous gate stops promotion; no blind resend or provider write is allowed.
