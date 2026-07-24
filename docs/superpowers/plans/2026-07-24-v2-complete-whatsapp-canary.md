# Complete V2 WhatsApp Canary Implementation Plan

> **Execution mode:** parent-controlled, inline TDD in the existing isolated worktree. Do not use autonomous coding subagents. Follow `superpowers:test-driven-development` and `superpowers:verification-before-completion` for every slice.

**Goal:** Compose and deploy an allowlisted WhatsApp canary for subscriber `1873018537` that can converse with `openai-codex/gpt-5.6-luna`, create real Cloudbeds/Bókun reservations only after natural confirmation, create Stripe **test-mode** payment links, deliver through ManyChat durable outboxes, and fail uncertain writes to reconciliation/manual review without blind retry.

**Architecture:** Keep the model tool-free. The model emits a closed proposal; parent-owned reducers validate it and persist domain commands atomically. Separate fenced workers own Cloudbeds, Bókun, Stripe-test, ManyChat and handoff effects. A durable obligation/projector layer links confirmed reservation outcomes to payment-link and delivery jobs without allowing one provider worker to call another provider. All ingress and egress enforce the single allowlisted subscriber, the immutable candidate identity, the time-bounded gate, and the global kill switch.

**Tech stack:** Python 3.12, Starlette/Uvicorn, SQLite ledgers/outboxes, Docker Compose, stdlib HTTP adapters, Hermes Agent private sessions, ManyChat API, Cloudbeds API, Bókun API, Stripe API test environment, pytest/unittest.

**Approved design:** `docs/superpowers/specs/2026-07-24-v2-complete-whatsapp-canary-design.md`

**Baseline candidate:** `566558c` (design commit on top of qualified `47965c5`)

---

## Non-negotiable invariants

1. `openai-codex/gpt-5.6-luna` only; no silent fallback and `tools=0`.
2. Ingress reject occurs before durable persistence, profile fetch, model call, provider read or write.
3. Public target is exact ManyChat subscriber `1873018537`; phone is corroboration only.
4. One natural confirmation may authorize a package, but each item receives its own command, request hash, permit and provider fence.
5. A mutation exception/timeout/malformed response after dispatch becomes `CALLED_UNKNOWN` or equivalent and requires manual review; never automatic retry.
6. Cloudbeds/Bókun writes, Stripe-test link creation, ManyChat delivery and handoff have independent gates plus one global kill switch and an expiring window.
7. Stripe keys must be syntactically test/restricted-test keys; live keys are rejected before network I/O.
8. Reservation outcome must be proven `EFFECT_CONFIRMED` before payment planning. Unknown or unconfirmed outcomes block all dependent jobs.
9. Creation of a link and delivery of that link are separate durable obligations. Delivery retries cannot recreate the link.
10. All live candidate evidence is sanitized and private. No API key, raw payment URL, guest PII or provider raw response appears in logs/reports.
11. No real provider mutation or outbound WhatsApp message occurs during implementation/qualification. Real gates remain closed until Carlos explicitly opens the test window.

---

## Task 1: Freeze baseline and encode the approved runtime contract

**Files:**
- Modify: `v2_host/settings.py`
- Modify: `tests/test_v2_settings.py`
- Modify: `tests/test_v2_production_composition.py`
- Test: `tests/test_v2_complete_whatsapp_contract.py` (new)

**Step 1 — RED:** Add tests asserting a new runtime mode `controlled_write`, exact subscriber allowlist, expiring write window, global kill switch, independent provider gates, Stripe environment=`test`, and Luna exact identity. Assert missing/ambiguous values fail closed.

**Step 2 — Verify RED:**
```bash
pytest -q tests/test_v2_settings.py tests/test_v2_production_composition.py tests/test_v2_complete_whatsapp_contract.py
```
Expected: failures for absent settings/contracts.

