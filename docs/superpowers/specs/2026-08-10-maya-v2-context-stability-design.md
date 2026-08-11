# Maya V2 Conversational Context Stability Design

**Date:** 2026-08-10

**Status:** Approved for local implementation by Carlos Eduardo

**Base:** `9d3be1a45a5798a1982f906e1fdf192695e01973`

## 1. Problem

The qualified base is transactionally safe and runtime-stable, but repeated real-model conversations exposed four causal conversational failures:

1. the model receives the complete current message but not a bounded public dialogue history, so a weak first frame can erase the topic on the next turn;
2. an `inform` proposal containing only generic prose is structurally valid, so a no-progress generation can be delivered without a semantic second look;
3. positive-observation grounding replaces the entire reply, deleting a valid holder clarification question together with an ungrounded commercial claim;
4. `handoff_active: bool` collapses requested/pending/acknowledged/completed states and lets the model describe a pending internal relay as if a human were already following the conversation.

A controlled ablation using the same model and prompt proved that a short high-salience progression instruction and bounded dialogue each improved the target behavior from a variable baseline to `3/3` successful repetitions. No business provider was called.

## 2. Goals

- Preserve conversational continuity across turns without exposing private values publicly.
- Keep Maya as the sole semantic interpreter of the original customer message.
- Ensure a complete commercial request advances in the same turn, or produces an explicit clarification, conclusion, or real handoff.
- Give one bounded model-owned retry to a structurally empty proposal.
- Ground accepted provider results without erasing a separately typed clarification question.
- Represent handoff lifecycle truthfully from durable status and receipts.
- Preserve every existing transactional, privacy, provider, and idempotency guard.

## 3. Non-goals

- No deterministic NLU, date/person/country/intent extractor, regex interpretation, customer-text alias matching, or keyword trigger in parent code.
- No controller-derived read request, fact, selection, confirmation, or handoff from customer prose.
- No unbounded transcript, general vector memory, or public projection of raw dialogue.
- No deploy, canary, production provider write, or broad rollout in this phase.
- No weakening of confirmation, Stripe, Wise, Pix, Cloudbeds, Bókun, outbox, receipt, or writer/reconciler authority.

## 4. Architecture

### 4.1 Private bounded dialogue owner

The existing private customer SQLite owner gains a `private_dialogue_turns` table in the same `0600` file. Each committed turn stores:

- `lead_id`;
- `source_turn_id`;
- aggregate `source_event_hash`;
- exact current customer message;
- exact committed public reply chunks;
- a domain-separated content hash;
- committed UTC timestamp.

Writes are exact-idempotent by `(lead_id, source_turn_id)`. Divergent replay fails closed. The transaction prunes older rows and retains only the newest four committed exchanges per lead.

Recording happens after the boundary commit and before `execute()` returns. If a process fails in that narrow interval, the inbox replay path reconstructs the same committed reply from the receipt and repairs the idempotent private dialogue row before returning. A failed private write therefore causes retry rather than silent continuity loss.

Raw dialogue is never included in boundary artifacts, technical logs, public state, evidence, hashes with reversible material, or `repr`. Only the model request receives it.

### 4.2 Closed model context contract

`ModelRequest` gains:

```python
recent_dialogue: tuple[ConversationExchange, ...]  # oldest to newest, max 4
handoff_status: str | None
progress_review_required: bool
```

`ConversationExchange` is `repr=False`, bounded, and contains only:

```python
customer_message: str
assistant_reply_chunks: tuple[str, ...]
```

The adapter serializes committed dialogue as alternating private model messages before the final current-request JSON. The child accepts only a closed odd-length sequence of `user, assistant, ..., user`, with at most four exchanges plus the current request. It renders the history as explicitly untrusted transcript context, never as system instruction.

The current message remains a separate complete field. Accepted commercial state facts, private field-presence names, consultation evidence, pending action, execution status, and handoff status remain separate typed channels.

### 4.3 One model-owned progress review

The parent evaluates only proposal structure. It does not inspect customer words or infer intent.

An initial proposal is structurally empty when all are true:

- `intent == "inform"`;
- no facts other than optional authoritative language;
- no read requests;
- no target or selection request;
- no passengers or effects;
- no separately typed clarification question.

