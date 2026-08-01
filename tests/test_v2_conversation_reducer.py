from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

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
    AwaitingAdjustmentState,
    AwaitingConfirmationState,
    CustomerFacts,
    DraftRequested,
    EconomicTerms,
    ExecutionCertainty,
    ExecutionFinished,
    ExecutionOutcome,
    ExecutionQueuedState,
    ExecutionStarted,
    FailedBeforeProviderState,
    FailedNoEffectState,
    LookupEvidence,
    LookupRecorded,
    LookupStatus,
    Money,
    OfferChosen,
    OfferSnapshot,
    PassengerFacts,
    Party,
    ReadyToSummarizeState,
    ReservationCommand,
    ReservationOperation,
    SearchQuery,
    ServiceKind,
    StartSearch,
    SummaryRecorded,
    dumps_command,
    new_workflow,
)
from reservation_domain import (
    reduce as reduce_domain,
)
from reservation_execution import PreparationFailure
from reservation_followup import (
    HandoffEffectPolicy,
    HandoffReasonCode,
    HandoffRequested,
    new_handoff,
)
from v2_adapters.cloudbeds import CloudbedsReadAdapter
from v2_application.conversation import (
    ConversationReductionError,
    PackageCommandCoordinator,
    V2ConversationReducer,
    _handoff_effect_guard_reply,
)
from v2_application.passengers import projection_passengers
from v2_application.critical_actions import (
    CriticalActionPolicy,
    critical_action_context,
    critical_proposal_digest,
    critical_summary_outbox_id,
)
from v2_application.reads import (
    PrivateOfferBindingResolver,
)
from v2_application.reservations import (
    ReservationAllocator,
    V2ReservationExecutionAdapter,
)
from v2_contracts.critical_actions import ApprovalBasis, CriticalActionKind
from v2_contracts.model import ModelFact, ModelProposal
from v2_contracts.passengers import PassengerInput
from v2_contracts.profile import PrivateCustomerBinding
from v2_contracts.providers import (
    ProviderWriteAuthorization,
    ReadKind,
    ReadObservation,
    ReadRequest,
)

NOW = datetime(2026, 7, 23, 20, 0, tzinfo=timezone.utc)
LEAD_ID = "manychat:subscriber-001"
FRAME_HASH = "f" * 64
LODGING_OFFER_ID = "offer:" + "a" * 32
ACTIVITY_OFFER_ID = "offer:" + "c" * 32
LODGING_BINDING_HASH = "b" * 64
ACTIVITY_BINDING_HASH = "d" * 64


def _passenger(
    position: int,
    participant_type: str,
    *,
    full_name: str | None,
    birth_date: date | None,
    gender: str | None,
    country_code: str | None = "BR",
) -> PassengerInput:
    return PassengerInput(
        position=position,
        participant_type=participant_type,
        full_name=full_name,
        birth_date=birth_date,
        gender=gender,
        country_code=country_code,
    )


