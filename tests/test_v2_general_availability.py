from __future__ import annotations

from datetime import datetime, timedelta, timezone

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from v2_contracts.channel import InboundBatch, InboundEvent
from v2_host.public_authority import GeneralAvailabilityPublicAuthorityResolver


NOW = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
KEY = b"general-availability-authority-key-0001"


def _batch(subscriber_id: str, suffix: str) -> InboundBatch:
    event = InboundEvent(
        event_id=f"event:ga-{suffix}",
        lead_id=f"manychat:{subscriber_id}",
        subscriber_id=subscriber_id,
        conversation_id=f"conversation:ga-{suffix}",
        text="Oi",
        media_url=None,
        media_type=None,
        occurred_at=NOW - timedelta(seconds=1),
        payload_hash=("a" if suffix == "one" else "b") * 64,
    )
    return InboundBatch(
        batch_id=f"batch:ga-{suffix}",
        lead_id=event.lead_id,
        subscriber_id=event.subscriber_id,
        events=(event,),
        combined_text=event.text,
    )


def test_general_availability_installs_finite_isolated_turn_authority() -> None:
    store = SQLiteBoundaryStore.open_memory_v8()
    try:
        resolver = GeneralAvailabilityPublicAuthorityResolver(
            store=store,
            hmac_key=KEY,
            turn_ttl=timedelta(hours=24),
        )
        first = resolver.resolve(_batch("111111", "one"), chunk_count=2, now=NOW)
        replay = resolver.resolve(
            _batch("111111", "one"),
            chunk_count=2,
            now=NOW + timedelta(seconds=1),
        )
        second = resolver.resolve(_batch("222222", "two"), chunk_count=1, now=NOW)

        assert first == replay
        assert first.scope_subject_id == "111111"
        assert second.scope_subject_id == "222222"
        assert first.authorization_id != second.authorization_id
        assert first.allocation_ids != second.allocation_ids
        assert len(first.allocation_ids) == 2
        assert len(second.allocation_ids) == 1
        rows = tuple(
            store._connection.execute(
                "SELECT scope_subject_id,allowed_chunk_ordinal,state "
                "FROM boundary_dispatch_authority WHERE row_kind='allocation' "
                "ORDER BY scope_subject_id,allowed_chunk_ordinal"
            )
        )
        assert rows == (
            ("111111", 0, "available"),
            ("111111", 1, "available"),
            ("222222", 0, "available"),
        )
    finally:
        store.close()
