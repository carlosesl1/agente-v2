# Cloudbeds V2 Monotonic Submit Confirmation Implementation Plan

> **For Hermes:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use superpowers:test-driven-development for every behavior change and superpowers:verification-before-completion before any pass/complete claim.

**Goal:** Confirm an accepted Cloudbeds `postReservation` immediately, persist its principal reservation ID privately, and move read-back into bounded GET-only audit without permitting downgrade, duplicate completion, or redispatch.

**Architecture:** Keep the Cloudbeds write transport one-shot and classify only its submit envelope. Extend the provider result with an optional private raw reference used only by Cloudbeds, while preserving Bókun fingerprints. Add a separate SQLite-backed Cloudbeds audit projector/worker invoked inside the existing reconciliation stage; it derives audit work only from already-confirmed outcomes and has a GET-only transport. Completion remains driven exclusively by the immutable execution outcome.

**Tech stack:** Python 3.11+, dataclasses, sqlite3 STRICT tables, httpx `MockTransport`, pytest, Ruff, GitHub Actions, Docker/OCI.

**Design:** `docs/superpowers/specs/2026-08-01-cloudbeds-monotonic-submit-confirmation-design.md`

---

## Global safety gate

Before and after every task:

```bash
git status --short
```

Forbidden during implementation and qualification:

- Cloudbeds credentials or provider network;
- `postReservation` outside an `httpx.MockTransport`/fake;
- modifying or querying reservation `2547077136052`;
- deploy/canary/real write.

Use only:

```bash
PY=/home/ubuntu/chapada-leads-hermes/venv/bin/python
```

### Task 1: RED — accepted submit is monotonic and write transport never read-backs

**Files:**
- Modify: `tests/test_v2_cloudbeds_write_transport.py`
- Test: `tests/test_v2_cloudbeds_write_transport.py`

**Step 1: Add failing tests**

Add/adjust fake HTTP route tests proving:

- accepted 2xx + one principal ID + no failure returns confirmed;
- request log contains exactly one `POST /api/v1.1/postReservation`;
- no `/api/v1.3/getReservation` occurs in the write transport;
- a fake read-back that would be not-found/divergent is therefore incapable of changing the submit result;
- non-2xx with ID, `success:false`, missing ID, conflicting aliases/nested IDs, invalid JSON, and timeout fail closed with zero POST retry.

**Step 2: Run RED**

```bash
$PY -B -m pytest -q -p no:cacheprovider tests/test_v2_cloudbeds_write_transport.py
```

Expected: failures showing the current synchronous GET and old read-back downgrade.

**Step 3: Record RED witness**

Capture exact failing test names and assertions in the final qualification notes. Do not modify production before RED is observed.

### Task 2: GREEN — remove GET from the Cloudbeds write confirmation boundary

**Files:**
- Modify: `v2_adapters/provider_http.py`
- Test: `tests/test_v2_cloudbeds_write_transport.py`
- Regression: `tests/test_v2_bokun_write_transport.py`

**Step 1: Minimal implementation**

In `CloudbedsHTTPTransport._reserve_lodging`:

- retain all pre-submit validation and revalidation;
- retain exactly one `_write_request(method="POST", path="/api/v1.1/postReservation", ...)`;
- retain `_cloudbeds_submit_evidence` strict ID/failure classification;
- return `{"status":"confirmed","reservation_id": reservation_id}` immediately after accepted evidence;
- delete the synchronous `_read_request(getReservation)` and `_validate_cloudbeds_readback` call from this path;
- keep the validator available for the separate auditor.

**Step 2: Run GREEN and Bókun regression**

```bash
$PY -B -m pytest -q -p no:cacheprovider \
  tests/test_v2_cloudbeds_write_transport.py \
  tests/test_v2_bokun_write_transport.py
```

Expected: all pass; Bókun behavior unchanged.

**Step 3: Commit**

```bash
git add v2_adapters/provider_http.py tests/test_v2_cloudbeds_write_transport.py
git commit -m "fix: confirm accepted Cloudbeds submit immediately"
```

