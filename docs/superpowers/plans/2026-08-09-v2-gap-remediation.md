# Maya V2 Gap Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a new immutable successor of `1a7ac99818f6ea3a15206f4ec58a4100d6e87f67` that closes the confirmed preproduction gaps without changing the running canary or weakening one-submit, phone-language, allowlist, idempotency, and manual-review safety contracts.

**Architecture:** Keep provider creation evidence monotonic and add independent read-only audit/health dimensions. Split runtime configuration by process role, make channel acceptance honest, and reconcile ambiguous Stripe test-mode effects without automatic duplicate creates. All implementation and tests remain local/fake-only; deployment and real effects remain separate operator gates.

**Tech Stack:** Python 3.12, dataclasses, SQLite, FastAPI, httpx MockTransport, Docker Compose, GitHub Actions, pytest, Ruff.

## Global Constraints

- Parent candidate is immutable: `1a7ac99818f6ea3a15206f4ec58a4100d6e87f67` with tree `994fdf30705e71fce5509641fbc6265836effd7c`.
- Implement only on branch `maya-v2-gap-remediation`; never amend or force-move the parent branch.
- Do not promote, restart, close, or alter the active canary.
- Do not create provider reservations, Stripe objects, payments, ManyChat messages, or external handoffs.
- Accepted provider submit plus no explicit failure plus exactly one conflict-free principal ID remains `EFFECT_CONFIRMED` immediately.
- Provider audit is GET-only and cannot downgrade confirmed creation, retract completion, enqueue a retrying POST, or mutate the public outbox.
- ManyChat HTTP success proves at most `accepted_by_manychat`; a local hash is correlation only; no resend follows an accepted/ambiguous mutation.
- Customer language is derived only from authenticated canonical phone: `+55...` → `pt-BR`; other valid international DDI → `en`; national phone without DDI fails closed.
- Conversational phone never replaces authenticated ManyChat phone. No deterministic language parser or semantic extractor may compete with Maya.
- Stripe remains test mode and receives no phone or PII.
- Deadline remains optional for the persistent allowlisted canary; do not introduce global auto-close.
- Secrets, customer PII, provider IDs, authenticated URLs, request bodies, and raw exception text must not enter repr, logs, heartbeat, evidence, or public artifacts.
- Every production change follows RED → observed causal failure → minimal GREEN → focused regression → commit.
- Heavy full pytest/Ruff/boundary/build gates run once after the final source candidate is frozen.

---

### Task 0: Baseline, plan, and immutable lineage

**Files:**
- Create: `docs/superpowers/plans/2026-08-09-v2-gap-remediation.md`
- Scratch only: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: parent SHA/tree and official Phase 8 deselection list.
- Produces: immutable execution authority and progress ledger.

- [x] Verify branch parent, tree, remote parent, and clean status.
- [x] Run the official clean-environment suite and record `1587 passed, 7 deselected, 2940 subtests passed`.
- [ ] Commit only this plan before production edits.

Run:
```bash
git add docs/superpowers/plans/2026-08-09-v2-gap-remediation.md
git commit -m "docs(v2): plan preproduction gap remediation"
```

---

### Task 1: Role-scoped settings, least privilege, and redaction

**Files:**
- Modify: `v2_host/settings.py`
- Modify: `v2_host/api_main.py`
- Modify: `v2_host/worker_main.py`
- Modify: `compose.v2.yaml`
- Modify: `Dockerfile.v2`
- Modify: `tests/test_v2_settings.py`
- Modify: `tests/test_v2_production_composition.py`
- Create or modify: `tests/test_v2_role_scoped_settings.py`

**Interfaces:**
- Produces: `V2ProcessRole.API | WORKER`; `V2Settings.from_env(environ, *, process_role=...)`; safe repr that exposes only non-sensitive operational metadata.
- API role may boot without model/provider/payment-write credentials and must reject effect/provider secrets in its parsed object.
- Worker role retains existing controlled-write validation and provider capabilities.

