from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient

from deploy.canary_router import build_app


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
V2_URL = "http://v2-api:8788/webhook/manychat"
READY_URL = "http://v2-api:8788/readyz"
LEGACY_URL = "http://legacy:8080/webhook/manychat"
SECRET = "relay-test-secret"
TESTER = "1873018537"


def _payload(subscriber_id: str = TESTER) -> dict[str, object]:
    return {
        "subscriber_id": subscriber_id,
        "message_id": "message:relay:001",
        "message": "Oi",
        "message_timestamp": NOW.isoformat(),
    }


def _client(*, ready: bool, now: datetime):
    forwarded: list[tuple[str, bytes, dict[str, str]]] = []
    probes: list[str] = []

    async def forward(target: str, body: bytes, headers: dict[str, str]):
        forwarded.append((target, body, headers))
        return 202, b'{"status":"accepted"}', "application/json"

    async def probe(target: str) -> bool:
        probes.append(target)
        return ready

    app = build_app(
        shared_secret=SECRET,
        allowed_subscriber_id=TESTER,
        v2_url=V2_URL,
        v2_ready_url=READY_URL,
        legacy_url=LEGACY_URL,
        cutover_deadline=NOW + timedelta(minutes=30),
        forward=forward,
        ready_probe=probe,
        clock=lambda: now,
    )
    return TestClient(app), forwarded, probes


def test_ready_tester_routes_once_to_v2() -> None:
    client, forwarded, probes = _client(ready=True, now=NOW)

    response = client.post(
        "/webhook/manychat",
        headers={"X-Hermes-Webhook-Secret": SECRET},
        json=_payload(),
    )

    assert response.status_code == 200
    assert probes == [READY_URL]
    assert [item[0] for item in forwarded] == [V2_URL]
    canonical = json.loads(forwarded[0][1])
    assert canonical["subscriber_id"] == TESTER
    assert canonical["event_id"].startswith("manychat-event:")


def test_expired_deadline_fails_closed_for_tester_without_any_forward() -> None:
    client, forwarded, probes = _client(
        ready=True,
        now=NOW + timedelta(minutes=30),
    )
    payload = _payload()

    response = client.post(
        "/webhook/manychat",
        headers={"X-V2-Webhook-Secret": SECRET},
        json=payload,
    )

    assert response.status_code == 503
    assert response.json() == {"status": "canary_closed"}
    assert probes == []
    assert forwarded == []


def test_unready_v2_fails_closed_for_tester_before_any_forward() -> None:
    client, forwarded, probes = _client(ready=False, now=NOW)

    response = client.post(
        "/webhook/manychat",
        headers={"X-Hermes-Webhook-Secret": SECRET},
        json=_payload(),
    )

    assert response.status_code == 503
    assert response.json() == {"status": "canary_unavailable"}
    assert probes == [READY_URL]
    assert forwarded == []


def test_non_allowlisted_subscriber_never_probes_v2() -> None:
    client, forwarded, probes = _client(ready=True, now=NOW)

    response = client.post(
        "/webhook/manychat",
        headers={"X-Hermes-Webhook-Secret": SECRET},
        json=_payload("999999"),
    )

    assert response.status_code == 202
    assert probes == []
    assert [item[0] for item in forwarded] == [LEGACY_URL]
