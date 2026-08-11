# Maya Agent Process and Read Enrichment Design

**Date:** 2026-08-11

**Status:** Approved by Carlos Eduardo through the instruction to continue after reviewing the prompt/skill/tool-first approach.

**Starting candidate:** `496799ed0d8c30f9d966fdea9e9c86b546ac992e`

## 1. Problem

The real-read conversational audit found five semantic failures even though ten attempts eventually produced structurally valid reservation commands. The failures were concentrated in information and process:

- Maya embellished a recommendation without a current descriptive observation;
- Maya merged two consecutive one-hour trail segments into two hours in the riverbed;
- the knowledge lookup had no direct answer for how a shared dormitory works and returned unrelated entries;
- Maya inferred privacy from public room names without requesting the existing room-description read;
- a package response offered a female dormitory to a male lead after a post-read rewrite.

No Cloudbeds, Bókun, Stripe, or ManyChat effect occurred.

## 2. Decision

Improve the model-owned atendimento before considering any new controller logic.

The implementation has only three production changes:

1. add official hostel facts to the existing `config/cerebro_faq.yaml`;
2. teach Maya in the existing system prompt to answer the direct question first, use the existing description reads, preserve quantitative sequences, avoid unsupported future promises, and avoid inferring room characteristics or eligibility from names;
3. include the selected room's public name in the existing Cloudbeds room-description DTO.

No new tool, worker, state field, controller gate, semantic parser, regex router, or skill inventory is introduced.

## 3. Authoritative facts

Hostel facts are taken from the public company site and Cloudbeds booking page supplied by Carlos:

- `https://chapadabackpackers.com/hostel`
- `https://hotels.cloudbeds.com/en/reservation/AnQ8cK?currency=brl`

The official site states that the hostel offers female and mixed dormitories with 4, 6, or 8 beds; dormitories have private bathrooms, bunk beds, and individual lockers; the property has two equipped shared kitchens, hot showers, common areas, Wi-Fi, private rooms, family rooms, and suites. Cloudbeds states check-in at 12:00, check-out at 11:00, 24-hour reception, and breakfast on demand.

Existing project-owned business facts remain authoritative where they are more specific, including the configured breakfast price of R$ 30 per person per day and the reception/portaria distinction already present in the knowledge file.

Tour descriptions remain provider-owned through Bókun `activity_description`. Website tour pages are corroborating evidence only; their prose is not copied into the prompt or duplicated into a new catalog.

## 4. Conversational process

Maya follows these compact rules:

1. Answer the lead's direct question before trying to advance the sale.
2. If the current message and state already contain the fields for a read, request it immediately without asking permission to consult.
3. Use `knowledge` for generic hostel operation and policy.
4. Use `room_description` for room-specific privacy, bathroom, amenities, noise/quietness, accessibility, layout, or eligibility. Never infer these from a room name.
5. Use `activity_description` for itinerary, duration, included transport, difficulty, preparation, and suitability. Preserve quantities and segment relationships exactly; never add adjacent durations into a different activity or location.
6. Promise a later verification only when the current response requests the available read or explicitly offers a human handoff. Otherwise state what is not confirmed.
7. Do not offer a gender-restricted room unless the authenticated observation or current conversation establishes compatibility. If compatibility is not established, present neutral compatible options or ask naturally.

These are model instructions, not deterministic text inspection or authorization rules.

## 5. Cloudbeds DTO

`CloudbedsHTTPTransport._room_description()` already reads `/api/v1.3/getRoomTypes` using the private offer binding. It will return `room_public_name` from the same selected provider record alongside `description` and `amenities`. `CloudbedsReadAdapter` validates and exposes that exact public field.

The availability DTO remains unchanged. Room names do not become technical identity and do not authorize selection or write.

## 6. Knowledge retrieval

Add focused entries for:

- how a shared dormitory works and what the official hostel provides;
- the distinction between private rooms/suites and shared dormitories, without promising silence;
- arrival/check-in guidance using existing business hours and an explicit non-promise for unconfirmed late-arrival instructions.

The existing deterministic lookup algorithm remains unchanged. Tests use the exact previously failing natural question and require the focused source to rank first.

## 7. Safety and scope

- No provider POST, reservation, payment link, checkout, charge, message, deploy, restart, or canary promotion.
- No changes to confirmation, idempotency, command, relay, worker, payment, or effect authority.
- No privacy/PII work.
- No deterministic interpretation of customer language.
- No website crawler or synchronization process is added to production.

## 8. Verification

1. Witness focused REDs for missing prompt process, missing FAQ retrieval, and missing Cloudbeds room name.
2. Implement the minimum changes.
3. Run focused tests.
4. Run canonical clean-environment pytest, Ruff, and `git diff --check` once on the frozen candidate.
5. Run a controlled read-only/fake-effect conversation qualification only if the existing harness can consume the new candidate without enabling workers.
6. Re-audit reservation relays and effect artifacts before reporting.

## 9. Acceptance criteria

- The exact shared-dorm question retrieves the new official hostel answer first.
- The prompt explicitly routes room characteristics to `room_description` and tour detail questions to `activity_description`.
- The prompt forbids inferring room characteristics from names and forbids merging quantitative segments.
- The room-description observation includes the selected room's public name.
- Existing critical action/effect contracts are unchanged.
- Focused and full gates pass.
- External commercial effects remain zero.
