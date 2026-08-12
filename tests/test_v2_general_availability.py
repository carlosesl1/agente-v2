from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from reservation_boundary.sqlite_store import SQLiteBoundaryStore
from v2_contracts.channel import InboundBatch, InboundEvent
from v2_host.public_authority import GeneralAvailabilityPublicAuthorityResolver
from v2_host.composition import V2Container, V2Role
from v2_host.settings import RuntimeMode, V2ProcessRole, V2Settings


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


def test_general_availability_reports_on_demand_turn_capacity(tmp_path: Path) -> None:
    knowledge = (tmp_path / "knowledge.sqlite3").resolve()
    knowledge.touch()
    settings = V2Settings(
        webhook_secret="",
        sqlite_path=(tmp_path / "inbox.sqlite3").resolve(),
        process_role=V2ProcessRole.WORKER,
        runtime_mode=RuntimeMode.GENERAL_AVAILABILITY,
        hermes_model="openai-codex/gpt-5.6-luna",
        candidate_git_sha="a" * 40,
        candidate_image_digest="sha256:" + "b" * 64,
        cloudbeds_api_key="cloudbeds-secret",
        cloudbeds_property_id="property-1",
        bokun_access_key="bokun-access",
        bokun_secret_key="bokun-secret",
        bokun_product_map={"product:buracao": "913372"},
        manychat_api_key="manychat-secret",
        hermes_command=("python", "hermes_child.py", "hermes"),
        hermes_system_prompt="closed prompt",
        hermes_transcript_key=b"general-availability-transcript-key",
        knowledge_base_path=knowledge,
        read_probe_check_in="2026-08-05",
        read_probe_check_out="2026-08-06",
        read_probe_activity_date="2026-08-05",
        read_probe_product_id="product:buracao",
        public_authority_hmac_key=KEY,
    )
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        resolver = GeneralAvailabilityPublicAuthorityResolver(
            store=container.boundary,
            hmac_key=KEY,
        )
        container.register_public_authority_resolver(resolver)

        assert container.public_turn_capacity(now=NOW) == 1
        assert container.controlled_public_ingress_reason(now=NOW) is None
    finally:
        container.close()
