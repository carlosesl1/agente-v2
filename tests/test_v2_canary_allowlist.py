from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from v2_application.inbox import SQLiteInbox
from v2_host.app import create_app
from v2_host.settings import V2Settings


AUTH = {"X-V2-Webhook-Secret": "test-secret"}
ALLOWED = "1873018537"
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "subscriber_id": ALLOWED,
        "contact_id": "contact-independent-id",
        "conversation_id": "conversation-carlos",
        "message_id": "message-001",
        "message": "Quero reservar",
        "occurred_at": NOW.isoformat(),
    }
    payload.update(overrides)
    return payload


def _client(tmp_path: Path) -> tuple[TestClient, SQLiteInbox]:
    inbox = SQLiteInbox(tmp_path / "inbox.sqlite3")
    settings = V2Settings(
        webhook_secret="test-secret",
        sqlite_path=inbox.path,
        allowed_subscriber_ids=(ALLOWED,),
    )
    return TestClient(create_app(settings, inbox, clock=lambda: NOW)), inbox


def test_wrong_subscriber_is_forbidden_before_inbox_persistence(tmp_path: Path) -> None:
    client, inbox = _client(tmp_path)
    with client:
        response = client.post(
            "/webhook/manychat",
            headers=AUTH,
            json=_payload(subscriber_id="999999"),
        )

    assert response.status_code == 403
    assert response.json() == {"status": "forbidden"}
    assert inbox.pending_count() == 0


def test_contact_id_or_phone_cannot_replace_the_allowlisted_subscriber(tmp_path: Path) -> None:
    client, inbox = _client(tmp_path)
    missing_subscriber = _payload(contact_id=ALLOWED, phone="+5575999999999")
    missing_subscriber.pop("subscriber_id")
    wrong_subscriber = _payload(
        subscriber_id="999999",
        contact_id=ALLOWED,
        phone="+5575999999999",
    )

    with client:
        first = client.post("/webhook/manychat", headers=AUTH, json=missing_subscriber)
        second = client.post("/webhook/manychat", headers=AUTH, json=wrong_subscriber)

    assert first.status_code == 403
    assert second.status_code == 403
    assert inbox.pending_count() == 0


def test_conflicting_explicit_subscriber_id_is_forbidden(tmp_path: Path) -> None:
    client, inbox = _client(tmp_path)
    payload = _payload(subscriber={"id": "999999"})

    with client:
        response = client.post("/webhook/manychat", headers=AUTH, json=payload)

    assert response.status_code == 403
    assert inbox.pending_count() == 0


def test_exact_allowlisted_subscriber_reaches_normal_durable_dedupe(tmp_path: Path) -> None:
    client, inbox = _client(tmp_path)
    payload = _payload(subscriber={"id": ALLOWED})

    with client:
        first = client.post("/webhook/manychat", headers=AUTH, json=payload)
        duplicate = client.post("/webhook/manychat", headers=AUTH, json=payload)

    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert inbox.pending_count() == 1


def test_allowlist_reject_happens_before_full_message_validation(tmp_path: Path) -> None:
    client, inbox = _client(tmp_path)
    payload = {
        "subscriber_id": "999999",
        "message_id": "outside-invalid",
    }

    with client:
        response = client.post("/webhook/manychat", headers=AUTH, json=payload)

    assert response.status_code == 403
    assert inbox.pending_count() == 0
