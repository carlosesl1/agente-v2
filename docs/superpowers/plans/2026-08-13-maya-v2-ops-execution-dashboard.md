# Maya V2 Ops Execution Dashboard — Implementation Plan

> **For Hermes:** REQUIRED SUB-SKILLS: use `superpowers:executing-plans`, `superpowers:test-driven-development`, and `superpowers:verification-before-completion`. Execute every behavior change RED → GREEN → REFACTOR. Do not deploy from a dirty tree.

**Goal:** Deliver a separately authenticated, strictly read-only operational dashboard at `https://hermes.chapadabackpackers.com/ops` that groups one execution per inbound event by Lead ID and renders the real Maya/provider/effect/delivery path as an n8n-style canvas with Input and Output visible together.

**Architecture:** The V2 router/worker processes emit best-effort, typed, allowlisted events into a dedicated SQLite trace. The new `v2_ops` service reads that trace and existing ledgers through read-only SQLite connections, projects immutable execution/node DTOs, authenticates its own sessions, and serves a same-origin static HTML/CSS/JS canvas under `/ops`. The dashboard has no business ports, provider credentials, write API, Docker socket, or writable operational-state mount.

**Tech Stack:** Python 3.12, stdlib SQLite/JSON/HMAC/scrypt, `cryptography` AES-GCM for permitted full node content, FastAPI/Starlette, vanilla HTML/CSS/JavaScript, pytest/httpx, Docker Compose, Traefik.

**Authority:**
- Design: `docs/superpowers/specs/2026-08-13-maya-v2-ops-execution-dashboard-design.md`
- Branch: `maya-v2-ops-dashboard`
- Worktree: `/home/ubuntu/agente-v2/.worktrees/maya-v2-ops-dashboard`
- Immutable base: `9226d1b91cdf6007c8f5ce72d0a572e35c4f8a5b`
- V3 is out of scope.

## Global implementation rules

1. Never persist headers, credentials, cookies, signed URLs, raw provider payloads, customer names, email addresses, phone numbers, document values, or unbounded message history.
2. Derive summaries from typed fields; do not interpret prose or alter Maya/controller decisions.
3. Trace writes are best effort after argument/type construction. Trace failure may log `trace_degraded` but must not retry a provider, change a commercial outcome, or fail an already valid turn.
4. Test transports and temporary SQLite files only. No real Cloudbeds/Bókun reservation, Stripe object, Pix/Wise settlement, ManyChat delivery, or handoff.
5. The UI and API have GET-only operational surfaces. The only POST routes are login/logout; no replay, retry, mutation, reservation, payment, message, handoff, container, or provider endpoint.
6. Existing ledger fallback is honest: `complete_trace`, `partial_trace`, or `ledger_only`; never fabricate absent request/response nodes.
7. One `execution_id` equals one inbound `event_id`. A batched turn is represented by a shared `batch_id`; batch-level nodes are attached to the deterministic primary event and linked from sibling executions as a shared-batch reference rather than duplicated as independent provider calls.
8. Each task ends with focused tests and `git diff --check`. Commit only after GREEN.

---

### Task 1: Freeze baseline and create the trace contract

**Files:**
- Create: `v2_ops/__init__.py`
- Create: `v2_ops/contracts.py`
- Create: `tests/test_v2_ops_contracts.py`
- Modify: `pyproject.toml`

**Step 1: Verify isolation and baseline**

Run:
```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" && pwd -P)
test "$GIT_DIR" != "$GIT_COMMON"
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q \
  --deselect=tests/test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only \
  --deselect=tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts \
  --deselect=tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_manifest_is_deterministic_current_and_covers_runtime_patch \
  --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_installed_wheel_imports_without_checkout_on_sys_path \
  --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_project_metadata_declares_closed_distribution \
  --deselect=tests/test_phase7_package.py::Phase7PackageTests::test_two_builds_are_byte_identical_closed_and_self_hashing \
  --deselect=tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed
```
Expected: linked worktree and `1778 passed, 7 deselected, 2953 subtests passed`. These seven exact historical tests also fail unchanged on immutable base `9226d1b9`: six enforce the closed Phase 7 wheel/evidence contract (`0.7.0`) against the later V2 package (`0.8.0`), and one enforces an obsolete Phase 8 index phrase. They are baseline exclusions, not dashboard regressions; do not broaden the selector.

