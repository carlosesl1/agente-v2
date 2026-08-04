# Maya-Owned Reservation-Holder Interpretation Design

**Date:** 2026-08-03
**Status:** approved by Carlos's explicit numbered-flow request
**Branch:** `maya-v2-operational-readiness`
**Baseline:** `b110662461dca1ce5d2f15c0d6bfe366b9ce00a2`
**Supersedes:** the parent-owned deterministic extraction/redaction portions of `2026-08-03-same-turn-private-profile-continuation-design.md`

## 1. Problem

The current V2 ingress runs `collect_private_customer_facts()` before Maya. The function uses regular expressions to identify names, email addresses, countries, and typed phone numbers, persists accepted values, and replaces them with bracketed markers in the model message.

That boundary destroys semantic context. A regex cannot reliably decide whether a value belongs to the lead, a spouse, another passenger, a hostel, or a third party. It also prevents Maya from seeing the complete customer-authored message even though the customer supplied those values for service.

The deterministic extractor must therefore leave the initial conversational path entirely. It must not remain as a hint, preprocessor, progress gate, or fallback attribution mechanism.

## 2. Product Contract

1. Maya receives the exact aggregate customer message in `InboundBatch.combined_text`.
2. Maya interprets whether an explicitly supplied name, email, and country identify the reservation holder.
3. Maya returns holder facts through the existing closed `ModelProposal.facts` grammar.
4. The parent controller validates and canonicalizes only `full_name`, `email`, and `country_code`, persists accepted values in the private owner, and removes those facts from the public proposal before reduction and artifact construction.
5. Valid persisted conversational values win over ManyChat for those three fields. Valid fresh ManyChat values remain fallback when conversational values are absent.
6. Mentions of a companion, spouse, hostel, or third party do not update the holder unless the lead explicitly says that person is or will be the reservation holder.
7. Real ambiguity yields a natural clarification question and no guessed holder fact.
8. `phone_e164` remains exclusively sourced from the authenticated fresh ManyChat/WhatsApp binding. A phone typed in the conversation may remain visible to Maya as context but is discarded if proposed as a holder fact and never changes authenticated identity.
9. A fresh authenticated `phone_e164` is sufficient for read-only commercial provider queries. Missing `full_name`, `email`, or `country_code` must not block availability, price, or description reads; the complete effective profile remains mandatory only for `select`, `confirm`, reservation commands, and provider writes.
10. Public-safe availability observations from committed turns remain available to Maya as bounded `recap_only` consultation history. That history supports later summaries and comparisons but never authorizes selection, confirmation, reservation, payment, or any other effect; those paths still require a fresh current-turn provider read.

## 3. Chosen Architecture

### 3.1 One semantic proposal, no extraction sub-call

Use the existing productive model call and closed `ModelProposal.facts` contract. This avoids a second semantic-extraction model, preserves latency, and allows Maya to return holder facts, commercial facts, read requests, and a natural reply coherently.

Rejected alternatives:

- **Regex as hint or fallback:** rejected because it still decides semantic ownership outside Maya.
- **Dedicated extraction model call:** rejected because it adds latency and another failure/retry surface without increasing parent validation strength.
- **Persist raw message as private profile material:** rejected because only validated structured facts belong in the private profile owner.

### 3.2 Raw model message

Every model call for the aggregate turn receives `batch.combined_text` unchanged:

- initial productive call;
- optional semantic review call;
- post-read follow-up call;
- protocol-repair retry within the adapter.

No name, email, country, or phone replacement is permitted. `ModelRequest` remains `repr=False`, and the adapter does not log stdin or child stdout.

Existing private-presence field names may still be supplied so Maya knows which durable fields already exist, but they never replace the current message or expose stored values.

### 3.3 Prompt semantics

The private-profile system suffix must state:

- the current message is original customer text and may contain private context;
- output `full_name`, `email`, or ISO alpha-2 `country_code` only when the message semantically identifies the reservation holder;
- first-person self-identification is holder evidence;
- spouse, companion, passenger, hostel, property, agency, and third-party values are not holder facts by default;
- an explicit statement that another person will be the reservation holder authorizes that person's holder facts;
- ambiguity requires a natural clarification question with no guessed private fact, provider read, selection, confirmation, or effect;
- never output `phone_e164` from conversational text;
- do not echo private values unnecessarily in the customer-facing reply;
- a proposal may include newly interpreted holder facts and a read request in the same response; the parent validates and persists before dispatching any read.

