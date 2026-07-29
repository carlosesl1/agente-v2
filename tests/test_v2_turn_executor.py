from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from reservation_boundary import ConversationStage, StringSlot, TypedFact
from reservation_boundary.conversation import ConversationProjection
from reservation_boundary.effects import HandoffRelayBundle, ReservationRelayBundle
from reservation_boundary.sqlite_store import ConcurrencyConflict, SQLiteBoundaryStore
from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_application.conversation import V2ConversationReducer
from v2_application.critical_actions import CriticalActionPolicy
from v2_application.public_delivery import (
    BoundaryPublicDeliveryWorker,
    BoundaryPublicDisposition,
)
from v2_application.reads import V2ReadService
from v2_application.relay_worker import BoundaryRelayWorker, RelayWorkerDisposition
from v2_application.turn_executor import (
    _critical_confirmation_bound,
    _critical_outcome,
    _critical_model_reads_allowed,
    PublicTurnAuthority,
    TurnExecutionError,
    V2TurnExecutor,
    _confirmation_read_requests,
    _explicit_customer_fact_commitment,
    _extract_explicit_commercial_facts,
    _extract_explicit_customer_facts,
    _explicit_summary_preparation_requested,
    _force_structured_activity_summary_preparation,
    _merge_explicit_customer_facts,
    _repair_requested_activity_selection,
    _structured_selection_review_required,
    _state_model_facts,
)
from v2_contracts.channel import InboundBatch, InboundEvent, PublicDeliveryUnknown
from v2_contracts.critical_actions import (
    ApprovalBasis,
    CriticalActionKind,
    PendingCriticalActionContext,
)
from v2_contracts.model import (
    AuditedModelTurn,
    AuditedTranscriptFrame,
    ModelFact,
    ModelProposal,
    ModelRequest,
)
from v2_contracts.profile import PrivateCustomerBinding
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

NOW = datetime(2026, 7, 23, 22, 0, tzinfo=timezone.utc)
TRANSCRIPT_KEY = b"t" * 32
CAPABILITY_DIGEST = "a" * 64
EFFECT_DIGEST = "b" * 64
TARGET_DIGEST = "c" * 64


def test_critical_outcome_is_separate_from_material_state_facts() -> None:
    projection = ConversationProjection(
        stage=ConversationStage.RECEPTIONIST,
        desired_services=(),
        locale="pt-BR",
        facts=(
            TypedFact(
                "critical_outcome",
                StringSlot("proposal_revoked_after_refresh"),
                "d" * 64,
            ),
        ),
        reservation_execution_projection=None,
    )

    assert _critical_outcome(projection) == "proposal_revoked_after_refresh"
    assert _state_model_facts(projection) == ()


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        (
            "I was born on 17 May 1991 and I am female.",
            (ModelFact("birth_date", date(1991, 5, 17)), ModelFact("gender", "f")),
        ),
        (
            "Nasci em 14/04/1990 e sou mulher.",
            (ModelFact("birth_date", date(1990, 4, 14)), ModelFact("gender", "f")),
        ),
        (
            "Minha data de nascimento é 11/01/1988. Sou homem.",
            (ModelFact("birth_date", date(1988, 1, 11)), ModelFact("gender", "m")),
        ),
    ),
)
def test_parent_extracts_only_explicit_customer_facts(
    message: str, expected: tuple[ModelFact, ...]
) -> None:
    assert _extract_explicit_customer_facts(message) == expected


def test_parent_customer_fact_extraction_ignores_unbound_dates_and_gender_words() -> None:
    assert _extract_explicit_customer_facts(
        "The tour is on 17 May 2026 and the guide may be female."
    ) == ()


def test_parent_extracts_unambiguous_catalog_product_date_and_party() -> None:
    assert _extract_explicit_commercial_facts(
        "Hi! I am considering the 4Ps Tour on November 18, 2026, for one adult."
    ) == (
        ModelFact("language", "en"),
        ModelFact("service", "agency"),
        ModelFact("product_id", "product:tour-4ps"),
        ModelFact("activity_date", date(2026, 11, 18)),
        ModelFact("adults", 1),
        ModelFact("children", 0),
    )


def test_parent_commercial_extraction_ignores_date_without_product() -> None:
    assert _extract_explicit_commercial_facts(
        "I will be free on November 18, 2026, but have not chosen a tour."
    ) == ()


def test_parent_extracts_only_explicit_payment_choice() -> None:
    assert _extract_explicit_commercial_facts(
        "I choose card. Please prepare the final booking summary."
    ) == (ModelFact("payment_method", "stripe"),)
    assert _extract_explicit_commercial_facts(
        "Pensando melhor, vou usar cartão com sinal de 20%."
    ) == (ModelFact("payment_method", "stripe"),)
    assert _extract_explicit_commercial_facts("Can I use card?") == ()
    assert _extract_explicit_commercial_facts("Maybe Pix would be better.") == ()


def test_selection_review_gate_uses_only_complete_structured_facts() -> None:
    state_facts = (
        ModelFact("service", "agency"),
        ModelFact("product_id", "product:tour-4ps"),
        ModelFact("activity_date", date(2026, 11, 18)),
        ModelFact("adults", 1),
        ModelFact("children", 0),
        ModelFact("birth_date", date(1991, 5, 17)),
        ModelFact("gender", "f"),
    )
    payment = (ModelFact("payment_method", "stripe"),)
    assert _structured_selection_review_required(
        state_facts,
        payment,
        private_profile_complete=True,
    )
    assert not _structured_selection_review_required(
        state_facts,
        (),
        private_profile_complete=True,
    )
    assert not _structured_selection_review_required(
        state_facts[:-1],
        payment,
        private_profile_complete=True,
    )


def test_explicit_summary_preparation_is_strict_and_non_authorizing() -> None:
    assert _explicit_summary_preparation_requested(
        "I choose card. Please prepare the final booking summary before executing anything."
    )
    assert _explicit_summary_preparation_requested(
        "Pode preparar o resumo final, mas ainda não execute."
    )
    assert not _explicit_summary_preparation_requested(
        "Do not prepare the final booking summary yet."
    )
    assert not _explicit_summary_preparation_requested(
        "Can you explain what a booking summary is?"
    )


def test_parent_forces_only_a_fresh_read_for_explicit_summary_preparation() -> None:
    proposal = ModelProposal(
        source_event_id="batch:force-summary",
        intent="inform",
        reply_chunks=("I can continue helping.",),
        facts=(ModelFact("payment_method", "stripe"),),
        read_requests=(),
        effect_proposals=(),
    )
    state_facts = (
        ModelFact("service", "agency"),
        ModelFact("product_id", "product:tour-4ps"),
        ModelFact("activity_date", date(2026, 11, 18)),
        ModelFact("adults", 1),
        ModelFact("children", 0),
        ModelFact("birth_date", date(1991, 5, 17)),
        ModelFact("gender", "f"),
    )
    forced = _force_structured_activity_summary_preparation(
        proposal,
        state_facts=state_facts,
        explicit_facts=(ModelFact("payment_method", "stripe"),),
    )
    assert forced.intent == "inform"
    assert forced.selection_requested is True
    assert forced.effect_proposals == ()
    assert len(forced.read_requests) == 1
    request = forced.read_requests[0]
    assert request.kind is ReadKind.ACTIVITY
    assert request.product_id == "product:tour-4ps"
    assert request.activity_date == date(2026, 11, 18)
    assert request.participants == 1


