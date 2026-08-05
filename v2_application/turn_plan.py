from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date

from reservation_boundary.conversation import ConversationProjection
from reservation_boundary.types import BoundaryState
from reservation_domain import AwaitingConfirmationState, ServiceKind
from v2_contracts.model import ModelProposal
from v2_contracts.providers import ReadKind, ReadRequest


def preserve_initial_facts(
    initial: ModelProposal,
    observation_frame: ModelProposal,
) -> ModelProposal:
    """Keep the accepted semantic facts stable across the post-read phrasing frame."""

    if type(initial) is not ModelProposal or type(observation_frame) is not ModelProposal:
        raise TypeError("turn plan requires exact ModelProposal values")
    initial_names = {item.name for item in initial.facts}
    return replace(
        observation_frame,
        facts=(
            *(
                item
                for item in observation_frame.facts
                if item.name not in initial_names
            ),
            *initial.facts,
        ),
    )


def preserve_initial_adjustment(
    initial: ModelProposal,
    observation_frame: ModelProposal,
) -> ModelProposal:
    """Let the observation frame phrase an adjustment without changing its disposition."""

    if type(initial) is not ModelProposal or type(observation_frame) is not ModelProposal:
        raise TypeError("turn plan requires exact ModelProposal values")
    if initial.intent != "adjust":
        return observation_frame
    return replace(
        observation_frame,
        intent="adjust",
        target_offer_id=None,
        target_offer_ids=(),
        confirmed_summary_version=None,
        confirmed_action_kinds=(),
        approval_basis=None,
        selection_requested=False,
        pending_disposition=initial.pending_disposition or "revoke",
    )


def _adjustment_read_id(source_event_id: str, kind: ReadKind) -> str:
    material = f"{source_event_id}\0{kind.value}".encode("utf-8")
    return "adjust-read:" + hashlib.sha256(material).hexdigest()[:32]


def derive_adjustment_reads(
    state: BoundaryState,
    projection: ConversationProjection,
    proposal: ModelProposal,
) -> tuple[ReadRequest, ...]:
    """Derive fresh read-only queries from an accepted material adjustment plan."""

    if (
        type(state) is not BoundaryState
        or type(projection) is not ConversationProjection
        or type(proposal) is not ModelProposal
    ):
        raise TypeError("adjustment reads require exact V2 contracts")
    workflow = state.workflow
    material_names = {
        "service",
        "product_id",
        "start_date",
        "end_date",
        "activity_date",
        "adults",
        "children",
    }
    if (
        type(workflow) is not AwaitingConfirmationState
        or proposal.intent != "adjust"
        or proposal.pending_disposition != "revoke"
        or proposal.read_requests
        or not any(item.name in material_names for item in proposal.facts)
    ):
        return ()

    values = {item.name: item.value.value for item in projection.facts}
    values.update({item.name: item.value for item in proposal.facts})
    requested_service = values.get("service")
    if requested_service == "hostel":
        services = (ServiceKind.LODGING,)
    elif requested_service == "agency":
        services = (ServiceKind.ACTIVITY,)
    elif requested_service == "package":
        services = (ServiceKind.LODGING, ServiceKind.ACTIVITY)
    else:
        services = tuple(dict.fromkeys(item.service for item in workflow.draft.components))

    adults = values.get("adults")
    children = values.get("children", 0)
    if type(adults) is not int or type(children) is not int:
        return ()
    requests: list[ReadRequest] = []
    for service in services:
        if service is ServiceKind.LODGING:
            check_in = values.get("start_date")
            check_out = values.get("end_date")
            if type(check_in) is not date or type(check_out) is not date:
                return ()
            requests.append(
                ReadRequest(
                    request_id=_adjustment_read_id(
                        proposal.source_event_id,
                        ReadKind.LODGING,
                    ),
                    kind=ReadKind.LODGING,
                    check_in=check_in,
                    check_out=check_out,
                    adults=adults,
                    children=children,
                )
            )
        elif service is ServiceKind.ACTIVITY:
            product_id = values.get("product_id")
            activity_date = values.get("activity_date") or values.get("start_date")
            if type(product_id) is not str or type(activity_date) is not date:
                return ()
            requests.append(
                ReadRequest(
                    request_id=_adjustment_read_id(
                        proposal.source_event_id,
                        ReadKind.ACTIVITY,
                    ),
                    kind=ReadKind.ACTIVITY,
                    product_id=product_id,
                    activity_date=activity_date,
                    adults=adults,
                    children=children,
                )
            )
        else:
            return ()
    return tuple(requests)
