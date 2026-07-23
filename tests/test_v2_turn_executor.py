from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

import pytest

from reservation_boundary.conversation import ConversationProjection
from reservation_boundary.effects import HandoffRelayBundle, ReservationRelayBundle
from reservation_boundary.sqlite_store import ConcurrencyConflict, SQLiteBoundaryStore
from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_application.conversation import V2ConversationReducer
from v2_application.public_delivery import (
    BoundaryPublicDeliveryWorker,
    BoundaryPublicDisposition,
)
from v2_application.reads import V2ReadService
from v2_application.relay_worker import BoundaryRelayWorker, RelayWorkerDisposition
from v2_application.turn_executor import (
    PublicTurnAuthority,
    TurnExecutionError,
    V2TurnExecutor,
)
from v2_contracts.channel import InboundBatch, InboundEvent, PublicDeliveryUnknown
from v2_contracts.model import AuditedModelTurn, ModelFact, ModelProposal, ModelRequest
from v2_contracts.profile import PrivateCustomerBinding
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

NOW = datetime(2026, 7, 23, 22, 0, tzinfo=timezone.utc)
TRANSCRIPT_KEY = b"t" * 32
CAPABILITY_DIGEST = "a" * 64
EFFECT_DIGEST = "b" * 64
TARGET_DIGEST = "c" * 64
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
        reducer=V2ConversationReducer(),
        public_authority=FixedAuthority(),
        clock=FixedClock(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=2,
    )


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
        reducer=V2ConversationReducer(),
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


def test_confirmed_turn_commits_reservation_command_and_relay_atomically(
    tmp_path,
) -> None:
    second_event = InboundEvent(
        event_id="event:turn-executor-002",
        lead_id=BATCH.lead_id,
        subscriber_id=BATCH.subscriber_id,
        conversation_id=EVENT.conversation_id,
        text="Confirmo a proposta.",
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
    confirmation_read = ReadRequest(
        request_id="read:confirmation-lodging-002",
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
        ModelProposal(
            source_event_id=second_batch.batch_id,
            intent="inform",
            reply_chunks=(),
            facts=(),
            read_requests=(confirmation_read,),
            effect_proposals=(),
        ),
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
        reducer=V2ConversationReducer(),
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

        assert summary.receipt.committed_state_version == 1
        assert confirmed.receipt.committed_state_version == 2
        assert len(confirmed.receipt.command_rows) == 1
        assert len(confirmed.receipt.relay_rows) == 1
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
