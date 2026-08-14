from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from reservation_boundary import SQLiteBoundaryStore
from v2_application.conversation import V2ConversationReducer
from v2_application.critical_actions import CriticalActionPolicy
from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
from v2_application.reads import V2ReadService
from v2_application.turn_executor import PublicTurnAuthority, V2TurnExecutor
from v2_contracts.channel import InboundBatch, InboundEvent
from v2_contracts.critical_actions import CriticalActionKind
from v2_contracts.model import AuditedModelTurn, ModelProposal, ModelRequest
from v2_contracts.profile import PrivateCustomerBinding
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
START = date(2026, 9, 10)
TRANSCRIPT_KEY = b"m" * 32


def _candidate(
    product_id: str,
    public_name: str,
    *,
    day: int = 10,
    group_status: str = "not_matched",
    frequent: bool = False,
    duration_days: int = 1,
) -> dict[str, object]:
    return {
        "product_id": product_id,
        "product_public_name": public_name,
        "activity_date": date(2026, 9, day).isoformat(),
        "duration_days": duration_days,
        "total_amount": "300.00",
        "currency": "BRL",
        "group_status": group_status,
        "existing_group": group_status == "matched",
        "frequent_alternative": frequent,
    }


class ScriptedModel:
    def __init__(self, store: SQLiteBoundaryStore, proposals: list[ModelProposal]) -> None:
        self.store = store
        self.proposals = proposals
        self.calls: list[ModelRequest] = []
        self.completed: list[ModelProposal] = []

    def complete_audited(self, request: ModelRequest) -> AuditedModelTurn:
        assert self.store._connection.in_transaction is False
        self.calls.append(request)
        proposal = self.proposals.pop(0)
        self.completed.append(proposal)
        response = f"{proposal.source_event_id}|{proposal.intent}".encode()
        return AuditedModelTurn.from_exchange(
            proposal=proposal,
            stdin_bytes=f"{request.request_id}|{len(request.observations)}".encode(),
            stdout_bytes=b"TASK6_RESULT\x00" + response,
            response_bytes=response,
            transcript_key=TRANSCRIPT_KEY,
            ephemeral_session_id="uds:task-6-scripted-model",
        )


class FakeManyChatProfile:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store
        self.calls: list[str] = []

    def read(self, lead_id: str, *, now: datetime) -> PrivateCustomerBinding:
        assert self.store._connection.in_transaction is False
        self.calls.append(lead_id)
        return PrivateCustomerBinding(
            binding_id="profile-binding:" + "d" * 64,
            content_hash="e" * 64,
            full_name=None,
            email=None,
            phone_e164="+5575999990199",
            country_code="BR",
            observed_at=now,
            expires_at=now + timedelta(minutes=5),
            complete=False,
        )


class FakeRecommendationReadPort:
    def __init__(
        self,
        store: SQLiteBoundaryStore,
        candidates: tuple[dict[str, object], ...],
        *,
        group_source_status: str = "available",
    ) -> None:
        self.store = store
        self.candidates = candidates
        self.group_source_status = group_source_status
        self.calls: list[ReadRequest] = []
        self.write_calls: list[object] = []

    def read(self, request: ReadRequest) -> ReadObservation:
        assert self.store._connection.in_transaction is False
        self.calls.append(request)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="bokun",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            public_payload={
                "recommendation_period": {
                    "start": request.period_start.isoformat(),
                    "end": request.period_end.isoformat(),
                },
                "party": {"adults": request.adults, "children": request.children},
                "group_source_status": self.group_source_status,
                "candidates": list(self.candidates),
                "candidate_count": len(self.candidates),
            },
            private_binding_hash="4" * 64,
        )

    def write(self, value: object) -> None:
        self.write_calls.append(value)
        raise AssertionError("provider writes are forbidden in Task 6")


class FakeOrdinaryActivityReadPort:
    def __init__(self, store: SQLiteBoundaryStore) -> None:
        self.store = store
        self.calls: list[ReadRequest] = []
        self.write_calls: list[object] = []

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
                "product_id": request.product_id,
                "product_public_name": request.product_id.removeprefix("product:").title(),
                "activity_date": request.activity_date.isoformat(),
                "adults": adults,
                "children": children,
                "participants": adults + children,
                "total_amount": "300.00",
                "currency": "BRL",
                "available": True,
                "group_status": "not_matched",
                "existing_group": False,
                "group_participants": None,
                "solo_group_booking": False,
            },
            private_binding_hash="5" * 64,
        )

    def write(self, value: object) -> None:
        self.write_calls.append(value)
        raise AssertionError("provider writes are forbidden in Task 6")