def test_activity_party_over_one_no_longer_forces_operational_handoff() -> None:
    proposal = _proposal(
        source="event:unsupported-activity-party",
        intent="adjust",
        service="agency",
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=proposal,
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.commands == ()
    assert decision.handoff_request is None
    assert decision.next_state.handoff is None
    assert decision.public_reply.kind == "inform"


def test_model_requested_group_handoff_remains_customer_requested() -> None:
    proposal = _proposal(
        source="event:model-requested-group-handoff",
        intent="request_handoff",
        service="agency",
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=proposal,
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.commands == ()
    assert decision.handoff_request is not None
    assert decision.handoff_request.reason_code is HandoffReasonCode.CUSTOMER_REQUESTED
    assert decision.public_reply.chunks == ("Mensagem pública do modelo.",)


def test_single_activity_participant_does_not_force_operational_handoff() -> None:
    proposal = _proposal(
        source="event:supported-activity-party",
        intent="adjust",
        service="agency",
    )
    proposal = replace(
        proposal,
        facts=tuple(
            ModelFact("adults", 1) if fact.name == "adults" else fact
            for fact in proposal.facts
        ),
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=proposal,
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.handoff_request is None
    assert decision.next_state.handoff is None


def test_historical_group_workflow_without_manifest_stays_blocked() -> None:
    package_ready = PackageCommandCoordinator().combine(
        workflow_id="workflow:group-package",
        draft_id="draft:group-package",
        lodging=_ready_state(
            service=ServiceKind.LODGING,
            workflow_id="workflow:group-package-lodging",
        ),
        activity=_ready_state(
            service=ServiceKind.ACTIVITY,
            workflow_id="workflow:group-package-activity",
        ),
        now=NOW,
    )
    awaiting = _awaiting_from_ready(package_ready)

    decision = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(package=True),
        proposal=_proposal(
            source="event:confirm-incomplete-group-package",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
            service="package",
        ),
        profile=_profile(),
        reads=tuple(_read_for_component(item) for item in awaiting.draft.components),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )

    assert decision.commands == ()
    assert decision.handoff_request is None
    assert decision.public_reply.kind == "profile_completion"


def test_complete_mixed_group_creates_signed_summary_without_handoff() -> None:
    proposal = ModelProposal(
        source_event_id="event:complete-mixed-group",
        intent="select",
        reply_chunks=("Vou preparar o resumo do grupo.",),
        facts=(
            ModelFact("language", "pt-BR"),
            ModelFact("service", "agency"),
            ModelFact("product_id", "product:buracao-001"),
            ModelFact("activity_date", date(2026, 8, 11)),
            ModelFact("adults", 2),
            ModelFact("children", 1),
            ModelFact("payment_method", "wise"),
        ),
        passengers=(
            _passenger(
                1,
                "adult",
                full_name="Pessoa Grupo Um",
                birth_date=date(1990, 1, 2),
                gender="f",
            ),
            _passenger(
                2,
                "adult",
                full_name="Pessoa Grupo Dois",
                birth_date=date(1992, 3, 4),
                gender="m",
            ),
            _passenger(
                3,
                "child",
                full_name="Pessoa Grupo Três",
                birth_date=date(2016, 5, 6),
                gender="f",
            ),
        ),
        read_requests=(),
        effect_proposals=(),
        target_offer_id=ACTIVITY_OFFER_ID,
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=proposal,
        profile=_profile(),
        reads=(_activity_read(adults=2, children=1, start_time="08:30"),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.handoff_request is None
    assert decision.commands == ()
    assert decision.public_reply.kind == "summary"
    assert type(decision.next_state.workflow) is AwaitingConfirmationState
    assert decision.next_state.workflow.draft.components[0].start_time == "08:30"
    assert "às 08:30" in " ".join(decision.public_reply.chunks)
    assert decision.next_state.workflow.draft.customer.passengers == (
        PassengerFacts(
            1,
            "adult",
            "Pessoa Grupo Um",
            date(1990, 1, 2),
            "f",
            "BR",
        ),
        PassengerFacts(
            2,
            "adult",
            "Pessoa Grupo Dois",
            date(1992, 3, 4),
            "m",
            "BR",
        ),
        PassengerFacts(
            3,
            "child",
            "Pessoa Grupo Três",
            date(2016, 5, 6),
            "f",
            "BR",
        ),
    )
    values = {item.name: item.value.value for item in decision.projection.facts}
    assert "passenger_manifest" in values


def test_post_summary_passenger_correction_revokes_old_authority_without_command() -> None:
    reducer = _reducer()
    selected = reducer.reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=ModelProposal(
            source_event_id="event:group-summary-before-correction",
            intent="select",
            reply_chunks=("Vou preparar o resumo do grupo.",),
            facts=(
                ModelFact("language", "pt-BR"),
                ModelFact("service", "agency"),
                ModelFact("product_id", "product:buracao-001"),
                ModelFact("activity_date", date(2026, 8, 11)),
                ModelFact("adults", 2),
                ModelFact("children", 0),
                ModelFact("payment_method", "wise"),
            ),
            passengers=(
                _passenger(
                    1,
                    "adult",
                    full_name="Pessoa Grupo Um",
                    birth_date=date(1990, 1, 2),
                    gender="f",
                ),
                _passenger(
                    2,
                    "adult",
                    full_name="Pessoa Grupo Dois",
                    birth_date=date(1992, 3, 4),
                    gender="m",
                ),
            ),
            read_requests=(),
            effect_proposals=(),
            target_offer_id=ACTIVITY_OFFER_ID,
        ),
        profile=_profile(),
        reads=(_activity_read(adults=2),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )
    awaiting = selected.next_state.workflow
    assert type(awaiting) is AwaitingConfirmationState
    old_signature = awaiting.draft.subject_signature

    corrected = reducer.reduce(
        state=selected.next_state,
        projection=selected.projection,
        proposal=ModelProposal(
            source_event_id="event:group-passenger-correction",
            intent="adjust",
            reply_chunks=("Atualizei os dados e descartei o resumo anterior.",),
            facts=(),
            passengers=(
                _passenger(
                    2,
                    "adult",
                    full_name="Pessoa Grupo Corrigida",
                    birth_date=date(1992, 3, 4),
                    gender="m",
                ),
            ),
            read_requests=(),
            effect_proposals=(),
            pending_disposition="revoke",
        ),
        profile=_profile(),
        reads=(),
        fact_commitment_hash="e" * 64,
        now=NOW + timedelta(seconds=1),
    )

    assert type(corrected.next_state.workflow) is AwaitingAdjustmentState
    assert corrected.commands == ()
    assert corrected.receipt_requirements == ("proposal_revoked",)
    assert corrected.next_state.workflow.draft.subject_signature == old_signature
    assert (
        reducer.confirmation_projection_matches(
            awaiting,
            corrected.projection,
        )
        is False
    )
    passengers = projection_passengers(corrected.projection, Party(2, 0))
    assert passengers is not None
    assert passengers[1].full_name == "Pessoa Grupo Corrigida"


def test_incomplete_group_manifest_is_persisted_but_cannot_select() -> None:
    proposal = ModelProposal(
        source_event_id="event:incomplete-mixed-group",
        intent="select",
        reply_chunks=("Ainda falta um dado do grupo.",),
        facts=(
            ModelFact("service", "agency"),
            ModelFact("product_id", "product:buracao-001"),
            ModelFact("activity_date", date(2026, 8, 11)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", "wise"),
        ),
        passengers=(
            _passenger(
                1,
                "adult",
                full_name="Pessoa Completa",
                birth_date=date(1990, 1, 2),
                gender="f",
            ),
            _passenger(
                2,
                "adult",
                full_name="Pessoa Incompleta",
                birth_date=None,
                gender="m",
            ),
        ),
        read_requests=(),
        effect_proposals=(),
        target_offer_id=ACTIVITY_OFFER_ID,
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=proposal,
        profile=_profile(),
        reads=(_activity_read(adults=2),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.handoff_request is None
    assert decision.commands == ()
    assert decision.next_state.workflow is None
    assert decision.public_reply.kind == "profile_completion"
    assert any(
        item.name == "passenger_manifest" for item in decision.projection.facts
    )


def test_authenticated_single_party_workflow_overrides_stale_projection_group() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(
            service=ServiceKind.ACTIVITY,
            workflow_id="workflow:single-party",
            adults=1,
        )
    )
    stale_projection = ConversationProjection(
        stage=ConversationStage.CLOSING,
        desired_services=(DesiredService.AGENCY,),
        locale="pt-BR",
        facts=(
            TypedFact("service", StringSlot("agency"), FRAME_HASH),
            TypedFact("adults", IntegerSlot(2), FRAME_HASH),
            TypedFact("children", IntegerSlot(0), FRAME_HASH),
        ),
        reservation_execution_projection=None,
    )
    proposal = ModelProposal(
        source_event_id="event:single-party-inform",
        intent="inform",
        reply_chunks=("A reserva atual é para uma pessoa.",),
        facts=(ModelFact("children", 0),),
        read_requests=(),
        effect_proposals=(),
    )

    decision = _reducer().reduce(
        state=_boundary(awaiting),
        projection=stale_projection,
        proposal=proposal,
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )

    assert decision.handoff_request is None
    assert decision.next_state.handoff is None
    assert decision.public_reply.kind == "inform"


def test_stale_group_projection_without_workflow_does_not_force_handoff() -> None:
    stale_projection = ConversationProjection(
        stage=ConversationStage.AGENCY,
        desired_services=(DesiredService.AGENCY,),
        locale="pt-BR",
        facts=(
            TypedFact("service", StringSlot("agency"), FRAME_HASH),
            TypedFact("adults", IntegerSlot(2), FRAME_HASH),
            TypedFact("children", IntegerSlot(0), FRAME_HASH),
        ),
        reservation_execution_projection=None,
    )
    proposal = ModelProposal(
        source_event_id="event:stale-group-inform",
        intent="inform",
        reply_chunks=("Agora quero saber apenas sobre o hostel.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=stale_projection,
        proposal=proposal,
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )

    assert decision.handoff_request is None
    assert decision.next_state.handoff is None
    assert decision.commands == ()
    assert decision.public_reply.kind == "inform"


def test_current_return_to_one_overrides_authenticated_group_party() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(
            service=ServiceKind.ACTIVITY,
            workflow_id="workflow:return-to-one",
        )
    )
    proposal = ModelProposal(
        source_event_id="event:return-to-one",
        intent="inform",
        reply_chunks=("Atualizei para uma pessoa.",),
        facts=(
            ModelFact("service", "agency"),
            ModelFact("adults", 1),
        ),
        read_requests=(),
        effect_proposals=(),
    )

    decision = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(package=True),
        proposal=proposal,
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )

    assert decision.handoff_request is None
    assert decision.next_state.handoff is None
    values = {fact.name: fact.value.value for fact in decision.projection.facts}
    assert values["adults"] == 1


def test_active_handoff_effect_guard_is_localized() -> None:
    assert _handoff_effect_guard_reply("pt-BR") == (
        "Seu atendimento humano continua ativo; não vou executar efeitos."
    )
    assert _handoff_effect_guard_reply("en-US") == (
        "Your human support conversation is still active; I won’t execute any actions."
    )


def test_active_handoff_reducer_guard_uses_projection_locale() -> None:
    handoff = new_handoff(
        HandoffRequested(
            handoff_id="handoff:english-guard",
            lead_key_hash="a" * 64,
            incident_key="incident:english-guard",
            reason_code=HandoffReasonCode.CUSTOMER_REQUESTED,
            source_event_id="event:open-english-handoff",
            reservation_anchor=None,
            requested_at=NOW,
        ),
        HandoffEffectPolicy.default_email_disabled(),
    ).state
    state = replace(_boundary(), handoff=handoff)
    proposal = _proposal(
        source="event:english-select-after-handoff",
        intent="select",
        target_offer_id=LODGING_OFFER_ID,
    )
    proposal = replace(
        proposal,
        facts=tuple(
            ModelFact("language", "en-US") if fact.name == "language" else fact
            for fact in proposal.facts
        ),
    )

    decision = _reducer().reduce(
        state=state,
        projection=replace(_projection(), locale="en-US"),
        proposal=proposal,
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.commands == ()
    assert decision.public_reply.chunks == (
        "Your human support conversation is still active; I won’t execute any actions.",
    )


def _enabled_policy() -> CriticalActionPolicy:
    return CriticalActionPolicy(
        frozenset(
            {
                CriticalActionKind.RESERVE_LODGING,
                CriticalActionKind.BOOK_ACTIVITY,
                CriticalActionKind.BOOK_PACKAGE,
                CriticalActionKind.INITIATE_PAYMENT,
            }
        ),
        enabled_payment_methods=frozenset({"stripe", "wise", "pix"}),
    )


def _reducer() -> V2ConversationReducer:
    return V2ConversationReducer(critical_action_policy=_enabled_policy())


def _profile(*, complete: bool = True) -> PrivateCustomerBinding:
    return PrivateCustomerBinding(
        binding_id="profile:subscriber-001",
        content_hash="e" * 64,
        full_name="Carlos Teste" if complete else None,
        email="carlos@example.invalid" if complete else None,
        phone_e164="+5575999990000" if complete else None,
        country_code="BR" if complete else None,
        observed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
        complete=complete,
    )


def _authenticated_manychat_contact_without_country() -> PrivateCustomerBinding:
    return PrivateCustomerBinding(
        binding_id="profile:subscriber-contact-only",
        content_hash="c" * 64,
        full_name="Carlos Teste",
        email="maya.cloudbeds.canary@example.com",
        phone_e164="+12025550123",
        country_code=None,
        observed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
        complete=False,
    )


def _projection(*, package: bool = False) -> ConversationProjection:
    return ConversationProjection(
        stage=ConversationStage.RECEPTIONIST,
        desired_services=(
            (DesiredService.HOSTEL, DesiredService.AGENCY)
            if package
            else (DesiredService.HOSTEL,)
        ),
        locale="pt-BR",
        facts=(),
        reservation_execution_projection=None,
    )


def _boundary(workflow=None) -> BoundaryState:
    return BoundaryState(
        schema_version=7,
        lead_key=LEAD_ID,
        version=0,
        workflow=workflow,
        handoff=None,
        payments=(),
        processed_event_ids=(),
    )


def _proposal(
    *,
    source: str,
    intent: str,
    target_offer_id: str | None = None,
    confirmed_summary_version: int | None = None,
    service: str = "hostel",
    payment_method: str = "stripe",
) -> ModelProposal:
    if intent == "confirm":
        action = {
            "hostel": CriticalActionKind.RESERVE_LODGING,
            "agency": CriticalActionKind.BOOK_ACTIVITY,
            "package": CriticalActionKind.BOOK_PACKAGE,
        }[service]
        confirmed_action_kinds = tuple(
            sorted(
                (action, CriticalActionKind.INITIATE_PAYMENT),
                key=lambda item: item.value,
            )
        )
        approval_basis = ApprovalBasis.CONTEXTUAL_REFERENCE
    else:
        confirmed_action_kinds = ()
        approval_basis = None
    return ModelProposal(
        source_event_id=source,
        intent=intent,
        reply_chunks=("Mensagem pública do modelo.",),
        facts=(
            ()
            if intent == "confirm"
            else (
                ModelFact("language", "pt-BR"),
                ModelFact("service", service),
                ModelFact("start_date", date(2026, 8, 10)),
                ModelFact("end_date", date(2026, 8, 12)),
                ModelFact("adults", 2),
                ModelFact("children", 0),
                ModelFact("payment_method", payment_method),
            )
        ),
        target_offer_id=target_offer_id,
        confirmed_summary_version=confirmed_summary_version,
        confirmed_action_kinds=confirmed_action_kinds,
        approval_basis=approval_basis,
        read_requests=(),
        effect_proposals=(),
    )


def _lodging_read(*, amount: str = "480.00", adults: int = 2) -> ReadObservation:
    return ReadObservation(
        request_hash="1" * 64,
        provider="cloudbeds",
        observed_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
        public_payload={
            "offer_id": LODGING_OFFER_ID,
            "room_public_name": "Suíte Casal",
            "check_in": "2026-08-10",
            "check_out": "2026-08-12",
            "adults": adults,
            "children": 0,
            "total_amount": amount,
            "currency": "BRL",
            "available_units": 1,
        },
        private_binding_hash=LODGING_BINDING_HASH,
    )


def _activity_read(
    *, adults: int = 2, children: int = 0, start_time: str | None = None
) -> ReadObservation:
    public_payload = {
        "offer_id": ACTIVITY_OFFER_ID,
        "product_id": "product:buracao-001",
        "product_public_name": "Buracão",
        "activity_date": "2026-08-11",
        "adults": adults,
        "children": children,
        "participants": adults + children,
        "total_amount": "400.00",
        "currency": "BRL",
        "available": True,
    }
    if start_time is not None:
        public_payload["start_time"] = start_time
    return ReadObservation(
        request_hash="2" * 64,
        provider="bokun",
        observed_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
        public_payload=public_payload,
        private_binding_hash=ACTIVITY_BINDING_HASH,
    )


def test_named_activity_product_is_persisted_as_canonical_private_fact() -> None:
    proposal = ModelProposal(
        source_event_id="event:remember-buracao",
        intent="inform",
        reply_chunks=("Em qual data você quer conhecer o Buracão?",),
        facts=(
            ModelFact("language", "pt-BR"),
            ModelFact("service", "agency"),
            ModelFact("product_id", "product:buracao"),
        ),
        read_requests=(),
        effect_proposals=(),
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=proposal,
        profile=_profile(complete=False),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert {
        fact.name: fact.value.value for fact in decision.projection.facts
    }["product_id"] == "product:buracao"


def _ready_state(
    *,
    service: ServiceKind,
    workflow_id: str,
    adults: int = 2,
    birth_date: date | None = None,
    gender: str | None = None,
) -> ReadyToSummarizeState:
    query = SearchQuery(
        service=service,
        start_date=date(2026, 8, 10 if service is ServiceKind.LODGING else 11),
        end_date=date(2026, 8, 12) if service is ServiceKind.LODGING else None,
        start_time=None,
        party=Party(adults=adults, children=0),
    )
    binding = (
        LODGING_BINDING_HASH
        if service is ServiceKind.LODGING
        else ACTIVITY_BINDING_HASH
    )
    offer_id = LODGING_OFFER_ID if service is ServiceKind.LODGING else ACTIVITY_OFFER_ID
    if service is ServiceKind.LODGING:
        request_hash = ReadRequest(
            request_id="stable-query",
            kind=ReadKind.LODGING,
            check_in=query.start_date,
            check_out=query.end_date,
            adults=adults,
            children=0,
        ).query_hash()
    else:
        request_hash = ReadRequest(
            request_id="stable-query",
            kind=ReadKind.ACTIVITY,
            product_id="product:buracao-001",
            activity_date=query.start_date,
            adults=adults,
            children=0,
        ).query_hash()
    lookup_id = (
        f"lookup:{request_hash}"
        if service is ServiceKind.LODGING
        else f"lookup:product:buracao-001:{request_hash}"
    )
    offer = OfferSnapshot(
        offer_id=offer_id,
        lookup_id=lookup_id,
        service=service,
        provider_ref=binding,
        public_label="Suíte Casal" if service is ServiceKind.LODGING else "Buracão",
        start_date=query.start_date,
        end_date=query.end_date,
        start_time=None,
        party=query.party,
        total=Money(
            amount=Decimal("480.00" if service is ServiceKind.LODGING else "400.00"),
            currency="BRL",
        ),
        available=True,
    )
    evidence = LookupEvidence(
        lookup_id=lookup_id,
        service=service,
        query_signature=query.signature,
        observed_at=NOW - timedelta(seconds=15),
        expires_at=NOW + timedelta(minutes=5),
        snapshot_hash="9" * 64,
        status=LookupStatus.POSITIVE,
    )
    state = new_workflow(workflow_id=workflow_id, started_at=NOW - timedelta(minutes=1))
    state = reduce_domain(
        state,
        StartSearch(
            event_id=f"{workflow_id}:search",
            occurred_at=NOW - timedelta(seconds=20),
            query=query,
        ),
    ).state
    state = reduce_domain(
        state,
        LookupRecorded(
            event_id=f"{workflow_id}:lookup",
            occurred_at=NOW - timedelta(seconds=10),
            evidence=evidence,
            offers=(offer,),
        ),
    ).state
    state = reduce_domain(
        state,
        OfferChosen(
            event_id=f"{workflow_id}:choice",
            occurred_at=NOW - timedelta(seconds=7),
            offer_id=offer_id,
        ),
    ).state
    state = reduce_domain(
        state,
        DraftRequested(
            event_id=f"{workflow_id}:draft",
            occurred_at=NOW - timedelta(seconds=5),
            draft_id=f"draft:{workflow_id}",
            customer=CustomerFacts(
                customer_ref=_profile().binding_id,
                full_name=_profile().full_name,
                email=_profile().email,
                phone_e164=_profile().phone_e164,
                country_code=_profile().country_code,
                birth_date=birth_date,
                gender=gender,
            ),
            terms=EconomicTerms(payment_method="wise", add_ons=()),
        ),
    ).state
    assert type(state) is ReadyToSummarizeState
    return state


def _read_for_component(component: OfferSnapshot) -> ReadObservation:
    request_hash = component.lookup_id.rsplit(":", 1)[-1]
    if component.service is ServiceKind.LODGING:
        payload = {
            "offer_id": component.offer_id,
            "room_public_name": component.public_label,
            "check_in": component.start_date.isoformat(),
            "check_out": component.end_date.isoformat(),
            "adults": component.party.adults,
            "children": component.party.children,
            "total_amount": format(component.total.amount, ".2f"),
            "currency": component.total.currency,
            "available_units": 1,
        }
        provider = "cloudbeds"
    else:
        payload = {
            "offer_id": component.offer_id,
            "product_id": "product:buracao-001",
            "product_public_name": component.public_label,
            "activity_date": component.start_date.isoformat(),
            "adults": component.party.adults,
            "children": component.party.children,
            "participants": component.party.adults + component.party.children,
            "total_amount": format(component.total.amount, ".2f"),
            "currency": component.total.currency,
            "available": True,
        }
        provider = "bokun"
    return ReadObservation(
        request_hash=request_hash,
        provider=provider,
        observed_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
        public_payload=payload,
        private_binding_hash=component.provider_ref,
    )


def _awaiting_from_ready(state: ReadyToSummarizeState, *, version: int | None = None):
    if version is not None:
        state = replace(state, draft=replace(state.draft, version=version))
    context = critical_action_context(
        state.draft,
        summary_version=state.draft.version,
        presented_at=NOW,
        locale="pt-BR",
        approval_ttl=timedelta(minutes=30),
        agency_payment_percentage=20,
        hostel_payment_percentage=100,
        policy=_enabled_policy(),
    )
    digest = critical_proposal_digest(state.draft, context)
    summary_id = "summary:001"
    transition = reduce_domain(
        state,
        SummaryRecorded(
            event_id="event:summary",
            occurred_at=NOW,
            summary_event_id=summary_id,
            draft_version=state.draft.version,
            subject_signature=state.draft.subject_signature,
            outbox_message_id=critical_summary_outbox_id(summary_id, digest),
        ),
    )
    assert type(transition.state) is AwaitingConfirmationState
    return transition.state


def test_incomplete_profile_and_stale_confirmation_never_emit_command() -> None:
    reducer = _reducer()
    incomplete = reducer.reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=_proposal(
            source="event:select-incomplete",
            intent="select",
            target_offer_id=LODGING_OFFER_ID,
        ),
        profile=_profile(complete=False),
        reads=(_lodging_read(),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )
    assert incomplete.commands == ()
    assert incomplete.public_reply.kind == "profile_completion"

    awaiting = _awaiting_from_ready(
        _ready_state(service=ServiceKind.LODGING, workflow_id="workflow:stale"),
        version=2,
    )
    stale = reducer.reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=_proposal(
            source="event:stale-confirm",
            intent="confirm",
            confirmed_summary_version=1,
        ),
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    assert stale.commands == ()
    assert stale.next_state.workflow == awaiting
    assert stale.public_reply.kind == "stale_confirmation"


def test_conversation_country_completes_authenticated_manychat_contact() -> None:
    projection = replace(
        _projection(),
        facts=(TypedFact("country_code", StringSlot("US"), FRAME_HASH),),
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=projection,
        proposal=_proposal(
            source="event:select-with-conversation-country",
            intent="select",
            target_offer_id=LODGING_OFFER_ID,
        ),
        profile=_authenticated_manychat_contact_without_country(),
        reads=(_lodging_read(),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.commands == ()
    assert type(decision.next_state.workflow) is AwaitingConfirmationState
    assert decision.next_state.workflow.draft.customer.full_name == "Carlos Teste"
    assert (
        decision.next_state.workflow.draft.customer.email
        == "maya.cloudbeds.canary@example.com"
    )
    assert decision.next_state.workflow.draft.customer.phone_e164 == "+12025550123"
    assert decision.next_state.workflow.draft.customer.country_code == "US"


def test_conversation_country_does_not_complete_expired_manychat_contact() -> None:
    projection = replace(
        _projection(),
        facts=(TypedFact("country_code", StringSlot("US"), FRAME_HASH),),
    )
    expired = replace(
        _authenticated_manychat_contact_without_country(),
        observed_at=NOW - timedelta(minutes=20),
        expires_at=NOW - timedelta(seconds=1),
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=projection,
        proposal=_proposal(
            source="event:select-with-expired-manychat-contact",
            intent="select",
            target_offer_id=LODGING_OFFER_ID,
        ),
        profile=expired,
        reads=(_lodging_read(),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.commands == ()
    assert decision.next_state.workflow is None
    assert decision.public_reply.kind == "profile_completion"


def test_conversation_contact_facts_cannot_replace_authenticated_manychat_contact() -> None:
    projection = replace(
        _projection(),
        facts=(
            TypedFact("full_name", StringSlot("Conversation Name"), FRAME_HASH),
            TypedFact(
                "email",
                StringSlot("conversation@example.com"),
                FRAME_HASH,
            ),
            TypedFact("phone_e164", StringSlot("+12025550124"), FRAME_HASH),
            TypedFact("country_code", StringSlot("US"), FRAME_HASH),
        ),
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=projection,
        proposal=_proposal(
            source="event:select-with-conversation-contact",
            intent="select",
            target_offer_id=LODGING_OFFER_ID,
        ),
        profile=_profile(complete=False),
        reads=(_lodging_read(),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.commands == ()
    assert decision.next_state.workflow is None
    assert decision.public_reply.kind == "profile_completion"


@pytest.mark.parametrize("intent", ("inform", "adjust"))
def test_incomplete_profile_allows_non_identity_dependent_conversation(
    intent: str,
) -> None:
    proposal = ModelProposal(
        source_event_id=f"event:{intent}-incomplete-profile",
        intent=intent,
        reply_chunks=("Posso te ajudar com informações antes da reserva.",),
        facts=(ModelFact("language", "pt-BR"),),
        read_requests=(),
        effect_proposals=(),
    )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=proposal,
        profile=_profile(complete=False),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.commands == ()
    assert decision.handoff_request is None
    assert decision.public_reply.kind == "inform"
    assert decision.public_reply.chunks == proposal.reply_chunks
    assert decision.receipt_requirements == ()


def test_selection_builds_authoritative_summary_without_command() -> None:
    divergent = _proposal(
        source="event:select-divergent",
        intent="select",
        target_offer_id=LODGING_OFFER_ID,
    )
    divergent = replace(
        divergent,
        facts=tuple(
            replace(item, value=3) if item.name == "adults" else item
            for item in divergent.facts
        ),
    )
    with pytest.raises(ConversationReductionError, match="diverge"):
        _reducer().reduce(
            state=_boundary(),
            projection=_projection(),
            proposal=divergent,
            profile=_profile(),
            reads=(_lodging_read(),),
            fact_commitment_hash=FRAME_HASH,
            now=NOW,
        )

    decision = _reducer().reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=_proposal(
            source="event:select-lodging",
            intent="select",
            target_offer_id=LODGING_OFFER_ID,
        ),
        profile=_profile(),
        reads=(_lodging_read(),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.commands == ()
    assert type(decision.next_state.workflow) is AwaitingConfirmationState
    assert decision.public_reply.kind == "summary"
    assert decision.public_reply.chunks == (
        "Só para confirmar: vou reservar Suíte Casal de 10/08/2026 a "
        "12/08/2026 para 2 pessoas, pelo total final de R$ 480,00, e depois "
        "gerar o link do pagamento de R$ 480,00 no cartão. Posso fazer essa reserva?",
    )
    assert "BRL" not in decision.public_reply.chunks[0]
    assert "stripe" not in decision.public_reply.chunks[0]
    assert decision.projection.stage is ConversationStage.CLOSING
    assert (
        tuple(fact.name for fact in decision.projection.facts)[-1] == "payment_method"
    )
    assert (
        ConversationProjection.from_canonical_bytes(
            decision.projection.to_canonical_bytes()
        )
        == decision.projection
    )


def test_disabled_critical_capability_fails_closed_with_public_denial() -> None:
    reducer = V2ConversationReducer(
        critical_action_policy=CriticalActionPolicy(
            frozenset({CriticalActionKind.RESERVE_LODGING})
        )
    )

    decision = reducer.reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=_proposal(
            source="batch:capability-denied",
            intent="select",
            target_offer_id=LODGING_OFFER_ID,
        ),
        profile=_profile(),
        reads=(_lodging_read(),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert decision.next_state.workflow is None
    assert decision.commands == ()
    assert decision.public_reply.kind == "critical_action_unavailable"
    assert decision.receipt_requirements == ("critical_action_denied",)


def test_confirmed_summary_emits_domain_command_only() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(service=ServiceKind.LODGING, workflow_id="workflow:single")
    )
    missing = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=_proposal(
            source="event:confirm-lodging",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
        ),
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    assert missing.commands == ()
    assert missing.public_reply.kind == "fresh_reads_required"

    stale_reads = tuple(
        replace(item, expires_at=NOW + timedelta(milliseconds=500))
        for item in (
            _read_for_component(component) for component in awaiting.draft.components
        )
    )
    stale = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=_proposal(
            source="event:confirm-lodging",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
        ),
        profile=_profile(),
        reads=stale_reads,
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    assert stale.commands == ()
    assert stale.public_reply.kind == "fresh_reads_required"

    decision = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=_proposal(
            source="event:confirm-lodging",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
        ),
        profile=_profile(),
        reads=tuple(_read_for_component(item) for item in awaiting.draft.components),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )

    assert len(decision.commands) == 1
    assert type(decision.commands[0]) is ReservationCommand
    assert decision.commands[0].operation is ReservationOperation.RESERVE_LODGING
    assert type(decision.next_state.workflow) is ExecutionQueuedState


def test_post_command_guard_reply_is_localized() -> None:
    from v2_application.conversation import _reservation_already_processing_reply

    assert _reservation_already_processing_reply("pt-BR") == (
        "Já existe uma reserva em processamento ou concluída neste atendimento; "
        "não vou criar outra automaticamente."
    )
    assert _reservation_already_processing_reply("en-US") == (
        "A booking is already being processed or completed in this conversation; "
        "I won’t create another one automatically."
    )


def test_post_command_offer_overlap_detects_partial_package_duplicate() -> None:
    from v2_application.conversation import _post_command_offer_overlap

    assert _post_command_offer_overlap(("offer:a",), ("offer:a",)) is True
    assert _post_command_offer_overlap(("offer:a",), ("offer:a", "offer:b")) is True
    assert _post_command_offer_overlap(("offer:a", "offer:b"), ("offer:b",)) is True
    assert _post_command_offer_overlap(("offer:a",), ("offer:c",)) is False
    assert _post_command_offer_overlap(None, ("offer:a",)) is False


@pytest.mark.parametrize(
    ("certainty", "expected_state"),
    (
        (ExecutionCertainty.NOT_CALLED, FailedBeforeProviderState),
        (ExecutionCertainty.CALLED_NO_EFFECT, FailedNoEffectState),
    ),
)
def test_proven_no_effect_states_still_block_implicit_duplicate_command(
    certainty: ExecutionCertainty,
    expected_state: type,
) -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(service=ServiceKind.LODGING, workflow_id=f"workflow:{certainty.value}")
    )
    confirmed = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=_proposal(
            source=f"event:{certainty.value}:confirm",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
        ),
        profile=_profile(),
        reads=tuple(_read_for_component(item) for item in awaiting.draft.components),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    queued = confirmed.next_state.workflow
    assert type(queued) is ExecutionQueuedState
    executing = reduce_domain(
        queued,
        ExecutionStarted(
            event_id=f"event:{certainty.value}:started",
            occurred_at=NOW + timedelta(seconds=2),
            command_id=queued.command.command_id,
        ),
    ).state
    failed = reduce_domain(
        executing,
        ExecutionFinished(
            event_id=f"event:{certainty.value}:finished",
            occurred_at=NOW + timedelta(seconds=3),
            command_id=queued.command.command_id,
            outcome=ExecutionOutcome(
                command_id=queued.command.command_id,
                certainty=certainty,
                normalized_status=certainty.value,
            ),
        ),
    ).state
    assert type(failed) is expected_state

    guarded = _reducer().reduce(
        state=_boundary(failed),
        projection=confirmed.projection,
        proposal=_proposal(
            source=f"event:{certainty.value}:select-again",
            intent="select",
            target_offer_id=LODGING_OFFER_ID,
            payment_method="pix",
        ),
        profile=_profile(),
        reads=(_lodging_read(),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=4),
    )

    assert guarded.commands == ()
    assert guarded.next_state.workflow == failed
    assert guarded.public_reply.kind == "reservation_already_processing"


def test_execution_queued_allows_materially_different_offer_selection() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(service=ServiceKind.LODGING, workflow_id="workflow:queued-new-offer")
    )
    confirmed = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=_proposal(
            source="event:queued-first-confirm",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
        ),
        profile=_profile(),
        reads=tuple(_read_for_component(item) for item in awaiting.draft.components),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    assert type(confirmed.next_state.workflow) is ExecutionQueuedState

    different_offer_id = "offer:" + "9" * 32
    different_read = replace(
        _lodging_read(amount="520.00"),
        request_hash="8" * 64,
        private_binding_hash="7" * 64,
        public_payload={
            **_lodging_read(amount="520.00").public_payload,
            "offer_id": different_offer_id,
            "room_public_name": "Outra Suíte",
        },
    )
    selected = _reducer().reduce(
        state=confirmed.next_state,
        projection=confirmed.projection,
        proposal=_proposal(
            source="event:queued-different-offer",
            intent="select",
            target_offer_id=different_offer_id,
        ),
        profile=_profile(),
        reads=(different_read,),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=2),
    )

    assert selected.commands == ()
    assert type(selected.next_state.workflow) is AwaitingConfirmationState
    assert (
        selected.next_state.workflow.draft.subject_signature
        != confirmed.next_state.workflow.command.subject_signature
    )
    assert selected.public_reply.kind == "summary"


def test_execution_queued_state_rejects_new_select_and_confirm_without_reset() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(service=ServiceKind.LODGING, workflow_id="workflow:queued-guard")
    )
    confirmed = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=_proposal(
            source="event:queued-original-confirm",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
        ),
        profile=_profile(),
        reads=tuple(_read_for_component(item) for item in awaiting.draft.components),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    queued = confirmed.next_state.workflow
    assert type(queued) is ExecutionQueuedState
    queued_projection = replace(
        confirmed.projection,
        stage=ConversationStage.HOSTEL,
    )

    selected_again = _reducer().reduce(
        state=confirmed.next_state,
        projection=queued_projection,
        proposal=_proposal(
            source="event:queued-select-again",
            intent="select",
            target_offer_id=LODGING_OFFER_ID,
        ),
        profile=_profile(),
        reads=(_lodging_read(),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=2),
    )
    confirmed_again = _reducer().reduce(
        state=selected_again.next_state,
        projection=selected_again.projection,
        proposal=_proposal(
            source="event:queued-confirm-again",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
        ),
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=3),
    )

    for guarded in (selected_again, confirmed_again):
        assert guarded.commands == ()
        assert guarded.next_state.workflow == queued
        assert guarded.projection == queued_projection
        assert guarded.public_reply.kind == "reservation_already_processing"
        assert guarded.receipt_requirements == ("reservation_already_processing",)


@pytest.mark.parametrize(
    ("service", "mutator", "expected_kind", "expected_text"),
    (
        (
            ServiceKind.ACTIVITY,
            lambda read: replace(
                read,
                public_payload={**read.public_payload, "available": False},
            ),
            "offer_unavailable",
            "A vaga não está mais disponível",
        ),
        (
            ServiceKind.LODGING,
            lambda read: replace(
                read,
                public_payload={**read.public_payload, "total_amount": "510.00"},
                private_binding_hash="1" * 64,
            ),
            "proposal_changed",
            "A disponibilidade ou o valor mudou",
        ),
    ),
)
def test_confirmation_refresh_mismatch_revokes_pending_summary_without_command(
    service: ServiceKind,
    mutator,
    expected_kind: str,
    expected_text: str,
) -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(
            service=service,
            workflow_id=f"workflow:refresh-{service.value}",
            adults=1 if service is ServiceKind.ACTIVITY else 2,
            birth_date=(
                date(1990, 1, 2) if service is ServiceKind.ACTIVITY else None
            ),
            gender="f" if service is ServiceKind.ACTIVITY else None,
        )
    )
    projection = _projection()
    proposal_service = "hostel"
    if service is ServiceKind.ACTIVITY:
        projection = replace(
            projection,
            desired_services=(DesiredService.AGENCY,),
            facts=(
                TypedFact(
                    "product_id",
                    StringSlot("product:buracao-001"),
                    FRAME_HASH,
                ),
                TypedFact("birth_date", DateSlot(date(1990, 1, 2)), FRAME_HASH),
                TypedFact("gender", StringSlot("f"), FRAME_HASH),
            ),
        )
        proposal_service = "agency"
    fresh_read = _read_for_component(awaiting.draft.components[0])

    decision = _reducer().reduce(
        state=_boundary(awaiting),
        projection=projection,
        proposal=_proposal(
            source=f"event:refresh-{service.value}",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
            service=proposal_service,
        ),
        profile=_profile(),
        reads=(mutator(fresh_read),),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )

    assert decision.commands == ()
    assert type(decision.next_state.workflow) is AwaitingAdjustmentState
    assert _reducer().pending_action(
        decision.next_state.workflow,
        locale="pt-BR",
    ) is None
    assert decision.public_reply.kind == expected_kind
    assert expected_text in decision.public_reply.chunks[0]
    assert "Nada foi reservado" in decision.public_reply.chunks[0]
    assert decision.receipt_requirements == ("proposal_revoked_after_refresh",)
    assert {
        fact.name: fact.value.value for fact in decision.projection.facts
    }["critical_outcome"] == "proposal_revoked_after_refresh"

    if service is ServiceKind.LODGING:
        reselection = _reducer().reduce(
            state=decision.next_state,
            projection=decision.projection,
            proposal=_proposal(
                source="event:selection-after-refresh-revocation",
                intent="select",
                target_offer_id=LODGING_OFFER_ID,
            ),
            profile=_profile(),
            reads=(_lodging_read(),),
            fact_commitment_hash="2" * 64,
            now=NOW + timedelta(seconds=2),
        )
        assert "critical_outcome" not in {
            fact.name for fact in reselection.projection.facts
        }


def test_confirmation_scope_mismatch_and_expiry_fail_closed_without_command() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(service=ServiceKind.LODGING, workflow_id="workflow:approval-guard")
    )
    fresh_reads = tuple(
        _read_for_component(item) for item in awaiting.draft.components
    )
    correct = _proposal(
        source="event:approval-guard",
        intent="confirm",
        confirmed_summary_version=awaiting.draft.version,
    )
    wrong_scope = replace(
        correct,
        confirmed_action_kinds=(
            CriticalActionKind.CANCEL_RESERVATION,
            CriticalActionKind.INITIATE_PAYMENT,
        ),
    )

    rejected = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=wrong_scope,
        profile=_profile(),
        reads=fresh_reads,
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    assert rejected.commands == ()
    assert rejected.public_reply.kind == "stale_confirmation"

    expired = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=correct,
        profile=replace(_profile(), expires_at=NOW + timedelta(hours=1)),
        reads=fresh_reads,
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(minutes=30),
    )
    assert expired.commands == ()
    assert expired.public_reply.kind == "approval_expired"
    assert type(expired.next_state.workflow) is AwaitingAdjustmentState
    assert _reducer().pending_action(
        expired.next_state.workflow,
        locale="pt-BR",
    ) is None
    assert {
        fact.name: fact.value.value for fact in expired.projection.facts
    }["critical_outcome"] == "proposal_expired"


def test_adjustment_or_refusal_revokes_the_pending_proposal_version() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(service=ServiceKind.LODGING, workflow_id="workflow:adjust-revoke")
    )
    adjustment = ModelProposal(
        source_event_id="event:adjust-revoke",
        intent="adjust",
        reply_chunks=("Tudo bem, não vou reservar essa opção.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )

    revoked = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=adjustment,
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )

    assert revoked.commands == ()
    assert type(revoked.next_state.workflow) is AwaitingAdjustmentState
    assert revoked.public_reply.kind == "adjust"
    assert _reducer().pending_action(
        revoked.next_state.workflow,
        locale="pt-BR",
    ) is None

    preserved = _reducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=replace(
            adjustment,
            source_event_id="event:adjust-preserve",
            reply_chunks=("Vou manter o resumo para você conferir novamente.",),
            pending_disposition="preserve",
        ),
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    assert preserved.next_state.workflow == awaiting
    assert preserved.public_reply.kind == "inform"
    assert _reducer().pending_action(
        preserved.next_state.workflow,
        locale="pt-BR",
    ) is not None

    late_confirmation = _reducer().reduce(
        state=revoked.next_state,
        projection=revoked.projection,
        proposal=_proposal(
            source="event:late-old-confirmation",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
        ),
        profile=_profile(),
        reads=tuple(_read_for_component(item) for item in awaiting.draft.components),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=2),
    )
    assert late_confirmation.commands == ()


def test_material_projection_change_supersedes_old_confirmation_without_command() -> None:
    reducer = _reducer()
    awaiting = _awaiting_from_ready(
        _ready_state(service=ServiceKind.LODGING, workflow_id="workflow:material-change")
    )
    changed = reducer.reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=ModelProposal(
            source_event_id="event:material-change-inform",
            intent="inform",
            reply_chunks=("Entendi, agora são três pessoas.",),
            facts=(ModelFact("adults", 3),),
            read_requests=(),
            effect_proposals=(),
        ),
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    assert type(changed.next_state.workflow) is AwaitingConfirmationState
    assert (
        reducer.confirmation_projection_matches(
            changed.next_state.workflow,
            changed.projection,
        )
        is False
    )

    stale_confirmation = reducer.reduce(
        state=changed.next_state,
        projection=changed.projection,
        proposal=_proposal(
            source="event:old-confirmation-after-change",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
        ),
        profile=_profile(),
        reads=tuple(
            _read_for_component(component) for component in awaiting.draft.components
        ),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=2),
    )

    assert type(stale_confirmation.next_state.workflow) is AwaitingAdjustmentState
    assert stale_confirmation.commands == ()
    assert stale_confirmation.public_reply.kind == "proposal_changed"
    assert stale_confirmation.receipt_requirements == ("proposal_superseded",)


def test_informational_detour_preserves_pending_proposal_for_later_short_confirmation() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(
            service=ServiceKind.LODGING,
            workflow_id="workflow:short-confirm-after-inform",
        )
    )
    reducer = _reducer()
    informed = reducer.reduce(
        state=_boundary(awaiting),
        projection=_projection(),
        proposal=ModelProposal(
            source_event_id="event:inform-before-short-confirm",
            intent="inform",
            reply_chunks=("O sinal é o valor informado no resumo.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
        ),
        profile=_profile(),
        reads=(),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )
    assert informed.next_state.workflow == awaiting
    assert informed.commands == ()

    confirmed = reducer.reduce(
        state=informed.next_state,
        projection=informed.projection,
        proposal=ModelProposal(
            source_event_id="event:short-confirm-after-inform",
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=awaiting.draft.version,
            confirmed_action_kinds=(
                CriticalActionKind.INITIATE_PAYMENT,
                CriticalActionKind.RESERVE_LODGING,
            ),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        ),
        profile=_profile(),
        reads=tuple(
            _read_for_component(item) for item in awaiting.draft.components
        ),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=2),
    )
    assert len(confirmed.commands) == 1


def test_runtime_package_selection_builds_one_bound_summary_then_two_child_commands() -> None:
    proposal = ModelProposal(
        source_event_id="event:select-package-runtime",
        intent="select",
        reply_chunks=("Encontrei hospedagem e passeio.",),
        facts=(
            ModelFact("language", "pt-BR"),
            ModelFact("service", "package"),
            ModelFact("start_date", date(2026, 8, 10)),
            ModelFact("end_date", date(2026, 8, 12)),
            ModelFact("activity_date", date(2026, 8, 11)),
            ModelFact("adults", 1),
            ModelFact("children", 0),
            ModelFact("payment_method", "stripe"),
            ModelFact("birth_date", date(1990, 1, 2)),
            ModelFact("gender", "m"),
        ),
        target_offer_ids=(LODGING_OFFER_ID, ACTIVITY_OFFER_ID),
        read_requests=(),
        effect_proposals=(),
    )
    selected = _reducer().reduce(
        state=_boundary(),
        projection=_projection(package=True),
        proposal=proposal,
        profile=_profile(),
        reads=(_lodging_read(adults=1), _activity_read(adults=1)),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )

    assert selected.commands == ()
    assert type(selected.next_state.workflow) is AwaitingConfirmationState
    assert len(selected.next_state.workflow.draft.components) == 2
    assert selected.public_reply.kind == "summary"
    public_text = " ".join(selected.public_reply.chunks)
    assert "Suíte Casal" in public_text
    assert "Buracão" in public_text
    assert "product:" not in public_text
    assert LODGING_OFFER_ID not in public_text
    assert ACTIVITY_OFFER_ID not in public_text

    awaiting = selected.next_state.workflow
    confirmed = _reducer().reduce(
        state=selected.next_state,
        projection=selected.projection,
        proposal=ModelProposal(
            source_event_id="event:confirm-package-runtime",
            intent="confirm",
            reply_chunks=("Sim, pode reservar.",),
            facts=(),
            confirmed_summary_version=awaiting.draft.version,
            confirmed_action_kinds=(
                CriticalActionKind.BOOK_PACKAGE,
                CriticalActionKind.INITIATE_PAYMENT,
            ),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
            read_requests=(),
            effect_proposals=(),
        ),
        profile=_profile(),
        reads=(_lodging_read(adults=1), _activity_read(adults=1)),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )

    assert len(confirmed.commands) == 1
    assert confirmed.commands[0].operation is ReservationOperation.RESERVE_PACKAGE
    children = ReservationAllocator().allocate(confirmed.commands[0]).commands
    assert tuple(item.operation for item in children) == (
        ReservationOperation.RESERVE_LODGING,
        ReservationOperation.BOOK_ACTIVITY,
    )
    assert len({item.idempotency_key for item in children}) == 2


def test_package_has_one_summary_one_confirmation_and_two_allocated_components() -> (
    None
):
    package_ready = PackageCommandCoordinator().combine(
        workflow_id="workflow:package",
        draft_id="draft:package",
        lodging=_ready_state(
            service=ServiceKind.LODGING,
            workflow_id="workflow:package-lodging",
            adults=1,
            birth_date=date(1990, 1, 2),
            gender="f",
        ),
        activity=_ready_state(
            service=ServiceKind.ACTIVITY,
            workflow_id="workflow:package-activity",
            adults=1,
            birth_date=date(1990, 1, 2),
            gender="f",
        ),
        now=NOW,
    )
    awaiting = _awaiting_from_ready(package_ready)
    projection = replace(
        _projection(package=True),
        facts=(
            TypedFact("birth_date", DateSlot(date(1990, 1, 2)), FRAME_HASH),
            TypedFact("gender", StringSlot("f"), FRAME_HASH),
        ),
    )

    decision = _reducer().reduce(
        state=_boundary(awaiting),
        projection=projection,
        proposal=_proposal(
            source="event:confirm-package",
            intent="confirm",
            confirmed_summary_version=awaiting.draft.version,
            service="package",
            payment_method="wise",
        ),
        profile=_profile(),
        reads=tuple(_read_for_component(item) for item in awaiting.draft.components),
        fact_commitment_hash=FRAME_HASH,
        now=NOW + timedelta(seconds=1),
    )

    assert len(decision.commands) == 1
    assert decision.commands[0].operation is ReservationOperation.RESERVE_PACKAGE
    allocated = ReservationAllocator().allocate(decision.commands[0])
    assert tuple(command.operation for command in allocated.commands) == (
        ReservationOperation.RESERVE_LODGING,
        ReservationOperation.BOOK_ACTIVITY,
    )


class _NoopReservationPort:
    provider = "cloudbeds"

    def execute(self, permit):  # pragma: no cover - preparation test never dispatches
        raise AssertionError("provider must not be called during preparation")


class _Clock:
    def now(self) -> datetime:
        return NOW


def test_private_offer_resolution_rechecks_all_bindings_during_prepare() -> None:
    provider_state = {"amount": "480.00"}

    def transport(operation, payload):
        assert operation == "lodging"
        return {
            "options": [
                {
                    "room_public_name": "Suíte Casal",
                    **payload,
                    "total_amount": provider_state["amount"],
                    "currency": "BRL",
                    "available_units": 1,
                    "room_type_id": "room-private-001",
                    "room_rate_id": "rate-private-001",
                },
                {
                    "room_public_name": "Suíte Família",
                    **payload,
                    "total_amount": "720.00",
                    "currency": "BRL",
                    "available_units": 1,
                    "room_type_id": "room-private-002",
                    "room_rate_id": "rate-private-002",
                },
            ]
        }

    cloudbeds = CloudbedsReadAdapter(
        transport=transport,
        clock=_Clock(),
        ttl=timedelta(minutes=5),
    )
    observation = cloudbeds.read(
        ReadRequest(
            request_id="read:binding-lodging",
            kind=ReadKind.LODGING,
            check_in=date(2026, 8, 10),
            check_out=date(2026, 8, 12),
            adults=2,
            children=0,
        )
    )
    offer_id = observation.public_payload["options"][0]["offer_id"]
    selected = _reducer().reduce(
        state=_boundary(),
        projection=_projection(),
        proposal=_proposal(
            source="event:select-binding",
            intent="select",
            target_offer_id=offer_id,
        ),
        profile=_profile(),
        reads=(observation,),
        fact_commitment_hash=FRAME_HASH,
        now=NOW,
    )
    command = (
        _reducer()
        .reduce(
            state=selected.next_state,
            projection=_projection(),
            proposal=_proposal(
                source="event:confirm-binding",
                intent="confirm",
                confirmed_summary_version=selected.next_state.workflow.draft.version,
            ),
            profile=_profile(),
            reads=(observation,),
            fact_commitment_hash=FRAME_HASH,
            now=NOW + timedelta(seconds=1),
        )
        .commands[0]
    )

    unresolved = V2ReservationExecutionAdapter(
        provider="cloudbeds",
        port=_NoopReservationPort(),
        authorization=ProviderWriteAuthorization(
            provider="cloudbeds",
            enabled=True,
            authorization_id="authorization:cloudbeds-unresolved-test",
        ),
    )
    with pytest.raises(PreparationFailure) as unresolved_error:
        unresolved.prepare(command)
    assert unresolved_error.value.reason == "private_binding_resolver_unavailable"

    resolver = PrivateOfferBindingResolver({ServiceKind.LODGING: cloudbeds})
    adapter = V2ReservationExecutionAdapter(
        provider="cloudbeds",
        port=_NoopReservationPort(),
        authorization=ProviderWriteAuthorization(
            provider="cloudbeds",
            enabled=True,
            authorization_id="authorization:cloudbeds-test",
        ),
        binding_resolver=resolver,
        clock=_Clock(),
    )

    request = adapter.prepare(command)
    assert request.canonical_payload == dumps_command(command)
    assert adapter._prepared_private_bindings[command.command_id]["room_type_id"] == (
        "room-private-001"
    )

    provider_state["amount"] = "481.00"
    changed_adapter = V2ReservationExecutionAdapter(
        provider="cloudbeds",
        port=_NoopReservationPort(),
        authorization=ProviderWriteAuthorization(
            provider="cloudbeds",
            enabled=True,
            authorization_id="authorization:cloudbeds-test",
        ),
        binding_resolver=resolver,
        clock=_Clock(),
    )
    with pytest.raises(PreparationFailure) as changed_error:
        changed_adapter.prepare(command)
    assert changed_error.value.reason == "private_binding_mismatch"
