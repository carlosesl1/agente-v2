from __future__ import annotations

import ast
import hashlib
import inspect
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from reservation_boundary import ConversationStage, StringSlot, TypedFact
from reservation_boundary.conversation import ConversationProjection, PublicReplyChunk
from reservation_boundary.effects import HandoffRelayBundle, ReservationRelayBundle
from reservation_boundary.sqlite_store import (
    ConcurrencyConflict,
    DataCorruption,
    SQLiteBoundaryStore,
)
from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from reservation_domain import (
    AwaitingAdjustmentState,
    AwaitingConfirmationState,
)
from v2_application.conversation import V2ConversationReducer
from v2_application.critical_actions import CriticalActionPolicy
from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
from v2_application.public_delivery import (
    BoundaryPublicDeliveryWorker,
    BoundaryPublicDisposition,
)
from v2_application.reads import V2ReadService
from v2_application.relay_worker import BoundaryRelayWorker, RelayWorkerDisposition
from v2_application.turn_executor import (
    _active_execution_guard_proposal,
    _authoritative_phone_locale_projection,
    _collection_only_proposal,
    _critical_confirmation_bound,
    _critical_outcome,
    _critical_model_reads_allowed,
    PublicTurnAuthority,
    TurnExecutionError,
    V2TurnExecutor,
    _confirmation_read_requests,
    _intent,
    _private_update_no_command_proposal,
    _request_public_reply_correction,
    _repair_requested_activity_selection,
    _selection_binding_failure_proposal,
    _structured_selection_review_required,
    _state_model_facts,
)
from v2_contracts.channel import (
    InboundBatch,
    InboundEvent,
    PublicAcceptanceOperation,
    PublicAcceptanceState,
    PublicChannelAcceptance,
    PublicDeliveryUnknown,
    PublicMessageAuthor,
)
from v2_contracts.critical_actions import (
    ApprovalBasis,
    CriticalActionKind,
    PendingCriticalActionContext,
)
from v2_contracts.model import (
    AuditedModelTurn,
    AuditedTranscriptFrame,
    ConversationExchange,
    EffectProposal,
    ModelFact,
    ModelProposal,
    ModelRequest,
    PublicReplyCorrectionReason,
    proposal_requires_progress_review,
)
from v2_contracts.profile import PrivateCustomerBinding
from v2_contracts.passengers import PassengerInput
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

NOW = datetime(2026, 7, 23, 22, 0, tzinfo=timezone.utc)
TRANSCRIPT_KEY = b"t" * 32
CAPABILITY_DIGEST = "a" * 64
EFFECT_DIGEST = "b" * 64
TARGET_DIGEST = "c" * 64