def test_parent_customer_fact_merge_rejects_model_conflict_and_commits_source() -> None:
    extracted = _extract_explicit_customer_facts(
        "I was born on 17 May 1991 and I am female."
    )
    proposal = ModelProposal(
        source_event_id="batch:explicit-customer-facts",
        intent="inform",
        reply_chunks=("Got it.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    merged = _merge_explicit_customer_facts(proposal, extracted)
    assert merged.facts == extracted
    commitment = _explicit_customer_fact_commitment(
        "d" * 64,
        "e" * 64,
        extracted,
    )
    assert len(commitment) == 64
    assert commitment != "d" * 64

    with pytest.raises(TurnExecutionError, match="conflicts with explicit customer fact"):
        _merge_explicit_customer_facts(
            replace(proposal, facts=(ModelFact("gender", "m"),)),
            extracted,
        )


def test_parent_repairs_only_structured_requested_activity_selection() -> None:
    request = ReadRequest(
        request_id="batch:structured-selection:read:activity",
        kind=ReadKind.ACTIVITY,
        product_id="product:tour-4ps",
        activity_date=date(2026, 11, 18),
        participants=1,
    )
    state_facts = (
        ModelFact("language", "pt-BR"),
        ModelFact("service", "agency"),
        ModelFact("product_id", "product:tour-4ps"),
        ModelFact("activity_date", date(2026, 11, 18)),
        ModelFact("adults", 1),
        ModelFact("children", 0),
        ModelFact("payment_method", "stripe"),
        ModelFact("birth_date", date(1990, 4, 14)),
        ModelFact("gender", "f"),
    )
    first = ModelProposal(
        source_event_id="batch:structured-selection",
        intent="inform",
        reply_chunks=("Vou verificar a oferta atual.",),
        facts=(),
        read_requests=(request,),
        effect_proposals=(),
        selection_requested=True,
    )
    second = ModelProposal(
        source_event_id=first.source_event_id,
        intent="inform",
        reply_chunks=("Vou preparar o resumo.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    observation = ReadObservation(
        request_hash=request.canonical_hash(),
        provider="bokun",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        public_payload={
            "offer_id": "offer:" + "a" * 64,
            "product_id": "product:tour-4ps",
            "activity_date": "2026-11-18",
            "participants": 1,
            "available": True,
            "price_includes_booking_fee": True,
            "total_amount": "334.95",
            "currency": "BRL",
        },
        private_binding_hash="f" * 64,
    )

    repaired = _repair_requested_activity_selection(
        first,
        second,
        state_facts=state_facts,
        observations=(observation,),
        private_profile_complete=True,
    )
    assert repaired.intent == "select"
    assert repaired.target_offer_id == "offer:" + "a" * 64
    assert repaired.selection_requested is False
    repaired_facts = {item.name: item.value for item in repaired.facts}
    assert repaired_facts == {
        "service": "agency",
        "product_id": "product:tour-4ps",
        "activity_date": date(2026, 11, 18),
        "adults": 1,
        "children": 0,
        "payment_method": "stripe",
        "birth_date": date(1990, 4, 14),
        "gender": "f",
    }

    forged_selection = replace(
        second,
        intent="select",
        target_offer_id="offer:" + "b" * 64,
    )
    canonicalized = _repair_requested_activity_selection(
        first,
        forged_selection,
        state_facts=state_facts,
        observations=(observation,),
        private_profile_complete=True,
    )
    assert canonicalized.intent == "select"
    assert canonicalized.target_offer_id == "offer:" + "a" * 64

    not_requested = replace(first, selection_requested=False)
    assert _repair_requested_activity_selection(
        not_requested,
        second,
        state_facts=state_facts,
        observations=(observation,),
        private_profile_complete=True,
    ) is second
    assert _repair_requested_activity_selection(
        first,
        second,
        state_facts=state_facts,
        observations=(observation,),
        private_profile_complete=False,
    ) is second


def _enabled_reducer(
    *,
    approval_ttl: timedelta = timedelta(minutes=30),
    valid_until: datetime | None = None,
) -> V2ConversationReducer:
    return V2ConversationReducer(
        approval_ttl=approval_ttl,
        critical_action_policy=CriticalActionPolicy(
            frozenset(
                {
                    CriticalActionKind.RESERVE_LODGING,
                    CriticalActionKind.BOOK_ACTIVITY,
                    CriticalActionKind.BOOK_PACKAGE,
                    CriticalActionKind.INITIATE_PAYMENT,
                }
            ),
            enabled_payment_methods=frozenset({"stripe", "wise", "pix"}),
            valid_until=valid_until,
        ),
    )
EVENT = InboundEvent(
    event_id="event:turn-executor-001",
    lead_id="manychat:lead-executor-001",
    subscriber_id="lead-executor-001",
    conversation_id="conversation:turn-executor-001",
    text="Oi, quero informações.",
    media_url=None,
    media_type=None,
    occurred_at=NOW - timedelta(seconds=1),
    payload_hash="1" * 64,
)
BATCH = InboundBatch(
    batch_id="batch:turn-executor-001",
    lead_id=EVENT.lead_id,
    subscriber_id=EVENT.subscriber_id,
    events=(EVENT,),
    combined_text=EVENT.text,
)
AUTHORITY = PublicTurnAuthority(
    authorization_kind="conversation_test",
    authorization_id="auth:turn-executor-001",
    scope_subject_id=BATCH.subscriber_id,
    target_binding_hash=TARGET_DIGEST,
    channel_id="manychat:channel-001",
    channel_scope="manychat:conversation-001",
    immutable_generation=1,
    allocation_ids=("allocation:public-001",),
    capability_policy_digest=CAPABILITY_DIGEST,
    effect_authorization_binding_digest=EFFECT_DIGEST,
    contract_digest="f" * 64,
    allocation_manifest_hash="9" * 64,
    deadline_at=NOW + timedelta(minutes=1),
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequenceClock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        value = NOW + timedelta(seconds=self.calls)
        self.calls += 1
        return value


class ScriptedClock:
    def __init__(self, values: tuple[datetime, ...]) -> None:
        if not values:
            raise ValueError("scripted clock requires values")
        self._values = list(values)
        self._last = values[-1]

    def now(self) -> datetime:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


class FakeProfile:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store
        self.calls = 0

    def read(self, lead_id: str, *, now: datetime) -> PrivateCustomerBinding:
        assert self.store._connection.in_transaction is False
        self.calls += 1
        return PrivateCustomerBinding(
            binding_id="profile-binding:" + "d" * 64,
            content_hash="e" * 64,
            full_name="Pessoa Teste",
            email="person@example.invalid",
            phone_e164="+5511999999999",
            country_code="BR",
            observed_at=now,
            expires_at=now + timedelta(minutes=5),
            complete=True,
        )


class RecordingPublicDelivery:
    def __init__(self, *, uncertain: bool = False) -> None:
        self.uncertain = uncertain
        self.calls = []

    def send(self, claim):
        self.calls.append(claim)
        if self.uncertain:
            raise PublicDeliveryUnknown("provider response lost after call")
        return "manychat:receipt:turn-executor-001"


class FakeAuditedModel:
    def __init__(
        self,
        store: SQLiteBoundaryStore,
        proposals: list[ModelProposal],
    ) -> None:
        self.store = store
        self.proposals = proposals
        self.calls: list[ModelRequest] = []
        self.on_call = None

    def complete_audited(self, request: ModelRequest) -> AuditedModelTurn:
        assert self.store._connection.in_transaction is False
        if self.on_call is not None:
            self.on_call()
        self.calls.append(request)
        proposal = self.proposals.pop(0)
        stdin = (
            f"{request.request_id}|{request.state_version}|{len(request.observations)}"
        ).encode()
        response = f"{proposal.source_event_id}|{proposal.intent}".encode()
        stdout = b"child-log\nPHASE8_RESULT\x00" + response
        return AuditedModelTurn.from_exchange(
            proposal=proposal,
            stdin_bytes=stdin,
            stdout_bytes=stdout,
            response_bytes=response,
            transcript_key=TRANSCRIPT_KEY,
            ephemeral_session_id="uds:fake-model-session",
        )


class DeterministicFallbackAuditedModel:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store

    def complete_audited(self, request: ModelRequest) -> AuditedModelTurn:
        assert self.store._connection.in_transaction is False
        proposal = ModelProposal(
            source_event_id=request.source_event_id,
            intent="inform",
            reply_chunks=("Pode repetir sua última mensagem?",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
        )
        frames = tuple(
            AuditedTranscriptFrame.create(
                stdin_bytes=f"attempt:{index}:{request.request_id}".encode(),
                stdout_bytes=b"V2_AUDIT\x00" + response,
                response_bytes=response,
                transcript_key=TRANSCRIPT_KEY,
            )
            for index, response in enumerate(
                (b"invalid-one", b"invalid-two", b"deterministic-fallback"),
                start=1,
            )
        )
        return AuditedModelTurn.from_frames(
            proposal=proposal,
            frames=frames,
            ephemeral_session_id="deterministic:test-fallback",
        )


class FixedAuthority:
    def resolve(
        self,
        batch: InboundBatch,
        *,
        chunk_count: int,
        now: datetime,
    ) -> PublicTurnAuthority:
        assert batch == BATCH
        assert chunk_count == 1
        assert now == NOW
        return AUTHORITY


class MappingAuthority:
    def __init__(self, values: dict[str, PublicTurnAuthority]) -> None:
        self.values = values

    def resolve(
        self,
        batch: InboundBatch,
        *,
        chunk_count: int,
        now: datetime,
    ) -> PublicTurnAuthority:
        value = self.values[batch.batch_id]
        assert chunk_count == len(value.allocation_ids)
        assert NOW <= now < value.deadline_at
        return value


class FakeLodgingReadPort:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store
        self.calls: list[ReadRequest] = []

    def read(self, request: ReadRequest) -> ReadObservation:
        assert self.store._connection.in_transaction is False
        self.calls.append(request)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="cloudbeds",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            public_payload={
                "offer_id": "offer:" + "7" * 64,
                "room_public_name": "Suíte Casal",
                "check_in": "2026-08-10",
                "check_out": "2026-08-12",
                "adults": 2,
                "children": 0,
                "total_amount": "480.00",
                "currency": "BRL",
                "available": True,
                "available_units": 1,
            },
            private_binding_hash="8" * 64,
        )


class FakeActivityReadPort:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store
        self.calls: list[ReadRequest] = []

    def read(self, request: ReadRequest) -> ReadObservation:
        assert self.store._connection.in_transaction is False
        self.calls.append(request)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="bokun",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            public_payload={
                "offer_id": "offer:" + "6" * 64,
                "product_id": "product:buracao",
                "product_public_name": "Cachoeira do Buracão",
                "activity_date": "2026-08-12",
                "participants": 2,
                "total_amount": "1300.00",
                "currency": "BRL",
                "available": True,
            },
            private_binding_hash="5" * 64,
        )


class FakeActivityDescriptionReadPort:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store
        self.calls: list[ReadRequest] = []

    def read(self, request: ReadRequest) -> ReadObservation:
        assert self.store._connection.in_transaction is False
        self.calls.append(request)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="commercial_catalog",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            public_payload={
                "product_id": "product:buracao",
                "public_name": "Cachoeira do Buracão",
                "description": "Passeio disponível às quartas-feiras.",
            },
            private_binding_hash="4" * 64,
        )


class ProviderClockedLodgingReadPort:
    """Stamp the observation during the provider call, after turn start."""

    def __init__(self, store: SQLiteBoundaryStore, clock: SequenceClock) -> None:
        self.store = store
        self.clock = clock
        self.calls: list[ReadRequest] = []

    def read(self, request: ReadRequest) -> ReadObservation:
        assert self.store._connection.in_transaction is False
        self.calls.append(request)
        observed_at = self.clock.now()
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="cloudbeds",
            observed_at=observed_at,
            expires_at=observed_at + timedelta(minutes=5),
            public_payload={
                "offer_id": "offer:" + "7" * 64,
                "room_public_name": "Suíte Casal",
                "check_in": "2026-08-10",
                "check_out": "2026-08-12",
                "adults": 2,
                "children": 0,
                "total_amount": "480.00",
                "currency": "BRL",
                "available": True,
                "available_units": 1,
            },
            private_binding_hash="8" * 64,
        )


class FaultingStore:
    def __init__(self, inner: SQLiteBoundaryStore, stage: str) -> None:
        self.inner = inner
        self.stage = stage

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def commit_turn_v8(self, **values):
        def fault(stage: str) -> None:
            if stage == self.stage:
                raise RuntimeError("injected atomic turn failure")

        return self.inner.commit_turn_v8(**values, fault_hook=fault)


def _proposal(text: str = "Olá! Como posso ajudar?") -> ModelProposal:
    return ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(text,),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )


