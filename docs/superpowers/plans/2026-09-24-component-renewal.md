# Component Renewal and Receipt Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use executing-plans inline, without subagents, as preferred by Carlos. Execute each causal test before implementation.

**Goal:** Allow a newly authorized replacement of a terminal unpaid component without duplicating an active sibling or replaying an old command; qualify Cloudbeds receipt evidence.

**Architecture:** Extend the existing active-execution guard with the existing per-component context. The reducer still creates a new draft and requires a new confirmation; existing durable execution/payment owners retain all history. No new state machine or effect queue.

**Tech Stack:** Python, pytest, SQLite, httpx controlled transports, existing V2 runtime.

## Global Constraints

- Approved spec: `docs/superpowers/specs/2026-09-24-component-renewal-design.md`.
- Same declared worktree/branch, base `ab58c2f`; no live state writes or external effects.
- Maya owns semantics. Only typed service/selection/current draft defines renewal scope, never prose.
- Fresh provider status and verified local financial context; no payment transfer or automatic refund.
- Historical incident response unavailable: no invented root cause or E2E claim.
- Baseline: 64 relevant tests pass, one upstream Starlette warning.

## Task 1 — Terminal component renewal through existing guard and reducer

Files: `v2_application/active_execution.py`, `v2_application/conversation.py`, `v2_application/turn_executor.py`, `v2_adapters/hermes_model.py`, new `tests/test_v2_component_renewal.py`.

Interfaces:
```python
# Optional arguments preserve existing callers; absent context never unlocks a commanded workflow.
blocks_active_commercial_progression(state, proposal, *, execution_status=None,
                                    execution_context=None, now=None) -> bool
component_renewal_allowed(state, proposal, *, execution_context, now) -> bool
# Reducer consumes the same authenticated context, not a model-provided bypass flag.
V2ConversationReducer.reduce(..., execution_context=None)
```

- [ ] Add a causal test using the actual turn executor, initial selection+confirmation and fresh terminal status. Assert a new summary with no command; on old code assert the blocked result, not an unrelated harness failure.
- [ ] Add focused cases: activity TIMEOUT plus active lodging; active target; unknown outcome; absent/stale/future status; local capture/manual review; unavailable financial source; no explicit service; old confirmation.
- [ ] Implement the existing guard's narrow exception: the requested service has matching historical executed components, all with confirmed creation, native terminal status, fresh observation, zero paid amount and no capture/settlement conflict. Require a new selection, never old confirmation. Confirming a new draft uses its services and rechecks old components.
- [ ] Reducer permits same-offer renewal only via this factual predicate. New IDs continue to derive from the new source event. Target services must bind the selected offers through existing checks.
- [ ] Thread context through three guard sites and reducer. At finalization refresh factual context for a new summary/command and reject changed status using existing action-rejection continuation. Keep deadlines monotonic.
- [ ] Replace the prompt's unconditional prohibition on replacement with the scoped behavior: explain expiration, offer fresh availability, get new confirmation, preserve other components and never reuse old payment artifacts.
- [ ] Prove summary→new confirmation→one new child command, replay/reopen and historical ledgers unchanged. Verify a provider status change before confirmation/commit blocks effects.

## Task 2 — Cloudbeds receipt evidence through durable outcome

Files: `v2_host/stripe_settlement.py`, `tests/test_v2_payment_incident.py` (or focused new tests).

- [ ] Exercise HTTP 200/success/IDs through real adapter+worker+SQLite and Maya wire projection; count physical requests, reopen, rerun and assert a single POST.
- [ ] Test diagnostics through a normal logging Formatter, not only caplog record extras. Assert missing/invalid IDs leave unknown with response hash and recognizable field types.
- [ ] Only fix demonstrated receipt/diagnostic defects. Preserve documented field semantics, redirect-disabled transport and permanent dispatch fence. Do not promote balance or a partial receipt into success.
- [ ] Cover malformed JSON, null/empty/wrong-type IDs, explicit refusal and remote acceptance followed by timeout. Retain RED evidence separately.

## Task 3 — Regression and closed qualification

Commands (all in declared worktree):
```bash
env -i HOME=/tmp PATH=/usr/bin:/bin PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 HERMES_LEADS_AGENT_CONFIG_PATH=/dev/null /home/ubuntu/workspace/v2-stripe-settlement-727d3625/venv/bin/python -m pytest -q tests/test_v2_component_renewal.py tests/test_v2_payment_incident.py tests/test_v2_stripe_settlement.py tests/test_v2_execution_context.py tests/test_v2_turn_executor.py tests/test_v2_conversation_reducer.py
env -i HOME=/tmp PATH=/usr/bin:/bin /home/ubuntu/workspace/v2-stripe-settlement-727d3625/venv/bin/python scripts/check_fasttrack_boundaries.py
git diff --check
```
- [ ] Run focused tests after each round. Full hermetic suite after generated phase6 manifest is current.
- [ ] Review changed source inline; persist test outputs/commit identity and distinguish controlled HTTP from real providers.
- [ ] If candidate passes, exercise exact candidate source in network-disabled image over synthetic databases. Model-only qualification may use isolated credentials but no external business/channel tools.
- [ ] Reverify authority and compare manifest/container identities to baseline. Do not deploy or reopen.
- [ ] Update ACTIVE.md and write result with GO for local candidate or explicit blockers. Real WhatsApp/provider E2E and production remain pending separate authorization.