**Step 2: Write failing contract tests**

Test exact enums and validation for:
- `ExecutionStatus`: `pending`, `running`, `completed`, `failed`, `manual_review`;
- `TraceCompleteness`: `complete_trace`, `partial_trace`, `ledger_only`;
- closed `NodeType` catalog from the approved spec;
- `OpsExecution`, `OpsNodeStart`, and `OpsNodeFinish` immutable DTOs;
- exact UTC timestamps, deterministic node IDs, monotonic ordinals, attempts >= 1;
- closed JSON only;
- rejection of forbidden keys recursively (`authorization`, `token`, `secret`, `password`, `cookie`, `header`, `signature`, raw contact/document keys);
- no signed URLs or provider credentials in full content.

Run:
```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_ops_contracts.py
```
Expected RED: `v2_ops.contracts` missing.

**Step 3: Implement minimal typed contract**

Implement exact dataclasses/enums, canonical JSON helpers, deterministic IDs (`sha256` domain separation), recursive allowlist validation, and payload byte limits. Add `v2_ops` to `[tool.v2-fasttrack].packages`.

**Step 4: Verify GREEN**

Run the focused test and `git diff --check`.

**Step 5: Commit**

```bash
git add pyproject.toml v2_ops tests/test_v2_ops_contracts.py
git commit -m "feat(v2-ops): define closed execution trace contract"
```

---

### Task 2: Implement encrypted, monotonic SQLite trace storage

**Files:**
- Create: `v2_ops/crypto.py`
- Create: `v2_ops/store.py`
- Create: `tests/test_v2_ops_store.py`

**Step 1: Write failing store tests**

Cover:
- deterministic schema bootstrap;
- one execution per `event_id`, grouped by exact `lead_id`;
- insert/replay idempotency;
- deterministic node start and finish;
- legal monotonic transitions only;
- stale finish cannot overwrite a terminal node;
- crash leaves `running`, later projected as `running_stale` without claiming success;
- full input/output encrypted at rest with AES-256-GCM and per-value nonce/AAD bound to execution/node/side;
- plaintext DB scan cannot find protected full content;
- wrong key cannot decrypt;
- summary/list/detail/full queries;
- exact Lead ID search, recent sort, status filter and cursor pagination;
- read-only URI connection rejects `INSERT`, `UPDATE`, `DELETE`, DDL and `PRAGMA journal_mode` mutation;
- contention uses busy timeout/WAL without duplicate nodes.

Expected RED: store unavailable.

**Step 2: Implement minimal store**

Create `SQLiteOpsTraceWriter` and `SQLiteOpsTraceReader`. The writer owns schema creation and monotonic transactions. The reader requires an existing absolute file and opens `file:...?...mode=ro&immutable=0` with `uri=True`, `PRAGMA query_only=ON`, a short busy timeout, and no schema bootstrap.

Use `V2_OPS_TRACE_KEY_HEX` as an independent 32-byte key. Store only allowlisted summaries in plaintext; encrypt permitted full JSON. Never store a provider credential.

