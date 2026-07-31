from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from reservation_domain import (
    CommandPayload,
    CustomerFacts,
    EconomicTerms,
    ExecutionCertainty,
    ManualReviewState,
    Money,
    PassengerFacts,
    Party,
    ReservationCommand,
    ReservationOperation,
    ServiceKind,
    SucceededState,
    dumps_command,
)
from reservation_domain.signature import command_identity, subject_signature
from reservation_execution import DispatchPermit, Lease, PreparationFailure
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.phase5_helpers import T0, _lookup, persist_script, workflow_events
from v2_adapters.bokun import BokunReservationPort
from v2_adapters.cloudbeds import CloudbedsReservationPort
from v2_application.reads import PrivateOfferBindingResolver
from v2_application.reservations import (
    DispatchRejected,
    ReservationAllocator,
    V2ReservationExecutionAdapter,
    _provider_payload,
)
from v2_application.relay_worker import (
    build_reservation_relay_bundle,
    reservation_target_operation_id,
)
from v2_application.turn_executor import _execution_commands
from v2_application.workers import V2ReservationWorker, V2WorkerDisposition
from v2_contracts.private_offers import PrivateOfferBinding
from v2_contracts.providers import (
    ProviderCertainty,
    ProviderDispatchPermit,
    ProviderExecutionResult,
    ProviderWriteAuthorization,
)


NOW = T0 + timedelta(minutes=1)


class FakeReservationPort:
    def __init__(self, provider: str, action: object) -> None:
        self.provider = provider
        self.action = action
        self.calls: list[ProviderDispatchPermit] = []

    def execute(self, permit: ProviderDispatchPermit) -> ProviderExecutionResult:
        self.calls.append(permit)
        if isinstance(self.action, BaseException):
            raise self.action
        assert type(self.action) is ProviderExecutionResult
        return self.action


class FakeCommercialEffectGuard:
    def __init__(self, blocked_workflow_ids: frozenset[str] = frozenset()) -> None:
        self.blocked_workflow_ids = blocked_workflow_ids
        self.calls: list[str] = []

    def allows_workflow(self, workflow_id: str) -> bool:
        self.calls.append(workflow_id)
        return workflow_id not in self.blocked_workflow_ids


class FixedBindingPort:
    def resolve(self, query):
        return PrivateOfferBinding(
            provider="cloudbeds",
            query=query,
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            provider_fields=(
                ("room_rate_id", "rate-private-fenced-001"),
                ("room_type_id", "room-private-fenced-001"),
            ),
        )


class FixedReservationClock:
    def now(self):
        return NOW


def _authorization(provider: str, *, enabled: bool = True) -> ProviderWriteAuthorization:
    return ProviderWriteAuthorization(
        provider=provider,
        enabled=enabled,
        authorization_id=f"authorization:{provider}:task4",
    )


def _queued_cloudbeds_store(tmp_path: Path) -> SQLiteUnitOfWork:
    store = SQLiteUnitOfWork.open(tmp_path / "execution.sqlite3")
    workflow_id = "workflow:v2-task4-lodging"
    initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
    store.create_workflow(initial)
    persist_script(store, workflow_id, script)
    return store


def _result(certainty: ProviderCertainty) -> ProviderExecutionResult:
    return ProviderExecutionResult(
        certainty=certainty,
        normalized_status={
            ProviderCertainty.EFFECT_CONFIRMED: "confirmed",
            ProviderCertainty.CALLED_NO_EFFECT: "rejected",
            ProviderCertainty.CALLED_UNKNOWN: "unknown",
            ProviderCertainty.NOT_CALLED: "not_called",
        }[certainty],
        provider_reference_fingerprint=(
            "a" * 64 if certainty is ProviderCertainty.EFFECT_CONFIRMED else None
        ),
        evidence=("b" * 64,),
    )


