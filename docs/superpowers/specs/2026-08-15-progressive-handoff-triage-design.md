# Progressive Handoff Triage Design

**Date:** 2026-08-15

**Scope:** Maya V2 conversational behavior and existing handoff path.

## Goal

Keep Maya responsible for completing the normal sales journey through reservation and payment. When automation cannot safely complete it, Maya must still produce useful commercial progress before handoff: identify the lead's interest and, whenever possible, collect everything required up to the reservation or payment boundary.

Handoff remains an exceptional path, not the normal endpoint.

## Non-goals

This change does not add:

- a new workflow, state machine, database table, agent, or provider service;
- automatic discounts, negotiation, cancellation, alteration, or refunds;
- a weaker reservation, payment, idempotency, or provider-evidence gate;
- a promise that a human has read or accepted the conversation without a verified receipt;
- collection questions after an explicit human request, sensitive complaint, or safety concern.

## Approved approach

Use the current conversation, reservation, payment, and handoff workflows. Change only the Maya behavior contract, the existing model progression guidance, the closed operational handoff wording where necessary, and focused regression tests.

Do not create a technical `pre_handoff` phase. The model owns semantic qualification and data collection; deterministic runtime contracts continue to own provider evidence, write authorization, execution certainty, and handoff effects.

## Normal path

The preferred customer journey remains:

```text
interest
→ consultation
→ choice
→ customer and passenger data
→ authenticated summary
→ one natural confirmation
→ reservation
→ payment
→ completion
```

Maya must attempt this path whenever provider evidence and supported business rules permit it. Routine uncertainty or a recoverable missing field does not justify handoff.

## Immediate handoff

Maya opens handoff immediately, without additional triage or questions, only for:

1. an explicit request to speak with a person or the team;
2. a sensitive complaint;
3. a real safety concern or uncertain safety decision requiring human review.

The public acknowledgement is short and does not claim that a human has already read the conversation:

> Vou chamar alguém da nossa equipe para ajudar você por aqui.

The equivalent natural English wording follows the authenticated locale.

## Progressive handoff

The following situations do not normally open handoff at the first sign of difficulty:

- a real discount, coupon, or negotiation request;
- a persistent consultation/provider-read failure;
- inability to create a reservation;
- an uncertain reservation-write result;
- failure to create the card payment link;
- an operational payment problem;
- another supported commercial flow that has reached an exception but can still collect useful facts safely.

Before handoff, Maya advances to the furthest safe stage allowed by current evidence. It must not repeat a provider write, fabricate a result, or ask for data unrelated to completing or handing off the requested service.

### Stage A: interest not yet qualified

Collect naturally, without reciting a form:

- service: lodging, activity, or package;
- desired activity/accommodation or relevant preferences;
- date or period;
- adults and children;
- material needs or constraints voluntarily raised by the lead.

After this minimum qualification, use wording equivalent to:

> Já entendi o que você procura e reuni os principais detalhes. Agora uma pessoa da nossa equipe vai assumir a conversa e continuar seu atendimento por aqui.

### Stage B: option selected or reservation preparation started

Collect every still-missing field required by the existing reservation contracts:

- exact selected option and service dates;
- party composition;
- authenticated contact/profile completeness;
- required holder and passenger fields;
- payment method;
- required provider answers exposed by the current reservation path.

When all required data exists but the reservation cannot safely be finalized, use wording equivalent to:

> Já temos tudo para realizar sua reserva. Agora uma pessoa da nossa equipe vai assumir a conversa e finalizar seu atendimento por aqui.

This message does not claim that the reservation exists.

### Stage C: reservation write failed or became uncertain

Do not ask the lead to repeat data already collected. Do not retry a write whose outcome is uncertain. Preserve the collected commercial subject and state the evidence honestly:

> Já temos todos os seus dados e os detalhes da reserva. Tivemos uma dificuldade na finalização, então uma pessoa da nossa equipe vai assumir a conversa e concluir essa etapa por aqui.

If the canonical outcome proves that no reservation was created, Maya may say so. If the outcome is uncertain, Maya must not claim either creation or non-creation.

### Stage D: payment problem after reservation