**Step 3: Verify GREEN and commit**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_ops_contracts.py tests/test_v2_ops_store.py
git diff --check
git add v2_ops tests/test_v2_ops_store.py
git commit -m "feat(v2-ops): persist encrypted monotonic execution traces"
```

---

### Task 3: Add a best-effort trace recorder and typed serializers

**Files:**
- Create: `v2_ops/recording.py`
- Create: `v2_ops/serialization.py`
- Create: `tests/test_v2_ops_recording.py`
- Create: `tests/test_v2_ops_serialization.py`

**Step 1: Write failing tests**

Prove:
- `NullOpsRecorder` has no effects;
- `SQLiteOpsRecorder` starts/completes/fails nodes;
- recorder catches trace-store exceptions and reports degradation through a safe callback;
- trace failure never calls a wrapped provider twice and never changes its return/exception;
- typed serializers for `InboundEvent`, `InboundBatch`, `ModelRequest`, `ModelProposal`, `ReadRequest`, `ReadObservation`, `ProviderDispatchPermit`, `ProviderExecutionResult`, `StripeStepReceipt`, `PaymentInstruction`, `PublicDispatchClaim`, and `PublicAcceptanceReceipt` expose only approved fields;
- customer text is summarized by byte length/media presence/hash, not persisted verbatim in summaries;
- no private binding payload, account secret, signed URL, raw passenger/contact value, Stripe canonical URL, or ManyChat text leaks;
- “full” remains a typed permitted representation, not `repr()`/`asdict()` over arbitrary objects.

**Step 2: Implement minimal recorder/serializers**

The recorder API receives an execution/batch context and exact typed DTOs. It does not inspect arbitrary mappings from transports.

**Step 3: Verify and commit**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_ops_recording.py tests/test_v2_ops_serialization.py
git diff --check
git add v2_ops tests/test_v2_ops_recording.py tests/test_v2_ops_serialization.py
git commit -m "feat(v2-ops): record typed redacted runtime boundaries"
```

---

### Task 4: Trace ingress, inbox, Maya rounds, reads, and reducer commits

**Files:**
- Modify: `v2_application/inbox.py`
- Modify: `v2_application/inbox_worker.py`
- Modify: `v2_application/reads.py`
- Modify: `v2_application/turn_executor.py`
- Modify: `v2_host/production.py`
- Modify: `v2_host/settings.py`
- Modify: `tests/test_v2_inbox.py`
- Modify: `tests/test_v2_inbox_worker.py`
- Modify: `tests/test_v2_reads.py`
- Modify: `tests/test_v2_turn_executor.py`
- Modify: `tests/test_v2_production_composition.py`
- Create: `tests/test_v2_ops_turn_trace.py`

**Step 1: Write failing integration tests**

Using fake model/read ports and a temporary trace, assert the exact path:

```text
manychat_webhook → router_validation → inbox_accept → inbox_claim
→ maya_request(1) → maya_response(1) → maya_read_request
→ provider_read_request → provider_read_response → maya_observation
→ maya_request(2) → maya_response(2) → conversation_reducer → turn_commit
```

Also test:
- no-read path omits read/provider/round-2 nodes;
- model correction/review nodes use the correct attempt/round;
- exception marks only the active node failed and execution failed;
- deduplicated ingress updates existing execution, not a duplicate;
- multi-event batch does not duplicate provider calls across sibling event executions;
- trace writer failure leaves the original turn result and provider call count unchanged.

Expected RED: current constructors have no recorder seam.

**Step 2: Add optional recorder seams**

Default every seam to `NullOpsRecorder`. Emit at existing typed boundaries only. Preserve existing method signatures where possible with keyword-only optional dependencies. Do not wrap transports generically.

Add settings:
- `V2_OPS_TRACE_PATH` (absolute; disabled when absent);
- `V2_OPS_TRACE_KEY_HEX` (required only when trace enabled);
- `V2_OPS_TRACE_FULL_CONTENT` (default false until deployment explicitly enables encrypted full content).

**Step 3: Verify focused regression and commit**

Run all tests named above, then commit:
```bash
git commit -am "feat(v2-ops): trace ingress Maya reads and turn commits"
```

---

### Task 5: Trace fenced provider writes and reconciliation without changing effect semantics

**Files:**
- Modify: `v2_application/reservations.py`
- Modify: `v2_application/workers.py`
- Modify: `reservation_execution/worker.py` only if a generic exact-result hook is required; otherwise leave kernel unchanged
- Modify: `v2_host/production.py`
- Modify: `tests/test_v2_reservations.py`
- Modify: `tests/test_v2_workers.py`
- Create: `tests/test_v2_ops_provider_effect_trace.py`

**Step 1: Write failing tests**