### Task 3: RED/GREEN — persist the raw Cloudbeds reservation ID privately

**Files:**
- Modify: `v2_contracts/providers.py`
- Modify: `v2_adapters/_provider_common.py`
- Modify: `v2_adapters/cloudbeds.py`
- Modify: `v2_application/reservations.py`
- Modify: `tests/test_v2_reservations.py`
- Modify: `tests/test_v2_bokun_write_transport.py` only if contract construction requires explicit assertions

**Step 1: Add RED tests**

Require:

- `ProviderExecutionResult` may carry a canonical raw provider reference only with `EFFECT_CONFIRMED` and a matching fingerprint;
- Cloudbeds persists `provider:cloudbeds:<reservationID>` in the private `ExecutionOutcome`;
- Bókun still persists `provider:bokun:<fingerprint-prefix>` and never the raw booking ID;
- public completion text contains neither reference.

**Step 2: Run RED**

```bash
$PY -B -m pytest -q -p no:cacheprovider tests/test_v2_reservations.py
```

**Step 3: Implement minimally**

- Add optional `provider_reference: str | None = None` to `ProviderExecutionResult` with strict canonical validation and certainty matrix.
- Add an explicit `persist_provider_reference` switch to `reservation_result`, default false.
- Enable it only in `CloudbedsReservationPort.execute`.
- In `V2ReservationExecutionAdapter`, use the raw reference only for provider `cloudbeds`; retain the existing fingerprint path for Bókun.
- Do not put the raw reference in evidence, public projection, logs, or reply text.

**Step 4: Run GREEN**

```bash
$PY -B -m pytest -q -p no:cacheprovider \
  tests/test_v2_reservations.py \
  tests/test_v2_completion_projector.py \
  tests/test_v2_bokun_write_transport.py
```

**Step 5: Commit**

```bash
git add v2_contracts/providers.py v2_adapters/_provider_common.py \
  v2_adapters/cloudbeds.py v2_application/reservations.py \
  tests/test_v2_reservations.py tests/test_v2_bokun_write_transport.py
git commit -m "feat: persist private Cloudbeds reservation reference"
```

### Task 4: RED/GREEN — GET-only durable audit with bounded retries

**Files:**
- Create: `v2_application/cloudbeds_audit.py`
- Modify: `v2_adapters/provider_http.py`
- Create: `tests/test_v2_cloudbeds_audit.py`

**Step 1: Add RED tests for the closed audit state machine**

Test:

- deterministic one-task projection from confirmed Cloudbeds outcomes;
- duplicate projection inserts zero rows;
- no task for unknown/no-effect/Bókun outcomes;
- auditor port surface has only GET audit capability;
- first GET not-visible/incomplete records retryable state;
- later exact GET records matched;
- conflicting principal ID records divergent without changing reservation outcome;
- max attempts are closed and no further GET occurs;
- lease expiry after `KeyboardInterrupt` permits only another GET;
- every row remains bound to command ID, persisted reservation ID, expected stable facts, and attempt count.

**Step 2: Run RED**

```bash
$PY -B -m pytest -q -p no:cacheprovider tests/test_v2_cloudbeds_audit.py
```

**Step 3: Implement the audit module**

Implement:

- strict `CloudbedsAuditStatus` and DTOs;
- `SQLiteCloudbedsAuditStore` in a separate absolute SQLite path with `journal_mode=WAL`, `synchronous=FULL`, strict identity checks, leases, bounded attempts, and idempotent enqueue;
- `CloudbedsAuditProjector` reading `execution.list_outcome_projection_inputs()`;
- `CloudbedsAuditWorker` that can call only a `get_reservation` protocol;
- `CloudbedsGETAuditTransport` in `provider_http.py`, with no POST method and strict bearer GET to `/api/v1.3/getReservation`;
- stable-fact validator for principal reservation ID, property, dates, party, amount/currency/status where present;
- retryable classification for bounded eventual-consistency observations; terminal divergence only in the audit DB.

