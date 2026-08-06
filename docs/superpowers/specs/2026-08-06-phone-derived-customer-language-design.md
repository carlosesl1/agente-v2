# Phone-derived customer language — design

**Date:** 2026-08-06
**Status:** approved by explicit implementation request plus timeout/default choice
**Scope:** V2 ManyChat phone normalization, Stripe checkout copy, reservation-confirmation messages, and Stripe payment-link messages

## Goal

Use the customer's WhatsApp phone number as the deterministic source of language for V2 automatic customer-facing payment/reservation messages:

- Brazilian DDI `55` → Brazilian Portuguese (`pt-BR`);
- every other international DDI → English (`en`).

The current bilingual Stripe Product copy must become single-language copy. Free-form Maya replies remain semantically interpreted by Maya and are outside this deterministic rule.

## Selected approach

Derive a closed, non-PII language value once from the normalized private phone and carry only that value into payment display contracts.

Rejected alternatives:

1. Carry the raw phone into Stripe rendering. Rejected because it spreads PII and permits renderer-specific decisions.
2. Infer from `country_code` or conversation text. Rejected because the operator explicitly selected the phone number as authority and those sources can conflict.
3. Keep bilingual copy. Rejected because the requested behavior is one language selected per customer.

## Phone normalization boundary

ManyChat can return a full international phone with or without a leading `+`.

`ManyChatHTTPTransport.fetch_profile()` will return canonical E.164:

- `+557...` remains `+557...`;
- `557...` becomes `+557...`;
- `447...` becomes `+447...`;
- `346...` becomes `+346...`;
- a digits-only local number paired with `country_code=BR` receives the `+55` prefix;
- whitespace, punctuation, letters, impossible length, a leading zero DDI, or ambiguous malformed input fails closed as a profile-read error.

A canonical E.164 value is required before customer facts and reservation commands can exist.

## Closed language contract

Add an exact enum:

- `CustomerLanguage.PT_BR = "pt-BR"`
- `CustomerLanguage.EN = "en"`

Add a pure function accepting canonical E.164:

- `+55...` → `PT_BR`
- every other valid E.164 → `EN`
- invalid input → `ValueError`

The function performs no natural-language interpretation and does not use aliases, message text, names, e-mail, or country as fallback.

## Data flow

1. ManyChat profile read normalizes the private phone to E.164.
2. Existing customer resolution places the canonical private phone in the signed reservation command.
3. `ReservationOutcomeProjector` derives `CustomerLanguage` from `command.payload.customer.phone_e164`.
4. The projector places the language in immutable `PaymentDisplayDetails`.
5. Payment selection serialization and identity include the language.
6. `StripeLinkAdapter` carries the language into `StripeLinkRequest`.
7. Stripe Product name, description, and presentation hash bind the selected language and exact rendered copy.
8. `StripePaymentLink` carries only the language enum, never the phone.
9. `CompletionProjector` uses the same derivation for reservation confirmations and the stored language for payment-link messages.

No raw phone enters Stripe Product data, Stripe metadata, public outbox text, logs, or payment-link result objects.

## Compatibility and fail-closed behavior

- Newly projected payment obligations must always contain a language.
- Legacy selections without language remain decodable for audit/replay compatibility.
- A legacy selection without language cannot create a new Stripe effect.
- A legacy completed offer without language cannot produce a new public payment-link message.
- Existing already-created Stripe links are immutable and remain unchanged.

This avoids silently defaulting an unknown legacy customer to Portuguese or English.

## Customer-facing copy

### PT-BR

Activity package deposit name:

`Pacote — Roteiro dos 4Ps — Sinal 20%`

Description:

`Passeio • 03/12/2026 às 08:30 • 1 adulto • Total R$ 334,95 • Pagar agora R$ 66,99 (20%) • Saldo restante R$ 267,96`

Lodging package full-payment name:

`Pacote — Suite Casal — Pagamento integral`

Description:

`Hospedagem • Check-in 02/12/2026 • Check-out 04/12/2026 • 1 hóspede • Total R$ 300,00 • Pagar agora R$ 300,00 (100%)`

Automatic messages include:

- `Sua hospedagem foi confirmada.`
- `Seu passeio foi confirmado.`
- `Sua hospedagem e seu passeio foram confirmados.`
- `Link de pagamento da hospedagem: <url>`
- `Link de pagamento do passeio: <url>`

### English

Activity package deposit name:

`Package — Roteiro dos 4Ps — Deposit 20%`

Description:

`Tour • 03 Dec 2026 at 08:30 • 1 adult • Total R$334.95 • Pay now R$66.99 (20%) • Remaining balance R$267.96`

Lodging package full-payment name:

`Package — Suite Casal — Full payment`

Description:

`Accommodation • Check-in 02 Dec 2026 • Check-out 04 Dec 2026 • 1 guest • Total R$300.00 • Pay now R$300.00 (100%)`

Automatic messages include:

- `Your accommodation has been confirmed.`
- `Your tour has been confirmed.`
- `Your accommodation and tour have been confirmed.`
- `Accommodation payment link: <url>`
- `Tour payment link: <url>`

## Formatting rules

- Product name maximum: 120 characters.
- Product description maximum: 500 characters.
- PT-BR dates use `DD/MM/YYYY`; English dates use unambiguous `DD Mon YYYY` with fixed English month abbreviations.
- PT-BR BRL uses `R$ 1.234,56`; English BRL uses `R$1,234.56`.
- Party labels are localized for lodging/activity, singular/plural, adults/children.
- Missing activity time is omitted in both languages.
- Partial payment includes percentage and remaining balance; full payment omits remaining balance.

## Idempotency and integrity

Language participates in:

- persisted `PaymentSelection` canonical bytes;
- initiation identity;
- `StripeLinkRequest`;
- exact Product presentation hash;
- Product and Payment Link metadata verification.

A replay cannot switch language without changing the durable selection/presentation identity. Stripe response mismatch remains manual-review/fail-closed and never advances to another effect.

## Tests

Required RED→GREEN coverage:

1. `+55` and digits-only `55` profile phone normalize to E.164 and select PT-BR.
2. UK/Spain/US phones with and without `+` select English.
3. Local BR digits with `country_code=BR` normalize to `+55` and select PT-BR.
4. Malformed phone input fails before reservation/payment/message effects.
5. Payment selection round-trip preserves language; legacy decode yields unknown language and fails before new Stripe HTTP.
6. PT-BR and English Stripe names/descriptions cover activity, lodging, package, signal, full payment, children, missing time, length bounds, and privacy.
7. Product/Payment Link read-back verifies the language-bound presentation hash.
8. Reservation confirmations and Stripe link messages are localized consistently.
9. Existing idempotency, payment, completion, E2E, and Docker compile/build paths remain green.

## Operational boundaries

- No live Stripe link creation.
- No Bókun or Cloudbeds write/read.
- No ManyChat send.
- No deployment, push, promotion, canary mutation, or reservation cleanup.
- Validation uses tests, fake transports, and an offline Docker image preview only.
