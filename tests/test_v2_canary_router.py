from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from deploy.canary_router import build_app, create_app_from_env


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


def _client(
    *,
    ready: bool,
    now: datetime,
    identity: bool = True,
    deadline: bool = True,
):
    forwarded: list[tuple[str, bytes, dict[str, str]]] = []
    probes: list[str] = []
    identity_probes: list[str] = []

    async def forward(target: str, body: bytes, headers: dict[str, str]):
        forwarded.append((target, body, headers))
        return 202, b'{"status":"accepted"}', "application/json"

    async def probe(target: str) -> bool:
        probes.append(target)
        return ready

    async def identity_probe() -> bool:
        identity_probes.append("identity")
        return identity

    app = build_app(
        shared_secret=SECRET,
        allowed_subscriber_id=TESTER,
        v2_url=V2_URL,
        v2_ready_url=READY_URL,
        legacy_url=LEGACY_URL,
        cutover_deadline=(NOW + timedelta(minutes=30) if deadline else None),
        forward=forward,
        ready_probe=probe,
        identity_probe=identity_probe,
        clock=lambda: now,
    )
    return TestClient(app), forwarded, probes, identity_probes


def test_ready_tester_routes_once_to_v2() -> None:
    client, forwarded, probes, identity_probes = _client(ready=True, now=NOW)

    response = client.post(
        "/webhook/manychat",
        headers={"X-Hermes-Webhook-Secret": SECRET},
        json=_payload(),
    )

    assert response.status_code == 200
    assert identity_probes == ["identity", "identity"]
    assert probes == [READY_URL]
    assert [item[0] for item in forwarded] == [V2_URL]
    canonical = json.loads(forwarded[0][1])
    assert canonical["subscriber_id"] == TESTER
    assert canonical["event_id"].startswith("manychat-event:")