**Step 3 — GREEN:** Add immutable parsed settings for:
- `V2_RUNTIME_MODE=controlled_write`;
- `V2_ALLOWED_SUBSCRIBER_IDS=1873018537`;
- `V2_GLOBAL_KILL_SWITCH`;
- `V2_WRITE_WINDOW_END` (UTC, future, bounded);
- `V2_ENABLE_CLOUDBEDS_WRITES`;
- `V2_ENABLE_BOKUN_WRITES`;
- `V2_ENABLE_STRIPE_LINKS`;
- `V2_ENABLE_MANYCHAT_DELIVERY`;
- `V2_ENABLE_MANYCHAT_HANDOFF`;
- `V2_STRIPE_ENVIRONMENT=test`;
- per-provider base URLs and secret-presence fields;
- ManyChat reply/payment field IDs and flow namespaces.

Reject live Stripe key prefixes, expired windows, multiple subscribers, write gates without global approval, and controlled mode without immutable candidate metadata.

**Step 4 — Verify GREEN and commit.**

---

## Task 2: Enforce the canary allowlist before persistence

**Files:**
- Modify: `v2_host/app.py`
- Modify: `v2_adapters/manychat.py`
- Modify: `v2_host/production.py`
- Modify: `tests/test_v2_manychat_ingress.py`
- Test: `tests/test_v2_canary_allowlist.py` (new)

**Step 1 — RED:** Prove that wrong/missing/mismatched subscriber IDs return 403 before inbox persistence and that matching phone alone cannot authorize. Prove rejected requests do not touch profile/model/provider/store.

**Step 2 — Verify RED.**

**Step 3 — GREEN:** Introduce an exact `SubscriberAllowlist` checked immediately after signature/schema parsing and before `DurableInboxIngress`. Keep `PublicAuthorityResolver` as a second egress guard.

**Step 4 — Verify restart/dedupe and commit.**

---

## Task 3: Extend the closed Luna proposal for package writes and payment choice

**Files:**
- Modify: `v2_application/conversation.py`
- Modify: `v2_application/turn_executor.py`
- Modify: `v2_adapters/hermes_model.py`
- Modify: `v2_contracts/models.py`
- Modify: `tests/test_v2_conversation.py`
- Modify: `tests/test_v2_turn_executor.py`
- Test: `tests/test_v2_package_confirmation.py` (new)

**Step 1 — RED:** Cover lodging-only, tour-only and combined package proposals. Require complete scalar facts, explicit final summary, `confirmation_by_lead=true`, payment method/percentage and one typed command per item. Reject internal product IDs in public text, model-supplied provider IDs, partial package preflight and fabricated amounts.

**Step 2 — Verify RED.**

**Step 3 — GREEN:** Extend the exact proposal schema with a bounded tuple of reservation commands and closed payment preference. The parent injects canonical IDs from prior provider observations/catalog and derives deterministic command IDs/idempotency keys. The Luna still receives zero tools.

**Step 4 — Verify both routes, handoff biconditional and commit.**

---

## Task 4: Make the boundary-to-reservation relay an explicit worker queue

**Files:**
- Modify: `v2_host/composition.py`
- Modify: `v2_host/worker_main.py`
- Modify: `v2_host/production.py`
- Modify: `v2_application/relay_worker.py`
- Modify: `tests/test_v2_worker_main.py`
- Modify: `tests/test_v2_inbox_relay_workers.py`

**Step 1 — RED:** Assert worker catalog includes a distinct relay owner, and that reservation workers never inspect boundary tables directly. Assert duplicate restart cycles produce one reservation command.

**Step 2 — GREEN:** Add `boundary_relay` queue with lease/heartbeat/readiness. Compose `ReservationCommandRelayWorker` separately from provider workers.

**Step 3 — Verify crash/restart/fencing and commit.**

---

## Task 5: Implement the Cloudbeds reservation write transport

**Files:**
- Modify: `v2_adapters/provider_http.py`
- Modify: `v2_adapters/cloudbeds.py`
- Modify: `v2_host/production.py`
- Test: `tests/test_v2_cloudbeds_write_transport.py` (new)
- Modify: `tests/test_v2_reservations.py`

**Step 1 — RED:** With a fake HTTP server/transport, assert exact `POST /api/v1.1/postReservation` form, bearer auth presence (not value), request/permit binding, normalized success, explicit `success:false`, 4xx not-called/no-effect classification, timeout/5xx/malformed response after dispatch -> `CALLED_UNKNOWN`, and no adapter-level retry.

