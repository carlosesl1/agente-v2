from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from reservation_domain import (
    ExecutionCertainty,
    ServiceKind,
    loads_outcome,
)
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.test_v2_production_composition import _audit_enabled_settings
from tests.test_v2_reservations import _cloudbeds_group_command
from v2_adapters.cloudbeds import CloudbedsReservationPort
from v2_adapters.provider_http import (
    CloudbedsGETAuditTransport,
    CloudbedsHTTPTransport,
)
from v2_application.cloudbeds_audit import (
    CloudbedsAuditProjector,
    CloudbedsAuditStatus,
    CloudbedsAuditWorker,
    SQLiteCloudbedsAuditStore,
)
from v2_application.completion import PublicOutboxStore
from v2_application.completion_projector import CompletionProjector
from v2_application.inbox import SQLiteInbox
from v2_application.payments import SQLitePaymentInitiationStore
from v2_application.reads import PrivateOfferBindingResolver
from v2_application.relay_worker import (
    build_reservation_relay_bundle,
    reservation_target_operation_id,
)
from v2_application.reservations import V2ReservationExecutionAdapter
from v2_application.workers import V2ReservationWorker, V2WorkerDisposition
from v2_contracts.channel import AcceptDisposition, InboundEvent
from v2_contracts.private_offers import PrivateOfferBinding
from v2_contracts.providers import ProviderWriteAuthorization
from v2_host.composition import V2Container, V2Role
from v2_host.production import ClosedCapabilityWorker, build_worker_set
from v2_host.worker_main import WorkerQueue


NOW = datetime(2026, 11, 1, 13, 0, tzinfo=timezone.utc)
RESERVATION_ID = "reservation-monotonic-e2e-001"
PROPERTY_ID = "property-monotonic-e2e"
ROOM_TYPE_ID = "room-type-monotonic-e2e"
ROOM_RATE_ID = "room-rate-monotonic-e2e"
PAYMENT_RESULT_KEY = bytes.fromhex("11" * 32)


class _AllowEffects:
    def allows_workflow(self, workflow_id: str) -> bool:
        return bool(workflow_id)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _PrivateBindingPort:
    def resolve(self, query) -> PrivateOfferBinding:
        return PrivateOfferBinding(
            provider="cloudbeds",
            query=query,
            observed_at=NOW - timedelta(seconds=1),
            expires_at=NOW + timedelta(hours=1),
            provider_fields=(
                ("room_rate_id", ROOM_RATE_ID),
                ("room_type_id", ROOM_TYPE_ID),
            ),
        )


def _command_and_source() -> tuple[object, str]:
    return _cloudbeds_group_command(), "source:cloudbeds-monotonic-e2e-confirmation"


def _daily_rates(start: date, end: date, total: Decimal) -> list[dict[str, object]]:
    dates = []
    current = start
    while current < end:
        dates.append(current)
        current += timedelta(days=1)
    rate = total / len(dates)
    return [
        {
            "date": day.isoformat(),
            "rate": f"{rate:.2f}",
            "roomsAvailable": 1,
        }
        for day in dates
    ]


def _submit_handler(command, seen: list[httpx.Request]):
    component = command.payload.components[0]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            assert request.url.path.endswith("/api/v1.3/getAvailableRoomTypes")
            query = parse_qs(request.url.query.decode())
            assert query["propertyID"] == [PROPERTY_ID]
            assert query["startDate"] == [component.start_date.isoformat()]
            assert query["endDate"] == [component.end_date.isoformat()]
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "data": [
                        {
                            "propertyCurrency": {
                                "currencyCode": component.total.currency
                            },
                            "propertyRooms": [
                                {
                                    "roomTypeID": ROOM_TYPE_ID,
                                    "roomRateID": ROOM_RATE_ID,
                                    "roomTypeName": "Quarto sintético",
                                    "roomsAvailable": 1,
                                    "maxGuests": 8,
                                    "roomRateDetailed": _daily_rates(
                                        component.start_date,
                                        component.end_date,
                                        component.total.amount,
                                    ),
                                }
                            ],
                        }
                    ],
                },
            )
        assert request.method == "POST"
        assert request.url.path.endswith("/api/v1.1/postReservation")
        assert request.headers["X-Idempotency-Key"] == command.idempotency_key
        return httpx.Response(
            201,
            request=request,
            json={"reservationID": RESERVATION_ID},
        )

    return handler


