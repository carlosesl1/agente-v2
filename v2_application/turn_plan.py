from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta

from reservation_boundary.conversation import ConversationProjection
from reservation_boundary.types import BoundaryState
from reservation_domain import AwaitingConfirmationState, ServiceKind
from v2_contracts.model import ConsultationHistoryEntry, ModelProposal
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


def _initial_read_id(source_event_id: str, kind: ReadKind) -> str:
    material = f"{source_event_id}\0{kind.value}".encode("utf-8")
    return "commercial-read:" + hashlib.sha256(material).hexdigest()[:32]


def _commercial_requests(
    *,
    source_event_id: str,
    values: dict[str, object],
) -> tuple[ReadRequest, ...]:
    service = values.get("service")
    if service == "hostel":
        kinds = (ReadKind.LODGING,)
    elif service == "agency":
        kinds = (ReadKind.ACTIVITY,)
    elif service == "package":
        kinds = (ReadKind.LODGING, ReadKind.ACTIVITY)
    else:
        return ()

    adults = values.get("adults")
    children = values.get("children")
    if type(adults) is not int or type(children) is not int:
        return ()
    requests: list[ReadRequest] = []
    for kind in kinds:
        if kind is ReadKind.LODGING:
            check_in = values.get("start_date")
            check_out = values.get("end_date")
            if type(check_in) is not date or type(check_out) is not date:
                return ()
            requests.append(
                ReadRequest(
                    request_id=_initial_read_id(source_event_id, kind),
                    kind=kind,
                    check_in=check_in,
                    check_out=check_out,
                    adults=adults,
                    children=children,
                )
            )
        else:
            product_id = values.get("product_id")
            activity_date = values.get("activity_date")
            if type(product_id) is not str or type(activity_date) is not date:
                return ()
            requests.append(
                ReadRequest(
                    request_id=_initial_read_id(source_event_id, kind),
                    kind=kind,
                    product_id=product_id,
                    activity_date=activity_date,
                    adults=adults,
                    children=children,
                )
            )
    return tuple(requests)


def normalize_initial_commercial_plan(
    proposal: ModelProposal,
) -> ModelProposal:
    """Require model-owned reads and safely refresh model-owned selections.

    Informational provider reads are semantic actions and must be present in the model
    proposal. A typed ``select`` intent may still be converted into a current provider
    refresh because an unobserved target is never execution authority.
    """

    if type(proposal) is not ModelProposal:
        raise TypeError("commercial plan requires an exact ModelProposal")
    if proposal.read_requests or proposal.intent != "select":
        return proposal

    requests = _commercial_requests(
        source_event_id=proposal.source_event_id,
        values={item.name: item.value for item in proposal.facts},
    )
    if requests:
        # A model-supplied target without current-turn evidence is not authority.
        # Refresh first and ask the observation frame to bind a public offer.
        return replace(
            proposal,
            intent="inform",
            read_requests=requests,
            target_offer_id=None,
            target_offer_ids=(),
            selection_requested=True,
        )

    language = next(
        (item.value for item in proposal.facts if item.name == "language"),
        None,
    )
    reply = (
        "I need the complete dates and party before I can refresh availability safely."
        if type(language) is str and language.casefold().startswith("en")
        else "Preciso das datas e da ocupação completas para atualizar a disponibilidade com segurança."
    )
    return replace(
        proposal,
        intent="inform",
        reply_chunks=(reply,),
        target_offer_id=None,
        target_offer_ids=(),
        selection_requested=False,
    )


def _request_matches_history(
    request: ReadRequest,
    entry: ConsultationHistoryEntry,
    *,
    now: datetime,
) -> bool:
    if not (
        entry.fresh_at_turn_start
        and entry.observed_at <= now < entry.expires_at
    ):
        return False
    context = entry.public_context
    query = context["query"]
    if request.kind is ReadKind.LODGING:
        expected = {
            "check_in": request.check_in.isoformat(),
            "check_out": request.check_out.isoformat(),
            "adults": request.adults,
            "children": request.children,
        }
        return context["service"] == "lodging" and query == expected
    if request.kind is ReadKind.ACTIVITY:
        adults, children = request.activity_party()
        expected = {
            "product_id": request.product_id,
            "activity_date": request.activity_date.isoformat(),
            "adults": adults,
            "children": children,
        }
        return context["service"] == "activity" and query == expected
    return False


def reuses_fresh_consultation(
    proposal: ModelProposal,
    history: tuple[ConsultationHistoryEntry, ...],
    *,
    now: datetime,
) -> bool:
    """Return true only for a recap-only exact repeat of fresh read scopes."""

    if type(proposal) is not ModelProposal or type(history) is not tuple:
        raise TypeError("consultation reuse requires exact V2 contracts")
    if (
        type(now) is not datetime
        or now.tzinfo is None
        or now.utcoffset() != timedelta(0)
    ):
        raise ValueError("consultation reuse time must be exact UTC")
    if any(type(item) is not ConsultationHistoryEntry for item in history):
        raise TypeError("consultation history contains an invalid entry")
    if (
        proposal.intent != "inform"
        or not proposal.read_requests
        or proposal.facts
        or proposal.passengers
        or proposal.selection_requested
        or proposal.effect_proposals
    ):
        return False
    return all(
        any(
            _request_matches_history(request, entry, now=now)
            for entry in history
        )
        for request in proposal.read_requests
    )


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
