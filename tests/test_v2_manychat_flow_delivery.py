from __future__ import annotations

from datetime import datetime, timedelta, timezone
import httpx

from v2_adapters.manychat import ManyChatFlowDeliveryAdapter
from v2_adapters.provider_http import ManyChatHTTPTransport
from v2_application.completion import (
    PublicDeliveryWorker,
    PublicOutboxStore,
    PublicReply,
)

NOW = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)


def _transport(handler) -> ManyChatHTTPTransport:
    return ManyChatHTTPTransport(
        api_key="manychat-test-key",
        base_url="https://api.manychat.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _adapter(transport: ManyChatHTTPTransport) -> ManyChatFlowDeliveryAdapter:
    return ManyChatFlowDeliveryAdapter(
        transport=transport,
        allowed_subscriber_id="1873018537",
        reply_field_id=101,
        reply_flow_ns="flow:reply:v2",
        payment_link_field_id=201,
        payment_description_field_id=202,
        payment_flow_ns="flow:payment:v2",
    )


def test_reply_and_payment_use_typed_custom_fields_then_flows(tmp_path) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        seen.append((request.url.path, body, request.headers.get("Idempotency-Key")))
        if request.url.path.endswith("setCustomField"):
            assert set(body) == {"subscriber_id", "field_id", "field_value"}
        elif request.url.path.endswith("setCustomFields"):
            assert set(body) == {"subscriber_id", "fields"}
        else:
            assert request.url.path.endswith("sendFlow")
            assert set(body) == {"subscriber_id", "flow_ns"}
        return httpx.Response(
            200,
            request=request,
            json={"status": "success", "request_id": f"receipt-{len(seen)}"},
        )

    store = PublicOutboxStore((tmp_path / "public.sqlite3").resolve())
    store.enqueue(
        PublicReply(
            release_id="release:reply:flow",
            lead_id="manychat:1873018537",
            message_id="message:reply:flow",
            channel="manychat",
            chunks=("Resposta da Maya.",),
        ),
        now=NOW,
    )
    store.enqueue(
        PublicReply(
            release_id="release:payment:flow",
            lead_id="manychat:1873018537",
            message_id="message:payment-link:flow",
            channel="manychat",
            chunks=(
                "Link de pagamento da hospedagem: "
                "https://buy.stripe.com/test_completion",
            ),
        ),
        now=NOW,
    )
    worker = PublicDeliveryWorker(
        store=store,
        delivery=_adapter(_transport(handler)),
        worker_id="worker:manychat-flow",
        lease_ttl=timedelta(seconds=30),
    )

    assert worker.run_once(now=NOW + timedelta(seconds=1)).value == "delivered"
    assert worker.run_once(now=NOW + timedelta(seconds=2)).value == "delivered"

    paths = tuple(item[0] for item in seen)
    assert paths == (
        "/fb/subscriber/setCustomFields",
        "/fb/sending/sendFlow",
        "/fb/subscriber/setCustomField",
        "/fb/sending/sendFlow",
    )
    assert seen[0][1] == {
        "subscriber_id": "1873018537",
        "fields": [
            {"field_id": 201, "field_value": "https://buy.stripe.com/test_completion"},
            {"field_id": 202, "field_value": "Link de pagamento da hospedagem"},
        ],
    }
    assert seen[1][1] == {
        "subscriber_id": "1873018537",
        "flow_ns": "flow:payment:v2",
    }
    assert seen[2][1] == {
        "subscriber_id": "1873018537",
        "field_id": 101,
        "field_value": "Resposta da Maya.",
    }
    assert seen[3][1] == {
        "subscriber_id": "1873018537",
        "flow_ns": "flow:reply:v2",
    }
    assert all(item[2] for item in seen)
    store.close()


def test_partial_manychat_mutation_is_manual_review_without_resend(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                request=request,
                json={"status": "success", "request_id": "field-ok"},
            )
        raise httpx.ConnectError("flow connection failed", request=request)

    store = PublicOutboxStore((tmp_path / "partial.sqlite3").resolve())
    store.enqueue(
        PublicReply(
            release_id="release:partial",
            lead_id="manychat:1873018537",
            message_id="message:reply:partial",
            channel="manychat",
            chunks=("Mensagem parcial.",),
        ),
        now=NOW,
    )
    worker = PublicDeliveryWorker(
        store=store,
        delivery=_adapter(_transport(handler)),
        worker_id="worker:manychat-partial",
        lease_ttl=timedelta(seconds=30),
    )

    assert worker.run_once(now=NOW + timedelta(seconds=1)).value == "manual_review"
    assert worker.run_once(now=NOW + timedelta(seconds=2)).value == "idle"
    assert calls == 2
    assert store.manual_review_count() == 1
    store.close()


def test_first_manychat_connect_failure_requeues(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("before request", request=request)

    store = PublicOutboxStore((tmp_path / "not-called.sqlite3").resolve())
    store.enqueue(
        PublicReply(
            release_id="release:not-called",
            lead_id="manychat:1873018537",
            message_id="message:reply:not-called",
            channel="manychat",
            chunks=("Mensagem segura.",),
        ),
        now=NOW,
    )
    worker = PublicDeliveryWorker(
        store=store,
        delivery=_adapter(_transport(handler)),
        worker_id="worker:manychat-not-called",
        lease_ttl=timedelta(seconds=30),
    )

    assert worker.run_once(now=NOW + timedelta(seconds=1)).value == "retryable_failure"
    assert store.pending_count() == 1
    assert calls == 1
    store.close()


def test_delivery_allowlist_blocks_foreign_subscriber_before_transport(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request, json={"status": "success"})

    store = PublicOutboxStore((tmp_path / "foreign.sqlite3").resolve())
    store.enqueue(
        PublicReply(
            release_id="release:foreign",
            lead_id="manychat:999999",
            message_id="message:reply:foreign",
            channel="manychat",
            chunks=("Não deve sair.",),
        ),
        now=NOW,
    )
    worker = PublicDeliveryWorker(
        store=store,
        delivery=_adapter(_transport(handler)),
        worker_id="worker:manychat-allowlist",
        lease_ttl=timedelta(seconds=30),
    )

    assert worker.run_once(now=NOW + timedelta(seconds=1)).value == "manual_review"
    assert calls == 0
    assert store.pending_count() == 0
    assert store.manual_review_count() == 1
    store.close()