def test_committed_dialogue_reaches_next_turn_and_replay_repairs_private_row(
    tmp_path,
) -> None:
    first_event = replace(
        EVENT,
        event_id="event:dialogue-context-001",
        text="I need a private room for two nights.",
        payload_hash="2" * 64,
    )
    first_batch = InboundBatch(
        batch_id="batch:dialogue-context-001",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(first_event,),
        combined_text=first_event.text,
    )
    second_event = replace(
        EVENT,
        event_id="event:dialogue-context-002",
        text="Can I pay that with Wise?",
        payload_hash="3" * 64,
    )
    second_batch = InboundBatch(
        batch_id="batch:dialogue-context-002",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(second_event,),
        combined_text=second_event.text,
    )
    first_authority = replace(
        AUTHORITY,
        authorization_id="auth:dialogue-context-001",
        allocation_ids=("allocation:dialogue-context-001",),
        allocation_manifest_hash="2" * 64,
    )
    second_authority = replace(
        AUTHORITY,
        authorization_id="auth:dialogue-context-002",
        allocation_ids=("allocation:dialogue-context-002",),
        allocation_manifest_hash="3" * 64,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "dialogue-context.sqlite3"
    )
    model = FakeAuditedModel(
        store,
        [
            ModelProposal(
                source_event_id=first_batch.batch_id,
                intent="inform",
                reply_chunks=("Which dates should I check?",),
                facts=(),
                read_requests=(),
                effect_proposals=(),
            ),
            ModelProposal(
                source_event_id=second_batch.batch_id,
                intent="inform",
                reply_chunks=("Wise is available for eligible agency payments.",),
                facts=(),
                read_requests=(),
                effect_proposals=(),
            ),
        ],
    )
    for authority in (first_authority, second_authority):
        _install_public_authority(store, authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=ForeignPhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority(
            {
                first_batch.batch_id: first_authority,
                second_batch.batch_id: second_authority,
            }
        ),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    try:
        first = executor.execute(first_batch)
        assert private_store.load_recent_dialogue(first_batch.lead_id) == (
            ConversationExchange(first_batch.combined_text, first.reply_chunks),
        )

        private_store._connection.execute(
            "DELETE FROM private_dialogue_turns WHERE lead_id=?",
            (first_batch.lead_id,),
        )
        replay = executor.execute(first_batch)
        assert replay.replayed is True
        assert len(model.calls) == 1
        assert private_store.load_recent_dialogue(first_batch.lead_id) == (
            ConversationExchange(first_batch.combined_text, first.reply_chunks),
        )

        executor.execute(second_batch)

        assert model.calls[1].message == second_batch.combined_text
        assert model.calls[1].recent_dialogue == (
            ConversationExchange(first_batch.combined_text, first.reply_chunks),
        )
    finally:
        private_store.close()
        store.close()


def test_progress_review_gate_is_structural_and_ignores_reply_words() -> None:
    keyword_rich = ModelProposal(
        source_event_id="batch:progress-review-001",
        intent="inform",
        reply_chunks=("availability price reserve package",),
        facts=(ModelFact("language", "en"),),
        read_requests=(),
        effect_proposals=(),
    )
    unrelated = replace(
        keyword_rich,
        reply_chunks=("Completely unrelated prose.",),
    )

    assert proposal_requires_progress_review(keyword_rich) is True
    assert proposal_requires_progress_review(unrelated) is True


def test_explicit_typed_clarification_or_structured_read_skips_progress_review() -> None:
    empty = ModelProposal(
        source_event_id="batch:progress-review-002",
        intent="inform",
        reply_chunks=(
            "I need one detail.",
            "Which check-in date should I use?",
        ),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        clarification_question="Which check-in date should I use?",
    )
    with_read = replace(
        empty,
        clarification_question=None,
        read_requests=(
            ReadRequest(
                request_id="read:progress-review-knowledge-001",
                kind=ReadKind.KNOWLEDGE,
                query="payment options",
                locale="en",
            ),
        ),
    )

    assert proposal_requires_progress_review(empty) is False
    assert proposal_requires_progress_review(with_read) is False


def test_task5_structural_guards_never_replace_maya_chunks_or_typed_question() -> None:
    question = "Maya sentinel: qual opção deve permanecer?"
    selected = ModelProposal(
        source_event_id="batch:task5-structural-guards",
        intent="select",
        reply_chunks=("Maya sentinel: contexto exato.", question),
        clarification_question=question,
        facts=(ModelFact("service", "hostel"),),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "7" * 64,
    )

    selection_guard = _selection_binding_failure_proposal(selected, locale="pt-BR")
    active_guard = _active_execution_guard_proposal(selected, locale="pt-BR")
    collection_guard = _collection_only_proposal(
        selected,
        public_facts=(ModelFact("service", "hostel"),),
        locale="pt-BR",
        invalid_fact_names=("full_name",),
    )
    private_guard = _private_update_no_command_proposal(
        replace(selected, intent="inform", target_offer_id=None),
        pending_action=None,
        locale="pt-BR",
    )

    for guarded in (
        selection_guard,
        active_guard,
        collection_guard,
        private_guard,
    ):
        assert guarded.reply_chunks == selected.reply_chunks
        assert guarded.clarification_question == selected.clarification_question
        assert guarded.read_requests == ()
        assert guarded.effect_proposals == ()
        assert guarded.target_offer_id is None
        assert guarded.target_offer_ids == ()
        assert guarded.selection_requested is False


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


def test_phone_locale_authority_removes_a_stale_model_language_fact() -> None:
    projection = ConversationProjection(
        stage=ConversationStage.RECEPTIONIST,
        desired_services=(),
        locale="pt-BR",
        facts=(
            TypedFact("language", StringSlot("pt-BR"), "d" * 64),
            TypedFact("service", StringSlot("hostel"), "e" * 64),
        ),
        reservation_execution_projection=None,
    )

    authoritative = _authoritative_phone_locale_projection(projection, "en")

    assert authoritative.locale == "en"
    assert tuple(fact.name for fact in authoritative.facts) == ("service",)


def test_parent_does_not_promote_birth_or_gender_from_raw_customer_text(
    tmp_path,
) -> None:
    message = "Nasci em 14/04/1990 e sou mulher."
    event = replace(
        EVENT,
        event_id="event:no-parent-private-extraction",
        text=message,
        payload_hash="9" * 64,
    )
    batch = InboundBatch(
        batch_id="batch:no-parent-private-extraction",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(event,),
        combined_text=message,
    )
    authority = replace(
        AUTHORITY,
        authorization_id="auth:no-parent-private-extraction",
        allocation_ids=("allocation:no-parent-private-extraction",),
        allocation_manifest_hash="9" * 64,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "no-parent-private-extraction.sqlite3"
    )
    model = FakeAuditedModel(
        store,
        [
            ModelProposal(
                source_event_id=batch.batch_id,
                intent="inform",
                reply_chunks=("Como posso continuar ajudando?",),
                facts=(),
                read_requests=(),
                effect_proposals=(),
            )
        ],
    )
    _install_public_authority(store, authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority({batch.batch_id: authority}),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        executor.execute(batch)
        projection = store.load_latest_conversation_projection(batch.lead_id)

        assert projection is not None
        assert not {"birth_date", "gender"}.intersection(
            fact.name for fact in projection.facts
        )
    finally:
        private_store.close()
        store.close()


def test_parent_does_not_reinterpret_personal_date_as_commercial_fact(
    tmp_path,
) -> None:
    message = "Para o 4Ps, meu nascimento é 12/05/1991 e sou mulher."
    event = replace(
        EVENT,
        event_id="event:model-owned-personal-date",
        text=message,
        payload_hash="8" * 64,
    )
    batch = InboundBatch(
        batch_id="batch:model-owned-personal-date",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(event,),
        combined_text=message,
    )
    authority = replace(
        AUTHORITY,
        authorization_id="auth:model-owned-personal-date",
        allocation_ids=("allocation:model-owned-personal-date",),
        allocation_manifest_hash="8" * 64,
    )
    proposal = ModelProposal(
        source_event_id=batch.batch_id,
        intent="inform",
        reply_chunks=("Obrigado. Registrei seus dados para continuar.",),
        facts=(
            ModelFact("service", "agency"),
            ModelFact("product_id", "product:tour-4ps"),
            ModelFact("activity_date", date(2026, 12, 3)),
            ModelFact("adults", 1),
            ModelFact("children", 0),
            ModelFact("birth_date", date(1991, 5, 12)),
            ModelFact("gender", "f"),
        ),
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "model-owned-personal-date.sqlite3"
    )
    model = FakeAuditedModel(store, [proposal])
    _install_public_authority(store, authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority({batch.batch_id: authority}),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    try:
        executor.execute(batch)

        projection = store.load_latest_conversation_projection(batch.lead_id)
        assert projection is not None
        values = {fact.name: fact.value.value for fact in projection.facts}
        private_snapshot = private_store.load(batch.lead_id)
        artifact_json = "\n".join(
            row[0]
            for row in store._connection.execute(
                "SELECT artifact_json FROM boundary_turn_artifacts"
            ).fetchall()
        )
        assert values["activity_date"] == date(2026, 12, 3)
        assert "birth_date" not in values
        assert "gender" not in values
        assert private_snapshot.birth_date == date(1991, 5, 12)
        assert private_snapshot.gender == "f"
        assert "1991-05-12" not in artifact_json
    finally:
        private_store.close()
        store.close()


def test_turn_executor_has_no_raw_text_semantic_extractors() -> None:
    import v2_application.turn_executor as turn_executor

    for name in (
        "_extract_explicit_commercial_facts",
        "_explicit_birth_date_candidates",
        "_explicit_commercial_read_requested",
        "_explicit_summary_preparation_requested",
        "_merge_explicit_customer_facts",
    ):
        assert not hasattr(turn_executor, name), name


def test_package_intent_closure_commits_both_targets_without_selecting_first() -> None:
    targets = ("offer:lodging-public", "offer:activity-public")

    def proposal(order: tuple[str, str]) -> ModelProposal:
        return ModelProposal(
            source_event_id="event:package-intent-closure",
            intent="select",
            reply_chunks=("Encontrei as duas opções.",),
            facts=(
                ModelFact("language", "pt-BR"),
                ModelFact("service", "package"),
                ModelFact("start_date", date(2026, 8, 10)),
                ModelFact("end_date", date(2026, 8, 12)),
                ModelFact("activity_date", date(2026, 8, 11)),
                ModelFact("adults", 1),
                ModelFact("children", 0),
                ModelFact("payment_method", "stripe"),
            ),
            read_requests=(),
            effect_proposals=(),
            target_offer_ids=order,
        )

    expected = "package-selection:" + hashlib.sha256(
        b"v2-package-selection-v1\x00"
        + b"\x00".join(item.encode("utf-8") for item in sorted(targets))
    ).hexdigest()
    forward = _intent(proposal(targets))
    reverse = _intent(proposal(tuple(reversed(targets))))

    assert forward.selection == expected
    assert reverse.selection == expected
    assert forward.selection not in targets


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
    assert _structured_selection_review_required(
        state_facts[:-1],
        payment,
        private_profile_complete=True,
    )

    package_facts = (
        ModelFact("service", "package"),
        ModelFact("product_id", "product:tour-4ps"),
        ModelFact("start_date", date(2026, 12, 16)),
        ModelFact("end_date", date(2026, 12, 18)),
        ModelFact("activity_date", date(2026, 12, 17)),
        ModelFact("adults", 1),
        ModelFact("children", 0),
    )
    assert _structured_selection_review_required(
        package_facts,
        payment,
        private_profile_complete=True,
        passenger_manifest_complete=True,
    )
    assert _structured_selection_review_required(
        package_facts,
        payment,
        private_profile_complete=True,
        passenger_manifest_complete=False,
    )
    assert _structured_selection_review_required(
        (
            *package_facts,
            ModelFact("birth_date", date(1988, 6, 18)),
            ModelFact("gender", "m"),
        ),
        payment,
        private_profile_complete=True,
        passenger_manifest_complete=False,
    )
    assert _structured_selection_review_required(
        (*package_facts, *payment),
        (),
        private_profile_complete=True,
        passenger_manifest_complete=False,
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
            "adults": 1,
            "children": 0,
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


class AuthenticatedManyChatContactWithoutCountry:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store
        self.calls = 0

    def read(self, lead_id: str, *, now: datetime) -> PrivateCustomerBinding:
        assert self.store._connection.in_transaction is False
        self.calls += 1
        return PrivateCustomerBinding(
            binding_id="profile-binding:" + "c" * 64,
            content_hash="b" * 64,
            full_name="Pessoa Teste",
            email="maya.cloudbeds.canary@example.com",
            phone_e164="+12025550123",
            country_code=None,
            observed_at=now,
            expires_at=now + timedelta(minutes=5),
            complete=False,
        )


class PhoneOnlyManyChatContact:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store
        self.calls = 0

    def read(self, lead_id: str, *, now: datetime) -> PrivateCustomerBinding:
        assert self.store._connection.in_transaction is False
        self.calls += 1
        return PrivateCustomerBinding(
            binding_id="profile-binding:" + "9" * 64,
            content_hash="8" * 64,
            full_name=None,
            email=None,
            phone_e164="".join(("+55", "75", "99999", "0199")),
            country_code=None,
            observed_at=now,
            expires_at=now + timedelta(minutes=5),
            complete=False,
        )


class ForeignPhoneOnlyManyChatContact(PhoneOnlyManyChatContact):
    def read(self, lead_id: str, *, now: datetime) -> PrivateCustomerBinding:
        value = super().read(lead_id, now=now)
        return replace(value, phone_e164="".join(("+1", "202", "555", "0199")))


class UnusableManyChatPhone:
    def __init__(self, store: SQLiteBoundaryStore, mode: str) -> None:
        self.store = store
        self.mode = mode

    def read(self, lead_id: str, *, now: datetime) -> PrivateCustomerBinding:
        assert self.store._connection.in_transaction is False
        observed_at = now
        expires_at = now + timedelta(minutes=5)
        phone = None
        if self.mode == "future":
            observed_at = now + timedelta(minutes=1)
            expires_at = now + timedelta(minutes=6)
            phone = "".join(("+1", "202", "555", "0198"))
        elif self.mode == "expired":
            observed_at = now - timedelta(minutes=6)
            expires_at = now
            phone = "".join(("+1", "202", "555", "0198"))
        elif self.mode != "missing":
            raise AssertionError("unknown unusable phone mode")
        return PrivateCustomerBinding(
            binding_id=f"profile-binding:unusable-phone:{self.mode}",
            content_hash="9" * 64,
            full_name=None,
            email=None,
            phone_e164=phone,
            country_code=None,
            observed_at=observed_at,
            expires_at=expires_at,
            complete=False,
        )


class RecordingPublicDelivery:
    def __init__(self, *, uncertain: bool = False) -> None:
        self.uncertain = uncertain
        self.calls = []

    def send(self, claim):
        self.calls.append(claim)
        if self.uncertain:
            raise PublicDeliveryUnknown("provider response lost after call")
        return PublicChannelAcceptance(
            state=PublicAcceptanceState.ACCEPTED_BY_MANYCHAT,
            operations=(PublicAcceptanceOperation.TRIGGER_FLOW,),
            provider_request_ids=("manychat:request:turn-executor-001",),
            dispatch_correlation_ids=(
                "manychat-correlation:turn-executor-001",
            ),
        )


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


def _with_authenticated_system_slot(
    authority: PublicTurnAuthority,
) -> PublicTurnAuthority:
    allocation_id = f"{authority.allocation_ids[-1]}-authenticated-system"
    return replace(
        authority,
        allocation_ids=authority.allocation_ids + (allocation_id,),
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
        assert chunk_count in (1, 2)
        assert now == NOW
        return (
            AUTHORITY
            if chunk_count == 1
            else _with_authenticated_system_slot(AUTHORITY)
        )


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
        if chunk_count == len(value.allocation_ids) + 1:
            value = _with_authenticated_system_slot(value)
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


class ManyOptionLodgingReadPort(FakeLodgingReadPort):
    def read(self, request: ReadRequest) -> ReadObservation:
        assert self.store._connection.in_transaction is False
        self.calls.append(request)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="cloudbeds",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            public_payload={
                "options": [
                    {
                        "offer_id": f"offer:{index:064x}",
                        "room_public_name": f"Quarto Privativo {index:02d}",
                        "check_in": "2026-09-12",
                        "check_out": "2026-09-15",
                        "adults": 2,
                        "children": 0,
                        "total_amount": f"{450 + index}.00",
                        "currency": "BRL",
                        "available": True,
                        "available_units": 1,
                    }
                    for index in range(1, 34)
                ]
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
        adults, children = request.activity_party()
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
                "adults": adults,
                "children": children,
                "participants": adults + children,
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


class OneCommitConflictStore:
    def __init__(self, inner: SQLiteBoundaryStore) -> None:
        self.inner = inner
        self.conflicts = 0

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def commit_turn_v8(self, **values):
        if self.conflicts == 0:
            self.conflicts += 1
            raise ConcurrencyConflict("injected aggregate commit conflict")
        return self.inner.commit_turn_v8(**values)


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
    authority_with_system_slot = _with_authenticated_system_slot(authority)
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
        for ordinal, allocation_id in enumerate(
            authority_with_system_slot.allocation_ids
        ):
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
    public_authority=None,
    private_customer_facts: SQLitePrivateCustomerFactStore | None = None,
) -> V2TurnExecutor:
    return V2TurnExecutor(
        store=store,
        model=model,
        reads=reads or V2ReadService({}),
        profile=profile,
        private_customer_facts=(
            private_customer_facts
            if private_customer_facts is not None
            else SQLitePrivateCustomerFactStore.open_memory()
        ),
        reducer=_enabled_reducer(),
        public_authority=public_authority or FixedAuthority(),
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
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
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
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
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
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
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


def test_boundary_public_worker_fences_then_persists_acceptance_receipt() -> None:
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
        accepted = worker.run_once(now=NOW + timedelta(seconds=1))
        idle = worker.run_once(now=NOW + timedelta(seconds=2))
        assert accepted is BoundaryPublicDisposition.ACCEPTED
        assert idle is BoundaryPublicDisposition.IDLE
        assert len(delivery.calls) == 1
        assert store._connection.execute(
            "SELECT status,dispatch_slots_consumed,delivery_receipt_hash "
            "FROM boundary_public_outbox"
        ).fetchone()[0:2] == ("delivered", 1)
        receipt_json = store._connection.execute(
            "SELECT delivery_receipt_json FROM boundary_public_outbox"
        ).fetchone()[0]
        receipt = json.loads(receipt_json)
        assert receipt["schema"] == "phase8-public-acceptance-receipt"
        assert receipt["data"]["acceptance"]["data"]["state"] == (
            "accepted_by_manychat"
        )
        assert "delivered_at" not in receipt_json
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
        acceptance = delivery.send(claim)
        assert acceptance.state is PublicAcceptanceState.ACCEPTED_BY_MANYCHAT
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
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
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
    final = _proposal("I’m here to help. What would you like to check next?")
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
        assert result.reply_chunks == (
            "I’m here to help. What would you like to check next?",
        )
        row = store._connection.execute(
            "SELECT artifact_kind,frame_sequence,frame_reference "
            "FROM boundary_turn_artifacts WHERE artifact_kind='read_observation'"
        ).fetchone()
        assert row[0] == "read_observation"
        assert row[1] is None
        assert type(row[2]) is str and len(row[2]) == 64
    finally:
        store.close()


def test_parent_does_not_invent_read_when_model_omits_it() -> None:
    message = (
        "I need a private room from September 10 to September 12, 2026, for one "
        "adult. Do you have availability? Please do not book anything."
    )
    event = replace(
        EVENT,
        event_id="event:model-owned-read-intent",
        text=message,
        payload_hash="c" * 64,
    )
    batch = InboundBatch(
        batch_id="batch:model-owned-read-intent",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(event,),
        combined_text=message,
    )
    authority = replace(
        AUTHORITY,
        authorization_id="auth:model-owned-read-intent",
        allocation_ids=("allocation:model-owned-read-intent",),
        allocation_manifest_hash="c" * 64,
    )
    proposal = ModelProposal(
        source_event_id=batch.batch_id,
        intent="inform",
        reply_chunks=("Tell me if you want me to check current availability.",),
        facts=(
            ModelFact("language", "en"),
            ModelFact("service", "hostel"),
            ModelFact("start_date", date(2026, 9, 10)),
            ModelFact("end_date", date(2026, 9, 12)),
            ModelFact("adults", 1),
            ModelFact("children", 0),
        ),
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [proposal])
    port = FakeLodgingReadPort(store)
    _install_public_authority(store, authority)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.LODGING: port}),
        public_authority=MappingAuthority({batch.batch_id: authority}),
    )
    try:
        result = executor.execute(batch)

        assert port.calls == []
        assert len(model.calls) == 1
        assert result.receipt.read_observations == ()
    finally:
        store.close()


def test_ambiguous_holder_does_not_block_complete_commercial_read(tmp_path) -> None:
    message = (
        "Vou viajar com minha amiga Laura Pessoa Teste, e o e-mail dela é "
        "laura.pessoa@example.invalid. Ainda não decidi em nome de quem faremos "
        "a reserva. Quero só saber se tem quarto para 2 adultos de 10/08/2026 "
        "a 12/08/2026."
    )
    event = replace(
        EVENT,
        event_id="event:ambiguous-holder-read",
        text=message,
        payload_hash="b" * 64,
    )
    batch = InboundBatch(
        batch_id="batch:ambiguous-holder-read",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(event,),
        combined_text=message,
    )
    authority = replace(
        AUTHORITY,
        authorization_id="auth:ambiguous-holder-read",
        allocation_ids=("allocation:ambiguous-holder-read",),
        allocation_manifest_hash="b" * 64,
    )
    requested_read = ReadRequest(
        request_id="read:ambiguous-holder-lodging",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    first = ModelProposal(
        source_event_id=batch.batch_id,
        intent="inform",
        reply_chunks=("Antes de consultar, preciso saber quem será o titular.",),
        facts=(
            ModelFact("service", "hostel"),
            ModelFact("start_date", date(2026, 8, 10)),
            ModelFact("end_date", date(2026, 8, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
        ),
        read_requests=(requested_read,),
        effect_proposals=(),
    )
    final = ModelProposal(
        source_event_id=batch.batch_id,
        intent="inform",
        reply_chunks=("Há disponibilidade; antes de reservar, confirmaremos o titular.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(tmp_path / "ambiguous-holder.sqlite3")
    model = FakeAuditedModel(store, [first, final])
    port = FakeLodgingReadPort(store)
    _install_public_authority(store, authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: port}),
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority({batch.batch_id: authority}),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    try:
        result = executor.execute(batch)

        assert len(port.calls) == 1
        assert len(model.calls) == 2
        assert private_store.load(batch.lead_id).present_fact_names == ()
        public_text = " ".join(result.reply_chunks)
        assert public_text == (
            "Há disponibilidade; antes de reservar, confirmaremos o titular."
        )
        assert "Laura Pessoa Teste" not in public_text
        assert "laura.pessoa@example.invalid" not in public_text
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
    finally:
        private_store.close()
        store.close()


def test_selection_without_read_derives_fresh_read_instead_of_reducer_error() -> None:
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar o resumo.",),
        facts=(
            ModelFact("service", "hostel"),
            ModelFact("start_date", date(2026, 8, 10)),
            ModelFact("end_date", date(2026, 8, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", "pix"),
        ),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "7" * 64,
    )
    selected = replace(first, read_requests=())
    authority = replace(
        AUTHORITY,
        authorization_id="auth:selection-summary",
        allocation_ids=(
            "allocation:selection-summary-maya",
            "allocation:selection-summary-system",
        ),
        allocation_manifest_hash="8" * 64,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [first, selected])
    port = FakeLodgingReadPort(store)
    _install_public_authority(store, authority)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.LODGING: port}),
        public_authority=MappingAuthority({BATCH.batch_id: authority}),
    )
    try:
        result = executor.execute(BATCH)

        assert len(port.calls) == 1
        assert len(model.calls) == 2
        assert isinstance(store.load_state(BATCH.lead_id).state.workflow, AwaitingConfirmationState)
        assert result.reply_chunks[0] == "Vou preparar o resumo."
        assert result.reply_chunks[1].startswith("Só para confirmar:")
        committed_chunks = tuple(
            PublicReplyChunk.from_canonical_bytes(row[2])
            for row in result.receipt.public_chunks
        )
        assert tuple(chunk.author for chunk in committed_chunks) == (
            PublicMessageAuthor.MAYA,
            PublicMessageAuthor.AUTHENTICATED_SYSTEM,
        )
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
    finally:
        store.close()


def test_incomplete_selection_without_read_fails_closed_without_reducer_error() -> None:
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar essa opção.",),
        facts=(ModelFact("service", "hostel"),),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "7" * 64,
    )
    corrected_text = "Maya corrigiu: preciso das datas e da ocupação para consultar."
    corrected = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(corrected_text,),
        facts=first.facts,
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [first, corrected])
    port = FakeLodgingReadPort(store)
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.LODGING: port}),
    )
    try:
        result = executor.execute(BATCH)

        assert port.calls == []
        assert result.reply_chunks == (corrected_text,)
        assert len(model.calls) == 2
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.READ_REMOVED_BY_AUTHORITY,
        )
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert store.load_state(BATCH.lead_id).state.workflow is None
    finally:
        store.close()