def _worker(
    store: SQLiteUnitOfWork,
    port: FakeReservationPort,
    *,
    enabled: bool = True,
    blocked_workflow_ids: frozenset[str] = frozenset(),
) -> V2ReservationWorker:
    adapter = V2ReservationExecutionAdapter(
        provider="cloudbeds",
        port=port,
        authorization=_authorization("cloudbeds", enabled=enabled),
        require_private_binding=False,
    )
    return V2ReservationWorker(
        store=store,
        adapters=(adapter,),
        effect_guard=FakeCommercialEffectGuard(blocked_workflow_ids),
        worker_id="worker:v2-reservation",
        lease_ttl=timedelta(seconds=30),
    )


def test_duplicate_worker_claim_calls_cloudbeds_once(tmp_path: Path) -> None:
    store = _queued_cloudbeds_store(tmp_path)
    port = FakeReservationPort("cloudbeds", _result(ProviderCertainty.EFFECT_CONFIRMED))
    worker = _worker(store, port)
    try:
        first = worker.run_once(now=NOW)
        second = worker.run_once(now=NOW + timedelta(seconds=1))

        assert first.disposition is V2WorkerDisposition.EFFECT_CONFIRMED
        assert second.disposition is V2WorkerDisposition.IDLE
        assert len(port.calls) == 1
        assert port.calls[0].provider == "cloudbeds"
        assert port.calls[0].operation == "reserve_lodging"
        assert port.calls[0].fencing_token == 1
    finally:
        store.close()


def _cloudbeds_group_command() -> ReservationCommand:
    component = replace(
        _lookup("cloudbeds").offers[0],
        party=Party(2, 1),
        provider_ref="a" * 64,
    )
    customer = CustomerFacts(
        customer_ref="customer:v2-cloudbeds-group-001",
        full_name="Contato Hospedagem",
        email="hosting@example.invalid",
        phone_e164="+5571999999999",
        country_code="BR",
    )
    terms = EconomicTerms(payment_method="stripe")
    components = (component,)
    signature = subject_signature(
        components=components,
        customer=customer,
        terms=terms,
    )
    command_id, idempotency_key = command_identity(
        workflow_id="workflow:v2-cloudbeds-group-001",
        draft_id="draft:v2-cloudbeds-group-001",
        draft_version=1,
        signature=signature,
        operation=ReservationOperation.RESERVE_LODGING,
    )
    return ReservationCommand(
        command_id=command_id,
        idempotency_key=idempotency_key,
        workflow_id="workflow:v2-cloudbeds-group-001",
        draft_id="draft:v2-cloudbeds-group-001",
        draft_version=1,
        subject_signature=signature,
        operation=ReservationOperation.RESERVE_LODGING,
        payload=CommandPayload(components, customer, terms),
        created_at=NOW,
    )


def test_cloudbeds_multi_room_components_are_rejected_before_provider() -> None:
    command = _cloudbeds_group_command()
    first = command.payload.components[0]
    second = replace(
        first,
        offer_id="offer:second-room",
        provider_ref="b" * 64,
    )
    components = (first, second)
    payload = CommandPayload(components, command.payload.customer, command.payload.terms)
    signature = subject_signature(
        components=components,
        customer=payload.customer,
        terms=payload.terms,
    )
    command_id, idempotency_key = command_identity(
        workflow_id=command.workflow_id,
        draft_id=command.draft_id,
        draft_version=command.draft_version,
        signature=signature,
        operation=command.operation,
    )
    multi_room = replace(
        command,
        command_id=command_id,
        idempotency_key=idempotency_key,
        subject_signature=signature,
        payload=payload,
    )

    with pytest.raises(DispatchRejected, match="exactly one component"):
        _provider_payload(
            multi_room,
            "cloudbeds",
            {
                "room_rate_id": "rate-private-fenced-001",
                "room_type_id": "room-private-fenced-001",
            },
        )


