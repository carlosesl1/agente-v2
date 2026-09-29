# Tour liability-form buttons (V2)

## Operator contract

Carlos supplied the form links, ManyChat flow IDs and link field, then selected
**send after confirmation of payment**. No form is required for lodging.
One form is sufficient even when the lead books several activities.

The canonical inputs live in `config/v2_booking_forms.json`:

| Tour family | PT-BR | English |
| --- | --- | --- |
| Vale do Pati (all configured durations) | https://forms.gle/bXEoQUBcwzBaMQ8t7 | https://forms.gle/SsGvxtLQ6pjyV6qi8 |
| One-day excursions | https://forms.gle/pTD7jYosU6EzuyrAA | https://forms.gle/N8Lqnhg8LxT5ABr76 |
| Mixíla (1d/2d) and Fumaça from Below | https://forms.gle/jgUBDfSrqDQiizhR7 | https://forms.gle/AYgrYEaND8328BwN6 |

- Custom field: `14426643`.
- PT-BR flow: `content20260327015856_644212`.
- English flow: `content20260327021027_819398`.
- Category is selected by the immutable canonical product/lookup identity, never
  a customer-text keyword or a public description. The catalog covers exactly
  the currently mapped Bókun products; future products need an explicit mapping.
- Language uses the supported canonical conversational locale; if unavailable,
  it uses the existing phone-based PT/EN localization convention.

## Execution and ownership

1. The existing payment worker completes settlement (Pix, Wise or Stripe).
2. `BookingFormProjector` reads `PAID` plus the exact `SETTLED` result and validates
   the financial subject's confirmed reservation anchor against the ledger.
3. The first eligible paid activity chooses the form. A single per-lead public
   release is inserted atomically; later activities/payments cannot add one.
4. The stored route is immutable across restart or a later locale change.
5. The ManyChat adapter validates the stored claim against that paid subject,
   sets the supplied link field, then triggers the supplied localized flow.
6. The existing public worker records acceptance or preserves its uncertainty
   fence. Known-not-called failures can retry; ambiguous calls cannot auto-repeat.
7. A terminal/expired form delivery issue opens the existing human-handoff
   workflow once. A live in-flight fence does not create a false incident.

This is a communication projection, not an additional financial action, and
there is no new database/schema. The unrelated legacy
`PaymentEffectPolicy.booking_form` remains disabled: do not enable both owners.
The form URL is never delivered by the ordinary text reply flow. Maya owns her
text unchanged. A delivered button does not prove that the customer completed,
signed or accepted a Google Form; no submission tracking is implemented here.

Deduplication is per lead for the retained public-outbox history, not per draft
or per tour. A reset/new-trip resend policy is outside this change. Do not delete
operational rows or clear live state to force a resend.

## Activation (separate publication authorization required)

Without `V2_BOOKING_FORMS_FROM`, projection is disabled. A future authorized
release must set this worker setting to the release's explicit UTC cutoff,
for example the *actual* activation instant in `YYYY-MM-DDTHH:MM:SSZ` format.
Do not use a historic/example timestamp: it would qualify historical payments.
Only settlements at or after the cutoff are eligible. API-role settings do not
load this worker-only option. Existing ManyChat write-window/kill-switch gates
still control whether the queued button can be sent.

Before activation, verify the actual ManyChat flows contain native URL buttons
bound to field `14426643` and that the PT/EN text is correct. User-supplied IDs and
Google Forms HTTP checks do not prove live flow contents or WhatsApp delivery.
Follow `runtime-authority.md`, build/pin the exact image, preserve isolated-test
and GA mounts, and use `/readyz`. This document does not authorize deployment,
a real message, a reservation or a payment.

Disabling the cutoff stops new form projection; it is not an emergency sender
kill switch. Use the existing sender gate to pause outbound delivery before any
rollback/reconciliation of already queued rows. Never manually edit live
SQLite/WAL/SHM or repeat an uncertain external send.

## Evidence boundaries

`tests/test_v2_booking_form*.py` exercise real domain workflows, SQLite commits,
projection, ManyChat payload serialization, dedup/restart/concurrency and
handoff, with external provider HTTP and model responses simulated. The
production composition test binds the actual worker factory to the form owner.
Real-model qualification, when present in the task evidence, proves the
completion-to-recording-transport path, not real booking/payment/WhatsApp E2E.
Task evidence is under `workspace/v2-waiver-forms-727d3625` outside Git.