def test_final_selection_with_unbound_offer_fails_closed_after_fresh_read() -> None:
    request = ReadRequest(
        request_id="read:unbound-final-selection",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    facts = (
        ModelFact("service", "hostel"),
        ModelFact("start_date", date(2026, 8, 10)),
        ModelFact("end_date", date(2026, 8, 12)),
        ModelFact("adults", 2),
        ModelFact("children", 0),
        ModelFact("payment_method", "wise"),
    )
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Vou consultar.",),
        facts=facts,
        read_requests=(request,),
        effect_proposals=(),
    )
    final = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar essa opção.",),
        facts=facts,
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "8" * 64,
    )
    corrected_text = "Maya corrigiu: não consegui vincular essa opção; nada foi reservado."
    corrected = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(corrected_text,),
        facts=facts,
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [first, final, corrected])
    port = FakeLodgingReadPort(store)
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.LODGING: port}),
    )
    try:
        result = executor.execute(BATCH)

        assert len(port.calls) == 1
        assert result.reply_chunks == (corrected_text,)
        assert len(model.calls) == 3
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.SELECTION_BINDING_FAILURE,
        )
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert not isinstance(
            store.load_state(BATCH.lead_id).state.workflow,
            AwaitingConfirmationState,
        )
    finally:
        store.close()


def test_fresh_equivalent_history_suppresses_redundant_informational_read() -> None:
    first_read = ReadRequest(
        request_id="read:history-reuse-first",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    followup_event = replace(
        EVENT,
        event_id="event:history-reuse-followup",
        text="Gostei. O que você precisa para eu reservar depois?",
        payload_hash="c" * 64,
    )
    followup_batch = InboundBatch(
        batch_id="batch:history-reuse-followup",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(followup_event,),
        combined_text=followup_event.text,
    )
    followup_authority = replace(
        AUTHORITY,
        authorization_id="auth:history-reuse-followup",
        allocation_ids=("allocation:history-reuse-followup",),
        allocation_manifest_hash="c" * 64,
    )
    repeated_read = replace(first_read, request_id="read:history-reuse-repeated")
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Vou consultar.",),
        facts=(),
        read_requests=(first_read,),
        effect_proposals=(),
    )
    first_final = _proposal("A suíte está disponível por BRL 480.00.")
    repeated = ModelProposal(
        source_event_id=followup_batch.batch_id,
        intent="inform",
        reply_chunks=("Vou consultar a mesma disponibilidade novamente.",),
        facts=(),
        read_requests=(repeated_read,),
        effect_proposals=(),
    )
    invalid_reused_reply = ModelProposal(
        source_event_id=followup_batch.batch_id,
        intent="inform",
        reply_chunks=("Vou alterar o pagamento durante a recapitulação.",),
        facts=(ModelFact("payment_method", "pix"),),
        read_requests=(),
        effect_proposals=(),
    )
    corrected_text = "Maya corrigiu: a consulta anterior continua fresca e nada foi reservado."
    corrected_reuse = ModelProposal(
        source_event_id=followup_batch.batch_id,
        intent="inform",
        reply_chunks=(corrected_text,),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(
        store,
        [first, first_final, repeated, invalid_reused_reply, corrected_reuse],
    )
    port = FakeLodgingReadPort(store)
    _install_public_authority(store)
    _install_public_authority(store, followup_authority)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.LODGING: port}),
        public_authority=MappingAuthority(
            {
                BATCH.batch_id: AUTHORITY,
                followup_batch.batch_id: followup_authority,
            }
        ),
    )
    try:
        executor.execute(BATCH)
        followup = executor.execute(followup_batch)

        assert port.calls == [first_read]
        assert len(model.calls) == 5
        assert model.calls[-1].observations == ()
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.STALE_CONSULTATION_REUSE,
        )
        assert followup.reply_chunks == (corrected_text,)
        assert followup.receipt.command_rows == ()
        assert followup.receipt.relay_rows == ()
    finally:
        store.close()


@pytest.mark.parametrize("expire_on_model_call", (1, 2))
def test_consultation_expiry_during_model_is_refreshed_or_fails_closed(
    expire_on_model_call: int,
) -> None:
    class MutableClock:
        def __init__(self) -> None:
            self.value = NOW

        def now(self) -> datetime:
            return self.value

    class CurrentTimeLodgingPort:
        def __init__(self, store: SQLiteBoundaryStore, clock: MutableClock) -> None:
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

    first_read = ReadRequest(
        request_id="read:history-expiry-first",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    repeated_read = replace(first_read, request_id="read:history-expiry-repeated")
    followup_event = replace(
        EVENT,
        event_id="event:history-expiry-followup",
        text="Pode conferir de novo essa mesma disponibilidade?",
        payload_hash="d" * 64,
    )
    followup_batch = InboundBatch(
        batch_id="batch:history-expiry-followup",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(followup_event,),
        combined_text=followup_event.text,
    )
    first_authority = replace(AUTHORITY, deadline_at=NOW + timedelta(minutes=30))
    followup_authority = replace(
        first_authority,
        authorization_id="auth:history-expiry-followup",
        allocation_ids=("allocation:history-expiry-followup",),
        allocation_manifest_hash="d" * 64,
    )
    proposals = [
        ModelProposal(
            source_event_id=BATCH.batch_id,
            intent="inform",
            reply_chunks=("Vou consultar.",),
            facts=(),
            read_requests=(first_read,),
            effect_proposals=(),
        ),
        _proposal("A suíte está disponível por BRL 480.00."),
        ModelProposal(
            source_event_id=followup_batch.batch_id,
            intent="inform",
            reply_chunks=("Vou conferir novamente.",),
            facts=(),
            read_requests=(repeated_read,),
            effect_proposals=(),
        ),
        replace(
            _proposal("Atualizei: a suíte continua disponível por BRL 480.00."),
            source_event_id=followup_batch.batch_id,
        ),
    ]
    store = SQLiteBoundaryStore.open_memory_v8()
    clock = MutableClock()
    model = FakeAuditedModel(store, proposals)
    initial_port = FakeLodgingReadPort(store)
    current_port = CurrentTimeLodgingPort(store, clock)
    for authority in (first_authority, followup_authority):
        _install_public_authority(store, authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: initial_port}),
        profile=FakeProfile(store),
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority(
            {
                BATCH.batch_id: first_authority,
                followup_batch.batch_id: followup_authority,
            }
        ),
        clock=clock,
        locale="pt-BR",
        turn_timeout=timedelta(minutes=10),
        max_commit_attempts=1,
    )
    try:
        executor.execute(BATCH)
        executor._reads = V2ReadService({ReadKind.LODGING: current_port})

        model_call_count = 0

        def expire_during_model() -> None:
            nonlocal model_call_count
            model_call_count += 1
            if model_call_count == expire_on_model_call:
                clock.value = NOW + timedelta(minutes=6)
                model.on_call = None

        model.on_call = expire_during_model
        if expire_on_model_call == 1:
            result = executor.execute(followup_batch)

            assert current_port.calls == [repeated_read]
            assert model.calls[-1].recap_reuse_required is False
            assert len(model.calls[-1].observations) == 1
            assert result.reply_chunks == (
                "Atualizei: a suíte continua disponível por BRL 480.00.",
            )
            assert result.receipt.command_rows == ()
            assert result.receipt.relay_rows == ()
        else:
            with pytest.raises(
                TurnExecutionError,
                match="consultation expired before recap decision",
            ):
                executor.execute(followup_batch)

            assert current_port.calls == []
            assert model.calls[-1].recap_reuse_required is True
            assert model.calls[-1].observations == ()
            assert store.turn_receipt_count(followup_batch.batch_id) == 0
        assert initial_port.calls == [first_read]
    finally:
        store.close()


def test_private_update_with_positive_read_keeps_commercial_reply_without_private_echo(
    tmp_path,
) -> None:
    private_name = "Pessoa Consulta Privada"
    private_email = "consulta.privada@example.invalid"
    request = ReadRequest(
        request_id="read:private-update-commercial-reply",
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
        facts=(
            ModelFact("full_name", private_name),
            ModelFact("email", private_email),
            ModelFact("country_code", "BR"),
        ),
        read_requests=(request,),
        effect_proposals=(),
    )
    followup = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(f"{private_name}, encontrei uma suíte disponível.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    corrected_text = "Encontrei uma suíte disponível."
    corrected = replace(followup, reply_chunks=(corrected_text,))
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(tmp_path / "private-reply.sqlite3")
    model = FakeAuditedModel(store, [first, followup, corrected])
    port = FakeLodgingReadPort(store)
    _install_public_authority(store)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: port}),
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    try:
        result = executor.execute(BATCH)

        public_text = " ".join(result.reply_chunks)
        assert result.reply_chunks == (corrected_text,)
        assert len(model.calls) == 3
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
        assert private_name not in public_text
        assert private_email not in public_text
        assert "Guardei esses dados" not in public_text
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
    finally:
        private_store.close()
        store.close()


