# Contextual confirmation binding — design

## Status

Approved by the controller using the conservative default after the interactive approval window elapsed. This design is limited to contextual confirmation classification and binding. It does not change Cloudbeds transport, provider idempotency, payment scope, or rollout authority.

## Problem

A pending critical action already carries the exact public summary, summary version, action scope, and expiry. Today a general-purpose model proposal must do two jobs at once:

1. understand whether the current message semantically approves that exact summary; and
2. reconstruct the full approval assertion (`intent=confirm`, summary version, action kinds, and contextual basis).

When the first proposal returns `inform`, the executor asks the same broad proposal contract to classify the message again. A second `inform` leaves the parent without an independent semantic verdict. In the controlled Cloudbeds canary, an unequivocal contextual approval remained `inform`, so the runtime correctly emitted zero commands and zero provider writes but could not complete the end-to-end gate.

Prompt-only reinforcement is insufficient because it leaves semantic classification coupled to reply generation and exact binding reconstruction. Lexical rules are prohibited: no word, phrase, regex, emoji, locale-specific allowlist, or substring may authorize a critical effect.

## Safety invariant

A critical effect is authorized only when all of the following hold:

- an exact `PendingCriticalActionContext` exists;
- its expiry has not elapsed;
- the current workflow projection still matches the summary subject;
- the runtime capability policy still permits every pending action;
- a narrow semantic review classifies the complete current message against the complete pending public summary as `approve`;
- the parent binds the approval to the pending summary version, exact action tuple, and `contextual_reference` basis;
- the normal refresh reads and post-read binding checks succeed.

Every missing, invalid, contradictory, expired, conditional, ambiguous, or failed review remains non-authorizing.

## Closed semantic-review contract

Introduce a dedicated closed wire response:

```json
{
  "schema": "v2-contextual-confirmation-review-v1",
  "source_event_id": "<exact current source event>",
  "decision": "approve|reject|adjust|uncertain"
}
```

The request contains only the data required for the semantic comparison:

- request and source-event identities;
- current message and locale;
- pending public summary;
- pending summary version;
- pending action kinds;
- pending expiry.

It must not contain provider references, offer IDs, subject signatures, profile values, credentials, observations, or private customer data.

Decision semantics:

- `approve`: the complete message unconditionally approves the complete pending summary without changing or narrowing any material term;
- `reject`: the message refuses, cancels, postpones, or withdraws approval;
- `adjust`: the message adds a condition or changes product, dates, party, price, currency, payment, or effect scope;
- `uncertain`: question, ambiguity, hesitation, unrelated text, or insufficient evidence.

No decision is inferred from words alone. The classifier evaluates semantic entailment of the complete message against the complete summary. Examples may be used only in tests as diverse witnesses; they are never runtime rules.

## Parent-owned binding

The model never supplies authoritative summary/action binding in the narrow review. After a valid `approve` decision, the adapter constructs a normal `ModelProposal` with:

- `intent="confirm"`;
- no facts, passengers, selection, or model-proposed effects;
- `confirmed_summary_version` copied from the pending context;
- `confirmed_action_kinds` copied from the pending context;
- `approval_basis=contextual_reference`.

The existing executor and reducer revalidate expiry, projection equality, capability policy, refresh reads, subject binding, and command derivation. The narrow review therefore removes a false-negative source without granting the model new authority.

For `reject` and `adjust`, the review maps to a non-authorizing adjustment with revocation. `uncertain`, malformed output, child failure, timeout, or protocol fallback remains `inform` and preserves zero effect.

## Child protocol

The tool-free child must not append a competing model-proposal schema version. Its wrapper instruction is schema-neutral: return exactly one JSON object matching the supplied system contract. The general proposal prompt continues to require `v2-model-proposal-v6`; the narrow review prompt requires only `v2-contextual-confirmation-review-v1`.

Each review uses a distinct deterministic request identity from the first general classification. Frames remain transcript-authenticated and are appended to the same audited turn.

## Data flow

1. Load and validate the pending action.
2. Run the normal general proposal.
3. If it already returns a bound `confirm`, continue unchanged.
4. If it returns `inform` without reads while a pending action exists, issue a distinct narrow semantic-review request.
5. Parse the exact review schema.
6. Parent-map `approve` to a bound confirmation; map all other outcomes to non-authorizing proposals.
7. Re-run existing material-scope, expiry, capability, refresh-read, reducer, fence, and commit checks.
8. Only then can a reservation command be committed.

## TDD and verification

RED tests must demonstrate the current failure before production edits:

- the narrow review schema is unsupported;
- the child appends a conflicting fixed schema instruction;
- an `approve` verdict does not yet become a parent-bound `confirm`;
- a general `inform` followed by semantic `approve` does not yet commit the command;
- distinct natural paraphrases cannot depend on a phrase list.

GREEN coverage must include:

- Portuguese and English contextual approvals with structurally different wording;
- a terse approval and a longer paraphrase that repeats no canonical example;
- question, hesitation, refusal, postponement, conditional approval, product/date/party/price/payment change, and scope narrowing;
- missing/expired pending action;
- malformed, duplicate-key, wrong-source, unknown-decision, timeout, nonzero child, and protocol-fallback outputs;
- distinct request identities and authenticated transcript frames;
- refresh mismatch and capability closure still preventing commands;
- replay still producing no duplicate command;
- existing activity, package, Cloudbeds, Bókun, reducer, executor, child, adapter, and prompt regressions.

A real-model sandbox test is required after deterministic tests. It may exercise only the classifier with synthetic public summaries and must have no provider, payment, handoff, delivery, or filesystem side-effect capability. A new live provider canary requires separate authorization and is outside this correction.

## Acceptance criteria

- No lexical authorization helper exists in production code.
- A valid narrow `approve` is deterministically bound by the parent to the exact pending context.
- Every non-approve or invalid review remains zero-effect.
- Existing confirmation guards remain intact and tests prove they still dominate.
- The exact candidate passes focused tests, causal regression, static checks, independent review, and real-model sandbox classification before publication.