def _install_public_authority(
    store: SQLiteBoundaryStore,
    authority: PublicTurnAuthority = AUTHORITY,
) -> None:
    common = (
        authority.authorization_id,
        authority.scope_subject_id,
        authority.channel_scope,
        authority.immutable_generation,
        authority.authorization_kind,
        authority.qualification_id,
        authority.scenario_id,
        authority.contract_digest,
        authority.effect_authorization_binding_digest,
        authority.capability_policy_digest,
        authority.target_binding_hash,
        authority.allocation_manifest_hash,
    )
    with store._transaction():
        store._connection.execute(
            "INSERT INTO boundary_dispatch_authority "
            "(authorization_id,scope_subject_id,channel_scope,generation,allocation_id,"
            "row_kind,authorization_kind,qualification_id,scenario_id,contract_digest,"
            "effect_authorization_binding_digest,capability_policy_digest,target_binding_hash,"
            "allowed_chunk_ordinal,allocation_manifest_hash,state,public_row_id,cas_revision,"
            "closure_receipt_hash,created_at,updated_at,fenced_at) "
            "VALUES (?,?,?,?,?,'generation_header',?,?,?,?,?,?,?,?,?,'open',NULL,0,NULL,?,?,NULL)",
            common[:4]
            + ("__header__",)
            + common[4:11]
            + (None, common[11], NOW.isoformat(), NOW.isoformat()),
        )
        for ordinal, allocation_id in enumerate(authority.allocation_ids):
            store._connection.execute(
                "INSERT INTO boundary_dispatch_authority "
                "(authorization_id,scope_subject_id,channel_scope,generation,allocation_id,"
                "row_kind,authorization_kind,qualification_id,scenario_id,contract_digest,"
                "effect_authorization_binding_digest,capability_policy_digest,target_binding_hash,"
                "allowed_chunk_ordinal,allocation_manifest_hash,state,public_row_id,cas_revision,"
                "closure_receipt_hash,created_at,updated_at,fenced_at) "
                "VALUES (?,?,?,?,?,'allocation',?,?,?,?,?,?,?,?,?,'available',NULL,0,NULL,?,?,NULL)",
                common[:4]
                + (allocation_id,)
                + common[4:11]
                + (ordinal, common[11], NOW.isoformat(), NOW.isoformat()),
            )