def test_committed_positive_and_negative_reads_reach_the_next_turn_as_recap_only_history() -> None:
    lodging = ReadRequest(
        request_id="read:history-lodging",
        kind=ReadKind.LODGING,
        check_in=date(2026, 9, 12),
        check_out=date(2026, 9, 15),
        adults=2,
        children=0,
    )
    activity = ReadRequest(
        request_id="read:history-activity",
        kind=ReadKind.ACTIVITY,
        product_id="product:tour-4ps",
        activity_date=date(2026, 9, 13),
        adults=2,
        children=0,
    )

    class NegativeActivityReadPort:
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
                    "product_id": "product:tour-4ps",
                    "product_public_name": "Roteiro dos 4Ps",
                    "activity_date": "2026-09-13",
                    "adults": 2,
                    "children": 0,
                    "available": False,
                },
                private_binding_hash="4" * 64,
            )

    first_proposal = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Vou consultar hospedagem e passeio.",),
        facts=(),
        read_requests=(lodging, activity),
        effect_proposals=(),
    )
    first_final = _proposal(
        "Há uma suíte disponível por BRL 480.00; o Roteiro dos 4Ps está indisponível."
    )
    recap_event = replace(
        EVENT,
        event_id="event:consultation-history-recap",
        text="Resuma tudo sem reservar.",
        occurred_at=NOW + timedelta(seconds=1),
        payload_hash="4" * 64,
    )
    recap_batch = InboundBatch(
        batch_id="batch:consultation-history-recap",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(recap_event,),
        combined_text=recap_event.text,
    )
    recap_final = ModelProposal(
        source_event_id=recap_batch.batch_id,
        intent="inform",
        reply_chunks=(
            "A suíte consultada estava disponível por BRL 480.00 e o Roteiro dos "
            "4Ps estava indisponível; nada foi reservado.",
        ),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    recap_authority = replace(
        AUTHORITY,
        authorization_id="auth:consultation-history-recap",
        allocation_ids=("allocation:consultation-history-recap",),
        allocation_manifest_hash="4" * 64,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [first_proposal, first_final, recap_final])
    lodging_port = ManyOptionLodgingReadPort(store)
    activity_port = NegativeActivityReadPort(store)
    _install_public_authority(store)
    _install_public_authority(store, recap_authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService(
            {
                ReadKind.LODGING: lodging_port,
                ReadKind.ACTIVITY: activity_port,
            }
        ),
        profile=FakeProfile(store),
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority(
            {
                BATCH.batch_id: AUTHORITY,
                recap_batch.batch_id: recap_authority,
            }
        ),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        first_result = executor.execute(BATCH)
        recap_result = executor.execute(recap_batch)

        assert len(first_result.receipt.read_observations) == 2
        assert len(model.calls) == 3
        recap_request = model.calls[2]
        assert recap_request.observations == ()
        assert len(recap_request.consultation_history) == 2
        history = {
            item.public_context["service"]: item
            for item in recap_request.consultation_history
        }
        lodging_history = history["lodging"]
        assert lodging_history.public_context["status"] == "positive"
        assert lodging_history.public_context["query"] == {
            "check_in": "2026-09-12",
            "check_out": "2026-09-15",
            "adults": 2,
            "children": 0,
        }
        assert lodging_history.public_context["offer_count"] == 33
        assert lodging_history.public_context["offers_truncated"] is True
        assert len(lodging_history.public_context["offers"]) == 32
        assert lodging_history.public_context["offers"][0]["public_label"] == (
            "Quarto Privativo 01"
        )
        assert lodging_history.public_context["offers"][-1]["public_label"] == (
            "Quarto Privativo 32"
        )
        activity_history = history["activity"]
        assert activity_history.public_context["status"] == "negative"
        assert activity_history.public_context["query"] == {
            "product_id": "product:tour-4ps",
            "activity_date": "2026-09-13",
            "adults": 2,
            "children": 0,
        }
        assert activity_history.public_context["offers"] == []
        assert activity_history.public_context["offer_count"] == 0
        assert activity_history.public_context["offers_truncated"] is False
        assert all(item.fresh_at_turn_start for item in history.values())
        assert store.load_recent_public_lookup_observations(
            "manychat:another-lead"
        ) == ()
        assert recap_result.reply_chunks == recap_final.reply_chunks
        assert recap_result.receipt.read_observations == ()
        assert recap_result.receipt.command_rows == ()
        assert recap_result.receipt.relay_rows == ()

        store._connection.execute(
            "UPDATE boundary_turn_artifacts SET artifact_json='{}' "
            "WHERE artifact_kind='read_observation'"
        )
        with pytest.raises(DataCorruption, match="public lookup history"):
            store.load_recent_public_lookup_observations(BATCH.lead_id)
    finally:
        store.close()


def test_maya_private_facts_are_durable_and_absent_from_public_artifacts(
    tmp_path,
) -> None:
    private_name = "Pessoa Privada Silva"
    private_email = "private.person@example.invalid"
    private_country = "BR"
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "private-customer.sqlite3"
    )
    proposal = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(
            f"Dados do titular: {private_name}, {private_email}, {private_country}.",
        ),
        facts=(
            ModelFact("full_name", private_name),
            ModelFact("email", private_email),
            ModelFact("country_code", private_country),
        ),
        read_requests=(),
        effect_proposals=(),
    )
    corrected_text = "Maya corrigiu a resposta sem expor os dados privados."
    corrected = replace(
        proposal,
        reply_chunks=(corrected_text,),
        facts=(),
    )
    model = FakeAuditedModel(store, [proposal, corrected])
    _install_public_authority(store)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        result = executor.execute(BATCH)
        snapshot = private_store.load(BATCH.lead_id)
        projection = store.load_latest_conversation_projection(BATCH.lead_id)
        artifact_json = "\n".join(
            row[0]
            for row in store._connection.execute(
                "SELECT artifact_json FROM boundary_turn_artifacts ORDER BY artifact_index"
            ).fetchall()
        )

        assert result.reply_chunks == (corrected_text,)
        assert len(model.calls) == 2
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert snapshot.full_name == private_name
        assert snapshot.email == private_email
        assert snapshot.country_code == private_country
        assert projection is not None
        assert not {
            "full_name",
            "email",
            "phone_e164",
            "country_code",
        } & {item.name for item in projection.facts}
        public_bytes = "\n".join(
            (
                repr(result),
                repr(proposal),
                projection.to_canonical_bytes().decode(),
                artifact_json,
            )
        )
        for private_value in (private_name, private_email):
            assert private_value not in public_bytes
        assert '"country_code"' not in projection.to_canonical_bytes().decode()
        assert '"country_code"' not in artifact_json
    finally:
        private_store.close()
        store.close()


def test_private_holder_update_preserves_exact_model_owned_clarification(
    tmp_path,
) -> None:
    question = "Haverá alguma criança no grupo?"
    batch = replace(BATCH, batch_id="event:holder-clarification")
    authority = replace(
        AUTHORITY,
        authorization_id="auth:holder-clarification",
        allocation_ids=("allocation:holder-clarification",),
        allocation_manifest_hash="1" * 64,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "holder-clarification.sqlite3"
    )
    model = FakeAuditedModel(
        store,
        [
            ModelProposal(
                source_event_id="event:holder-clarification",
                intent="inform",
                reply_chunks=(question,),
                clarification_question=question,
                facts=(
                    ModelFact("full_name", "Bruno Exemplo"),
                    ModelFact("email", "bruno@example.invalid"),
                    ModelFact("country_code", "BR"),
                ),
                read_requests=(),
                effect_proposals=(),
            )
        ],
    )
    _install_public_authority(store, authority)
    executor = _executor(
        store=store,
        model=model,
        profile=PhoneOnlyManyChatContact(store),
        public_authority=MappingAuthority({batch.batch_id: authority}),
        private_customer_facts=private_store,
    )
    try:
        result = executor.execute(batch)
        snapshot = private_store.load(batch.lead_id)
        projection = store.load_latest_conversation_projection(batch.lead_id)

        assert result.reply_chunks == (question,)
        assert tuple(
            json.loads(payload.decode("utf-8"))["data"]["text"]
            for _, _, payload, _ in result.receipt.public_chunks
        ) == (question,)
        assert not result.receipt.command_rows
        assert not result.receipt.relay_rows
        assert not result.receipt.internal_outbox_rows
        assert (
            "bruno@example.invalid"
            not in result.receipt.to_canonical_bytes().decode("utf-8")
        )
        assert (snapshot.full_name, snapshot.email, snapshot.country_code) == (
            "Bruno Exemplo",
            "bruno@example.invalid",
            "BR",
        )
        assert projection is not None
        assert not {"full_name", "email", "country_code", "phone_e164"} & {
            fact.name for fact in projection.facts
        }
        for table in (
            "boundary_commands",
            "boundary_command_relays",
            "boundary_outbox",
        ):
            assert store._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone() == (0,)
    finally:
        private_store.close()
        store.close()


@pytest.mark.parametrize("correction_case", ("safe", "repeated_exposure", "invalid_structure"))
def test_private_value_exposure_gets_one_terminal_model_owned_correction(
    tmp_path,
    correction_case: str,
) -> None:
    private_name = "Pessoa Parcial Silva"
    read_request = ReadRequest(
        request_id="read:filtered-private-echo",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    proposal = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(f"Vou consultar a hospedagem para {private_name}.",),
        facts=(ModelFact("full_name", private_name),),
        read_requests=(read_request,),
        effect_proposals=(),
    )
    corrected_text = (
        f"Ainda vou expor {private_name}."
        if correction_case == "repeated_exposure"
        else "Maya corrigiu sem repetir nenhum dado privado."
    )
    corrected = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(corrected_text,),
        facts=(
            (ModelFact("service", "hostel"),)
            if correction_case == "invalid_structure"
            else ()
        ),
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / f"filtered-private-echo-{correction_case}.sqlite3"
    )
    model = FakeAuditedModel(store, [proposal, corrected])
    read_port = FakeLodgingReadPort(store)
    _install_public_authority(store)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        profile=UnusableManyChatPhone(store, "missing"),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        if correction_case == "safe":
            result = executor.execute(BATCH)
            artifact_json = "\n".join(
                row[0]
                for row in store._connection.execute(
                    "SELECT artifact_json FROM boundary_turn_artifacts "
                    "ORDER BY artifact_index"
                ).fetchall()
            )
            assert result.reply_chunks == (corrected_text,)
            assert private_name not in artifact_json
            assert result.receipt.command_rows == ()
            assert result.receipt.relay_rows == ()
        else:
            with pytest.raises(TurnExecutionError, match="public reply correction"):
                executor.execute(BATCH)
            assert store.turn_receipt_count(BATCH.batch_id) == 0
            for table in (
                "boundary_commands",
                "boundary_command_relays",
                "boundary_outbox",
            ):
                assert store._connection.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone() == (0,)

        snapshot = private_store.load(BATCH.lead_id)
        assert snapshot.full_name == private_name
        assert read_port.calls == []
        assert len(model.calls) == 2
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
        assert model.proposals == []
    finally:
        private_store.close()
        store.close()


@pytest.mark.parametrize(
    ("case_id", "message", "maya_facts", "expected", "reply"),
    (
        (
            "lead-not-wife-or-hostel",
            "Eu sou Ana Titular Silva, ana.titular@example.invalid, do Brasil. "
            "Minha esposa Beatriz Acompanhante Souza usa "
            "beatriz.acompanhante@example.invalid e o Hostel Terceiro usa "
            "reservas.hostel@example.invalid.",
            (
                ModelFact("full_name", "Ana Titular Silva"),
                ModelFact("email", "ana.titular@example.invalid"),
                ModelFact("country_code", "BR"),
            ),
            ("Ana Titular Silva", "ana.titular@example.invalid", "BR"),
            "Entendi quem será a titular.",
        ),
        (
            "explicit-wife-holder",
            "Eu sou Carlos Pagante Silva, mas minha esposa Beatriz Titular Souza "
            "será a titular da reserva; o e-mail dela é "
            "beatriz.titular@example.invalid e ela é da Argentina.",
            (
                ModelFact("full_name", "Beatriz Titular Souza"),
                ModelFact("email", "beatriz.titular@example.invalid"),
                ModelFact("country_code", "AR"),
            ),
            ("Beatriz Titular Souza", "beatriz.titular@example.invalid", "AR"),
            "Perfeito, registrei a titular indicada.",
        ),
        (
            "ambiguous-holder",
            "A reserva é para Ana Silva e Beatriz Souza. Pode usar "
            "ana@example.invalid ou beatriz@example.invalid; ainda não sei quem "
            "ficará como titular.",
            (),
            (None, None, None),
            "Quem ficará como titular da reserva?",
        ),
    ),
)
def test_maya_semantics_assign_holder_without_parent_regex(
    tmp_path,
    case_id: str,
    message: str,
    maya_facts: tuple[ModelFact, ...],
    expected: tuple[str | None, str | None, str | None],
    reply: str,
) -> None:
    event = replace(
        EVENT,
        event_id=f"event:semantic-holder:{case_id}",
        text=message,
        payload_hash="a" * 64,
    )
    batch = InboundBatch(
        batch_id=f"batch:semantic-holder:{case_id}",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(event,),
        combined_text=message,
    )
    authority = replace(
        AUTHORITY,
        authorization_id=f"auth:semantic-holder:{case_id}",
        allocation_ids=(f"allocation:semantic-holder:{case_id}",),
        allocation_manifest_hash="a" * 64,
    )
    proposal = ModelProposal(
        source_event_id=batch.batch_id,
        intent="inform",
        reply_chunks=(reply,),
        facts=maya_facts,
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / f"semantic-holder-{case_id}.sqlite3"
    )
    model = FakeAuditedModel(store, [proposal])
    _install_public_authority(store, authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority({batch.batch_id: authority}),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        result = executor.execute(batch)
        snapshot = private_store.load(batch.lead_id)
        projection = store.load_latest_conversation_projection(batch.lead_id)

        assert model.calls[0].message == message
        assert (snapshot.full_name, snapshot.email, snapshot.country_code) == expected
        assert result.reply_chunks == (reply,)
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert projection is not None
        assert not {"full_name", "email", "country_code", "phone_e164"} & {
            fact.name for fact in projection.facts
        }
    finally:
        private_store.close()
        store.close()


@pytest.mark.parametrize("phone_mode", ("missing", "future", "expired"))
def test_unusable_manychat_phone_blocks_provider_reads_and_commands(
    tmp_path,
    phone_mode: str,
) -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / f"unusable-phone-{phone_mode}.sqlite3"
    )
    private_store.persist_turn(
        lead_id=BATCH.lead_id,
        source_turn_id=f"batch:profile-before-unusable-phone:{phone_mode}",
        source_event_hash="d" * 64,
        facts=(
            ModelFact("full_name", "Pessoa Phone Silva"),
            ModelFact("email", "phone.person@example.invalid"),
            ModelFact("country_code", "BR"),
        ),
        persisted_at=NOW - timedelta(minutes=1),
    )
    request = ReadRequest(
        request_id=f"read:unusable-phone:{phone_mode}",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    proposal = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar o resumo.",),
        facts=(),
        read_requests=(request,),
        effect_proposals=(),
        target_offer_id="offer:" + "7" * 64,
    )
    model = FakeAuditedModel(store, [proposal])
    read_port = FakeLodgingReadPort(store)
    _install_public_authority(store)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        profile=UnusableManyChatPhone(store, phone_mode),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        result = executor.execute(BATCH)

        assert read_port.calls == []
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert "telefone" not in " ".join(result.reply_chunks).casefold()
    finally:
        private_store.close()
        store.close()


