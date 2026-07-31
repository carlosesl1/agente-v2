# Cloudbeds V2 Strict Submit and Read-back Implementation Plan

> **Execution:** controller-owned critical path; TDD RED→GREEN; no provider write until immutable artifact qualification.

**Goal:** Safely confirm exactly one single-room Cloudbeds reservation only after fresh rate revalidation and strict read-back, preserving room, rate, dates, aggregate adults/children and exact BRL economics with durable replay protection.

**Parent:** `042a3faceb0fb9fbe4db797aa43c9f6192868d00`

**Design:** `docs/superpowers/specs/2026-07-31-cloudbeds-v2-strict-submit-readback-design.md`

## Global constraints

- Exactly one Cloudbeds POST per authorized command; zero submit retries.
- Multi-room and individualized guest manifests are rejected before HTTP in this release.
- Bókun behavior and the legacy Bókun path must remain unchanged.
- All offline tests use `httpx.MockTransport` and local SQLite stores.
- No payment, cancellation, e-mail or handoff.
- Never log raw provider payloads, credentials or PII.

### Task 1: RED — characterize party variants and strict submit evidence

**Files:**
- Modify: `tests/test_v2_cloudbeds_write_transport.py`

- [ ] Add helpers for a complete live-shaped availability envelope and complete v1.3 reservation read-back.
- [ ] Parameterize 1+0, 2+0 and 2+1; assert exact GET query, one-room POST form and GET read-back.
- [ ] Assert selected `room_type_id`, `room_rate_id`, dates, daily rate sum, BRL amount and aggregate party survive to the boundary.
- [ ] Add failing cases for non-2xx+ID, root/nested `success:false`+ID, missing ID and conflicting ID aliases.
- [ ] Assert no read-back after bad submit evidence and exactly one POST.
- [ ] Run selectors and capture RED on parent.

Run with:

```bash
env -i HOME=/home/ubuntu PATH=/usr/bin:/bin \
  HERMES_LEADS_AGENT_CONFIG_PATH=/home/ubuntu/chapada-leads-hermes/config/agent.yaml \
  /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  tests/test_v2_cloudbeds_write_transport.py
```

### Task 2: RED — strict preflight and read-back mismatches

**Files:**
- Modify: `tests/test_v2_cloudbeds_write_transport.py`

- [ ] Reject missing/conflicting selected room/rate, stale total, partial stay, unavailable units and wrong currency before POST.
- [ ] Reject unknown multi-room/room-allocation and individualized guest fields before any HTTP.
- [ ] Parameterize read-back divergence in ID, room type, top/room dates, adults, children, daily-date set, daily values, room total and reservation total.
- [ ] Require `/api/v1.3/getReservation` and `success:true`.
- [ ] Assert all post-submit mismatches perform exactly one POST and become ambiguous/non-retryable.
- [ ] Capture RED.

### Task 3: GREEN — implement closed Cloudbeds evidence validators

**Files:**
- Modify: `v2_adapters/provider_http.py`

- [ ] Replace first-ID recursion with a conflict-detecting reservation-level collector.
- [ ] Add recursive explicit-failure detection for submit/read-back envelopes.
- [ ] Add fresh availability selector keyed by exact room type + room rate.
- [ ] Add strict complete daily-rate normalization and exact Decimal comparison.
- [ ] Add exact single-room read-back validator for ID, dates, occupancy and economics.
- [ ] Make all non-2xx, `success:false`, missing/conflicting IDs and read-back divergence raise the existing ambiguous provider error after at most one POST.
- [ ] Use `/api/v1.3/getReservation`.
- [ ] Keep one-room post form and no retry loop.
- [ ] Run transport file to GREEN.
- [ ] Commit transport + tests.

### Task 4: Prove closed scope and durable replay

**Files:**
- Modify: `tests/test_v2_reservations.py`
- Modify only if needed: `tests/test_v2_completion_projector.py`

- [ ] Add Cloudbeds worker witness with 2 adults + 1 child and exact private binding.
- [ ] Assert one fence slot, one provider execution, `EFFECT_CONFIRMED` and opaque reference fingerprint.
- [ ] Replay and assert `IDLE`, zero second provider call and unchanged slot count.
- [ ] Add/retain completion and public outbox once witness.
- [ ] Prove multi-component/multi-room lodging cannot cross the current provider contract.
- [ ] Run focused durable journey.
- [ ] Commit durable tests.

### Task 5: Regression and qualification

- [ ] Run Cloudbeds transport + reservation worker + outcome/completion projectors + production composition + conversation tests.
- [ ] Run 1+0, 2+0, 2+1, multi-room rejection and replay selectors explicitly.
- [ ] Run causal suite and full official suite once on the final candidate.
- [ ] Run Ruff, fast-track boundaries and `git diff --check`.
- [ ] Compare Bókun V2/legacy relevant AST or test witnesses against parent.
- [ ] Freeze clean SHA/tree.
- [ ] Dispatch independent read-only review for that exact SHA.
- [ ] Push exact SHA and require CI test/image/gate success.
- [ ] Resolve immutable OCI digest and verify revision label.

### Task 6: Dark canary and real one-write conversational canary

- [ ] Create isolated deploy/state from the exact digest.
- [ ] Run readiness and read-only availability probes first.
- [ ] Establish single tester allowlist, one-write budget, deadline, auto-close and rollback.
- [ ] Open only Cloudbeds reservation + public delivery; keep payment/cancellation/e-mail/handoff closed.
- [ ] Run one real conversational flow to a natural final confirmation.
- [ ] Verify pre-submit event count before allowing the confirmed turn.
- [ ] Execute exactly one POST and one strict read-back.
- [ ] Seal evidence: reservation reference fingerprint, provider GET status, one slot, one completion, one public message.
- [ ] Replay same ingress/command and prove no second POST.
- [ ] Roll back route immediately.
- [ ] Report the resulting reservation to the operator; do not cancel it without separate authorization.

### Task 7: Release decision

- [ ] Keep broad customer rollout closed unless every gate above is green.
- [ ] If the real read-back omits any field required by the validator, stop at `CALLED_UNKNOWN`, do not retry, preserve the single created reservation for manual review and report the blocker.
- [ ] Publish a redacted release attestation bound to SHA, tree, OCI digest, review verdict and canary evidence.