With fake `ReservationPort`s, prove separate nodes for:
- Cloudbeds reservation request/response;
- Bókun booking request/response;
- claim/lease/fencing token/idempotency hash metadata;
- `NOT_CALLED`, `CALLED_NO_EFFECT`, `EFFECT_CONFIRMED`, `CALLED_UNKNOWN`;
- post-fence exception becomes unknown/manual review exactly as before;
- preparation failure emits no provider request;
- replay emits no second provider request;
- trace exception emits no provider retry.

**Step 2: Instrument immediately around `port.execute(provider_permit)`**

Serialize the typed permit through an allowlist. Never include `canonical_payload` wholesale; derive the permitted request DTO from the validated command/provider payload. Record only the normalized `ProviderExecutionResult` after validation.

**Step 3: Verify and commit**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_reservations.py tests/test_v2_workers.py tests/test_v2_ops_provider_effect_trace.py
git diff --check
git add v2_application reservation_execution v2_host tests
git commit -m "feat(v2-ops): trace fenced provider effects and outcomes"
```

---

### Task 6: Trace Stripe, Pix/Wise, ManyChat, handoff, and delivery receipts

**Files:**
- Modify: `v2_application/payments.py`
- Modify: `v2_application/public_delivery.py`
- Modify: `v2_adapters/provider_http.py`
- Modify: `v2_host/production.py`
- Modify: existing payment/delivery tests selected by symbol search
- Create: `tests/test_v2_ops_payment_delivery_trace.py`

**Step 1: Write failing tests**

Using only fake transports, assert:
- Stripe Product, Price and Payment Link are separate intent/accepted nodes;
- no Payment Link URL or Stripe secret appears in summaries/full payload;
- Pix/Wise instruction nodes expose method, amount/currency/economic version/receiver profile ID but not private payment instructions or settlement claims;
- ManyChat request/response nodes expose subscriber hash, chunk ordinal, idempotency hash, HTTP/result class and receipt hash, not message body/token;
- handoff request/delivery and reconciliation/manual-review nodes appear when exact typed outcomes exist;
- accepted/replayed delivery does not create a duplicate visual effect;
- trace failure cannot cause a second HTTP/provider call.

**Step 2: Add typed callbacks at existing step boundaries**

Instrument after DTO validation and before/after the exact transport call. Never intercept raw headers.

**Step 3: Verify and commit**

Run focused payment/delivery suites plus the new integration test, then commit:
```bash
git commit -am "feat(v2-ops): trace payment and public delivery stages"
```

---

### Task 7: Project live trace, ledger-only history, harness, and release metadata

**Files:**
- Create: `v2_ops/projection.py`
- Create: `v2_ops/sources.py`
- Create: `tests/test_v2_ops_projection.py`
- Create: `tests/test_v2_ops_sources.py`

**Step 1: Write failing tests**

Build small real temporary SQLite fixtures with the actual repository schemas. Cover:
- trace detail and edge projection;
- honest `complete_trace`, `partial_trace`, `ledger_only` classification;
- old inbox event with turn receipt but no trace;
- boundary/execution/payment/public-outbox milestones when present;
- absent ledgers do not create invented request/response nodes;
- stale heartbeat banner data;
- ten queue counts/oldest ages from read-only stores;
- release SHA/image digest/config fingerprint from metadata allowlist;
- locked/missing DB maps to sanitized unavailable/degraded source, never write fallback;
- projection emits no filesystem path, SQL, raw payload, subscriber/contact field, or secret.

**Step 2: Implement bounded read-only source adapters**

Use explicit SQL statements per known schema with table/column capability checks. No SQL comes from HTTP input. Enforce maximum row/node limits.

**Step 3: Verify and commit**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_ops_projection.py tests/test_v2_ops_sources.py
git diff --check
git add v2_ops tests/test_v2_ops_projection.py tests/test_v2_ops_sources.py
git commit -m "feat(v2-ops): project traces ledgers and harness health"
```

---

### Task 8: Implement independent authentication and read-only FastAPI API

**Files:**
- Create: `v2_ops/auth.py`
- Create: `v2_ops/settings.py`
- Create: `v2_ops/app.py`
- Create: `v2_ops/main.py`
- Create: `tests/test_v2_ops_auth.py`
- Create: `tests/test_v2_ops_api.py`

