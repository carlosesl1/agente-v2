# ManyChat General Availability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the qualified Maya V2 candidate from a single-subscriber canary to persistent ManyChat operation for all valid subscribers without weakening per-lead isolation, provider idempotency, reservation confirmation, or payment-delivery fencing.

**Architecture:** Keep the existing V2 transaction pipeline and controlled-write gates. Replace canary-only fixed subscriber assumptions with exact subscriber identity carried by each inbound batch and durable claim; use a live authority resolver that derives bounded authority per subscriber/turn instead of a pre-enumerated single-subscriber manifest. Route every authenticated ManyChat payload to V2 only after immutable runtime identity and readiness checks pass.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Docker Compose, GitHub Actions/GHCR, ManyChat API, Cloudbeds, Bókun, Stripe test mode.

## Global Constraints

- V2 only; do not read, modify, or deploy the V3 project.
- Preserve the qualified conversation behavior and provider contracts at commit `09ea56448356c94aecd98c2c65125ded2fcacad1`.
- Provider writes remain confirmation-gated, idempotent, fenced, and persisted.
- Bókun acceptance remains based on the primary submit ID; later GET is diagnostic only.
- Stripe remains test mode; do not open Checkout or charge a payment instrument.
- ManyChat delivery may be enabled, but no synthetic outbound message is sent during deployment verification.
- Roll back routing before restoring the previous runtime if readiness or identity checks fail.

---

### Task 1: Multi-subscriber runtime contract

**Files:**
- Modify: `v2_host/settings.py`
- Modify: `v2_adapters/manychat.py`
- Test: `tests/test_v2_settings.py`
- Test: `tests/test_v2_canary_allowlist.py`

- [ ] Add failing tests for a controlled-write `all` subscriber scope and rejection of malformed subscriber identities.
- [ ] Run focused tests and verify the canary-only contract fails.
- [ ] Implement the minimal exact live subscriber scope while retaining canary tuple support.
- [ ] Run focused tests and verify they pass.

### Task 2: Dynamic per-lead authority and transaction projection

**Files:**
- Modify: `v2_host/public_authority.py`
- Modify: `v2_host/production.py`
- Modify: `v2_application/completion_projector.py`
- Modify: `v2_adapters/stripe.py`
- Modify: `v2_adapters/manychat.py`
- Test: `tests/test_v2_public_authority_manifest.py`
- Test: `tests/test_v2_payment_initiation.py`
- Test: `tests/test_v2_completion_projector.py`
- Test: `tests/test_v2_manychat_flow_delivery.py`
- Test: `tests/test_v2_production_composition.py`

- [ ] Add failing tests proving two subscribers remain isolated through authority, payment metadata, completion, and delivery.
- [ ] Run focused tests and verify each fixed-subscriber assumption fails for the expected reason.
- [ ] Carry the exact subscriber from durable transaction state through each adapter; never infer it from process configuration.
- [ ] Run focused tests and verify all multi-subscriber invariants pass.

### Task 3: Full V2 router and deployment contract

**Files:**
- Modify: `deploy/canary_router.py`
- Modify: `compose.v2.yaml`
- Modify: `.github/workflows/phase8.yml`
- Test: `tests/test_v2_canary_router.py`
- Test: `tests/test_phase8_ops_artifacts.py`

- [ ] Add failing tests for authenticated all-subscriber V2 routing and no fallback after V2 selection.
- [ ] Implement an explicit `all` route mode with immutable image/readiness probes.
- [ ] Render Compose with fake values and run router tests.
- [ ] Enable CI/image publication on the release branch.

### Task 4: Qualification and immutable publication

**Files:**
- Update: `docs/refactor/ACTIVE.md`

- [ ] Run focused transactional, ManyChat, authority, and routing tests.
- [ ] Run the canonical suite with the seven historical contracts deselected.
- [ ] Commit and push the V2-only change.
- [ ] Require successful GitHub Actions and obtain the immutable multi-arch GHCR digest.

### Task 5: Persistent deployment and verification

**Files:**
- Modify: `/home/ubuntu/workspace/agente-v2-canary-deploy/compose.yaml`
- Create: `/home/ubuntu/workspace/agente-v2-canary-deploy/GENERAL_AVAILABILITY_<sha>_<timestamp>.json`

- [ ] Back up the current stopped V2 release configuration and state.
- [ ] Install the immutable image digest and all-subscriber runtime authority.
- [ ] Start API and worker dark; verify image identity, health, worker heartbeat, and provider reads.
- [ ] Start the router and move authenticated ManyChat traffic to V2.
- [ ] Verify public TLS health, unauthorized webhook rejection, authenticated non-mutating validation, queue health, and zero unexpected provider/ManyChat writes.
- [ ] Record rollback instructions and final evidence.
