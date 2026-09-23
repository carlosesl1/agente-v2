# Native Stripe TEST receipt and settlement

## Boundaries

`POST /webhook/payments/stripe/{business_unit}` accepts only configured `hostel` or `agency` accounts. It is distinct from the existing normalized internal webhook and any legacy `/webhook/stripe` route. Do not redirect a legacy or live-account endpoint to it.

The API verifies the native timestamped signature, TEST mode, exact account, checkout session and PaymentIntent through authenticated GETs. It correlates the Payment Link with the encrypted issuance result and every durable product/price/link receipt. Unit, local reservation command/outcome, economic version, receiver profile, price, paid currency and exact quantity must agree. Selected EUR/USD amounts are authenticated against the issued Price; the provider obligation remains its recorded BRL amount, not a new conversion at settlement time.

The hosted checkout acceptance records the financial summary/confirmation in the existing follow-up owner. The canonical `payment_intent.succeeded` event obtained from Stripe owns the global claim, so repeated native notifications do not credit the reservation twice. A checkout that overtakes local offer publication retries instead of being acknowledged and lost.

The settlement worker reads the reservation immediately before dispatch. Only a still-payable Cloudbeds `confirmed`/`checked_in` or Bókun `RESERVED`/`CONFIRMED` booking can pass. This initial-prepayment path does not blindly add another credit where the provider already reports paid funds. Cancelled, aborted, checked-out, unknown or divergent states do not receive a payment or reservation reactivation.

Cloudbeds normalizes both origin-only and historically version-prefixed configured URLs to its qualified PMS v1.3 contract. Missing reservation currency is resolved through authenticated `getCurrencySettings`, never an assumed BRL fallback. Real GETs verified the property currency and enabled custom method. It uses form-encoded `postPayment`, authenticated property identity and the enabled custom method `Cartãodecrédito`. Acceptance requires `success=true`, `paymentID` and `transactionID`. Bókun uses its signed REST-v1 confirm-with-payment contract, BRL amount and the exact PaymentIntent reference; no customer email is triggered. A confirmed response must contain the matching payment record. Both physical writes disable redirects, use the existing permanent dispatch fence and authenticate the current permit/lease. Ambiguous results are not automatically retried.

Payment completion is a factual event for Maya, not canned public prose. Human handoff is opened through the existing coordinator on terminal settlement failure. Existing postpayment jobs observe the actual paid state, handoff queue and bound ManyChat API-acceptance receipts; they do not send a second message or fabricate delivery. Internal email and booking-form delivery are not certified by this change.

## Configuration

API only:

- `V2_STRIPE_NATIVE_ACCOUNTS_JSON`: map keyed by business unit. Each value has exactly `profile_id`, `account_id`, `api_key`, `webhook_secret`.
- `V2_STRIPE_NATIVE_RESULT_KEY_HEX`: the same 32-byte encryption key used by the issuance owner. Do not generate another key for existing state.
- Each account key must be TEST-only. Distinct units must name distinct account IDs.
- Native ingress does not receive provider-write credentials or execute provider POSTs.

Worker only:

- `V2_ENABLE_STRIPE_SETTLEMENT=true` explicitly enables the capability.
- Existing productive runtime mode, provider-write flags, Stripe TEST link issuance, ManyChat delivery and handoff, acknowledgement, kill switch, write window and durable ownership still apply.
- Existing state mounts and account profile IDs remain authoritative. No new financial ledger or active DB edits.

Stripe permissions verified by authenticated reads: Accounts **Read**, Events **Read**, Checkout Sessions **Read**, Payment Intents **Read**, Payment Links **Read**, Prices **Read**. Provisioning also needs webhook endpoint management. Keep the existing issuance permissions. Subscribe only to `checkout.session.completed` and `checkout.session.async_payment_succeeded` in the matching TEST account, with separate signing secrets and exact unit URLs.

## Qualification and activation status (2026-09-23)

- Causal synthetic-producer tests cover native HTTP acceptance/replay, malformed signatures, ownership/value/currency mismatches, EUR/BRL handling, initial deposit, cancellation, permanent one-POST behavior across restart, timeout, redirect, expired permit, handoff and genuine channel acceptance bytes.
- Final isolated full suite on functional candidate `1341cd8`: **2446 tests and 2958 subtests passed**. The immutable source-bound image passed **130 tests and 6 subtests**, with network disabled and the runtime code loaded from the image rather than a source bind mount. The configured-base/currency correction passed its three causal regressions within **37 focused tests**. Focused boundary regression: **71 tests and 6 subtests passed**. The earlier broad run identified layer-placement and generated-manifest failures; both were corrected and the final full run supersedes it.
- Authenticated replay of the operator's actual Stripe TEST receipt, using SQLite API backups and a transport that physically rejects every non-GET: accepted, duplicate on re-delivery, cancelled reservation rejected before dispatch, **zero live state writes and zero provider POSTs**. This is not a real webhook delivery or a live settlement.
- External activation is pending: the configured Agency TEST key returns `403 more_permissions_required` for Accounts Read (`connected_account_read`); the other six required read probes return 200. Hostel passes all seven read probes. No missing permission was bypassed.
- No webhook endpoint was provisioned or redirected, no payment was replayed into active state, no provider was credited, and no GA/Ops/contact configuration was changed during qualification.

Activation requires rechecking the account permission, exact source/image identity, authoritative isolated deployment, exact proxy paths, real signed delivery, and a separately authorized **active** reservation journey. Do not reuse the cancelled historical reservation to claim end-to-end payment success. Keep code qualification, image qualification, endpoint activation, provider settlement and customer communication as separate evidence levels.
