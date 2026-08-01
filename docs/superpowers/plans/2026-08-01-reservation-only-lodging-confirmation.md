# Reservation-only lodging confirmation plan

**Date:** 2026-08-01

## Goal

Decouple a lodging reservation confirmation from payment initiation so a Cloudbeds-only controlled window can create exactly one `reserve_lodging` command while every payment capability remains closed.

## Contract

- A draft containing exactly one lodging component has `action_kinds=(reserve_lodging,)`.
- Its PT/EN public summary states that payment is a separate later step and is not part of the current confirmation.
- Confirmation emits exactly one lodging reservation command.
- Activity and package drafts continue to require `initiate_payment` and their existing payment summaries.
- No canary-only bypass, synthetic command, adapter-direct submit, or payment worker is introduced.

## Files

- `v2_application/critical_actions.py`
- `tests/test_v2_critical_actions.py`
- `tests/test_v2_conversation_reducer.py`
- `tests/test_v2_turn_executor.py`

## Verification

- [x] RED: lodging-only policy was denied under the old coupled action scope.
- [x] GREEN: PT/EN summaries and one-command reducer path pass.
- [x] Impacted regression: 119 tests passed.
- [x] Blast-radius regression: 238 tests passed.
- [ ] Canonical full suite and static boundary gate on frozen SHA.
- [ ] Push, exact-SHA CI, immutable OCI, independent read-only review.
- [ ] Fresh-state transactional canary; no historical state reuse.