def test_unusable_manychat_phone_blocks_two_stage_selection_probe(tmp_path) -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "unusable-phone-two-stage.sqlite3"
    )
    private_store.persist_turn(
        lead_id=BATCH.lead_id,
        source_turn_id="batch:profile-before-two-stage-selection",
        source_event_hash="7" * 64,
        facts=(
            ModelFact("full_name", "Pessoa Phone Silva"),
            ModelFact("email", "phone.person@example.invalid"),
            ModelFact("country_code", "BR"),
        ),
        persisted_at=NOW - timedelta(minutes=1),
    )
    request = ReadRequest(
        request_id="read:unusable-phone:two-stage",
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
    second = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar o resumo.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "7" * 64,
    )
    model = FakeAuditedModel(store, [first, second])
    read_port = FakeLodgingReadPort(store)
    _install_public_authority(store)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        profile=UnusableManyChatPhone(store, "missing"),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        result = executor.execute(BATCH)

        assert read_port.calls == []
        assert len(model.calls) == 1
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
    finally:
        private_store.close()
        store.close()


def test_invalid_model_private_facts_are_collection_only_without_controller_prose(
    tmp_path,
) -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "invalid-model-private.sqlite3"
    )
    maya_text = "Maya sentinel: preciso que você reenvie os dados do titular."
    proposal = replace(
        _proposal(maya_text),
        facts=(
            ModelFact("full_name", "Mononym"),
            ModelFact("email", "@example.invalid"),
            ModelFact("country_code", "ZZ"),
            ModelFact("phone_e164", "+1" + "202" + "555" + "0177"),
        ),
    )
    model = FakeAuditedModel(store, [proposal])
    _install_public_authority(store)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        result = executor.execute(BATCH)
        snapshot = private_store.load(BATCH.lead_id)

        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert result.reply_chunks == (maya_text,)
        assert len(model.calls) == 1
        assert snapshot.present_fact_names == ()
    finally:
        private_store.close()
        store.close()


