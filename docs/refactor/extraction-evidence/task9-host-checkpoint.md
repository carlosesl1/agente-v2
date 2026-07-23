# Task 9 final checkpoint — standalone V2 host, signed E2E and image

Date: 2026-07-23
Status: FUNCTIONALLY COMPLETE; rollout remains `NO-GO`
Functional candidate commit: recorded by the following control commit in `docs/refactor/ACTIVE.md`

## Final runtime

- `v2_host` is the only productive composition root.
- API and worker are separate roles from one image.
- API owns only the durable inbox; financial followup UOWs are request-scoped.
- Worker owns one boundary, execution, followup, payment-initiation and legacy completion-public-outbox instance, plus the shared inbox path.
- The conversation path uses one tool-free audited Hermes exchange, the pure V2 reducer and one `commit_turn_v8` publication.
- Reservation and handoff relays use canonical Phase 5/6 bundles and deterministic target operation receipts.
- Public turn delivery uses the v8 public outbox, allocation fence and terminal/manual-review authority states.
- Stripe, Wise and Pix evidence routes authenticate raw bytes before normalized event decoding.
- Worker composition requires exactly seven concrete stages; noop/fallback and arbitrary dynamic factories are rejected.

## Mandatory signed scenarios

The final runner executes exactly:

1. lodging + Stripe;
2. activity + Pix;
3. package + Wise.

Each scenario:

- posts an authenticated ManyChat payload and its replay;
- processes one durable inbox lease through the seven-stage qualification cycle;
- executes reservation/payment/public effects only against counted local fake providers;
- posts provider-specific HMAC-authenticated financial evidence and its replay;
- drains settlement and post-payment effects to idle;
- proves one provider call per idempotency identity;
- proves the expected owner counts and closed real-effect gates;
- derives completion from durable workflow/outbox state without a parallel completion database.

Host runner result:

```json
{
  "exit_code": 0,
  "providers": "fake_only",
  "real_effects": false,
  "scenarios": ["lodging_stripe", "activity_pix", "package_wise"],
  "signed_webhooks": true,
  "status": "passed",
  "worker_queues": 7
}
```

```text
3 passed, 1 warning in 4.10s
```

## Repository gate

Expanded final suite:

```text
1015 passed, 3 deselected, 1 warning, 2924 subtests passed in 209.40s
```

Explicit historical exclusions:

1. `tests/test_phase7_package.py` — the historical wheel contract is pinned to `0.7.0`, while the current package is `0.8.0`.
2. `tests/test_phase7_closeout.py::Phase7EntryContractTests::test_wheel_bootstrap_is_closed_and_stdlib_only` — same historical `0.7.0` pin.
3. `tests/test_phase7_closeout.py::Phase7CloseoutContractTests::test_evidence_validator_reflects_current_terminal_artifacts` — the historical validator intentionally requires Phase 7 closed imports/package metadata and reports `closed_imports` plus `package_version` after the V2 host was added.
4. `tests/test_phase8_entry.py::Phase8EntryTests::test_phase_index_keeps_slice_zero_and_rollout_closed` — expects the pre-fast-track README phrase `design aprovado`; the current canonical index says fast-track architecture/plan active.

The regenerated Phase 6 and Phase 7 manifests/checksums pass their exact current-manifest tests. No historical package version or README phrase was falsified to hide the exclusions.

Static gates:

```text
ruff critical checks: PASS
fasttrack-boundaries: OK
compileall: PASS
phase6 manifest check: PASS
phase7 manifest check: PASS
git diff --check: PASS
secret scan: no private-key/credential matches
```

## Final image qualification

Image:

```text
agente-v2:task9-final
sha256:47c1c2779cc121be9deb825e32e39bc3a592c74c886eb777c5edfa771779d43f
```

The signed runner passed inside the image under the final security envelope:

```text
3 passed, 1 warning in 3.04s
uid=10001
gid=10001
/app read-only
cap-drop=ALL
security=no-new-privileges
providers=fake_only
real_effects=false
signed_webhooks=true
worker_queues=7
```

No container using the final image remained active after qualification.

## Operational status

Implementation and local qualification are complete. The following remain blocked pending explicit approval:

- push/merge;
- deploy or restart;
- real Cloudbeds/Bókun/Stripe/ManyChat writes;
- public ManyChat traffic;
- shadow/canary rollout.

Task 9 completion does not authorize rollout; `ACTIVE.md` retains `NO-GO` and closed provider-write/public-message gates.
