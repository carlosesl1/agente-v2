from __future__ import annotations

from datetime import timedelta
import inspect
from pathlib import Path
import traceback

import httpx
import pytest

from reservation_domain import ExecutionCertainty
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.phase5_helpers import T0
from tests.test_v2_cloudbeds_audit import _add_terminal_outcome
from v2_adapters.provider_http import BokunGETAuditTransport
from v2_application.bokun_audit import (
    BokunAuditProjector,
    BokunAuditStatus,
    BokunAuditWorker,
    BokunBookingGETAuditPort,
    SQLiteBokunAuditStore,
)


NOW = T0 + timedelta(minutes=4)


class ScriptedGET:
    def __init__(self, actions: list[object]) -> None:
        self.actions = list(actions)
        self.calls: list[str] = []

    def get_booking(self, booking_id: str) -> object:
        self.calls.append(booking_id)
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


def _seed(tmp_path: Path, *, max_attempts: int = 3):
    execution = SQLiteUnitOfWork.open(tmp_path / "execution.sqlite3")
    command = _add_terminal_outcome(
        execution,
        provider="bokun",
        workflow_suffix="bokun-get-audit",
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
    )
    store = SQLiteBokunAuditStore((tmp_path / "bokun-audit.sqlite3").resolve())
    projection = BokunAuditProjector(
        execution=execution,
        audit_store=store,
        max_attempts=max_attempts,
    ).run_once()
    return execution, command, store, projection


def _matching(snapshot):
    expected = snapshot.task.expected
    return {
        "bookingId": snapshot.task.booking_id,
        "status": "CONFIRMED",
        "totalPrice": expected.total,
        "currency": expected.currency,
        "activityBooking": {
            "activityId": expected.product_id,
            "date": expected.activity_date,
            "startTimeId": expected.provider_start_time_id,
            "rateId": expected.provider_rate_id,
            "startTime": expected.start_time,
            "adults": expected.adults,
            "children": expected.children,
        },
    }


def test_confirmed_bokun_projects_once_and_audits_with_exactly_one_get(
    tmp_path: Path,
) -> None:
    execution, command, store, projection = _seed(tmp_path)
    try:
        rows = store.list_tasks()
        replay = BokunAuditProjector(
            execution=execution,
            audit_store=store,
            max_attempts=3,
        ).run_once()
        assert (projection.inserted, projection.replayed) == (1, 0)
        assert (replay.inserted, replay.replayed) == (0, 1)
        assert len(rows) == 1
        snapshot = rows[0]
        assert snapshot.task.command_id == command.command_id
        rendered = repr(snapshot)
        for private_reference in (
            snapshot.task.booking_id,
            snapshot.task.expected.product_id,
            snapshot.task.expected.provider_start_time_id,
            snapshot.task.expected.provider_rate_id,
        ):
            assert private_reference not in rendered
        assert snapshot.task.expected.total == "1300.00"

        port = ScriptedGET([_matching(snapshot)])
        result = BokunAuditWorker(
            store=store,
            port=port,
            worker_id="worker:bokun-audit",
            lease_ttl=timedelta(seconds=30),
        ).run_once(now=NOW)

        assert result is not None
        assert result.status is BokunAuditStatus.MATCHED
        assert result.attempts == 1
        assert port.calls == [snapshot.task.booking_id]
        assert execution.list_outcome_projection_inputs()[0][1].outcome_json is not None
    finally:
        store.close()
        execution.close()


def test_not_visible_exhausts_closed_budget_without_any_post(tmp_path: Path) -> None:
    execution, _command, store, _projection = _seed(tmp_path, max_attempts=2)
    port = ScriptedGET([RuntimeError("not visible"), RuntimeError("still not visible")])
    worker = BokunAuditWorker(
        store=store,
        port=port,
        worker_id="worker:bokun-audit",
        lease_ttl=timedelta(seconds=30),
    )
    try:
        first = worker.run_once(now=NOW)
        second = worker.run_once(now=NOW + timedelta(seconds=1))
        idle = worker.run_once(now=NOW + timedelta(seconds=2))

        assert first is not None
        assert first.status is BokunAuditStatus.RETRYABLE_NOT_VISIBLE
        assert second is not None
        assert second.status is BokunAuditStatus.ATTEMPTS_EXHAUSTED
        assert idle is None
        assert len(port.calls) == 2
        assert not hasattr(port, "post")
    finally:
        store.close()
        execution.close()


def test_divergent_audit_is_terminal_and_does_not_change_confirmed_outcome(
    tmp_path: Path,
) -> None:
    execution, _command, store, _projection = _seed(tmp_path)
    before = execution.list_outcome_projection_inputs()[0][1].outcome_json
    snapshot = store.list_tasks()[0]
    divergent = _matching(snapshot)
    divergent["totalPrice"] = "999.00"
    try:
        result = BokunAuditWorker(
            store=store,
            port=ScriptedGET([divergent]),
            worker_id="worker:bokun-audit",
            lease_ttl=timedelta(seconds=30),
        ).run_once(now=NOW)

        assert result is not None
        assert result.status is BokunAuditStatus.DIVERGENT
        assert execution.list_outcome_projection_inputs()[0][1].outcome_json == before
    finally:
        store.close()
        execution.close()


def test_bokun_audit_transport_surface_is_get_only_signed_and_no_redirect() -> None:
    protocol_methods = {
        name
        for name, member in inspect.getmembers(
            BokunBookingGETAuditPort,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }
    transport_methods = {
        name
        for name, member in inspect.getmembers(
            BokunGETAuditTransport,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"bookingId": "booking-123"},
        )

    transport = BokunGETAuditTransport(
        access_key="private-access",
        secret_key="private-secret",
        base_url="https://bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-08-09 12:00:00",
    )
    response = transport.get_booking("booking-123")

    assert protocol_methods == {"get_booking"}
    assert transport_methods == {"get_booking"}
    assert "__call__" not in BokunGETAuditTransport.__dict__
    assert response == {"bookingId": "booking-123"}
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/booking.json/booking/booking-123"
    assert seen[0].headers["X-Bokun-AccessKey"] == "private-access"
    assert seen[0].headers["X-Bokun-Signature"]
    rendered = repr(transport)
    assert "private-access" not in rendered
    assert "private-secret" not in rendered


def test_bokun_audit_error_trace_omits_private_booking_url() -> None:
    booking_id = "private-booking-id-123"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request, json={"status": "error"})

    transport = BokunGETAuditTransport(
        access_key="private-access",
        secret_key="private-secret",
        base_url="https://bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-08-09 12:00:00",
    )

    with pytest.raises(RuntimeError) as captured:
        transport.get_booking(booking_id)
    rendered = "".join(
        traceback.format_exception(
            captured.type,
            captured.value,
            captured.tb,
        )
    )
    assert booking_id not in rendered
    assert "bokun.invalid" not in rendered