def test_maya_holder_facts_persist_before_read_and_continue_to_summary_same_turn(
    tmp_path,
) -> None:
    private_name = "Pessoa Prompt Silva"
    private_email = "prompt.person@example.invalid"
    private_country = "Brasil"
    typed_phone = "+1" + "202" + "555" + "0101"
    message = (
        f"Meu nome completo é {private_name}, meu e-mail é {private_email} "
        f"e sou do {private_country}. Meu telefone é {typed_phone}."
    )
    event = replace(
        EVENT,
        event_id="event:private-prompt-redaction",
        text=message,
        payload_hash="5" * 64,
    )
    batch = InboundBatch(
        batch_id="batch:private-prompt-redaction",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(event,),
        combined_text=message,
    )
    authority = replace(
        AUTHORITY,
        authorization_id="auth:private-prompt-redaction",
        allocation_ids=("allocation:private-prompt-redaction",),
        allocation_manifest_hash="5" * 64,
    )
    read_request = ReadRequest(
        request_id="read:private-prompt-redaction",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    first = ModelProposal(
        source_event_id=batch.batch_id,
        intent="inform",
        reply_chunks=("Vou consultar.",),
        facts=(
            ModelFact("full_name", private_name),
            ModelFact("email", private_email),
            ModelFact("country_code", "BR"),
            ModelFact("phone_e164", typed_phone),
        ),
        read_requests=(read_request,),
        effect_proposals=(),
    )
    selection = ModelProposal(
        source_event_id=batch.batch_id,
        intent="select",
        reply_chunks=(
            f"Vou preparar o resumo para {private_name}, {private_email}, "
            f"{private_country}.",
        ),
        facts=(
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
    corrected_selection = replace(
        selection,
        reply_chunks=("Vou preparar o resumo sem repetir dados privados.",),
    )
    boundary_path = tmp_path / "private-prompt-boundary.sqlite3"
    store = SQLiteBoundaryStore.open_path_v8(boundary_path)
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "private-prompt-redaction.sqlite3"
    )
    model = FakeAuditedModel(store, [first, selection, corrected_selection])
    read_port = FakeLodgingReadPort(store)
    _install_public_authority(store, authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({ReadKind.LODGING: read_port}),
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority({batch.batch_id: authority}),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        result = executor.execute(batch)
        snapshot = private_store.load(batch.lead_id)
        state = store.load_state(batch.lead_id).state
        artifact_blob = "\n".join(
            row[0]
            for row in store._connection.execute(
                "SELECT artifact_json FROM boundary_turn_artifacts "
                "ORDER BY artifact_index"
            ).fetchall()
        )

        assert result.reply_chunks[0] == corrected_selection.reply_chunks[0]
        assert "Só para confirmar" in result.reply_chunks[1]
        assert "Guardei esses dados" not in " ".join(result.reply_chunks)
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert read_port.calls == [read_request]
        assert len(model.calls) == 3
        assert model.calls[0].private_customer_fact_names == ("phone_e164",)
        assert model.calls[1].private_customer_fact_names == (
            "full_name",
            "email",
            "phone_e164",
            "country_code",
        )
        assert model.calls[2].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
        assert model.calls[0].message == message
        assert model.calls[1].message == message
        for private_value in (private_name, private_email, private_country):
            assert private_value not in repr(model.calls[0])
            assert private_value not in repr(model.calls[1])
            assert private_value not in repr(snapshot)
            assert private_value not in artifact_blob
        assert snapshot.full_name == private_name
        assert snapshot.email == private_email
        assert snapshot.country_code == "BR"
        assert type(state.workflow) is AwaitingConfirmationState
        assert state.workflow.draft.customer.phone_e164 == "+" + "5575999990199"
        assert state.workflow.draft.customer.phone_e164 != typed_phone
        reopened = SQLiteBoundaryStore.open_readonly_v8(boundary_path)
        reopened.close()
    finally:
        private_store.close()
        store.close()


def test_private_update_journal_survives_crash_without_becoming_progress_gate(
    tmp_path,
) -> None:
    private_store_path = tmp_path / "private-customer-replay.sqlite3"
    private_name = "Pessoa Replay Silva"
    private_email = "replay.person@example.invalid"
    private_country = "US"
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(private_store_path)
    first = replace(
        _proposal("Dados recebidos."),
        facts=(
            ModelFact("full_name", private_name),
            ModelFact("email", private_email),
            ModelFact("country_code", private_country),
        ),
    )
    retry = _proposal("Continuação autenticada.")
    model = FakeAuditedModel(store, [first, retry])
    _install_public_authority(store)

    def build(boundary_store) -> V2TurnExecutor:
        return V2TurnExecutor(
            store=boundary_store,
            model=model,
            reads=V2ReadService({}),
            profile=PhoneOnlyManyChatContact(store),
            private_customer_facts=private_store,
            reducer=_enabled_reducer(),
            public_authority=FixedAuthority(),
            clock=FixedClock(),
            locale="pt-BR",
            turn_timeout=timedelta(seconds=30),
            max_commit_attempts=1,
        )

    try:
        with pytest.raises(RuntimeError, match="injected atomic turn failure"):
            build(FaultingStore(store, "after_public_outbox_insert_0")).execute(BATCH)
        assert store.load_turn_receipt(BATCH.batch_id) is None
        assert private_store.turn_supplied_fact_names(
            BATCH.lead_id, BATCH.batch_id
        ) == ("full_name", "email", "country_code")

        result = build(store).execute(BATCH)

        assert result.reply_chunks == ("Continuação autenticada.",)
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert len(model.calls) == 2
        assert model.calls[1].private_profile_complete is True
    finally:
        private_store.close()
        store.close()


def test_conversation_country_marks_authenticated_manychat_contact_complete_next_turn() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    second_event = replace(
        EVENT,
        event_id="event:conversation-country-next-turn",
        text="Pode continuar com a reserva.",
        payload_hash="6" * 64,
    )
    second_batch = InboundBatch(
        batch_id="batch:conversation-country-next-turn",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        events=(second_event,),
        combined_text=second_event.text,
    )
    second_authority = replace(
        AUTHORITY,
        authorization_id="auth:conversation-country-next-turn",
        allocation_ids=("allocation:conversation-country-next-turn",),
        allocation_manifest_hash="6" * 64,
    )
    first = replace(
        _proposal("País registrado."),
        facts=(ModelFact("country_code", "US"),),
    )
    second = replace(
        _proposal("Vou continuar."),
        source_event_id=second_batch.batch_id,
    )
    model = FakeAuditedModel(store, [first, second])
    profile = AuthenticatedManyChatContactWithoutCountry(store)
    _install_public_authority(store)
    _install_public_authority(store, second_authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=profile,
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority(
            {
                BATCH.batch_id: AUTHORITY,
                second_batch.batch_id: second_authority,
            }
        ),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        executor.execute(BATCH)
        executor.execute(second_batch)

        assert model.calls[0].private_profile_complete is False
        assert model.calls[1].private_profile_complete is True
        assert "country_code" in model.calls[1].private_customer_fact_names
    finally:
        store.close()


def test_package_turn_accepts_two_reads_bound_to_the_same_model_frame() -> None:
    package_event = replace(
        EVENT,
        text=(
            "Quero hostel de 10/08/2026 a 12/08/2026 e Buracão em "
            "12/08/2026 para 2 adultos e nenhuma criança. Consulte os dois."
        ),
        payload_hash="5" * 64,
    )
    package_batch = replace(
        BATCH,
        events=(package_event,),
        combined_text=package_event.text,
    )
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
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    semantic_review = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Encontrei opções de hospedagem e Buracão.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [first, final, semantic_review])
    lodging_port = FakeLodgingReadPort(store)
    activity_port = FakeActivityReadPort(store)
    package_authority = replace(
        AUTHORITY,
        allocation_ids=("allocation:package:0",),
        allocation_manifest_hash="5" * 64,
    )
    _install_public_authority(store, package_authority)
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
        public_authority=MappingAuthority(
            {package_batch.batch_id: package_authority}
        ),
    )
    try:
        result = executor.execute(package_batch)

        assert result.reply_chunks == (
            "Encontrei opções de hospedagem e Buracão.",
        )
        assert lodging_port.calls == [lodging]
        assert activity_port.calls == [replace(activity, locale="pt-BR")]
        assert len(model.calls) == 3
        assert model.calls[2].selection_review_required is True
        assert tuple(item.provider for item in model.calls[2].observations) == (
            "cloudbeds",
            "bokun",
        )
        assert len(result.receipt.read_observations) == 2
        projection = store.load_latest_conversation_projection(BATCH.lead_id)
        assert projection is not None
        assert {item.value for item in projection.desired_services} == {
            "hostel",
            "agency",
        }
        service_fact = next(item for item in projection.facts if item.name == "service")
        assert service_fact.value.value == "package"
    finally:
        store.close()


def test_post_read_selection_review_private_fact_is_owned_and_never_published() -> None:
    raw_email = "Hybrid@Example.INVALID"
    canonical_email = "hybrid@example.invalid"
    package_event = replace(
        EVENT,
        text=(
            "Quero hostel de 10/08/2026 a 12/08/2026 e Buracão em "
            "12/08/2026 para 2 adultos e nenhuma criança. Consulte os dois."
        ),
        payload_hash="6" * 64,
    )
    package_batch = replace(
        BATCH,
        events=(package_event,),
        combined_text=package_event.text,
    )
    lodging = ReadRequest(
        request_id="read:review-private-lodging",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    activity = ReadRequest(
        request_id="read:review-private-activity",
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
    final = _proposal("Encontrei opções de hospedagem e Buracão.")
    unsafe_review = replace(
        final,
        reply_chunks=(f"As opções foram separadas para {raw_email}.",),
        facts=(ModelFact("email", raw_email),),
    )
    corrected_review = replace(
        final,
        reply_chunks=("Encontrei as opções sem repetir dados privados.",),
        facts=(ModelFact("service", "package"),),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    model = FakeAuditedModel(
        store,
        [first, final, unsafe_review, corrected_review],
    )
    lodging_port = FakeLodgingReadPort(store)
    activity_port = FakeActivityReadPort(store)
    package_authority = replace(
        AUTHORITY,
        allocation_ids=("allocation:review-private:0",),
        allocation_manifest_hash="6" * 64,
    )
    _install_public_authority(store, package_authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService(
            {
                ReadKind.LODGING: lodging_port,
                ReadKind.ACTIVITY: activity_port,
            }
        ),
        profile=FakeProfile(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority(
            {package_batch.batch_id: package_authority}
        ),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    try:
        result = executor.execute(package_batch)

        assert result.reply_chunks == corrected_review.reply_chunks
        assert len(model.calls) == 4
        assert model.calls[2].selection_review_required is True
        assert model.calls[3].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
        assert private_store.load(BATCH.lead_id).email == canonical_email
        assert raw_email not in result.receipt.to_canonical_bytes().decode("utf-8")
        public_rows = "\n".join(
            row[0]
            for row in store._connection.execute(
                "SELECT chunk_json FROM boundary_public_outbox"
            ).fetchall()
        )
        artifact_rows = "\n".join(
            row[0]
            for row in store._connection.execute(
                "SELECT artifact_json FROM boundary_turn_artifacts"
            ).fetchall()
        )
        assert raw_email not in public_rows
        assert raw_email not in artifact_rows
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
    finally:
        private_store.close()
        store.close()


def test_post_read_selection_review_discarded_passenger_never_leaks() -> None:
    private_name = "Pessoa Passageira Privada"
    package_event = replace(
        EVENT,
        text=(
            "Quero hostel de 10/08/2026 a 12/08/2026 e Buracão em "
            "12/08/2026 para 2 adultos e nenhuma criança. Consulte os dois."
        ),
        payload_hash="7" * 64,
    )
    package_batch = replace(
        BATCH,
        events=(package_event,),
        combined_text=package_event.text,
    )
    lodging = ReadRequest(
        request_id="read:review-passenger-lodging",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    activity = ReadRequest(
        request_id="read:review-passenger-activity",
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
    final = _proposal("Encontrei opções de hospedagem e Buracão.")
    unsafe_review = replace(
        final,
        reply_chunks=(f"Separei as opções para {private_name}.",),
        passengers=(
            PassengerInput(
                position=1,
                participant_type="adult",
                full_name=private_name,
                birth_date=None,
                gender=None,
                country_code=None,
            ),
        ),
    )
    corrected_review = replace(
        final,
        reply_chunks=("Encontrei as opções sem repetir dados privados.",),
        facts=(ModelFact("service", "package"),),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    model = FakeAuditedModel(
        store,
        [first, final, unsafe_review, corrected_review],
    )
    lodging_port = FakeLodgingReadPort(store)
    activity_port = FakeActivityReadPort(store)
    package_authority = replace(
        AUTHORITY,
        allocation_ids=("allocation:review-passenger:0",),
        allocation_manifest_hash="7" * 64,
    )
    _install_public_authority(store, package_authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService(
            {
                ReadKind.LODGING: lodging_port,
                ReadKind.ACTIVITY: activity_port,
            }
        ),
        profile=FakeProfile(store),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=MappingAuthority(
            {package_batch.batch_id: package_authority}
        ),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    try:
        result = executor.execute(package_batch)

        assert result.reply_chunks == corrected_review.reply_chunks
        assert len(model.calls) == 4
        assert model.calls[2].selection_review_required is True
        assert model.calls[3].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
        assert private_store.load_passenger_manifest(BATCH.lead_id) is None
        assert private_name not in result.receipt.to_canonical_bytes().decode("utf-8")
        public_rows = "\n".join(
            row[0]
            for row in store._connection.execute(
                "SELECT chunk_json FROM boundary_public_outbox"
            ).fetchall()
        )
        artifact_rows = "\n".join(
            row[0]
            for row in store._connection.execute(
                "SELECT artifact_json FROM boundary_turn_artifacts"
            ).fetchall()
        )
        assert private_name not in public_rows
        assert private_name not in artifact_rows
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
    finally:
        private_store.close()
        store.close()


def test_authenticated_phone_allows_read_only_package_without_country() -> None:
    lodging = ReadRequest(
        request_id="read:incomplete-country-package-lodging",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    activity = ReadRequest(
        request_id="read:incomplete-country-package-activity",
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
        reply_chunks=("Encontrei Suíte Casal e Buracão disponíveis.",),
        facts=(),
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
        profile=AuthenticatedManyChatContactWithoutCountry(store),
        reads=V2ReadService(
            {
                ReadKind.LODGING: lodging_port,
                ReadKind.ACTIVITY: activity_port,
            }
        ),
    )
    try:
        result = executor.execute(BATCH)

        assert model.calls[0].private_profile_complete is False
        assert model.calls[1].private_profile_complete is False
        assert len(model.calls[1].observations) == 2
        assert result.reply_chunks == (
            "Encontrei Suíte Casal e Buracão disponíveis.",
        )
        assert lodging_port.calls == [lodging]
        assert activity_port.calls == [replace(activity, locale="en")]
        assert len(result.receipt.read_observations) == 2
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
    finally:
        store.close()


def test_incomplete_country_allows_read_but_blocks_followup_selection() -> None:
    request = ReadRequest(
        request_id="read:incomplete-country-selection",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        adults=2,
        children=0,
    )
    first = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Vou consultar a hospedagem.",),
        facts=(),
        read_requests=(request,),
        effect_proposals=(),
    )
    selection = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="select",
        reply_chunks=("Vou preparar o resumo.",),
        facts=(
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
    model = FakeAuditedModel(store, [first, selection])
    read_port = FakeLodgingReadPort(store)
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=AuthenticatedManyChatContactWithoutCountry(store),
        reads=V2ReadService({ReadKind.LODGING: read_port}),
    )
    try:
        result = executor.execute(BATCH)

        assert read_port.calls == [request]
        assert len(model.calls) == 2
        assert len(result.receipt.read_observations) == 1
        assert result.reply_chunks == selection.reply_chunks
        assert not isinstance(store.load_state(BATCH.lead_id).state.workflow, AwaitingConfirmationState)
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
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
        participants=1,
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
            ModelFact("adults", 1),
            ModelFact("children", 0),
            ModelFact("payment_method", "stripe"),
        ),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "6" * 64,
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [first, selection])
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
        reads=V2ReadService({ReadKind.ACTIVITY: FakeActivityReadPort(store)}),
        private_customer_facts=private_store,
    )
    try:
        result = executor.execute(BATCH)
        projection = store.load_latest_conversation_projection(BATCH.lead_id)

        assert result.reply_chunks[0] == selection.reply_chunks[0]
        assert result.reply_chunks[1].startswith("Só para confirmar:")
        assert projection is not None
        values = {fact.name: fact.value.value for fact in projection.facts}
        assert "birth_date" not in values
        assert "gender" not in values
        assert private_store.load(BATCH.lead_id).birth_date == date(1992, 4, 15)
        assert private_store.load(BATCH.lead_id).gender == "f"

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
                    participants=1,
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
            private_customer_facts=private_store,
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
        assert confirmed.reply_chunks == confirmation.reply_chunks
    finally:
        private_store.close()
        store.close()


def test_activity_confirmation_derives_current_provider_read() -> None:
    first_read = ReadRequest(
        request_id="read:derive-activity-selection",
        kind=ReadKind.ACTIVITY,
        product_id="product:buracao",
        activity_date=date(2026, 8, 12),
        participants=1,
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
            ModelFact("adults", 1),
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
        assert derived[0].activity_party() == (1, 0)
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
            CriticalActionKind.RESERVE_LODGING,
        ),
        public_summary="Só para confirmar: vou reservar; o pagamento fica separado.",
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
    assert reads_allowed(adjustment) is True
    assert reads_allowed(adjustment, now=pending.expires_at) is True
    assert reads_allowed(adjustment, material_scope_bound=False) is True
    information = replace(
        adjustment,
        source_event_id="batch:critical-information",
        intent="inform",
    )
    assert reads_allowed(information) is True

@pytest.mark.parametrize(
    ("review_intent", "pending_disposition"),
    (
        ("inform", None),
        ("adjust", "revoke"),
    ),
)
def test_non_authorizing_confirmation_review_commits_zero_effect_rows(
    review_intent: str,
    pending_disposition: str | None,
) -> None:
    store, model, read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=5),
        confirmation_clock=FixedClock(),
    )
    initial = ModelProposal(
        source_event_id=second_batch.batch_id,
        intent="inform",
        reply_chunks=("Vou revisar sua mensagem no contexto do resumo.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    review = ModelProposal(
        source_event_id=second_batch.batch_id,
        intent=review_intent,
        reply_chunks=("Nenhuma execução foi autorizada.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        pending_disposition=pending_disposition,
    )
    model.proposals[:] = [initial, review]
    prior_calls = len(model.calls)
    try:
        result = executor.execute(second_batch)

        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert len(read_port.calls) == 1
        assert len(model.calls) == prior_calls + 2
        general_request, review_request = model.calls[-2:]
        assert general_request.confirmation_review_required is False
        assert review_request.confirmation_review_required is True
        assert review_request.request_id != general_request.request_id
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_command_relays"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_confirmation_without_domain_command_never_claims_processing() -> None:
    store, model, read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=5),
        confirmation_clock=FixedClock(),
    )
    try:
        result = executor.execute(second_batch)

        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert result.reply_chunks == ("Confirmado.",)
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_command_relays"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_adjustment_revokes_pending_summary_and_reads_new_scope_in_same_turn() -> None:
    store, model, read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=30),
        confirmation_clock=SequenceClock(),
    )
    adjusted_event = replace(
        second_batch.events[0],
        text="Mude para 11/08/2026 a 13/08/2026 e consulte novamente.",
        payload_hash="3" * 64,
    )
    adjusted_batch = replace(
        second_batch,
        events=(adjusted_event,),
        combined_text=adjusted_event.text,
    )
    adjusted_read = ReadRequest(
        request_id="read:adjusted-scope",
        kind=ReadKind.LODGING,
        check_in=date(2026, 8, 11),
        check_out=date(2026, 8, 13),
        adults=2,
        children=0,
    )
    model.proposals[:] = [
        ModelProposal(
            source_event_id=adjusted_batch.batch_id,
            intent="adjust",
            reply_chunks=("Vou consultar as novas datas.",),
            facts=(
                ModelFact("start_date", date(2026, 8, 11)),
                ModelFact("end_date", date(2026, 8, 13)),
            ),
            read_requests=(),
            effect_proposals=(),
            pending_disposition="revoke",
        ),
        ModelProposal(
            source_event_id=adjusted_batch.batch_id,
            intent="inform",
            reply_chunks=("As novas datas estão disponíveis.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
        ),
    ]
    try:
        result = executor.execute(adjusted_batch)

        observed_adjustment_read = read_port.calls[-1]
        assert replace(
            observed_adjustment_read,
            request_id=adjusted_read.request_id,
        ) == adjusted_read
        assert len(read_port.calls) == 2
        assert result.reply_chunks == ("As novas datas estão disponíveis.",)
        assert type(store.load_state(BATCH.lead_id).state.workflow) is AwaitingAdjustmentState
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
    finally:
        store.close()


def test_new_duplicate_confirmation_after_queue_reports_status_without_new_read() -> None:
    store, model, read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=30),
        confirmation_clock=SequenceClock(),
    )
    try:
        confirmed = executor.execute(second_batch)
        assert len(confirmed.receipt.command_rows) == 1
        reads_after_confirmation = len(read_port.calls)

        duplicate_event = replace(
            second_batch.events[0],
            event_id="event:queued-duplicate-new-event",
            occurred_at=NOW + timedelta(seconds=1),
            payload_hash="4" * 64,
        )
        duplicate_batch = InboundBatch(
            batch_id="batch:queued-duplicate-new-event",
            lead_id=second_batch.lead_id,
            subscriber_id=second_batch.subscriber_id,
            events=(duplicate_event,),
            combined_text=duplicate_event.text,
        )
        duplicate_authority = replace(
            AUTHORITY,
            authorization_id="auth:queued-duplicate-new-event",
            allocation_ids=("allocation:queued-duplicate-new-event",),
            allocation_manifest_hash="4" * 64,
        )
        _install_public_authority(store, duplicate_authority)
        executor._public_authority.values[duplicate_batch.batch_id] = duplicate_authority
        repeated_read = ReadRequest(
            request_id="read:queued-duplicate-new-event",
            kind=ReadKind.LODGING,
            check_in=date(2026, 8, 10),
            check_out=date(2026, 8, 12),
            adults=2,
            children=0,
        )
        corrected_status = "Maya: a reserva existente continua em processamento."
        model.proposals[:] = [
            ModelProposal(
                source_event_id=duplicate_batch.batch_id,
                intent="inform",
                reply_chunks=("Vou consultar novamente.",),
                facts=(),
                read_requests=(repeated_read,),
                effect_proposals=(),
                selection_requested=True,
            ),
            ModelProposal(
                source_event_id=duplicate_batch.batch_id,
                intent="inform",
                reply_chunks=(corrected_status,),
                facts=(),
                read_requests=(),
                effect_proposals=(),
            ),
        ]

        duplicate = executor.execute(duplicate_batch)

        assert len(read_port.calls) == reads_after_confirmation
        assert duplicate.reply_chunks == (corrected_status,)
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.ACTIVE_EXECUTION_CONFLICT,
        )
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone() == (1,)
    finally:
        store.close()


def test_active_execution_blocks_new_commercial_scope_without_replacing_workflow() -> None:
    store, model, read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=30),
        confirmation_clock=SequenceClock(),
    )
    try:
        executor.execute(second_batch)
        before = store.load_state(BATCH.lead_id).state.workflow
        command_count = store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone()
        read_count = len(read_port.calls)

        new_event = replace(
            EVENT,
            event_id="evt:" + "9" * 64,
            text=(
                "Tem quarto disponível de 13/08/2026 a 15/08/2026 para 2 adultos?"
            ),
            occurred_at=NOW + timedelta(seconds=4),
        )
        new_batch = replace(
            BATCH,
            batch_id="agg:" + "9" * 64,
            events=(new_event,),
            combined_text=new_event.text,
        )
        new_request = ReadRequest(
            request_id="read:active-new-scope",
            kind=ReadKind.LODGING,
            check_in=date(2026, 8, 13),
            check_out=date(2026, 8, 15),
            adults=2,
            children=0,
        )
        corrected_status = "Maya: a reserva existente continua em processamento."
        model.proposals.extend(
            (
                ModelProposal(
                    source_event_id=new_batch.batch_id,
                    intent="inform",
                    reply_chunks=("Vou consultar essa outra hospedagem.",),
                    facts=(
                        ModelFact("service", "hostel"),
                        ModelFact("start_date", date(2026, 8, 13)),
                        ModelFact("end_date", date(2026, 8, 15)),
                        ModelFact("adults", 2),
                        ModelFact("children", 0),
                    ),
                    read_requests=(new_request,),
                    effect_proposals=(),
                ),
                ModelProposal(
                    source_event_id=new_batch.batch_id,
                    intent="inform",
                    reply_chunks=(corrected_status,),
                    facts=(),
                    read_requests=(),
                    effect_proposals=(),
                ),
            )
        )
        new_authority = replace(
            AUTHORITY,
            authorization_id="auth:active-new-scope",
            allocation_ids=("allocation:active-new-scope",),
            allocation_manifest_hash="9" * 64,
        )
        _install_public_authority(store, new_authority)
        executor._public_authority = MappingAuthority(
            {
                BATCH.batch_id: AUTHORITY,
                second_batch.batch_id: AUTHORITY,
                new_batch.batch_id: new_authority,
            }
        )

        result = executor.execute(new_batch)
        after = store.load_state(BATCH.lead_id).state.workflow

        assert after == before
        assert len(read_port.calls) == read_count
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone() == command_count
        assert result.reply_chunks == (corrected_status,)
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.ACTIVE_EXECUTION_CONFLICT,
        )
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert model.proposals == []
    finally:
        store.close()


