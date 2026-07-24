from __future__ import annotations

from datetime import timedelta
import json

import httpx

from reservation_followup import HandoffEffectPolicy
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from reservation_followup.workers import HandoffOutboxWorker
from tests.phase6_helpers import T0, handoff_requested
from v2_adapters.provider_http import ManyChatHTTPTransport
from v2_host.manychat_handoff import ManyChatHandoffDeliveryAdapter

NOW = T0 + timedelta(minutes=1)


class _Clock:
    def now(self):
        return NOW


def _transport(handler) -> ManyChatHTTPTransport:
    return ManyChatHTTPTransport(
        api_key="manychat-test-key",
        base_url="https://api.manychat.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _adapter(transport) -> ManyChatHandoffDeliveryAdapter:
    return ManyChatHandoffDeliveryAdapter(
        transport=transport,
        subscriber_id="1873018537",
        tag_id=301,
        flow_ns="flow:handoff:v2",
        clock=_Clock(),
    )


def _store(tmp_path):
    store = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
    store.open_handoff(
        handoff_requested(),
        HandoffEffectPolicy.default_email_disabled(),
    )
    return store


def test_handoff_adds_tag_then_triggers_flow_once(tmp_path) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            request=request,
            json={"status": "success", "request_id": f"handoff-{len(seen)}"},
        )

    store = _store(tmp_path)
    worker = HandoffOutboxWorker(
        store=store,
        delivery=_adapter(_transport(handler)),
        worker_id="worker:manychat-handoff",
        lease_ttl=timedelta(seconds=30),
    )

    first = worker.run_once(now=NOW)
    second = worker.run_once(now=NOW + timedelta(seconds=1))

    assert first.disposition.value == "delivered"
    assert second.disposition.value == "idle"
    assert seen == [
        (
            "/fb/subscriber/addTag",
            {"subscriber_id": "1873018537", "tag_id": 301},
        ),
        (
            "/fb/sending/sendFlow",
            {"subscriber_id": "1873018537", "flow_ns": "flow:handoff:v2"},
        ),
    ]
    store.close()


def test_handoff_partial_mutation_is_manual_review_without_retry(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                request=request,
                json={"status": "success", "request_id": "tag-confirmed"},
            )
        raise httpx.ConnectError("flow response unavailable", request=request)

    store = _store(tmp_path)
    worker = HandoffOutboxWorker(
        store=store,
        delivery=_adapter(_transport(handler)),
        worker_id="worker:manychat-handoff-partial",
        lease_ttl=timedelta(seconds=30),
    )

    first = worker.run_once(now=NOW)
    second = worker.run_once(now=NOW + timedelta(seconds=1))

    assert first.disposition.value == "manual_review"
    assert second.disposition.value == "idle"
    assert calls == 2
    assert store._connection.execute(
        "SELECT status FROM handoff_outbox"
    ).fetchone() == ("manual_review",)
    store.close()


def test_handoff_pre_call_failure_remains_retryable(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("tag connection unavailable", request=request)

    store = _store(tmp_path)
    worker = HandoffOutboxWorker(
        store=store,
        delivery=_adapter(_transport(handler)),
        worker_id="worker:manychat-handoff-not-called",
        lease_ttl=timedelta(seconds=30),
    )

    result = worker.run_once(now=NOW)

    assert result.disposition.value == "retryable_failure"
    assert calls == 1
    assert store._connection.execute(
        "SELECT status FROM handoff_outbox"
    ).fetchone() == ("pending",)
    store.close()
