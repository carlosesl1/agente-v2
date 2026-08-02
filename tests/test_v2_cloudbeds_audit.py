from __future__ import annotations

from datetime import timedelta
import importlib
import inspect
import logging
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs

import httpx
import pytest

from reservation_domain import ExecutionCertainty, dumps_command
from reservation_execution import DispatchRequest
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.phase5_helpers import T0, persist_script, workflow_events


NOW = T0 + timedelta(minutes=2)
PROPERTY_ID = "property-audit-1"
RESERVATION_ID = "reservation-audit-123"


def _audit_api() -> ModuleType:
    try:
        return importlib.import_module("v2_application.cloudbeds_audit")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Task 4 Cloudbeds audit module is not implemented: {exc}")


def _provider_http_api() -> ModuleType:
    module = importlib.import_module("v2_adapters.provider_http")
    if not hasattr(module, "CloudbedsGETAuditTransport"):
        pytest.fail("Task 4 GET-only Cloudbeds audit transport is not implemented")
    return module


def _add_terminal_outcome(
    execution: SQLiteUnitOfWork,
    *,
    provider: str,
    workflow_suffix: str,
    certainty: ExecutionCertainty,
):
    workflow_id = f"workflow:audit-{workflow_suffix}"
    initial, script = workflow_events(provider, workflow_id=workflow_id)
    execution.create_workflow(initial)
    persist_script(execution, workflow_id, script)
    claim = execution.claim_command(
        worker_id=f"worker:audit-seed-{workflow_suffix}",
        now=NOW,
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    request = DispatchRequest.from_command(claim.command, dumps_command(claim.command))
    permit = execution.fence_dispatch(claim, request, now=NOW)
    provider_reference = {
        ("cloudbeds", ExecutionCertainty.EFFECT_CONFIRMED): (
            f"provider:cloudbeds:{RESERVATION_ID}"
        ),
        ("cloudbeds", ExecutionCertainty.CALLED_NO_EFFECT): None,
        ("cloudbeds", ExecutionCertainty.CALLED_UNKNOWN): None,
        ("bokun", ExecutionCertainty.EFFECT_CONFIRMED): (
            "provider:bokun:" + "b" * 32
        ),
    }[(provider, certainty)]
    outcome = claim.command.outcome(
        certainty=certainty,
        normalized_status={
            ExecutionCertainty.EFFECT_CONFIRMED: "confirmed",
            ExecutionCertainty.CALLED_NO_EFFECT: "rejected",
            ExecutionCertainty.CALLED_UNKNOWN: "unknown",
        }[certainty],
        provider_reference=provider_reference,
        evidence=(request.payload_hash,),
    )
    execution.record_outcome(
        permit,
        outcome,
        now=NOW + timedelta(seconds=1),
    )
    return claim.command


def _confirmed_execution(tmp_path: Path):
    execution = SQLiteUnitOfWork.open(tmp_path / "execution.sqlite3")
    command = _add_terminal_outcome(
        execution,
        provider="cloudbeds",
        workflow_suffix="cloudbeds-confirmed",
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
    )
    return execution, command


def _project(
    api: ModuleType,
    execution: SQLiteUnitOfWork,
    audit_store,
    *,
    property_id: str = PROPERTY_ID,
    max_attempts: int = 3,
):
    return api.CloudbedsAuditProjector(
        execution=execution,
        audit_store=audit_store,
        property_id=property_id,
        max_attempts=max_attempts,
    ).run_once()


def _exact_payload(snapshot, *, reservation_id: str | None = None):
    facts = snapshot.task.expected
    return {
        "success": True,
        "data": {
            "reservationID": reservation_id or snapshot.task.reservation_id,
            "propertyID": facts.property_id,
            "startDate": facts.start_date,
            "endDate": facts.end_date,
            "adults": facts.adults,
            "children": facts.children,
            "total": facts.amount,
            "currency": facts.currency,
            "status": facts.status,
        },
    }


class ScriptedGETPort:
    def __init__(self, actions: list[object]) -> None:
        self.actions = list(actions)
        self.calls: list[str] = []

    def get_reservation(self, reservation_id: str) -> object:
        self.calls.append(reservation_id)
        if not self.actions:
            raise AssertionError("unexpected synthetic GET audit call")
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


def _worker(api: ModuleType, audit_store, port: ScriptedGETPort):
    return api.CloudbedsAuditWorker(
        store=audit_store,
        port=port,
        worker_id="worker:cloudbeds-audit",
        lease_ttl=timedelta(seconds=30),
    )


def test_projection_is_deterministic_idempotent_and_confirmed_cloudbeds_only(
    tmp_path: Path,
) -> None:
    api = _audit_api()
    execution = SQLiteUnitOfWork.open(tmp_path / "projection-execution.sqlite3")
    audit_path = (tmp_path / "private-cloudbeds-audit.sqlite3").resolve()
    audit_store = api.SQLiteCloudbedsAuditStore(audit_path)
    try:
        cloudbeds = _add_terminal_outcome(
            execution,
            provider="cloudbeds",
            workflow_suffix="project-confirmed",
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        _add_terminal_outcome(
            execution,
            provider="cloudbeds",
            workflow_suffix="project-no-effect",
            certainty=ExecutionCertainty.CALLED_NO_EFFECT,
        )
        _add_terminal_outcome(
            execution,
            provider="cloudbeds",
            workflow_suffix="project-unknown",
            certainty=ExecutionCertainty.CALLED_UNKNOWN,
        )
        _add_terminal_outcome(
            execution,
            provider="bokun",
            workflow_suffix="project-bokun",
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )

        first = _project(api, execution, audit_store)
        rows = audit_store.list_tasks()
        second = _project(api, execution, audit_store)

        assert (first.inserted, first.replayed, first.ignored) == (1, 0, 3)
        assert (second.inserted, second.replayed, second.ignored) == (0, 1, 3)
        assert len(rows) == 1
        snapshot = rows[0]
        component = cloudbeds.payload.components[0]
        assert snapshot.task.task_id == api.cloudbeds_audit_task_id(
            cloudbeds.command_id
        )
        assert snapshot.task.command_id == cloudbeds.command_id
        assert snapshot.task.reservation_id == RESERVATION_ID
        assert snapshot.task.expected.property_id == PROPERTY_ID
        assert snapshot.task.expected.start_date == component.start_date.isoformat()
        assert snapshot.task.expected.end_date == component.end_date.isoformat()
        assert snapshot.task.expected.adults == component.party.adults
        assert snapshot.task.expected.children == component.party.children
        assert snapshot.task.expected.amount == f"{component.total.amount:.2f}"
        assert snapshot.task.expected.currency == component.total.currency
        assert snapshot.task.expected.status == "confirmed"
        assert snapshot.task.max_attempts == 3
        assert snapshot.attempts == 0
        assert audit_store.list_tasks() == rows
        assert audit_store.path == audit_path
        assert audit_store.path != execution.path.resolve()
    finally:
        audit_store.close()
        execution.close()


def test_store_is_separate_absolute_wal_full_and_rejects_binding_conflicts(
    tmp_path: Path,
) -> None:
    api = _audit_api()
    with pytest.raises(ValueError, match="absolute"):
        api.SQLiteCloudbedsAuditStore(Path("relative-audit.sqlite3"))

    execution, _ = _confirmed_execution(tmp_path)
    audit_store = api.SQLiteCloudbedsAuditStore(
        (tmp_path / "strict-audit.sqlite3").resolve()
    )
    try:
        _project(api, execution, audit_store)
        assert audit_store._connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert audit_store._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        with pytest.raises(api.CloudbedsAuditIdentityConflict):
            _project(
                api,
                execution,
                audit_store,
                property_id="property-audit-divergent",
            )
        assert len(audit_store.list_tasks()) == 1
    finally:
        audit_store.close()
        execution.close()


def test_auditor_port_and_http_transport_have_only_get_reservation_capability() -> None:
    api = _audit_api()
    provider_api = _provider_http_api()

    protocol_methods = {
        name
        for name, member in inspect.getmembers(
            api.CloudbedsReservationGETAuditPort,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }
    transport_methods = {
        name
        for name, member in inspect.getmembers(
            provider_api.CloudbedsGETAuditTransport,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }

    assert protocol_methods == {"get_reservation"}
    assert transport_methods == {"get_reservation"}
    assert "__call__" not in provider_api.CloudbedsGETAuditTransport.__dict__
    forbidden = {"source_id", "idempotency_key", "post", "post_reservation"}
    assert forbidden.isdisjoint(provider_api.CloudbedsGETAuditTransport.__dict__)


def test_http_transport_uses_strict_bearer_get_to_get_reservation() -> None:
    provider_api = _provider_http_api()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"success": True, "data": {"reservationID": RESERVATION_ID}},
        )

    transport = provider_api.CloudbedsGETAuditTransport(
        api_key="synthetic-cloudbeds-secret",
        property_id=PROPERTY_ID,
        base_url="https://api.cloudbeds.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = transport.get_reservation(RESERVATION_ID)

    assert result == {"success": True, "data": {"reservationID": RESERVATION_ID}}
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/v1.3/getReservation"
    assert parse_qs(seen[0].url.query.decode()) == {
        "propertyID": [PROPERTY_ID],
        "reservationID": [RESERVATION_ID],
    }
    assert seen[0].headers["Authorization"] == "Bearer synthetic-cloudbeds-secret"
    assert "X-Idempotency-Key" not in seen[0].headers
    assert not hasattr(transport, "source_id")
    assert not hasattr(transport, "post")


def test_eventual_incomplete_then_exact_get_retries_and_matches_with_bindings(
    tmp_path: Path,
) -> None:
    api = _audit_api()
    execution, command = _confirmed_execution(tmp_path)
    audit_store = api.SQLiteCloudbedsAuditStore(
        (tmp_path / "eventual-audit.sqlite3").resolve()
    )
    try:
        _project(api, execution, audit_store)
        initial = audit_store.list_tasks()[0]
        port = ScriptedGETPort(
            [
                {"success": False, "message": "synthetic not visible"},
                _exact_payload(initial),
            ]
        )
        worker = _worker(api, audit_store, port)

        first = worker.run_once(now=NOW + timedelta(seconds=2))
        after_first = audit_store.load(initial.task.task_id)
        second = worker.run_once(now=NOW + timedelta(seconds=3))
        matched = audit_store.load(initial.task.task_id)

        assert first.status is api.CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
        assert after_first.status is api.CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
        assert after_first.attempts == 1
        assert second.status is api.CloudbedsAuditStatus.MATCHED
        assert matched.status is api.CloudbedsAuditStatus.MATCHED
        assert matched.attempts == 2
        assert port.calls == [RESERVATION_ID, RESERVATION_ID]
        assert matched.task == initial.task
        assert matched.task.command_id == command.command_id
        assert matched.task.reservation_id == RESERVATION_ID
        assert matched.task.expected == initial.task.expected
        assert matched.task.max_attempts == 3
        assert matched.lease is None
        assert worker.run_once(now=NOW + timedelta(seconds=4)) is None
        assert len(port.calls) == 2
    finally:
        audit_store.close()
        execution.close()


def test_conflicting_principal_id_is_terminal_without_changing_outcome(
    tmp_path: Path,
) -> None:
    api = _audit_api()
    execution, command = _confirmed_execution(tmp_path)
    audit_store = api.SQLiteCloudbedsAuditStore(
        (tmp_path / "divergent-audit.sqlite3").resolve()
    )
    try:
        _project(api, execution, audit_store)
        initial = audit_store.list_tasks()[0]
        before = execution.load_ledger(command.command_id)
        port = ScriptedGETPort(
            [_exact_payload(initial, reservation_id="reservation-audit-divergent")]
        )
        worker = _worker(api, audit_store, port)

        result = worker.run_once(now=NOW + timedelta(seconds=2))
        after = execution.load_ledger(command.command_id)
        snapshot = audit_store.load(initial.task.task_id)

        assert result.status is api.CloudbedsAuditStatus.DIVERGENT
        assert snapshot.status is api.CloudbedsAuditStatus.DIVERGENT
        assert snapshot.attempts == 1
        assert snapshot.task.command_id == command.command_id
        assert snapshot.task.reservation_id == RESERVATION_ID
        assert (after.status, after.outcome_json, after.outcome_hash, after.updated_at) == (
            before.status,
            before.outcome_json,
            before.outcome_hash,
            before.updated_at,
        )
        assert worker.run_once(now=NOW + timedelta(seconds=3)) is None
        assert port.calls == [RESERVATION_ID]
    finally:
        audit_store.close()
        execution.close()


def test_attempt_budget_is_closed_and_stops_further_gets(tmp_path: Path) -> None:
    api = _audit_api()
    execution, _ = _confirmed_execution(tmp_path)
    audit_store = api.SQLiteCloudbedsAuditStore(
        (tmp_path / "budget-audit.sqlite3").resolve()
    )
    try:
        _project(api, execution, audit_store, max_attempts=3)
        task_id = audit_store.list_tasks()[0].task.task_id
        port = ScriptedGETPort([{}, {}, {}])
        worker = _worker(api, audit_store, port)

        assert worker.run_once(now=NOW + timedelta(seconds=2)).status is (
            api.CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
        )
        assert worker.run_once(now=NOW + timedelta(seconds=3)).status is (
            api.CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
        )
        exhausted = worker.run_once(now=NOW + timedelta(seconds=4))

        assert exhausted.status is api.CloudbedsAuditStatus.ATTEMPTS_EXHAUSTED
        assert audit_store.load(task_id).status is (
            api.CloudbedsAuditStatus.ATTEMPTS_EXHAUSTED
        )
        assert audit_store.load(task_id).attempts == 3
        assert worker.run_once(now=NOW + timedelta(seconds=5)) is None
        assert port.calls == [RESERVATION_ID] * 3
    finally:
        audit_store.close()
        execution.close()


def test_conflicting_readback_reservation_alias_is_divergent(tmp_path: Path) -> None:
    api = _audit_api()
    execution, _ = _confirmed_execution(tmp_path)
    audit_store = api.SQLiteCloudbedsAuditStore(
        (tmp_path / "alias-conflict-audit.sqlite3").resolve()
    )
    try:
        _project(api, execution, audit_store)
        initial = audit_store.list_tasks()[0]
        payload = _exact_payload(initial)
        payload["data"]["reservationId"] = "reservation-conflicting-alias"

        result = _worker(api, audit_store, ScriptedGETPort([payload])).run_once(
            now=NOW + timedelta(seconds=2)
        )

        assert result.status is api.CloudbedsAuditStatus.DIVERGENT
    finally:
        audit_store.close()
        execution.close()


def test_transport_exception_consumes_one_bounded_retry_without_retaining_lease(
    tmp_path: Path,
) -> None:
    api = _audit_api()
    execution, _ = _confirmed_execution(tmp_path)
    audit_store = api.SQLiteCloudbedsAuditStore(
        (tmp_path / "transport-error-audit.sqlite3").resolve()
    )
    try:
        _project(api, execution, audit_store)
        initial = audit_store.list_tasks()[0]

        result = _worker(
            api,
            audit_store,
            ScriptedGETPort([RuntimeError("synthetic transport failure")]),
        ).run_once(now=NOW + timedelta(seconds=2))

        assert result.status is api.CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
        assert result.attempts == 1
        assert result.lease is None
        assert audit_store.load(initial.task.task_id) == result
    finally:
        audit_store.close()
        execution.close()


def test_private_reservation_id_is_absent_from_audit_repr(tmp_path: Path) -> None:
    api = _audit_api()
    execution, _ = _confirmed_execution(tmp_path)
    audit_store = api.SQLiteCloudbedsAuditStore(
        (tmp_path / "repr-audit.sqlite3").resolve()
    )
    try:
        _project(api, execution, audit_store)
        snapshot = audit_store.list_tasks()[0]

        assert RESERVATION_ID not in repr(snapshot.task)
        assert RESERVATION_ID not in repr(snapshot)
    finally:
        audit_store.close()
        execution.close()


def test_audit_get_redacts_private_reservation_id_from_http_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="httpx")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"success": True, "data": {"reservationID": RESERVATION_ID}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = _provider_http_api().CloudbedsGETAuditTransport(
        api_key="synthetic-cloudbeds-secret",
        property_id=PROPERTY_ID,
        base_url="https://api.cloudbeds.invalid",
        client=client,
    )
    try:
        transport.get_reservation(RESERVATION_ID)
    finally:
        client.close()

    assert RESERVATION_ID not in caplog.text


def test_non_2xx_audit_exception_traceback_never_contains_private_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request, json={"success": False})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider_api = _provider_http_api()
    transport = provider_api.CloudbedsGETAuditTransport(
        api_key="synthetic-cloudbeds-secret",
        property_id=PROPERTY_ID,
        base_url="https://api.cloudbeds.invalid",
        client=client,
    )
    try:
        try:
            transport.get_reservation(RESERVATION_ID)
        except provider_api.ProviderHTTPError as exc:
            assert exc.__cause__ is None
            logging.getLogger("cloudbeds-audit-witness").exception("audit failed")
        else:
            pytest.fail("non-2xx audit request must fail")
    finally:
        client.close()

    assert RESERVATION_ID not in caplog.text


def test_audit_get_never_follows_redirects_even_when_client_default_does() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.cloudbeds.invalid":
            return httpx.Response(
                307,
                request=request,
                headers={"Location": "https://redirect.invalid/audit"},
            )
        return httpx.Response(
            200,
            request=request,
            json={"success": True, "data": {"reservationID": RESERVATION_ID}},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    transport = _provider_http_api().CloudbedsGETAuditTransport(
        api_key="synthetic-cloudbeds-secret",
        property_id=PROPERTY_ID,
        base_url="https://api.cloudbeds.invalid",
        client=client,
    )
    try:
        with pytest.raises(Exception, match="status=307"):
            transport.get_reservation(RESERVATION_ID)
    finally:
        client.close()

    assert [(request.method, request.url.host) for request in seen] == [
        ("GET", "api.cloudbeds.invalid")
    ]


def test_keyboard_interrupt_leaves_lease_then_expiry_allows_only_another_get(
    tmp_path: Path,
) -> None:
    api = _audit_api()
    execution, _ = _confirmed_execution(tmp_path)
    audit_store = api.SQLiteCloudbedsAuditStore(
        (tmp_path / "crash-audit.sqlite3").resolve()
    )
    try:
        _project(api, execution, audit_store)
        initial = audit_store.list_tasks()[0]
        port = ScriptedGETPort([KeyboardInterrupt(), _exact_payload(initial)])
        worker = _worker(api, audit_store, port)
        claimed_at = NOW + timedelta(seconds=2)

        with pytest.raises(KeyboardInterrupt):
            worker.run_once(now=claimed_at)

        leased = audit_store.load(initial.task.task_id)
        assert leased.status is api.CloudbedsAuditStatus.LEASED
        assert leased.attempts == 1
        assert leased.lease is not None
        assert worker.run_once(now=claimed_at + timedelta(seconds=29)) is None
        recovered = worker.run_once(now=claimed_at + timedelta(seconds=31))
        final = audit_store.load(initial.task.task_id)

        assert recovered.status is api.CloudbedsAuditStatus.MATCHED
        assert final.status is api.CloudbedsAuditStatus.MATCHED
        assert final.attempts == 2
        assert final.lease is None
        assert final.task == initial.task
        assert port.calls == [RESERVATION_ID, RESERVATION_ID]
        public_worker_fields = {
            name for name in vars(worker) if not name.startswith("_")
        }
        assert public_worker_fields.isdisjoint(
            {"source_id", "idempotency_key", "post", "post_reservation"}
        )
    finally:
        audit_store.close()
        execution.close()