**Step 2 — GREEN:** Implement parent-owned `CloudbedsReservationHttpTransport` and normalization. Keep stable idempotency in the V2 ledger even though Cloudbeds has no native idempotency header. Add read-back reconciliation by provider reference when success is uncertain.

**Step 3 — Verify no network with gate closed and commit.**

---

## Task 6: Implement the fenced Bókun cart + checkout write transport

**Files:**
- Modify: `v2_adapters/provider_http.py`
- Modify: `v2_adapters/bokun.py`
- Modify: `v2_host/production.py`
- Test: `tests/test_v2_bokun_write_transport.py` (new)
- Modify: `tests/test_v2_reservations.py`

**Step 1 — RED:** Assert canonical `product:buracao` binds to configured Bókun product and price category IDs, availability facts bind to the command, and cart + checkout operate under one fenced command. Prove cart uncertainty prevents checkout and enters manual review; checkout uncertainty after cart also enters manual review; no blind retry.

**Step 2 — GREEN:** Implement `BokunReservationHttpTransport` using the exact cart/session/checkout contract derived from the existing API reference, with deterministic sub-operation idempotency keys and sanitized booking DTOs.

**Step 3 — Verify restricted/unknown products fail before HTTP and commit.**

---

## Task 7: Add Stripe test-mode payment-link transport

**Files:**
- Modify: `v2_adapters/stripe.py`
- Modify: `v2_host/settings.py`
- Modify: `v2_host/production.py`
- Test: `tests/test_v2_stripe_test_transport.py` (new)
- Modify: `tests/test_v2_payment_initiation.py`

**Step 1 — RED:** Assert Product → Price → Payment Link requests use deterministic Stripe `Idempotency-Key` suffixes, exact test key family, confirmed amount/currency/percentage, provider reservation metadata and allowlisted subscriber fingerprint. Assert live keys and live URLs fail before HTTP. Assert partial/unknown creation goes to manual review and does not recreate earlier objects.

**Step 2 — GREEN:** Implement `StripeTestLinkPort` with closed form payloads and private URL handling. Persist only URL ciphertext/private storage plus a public fingerprint; never expose URL to model/logs.

**Step 3 — Verify via fake Stripe API and commit.**

---

## Task 8: Project confirmed reservation outcomes into payment obligations

**Files:**
- Add: `v2_application/obligations.py`
- Add: `v2_application/sqlite_obligations.py`
- Modify: `v2_application/payments.py`
- Modify: `reservation_execution/sqlite_store.py` only if a read-only outcome projection method is required
- Test: `tests/test_v2_obligation_projector.py` (new)

**Step 1 — RED:** Assert only `EFFECT_CONFIRMED` outcomes produce payment selections; `CALLED_UNKNOWN`, no-effect, failed package member and missing amount block dependent jobs. Assert the projection is idempotent across crash/restart and preserves command/outcome/payment correlations.

**Step 2 — GREEN:** Add a durable `ReservationOutcomeProjector` with its own cursor/receipt ledger. It creates one `PaymentSelection` per confirmed provider item. For packages, each item remains independently auditable; downstream public completion waits for all package obligations or a terminal/manual-review state.

**Step 3 — Verify and commit.**

---

## Task 9: Add typed ManyChat delivery actions and durable provider-result outboxes

**Files:**
- Modify: `v2_contracts/channel.py`
- Modify: `v2_adapters/manychat.py`
- Modify: `v2_application/public_delivery.py`
- Add: `v2_application/provider_delivery.py`
- Add: `v2_application/sqlite_provider_delivery.py`
- Test: `tests/test_v2_manychat_actions.py` (new)
- Modify: `tests/test_v2_public_delivery.py`

**Step 1 — RED:** Cover actions:
- reply text via reply custom field + reply flow;
- payment link via provider-specific link/description fields + payment flow;
- room/activity images via bounded field lists + image flow;
- add handoff tag;
- no arbitrary model-defined flow/field/tag IDs.

Prove link creation completion is committed atomically with an outbox action, restart redelivery does not recreate links, and successful receipts dedupe sends.