def _executor(
    *,
    store: SQLiteBoundaryStore,
    model: FakeAuditedModel,
    profile: FakeProfile,
    reads: V2ReadService | None = None,
) -> V2TurnExecutor:
    return V2TurnExecutor(
        store=store,
        model=model,
        reads=reads or V2ReadService({}),
        profile=profile,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )


def _approval_expiry_fixture(
    *,
    approval_ttl: timedelta,
    confirmation_clock: ScriptedClock,
) -> tuple[
    SQLiteBoundaryStore,
    FakeAuditedModel,
    FakeLodgingReadPort,
    InboundBatch,
    V2TurnExecutor,
]:
    second_event = InboundEvent(
        event_id="event:approval-expiry-002",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        conversation_id=EVENT.conversation_id,
        text="Pode reservar exatamente assim.",
        media_url=None,
        media_type=None,
        occurred_at=NOW,
        payload_hash="9" * 64,
    )
    second_batch = InboundBatch(
        batch_id="batch:approval-expiry-002",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(second_event,),
        combined_text=second_event.text,
    )
    second_authority = replace(
        AUTHORITY,
        authorization_id="auth:approval-expiry-002",
        allocation_ids=("allocation:approval-expiry-002",),
        allocation_manifest_hash="7" * 64,
        deadline_at=NOW + timedelta(minutes=1),
    )
    first_read = ReadRequest(
        request_id="read:approval-expiry-selection",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    selection = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar o resumo.",),
        facts=(
            ModelFact("language", "pt-BR"),
            ModelFact("service", "hostel"),
            ModelFact("start_date", date(2026, 8, 10)),
            ModelFact("end_date", date(2026, 8, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", "stripe"),
        ),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "7" * 64,
    )
    confirmation = ModelProposal(
        source_event_id=second_batch.batch_id,
        intent="confirm",
        reply_chunks=("Confirmado.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        confirmed_summary_version=1,
        confirmed_action_kinds=(
            CriticalActionKind.INITIATE_PAYMENT,
            CriticalActionKind.RESERVE_LODGING,
        ),
        approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
    )
    proposals = [
        ModelProposal(
            source_event_id=BATCH.batch_id,
            intent="inform",
            reply_chunks=(),
            facts=(),
            read_requests=(first_read,),
            effect_proposals=(),
        ),
        selection,
        confirmation,
        confirmation,
    ]
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, proposals)
    profile = FakeProfile(store)
    read_port = FakeLodgingReadPort(store)
    reads = V2ReadService({ReadKind.LODGING: read_port})
    authority = MappingAuthority(
        {
            BATCH.batch_id: AUTHORITY,
            second_batch.batch_id: second_authority,
        }
    )
    _install_public_authority(store, AUTHORITY)
    _install_public_authority(store, second_authority)
    reducer = _enabled_reducer(approval_ttl=approval_ttl)
    V2TurnExecutor(
        store=store,
        model=model,
        reads=reads,
        profile=profile,
        reducer=reducer,
        public_authority=authority,
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    ).execute(BATCH)
    confirmation_executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=reads,
        profile=profile,
        reducer=reducer,
        public_authority=authority,
        clock=confirmation_clock,
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    return store, model, read_port, second_batch, confirmation_executor


def test_atomic_executor_commits_projection_receipt_public_row_and_replays() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [_proposal()])
    profile = FakeProfile(store)
    _install_public_authority(store)
    executor = _executor(store=store, model=model, profile=profile)
    try:
        first = executor.execute(BATCH)
        replay = executor.execute(BATCH)

        assert first.replayed is False
        assert replay.replayed is True
        assert replay.receipt == first.receipt
        assert first.reply_chunks == ("Olá! Como posso ajudar?",)
        assert first.receipt.committed_state_version == 1
        assert store.turn_receipt_count(BATCH.batch_id) == 1
        assert store.load_turn_receipt(BATCH.batch_id) == first.receipt
        projection = store.load_latest_conversation_projection(BATCH.lead_id)
        assert type(projection) is ConversationProjection
        assert (
            projection.canonical_hash() == first.receipt.behavior_state_snapshot_digest
        )
        assert model.calls and len(model.calls) == 1
        assert profile.calls == 1
        assert (
            store._connection.execute(
                "SELECT count(*) FROM boundary_public_outbox"
            ).fetchone()[0]
            == 1
        )
        assert (
            store._connection.execute(
                "SELECT count(*) FROM boundary_turn_artifacts "
                "WHERE artifact_kind='conversation_projection'"
            ).fetchone()[0]
            == 0
        )
        assert (
            store._connection.execute(
                "SELECT count(*) FROM boundary_turn_artifacts "
                "WHERE artifact_kind='typed_fact'"
            ).fetchone()[0]
            == 1
        )
        assert hashlib.sha256(model.calls[0].request_id.encode()).hexdigest() != ""
    finally:
        store.close()


def test_executor_accepts_observation_stamped_after_turn_start() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    clock = SequenceClock()
    request = ReadRequest(
        request_id="read:post-call-clock",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Vou consultar.",),
        facts=(),
        read_requests=(request,),
        effect_proposals=(),
    )
    model = FakeAuditedModel(store, [first, _proposal("Temos uma opção disponível.")])
    port = ProviderClockedLodgingReadPort(store, clock)
    _install_public_authority(store)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: port}),
        profile=FakeProfile(store),
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority({BATCH.batch_id: AUTHORITY}),
        clock=clock,
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == ("Temos uma opção disponível.",)
        assert len(port.calls) == 1
        assert len(model.calls) == 2
    finally:
        store.close()


def test_executor_commits_multi_frame_deterministic_fallback_without_effects() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=DeterministicFallbackAuditedModel(store),
        profile=FakeProfile(store),
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == ("Pode repetir sua última mensagem?",)
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_turn_artifacts "
            "WHERE artifact_kind='frame_commitment'"
        ).fetchone() == (3,)
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone() == (0,)
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_outbox"
        ).fetchone() == (0,)
    finally:
        store.close()


def _committed_public_store() -> SQLiteBoundaryStore:
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [_proposal()])
    _install_public_authority(store)
    _executor(store=store, model=model, profile=FakeProfile(store)).execute(BATCH)
    return store


def test_boundary_public_worker_fences_then_persists_delivery_receipt() -> None:
    store = _committed_public_store()
    queues = SQLiteBoundaryWorkerStore(store)
    delivery = RecordingPublicDelivery()
    worker = BoundaryPublicDeliveryWorker(
        boundary=queues,
        delivery=delivery,
        worker_id="worker:v2-public",
        lease_ttl=timedelta(seconds=30),
    )
    try:
        delivered = worker.run_once(now=NOW + timedelta(seconds=1))
        idle = worker.run_once(now=NOW + timedelta(seconds=2))
        assert delivered is BoundaryPublicDisposition.DELIVERED
        assert idle is BoundaryPublicDisposition.IDLE
        assert len(delivery.calls) == 1
        assert store._connection.execute(
            "SELECT status,dispatch_slots_consumed,delivery_receipt_hash "
            "FROM boundary_public_outbox"
        ).fetchone()[0:2] == ("delivered", 1)
        assert store._connection.execute(
            "SELECT state FROM boundary_dispatch_authority WHERE row_kind='allocation'"
        ).fetchone() == ("terminal",)
    finally:
        store.close()


