# Conversation-First Reservation Profile Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make valid persisted conversational name, email, and country override divergent ManyChat values while preserving a fresh ManyChat-only phone and source-aware confirmation binding.

**Architecture:** `v2_application.conversation` owns field-level source selection and an opaque hash of the effective selected profile material. `v2_application.turn_executor` reuses that hash before sensitive reduction and immediately before command commit so unused ManyChat metadata cannot revoke a valid confirmation, while binding/phone/selected-field changes still fail closed.

**Tech Stack:** Python 3.12, immutable dataclasses, SQLite, pytest, Ruff, existing V2 boundary/reducer/executor contracts.

## Global Constraints

- Persisted canonical conversation values win for `full_name`, `email`, and `country_code`.
- Valid fresh ManyChat values supply only approved fields absent from the private conversational snapshot.
- `phone_e164` remains exclusively ManyChat-authenticated and fresh.
- Current-turn collection remains persist-first and command-free.
- Public projection, model wire, artifacts, errors, logs, and `repr` contain no private values.
- Binding identity, authenticated phone, selected ManyChat values, selected conversational values, private snapshot, and effective customer remain material.
- Unused ManyChat name/email/country values and raw `content_hash`/`complete` metadata are not independently material.
- No real provider, ManyChat, payment, relay, deploy, or rollout effect.

---

### Task 1: Conversation-first resolver and effective material identity

**Files:**
- Modify: `tests/test_v2_effective_customer_profile.py`
- Modify: `v2_application/conversation.py`

**Interfaces:**
- Consumes: `PrivateCustomerBinding`, `PrivateCustomerFactSnapshot`, `ConversationProjection`.
- Produces: `resolve_effective_customer(...) -> EffectiveCustomerResolution` with conversation-first fields.
- Produces: `effective_customer_material_hash(profile, projection, now, *, private_facts) -> str | None`.

- [ ] **Step 1: Replace the conflict witness with a failing precedence witness**

Change the divergent valid-values test to require `ready is True`, `conflicting_fields == ()`, and exact conversational name/email/country with ManyChat phone.

- [ ] **Step 2: Add a failing source-aware identity witness**

Resolve the same private snapshot against two fresh bindings whose binding ID and phone are equal but whose unused name/email/country/content hash differ. Require equal `customer_ref` and equal `effective_customer_material_hash`. Then change phone and require the material hash to differ.

- [ ] **Step 3: Run RED selectors**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_v2_effective_customer_profile.py::test_valid_conversation_values_override_divergent_manychat_values \
  tests/test_v2_effective_customer_profile.py::test_unused_manychat_identity_fields_do_not_change_effective_material
```

Expected: causal assertion failures because the resolver still reports conflicts and binds raw ManyChat content.

- [ ] **Step 4: Implement minimal field-level source selection**

For each approved field, canonicalize the private value first. If present and valid, select it. Otherwise select the valid fresh ManyChat value. Invalid ManyChat is missing. Invalid private material remains an error/conflict boundary.

- [ ] **Step 5: Implement the opaque effective material hash**

Hash a canonical payload containing binding ID, authenticated phone, selected effective name/email/country, per-field source labels, and private snapshot hash. Never return or persist the payload. Use the digest for split-origin `customer_ref` and export the helper for executor reauthentication.

- [ ] **Step 6: Run GREEN and resolver regressions**

Run the two named selectors and all of `tests/test_v2_effective_customer_profile.py`. Expected: all pass.

- [ ] **Step 7: Static check and commit**

Run Ruff and `git diff --check`. Commit only the resolver/test files with `fix: prefer explicit conversational identity`.

---

### Task 2: Source-aware decision and pre-commit reauthentication

**Files:**
- Modify: `tests/test_v2_split_origin_cloudbeds_e2e.py`
- Modify: `v2_application/turn_executor.py`

**Interfaces:**
- Consumes: `effective_customer_material_hash(...)` from Task 1.
- Preserves: `_PreparedTurn.private_profile_material_hash: str | None` as an opaque digest only.

- [ ] **Step 1: Add the unused-ManyChat-mutation RED witness**

Build a summary from a private conversational name/email/country and ManyChat phone. During confirmation authority resolution, mutate only ManyChat name/email/country/content hash while keeping binding ID and phone. Require one command and one relay instead of `TurnExecutionError`.

- [ ] **Step 2: Preserve the authenticated-phone mutation witness**

Keep the existing pre-commit mutation test requiring `TurnExecutionError("private profile changed before commit")` and zero command/relay when phone changes.

- [ ] **Step 3: Add selected-ManyChat-field mutation coverage**

When no conversational email is stored and the summary selects ManyChat email, mutate that email before command commit. Require generic abort and zero command/relay.

- [ ] **Step 4: Run RED selectors**

Run only the three mutation selectors. Expected: unused-field mutation still aborts under the raw-profile material hash.

- [ ] **Step 5: Replace raw-profile comparisons**

Before a sensitive decision, recompute `effective_customer_material_hash` with the same projection/private snapshot and compare it to the initial digest. Immediately before `commit_turn_v8`, reload projection, private snapshot, and profile; require freshness and the same effective material digest. Do not place values in `_PreparedTurn`, errors, receipts, or artifacts.

- [ ] **Step 6: Run GREEN and E2E regressions**

Run the three mutation selectors and `tests/test_v2_split_origin_cloudbeds_e2e.py`. Require exact one-command/relay behavior on the allowed mutation and zero on material mutations.

- [ ] **Step 7: Static check and commit**

Run Ruff and `git diff --check`. Commit only executor/E2E files with `fix: bind confirmation to selected customer material`.

---

### Task 3: Qualification, ledger, and final review

**Files:**
- Modify: `docs/refactor/ACTIVE.md`

- [ ] **Step 1: Run proportional regression**

Run effective-profile, collection, reducer, executor, split-origin Cloudbeds, private-store, Cloudbeds one-shot/audit, Bókun, and payment-separation tests with no network.

- [ ] **Step 2: Run official clean regression**

Use the repository's seven documented historical deselections and explicit config path. Require zero unexpected failures.

- [ ] **Step 3: Run static gates**

Run Ruff, `scripts/check_fasttrack_boundaries.py`, compileall, and `git diff --check`.

- [ ] **Step 4: Update the active ledger**

Record the authority override, RED/GREEN witnesses, exact counts, functional commits, zero real effects, and the next independent-review gate.

- [ ] **Step 5: Commit the ledger and authenticate candidate**

Commit only `docs/refactor/ACTIVE.md`; record exact HEAD/tree and clean status.

- [ ] **Step 6: Obtain independent read-only review**

Review only the authority-override delta on the exact SHA. Any reproducible Critical/Important finding creates a new RED and invalidates the candidate.

- [ ] **Step 7: Publish only after CLEAR**

Push the exact reviewed SHA and require remote CI jobs `test`, `image`, and `gate` to succeed on that SHA. Do not deploy or open effects.