**Step 1: Write failing auth tests**

Cover:
- strict `V2_OPS_USERNAME`, scrypt password-hash grammar, 32-byte session key, TTL and secure defaults;
- constant-shape error for bad user/password;
- process-local bounded rate limit for repeated login failures;
- HMAC-signed expiring session, key rotation rejection, logout invalidation;
- `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/ops` cookie;
- CSRF token and same-origin validation for login/logout;
- expired/tampered session returns login/401 without leaking cause.

**Step 2: Write failing API tests**

Cover all approved routes and prove:
- unauthenticated operational HTML/API inaccessible;
- login/health are the only public surfaces;
- list/detail/nodes/full/harness/release are read-only;
- exact Lead ID validation, bounded limit/cursor/status;
- ETag and `304 Not Modified`;
- full-content endpoint returns `not_recorded` honestly;
- method matrix rejects PUT/PATCH/DELETE and all unapproved POSTs with 405/404;
- OpenAPI, docs, redoc disabled in production;
- response security headers and `Cache-Control: no-store`;
- sanitized 503 for source unavailability.

**Step 3: Implement settings/auth/app**

Use stdlib `hashlib.scrypt`, `hmac.compare_digest`, signed session payloads, server-validated expiry, CSRF nonce, and an in-memory bounded login limiter. Do not reuse WebUI/V2 webhook credentials.

**Step 4: Verify and commit**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_ops_auth.py tests/test_v2_ops_api.py
git diff --check
git add v2_ops tests/test_v2_ops_auth.py tests/test_v2_ops_api.py
git commit -m "feat(v2-ops): serve authenticated read-only operations API"
```

---

### Task 9: Build the execution canvas UI

**Files:**
- Create: `v2_ops/static/index.html`
- Create: `v2_ops/static/ops.css`
- Create: `v2_ops/static/ops.js`
- Create: `v2_ops/templates/login.html`
- Create: `tests/test_v2_ops_ui.py`
- Create: `tests/js/ops-ui.test.mjs`

**Step 1: Write failing structure/behavior tests**

Assert:
- login form and no operational data before login;
- recent list, exact Lead ID search, status/completeness badges;
- connected SVG/canvas nodes, branches, zoom, fit and node selection;
- Input and Output panels exist simultaneously in the inspector (no tabs);
- full input and full output load independently on explicit action;
- metadata/error collapsible;
- two-second polling, ETag preservation, selected node/canvas position retained;
- disconnected/stale/trace-degraded banners;
- no replay/retry/edit/send/reserve/payment controls or mutation fetches;
- responsive linear fallback on narrow screens;
- escaped rendering through `textContent`, not `innerHTML` with API data.

Expected RED: assets missing.

**Step 2: Implement vanilla frontend from the approved mockup**

Keep static assets local; no CDN, analytics or third-party scripts. Apply strict CSP with self-only scripts/styles and no connections outside same origin.

**Step 3: Run JS and Python UI tests, then commit**

```bash
node --test tests/js/ops-ui.test.mjs
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_ops_ui.py
git diff --check
git add v2_ops/static v2_ops/templates tests/test_v2_ops_ui.py tests/js
git commit -m "feat(v2-ops): render live execution canvas and joint inspector"
```

---

### Task 10: Add hardened container and deployment manifest

**Files:**
- Create: `Dockerfile.v2-ops`
- Create: `deploy/v2-ops/compose.ops.yaml`
- Create: `deploy/v2-ops/env.example`
- Create: `deploy/v2-ops/README.md`
- Create: `tests/test_v2_ops_deploy_contract.py`
- Modify: `.github/workflows/ci.yml` or the repository’s current owning workflow only after inspecting its exact structure

**Step 1: Write failing deployment contract tests**

Statically parse the Dockerfile/Compose and require:
- non-root runtime;
- read-only root filesystem;
- `cap_drop: [ALL]`, `no-new-privileges`, tmpfs, bounded resources;
- no Docker socket;
- individual `:ro` mounts for only trace, inbox, boundary, execution,
  payment-initiation, follow-up, public-outbox, Cloudbeds/Bókun audits,
  heartbeat and release metadata; no whole-`ga-state` mount and no
  `v2-private-customer.sqlite3` mount;
- only independent ops username/password hash/session key/trace key env values;
- no Cloudbeds/Bókun/Stripe/ManyChat/provider env names;
- no host-published port;
- Traefik router for exact host plus `Path(`/ops`) || PathPrefix(`/ops/`)`, higher priority than WebUI;
- healthcheck on an internal safe endpoint;
- source/image build includes `v2_ops` static/templates.

**Step 2: Implement container/manifest**

The dashboard process is separate from API/worker/router. Each authorized
trace/state file is mounted individually and read-only so unrelated databases
are absent from the container namespace. The V2 writer image receives only
trace path/key in addition to its existing runtime environment.

**Step 3: Verify render and commit**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_ops_deploy_contract.py
docker compose -f deploy/v2-ops/compose.ops.yaml config >/tmp/v2-ops-compose.rendered.yaml
git diff --check
git add Dockerfile.v2-ops deploy tests/test_v2_ops_deploy_contract.py .github/workflows
git commit -m "build(v2-ops): package hardened read-only dashboard"
```

