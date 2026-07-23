# Task 8 — Package recovery and exceptional handoff evidence

Date: 2026-07-23

## V2-owned additions

- `v2_application/recovery.py` derives package progress from component certainties, payment obligations, settlement identities, and required receipts.
- It persists no parallel `hostel_done` / `agency_done` flags.
- `HandoffCoordinator` opens one deterministic handoff with the mature follow-up store and has no reservation, payment, transport, or provider capability.
- `HandoffEffectGuard` consults that same store and denies commercial admission while the queue is active.
- `V2ReservationWorker` requires a `CommercialEffectGuard`; it checks the workflow before fencing, so a queued command blocked by handoff consumes zero dispatch slots.

## Mature components retained behind V2 policy

- Reservation pre-fence release and post-fence unknown handling remain owned by `reservation_execution.reconciliation.Reconciler` and its execution ledger.
- Settlement pre-fence release and post-fence manual review remain owned by `reservation_followup.reconciliation.PaymentReconciler` and its payment ledger.
- Handoff workflow, replay, outbox, and receipt ownership remain in `reservation_followup`; Task 8 adds only the public `find_active_handoff_by_lead_hash` read.

No second reconciliation ledger, completion store, or handoff workflow was created.

## Safety properties exercised

- confirmed package component is never returned as dispatchable after restart;
- `called_unknown` and `called_no_effect` cannot be redispatched automatically;
- hostel and agency obligations use distinct receiver profiles and claim namespaces;
- repeated discount requests and process restart preserve one active handoff and one public acknowledgement job;
- an active handoff blocks commercial-effect admission;
- provider writes and public ManyChat delivery remain disabled and were not invoked.

## Gate evidence

Focused Task 8 and proportional crash/payment/handoff suite:

```text
123 passed, 67 subtests passed
```

Follow-up store/handoff persistence suite:

```text
60 passed, 54 subtests passed
```

Architecture and static gates:

```text
fasttrack-boundaries: OK
Ruff: All checks passed!
py_compile: exit 0
git diff --check: exit 0
```
