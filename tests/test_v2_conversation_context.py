from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v2_application.private_customer_facts import (
    PrivateCustomerFactIdentityConflict,
    SQLitePrivateCustomerFactStore,
)
from v2_contracts.model import ConversationExchange, InvalidModelProposal, ModelRequest


UTC = timezone.utc


def _request(*, recent_dialogue: tuple[ConversationExchange, ...]) -> ModelRequest:
    return ModelRequest(
        request_id="request:context",
        lead_id="lead:context",
        source_event_id="turn:current",
        message="Current complete customer message",
        locale="en-US",
        state_version=3,
        recent_dialogue=recent_dialogue,
    )


def _record(
    store: SQLitePrivateCustomerFactStore,
    index: int,
    *,
    customer_message: str | None = None,
    reply_chunks: tuple[str, ...] | None = None,
) -> None:
    store.record_dialogue_turn(
        lead_id="lead:context",
        source_turn_id=f"turn:{index}",
        source_event_hash=f"{index:064x}",
        customer_message=customer_message or f"customer message {index}",
        assistant_reply_chunks=reply_chunks or (f"assistant reply {index}",),
        committed_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
        + timedelta(minutes=index),
    )


def test_conversation_exchange_is_bounded_and_hides_raw_text_from_repr() -> None:
    exchange = ConversationExchange(
        customer_message="My private booking context",
        assistant_reply_chunks=("Public reply", "One question?"),
    )

    assert exchange.customer_message == "My private booking context"
    assert exchange.assistant_reply_chunks == ("Public reply", "One question?")
    assert "private booking" not in repr(exchange)
    assert "Public reply" not in repr(exchange)

    with pytest.raises(InvalidModelProposal, match="customer_message exceeds"):
        ConversationExchange(
            customer_message="x" * 16_385,
            assistant_reply_chunks=("reply",),
        )
    with pytest.raises(InvalidModelProposal, match="assistant_reply_chunks"):
        ConversationExchange(customer_message="message", assistant_reply_chunks=())


def test_model_request_accepts_at_most_four_recent_exchanges() -> None:
    exchanges = tuple(
        ConversationExchange(f"customer {index}", (f"assistant {index}",))
        for index in range(4)
    )

    assert _request(recent_dialogue=exchanges).recent_dialogue == exchanges

    with pytest.raises(InvalidModelProposal, match="four-exchange bound"):
        _request(
            recent_dialogue=(
                *exchanges,
                ConversationExchange("customer 5", ("assistant 5",)),
            )
        )


def test_private_dialogue_store_is_exact_idempotent_bounded_and_restart_safe(
    tmp_path,
) -> None:
    path = tmp_path / "private-customer.sqlite3"
    store = SQLitePrivateCustomerFactStore(path)
    for index in range(1, 6):
        _record(store, index)

    loaded = store.load_recent_dialogue("lead:context")
    assert tuple(item.customer_message for item in loaded) == (
        "customer message 2",
        "customer message 3",
        "customer message 4",
        "customer message 5",
    )
    assert tuple(item.assistant_reply_chunks for item in loaded) == (
        ("assistant reply 2",),
        ("assistant reply 3",),
        ("assistant reply 4",),
        ("assistant reply 5",),
    )

    _record(store, 5)
    assert store.load_recent_dialogue("lead:context") == loaded
    store.close()

    restarted = SQLitePrivateCustomerFactStore(path)
    assert restarted.load_recent_dialogue("lead:context") == loaded
    assert path.stat().st_mode & 0o777 == 0o600
    restarted.close()


def test_private_dialogue_store_rejects_divergent_replay(tmp_path) -> None:
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer.sqlite3")
    _record(store, 1)

    with pytest.raises(
        PrivateCustomerFactIdentityConflict,
        match="private dialogue source turn conflicts",
    ):
        _record(store, 1, customer_message="divergent customer message")


def test_private_dialogue_retention_uses_commit_order_when_timestamps_tie(
    tmp_path,
) -> None:
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer.sqlite3")
    instant = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    turn_ids = ("turn:z", "turn:y", "turn:x", "turn:w", "turn:a")
    for index, turn_id in enumerate(turn_ids, start=1):
        store.record_dialogue_turn(
            lead_id="lead:context",
            source_turn_id=turn_id,
            source_event_hash=f"{index:064x}",
            customer_message=f"customer message {index}",
            assistant_reply_chunks=(f"assistant reply {index}",),
            committed_at=instant,
        )

    assert tuple(
        item.customer_message for item in store.load_recent_dialogue("lead:context")
    ) == (
        "customer message 2",
        "customer message 3",
        "customer message 4",
        "customer message 5",
    )


def test_private_dialogue_store_detects_content_tampering(tmp_path) -> None:
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer.sqlite3")
    _record(store, 1)
    store._connection.execute(
        "UPDATE private_dialogue_turns SET customer_message=? "
        "WHERE lead_id=? AND source_turn_id=?",
        ("tampered raw dialogue", "lead:context", "turn:1"),
    )

    with pytest.raises(RuntimeError, match="private dialogue row is invalid"):
        store.load_recent_dialogue("lead:context")
