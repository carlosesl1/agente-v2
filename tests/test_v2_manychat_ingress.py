from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from v2_adapters.manychat import ManyChatPayloadError, parse_manychat_payload
from v2_application.inbox import SQLiteInbox
from v2_contracts.channel import AcceptDisposition
from v2_host.app import create_app
from v2_host.settings import V2Settings


FIXTURE = Path(__file__).parent / "fixtures" / "v2" / "manychat_text.json"
TEXT_PAYLOAD = json.loads(FIXTURE.read_text(encoding="utf-8"))
AUTH = {"X-V2-Webhook-Secret": "test-secret"}
NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def inbox(tmp_path: Path) -> SQLiteInbox:
    return SQLiteInbox(tmp_path / "v2.sqlite3")


@pytest.fixture
def client(inbox: SQLiteInbox) -> TestClient:
    settings = V2Settings(
        webhook_secret="test-secret",
        sqlite_path=inbox.path,
        max_body_bytes=4096,
    )
    with TestClient(create_app(settings, inbox)) as test_client:
        yield test_client


def _payload(
    *,
    event_id: str,
    subscriber_id: str,
    text: str,
    occurred_at: datetime,
) -> dict[str, object]:
    return {
        **TEXT_PAYLOAD,
        "message_id": event_id,
        "subscriber_id": subscriber_id,
        "contact_id": subscriber_id,
        "conversation_id": f"conversation-{subscriber_id}",
        "message": text,
        "occurred_at": occurred_at.isoformat(),
    }


def test_webhook_accepts_once_and_never_calls_turn_inline(
    client: TestClient,
    inbox: SQLiteInbox,
) -> None:
    first = client.post("/webhook/manychat", headers=AUTH, json=TEXT_PAYLOAD)
    duplicate = client.post("/webhook/manychat", headers=AUTH, json=TEXT_PAYLOAD)

    assert first.status_code == 202
    assert first.json() == {"status": "accepted"}
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "duplicate"}
    assert inbox.pending_count() == 1


def test_same_event_id_with_different_payload_is_conflict(client: TestClient) -> None:
    client.post("/webhook/manychat", headers=AUTH, json=TEXT_PAYLOAD)
    changed = {**TEXT_PAYLOAD, "message": "conteúdo divergente"}

    response = client.post("/webhook/manychat", headers=AUTH, json=changed)

    assert response.status_code == 409
    assert response.json() == {"status": "conflict"}


def test_webhook_rejects_bad_secret_and_oversized_body(
    client: TestClient,
    inbox: SQLiteInbox,
) -> None:
    unauthorized = client.post("/webhook/manychat", json=TEXT_PAYLOAD)
    oversized = client.post(
        "/webhook/manychat",
        headers={**AUTH, "Content-Type": "application/json"},
        content=b'{' + (b'x' * 5000) + b'}',
    )

    assert unauthorized.status_code == 401
    assert oversized.status_code == 413
    assert inbox.pending_count() == 0


def test_parser_hash_is_canonical_and_requires_stable_event_identity() -> None:
    reordered = dict(reversed(tuple(TEXT_PAYLOAD.items())))

    first = parse_manychat_payload(TEXT_PAYLOAD, NOW)
    second = parse_manychat_payload(reordered, NOW)

    assert first == second
    assert first.event_id == "mc-message-001"
    assert first.lead_id == "manychat:mc-subscriber-001"
    assert first.text == "Olá, quero hospedagem para duas pessoas."
    assert len(first.payload_hash) == 64
    with pytest.raises(ManyChatPayloadError, match="event identity"):
        parse_manychat_payload({"subscriber_id": "mc-subscriber-001", "message": "oi"}, NOW)


def test_concurrent_delivery_is_accepted_exactly_once(inbox: SQLiteInbox) -> None:
    event = parse_manychat_payload(TEXT_PAYLOAD, NOW)

    with ThreadPoolExecutor(max_workers=8) as pool:
        dispositions = tuple(pool.map(lambda _: inbox.accept(event), range(8)))

    assert Counter(dispositions) == {
        AcceptDisposition.ACCEPTED: 1,
        AcceptDisposition.DUPLICATE: 7,
    }
    assert inbox.pending_count() == 1


def test_claim_ready_groups_one_lead_in_order_and_leases_it(
    inbox: SQLiteInbox,
) -> None:
    events = (
        parse_manychat_payload(
            _payload(
                event_id="a-1",
                subscriber_id="lead-a",
                text="primeira",
                occurred_at=NOW - timedelta(seconds=10),
            ),
            NOW,
        ),
        parse_manychat_payload(
            _payload(
                event_id="b-1",
                subscriber_id="lead-b",
                text="outra pessoa",
                occurred_at=NOW - timedelta(seconds=9),
            ),
            NOW,
        ),
        parse_manychat_payload(
            _payload(
                event_id="a-2",
                subscriber_id="lead-a",
                text="segunda",
                occurred_at=NOW - timedelta(seconds=8),
            ),
            NOW,
        ),
    )
    for event in events:
        assert inbox.accept(event) is AcceptDisposition.ACCEPTED

    batch = inbox.claim_ready(
        now=NOW,
        quiet_window=timedelta(seconds=5),
        lease_for=timedelta(seconds=30),
    )

    assert batch is not None
    assert batch.lead_id == "manychat:lead-a"
    assert tuple(event.event_id for event in batch.events) == ("a-1", "a-2")
    assert batch.combined_text == "primeira\nsegunda"
    assert inbox.pending_count() == 1

    second = inbox.claim_ready(
        now=NOW,
        quiet_window=timedelta(seconds=5),
        lease_for=timedelta(seconds=30),
    )
    assert second is not None
    assert second.lead_id == "manychat:lead-b"
    assert second.events == (events[1],)


def test_expired_lease_reclaims_the_same_deterministic_batch_id(tmp_path: Path) -> None:
    inbox = SQLiteInbox(tmp_path / "inbox.sqlite3")
    event = parse_manychat_payload(
        {**TEXT_PAYLOAD, "message_id": "lease-event-001"},
        received_at=NOW,
    )
    assert inbox.accept(event) is AcceptDisposition.ACCEPTED

    first = inbox.claim_ready(
        now=NOW,
        quiet_window=timedelta(0),
        lease_for=timedelta(seconds=10),
    )
    second = inbox.claim_ready(
        now=NOW + timedelta(seconds=11),
        quiet_window=timedelta(0),
        lease_for=timedelta(seconds=10),
    )

    assert first is not None
    assert second is not None
    assert second.batch_id == first.batch_id
    assert second.events == first.events


def test_settings_require_secret_and_absolute_store_path(tmp_path: Path) -> None:
    settings = V2Settings.from_env(
        {
            "V2_MANYCHAT_WEBHOOK_SECRET": "secret-value",
            "V2_SQLITE_PATH": str(tmp_path / "runtime.sqlite3"),
        }
    )
    assert settings.webhook_secret == "secret-value"
    assert settings.sqlite_path.is_absolute()

    with pytest.raises(ValueError, match="absolute"):
        V2Settings.from_env(
            {
                "V2_MANYCHAT_WEBHOOK_SECRET": "secret-value",
                "V2_SQLITE_PATH": "relative.sqlite3",
            }
        )