class MappingAuthority:
    def __init__(self, authorities: dict[str, PublicTurnAuthority]) -> None:
        self.authorities = authorities

    def resolve(
        self,
        batch: InboundBatch,
        *,
        chunk_count: int,
        now: datetime,
    ) -> PublicTurnAuthority:
        authority = self.authorities[batch.batch_id]
        assert chunk_count == len(authority.allocation_ids)
        assert NOW <= now < authority.deadline_at
        return authority


@dataclass
class ScenarioHarness:
    store: SQLiteBoundaryStore
    private_store: SQLitePrivateCustomerFactStore
    executor: V2TurnExecutor
    model: ScriptedModel
    recommendation_port: FakeRecommendationReadPort
    ordinary_port: FakeOrdinaryActivityReadPort
    batches: tuple[InboundBatch, ...]
    payment_calls: list[object]
    handoff_calls: list[object]
    manychat_delivery_calls: list[object]

    def close(self) -> None:
        self.private_store.close()
        self.store.close()


def _reducer() -> V2ConversationReducer:
    return V2ConversationReducer(
        approval_ttl=timedelta(minutes=30),
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
        ),
    )


def _batch(index: int, message: str) -> InboundBatch:
    event = InboundEvent(
        event_id=f"event:task6:{index}",
        lead_id="manychat:lead-task6",
        subscriber_id="subscriber-task6",
        conversation_id="manychat:conversation-task6",
        text=message,
        media_url=None,
        media_type=None,
        occurred_at=NOW - timedelta(seconds=1),
        payload_hash=f"{index:x}" * 64,
    )
    return InboundBatch(
        batch_id=f"batch:task6:{index}",
        lead_id=event.lead_id,
        subscriber_id=event.subscriber_id,
        events=(event,),
        combined_text=message,
    )


def _authority(index: int, batch: InboundBatch) -> PublicTurnAuthority:
    digest = f"{index:x}" * 64
    return PublicTurnAuthority(
        authorization_kind="conversation_test",
        authorization_id=f"auth:task6:{index}",
        scope_subject_id=batch.subscriber_id,
        target_binding_hash="c" * 64,
        channel_id="manychat:channel-task6",
        channel_scope=batch.events[0].conversation_id,
        immutable_generation=index,
        allocation_ids=(f"allocation:task6:{index}",),
        capability_policy_digest="a" * 64,
        effect_authorization_binding_digest="b" * 64,
        contract_digest="f" * 64,
        allocation_manifest_hash=digest,
        deadline_at=NOW + timedelta(minutes=1),
    )


def _install_authority(store: SQLiteBoundaryStore, authority: PublicTurnAuthority) -> None:
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
        store._connection.execute(
            "INSERT INTO boundary_dispatch_authority "
            "(authorization_id,scope_subject_id,channel_scope,generation,allocation_id,"
            "row_kind,authorization_kind,qualification_id,scenario_id,contract_digest,"
            "effect_authorization_binding_digest,capability_policy_digest,target_binding_hash,"
            "allowed_chunk_ordinal,allocation_manifest_hash,state,public_row_id,cas_revision,"
            "closure_receipt_hash,created_at,updated_at,fenced_at) "
            "VALUES (?,?,?,?,?,'allocation',?,?,?,?,?,?,?,?,?,'available',NULL,0,NULL,?,?,NULL)",
            common[:4]
            + (authority.allocation_ids[0],)
            + common[4:11]
            + (0, common[11], NOW.isoformat(), NOW.isoformat()),
        )


def _recommendation_request(
    batch: InboundBatch,
    *,
    adults: int,
    days: int = 3,
) -> ReadRequest:
    return ReadRequest(
        request_id=f"{batch.batch_id}:recommendation",
        kind=ReadKind.ACTIVITY_RECOMMENDATION,
        period_start=START,
        period_end=START + timedelta(days=days - 1),
        adults=adults,
        children=0,
        locale="pt-BR",
    )