**Step 2 — GREEN:** Extend ManyChat HTTP transport with closed methods `setCustomField(s)`, `sendFlow`, and `addTag`. Add exact action DTOs whose config references are selected by parent policy, not by the model. Preserve public conversational outbox separately but route both through the same allowlisted transport guard.

**Step 3 — Verify partial flow failures release only delivery obligation and commit.**

---

## Task 10: Compose handoff and package completion without duplicate responses

**Files:**
- Modify: `reservation_followup/workers.py`
- Modify: `v2_host/production.py`
- Modify: `v2_host/worker_main.py`
- Add: `v2_adapters/manychat_effects.py`
- Test: `tests/test_v2_manychat_handoff.py` (new)
- Test: `tests/test_v2_package_completion.py` (new)

**Step 1 — RED:** Assert handoff creates one durable ManyChat tag/flow action; e-mail remains disabled. Assert reservation confirmation/public copy is emitted once per package and the legacy service cannot also respond for the allowlisted subscriber.

**Step 2 — GREEN:** Compose `HandoffOutboxWorker`, typed ManyChat effect adapters and a package completion projector. Use deterministic action IDs and effect receipts.

**Step 3 — Verify and commit.**

---

## Task 11: Complete reconciliation and manual-review operators

**Files:**
- Modify: `v2_application/reconciliation.py`
- Add: `v2_host/operator_api.py`
- Modify: `v2_host/app.py`
- Modify: `v2_host/production.py`
- Test: `tests/test_v2_operator_reconciliation.py` (new)

**Step 1 — RED:** Assert uncertain Cloudbeds/Bókun/Stripe results are visible through a private authenticated operator endpoint with fingerprints only; no retry endpoint exists for uncertain writes. Assert reconciliation may resolve to confirmed/no-effect only with provider read-back evidence.

**Step 2 — GREEN:** Compose `ReconciliationQueue`, provider-specific read-back probes and read-only operator status. Keep manual acknowledgement/reconciliation separate from mutation dispatch.

**Step 3 — Verify redaction and commit.**

---

## Task 12: Compose `controlled_write` production graph and readiness

**Files:**
- Modify: `v2_host/production.py`
- Modify: `v2_host/composition.py`
- Modify: `v2_host/worker_main.py`
- Modify: `v2_host/app.py`
- Modify: `tests/test_v2_production_composition.py`
- Modify: `tests/test_v2_worker_main.py`
- Test: `tests/test_v2_controlled_write_graph.py` (new)

**Step 1 — RED:** Assert exact worker graph:
- inbox;
- boundary relay;
- Cloudbeds reservation;
- Bókun reservation;
- outcome/payment obligation projector;
- Stripe test-link initiation;
- provider-result delivery;
- public conversational delivery;
- handoff;
- reconciliation.

Settlement/payment-confirmation workers remain closed. Assert readiness reports every configured worker heartbeat plus provider credential/gate posture without exposing secrets.

**Step 2 — GREEN:** Build the real graph under `controlled_write`. Every live adapter receives the same allowlist/window/kill-switch policy. Keep `dark_read_only` unchanged.

**Step 3 — Verify startup with all effect gates false and commit.**

---

## Task 13: Add operational compose, routing isolation and one-command rollback

**Files:**
- Modify: `compose.v2.yaml`
- Add: `compose.v2.controlled-write.yaml`
- Add: `scripts/v2_canary_gate.py`
- Add: `scripts/v2_canary_rollback.sh`
- Add: `tests/test_phase8_ops_artifacts.py`
- Add: `tests/test_v2_canary_ops.py`

**Step 1 — RED:** Validate immutable image digest, non-root/read-only containers, private state volumes, localhost-only upstream, healthchecks, all gates false by default, exclusive route token, and rollback that closes gates before stopping services.

**Step 2 — GREEN:** Add controlled compose overlay and gate/rollback tooling. Gate tool requires exact candidate digest, subscriber, future expiry, Stripe test posture and a fresh readiness proof. It must never send a message.

**Step 3 — Verify compose rendering and rollback in an isolated temporary project; commit.**

---

## Task 14: Deterministic no-effect qualification