def test_active_execution_blocks_material_facts_without_read_or_projection_drift() -> None:
    store, model, read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=30),
        confirmation_clock=SequenceClock(),
    )
    try:
        executor.execute(second_batch)
        before_state = store.load_state(BATCH.lead_id).state.workflow
        before_projection = store.load_latest_conversation_projection(BATCH.lead_id)
        assert before_projection is not None
        read_count = len(read_port.calls)
        command_count = store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone()

        material_event = replace(
            EVENT,
            event_id="evt:" + "a" * 64,
            text="Mudança: agora seriam 3 adultos de 16/08/2026 a 18/08/2026.",
            occurred_at=NOW + timedelta(seconds=5),
            payload_hash="a" * 64,
        )
        material_batch = replace(
            BATCH,
            batch_id="agg:" + "a" * 64,
            events=(material_event,),
            combined_text=material_event.text,
        )
        corrected_status = "Maya: a reserva existente continua em processamento."
        model.proposals.extend(
            (
                ModelProposal(
                    source_event_id=material_batch.batch_id,
                    intent="inform",
                    reply_chunks=("Atualizei as datas e a ocupação.",),
                    facts=(
                        ModelFact("service", "hostel"),
                        ModelFact("start_date", date(2026, 8, 16)),
                        ModelFact("end_date", date(2026, 8, 18)),
                        ModelFact("adults", 3),
                        ModelFact("children", 0),
                    ),
                    read_requests=(),
                    effect_proposals=(),
                    passengers=(
                        PassengerInput(
                            position=1,
                            participant_type="adult",
                            full_name="Pessoa Passageira Fictícia",
                            birth_date=None,
                            gender=None,
                            country_code=None,
                        ),
                    ),
                ),
                ModelProposal(
                    source_event_id=material_batch.batch_id,
                    intent="inform",
                    reply_chunks=(corrected_status,),
                    facts=(),
                    read_requests=(),
                    effect_proposals=(),
                ),
            )
        )
        material_authority = replace(
            AUTHORITY,
            authorization_id="auth:active-material-facts",
            allocation_ids=("allocation:active-material-facts",),
            allocation_manifest_hash="a" * 64,
        )
        _install_public_authority(store, material_authority)
        executor._public_authority = MappingAuthority(
            {
                BATCH.batch_id: AUTHORITY,
                second_batch.batch_id: AUTHORITY,
                material_batch.batch_id: material_authority,
            }
        )

        result = executor.execute(material_batch)
        after_state = store.load_state(BATCH.lead_id).state.workflow
        after_projection = store.load_latest_conversation_projection(BATCH.lead_id)

        assert after_state == before_state
        assert after_projection is not None
        assert after_projection.facts == before_projection.facts
        assert after_projection.desired_services == before_projection.desired_services
        assert len(read_port.calls) == read_count
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone() == command_count
        assert result.reply_chunks == (corrected_status,)
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.ACTIVE_EXECUTION_CONFLICT,
        )
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert model.proposals == []
    finally:
        store.close()


def test_short_inert_reaffirmation_after_queue_reports_existing_processing() -> None:
    store, model, read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=30),
        confirmation_clock=SequenceClock(),
    )
    try:
        confirmed = executor.execute(second_batch)
        assert len(confirmed.receipt.command_rows) == 1
        reads_after_confirmation = len(read_port.calls)

        reaffirmation_event = replace(
            second_batch.events[0],
            event_id="event:queued-short-reaffirmation",
            text="Isso, pode seguir.",
            occurred_at=NOW + timedelta(seconds=1),
            payload_hash="5" * 64,
        )
        reaffirmation_batch = InboundBatch(
            batch_id="batch:queued-short-reaffirmation",
            lead_id=second_batch.lead_id,
            subscriber_id=second_batch.subscriber_id,
            events=(reaffirmation_event,),
            combined_text=reaffirmation_event.text,
        )
        reaffirmation_authority = replace(
            AUTHORITY,
            authorization_id="auth:queued-short-reaffirmation",
            allocation_ids=("allocation:queued-short-reaffirmation",),
            allocation_manifest_hash="5" * 64,
        )
        _install_public_authority(store, reaffirmation_authority)
        executor._public_authority.values[
            reaffirmation_batch.batch_id
        ] = reaffirmation_authority
        corrected_status = "Maya: a reserva existente continua em processamento."
        model.proposals[:] = [
            ModelProposal(
                source_event_id=reaffirmation_batch.batch_id,
                intent="inform",
                reply_chunks=("Qual opção você prefere: dormitório ou quarto privativo?",),
                facts=(),
                read_requests=(),
                effect_proposals=(),
                clarification_question=(
                    "Qual opção você prefere: dormitório ou quarto privativo?"
                ),
            ),
            ModelProposal(
                source_event_id=reaffirmation_batch.batch_id,
                intent="inform",
                reply_chunks=(corrected_status,),
                facts=(),
                read_requests=(),
                effect_proposals=(),
            ),
        ]

        reaffirmation = executor.execute(reaffirmation_batch)

        assert len(read_port.calls) == reads_after_confirmation
        assert reaffirmation.reply_chunks == (corrected_status,)
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.OPERATIONAL_STATUS_CONFLICT,
        )
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone() == (1,)

        faq_event = replace(
            reaffirmation_event,
            event_id="event:queued-faq",
            text="Informe o horário do check-in.",
            occurred_at=NOW + timedelta(seconds=2),
            payload_hash="6" * 64,
        )
        faq_batch = InboundBatch(
            batch_id="batch:queued-faq",
            lead_id=second_batch.lead_id,
            subscriber_id=second_batch.subscriber_id,
            events=(faq_event,),
            combined_text=faq_event.text,
        )
        faq_authority = replace(
            AUTHORITY,
            authorization_id="auth:queued-faq",
            allocation_ids=("allocation:queued-faq",),
            allocation_manifest_hash="6" * 64,
        )
        _install_public_authority(store, faq_authority)
        executor._public_authority.values[faq_batch.batch_id] = faq_authority
        faq_reply = "O check-in começa às 14h."
        model.proposals[:] = [
            ModelProposal(
                source_event_id=faq_batch.batch_id,
                intent="inform",
                reply_chunks=(faq_reply,),
                facts=(),
                read_requests=(),
                effect_proposals=(),
            )
        ]

        faq = executor.execute(faq_batch)

        assert faq.reply_chunks == (faq_reply,)
        assert len(read_port.calls) == reads_after_confirmation
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_commands"
        ).fetchone() == (1,)
    finally:
        store.close()


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
        expired_text = "A autorização expirou antes da execução; nenhuma reserva foi feita."
        model.proposals[1] = replace(
            model.proposals[1],
            reply_chunks=(expired_text,),
        )
        expired = executor.execute(second_batch)
        assert expired.reply_chunks == (expired_text,)
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.CRITICAL_AUTHORITY_EXPIRED,
        )
        assert len(read_port.calls) == 1
        assert len(model.calls) == 4
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
    ("confirmation_text", "review_expected"),
    (
        ("Sim", False),
        ("Pode reservar", False),
        ("Pode sim", False),
        ("Confirmado", False),
        ("Isso mesmo", False),
        ("Sim, por favor", False),
        (
            "Pode reservar esse passeio e gerar o link do sinal no cartão.",
            False,
        ),
        (
            "Sim, confirmo exatamente esse resumo. Pode fazer a reserva agora.",
            True,
        ),
        (
            "O que ficou descrito acima corresponde integralmente ao que quero; "
            "siga com o conjunto completo sem mudar nada.",
            True,
        ),
        (
            "Confirmo exatamente a hospedagem de 10/08/2026 a 12/08/2026 para "
            "2 adultos e 0 crianças. Pode reservar agora.",
            True,
        ),
        ("Confirmed. Please book exactly that summary.", True),
    ),
)
def test_confirmed_turn_commits_reservation_command_and_relay_atomically(
    tmp_path,
    confirmation_text: str,
    review_expected: bool,
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
            CriticalActionKind.RESERVE_LODGING,
        ),
        approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
    )
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
        private_customer_facts=SQLitePrivateCustomerFactStore.open_memory(),
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
        expected_confirmation_reply = "Confirmado."
        assert confirmed.reply_chunks == (expected_confirmation_reply,)
        assert len(confirmed.receipt.command_rows) == 1
        assert len(confirmed.receipt.relay_rows) == 1
        assert len(model.calls) == (5 if review_expected else 4)
        assert model.calls[0].pending_action is None
        assert model.calls[1].pending_action is None
        pending = model.calls[2].pending_action
        assert pending is not None
        assert pending.public_summary == summary.reply_chunks[1]
        assert pending.action_kinds == (
            CriticalActionKind.RESERVE_LODGING,
        )
        assert model.calls[2].confirmation_review_required is False
        review_index = 3 if review_expected else 2
        followup_index = review_index + 1
        assert model.calls[review_index].pending_action == pending
        assert model.calls[review_index].confirmation_review_required is review_expected
        if review_expected:
            assert model.calls[review_index].request_id != model.calls[2].request_id
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


def test_first_model_request_and_committed_language_follow_authenticated_phone() -> (
    None
):
    proposal = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=("Hello. How can I help?",),
        facts=(ModelFact("language", "pt-BR"),),
        read_requests=(),
        effect_proposals=(),
    )
    store = SQLiteBoundaryStore.open_memory_v8()
    model = FakeAuditedModel(store, [proposal])
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=ForeignPhoneOnlyManyChatContact(store),
    )
    try:
        result = executor.execute(BATCH)
        projection = store.load_latest_conversation_projection(BATCH.lead_id)

        assert model.calls[0].locale == "en"
        assert result.reply_chunks == proposal.reply_chunks
        assert projection is not None
        assert projection.locale == "en"
        assert {fact.name: fact.value.value for fact in projection.facts}[
            "language"
        ] == "en"
    finally:
        store.close()


def test_parent_overrides_model_read_locale_before_provider_dispatch() -> None:
    from v2_application import turn_executor as module

    request = ReadRequest(
        request_id="read:locale-authority",
        kind=ReadKind.ACTIVITY,
        locale="pt-BR",
        product_id="product:buracao",
        activity_date=date(2026, 8, 11),
        participants=1,
    )

    localized = module._authoritative_read_locales((request,), locale="en")

    assert localized == (replace(request, locale="en"),)


