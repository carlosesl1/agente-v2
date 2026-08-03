# Same-Turn Private Profile Continuation Design

**Date:** 2026-08-03
**Status:** approved by explicit product request; written design pending final user review
**Branch:** `maya-v2-operational-readiness`
**Parent design:** `docs/superpowers/specs/2026-08-02-split-origin-reservation-profile-authority-design.md`

## 1. Problem

The split-origin executor currently treats every valid collection or correction of `full_name`, `email`, or `country_code` as `collection_only`. It persists the private fact before model use, but then replaces the otherwise valid proposal with a deterministic acknowledgement such as:

> Obrigado. Guardei esses dados para continuar a reserva.

The lead must send a semantically empty follow-up such as “pode continuar” before Maya can present a reservation summary. This extra inbound turn provides no new authority, identity, commercial fact, or confirmation and must be removed.

## 2. Product decision

A turn whose parent-owned deterministic collector supplies or corrects a valid conversational `full_name`, `email`, or `country_code` may continue through the normal reservation flow and publish a fresh summary in that same turn.

The same turn must never create a reservation command, command relay, payment initiation, or provider write merely because the newly persisted facts made the profile complete. A separate later confirmation remains mandatory for all effect-producing reservation actions.

This amendment supersedes only the parent design's requirement that every valid private collection/correction be collection-only and that a later `batch_id` always be required for summary creation. All privacy, authority, freshness, reauthentication, idempotency, and provider safety requirements remain in force.

## 3. Scope

The change applies exclusively to valid conversational facts in this closed catalog:

- `full_name`;
- `email`;
- `country_code`.

It does not relax behavior for:

- conversational `phone_e164`, which remains unauthorized;
- invalid name, email, or country values;
- passenger identity or manifest collection;
- birth date or gender;
- commercial corrections such as dates, party, product, or payment method;
- confirmation, command, relay, provider, or payment effects.

An explicit customer request for human handoff remains governed by the existing handoff policy and is not suppressed merely because the same message also contains an approved private fact.

## 4. Considered approaches

### A. Remove every collection guard

This would allow the proposal to continue without distinction and could let a model-produced `confirm` create a command in the same turn as an identity change. Rejected because it couples customer mutation and an external effect under one utterance.

### B. Persist first, permit summary, suppress effects (selected)

Persist and authenticate valid private facts before the model request, keep the inbound message sanitized, permit normal reads/selection/summary generation, and enforce a closed no-effect fence for the current source turn. This removes the useless conversational round trip without weakening confirmation authority.

### C. Preserve only the model's normal acknowledgement

This removes the canned text but does not guarantee that a summary can be produced in the same turn. Rejected because the lead may still need to send “pode continuar”.

## 5. Turn protocol

### 5.1 Persist-first extraction

Before the first model call:

1. deterministically extract candidate `full_name`, `email`, and `country_code` values from the inbound text;
2. canonicalize and validate them;
3. persist accepted values in the private SQLite owner under the current lead, batch, event hash, and authenticated turn journal;
4. reload/use the resulting authenticated private snapshot;
5. sanitize the inbound message so no private value reaches the model;
6. expose only closed field-name presence markers and the resulting profile-complete bit.

The model therefore reasons after persistence but never receives the private values.

### 5.2 Valid collection without a pending summary

When at least one valid approved private fact is supplied and no previous summary is pending:

- do not force the deterministic “dados guardados” proposal;
- permit the normal model/read/selection pipeline;
- permit a new summary if the complete effective customer and all commercial prerequisites are available;
- if prerequisites are still missing, allow Maya's normal non-technical response asking only for what remains missing;
- suppress all effect-producing commands and relays for the turn.

A model proposal that attempts to confirm or directly produce an effect is demoted/rejected before reducer command creation. The persisted data remains valid; the public response must continue safely without an effect.

### 5.3 Valid correction with a pending summary

When a valid approved private fact changes while a summary is pending:

1. revoke the old pending summary and its confirmation capability;
2. keep the already persisted corrected private snapshot;
3. preserve commercial facts and selected offer only as inputs for rebuilding, never as authority to reuse the old confirmation;
4. reauthenticate any provider read required by the existing summary policy;
5. produce a fresh summary bound to the corrected effective customer in the same turn when prerequisites remain valid;
6. require a separate later confirmation of the new summary before any command.

