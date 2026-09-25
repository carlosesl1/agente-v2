# Independent purchase Implementation Plan

> **For agentic workers:** Execute inline with executing-plans; no subagents.

**Goal:** Allow separately confirmed new purchase without discarding old captured, not-dispatched payment.
**Architecture:** Extend existing typed component progression predicate. Reuse summary/confirmation identities and canonical ledger. Handoff guard stays closed until scoped native operator closure.
**Tech Stack:** Python, pytest, SQLite, immutable Docker TEST release.

## Global Constraints
No regex intent routing, no direct active SQLite edits, no old payment replay/refund/transfer, no GA/Ops/V3 change.

### 1. Causal progression tests and minimal correction
Files: `tests/test_v2_independent_purchase.py`, `tests/test_v2_package_renewal.py`, `v2_application/active_execution.py`, `v2_adapters/hermes_model.py` (remove contradictory blanket capture rule, preserving pending/unknown and handoff constraints).
- [ ] RED: old capture+terminal not_dispatched can reach fresh summary and new confirmation; old evidence visible, original commands untouched.
- [ ] Negatives: pending/unknown/settled/source missing/stale/provider reactivation, old confirm and adjust remain blocked.
- [ ] GREEN: in `_terminal_unpaid_component`, preserve existing fresh terminal/zero-paid checks; accept final not_dispatched/retryable separately from prior no-evidence rule. No producer schema changes.
- [ ] Exercise real native receipt→worker predispatch failure→resolver→same guard, and package restart.
Run: `env -i HOME=/tmp PATH=/usr/bin:/bin PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null /home/ubuntu/workspace/v2-stripe-settlement-727d3625/venv/bin/python -m pytest -q tests/test_v2_independent_purchase.py tests/test_v2_component_renewal.py tests/test_v2_component_renewal_guards.py tests/test_v2_package_renewal.py`.

### 2. Qualification and TEST
- [ ] Full suite/current package manifests; commit exact source/test/docs only (preserve preexisting ACTIVE.md).
- [ ] Immutable image build, tests from image and real-model/copied-owner progression.
- [ ] Publish TEST closed, verify authority/state/GA-Ops unchanged.

### 3. Authorized E2E
- [ ] Reconcile providers/receipts/queues, qualify exact handoff closure on copies, preserve every original financial row.
- [ ] Prepare same-state ordinary closure and bounded full-path worker window before one booking/link.
- [ ] One natural summary and confirmation via real channel; fresh provider/Stripe/native CTA read-back.
- [ ] Await human TEST payment with settlement active, then prove genuine webhook, unique Bókun payment, and Maya/WhatsApp. If a prerequisite fails, close effects and report actual boundary.
