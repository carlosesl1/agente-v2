from __future__ import annotations

from reservation_boundary.conversation import ConversationProjection
from reservation_boundary.types import BoundaryState, StringSlot
from reservation_domain import ExecutingState, ExecutionQueuedState, ServiceKind
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


def execution_in_progress_reply(locale: str) -> tuple[str, ...]:
    if locale.casefold().startswith("en"):
        return ("That booking is already being processed. I won’t submit it again.",)
    return ("Essa reserva já está em processamento. Não vou enviá-la novamente.",)


def blocks_active_commercial_progression(
    state: BoundaryState,
    proposal: ModelProposal,
) -> bool:
    """Keep an enqueued/executing draft authoritative over new commercial progress."""

    if type(state) is not BoundaryState or type(proposal) is not ModelProposal:
        raise TypeError("active progression guard requires exact V2 contracts")
    if active_execution_status(state) is None:
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
) -> bool:
    """Ground a regressive model output while the same command is active.

    This is status-only: it cannot authorize, enqueue, select, or read anything. It
    validates only the structured model proposal and never interprets customer text.
    """

    if type(state) is not BoundaryState or type(proposal) is not ModelProposal:
        raise TypeError("post-command reply guard requires exact V2 contracts")
    if active_execution_status(state) is None:
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
    return any("?" in chunk or "¿" in chunk for chunk in proposal.reply_chunks)


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
) -> bool:
    if not requests or type(state.workflow) not in {ExecutionQueuedState, ExecutingState}:
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
