from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from reservation_boundary import SQLiteBoundaryStore
from reservation_domain import AwaitingConfirmationState
from v2_adapters._provider_common import binding_hash
from v2_adapters.activity_recommendations import ActivityRecommendationReadAdapter
from v2_adapters.bokun import BokunReadAdapter
from v2_adapters.bokun_groups import (
    GroupDateCandidate,
    GroupLookupResult,
    load_activity_group_policy,
)
from v2_adapters.group_enriched_activity import GroupEnrichedActivityReadAdapter
from v2_application.conversation import V2ConversationReducer
from v2_application.critical_actions import CriticalActionPolicy
from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
from v2_application.reads import V2ReadService
from v2_application.turn_executor import PublicTurnAuthority, V2TurnExecutor
from v2_contracts.channel import InboundBatch, InboundEvent
from v2_contracts.critical_actions import CriticalActionKind
from v2_contracts.model import (
    AuditedModelTurn,
    ModelFact,
    ModelProposal,
    ModelRequest,
)
from v2_contracts.profile import PrivateCustomerBinding
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
START = date(2026, 9, 10)
TRANSCRIPT_KEY = b"m" * 32
READ_OPERATIONS = frozenset({"activity"})
PRODUCT_NAMES = {
    "product:buracao": "Buracão",
    "product:catacumbas": "Catacumbas",
    "product:marimbus": "Marimbus",
    "product:pati-3d": "Pati 3 dias",
    "product:sossego": "Sossego",
    "product:tour-4ps": "4Ps",
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
            full_name="Pessoa Teste",
            email="person@example.invalid",
            phone_e164="+5571999990199",
            country_code="BR",
            observed_at=now,
            expires_at=now + timedelta(minutes=5),
            complete=True,
        )


class ControlledGroupDiscovery:
    """Sanitized fake at the private group-source boundary only."""

    def __init__(
        self,
        discovered: tuple[GroupDateCandidate, ...] | None,
        *,
        lookup_status: str = "not_matched",
    ) -> None:
        self.discovered = discovered
        self.lookup_status = lookup_status
        self.discover_calls: list[tuple[date, date, int]] = []
        self.lookup_calls: list[tuple[str, date]] = []

    def discover(
        self, *, period_start: date, period_end: date, max_candidates: int
    ) -> tuple[GroupDateCandidate, ...] | None:
        self.discover_calls.append((period_start, period_end, max_candidates))
        return self.discovered

    def lookup(
        self, *, canonical_product_id: str, activity_date: date
    ) -> GroupLookupResult:
        self.lookup_calls.append((canonical_product_id, activity_date))
        status = self.lookup_status
        return GroupLookupResult(
            status=status,  # type: ignore[arg-type]
            canonical_product_id=canonical_product_id,
            activity_date=activity_date,
            participant_count=3 if status == "matched" else None,
        )


