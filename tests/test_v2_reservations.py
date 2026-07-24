from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
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
    ReservationCommand,
    ReservationOperation,
)
from reservation_domain.signature import command_identity, subject_signature
from reservation_execution import PreparationFailure
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.phase5_helpers import T0, _lookup, persist_script, workflow_events
from v2_adapters.bokun import BokunReservationPort
from v2_adapters.cloudbeds import CloudbedsReservationPort
from v2_application.reservations import (
    DispatchRejected,
    ReservationAllocator,
    V2ReservationExecutionAdapter,
)
from v2_application.workers import V2ReservationWorker, V2WorkerDisposition
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


def _package_command() -> ReservationCommand:
    lodging = _lookup("cloudbeds").offers[0]
    activity = _lookup("bokun").offers[0]
    activity = replace(activity, total=Money(activity.total.amount, lodging.total.currency))
    components = (lodging, activity)
    customer = CustomerFacts(
        customer_ref="customer:v2-package-001",
        full_name="Carlos Synthetic",
        email="carlos.synthetic@example.invalid",
        phone_e164="+99900000000",
        country_code="ZZ",
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


def test_package_allocation_produces_two_provider_commands_as_one_batch() -> None:
    package = _package_command()

    allocation = ReservationAllocator().allocate(package)

    assert allocation.source_command_id == package.command_id
    assert tuple(command.operation for command in allocation.commands) == (
        ReservationOperation.RESERVE_LODGING,
        ReservationOperation.BOOK_ACTIVITY,
    )
    assert len({command.command_id for command in allocation.commands}) == 2
    assert all(len(command.payload.components) == 1 for command in allocation.commands)
    assert ReservationAllocator().allocate(package) == allocation
    assert ReservationAllocator().expand_commands((package,)) == allocation.commands


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
