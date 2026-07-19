"""Total pure reducer for the typed reservation workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
from typing import Callable

from .signature import (
    build_commercial_draft,
    command_identity,
    operation_for_components,
)
from .serialization import dumps_event
from .types import (
    EVENT_TYPES,
    STATE_TYPES,
    AwaitingAdjustmentState,
    AwaitingConfirmationState,
    CancelledState,
    CollectingState,
    CommandPayload,
    ConfirmationDecisionKind,
    ConfirmationReceived,
    ConfirmationRecord,
    DomainEvent,
    DraftAdjusted,
    DraftRequested,
    Event,
    ExecutionCertainty,
    ExecutionFinished,
    ExecutionQueuedState,
    ExecutionStarted,
    ExecutingState,
    ExpiredState,
    FailedBeforeProviderState,
    FailedNoEffectState,
    LookupRecorded,
    LookupStatus,
    ManualReviewRequested,
    ManualReviewState,
    OfferChosen,
    OfferedState,
    ReadyToSummarizeState,
    ReservationCommand,
    SearchingState,
    SelectedState,
    StartSearch,
    State,
    StateMeta,
    SucceededState,
    SummaryPresented,
    SummaryRecorded,
    TransitionStatus,
    UncertainState,
    WorkflowCancelled,
    WorkflowExpired,
    WorkflowPhase,
    WorkflowState,
    _require_utc,
    validate_state_consistency,
)


@dataclass(frozen=True, slots=True)
class Transition:
    state: State
    status: TransitionStatus
    reason: str
    commands: tuple[ReservationCommand, ...] = ()


@dataclass(frozen=True, slots=True)
class _Decision:
    state: State
    status: TransitionStatus
    reason: str
    commands: tuple[ReservationCommand, ...] = ()


Handler = Callable[[State, Event], _Decision]
_HANDLERS: dict[tuple[type[WorkflowState], type[DomainEvent]], Handler] = {}


def _register(
    state_types: tuple[type[WorkflowState], ...],
    event_type: type[DomainEvent],
):
    def decorator(handler: Handler) -> Handler:
        for state_type in state_types:
            key = (state_type, event_type)
            if key in _HANDLERS:
                raise RuntimeError(f"duplicate reducer handler for {key}")
            _HANDLERS[key] = handler
        return handler

    return decorator


def new_workflow(*, workflow_id: str, started_at: datetime) -> CollectingState:
    instant = _require_utc(started_at, "started_at")
    state = CollectingState(
        meta=StateMeta(
            workflow_id=workflow_id,
            revision=0,
            last_event_at=instant,
            seen_event_ids=(),
            seen_event_hashes=(),
            command_ids=(),
        )
    )
    validate_state_consistency(state)
    return state


def _with_meta(state: State, meta: StateMeta) -> State:
    return replace(state, meta=meta)


def _event_digest(event: Event) -> str:
    return hashlib.sha256(dumps_event(event).encode("utf-8")).hexdigest()


def _advance_meta(
    meta: StateMeta,
    event: DomainEvent,
    *,
    command_ids: tuple[str, ...] = (),
) -> StateMeta:
    seen = meta.seen_event_ids
    hashes = meta.seen_event_hashes
    if event.event_id not in seen:
        seen = (*seen, event.event_id)
        hashes = (*hashes, _event_digest(event))
    commands = meta.command_ids
    for command_id in command_ids:
        if command_id not in commands:
            commands = (*commands, command_id)
    return StateMeta(
        workflow_id=meta.workflow_id,
        revision=meta.revision + 1,
        last_event_at=max(meta.last_event_at, event.occurred_at),
        seen_event_ids=seen,
        seen_event_hashes=hashes,
        command_ids=commands,
    )


def _finalize(decision: _Decision, event: DomainEvent) -> Transition:
    command_ids = tuple(item.command_id for item in decision.commands)
    state = _with_meta(
        decision.state,
        _advance_meta(decision.state.meta, event, command_ids=command_ids),
    )
    validate_state_consistency(state)
    return Transition(
        state=state,
        status=decision.status,
        reason=decision.reason,
        commands=decision.commands,
    )


def reduce(state: State, event: Event) -> Transition:
    """Apply every public event to every public state without side effects.

    Duplicate events are exact no-ops. New but out-of-order events are recorded
    and rejected, preventing a delayed confirmation from authorizing a newer
    draft. Missing state/event handlers are explicit ignored policies.
    """

    validate_state_consistency(state)
    if event.event_id in state.meta.seen_event_ids:
        index = state.meta.seen_event_ids.index(event.event_id)
        if state.meta.seen_event_hashes[index] != _event_digest(event):
            return Transition(
                state=state,
                status=TransitionStatus.REJECTED,
                reason="conflicting_duplicate_event",
            )
        return Transition(
            state=state,
            status=TransitionStatus.IGNORED,
            reason="duplicate_event",
        )
    if event.occurred_at < state.meta.last_event_at:
        return _finalize(
            _Decision(
                state=state,
                status=TransitionStatus.REJECTED,
                reason="out_of_order_event",
            ),
            event,
        )
    handler = _HANDLERS.get((type(state), type(event)))
    if handler is None:
        return _finalize(
            _Decision(
                state=state,
                status=TransitionStatus.IGNORED,
                reason="event_not_applicable_in_phase",
            ),
            event,
        )
    return _finalize(handler(state, event), event)


def _offer_matches_query(offer, query) -> bool:
    return bool(
        offer.service is query.service
        and offer.start_date == query.start_date
        and offer.end_date == query.end_date
        and (query.start_time is None or offer.start_time == query.start_time)
        and offer.party == query.party
    )


def _terms_match_component_currencies(components, terms) -> bool:
    component_currencies = {item.total.currency for item in components}
    add_on_currencies = {item.unit_price.currency for item in terms.add_ons}
    return len(component_currencies) == 1 and (
        not add_on_currencies or add_on_currencies == component_currencies
    )


def _command_for(
    *,
    workflow_id: str,
    draft,
    created_at: datetime,
) -> ReservationCommand:
    operation = operation_for_components(draft.components)
    command_id, idempotency_key = command_identity(
        workflow_id=workflow_id,
        draft_id=draft.draft_id,
        draft_version=draft.version,
        signature=draft.subject_signature,
        operation=operation,
    )
    return ReservationCommand(
        command_id=command_id,
        idempotency_key=idempotency_key,
        workflow_id=workflow_id,
        draft_id=draft.draft_id,
        draft_version=draft.version,
        subject_signature=draft.subject_signature,
        operation=operation,
        payload=CommandPayload(
            components=draft.components,
            customer=draft.customer,
            terms=draft.terms,
        ),
        created_at=created_at,
    )


_PRE_COMMAND_STATES = (
    CollectingState,
    SearchingState,
    OfferedState,
    SelectedState,
    ReadyToSummarizeState,
    AwaitingConfirmationState,
    AwaitingAdjustmentState,
)


@_register(_PRE_COMMAND_STATES, StartSearch)
def _start_search(state: State, event: Event) -> _Decision:
    assert isinstance(event, StartSearch)
    return _Decision(
        state=SearchingState(meta=state.meta, query=event.query),
        status=TransitionStatus.APPLIED,
        reason="search_started",
    )


@_register((SearchingState,), LookupRecorded)
def _record_lookup(state: State, event: Event) -> _Decision:
    assert isinstance(state, SearchingState)
    assert isinstance(event, LookupRecorded)
    evidence = event.evidence
    offers = event.offers
    valid = bool(
        evidence.status is LookupStatus.POSITIVE
        and evidence.service is state.query.service
        and evidence.query_signature == state.query.signature
        and evidence.is_fresh(event.occurred_at)
        and offers
        and len({item.offer_id for item in offers}) == len(offers)
        and all(
            item.available
            and item.lookup_id == evidence.lookup_id
            and item.service is evidence.service
            and _offer_matches_query(item, state.query)
            for item in offers
        )
    )
    if not valid:
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="lookup_not_fresh_positive_or_consistent",
        )
    return _Decision(
        state=OfferedState(
            meta=state.meta,
            query=state.query,
            evidence=evidence,
            offers=offers,
        ),
        status=TransitionStatus.APPLIED,
        reason="positive_offers_recorded",
    )


@_register((OfferedState,), OfferChosen)
def _choose_offer(state: State, event: Event) -> _Decision:
    assert isinstance(state, OfferedState)
    assert isinstance(event, OfferChosen)
    if not state.evidence.is_fresh(event.occurred_at):
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="offer_evidence_expired",
        )
    matches = tuple(item for item in state.offers if item.offer_id == event.offer_id)
    if len(matches) != 1:
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="offer_id_not_uniquely_available",
        )
    return _Decision(
        state=SelectedState(
            meta=state.meta,
            query=state.query,
            evidence=state.evidence,
            offer=matches[0],
        ),
        status=TransitionStatus.APPLIED,
        reason="offer_selected_by_id",
    )


@_register((SelectedState,), DraftRequested)
def _request_draft(state: State, event: Event) -> _Decision:
    assert isinstance(state, SelectedState)
    assert isinstance(event, DraftRequested)
    if not state.evidence.is_fresh(event.occurred_at):
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="selected_offer_evidence_expired",
        )
    components = (state.offer,)
    if not _terms_match_component_currencies(components, event.terms):
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="economic_terms_currency_mismatch",
        )
    draft = build_commercial_draft(
        draft_id=event.draft_id,
        version=1,
        created_at=event.occurred_at,
        components=components,
        customer=event.customer,
        terms=event.terms,
    )
    return _Decision(
        state=ReadyToSummarizeState(meta=state.meta, draft=draft),
        status=TransitionStatus.APPLIED,
        reason="commercial_draft_created",
    )


@_register(
    (ReadyToSummarizeState, AwaitingConfirmationState, AwaitingAdjustmentState),
    DraftAdjusted,
)
def _adjust_draft(state: State, event: Event) -> _Decision:
    assert isinstance(
        state,
        (ReadyToSummarizeState, AwaitingConfirmationState, AwaitingAdjustmentState),
    )
    assert isinstance(event, DraftAdjusted)
    if not _terms_match_component_currencies(state.draft.components, event.terms):
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="economic_terms_currency_mismatch",
        )
    draft = build_commercial_draft(
        draft_id=state.draft.draft_id,
        version=state.draft.version + 1,
        created_at=event.occurred_at,
        components=state.draft.components,
        customer=event.customer,
        terms=event.terms,
    )
    if draft.subject_signature == state.draft.subject_signature:
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="adjustment_did_not_change_subject",
        )
    return _Decision(
        state=ReadyToSummarizeState(meta=state.meta, draft=draft),
        status=TransitionStatus.APPLIED,
        reason="commercial_draft_version_incremented",
    )


@_register((ReadyToSummarizeState,), SummaryRecorded)
def _record_summary(state: State, event: Event) -> _Decision:
    assert isinstance(state, ReadyToSummarizeState)
    assert isinstance(event, SummaryRecorded)
    valid = bool(
        event.draft_version == state.draft.version
        and event.subject_signature == state.draft.subject_signature
        and event.occurred_at >= state.draft.created_at
    )
    if not valid:
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="summary_does_not_match_current_draft",
        )
    summary = SummaryPresented(
        summary_event_id=event.summary_event_id,
        draft_id=state.draft.draft_id,
        draft_version=state.draft.version,
        subject_signature=state.draft.subject_signature,
        outbox_message_id=event.outbox_message_id,
        presented_at=event.occurred_at,
    )
    return _Decision(
        state=AwaitingConfirmationState(
            meta=state.meta,
            draft=state.draft,
            summary=summary,
        ),
        status=TransitionStatus.APPLIED,
        reason="summary_presentation_recorded",
    )


@_register((AwaitingConfirmationState,), ConfirmationReceived)
def _receive_confirmation(state: State, event: Event) -> _Decision:
    assert isinstance(state, AwaitingConfirmationState)
    assert isinstance(event, ConfirmationReceived)
    matches = bool(
        event.target_draft_version == state.draft.version
        and event.subject_signature == state.draft.subject_signature
        and event.subject_signature == state.summary.subject_signature
    )
    if not matches:
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="confirmation_does_not_match_presented_draft",
        )
    if event.occurred_at <= state.summary.presented_at:
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="confirmation_not_posterior_to_summary",
        )
    record = ConfirmationRecord(
        confirmation_event_id=event.confirmation_event_id,
        decision=event.decision,
        target_draft_version=event.target_draft_version,
        subject_signature=event.subject_signature,
        decided_at=event.occurred_at,
    )
    if event.decision is ConfirmationDecisionKind.REJECT:
        return _Decision(
            state=CancelledState(
                meta=state.meta,
                previous_phase=state.phase,
                reason="lead_rejected_current_draft",
            ),
            status=TransitionStatus.APPLIED,
            reason="current_draft_rejected",
        )
    if event.decision is ConfirmationDecisionKind.ADJUST:
        return _Decision(
            state=AwaitingAdjustmentState(
                meta=state.meta,
                draft=state.draft,
                summary=state.summary,
                decision=record,
            ),
            status=TransitionStatus.APPLIED,
            reason="lead_requested_adjustment",
        )
    if event.decision is ConfirmationDecisionKind.AMBIGUOUS:
        return _Decision(
            state=state,
            status=TransitionStatus.APPLIED,
            reason="confirmation_ambiguous",
        )
    command = _command_for(
        workflow_id=state.meta.workflow_id,
        draft=state.draft,
        created_at=event.occurred_at,
    )
    return _Decision(
        state=ExecutionQueuedState(
            meta=state.meta,
            draft=state.draft,
            summary=state.summary,
            confirmation=record,
            command=command,
        ),
        status=TransitionStatus.APPLIED,
        reason="reservation_command_created",
        commands=(command,),
    )


@_register((ExecutionQueuedState,), ExecutionStarted)
def _start_execution(state: State, event: Event) -> _Decision:
    assert isinstance(state, ExecutionQueuedState)
    assert isinstance(event, ExecutionStarted)
    if event.command_id != state.command.command_id:
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="execution_command_id_mismatch",
        )
    return _Decision(
        state=ExecutingState(
            meta=state.meta,
            draft=state.draft,
            summary=state.summary,
            confirmation=state.confirmation,
            command=state.command,
            attempt=1,
        ),
        status=TransitionStatus.APPLIED,
        reason="execution_started_once",
    )


@_register((ExecutingState,), ExecutionFinished)
def _finish_execution(state: State, event: Event) -> _Decision:
    assert isinstance(state, ExecutingState)
    assert isinstance(event, ExecutionFinished)
    if (
        event.command_id != state.command.command_id
        or event.outcome.command_id != state.command.command_id
    ):
        return _Decision(
            state=state,
            status=TransitionStatus.REJECTED,
            reason="execution_outcome_command_id_mismatch",
        )
    certainty = event.outcome.certainty
    if certainty is ExecutionCertainty.EFFECT_CONFIRMED:
        next_state: State = SucceededState(
            meta=state.meta,
            command=state.command,
            outcome=event.outcome,
        )
        reason = "provider_effect_confirmed"
    elif certainty is ExecutionCertainty.CALLED_UNKNOWN:
        next_state = UncertainState(
            meta=state.meta,
            command=state.command,
            outcome=event.outcome,
        )
        reason = "provider_effect_uncertain"
    elif certainty is ExecutionCertainty.NOT_CALLED:
        next_state = FailedBeforeProviderState(
            meta=state.meta,
            command=state.command,
            outcome=event.outcome,
        )
        reason = "provider_proven_not_called"
    else:
        next_state = FailedNoEffectState(
            meta=state.meta,
            command=state.command,
            outcome=event.outcome,
        )
        reason = "provider_called_without_effect"
    return _Decision(
        state=next_state,
        status=TransitionStatus.APPLIED,
        reason=reason,
    )


@_register((UncertainState,), ManualReviewRequested)
def _request_manual_review(state: State, event: Event) -> _Decision:
    assert isinstance(state, UncertainState)
    assert isinstance(event, ManualReviewRequested)
    return _Decision(
        state=ManualReviewState(
            meta=state.meta,
            command=state.command,
            outcome=state.outcome,
            reason=event.reason,
        ),
        status=TransitionStatus.APPLIED,
        reason="manual_review_required",
    )


@_register(_PRE_COMMAND_STATES, WorkflowCancelled)
def _cancel_workflow(state: State, event: Event) -> _Decision:
    assert isinstance(event, WorkflowCancelled)
    return _Decision(
        state=CancelledState(
            meta=state.meta,
            previous_phase=state.phase,
            reason=event.reason,
        ),
        status=TransitionStatus.APPLIED,
        reason="workflow_cancelled_before_command",
    )


@_register(_PRE_COMMAND_STATES, WorkflowExpired)
def _expire_workflow(state: State, event: Event) -> _Decision:
    assert isinstance(event, WorkflowExpired)
    return _Decision(
        state=ExpiredState(
            meta=state.meta,
            previous_phase=state.phase,
            reason=event.reason,
        ),
        status=TransitionStatus.APPLIED,
        reason="workflow_expired_before_command",
    )


_EXPLICIT_POLICY_CODES = {
    "collecting": "EIIIIIIIIIEE",
    "searching": "EEIIIIIIIIEE",
    "offered": "EIEIIIIIIIEE",
    "selected": "EIIEIIIIIIEE",
    "ready_to_summarize": "EIIIEEIIIIEE",
    "awaiting_confirmation": "EIIIEIEIIIEE",
    "awaiting_adjustment": "EIIIEIIIIIEE",
    "execution_queued": "IIIIIIIEIIII",
    "executing": "IIIIIIIIEIII",
    "succeeded": "IIIIIIIIIIII",
    "failed_before_provider": "IIIIIIIIIIII",
    "failed_no_effect": "IIIIIIIIIIII",
    "uncertain": "IIIIIIIIIEII",
    "manual_review": "IIIIIIIIIIII",
    "cancelled": "IIIIIIIIIIII",
    "expired": "IIIIIIIIIIII",
}


def transition_matrix() -> dict[str, dict[str, str]]:
    state_tags = tuple(item.TYPE for item in STATE_TYPES)
    event_tags = tuple(item.TYPE for item in EVENT_TYPES)
    if len(set(state_tags)) != len(state_tags) or set(state_tags) != set(
        _EXPLICIT_POLICY_CODES
    ):
        raise ValueError("state universe differs from explicit transition policy")
    if len(set(event_tags)) != len(event_tags):
        raise ValueError("event tags must be unique")
    if any(
        len(codes) != len(event_tags) or set(codes) - {"E", "I"}
        for codes in _EXPLICIT_POLICY_CODES.values()
    ):
        raise ValueError("explicit transition policy has invalid row shape")
    matrix = {
        state_tag: {
            event_tag: "evaluate" if code == "E" else "ignore"
            for event_tag, code in zip(event_tags, _EXPLICIT_POLICY_CODES[state_tag])
        }
        for state_tag in state_tags
    }
    declared_handlers = {
        (state_tag, event_tag)
        for state_tag, row in matrix.items()
        for event_tag, policy in row.items()
        if policy == "evaluate"
    }
    actual_handlers = {
        (state_type.TYPE, event_type.TYPE) for state_type, event_type in _HANDLERS
    }
    if declared_handlers != actual_handlers:
        raise ValueError("registered handlers differ from explicit transition policy")
    return matrix