def test_expired_deadline_fails_closed_for_tester_without_any_forward() -> None:
    client, forwarded, probes, identity_probes = _client(
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
    assert identity_probes == ["identity"]
    assert probes == []
    assert forwarded == []


def test_unready_v2_fails_closed_for_tester_before_any_forward() -> None:
    client, forwarded, probes, identity_probes = _client(ready=False, now=NOW)

    response = client.post(
        "/webhook/manychat",
        headers={"X-Hermes-Webhook-Secret": SECRET},
        json=_payload(),
    )

    assert response.status_code == 503
    assert response.json() == {"status": "canary_unavailable"}
    assert identity_probes == ["identity"]
    assert probes == [READY_URL]
    assert forwarded == []


def test_non_allowlisted_subscriber_never_probes_v2() -> None:
    client, forwarded, probes, identity_probes = _client(ready=True, now=NOW)

    response = client.post(
        "/webhook/manychat",
        headers={"X-Hermes-Webhook-Secret": SECRET},
        json=_payload("999999"),
    )

    assert response.status_code == 202
    assert identity_probes == ["identity", "identity"]
    assert probes == []
    assert [item[0] for item in forwarded] == [LEGACY_URL]


def test_identity_mismatch_fails_before_ready_probe_or_any_forward() -> None:
    client, forwarded, probes, identity_probes = _client(
        ready=True,
        now=NOW,
        identity=False,
    )

    response = client.post(
        "/webhook/manychat",
        headers={"X-Hermes-Webhook-Secret": SECRET},
        json=_payload(),
    )

    assert response.status_code == 503
    assert response.json() == {"status": "runtime_identity_rejected"}
    assert identity_probes == ["identity"]
    assert probes == []
    assert forwarded == []


def test_router_readiness_fails_closed_on_identity_mismatch() -> None:
    client, forwarded, probes, identity_probes = _client(
        ready=True,
        now=NOW,
        identity=False,
    )

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unready"}
    assert identity_probes == ["identity"]
    assert probes == []
    assert forwarded == []


def test_persistent_canary_without_deadline_remains_eligible() -> None:
    client, forwarded, probes, identity_probes = _client(
        ready=True,
        now=NOW + timedelta(days=365),
        deadline=False,
    )

    response = client.post(
        "/webhook/manychat",
        headers={"X-Hermes-Webhook-Secret": SECRET},
        json=_payload(),
    )

    assert response.status_code == 200
    assert identity_probes == ["identity", "identity"]
    assert probes == [READY_URL]
    assert [item[0] for item in forwarded] == [V2_URL]


def test_env_factory_reads_mounted_identity_metadata_without_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_sha = "a" * 40
    digest = "sha256:" + "b" * 64
    image_ref = f"registry.invalid/agente-v2@{digest}"
    metadata = tmp_path / "runtime-identity.json"
    metadata.write_text(
        json.dumps(
            {
                "schema": "v2-runtime-image-metadata-v1",
                "labels": {"org.opencontainers.image.revision": git_sha},
                "repo_digests": [image_ref],
            }
        ),
        encoding="utf-8",
    )
    environment = {
        "CANARY_WEBHOOK_SECRET": SECRET,
        "CANARY_SUBSCRIBER_ID": TESTER,
        "CANARY_V2_URL": V2_URL,
        "CANARY_V2_READY_URL": READY_URL,
        "CANARY_LEGACY_URL": LEGACY_URL,
        "CANARY_CUTOVER_DEADLINE": "",
        "CANARY_EXPECTED_GIT_SHA": git_sha,
        "CANARY_EXPECTED_IMAGE_REF": image_ref,
        "CANARY_EXPECTED_IMAGE_DIGEST": digest,
        "CANARY_RUNTIME_IDENTITY_METADATA_PATH": str(metadata),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with TestClient(create_app_from_env()) as client:
        assert client.get("/readyz").json() == {"status": "ready"}

    monkeypatch.setenv("CANARY_EXPECTED_GIT_SHA", "c" * 40)
    with TestClient(create_app_from_env()) as client:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"status": "unready"}


def test_identity_is_rechecked_after_ready_probe_immediately_before_forward() -> None:
    identity_results = iter((True, False))
    forwarded: list[str] = []

    async def identity_probe() -> bool:
        return next(identity_results)

    async def ready_probe(_target: str) -> bool:
        return True

    async def forward(target: str, _body: bytes, _headers: dict[str, str]):
        forwarded.append(target)
        return 200, b'{"status":"ok"}', "application/json"

    app = build_app(
        shared_secret=SECRET,
        allowed_subscriber_id=TESTER,
        v2_url=V2_URL,
        v2_ready_url=READY_URL,
        legacy_url=LEGACY_URL,
        cutover_deadline=None,
        forward=forward,
        ready_probe=ready_probe,
        identity_probe=identity_probe,
        clock=lambda: NOW,
    )

    with TestClient(app) as client:
        response = client.post(
            "/webhook/manychat",
            headers={"X-Hermes-Webhook-Secret": SECRET},
            json=_payload(),
        )

    assert response.status_code == 503
    assert response.json() == {"status": "runtime_identity_rejected"}
    assert forwarded == []


def test_deadline_is_rechecked_after_ready_probe_immediately_before_forward() -> None:
    now = NOW
    deadline = NOW + timedelta(seconds=1)
    forwarded: list[str] = []

    async def identity_probe() -> bool:
        return True

    async def ready_probe(_target: str) -> bool:
        nonlocal now
        now = deadline
        return True

    async def forward(target: str, _body: bytes, _headers: dict[str, str]):
        forwarded.append(target)
        return 200, b'{"status":"ok"}', "application/json"

    app = build_app(
        shared_secret=SECRET,
        allowed_subscriber_id=TESTER,
        v2_url=V2_URL,
        v2_ready_url=READY_URL,
        legacy_url=LEGACY_URL,
        cutover_deadline=deadline,
        forward=forward,
        ready_probe=ready_probe,
        identity_probe=identity_probe,
        clock=lambda: now,
    )

    with TestClient(app) as client:
        response = client.post(
            "/webhook/manychat",
            headers={"X-Hermes-Webhook-Secret": SECRET},
            json=_payload(),
        )

    assert response.status_code == 503
    assert response.json() == {"status": "canary_closed"}
    assert forwarded == []