class ControlledBokunGetTransport:
    """Controlled GET-like Bókun source; any non-read operation fails closed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        recorded = dict(payload)
        self.calls.append((operation, recorded))
        if operation not in READ_OPERATIONS:
            raise AssertionError(f"non-read Bókun operation is forbidden: {operation}")
        product_id = recorded["product_id"]
        assert type(product_id) is str
        assert recorded.get("activity_date") is not None
        assert recorded.get("quote_scope") is not None
        public_name = PRODUCT_NAMES[product_id]
        if recorded.get("availability_only") is True:
            return {
                "product_id": product_id,
                "product_public_name": public_name,
                "total_amount": "0.00",
                "currency": "BRL",
                "available": False,
            }
        slug = product_id.removeprefix("product:")
        return {
            "product_id": product_id,
            "bokun_product_id": recorded.get("expected_bokun_product_id", f"bokun-{slug}"),
            "start_time_id": f"start-{slug}",
            "rate_id": recorded.get("expected_rate_id", f"rate-{slug}"),
            "adult_pricing_category_id": recorded.get(
                "expected_adult_category_id", f"adult-{slug}"
            ),
            "product_public_name": public_name,
            "base_amount": "300.00",
            "booking_fee_amount": "4.50",
            "total_amount": "304.50",
            "currency": "BRL",
            "price_includes_booking_fee": True,
            "available": True,
        }

    def payloads_for(self, product_id: str) -> tuple[dict[str, object], ...]:
        return tuple(payload for _, payload in self.calls if payload["product_id"] == product_id)


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
    recommendation: ActivityRecommendationReadAdapter
    activity: GroupEnrichedActivityReadAdapter
    groups: ControlledGroupDiscovery
    transport: ControlledBokunGetTransport
    batches: tuple[InboundBatch, ...]

    def close(self) -> None:
        self.private_store.close()
        self.store.close()


def _policy():
    return load_activity_group_policy(
        ROOT / "config/v2_activity_group_policy.json",
        ROOT / "config/v2_bokun_product_map.json",
    )


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


def _authority(
    index: int, batch: InboundBatch, *, allocation_count: int = 1
) -> PublicTurnAuthority:
    digest = f"{index:x}" * 64
    return PublicTurnAuthority(
        authorization_kind="conversation_test",
        authorization_id=f"auth:task6:{index}",
        scope_subject_id=batch.subscriber_id,
        target_binding_hash="c" * 64,
        channel_id="manychat:channel-task6",
        channel_scope=batch.events[0].conversation_id,
        immutable_generation=index,
        allocation_ids=tuple(
            f"allocation:task6:{index}:{ordinal}" for ordinal in range(allocation_count)
        ),
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


def _activity_request(
    batch: InboundBatch,
    product_id: str,
    *,
    adults: int,
    activity_date: date = START,
    suffix: str = "ordinary",
) -> ReadRequest:
    return ReadRequest(
        request_id=f"{batch.batch_id}:{suffix}",
        kind=ReadKind.ACTIVITY,
        product_id=product_id,
        activity_date=activity_date,
        adults=adults,
        children=0,
        locale="pt-BR",
    )


def _proposal(
    batch: InboundBatch,
    text: str,
    *,
    reads: tuple[ReadRequest, ...] = (),
    facts: tuple[ModelFact, ...] = (),
    intent: str = "inform",
    selection_requested: bool = False,
    target_offer_id: str | None = None,
) -> ModelProposal:
    return ModelProposal(
        source_event_id=batch.batch_id,
        intent=intent,
        reply_chunks=(text,),
        facts=facts,
        read_requests=reads,
        effect_proposals=(),
        selection_requested=selection_requested,
        target_offer_id=target_offer_id,
    )


def _harness(
    *,
    messages: tuple[str, ...],
    proposals_for: Any,
    discovered: tuple[GroupDateCandidate, ...] | None = (),
    lookup_status: str = "not_matched",
    allocation_counts: tuple[int, ...] | None = None,
) -> ScenarioHarness:
    store = SQLiteBoundaryStore.open_memory_v8()
    private_store = SQLitePrivateCustomerFactStore.open_memory()
    batches = tuple(_batch(index, message) for index, message in enumerate(messages, 1))
    model = ScriptedModel(store, proposals_for(batches))
    groups = ControlledGroupDiscovery(discovered, lookup_status=lookup_status)
    transport = ControlledBokunGetTransport()
    policy = _policy()
    bokun = BokunReadAdapter(
        transport=transport,
        clock=SimpleNamespace(now=lambda: NOW),
        ttl=timedelta(minutes=5),
    )
    activity = GroupEnrichedActivityReadAdapter(
        bokun=bokun,
        groups_source=groups,
        policy=policy,
    )
    recommendation = ActivityRecommendationReadAdapter(
        groups=groups,
        policy=policy,
        activity=activity,
        clock=SimpleNamespace(now=lambda: NOW),
        ttl=timedelta(minutes=5),
    )
    counts = allocation_counts or (1,) * len(batches)
    authorities = {
        batch.batch_id: _authority(index, batch, allocation_count=counts[index - 1])
        for index, batch in enumerate(batches, 1)
    }
    for authority in authorities.values():
        _install_authority(store, authority)
    executor = V2TurnExecutor(
        store=store,
        model=model,
        reads=V2ReadService(
            {
                ReadKind.ACTIVITY_RECOMMENDATION: recommendation,
                ReadKind.ACTIVITY: activity,
            }
        ),
        profile=FakeManyChatProfile(store),
        private_customer_facts=private_store,
        reducer=_reducer(),
        public_authority=MappingAuthority(authorities),
        clock=SimpleNamespace(now=lambda: NOW),
        locale="pt-BR",
        turn_timeout=timedelta(seconds=30),
        max_commit_attempts=1,
    )
    return ScenarioHarness(
        store,
        private_store,
        executor,
        model,
        recommendation,
        activity,
        groups,
        transport,
        batches,
    )


def _assert_real_composition(harness: ScenarioHarness) -> None:
    assert type(harness.recommendation) is ActivityRecommendationReadAdapter
    assert type(harness.activity) is GroupEnrichedActivityReadAdapter
    assert type(harness.activity._bokun) is BokunReadAdapter
    assert harness.recommendation._activity is harness.activity
    assert harness.recommendation._groups is harness.groups
    assert harness.recommendation._policy is harness.activity._policy
    assert all(operation in READ_OPERATIONS for operation, _ in harness.transport.calls)


def _assert_zero_effects(harness: ScenarioHarness, *results: Any) -> None:
    _assert_real_composition(harness)
    for result in results:
        assert result.receipt.command_rows == ()
        assert result.receipt.relay_rows == ()
        assert result.receipt.internal_outbox_rows == ()
    for table in ("boundary_commands", "boundary_command_relays", "boundary_outbox"):
        assert harness.store._connection.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone() == (0,)
    assert all(proposal.effect_proposals == () for proposal in harness.model.completed)


def _recommendation_observation(request: ModelRequest) -> ReadObservation:
    matches = tuple(
        observation
        for observation in request.observations
        if "recommendation_period" in observation.public_payload
    )
    assert len(matches) == 1
    return matches[0]


def _ordinary_observation(request: ModelRequest, product_id: str) -> ReadObservation:
    matches = tuple(
        observation
        for observation in request.observations
        if observation.public_payload.get("product_id") == product_id
    )
    assert len(matches) == 1
    return matches[0]


def _run_recommendation(
    *,
    message: str,
    reply: str,
    adults: int = 2,
    days: int = 3,
    discovered: tuple[GroupDateCandidate, ...] | None = (),
    lookup_status: str = "not_matched",
    additional_reads: Any = None,
) -> tuple[ScenarioHarness, Any]:
    def proposals_for(batches: tuple[InboundBatch, ...]) -> list[ModelProposal]:
        batch = batches[0]
        extras = () if additional_reads is None else additional_reads(batch)
        return [
            _proposal(
                batch,
                "Vou comparar opções adequadas para o período.",
                reads=(_recommendation_request(batch, adults=adults, days=days), *extras),
            ),
            _proposal(batch, reply),
        ]

    harness = _harness(
        messages=(message,),
        proposals_for=proposals_for,
        discovered=discovered,
        lookup_status=lookup_status,
    )
    return harness, harness.executor.execute(harness.batches[0])


def _group(product_id: str, day: int = 10) -> GroupDateCandidate:
    return GroupDateCandidate(product_id, date(2026, 9, day), 3)


def test_period_preference_mentions_matched_candidate_first_among_suitable_options() -> None:
    reply = "Marimbus é a primeira opção adequada e tem grupo formado; 4Ps vem depois."
    harness, result = _run_recommendation(
        message="Estarei de 10 a 12 de setembro e prefiro água e natureza, sem trilha pesada.",
        reply=reply,
        discovered=(_group("product:marimbus"),),
    )
    try:
        candidates = _recommendation_observation(harness.model.calls[1]).public_payload[
            "candidates"
        ]
        assert [item["product_id"] for item in candidates[:2]] == [
            "product:marimbus",
            "product:tour-4ps",
        ]
        assert candidates[0]["group_status"] == "matched"
        assert candidates[1]["group_status"] == "not_matched"
        assert reply.index("Marimbus") < reply.index("4Ps")
        assert result.reply_chunks == (reply,)
        assert harness.groups.discover_calls == [(START, START + timedelta(days=2), 24)]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_suitability_precedes_group_priority_when_grouped_option_is_strenuous() -> None:
    reply = "4Ps combina melhor com seu pedido leve; Buracão tem grupo, mas é mais exigente."
    harness, result = _run_recommendation(
        message="Quero algo bem leve, sem trilha puxada, entre 10 e 12 de setembro.",
        reply=reply,
        discovered=(_group("product:buracao"),),
    )
    try:
        candidates = _recommendation_observation(harness.model.calls[1]).public_payload[
            "candidates"
        ]
        assert candidates[0]["product_id"] == "product:buracao"
        assert candidates[0]["existing_group"] is True
        assert any(item["product_id"] == "product:tour-4ps" for item in candidates)
        assert reply.index("4Ps") < reply.index("Buracão")
        assert result.reply_chunks == (reply,)
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_no_groups_still_recommends_bokun_available_options() -> None:
    reply = "Mesmo sem grupo formado, 4Ps e Pati 3 dias estão disponíveis no Bókun."
    harness, result = _run_recommendation(
        message="O que está disponível de 10 a 12 de setembro para duas pessoas?",
        reply=reply,
    )
    try:
        observation = _recommendation_observation(harness.model.calls[1])
        candidates = observation.public_payload["candidates"]
        assert observation.public_payload["group_source_status"] == "available"
        assert observation.public_payload["candidate_count"] == 4
        assert {item["product_id"] for item in candidates} == {
            "product:tour-4ps",
            "product:pati-3d",
        }
        assert all(item["existing_group"] is False for item in candidates)
        assert result.reply_chunks == (reply,)
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_unavailable_group_source_makes_no_group_claim_and_keeps_recommendation() -> None:
    reply = "4Ps segue disponível no Bókun; não consegui consultar a fonte de grupos agora."
    harness, result = _run_recommendation(
        message="Pode recomendar um passeio leve de 10 a 12 de setembro?",
        reply=reply,
        discovered=None,
        lookup_status="unavailable",
    )
    try:
        observation = _recommendation_observation(harness.model.calls[1])
        candidates = observation.public_payload["candidates"]
        assert observation.public_payload["group_source_status"] == "unavailable"
        assert observation.public_payload["candidate_count"] == 4
        assert all(
            item["group_status"] == "unavailable"
            and item["existing_group"] is False
            for item in candidates
        )
        assert "grupo formado" not in reply
        assert "4Ps" in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_arbitrary_non_frequent_product_uses_fresh_ordinary_activity_read() -> None:
    def proposals_for(batches: tuple[InboundBatch, ...]) -> list[ModelProposal]:
        recommendation_batch, arbitrary_batch = batches
        return [
            _proposal(
                recommendation_batch,
                "Vou comparar algumas opções.",
                reads=(_recommendation_request(recommendation_batch, adults=2),),
            ),
            _proposal(
                recommendation_batch,
                "Marimbus é uma sugestão; estes não são os únicos passeios que podem ser consultados.",
            ),
            _proposal(
                arbitrary_batch,
                "Vou consultar o Catacumbas.",
                reads=(
                    _activity_request(
                        arbitrary_batch,
                        "product:catacumbas",
                        adults=2,
                        activity_date=date(2026, 9, 12),
                        suffix="catacumbas:fresh",
                    ),
                ),
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
        discovered=(_group("product:marimbus"),),
    )
    try:
        recommendation_result = harness.executor.execute(harness.batches[0])
        assert recommendation_result.receipt.read_observations == ()
        assert harness.store.load_state(harness.batches[0].lead_id).state.workflow is None

        arbitrary_result = harness.executor.execute(harness.batches[1])
        assert harness.model.calls[2].observations == ()
        assert harness.model.calls[2].consultation_history == ()
        ordinary = _ordinary_observation(harness.model.calls[3], "product:catacumbas")
        assert ordinary.public_payload["available"] is True
        assert len(arbitrary_result.receipt.read_observations) == 1
        assert harness.groups.lookup_calls == [
            ("product:catacumbas", date(2026, 9, 12))
        ]
        assert len(harness.groups.discover_calls) == 1
        recommendation_text = recommendation_result.reply_chunks[0].lower()
        assert "não são os únicos passeios" in recommendation_text
        assert "só estes passeios podem" not in recommendation_text
        _assert_zero_effects(harness, recommendation_result, arbitrary_result)
    finally:
        harness.close()


def test_4ps_without_group_is_frequent_but_never_claimed_as_confirmed_departure() -> None:
    reply = "4Ps é uma alternativa frequente e está disponível, mas não há grupo ou saída confirmada."
    harness, result = _run_recommendation(
        message="Que passeio costuma ser uma opção entre 10 e 12 de setembro?",
        reply=reply,
    )
    try:
        candidates = _recommendation_observation(harness.model.calls[1]).public_payload[
            "candidates"
        ]
        candidate = next(
            item for item in candidates if item["product_id"] == "product:tour-4ps"
        )
        assert candidate["frequent_alternative"] is True
        assert candidate["group_status"] == "not_matched"
        assert candidate["existing_group"] is False
        assert "não há grupo ou saída confirmada" in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_pati_3d_is_absent_when_period_has_no_three_day_window() -> None:
    reply = "Para 10 e 11 de setembro, as alternativas observadas são saídas de 4Ps."
    harness, result = _run_recommendation(
        message="Tenho apenas 10 e 11 de setembro; o que você recomenda?",
        reply=reply,
        days=2,
    )
    try:
        observed = _recommendation_observation(harness.model.calls[1]).public_payload[
            "candidates"
        ]
        assert {item["product_id"] for item in observed} == {"product:tour-4ps"}
        assert harness.transport.payloads_for("product:pati-3d") == ()
        assert harness.groups.discover_calls == [(START, START + timedelta(days=1), 24)]
        assert "Pati" not in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_restricted_solo_product_without_group_is_absent_under_existing_policy() -> None:
    def buracao_read(batch: InboundBatch) -> tuple[ReadRequest, ...]:
        return (_activity_request(batch, "product:buracao", adults=1),)

    reply = "Para uma pessoa sem grupo, Buracão não está disponível; posso indicar 4Ps."
    harness, result = _run_recommendation(
        message="Viajo sozinho e queria Buracão entre 10 e 12 de setembro.",
        reply=reply,
        adults=1,
        additional_reads=buracao_read,
    )
    try:
        request = harness.model.completed[0].read_requests[1]
        recommendation = _recommendation_observation(harness.model.calls[1])
        ordinary = _ordinary_observation(harness.model.calls[1], "product:buracao")
        assert all(
            item["product_id"] != "product:buracao"
            for item in recommendation.public_payload["candidates"]
        )
        assert ordinary.public_payload["available"] is False
        assert ordinary.public_payload["group_status"] == "not_matched"
        assert "offer_id" not in ordinary.public_payload
        payloads = harness.transport.payloads_for("product:buracao")
        assert payloads == (
            {
                "product_id": "product:buracao",
                "activity_date": START.isoformat(),
                "adults": 1,
                "children": 0,
                "quote_scope": request.query_hash(),
                "availability_only": True,
                "locale": "pt-BR",
            },
        )
        assert "não está disponível" in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def test_two_people_keep_same_product_available_without_group() -> None:
    def buracao_read(batch: InboundBatch) -> tuple[ReadRequest, ...]:
        return (_activity_request(batch, "product:buracao", adults=2),)

    reply = "Buracão continua disponível para duas pessoas, mesmo sem grupo formado."
    harness, result = _run_recommendation(
        message="Somos duas pessoas e queremos Buracão em 10 de setembro.",
        reply=reply,
        adults=2,
        additional_reads=buracao_read,
    )
    try:
        recommendation = _recommendation_observation(harness.model.calls[1])
        ordinary = _ordinary_observation(harness.model.calls[1], "product:buracao")
        assert all(
            item["product_id"] != "product:buracao"
            for item in recommendation.public_payload["candidates"]
        )
        assert ordinary.public_payload["available"] is True
        assert ordinary.public_payload["group_status"] == "not_matched"
        payload = harness.transport.payloads_for("product:buracao")[0]
        assert payload["adults"] == 2
        assert "availability_only" not in payload
        assert "expected_rate_id" not in payload
        assert "continua disponível" in result.reply_chunks[0]
        _assert_zero_effects(harness, result)
    finally:
        harness.close()


def _ordinary_offer_id(request: ReadRequest) -> str:
    assert request.product_id is not None
    slug = request.product_id.removeprefix("product:")
    private = {
        "bokun_product_id": f"bokun-{slug}",
        "start_time_id": f"start-{slug}",
        "rate_id": f"rate-{slug}",
        "adult_pricing_category_id": f"adult-{slug}",
    }
    return "offer:" + binding_hash({"request_hash": request.query_hash(), **private})


def test_choice_after_recommendation_performs_fresh_ordinary_activity_read() -> None:
    def proposals_for(batches: tuple[InboundBatch, ...]) -> list[ModelProposal]:
        recommendation_batch, choice_batch = batches
        ordinary = _activity_request(
            choice_batch,
            "product:tour-4ps",
            adults=1,
            suffix="4ps:fresh",
        )
        return [
            _proposal(
                recommendation_batch,
                "Vou comparar.",
                reads=(_recommendation_request(recommendation_batch, adults=1),),
            ),
            _proposal(
                recommendation_batch,
                "4Ps é adequado e é apenas uma recomendação; outros passeios também podem ser consultados.",
            ),
            _proposal(
                choice_batch,
                "Vou atualizar a disponibilidade de 4Ps.",
                reads=(ordinary,),
                facts=(
                    ModelFact("birth_date", date(1992, 4, 15)),
                    ModelFact("gender", "f"),
                ),
                selection_requested=True,
            ),
            _proposal(
                choice_batch,
                "Vou preparar o resumo de 4Ps.",
                intent="select",
                facts=(
                    ModelFact("service", "agency"),
                    ModelFact("product_id", "product:tour-4ps"),
                    ModelFact("activity_date", START),
                    ModelFact("adults", 1),
                    ModelFact("children", 0),
                    ModelFact("payment_method", "stripe"),
                    ModelFact("birth_date", date(1992, 4, 15)),
                    ModelFact("gender", "f"),
                ),
                target_offer_id=_ordinary_offer_id(ordinary),
            ),
        ]

    harness = _harness(
        messages=(
            "Recomende um passeio para 10 a 12 de setembro.",
            "Escolho 4Ps no dia 10 para uma pessoa.",
        ),
        proposals_for=proposals_for,
        allocation_counts=(1, 2),
    )
    try:
        recommendation_result = harness.executor.execute(harness.batches[0])
        assert recommendation_result.receipt.read_observations == ()
        assert recommendation_result.receipt.command_rows == ()
        assert harness.store.load_state(harness.batches[0].lead_id).state.workflow is None

        choice_result = harness.executor.execute(harness.batches[1])
        assert harness.model.calls[2].observations == ()
        assert harness.model.calls[2].consultation_history == ()
        ordinary = _ordinary_observation(harness.model.calls[3], "product:tour-4ps")
        assert ordinary.public_payload["available"] is True
        assert ordinary.public_payload["offer_id"] == _ordinary_offer_id(
            harness.model.completed[2].read_requests[0]
        )
        assert len(choice_result.receipt.read_observations) == 1
        workflow = harness.store.load_state(harness.batches[0].lead_id).state.workflow
        assert isinstance(workflow, AwaitingConfirmationState), (
            choice_result.reply_chunks,
            harness.model.calls[3].observations,
            choice_result.receipt.read_observations,
        )
        assert harness.groups.discover_calls == [(START, START + timedelta(days=2), 24)]
        assert harness.groups.lookup_calls == [("product:tour-4ps", START)]
        recommendation_text = recommendation_result.reply_chunks[0].lower()
        assert "outros passeios também podem ser consultados" in recommendation_text
        assert "só estes passeios" not in recommendation_text
        _assert_zero_effects(harness, recommendation_result, choice_result)
    finally:
        harness.close()