The audit module must never call `record_outcome`, alter execution tables, or enqueue public completion.

**Step 4: Run GREEN**

```bash
$PY -B -m pytest -q -p no:cacheprovider tests/test_v2_cloudbeds_audit.py
```

**Step 5: Commit**

```bash
git add v2_application/cloudbeds_audit.py v2_adapters/provider_http.py \
  tests/test_v2_cloudbeds_audit.py
git commit -m "feat: audit Cloudbeds confirmations with GET-only retries"
```

### Task 5: RED/GREEN — wire audit into reconciliation without write capability

**Files:**
- Modify: `v2_host/settings.py`
- Modify: `v2_host/production.py`
- Modify: `tests/test_v2_production_composition.py`
- Modify: `tests/test_v2_settings.py`
- Modify: `tests/test_v2_worker_main.py` if result shape assertions require it

**Step 1: Add RED composition tests**

Require:

- audit SQLite path is deterministic and separate from execution/public/payment databases;
- `ReconciliationStage` projects then runs at most one GET audit attempt per cycle;
- stage opens/closes the audit store per cycle or otherwise proves lifecycle ownership;
- Cloudbeds audit is closed when writes/config are closed;
- no audit object exposes/carries `source_id`, idempotency key, or POST capability;
- reconciliation result reports audit counters without changing the existing reservation/payment recovery semantics.

**Step 2: Run RED**

```bash
$PY -B -m pytest -q -p no:cacheprovider \
  tests/test_v2_production_composition.py tests/test_v2_settings.py
```

**Step 3: Implement wiring**

- Add `cloudbeds_audit` to `V2Settings.sqlite_paths`.
- Configure a GET-only audit transport from Cloudbeds read credentials.
- Extend `ReconciliationStage` with optional projector/worker construction using the separate path.
- Run core expired-fence reconciliation independently of audit failures.
- Keep worker queue catalog unchanged; audit remains a substage of `RECONCILIATION`.

**Step 4: Run GREEN**

```bash
$PY -B -m pytest -q -p no:cacheprovider \
  tests/test_v2_production_composition.py tests/test_v2_settings.py \
  tests/test_v2_worker_main.py
```

**Step 5: Commit**

```bash
git add v2_host/settings.py v2_host/production.py \
  tests/test_v2_production_composition.py tests/test_v2_settings.py \
  tests/test_v2_worker_main.py
git commit -m "feat: reconcile Cloudbeds audits outside reservation completion"
```

### Task 6: RED/GREEN — fake E2E for eventual GET, crash, completion, and replay

**Files:**
- Create: `tests/test_v2_cloudbeds_monotonic_e2e.py`
- Modify: `tests/test_v2_e2e.py` only if shared qualification helpers are required
- Regression: `tests/test_v2_critical_actions.py`
- Regression: `tests/test_v2_completion_projector.py`

**Step 1: Write fake E2E RED tests**

Use real application/store/worker classes with only fake/httpx mock transports. Prove:

1. one lodging command and one dispatch slot;
2. accepted POST immediately records `EFFECT_CONFIRMED` and raw private ID;
3. completion projector inserts one final lodging reply before audit success;
4. audit GET returns not-visible, crashes once, then matches within closed GET budget;
5. POST count remains one through every retry/restart;
6. exact source/boundary replay is duplicate and reservation worker is idle;
7. second projection inserts zero public rows;
8. payment initiation rows/effects are zero;
9. no handoff/e-mail/cancellation rows or calls;
10. no second command, slot, provider POST, completion, or public outbox row.

**Step 2: Run RED**

```bash
$PY -B -m pytest -q -p no:cacheprovider tests/test_v2_cloudbeds_monotonic_e2e.py
```

**Step 3: Implement only missing seams**

Prefer test helpers and small production seams. Do not introduce provider credentials, sleeps, or network.

**Step 4: Run GREEN plus payment/Bókun regressions**

