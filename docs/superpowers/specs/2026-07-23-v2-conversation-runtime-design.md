# V2 Conversation Runtime Promotion Design

Date: 2026-07-23
Status: proposed sub-slice for Task 9

## Purpose

Promote the existing Phase 8 typed conversation, dispatch, authority and v8 receipt contracts into the production V2 host so that durable ManyChat inbox batches can drive one deterministic reservation/payment workflow. The promotion must use the existing `SQLiteBoundaryStore` v8 owner and the existing execution/followup ledgers; it must not create a second cognitive state store, a second reservation ledger or a second financial ledger.

## Non-negotiable invariants

- `v2_host` remains the only production composition root.
- The old agent, planner and legacy cognitive runtime are never imported or executed.
- Maya is tool-free and receives only public observations.
- Provider IDs, reservable bindings and customer profile values remain private to deterministic application ports.
- Maya never creates a `ReservationCommand`, payment claim, provider payload, price, availability, receipt or confirmation.
- A provider write is possible only from a durable command already authorized by the deterministic reducer after a version-bound summary and explicit confirmation.
- Model and provider calls execute outside SQLite transactions.
- Each provider effect remains fenced, idempotent and receipt-backed in its existing owner ledger.
- An active handoff blocks commercial admission and reservation dispatch before the fence.
- Real provider and ManyChat gates remain false by default and require the existing exact operational acknowledgment.

## Chosen architecture

### 1. Durable ingress and per-lead serialization

The existing `SQLiteInbox` remains the ingress owner. A concrete `InboxTurnWorker` claims one ready lead batch, invokes the conversation runtime once, persists the result, and only then completes the inbox claim. A pre-commit failure releases the batch. A committed boundary receipt followed by an inbox completion failure is replayed safely because the aggregate turn identity and source event hashes are deterministic.

### 2. Public model grammar

`ModelProposal` remains capability-free. Its fact catalog is expanded by one closed fact, `payment_method`, whose exact values are `stripe`, `wise` and `pix`. Maya may return:

- public reply chunks;
- public itinerary facts: language, service, dates, adults, children and payment method;
- closed read requests;
- selection of one public offer ID;
- explicit confirmation of one summary version;
- a handoff request.

`effect_proposals` are removed from the productive path rather than trusted or silently discarded. A model response carrying an effect proposal is rejected. This prevents an ambiguous second command grammar.

The productive model port returns an `AuditedModelTurn`, not a bare proposal. It contains the exact `ModelProposal`, ordered request/response frame commitments and a closure bound to the final frame. `HermesModelAdapter` derives these values from the exact child-process stdin/stdout bytes it already observes. The application never manufactures transcript hashes after the model call, and the existing simple `ModelPort` remains available only to isolated contract tests.

### 3. Private customer-profile read

A new `CustomerProfileReadPort` receives the canonical `manychat:<subscriber_id>` lead ID and returns a `PrivateCustomerBinding` containing:

- opaque binding ID and content hash;
- full name;
- normalized phone;
- email;
- country code;
- observed-at and expiry timestamps;
- completeness status.

The ManyChat adapter may call only the contact/profile read API. The binding is never included in `ModelRequest`, public read observations, reply chunks or logs. The reducer receives the private binding separately. Missing or expired required fields block summary/command creation. The public reply may ask the user to complete the ManyChat profile flow without echoing private values. Exactly one profile-completion prompt is allowed per workflow; the next inbound event re-reads the profile, and a still-incomplete profile creates one handoff and no commercial effect. The prompt count is derived from the boundary public-outbox/turn receipts, not a parallel flag.

### 4. Deterministic conversation reducer

A new `V2ConversationReducer` consumes:

- authenticated v8 boundary state;
- exact `ModelProposal`;
- sanitized public read observations plus private provider bindings from `V2ReadCoordinator`;
- the private customer binding;
- a trusted UTC instant.

It produces a capability-free `V2ConversationDecision` containing the next `ConversationProjection`, domain events, at most one version-bound public summary, durable boundary commands/relays, public outbox chunks and optional handoff request.

The reducer uses the existing reservation-domain reducer to move through search, lookup, selection, draft, summary and confirmation. It does not reconstruct domain state from flags. Public offers become canonical domain offer snapshots while private provider IDs stay in the read binding cache. Customer facts are built only from `PrivateCustomerBinding`. Economic terms are built from the closed payment-method fact. On explicit confirmation, the reservation domain produces the authoritative `ReservationCommand`; `ToolDispatch` may verify its binding but may not manufacture it.

The current read adapters expose a private binding hash but intentionally do not retain raw provider IDs. Production therefore uses a `PrivateOfferBindingResolver` during reservation preparation, before the dispatch fence. It repeats the exact provider read from the canonical command query, recomputes public offer IDs and the private binding hash, and returns raw IDs only when all bindings still match. The fenced canonical provider payload contains the resolved private IDs; a mismatch is a terminal pre-provider failure. No raw-ID cache or second state owner is introduced.

