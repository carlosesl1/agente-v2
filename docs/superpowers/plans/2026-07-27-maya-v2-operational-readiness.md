# Maya V2 Operational Readiness Implementation Plan

> Execute controller-direct, sandbox-first, with all real effects closed until capability-specific canaries.

**Goal:** deliver an immutable Maya V2 candidate that can converse, retrieve commercial knowledge, query providers, and safely stage all commercial functions under independent gates.

**Architecture:** keep the deterministic V2 parent as authority and effect owner. Expand the model-visible read contract and reviewed knowledge artifact; move identity gating to write boundaries; use post-call read timestamps; create a deterministic no-effect protocol fallback; make readiness/relay decisions use effective authority.

**Tech stack:** Python 3.12, pytest, Ruff, Docker, SQLite V8 boundary store, Hermes CLI profile `leads`, ManyChat, Cloudbeds, Bókun, Stripe test transport.

---

## Task 1: RED tests for incomplete profiles

**Files:** `tests/test_v2_conversation_reducer.py`, `tests/test_v2_customer_collection.py`

- Add cases proving greeting/inform/read requests survive an incomplete profile.
- Add cases proving reservation/payment commands remain blocked.
- Run focused tests and preserve RED output.

## Task 2: Move profile enforcement to identity-dependent effects

**File:** `v2_application/conversation.py`

- Classify identity-dependent proposals structurally from commands/effects, not keywords.
- Permit no-command informational/read proposals before profile completion.
- Preserve existing handoff and subject-binding guards.
- Run focused tests GREEN.

## Task 3: RED tests and repair provider observation clock

**Files:** `tests/test_v2_reads.py`, `tests/test_v2_turn_executor.py`, `v2_application/turn_executor.py`

- Reproduce provider `observed_at` occurring after pre-call reference.
- Capture post-call reference per read and bind against it.
- Retain RED tests for genuinely future and stale observations.

## Task 4: RED tests and deterministic response fallback

**Files:** `tests/test_v2_hermes_model.py`, `tests/test_v2_inbox_worker.py`, `v2_adapters/hermes_model.py`, `v2_application/inbox_worker.py`

- Cover empty/malformed `reply_chunks` on initial and repair responses.
- Make bounded protocol repair explicit.
- Persist a localized deterministic no-effect fallback if repair fails.
- Prove no provider read, command, handoff promise, or write can originate from fallback.

## Task 5: Build reviewed commercial knowledge bundle

**Files:** `config/v2_commercial_knowledge.json`, `config/v2_tour_catalog.json`, `v2_adapters/knowledge.py`, `v2_host/settings.py`, `v2_host/production.py`, prompt and tests.

- Import reviewed V1 FAQ and commercial guidance.
- Add hostel facts, payment policy, and all supported tours.
- Define canonical V2 product IDs and deployment-only provider mapping.
- Teach live prompt to request knowledge and provider reads.
- Test that provider IDs and internal terms never enter public replies.

## Task 6: Compose and attest every effect path

**Files:** production composition, settings, worker tests, adapter tests.

- Verify Cloudbeds reservation, Bókun booking, Stripe test links, Pix/Wise instructions/evidence, handoff, public delivery, settlement, completion, and reconciliation workers.
- Add missing production wiring or explicit `closed/not_ready` readiness for unsupported paths.
- Never label a capability ready when only a domain stub exists.

## Task 7: Effective readiness and relay expiry

**Files:** `v2_host/composition.py`, `v2_host/app.py`, tests, deploy relay/status/rollback/auto-close scripts.

- Include signed authority validity, allocation, heartbeat, and effective gate in readiness.
- Add a route-eligibility endpoint/field.
- Make relay preflight and deadline fallback to legacy before POST.
- Make cleanup idempotent and remove dependency on Hermes cron supplementary Docker groups for correctness.

## Task 8: Integrated verification

- Run focused tests after each GREEN.
- Run Ruff, boundary checks, compose checks, and full clean-env pytest once on the integrated candidate.
- Review the integrated diff once.
- Repeat ten live-prompt scenarios in an isolated read-only container with exact prompt, SOUL, model, profile, adapters, and providers.

## Task 9: Immutable publication and closed deployment

- Commit and push candidate branch.
- Require CI green for exact SHA.
- build/publish immutable GHCR digest.
- Deploy API/worker with relay disabled, allowlist limited to `1873018537`, all real effects closed.
- Verify readiness and rollback.

## Task 10: Limited capability canaries

- Open public delivery only and verify one read-back ManyChat conversation.
- Then test handoff, Cloudbeds write, Bókun write, Stripe test link, and payment evidence one at a time with explicit windows.
- Close each gate after its test and audit all durable stores before advancing.
