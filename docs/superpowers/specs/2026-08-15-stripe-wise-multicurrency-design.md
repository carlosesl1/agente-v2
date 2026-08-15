# Stripe Wise Multi-Currency Design

**Date:** 2026-08-15
**Scope:** Maya V2 payment-link creation for both agency/Bókun and hostel/Cloudbeds Stripe accounts.

## Goal

Offer BRL, USD, and EUR in the same Stripe Payment Link while preserving the provider obligation as one immutable BRL amount.

## Confirmed V1 behavior

V1 obtains rates from Wise using authenticated read-only requests:

```text
GET https://api.wise.com/v1/rates
Authorization: Bearer <WISE_API_TOKEN>
source=BRL
target=USD or EUR
```

For each target, Wise returns the amount of target currency for one BRL. V1 converts it to BRL per foreign unit and applies a fixed business adjustment:

```text
adjusted_brl_per_foreign = (1 / wise_brl_to_foreign_rate) - 0.20
foreign_amount = canonical_brl_amount / adjusted_brl_per_foreign
```

Amounts are rounded to Stripe minor units with `ROUND_HALF_UP`.

## V2 design

### Rate source

Add a small Wise exchange-rate reader owned by the Stripe transport. It receives a dedicated `V2_WISE_API_TOKEN`, calls the canonical Wise API origin, and accepts only positive decimal rates for exact routes `BRL→USD` and `BRL→EUR`.

The V2 deployment may initialize its dedicated variable from the same operational Wise credential currently used by V1, but V2 configuration remains explicit and independently validated.

### Stripe Price payload

At each Payment Link creation, before creating the Stripe Price:

1. Fetch current BRL→USD and BRL→EUR rates from Wise.
2. Calculate adjusted BRL-per-foreign rates using `-0.20 BRL`.
3. Convert the canonical payable BRL minor amount to USD and EUR minor amounts using `ROUND_HALF_UP`.
4. Create one Stripe Price with:

```text
currency=brl
unit_amount=<canonical BRL minor amount>
currency_options[usd][unit_amount]=<converted USD minor amount>
currency_options[eur][unit_amount]=<converted EUR minor amount>
```

BRL remains the default and canonical currency. Quantity remains exactly one.

### Metadata and reconciliation

The Payment Link metadata binds:

- `currency_base=BRL`
- `amount_brl_cents`
- `amount_usd_cents`
- `amount_eur_cents`
- `cambio_usd_brl`
- `cambio_eur_brl`

Rates are serialized with eight decimal places. Foreign values and rates are audit evidence only. Any eventual provider payment write must use the immutable canonical BRL amount, not a reverse conversion of the paid foreign amount.

The existing display-details hash and journal bindings must include the exact multi-currency quote evidence so retries and reconciliation cannot silently accept a different rate or foreign amount under the same economic version.

### Failure behavior

Fail closed before creating a Stripe Price when:

- the Wise token is missing;
- Wise is unavailable, times out, rejects authentication, or returns a malformed envelope;
- either exact route is absent;
- a raw or adjusted rate is non-positive;
- conversion yields a non-positive minor-unit amount.

No hard-coded or stale exchange-rate fallback is permitted. A Stripe Product may have been accepted before a Wise failure; the existing journal/reconciliation flow must treat that as a partial chain and safely resume with the same bound quote. To avoid rate drift after a Product acceptance, the quote must be obtained and bound before any Stripe create call.

### Configuration and readiness

Add worker-owned settings:

- `V2_WISE_API_TOKEN`
- `V2_WISE_BASE_URL=https://api.wise.com`
- fixed adjustment in code: `-0.20 BRL` to preserve the approved V1 business rule.

When Stripe links are enabled, worker readiness requires a Wise token and canonical Wise API origin. API/router roles do not need the token unless they compose the payment worker.

## Testing

Tests must prove:

1. exact authenticated Wise requests for BRL→USD and BRL→EUR;
2. the `-0.20 BRL` formula and `ROUND_HALF_UP` minor-unit conversion;
3. Stripe Price form contains BRL, USD, and EUR for hostel and agency obligations;
4. Payment Link metadata contains canonical BRL, alternatives, and eight-decimal rates;
5. malformed/missing Wise data fails before any Stripe create call;
6. retry/reconciliation bindings include the immutable quote evidence;
7. no secret or raw Wise payload is returned or logged;
8. current single-currency regressions are updated without weakening test-mode, account-routing, idempotency, or canonical-URL guards.

## Deployment

Build and publish a new immutable image, preserve the current digest as rollback, inject `V2_WISE_API_TOKEN` without printing it, promote API/worker/router consistently, and verify health plus an isolated mocked-provider smoke. Do not create a real Stripe link during deployment verification.
