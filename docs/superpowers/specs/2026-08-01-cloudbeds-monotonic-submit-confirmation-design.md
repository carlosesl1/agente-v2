# Cloudbeds V2 Monotonic Submit Confirmation Design

**Date:** 2026-08-01  
**Status:** Approved design choice — minimal confirmation-boundary patch  
**Scope:** Cloudbeds V2 fake-only qualification; no real provider write

## Goal

Make a Cloudbeds `postReservation` result monotonic:

```text
HTTP 2xx
+ exactly one canonical principal reservationID
+ no explicit failure marker
= EFFECT_CONFIRMED immediately
```

A later `getReservation` is audit/reconciliation only. It must never downgrade the execution outcome, suppress the final lead reply, authorize a second POST, or create another reservation command/outbox row.

## Accepted submit boundary

`CloudbedsHTTPTransport.__call__("reserve_lodging", ...)` keeps the existing pre-submit revalidation and exactly-one POST behavior. After the POST it classifies only the submit response:

| Submit observation | Result |
|---|---|
| 2xx + one unambiguous principal `reservationID` + no explicit failure | `{"status":"confirmed","reservation_id":"..."}` immediately |
| 2xx + `success:false` | fail closed as explicit no-effect/rejection |
| 2xx + missing ID | ambiguous exception → `CALLED_UNKNOWN` |
| 2xx + conflicting IDs | ambiguous exception → `CALLED_UNKNOWN` |
| non-2xx with any ID | ambiguous exception → `CALLED_UNKNOWN` |
| non-2xx without accepted evidence | fail closed; no POST retry |
| invalid JSON, contradictory envelope, timeout | ambiguous exception → `CALLED_UNKNOWN` |

The write transport performs no GET after the POST. No exception from read-back can occur between accepted submit evidence and the `confirmed` return.

## Persistence

The Cloudbeds principal `reservationID` is persisted in the private execution outcome as the provider reference for an `EFFECT_CONFIRMED` command. It is not copied into the conversational projection or public reply. Bókun retains its existing fingerprint-only reference behavior.

The outcome remains bound to:

- command ID;
- command payload hash;
- provider payload hash;
- submit response evidence hash;
- exactly one consumed dispatch slot;
- the original idempotency key.

## GET-only audit

A Cloudbeds audit projector derives one deterministic audit task from each confirmed Cloudbeds outcome. The task is stored in a separate private SQLite audit database so audit state cannot mutate the execution ledger.

The auditor:

1. claims at most one task;
2. calls only `GET /api/v1.3/getReservation` for the persisted reservation ID;
3. validates the returned principal ID and stable reservation facts available from the command;
4. records `matched`, `retryable_not_visible`, or `divergent` audit state;
5. retries only GET, with a closed maximum attempt count and lease fencing;
6. never calls `postReservation` and has no reservation-write capability;
7. never changes an existing `EFFECT_CONFIRMED` outcome or public completion.

Eventual consistency is represented by retryable GET observations such as not-found/temporarily incomplete payloads. Exhaustion records an audit discrepancy for operators; it does not downgrade the reservation.

## Crash semantics

### Covered and required

- Crash/failure after `EFFECT_CONFIRMED` is durably recorded but before completion projection: restart projects exactly one final reply.
- Crash/failure while auditing: restart may issue another GET within the bounded GET-only budget; no POST is reachable.
- Replay of the original source event: duplicate/idle; no command, dispatch slot, provider POST, completion, or public outbox duplication.

### Explicit minimal-patch limit

A process death in the physical interval after the Cloudbeds response bytes are accepted but before the local execution outcome commits remains ambiguous. Recovery must not redispatch; the existing expired-fence path resolves to manual review. Eliminating this final interval would require the rejected broader design: a pre-outcome durable accepted-submit receipt journal.

## Completion and payment separation

Once `EFFECT_CONFIRMED` is committed:

- `CompletionProjector` emits one deterministic lodging confirmation release;
- replay inserts zero additional public rows;
- delivery is independent of audit status;
- lodging confirmation authorizes only `reserve_lodging`;
- payment remains a later, separate workflow;
- no Stripe, Pix, Wise, e-mail, handoff, or cancellation effect is introduced.

## Bókun isolation

The Bókun transport, submit classification, booking reference fingerprint, package allocation, and activity payment policy are unchanged. Focused Bókun write tests and the full suite are mandatory gates.

## TDD and qualification

RED tests must cover:

1. accepted 2xx submit returns confirmed before any GET;
2. `success:false`, missing ID, conflicting IDs, non-2xx with ID, invalid JSON, and timeout all fail closed without POST retry;
3. raw Cloudbeds reservation ID is durably persisted privately;
4. completion occurs once before/independent of GET audit;
5. GET not visible → bounded GET-only retry → matched;
6. crash in GET audit → restart → GET-only retry, no POST;
7. crash after confirmed outcome → completion recovery, no redispatch;
8. exact source replay is duplicate/idle with unchanged row counts;
9. lodging-only payment separation;
10. Bókun behavior unchanged.

Qualification order:

1. focused fake-provider tests;
2. fake E2E;
3. full canonical suite with only pre-existing documented deselections;
4. Ruff, compile, diff check, boundary/static gates;
5. exact SHA/tree commit and push;
6. independent read-only review on that exact SHA;
7. GitHub CI (`test`, `image`, `gate`);
8. immutable OCI digest and revision-label verification;
9. stop before any real Cloudbeds POST.

## Real-provider prohibition

This candidate may use only local/fake providers. The historical reservation `2547077136052` must not be queried, modified, or recreated by this work. A next-canary plan may be produced, but any new real `postReservation` requires fresh explicit user authorization.
