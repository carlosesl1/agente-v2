# Bókun Commercial Query Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Bókun offer confirmed from a localized conversation survive private revalidation in a fresh worker without relaxing any commercial or executable-field comparison.

**Architecture:** Keep exact request identity locale-sensitive in `canonical_hash()`, but define `query_hash()` as locale-independent commercial identity. Reuse the existing private-binding resolver and provider-field hash unchanged.

**Tech Stack:** Python 3.12, dataclasses, pytest, existing V2 provider adapters.

## Global Constraints

- Base commit is `89f50d3b038f1f2f9c61704c43f4a118665e870a` and remains immutable.
- Work only in branch `maya-v2-bokun-commercial-binding` at `/home/ubuntu/agente-v2/.worktrees/maya-v2-bokun-commercial-binding`.
- No deploy, restart, provider POST, booking, Stripe object, ManyChat delivery, or handoff.
- Keep all existing fail-closed checks for commercial and private executable changes.
- Do not promote or modify any historical `EFFECTS_BLOCKED` root.

---

### Task 1: Canonical commercial query identity

**Files:**
- Modify: `v2_contracts/providers.py:258-269`
- Modify: `v2_application/reads.py:_query_from_component`
- Modify: `tests/test_v2_reads.py`

**Interfaces:**
- Consumes: `ReadRequest.to_canonical_bytes() -> bytes`
- Produces: `ReadRequest.query_hash() -> str`, excluding `request_id` for every read and excluding `locale` only for `ReadKind.ACTIVITY`

- [x] **Step 1: Write the failing locale identity test**

Add a test asserting that two otherwise identical activity requests with `locale="pt-BR"` and `locale="en"` have different `canonical_hash()` values but the same `query_hash()`. In the same test, assert that equivalent knowledge reads in different locales still have different `query_hash()` values.

- [x] **Step 2: Write the failing fresh-composition regression**

Create an activity observation from a `BokunReadAdapter` using `locale="pt-BR"`; build the confirmed `OfferSnapshot` with a lookup hash reconstructed without locale; resolve it through a distinct `BokunReadAdapter` and `PrivateOfferBindingResolver`. Assert the exact original binding and provider fields survive.

- [x] **Step 3: Run RED tests**

Run:

```bash
venv/bin/python -m pytest -q \
  tests/test_v2_reads.py::test_activity_commercial_query_hash_excludes_presentation_locale \
  tests/test_v2_reads.py::test_localized_bokun_offer_survives_fresh_worker_composition
```

Expected: both tests fail because `query_hash()` includes locale.

- [x] **Step 4: Implement the minimum change**

In `ReadRequest.query_hash()`, remove `locale` from the canonical value only when `self.kind is ReadKind.ACTIVITY`, while leaving `to_canonical_bytes()`, `canonical_hash()`, and non-activity query hashes unchanged. In `_query_from_component()`, reconstruct the activity query hash from the component and reject product/date/party disagreement with `lookup_id` before provider I/O; preserve the no-children legacy `participants` shape.

- [x] **Step 5: Run GREEN and mutation gates**

Run the two new tests and the existing Bókun changed-terms regression. Expected: PASS, including pre-I/O rejection of incoherent product/date/party plus `PrivateBindingMismatch` after price or executable-ID mutation.

### Task 2: Verification and read-only provider proof

**Files:**
- Modify: `docs/refactor/ACTIVE.md`
- No runtime file changes beyond Task 1.

**Interfaces:**
- Consumes: fixed `query_hash()` and existing adapters.
- Produces: test output and a redacted read-only proof artifact outside Git.

- [x] **Step 1: Run focused provider/reservation tests**

```bash
venv/bin/python -m pytest -q \
  tests/test_v2_reads.py \
  tests/test_v2_bokun_party_reads.py \
  tests/test_v2_provider_http_transports.py \
  tests/test_v2_reservations.py
```

- [x] **Step 2: Run canonical tests and static checks**

Run the repository's clean-environment pytest command, `compileall`, and `git diff --check`.

- [x] **Step 3: Run the real-provider read-only proof**

Use two newly constructed `BokunHTTPTransport`/`BokunReadAdapter` instances with the same commercial request and different request IDs/locales. Compare only redacted public fields and SHA-256 bindings. Instantiate zero reservation/payment workers and perform no provider writes.

- [x] **Step 4: Audit**

Record base/output commits, commands, exit codes, hashes, open risks, and GO/NO-GO in `docs/refactor/ACTIVE.md`. Verify Git status and no generated credentials/provider payloads are tracked.
