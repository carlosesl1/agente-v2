# Split-Origin Reservation Profile Authority Design

**Date:** 2026-08-02
**Status:** approved by the user request captured in this session
**Candidate base:** `1219ff2c12efa989f44f5caa7364363011ea281d`
**Branch:** `maya-v2-operational-readiness`

## 1. Problem

The current V2 reservation boundary accepts `full_name`, `email`, and `phone_e164` only from a fresh `PrivateCustomerBinding`; only `country_code` may fall back to a persisted conversational fact. Maya may collect a missing name or email, but those values currently remain in the public conversation projection and are ignored when the reservation customer is built. Real WhatsApp leads whose ManyChat profile has only an authenticated phone therefore cannot reach a reservation summary.

The correction deliberately replaces the former profile rule. It must preserve the channel-authenticated phone boundary and all existing reservation/payment effect guards.

## 2. Goals

1. Keep the phone exclusively ManyChat/WhatsApp-authenticated and fresh.
2. Use valid ManyChat name/email/country first.
3. Permit canonical conversational `full_name`, `email`, and `country_code` only as fallbacks.
4. Persist fallback values under a parent-owned private durable owner before they can contribute to a summary.
5. Never put those values in public conversation facts, `state_facts`, model-history state, Maya artifacts, logs, exception text, `repr`, evidence, or public serialization.
6. Keep the model wire limited to field-presence markers after collection; do not rehydrate private values into later prompts.
7. Require a later turn for summary creation and a still later contextual confirmation for command creation.
8. Revalidate a fresh ManyChat binding and the exact effective customer frozen into the draft before a command.
9. Preserve exactly-once command/relay/dispatch and one-POST provider behavior.

## 3. Non-goals

- No real Cloudbeds/Bókun POST, reservation, cancellation, reconciliation, payment, ManyChat delivery, relay opening, rollout, or state reset.
- No phone fallback from model output, transcript facts, locale, language, country, or default.
- No provider payload or Cloudbeds confirmation semantic changes.
- No redesign of the passenger manifest, birth-date, gender, payment, completion, or outbox owners.
- No import or execution dependency on `/home/ubuntu/chapada-leads-hermes`.

## 4. Considered approaches

### A. Keep fallback values in `ConversationProjection.facts`

Smallest code change, but rejected. `ConversationProjection`, `MayaTurnProposal`, `typed_fact` artifacts, graph digests, and evidence validators treat these facts as public/authenticated conversation artifacts. Merely hiding them from `ModelRequest.state_facts` does not satisfy the requested privacy boundary.

### B. Add private columns/tables to the boundary SQLite database

Could make the public turn and private facts atomic, but rejected for this repair. The boundary database has a closed evidence/schema contract and is reconstructed from public Maya artifacts. Adding private values there expands the trusted/evidence surface and risks accidental graph serialization.

### C. Dedicated private SQLite owner (selected)

A separate `PrivateConversationCustomerStore` owns only conversational reservation-profile fallbacks. It is opened by the worker container at a physically distinct path and never participates in public artifact serialization. Its idempotent turn journal binds each write to `lead_id`, `batch_id`, event hash, canonical field names, and a private content hash.

This approach best matches the split-origin rule, minimizes public-boundary changes, and makes the privacy assertion mechanically testable.

## 5. Authority model

### 5.1 Phone

The effective phone is valid only when all conditions hold:

- the object is an exact `PrivateCustomerBinding`;
- `observed_at <= now < expires_at`;
- the binding carries a canonical E.164 value;
- the binding identity belongs to the current ManyChat lead.

No conversational phone fact is stored or accepted as fallback. Missing, invalid, future, or expired binding means `not_ready`, no authorizable summary, no command, and no provider call.

### 5.2 Full name

A canonical reservation full name:

- is Unicode NFKC;
- collapses internal whitespace;
- contains no control characters;
- is bounded to 200 code points;
- has at least two non-empty name components;
- never invents or expands a surname.

A valid complete ManyChat name wins. If ManyChat has no name or only an incomplete one-component name, a persisted conversational full name may be used. If a valid complete ManyChat name and a persisted conversational name differ after canonical comparison, resolution is `conflict`; neither silently overwrites the other and no summary/command is authorized until the lead explicitly supplies a value agreeing with the authoritative profile or the profile is corrected and a new summary is generated.

### 5.3 Email

A canonical reservation email:

- is stripped and normalized to the same lowercase canonical form already used by `CustomerFacts`;
- contains exactly one `@`, non-empty local/domain parts, no whitespace/control characters, and at most 254 characters.

A valid ManyChat email wins. A persisted conversational email is fallback only when ManyChat has none. Divergence between a valid ManyChat email and a persisted conversational email is a conflict, not a silent overwrite.

### 5.4 Country

The existing rule remains:

- valid ManyChat ISO alpha-2 first;
- otherwise persisted conversational ISO alpha-2;
- no inference from phone, locale, language, IP, or default.

Divergent valid values are treated consistently with name/email: fail closed until correction/new summary.

### 5.5 Effective customer identity

The parent resolver returns a private `EffectiveReservationCustomer`/`CustomerFacts` only when:

- the binding is fresh and phone-authenticated;
- each required field resolves without conflict;
- activity passenger requirements, when applicable, still pass existing checks.

`customer_ref` is a domain-separated digest of the fresh ManyChat binding identity plus the private fallback snapshot hash. The draft therefore freezes both origins without exposing either value publicly.

## 6. Private durable protocol

The dedicated SQLite owner contains two private tables:

