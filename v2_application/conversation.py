"""Pure deterministic conversation-to-domain reducer for Agente V2."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from reservation_boundary import (
    BoundaryState,
    ConversationProjection,
    ConversationStage,
    DateSlot,
    DesiredService,
    IntegerSlot,
    StringSlot,
    TypedFact,
)
from reservation_domain import (
    AwaitingConfirmationState,
    CommercialDraft,
    ConfirmationDecisionKind,
    ConfirmationReceived,
    CustomerFacts,
    DraftRequested,
    EconomicTerms,
    ExecutionQueuedState,
    ExecutingState,
    FailedBeforeProviderState,
    FailedNoEffectState,
    LookupEvidence,
    LookupRecorded,
    LookupStatus,
    ManualReviewState,
    Money,
    OfferChosen,
    OfferSnapshot,
    Party,
    ReadyToSummarizeState,
    ReservationCommand,
    SearchQuery,
    ServiceKind,
    StartSearch,
    SummaryRecorded,
    SucceededState,
    UncertainState,
    build_commercial_draft,
    new_workflow,
)
from reservation_domain import (
    reduce as reduce_domain,
)
from reservation_followup import (
    HandoffEffectPolicy,
    HandoffReasonCode,
    HandoffRequested,
    HandoffWorkflow,
)
from v2_application.critical_actions import (
    ApprovalMatch,
    CriticalActionDenied,
    CriticalActionPolicy,
    approval_assertion_matches,
    critical_action_context,
    critical_action_scope_available,
    critical_proposal_digest,
    critical_summary_outbox_id,
    pending_action_context,
)
from v2_application.turns import validate_productive_proposal
from v2_contracts.critical_actions import PendingCriticalActionContext
from v2_contracts.model import ModelFact, ModelProposal
from v2_contracts.profile import PrivateCustomerBinding
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_FACT_ORDER = {
    "language": 0,
    "service": 1,
    "product_id": 2,
    "start_date": 3,
    "end_date": 4,
    "activity_date": 5,
    "adults": 6,
    "children": 7,
    "payment_method": 8,
    "full_name": 9,
    "email": 10,
    "phone_e164": 11,
    "country_code": 12,
    "birth_date": 13,
    "gender": 14,
    "critical_outcome": 15,
}


class ConversationReductionError(ValueError):
    """The productive proposal cannot be reduced into authenticated domain state."""


@dataclass(frozen=True, slots=True)
class ConversationReply:
    kind: str
    chunks: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not str or not self.kind:
            raise ValueError("reply kind must be exact non-empty text")
        if (
            type(self.chunks) is not tuple
            or not self.chunks
            or any(type(item) is not str or not item.strip() for item in self.chunks)
        ):
            raise ValueError("reply chunks must be a non-empty exact text tuple")


@dataclass(frozen=True, slots=True)
class V2ConversationDecision:
    next_state: BoundaryState
    projection: ConversationProjection
    commands: tuple[ReservationCommand, ...]
    public_reply: ConversationReply
    handoff_request: HandoffRequested | None = None
    receipt_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.next_state) is not BoundaryState:
            raise TypeError("next_state must be exact BoundaryState")
        if type(self.projection) is not ConversationProjection:
            raise TypeError("projection must be exact ConversationProjection")
        if type(self.commands) is not tuple or any(
            type(item) is not ReservationCommand for item in self.commands
        ):
            raise TypeError("commands must contain exact ReservationCommand values")
        if type(self.public_reply) is not ConversationReply:
            raise TypeError("public_reply must be exact ConversationReply")
        if (
            self.handoff_request is not None
            and type(self.handoff_request) is not HandoffRequested
        ):
            raise TypeError("handoff_request must be exact HandoffRequested or None")
        if type(self.receipt_requirements) is not tuple or any(
            type(item) is not str or not item for item in self.receipt_requirements
        ):
            raise TypeError("receipt_requirements must be exact non-empty text values")


def _utc(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value


def _identity(*parts: str, prefix: str) -> str:
    material = "\0".join(parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(material).hexdigest()[:32]}"


def _event_id(source_event_id: str, event_kind: str) -> str:
    return _identity(source_event_id, event_kind, prefix="event")


def _model_fact_slot(fact: ModelFact):
    if fact.name in (
        "language",
        "service",
        "product_id",
        "payment_method",
        "full_name",
        "email",
        "phone_e164",
        "country_code",
        "gender",
    ):
        return StringSlot(fact.value)
    if fact.name in ("start_date", "end_date", "activity_date", "birth_date"):
        return DateSlot(fact.value)
    if fact.name in ("adults", "children"):
        return IntegerSlot(fact.value)
    raise ConversationReductionError("model fact is outside the projection catalog")


def typed_facts_from_proposal(
    proposal: ModelProposal,
    *,
    frame_commitment_hash: str,
) -> tuple[TypedFact, ...]:
    if type(proposal) is not ModelProposal:
        raise TypeError("proposal must be an exact ModelProposal")
    if (
        type(frame_commitment_hash) is not str
        or _HASH_RE.fullmatch(frame_commitment_hash) is None
    ):
        raise ValueError("frame_commitment_hash must be a lowercase SHA-256")
    facts = tuple(
        TypedFact(
            name=fact.name,
            value=_model_fact_slot(fact),
            frame_commitment_hash=frame_commitment_hash,
        )
        for fact in proposal.facts
    )
    return tuple(sorted(facts, key=lambda item: _FACT_ORDER[item.name]))


def _merge_projection(
    projection: ConversationProjection,
    proposal: ModelProposal,
    *,
    fact_commitment_hash: str,
    stage: ConversationStage | None = None,
) -> ConversationProjection:
    existing = {item.name: item for item in projection.facts}
    for fact in typed_facts_from_proposal(
        proposal,
        frame_commitment_hash=fact_commitment_hash,
    ):
        existing[fact.name] = fact
    facts = tuple(sorted(existing.values(), key=lambda item: _FACT_ORDER[item.name]))
    values = {item.name: item.value.value for item in facts}
    service = values.get("service")
    if service == "hostel":
        desired = (DesiredService.HOSTEL,)
    elif service == "agency":
        desired = (DesiredService.AGENCY,)
    elif service == "package":
        desired = (DesiredService.HOSTEL, DesiredService.AGENCY)
    else:
        desired = projection.desired_services
    locale = values.get("language", projection.locale)
    return ConversationProjection(
        stage=stage or projection.stage,
        desired_services=desired,
        locale=locale,
        facts=facts,
        reservation_execution_projection=projection.reservation_execution_projection,
    )


def _with_critical_outcome(
    projection: ConversationProjection,
    *,
    outcome: str,
    fact_commitment_hash: str,
) -> ConversationProjection:
    facts = {
        item.name: item
        for item in projection.facts
        if item.name != "critical_outcome"
    }
    facts["critical_outcome"] = TypedFact(
        "critical_outcome",
        StringSlot(outcome),
        fact_commitment_hash,
    )
    return replace(
        projection,
        facts=tuple(
            sorted(facts.values(), key=lambda item: _FACT_ORDER[item.name])
        ),
    )


def _without_critical_outcome(
    projection: ConversationProjection,
) -> ConversationProjection:
    return replace(
        projection,
        facts=tuple(
            item for item in projection.facts if item.name != "critical_outcome"
        ),
    )


def _projection_values(projection: ConversationProjection) -> dict[str, object]:
    return {item.name: item.value.value for item in projection.facts}


def _customer(
    profile: PrivateCustomerBinding,
    projection: ConversationProjection,
) -> CustomerFacts:
    facts = _projection_values(projection)
    full_name = profile.full_name or facts.get("full_name")
    email = profile.email or facts.get("email")
    phone = profile.phone_e164 or facts.get("phone_e164")
    country = profile.country_code or facts.get("country_code")
    if any(type(value) is not str for value in (full_name, email, phone, country)):
        raise ConversationReductionError("complete profile has missing customer facts")
    return CustomerFacts(
        customer_ref=profile.binding_id,
        full_name=full_name,
        email=email,
        phone_e164=phone,
        country_code=country,
        birth_date=facts.get("birth_date"),
        gender=facts.get("gender"),
    )


def _profile_ready(
    profile: PrivateCustomerBinding,
    projection: ConversationProjection,
    now: datetime,
) -> bool:
    if not profile.observed_at <= now < profile.expires_at:
        return False
    try:
        _customer(profile, projection)
    except (ConversationReductionError, TypeError, ValueError):
        return False
    return True


def _proposal_values(proposal: ModelProposal) -> dict[str, object]:
    return {item.name: item.value for item in proposal.facts}


def _proposal_binds_offer(proposal: ModelProposal, offer: OfferSnapshot) -> bool:
    values = _proposal_values(proposal)
    expected_service = {
        ServiceKind.LODGING: "hostel",
        ServiceKind.ACTIVITY: "agency",
    }[offer.service]
    if not (
        values.get("service") == expected_service
        and values.get("adults") == offer.party.adults
        and values.get("children") == offer.party.children
    ):
        return False
    if offer.service is ServiceKind.LODGING:
        return (
            values.get("start_date") == offer.start_date
            and values.get("end_date") == offer.end_date
        )
    product_id = values.get("product_id")
    if not isinstance(product_id, str) or not offer.lookup_id.startswith(
        f"lookup:{product_id}:"
    ):
        return False
    start_date = values.get("start_date")
    activity_date = values.get("activity_date")
    if start_date is None:
        return activity_date == offer.start_date
    return start_date == offer.start_date and activity_date in (None, offer.start_date)


def _proposal_binds_package(
    proposal: ModelProposal,
    *,
    lodging: OfferSnapshot,
    activity: OfferSnapshot,
) -> bool:
    values = _proposal_values(proposal)
    if (
        lodging.service is not ServiceKind.LODGING
        or activity.service is not ServiceKind.ACTIVITY
        or values.get("service") != "package"
        or values.get("start_date") != lodging.start_date
        or values.get("end_date") != lodging.end_date
        or values.get("activity_date") != activity.start_date
        or values.get("adults") != lodging.party.adults
        or values.get("children") != lodging.party.children
    ):
        return False
    party_size = lodging.party.adults + lodging.party.children
    return activity.party.adults == party_size and activity.party.children == 0


def _projection_binds_draft(
    projection: ConversationProjection,
    draft: CommercialDraft,
) -> bool:
    if type(projection) is not ConversationProjection or type(draft) is not CommercialDraft:
        raise TypeError("projection/draft must be exact values")
    values = _projection_values(projection)
    components = draft.components
    if len(components) == 1:
        component = components[0]
        expected: dict[str, object] = {
            "service": (
                "hostel" if component.service is ServiceKind.LODGING else "agency"
            ),
            "start_date": component.start_date,
            "adults": component.party.adults,
            "children": component.party.children,
            "payment_method": draft.terms.payment_method,
        }
        if component.service is ServiceKind.LODGING:
            expected["end_date"] = component.end_date
        else:
            product_id = values.get("product_id")
            if not isinstance(product_id, str) or not component.lookup_id.startswith(
                f"lookup:{product_id}:"
            ):
                return False
            expected["product_id"] = product_id
            expected["activity_date"] = component.start_date
    elif len(components) == 2:
        lodging = next(
            item for item in components if item.service is ServiceKind.LODGING
        )
        activity = next(
            item for item in components if item.service is ServiceKind.ACTIVITY
        )
        expected = {
            "service": "package",
            "start_date": lodging.start_date,
            "end_date": lodging.end_date,
            "activity_date": activity.start_date,
            "adults": lodging.party.adults,
            "children": lodging.party.children,
            "payment_method": draft.terms.payment_method,
        }
        product_id = values.get("product_id")
        if product_id is not None:
            if not isinstance(product_id, str) or not activity.lookup_id.startswith(
                f"lookup:{product_id}:"
            ):
                return False
            expected["product_id"] = product_id
    else:
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
    return all(
        name not in values or (name in expected and values[name] == expected[name])
        for name in material_names
    )


def _canonical_public_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _date_value(value: object, name: str) -> date:
    if type(value) is not str:
        raise ConversationReductionError(f"{name} must be a canonical date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ConversationReductionError(f"{name} is not a valid date") from exc
    if parsed.isoformat() != value:
        raise ConversationReductionError(f"{name} is not a canonical date")
    return parsed


def _money(payload: dict[str, object]) -> Money:
    amount = payload.get("total_amount")
    currency = payload.get("currency")
    if type(amount) is not str or type(currency) is not str:
        raise ConversationReductionError("read amount/currency is incomplete")
    try:
        return Money(amount=Decimal(amount), currency=currency)
    except (InvalidOperation, ValueError) as exc:
        raise ConversationReductionError("read amount/currency is invalid") from exc


def _selected_payload(
    observation: ReadObservation,
    target_offer_id: str,
) -> dict[str, object]:
    public = observation.public_payload
    raw_options = public.get("options")
    if type(raw_options) is list:
        matches = tuple(
            item
            for item in raw_options
            if type(item) is dict and item.get("offer_id") == target_offer_id
        )
    else:
        matches = (public,) if public.get("offer_id") == target_offer_id else ()
    if len(matches) != 1:
        raise ConversationReductionError(
            "target offer is not uniquely present in the read"
        )
    return matches[0]


def _offer_and_query(
    observation: ReadObservation,
    target_offer_id: str,
    *,
    now: datetime,
) -> tuple[OfferSnapshot, SearchQuery, LookupEvidence]:
    if not (observation.observed_at <= now < observation.expires_at):
        raise ConversationReductionError("read observation is stale or from the future")
    payload = _selected_payload(observation, target_offer_id)
    product_id = None
    if observation.provider == "cloudbeds":
        service = ServiceKind.LODGING
        start_date = _date_value(payload.get("check_in"), "check_in")
        end_date = _date_value(payload.get("check_out"), "check_out")
        adults = payload.get("adults")
        children = payload.get("children")
        available_units = payload.get("available_units")
        if type(available_units) is not int or available_units < 1:
            raise ConversationReductionError("lodging offer is not available")
        label = payload.get("room_public_name")
    elif observation.provider == "bokun":
        product_id = payload.get("product_id")
        if type(product_id) is not str or not product_id:
            raise ConversationReductionError("activity product_id is invalid")
        service = ServiceKind.ACTIVITY
        start_date = _date_value(payload.get("activity_date"), "activity_date")
        end_date = None
        adults = payload.get("participants")
        children = 0
        if payload.get("available") is not True:
            raise ConversationReductionError("activity offer is not available")
        label = payload.get("product_public_name")
    else:
        raise ConversationReductionError(
            "read provider cannot authorize a reservation offer"
        )
    if (
        type(adults) is not int
        or adults < 1
        or type(children) is not int
        or children < 0
    ):
        raise ConversationReductionError("read party is invalid")
    if type(label) is not str or not label:
        raise ConversationReductionError("read public label is invalid")
    party = Party(adults=adults, children=children)
    query = SearchQuery(
        service=service,
        start_date=start_date,
        end_date=end_date,
        start_time=None,
        party=party,
    )
    if service is ServiceKind.LODGING:
        stable_query_hash = ReadRequest(
            request_id="stable-query",
            kind=ReadKind.LODGING,
            check_in=start_date,
            check_out=end_date,
            adults=adults,
            children=children,
        ).query_hash()
        lookup_id = "lookup:" + stable_query_hash
    else:
        stable_query_hash = ReadRequest(
            request_id="stable-query",
            kind=ReadKind.ACTIVITY,
            product_id=product_id,
            activity_date=start_date,
            participants=adults,
        ).query_hash()
        lookup_id = f"lookup:{product_id}:{stable_query_hash}"
    offer = OfferSnapshot(
        offer_id=target_offer_id,
        lookup_id=lookup_id,
        service=service,
        provider_ref=observation.private_binding_hash,
        public_label=label,
        start_date=start_date,
        end_date=end_date,
        start_time=None,
        party=party,
        total=_money(payload),
        available=True,
    )
    evidence = LookupEvidence(
        lookup_id=lookup_id,
        service=service,
        query_signature=query.signature,
        observed_at=observation.observed_at,
        expires_at=observation.expires_at,
        snapshot_hash=_canonical_public_hash(payload),
        status=LookupStatus.POSITIVE,
    )
    return offer, query, evidence


def _ready_component(
    *,
    workflow_id: str,
    event_seed: str,
    draft_id: str,
    offer: OfferSnapshot,
    query: SearchQuery,
    evidence: LookupEvidence,
    customer: CustomerFacts,
    terms: EconomicTerms,
    now: datetime,
) -> ReadyToSummarizeState:
    domain_state = new_workflow(workflow_id=workflow_id, started_at=now)
    domain_state = reduce_domain(
        domain_state,
        StartSearch(
            event_id=_event_id(event_seed, "search"),
            occurred_at=now,
            query=query,
        ),
    ).state
    domain_state = reduce_domain(
        domain_state,
        LookupRecorded(
            event_id=_event_id(event_seed, "lookup"),
            occurred_at=now,
            evidence=evidence,
            offers=(offer,),
        ),
    ).state
    domain_state = reduce_domain(
        domain_state,
        OfferChosen(
            event_id=_event_id(event_seed, "selection"),
            occurred_at=now,
            offer_id=offer.offer_id,
        ),
    ).state
    domain_state = reduce_domain(
        domain_state,
        DraftRequested(
            event_id=_event_id(event_seed, "draft"),
            occurred_at=now,
            draft_id=draft_id,
            customer=customer,
            terms=terms,
        ),
    ).state
    if type(domain_state) is not ReadyToSummarizeState:
        raise ConversationReductionError("domain did not create a commercial draft")
    return domain_state


def _replace_boundary(
    state: BoundaryState,
    *,
    workflow,
    source_event_id: str,
) -> BoundaryState:
    processed = state.processed_event_ids
    if source_event_id not in processed:
        processed = (*processed, source_event_id)
    return BoundaryState(
        schema_version=state.schema_version,
        lead_key=state.lead_key,
        version=state.version + 1,
        workflow=workflow,
        handoff=state.handoff,
        payments=state.payments,
        processed_event_ids=processed,
    )


def _consume_without_workflow_transition(
    state: BoundaryState,
    source_event_id: str,
) -> BoundaryState:
    if source_event_id in state.processed_event_ids:
        raise ConversationReductionError("source event is already processed")
    return replace(
        state,
        version=state.version + 1,
        processed_event_ids=state.processed_event_ids + (source_event_id,),
    )


def _reads_bind_draft(
    workflow: AwaitingConfirmationState,
    reads: tuple[ReadObservation, ...],
    *,
    now: datetime,
) -> bool:
    provider_by_service = {
        ServiceKind.LODGING: "cloudbeds",
        ServiceKind.ACTIVITY: "bokun",
    }
    for component in workflow.draft.components:
        matches = []
        for observation in reads:
            if observation.provider != provider_by_service[component.service]:
                continue
            try:
                candidate, _, _ = _offer_and_query(
                    observation,
                    component.offer_id,
                    now=now,
                )
            except ConversationReductionError:
                continue
            if candidate == component:
                matches.append(observation)
        if len(matches) != 1:
            return False
    return True


def _reads_explicitly_unavailable(reads: tuple[ReadObservation, ...]) -> bool:
    for observation in reads:
        payload = observation.public_payload
        if observation.provider == "bokun" and payload.get("available") is False:
            return True
        if observation.provider != "cloudbeds":
            continue
        raw_options = payload.get("options")
        options = (
            tuple(item for item in raw_options if type(item) is dict)
            if type(raw_options) is list
            else (payload,)
        )
        if options and all(
            type(item.get("available_units")) is int
            and item.get("available_units") < 1
            for item in options
        ):
            return True
    return False


def _refresh_revoked_reply(*, unavailable: bool, locale: str) -> str:
    if locale.casefold().startswith("en"):
        if unavailable:
            return (
                "The spot is no longer available. Nothing was booked; "
                "I’ll need to check another option before asking for confirmation again."
            )
        return (
            "Availability or price changed. Nothing was booked; "
            "I’ll present a new summary before asking for confirmation again."
        )
    if unavailable:
        return (
            "A vaga não está mais disponível. Nada foi reservado; "
            "vou verificar outra opção antes de pedir uma nova confirmação."
        )
    return (
        "A disponibilidade ou o valor mudou. Nada foi reservado; "
        "vou apresentar um novo resumo antes de pedir outra confirmação."
    )


def _generic_reply(proposal: ModelProposal) -> ConversationReply:
    chunks = proposal.reply_chunks or ("Preciso de mais informações para continuar.",)
    return ConversationReply("inform", chunks)


def _confirmed_reply(locale: str) -> str:
    if locale.casefold().startswith("en"):
        return "Perfect — I’ll process your booking now."
    return "Perfeito — vou processar sua reserva agora."


def _confirmation_not_authorized_reply(locale: str) -> str:
    if locale.casefold().startswith("en"):
        return "I could not authorize this action safely; no booking was made."
    return (
        "Não consegui autorizar essa execução com segurança; "
        "nenhuma reserva foi feita."
    )


def _reservation_already_processing_reply(locale: str) -> str:
    if locale.casefold().startswith("en"):
        return (
            "A booking is already being processed or completed in this conversation; "
            "I won’t create another one automatically."
        )
    return (
        "Já existe uma reserva em processamento ou concluída neste atendimento; "
        "não vou criar outra automaticamente."
    )


_POST_COMMAND_WORKFLOW_TYPES = {
    ExecutionQueuedState,
    ExecutingState,
    FailedBeforeProviderState,
    FailedNoEffectState,
    SucceededState,
    UncertainState,
    ManualReviewState,
}


def _post_command_offer_ids(workflow: object) -> tuple[str, ...] | None:
    if type(workflow) not in _POST_COMMAND_WORKFLOW_TYPES:
        return None
    return tuple(
        sorted(component.offer_id for component in workflow.command.payload.components)
    )


def _post_command_offer_overlap(
    existing_offer_ids: tuple[str, ...] | None,
    candidate_offer_ids: tuple[str, ...],
) -> bool:
    if existing_offer_ids is None:
        return False
    return bool(set(existing_offer_ids).intersection(candidate_offer_ids))


def _post_command_guard_decision(
    *,
    state: BoundaryState,
    projection: ConversationProjection,
    source_event_id: str,
) -> V2ConversationDecision:
    return V2ConversationDecision(
        next_state=_consume_without_workflow_transition(
            state,
            source_event_id,
        ),
        projection=replace(
            projection,
            stage=ConversationStage.CLOSING,
        ),
        commands=(),
        public_reply=ConversationReply(
            "reservation_already_processing",
            (_reservation_already_processing_reply(projection.locale),),
        ),
        receipt_requirements=("reservation_already_processing",),
    )


def _handoff_effect_guard_reply(locale: str) -> str:
    if type(locale) is not str or not locale:
        raise ValueError("locale must be non-empty exact text")
    if locale.casefold().startswith("en"):
        return (
            "Your human support conversation is still active; "
            "I won’t execute any actions."
        )
    return "Seu atendimento humano continua ativo; não vou executar efeitos."


def _unsupported_activity_party(projection: ConversationProjection) -> bool:
    if DesiredService.AGENCY not in projection.desired_services:
        return False
    values = {fact.name: fact.value.value for fact in projection.facts}
    adults = values.get("adults", 0)
    children = values.get("children", 0)
    if type(adults) is not int or type(children) is not int:
        return False
    return adults + children > 1


def _activity_group_handoff_reply(locale: str) -> str:
    if locale.casefold().startswith("en"):
        return (
            "For this tour with more than one person, I’ll ask the team "
            "to continue the group booking."
        )
    return (
        "Para esse passeio com mais de uma pessoa, vou chamar a equipe "
        "para continuar a reserva do grupo."
    )


class PackageCommandCoordinator:
    """Combine two authorized drafts into one package confirmation subject."""

    def combine(
        self,
        *,
        workflow_id: str,
        draft_id: str,
        lodging: ReadyToSummarizeState,
        activity: ReadyToSummarizeState,
        now: datetime,
    ) -> ReadyToSummarizeState:
        instant = _utc(now, "now")
        if (
            type(lodging) is not ReadyToSummarizeState
            or type(activity) is not ReadyToSummarizeState
        ):
            raise TypeError(
                "package components must be exact ReadyToSummarizeState values"
            )
        lodging_components = lodging.draft.components
        activity_components = activity.draft.components
        if (
            len(lodging_components) != 1
            or lodging_components[0].service is not ServiceKind.LODGING
            or len(activity_components) != 1
            or activity_components[0].service is not ServiceKind.ACTIVITY
        ):
            raise ConversationReductionError(
                "package requires one lodging and one activity draft"
            )
        if lodging.draft.customer != activity.draft.customer:
            raise ConversationReductionError("package customer facts diverge")
        if lodging.draft.terms != activity.draft.terms:
            raise ConversationReductionError("package economic terms diverge")
        components = (lodging_components[0], activity_components[0])
        draft = build_commercial_draft(
            draft_id=draft_id,
            version=1,
            created_at=instant,
            components=components,
            customer=lodging.draft.customer,
            terms=lodging.draft.terms,
        )
        meta = new_workflow(workflow_id=workflow_id, started_at=instant).meta
        return ReadyToSummarizeState(meta=meta, draft=draft)


class V2ConversationReducer:
    def __init__(
        self,
        *,
        approval_ttl: timedelta = timedelta(minutes=30),
        agency_payment_percentage: int = 20,
        hostel_payment_percentage: int = 100,
        critical_action_policy: CriticalActionPolicy | None = None,
    ) -> None:
        if type(approval_ttl) is not timedelta or approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be a positive exact timedelta")
        for value, name in (
            (agency_payment_percentage, "agency_payment_percentage"),
            (hostel_payment_percentage, "hostel_payment_percentage"),
        ):
            if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 100:
                raise ValueError(f"{name} must be an exact integer from 1 to 100")
        policy = critical_action_policy or CriticalActionPolicy.default()
        if type(policy) is not CriticalActionPolicy:
            raise TypeError("critical_action_policy must be exact CriticalActionPolicy")
        self._approval_ttl = approval_ttl
        self._agency_payment_percentage = agency_payment_percentage
        self._hostel_payment_percentage = hostel_payment_percentage
        self._critical_action_policy = policy

    def _critical_context(
        self,
        draft,
        *,
        summary_version: int,
        presented_at: datetime,
        locale: str,
    ) -> PendingCriticalActionContext:
        try:
            return critical_action_context(
                draft,
                summary_version=summary_version,
                presented_at=presented_at,
                locale=locale,
                approval_ttl=self._approval_ttl,
                agency_payment_percentage=self._agency_payment_percentage,
                hostel_payment_percentage=self._hostel_payment_percentage,
                policy=self._critical_action_policy,
            )
        except CriticalActionDenied:
            raise
        except (TypeError, ValueError) as exc:
            raise ConversationReductionError(
                "critical action proposal is denied or invalid"
            ) from exc

    def pending_action(
        self,
        workflow,
        *,
        locale: str,
    ) -> PendingCriticalActionContext | None:
        if type(workflow) is not AwaitingConfirmationState:
            return None
        return pending_action_context(
            workflow,
            locale=locale,
            approval_ttl=self._approval_ttl,
            agency_payment_percentage=self._agency_payment_percentage,
            hostel_payment_percentage=self._hostel_payment_percentage,
            policy=self._critical_action_policy,
        )

    @staticmethod
    def confirmation_projection_matches(
        workflow: object,
        projection: ConversationProjection,
    ) -> bool:
        if type(workflow) is not AwaitingConfirmationState:
            return False
        return _projection_binds_draft(projection, workflow.draft)

    def confirmation_capability_available(
        self,
        workflow: object,
        *,
        now: datetime,
    ) -> bool:
        if type(workflow) is not AwaitingConfirmationState:
            return False
        return critical_action_scope_available(
            workflow.draft,
            policy=self._critical_action_policy,
            now=now,
        )

    def approval_deadline(
        self,
        pending_action: PendingCriticalActionContext | None,
    ) -> datetime | None:
        if pending_action is None:
            return None
        if type(pending_action) is not PendingCriticalActionContext:
            raise TypeError(
                "pending_action must be exact PendingCriticalActionContext or None"
            )
        if self._critical_action_policy.valid_until is None:
            return pending_action.expires_at
        return min(
            pending_action.expires_at,
            self._critical_action_policy.valid_until,
        )

    @staticmethod
    def _critical_action_denied(
        state: BoundaryState,
        projection: ConversationProjection,
        source_event_id: str,
    ) -> V2ConversationDecision:
        return V2ConversationDecision(
            next_state=_consume_without_workflow_transition(state, source_event_id),
            projection=projection,
            commands=(),
            public_reply=ConversationReply(
                "critical_action_unavailable",
                (
                    "Essa ação não está disponível com segurança agora. Posso continuar ajudando sem executá-la.",
                ),
            ),
            receipt_requirements=("critical_action_denied",),
        )

    def reduce(
        self,
        *,
        state: BoundaryState,
        projection: ConversationProjection,
        proposal: ModelProposal,
        profile: PrivateCustomerBinding,
        reads: tuple[ReadObservation, ...],
        fact_commitment_hash: str,
        now: datetime,
    ) -> V2ConversationDecision:
        if (
            type(state) is not BoundaryState
            or type(projection) is not ConversationProjection
        ):
            raise TypeError("state/projection must be exact boundary values")
        proposal = validate_productive_proposal(proposal)
        if type(profile) is not PrivateCustomerBinding:
            raise TypeError("profile must be exact PrivateCustomerBinding")
        if type(reads) is not tuple or any(
            type(item) is not ReadObservation for item in reads
        ):
            raise TypeError("reads must contain exact ReadObservation values")
        if (
            type(fact_commitment_hash) is not str
            or _HASH_RE.fullmatch(fact_commitment_hash) is None
        ):
            raise ValueError("fact_commitment_hash must be a lowercase SHA-256")
        instant = _utc(now, "now")
        merged = _merge_projection(
            projection,
            proposal,
            fact_commitment_hash=fact_commitment_hash,
        )
        if proposal.intent == "select":
            merged = _without_critical_outcome(merged)
        force_activity_group_handoff = (
            proposal.intent != "request_handoff"
            and state.handoff is None
            and _unsupported_activity_party(merged)
        )
        if proposal.intent == "request_handoff" or force_activity_group_handoff:
            if state.handoff is None:
                handoff_request = HandoffRequested(
                    handoff_id=_identity(
                        state.lead_key,
                        proposal.source_event_id,
                        prefix="handoff",
                    ),
                    lead_key_hash=hashlib.sha256(
                        b"v2-handoff-lead-v1\x00" + state.lead_key.encode("utf-8")
                    ).hexdigest(),
                    incident_key=_identity(
                        proposal.source_event_id,
                        prefix="incident",
                    ),
                    reason_code=(
                        HandoffReasonCode.OPERATIONAL_REVIEW
                        if force_activity_group_handoff
                        else HandoffReasonCode.CUSTOMER_REQUESTED
                    ),
                    source_event_id=proposal.source_event_id,
                    reservation_anchor=None,
                    requested_at=instant,
                )
                handoff = HandoffWorkflow.from_request(
                    handoff_request,
                    HandoffEffectPolicy.default_email_disabled(),
                )
                next_state = replace(
                    _consume_without_workflow_transition(
                        state,
                        proposal.source_event_id,
                    ),
                    handoff=handoff,
                )
            else:
                handoff_request = None
                next_state = _consume_without_workflow_transition(
                    state,
                    proposal.source_event_id,
                )
            return V2ConversationDecision(
                next_state=next_state,
                projection=replace(merged, stage=ConversationStage.CLOSING),
                commands=(),
                public_reply=ConversationReply(
                    "handoff",
                    (
                        (_activity_group_handoff_reply(merged.locale),)
                        if force_activity_group_handoff
                        else proposal.reply_chunks
                        or ("Vou encaminhar seu atendimento para uma pessoa.",)
                    ),
                ),
                handoff_request=handoff_request,
                receipt_requirements=("handoff_relay",),
            )
        # An active human handoff suppresses identity-dependent effects even when the
        # private profile is incomplete.  Informational turns remain conversational.
        if state.handoff is not None and proposal.intent in {"select", "confirm"}:
            return V2ConversationDecision(
                next_state=_consume_without_workflow_transition(
                    state,
                    proposal.source_event_id,
                ),
                projection=replace(merged, stage=ConversationStage.CLOSING),
                commands=(),
                public_reply=ConversationReply(
                    "handoff",
                    (_handoff_effect_guard_reply(merged.locale),),
                ),
                receipt_requirements=("handoff_effect_guard",),
            )

        workflow = state.workflow
        post_command_offer_ids = _post_command_offer_ids(workflow)
        if post_command_offer_ids is not None and proposal.intent == "confirm":
            return _post_command_guard_decision(
                state=state,
                projection=projection,
                source_event_id=proposal.source_event_id,
            )

        # A complete customer binding is a write-boundary requirement, not a
        # prerequisite for greetings, discovery, FAQ, or read-only provider work.
        if proposal.intent in {"select", "confirm"} and not _profile_ready(
            profile, merged, instant
        ):
            return V2ConversationDecision(
                next_state=_consume_without_workflow_transition(
                    state, proposal.source_event_id
                ),
                projection=merged,
                commands=(),
                public_reply=ConversationReply(
                    "profile_completion",
                    (
                        "Para avançar com a reserva, preciso dos seus dados de contato.",
                    ),
                ),
                receipt_requirements=("profile_completion",),
            )

        if proposal.intent == "select":
            customer = _customer(profile, merged)
            if DesiredService.AGENCY in merged.desired_services and (
                customer.birth_date is None or customer.gender is None
            ):
                return V2ConversationDecision(
                    next_state=_consume_without_workflow_transition(
                        state, proposal.source_event_id
                    ),
                    projection=merged,
                    commands=(),
                    public_reply=ConversationReply(
                        "profile_completion",
                        (
                            "Para reservar o passeio, preciso da data de nascimento e gênero cadastral.",
                        ),
                    ),
                    receipt_requirements=("profile_completion",),
                )

        if type(workflow) is AwaitingConfirmationState and proposal.intent == "adjust":
            if (
                proposal.pending_disposition == "preserve"
                and self.confirmation_projection_matches(workflow, merged)
            ):
                return V2ConversationDecision(
                    next_state=_consume_without_workflow_transition(
                        state,
                        proposal.source_event_id,
                    ),
                    projection=merged,
                    commands=(),
                    public_reply=ConversationReply(
                        "inform",
                        proposal.reply_chunks
                        or ("O resumo continua válido; fico aguardando sua decisão.",),
                    ),
                    receipt_requirements=("proposal_preserved",),
                )
            transition = reduce_domain(
                workflow,
                ConfirmationReceived(
                    event_id=_event_id(proposal.source_event_id, "adjustment"),
                    occurred_at=instant,
                    confirmation_event_id=_identity(
                        proposal.source_event_id,
                        workflow.draft.subject_signature,
                        prefix="adjustment",
                    ),
                    decision=ConfirmationDecisionKind.ADJUST,
                    target_draft_version=workflow.draft.version,
                    subject_signature=workflow.draft.subject_signature,
                ),
            )
            return V2ConversationDecision(
                next_state=_replace_boundary(
                    state,
                    workflow=transition.state,
                    source_event_id=proposal.source_event_id,
                ),
                projection=merged,
                commands=(),
                public_reply=ConversationReply(
                    "adjust",
                    proposal.reply_chunks
                    or ("Tudo bem. Não vou executar esse resumo.",),
                ),
                receipt_requirements=("proposal_revoked",),
            )
        if type(workflow) is AwaitingConfirmationState and proposal.intent == "confirm":
            if not self.confirmation_capability_available(workflow, now=instant):
                transition = reduce_domain(
                    workflow,
                    ConfirmationReceived(
                        event_id=_event_id(proposal.source_event_id, "capability-denied"),
                        occurred_at=instant,
                        confirmation_event_id=_identity(
                            proposal.source_event_id,
                            workflow.draft.subject_signature,
                            prefix="capability-denied",
                        ),
                        decision=ConfirmationDecisionKind.ADJUST,
                        target_draft_version=workflow.draft.version,
                        subject_signature=workflow.draft.subject_signature,
                    ),
                )
                return V2ConversationDecision(
                    next_state=_replace_boundary(
                        state,
                        workflow=transition.state,
                        source_event_id=proposal.source_event_id,
                    ),
                    projection=merged,
                    commands=(),
                    public_reply=ConversationReply(
                        "critical_action_unavailable",
                        (
                            "Essa ação não está disponível com segurança agora. Não vou executar o resumo anterior.",
                        ),
                    ),
                    receipt_requirements=("critical_action_denied",),
                )
            if not self.confirmation_projection_matches(workflow, merged):
                transition = reduce_domain(
                    workflow,
                    ConfirmationReceived(
                        event_id=_event_id(proposal.source_event_id, "superseded"),
                        occurred_at=instant,
                        confirmation_event_id=_identity(
                            proposal.source_event_id,
                            workflow.draft.subject_signature,
                            prefix="superseded",
                        ),
                        decision=ConfirmationDecisionKind.ADJUST,
                        target_draft_version=workflow.draft.version,
                        subject_signature=workflow.draft.subject_signature,
                    ),
                )
                return V2ConversationDecision(
                    next_state=_replace_boundary(
                        state,
                        workflow=transition.state,
                        source_event_id=proposal.source_event_id,
                    ),
                    projection=merged,
                    commands=(),
                    public_reply=ConversationReply(
                        "proposal_changed",
                        (
                            "Os detalhes mudaram. Não vou executar o resumo anterior; vou atualizar a proposta antes de pedir nova confirmação.",
                        ),
                    ),
                    receipt_requirements=("proposal_superseded",),
                )
            pending = self.pending_action(workflow, locale=merged.locale)
            approval_match = approval_assertion_matches(
                workflow=workflow,
                pending_action=pending,
                proposal=proposal,
                now=instant,
            )
            if approval_match is not ApprovalMatch.MATCH:
                if approval_match is ApprovalMatch.EXPIRED:
                    transition = reduce_domain(
                        workflow,
                        ConfirmationReceived(
                            event_id=_event_id(
                                proposal.source_event_id,
                                "approval-expired",
                            ),
                            occurred_at=instant,
                            confirmation_event_id=_identity(
                                proposal.source_event_id,
                                workflow.draft.subject_signature,
                                prefix="approval-expired",
                            ),
                            decision=ConfirmationDecisionKind.ADJUST,
                            target_draft_version=workflow.draft.version,
                            subject_signature=workflow.draft.subject_signature,
                        ),
                    )
                    expired_projection = _with_critical_outcome(
                        merged,
                        outcome="proposal_expired",
                        fact_commitment_hash=fact_commitment_hash,
                    )
                    return V2ConversationDecision(
                        next_state=_replace_boundary(
                            state,
                            workflow=transition.state,
                            source_event_id=proposal.source_event_id,
                        ),
                        projection=expired_projection,
                        commands=(),
                        public_reply=ConversationReply(
                            "approval_expired",
                            (
                                "Esse resumo expirou. Vou atualizar disponibilidade e valores antes de pedir uma nova confirmação.",
                            ),
                        ),
                        receipt_requirements=("approval_expired",),
                    )
                return V2ConversationDecision(
                    next_state=_consume_without_workflow_transition(
                        state, proposal.source_event_id
                    ),
                    projection=merged,
                    commands=(),
                    public_reply=ConversationReply(
                        "stale_confirmation",
                        (
                            "Essa resposta não autoriza exatamente o resumo atual. Vou apresentar os termos novamente.",
                        ),
                    ),
                    receipt_requirements=("stale_confirmation",),
                )
            if workflow.draft.customer != _customer(profile, merged):
                return V2ConversationDecision(
                    next_state=_consume_without_workflow_transition(
                        state, proposal.source_event_id
                    ),
                    projection=merged,
                    commands=(),
                    public_reply=ConversationReply(
                        "profile_completion",
                        ("Seus dados mudaram; revise o perfil antes de confirmar.",),
                    ),
                    receipt_requirements=("profile_completion",),
                )
            if not _reads_bind_draft(workflow, reads, now=instant):
                current_refresh = bool(reads) and all(
                    observation.observed_at <= instant < observation.expires_at
                    for observation in reads
                )
                if current_refresh:
                    unavailable = _reads_explicitly_unavailable(reads)
                    revoked_projection = _with_critical_outcome(
                        merged,
                        outcome="proposal_revoked_after_refresh",
                        fact_commitment_hash=fact_commitment_hash,
                    )
                    transition = reduce_domain(
                        workflow,
                        ConfirmationReceived(
                            event_id=_event_id(
                                proposal.source_event_id,
                                "refresh-mismatch",
                            ),
                            occurred_at=instant,
                            confirmation_event_id=_identity(
                                proposal.source_event_id,
                                workflow.draft.subject_signature,
                                prefix="refresh-mismatch",
                            ),
                            decision=ConfirmationDecisionKind.ADJUST,
                            target_draft_version=workflow.draft.version,
                            subject_signature=workflow.draft.subject_signature,
                        ),
                    )
                    return V2ConversationDecision(
                        next_state=_replace_boundary(
                            state,
                            workflow=transition.state,
                            source_event_id=proposal.source_event_id,
                        ),
                        projection=revoked_projection,
                        commands=(),
                        public_reply=ConversationReply(
                            "offer_unavailable" if unavailable else "proposal_changed",
                            (
                                _refresh_revoked_reply(
                                    unavailable=unavailable,
                                    locale=merged.locale,
                                ),
                            ),
                        ),
                        receipt_requirements=("proposal_revoked_after_refresh",),
                    )
                return V2ConversationDecision(
                    next_state=_consume_without_workflow_transition(
                        state, proposal.source_event_id
                    ),
                    projection=merged,
                    commands=(),
                    public_reply=ConversationReply(
                        "fresh_reads_required",
                        (
                            "Vou atualizar disponibilidade e valores antes de confirmar.",
                        ),
                    ),
                    receipt_requirements=("fresh_reads_required",),
                )
            transition = reduce_domain(
                workflow,
                ConfirmationReceived(
                    event_id=_event_id(proposal.source_event_id, "confirmation"),
                    occurred_at=instant,
                    confirmation_event_id=_identity(
                        proposal.source_event_id,
                        workflow.draft.subject_signature,
                        prefix="confirmation",
                    ),
                    decision=ConfirmationDecisionKind.ACCEPT,
                    target_draft_version=workflow.draft.version,
                    subject_signature=workflow.draft.subject_signature,
                ),
            )
            if not transition.commands:
                return V2ConversationDecision(
                    next_state=_consume_without_workflow_transition(
                        state,
                        proposal.source_event_id,
                    ),
                    projection=merged,
                    commands=(),
                    public_reply=ConversationReply(
                        "critical_action_unavailable",
                        (_confirmation_not_authorized_reply(merged.locale),),
                    ),
                    receipt_requirements=("reservation_command_absent",),
                )
            next_state = _replace_boundary(
                state,
                workflow=transition.state,
                source_event_id=proposal.source_event_id,
            )
            return V2ConversationDecision(
                next_state=next_state,
                projection=replace(merged, stage=ConversationStage.CLOSING),
                commands=transition.commands,
                public_reply=ConversationReply(
                    "reservation_authorized",
                    (_confirmed_reply(merged.locale),),
                ),
                receipt_requirements=("reservation_command",),
            )

        if proposal.intent == "select" and proposal.target_offer_ids:
            selected: list[tuple[OfferSnapshot, SearchQuery, LookupEvidence]] = []
            for target_offer_id in proposal.target_offer_ids:
                matching_observations = []
                for observation in reads:
                    try:
                        _selected_payload(observation, target_offer_id)
                    except ConversationReductionError:
                        continue
                    matching_observations.append(observation)
                if len(matching_observations) != 1:
                    raise ConversationReductionError(
                        "package selection requires each offer in one uniquely bound read"
                    )
                selected.append(
                    _offer_and_query(
                        matching_observations[0],
                        target_offer_id,
                        now=instant,
                    )
                )
            by_service = {item[0].service: item for item in selected}
            if set(by_service) != {ServiceKind.LODGING, ServiceKind.ACTIVITY}:
                raise ConversationReductionError(
                    "package selection requires one lodging and one activity offer"
                )
            lodging_offer, lodging_query, lodging_evidence = by_service[
                ServiceKind.LODGING
            ]
            activity_offer, activity_query, activity_evidence = by_service[
                ServiceKind.ACTIVITY
            ]
            if not _proposal_binds_package(
                proposal,
                lodging=lodging_offer,
                activity=activity_offer,
            ):
                raise ConversationReductionError(
                    "proposal facts diverge from the selected package offers"
                )
            values = _proposal_values(proposal)
            payment_method = values.get("payment_method")
            if payment_method not in ("stripe", "wise", "pix"):
                raise ConversationReductionError(
                    "payment method is incomplete or invalid"
                )
            workflow_id = _identity(
                state.lead_key, proposal.source_event_id, prefix="workflow"
            )
            customer = _customer(profile, merged)
            terms = EconomicTerms(payment_method=payment_method, add_ons=())
            lodging_state = _ready_component(
                workflow_id=_identity(workflow_id, "lodging", prefix="workflow"),
                event_seed=proposal.source_event_id + ":lodging",
                draft_id=_identity(
                    workflow_id, lodging_offer.offer_id, prefix="draft"
                ),
                offer=lodging_offer,
                query=lodging_query,
                evidence=lodging_evidence,
                customer=customer,
                terms=terms,
                now=instant,
            )
            activity_state = _ready_component(
                workflow_id=_identity(workflow_id, "activity", prefix="workflow"),
                event_seed=proposal.source_event_id + ":activity",
                draft_id=_identity(
                    workflow_id, activity_offer.offer_id, prefix="draft"
                ),
                offer=activity_offer,
                query=activity_query,
                evidence=activity_evidence,
                customer=customer,
                terms=terms,
                now=instant,
            )
            domain_state = PackageCommandCoordinator().combine(
                workflow_id=workflow_id,
                draft_id=_identity(
                    workflow_id,
                    *sorted(proposal.target_offer_ids),
                    prefix="draft",
                ),
                lodging=lodging_state,
                activity=activity_state,
                now=instant,
            )
            if _post_command_offer_overlap(
                post_command_offer_ids,
                tuple(
                    component.offer_id for component in domain_state.draft.components
                ),
            ):
                return _post_command_guard_decision(
                    state=state,
                    projection=projection,
                    source_event_id=proposal.source_event_id,
                )
            summary_id = _identity(
                domain_state.draft.draft_id,
                str(domain_state.draft.version),
                prefix="summary",
            )
            try:
                critical_context = self._critical_context(
                    domain_state.draft,
                    summary_version=domain_state.draft.version,
                    presented_at=instant,
                    locale=merged.locale,
                )
            except CriticalActionDenied:
                return self._critical_action_denied(
                    state, merged, proposal.source_event_id
                )
            proposal_digest = critical_proposal_digest(
                domain_state.draft,
                critical_context,
            )
            domain_state = reduce_domain(
                domain_state,
                SummaryRecorded(
                    event_id=_event_id(proposal.source_event_id, "summary"),
                    occurred_at=instant,
                    summary_event_id=summary_id,
                    draft_version=domain_state.draft.version,
                    subject_signature=domain_state.draft.subject_signature,
                    outbox_message_id=critical_summary_outbox_id(
                        summary_id,
                        proposal_digest,
                    ),
                ),
            ).state
            if type(domain_state) is not AwaitingConfirmationState:
                raise ConversationReductionError(
                    "domain did not bind the package confirmation summary"
                )
            next_state = _replace_boundary(
                state,
                workflow=domain_state,
                source_event_id=proposal.source_event_id,
            )
            return V2ConversationDecision(
                next_state=next_state,
                projection=replace(merged, stage=ConversationStage.CLOSING),
                commands=(),
                public_reply=ConversationReply(
                    "summary",
                    (critical_context.public_summary,),
                ),
                receipt_requirements=("summary_presented",),
            )

        if proposal.intent == "select" and proposal.target_offer_id is not None:
            matching = []
            for observation in reads:
                try:
                    payload = _selected_payload(observation, proposal.target_offer_id)
                except ConversationReductionError:
                    continue
                matching.append((observation, payload))
            if len(matching) != 1:
                raise ConversationReductionError(
                    "selection requires one uniquely bound read"
                )
            observation = matching[0][0]
            offer, query, evidence = _offer_and_query(
                observation,
                proposal.target_offer_id,
                now=instant,
            )
            if not _proposal_binds_offer(proposal, offer):
                raise ConversationReductionError(
                    "proposal facts diverge from the selected provider offer"
                )
            values = _proposal_values(proposal)
            payment_method = values.get("payment_method")
            if payment_method not in ("stripe", "wise", "pix"):
                raise ConversationReductionError(
                    "payment method is incomplete or invalid"
                )
            workflow_id = _identity(
                state.lead_key, proposal.source_event_id, prefix="workflow"
            )
            domain_state = new_workflow(workflow_id=workflow_id, started_at=instant)
            domain_state = reduce_domain(
                domain_state,
                StartSearch(
                    event_id=_event_id(proposal.source_event_id, "search"),
                    occurred_at=instant,
                    query=query,
                ),
            ).state
            domain_state = reduce_domain(
                domain_state,
                LookupRecorded(
                    event_id=_event_id(proposal.source_event_id, "lookup"),
                    occurred_at=instant,
                    evidence=evidence,
                    offers=(offer,),
                ),
            ).state
            domain_state = reduce_domain(
                domain_state,
                OfferChosen(
                    event_id=_event_id(proposal.source_event_id, "selection"),
                    occurred_at=instant,
                    offer_id=offer.offer_id,
                ),
            ).state
            domain_state = reduce_domain(
                domain_state,
                DraftRequested(
                    event_id=_event_id(proposal.source_event_id, "draft"),
                    occurred_at=instant,
                    draft_id=_identity(workflow_id, offer.offer_id, prefix="draft"),
                    customer=_customer(profile, merged),
                    terms=EconomicTerms(payment_method=payment_method, add_ons=()),
                ),
            ).state
            if type(domain_state) is not ReadyToSummarizeState:
                raise ConversationReductionError(
                    "domain did not create a commercial draft"
                )
            if _post_command_offer_overlap(
                post_command_offer_ids,
                tuple(
                    component.offer_id for component in domain_state.draft.components
                ),
            ):
                return _post_command_guard_decision(
                    state=state,
                    projection=projection,
                    source_event_id=proposal.source_event_id,
                )
            summary_id = _identity(
                domain_state.draft.draft_id,
                str(domain_state.draft.version),
                prefix="summary",
            )
            try:
                critical_context = self._critical_context(
                    domain_state.draft,
                    summary_version=domain_state.draft.version,
                    presented_at=instant,
                    locale=merged.locale,
                )
            except CriticalActionDenied:
                return self._critical_action_denied(
                    state, merged, proposal.source_event_id
                )
            proposal_digest = critical_proposal_digest(
                domain_state.draft,
                critical_context,
            )
            domain_state = reduce_domain(
                domain_state,
                SummaryRecorded(
                    event_id=_event_id(proposal.source_event_id, "summary"),
                    occurred_at=instant,
                    summary_event_id=summary_id,
                    draft_version=domain_state.draft.version,
                    subject_signature=domain_state.draft.subject_signature,
                    outbox_message_id=critical_summary_outbox_id(
                        summary_id,
                        proposal_digest,
                    ),
                ),
            ).state
            if type(domain_state) is not AwaitingConfirmationState:
                raise ConversationReductionError(
                    "domain did not bind the confirmation summary"
                )
            next_state = _replace_boundary(
                state,
                workflow=domain_state,
                source_event_id=proposal.source_event_id,
            )
            return V2ConversationDecision(
                next_state=next_state,
                projection=replace(merged, stage=ConversationStage.CLOSING),
                commands=(),
                public_reply=ConversationReply(
                    "summary",
                    (critical_context.public_summary,),
                ),
                receipt_requirements=("summary_presented",),
            )

        return V2ConversationDecision(
            next_state=_consume_without_workflow_transition(
                state, proposal.source_event_id
            ),
            projection=merged,
            commands=(),
            public_reply=_generic_reply(proposal),
        )


__all__ = [
    "ConversationReductionError",
    "ConversationReply",
    "PackageCommandCoordinator",
    "V2ConversationDecision",
    "V2ConversationReducer",
    "typed_facts_from_proposal",
]