When there is no pending critical action, active reservation execution, active/terminal handoff, or parent-owned critical outcome, this shape triggers one second `ModelRequest` with `progress_review_required=True`.

The second call receives the same original message, recent dialogue, typed state, and private presence markers. A high-salience terminal protocol tells Maya to:

- emit the missing typed facts/read now when the request is complete;
- otherwise emit one explicit typed clarification question;
- or return a genuinely conclusive grounded answer;
- never invent a read, fact, effect, or acknowledgement.

The parent accepts the second valid proposal even if it remains an informational answer. There is no loop. If it produces reads, the normal single read round follows. Existing confirmation and selection semantic reviews remain mutually exclusive with the progress review.

### 4.4 Typed clarification and granular positive grounding

The model proposal protocol advances to `v2-model-proposal-v7` and adds:

```json
"clarification_question": null | "one explicit customer-facing question"
```

The field is semantic model output, not extracted by the controller. When set, it is canonical, bounded, and must also be appropriate to the proposal contract. The prompt requires Maya to put the actual next question there rather than hiding it only in prose.

For positive current-turn provider observations, the parent no longer scans generated prose with availability/price keywords or regex. It always renders accepted typed positive payloads through the deterministic provider-grounding renderer. It discards the untrusted commercial draft for that positive result and appends the separately typed clarification question, if any. Thus a commercial claim cannot erase the question, and the controller never classifies natural language.

For turns without positive observations, the proposal reply remains model-owned. Negative and knowledge paths retain their existing typed-observation rules.

### 4.5 Exact handoff lifecycle

`handoff_active: bool` is removed from the model wire and replaced by the exact durable status:

```text
null
requested
active
acknowledgement_pending
acknowledged
manual_review
completed
cancelled
```

The adapter adds a terminal handoff protocol:

- `requested`, `active`, and `acknowledgement_pending`: a request/relay exists, but no human receipt is proven; never say a person is already monitoring;
- `acknowledged`: the configured external handoff acknowledgement has a receipt; say the forwarding was acknowledged, not that a human has read the chat;
- `manual_review`: delivery requires internal review; do not promise receipt;
- `completed`: human service is durably completed;
- `cancelled`: the handoff is closed/cancelled.

A second `request_handoff` remains forbidden whenever a nonterminal handoff already exists.

## 5. Prompt salience

A compact `TURN COMPLETION PRIORITY` suffix is always the final part of the effective system prompt. It reiterates, in this order:

1. use the complete current message plus bounded committed dialogue;
2. advance a complete typed query now;
3. ask one explicit typed clarification when material information is missing;
4. conclude grounded questions naturally;
5. never use generic future-tense placeholders;
6. never infer human receipt from pending handoff.

The suffix does not contain customer keyword lists and grants read-only semantic authority only.

## 6. Dialogue integrity

- Private dialogue DB mode remains `0600` and is a distinct durable owner from public artifacts.
- Dataclasses carrying raw dialogue use `repr=False`.
- Only four committed exchanges are retained and exposed.
- Hashes bind exact source turn, event hash, customer message, public reply chunks, and timestamp.
- Replays with divergent content fail closed.
- No raw context enters `model_calls`, boundary artifacts, evidence, exceptions, or logs; existing transcript frames continue storing commitments/hashes only.
- Customer data may reach the model through the original message and may be repeated in Maya-authored public text. Dialogue storage rules do not authorize output inspection, correction, redaction, or blocking.

## 7. Completion and stability criteria

The successor may be called qualified only when:

1. causal RED tests prove all four baseline failures and then pass;
2. no new customer-text regex, alias table, substring test, token allowlist, or keyword branch exists in controller/application code;
3. focused tests, full pytest, Ruff, compileall, boundary checks, Compose validation, and CI-equivalent jobs pass;
4. repeated real-Hermes/fake-provider conversations complete target journeys without no-op, lost context, false human acknowledgement, duplicate read/effect, output privacy intervention, or external receipt;
5. provider POST count and external effect count remain zero during qualification;
6. the new commit/tree/OCI identity is frozen and the rollout decision is reported separately.
