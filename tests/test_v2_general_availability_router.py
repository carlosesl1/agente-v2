from __future__ import annotations

from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from deploy.canary_router import build_app


NOW = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
V2_URL = "http://v2-api:8788/webhook/manychat"
READY_URL = "http://v2-api:8788/readyz"
LEGACY_URL = "http://legacy:8080/webhook/manychat"
SECRET = "ga-relay-test-secret"


def test_general_availability_routes_arbitrary_subscriber_to_v2() -> None:
    forwarded: list[tuple[str, bytes]] = []

    async def forward(target: str, body: bytes, _headers: dict[str, str]):
        forwarded.append((target, body))
        return 202, b'{"status":"accepted"}', "application/json"

    async def ready(_target: str) -> bool:
        return True

    async def identity() -> bool:
        return True

    app = build_app(
        shared_secret=SECRET,
        allowed_subscriber_id=None,
        v2_url=V2_URL,
        v2_ready_url=READY_URL,
        legacy_url=LEGACY_URL,
        cutover_deadline=None,
        forward=forward,
        ready_probe=ready,
        identity_probe=identity,
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        response = client.post(
            "/webhook/manychat",
            headers={"X-Hermes-Webhook-Secret": SECRET},
            json={
                "subscriber_id": "999999",
                "message_id": "message:ga-relay:001",
                "message": "Oi",
                "message_timestamp": NOW.isoformat(),
            },
        )

    assert response.status_code == 200
    assert [target for target, _ in forwarded] == [V2_URL]
    assert json.loads(forwarded[0][1])["subscriber_id"] == "999999"
