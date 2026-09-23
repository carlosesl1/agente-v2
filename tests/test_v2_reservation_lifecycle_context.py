"""Native GET lifecycle facts, separate from immutable creation/payment ledgers."""

import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from reservation_domain import ExecutionCertainty, dumps_command
from reservation_execution import DispatchRequest
from tests.test_v2_execution_context import _queued_state
from tests.test_v2_outcome_projector import NOW, _package_command, _persist, _stores
from v2_adapters import execution_context as adapter
from v2_adapters.provider_http import BokunGETAuditTransport, CloudbedsGETAuditTransport
from v2_application.active_execution import ReservationExecutionStatusResolver
from v2_application.reservations import ReservationAllocator


class Clock:
    def now(self):
        return NOW


def _reader(handler):
    factory = getattr(adapter, "ReservationStatusReader", None)
    assert factory is not None, "missing native lifecycle reader API"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return factory(
        cloudbeds=CloudbedsGETAuditTransport(
            api_key="test", property_id="1", client=client
        ),
        bokun=BokunGETAuditTransport(
            access_key="test", secret_key="test", client=client
        ),
        clock=Clock(),
    )


def _outcome(provider):
    command = next(
        c
        for c in ReservationAllocator().allocate(_package_command()).commands
        if c.payload.components[0].service.value
        == ("lodging" if provider == "cloudbeds" else "activity")
    )
    reference = (
        "provider:cloudbeds:12345"
        if provider == "cloudbeds"
        else "provider:bokun:id:67890"
    )
    return command, command.outcome(
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        normalized_status="confirmed",
        provider_reference=reference,
        evidence=("a" * 64,),
    )


@pytest.mark.parametrize(
    "status,payment,paid,due",
    [
        ("RESERVED", "NOT_PAID", 0, 730.8),
        ("CONFIRMED", "PARTIALLY_PAID", 146.16, 584.64),
        ("CONFIRMED", "PAID", 730.8, 0),
        ("ABORTED", "NOT_PAID", 0, 730.8),
    ],
)
def test_bokun_preserves_exact_status_and_independent_payment(
    status, payment, paid, due
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "bookingId": 67890,
                "status": status,
                "paymentType": payment,
                "totalPaid": paid,
                "totalDue": due,
                "currency": "BRL",
            },
        )

    reader = _reader(handler)
    result = reader.read(
        service="activity", provider_reference=_outcome("bokun")[1].provider_reference
    )
    assert result.source_status == "observed"
    assert result.reservation_status == status
    assert result.payment_status == payment
    assert result.paid_amount == f"{paid:.2f}"
    assert result.balance_due == f"{due:.2f}"
    assert result.observed_at == NOW
    assert len(calls) == 1 and calls[0].method == "GET"
    assert calls[0].url.path == "/booking.json/booking/67890"


@pytest.mark.parametrize(
    "status,paid,balance,expected",
    [
        ("confirmed", 0, 450, "NOT_PAID"),
        ("confirmed", 100, 350, "PARTIALLY_PAID"),
        ("confirmed", 450, 0, "PAID"),
        ("canceled", 0, 450, "NOT_PAID"),
        ("canceled", 0, 0, None),
    ],
)
def test_cloudbeds_confirmation_and_cancellation_do_not_imply_paid(
    status, paid, balance, expected
):
    def handler(request):
        assert request.method == "GET"
        assert request.url.params["reservationID"] == "12345"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "reservationID": "12345",
                    "status": status,
                    "total": 450,
                    "balance": balance,
                    "balanceDetailed": {"paid": paid, "grandTotal": 450},
                },
            },
        )

    result = _reader(handler).read(
        service="lodging",
        provider_reference=_outcome("cloudbeds")[1].provider_reference,
    )
    assert result.reservation_status == status
    assert result.payment_status == expected
    assert result.paid_amount == f"{paid:.2f}"
    assert result.balance_due == f"{balance:.2f}"
    assert result.currency is None  # do not invent a currency absent in this GET


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"bookingId": 999, "status": "PAID"},
        {"bookingId": 67890, "status": "RESERVED", "bookingStatus": "CONFIRMED"},
        {"bookingId": 67890, "success": False, "status": "CONFIRMED"},
    ],
)
def test_missing_or_conflicting_read_never_becomes_current_confirmation(payload):
    result = _reader(lambda r: httpx.Response(200, json=payload)).read(
        service="activity", provider_reference=_outcome("bokun")[1].provider_reference
    )
    assert result.source_status == "unavailable"
    assert result.reservation_status is None and result.payment_status is None