def test_cloudbeds_confirmation_is_durable_and_group_replay_is_idle(
    tmp_path: Path,
) -> None:
    command = _cloudbeds_group_command()
    store = SQLiteUnitOfWork.open_v6(tmp_path / "cloudbeds-confirmed.sqlite3")
    bundle = build_reservation_relay_bundle(command)
    source_hash = hashlib.sha256(command.command_id.encode()).hexdigest()
    raw_reference = "reservation-123"
    calls: list[tuple[str, dict[str, object], str]] = []

    def transport(operation, payload, *, idempotency_key):
        calls.append((operation, payload, idempotency_key))
        assert payload["offer"]["party"] == {"adults": 2, "children": 1}
        assert payload["offer"]["private_binding"] == {
            "room_rate_id": "rate-private-fenced-001",
            "room_type_id": "room-private-fenced-001",
        }
        return {"status": "confirmed", "reservation_id": raw_reference}

    store.accept_boundary_reservation(
        operation_id=reservation_target_operation_id(
            bundle_hash=bundle.artifact_hash,
            source_turn_receipt_hash=source_hash,
        ),
        source_turn_receipt_hash=source_hash,
        bundle=bundle,
    )
    worker = V2ReservationWorker(
        store=store,
        adapters=(
            V2ReservationExecutionAdapter(
                provider="cloudbeds",
                port=CloudbedsReservationPort(transport),
                authorization=_authorization("cloudbeds"),
                binding_resolver=PrivateOfferBindingResolver(
                    {ServiceKind.LODGING: FixedBindingPort()}
                ),
                clock=FixedReservationClock(),
            ),
        ),
        effect_guard=FakeCommercialEffectGuard(),
        worker_id="worker:cloudbeds-confirmed",
        lease_ttl=timedelta(seconds=30),
    )
    try:
        first = worker.run_once(now=NOW + timedelta(seconds=1))
        replay = worker.run_once(now=NOW + timedelta(seconds=2))

        assert first.disposition is V2WorkerDisposition.EFFECT_CONFIRMED
        assert replay.disposition is V2WorkerDisposition.IDLE
        assert len(calls) == 1
        assert isinstance(first.transition.state, SucceededState)
        outcome = first.transition.state.outcome
        assert outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED
        fingerprint = hashlib.sha256(raw_reference.encode()).hexdigest()
        assert outcome.provider_reference == (
            f"provider:cloudbeds:{fingerprint[:32]}"
        )
        assert raw_reference not in repr(outcome)
        assert store._connection.execute(
            "SELECT dispatch_slots_consumed,status FROM execution_ledger "
            "WHERE command_id=?",
            (command.command_id,),
        ).fetchone() == (1, "outcome_recorded")
    finally:
        store.close()


def test_bokun_booking_id_confirmation_is_durable_and_not_replayed(
    tmp_path: Path,
) -> None:
    command = _group_activity_command(_group_passengers())
    store = SQLiteUnitOfWork.open_v6(tmp_path / "bokun-confirmed.sqlite3")
    bundle = build_reservation_relay_bundle(command)
    source_hash = hashlib.sha256(command.command_id.encode()).hexdigest()
    raw_reference = "booking-123"
    calls: list[tuple[str, dict[str, object], str]] = []

    def transport(operation, payload, *, idempotency_key):
        calls.append((operation, payload, idempotency_key))
        return {"status": "confirmed", "booking_id": raw_reference}

    store.accept_boundary_reservation(
        operation_id=reservation_target_operation_id(
            bundle_hash=bundle.artifact_hash,
            source_turn_receipt_hash=source_hash,
        ),
        source_turn_receipt_hash=source_hash,
        bundle=bundle,
    )
    worker = V2ReservationWorker(
        store=store,
        adapters=(
            V2ReservationExecutionAdapter(
                provider="bokun",
                port=BokunReservationPort(transport),
                authorization=_authorization("bokun"),
                require_private_binding=False,
            ),
        ),
        effect_guard=FakeCommercialEffectGuard(),
        worker_id="worker:bokun-confirmed",
        lease_ttl=timedelta(seconds=30),
    )
    try:
        first = worker.run_once(now=NOW + timedelta(seconds=1))
        replay = worker.run_once(now=NOW + timedelta(seconds=2))

        assert first.disposition is V2WorkerDisposition.EFFECT_CONFIRMED
        assert replay.disposition is V2WorkerDisposition.IDLE
        assert len(calls) == 1
        assert isinstance(first.transition.state, SucceededState)
        outcome = first.transition.state.outcome
        assert outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED
        fingerprint = hashlib.sha256(raw_reference.encode()).hexdigest()
        assert outcome.provider_reference == f"provider:bokun:{fingerprint[:32]}"
        assert raw_reference not in repr(outcome)
        assert store._connection.execute(
            "SELECT dispatch_slots_consumed,status FROM execution_ledger "
            "WHERE command_id=?",
            (command.command_id,),
        ).fetchone() == (1, "outcome_recorded")
    finally:
        store.close()