---

### Task 11: Run focused, canonical, security, and no-effect gates

**Files:**
- Create: `tests/test_v2_ops_no_effect_surface.py`
- Create: `scripts/verify_v2_ops_no_secrets.py`
- Create: `docs/superpowers/evidence/v2-ops-dashboard-gate.md`

**Step 1: Add final adversarial REDs**

Prove:
- recursive secret/PII corpus is rejected or redacted;
- ASGI route inventory has no business mutation surface;
- dashboard package import graph does not import concrete provider transports, reservation workers, payment initiators or delivery senders;
- fake provider/delivery call counters remain zero during every dashboard API/UI smoke;
- read-only SQLite authorizer/URI blocks mutation;
- malicious JSON/HTML strings are escaped in UI;
- oversized content/cursors/Lead IDs fail boundedly.

**Step 2: Run final focused suite**

```bash
/home/ubuntu/chapada-leads-hermes/venv/bin/python -m pytest -q tests/test_v2_ops_*.py
node --test tests/js/ops-ui.test.mjs
python scripts/verify_v2_ops_no_secrets.py
```

**Step 3: Run repository canonical gate in a clean explicit environment**

Use the repository’s existing documented command and the explicit V2 config path. Record exact command, exit code, counts, commit SHA, tree and output hash. Do not claim the historical gate as evidence for this branch.

**Step 4: Build and inspect immutable image**

```bash
docker build -f Dockerfile.v2-ops -t agente-v2-ops:<sha> .
docker inspect agente-v2-ops:<sha>
```

Run it locally with synthetic trace/ledgers and fake secrets only. Verify login, APIs, UI assets, filesystem read-only behavior and zero business calls.

**Step 5: Commit evidence**

```bash
git add tests scripts docs/superpowers/evidence
git commit -m "test(v2-ops): prove read-only no-effect dashboard boundary"
```

---

### Task 12: Read-only review and correction gate

**Files:** any files implicated by concrete findings; each fix requires a new named failing regression.

**Step 1: Request bounded independent review**

Review scopes:
1. trace monotonicity/correlation and “one event = one execution” truth;
2. redaction/encryption/auth/read-only security;
3. instrumentation no-change/no-retry semantics;
4. UI requirements and no-write surface;
5. deploy isolation and `/ops` routing.

**Step 2: Reproduce every material finding as RED**

Do not patch speculative prose. A material finding needs a concrete current-branch counterexample.

**Step 3: Fix, rerun focused selectors, then rerun final affected gate**

Obtain read-only re-review after corrections. Commit corrective changes separately.

---

### Task 13: Dark deploy dashboard only at `/ops`

