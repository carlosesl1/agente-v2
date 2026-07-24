from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from reservation_boundary import (
    BoundaryState,
    ConversationProjection,
    ConversationStage,
    DesiredService,
)
from reservation_domain import (
    AwaitingConfirmationState,
    CustomerFacts,
    DraftRequested,
    EconomicTerms,
    ExecutionQueuedState,
    LookupEvidence,
    LookupRecorded,
    LookupStatus,
    Money,
    OfferChosen,
    OfferSnapshot,
    Party,
    ReadyToSummarizeState,
    ReservationCommand,
    ReservationOperation,
    SearchQuery,
    ServiceKind,
    StartSearch,
    SummaryRecorded,
    new_workflow,
)
from reservation_domain import (
    reduce as reduce_domain,
)
from reservation_execution import PreparationFailure
from v2_adapters.cloudbeds import CloudbedsReadAdapter
from v2_application.conversation import (
    ConversationReductionError,
    PackageCommandCoordinator,
    V2ConversationReducer,
)
from v2_application.reads import (
    PrivateOfferBindingResolver,
)
from v2_application.reservations import (
    ReservationAllocator,
    V2ReservationExecutionAdapter,
)
from v2_contracts.model import ModelFact, ModelProposal
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
    return ModelProposal(
        source_event_id=source,
        intent=intent,
        reply_chunks=("Mensagem pública do modelo.",),
        facts=(
            ModelFact("language", "pt-BR"),
            ModelFact("service", service),
            ModelFact("start_date", date(2026, 8, 10)),
            ModelFact("end_date", date(2026, 8, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", payment_method),
        ),
        target_offer_id=target_offer_id,
        confirmed_summary_version=confirmed_summary_version,
        read_requests=(),
        effect_proposals=(),
    )


def _lodging_read(*, amount: str = "480.00") -> ReadObservation:
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
            "adults": 2,
            "children": 0,
            "total_amount": amount,
            "currency": "BRL",
            "available_units": 1,
        },
        private_binding_hash=LODGING_BINDING_HASH,
    )


def _activity_read() -> ReadObservation:
    return ReadObservation(
        request_hash="2" * 64,
        provider="bokun",
        observed_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
        public_payload={
            "offer_id": ACTIVITY_OFFER_ID,
            "product_id": "product:buracao-001",
            "product_public_name": "Buracão",
            "activity_date": "2026-08-11",
            "participants": 2,
            "total_amount": "400.00",
            "currency": "BRL",
            "available": True,
        },
        private_binding_hash=ACTIVITY_BINDING_HASH,
    )


def _ready_state(*, service: ServiceKind, workflow_id: str) -> ReadyToSummarizeState:
    query = SearchQuery(
        service=service,
        start_date=date(2026, 8, 10 if service is ServiceKind.LODGING else 11),
        end_date=date(2026, 8, 12) if service is ServiceKind.LODGING else None,
        start_time=None,
        party=Party(adults=2, children=0),
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
            adults=2,
            children=0,
        ).query_hash()
    else:
        request_hash = ReadRequest(
            request_id="stable-query",
            kind=ReadKind.ACTIVITY,
            product_id="product:buracao-001",
            activity_date=query.start_date,
            participants=2,
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
            "participants": component.party.adults,
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
    transition = reduce_domain(
        state,
        SummaryRecorded(
            event_id="event:summary",
            occurred_at=NOW,
            summary_event_id="summary:001",
            draft_version=state.draft.version,
            subject_signature=state.draft.subject_signature,
            outbox_message_id="outbox:summary:001",
        ),
    )
    assert type(transition.state) is AwaitingConfirmationState
    return transition.state


def test_incomplete_profile_and_stale_confirmation_never_emit_command() -> None:
    reducer = V2ConversationReducer()
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
        V2ConversationReducer().reduce(
            state=_boundary(),
            projection=_projection(),
            proposal=divergent,
            profile=_profile(),
            reads=(_lodging_read(),),
            fact_commitment_hash=FRAME_HASH,
            now=NOW,
        )

    decision = V2ConversationReducer().reduce(
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


def test_confirmed_summary_emits_domain_command_only() -> None:
    awaiting = _awaiting_from_ready(
        _ready_state(service=ServiceKind.LODGING, workflow_id="workflow:single")
    )
    missing = V2ConversationReducer().reduce(
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
    stale = V2ConversationReducer().reduce(
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

    decision = V2ConversationReducer().reduce(
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
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", "stripe"),
        ),
        target_offer_ids=(LODGING_OFFER_ID, ACTIVITY_OFFER_ID),
        read_requests=(),
        effect_proposals=(),
    )
    selected = V2ConversationReducer().reduce(
        state=_boundary(),
        projection=_projection(package=True),
        proposal=proposal,
        profile=_profile(),
        reads=(_lodging_read(), _activity_read()),
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
    confirmed = V2ConversationReducer().reduce(
        state=selected.next_state,
        projection=selected.projection,
        proposal=ModelProposal(
            source_event_id="event:confirm-package-runtime",
            intent="confirm",
            reply_chunks=("Sim, pode reservar.",),
            facts=(
                ModelFact("language", "pt-BR"),
                ModelFact("service", "package"),
                ModelFact("start_date", date(2026, 8, 10)),
                ModelFact("end_date", date(2026, 8, 12)),
                ModelFact("activity_date", date(2026, 8, 11)),
                ModelFact("adults", 2),
                ModelFact("children", 0),
                ModelFact("payment_method", "stripe"),
            ),
            confirmed_summary_version=awaiting.draft.version,
            read_requests=(),
            effect_proposals=(),
        ),
        profile=_profile(),
        reads=(_lodging_read(), _activity_read()),
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
            service=ServiceKind.LODGING, workflow_id="workflow:package-lodging"
        ),
        activity=_ready_state(
            service=ServiceKind.ACTIVITY, workflow_id="workflow:package-activity"
        ),
        now=NOW,
    )
    awaiting = _awaiting_from_ready(package_ready)

    decision = V2ConversationReducer().reduce(
        state=_boundary(awaiting),
        projection=_projection(package=True),
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
    selected = V2ConversationReducer().reduce(
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
        V2ConversationReducer()
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
    prepared = json.loads(request.canonical_payload)
    assert prepared["schema"] == "v2-reservation-prepared-v2"
    assert prepared["private_binding"]["room_type_id"] == "room-private-001"

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
