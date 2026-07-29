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
    AwaitingAdjustmentState,
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
            ModelFact("adults", 2),
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

    decision = _reducer().reduce(
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
