from __future__ import annotations

import base64
import json
import sqlite3
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from v2_ops.contracts import (
    ExecutionStatus,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
    canonical_json_bytes,
)
from v2_ops.crypto import AES256GCMCipher, parse_trace_key_hex
from v2_ops.store import (
    MAX_NODE_QUERY_LIMIT,
    MAX_PAGE_LIMIT,
    SCHEMA_FINGERPRINT,
    SCHEMA_VERSION,
    OpsTraceConflictError,
    OpsTraceStoreError,
    SQLiteOpsTraceReader,
    SQLiteOpsTraceWriter,
)


UTC_NOW = datetime(2026, 8, 13, 15, 0, 0, 123456, tzinfo=timezone.utc)
KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))


def execution(
    execution_id: str = "event-001",
    *,
    lead_id: str = "lead:opaque-001",
    received_at: datetime = UTC_NOW,
    status: ExecutionStatus = ExecutionStatus.PENDING,
    current_node_id: str | None = None,
    completed_at: datetime | None = None,
    terminal_reason: str | None = None,
) -> OpsExecution:
    return OpsExecution(
        execution_id=execution_id,
        lead_id=lead_id,
        received_at=received_at,
        status=status,
        trace_completeness=TraceCompleteness.COMPLETE_TRACE,
        current_node_id=current_node_id,
        completed_at=completed_at,
        terminal_reason=terminal_reason,
    )


def node_start(
    execution_id: str = "event-001",
    *,
    ordinal: int = 1,
    node_type: NodeType = NodeType.MAYA_REQUEST,
    started_at: datetime = UTC_NOW + timedelta(seconds=1),
    input_summary: dict[str, object] | None = None,
    input_full: dict[str, object] | None = None,
) -> OpsNodeStart:
    return OpsNodeStart(
        execution_id=execution_id,
        node_type=node_type,
        ordinal=ordinal,
        started_at=started_at,
        input_summary={"kind": "summary"} if input_summary is None else input_summary,
        input_full={"permitted": "input-full"} if input_full is None else input_full,
        technical_metadata={"round": ordinal},
    )


def node_finish(
    start: OpsNodeStart,
    *,
    completed_at: datetime | None = None,
    status: ExecutionStatus = ExecutionStatus.COMPLETED,
    output_summary: dict[str, object] | None = None,
    output_full: dict[str, object] | None = None,
) -> OpsNodeFinish:
    return OpsNodeFinish.from_start(
        start,
        status=status,
        completed_at=completed_at or start.started_at + timedelta(seconds=1),
        output_summary=(
            {"result": "summary"} if output_summary is None else output_summary
        ),
        output_full={"permitted": "output-full"} if output_full is None else output_full,
        technical_metadata={"duration_ms": 1000},
    )


def raw_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def database_bytes(path: Path) -> bytes:
    content = bytearray()
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            content.extend(candidate.read_bytes())
    return bytes(content)


def test_key_is_exactly_32_bytes_and_env_hex_is_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    assert parse_trace_key_hex(KEY.hex()) == KEY
    monkeypatch.setenv("V2_OPS_TRACE_KEY_HEX", KEY.hex())
    assert parse_trace_key_hex() == KEY

    for invalid in (b"x" * 32, "", "00" * 31, "00" * 33, "gg" * 32):
        with pytest.raises((TypeError, ValueError), match="32-byte|64 hexadecimal"):
            parse_trace_key_hex(invalid)  # type: ignore[arg-type]
    for invalid_key in ("x" * 32, b"x" * 31, b"x" * 33):
        with pytest.raises((TypeError, ValueError), match="32-byte"):
            AES256GCMCipher(invalid_key)  # type: ignore[arg-type]