Never recreate or repeat the reservation merely because payment initiation or payment handling failed. Preserve the confirmed reservation anchor and transfer only the financial finalization:

> Sua reserva e seus dados já estão preparados. Uma pessoa da nossa equipe vai assumir a conversa e ajudar você a finalizar o pagamento por aqui.

Use `reserva` as confirmed only when the runtime supplies a confirmed reservation outcome. Otherwise use non-confirming wording such as `os detalhes para sua reserva`.

## Negotiation behavior

A discount, coupon, or negotiation request still requires a human decision, but Maya first performs safe commercial triage unless an immediate-handoff condition is also present.

Examples:

- If product, dates, and party are missing, collect them before handoff.
- If an exact current option is already available, preserve its authenticated price and collect reservation data as far as safely possible.
- Never calculate, promise, imply, or apply a negotiated discount.
- The standard 20% agency signal is not negotiation and must continue through automation.

## Evidence and precedence

Public reply precedence remains:

```text
private/safety leak correction
→ confirmed provider outcome
→ immediate handoff condition
→ progressive handoff after useful collection
→ missing-field or confirmation follow-up
```

Additional invariants:

1. Explicit human request, sensitive complaint, and safety concern override progressive collection.
2. A handoff request is terminal for that turn: do not append consultation, collection, or confirmation questions.
3. A progressive-handoff decision is emitted only after the current turn has collected or exhausted the useful safe facts available for its stage.
4. Existing `handoff_status` receipt semantics remain authoritative; `requested`, `active`, and `acknowledgement_pending` never prove human reading.
5. Reservation/payment wording is derived from canonical outcomes, never from model confidence.
6. Handoff cannot reopen or duplicate a reservation or payment command.

## Components changed

### Maya system prompt

Refine the HANDOFF and payment/error guidance to distinguish:

- immediate handoff reasons;
- progressive handoff reasons;
- minimum qualification before progressive handoff;
- full reservation-data collection when an option is already selected;
- evidence-aware wording after reservation/payment failures.

### Model progression suffix

Add high-salience guidance that recoverable operational difficulty should continue collecting useful facts rather than immediately returning `request_handoff`. This suffix remains semantic guidance only and grants no effect authority.

### Closed operational handoff projection

Where handoff is opened by a deterministic operational failure rather than Maya's current public reply, select a safe fixed message based on canonical reservation/payment certainty. Do not accept arbitrary error text or private data.

### Existing handoff workflow

No structural changes. Reuse the current durable handoff relay, queue, ManyChat tag/flow delivery, idempotency, receipt semantics, and terminal effect guard.

## Testing

Focused tests must prove:

1. explicit human request produces immediate handoff with no collection question;
2. sensitive complaint produces immediate handoff;
3. safety concern produces immediate handoff;
4. discount request with incomplete qualification collects the next useful commercial facts before handoff;
5. discount request with a selected option collects reservation-required fields without promising a discount;
6. recoverable consultation failure does not immediately hand off while useful qualification is missing;
7. reservation failure after complete collection produces progressive handoff and no duplicate provider write;
8. uncertain reservation result uses uncertainty-safe wording;
9. payment-link failure after a confirmed reservation preserves that reservation and does not create another command;
10. no public reply claims reservation, payment, human reading, or delivery without the corresponding canonical receipt;
11. terminal handoff suppresses stale confirmation and collection prompts;
12. Portuguese and English replies follow authenticated locale and remain WhatsApp-natural.

Verification should include prompt-contract tests, model-adapter behavior tests, reducer/turn-executor handoff tests, public handoff projection tests, focused regression suites, Ruff, compile checks, boundary checks, and an isolated no-effect conversational smoke before deployment.

## Rollout

1. Implement with test-first focused regressions.
2. Run affected suites and static checks.
3. Build one immutable image with rollback to the current digest.
4. Run an isolated conversational smoke with provider writes, payment creation, ManyChat delivery, and handoff delivery closed or mocked.
5. Promote API, worker, and router consistently.
6. Verify image identity, health, readiness, and worker logs.
7. Conduct a controlled natural WhatsApp/ManyChat test only under separate authorization.

The deployment smoke must not create reservations, payment links, handoffs, or outbound customer messages.