def _proposal(
    batch: InboundBatch,
    text: str,
    *,
    reads: tuple[ReadRequest, ...] = (),
) -> ModelProposal:
    return ModelProposal(
        source_event_id=batch.batch_id,
        intent="inform",
        reply_chunks=(text,),
        facts=(),
        read_requests=reads,
        effect_proposals=(),
    )


def _harness(
    *,
    messages: tuple[str, ...],
    proposals_for: Any,
    candidates: tuple[dict[str, object], ...],
    group_source_status: str = "available",
) -> ScenarioHarness:
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    batches = tuple(_batch(index, message) for index, message in enumerate(messages, 1))
    proposals = proposals_for(batches)
    model = ScriptedModel(store, proposals)
    recommendation_port = FakeRecommendationReadPort(
        store,
        candidates,
        group_source_status=group_source_status,
    )
    ordinary_port = FakeOrdinaryActivityReadPort(store)
    authorities = {
        batch.batch_id: _authority(index, batch)
        for index, batch in enumerate(batches, 1)
    }
    for authority in authorities.values():
        _install_authority(store, authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService(
            {
                ReadKind.ACTIVITY_RECOMMENDATION: recommendation_port,
                ReadKind.ACTIVITY: ordinary_port,
            }
        ),
        profile=FakeManyChatProfile(store),
        private_customer_facts=private_store,
        reducer=_reducer(),
        public_authority=MappingAuthority(authorities),
        clock=type("FixedClock", (), {"now": lambda self: NOW})(),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    return ScenarioHarness(
        store,
        private_store,
        executor,
        model,
        recommendation_port,
        ordinary_port,
        batches,
        [],
        [],
        [],
    )


def _assert_zero_effects(harness: ScenarioHarness, *results: Any) -> None:
    for result in results:
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
    assert harness.recommendation_port.write_calls == []
    assert harness.ordinary_port.write_calls == []
    assert all(proposal.effect_proposals == () for proposal in harness.model.completed)
    assert harness.payment_calls == []
    assert harness.handoff_calls == []
    assert harness.manychat_delivery_calls == []


def _run_recommendation(
    *,
    message: str,
    candidates: tuple[dict[str, object], ...],
    reply: str,
    adults: int = 2,
    days: int = 3,
    group_source_status: str = "available",
) -> tuple[ScenarioHarness, Any]:
    def proposals_for(batches: tuple[InboundBatch, ...]) -> list[ModelProposal]:
        batch = batches[0]
        return [
            _proposal(
                batch,
                "Vou comparar opções adequadas para o período.",
                reads=(_recommendation_request(batch, adults=adults, days=days),),
            ),
            _proposal(batch, reply),
        ]

    harness = _harness(
        messages=(message,),
        proposals_for=proposals_for,
        candidates=candidates,
        group_source_status=group_source_status,
    )
    return harness, harness.executor.execute(harness.batches[0])


def test_period_preference_mentions_matched_candidate_first_among_suitable_options() -> None:
    candidates = (
        _candidate("product:marimbus", "Marimbus", group_status="matched"),
        _candidate("product:sossego", "Sossego"),
    )
    reply = "Marimbus é a primeira opção adequada e tem grupo formado; Sossego vem depois."
    harness, result = _run_recommendation(
        message="Estarei de 10 a 12 de setembro e prefiro água e natureza, sem trilha pesada.",
        candidates=candidates,
        reply=reply,
    )
    try:
        assert result.reply_chunks == (reply,)
        assert reply.index("Marimbus") < reply.index("Sossego")
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_suitability_precedes_group_priority_when_grouped_option_is_strenuous() -> None:
    candidates = (
        _candidate("product:buracao", "Buracão", group_status="matched"),
        _candidate("product:sossego", "Sossego"),
    )
    reply = "Sossego combina melhor com seu pedido leve; Buracão tem grupo, mas é mais exigente."
    harness, result = _run_recommendation(
        message="Quero algo bem leve, sem trilha puxada, entre 10 e 12 de setembro.",
        candidates=candidates,
        reply=reply,
    )
    try:
        observed = harness.model.calls[1].observations[0].public_payload["candidates"]
        assert observed[0]["product_id"] == "product:buracao"
        assert reply.index("Sossego") < reply.index("Buracão")
        assert result.reply_chunks == (reply,)
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_no_groups_still_recommends_bokun_available_options() -> None:
    candidates = (
        _candidate("product:sossego", "Sossego"),
        _candidate("product:tour-4ps", "4Ps", frequent=True),
    )
    reply = "Mesmo sem grupo formado, Sossego e 4Ps estão disponíveis no Bókun."
    harness, result = _run_recommendation(
        message="O que está disponível de 10 a 12 de setembro para duas pessoas?",
        candidates=candidates,
        reply=reply,
    )
    try:
        observation = harness.model.calls[1].observations[0]
        assert observation.public_payload["candidate_count"] == 2
        assert all(item["existing_group"] is False for item in candidates)
        assert result.reply_chunks == (reply,)
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_unavailable_group_source_makes_no_group_claim_and_keeps_recommendation() -> None:
    candidates = (_candidate("product:sossego", "Sossego", group_status="unavailable"),)
    reply = "Sossego segue disponível no Bókun; não consegui consultar a fonte de grupos agora."
    harness, result = _run_recommendation(
        message="Pode recomendar um passeio leve de 10 a 12 de setembro?",
        candidates=candidates,
        reply=reply,
        group_source_status="unavailable",
    )
    try:
        observation = harness.model.calls[1].observations[0]
        assert observation.public_payload["group_source_status"] == "unavailable"
        assert "grupo formado" not in reply
        assert "Sossego" in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_arbitrary_non_frequent_product_uses_fresh_ordinary_activity_read() -> None:
    def proposals_for(batches: tuple[InboundBatch, ...]) -> list[ModelProposal]:
        recommendation_batch, arbitrary_batch = batches
        recommendation = _recommendation_request(recommendation_batch, adults=2)
        ordinary = ReadRequest(
            request_id=f"{arbitrary_batch.batch_id}:catacumbas",
            kind=ReadKind.ACTIVITY,
            product_id="product:catacumbas",
            activity_date=date(2026, 9, 12),
            participants=2,
            locale="pt-BR",
        )
        return [
            _proposal(
                recommendation_batch,
                "Vou comparar algumas opções.",
                reads=(recommendation,),
            ),
            _proposal(
                recommendation_batch,
                "Marimbus é uma sugestão; estes não são os únicos passeios que podem ser consultados.",
            ),
            _proposal(
                arbitrary_batch,
                "Vou consultar o Catacumbas.",
                reads=(ordinary,),
            ),
            _proposal(
                arbitrary_batch,
                "Catacumbas está disponível para duas pessoas em 12 de setembro.",
            ),
        ]

    harness = _harness(
        messages=(
            "Quais passeios você recomenda de 10 a 12 de setembro?",
            "Além dessas sugestões, quero saber do Catacumbas em 12 de setembro para duas pessoas.",
        ),
        proposals_for=proposals_for,
        candidates=(_candidate("product:marimbus", "Marimbus"),),
    )
    try:
        recommendation_result = harness.executor.execute(harness.batches[0])
        arbitrary_result = harness.executor.execute(harness.batches[1])
        assert len(harness.ordinary_port.calls) == 1
        assert harness.ordinary_port.calls[0].kind is ReadKind.ACTIVITY
        assert harness.ordinary_port.calls[0].product_id == "product:catacumbas"
        assert len(harness.recommendation_port.calls) == 1
        recommendation_text = recommendation_result.reply_chunks[0].lower()
        assert "não são os únicos passeios" in recommendation_text
        assert "só estes passeios podem" not in recommendation_text
        _assert_zero_effects(harness, recommendation_result, arbitrary_result)
    finally:
        harness.close()


def test_4ps_without_group_is_frequent_but_never_claimed_as_confirmed_departure() -> None:
    candidates = (_candidate("product:tour-4ps", "4Ps", frequent=True),)
    reply = "4Ps é uma alternativa frequente e está disponível, mas não há grupo ou saída confirmada."
    harness, result = _run_recommendation(
        message="Que passeio costuma ser uma opção entre 10 e 12 de setembro?",
        candidates=candidates,
        reply=reply,
    )
    try:
        candidate = harness.model.calls[1].observations[0].public_payload["candidates"][0]
        assert candidate["frequent_alternative"] is True
        assert candidate["existing_group"] is False
        assert "não há grupo ou saída confirmada" in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_pati_3d_is_absent_when_period_has_no_three_day_window() -> None:
    candidates = (_candidate("product:tour-4ps", "4Ps", frequent=True),)
    reply = "Para 10 e 11 de setembro, a alternativa observada é 4Ps."
    harness, result = _run_recommendation(
        message="Tenho apenas 10 e 11 de setembro; o que você recomenda?",
        candidates=candidates,
        reply=reply,
        days=2,
    )
    try:
        observed = harness.model.calls[1].observations[0].public_payload["candidates"]
        assert all(item["product_id"] != "product:pati-3d" for item in observed)
        assert "Pati" not in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_restricted_solo_product_without_group_is_absent_under_existing_policy() -> None:
    candidates = (_candidate("product:tour-4ps", "4Ps", frequent=True),)
    reply = "Para uma pessoa sem grupo, Buracão não está disponível; posso indicar 4Ps."
    harness, result = _run_recommendation(
        message="Viajo sozinho e queria Buracão entre 10 e 12 de setembro.",
        candidates=candidates,
        reply=reply,
        adults=1,
    )
    try:
        observed = harness.model.calls[1].observations[0].public_payload["candidates"]
        assert all(item["product_id"] != "product:buracao" for item in observed)
        assert "não está disponível" in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_two_people_keep_same_product_available_without_group() -> None:
    candidates = (_candidate("product:buracao", "Buracão"),)
    reply = "Buracão continua disponível para duas pessoas, mesmo sem grupo formado."
    harness, result = _run_recommendation(
        message="Somos duas pessoas e queremos Buracão em 10 de setembro.",
        candidates=candidates,
        reply=reply,
        adults=2,
    )
    try:
        candidate = harness.model.calls[1].observations[0].public_payload["candidates"][0]
        assert candidate["product_id"] == "product:buracao"
        assert candidate["existing_group"] is False
        assert "continua disponível" in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_choice_after_recommendation_performs_fresh_ordinary_activity_read() -> None:
    candidates = (_candidate("product:marimbus", "Marimbus", group_status="matched"),)

    def proposals_for(batches: tuple[InboundBatch, ...]) -> list[ModelProposal]:
        recommendation_batch, choice_batch = batches
        recommendation = _recommendation_request(recommendation_batch, adults=2)
        ordinary = ReadRequest(
            request_id=f"{choice_batch.batch_id}:marimbus:fresh",
            kind=ReadKind.ACTIVITY,
            product_id="product:marimbus",
            activity_date=START,
            participants=2,
            locale="pt-BR",
        )
        return [
            _proposal(recommendation_batch, "Vou comparar.", reads=(recommendation,)),
            _proposal(
                recommendation_batch,
                "Marimbus é adequado, tem grupo formado e é apenas uma recomendação; outros passeios também podem ser consultados.",
            ),
            _proposal(
                choice_batch,
                "Vou atualizar a disponibilidade de Marimbus.",
                reads=(ordinary,),
            ),
            _proposal(choice_batch, "Marimbus continua disponível após a nova consulta."),
        ]

    harness = _harness(
        messages=(
            "Recomende algo com água para 10 a 12 de setembro.",
            "Escolho Marimbus no dia 10 para duas pessoas.",
        ),
        proposals_for=proposals_for,
        candidates=candidates,
    )
    try:
        recommendation_result = harness.executor.execute(harness.batches[0])
        choice_result = harness.executor.execute(harness.batches[1])
        assert len(harness.recommendation_port.calls) == 1
        assert len(harness.ordinary_port.calls) == 1
        assert harness.ordinary_port.calls[0].kind is ReadKind.ACTIVITY
        assert harness.ordinary_port.calls[0].request_id.endswith(":marimbus:fresh")
        assert harness.model.calls[2].observations == ()
        assert harness.model.calls[3].observations[0].public_payload["product_id"] == (
            "product:marimbus"
        )
        recommendation_text = recommendation_result.reply_chunks[0].lower()
        assert "outros passeios também podem ser consultados" in recommendation_text
        assert "só estes passeios" not in recommendation_text
        _assert_zero_effects(harness, recommendation_result, choice_result)
    finally:
        harness.close()