- [ ] RED: assert synthetic sentinels for webhook, provider, Stripe, ManyChat, transcript, authority, and financial secrets never occur in `repr(settings)`.
- [ ] RED: assert API settings parse a controlled-write API environment without provider/model secrets and leave every worker-only field empty.
- [ ] RED: assert worker settings still fail closed when required controlled-write provider/model material is missing.
- [ ] RED: parse rendered Compose and assert API environment does not contain names for Cloudbeds/Bókun/Stripe link/ManyChat API/model/transcript/authority secrets; worker contains only required worker names.
- [ ] Run the three RED selectors and confirm failures are semantic.
- [ ] Add exact process-role enum and role-aware parsing/validation. Mark every secret/private dataclass field `repr=False`; implement a closed custom repr with role, runtime mode, SQLite base path name, candidate SHA/digest fingerprints, gate names, and no values.
- [ ] Split Compose anchors into common/API/worker; do not add router secrets to API/worker.
- [ ] Correct `org.opencontainers.image.source` to the canonical repository URL while retaining `revision=$VCS_REF`.
- [ ] Run focused settings/composition/package tests and `git diff --check`.
- [ ] Commit:
```bash
git add v2_host/settings.py v2_host/api_main.py v2_host/worker_main.py compose.v2.yaml Dockerfile.v2 tests/test_v2_settings.py tests/test_v2_production_composition.py tests/test_v2_role_scoped_settings.py
git commit -m "fix(v2): isolate runtime credentials by process role"
```

---

### Task 2: Authoritative locale and complete offer projection

**Files:**
- Modify: `config/v2_luna_system_prompt.txt`
- Modify: `v2_adapters/provider_http.py`
- Modify: `v2_application/public_reply.py`
- Modify: `tests/test_v2_bokun_write_transport.py`
- Modify: `tests/test_v2_public_reply.py`
- Modify: `tests/test_v2_turn_executor.py`

**Interfaces:**
- Consumes: `CustomerLanguage.PT_BR | EN` already projected before first `ModelRequest`.
- Produces: closed Bókun locale mapping (`pt-BR` → provider-supported Portuguese locale; `en` → provider-supported English locale), with no phone on the wire solely for locale selection.

- [ ] RED: inspect the exact first and recursive model requests and assert prompt text cannot authorize the current-message language to override phone-derived locale.
- [ ] RED: send otherwise identical Bókun commands in `pt-BR` and `en`; assert checkout/main-contact locale fields differ exactly and no unsupported raw locale crosses the transport.
- [ ] RED: pass two relevant same-domain offers and assert public grounding renders both in deterministic provider-observation order rather than silently selecting the first.
- [ ] Observe causal failures.
- [ ] Replace contradictory prompt language with the authenticated-phone contract and explicit prohibition on conversational phone override.
- [ ] Thread only closed locale into Bókun payload construction and map it at the adapter boundary.
- [ ] Render every positive observation group that the proposal actually grounds; retain fail-closed domain/amount/date anchors.
- [ ] Run affected turn, Bókun, public-reply, checkout, and completion projector tests.
- [ ] Commit:
```bash
git add config/v2_luna_system_prompt.txt v2_adapters/provider_http.py v2_application/public_reply.py tests/test_v2_bokun_write_transport.py tests/test_v2_public_reply.py tests/test_v2_turn_executor.py
git commit -m "fix(v2): enforce phone-derived locale end to end"
```

---

### Task 3: Honest ManyChat acceptance receipts

**Files:**
- Modify: `v2_adapters/manychat.py`
- Modify: `v2_adapters/provider_http.py`
- Modify: `v2_contracts/channel.py`
- Modify: `reservation_boundary/public_dispatch.py`
- Modify: `reservation_boundary/sqlite_store.py`
- Modify: `v2_application/completion.py`
- Modify: `v2_application/public_delivery.py`
- Modify: `tests/test_v2_manychat_flow_delivery.py`
- Modify: `tests/test_v2_completion.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_phase8_boundary_store.py` when required by the boundary schema owner.