1. `private_customer_fact_turns`
   - `(lead_id, source_turn_id)` primary key;
   - source event hash;
   - canonical ordered field-name list;
   - private content hash;
   - persisted UTC instant.

2. `private_customer_facts`
   - `(lead_id, fact_name)` primary key;
   - canonical private value;
   - value hash;
   - source turn ID and event hash;
   - monotonically increasing revision;
   - persisted UTC instant.

Accepted fallback names are exactly `full_name`, `email`, `country_code`. Phone is outside the catalog.

`persist_turn(...)` is transactional and idempotent:

- same lead/turn/event/payload returns the same snapshot;
- same lead/turn with divergent event or payload raises a generic identity-conflict error that contains no value;
- each field records the source turn;
- a replay after crash can still determine that the current batch supplied a newly authoritative private value and must remain collection-only;
- no SQL row, exception, or `repr` is copied into evidence.

A private snapshot is an immutable `repr=False` value with canonical fields, content hash, and per-field source-turn metadata. Its explicit public view is only the ordered tuple of present field names.

## 7. Turn protocol

### 7.1 Start of turn

1. Load boundary state/projection.
2. Read a fresh ManyChat profile.
3. Load the private conversational snapshot.
4. Build the initial model request with:
   - normal public state facts only;
   - presence markers for private customer fields;
   - `private_profile_complete` computed from the effective resolver;
   - no previously stored private values.

The current inbound user message remains the source utterance; stored private values are never reinserted into later model prompts.

### 7.2 Collection turn

After validating model output and deterministic explicit extraction:

1. Split reservation-profile private facts (`full_name`, `email`, `country_code`) from public facts.
2. Reject `phone_e164` as conversational authority.
3. Canonicalize and persist the private facts under the current `batch_id` and event hash.
4. Remove their values from the proposal passed to the public reducer and from all public artifacts.
5. Force a deterministic collection-only decision for any turn that introduced/updated a private field:
   - no reads used to authorize selection;
   - no summary;
   - no command/relay;
   - no provider call;
   - non-technical acknowledgement/correction request.

This remains true on retry after a crash because the private turn journal remembers the source `batch_id`.

### 7.3 Later summary turn

Only a later `batch_id` may use the persisted snapshot. The effective resolver combines it with the fresh ManyChat binding. If ready and conflict-free, the existing reducer may select an offer and create an `AwaitingConfirmationState`. `CustomerFacts` in the draft contains the exact effective values and the split-origin `customer_ref`.

### 7.4 Confirmation turn

A contextual natural confirmation remains valid; no magic phrase is added. Before command creation, the existing confirmation path additionally proves:

- fresh ManyChat binding;
- authenticated phone;
- current private snapshot;
- effective customer equals the draft customer exactly;
- projection/draft, offer, dates, party, amount, currency, payment method, capability, TTL, and fresh provider read remain equal under existing checks.

Any customer/profile/snapshot/offer/material change rejects the old confirmation and requires a new summary. The existing post-command guard prevents a second command for an already consumed workflow.

## 8. Public behavior

- Maya asks only for missing name/email/country fields and does so naturally.
- Maya does not ask for a phone when a fresh authenticated phone exists.
- Presence markers prevent asking again for persisted fields.
- Invalid or conflicting input produces a non-technical correction request.
- Public text never names ManyChat, Cloudbeds, provider, payload, schema, ledger, state, gate, or technical blocker.

## 9. Privacy properties

The following must contain no conversational name/email/country values:

- `ModelRequest.state_facts` and later request wires;
- `ConversationProjection.to_canonical_bytes()`;
- `MayaTurnProposal` and `typed_fact` artifacts;
- `TurnReceipt`, graph artifacts, public outbox rows, qualification/evidence outputs;
- `repr` of private snapshot/store/result classes;
- exception messages and logs.

The private SQLite store and the private reservation command/execution path may retain the exact values required for provider execution. Cloudbeds receives the effective canonical name parts, email, ManyChat phone, and country without reconstruction from public artifacts.

## 10. Failure handling

- Invalid private fact: reject/fail closed; no command; public correction response where a turn can safely continue.
- Profile absent/invalid/future/expired: no authorizable summary/command/provider call.
- ManyChat/fallback conflict: no silent overwrite and no command.
- Private store identity conflict or corruption: generic failure, no command.
- Crash after private persistence and before boundary commit: retry remains collection-only.
- Timeout/ambiguous provider result: existing `CALLED_UNKNOWN`, no POST retry.

## 11. Verification

Required tests cover:

- phone-only ManyChat + conversational name/email/country over collection → later summary → later contextual confirmation;
- one-word ManyChat name fallback;
- missing ManyChat email fallback;
- divergent valid ManyChat/fallback conflict;
- absent/invalid/future/expired phone binding;
- invalid name/email/country;
- customer change after summary;
- exact Cloudbeds customer payload;
- private round-trip and absence from model/public artifacts/logs/repr/exceptions/evidence;
- crash/replay/restart exactly-once command, slot, and maximum one POST;
- Bókun, Cloudbeds monotonic confirmation/audit, payment separation, completion/outbox, Pix/Stripe/Wise, and private Cloudbeds reference regressions.

## 12. Operational close

Implementation and qualification are local/fake-only. The final state remains:

```text
runtime=dark_read_only
kill_switch=true
post_budget_armed=false
relay_running=false
SAFE_FOR_BROAD_ROLLOUT=false
```

A green candidate may be committed, pushed, reviewed, and proven by CI/OCI, but no real effect or rollout is authorized.
