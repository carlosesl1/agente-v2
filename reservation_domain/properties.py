"""Deterministic property runner for reducer sequence invariants."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import random

from .reducer import reduce
from .types import (
    AddOn,
    AwaitingConfirmationState,
    CollectingState,
    ConfirmationDecisionKind,
    ConfirmationReceived,
    CustomerFacts,
    DraftAdjusted,
    DraftRequested,
    EconomicTerms,
    ExecutionCertainty,
    ExecutionFinished,
    ExecutionOutcome,
    ExecutionQueuedState,
    ExecutionStarted,
    ExecutingState,
    LookupEvidence,
    LookupRecorded,
    LookupStatus,
    ManualReviewRequested,
    Money,
    OfferChosen,
    OfferSnapshot,
    OfferedState,
    Party,
    ReadyToSummarizeState,
    SearchQuery,
    SearchingState,
    SelectedState,
    ServiceKind,
    StartSearch,
    SummaryRecorded,
    UncertainState,
    WorkflowCancelled,
    WorkflowExpired,
)
from .reducer import new_workflow


@dataclass(frozen=True, slots=True)
class PropertyReport:
    sequences: int
    max_events: int
    seed: int
    transitions: int
    applied: int
    ignored: int
    rejected: int
    exceptions: int
    premature_commands: int
    second_commands: int
    duplicate_reemissions: int
    conflicting_duplicate_acceptances: int
    authorized_accepts: int
    missing_authorized_commands: int
    out_of_order_probes: int
    out_of_order_policy_violations: int
    lookup_positive_cases: int
    lookup_negative_cases: int
    lookup_expired_cases: int
    lookup_unavailable_cases: int
    lookup_multi_offer_cases: int
    violations: tuple[str, ...]


def _same_business_state(left, right) -> bool:
    return type(left) is type(right) and replace(right, meta=left.meta) == left


def _lookup_case(event) -> str | None:
    if not isinstance(event, LookupRecorded):
        return None
    if event.evidence.status is not LookupStatus.POSITIVE:
        return "negative"
    if not event.evidence.is_fresh(event.occurred_at):
        return "expired"
    if any(not offer.available for offer in event.offers):
        return "unavailable"
    if len(event.offers) > 1:
        return "multi_offer"
    return "positive"


def _query(sequence: int) -> SearchQuery:
    service = ServiceKind.LODGING if sequence % 2 == 0 else ServiceKind.ACTIVITY
    return SearchQuery(
        service=service,
        start_date=date(2027, 1, 10),
        end_date=date(2027, 1, 13) if service is ServiceKind.LODGING else None,
        start_time=None if service is ServiceKind.LODGING else "08:00",
        party=Party(adults=1 + sequence % 3, children=sequence % 2),
    )


def _evidence(sequence: int, state: SearchingState, now) -> LookupEvidence:
    return LookupEvidence(
        lookup_id=f"lookup:{sequence}",
        service=state.query.service,
        query_signature=state.query.signature,
        observed_at=now,
        expires_at=now + timedelta(minutes=5),
        snapshot_hash=(f"{sequence:064x}"[-64:]),
        status=LookupStatus.POSITIVE,
    )


def _offer(sequence: int, state: SearchingState, lookup_id: str) -> OfferSnapshot:
    return OfferSnapshot(
        offer_id=f"offer:{sequence}",
        lookup_id=lookup_id,
        service=state.query.service,
        provider_ref=f"provider:{sequence}",
        public_label=f"Synthetic option {sequence}",
        start_date=state.query.start_date,
        end_date=state.query.end_date,
        start_time=state.query.start_time,
        party=state.query.party,
        total=Money(amount=Decimal("100.00") + sequence % 50, currency="BRL"),
        available=True,
    )


def _terms(sequence: int, variant: int = 0) -> EconomicTerms:
    return EconomicTerms(
        payment_method="card" if variant % 2 == 0 else "cash",
        add_ons=(
            AddOn(
                code=f"addon:{sequence % 7}",
                quantity=1 + variant % 3,
                unit_price=Money(amount=Decimal("10.00"), currency="BRL"),
            ),
        ),
    )


def _customer(sequence: int, variant: int = 0) -> CustomerFacts:
    return CustomerFacts(
        customer_ref=f"customer:{sequence}",
        full_name=f"Synthetic Person {sequence}-{variant}",
        email=f"synthetic.person.{sequence}.{variant}" + chr(64) + "example.invalid",
        phone_e164=f"+999{sequence % 100_000_000:08d}",
        country_code="ZZ",
    )


def _guided_event(sequence: int, step: int, state, now, rng: random.Random):
    event_id = f"evt:{sequence}:{step}:guided"
    if isinstance(state, CollectingState):
        return StartSearch(event_id=event_id, occurred_at=now, query=_query(sequence))
    if isinstance(state, SearchingState):
        evidence = _evidence(sequence, state, now)
        return LookupRecorded(
            event_id=event_id,
            occurred_at=now,
            evidence=evidence,
            offers=(_offer(sequence, state, evidence.lookup_id),),
        )
    if isinstance(state, OfferedState):
        return OfferChosen(
            event_id=event_id,
            occurred_at=now,
            offer_id=state.offers[0].offer_id,
        )
    if isinstance(state, SelectedState):
        return DraftRequested(
            event_id=event_id,
            occurred_at=now,
            draft_id=f"draft:{sequence}",
            customer=_customer(sequence),
            terms=_terms(sequence),
        )
    if isinstance(state, ReadyToSummarizeState):
        if rng.random() < 0.15:
            return DraftAdjusted(
                event_id=event_id,
                occurred_at=now,
                customer=_customer(sequence, step),
                terms=_terms(sequence, step),
            )
        return SummaryRecorded(
            event_id=event_id,
            occurred_at=now,
            summary_event_id=f"summary:{sequence}:{state.draft.version}",
            draft_version=state.draft.version,
            subject_signature=state.draft.subject_signature,
            outbox_message_id=f"outbox:{sequence}:{state.draft.version}",
        )
    if isinstance(state, AwaitingConfirmationState):
        decision = rng.choices(
            (
                ConfirmationDecisionKind.ACCEPT,
                ConfirmationDecisionKind.AMBIGUOUS,
                ConfirmationDecisionKind.ADJUST,
            ),
            weights=(8, 1, 1),
            k=1,
        )[0]
        if decision is ConfirmationDecisionKind.ADJUST:
            return DraftAdjusted(
                event_id=event_id,
                occurred_at=now,
                customer=_customer(sequence, step),
                terms=_terms(sequence, step),
            )
        return ConfirmationReceived(
            event_id=event_id,
            occurred_at=now,
            confirmation_event_id=f"confirm:{sequence}:{step}",
            decision=decision,
            target_draft_version=state.draft.version,
            subject_signature=state.draft.subject_signature,
        )
    if isinstance(state, ExecutionQueuedState):
        return ExecutionStarted(
            event_id=event_id,
            occurred_at=now,
            command_id=state.command.command_id,
        )
    if isinstance(state, ExecutingState):
        certainty = rng.choice(tuple(ExecutionCertainty))
        provider_reference = (
            f"reservation:{sequence}"
            if certainty is ExecutionCertainty.EFFECT_CONFIRMED
            else None
        )
        outcome = state.command.outcome(
            certainty=certainty,
            normalized_status=f"result:{certainty.value}",
            provider_reference=provider_reference,
        )
        return ExecutionFinished(
            event_id=event_id,
            occurred_at=now,
            command_id=state.command.command_id,
            outcome=outcome,
        )
    if isinstance(state, UncertainState):
        return ManualReviewRequested(
            event_id=event_id,
            occurred_at=now,
            reason="reconciliation_required",
        )
    return WorkflowExpired(
        event_id=event_id,
        occurred_at=now,
        reason="property_terminal_probe",
    )


def _arbitrary_event(sequence: int, step: int, state, now, rng: random.Random):
    event_id = f"evt:{sequence}:{step}:arbitrary"
    choice = rng.randrange(12)
    query = _query(sequence + step)
    if choice == 0:
        return StartSearch(event_id=event_id, occurred_at=now, query=query)
    if choice == 1:
        variant = (sequence + step) % 5
        observed_at = now
        expires_at = now + timedelta(minutes=1)
        status = LookupStatus.POSITIVE
        if variant == 1:
            status = LookupStatus.NEGATIVE
        elif variant == 2:
            observed_at = now - timedelta(minutes=2)
            expires_at = now - timedelta(seconds=1)
        fake_evidence = LookupEvidence(
            lookup_id=f"lookup:invalid:{sequence}:{step}",
            service=query.service,
            query_signature=query.signature,
            observed_at=observed_at,
            expires_at=expires_at,
            snapshot_hash=(f"{sequence + step + 1:064x}"[-64:]),
            status=status,
        )
        fake_state = SearchingState(meta=state.meta, query=query)
        first_offer = _offer(
            sequence + step + 1,
            fake_state,
            fake_evidence.lookup_id,
        )
        if variant == 1:
            offers = ()
        elif variant == 3:
            offers = (replace(first_offer, available=False),)
        elif variant == 4:
            offers = (
                first_offer,
                _offer(
                    sequence + step + 2,
                    fake_state,
                    fake_evidence.lookup_id,
                ),
            )
        else:
            offers = (first_offer,)
        return LookupRecorded(
            event_id=event_id,
            occurred_at=now,
            evidence=fake_evidence,
            offers=offers,
        )
    if choice == 2:
        return OfferChosen(
            event_id=event_id,
            occurred_at=now,
            offer_id=f"offer:unknown:{sequence}:{step}",
        )
    if choice == 3:
        return DraftRequested(
            event_id=event_id,
            occurred_at=now,
            draft_id=f"draft:random:{sequence}:{step}",
            customer=_customer(sequence, step),
            terms=_terms(sequence, step),
        )
    if choice == 4:
        return DraftAdjusted(
            event_id=event_id,
            occurred_at=now,
            customer=_customer(sequence, step),
            terms=_terms(sequence, step),
        )
    if choice == 5:
        return SummaryRecorded(
            event_id=event_id,
            occurred_at=now,
            summary_event_id=f"summary:random:{sequence}:{step}",
            draft_version=1,
            subject_signature="f" * 64,
            outbox_message_id=f"outbox:random:{sequence}:{step}",
        )
    if choice == 6:
        return ConfirmationReceived(
            event_id=event_id,
            occurred_at=now,
            confirmation_event_id=f"confirm:random:{sequence}:{step}",
            decision=rng.choice(tuple(ConfirmationDecisionKind)),
            target_draft_version=1 + rng.randrange(3),
            subject_signature="e" * 64,
        )
    if choice == 7:
        return ExecutionStarted(
            event_id=event_id,
            occurred_at=now,
            command_id=f"cmd:unknown:{sequence}:{step}",
        )
    if choice == 8:
        command_id = f"cmd:unknown:{sequence}:{step}"
        return ExecutionFinished(
            event_id=event_id,
            occurred_at=now,
            command_id=command_id,
            outcome=ExecutionOutcome(
                command_id=command_id,
                certainty=ExecutionCertainty.NOT_CALLED,
                normalized_status="not_called",
            ),
        )
    if choice == 9:
        return ManualReviewRequested(
            event_id=event_id,
            occurred_at=now,
            reason="random_review",
        )
    if choice == 10:
        return WorkflowCancelled(
            event_id=event_id,
            occurred_at=now,
            reason="random_cancel",
        )
    return WorkflowExpired(
        event_id=event_id,
        occurred_at=now,
        reason="random_expiry",
    )


def run_property_sequences(
    *,
    sequences: int,
    max_events: int,
    seed: int,
) -> PropertyReport:
    if sequences < 1 or max_events < 1:
        raise ValueError("sequences and max_events must be positive")
    rng = random.Random(seed)
    violations: list[str] = []
    exceptions = 0
    premature_commands = 0
    second_commands = 0
    duplicate_reemissions = 0
    conflicting_duplicate_acceptances = 0
    authorized_accepts = 0
    missing_authorized_commands = 0
    out_of_order_probes = 0
    out_of_order_policy_violations = 0
    lookup_positive_cases = 0
    lookup_negative_cases = 0
    lookup_expired_cases = 0
    lookup_unavailable_cases = 0
    lookup_multi_offer_cases = 0
    transitions = 0
    applied = 0
    ignored = 0
    rejected = 0

    for sequence in range(sequences):
        started_at = datetime(
            2026, 12, 1, 0, 0, tzinfo=timezone.utc
        ) + timedelta(seconds=sequence)
        state = new_workflow(
            workflow_id=f"workflow:property:{sequence}",
            started_at=started_at,
        )
        emitted: set[str] = set()
        prior_events = []
        for step in range(max_events):
            now = state.meta.last_event_at + timedelta(seconds=1)
            if rng.random() < 0.08:
                now = state.meta.last_event_at - timedelta(seconds=1)
            if prior_events and rng.random() < 0.12:
                event = rng.choice(prior_events)
                duplicate = True
                conflicting_duplicate = rng.random() < 0.15
                if conflicting_duplicate:
                    event = replace(
                        event,
                        occurred_at=event.occurred_at + timedelta(microseconds=1),
                    )
            else:
                duplicate = False
                conflicting_duplicate = False
                if rng.random() < 0.62:
                    event = _guided_event(sequence, step, state, now, rng)
                else:
                    event = _arbitrary_event(sequence, step, state, now, rng)
                prior_events.append(event)
            old_state = state
            out_of_order_probe = bool(
                not duplicate and event.occurred_at < old_state.meta.last_event_at
            )
            valid_authorization = bool(
                not duplicate
                and not out_of_order_probe
                and isinstance(old_state, AwaitingConfirmationState)
                and isinstance(event, ConfirmationReceived)
                and event.decision is ConfirmationDecisionKind.ACCEPT
                and event.target_draft_version == old_state.draft.version
                and event.subject_signature == old_state.draft.subject_signature
                and event.subject_signature == old_state.summary.subject_signature
                and event.occurred_at > old_state.summary.presented_at
            )
            if not duplicate:
                lookup_case = _lookup_case(event)
                lookup_positive_cases += lookup_case == "positive"
                lookup_negative_cases += lookup_case == "negative"
                lookup_expired_cases += lookup_case == "expired"
                lookup_unavailable_cases += lookup_case == "unavailable"
                lookup_multi_offer_cases += lookup_case == "multi_offer"
            try:
                transition = reduce(state, event)
            except Exception as exc:  # property report captures totality failures
                exceptions += 1
                if len(violations) < 20:
                    violations.append(
                        f"sequence={sequence} step={step} exception={type(exc).__name__}:{exc}"
                    )
                break
            transitions += 1
            applied += transition.status.value == "applied"
            ignored += transition.status.value == "ignored"
            rejected += transition.status.value == "rejected"
            if out_of_order_probe:
                out_of_order_probes += 1
                preserves_policy = bool(
                    transition.status.value == "rejected"
                    and transition.reason == "out_of_order_event"
                    and not transition.commands
                    and _same_business_state(old_state, transition.state)
                )
                if not preserves_policy:
                    out_of_order_policy_violations += 1
                    if len(violations) < 20:
                        violations.append(
                            f"sequence={sequence} step={step} out_of_order_policy_violation"
                        )
            if duplicate and (transition.state != old_state or transition.commands):
                duplicate_reemissions += 1
                if len(violations) < 20:
                    violations.append(f"sequence={sequence} step={step} duplicate_changed_state")
            if conflicting_duplicate and (
                transition.status.value != "rejected"
                or transition.reason != "conflicting_duplicate_event"
            ):
                conflicting_duplicate_acceptances += 1
                if len(violations) < 20:
                    violations.append(
                        f"sequence={sequence} step={step} conflicting_duplicate_accepted"
                    )
            if valid_authorization:
                authorized_accepts += 1
                command_contract_holds = bool(
                    len(transition.commands) == 1
                    and isinstance(transition.state, ExecutionQueuedState)
                    and transition.status.value == "applied"
                    and transition.reason == "reservation_command_created"
                    and transition.state.draft == old_state.draft
                    and transition.state.command == transition.commands[0]
                    and transition.commands[0].workflow_id == old_state.meta.workflow_id
                    and transition.commands[0].draft_id == old_state.draft.draft_id
                    and transition.commands[0].draft_version == old_state.draft.version
                    and transition.commands[0].subject_signature
                    == old_state.draft.subject_signature
                    and transition.commands[0].payload.components
                    == old_state.draft.components
                    and transition.commands[0].payload.customer
                    == old_state.draft.customer
                    and transition.commands[0].payload.terms == old_state.draft.terms
                    and transition.commands[0].created_at == event.occurred_at
                )
                if not command_contract_holds:
                    missing_authorized_commands += 1
                    if len(violations) < 20:
                        violations.append(
                            f"sequence={sequence} step={step} authorized_command_missing_or_invalid"
                        )
            elif transition.commands:
                premature_commands += len(transition.commands)
                if len(violations) < 20:
                    violations.append(f"sequence={sequence} step={step} premature_command")
            if transition.commands:
                for command in transition.commands:
                    if command.command_id in emitted:
                        second_commands += 1
                        if len(violations) < 20:
                            violations.append(f"sequence={sequence} step={step} command_reemitted")
                    emitted.add(command.command_id)
            state = transition.state
            if len(state.command_ids) > 1 or len(emitted) > 1:
                second_commands += 1
                if len(violations) < 20:
                    violations.append(f"sequence={sequence} step={step} second_command")

    return PropertyReport(
        sequences=sequences,
        max_events=max_events,
        seed=seed,
        transitions=transitions,
        applied=applied,
        ignored=ignored,
        rejected=rejected,
        exceptions=exceptions,
        premature_commands=premature_commands,
        second_commands=second_commands,
        duplicate_reemissions=duplicate_reemissions,
        conflicting_duplicate_acceptances=conflicting_duplicate_acceptances,
        authorized_accepts=authorized_accepts,
        missing_authorized_commands=missing_authorized_commands,
        out_of_order_probes=out_of_order_probes,
        out_of_order_policy_violations=out_of_order_policy_violations,
        lookup_positive_cases=lookup_positive_cases,
        lookup_negative_cases=lookup_negative_cases,
        lookup_expired_cases=lookup_expired_cases,
        lookup_unavailable_cases=lookup_unavailable_cases,
        lookup_multi_offer_cases=lookup_multi_offer_cases,
        violations=tuple(violations),
    )
