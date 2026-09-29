# Stripe production mode and documentary payments

`V2_STRIPE_ENVIRONMENT=test` remains the default. `live` is explicitly supported
for general availability, with mode-matched restricted/secret API keys. The
isolated `controlled_write` TEST runtime still rejects `live`.

## Stripe contract

The existing Product → Price → Payment Link writer, GET-only reconciler and
account-scoped native webhook consume the same explicit mode. Every authoritative
Stripe response must carry the exact expected boolean `livemode`; missing values,
integers, mixed modes and foreign account/obligation bindings fail closed. TEST
transport class names are retained for backward compatibility, not as a second
runtime implementation. Existing fences, receipts, global claims and no-repeat
behavior for ambiguous effects are unchanged.

The productive API uses `/webhook/payments/stripe/{hostel|agency}`. Each unit needs
its own native account binding (`profile_id`, `account_id`, `api_key`,
`webhook_secret`) in `V2_STRIPE_NATIVE_ACCOUNTS_JSON`. The API's
`V2_STRIPE_NATIVE_RESULT_KEY_HEX` must equal the worker's existing payment-result
store encryption key, not a newly generated unrelated key.

Native verification reads the authenticated account, checkout session, payment
intent, Payment Link, Price, line items and canonical success event. A restricted
key therefore needs **Accounts Read** (`connected_account_read`) in addition to
reads on those resources. Issuance separately requires Product, Price and Payment
Link writes. Webhook administration is an operator/deployment action, not a model
tool; no secret key or webhook signing secret belongs in Git or user-facing logs.

Before activation:

1. Verify ACTIVE_RUNTIME.json and source/image identity.
2. Authenticate both live account identities with `GET /v1/account`; a `403` is a
   missing permission, not permission to omit the check.
3. Establish account-specific native webhook endpoints/secrets. Do not reuse
   TEST signing secrets or redirect unrelated integrations.
4. Check existing live subscribers. Legacy workflows must ignore V2-issued
   metadata; inspect the **published** workflow version, not just its draft.
5. Reconcile pending TEST issuance/settlement before changing mode/account
   profiles. Preserve encryption keys and state, and never send an unresolved
   predecessor command to a new account.
6. Qualify productive composition and rollback using SQLite API backups with
   network disabled and zero worker cycles.
7. Publish via the runtime authority control plane and verify `/readyz`, worker
   queues, effective mode, routes, account/profile mapping and both settlement
   adapters. Code/image readiness alone is not financial activation.

## Receiver rotation and historical replay

Configured receiver profiles apply only to the first projection of an obligation.
Replaying an already-projected reservation preserves its authenticated payment
selection, including the original TEST/LIVE receiver. Ambiguous selections or
changes to the other economic facts are rejected. The activation gate must run
the outcome projector on state copies and prove that no payment is enqueued.
This pure projection check is separate from provider/dispatch worker cycles.

## Pix/Wise

The already-implemented visual-proof contract uses V9, explicit structured
beneficiaries in `V2_VISUAL_PROOF_RECEIVERS_JSON`, a retained proof archive,
`V2_ENABLE_VISUAL_PROOFS=true`, and the existing settlement/handoff dependencies.
Maya extracts the documentary observations; the runtime validates binding,
beneficiary, amount/currency, transaction identity, date and non-reuse before the
provider effect. This is documentary approval with later human review, **not**
bank-API settlement verification. Reading an image/PDF is not payment approval.

Use operator-confirmed structured recipient identifiers/names per business unit
and method. Do not silently infer them from customer/public instruction prose.

## Forms explicitly deferred

No form sender or catalog is enabled by this change. Payment confirmation remains
a factual input to the same Maya/public-delivery path. The operator will specify
form requirements separately. Do not import a legacy workflow's classification,
phone-language rules, links or sending logic as an assumed requirement.

## Qualification scope

`tests/test_v2_stripe_live.py` uses simulated HTTP endpoints and temporary SQLite
owners. Native/settlement regression tests cover both modes, replay, reopen,
terminal reservations, lost responses and handoff. These are **not** real charged
transactions. Report real API read preflights and any later real financial/channel
exercise as distinct evidence.