def test_failed_read_and_legacy_fingerprint_cannot_replay_booking_or_fake_status():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic failure")

    reader = _reader(handler)
    _command, outcome = _outcome("bokun")
    assert (
        reader.read(
            service="activity", provider_reference=outcome.provider_reference
        ).source_status
        == "unavailable"
    )
    legacy = replace(outcome, provider_reference="provider:bokun:" + "a" * 32)
    assert (
        reader.read(
            service="activity", provider_reference=legacy.provider_reference
        ).source_status
        == "unavailable"
    )
    assert len(calls) == 1 and calls[0].method == "GET"


def test_real_stores_refresh_status_after_reopen_without_changing_creation(tmp_path):
    execution, payments, _ = _stores(tmp_path)
    parent = _package_command()
    command, _ = _outcome("bokun")
    _persist(execution, (command,))
    claim = execution.claim_command(
        worker_id="worker:lifecycle", now=NOW, lease_ttl=timedelta(seconds=30)
    )
    request = DispatchRequest.from_command(command, dumps_command(command))
    permit = execution.fence_dispatch(claim, request, now=NOW)
    outcome = command.outcome(
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        normalized_status="confirmed",
        provider_reference="provider:bokun:id:67890",
        evidence=(request.payload_hash,),
    )
    execution.record_outcome(permit, outcome, now=NOW)
    execution.close()
    from reservation_execution.sqlite_store import SQLiteUnitOfWork

    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
    status = ["RESERVED"]

    def handler(request):
        return httpx.Response(
            200,
            json={
                "bookingId": 67890,
                "status": status[0],
                "paymentType": "NOT_PAID",
                "totalPaid": 0,
                "totalDue": 730.8,
                "currency": "BRL",
            },
        )

    reader = _reader(handler)
    try:
        resolver = ReservationExecutionStatusResolver(
            execution, payment_store=payments, reservation_status_reader=reader
        )
        initial = next(
            c
            for c in resolver.context(_queued_state(parent)).components
            if c.command_id
        )
        assert initial.reservation_status.reservation_status == "RESERVED"
        status[0] = "ABORTED"
        cancelled = next(
            c
            for c in resolver.context(_queued_state(parent)).components
            if c.command_id
        )
        assert cancelled.outcome == initial.outcome  # creation certainty is monotonic
        row = adapter.component_wire(cancelled)
        assert row["reservation"]["status"] == "ABORTED"
        assert row["reservation"]["payment_status"] == "NOT_PAID"
        assert row["execution_status_scope"] == "creation_result"
        assert row["payment"]["settlement_status"] == "unavailable"
        assert row["reservation"]["observed_at"] == NOW.isoformat()
        from v2_adapters.hermes_model import _request_wire
        from v2_contracts.model import ModelRequest

        request = ModelRequest(
            "request:lifecycle",
            "manychat:1",
            "event:lifecycle",
            "Qual o status exato?",
            "pt-BR",
            0,
            execution_components=(cancelled,),
        )
        frame = json.loads(
            json.loads(_request_wire(request, "Maya"))["messages"][-1][1]
        )
        assert frame["execution_components"][0]["reservation"] == row["reservation"]
        ledger = execution.list_outcome_projection_inputs()[0][1]
        assert ledger.dispatch_slots_consumed == 1
    finally:
        execution.close()
        payments.close()


def test_prompt_explicitly_separates_creation_current_status_and_payment():
    from v2_adapters.hermes_model import _ACTIVE_EXECUTION_SYSTEM_SUFFIX

    assert "creation_result" in _ACTIVE_EXECUTION_SYSTEM_SUFFIX
    assert "reservation.status" in _ACTIVE_EXECUTION_SYSTEM_SUFFIX
    assert "automatic cancellation" in _ACTIVE_EXECUTION_SYSTEM_SUFFIX
