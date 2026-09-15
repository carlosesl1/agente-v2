from __future__ import annotations

from typing import Protocol, cast

from reservation_boundary.conversation import ConversationProjection
from reservation_boundary.types import BoundaryState, StringSlot
from reservation_domain import (
    ExecutingState,
    ExecutionCertainty,
    ExecutionQueuedState,
    ReservationOperation,
    ServiceKind,
    loads_outcome,
)
from reservation_execution import LedgerStatus
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from v2_contracts.model import ModelProposal
from v2_contracts.providers import ReadKind, ReadRequest


def active_execution_status(state: BoundaryState) -> str | None:
    if type(state) is not BoundaryState:
        raise TypeError("active execution status requires an exact BoundaryState")
    if type(state.workflow) is ExecutionQueuedState:
        return "queued"
    if type(state.workflow) is ExecutingState:
        return "executing"
    return None


class ExecutionStatusResolver(Protocol):
    def resolve(self, state: BoundaryState) -> str | None: ...


class ReservationExecutionStatusResolver:
    """Resolve one boundary command against its durable expanded execution group."""

    def __init__(self, execution: SQLiteUnitOfWork) -> None:
        if type(execution) is not SQLiteUnitOfWork:
            raise TypeError("execution must be exact SQLiteUnitOfWork")
        self._execution = execution

    def resolve(self, state: BoundaryState) -> str | None:
        baseline = active_execution_status(state)
        if baseline is None:
            return None
        workflow = cast(ExecutionQueuedState | ExecutingState, state.workflow)
        parent = workflow.command
        members = tuple(
            (command, ledger)
            for command, ledger in self._execution.list_outcome_projection_inputs()
            if command.payload.customer.customer_ref
            == parent.payload.customer.customer_ref
            and command.draft_id == parent.draft_id
            and command.draft_version == parent.draft_version
        )
        expected_operations = {
            ReservationOperation.RESERVE_PACKAGE: frozenset(
                (
                    ReservationOperation.RESERVE_LODGING,
                    ReservationOperation.BOOK_ACTIVITY,
                )
            ),
            ReservationOperation.RESERVE_LODGING: frozenset(
                (ReservationOperation.RESERVE_LODGING,)
            ),
            ReservationOperation.BOOK_ACTIVITY: frozenset(
                (ReservationOperation.BOOK_ACTIVITY,)
            ),
        }[parent.operation]
        if len(members) != len(expected_operations) or frozenset(
            command.operation for command, _ in members
        ) != expected_operations:
            return baseline
        statuses = frozenset(ledger.status for _, ledger in members)
        if LedgerStatus.DISPATCH_FENCED in statuses or LedgerStatus.PREPARING in statuses:
            return "executing"
        if LedgerStatus.QUEUED in statuses:
            return "queued"
        outcomes = tuple(
            loads_outcome(ledger.outcome_json)
            for _, ledger in members
            if ledger.outcome_json is not None
        )
        if len(outcomes) != len(members):
            return baseline
        if any(
            outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN
            for outcome in outcomes
        ):
            return "uncertain"
        confirmed = sum(
            outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED
            for outcome in outcomes
        )
        if confirmed == len(outcomes):
            return "confirmed"
        if confirmed:
            return "partial_failure"
        if all(
            outcome.certainty is ExecutionCertainty.NOT_CALLED for outcome in outcomes
        ):
            return "failed_before_provider"
        return "failed_no_effect"


def blocks_active_commercial_progression(
    state: BoundaryState,
    proposal: ModelProposal,
    *,
    execution_status: str | None = None,
) -> bool:
    """Keep an enqueued/executing draft authoritative over new commercial progress."""

    if type(state) is not BoundaryState or type(proposal) is not ModelProposal:
        raise TypeError("active progression guard requires exact V2 contracts")
    status = execution_status or active_execution_status(state)
    if status is None:
        return False
    if (
        proposal.intent in {"select", "confirm", "adjust"}
        or proposal.target_offer_id is not None
        or proposal.target_offer_ids
        or proposal.selection_requested
        or proposal.passengers
        or proposal.effect_proposals
        or any(fact.name != "language" for fact in proposal.facts)
    ):
        return True
    return any(
        request.kind in {ReadKind.LODGING, ReadKind.ACTIVITY}
        for request in proposal.read_requests
    )


def is_regressive_post_command_reply(
    state: BoundaryState,
    proposal: ModelProposal,
    *,
    execution_status: str | None = None,
) -> bool:
    """Ground a regressive model output while the same command is active.

    This is status-only: it cannot authorize, enqueue, select, or read anything. It
    validates only the structured model proposal and never interprets customer text.
    """

    if type(state) is not BoundaryState or type(proposal) is not ModelProposal:
        raise TypeError("post-command reply guard requires exact V2 contracts")
    status = execution_status or active_execution_status(state)
    if status not in {"queued", "executing"}:
        return False
    if (
        proposal.intent != "inform"
        or proposal.facts
        or proposal.read_requests
        or proposal.effect_proposals
        or proposal.target_offer_id is not None
        or proposal.target_offer_ids
        or proposal.selection_requested
        or proposal.passengers
    ):
        return False
    return proposal.clarification_question is not None


def _request_matches_active_draft(
    request: ReadRequest,
    *,
    state: BoundaryState,
    projection: ConversationProjection,
) -> bool:
    workflow = state.workflow
    if type(workflow) not in {ExecutionQueuedState, ExecutingState}:
        return False
    product_id = next(
        (
            fact.value.value
            for fact in projection.facts
            if fact.name == "product_id" and type(fact.value) is StringSlot
        ),
        None,
    )
    for component in workflow.draft.components:
        if request.kind is ReadKind.LODGING and component.service is ServiceKind.LODGING:
            if (
                component.end_date is not None
                and request.check_in == component.start_date
                and request.check_out == component.end_date
                and request.adults == component.party.adults
                and request.children == component.party.children
            ):
                return True
        if request.kind is ReadKind.ACTIVITY and component.service is ServiceKind.ACTIVITY:
            if (
                request.product_id == product_id
                and request.activity_date == component.start_date
                and request.activity_party()
                == (component.party.adults, component.party.children)
            ):
                return True
    return False


def is_redundant_post_command_read(
    state: BoundaryState,
    projection: ConversationProjection,
    proposal: ModelProposal,
    requests: tuple[ReadRequest, ...],
    *,
    execution_status: str | None = None,
) -> bool:
    status = execution_status or active_execution_status(state)
    if not requests or status is None:
        return False
    material_names = {
        "service",
        "product_id",
        "start_date",
        "end_date",
        "activity_date",
        "adults",
        "children",
        "payment_method",
    }
    if any(fact.name in material_names for fact in proposal.facts):
        return False
    return all(
        _request_matches_active_draft(item, state=state, projection=projection)
        for item in requests
    )