def test_timeout_after_dispatch_becomes_called_unknown_without_retry(
    tmp_path: Path,
) -> None:
    store = _queued_cloudbeds_store(tmp_path)
    port = FakeReservationPort("cloudbeds", TimeoutError("after dispatch"))
    worker = _worker(store, port)
    try:
        first = worker.run_once(now=NOW)
        second = worker.run_once(now=NOW + timedelta(seconds=1))

        assert first.disposition is V2WorkerDisposition.MANUAL_REVIEW
        assert second.disposition is V2WorkerDisposition.IDLE
        assert len(port.calls) == 1
        assert isinstance(first.transition.state, ManualReviewState)
        assert first.transition.state.outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN
    finally:
        store.close()


def test_closed_write_gate_stops_before_fence_and_provider_call(tmp_path: Path) -> None:
    store = _queued_cloudbeds_store(tmp_path)
    port = FakeReservationPort("cloudbeds", _result(ProviderCertainty.EFFECT_CONFIRMED))
    worker = _worker(store, port, enabled=False)
    try:
        result = worker.run_once(now=NOW)

        assert result.disposition is V2WorkerDisposition.NOT_CALLED
        assert port.calls == []
        ledger = store._connection.execute(
            "SELECT dispatch_slots_consumed FROM execution_ledger"
        ).fetchone()
        assert ledger == (0,)
    finally:
        store.close()


def test_active_handoff_stops_queued_command_before_fence_and_provider(
    tmp_path: Path,
) -> None:
    store = _queued_cloudbeds_store(tmp_path)
    port = FakeReservationPort("cloudbeds", _result(ProviderCertainty.EFFECT_CONFIRMED))
    worker = _worker(
        store,
        port,
        blocked_workflow_ids=frozenset({"workflow:v2-task4-lodging"}),
    )
    try:
        result = worker.run_once(now=NOW)

        assert result.disposition is V2WorkerDisposition.NOT_CALLED
        assert port.calls == []
        assert store._connection.execute(
            "SELECT dispatch_slots_consumed, status FROM execution_ledger"
        ).fetchone() == (0, "outcome_recorded")
    finally:
        store.close()


def _package_command(*, booking_profile: bool = False) -> ReservationCommand:
    lodging = _lookup("cloudbeds").offers[0]
    activity = _lookup("bokun").offers[0]
    activity = replace(activity, total=Money(activity.total.amount, lodging.total.currency))
    components = (lodging, activity)
    customer = CustomerFacts(
        customer_ref="customer:v2-package-001",
        full_name="Carlos Synthetic",
        email="carlos.synthetic@example.invalid",
        phone_e164="+12025550123",
        country_code="ZZ",
        birth_date=date(1990, 1, 2) if booking_profile else None,
        gender="m" if booking_profile else None,
    )
    terms = EconomicTerms(payment_method="card")
    signature = subject_signature(
        components=components,
        customer=customer,
        terms=terms,
    )
    command_id, idempotency_key = command_identity(
        workflow_id="workflow:v2-package-001",
        draft_id="draft:v2-package-001",
        draft_version=1,
        signature=signature,
        operation=ReservationOperation.RESERVE_PACKAGE,
    )
    return ReservationCommand(
        command_id=command_id,
        idempotency_key=idempotency_key,
        workflow_id="workflow:v2-package-001",
        draft_id="draft:v2-package-001",
        draft_version=1,
        subject_signature=signature,
        operation=ReservationOperation.RESERVE_PACKAGE,
        payload=CommandPayload(components, customer, terms),
        created_at=NOW,
    )


