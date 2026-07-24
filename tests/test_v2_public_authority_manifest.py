from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from v2_contracts.channel import InboundBatch, InboundEvent
from v2_host.public_authority import ManifestPublicAuthorityResolver


NOW = datetime(2026, 7, 23, 18, 0, tzinfo=timezone.utc)
KEY = b"public-authority-key-for-tests-0001"


def _manifest(path: Path, *, key: bytes = KEY) -> None:
    allocations = [
        {"allocation_id": "allocation:prod-0-a", "ordinal": 0},
        {"allocation_id": "allocation:prod-0-b", "ordinal": 0},
        {"allocation_id": "allocation:prod-1-a", "ordinal": 1},
    ]
    authority = {
        "authorization_id": "authority:prod-1",
        "subscriber_id": "1873018537",
        "target_binding_hash": "1" * 64,
        "channel_id": "manychat:channel-prod",
        "channel_scope": "manychat:subscriber-1873018537",
        "generation": 1,
        "capability_policy_digest": "2" * 64,
        "effect_authorization_binding_digest": "3" * 64,
        "contract_digest": "4" * 64,
        "deadline_at": (NOW + timedelta(hours=1)).isoformat(),
        "allocations": allocations,
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
    signed["hmac_sha256"] = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    path.write_text(
        json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _batch() -> InboundBatch:
    event = InboundEvent(
        event_id="event:prod-authority-1",
        lead_id="manychat:1873018537",
        subscriber_id="1873018537",
        conversation_id="conversation:prod-authority-1",
        text="Oi",
        media_url=None,
        media_type=None,
        occurred_at=NOW - timedelta(seconds=1),
        payload_hash="5" * 64,
    )
    return InboundBatch(
        batch_id="batch:prod-authority-1",
        lead_id=event.lead_id,
        subscriber_id=event.subscriber_id,
        events=(event,),
        combined_text=event.text,
    )


def test_authenticated_manifest_installs_and_resolves_finite_shadow_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authority.json"
    _manifest(path)
    store = SQLiteBoundaryStore.open_memory_v8()
    try:
        resolver = ManifestPublicAuthorityResolver(
            store=store,
            manifest_path=path,
            hmac_key=KEY,
            now=NOW,
        )

        authority = resolver.resolve(_batch(), chunk_count=2, now=NOW)

        assert authority.authorization_kind == "conversation_test"
        assert authority.allocation_ids == (
            "allocation:prod-0-a",
            "allocation:prod-1-a",
        )
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_dispatch_authority"
        ).fetchone()[0] == 4
        assert authority.allocation_manifest_hash == hashlib.sha256(
            json.dumps(
                [
                    {"allocation_id": "allocation:prod-0-a", "ordinal": 0},
                    {"allocation_id": "allocation:prod-0-b", "ordinal": 0},
                    {"allocation_id": "allocation:prod-1-a", "ordinal": 1},
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    finally:
        store.close()


def test_manifest_tampering_fails_before_install(tmp_path: Path) -> None:
    path = tmp_path / "authority.json"
    _manifest(path)
    value = json.loads(path.read_text())
    value["authorities"][0]["subscriber_id"] = "attacker"
    path.write_text(json.dumps(value))
    store = SQLiteBoundaryStore.open_memory_v8()
    try:
        with pytest.raises(ValueError, match="authentication failed"):
            ManifestPublicAuthorityResolver(
                store=store,
                manifest_path=path,
                hmac_key=KEY,
                now=NOW,
            )
        assert store._connection.execute(
            "SELECT count(*) FROM boundary_dispatch_authority"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_exhausted_ordinal_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "authority.json"
    _manifest(path)
    store = SQLiteBoundaryStore.open_memory_v8()
    try:
        resolver = ManifestPublicAuthorityResolver(
            store=store,
            manifest_path=path,
            hmac_key=KEY,
            now=NOW,
        )
        with store._transaction():
            store._connection.execute(
                "UPDATE boundary_dispatch_authority SET state='bound',public_row_id=? "
                "WHERE allowed_chunk_ordinal=1",
                ("public:already-bound",),
            )
        with pytest.raises(ValueError, match="exhausted"):
            resolver.resolve(_batch(), chunk_count=2, now=NOW)
    finally:
        store.close()
