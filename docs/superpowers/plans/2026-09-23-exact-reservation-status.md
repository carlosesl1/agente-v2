# Exact reservation lifecycle — approved requirement

## Contract
Creation certainty is historical and monotonic. The current provider reservation status and provider payment state are independent, timestamped facts. A confirmed/reserved booking is not proof of payment. A payment link is not proof of settlement. Automatic cancellation is the operator's provider lifecycle; never fabricate cancellation or its deadline from elapsed local time. If a GET fails, report current status unavailable without downgrading creation or repeating effects.

## Minimal implementation
1. RED: Bókun accepted reference survives into its existing durable outcome; current context distinguishes historical execution from fresh native status. Add named `ReservationStatusReader` to the existing adapter context module and `ProviderReservationStatus` to its existing contract module. Tests for these new APIs initially fail by explicit assertions before implementation.
2. Reuse authenticated GET-only transports; resolve exact IDs already bound to owned commands, reject ambiguous IDs. No new SQLite schema. Supply status/payment values, amounts, currency and observation time to Maya. Old hash-only Bókun references remain unavailable, never guessed or replayed.
3. Refresh once per executor context and carry the same facts to every frame. Preserve authorial output. Wire productive composition with the existing provider credentials. Regression for cancelled/unpaid, paid, partial, unavailable, foreign lead and restarts.
4. Freeze source, run full clean suite and boundaries, review inline, commit/push, build/attest exact image and qualify offline.
5. Deploy only the isolated contact with fresh test state, exact rollback descriptor and canonical authority publication. Maintain GA/Ops identities.
6. Run real WhatsApp journey and received native buttons. Complete Stripe TEST only if end-to-end channel and settlement prerequisites pass. Preserve links/fields for inspection. Verify duplicates and state; never pay or mutate a pre-existing reservation.
7. Evaluate GA only after complete gates; if blocked, keep stable production and report exact highest completed boundary.
