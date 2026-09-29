# ManyChat liability-form buttons — implementation plan

> Execute inline with executing-plans; no subagents. Existing isolated worktree `cloudbeds-party-727d3625`, branch `fix/v2-cloudbeds-party-727d3625`.

**Goal:** Send one tour liability-form button after confirmed payment, selecting the supplied form and ManyChat flow by canonical tour and lead language.

**Architecture:** A communication-only projector reads the existing paid workflow, authenticated reservation anchor and conversational locale. It enqueues an authenticated-system row in the existing public outbox; the existing ManyChat adapter sets field 14426643 and triggers the matching flow. Maya remains the sole prose author; no reservations or financial writes are added.

**Tech stack:** Python, existing SQLite owners, httpx ManyChat transport, pytest.

## Global constraints / approved specification

- Exactly six user-provided forms: Pati, day excursions, Mixíla/Fumaça from Below × PT/EN. IDs and URLs in `workspace/v2-waiver-forms-727d3625/INPUTS.json`.
- Send only after canonical settlement is SETTLED/PAID; not after mere reservation creation, payment-link creation, receipt submission, or ambiguous settlement. Hostel-only never receives a form.
- One per lead in the current retained conversation history, not per activity/payment/draft. The first eligible paid tour supplies the form; later payments do not create another. Reset/new-trip policy is outside this change.
- Use canonical product IDs, never infer category from public labels or customer text. Mixíla (both durations) uses its specific waiver. Locale is the authenticated conversational locale, with the existing typed payment language as fallback when no supported conversation locale exists.
- Freeze selected form/language in the persisted row; validate it against the paid anchor before external I/O.
- Reuse permanent public dispatch fence: not-called can retry; uncertain field/flow dispatch cannot auto-resend.
- Activation has an explicit UTC cutoff setting (`V2_BOOKING_FORMS_FROM`); absent means disabled. Settlements before that cutoff cannot produce historical mass sends. No runtime/deployment configuration is modified in this task.
- No V3, legacy, SQLite-live, deploy, real ManyChat sends, reservations or financial effects.

## Task 1 — Catalog and typed route

Files: `config/v2_booking_forms.json`, `v2_contracts/booking_forms.py`, `tests/test_v2_booking_forms.py`.

- [x] Add parameterized tests for six exact URLs/flows/field ID, complete canonical product coverage, special Mixíla routing, and unknown products.
- [x] Run the failing tests before implementing the module.
- [x] Implement immutable `BookingFormRoute` and `BookingFormCatalog` with exact product/locale lookup.
- [x] Re-run catalog tests.

## Task 2 — Causal projection and channel delivery

Files: `v2_application/booking_forms.py`, `v2_application/completion_projector.py`, `v2_adapters/manychat.py`, `v2_host/{settings,production}.py`, `tests/test_v2_booking_form_delivery.py`.

- [x] Build isolated real SQLite workflow from accepted reservation → payment evidence → settlement; mock only external provider HTTP/model.
- [x] Red assertions: zero before paid; one after paid; exact custom field then correct sendFlow; no raw-link reply flow; no hostel form; one for multiple independent paid activities; second lead isolated.
- [x] Red recovery assertions: reopen/repeated ticks no resend; known-not-called retry; timeout/crash fenced; closed gate no HTTP; wrong lead/altered payload rejected before fields; no activation/history cutoff no send.
- [x] Implement projector and adapter branch using existing stores and public worker. Do not enable `PaymentEffectPolicy.booking_form`: it is a different legacy effect surface and the canonical form owner is the public outbox, analogous to payment buttons.
- [x] Wire only through the optional cutoff; add tests for production wiring/settings.
- [x] Update the Maya prompt with factual delivery policy (button, after paid, one enough, no invented delivery/completion claim), preserving authorship and reservation guarantees.

## Task 3 — Qualification and handoff

- [x] Run targeted tests and all V2 regressions with isolated env: `env -i HOME=/home/ubuntu PATH=/home/ubuntu/workspace/v2-pix-wise-727d3625/venv/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 HERMES_LEADS_AGENT_CONFIG_PATH=/tmp/no-live-config .../venv/bin/python -m pytest -q -p no:cacheprovider tests/test_v2*.py`.
- [x] Execute `python3 scripts/check_fasttrack_boundaries.py`, `git diff --check`, inspect exact diff and runtime authority.
- [x] Record counts, real vs fixture boundaries, remaining lack of live WhatsApp evidence, activation procedure, commit and clean tree. No deploy.

## Final qualification

Implemented and qualified without deployment. Broad V2 regression: 1,862 passed
before the last prompt-only locale clarification. Final focused suite after that
clarification: 122 passed. Real-model completion-to-recording-transport: five
passes, including three English repetitions; final prompt hash matched. Candidate
is disabled without the explicit activation cutoff. Guide:
`docs/operations/manychat-booking-forms.md`. Full evidence and preserved failed
iterations: `/home/ubuntu/workspace/v2-waiver-forms-727d3625/RESULTADO.md`.

## Baseline evidence

68 targeted tests passed (ManyChat flow, payment buttons, Stripe settlement). Existing Starlette/httpx deprecation warning. An initial baseline command referenced a nonexistent test filename; corrected to `tests/test_v2_stripe_settlement.py`, no production failure.
