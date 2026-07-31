# Bókun Submit Booking-ID Confirmation Design

**Date:** 2026-07-31
**Parent candidate:** `16d4d90e45c2ceb6bba12565f1862e092bcb9c56`

## Goal

When Bókun accepts a reservation submit and returns one unambiguous reservation-level booking ID, classify the provider effect as confirmed immediately. A strict GET read-back must not be able to erase that confirmation or prevent the final public completion from being projected.

## Authority boundary

The effect is confirmed only when all of the following hold:

1. the authenticated durable command passed the existing private binding, permit, fence and idempotency checks;
2. cart and checkout validation bound the exact product, activity date, selected start/rate, ordered passenger categories, party, base amount, fee-inclusive total and BRL currency before submit;
3. Bókun returned a successful submit response;
4. the closed reservation-reference collector found exactly one unique reservation-level booking ID across the allowed submit envelope;
5. no conflicting reservation-level ID alias exists.

Activity-local and passenger-local IDs remain excluded from reservation-reference collection.

## Data flow

1. Keep the existing quote, private pre-fence read, cart, checkout and submit sequence unchanged through the submit response.
2. Parse and consistency-check the booking ID immediately.
3. Return the canonical provider result as `confirmed` with that booking ID without performing a booking GET in the synchronous write transport.
4. Map the canonical provider result to `ExecutionCertainty.EFFECT_CONFIRMED` and persist the opaque provider-reference fingerprint through the existing durable outcome transition.
5. Run the existing completion projection exactly once, producing the final public result. Delivery to ManyChat remains a separate operational gate.

## Error classification

- Deterministic rejection before a booking ID remains `rejected`/no confirmed effect according to the existing closed status mapping.
- Timeout, transport failure, ambiguous response, missing booking ID after an uncertain submit, or conflicting reservation-level IDs remains post-fence unknown and must never trigger a submit retry.
- A successful response with one unambiguous booking ID is monotonic evidence of creation. No later synchronous validation may downgrade it.
- GET auditing, if added later, is a separate read-only observer concern. It may raise an operational alert for subject divergence but cannot mutate a confirmed creation into unknown or issue another submit.

## Scope

### In scope

- Bókun activity reservation write transport.
- Only the authenticated `v2-reservation-dispatch-v2` path used by the V2 agent.
- Provider result classification and existing completion path.
- Regression tests for confirmation, no read-back dependency, alias conflicts and submit count.
- Existing 2-adult + 1-child path.

### Out of scope

- Cloudbeds behavior.
- The separate legacy Bókun payload path.
- ManyChat delivery enablement.
- Payment, cancellation, email or handoff.
- A new asynchronous auditing subsystem.
- Reconciliation of historical `CALLED_UNKNOWN` rows, including booking `99079892`; that requires a separate authenticated migration/reconciliation operation and must not be patched directly.

## Required tests

1. Submit returns HTTP 2xx plus one valid booking ID: result is confirmed and no booking GET occurs.
2. A booking GET would have timed out or diverged under the old sequence: it is not called; confirmation remains successful.
3. Submit envelope contains conflicting reservation-level IDs: fail ambiguous after one submit, no GET and no retry.
4. Submit response lacks a booking ID under an ambiguous status: remain unknown after one submit, no retry.
5. End-to-end local worker test: one fence slot, one submit, `EFFECT_CONFIRMED`, one completion projection and one durable final public result.
6. Existing product/date/start/party/economic/private-binding/idempotency tests remain green.

## Release gates

- RED observed on the exact parent candidate.
- Focused provider, reservation-worker and completion tests.
- Full official test gate with the seven known package/closeout deselections.
- Ruff, fast-track boundaries and `git diff --check`.
- Immutable commit and tree, remote CI on the exact SHA, and independent read-only terminal review.
- No live Bókun write is required for this correction because the already captured real submit/booking evidence reproduces the faulty classification; qualification uses fake transports only.
