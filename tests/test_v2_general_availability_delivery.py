from __future__ import annotations

from datetime import datetime, timedelta, timezone
import httpx

from v2_adapters.manychat import ManyChatFlowDeliveryAdapter
from v2_adapters.provider_http import ManyChatHTTPTransport
from v2_application.completion import PublicDeliveryWorker, PublicOutboxStore, PublicReply
from v2_contracts.channel import PublicMessageAuthor


NOW = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)


def test_general_availability_delivery_uses_claim_subscriber_without_global_allowlist(
    tmp_path,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        seen.append(payload["subscriber_id"])
        return httpx.Response(200, request=request, json={"status": "success"})

    transport = ManyChatHTTPTransport(
        api_key="manychat-test-key",
        base_url="https://api.manychat.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter = ManyChatFlowDeliveryAdapter(
        transport=transport,
        allowed_subscriber_id=None,
        reply_field_id=101,
        reply_flow_ns="flow:reply:v2",
        payment_link_field_id=201,
        payment_description_field_id=202,
        payment_flow_ns="flow:payment:v2",
    )
    store = PublicOutboxStore((tmp_path / "public-ga.sqlite3").resolve())
    try:
        for index, subscriber_id in enumerate(("111111", "222222"), start=1):
            store.enqueue(
                PublicReply(
                    release_id=f"release:ga:{index}",
                    lead_id=f"manychat:{subscriber_id}",
                    message_id=f"message:ga:{index}",
                    channel="manychat",
                    chunks=(f"Resposta {index}",),
                    author=PublicMessageAuthor.MAYA,
                ),
                now=NOW,
            )
        worker = PublicDeliveryWorker(
            store=store,
            delivery=adapter,
            worker_id="worker:ga-delivery",
            lease_ttl=timedelta(seconds=30),
        )
        assert worker.run_once(now=NOW + timedelta(seconds=1)).value == "accepted"
        assert worker.run_once(now=NOW + timedelta(seconds=2)).value == "accepted"
        assert seen == ["111111", "111111", "222222", "222222"]
    finally:
        store.close()