```bash
$PY -B -m pytest -q -p no:cacheprovider \
  tests/test_v2_cloudbeds_monotonic_e2e.py \
  tests/test_v2_cloudbeds_audit.py \
  tests/test_v2_cloudbeds_write_transport.py \
  tests/test_v2_reservations.py \
  tests/test_v2_completion_projector.py \
  tests/test_v2_critical_actions.py \
  tests/test_v2_bokun_write_transport.py \
  tests/test_v2_e2e.py
```

**Step 5: Commit**

```bash
git add tests/test_v2_cloudbeds_monotonic_e2e.py tests/test_v2_e2e.py
git commit -m "test: prove monotonic Cloudbeds fake E2E"
```

### Task 7: Local qualification and final candidate freeze

**Files:**
- Modify only if a test/gate exposes a causal defect.
- Create: `docs/superpowers/plans/2026-08-01-cloudbeds-next-real-canary.md`

**Step 1: Focused blast radius**

Run Task 6 GREEN command and all changed-file related tests.

**Step 2: Full canonical suite**

Use the repository’s official command and only the seven pre-existing documented deselections, if still required by the exact HEAD. Record exact counts and duration.

**Step 3: Static gates**

```bash
$PY -B -m ruff check <all changed Python files>
$PY -B -m compileall -q v2_adapters v2_application v2_contracts v2_host reservation_execution
git diff --check
bash scripts/check_fasttrack_boundaries.sh
$PY -B scripts/static_gate.py
```

Use exact repository script names discovered at execution time; do not invent substitutes if paths changed.

**Step 4: Write next-canary plan**

Plan only, no execution. Require:

- new SHA/digest/state/Hermes home;
- fake gates and read-only provider probe first;
- one allowlisted lead and one-room lodging-only pending action;
- budget physically disarmed until contextual confirmation;
- explicit fresh user authorization before arming;
- exactly one POST budget;
- accepted submit → immediate confirmed ledger/reply;
- GET audit independently allowed to retry;
- replay duplicate/idle;
- zero payment/handoff/e-mail/cancellation;
- rollback to dark read-only;
- do not touch historical reservation `2547077136052`.

**Step 5: Freeze final commit**

```bash
git add -A
git commit -m "fix: make Cloudbeds submit confirmation monotonic"
FINAL_SHA=$(git rev-parse HEAD)
FINAL_TREE=$(git rev-parse HEAD^{tree})
test -z "$(git status --porcelain)"
```

No edits are allowed after recording `FINAL_SHA` without creating a new final commit and rerunning candidate-scoped gates.

### Task 8: Push, CI, independent review, and OCI qualification

**Step 1: Push exact candidate**

```bash
git push origin maya-v2-operational-readiness
```

Verify remote branch equals `FINAL_SHA`.

**Step 2: Independent review**

Delegate a read-only/local review on exact `FINAL_SHA`/tree with no network/provider/model, requiring:

- explicit `APPROVE` or `NEEDS_FIX`;
- Critical/Important/Minor counts;
- causal witnesses with file:line;
- confirmation-boundary, raw-ID privacy, audit GET-only, crash/replay, payment separation, and Bókun scope.

If `NEEDS_FIX`, fix on a descendant SHA and repeat all candidate-scoped gates.

**Step 3: CI**

Use GitHub tooling/API to identify the run for exact `FINAL_SHA`. Require:

- run conclusion `success`;
- jobs `test`, `image`, `gate` all `success`;
- `head_sha == FINAL_SHA`.

**Step 4: OCI**

Resolve the immutable image digest for exact `FINAL_SHA`, pull/inspect it, and verify:

- revision label equals `FINAL_SHA`;
- hashes of key changed files match the worktree;
- fake-only qualification inside the OCI passes without network.

**Step 5: Stop**

Do not deploy and do not issue a real Cloudbeds POST. Final report must include:

- final SHA/tree;
- OCI digest;
- focused/full/static test evidence;
- independent review verdict;
- CI run/jobs;
- fake E2E invariants;
- path to next-canary plan;
- `SAFE_FOR_BROAD_ROLLOUT=false` pending the separately authorized real canary.