**Files:**
- Add: `scripts/run_v2_complete_canary_qualification.py`
- Modify: `.github/workflows/phase8.yml`
- Add: `tests/test_v2_complete_qualification.py`

**Step 1 — RED:** The harness must fail unless it observes:
- exact Luna model, private session, tools=0;
- allowlist reject before persistence;
- package proposal and one confirmation;
- fake Cloudbeds/Bókun/Stripe/ManyChat contracts;
- command/permit/result/outbox/reconciliation correlations;
- uncertain writes stop downstream effects;
- crash/restart dedupe;
- zero real provider/delivery calls.

**Step 2 — GREEN:** Implement local fake-provider servers and an end-to-end controlled-write qualification run using temporary ledgers.

**Step 3 — Run focused + full suite; commit.**

---

## Task 15: Credential and provider preflight without effects

**Files:**
- Add: `scripts/v2_complete_provider_preflight.py`
- Add: `tests/test_v2_provider_preflight.py`
- Evidence: `/home/ubuntu/.local/share/agente-v2-phase8-evidence/complete-whatsapp/<candidate>/`

**Step 1 — RED:** Assert preflight is read-only, rejects live Stripe credentials, validates ManyChat field/flow/tag configuration syntax, validates Cloudbeds/Bókun read scopes and records only presence/fingerprints.

**Step 2 — GREEN:** Implement sanitized preflight against configured read endpoints; no reservation/link/message endpoints are permitted.

**Step 3 — Run with real credentials in a redacted environment and save mode-0700 evidence. Commit code only, never evidence/secrets.**

---

## Task 16: Freeze, review, publish and deploy the idle canary

**Files:**
- Candidate branch and CI artifacts
- Private deploy directory under `/home/ubuntu/workspace/`

**Step 1:** Run focused regression and full historical suite from a clean environment; distinguish the known baseline failures.

**Step 2:** Run `git diff --check`, secret scan, static import/compile checks and Docker build using `Dockerfile.v2`.

**Step 3:** Perform one final integrated review against the approved design; fix all Critical/Important findings and rerun affected tests.

**Step 4:** Commit candidate, push branch, run GitHub Actions, pin immutable GHCR digest.

**Step 5:** Deploy parallel canary by digest with every effect gate false. Configure exclusive ManyChat routing for subscriber `1873018537` without sending or initiating a conversation. Confirm public route, readiness, no legacy double-response route and zero residual synthetic effects.

**Step 6:** Exercise rollback before any live gate opens, then restore the idle canary. Save sanitized private evidence.

---

## Task 17: Hand the live test window to Carlos

No automated test message is sent.

Before opening the window, present:
- candidate digest and readiness proof;
- exact capabilities open/closed;
- expiration time;
- rollback command;
- test order: conversation → Bókun → Cloudbeds → Stripe test link → ManyChat delivery → restart/dedupe → manual-review simulation;
- evidence location.

Open live gates only after Carlos explicitly authorizes the bounded window. Writes may then be enabled one provider at a time; payment confirmation, Pix/Wise and Stripe live remain closed.

---

## Verification matrix before claiming “ready”

```bash
# Focused
pytest -q \
  tests/test_v2_settings.py \
  tests/test_v2_canary_allowlist.py \
  tests/test_v2_package_confirmation.py \
  tests/test_v2_cloudbeds_write_transport.py \
  tests/test_v2_bokun_write_transport.py \
  tests/test_v2_stripe_test_transport.py \
  tests/test_v2_obligation_projector.py \
  tests/test_v2_manychat_actions.py \
  tests/test_v2_manychat_handoff.py \
  tests/test_v2_operator_reconciliation.py \
  tests/test_v2_controlled_write_graph.py \
  tests/test_v2_canary_ops.py \
  tests/test_v2_complete_qualification.py

# Full candidate suite
pytest -q

# Repository and artifact checks
git diff --check
python -m compileall -q reservation_* v2_*
docker compose -f compose.v2.yaml -f compose.v2.controlled-write.yaml config -q
docker build -f Dockerfile.v2 -t agente-v2:complete-candidate .
```

A passing unit suite without the end-to-end no-effect harness, real read-only preflight, immutable image, rollback rehearsal and idle canary readiness is **not** sufficient.