def test_previously_persisted_private_value_still_requires_model_correction(
    tmp_path,
) -> None:
    private_name = "Pessoa Replay Silva"
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "persisted-private-reply.sqlite3"
    )
    private_store.persist_turn(
        lead_id=BATCH.lead_id,
        source_turn_id="batch:prior-private-owner",
        source_event_hash="1" * 64,
        facts=(ModelFact("full_name", private_name),),
        persisted_at=NOW,
    )
    leaked = _proposal(f"Olá, {private_name}.")
    corrected_text = "Olá! Como posso ajudar?"
    corrected = _proposal(corrected_text)
    model = FakeAuditedModel(store, [leaked, corrected])
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == (corrected_text,)
        assert len(model.calls) == 2
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
        assert private_name not in result.receipt.to_canonical_bytes().decode("utf-8")
    finally:
        private_store.close()
        store.close()


@pytest.mark.parametrize(
    ("private_name", "leaked_text"),
    (
        (
            "Bruno Exemplo",
            "Entendi: Bruno continua como titular da hospedagem.",
        ),
        (
            "Ana dos Santos",
            "Entendi: Ana continua como titular da hospedagem.",
        ),
    ),
)
def test_persisted_full_name_component_requires_model_owned_correction(
    tmp_path,
    private_name: str,
    leaked_text: str,
) -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / f"persisted-private-first-name-{private_name.split()[0]}.sqlite3"
    )
    private_store.persist_turn(
        lead_id=BATCH.lead_id,
        source_turn_id="batch:prior-private-holder",
        source_event_hash="2" * 64,
        facts=(ModelFact("full_name", private_name),),
        persisted_at=NOW,
    )
    leaked = _proposal(leaked_text)
    corrected_text = "Entendi: o acompanhante continua como titular da hospedagem."
    corrected = _proposal(corrected_text)
    model = FakeAuditedModel(store, [leaked, corrected])
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == (corrected_text,)
        assert len(model.calls) == 2
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
    finally:
        private_store.close()
        store.close()


def test_short_full_name_particle_does_not_block_common_public_word(tmp_path) -> None:
    private_name = "Ana dos Santos"
    public_text = "A hospedagem dos dois adultos ainda não foi reservada."
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore(
        tmp_path / "short-private-name-particle.sqlite3"
    )
    private_store.persist_turn(
        lead_id=BATCH.lead_id,
        source_turn_id="batch:prior-private-holder-particle",
        source_event_hash="3" * 64,
        facts=(ModelFact("full_name", private_name),),
        persisted_at=NOW,
    )
    model = FakeAuditedModel(store, [_proposal(public_text)])
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == (public_text,)
        assert len(model.calls) == 1
    finally:
        private_store.close()
        store.close()


@pytest.mark.parametrize(
    ("fact_name", "accepted_value", "public_text"),
    (
        (
            "email",
            "Alice@Example.INVALID",
            "Contato Alice@Example.INVALID confirmado.",
        ),
        (
            "full_name",
            "Pessoa Privada Silva",
            "prefixPessoa Privada SilvaX",
        ),
    ),
)
def test_private_gate_covers_raw_and_embedded_high_entropy_literals(
    fact_name: str,
    accepted_value: str,
    public_text: str,
) -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    leaked = replace(
        _proposal(public_text),
        facts=(ModelFact(fact_name, accepted_value),),
    )
    corrected_text = "Contato registrado sem expor dados privados."
    corrected = _proposal(corrected_text)
    model = FakeAuditedModel(store, [leaked, corrected])
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == (corrected_text,)
        assert len(model.calls) == 2
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
        assert public_text not in result.receipt.to_canonical_bytes().decode("utf-8")
    finally:
        private_store.close()
        store.close()


def test_low_entropy_private_code_inside_public_word_is_not_exposure() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    maya_text = "A diária permanece em BRL 480.00."
    proposal = replace(
        _proposal(maya_text),
        facts=(ModelFact("country_code", "BR"),),
    )
    model = FakeAuditedModel(store, [proposal])
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=PhoneOnlyManyChatContact(store),
        private_customer_facts=private_store,
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == (maya_text,)
        assert len(model.calls) == 1
    finally:
        private_store.close()
        store.close()


def test_passenger_private_value_requires_terminal_model_correction() -> None:
    private_name = "Pessoa Passageira Fictícia"
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    leaked = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(f"Registrei {private_name}.",),
        facts=(
            ModelFact("service", "agency"),
            ModelFact("adults", 1),
            ModelFact("children", 0),
        ),
        read_requests=(),
        effect_proposals=(),
        passengers=(
            PassengerInput(
                position=1,
                participant_type="adult",
                full_name=private_name,
                birth_date=None,
                gender=None,
                country_code=None,
            ),
        ),
    )
    corrected_text = "Registrei os dados da pessoa participante."
    corrected = replace(leaked, reply_chunks=(corrected_text,))
    model = FakeAuditedModel(store, [leaked, corrected])
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=UnusableManyChatPhone(store, "missing"),
        private_customer_facts=private_store,
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == (corrected_text,)
        assert len(model.calls) == 2
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
        assert private_name not in result.receipt.to_canonical_bytes().decode("utf-8")
    finally:
        private_store.close()
        store.close()


def test_public_reply_correction_budget_survives_commit_retry() -> None:
    private_name = "Pessoa Conflict Silva"
    inner = SQLiteBoundaryStore.open_memory_v8()
    store = OneCommitConflictStore(inner)
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    leaked = replace(
        _proposal(f"Olá, {private_name}."),
        facts=(ModelFact("full_name", private_name),),
    )
    corrected = _proposal("Olá! Como posso ajudar?")
    model = FakeAuditedModel(inner, [leaked, corrected, leaked, corrected])
    _install_public_authority(inner)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=PhoneOnlyManyChatContact(inner),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        with pytest.raises(
            TurnExecutionError,
            match="public reply correction was already consumed",
        ):
            executor.execute(BATCH)

        assert store.conflicts == 1
        assert len(model.calls) == 3
        assert model.proposals == [corrected]
        assert inner.turn_receipt_count(BATCH.batch_id) == 0
        for table in (
            "boundary_commands",
            "boundary_command_relays",
            "boundary_outbox",
            "boundary_public_outbox",
        ):
            assert inner._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone() == (0,)
    finally:
        private_store.close()
        inner.close()


def test_private_raw_value_corpus_survives_correction_and_commit_retry() -> None:
    raw_email = "Alice@Example.INVALID"
    canonical_email = "alice@example.invalid"
    inner = SQLiteBoundaryStore.open_memory_v8()
    store = OneCommitConflictStore(inner)
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    first_leak = replace(
        _proposal(f"Contato {raw_email} confirmado."),
        facts=(ModelFact("email", raw_email),),
    )
    first_correction = _proposal("Contato registrado sem expor dados privados.")
    retry_leak_without_fact = _proposal(f"Contato {raw_email} confirmado.")
    unused_second_correction = _proposal("Esta quarta chamada não pode acontecer.")
    model = FakeAuditedModel(
        inner,
        [
            first_leak,
            first_correction,
            retry_leak_without_fact,
            unused_second_correction,
        ],
    )
    _install_public_authority(inner)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=PhoneOnlyManyChatContact(inner),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        with pytest.raises(
            TurnExecutionError,
            match="public reply correction was already consumed",
        ):
            executor.execute(BATCH)

        assert store.conflicts == 1
        assert len(model.calls) == 3
        assert model.proposals == [unused_second_correction]
        private_snapshot = private_store.load(BATCH.lead_id)
        assert private_snapshot.email == canonical_email
        assert raw_email not in repr(private_snapshot)
        assert inner.turn_receipt_count(BATCH.batch_id) == 0
        for table in (
            "boundary_commands",
            "boundary_command_relays",
            "boundary_outbox",
            "boundary_public_outbox",
            "boundary_turn_artifacts",
        ):
            assert inner._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone() == (0,)
    finally:
        private_store.close()
        inner.close()


def test_passenger_country_raw_variant_corpus_survives_commit_retry() -> None:
    raw_country = "br"
    inner = SQLiteBoundaryStore.open_memory_v8()
    store = OneCommitConflictStore(inner)
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    first_leak = ModelProposal(
        source_event_id=BATCH.batch_id,
        intent="inform",
        reply_chunks=(f"País {raw_country} confirmado.",),
        facts=(
            ModelFact("service", "agency"),
            ModelFact("adults", 1),
            ModelFact("children", 0),
        ),
        read_requests=(),
        effect_proposals=(),
        passengers=(
            PassengerInput(
                position=1,
                participant_type="adult",
                full_name=None,
                birth_date=None,
                gender=None,
                country_code=raw_country,
            ),
        ),
    )
    first_correction = replace(
        first_leak,
        reply_chunks=("País registrado sem expor dados privados.",),
    )
    retry_leak_without_passengers = _proposal(f"País {raw_country} confirmado.")
    unused_second_correction = _proposal("Esta quarta chamada não pode acontecer.")
    model = FakeAuditedModel(
        inner,
        [
            first_leak,
            first_correction,
            retry_leak_without_passengers,
            unused_second_correction,
        ],
    )
    _install_public_authority(inner)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService({}),
        profile=PhoneOnlyManyChatContact(inner),
        private_customer_facts=private_store,
        reducer=_enabled_reducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )
    try:
        with pytest.raises(
            TurnExecutionError,
            match="public reply correction was already consumed",
        ):
            executor.execute(BATCH)

        assert store.conflicts == 1
        assert len(model.calls) == 3
        assert model.proposals == [unused_second_correction]
        manifest = private_store.load_passenger_manifest(BATCH.lead_id)
        assert manifest is not None
        assert '"country_code":"BR"' in manifest.value.value
        assert inner.turn_receipt_count(BATCH.batch_id) == 0
        for table in (
            "boundary_commands",
            "boundary_command_relays",
            "boundary_outbox",
            "boundary_public_outbox",
            "boundary_turn_artifacts",
        ):
            assert inner._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone() == (0,)
    finally:
        private_store.close()
        inner.close()


def test_turn_executor_never_assigns_model_owned_text_in_replace_calls() -> None:
    module = inspect.getmodule(V2TurnExecutor)
    assert module is not None
    tree = ast.parse(inspect.getsource(module))
    forbidden = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "replace":
            continue
        assigned = {
            keyword.arg
            for keyword in node.keywords
            if keyword.arg in {"reply_chunks", "clarification_question"}
        }
        if assigned:
            forbidden.append((node.lineno, tuple(sorted(assigned))))

    assert forbidden == []


def test_productively_invalid_correction_uses_terminal_turn_error() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    request = ModelRequest(
        request_id="model-request:invalid-correction",
        lead_id=BATCH.lead_id,
        source_event_id=BATCH.batch_id,
        message=BATCH.combined_text,
        locale="pt-BR",
        state_version=0,
    )
    expected = _proposal("Maya precisa corrigir esta resposta.")
    invalid = replace(
        expected,
        reply_chunks=("Correction with a forbidden effect.",),
        effect_proposals=(EffectProposal("forbidden_effect", {}),),
    )
    model = FakeAuditedModel(store, [expected, invalid])
    audited = model.complete_audited(request)
    try:
        with pytest.raises(
            TurnExecutionError,
            match="public reply correction remained invalid",
        ):
            _request_public_reply_correction(
                model,
                request=request,
                audited=audited,
                expected=expected,
                reason=PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
                private_values=(),
            )
    finally:
        store.close()


def test_confirmation_read_divergence_requires_complete_model_owned_correction() -> None:
    store, model, _read_port, second_batch, executor = _approval_expiry_fixture(
        approval_ttl=timedelta(minutes=5),
        confirmation_clock=FixedClock(),
    )
    confirmation = model.proposals[0]
    divergent = ModelProposal(
        source_event_id=second_batch.batch_id,
        intent="inform",
        reply_chunks=("A consulta mudou o contexto da confirmação.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    corrected_text = "Confirmação mantida após a consulta atualizada."
    corrected = replace(confirmation, reply_chunks=(corrected_text,))
    model.proposals[:] = [confirmation, divergent, corrected]
    prior_calls = len(model.calls)
    try:
        result = executor.execute(second_batch)

        assert result.reply_chunks != divergent.reply_chunks
        assert len(model.calls) == prior_calls + 3
        assert model.proposals == []
        correction_request = model.calls[-1]
        assert correction_request.public_reply_correction_reasons == (
            PublicReplyCorrectionReason.OPERATIONAL_STATUS_CONFLICT,
        )
    finally:
        store.close()


def test_authenticated_profile_private_value_requires_model_correction() -> None:
    private_name = "Pessoa Teste"
    store = SQLiteBoundaryStore.open_memory_v8()
    leaked = _proposal(f"Olá, {private_name}.")
    corrected_text = "Olá! Como posso ajudar?"
    corrected = _proposal(corrected_text)
    model = FakeAuditedModel(store, [leaked, corrected])
    _install_public_authority(store)
    executor = _executor(
        store=store,
        model=model,
        profile=FakeProfile(store),
    )
    try:
        result = executor.execute(BATCH)

        assert result.reply_chunks == (corrected_text,)
        assert len(model.calls) == 2
        assert model.calls[-1].public_reply_correction_reasons == (
            PublicReplyCorrectionReason.PRIVATE_VALUE_EXPOSURE,
        )
    finally:
        store.close()
