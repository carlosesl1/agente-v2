from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from v2_application.lead_identity import EffectTraceContext, SQLiteEffectTraceContextResolver


NOW = datetime(2026, 8, 13, 18, 0, tzinfo=timezone.utc)
RECEIPT = "a" * 64


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.executescript(
        """
        CREATE TABLE boundary_event_sources (
            lead_key TEXT NOT NULL,
            aggregate_turn_id TEXT NOT NULL,
            source_index INTEGER NOT NULL,
            source_event_id TEXT NOT NULL,
            source_turn_receipt_hash TEXT NOT NULL,
            PRIMARY KEY (lead_key, aggregate_turn_id, source_index)
        ) STRICT;
        CREATE TABLE boundary_commands (
            command_id TEXT PRIMARY KEY,
            lead_key TEXT NOT NULL,
            aggregate_turn_id TEXT NOT NULL,
            source_turn_receipt_hash TEXT NOT NULL
        ) STRICT;
        CREATE TABLE boundary_public_outbox (
            public_row_id TEXT PRIMARY KEY,
            lead_key TEXT NOT NULL,
            aggregate_turn_id TEXT NOT NULL,
            source_turn_receipt_hash TEXT NOT NULL
        ) STRICT;
        """
    )
    connection.execute(
        "INSERT INTO boundary_event_sources VALUES (?,?,?,?,?)",
        ("manychat:1873018537", "batch:001", 0, "event:primary", RECEIPT),
    )
    connection.execute(
        "INSERT INTO boundary_event_sources VALUES (?,?,?,?,?)",
        ("manychat:1873018537", "batch:001", 1, "event:coalesced", RECEIPT),
    )
    connection.execute(
        "INSERT INTO boundary_commands VALUES (?,?,?,?)",
        ("command:001", "manychat:1873018537", "batch:001", RECEIPT),
    )
    connection.execute(
        "INSERT INTO boundary_public_outbox VALUES (?,?,?,?)",
        ("public-row:001", "manychat:1873018537", "batch:001", RECEIPT),
    )
    return connection


def test_effect_context_is_exact_immutable_contract() -> None:
    context = EffectTraceContext(
        execution_id="event:primary",
        lead_id="manychat:1873018537",
        source_turn_receipt_hash=RECEIPT,
    )

    assert context.execution_id == "event:primary"
    with pytest.raises((TypeError, ValueError)):
        EffectTraceContext("event:primary", "lead:not-manychat", RECEIPT)
    with pytest.raises((TypeError, ValueError)):
        EffectTraceContext("event:primary", "manychat:1873018537", "bad")


def test_resolver_uses_only_primary_durable_source_for_command_and_public_row() -> None:
    connection = _connection()
    resolver = SQLiteEffectTraceContextResolver(connection)
    try:
        expected = EffectTraceContext(
            execution_id="event:primary",
            lead_id="manychat:1873018537",
            source_turn_receipt_hash=RECEIPT,
        )
        assert resolver.for_command("command:001") == expected
        assert resolver.for_public_row("public-row:001") == expected
        assert resolver.for_source_receipt(RECEIPT) == expected
    finally:
        connection.close()


def test_resolver_fails_closed_for_missing_or_divergent_correlation() -> None:
    connection = _connection()
    resolver = SQLiteEffectTraceContextResolver(connection)
    try:
        with pytest.raises(RuntimeError, match="one durable primary source"):
            resolver.for_command("command:missing")
        connection.execute(
            "UPDATE boundary_commands SET source_turn_receipt_hash=? WHERE command_id=?",
            ("b" * 64, "command:001"),
        )
        with pytest.raises(RuntimeError, match="correlation"):
            resolver.for_command("command:001")
    finally:
        connection.close()
