# Task 9 runtime checkpoint — authenticated financial API and worker role

Date: 2026-07-23
Status: COMPLETE for Runtime Task 5; Task 9 remains `NEXT`
Functional commit: `d9b3cf09b54e6f4c93f4ae39e7900c65d0eb0e79`

## Delivered

- Closed Stripe, Wise and Pix webhook routes under `/webhook/payments/{provider}`.
- HMAC-SHA256 verification over the exact raw request bytes occurs before JSON/event decoding or durable writes.
- Provider, external event ID, evidence type and configured trust profiles are cross-checked before acceptance.
- Financial events are decoded through the existing `PaymentEvidenceRecorded` wire contract and accepted by `V2PaymentEvidenceGateway`.
- The API opens `SQLiteFollowupUnitOfWork` only for one evidence request; the API container does not retain a followup owner.
- Replay returns the mature global-claim duplicate disposition and does not create a second evidence claim.
- API and worker readiness expose role-specific owner counts and the real-effect gate map.
- Settings are fail-closed: financial secrets/trust profiles are all-or-none and compose marks all required values explicitly.
- `build_worker_cycle` accepts only the worker role and exactly seven callable stages; noop/fallback stages remain forbidden.
- Worker factory loading uses a closed literal allowlist; arbitrary dynamic imports are rejected by design and by the boundary guard.
- Compose now runs API and worker as separate roles from the same image, both read-only, `cap_drop: ALL`, and `no-new-privileges`.

## Security and ownership proof

```text
API owners:
  inbox=1; boundary=execution=followup=payment_initiation=public_outbox=0

Worker owners:
  inbox=boundary=execution=followup=payment_initiation=public_outbox=1
```

The API route sequence is:

```text
raw body → provider HMAC verifier → trust/type/identity checks
→ short-lived followup UOW → V2PaymentEvidenceGateway
```

No provider write is performed by the API and all real-effect gates remained closed in qualification.

## Verification

Focused host gate:

```text
19 passed, 1 warning in 2.42s
```

Expanded V2 + Phase 8 gate:

```text
263 passed, 1 deselected, 1 warning, 486 subtests passed in 12.26s
```

Static and manifest gates:

```text
ruff critical checks: PASS
fasttrack-boundaries: OK
py_compile: PASS
git diff --check: PASS
API/worker image identity and hardening assertions: PASS
```

The deselected test remains the historical rollout-index assertion while Task 9 is still `NEXT`.

## Still blocked

- Runtime Task 6: signed Stripe/Pix/Wise E2Es, qualification worker factory, hardened image rebuild and final Task 9 evidence.
- The worker CLI accepts only `v2_host.qualification_workers:build_worker_set`; that factory is intentionally delivered and exercised in Task 6 rather than defaulting to fake providers.
- Real provider writes, public ManyChat traffic, deploy, restart and rollout remain blocked.