def _reservation_worker(store, command, transport) -> V2ReservationWorker:
    adapter = V2ReservationExecutionAdapter(
        provider="cloudbeds",
        port=CloudbedsReservationPort(transport),
        authorization=ProviderWriteAuthorization(
            provider="cloudbeds",
            enabled=True,
            authorization_id="authorization:cloudbeds-monotonic-e2e",
        ),
        binding_resolver=PrivateOfferBindingResolver(
            {ServiceKind.LODGING: _PrivateBindingPort()}
        ),
        clock=_Clock(),
    )
    return V2ReservationWorker(
        store=store,
        adapters=(adapter,),
        effect_guard=_AllowEffects(),
        worker_id="worker:cloudbeds-monotonic-e2e",
        lease_ttl=timedelta(seconds=30),
    )


def _audit_payload(command) -> dict[str, object]:
    component = command.payload.components[0]
    return {
        "success": True,
        "data": {
            "reservationID": RESERVATION_ID,
            "propertyID": PROPERTY_ID,
            "startDate": component.start_date.isoformat(),
            "endDate": component.end_date.isoformat(),
            "adults": component.party.adults,
            "children": component.party.children,
            "total": f"{component.total.amount:.2f}",
            "currency": component.total.currency,
            "status": "confirmed",
        },
    }


