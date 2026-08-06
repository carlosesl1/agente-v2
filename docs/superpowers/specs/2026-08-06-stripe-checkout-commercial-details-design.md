# Stripe checkout commercial details — design

**Date:** 2026-08-06
**Status:** approved by timeout/default decision
**Scope:** Maya V2 Stripe test-link creation for lodging, activity, and package components

## Problem

The Stripe Checkout page currently receives a generic Product name such as `V2 agency reservation payment` and the payable amount. A customer cannot identify the booked service, service date or stay period, party, whether the charge is a deposit or full payment, or the relationship between the charge and the reservation total.

The confirmed reservation command already contains the signed commercial source of truth: service kind, public offer label, dates, optional time, party, and reservation total. These values are currently discarded when the reservation outcome is projected into a payment obligation. Provider references remain private because some adapters intentionally persist only technical fingerprints.

## Goals

1. Make every new Stripe Checkout link self-explanatory for standalone lodging, standalone activity, and each component of a package.
2. Derive presentation only from the confirmed signed command and configured payment percentage, after the provider outcome has confirmed the reservation.
3. Exclude customer name, email, phone, country, birth date, gender, passenger manifest, and all other PII.
4. Preserve one-shot payment effects, deterministic idempotency, durable replay, and compatibility with payment rows created before this feature.
5. Avoid language inference. Fixed labels are compact PT/EN where useful; the signed `public_label` is preserved as the service name.

## Non-goals

- Do not create, update, deactivate, or replace existing Stripe links.
- Do not create reservations or execute provider traffic during implementation tests.
- Do not change payment percentages, prices, booking logic, checkout branding, or ManyChat copy.
- Do not add a semantic language detector or infer language from free text.
- Do not include package-wide combined payment links; package components remain separately payable.

## Considered approaches

### 1. Static labels in the Stripe adapter

Changing only the generic label to `Tour payment` or `Accommodation payment` is low risk but does not carry dates, party, package context, or total-versus-due information. Rejected as insufficient.

### 2. Immutable payment display details carried with the obligation — selected

A closed `PaymentDisplayDetails` value is derived while the projector still has the authenticated reservation command and outcome. It is serialized with the payment selection and copied into the Stripe request. The Stripe adapter formats and submits the Product name and description without querying conversation or reservation stores.

This preserves isolation and replay: the worker receives all effect inputs in one immutable, hashed selection.

### 3. Stripe adapter queries reservation execution state

This would avoid expanding payment contracts but couples the payment effect to another SQLite store and makes crash recovery and isolated workers dependent on extra mutable state. Rejected.

## Contract

Introduce an immutable `PaymentDisplayDetails` value with these non-PII fields:

- `service`: exact lodging/activity enum;
- `public_label`: signed normalized offer label;
- `start_date`;
- `end_date` for lodging, otherwise absent;
- `start_time`, optional;
- `adults` and `children`;
- `reservation_total_minor`;
- `package_component`: exact boolean.

`PaymentObligation` and `StripeLinkRequest` carry optional display details. Optionality is only for decoding durable legacy payment rows. New outcome projections must always populate it. A new Stripe effect must fail closed if display details are absent, so legacy queued rows cannot create a new generic link. Already completed legacy links remain readable.

The payment initiation serialization accepts both:

- legacy obligation payload without `display_details`;
- current payload with exact `display_details` fields.

The current serialization always writes `display_details` for newly projected Stripe obligations. The selection identity includes these bytes, binding the visible checkout description to the effect.

## Presentation

### Product name

- Standalone activity: `<public label> — Sinal <percentage>%` when percentage is below 100; otherwise `<public label> — Pagamento integral`.
- Standalone lodging: `<public label> — Pagamento integral` at 100%, or `<public label> — Sinal <percentage>%` for any configured partial payment.
- Package component: prefix `Pacote — ` before the same component-specific name.

Names are normalized, NUL-free, and bounded to Stripe's safe Product-name limit. Truncation is deterministic and uses an ellipsis without changing the signed source value stored in the payment contract.