The protocol-repair prompt preserves these rules through the shared suffix.

### 3.4 Durable consultation continuity

The authenticated boundary receipt/artifact graph remains the single owner of provider-read evidence. At the start of a later turn, the controller loads at most eight committed, public-safe lodging/activity observations for the same `lead_key`, authenticates each artifact against its turn receipt, and projects only public query/result fields into `ModelRequest.consultation_history`.

The model wire omits observation hashes, request hashes, private binding hashes, evidence hashes, and internal offer IDs. Positive history includes public labels, dates, party, total, and currency; negative history preserves the queried service/date/party and empty offers. Every entry carries observation/expiry times plus `fresh_at_turn_start`, while its fixed usage remains `recap_only` even when it has not yet expired.

`ModelRequest.observations` continues to mean current-turn provider evidence. Consultation history is never supplied to the reducer as current evidence and therefore cannot satisfy offer selection or any effect gate. Corrupt history or cross-lead lookup fails closed before model inference.

## 4. Parent Validation and Persistence

### 4.1 Partition before public reduction

For every model proposal stage:

1. partition `full_name`, `email`, `country_code`, and `phone_e164` from public facts;
2. canonicalize approved holder fields with the private owner's canonicalizers;
3. discard conversational `phone_e164` unconditionally;
4. persist accepted holder facts using the authenticated source turn/event identity;
5. recompute effective profile readiness;
6. reconstruct the audited proposal with public facts only before any reducer, public projection, Maya artifact, typed-fact artifact, or kernel input.

Private values may exist transiently in the model wire, audited in-memory frame, parsed private partition, and private SQLite owner. They must not enter the public projection or durable technical artifacts.

### 4.2 Invalid structured facts

If Maya emits a syntactically or semantically invalid holder fact, the parent drops it and returns a generic natural request for the affected field. Error text, exceptions, evidence, and artifacts never contain the rejected value.

A typed phone fact is dropped without becoming identity. It does not itself block progress when the authenticated channel already provides a valid fresh phone. If authenticated phone is missing or stale, existing profile-readiness fences continue blocking provider reads and commands.

### 4.3 Journal and retry

`turn_supplied_fact_names()` remains fully authenticated but is no longer a `collection_only` gate. Its purposes become:

- detect that this aggregate turn already persisted holder facts before a boundary crash;
- preserve the zero-command fence for the aggregate turn on retry;
- authenticate idempotent re-persistence of the same model facts.

On an uncommitted retry, Maya receives the original message again. A replayed private journal cannot authorize a command in that same aggregate turn. On a committed replay, the existing receipt is returned with no additional model or provider call.

## 5. Same-Turn Progress and Critical-Action Safety

A valid model-owned holder update may continue to provider reads and a new reservation summary in the same aggregate turn after persistence.

The aggregate turn is flagged as a private-update turn if either:

- an authenticated journal already records holder facts for this turn; or
- any accepted holder fact is produced by the first, semantic-review, or post-read proposal.

For every private-update turn:

- transform `confirm` before confirmation-read derivation;
- transform any post-read `confirm` again;
- assert zero reservation execution commands immediately after reduction;
- assert zero command rows and zero command relays before commit.

A correction while a summary is pending revokes the old capability and may produce a newly bound summary in the same turn. A later aggregate turn must naturally confirm that new summary.

## 6. Privacy Boundary

The raw current message is authorized model context, not technical evidence. Protections are:

- no raw stdin/stdout persistence; only transcript commitments/hashes are durable;
- `ModelRequest`, `ModelFact`, `ModelProposal`, private snapshots, and writes remain excluded from value-bearing `repr`;
- private holder facts are stripped before public reducer/projection/artifacts;
- when holder facts are accepted, collection/correction/handoff text is parent-owned and generic, including when a model-proposed provider read is rejected or filtered to zero; a same-turn `select` still yields the parent-owned reservation summary;
- an ambiguity that accepted no holder fact preserves Maya's natural clarification;
- the parent never creates `birth_date` or `gender` facts from raw text; a labelled birth date may only be excluded from commercial-date parsing and is not persisted;
- private SQLite journals contain hashes and field names, not raw values;
- new `kernel_decision` artifacts contain only a state/version/command-hash commitment; readers remain compatible with historical full-decision artifacts, but new turns do not duplicate the operational state payload into the artifact graph;
- errors remain categorical and never include values, payloads, SQL dumps, child stdout, or tracebacks with PII;
- provider-facing customer data remains available only through the authenticated effective-customer path at execution time;
- Cloudbeds reservation IDs remain private under existing execution/audit boundaries.