def _group_activity_command(
    passengers: tuple[PassengerFacts, ...],
    *,
    party: Party = Party(2, 1),
) -> ReservationCommand:
    component = replace(
        _lookup("bokun").offers[0],
        party=party,
    )
    customer = CustomerFacts(
        customer_ref="customer:v2-group-001",
        full_name="Contato Grupo",
        email="group@example.invalid",
        phone_e164="+1" + "202" + "555" + "0456",
        country_code="BR",
        passengers=passengers,
    )
    terms = EconomicTerms(payment_method="wise")
    components = (component,)
    signature = subject_signature(
        components=components,
        customer=customer,
        terms=terms,
    )
    command_id, idempotency_key = command_identity(
        workflow_id="workflow:v2-group-001",
        draft_id="draft:v2-group-001",
        draft_version=1,
        signature=signature,
        operation=ReservationOperation.BOOK_ACTIVITY,
    )
    return ReservationCommand(
        command_id=command_id,
        idempotency_key=idempotency_key,
        workflow_id="workflow:v2-group-001",
        draft_id="draft:v2-group-001",
        draft_version=1,
        subject_signature=signature,
        operation=ReservationOperation.BOOK_ACTIVITY,
        payload=CommandPayload(components, customer, terms),
        created_at=NOW,
    )


def _group_passengers() -> tuple[PassengerFacts, ...]:
    return (
        PassengerFacts(1, "adult", "Pessoa Um", date(1990, 1, 2), "f", "BR"),
        PassengerFacts(2, "adult", "Pessoa Dois", date(1992, 3, 4), "m", "BR"),
        PassengerFacts(3, "child", "Pessoa Três", date(2016, 5, 6), "f", "BR"),
    )


def _matrix_passengers(adults: int, children: int) -> tuple[PassengerFacts, ...]:
    return tuple(
        PassengerFacts(
            position,
            "adult" if position <= adults else "child",
            f"Pessoa Sintética {position}",
            date(1980 + position, 1, 2)
            if position <= adults
            else date(2010 + position, 1, 2),
            "f" if position % 2 else "m",
            "BR",
        )
        for position in range(1, adults + children + 1)
    )


def test_group_activity_prepare_accepts_exact_complete_manifest() -> None:
    command = _group_activity_command(_group_passengers())
    adapter = V2ReservationExecutionAdapter(
        provider="bokun",
        port=FakeReservationPort(
            "bokun", _result(ProviderCertainty.EFFECT_CONFIRMED)
        ),
        authorization=_authorization("bokun"),
        require_private_binding=False,
    )

    request = adapter.prepare(command)

    assert request.canonical_payload == dumps_command(command)


def test_group_activity_dispatch_v2_binds_each_passenger_in_party_order() -> None:
    command = _group_activity_command(_group_passengers())
    payload = json.loads(
        _provider_payload(
            command,
            "bokun",
            {
                "bokun_product_id": "912303",
                "start_time_id": "start-1",
                "rate_id": "rate-1",
                "adult_pricing_category_id": "adult-1",
                "child_pricing_category_id": "child-1",
            },
        )
    )

    assert payload["schema"] == "v2-reservation-dispatch-v2"
    assert payload["offer"]["party"] == {"adults": 2, "children": 1}
    assert payload["customer"]["passengers"] == [
        {
            "position": 1,
            "participant_type": "adult",
            "full_name": "Pessoa Um",
            "birth_date": "1990-01-02",
            "gender": "f",
            "country_code": "BR",
        },
        {
            "position": 2,
            "participant_type": "adult",
            "full_name": "Pessoa Dois",
            "birth_date": "1992-03-04",
            "gender": "m",
            "country_code": "BR",
        },
        {
            "position": 3,
            "participant_type": "child",
            "full_name": "Pessoa Três",
            "birth_date": "2016-05-06",
            "gender": "f",
            "country_code": "BR",
        },
    ]
    assert "birth_date" not in payload["customer"]
    assert "gender" not in payload["customer"]