def test_monotonic_cloudbeds_survives_crash_completes_and_replays_once(
    tmp_path: Path,
) -> None:
    command, source_event_id = _command_and_source()
    inbox = SQLiteInbox(tmp_path / "inbox.sqlite3")
    inbound = InboundEvent(
        event_id=source_event_id,
        lead_id="manychat:1873018537",
        subscriber_id="1873018537",
        conversation_id="conversation:cloudbeds-monotonic-e2e",
        text="Pode fazer.",
        media_url=None,
        media_type=None,
        occurred_at=NOW - timedelta(seconds=5),
        payload_hash=hashlib.sha256(source_event_id.encode()).hexdigest(),
    )
    assert inbox.accept(inbound) is AcceptDisposition.ACCEPTED

    execution_path = tmp_path / "execution.sqlite3"
    execution = SQLiteUnitOfWork.open_v6(execution_path)
    bundle = build_reservation_relay_bundle(command)
    source_receipt_hash = hashlib.sha256(inbound.payload_hash.encode()).hexdigest()
    operation_id = reservation_target_operation_id(
        bundle_hash=bundle.artifact_hash,
        source_turn_receipt_hash=source_receipt_hash,
    )
    first_boundary_receipt = execution.accept_boundary_reservation(
        operation_id=operation_id,
        source_turn_receipt_hash=source_receipt_hash,
        bundle=bundle,
    )
    replay_boundary_receipt = execution.accept_boundary_reservation(
        operation_id=operation_id,
        source_turn_receipt_hash=source_receipt_hash,
        bundle=bundle,
    )
    assert replay_boundary_receipt == first_boundary_receipt

    submit_seen: list[httpx.Request] = []
    submit_client = httpx.Client(
        transport=httpx.MockTransport(_submit_handler(command, submit_seen))
    )
    submit_transport = CloudbedsHTTPTransport(
        api_key="cloudbeds-test-secret",
        property_id=PROPERTY_ID,
        source_id="source-monotonic-e2e",
        base_url="https://api.cloudbeds.invalid",
        client=submit_client,
    )
    first_worker = _reservation_worker(execution, command, submit_transport)
    first_result = first_worker.run_once(now=NOW)
    assert first_result.disposition is V2WorkerDisposition.EFFECT_CONFIRMED

    ledger_row = execution._connection.execute(
        "SELECT dispatch_slots_consumed,outcome_json FROM execution_ledger"
    ).fetchone()
    assert ledger_row is not None
    assert ledger_row[0] == 1
    private_outcome = loads_outcome(ledger_row[1])
    assert private_outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED
    assert private_outcome.provider_reference == f"provider:cloudbeds:{RESERVATION_ID}"
    assert RESERVATION_ID not in repr(private_outcome)
    assert all(RESERVATION_ID not in item for item in private_outcome.evidence)

    # Simulated process crash/restart immediately after the accepted submit.
    execution.close()
    execution = SQLiteUnitOfWork.open_v6(execution_path)
    restarted_worker = _reservation_worker(execution, command, submit_transport)
    assert (
        restarted_worker.run_once(now=NOW + timedelta(seconds=1)).disposition
        is V2WorkerDisposition.IDLE
    )

    payments = SQLitePaymentInitiationStore(
        (tmp_path / "payments.sqlite3").resolve(),
        result_encryption_key=PAYMENT_RESULT_KEY,
    )
    public = PublicOutboxStore((tmp_path / "public.sqlite3").resolve())
    completion = CompletionProjector(
        execution=execution,
        payment_store=payments,
        public_store=public,
        subscriber_id="1873018537",
        account_profiles=None,
        include_payment_offers=False,
    )
    first_completion = completion.run_once(now=NOW + timedelta(seconds=2))
    assert first_completion.inserted == 1

    audit_actions: list[object] = [{}, KeyboardInterrupt(), _audit_payload(command)]
    audit_seen: list[httpx.Request] = []

    def audit_handler(request: httpx.Request) -> httpx.Response:
        audit_seen.append(request)
        assert request.method == "GET"
        assert request.url.path.endswith("/api/v1.3/getReservation")
        assert parse_qs(request.url.query.decode())["reservationID"] == [
            RESERVATION_ID
        ]
        action = audit_actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return httpx.Response(200, request=request, json=action)

    audit_client = httpx.Client(transport=httpx.MockTransport(audit_handler))
    audit_port = CloudbedsGETAuditTransport(
        api_key="cloudbeds-test-secret",
        property_id=PROPERTY_ID,
        base_url="https://api.cloudbeds.invalid",
        client=audit_client,
    )
    audit_path = tmp_path / "cloudbeds-audit.sqlite3"
    audit_store = SQLiteCloudbedsAuditStore(audit_path)
    projection = CloudbedsAuditProjector(
        execution=execution,
        audit_store=audit_store,
        property_id=PROPERTY_ID,
        max_attempts=3,
    ).run_once()
    assert projection.inserted == 1
    first_audit = CloudbedsAuditWorker(
        store=audit_store,
        port=audit_port,
        worker_id="worker:audit:first",
        lease_ttl=timedelta(seconds=10),
    ).run_once(now=NOW + timedelta(seconds=3))
    assert first_audit is not None
    assert first_audit.status is CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
    assert first_audit.attempts == 1
    audit_store.close()

    audit_store = SQLiteCloudbedsAuditStore(audit_path)
    with pytest.raises(KeyboardInterrupt):
        CloudbedsAuditWorker(
            store=audit_store,
            port=audit_port,
            worker_id="worker:audit:crashed",
            lease_ttl=timedelta(seconds=10),
        ).run_once(now=NOW + timedelta(seconds=4))
    audit_store.close()

    audit_store = SQLiteCloudbedsAuditStore(audit_path)
    matched = CloudbedsAuditWorker(
        store=audit_store,
        port=audit_port,
        worker_id="worker:audit:restart",
        lease_ttl=timedelta(seconds=10),
    ).run_once(now=NOW + timedelta(seconds=15))
    assert matched is not None
    assert matched.status is CloudbedsAuditStatus.MATCHED
    assert matched.attempts == 3

    assert inbox.accept(inbound) is AcceptDisposition.DUPLICATE
    assert (
        restarted_worker.run_once(now=NOW + timedelta(seconds=16)).disposition
        is V2WorkerDisposition.IDLE
    )
    replay_completion = completion.run_once(now=NOW + timedelta(seconds=17))
    assert replay_completion.inserted == 0

    post_requests = [request for request in submit_seen if request.method == "POST"]
    assert len(post_requests) == 1
    assert len(audit_seen) == 3
    assert all(request.method == "GET" for request in audit_seen)
    assert execution._connection.execute(
        "SELECT count(*) FROM reservation_commands"
    ).fetchone() == (1,)
    assert execution._connection.execute(
        "SELECT count(*),sum(dispatch_slots_consumed) FROM execution_ledger"
    ).fetchone() == (1, 1)
    assert payments._connection.execute(
        "SELECT count(*) FROM payment_initiations"
    ).fetchone() == (0,)
    public_rows = public._connection.execute(
        "SELECT text FROM public_outbox ORDER BY release_id,chunk_index"
    ).fetchall()
    assert public_rows == [("Sua hospedagem foi confirmada.",)]
    assert RESERVATION_ID not in public_rows[0][0]
    assert not (tmp_path / "followup.sqlite3").exists()
    assert {request.url.path for request in post_requests} == {
        "/api/v1.1/postReservation"
    }

    audit_store.close()
    audit_client.close()
    public.close()
    payments.close()
    execution.close()
    submit_client.close()