def test_boundary_public_unknown_after_call_moves_to_manual_without_redispatch() -> (
    None
):
    store = _committed_public_store()
    queues = SQLiteBoundaryWorkerStore(store)
    delivery = RecordingPublicDelivery(uncertain=True)
    worker = BoundaryPublicDeliveryWorker(
        boundary=queues,
        delivery=delivery,
        worker_id="worker:v2-public",
        lease_ttl=timedelta(seconds=30),
    )
    try:
        first = worker.run_once(now=NOW + timedelta(seconds=1))
        second = worker.run_once(now=NOW + timedelta(seconds=32))
        assert first is BoundaryPublicDisposition.MANUAL_REVIEW
        assert second is BoundaryPublicDisposition.IDLE
        assert len(delivery.calls) == 1
        assert store._connection.execute(
            "SELECT status,dispatch_slots_consumed FROM boundary_public_outbox"
        ).fetchone() == ("manual_review", 1)
        assert store._connection.execute(
            "SELECT state FROM boundary_dispatch_authority WHERE row_kind='allocation'"
        ).fetchone() == ("manual_review",)
    finally:
        store.close()


def test_public_crash_after_provider_call_recovers_to_manual_without_redispatch() -> (
    None
):
    store = _committed_public_store()
    queues = SQLiteBoundaryWorkerStore(store)
    delivery = RecordingPublicDelivery()
    try:
        claim = queues.claim_public_delivery(
            worker_id="worker:v2-public-crash",
            now=NOW + timedelta(seconds=1),
            lease_ttl=timedelta(seconds=30),
        )
        assert claim is not None
        queues.fence_public_delivery(claim, now=NOW + timedelta(seconds=1))
        assert delivery.send(claim) == "manychat:receipt:turn-executor-001"
        # Simulated process death: no receipt commit and no in-process exception handler.
        worker = BoundaryPublicDeliveryWorker(
            boundary=queues,
            delivery=delivery,
            worker_id="worker:v2-public-recovery",
            lease_ttl=timedelta(seconds=30),
        )
        assert (
            worker.run_once(now=NOW + timedelta(seconds=31))
            is BoundaryPublicDisposition.IDLE
        )
        assert len(delivery.calls) == 1
        assert store._connection.execute(
            "SELECT status FROM boundary_public_outbox"
        ).fetchone() == ("manual_review",)
    finally:
        store.close()


def test_atomic_executor_rolls_back_every_child_row_and_allocation_on_fault() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [_proposal()])
    profile = FakeProfile(store)
    _install_public_authority(store)
    executor = V2TurnExecutor(
        store=FaultingStore(store, "after_public_outbox_insert_0"),
        model=model,
        reads=V2ReadService({}),
        profile=profile,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    try:
        with pytest.raises(RuntimeError, match="injected atomic turn failure"):
            executor.execute(BATCH)

        assert store.load_state(BATCH.lead_id).version == 0
        assert store.turn_receipt_count(BATCH.batch_id) == 0
        for table in (
            "boundary_events",
            "boundary_event_sources",
            "boundary_turn_artifacts",
            "boundary_public_outbox",
        ):
            assert (
                store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                == 0
            )
        assert store._connection.execute(
            "SELECT state,public_row_id FROM boundary_dispatch_authority "
            "WHERE allocation_id=?",
            (AUTHORITY.allocation_ids[0],),
        ).fetchone() == ("available", None)
    finally:
        store.close()


def test_stale_fence_discards_decision_and_recomputes_the_whole_turn() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [_proposal(), _proposal()])
    profile = FakeProfile(store)
    _install_public_authority(store)

    def steal_first_fence() -> None:
        if not model.calls:
            store.acquire_fence(BATCH.lead_id)

    model.on_call = steal_first_fence
    executor = _executor(store=store, model=model, profile=profile)
    try:
        result = executor.execute(BATCH)

        assert result.replayed is False
        assert result.receipt.committed_state_version == 1
        assert len(model.calls) == 2
        assert profile.calls == 2
        assert store.turn_receipt_count(BATCH.batch_id) == 1
        assert store._connection.execute(
            "SELECT state FROM boundary_dispatch_authority WHERE allocation_id=?",
            (AUTHORITY.allocation_ids[0],),
        ).fetchone() == ("bound",)
    finally:
        store.close()


def test_read_loop_runs_outside_transaction_and_commits_phase8_read_artifact() -> None:
    request = ReadRequest(
        request_id="read:executor-lodging-001",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(),
        facts=(),
        read_requests=(request,),
        effect_proposals=(),
    )
    final = _proposal("A suíte está disponível por BRL 480.00.")
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [first, final])
    profile = FakeProfile(store)
    read_port = FakeLodgingReadPort(store)
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=profile,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
    )
    try:
        result = executor.execute(BATCH)

        assert len(model.calls) == 2
        assert model.calls[0].private_profile_complete is True
        assert model.calls[1].private_profile_complete is True
        assert len(model.calls[1].observations) == 1
        assert read_port.calls == [request]
        assert result.receipt.uds_final_seq == 2
        assert len(result.receipt.read_observations) == 1
        row = store._connection.execute(
            "SELECT artifact_kind,frame_sequence,frame_reference "
            "FROM boundary_turn_artifacts WHERE artifact_kind='read_observation'"
        ).fetchone()
        assert row[0] == "read_observation"
        assert row[1] is None
        assert type(row[2]) is str and len(row[2]) == 64
    finally:
        store.close()


def test_package_turn_accepts_two_reads_bound_to_the_same_model_frame() -> None:
    lodging = ReadRequest(
        request_id="read:package-lodging",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    activity = ReadRequest(
        request_id="read:package-activity",
        kind=ReadKind.ACTIVITY,
        product_id="product:buracao",
        activity_date=date(2026, 8, 12),
        participants=2,
    )
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(),
        facts=(ModelFact("service", "package"),),
        read_requests=(lodging, activity),
        effect_proposals=(),
    )
    final = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Encontrei opções de hospedagem e Buracão.",),
        facts=(ModelFact("service", "package"),),
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [first, final])
    lodging_port = FakeLodgingReadPort(store)
    activity_port = FakeActivityReadPort(store)
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService(
            {
                ReadKind.LODGING: lodging_port,
                ReadKind.ACTIVITY: activity_port,
            }
        ),
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == ("Encontrei opções de hospedagem e Buracão.",)
        assert lodging_port.calls == [lodging]
        assert activity_port.calls == [activity]
        assert len(result.receipt.read_observations) == 2
    finally:
        store.close()


def test_activity_description_read_does_not_enter_availability_bridge() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    description_request = ReadRequest(
        request_id="read:description-buracao",
        kind=ReadKind.ACTIVITY_DESCRIPTION,
        product_id="product:buracao",
    )
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Vou conferir os detalhes do passeio.",),
        facts=(
            ModelFact("service", "agency"),
            ModelFact("product_id", "product:buracao"),
        ),
        read_requests=(description_request,),
        effect_proposals=(),
    )
    second = _proposal("O Buracão acontece às quartas-feiras.")
    model = FakeAuditedModel(store, [first, second])
    port = FakeActivityDescriptionReadPort(store)
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.ACTIVITY_DESCRIPTION: port}),
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == ("O Buracão acontece às quartas-feiras.",)
        assert len(port.calls) == 1
        assert len(model.calls) == 2
        assert model.calls[1].observations[0].public_payload["description"].startswith(
            "Passeio disponível"
        )
        assert result.receipt.read_observations == ()
        assert (
            store._connection.execute(
                "SELECT count(*) FROM boundary_turn_artifacts "
                "WHERE artifact_kind='read_observation'"
            ).fetchone()
            == (0,)
        )
    finally:
        store.close()