@pytest.mark.parametrize(
    ("adults", "children"),
    ((1, 0), (2, 0), (1, 1), (4, 2)),
)
def test_supported_party_matrix_is_preserved_from_command_to_dispatch(
    adults: int,
    children: int,
) -> None:
    passengers = _matrix_passengers(adults, children)
    command = _group_activity_command(
        passengers,
        party=Party(adults, children),
    )
    private_binding = {
        "bokun_product_id": "912303",
        "start_time_id": "start-1",
        "rate_id": "rate-1",
        "adult_pricing_category_id": "adult-1",
    }
    if children:
        private_binding["child_pricing_category_id"] = "child-1"

    payload = json.loads(_provider_payload(command, "bokun", private_binding))

    assert payload["offer"]["party"] == {
        "adults": adults,
        "children": children,
    }
    assert len(payload["customer"]["passengers"]) == adults + children
    assert [
        item["participant_type"] for item in payload["customer"]["passengers"]
    ] == ["adult"] * adults + ["child"] * children


def test_group_activity_incomplete_manifest_fails_before_fence() -> None:
    command = _group_activity_command(_group_passengers()[:2])
    adapter = V2ReservationExecutionAdapter(
        provider="bokun",
        port=FakeReservationPort(
            "bokun", _result(ProviderCertainty.EFFECT_CONFIRMED)
        ),
        authorization=_authorization("bokun"),
        require_private_binding=False,
    )

    with pytest.raises(PreparationFailure) as raised:
        adapter.prepare(command)

    assert raised.value.reason == "booking_profile_incomplete"
    assert raised.value.retryable is False


def test_single_activity_dispatch_v2_derives_one_passenger_from_legacy_profile() -> None:
    activity = ReservationAllocator().allocate(
        _package_command(booking_profile=True)
    ).commands[1]
    payload = json.loads(
        _provider_payload(
            activity,
            "bokun",
            {
                "bokun_product_id": "912303",
                "start_time_id": "start-1",
                "rate_id": "rate-1",
                "adult_pricing_category_id": "adult-1",
            },
        )
    )

    assert payload["schema"] == "v2-reservation-dispatch-v2"
    assert len(payload["customer"]["passengers"]) == 1
    assert payload["customer"]["passengers"][0]["participant_type"] == "adult"
    assert payload["customer"]["passengers"][0]["birth_date"] == "1990-01-02"


def test_private_binding_prepare_keeps_exact_command_payload_through_fence(
    tmp_path: Path,
) -> None:
    command = ReservationAllocator().allocate(
        _package_command(booking_profile=True)
    ).commands[0]
    assert command.operation is ReservationOperation.RESERVE_LODGING
    component = replace(command.payload.components[0], provider_ref="a" * 64)
    components = (component,)
    payload = CommandPayload(components, command.payload.customer, command.payload.terms)
    signature = subject_signature(
        components=components,
        customer=payload.customer,
        terms=payload.terms,
    )
    command_id, idempotency_key = command_identity(
        workflow_id=command.workflow_id,
        draft_id=command.draft_id,
        draft_version=command.draft_version,
        signature=signature,
        operation=command.operation,
    )
    command = replace(
        command,
        command_id=command_id,
        idempotency_key=idempotency_key,
        subject_signature=signature,
        payload=payload,
    )
    port = FakeReservationPort(
        "cloudbeds", _result(ProviderCertainty.EFFECT_CONFIRMED)
    )
    adapter = V2ReservationExecutionAdapter(
        provider="cloudbeds",
        port=port,
        authorization=_authorization("cloudbeds"),
        binding_resolver=PrivateOfferBindingResolver(
            {ServiceKind.LODGING: FixedBindingPort()}
        ),
        clock=FixedReservationClock(),
    )
    store = SQLiteUnitOfWork.open_v6(tmp_path / "private-fenced.sqlite3")
    source_hash = hashlib.sha256(command.command_id.encode()).hexdigest()
    bundle = build_reservation_relay_bundle(command)
    try:
        store.accept_boundary_reservation(
            operation_id=reservation_target_operation_id(
                bundle_hash=bundle.artifact_hash,
                source_turn_receipt_hash=source_hash,
            ),
            source_turn_receipt_hash=source_hash,
            bundle=bundle,
        )
        claim = store.claim_command(
            worker_id="worker:private-fenced",
            now=NOW,
            lease_ttl=timedelta(seconds=30),
        )
        assert claim is not None

        request = adapter.prepare(claim.command)
        assert request.canonical_payload == dumps_command(claim.command)
        permit = store.fence_dispatch(claim, request, now=NOW)
        outcome = adapter.dispatch_fenced(
            permit,
            request,
            idempotency_key=claim.command.idempotency_key,
        )

        assert outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED
        assert adapter._prepared_private_bindings == {}
        assert len(port.calls) == 1
        provider_payload = json.loads(port.calls[0].canonical_payload)
        assert provider_payload["offer"]["private_binding"] == {
            "room_rate_id": "rate-private-fenced-001",
            "room_type_id": "room-private-fenced-001",
        }
    finally:
        store.close()