**Interfaces:**
- Produces: typed `ChannelAcceptanceReceipt` or equivalent with `provider_request_id: str | None`, mandatory local `dispatch_correlation_id`, and `accepted_at`; no `delivered_at` without documented downstream receipt.
- New durable terminal status is `accepted`; legacy `delivered` rows remain readable and terminal but no new 2xx path writes that status.

- [ ] RED: 200 `{"status":"success"}` without ID persists `accepted` with local correlation and never a provider delivery receipt.
- [ ] RED: 200 with `request_id` persists `accepted`, not delivered.
- [ ] RED: partial field mutation plus failed flow remains manual review and a second worker run makes zero HTTP calls.
- [ ] RED: legacy delivered row remains terminal and is not resent.
- [ ] Observe causal failures.
- [ ] Replace synthetic `provider_message_id` with explicit request/correlation fields.
- [ ] Migrate local outbox and boundary receipt semantics additively; preserve historical read compatibility.
- [ ] Rename dispositions and counting APIs to accepted semantics, leaving compatibility aliases only where external history requires them.
- [ ] Run all public-delivery, boundary, completion, ManyChat flow, handoff, and turn-executor selectors.
- [ ] Commit:
```bash
git add v2_adapters/manychat.py v2_adapters/provider_http.py v2_contracts/channel.py reservation_boundary/public_dispatch.py reservation_boundary/sqlite_store.py v2_application/completion.py v2_application/public_delivery.py tests/test_v2_manychat_flow_delivery.py tests/test_v2_completion.py tests/test_v2_turn_executor.py tests/test_phase8_boundary_store.py
git commit -m "fix(v2): distinguish ManyChat acceptance from delivery"
```

---

### Task 4: Redacted worker health and process ownership

**Files:**
- Modify: `v2_host/worker_main.py`
- Modify: `v2_host/production.py`
- Modify: `v2_host/composition.py`
- Modify: `tests/test_v2_worker_main.py`
- Modify: `tests/test_v2_production_composition.py`
- Modify: `tests/test_v2_composition.py`

**Interfaces:**
- Produces per queue: status, closed reason code, HMAC/SHA fingerprint over exception class plus process-local salt/domain (never message), consecutive-failure count, last success/failure timestamps, and bounded backoff seconds.
- Closed capability and idle are healthy typed outcomes; manual-review/divergent/exhausted outcomes degrade the relevant capability even when no exception is raised.

- [ ] RED: thrown synthetic exception containing phone, secret, URL, and provider ID produces heartbeat/log without any sentinel while preserving queue and stable failure class fingerprint.
- [ ] RED: consecutive failures increase bounded backoff; success resets count; one queue never retries twice in one cycle.
- [ ] RED: reconciliation `DIVERGENT`/`ATTEMPTS_EXHAUSTED` result degrades heartbeat without an exception.
- [ ] RED: process-role owner registry rejects API opening worker-owned stores and rejects same-inode aliases before schema writes.
- [ ] Observe failures.
- [ ] Extend cycle item/result classification, heartbeat schema v2, and backoff scheduling in main loop without logging `str(exc)` or traceback.
- [ ] Make store ownership declarations process-role scoped; keep capability-closed stores unopened.
- [ ] Run focused worker/composition/settings/SQLite owner tests.
- [ ] Commit:
```bash
git add v2_host/worker_main.py v2_host/production.py v2_host/composition.py tests/test_v2_worker_main.py tests/test_v2_production_composition.py tests/test_v2_composition.py
git commit -m "fix(v2): expose redacted capability health"
```

---

### Task 5: Bókun GET-only auditor and Cloudbeds degradation