def test_read_round_preserves_first_frame_customer_facts_for_selection() -> None:
    request = ReadRequest(
        request_id="read:preserve-customer-facts",
        kind=ReadKind.ACTIVITY,
        product_id="product:buracao",
        activity_date=date(2026, 8, 12),
        participants=2,
    )
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Vou conferir a disponibilidade.",),
        facts=(
            ModelFact("birth_date", date(1992, 4, 15)),
            ModelFact("gender", "f"),
        ),
        read_requests=(request,),
        effect_proposals=(),
    )
    selection = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar o resumo.",),
        facts=(
            ModelFact("service", "agency"),
            ModelFact("product_id", "product:buracao"),
            ModelFact("activity_date", date(2026, 8, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", "stripe"),
        ),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "6" * 64,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [first, selection])
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.ACTIVITY: FakeActivityReadPort(store)}),
    )
    try:
        result = executor.execute(BATCH)
        projection = store.load_latest_conversation_projection(BATCH.lead_id)

        assert result.reply_chunks[0].startswith("Só para confirmar:")
        assert projection is not None
        values = {fact.name: fact.value.value for fact in projection.facts}
        assert values["birth_date"] == date(1992, 4, 15)
        assert values["gender"] == "f"

        confirmation_event = replace(
            EVENT,
            event_id="event:preserve-customer-facts-confirm",
            text="Sim",
            occurred_at=NOW,
            payload_hash="8" * 64,
        )
        confirmation_batch = InboundBatch(
            batch_id="batch:preserve-customer-facts-confirm",
            lead_id=BATCH.lead_id,
            subscriber_id=BATCH.subscriber_id,
            events=(confirmation_event,),
            combined_text=confirmation_event.text,
        )
        confirmation_authority = replace(
            AUTHORITY,
            authorization_id="auth:preserve-customer-facts-confirm",
            allocation_ids=("allocation:preserve-customer-facts-confirm",),
            allocation_manifest_hash="8" * 64,
        )
        confirmation = ModelProposal(
            source_event_id=confirmation_batch.batch_id,
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(
                CriticalActionKind.BOOK_ACTIVITY,
                CriticalActionKind.INITIATE_PAYMENT,
            ),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        )
        confirmation_with_read = replace(
            confirmation,
            read_requests=(
                ReadRequest(
                    request_id="read:model-confirm-activity",
                    kind=ReadKind.ACTIVITY,
                    product_id="product:buracao",
                    activity_date=date(2026, 8, 12),
                    participants=2,
                ),
            ),
        )
        model.proposals.extend([confirmation_with_read, confirmation])
        _install_public_authority(store, confirmation_authority)
        confirmation_executor = V2TurnExecutor(
            store=store,
            model=model,
            reads=V2ReadService({ReadKind.ACTIVITY: FakeActivityReadPort(store)}),
            profile=FakeProfile(store),
            reducer=_enabled_reducer(),
            public_authority=MappingAuthority(
                {confirmation_batch.batch_id: confirmation_authority}
            ),
            clock=ScriptedClock((NOW + timedelta(seconds=1),)),
            locale="pt-BR",
            turn_timeout=timedelta(seconds=30),
            max_commit_attempts=2,
        )

        confirmed = confirmation_executor.execute(confirmation_batch)

        assert len(confirmed.receipt.command_rows) == 1
        assert confirmed.reply_chunks == ("Perfeito — vou processar sua reserva agora.",)
    finally:
        store.close()


def test_activity_confirmation_derives_current_provider_read() -> None:
    first_read = ReadRequest(
        request_id="read:derive-activity-selection",
        kind=ReadKind.ACTIVITY,
        product_id="product:buracao",
        activity_date=date(2026, 8, 12),
        participants=2,
    )
    selection = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar o resumo do passeio.",),
        facts=(
            ModelFact("language", "pt-BR"),
            ModelFact("service", "agency"),
            ModelFact("product_id", "product:buracao"),
            ModelFact("start_date", date(2026, 8, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", "stripe"),
            ModelFact("birth_date", date(1992, 4, 15)),
            ModelFact("gender", "f"),
        ),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "6" * 64,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(
        store,
        [
            ModelProposal(
                source_event_id=BATCH.batch_id,
                intent="inform",
                reply_chunks=(),
                facts=(),
                read_requests=(first_read,),
                effect_proposals=(),
            ),
            selection,
        ],
    )
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.ACTIVITY: FakeActivityReadPort(store)}),
    )
    try:
        executor.execute(BATCH)
        state = store.load_state(BATCH.lead_id).state
        projection = store.load_latest_conversation_projection(BATCH.lead_id)
        assert projection is not None
        confirmation = ModelProposal(
            source_event_id="batch:derive-activity-confirmation",
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(
                CriticalActionKind.BOOK_ACTIVITY,
                CriticalActionKind.INITIATE_PAYMENT,
            ),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        )

        derived = _confirmation_read_requests(state, projection, confirmation)
        assert len(derived) == 1
        assert derived[0].kind is ReadKind.ACTIVITY
        assert derived[0].product_id == "product:buracao"
        assert derived[0].activity_date == date(2026, 8, 12)
        assert derived[0].participants == 2
    finally:
        store.close()