def _composition_settings(tmp_path: Path):
    settings = _audit_enabled_settings(tmp_path)
    manifest = tmp_path / "authority.json"
    authority = {
        "authorization_id": "authority:monotonic-e2e",
        "subscriber_id": "1873018537",
        "target_binding_hash": "1" * 64,
        "channel_id": "manychat:monotonic-e2e",
        "channel_scope": "manychat:subscriber-1873018537",
        "generation": 1,
        "capability_policy_digest": "2" * 64,
        "effect_authorization_binding_digest": "3" * 64,
        "contract_digest": "4" * 64,
        "deadline_at": "2099-01-01T00:00:00+00:00",
        "allocations": [
            {"allocation_id": "allocation:monotonic-e2e:0", "ordinal": 0},
            {"allocation_id": "allocation:monotonic-e2e:1", "ordinal": 1},
        ],
    }
    signed = {
        "schema": "v2-public-authority-manifest-v1",
        "authorities": [authority],
    }
    canonical = json.dumps(
        signed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    signed["hmac_sha256"] = hmac.new(
        settings.public_authority_hmac_key,
        canonical,
        hashlib.sha256,
    ).hexdigest()
    manifest.write_text(json.dumps(signed), encoding="utf-8")
    knowledge = tmp_path / "knowledge.yaml"
    knowledge.write_text(
        "entries:\n  - id: faq-1\n    topic: geral\n    question: Oi?\n    answer: Olá.\n",
        encoding="utf-8",
    )
    return replace(
        settings,
        public_authority_manifest_path=manifest,
        knowledge_base_path=knowledge,
        stripe_hostel_account_profile_id="profile:hostel:closed-payment",
        stripe_agency_account_profile_id="profile:agency:closed-payment",
    )


def test_controlled_lodging_completion_stays_active_with_payment_closed(
    tmp_path: Path,
) -> None:
    settings = _composition_settings(tmp_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        workers = build_worker_set(container=container, settings=settings)

        assert type(workers[WorkerQueue.POST_PAYMENT]) is CompletionProjector
        assert type(workers[WorkerQueue.OUTCOME_PROJECTOR]) is ClosedCapabilityWorker
        assert type(workers[WorkerQueue.PAYMENT_INITIATION]) is ClosedCapabilityWorker
        projector = workers[WorkerQueue.POST_PAYMENT]
        assert projector._include_payment_offers is False
    finally:
        container.close()
