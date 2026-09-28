# Production payments implementation plan

> Execute inline with causal TDD, without subagents. User authorization: prepare and enable the complete V2 in production; forms explicitly deferred for the user's subsequent specification.

**Goal:** Enable an explicitly selected Stripe live environment end to end without weakening TEST isolation, and prepare Pix/Wise documentary settlement using the already-approved policy.

**Architecture:** Reuse current account-bound Stripe transport, reconciliation, native ingress, durable payment owner and settlement workers. A single explicit `StripeEnvironment` determines the expected credential prefixes and exact `livemode` boolean at all Stripe boundaries. Existing API/worker role separation, fences, pending-state ownership and model authorship are unchanged. No new payment ledger, bank API, conversational classifier or form sender.

**Baseline:** GA source 6b9f8887546bfc9a07d0746e6b98c5ff54de018d, verified through ACTIVE_RUNTIME.json. Worktree `.worktrees/production-payments-727d3625`; branch `feat/v2-production-payments-727d3625`.

## 1. Mode closure
- [ ] Add causal tests in `tests/test_v2_stripe_live.py`: GA live configuration with live credentials, wrong-mode key rejection, TEST default preservation, creation and GET-only reconciliation in both modes, mode mismatch/absence/nonboolean rejection.
- [ ] Extend `tests/native_stripe_helpers.py` to generate explicitly labelled fake TEST/LIVE Stripe responses using MockTransport, not external requests.
- [ ] Extend current `v2_adapters/stripe.py` classes with an explicit keyword-only `livemode=False`; validate exact bool and mode-matched keys; authenticate all Product/Price/Link responses and reconciled responses against this mode.
- [ ] `v2_host/settings.py`: permit explicit LIVE for GA, preserve controlled TEST-only; validate issuing/native keys by mode. `v2_host/production.py` and `api_main.py`: propagate mode to every Stripe owner.
- [ ] `v2_application/native_stripe.py`: validate account keys, signed checkout envelope and all authoritative GET observations in the selected mode; retain claim/replay and unit/account correlation.
- [ ] Test native HTTP accepted→duplicate and mismatch→no financial state in both modes and both business units.

## 2. Qualification and activation prerequisites
- [ ] Run affected tests with `env -i`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, explicit `HERMES_LEADS_AGENT_CONFIG_PATH`, then full current suite once and boundary guard. Do not count historical Git-package harness failures as passes.
- [ ] Review exact diff inline; regenerate official package manifest if required; freeze and commit explicit paths.
- [ ] Build immutable image and prove module origin, productive composition and candidate→predecessor→candidate open on private SQLite API backups with no network or dispatch.
- [ ] Authenticate live account credentials and native webhook secrets via account-bound GETs. No real booking/charge/payment test without an explicit commercial subject. Missing live credentials are an activation blocker, not permission to reuse TEST credentials.
- [ ] Pix/Wise: use confirmed structured beneficiary config matching the durable instruction receiver profiles; do not infer bank identities from free-form instructions. Enable existing V9/visual-proofs and existing settlement workers only with complete configuration.

## 3. Publication
- [ ] Reuse control-plane promotion helpers; successor authority/pointers/rollback prepared before cutover. Preserve live state and do not drain historical pending financial commands against another account/mode.
- [ ] Only GA changes. TEST and read-only Ops remain intact. Forms remain disabled.
- [ ] Verify `/readyz`, worker heartbeat/queues, runtime authority, exact environment mode, native endpoint authentication, configuration completeness and unaffected containers. Distinguish operational activation from real financial/channel E2E.

## Known discovery
The active V2 deployment configurations inspected contain Stripe TEST keys only and no structured visual-proof receivers. Any missing production credentials or receiver confirmation must be supplied through a private file, not invented or copied from another product. Technical readiness and full production activation will be reported separately.