### Product description

Activity example:

`Passeio / Tour • 03/12/2026 às 08:30 • 1 adulto • Total R$ 334,95 • Pagar agora R$ 66,99 (20%)`

Lodging example:

`Hospedagem / Accommodation • Check-in 02/12/2026 • Check-out 04/12/2026 • 1 hóspede • Total R$ 300,00 • Pagar agora R$ 300,00 (100%)`

Rules:

- deterministic Brazilian date and currency rendering;
- correct singular/plural for adult, child, and guest;
- omit time when unavailable;
- include both adults and children when children are present;
- omit provider references and technical fingerprints; each link remains identified by its public component label;
- show reservation total, payable amount now, and percentage;
- no customer identity or passenger names;
- maximum 500 characters, fail closed rather than silently dropping required commercial facts.

A package creates one enriched Product per confirmed component. The `Pacote —` prefix makes it clear each link pays one package component; the description identifies which service.

## Stripe wire behavior and verification

The Product creation request sends:

- `name`;
- `description`;
- existing hashed metadata;
- `metadata[display_details_sha256]`, calculated from a canonical display-details payload plus percentage and payable amount.

The Product response must be test-mode and must echo the exact name, description, and display hash. Any mismatch is an ambiguous Stripe creation outcome and enters the existing manual-review boundary; no second effect is attempted.

The Payment Link metadata also carries `display_details_sha256`. The existing Payment Link GET read-back must verify it along with the current business/economic metadata.

The Product, Price, and Payment Link idempotency keys remain unchanged because the enriched details are deterministically bound to the same signed reservation command and economic version. Existing Stripe objects are never modified by this feature.

## Data flow

1. Provider worker records an `EFFECT_CONFIRMED` outcome.
2. `ReservationOutcomeProjector` validates command/outcome hashes and derives `PaymentDisplayDetails` from the single component and confirmed outcome.
3. `SQLitePaymentInitiationStore` persists the full selection; its hash/identity includes display details.
4. `PaymentInitiationWorker` fences one dispatch slot.
5. `StripeLinkAdapter` calculates the payable amount and builds a `StripeLinkRequest` carrying the immutable details.
6. `StripeTestHTTPTransport` formats Product name/description, creates Product → Price → Payment Link once, and verifies Product and Link responses.
7. Completion behavior remains unchanged.

## Error handling

Fail before Stripe traffic when:

- current projection lacks display details;
- service/provider pairing is invalid;
- lodging lacks a valid end date;
- activity has an end date inconsistent with the contract;
- party counts are invalid;
- public label or required amounts are invalid;
- formatted name/description cannot include all required facts within limits.

After any Stripe POST, retain the current ambiguity/manual-review behavior. Do not retry consumed dispatch slots.

## Testing

Use TDD with causal RED witnesses before production changes.

Required tests:

1. Activity Product form includes service label, date/time, party, reservation total, payable amount, and deposit percentage, with no provider token.
2. Lodging Product form includes check-in/out, guest count, totals, and full-payment wording, with no provider token.
3. Package component adds `Pacote —` while preserving component details.
4. Children and pluralization render correctly; missing activity time is omitted.
5. No PII fields can enter the display contract; unexpected fields are rejected by strict deserialization.
6. Product response mismatch in name, description, or display hash becomes manual review and does not continue to Price/Link creation.
7. Payment Link read-back verifies the display hash.
8. Legacy selection bytes without display details still decode and completed legacy offers remain readable, but trying to create a new Stripe link from a legacy obligation fails before HTTP.
9. Outcome projection populates details for lodging, activity, and two-component packages from exact signed command/outcome data.
10. Existing idempotency and one-dispatch tests remain green.

## Delivery boundary

Implementation may edit payment contracts, payment serialization/projector, Stripe adapter, and their focused tests. It must not alter reservation provider writes, ManyChat delivery, production deployment, credentials, or existing external Stripe objects. Completion requires focused tests, relevant regression tests, global suite classification, syntax/diff checks, and a Docker build whose revision label matches the implementation commit.