def test_writer_bootstraps_deterministic_strict_schema_and_reader_never_bootstraps(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "ops-trace.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY):
        pass
    with SQLiteOpsTraceWriter(path, KEY):
        pass

    with raw_connect(path) as connection:
        metadata = connection.execute(
            "SELECT schema_version, schema_fingerprint FROM ops_schema"
        ).fetchone()
        objects = connection.execute(
            "SELECT name, sql FROM sqlite_schema "
            "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    assert tuple(metadata) == (SCHEMA_VERSION, SCHEMA_FINGERPRINT)
    assert {row["name"] for row in objects} == {
        "ops_schema",
        "executions",
        "nodes",
        "idx_executions_recent",
        "idx_executions_lead_recent",
        "idx_executions_status_recent",
        "idx_nodes_execution_order",
    }
    assert all(
        " STRICT" in row["sql"]
        for row in objects
        if row["name"] in {"ops_schema", "executions", "nodes"}
    )
    assert journal_mode == "wal"
    # PRAGMA foreign_keys is connection-local; the direct inspection connection is off.
    assert foreign_keys == 0

    missing = (tmp_path / "missing.sqlite3").resolve()
    with pytest.raises(OpsTraceStoreError, match="unavailable"):
        SQLiteOpsTraceReader(missing, KEY)
    assert not missing.exists()

    empty = (tmp_path / "empty.sqlite3").resolve()
    empty.touch()
    with pytest.raises(OpsTraceStoreError, match="unavailable"):
        SQLiteOpsTraceReader(empty, KEY)
    assert empty.stat().st_size == 0


def test_one_execution_per_event_exact_lead_grouping_and_idempotent_replay(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    first = execution("event-001")
    sibling = execution(
        "event-002", received_at=UTC_NOW + timedelta(microseconds=1)
    )
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(first)
        writer.write_execution(first)
        writer.write_execution(sibling)
        with pytest.raises(OpsTraceConflictError, match="identity"):
            writer.write_execution(execution("event-001", lead_id="lead:other"))

    with SQLiteOpsTraceReader(path, KEY) as reader:
        page = reader.list_executions(lead_id="lead:opaque-001", limit=10)
    assert [item.execution_id for item in page.executions] == [
        "event-002",
        "event-001",
    ]
    assert {item.lead_id for item in page.executions} == {"lead:opaque-001"}


def test_execution_transitions_are_monotonic_and_terminal_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    pending = execution()
    running = execution(status=ExecutionStatus.RUNNING)
    terminal = execution(
        status=ExecutionStatus.COMPLETED,
        completed_at=UTC_NOW + timedelta(seconds=3),
        terminal_reason="turn_committed",
    )
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(pending)
        writer.write_execution(running)
        writer.write_execution(terminal)
        writer.write_execution(terminal)
        for regression in (pending, running):
            with pytest.raises(OpsTraceConflictError, match="terminal|transition"):
                writer.write_execution(regression)
        with pytest.raises(OpsTraceConflictError, match="terminal|identity"):
            writer.write_execution(
                execution(
                    status=ExecutionStatus.FAILED,
                    completed_at=UTC_NOW + timedelta(seconds=4),
                    terminal_reason="different",
                )
            )

    with SQLiteOpsTraceReader(path, KEY) as reader:
        stored = reader.get_execution("event-001")
    assert stored.status == ExecutionStatus.COMPLETED.value
    assert stored.terminal_reason == "turn_committed"


def test_node_start_finish_round_trip_is_deterministic_and_deep_payload_canonical(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    start = node_start(
        input_summary={"z": 1, "nested": {"values": [True, None, "ok"]}},
        input_full={"z": 2, "nested": {"values": ["á", 3]}},
    )
    finish = node_finish(
        start,
        output_summary={"z": "done", "count": 1},
        output_full={"nested": {"offers": [1, 2]}, "z": "done"},
    )
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(execution())
        writer.start_node(start)
        writer.start_node(start)
        writer.finish_node(finish)
        writer.finish_node(finish)

    with SQLiteOpsTraceReader(path, KEY) as reader:
        detail = reader.get_execution("event-001")
        nodes = reader.list_nodes("event-001", limit=10)
        input_full = reader.read_full("event-001", start.node_id, side="input")
        output_full = reader.read_full("event-001", start.node_id, side="output")
    assert detail.current_node_id == start.node_id
    assert detail.status == ExecutionStatus.RUNNING.value
    assert len(nodes) == 1
    assert nodes[0].node_id == start.node_id
    assert nodes[0].status == ExecutionStatus.COMPLETED.value
    assert canonical_json_bytes(nodes[0].input_summary) == canonical_json_bytes(
        start.input_summary
    )
    assert canonical_json_bytes(nodes[0].output_summary) == canonical_json_bytes(
        finish.output_summary
    )
    assert canonical_json_bytes(input_full.value) == canonical_json_bytes(start.input_full)
    assert canonical_json_bytes(output_full.value) == canonical_json_bytes(
        finish.output_full
    )
    with pytest.raises(FrozenInstanceError):
        detail.status = "failed"  # type: ignore[misc]


def test_divergent_node_identity_and_nonmonotonic_ordinal_are_rejected(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    first = node_start()
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(execution())
        writer.start_node(first)
        with pytest.raises(OpsTraceConflictError, match="identity"):
            writer.start_node(
                node_start(input_full={"permitted": "different-full"})
            )
        with pytest.raises(OpsTraceConflictError, match="ordinal"):
            writer.start_node(
                node_start(
                    ordinal=1,
                    node_type=NodeType.MAYA_RESPONSE,
                    started_at=UTC_NOW + timedelta(seconds=2),
                )
            )


def test_stale_finish_cannot_overwrite_terminal_node(tmp_path: Path) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    start = node_start()
    accepted = node_finish(start, output_full={"winner": "first"})
    stale = node_finish(
        start,
        completed_at=accepted.completed_at - timedelta(microseconds=1),
        status=ExecutionStatus.FAILED,
        output_full={"winner": "stale"},
    )
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(execution())
        writer.start_node(start)
        writer.finish_node(accepted)
        with pytest.raises(OpsTraceConflictError, match="terminal"):
            writer.finish_node(stale)

    with SQLiteOpsTraceReader(path, KEY) as reader:
        stored = reader.list_nodes("event-001", limit=10)[0]
        full = reader.read_full("event-001", start.node_id, side="output")
    assert stored.status == ExecutionStatus.COMPLETED.value
    assert full.value == {"winner": "first"}


def test_running_stale_is_derived_without_mutating_stored_status(tmp_path: Path) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    start = node_start()
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(execution())
        writer.start_node(start)

    observed_at = start.started_at + timedelta(minutes=10)
    with SQLiteOpsTraceReader(path, KEY) as reader:
        detail = reader.get_execution(
            "event-001", now=observed_at, stale_after=timedelta(minutes=5)
        )
        node = reader.list_nodes(
            "event-001",
            limit=10,
            now=observed_at,
            stale_after=timedelta(minutes=5),
        )[0]
        stale_page = reader.list_executions(
            status="running_stale",
            limit=10,
            now=observed_at,
            stale_after=timedelta(minutes=5),
        )
    assert detail.status == "running_stale"
    assert node.status == "running_stale"
    assert [item.execution_id for item in stale_page.executions] == ["event-001"]
    with raw_connect(path) as connection:
        execution_status = connection.execute(
            "SELECT status FROM executions WHERE execution_id = 'event-001'"
        ).fetchone()[0]
        node_status = connection.execute(
            "SELECT status FROM nodes WHERE node_id = ?", (start.node_id,)
        ).fetchone()[0]
    assert (execution_status, node_status) == ("running", "running")


def test_running_stale_filter_does_not_stop_at_newer_nonstale_rows(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        rows = (
            ("event-old", UTC_NOW),
            ("event-new-a", UTC_NOW + timedelta(minutes=8)),
            ("event-new-b", UTC_NOW + timedelta(minutes=9)),
            ("event-new-c", UTC_NOW + timedelta(minutes=10)),
        )
        for event_id, received_at in rows:
            writer.write_execution(execution(event_id, received_at=received_at))
            writer.start_node(
                node_start(
                    event_id,
                    started_at=received_at + timedelta(seconds=1),
                )
            )

    with SQLiteOpsTraceReader(path, KEY) as reader:
        page = reader.list_executions(
            status="running_stale",
            limit=1,
            now=UTC_NOW + timedelta(minutes=11),
            stale_after=timedelta(minutes=5),
        )
        running = reader.list_executions(
            status=ExecutionStatus.RUNNING,
            limit=10,
            now=UTC_NOW + timedelta(minutes=11),
            stale_after=timedelta(minutes=5),
        )
    assert [item.execution_id for item in page.executions] == ["event-old"]
    assert [item.execution_id for item in running.executions] == [
        "event-new-c",
        "event-new-b",
        "event-new-a",
    ]


def test_current_node_must_exist_and_terminal_execution_requires_terminal_node(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    start = node_start()
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        with pytest.raises(OpsTraceConflictError, match="current node"):
            writer.write_execution(
                execution(
                    status=ExecutionStatus.RUNNING,
                    current_node_id="0" * 64,
                )
            )
        writer.write_execution(execution())
        with pytest.raises(OpsTraceConflictError, match="current node"):
            writer.write_execution(
                execution(
                    status=ExecutionStatus.RUNNING,
                    current_node_id="0" * 64,
                )
            )
        writer.start_node(start)
        with pytest.raises(OpsTraceConflictError, match="running node"):
            writer.write_execution(
                execution(
                    status=ExecutionStatus.COMPLETED,
                    current_node_id=None,
                    completed_at=UTC_NOW + timedelta(seconds=3),
                )
            )
        with pytest.raises(OpsTraceConflictError, match="running node"):
            writer.write_execution(
                execution(
                    status=ExecutionStatus.COMPLETED,
                    current_node_id=start.node_id,
                    completed_at=UTC_NOW + timedelta(seconds=3),
                )
            )
        writer.finish_node(node_finish(start))
        writer.write_execution(
            execution(
                status=ExecutionStatus.COMPLETED,
                current_node_id=start.node_id,
                completed_at=UTC_NOW + timedelta(seconds=3),
            )
        )


def test_full_input_output_are_aes_gcm_encrypted_with_distinct_nonce_and_bound_aad(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    protected_input = "PROTECTED-INPUT-PLAINTEXT-5f31"
    protected_output = "PROTECTED-OUTPUT-PLAINTEXT-a902"
    start = node_start(input_full={"protected_value": protected_input})
    finish = node_finish(start, output_full={"protected_value": protected_output})
    writer = SQLiteOpsTraceWriter(path, KEY)
    try:
        writer.write_execution(execution())
        writer.start_node(start)
        writer.finish_node(finish)

        with raw_connect(path) as connection:
            row = connection.execute(
                "SELECT input_full_nonce, input_full_ciphertext, "
                "output_full_nonce, output_full_ciphertext "
                "FROM nodes WHERE node_id = ?",
                (start.node_id,),
            ).fetchone()
        assert len(row["input_full_nonce"]) == 12
        assert len(row["output_full_nonce"]) == 12
        assert row["input_full_nonce"] != row["output_full_nonce"]
        assert protected_input.encode() not in row["input_full_ciphertext"]
        assert protected_output.encode() not in row["output_full_ciphertext"]

        all_files = database_bytes(path)
        assert protected_input.encode() not in all_files
        assert protected_output.encode() not in all_files
        assert Path(f"{path}-wal").exists()
        assert Path(f"{path}-shm").exists()
    finally:
        writer.close()

    with raw_connect(path) as connection:
        connection.execute(
            "UPDATE nodes SET "
            "input_full_nonce = output_full_nonce, "
            "input_full_ciphertext = output_full_ciphertext, "
            "output_full_nonce = input_full_nonce, "
            "output_full_ciphertext = input_full_ciphertext "
            "WHERE node_id = ?",
            (start.node_id,),
        )
        connection.commit()
    with SQLiteOpsTraceReader(path, KEY) as reader:
        with pytest.raises(OpsTraceStoreError, match="encrypted content unavailable"):
            reader.read_full("event-001", start.node_id, side="input")
        with pytest.raises(OpsTraceStoreError, match="encrypted content unavailable"):
            reader.read_full("event-001", start.node_id, side="output")


def test_wrong_key_and_tampered_ciphertext_fail_closed_without_crypto_details(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    start = node_start()
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(execution())
        writer.start_node(start)

    with SQLiteOpsTraceReader(path, OTHER_KEY) as reader:
        with pytest.raises(OpsTraceStoreError) as caught:
            reader.read_full("event-001", start.node_id, side="input")
    assert str(caught.value) == "encrypted trace content unavailable"

    with raw_connect(path) as connection:
        ciphertext = bytearray(
            connection.execute(
                "SELECT input_full_ciphertext FROM nodes WHERE node_id = ?",
                (start.node_id,),
            ).fetchone()[0]
        )
        ciphertext[-1] ^= 1
        connection.execute(
            "UPDATE nodes SET input_full_ciphertext = ? WHERE node_id = ?",
            (bytes(ciphertext), start.node_id),
        )
        connection.commit()
    with SQLiteOpsTraceReader(path, KEY) as reader:
        with pytest.raises(OpsTraceStoreError) as caught:
            reader.read_full("event-001", start.node_id, side="input")
    assert str(caught.value) == "encrypted trace content unavailable"


def test_public_url_round_trips_but_forbidden_signed_url_never_reaches_database(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    public_url = "https://provider.invalid/search?q=docs%26token%3Dpublic-word"
    marker = "NEVER-STORED-SIGNATURE-4471"
    start = node_start(input_full={"public_url": public_url})
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(execution())
        writer.start_node(start)
        with pytest.raises(ValueError, match="signed URL|credential"):
            forbidden = node_start(
                ordinal=2,
                node_type=NodeType.PROVIDER_READ_REQUEST,
                input_full={
                    "url": f"https://provider.invalid/a?X-Amz-Signature={marker}"
                },
            )
            writer.start_node(forbidden)

    with SQLiteOpsTraceReader(path, KEY) as reader:
        assert reader.read_full("event-001", start.node_id, side="input").value == {
            "public_url": public_url
        }
    assert marker.encode() not in database_bytes(path)


def test_recent_exact_lead_status_filter_and_stable_cursor_pagination(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    same_time = UTC_NOW
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        for event_id, lead_id, status in (
            ("event-d", "lead:a", ExecutionStatus.PENDING),
            ("event-c", "lead:a", ExecutionStatus.RUNNING),
            ("event-b", "lead:b", ExecutionStatus.PENDING),
            ("event-a", "lead:a", ExecutionStatus.PENDING),
        ):
            writer.write_execution(
                execution(
                    event_id,
                    lead_id=lead_id,
                    received_at=same_time,
                    status=status,
                )
            )

    with SQLiteOpsTraceReader(path, KEY) as reader:
        first = reader.list_executions(limit=2)
        second = reader.list_executions(limit=2, cursor=first.next_cursor)
        lead = reader.list_executions(lead_id="lead:a", limit=10)
        pending = reader.list_executions(status=ExecutionStatus.PENDING, limit=10)
    assert [item.execution_id for item in first.executions] == ["event-d", "event-c"]
    assert [item.execution_id for item in second.executions] == ["event-b", "event-a"]
    assert second.next_cursor is None
    assert [item.execution_id for item in first.executions + second.executions] == [
        "event-d",
        "event-c",
        "event-b",
        "event-a",
    ]
    assert [item.execution_id for item in lead.executions] == [
        "event-d",
        "event-c",
        "event-a",
    ]
    assert [item.execution_id for item in pending.executions] == [
        "event-d",
        "event-b",
        "event-a",
    ]


def test_query_limits_and_inputs_are_rejected_before_sql_work(tmp_path: Path) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(execution())
    reader = SQLiteOpsTraceReader(path, KEY)
    try:
        with patch.object(
            SQLiteOpsTraceReader,
            "_connect",
            side_effect=AssertionError("SQL connection must not be opened"),
        ):
            for invalid_limit in (0, -1, MAX_PAGE_LIMIT + 1, True):
                with pytest.raises((TypeError, ValueError), match="limit"):
                    reader.list_executions(limit=invalid_limit)  # type: ignore[arg-type]
            for invalid_limit in (0, MAX_NODE_QUERY_LIMIT + 1, True):
                with pytest.raises((TypeError, ValueError), match="limit"):
                    reader.list_nodes(
                        "event-001", limit=invalid_limit  # type: ignore[arg-type]
                    )
            with pytest.raises(ValueError, match="lead_id"):
                reader.list_executions(lead_id="", limit=10)
            malformed_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "execution_id": "event-001",
                        "received_at": "not-a-timestamp",
                        "version": 1,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).rstrip(b"=").decode("ascii")
            with pytest.raises(ValueError, match="cursor"):
                reader.list_executions(limit=10, cursor=malformed_cursor)
            for invalid_status in ("not-a-status", True):
                with pytest.raises((TypeError, ValueError), match="status"):
                    reader.list_executions(status=invalid_status, limit=10)  # type: ignore[arg-type]
            with pytest.raises(ValueError, match="side"):
                reader.read_full("event-001", "0" * 64, side="both")  # type: ignore[arg-type]
    finally:
        reader.close()


def test_reader_uses_read_only_uri_query_only_and_rejects_mutation_matrix(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY) as writer:
        writer.write_execution(execution())
    reader = SQLiteOpsTraceReader(path, KEY)
    try:
        with reader._connect() as connection:
            assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
            assert connection.execute("PRAGMA busy_timeout").fetchone()[0] > 0
            assert connection.execute("SELECT count(*) FROM executions").fetchone()[0] == 1
            mutations = (
                ("INSERT INTO executions(execution_id) VALUES ('x')", ()),
                ("UPDATE executions SET lead_id = 'x'", ()),
                ("DELETE FROM executions", ()),
                ("CREATE TABLE forbidden(value TEXT)", ()),
                ("DROP TABLE nodes", ()),
                ("ALTER TABLE nodes ADD COLUMN forbidden TEXT", ()),
                (f"ATTACH DATABASE '{tmp_path / 'attached.sqlite3'}' AS attached", ()),
                ("PRAGMA user_version = 2", ()),
                ("PRAGMA journal_mode = DELETE", ()),
                ("PRAGMA optimize", ()),
                ("PRAGMA wal_checkpoint", ()),
            )
            for statement, parameters in mutations:
                with pytest.raises(sqlite3.DatabaseError):
                    connection.execute(statement, parameters)
    finally:
        reader.close()
    assert not (tmp_path / "attached.sqlite3").exists()
    with raw_connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM executions").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_reader_rejects_unexpected_schema_objects(tmp_path: Path) -> None:
    path = (tmp_path / "unexpected.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY):
        pass
    with raw_connect(path) as connection:
        connection.execute("CREATE TABLE unexpected(value TEXT) STRICT")
        connection.commit()
    with pytest.raises(OpsTraceStoreError, match="unavailable"):
        SQLiteOpsTraceReader(path, KEY)


def test_reader_rejects_schema_ddl_drift_with_same_object_names(tmp_path: Path) -> None:
    path = (tmp_path / "drifted.sqlite3").resolve()
    with SQLiteOpsTraceWriter(path, KEY):
        pass
    with raw_connect(path) as connection:
        connection.execute("ALTER TABLE nodes ADD COLUMN unexpected TEXT")
        connection.commit()
    with pytest.raises(OpsTraceStoreError, match="unavailable"):
        SQLiteOpsTraceReader(path, KEY)


def test_reader_rejects_relative_corrupt_and_schema_mismatched_databases(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="absolute"):
        SQLiteOpsTraceReader(Path("relative.sqlite3"), KEY)

    corrupt = (tmp_path / "corrupt.sqlite3").resolve()
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises(OpsTraceStoreError) as caught:
        SQLiteOpsTraceReader(corrupt, KEY)
    assert str(caught.value) == "operational trace store unavailable"

    mismatched = (tmp_path / "mismatched.sqlite3").resolve()
    with SQLiteOpsTraceWriter(mismatched, KEY):
        pass
    with raw_connect(mismatched) as connection:
        connection.execute(
            "UPDATE ops_schema SET schema_fingerprint = ? WHERE singleton = 1",
            ("0" * 64,),
        )
        connection.commit()
    with pytest.raises(OpsTraceStoreError) as caught:
        SQLiteOpsTraceReader(mismatched, KEY)
    assert str(caught.value) == "operational trace store unavailable"


def test_env_constructor_and_null_full_side_are_honest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    monkeypatch.setenv("V2_OPS_TRACE_KEY_HEX", KEY.hex())
    start = node_start(input_full=None)
    # The helper default is a payload, so construct an explicit no-full start.
    start = OpsNodeStart(
        execution_id="event-001",
        node_type=NodeType.MAYA_REQUEST,
        ordinal=1,
        started_at=UTC_NOW + timedelta(seconds=1),
        input_summary={"kind": "summary"},
        input_full=None,
    )
    with SQLiteOpsTraceWriter.from_env(path) as writer:
        writer.write_execution(execution())
        writer.start_node(start)
    with SQLiteOpsTraceReader.from_env(path) as reader:
        assert reader.read_full("event-001", start.node_id, side="input") is None
        assert reader.read_full("event-001", start.node_id, side="output") is None


def test_distinct_writer_contention_is_bounded_and_does_not_duplicate_nodes(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "trace.sqlite3").resolve()
    item = execution()
    start = node_start()
    with SQLiteOpsTraceWriter(path, KEY, busy_timeout_ms=2000):
        pass

    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def contend() -> None:
        try:
            with SQLiteOpsTraceWriter(path, KEY, busy_timeout_ms=2000) as writer:
                barrier.wait(timeout=2)
                writer.write_execution(item)
                writer.start_node(start)
        except BaseException as exc:  # pragma: no cover - assertion reports the object
            failures.append(exc)

    threads = [threading.Thread(target=contend) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert failures == []

    with raw_connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM executions").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM nodes").fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(DISTINCT node_id) FROM nodes"
        ).fetchone()[0] == 1
    with SQLiteOpsTraceReader(path, KEY) as reader:
        assert len(reader.list_nodes("event-001", limit=10)) == 1