**Files:**
- Create: `v2_application/bokun_audit.py`
- Modify: `v2_adapters/provider_http.py`
- Modify: `v2_host/settings.py`
- Modify: `v2_host/production.py`
- Modify: `v2_host/worker_main.py`
- Create: `tests/test_v2_bokun_audit.py`
- Modify: `tests/test_v2_cloudbeds_audit.py`
- Modify: `tests/test_v2_production_composition.py`

**Interfaces:**
- Bókun auditor mirrors the durable GET-only lifecycle of Cloudbeds but validates the frozen activity/date/start/party/base/fee/total/currency/status subject.
- Audit statuses are independent of `ExecutionCertainty`; no auditor has a POST-capable port.

- [ ] RED: confirmed Bókun outcome projects exactly one private audit task and executes exactly one GET per attempt, zero POST.
- [ ] RED: not-visible retries with closed budget; exact match becomes matched; divergence/exhaustion becomes terminal and appears in heartbeat/capability health.
- [ ] RED: auditor repr, logs, traceback, redirects, and heartbeat omit private booking ID and authenticated URL.
- [ ] RED: matched/divergent audit never changes execution outcome, completion rows, or public outbox.
- [ ] Observe failures.
- [ ] Implement separate DTO/store/projector/worker and a capability-restricted Bókun GET transport.
- [ ] Wire a separate SQLite owner path and run it inside reconciliation without adding a new provider write surface.
- [ ] Propagate Cloudbeds terminal degradation to typed reconciliation health.
- [ ] Run Bókun audit, Cloudbeds audit, production composition, monotonic submit, and completion tests.
- [ ] Commit:
```bash
git add v2_application/bokun_audit.py v2_adapters/provider_http.py v2_host/settings.py v2_host/production.py v2_host/worker_main.py tests/test_v2_bokun_audit.py tests/test_v2_cloudbeds_audit.py tests/test_v2_production_composition.py
git commit -m "feat(v2): audit confirmed Bókun bookings read only"
```

---

### Task 6: Stripe test-mode step journal and reconciliation

**Files:**
- Modify: `v2_contracts/payments.py`
- Modify: `v2_adapters/stripe.py`
- Modify: `v2_application/payments.py`
- Modify: `v2_host/production.py`
- Modify: `tests/test_v2_stripe_test_transport.py`
- Modify: `tests/test_v2_payment_initiation.py`
- Create: `tests/test_v2_stripe_reconciliation.py`

**Interfaces:**
- Produces private step receipts for Product, Price, and Payment Link: expected metadata hash, stable idempotency key, provider object ID when accepted, canonical link URL only for final link, and status.
- Reconciliation port is GET/list/search only. Automatic create replay remains disabled unless an explicit, tested provider contract authorizes exact same-key replay; this task does not authorize it.

- [ ] RED: timeout after accepted Product persists Product receipt and a later worker performs only GET/list/search, never another Product POST.
- [ ] RED: Product+Price known but link unknown either discovers one exact test-mode link or remains manual review; conflicting/multiple results fail closed.
- [ ] RED: accepted final link with ID and URL is durable immediately; subsequent GET mismatch is audit/manual-review and does not create another link.
- [ ] RED: persisted receipts and repr contain no customer data, phone, Stripe key, provider raw body, or authenticated URL.
- [ ] Observe failures.
- [ ] Persist intent before each call and accepted receipt after each call in the payment initiation owner transaction boundary.
- [ ] Add private read-only reconciliation and preserve manual review when Stripe cannot prove one exact final link.
- [ ] Keep all tests on `api.stripe.invalid`/MockTransport and test-mode payloads.
- [ ] Run Stripe transport, payment initiation, completion projector, and E2E fake payment tests.
- [ ] Commit:
```bash
git add v2_contracts/payments.py v2_adapters/stripe.py v2_application/payments.py v2_host/production.py tests/test_v2_stripe_test_transport.py tests/test_v2_payment_initiation.py tests/test_v2_stripe_reconciliation.py
git commit -m "feat(v2): reconcile ambiguous Stripe test links"
```