def test_confirmation_read_derivation_requires_typed_confirm_and_current_version() -> None:
    first_read = ReadRequest(
        request_id="read:derive-guard-selection",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    selection = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar o resumo.",),
        facts=(
            ModelFact("language", "pt-BR"),
            ModelFact("service", "hostel"),
            ModelFact("start_date", date(2026, 8, 10)),
            ModelFact("end_date", date(2026, 8, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", "stripe"),
        ),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "7" * 64,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(
        store,
        [
            ModelProposal(
                source_event_id=BATCH.batch_id,
                intent="inform",
                reply_chunks=(),
                facts=(),
                read_requests=(first_read,),
                effect_proposals=(),
            ),
            selection,
        ],
    )
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.LODGING: FakeLodgingReadPort(store)}),
    )
    try:
        executor.execute(BATCH)
        state = store.load_state(BATCH.lead_id).state
        projection = store.load_latest_conversation_projection(BATCH.lead_id)
        assert projection is not None
        inform = ModelProposal(
            source_event_id="batch:derive-guard-inform",
            intent="inform",
            reply_chunks=("Posso ajudar com mais alguma coisa?",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
        )
        stale = ModelProposal(
            source_event_id="batch:derive-guard-stale",
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=2,
            confirmed_action_kinds=(
                CriticalActionKind.INITIATE_PAYMENT,
                CriticalActionKind.RESERVE_LODGING,
            ),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        )
        current = ModelProposal(
            source_event_id="batch:derive-guard-current",
            intent="confirm",
            reply_chunks=("Confirmado.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(
                CriticalActionKind.INITIATE_PAYMENT,
                CriticalActionKind.RESERVE_LODGING,
            ),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        )

        assert _confirmation_read_requests(state, projection, inform) == ()
        assert _confirmation_read_requests(state, projection, stale) == ()
        derived = _confirmation_read_requests(state, projection, current)
        assert len(derived) == 1
        assert derived[0].kind is ReadKind.LODGING
        assert derived[0].check_in == date(2026, 8, 10)
        assert derived[0].check_out == date(2026, 8, 12)
        assert derived[0].adults == 2
        assert derived[0].children == 0
    finally:
        store.close()


def test_critical_confirmation_binding_rejects_expiry_and_scope_drift() -> None:
    pending = PendingCriticalActionContext(
        summary_version=1,
        action_kinds=(
            CriticalActionKind.INITIATE_PAYMENT,
            CriticalActionKind.RESERVE_LODGING,
        ),
        public_summary="Só para confirmar: vou reservar e gerar o link.",
        expires_at=NOW + timedelta(minutes=30),
    )
    proposal = ModelProposal(
        source_event_id="batch:critical-binding",
        intent="confirm",
        reply_chunks=("Pode seguir exatamente assim.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        confirmed_summary_version=1,
        confirmed_action_kinds=pending.action_kinds,
        approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
    )

    def bound(
        candidate: ModelProposal,
        *,
        now: datetime = NOW,
        material_scope_bound: bool = True,
    ) -> bool:
        return _critical_confirmation_bound(
            pending,
            candidate,
            now=now,
            material_scope_bound=material_scope_bound,
        )

    def reads_allowed(
        candidate: ModelProposal,
        *,
        now: datetime = NOW,
        material_scope_bound: bool = True,
    ) -> bool:
        return _critical_model_reads_allowed(
            pending,
            candidate,
            now=now,
            material_scope_bound=material_scope_bound,
        )

    assert bound(proposal) is True
    assert bound(proposal, now=pending.expires_at) is False
    assert bound(proposal, material_scope_bound=False) is False
    wrong_scope = replace(
        proposal,
        confirmed_action_kinds=(
            CriticalActionKind.CANCEL_RESERVATION,
            CriticalActionKind.INITIATE_PAYMENT,
        ),
    )
    assert bound(wrong_scope) is False
    assert (
        _critical_confirmation_bound(
            None,
            proposal,
            now=NOW,
            material_scope_bound=True,
        )
        is False
    )
    assert (
        _critical_model_reads_allowed(
            None,
            proposal,
            now=NOW,
            material_scope_bound=True,
        )
        is False
    )
    assert reads_allowed(proposal) is True
    assert reads_allowed(proposal, now=pending.expires_at) is False
    assert reads_allowed(proposal, material_scope_bound=False) is False
    assert reads_allowed(wrong_scope) is False
    adjustment = ModelProposal(
        source_event_id="batch:critical-adjustment",
        intent="adjust",
        reply_chunks=("Vou ajustar antes de seguir.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    assert reads_allowed(adjustment) is False
    information = replace(
        adjustment,
        source_event_id="batch:critical-information",
        intent="inform",
    )
    assert reads_allowed(information) is True

def test_approval_expiring_during_model_call_starts_zero_confirmation_reads() -> None:
    store, model, read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(seconds=2),
        confirmation_clock=ScriptedClock(
            (
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
                NOW + timedelta(seconds=2),
                NOW + timedelta(seconds=2),
            )
        ),
    )
    try:
        assert len(read_port.calls) == 1
        expired = executor.execute(second_batch)
        assert "expirou" in " ".join(expired.reply_chunks).casefold()
        assert len(read_port.calls) == 1
        assert len(model.calls) == 3
        assert expired.receipt.command_rows == ()
        assert expired.receipt.relay_rows == ()
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_command_relays"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_approval_expiring_between_reducer_and_commit_persists_zero_effect_rows() -> None:
    store, model, read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(seconds=4),
        confirmation_clock=ScriptedClock(
            (
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=4),
            )
        ),
    )
    try:
        with pytest.raises(TurnExecutionError, match="approval expired before commit"):
            executor.execute(second_batch)
        assert len(read_port.calls) == 2
        assert len(model.calls) == 4
        assert store.load_state(BATCH.lead_id).version == 1
        assert store.load_turn_receipt(second_batch.batch_id) is None
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_command_relays"
        ).fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.parametrize(
    "confirmation_text",
    (
        "Sim",
        "Pode reservar",
        "Pode sim",
        "Confirmado",
        "Isso mesmo",
        "Sim, por favor",
        "Pode reservar esse passeio e gerar o link do sinal no cartão.",
        "Confirmed. Please book exactly that summary.",
    ),
)
def test_confirmed_turn_commits_reservation_command_and_relay_atomically(
    tmp_path,
    confirmation_text: str,
) -> None:
    second_event = InboundEvent(
        event_id="event:turn-executor-002",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        conversation_id=EVENT.conversation_id,
        text=confirmation_text,
        media_url=None,
        media_type=None,
        occurred_at=NOW,
        payload_hash="2" * 64,
    )
    second_batch = InboundBatch(
        batch_id="batch:turn-executor-002",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(second_event,),
        combined_text=second_event.text,
    )
    second_authority = PublicTurnAuthority(
        authorization_kind="conversation_test",
        authorization_id="auth:turn-executor-002",
        scope_subject_id=BATCH.subscriber_id,
        target_binding_hash=TARGET_DIGEST,
        channel_id="manychat:channel-001",
        channel_scope="manychat:conversation-001",
        immutable_generation=1,
        allocation_ids=("allocation:public-002",),
        capability_policy_digest=CAPABILITY_DIGEST,
        effect_authorization_binding_digest=EFFECT_DIGEST,
        contract_digest="f" * 64,
        allocation_manifest_hash="6" * 64,
        deadline_at=NOW + timedelta(minutes=1),
    )
    first_read = ReadRequest(
        request_id="read:selection-lodging-001",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    selection_language = (
        "en" if confirmation_text.startswith("Confirmed.") else "pt-BR"
    )
    selection = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar o resumo.",),
        facts=(
            ModelFact("language", selection_language),
            ModelFact("service", "hostel"),
            ModelFact("start_date", date(2026, 8, 10)),
            ModelFact("end_date", date(2026, 8, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", "stripe"),
        ),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "7" * 64,
    )
    confirmation = ModelProposal(
        source_event_id=second_batch.batch_id,
        intent="confirm",
        reply_chunks=("Confirmado.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        confirmed_summary_version=1,
        confirmed_action_kinds=(
            CriticalActionKind.INITIATE_PAYMENT,
            CriticalActionKind.RESERVE_LODGING,
        ),
        approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
    )
    review_expected = selection_language == "en"
    initial_confirmation = ModelProposal(
        source_event_id=second_batch.batch_id,
        intent="inform",
        reply_chunks=("How can I help?",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    proposals = [
        ModelProposal(
            source_event_id=BATCH.batch_id,
            intent="inform",
            reply_chunks=(),
            facts=(),
            read_requests=(first_read,),
            effect_proposals=(),
        ),
        selection,
        *((initial_confirmation,) if review_expected else ()),
        confirmation,
        confirmation,
    ]
    store = SQLiteBoundaryStore.open_memory_v8()
    queues = SQLiteBoundaryWorkerStore(store)
    model = FakeAuditedModel(store, proposals)
    profile = FakeProfile(store)
    read_port = FakeLodgingReadPort(store)
    _install_public_authority(store, AUTHORITY)
    _install_public_authority(store, second_authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        profile=profile,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority(
            {
                BATCH.batch_id: AUTHORITY,
                second_batch.batch_id: second_authority,
            }
        ),
        clock=SequenceClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        summary = executor.execute(BATCH)
        confirmed = executor.execute(second_batch)
        replayed = executor.execute(second_batch)

        assert summary.receipt.committed_state_version == 1
        assert confirmed.receipt.committed_state_version == 2
        assert replayed.replayed is True
        assert replayed.receipt == confirmed.receipt
        expected_confirmation_reply = (
            "Perfect — I’ll process your booking now."
            if selection_language == "en"
            else "Perfeito — vou processar sua reserva agora."
        )
        assert confirmed.reply_chunks == (expected_confirmation_reply,)
        assert len(confirmed.receipt.command_rows) == 1
        assert len(confirmed.receipt.relay_rows) == 1
        assert len(model.calls) == (5 if review_expected else 4)
        assert model.calls[0].pending_action is None
        assert model.calls[1].pending_action is None
        pending = model.calls[2].pending_action
        assert pending is not None
        assert pending.public_summary == summary.reply_chunks[0]
        assert pending.action_kinds == (
            CriticalActionKind.INITIATE_PAYMENT,
            CriticalActionKind.RESERVE_LODGING,
        )
        assert model.calls[2].confirmation_review_required is False
        review_index = 3 if review_expected else 2
        followup_index = review_index + 1
        assert model.calls[review_index].pending_action == pending
        assert model.calls[review_index].confirmation_review_required is review_expected
        assert model.calls[followup_index].pending_action == pending
        assert model.calls[followup_index].confirmation_review_required is False
        assert len(read_port.calls) == 2
        derived = read_port.calls[-1]
        assert derived.kind is ReadKind.LODGING
        assert derived.check_in == date(2026, 8, 10)
        assert derived.check_out == date(2026, 8, 12)
        assert derived.adults == 2
        assert derived.children == 0
        assert (
            store._connection.execute(
                "SELECT count(*) FROM boundary_commands"
            ).fetchone()[0]
            == 1
        )
        assert (
            store._connection.execute(
                "SELECT count(*) FROM boundary_command_relays WHERE status='pending'"
            ).fetchone()[0]
            == 1
        )
        relay_json, relay_hash = store._connection.execute(
            "SELECT bundle_json,bundle_hash FROM boundary_command_relays"
        ).fetchone()
        bundle = ReservationRelayBundle.from_canonical_bytes(relay_json.encode())
        assert bundle.artifact_hash == relay_hash
        claim = queues.claim_command_relay(
            worker_id="worker:v2-relay-test",
            now=NOW + timedelta(seconds=2),
            lease_ttl=timedelta(seconds=30),
        )
        assert claim is not None
        assert claim.command_id == confirmed.receipt.command_rows[0][0]
        assert claim.bundle_bytes == relay_json.encode()
        target = SQLiteUnitOfWork.open_v6(tmp_path / "reservation-target.sqlite3")
        try:
            first_receipt = target.accept_boundary_reservation(
                operation_id=claim.target_operation_id,
                source_turn_receipt_hash=claim.source_turn_receipt_hash,
                bundle=bundle,
            )
            replay_claim = queues.claim_command_relay(
                worker_id="worker:v2-relay-recovery",
                now=NOW + timedelta(seconds=33),
                lease_ttl=timedelta(seconds=30),
            )
            assert replay_claim is not None
            assert replay_claim.fencing_token == claim.fencing_token + 1
            replay_receipt = target.accept_boundary_reservation(
                operation_id=replay_claim.target_operation_id,
                source_turn_receipt_hash=replay_claim.source_turn_receipt_hash,
                bundle=ReservationRelayBundle.from_canonical_bytes(
                    replay_claim.bundle_bytes
                ),
            )
            assert replay_receipt == first_receipt
            queues.complete_command_relay(
                replay_claim,
                replay_receipt,
                now=NOW + timedelta(seconds=34),
            )
            assert target.load_command(claim.command_id) is not None
            assert store._connection.execute(
                "SELECT status,claim_count,target_receipt_hash "
                "FROM boundary_command_relays"
            ).fetchone() == (
                "acked",
                2,
                replay_receipt.canonical_hash(),
            )
            with pytest.raises(ConcurrencyConflict, match="completion CAS"):
                queues.complete_command_relay(
                    claim,
                    first_receipt,
                    now=NOW + timedelta(seconds=3),
                )
        finally:
            target.close()
    finally:
        store.close()


def test_handoff_turn_persists_active_guard_and_one_internal_job(tmp_path) -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    queues = SQLiteBoundaryWorkerStore(store)
    model = FakeAuditedModel(
        store,
        [
            ModelProposal(
                source_event_id=BATCH.batch_id,
                intent="request_handoff",
                reply_chunks=("Vou chamar uma pessoa.",),
                facts=(),
                read_requests=(),
                effect_proposals=(),
            )
        ],
    )
    profile = FakeProfile(store)
    _install_public_authority(store)
    executor = _executor(store=store, model=model, profile=profile)
    try:
        result = executor.execute(BATCH)
        replay = executor.execute(BATCH)

        assert result.receipt.internal_outbox_rows
        assert replay.replayed is True
        assert store.load_state(BATCH.lead_id).state.handoff is not None
        assert store._connection.execute(
            "SELECT job_kind,status,count(*) FROM boundary_outbox GROUP BY job_kind,status"
        ).fetchone() == ("handoff_relay", "pending", 1)
        artifact_json, artifact_hash = store._connection.execute(
            "SELECT artifact_json,artifact_hash FROM boundary_outbox"
        ).fetchone()
        bundle = HandoffRelayBundle.from_canonical_bytes(artifact_json.encode())
        assert bundle.artifact_hash == artifact_hash
        reservation_target = SQLiteUnitOfWork.open_v6(
            tmp_path / "unused-reservation-target.sqlite3"
        )
        with SQLiteFollowupUnitOfWork.open_v2(
            tmp_path / "followup-target.sqlite3"
        ) as target:
            worker = BoundaryRelayWorker(
                boundary=queues,
                reservation_target=reservation_target,
                handoff_target=target,
                worker_id="worker:v2-handoff-relay",
                lease_ttl=timedelta(seconds=30),
            )
            relayed = worker.run_once(now=NOW + timedelta(seconds=1))
            assert relayed.disposition is RelayWorkerDisposition.RELAYED
            assert relayed.receipt is not None
            receipt = relayed.receipt
        reservation_target.close()
        assert store._connection.execute(
            "SELECT status,target_receipt_hash FROM boundary_outbox"
        ).fetchone() == ("acked", receipt.canonical_hash())
    finally:
        store.close()


def test_executor_rejects_model_source_identity_before_any_turn_commit() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(
        store,
        [
            ModelProposal(
                source_event_id="batch:other-source",
                intent="inform",
                reply_chunks=("Resposta forjada.",),
                facts=(),
                read_requests=(),
                effect_proposals=(),
            )
        ],
    )
    profile = FakeProfile(store)
    _install_public_authority(store)
    executor = _executor(store=store, model=model, profile=profile)
    try:
        with pytest.raises(TurnExecutionError, match="source event diverged"):
            executor.execute(BATCH)
        assert store.turn_receipt_count(BATCH.batch_id) == 0
        assert store._connection.execute(
            "SELECT state FROM boundary_dispatch_authority WHERE allocation_id=?",
            (AUTHORITY.allocation_ids[0],),
        ).fetchone() == ("available",)
    finally:
        store.close()