**Operational files (outside repository, created only after candidate review):**
- Deploy directory: `/home/ubuntu/workspace/agente-v2-ops-dashboard-deploy`
- Preserve current WebUI deploy: `/home/ubuntu/workspace/hermes-webui-deploy`
- Preserve current V2 deploy/state: `/home/ubuntu/workspace/agente-v2-canary-deploy`

**Step 1: Preflight current live topology**

Capture:
- current WebUI/V2 container IDs, image digests, compose renders, Traefik labels and health;
- `https://hermes.chapadabackpackers.com/` behavior;
- V2 `/healthz` and `/readyz`;
- V3 HEAD/status for isolation evidence only, without modifying it;
- backup/rollback manifest for any deploy file changed.

**Step 2: Generate independent ops credentials**

Generate a unique password, scrypt hash, session key and trace key. Store only in the ops deploy’s restricted env file. Do not print or commit plaintext credentials. Deliver the initial password to Carlos through the current trusted private surface only.

**Step 3: Start dashboard against read-only state before worker instrumentation**

Mount existing ledgers read-only. Expect ledger-only/partial history. Verify:
- `/ops/login` returns login;
- `/ops/` and APIs require auth;
- authenticated list/harness/release work;
- `/` remains WebUI;
- V2 webhook health remains unchanged;
- no provider credentials in dashboard container env;
- no business rows/call counters changed.

**Step 4: Verify real browser UI**

Use a browser/computer-use tool if available. Check desktop and narrow layout, recent list, search, canvas, node selection, simultaneous Input/Output, full-content action, polling preservation, logout and expired session.

**Step 5: Record dark-deploy evidence and rollback command**

Do not instrument the worker until this dark dashboard gate is GREEN.

---

### Task 14: Roll out instrumented V2 writer and prove live trace without effects

**Step 1: Build/publish immutable V2 candidate**

Bind exact source SHA/tree/image digest and CI result. Preserve current active image digest and compose files for one-command rollback.

**Step 2: Update V2 router/worker trace-only configuration**

Add the trace state file under the existing persistent `ga-state` mount and independent trace key. Do not change provider feature gates, Stripe mode, reservation authorization, ManyChat behavior, queue topology, persisted DB paths or mounts.

**Step 3: Restart safely and verify base health**

Check API/worker/router health, heartbeat, queue health, public ingress, WebUI, `/ops`, and container image digests.

**Step 4: Generate one controlled no-effect observation**

Use a read-only/non-commercial test event or an existing naturally arriving event only under an approved test identity. The proof must not reserve, book, generate Product/Price/Payment Link, charge, send an artificial customer message, trigger handoff, Pix or Wise. If a safe event cannot be guaranteed, use an offline replay into a copied state/trace instead of production ingress.

Verify the dashboard transitions from running to completed and shows Maya/read/provider observation nodes when such a read occurs. Confirm business ledgers/call counters show no newly authorized effects caused by the smoke.

**Step 5: GO/NO-GO and rollback**

GO requires all 15 spec acceptance criteria, exact test evidence, working rollback, unchanged WebUI/webhook, dashboard no-write proof, and V3 isolation. Otherwise roll back the V2 image/config and/or remove only the `/ops` router/service, preserving state for diagnosis.

---

## Final verification checklist

- [ ] Exact branch/worktree and clean tree.
- [ ] Every production behavior started with an observed RED.
- [ ] Focused tests and full canonical gate GREEN on final SHA.
- [ ] Static secret/PII scan GREEN.
- [ ] Dashboard import/route matrix proves no provider/effect capability.
- [ ] Trace failure never changes business result or call count.
- [ ] One execution per event and exact Lead ID grouping proven.
- [ ] Maya query/provider request/provider response/Maya observation are distinct when present.
- [ ] Input and Output are simultaneously visible.
- [ ] Full permitted content is encrypted at rest and loaded only on demand.
- [ ] `/ops` authenticated; WebUI `/` unchanged.
- [ ] Dashboard state mounts read-only; no provider credentials; no Docker socket.
- [ ] Live/synthetic smoke causes zero reservations, bookings, Payment Links, charges, deliveries or handoffs.
- [ ] V2 rollback captured and tested mechanically.
- [ ] V3 path remains untouched.
