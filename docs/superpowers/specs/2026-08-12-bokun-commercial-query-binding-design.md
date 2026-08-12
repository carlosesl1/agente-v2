# Bókun commercial query binding design

## Problem

A localized activity read uses `ReadRequest.query_hash()` as both the Bókun quote scope and the private-binding input. The conversation later reconstructs the commercial lookup identity without `locale`. Because `query_hash()` currently includes `locale`, the confirmed private binding can never equal the binding re-read by a fresh worker composition, even when product, date, start time, party, price, currency, availability, and all provider execution fields are unchanged.

Observed persisted evidence on candidate `89f50d3b038f1f2f9c61704c43f4a118665e870a`:

- the command `lookup_id` equals the activity query hash without locale;
- the confirmed read used `locale="pt-BR"`;
- the localized and non-localized query hashes differ;
- 0/7 Bókun bindings survived a new worker while two new workers agreed with each other.

## Owner

`v2_contracts.providers.ReadRequest.query_hash()` owns stable commercial query identity. `canonical_hash()` continues to own the exact request identity, including presentation locale and request ID.

## Design

Exclude `locale` from `query_hash()` only for `ReadKind.ACTIVITY`. Keep `locale` in `to_canonical_bytes()` and therefore in `canonical_hash()`. Keep `locale` in the `query_hash()` of knowledge reads, where language can materially change the answer.

This preserves one representation for each concern:

- exact read request/audit identity: `canonical_hash()`;
- stable commercial query/quote scope: `query_hash()`;
- private executable IDs: resolved fresh by the provider adapter immediately before the durable fence.

At private re-read, the resolver also reconstructs the activity query identity from the confirmed component and requires it to match `lookup_id` before provider I/O. It accepts both the current `adults`/`children` party shape and the no-children legacy `participants` shape. This prevents an old lookup hash from being combined with a changed product, date, or party without adding a DTO, parser, cache, or controller gate.

No private ID is silently accepted as equivalent. The binding continues to include the canonical commercial query hash plus the Bókun product, start-time, rate, and pricing-category IDs. Any change to those values, or to product/date/time/party/price/currency/availability, still fails closed.

## Non-goals

- No provider write or booking.
- No Stripe link.
- No migration or promotion of blocked historical roots.
- No cache, persisted opaque quote, replacement binding, runtime parser, regex, or semantic gate.
- No weakening of claim, durable fence, idempotency, cart/checkout validation, or reconciliation.

## Acceptance criteria

1. Exact request hashes remain locale-sensitive.
2. Commercial query hashes are locale-insensitive.
3. A localized conversation read survives revalidation in a fresh adapter/resolver composition.
4. Product, date, and party incoherent with the persisted activity `lookup_id` fail before provider I/O.
5. Price and executable-provider-field changes still cause `PrivateBindingMismatch`.
6. Focused tests and canonical project tests pass.
7. A GET/read-only real-provider proof shows the same Bókun binding in two fresh transports; no booking/write/payment worker is instantiated.