Customer-facing replies and the channel's source message are conversational records, not diagnostic evidence. Maya should avoid unnecessary echoing, but the controller must not redact the message before inference.

## 7. Invariants Preserved

- conversation-first authority for valid `full_name`, `email`, and `country_code`;
- valid/fresh authenticated ManyChat fallback;
- ManyChat-only authenticated phone;
- same-turn collection/correction and summary;
- later natural confirmation required;
- correction invalidates and re-presents summary;
- zero reservation command/relay in a collection/correction turn;
- provider-read gating on a fresh authenticated phone, with the complete effective profile enforced at `select`, `confirm`, reservation-command, and provider-write boundaries;
- source-aware reauthentication before decision and command commit;
- SQLite owner integrity and authenticated journals;
- one-shot/monotonic Cloudbeds execution with at most one POST;
- Bókun GET-only behavior, payment separation, and existing audit fences;
- committed provider observations survive later turns as public recap context without becoming effect authority;
- after current observations exist, recursive model reads become a deterministic zero-read/zero-effect fallback instead of a second provider or repair round; explicit negative evidence yields a truthful unavailable/nothing-booked reply;
- the productive deadline covers up to three model completions with two protocol attempts each plus bounded provider overhead, and the inbox lease remains strictly longer than that deadline;
- private Cloudbeds reservation ID;
- runtime remains `dark_read_only`, kill switch enabled, POST budget disarmed, and broad rollout unsafe.

## 8. Causal Test Matrix

1. The first and follow-up `ModelRequest.message` equal the exact original message, including name, email, country, and typed phone.
2. Adapter wire contains the original private context and no bracketed replacement markers.
3. First-person lead facts from Maya are canonicalized, privately persisted, stripped from public artifacts, and allow same-turn summary after readiness becomes complete.
4. A spouse/companion/hostel/third-party mention with no holder designation produces no holder facts; Maya's clarification reply is preserved.
5. Explicit designation of another person as holder permits that person's structured facts.
6. Regex-shaped third-party values cannot update the holder unless Maya returns them as holder facts; no deterministic extractor is imported or called.
7. Conversational phone proposed by Maya never replaces binding identity and does not appear in the private holder store.
8. Invalid private facts are rejected generically with zero provider reads/commands.
9. Valid correction replaces the old summary and cannot confirm or command in the same turn.
10. Crash/retry uses the authenticated journal as a safety fence, not an acknowledgement-only gate.
11. Committed replay performs zero additional model/read/write calls.
12. Exactly one later valid confirmation can result in at most one Cloudbeds POST; no real transport is used.
13. Technical artifacts, public projection, errors, and `repr` do not contain holder fact values or Cloudbeds reservation ID.
14. A model reply that echoes accepted holder facts is replaced before closure/proposal/public-artifact construction, including the missing/stale authenticated-phone case where its proposed provider read is filtered to zero.
15. Raw text alone cannot create `birth_date` or `gender`; labelled birth dates remain excluded from commercial dates without persistence.
16. New kernel commitments authenticate state/version/command hashes without payload duplication, and the startup semantic scan still accepts historical full decisions.
17. With a fresh authenticated phone but no country, lodging and activity reads execute and return observations while `private_profile_complete=false`.
18. The same incomplete-country turn may read availability, but a post-read `select` remains profile-gated with zero command and zero relay.
19. A later turn receives both positive lodging options and negative activity availability from earlier committed reads while current-turn `observations` remains empty and command/relay counts remain zero.
20. Consultation history is scoped to the exact `lead_key`; modified artifact bytes fail as authenticated data corruption rather than reaching Maya.
21. A follow-up response that repeats reads after current observations uses one child invocation and becomes a deterministic, zero-read, zero-effect fallback.
22. Productive composition binds the inbox lease beyond the complete multi-call turn budget rather than the timeout for one model completion.

## 9. Qualification and Stop Boundary

Run focused contract/executor/E2E/privacy tests, proportional regression, the official local suite, Ruff, boundary checks, compileall, and `git diff --check`. Freeze the exact SHA/tree and obtain an independent read-only final verdict.

Stop before push, CI dispatch, deploy, runtime start, real WhatsApp/ManyChat delivery, payment, or any provider write.