The old summary version cannot be confirmed, replayed, or converted into a command after the correction.

### 5.4 Invalid private input and conversational phone

If an explicitly supplied name, email, or country is invalid, or if the conversation proposes a phone value:

- retain the existing correction-only behavior;
- do not persist invalid/unauthorized values;
- revoke an existing pending summary when the attempted correction makes it unsafe to retain;
- issue no provider read, summary, command, relay, payment, or external effect;
- return a non-technical correction request that names only the required field categories, not the rejected values.

### 5.5 Model-discovered private facts

If a valid private fact appears only in validated model output rather than deterministic parent extraction, retain the existing conservative collection-only behavior. Persist it privately, remove it from the public proposal, and return a safe acknowledgement without reads, summary, command, or relay. Rebuilding a second private-fact model stage is outside this narrowly scoped amendment. A proposal/frame that may contain raw PII must never become a public summary artifact.

## 6. No-effect fence

The executor records whether the current aggregate turn supplied or corrected an approved private fact. For such a turn:

- summary state and public summary chunks are allowed;
- reservation commands are forbidden;
- command relays are forbidden;
- payment initiation is forbidden;
- provider writes are forbidden;
- read-only availability/description operations remain governed by the existing complete-profile and freshness gates.

The existing explicit human-handoff path is unchanged. The ordinary public reply/outbox remains required.

This fence is checked after reduction and again before boundary commit. It is independent of model intent and survives crash/retry through the authenticated private turn journal.

## 7. Replay and crash safety

The private journal remains the authority for determining whether the current `batch_id` supplied an approved field, including no-change re-supply turns.

For an identical replay:

- private persistence is idempotent;
- the boundary receipt/public reply is replayed identically;
- reads are not repeated after a committed turn;
- no command or relay appears;
- no second summary version is created.

A crash after private persistence but before boundary commit may retry the same aggregate turn. The retry may rebuild the same summary, but the no-effect fence remains active and the eventual boundary commit remains exactly once.

## 8. Privacy

The amendment does not widen any PII surface. Exact name, email, and country values remain absent from:

- public `ConversationProjection` facts;
- `ModelRequest` state and wire payload;
- model-history state;
- frame, typed-fact, Maya, kernel, graph, receipt, public-outbox, log, evidence, exception, traceback, and `repr` surfaces.

Only the private SQLite owner, effective private customer, command payload after a later confirmed turn, and eventual authorized provider transport may contain exact values.

## 9. Required tests

### 9.1 Initial collection

A single inbound message that contains valid name/email/country plus sufficient reservation details must:

- persist all three facts before the first model request;
- redact their values from the request;
- expose only presence markers;
- perform only allowed read-only provider queries;
- return the normal fresh summary in the same turn;
- create zero command rows and zero relay rows;
- avoid the deterministic “Guardei esses dados” acknowledgement.

### 9.2 Valid correction after summary

A valid name, email, or country correction while awaiting confirmation must:

- revoke the old summary/capability;
- persist the correction;
- publish a new summary in that same turn;
- bind the new draft to the corrected effective customer;
- reject confirmation of the old version;
- create zero command/relay rows until a later confirmation.

### 9.3 Invalid correction

An invalid correction must retain the current correction request, revoke pending confirmation where required, persist no invalid value, and produce zero reads/summary/commands/relays.

### 9.4 Replay and privacy

Tests must prove identical replay, journal-authenticated no-effect behavior after private persistence, no duplicate reads or summary versions, and zero PII in every public/model/artifact/error surface.

### 9.5 Regression

The proportional and full gates must continue to cover:

- conversation-first field authority and ManyChat-only phone;
- source-aware effective-customer digest and pre-commit reauthentication;
- Cloudbeds exactly-once one-POST behavior;
- Bókun GET-only audit boundaries;
- payment separation;
- public outbox and completion behavior.

## 10. Operational state

Implementation and tests remain local/fake-only:

```text
runtime=dark_read_only
kill_switch=true
post_budget_armed=false
relay_running=false
SAFE_FOR_BROAD_ROLLOUT=false
```

No real Cloudbeds/Bókun POST, payment, ManyChat delivery/reset, deploy, reservation mutation, or rollout is authorized by this design.
