# V2 Production Composition Closure Plan

**Goal:** make `v2_host` the only executable composition root for the standalone runtime, with real read transports, explicit closed write capabilities, truthful readiness, native webhook verification, CI, and an immutable image candidate.

**Safety boundary:** all provider writes and public ManyChat delivery remain disabled. No merge, public deploy, reservation, payment, or message is authorized by this plan.

## RED — executable contracts

1. Add `tests/test_v2_production_composition.py` proving:
   - the allowlisted default worker factory is productive, not the qualification factory;
   - the factory builds every `WorkerQueue` from a real `V2Container`;
   - read-only mode creates real Cloudbeds/Bókun/Hermes/profile adapters while write/delivery workers stay closed without claiming durable jobs;
   - missing model/read credentials make worker readiness `not_ready` with closed reason codes;
   - worker-cycle failures are emitted through structured logging and make runtime health degraded.
2. Add `tests/test_v2_provider_http_transports.py` with representative Cloudbeds, Bókun, ManyChat, Stripe and reservation HTTP contracts. Assert authentication, canonical request binding, no secret/raw body leakage, timeout classification, and idempotency headers.
3. Extend ManyChat delivery tests so `ManyChatDeliveryAdapter` accepts the actual `PublicDispatchClaim` shape.
4. Add `tests/test_v2_native_financial_webhooks.py` for Stripe's native `Stripe-Signature`, Wise RSA signatures, and Pix HMAC envelopes; reject stale/replayed/invalid native payloads before normalization.
5. Extend host/readiness tests so `/readyz` cannot be `200` merely because SQLite opened.

## GREEN — operational composition

1. Extend `V2Settings` with exact runtime-mode/capability configuration and secret-only credentials. All write gates remain two-key fail-closed.
2. Add `v2_adapters/provider_http.py`:
   - direct Cloudbeds read transport and response normalization;
   - direct signed Bókun read transport and response normalization;
   - ManyChat profile/read and public delivery transport;
   - direct provider-write/Stripe transports present but unreachable while gates are closed.
3. Add `v2_host/production.py` to construct:
   - Cloudbeds/Bókun adapters and `V2ReadService`;
   - `HermesModelAdapter` and private ManyChat profile adapter;
   - `V2TurnExecutor`/`InboxTurnWorker`;
   - boundary relay, reservation, payment, settlement, post-payment, public-delivery, and reconciliation stages;
   - explicit closed-capability stages for disabled effects that never claim or fence work.
4. Make `worker_main` select the production factory by default, keep qualification factory explicitly opt-in, log every failed cycle, and expose a durable worker heartbeat/readiness document.
5. Make API readiness depend on authenticated ingress/webhook capability; make worker readiness depend on a successfully built production graph and required read/model capabilities.
6. Add native financial webhook verifiers/normalizers without weakening existing internal canonical evidence contracts.

## CI and immutable artifact

1. Add `.github/workflows/phase8.yml` running tests, Ruff, compile, boundary guards, manifest checks, and `git diff --check`.
2. Add `.dockerignore`; install only runtime dependencies and pin Hermes Agent in `Dockerfile.v2`; exclude tests/docs/git/cache from the runtime stage.
3. Build the image with OCI labels for source revision and run as `10001:10001`, read-only rootfs, dropped capabilities, no-new-privileges.
4. Tag the exact image by source SHA and record its local image/config digest. Push to a registry only when authenticated and without changing any deployment.

## Verification

1. Run focused RED/GREEN tests after each slice.
2. Run the complete active suite and all static/manifest guards once on the integrated candidate.
3. Start API and worker containers from the exact image with all effects closed.
4. Validate health/readiness, real Cloudbeds/Bókun reads, durable ingress replay across restart, worker heartbeat, and absence of network writes/public delivery.
5. Stop the previous canary before replacement; preserve only the durable evidence directory.
6. Commit and push the branch normally. Do not create/merge a PR or deploy unless separately authorized and authenticated.