The existing reservation-domain reducer produces single-service commands only. Package authorization is owned by a deterministic `PackageCommandCoordinator` in the boundary layer: after both selected single-service drafts are ready, it computes one package subject signature and one `RESERVE_PACKAGE` command with the existing `subject_signature` and `command_identity` functions. The public summary and explicit confirmation bind that package signature/version. The existing `ReservationAllocator` then expands the authorized package command into lodging and activity commands. Separate business-unit obligations are derived after confirmed component outcomes.

### 5. Atomic v8 commit

The host uses `SQLiteBoundaryStore.commit_turn_v8` for one atomic commit of:

- next boundary state/projection;
- source event identities;
- Maya proposal and kernel-decision artifacts;
- read observations and typed facts;
- command rows and relay bundles;
- internal jobs;
- public outbox rows;
- the authenticated turn receipt.

No model or provider runs inside that transaction. Exact replay returns the existing receipt. A divergent payload under the same aggregate turn ID is an identity conflict and enters manual review.

`SQLiteBoundaryStore` gains read-only access to the latest authenticated `ConversationProjection` and transcript receipt graph for a lead. The projection remains a v8 artifact under the boundary owner; it is not copied into a parallel V2 state table.

### 6. Relay and worker composition

The worker process uses the existing fixed queue order:

1. inbox/conversation;
2. reservation relay and reservation execution;
3. payment initiation;
4. payment evidence/settlement;
5. post-payment projection;
6. public ManyChat delivery;
7. reconciliation/handoff.

Every stage is concrete. The host must refuse worker startup if any required port is absent; no noop or fallback worker is allowed. Internal relay jobs use deterministic IDs and copy canonical commands into their existing owner stores exactly once. Completion is derived in the composition root from receipts already owned by boundary, execution, followup/payment and public-outbox stores.

### 7. API composition

The API role remains least-privileged:

- ManyChat ingress uses only `SQLiteInbox`.
- Financial webhooks use short-lived followup-store units of work and strict provider-specific verification ports.
- `/healthz` reports process liveness.
- `/readyz` authenticates local stores and reports gate state; it must not claim worker readiness when the worker composition is unavailable.

Provider-specific webhook verification precedes evidence normalization. Accepted evidence is persisted before HTTP 202. Replays are idempotent; identity conflicts return 409; unauthenticated or unverifiable payloads are rejected before persistence.

## Error and recovery policy

- Model schema violation: retry the same capability-free request once; two invalid responses create one handoff and no command.
- Public read transport failure: retry through a durable internal job up to three total attempts; exhaustion creates one handoff. Stale data is never upgraded to confirmed availability.
- Private profile unavailable/incomplete: emit at most one profile-completion prompt; on the next inbound event re-read once, then handoff if still incomplete. No summary or command is allowed.
- Boundary commit conflict: reload and replay by aggregate turn identity.
- Relay crash after boundary commit: internal job remains pending and is replayed idempotently.
- Reservation/payment pre-call failure: retry according to the existing ledger.
- Post-fence or post-call unknown: manual review/reconciliation; never blind redispatch.
- Public delivery unknown: manual review; never blind resend.
- Active handoff: blocks new command admission and reservation execution before fence.

## Required tests

### Contract tests

- `payment_method` accepts only stripe/wise/pix.
- Any model effect proposal is rejected in the productive path.
- Private customer fields never occur in `ModelRequest`, public observation serialization, reply chunks or logs.
- Missing/expired profile binding cannot produce a summary or command.
- Audited model frames recompose from exact child stdin/stdout and bind the Maya closure.
- Private offer resolution rejects any changed offer ID, amount, dates, party or binding hash before fence.

### Reducer tests

- Inform→read→select→summary→confirm yields one authoritative command.
- Stale summary confirmation yields no command.
- Package confirmation yields one package command that expands to two component commands.
- Discount request yields one handoff and zero reservation/payment rows.
- Active handoff yields no command.
- Duplicate source events yield the same receipt and no new row.

### Crash/replay tests

- Crash before boundary commit calls no provider and replays the turn.
- Crash after boundary commit but before inbox completion reuses the receipt.
- Crash during relay creates one execution command.
- Existing pre/post-fence reservation and payment reconciliation suites remain green.

### End-to-end qualification

The three mandatory scenarios must start from signed ManyChat webhook payloads and run through inbox, model fake, public/private reads, v8 commit, relay, reservation worker, payment initiation, settlement evidence, post-payment projection and public delivery:

1. lodging + Stripe;
2. activity + Pix;
3. package + Wise.

Each scenario must prove exact owner counts, one provider call per idempotency key, one public delivery per chunk, required receipts, completion status and all real-effect gates false. The image runner repeats these scenarios as UID 10001 with a read-only root filesystem.

## Scope exclusions

- No live provider credentials, booking, charge, payment confirmation or ManyChat delivery.
- No legacy cognitive-state import or fallback.
- No new UI.
- No generic plugin/tool execution.
- No attempt to infer missing customer profile values.

## Delivery gates

The sub-slice is complete only when:

- API and worker roles are both concrete in the same image;
- no required worker queue is a noop/fallback;
- the three signed-webhook E2Es pass inside the image;
- architecture guard, proportional regressions, repository-wide final suite, compile, Ruff and diff checks are green;
- `ACTIVE.md` records the final functional commit;
- rollout remains NO-GO until separate operational authorization.