---

### Task 7: Reproducible router composition, identity, CI, and rollback harness

**Files:**
- Modify: `compose.v2.yaml`
- Modify: `deploy/canary_router.py`
- Create: `deploy/verify_runtime_identity.py`
- Create: `deploy/rollback_harness.py`
- Modify: `.github/workflows/phase8.yml`
- Modify: `tests/test_v2_canary_router.py`
- Create: `tests/test_v2_runtime_identity.py`
- Create: `tests/test_v2_rollback_harness.py`
- Modify: `tests/test_phase8_dockerfile_contract.py` or the repository’s active Dockerfile contract owner.

**Interfaces:**
- Compose contains router, API, and worker services with immutable image ref and role-scoped environments.
- Identity verifier compares expected SHA/digest to OCI labels/RepoDigest supplied as injectable metadata; no Git dependency inside runtime.
- Rollback harness operates only on caller-supplied temporary directories/volumes in CI.

- [ ] RED: rendered Compose includes router and no externally published API port; router readiness fails closed on identity mismatch.
- [ ] RED: image revision mismatch, mutable image ref, or absent digest fails before ingress forwarding.
- [ ] RED: hermetic rollback restores previous fake candidate metadata and preserves temporary SQLite receipt fingerprints.
- [ ] RED: CI source contract includes Compose validation, network-denied boot/import smoke, router auth/allowlist probes, worker heartbeat, restart persistence, and rollback harness; it never references the operational deploy directory.
- [ ] Observe failures.
- [ ] Add router service and internal network topology; keep persistent no-deadline policy available.
- [ ] Add injectable identity verifier and temporary-only rollback harness.
- [ ] Extend CI with fake secrets, temp volumes, no provider routes, and immutable locally built image.
- [ ] Run Compose config, deployment contract tests, router tests, identity tests, and rollback tests.
- [ ] Commit:
```bash
git add compose.v2.yaml deploy/canary_router.py deploy/verify_runtime_identity.py deploy/rollback_harness.py .github/workflows/phase8.yml tests/test_v2_canary_router.py tests/test_v2_runtime_identity.py tests/test_v2_rollback_harness.py tests/test_phase8_dockerfile_contract.py
git commit -m "feat(v2): make routed deployment reproducible"
```

---

### Task 8: Freeze, full verification, and exact-SHA audit

**Files:**
- No production edits unless a new causal RED is first created.
- Create outside Git: qualification report and exact-SHA review package.

- [ ] Re-read this plan and map every implemented requirement to a test.
- [ ] Run all focused changed-byte selectors once from final bytes.
- [ ] Run official full pytest with seven historical deselections.
- [ ] Run Ruff, compileall, `git diff --check`, architecture/boundary checks, package/build tests, and deterministic secret/PII sentinel scans.
- [ ] Build one image labeled with final SHA and run network-disabled/read-only import/entrypoint/Compose smoke.
- [ ] Recheck active canary image/status and prove it did not change.
- [ ] Freeze commit/tree/image digest and dispatch independent exact-SHA reviewers for security, provider/payment safety, and deployment composition.
- [ ] For any Critical/Important finding: reproduce with a new RED, patch minimally, rerun affected tests plus the full gate, and obtain re-review.

---

### Task 9: Publish successor without promotion

**Files:**
- No new source edits.

- [ ] Push `maya-v2-gap-remediation`.
- [ ] Fetch and verify local HEAD equals `origin/maya-v2-gap-remediation`.
- [ ] Observe Phase 8 CI for the exact SHA and require success.
- [ ] Record commit, tree, CI run, OCI digest, test counts, and NO-GO/GO scope.
- [ ] Do not promote. Stop at the operator’s explicit canary-promotion decision.
