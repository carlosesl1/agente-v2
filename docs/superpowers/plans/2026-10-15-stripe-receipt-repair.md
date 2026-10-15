# Stripe receipt-only recovery implementation plan

> **For agentic workers:** Execute inline using `executing-plans`; no subagents.

**Goal:** Restore four existing Stripe callbacks without restarting conversational workers, migrating financial state, replaying events or settling payments.

**Architecture:** The existing identity-verified router gains an opt-in Stripe raw-body relay to the same API origin already selected for its ManyChat upstream. The API remains unready for conversations while its worker is suspended; callbacks use the existing native ingress, persisted ownership and signature/account/mode verification. Native ingress ignores authenticated, provider-retrieved Checkout Sessions with no Payment Link because this issuer only creates Payment Links. No new service, ledger, queue or agent.

**Tech Stack:** Existing FastAPI/httpx relay, native Stripe ingress, SQLite owners, Docker digest deployment and canonical runtime authority.

## Global constraints
- User authorized correcting Stripe webhooks; no financial migration was authorized. The unanswered architecture question is not additional authorization. Choose only the minimal receipt repair within the original request.
- Base is verified GA/TEST source `346cf3331cce473c3c880add381f82ca1e48fa82`, never main, CW18 or V3. Pilot CW18, Humano controls, OAuth and jobs remain unchanged.
- Original URLs, account/signing secrets, live/test separation, state mounts and result encryption keys remain unchanged.
- Both old workers remain exited, including throughout cutover and rollback; never invoke an unqualified compose `up`.
- Keep API `/readyz` 503 and authority-declared API health unhealthy while workers are suspended. Router `/readyz` authenticates its exact image identity. No fake heartbeat or liveness substitution.
- No historical POST/replay, bank/provider settlement, booking, charge, refund or customer message. Stripe GET only except ordinary incoming callbacks. Local probes are invalid signatures or signed non-payment synthetic events, never synthetic paid evidence.
- Active SQLite is read/backup only outside normal authenticated receipt processing. No manual data edits or migration.

## Task 1 — Account-wide foreign checkout handling
Files: `v2_application/native_stripe.py`, `tests/test_v2_stripe_receipt_repair.py`.
- [ ] Test signed live/test checkout without a Payment Link against a controlled provider GET; expect `NativeStripeIgnored`, no receipt/evidence/settlement changes.
- [ ] Preserve bad signature/mode/account/amount rejection; an empty or malformed non-null link remains invalid.
- [ ] After provider session retrieval and event consistency checks, add `if session.get('payment_link') is None: raise NativeStripeIgnored('checkout not issued through a Payment Link')`.
- [ ] Run red then green tests and existing native ingress/idempotency/settlement regression.

## Task 2 — Receipt relay independent of conversational readiness
Files: `deploy/canary_router.py`, `tests/test_v2_stripe_receipt_repair.py`.
- [ ] Test default closed, exact hostel/agency routes, identity failure, streaming body limit, preserved signature/body/status, upstream failure with no fallback/retry, and no effect on ManyChat readiness.
- [ ] Add explicit boolean `stripe_receipts_enabled=False`, configured via `CANARY_STRIPE_RECEIPTS_ENABLED=1`. Reject malformed opt-in values.
- [ ] Derive fixed Stripe target from the existing V2 origin, never user input or legacy. Require same private HTTP authority and preserve raw bytes and Stripe-Signature only; do not forward ManyChat credentials.
- [ ] Forward at most once; never parse/normalize JSON in relay. Check identity before reading/forwarding and immediately before outbound request. Native ingress owns signature/account/mode/business correlation.
- [ ] Run focal plus canary, native financial, live/test and runtime identity tests; run `python3 scripts/check_fasttrack_boundaries.py` and `git diff --check`.

## Task 3 — Immutable qualification and receipt-only deployment
Artifacts outside Git: `/home/ubuntu/workspace/maya-chatwoot-deploy/audits/20261015-stripe-repair/` and versioned deployment bundle.
- [ ] Freeze source; build candidate from exact archive, immutable predecessor image/dependencies, authenticated revision labels and local registry digest.
- [ ] Execute tests against installed image modules with tests/dependencies only mounted; network none.
- [ ] Take SQLite online backups and table fingerprints, container/config/authority snapshots, and protected predecessor rollback bundle.
- [ ] Build per-component compose/env/identity candidate: change source/image expectations, move only Stripe Traefik router/service labels from API to the already-running router, and enable receipt relay. Preserve other labels, networks, mounts, secrets and gates exactly. Keep stopped worker command non-executable during image recreation if needed; never run worker_main.
- [ ] Validate candidate and practice receiver/rollback on isolated state copies without provider/channel credentials or external network.
- [ ] Reverify active authority. Publish only API/router, preserve worker suspension, update canonical manifest atomically once exact runtime is verified; retained predecessor state is the rollback target.

## Task 4 — External verification and closeout
- [ ] Four external paths must reach receiver: unsigned/invalid signature returns 401, not 503; signed irrelevant event returns 200 ignored, no state writes.
- [ ] Verify live/test and hostel/agency signing-key isolation, raw-body integrity and missing/unsupported handling. No fake paid events.
- [ ] Recheck Stripe endpoint settings by authenticated GET, canonical runtime authority, conversational API unready, workers exited, pilot identity/Humano/jobs and all preexisting financial rows.
- [ ] Record actual observed results, intermediates and limitations. Historical deliveries still require endpoint-level reconciliation before any re-send; no claim of paid checkout or financial E2E from these tests.