def test_dispatch_fence_rejects_idempotency_key_divergent_from_signed_command() -> None:
    command = _group_activity_command(_group_passengers())
    port = FakeReservationPort(
        "bokun", _result(ProviderCertainty.EFFECT_CONFIRMED)
    )
    adapter = V2ReservationExecutionAdapter(
        provider="bokun",
        port=port,
        authorization=_authorization("bokun"),
        require_private_binding=False,
    )
    request = replace(
        adapter.prepare(command),
        idempotency_key="idempotency:forged-request",
    )
    permit = DispatchPermit(
        command_id=request.command_id,
        lease=Lease(
            owner="worker:forged-request",
            fencing_token=1,
            acquired_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
        ),
        dispatch_slot=1,
        request_hash=request.payload_hash,
        fenced_at=NOW,
    )

    with pytest.raises(DispatchRejected, match="idempotency"):
        adapter.dispatch_fenced(
            permit,
            request,
            idempotency_key=request.idempotency_key,
        )

    assert port.calls == []


def test_package_allocation_produces_two_provider_commands_as_one_batch() -> None:
    package = _package_command()

    allocation = ReservationAllocator().allocate(package)

    assert allocation.source_command_id == package.command_id
    assert tuple(command.operation for command in allocation.commands) == (
        ReservationOperation.RESERVE_LODGING,
        ReservationOperation.BOOK_ACTIVITY,
    )
    assert len({command.command_id for command in allocation.commands}) == 2
    assert len({command.workflow_id for command in allocation.commands}) == 2
    assert all(
        command.workflow_id != package.workflow_id for command in allocation.commands
    )
    assert all(len(command.payload.components) == 1 for command in allocation.commands)
    assert ReservationAllocator().allocate(package) == allocation
    assert ReservationAllocator().expand_commands((package,)) == allocation.commands
    assert _execution_commands((package,)) == allocation.commands


