# Task 9 runtime checkpoint — durable inbox and relay workers

Date: 2026-07-23
Status: COMPLETE for Runtime Task 4; Task 9 remains `NEXT`
Functional commit: `c1967075eb012adf97715f7698617abc2daa1226`

## Delivered

- `SQLiteInbox` now returns a private lease token and can complete a batch only while that exact token is live.
- Inbox completion stores the authenticated turn receipt hash; a crash after `commit_turn_v8` but before inbox completion reclaims the same batch and observes the same durable receipt.
- `InboxTurnWorker` completes inbox rows only after a committed or replayed V2 turn.
- Reservation commands are serialized as canonical `ReservationRelayBundle` values accepted by the real Phase 5 target store.
- Handoff jobs are serialized as canonical `HandoffRelayBundle` values accepted by the real Phase 6 target store.
- `SQLiteBoundaryWorkerStore` exposes lease/fence/ack surfaces for the existing v8 command, internal and public outboxes without modifying `commit_turn_v8` or adding another state owner.
- `BoundaryRelayWorker` drains reservation and handoff relays through idempotent target operation IDs and target receipts.
- `BoundaryPublicDeliveryWorker` fences the v8 allocation before the provider call, persists a canonical delivery receipt on success, and moves uncertain post-call outcomes to `manual_review` without redispatch.
- Expired `dispatch_fenced` public leases are recovered to `manual_review`; they never return to `pending`.
- `WorkerCycle` rejects `NoopWorker` and `FallbackWorker` entries and exposes its closed seven-queue mapping for composition validation.

## Ownership and transaction boundaries

- Model/profile/reads remain outside SQLite transactions.
- The pure reducer and single `commit_turn_v8` publication path are unchanged.
- Provider calls remain outside the boundary-store transaction and occur only after a durable public dispatch fence.
- Phase 5 and Phase 6 stores remain the target owners; relay retries reuse the same deterministic operation ID and target receipt.
- Queue operations are implemented in `reservation_boundary/worker_store.py`, separate from the atomic commit surface.
- The v8 DDL and schema fingerprint were not changed.
- The runtime currently emits no learning proposals (`learning_proposals=()`); no fake learning target or noop worker was invented.

## Failure proofs

1. **Crash after turn commit, before inbox completion**
   - first worker call commits one receipt and then crashes during inbox acknowledgement;
   - expired lease is reclaimed;
   - executor reports replay and inbox completes with the original receipt hash;
   - exactly one durable turn receipt exists.
2. **Crash after target reservation acceptance, before source ack**
   - target accepts the canonical bundle once;
   - source relay lease expires and receives a higher fencing token;
   - target replay returns the same receipt and stores one command;
   - stale source completion loses CAS.
3. **ManyChat result unknown after call**
   - allocation and public row are fenced before `send`;
   - unknown result moves row and authority to `manual_review`;
   - subsequent worker cycles do not call the provider again.
4. **Hard process death after public provider call**
   - no receipt or exception handler runs;
   - expired `dispatch_fenced` lease is recovered to `manual_review`;
   - provider call count remains one.
5. **Handoff relay**
   - active handoff persists atomically with one canonical internal job;
   - the real Phase 6 target accepts it and returns one authenticated receipt.

## Verification

Focused integrated worker gate:

```text
23 passed, 1 warning in 1.89s
```

Expanded V2 + Phase 8 gate:

```text
260 passed, 1 deselected, 1 warning, 486 subtests passed in 11.86s
```

The one deselected test is the historical rollout-index assertion that intentionally expects the runtime checkpoint to remain closed while Task 9 is still `NEXT`.

Static gates:

```text
All checks passed!
fasttrack-boundaries: OK
py_compile: PASS
git diff --check: PASS
secret scan: no private-key/token matches
```

## Still blocked

- Runtime Task 5: authenticated financial webhooks, receipt correlation, complete API/worker composition and role-specific readiness.
- Runtime Task 6: signed-webhook E2Es, hardened image rebuild and final Task 9 gate.
- Real provider writes, public ManyChat traffic, deploy, restart and rollout remain blocked.
