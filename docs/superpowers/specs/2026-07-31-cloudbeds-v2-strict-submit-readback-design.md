# Cloudbeds V2 Strict Submit and Read-back Design

**Date:** 2026-07-31
**Parent candidate:** `042a3faceb0fb9fbe4db797aa43c9f6192868d00`

## Goal

Create at most one Cloudbeds reservation from one authenticated V2 command while preserving one selected room type, one selected rate identity, stay dates, aggregate adult/child composition, and exact BRL economics. Confirm the effect only after a strict authenticated read-back proves the created subject. All uncertain post-submit states are fail-closed and non-retryable.

## Operational starting state

The previous live V2 route is closed: `/v2/readyz` returns `503` and no live relay exists. The audit and offline qualification run with all external writes closed.

## Findings on the parent

1. `_cloudbeds_reference()` returns the first recursive ID rather than requiring one unique reservation-level ID.
2. A response carrying an ID can be confirmed even when HTTP is non-2xx or `success:false`.
3. `getReservation` uses v1.1 in the write transport, while the authenticated live read contract is v1.3.
4. Read-back checks only the reservation ID, not room, dates, occupancy, daily rates, total, or status of the envelope.
5. The selected private `room_rate_id` is retained through the fence but unused by the write transport.
6. The public offer supports one component/one room and aggregate `Party(adults, children)` only. There is no honest multi-room allocation or per-room/per-guest manifest contract.
7. The real provider response observed read-only exposes `roomRateID` in availability, but omits `roomRateID` and `ratePlanID` from `getReservation`; it exposes room type, stay dates, adults, children, daily rates, room total, and reservation total.

## Scope decision

The first release supports exactly one room with:

- one selected `room_type_id`;
- one selected `room_rate_id` retained in the private binding;
- one primary guest;
- aggregate adults >= 1 and children >= 0;
- one exact date interval and BRL total.

Multi-room requests and individualized guest manifests fail before HTTP. Supporting either later requires a new domain command with ordered room allocations, occupancy per room, guest-to-room assignment, economics per room, and a new signature/fingerprint version. Partial inference is forbidden.

This is the safe default selected after the interactive scope question timed out.

## Considered approaches

### A. Validate only the submit envelope

Rejected. It cannot prove the selected rate remained current or that the created room/date/occupancy/economics match.

### B. Fresh rate revalidation, one submit, strict read-back — selected

Perform an authenticated GET-only availability revalidation immediately before the write, then exactly one POST, then exactly one GET by reservation ID. This preserves the selected rate identity before the write and proves the created economics afterward.

### C. Add multi-room and guest manifests now

Rejected for this release. Current V2 offers, confirmation signatures, private binding, command allocation and completion text represent one lodging component, not room allocations. Adding fields only at the provider edge would be unsafe and unsigned.

## Closed input contract

The transport accepts only the existing exact `v2-reservation-dispatch-v1` object. It rejects unknown fields, including `rooms`, `room_allocations`, `guests`, or `passengers`.

The private binding must contain exactly:

- `room_type_id`;
- `room_rate_id`.

The offer must contain exactly:

- one offer/binding identity;
- start/end dates, no start time;
- aggregate adults/children;
- canonical two-decimal amount;
- `BRL` currency.

## Provider sequence

For one fenced command:

1. Validate the complete closed dispatch before network access.
2. `GET /api/v1.3/getAvailableRoomTypes` with the exact property, dates, adults, children, and `detailedRates=true`.
3. Select exactly one option matching both private `room_type_id` and `room_rate_id`.
4. Require complete daily dates, positive availability, BRL currency, and daily-rate sum equal to the signed offer total.
5. Issue exactly one `POST /api/v1.1/postReservation` with one room and the exact aggregate adult/child counts. Never retry this POST.
6. Accept submit evidence only when HTTP is 2xx, no recursive `success:false` exists, and exactly one unique reservation-level ID is present across allowed aliases.
7. `GET /api/v1.3/getReservation` for that ID.
8. Require `success:true`, a matching top-level reservation ID, exactly one assigned-or-unassigned room, matching room type, top-level and room dates, adults, children, complete daily dates, room total and reservation total. The daily schedule and both totals must equal the signed BRL amount.
9. Only then return canonical `confirmed` and let the existing provider port persist an opaque reservation-reference fingerprint.

The Cloudbeds read-back does not repeat `room_rate_id`. Rate identity is therefore proven by the fresh pre-submit availability response; the post-submit proof is its exact economic projection (daily rates and totals) plus room/dates/occupancy.

## Evidence and error classification

### Confirmed

All preflight, submit and read-back conditions above pass.

### Not called / deterministic local rejection

Closed-schema validation, invalid dates/occupancy/economics, unsupported multi-room/manifest fields, stale/missing/conflicting rate availability, or preflight read failure happens before the POST. No dispatch slot is used by a provider write.

### Called unknown / manual review

After the POST is attempted, any of these is terminal and non-retryable:

- transport timeout/interruption;
- any HTTP non-2xx;
- malformed response;
- `success:false` anywhere in the submit envelope;
- absent or conflicting reservation IDs;
- read-back transport/envelope/schema failure;
- mismatch in ID, room, dates, occupancy, daily rates, room total, or reservation total.

A GET may reconcile or audit, but no result authorizes another POST.

## Idempotency and replay

- The durable execution ledger grants one dispatch slot per command.
- The provider receives the existing opaque idempotency key.
- The transport itself has no retry loop.
- Replay of a recorded confirmed or unknown outcome is `IDLE`/not called and consumes no second slot.
- Completion and public outbox remain idempotent projections.

## Required offline tests

1. One adult, zero children: exact preflight, POST form and read-back.
2. Two adults, zero children.
3. Two adults plus one child.
4. Multi-room/room-allocation and individualized-guest fields rejected before HTTP.
5. Fresh availability missing/conflicting room/rate, stale economics, partial dates, and wrong currency rejected before POST.
6. HTTP non-2xx with or without ID is unknown; no GET and no retry.
7. HTTP 2xx plus root/nested `success:false` is unknown; no GET and no retry.
8. Missing/conflicting reservation IDs are unknown; no GET and no retry.
9. Read-back mismatch for every protected field is unknown after exactly one POST.
10. Worker uses one slot and one provider call; replay is idle; outcome stores only opaque fingerprint.
11. Completion/outbox is projected once.
12. Bókun V2 and legacy paths remain behaviorally unchanged.

## Controlled real canary

Only after tests, independent review, CI and immutable image qualification:

- route exactly one allowlisted tester;
- use a new isolated state directory;
- authorize Cloudbeds reservation plus public delivery only;
- close payment, cancellation, e-mail and handoff;
- use a one-turn/one-write budget and a hard deadline;
- choose one available low-impact stay and one room;
- obtain one natural confirmation;
- audit exactly one Cloudbeds POST, one reservation ID, one strict read-back, one ledger slot and one public completion;
- replay the same event/command and prove zero second POST;
- rollback immediately after evidence is sealed.

No cancellation is included unless separately authorized after the resulting reservation is reported to the operator.