def test_package_peer_is_stopped_before_fence_after_unknown_outcome(
    tmp_path: Path,
) -> None:
    package = _package_command(booking_profile=True)
    children = ReservationAllocator().allocate(package).commands
    ordered = tuple(sorted(children, key=lambda item: item.command_id))
    store = SQLiteUnitOfWork.open_v6(tmp_path / "package-stop.sqlite3")
    try:
        for child in children:
            bundle = build_reservation_relay_bundle(child)
            source_hash = hashlib.sha256(child.command_id.encode()).hexdigest()
            store.accept_boundary_reservation(
                operation_id=reservation_target_operation_id(
                    bundle_hash=bundle.artifact_hash,
                    source_turn_receipt_hash=source_hash,
                ),
                source_turn_receipt_hash=source_hash,
                bundle=bundle,
            )
        provider_by_operation = {
            ReservationOperation.RESERVE_LODGING: "cloudbeds",
            ReservationOperation.BOOK_ACTIVITY: "bokun",
        }
        first_provider = provider_by_operation[ordered[0].operation]
        ports = {
            provider: FakeReservationPort(
                provider,
                _result(
                    ProviderCertainty.CALLED_UNKNOWN
                    if provider == first_provider
                    else ProviderCertainty.EFFECT_CONFIRMED
                ),
            )
            for provider in ("cloudbeds", "bokun")
        }
        worker = V2ReservationWorker(
            store=store,
            adapters=tuple(
                V2ReservationExecutionAdapter(
                    provider=provider,
                    port=ports[provider],
                    authorization=_authorization(provider),
                    require_private_binding=False,
                )
                for provider in ("cloudbeds", "bokun")
            ),
            effect_guard=FakeCommercialEffectGuard(),
            worker_id="worker:v2-package-stop",
            lease_ttl=timedelta(seconds=30),
        )

        first = worker.run_once(now=NOW + timedelta(seconds=1))
        second = worker.run_once(now=NOW + timedelta(seconds=2))

        assert first.disposition is V2WorkerDisposition.MANUAL_REVIEW
        assert second.disposition is V2WorkerDisposition.NOT_CALLED
        assert sum(len(port.calls) for port in ports.values()) == 1
        assert store._connection.execute(
            "SELECT status,dispatch_slots_consumed FROM execution_ledger "
            "WHERE command_id=?",
            (ordered[1].command_id,),
        ).fetchone() == ("outcome_recorded", 0)
    finally:
        store.close()


def test_bokun_missing_booking_profile_fails_before_fence() -> None:
    activity = ReservationAllocator().allocate(_package_command()).commands[1]
    adapter = V2ReservationExecutionAdapter(
        provider="bokun",
        port=FakeReservationPort(
            "bokun", _result(ProviderCertainty.EFFECT_CONFIRMED)
        ),
        authorization=_authorization("bokun"),
        require_private_binding=False,
    )

    with pytest.raises(PreparationFailure) as raised:
        adapter.prepare(activity)

    assert raised.value.reason == "booking_profile_incomplete"
    assert raised.value.retryable is False


def test_model_supplied_provider_payload_is_rejected_before_provider() -> None:
    port = FakeReservationPort("bokun", _result(ProviderCertainty.EFFECT_CONFIRMED))
    adapter = V2ReservationExecutionAdapter(
        provider="bokun",
        port=port,
        authorization=_authorization("bokun"),
    )

    with pytest.raises(DispatchRejected, match="ReservationCommand"):
        adapter.prepare(
            {
                "operation": "book_activity",
                "provider_payload": {"product_id": "forged"},
            }
        )

    assert port.calls == []


@pytest.mark.parametrize(
    ("port_type", "provider", "operation", "reference_field"),
    (
        (CloudbedsReservationPort, "cloudbeds", "reserve_lodging", "reservation_id"),
        (BokunReservationPort, "bokun", "book_activity", "booking_id"),
    ),
)
def test_specific_provider_ports_return_only_reference_fingerprint(
    port_type,
    provider: str,
    operation: str,
    reference_field: str,
) -> None:
    raw_reference = f"raw-{provider}-reference-001"
    calls = []

    def transport(selected_operation, payload, *, idempotency_key):
        calls.append((selected_operation, payload, idempotency_key))
        return {"status": "confirmed", reference_field: raw_reference}

    payload = json.dumps(
        {
            "command_id": "cmd:v2-provider-port-001",
            "operation": operation,
            "schema": "v2-reservation-dispatch-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    permit = ProviderDispatchPermit(
        provider=provider,
        operation=operation,
        command_id="cmd:v2-provider-port-001",
        idempotency_key="idem:v2-provider-port-001",
        request_hash="c" * 64,
        payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
        canonical_payload=payload,
        fencing_token=1,
        authorization_id=f"authorization:{provider}:task4",
    )

    result = port_type(transport).execute(permit)

    assert result.certainty is ProviderCertainty.EFFECT_CONFIRMED
    assert result.provider_reference_fingerprint == hashlib.sha256(
        raw_reference.encode()
    ).hexdigest()
    assert raw_reference not in repr(result)
    assert calls == [(operation, json.loads(payload), permit.idempotency_key)]
