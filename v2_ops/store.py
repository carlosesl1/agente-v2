from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from v2_ops.contracts import (
    ExecutionStatus,
    FrozenJSONValue,
    JSONValue,
    NodeType,
    OpsExecution,
    OpsNodeFinish,
    OpsNodeStart,
    TraceCompleteness,
    canonical_json_bytes,
    format_utc_timestamp,
    validate_closed_json,
)
from v2_ops.crypto import AES256GCMCipher, TraceCryptoError, parse_trace_key_hex


SCHEMA_VERSION = 1
MAX_PAGE_LIMIT = 100
MAX_NODE_QUERY_LIMIT = 512
_DEFAULT_WRITER_BUSY_TIMEOUT_MS = 1_000
_DEFAULT_READER_BUSY_TIMEOUT_MS = 250
_ID_RE = re.compile(r"^[^\x00]{1,256}$")
_NODE_ID_RE = re.compile(r"^[0-9a-f]{64}$")


_SCHEMA_SQL = """
CREATE TABLE ops_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    schema_fingerprint TEXT NOT NULL CHECK (
        length(schema_fingerprint) = 64
        AND schema_fingerprint = lower(schema_fingerprint)
        AND schema_fingerprint NOT GLOB '*[^0-9a-f]*'
    )
) STRICT;

CREATE TABLE executions (
    execution_id TEXT PRIMARY KEY CHECK (length(execution_id) BETWEEN 1 AND 256),
    lead_id TEXT NOT NULL CHECK (length(lead_id) BETWEEN 1 AND 256),
    received_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('pending','running','completed','failed','manual_review')
    ),
    trace_completeness TEXT NOT NULL CHECK (
        trace_completeness IN ('complete_trace','partial_trace','ledger_only')
    ),
    current_node_id TEXT CHECK (
        current_node_id IS NULL OR (
            length(current_node_id) = 64
            AND current_node_id = lower(current_node_id)
            AND current_node_id NOT GLOB '*[^0-9a-f]*'
        )
    ),
    terminal_reason TEXT CHECK (
        terminal_reason IS NULL OR length(terminal_reason) BETWEEN 1 AND 256
    ),
    CHECK (
        (status IN ('completed','failed','manual_review')) = (completed_at IS NOT NULL)
    )
) STRICT;

CREATE TABLE nodes (
    node_id TEXT PRIMARY KEY CHECK (
        length(node_id) = 64
        AND node_id = lower(node_id)
        AND node_id NOT GLOB '*[^0-9a-f]*'
    ),
    execution_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    parent_node_id TEXT CHECK (
        parent_node_id IS NULL OR (
            length(parent_node_id) = 64
            AND parent_node_id = lower(parent_node_id)
            AND parent_node_id NOT GLOB '*[^0-9a-f]*'
        )
    ),
    status TEXT NOT NULL CHECK (
        status IN ('running','completed','failed','manual_review')
    ),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    input_summary_json TEXT NOT NULL CHECK (json_valid(input_summary_json)),
    output_summary_json TEXT CHECK (
        output_summary_json IS NULL OR json_valid(output_summary_json)
    ),
    input_full_nonce BLOB,
    input_full_ciphertext BLOB,
    output_full_nonce BLOB,
    output_full_ciphertext BLOB,
    error_json TEXT CHECK (error_json IS NULL OR json_valid(error_json)),
    technical_metadata_json TEXT NOT NULL CHECK (json_valid(technical_metadata_json)),
    FOREIGN KEY (execution_id) REFERENCES executions(execution_id) ON DELETE RESTRICT,
    FOREIGN KEY (parent_node_id) REFERENCES nodes(node_id) ON DELETE RESTRICT,
    UNIQUE (execution_id, ordinal, attempt),
    CHECK ((status = 'running') = (completed_at IS NULL)),
    CHECK ((input_full_nonce IS NULL) = (input_full_ciphertext IS NULL)),
    CHECK (input_full_nonce IS NULL OR length(input_full_nonce) = 12),
    CHECK ((output_full_nonce IS NULL) = (output_full_ciphertext IS NULL)),
    CHECK (output_full_nonce IS NULL OR length(output_full_nonce) = 12)
) STRICT;

CREATE INDEX idx_executions_recent
    ON executions(received_at DESC, execution_id DESC);
CREATE INDEX idx_executions_lead_recent
    ON executions(lead_id, received_at DESC, execution_id DESC);
CREATE INDEX idx_executions_status_recent
    ON executions(status, received_at DESC, execution_id DESC);
CREATE INDEX idx_nodes_execution_order
    ON nodes(execution_id, ordinal, attempt, node_id);
""".strip()
SCHEMA_FINGERPRINT = hashlib.sha256(
    b"v2-ops-trace-schema-v1\0" + _SCHEMA_SQL.encode("utf-8")
).hexdigest()
_EXPECTED_SCHEMA_OBJECTS = {
    "ops_schema",
    "executions",
    "nodes",
    "idx_executions_recent",
    "idx_executions_lead_recent",
    "idx_executions_status_recent",
    "idx_nodes_execution_order",
}


def _live_schema_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,sql FROM sqlite_schema "
        "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type,name"
    ).fetchall()
    material = json.dumps(
        [tuple(row) for row in rows],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"v2-ops-live-schema-v1\0" + material).hexdigest()


def _expected_live_schema_fingerprint() -> str:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_SCHEMA_SQL)
        return _live_schema_fingerprint(connection)
    finally:
        connection.close()


_EXPECTED_LIVE_SCHEMA_FINGERPRINT = _expected_live_schema_fingerprint()


class OpsTraceStoreError(RuntimeError):
    """Sanitized operational trace persistence failure."""


class OpsTraceConflictError(OpsTraceStoreError):
    """A durable identity or monotonic transition conflicts."""


@dataclass(frozen=True, slots=True)
class OpsExecutionView:
    execution_id: str
    lead_id: str
    received_at: datetime
    completed_at: datetime | None
    status: str
    stored_status: ExecutionStatus
    trace_completeness: TraceCompleteness
    current_node_id: str | None
    terminal_reason: str | None


@dataclass(frozen=True, slots=True)
class OpsNodeView:
    node_id: str
    execution_id: str
    node_type: NodeType
    ordinal: int
    attempt: int
    parent_node_id: str | None
    status: str
    stored_status: ExecutionStatus
    started_at: datetime
    completed_at: datetime | None
    input_summary: JSONValue
    output_summary: JSONValue | None
    has_full_input: bool
    has_full_output: bool
    error: JSONValue | None
    technical_metadata: JSONValue


@dataclass(frozen=True, slots=True)
class OpsExecutionPage:
    executions: tuple[OpsExecutionView, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class OpsFullValue:
    value: JSONValue


def _require_path(path: Path, *, existing: bool) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("operational trace path must be an absolute pathlib.Path")
    if existing and (not path.exists() or not path.is_file()):
        raise OpsTraceStoreError("operational trace store unavailable")
    if not existing and not path.parent.is_dir():
        raise OpsTraceStoreError("operational trace store unavailable")
    return path


def _require_busy_timeout(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 30_000:
        raise ValueError("busy_timeout_ms must be an exact integer from 1 to 30000")
    return value


def _require_id(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be bounded non-empty NUL-free text")
    if len(value.encode("utf-8")) > 256:
        raise ValueError(f"{name} exceeds its 256-byte limit")
    return value


def _require_node_id(value: object) -> str:
    if type(value) is not str or _NODE_ID_RE.fullmatch(value) is None:
        raise ValueError("node_id must be a lowercase SHA-256")
    return value


def _require_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value


def _parse_utc(value: object, name: str) -> datetime:
    if type(value) is not str or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value
    ) is None:
        raise OpsTraceStoreError("operational trace store unavailable")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise OpsTraceStoreError("operational trace store unavailable") from exc
    return parsed


def _json_text(value: JSONValue | FrozenJSONValue) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _json_value(value: object, *, nullable: bool = False) -> JSONValue | None:
    if value is None and nullable:
        return None
    if type(value) is not str:
        raise OpsTraceStoreError("operational trace store unavailable")
    try:
        decoded = json.loads(value)
        validate_closed_json(decoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OpsTraceStoreError("operational trace store unavailable") from exc
    return decoded


def _connection_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/:')}?mode=ro&immutable=0"


def _execution_tuple(item: OpsExecution) -> tuple[object, ...]:
    return (
        item.execution_id,
        item.lead_id,
        format_utc_timestamp(item.received_at),
        None if item.completed_at is None else format_utc_timestamp(item.completed_at),
        item.status.value,
        item.trace_completeness.value,
        item.current_node_id,
        item.terminal_reason,
    )


def _cursor_encode(received_at: str, execution_id: str) -> str:
    payload = json.dumps(
        {"execution_id": execution_id, "received_at": received_at, "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _cursor_decode(value: object) -> tuple[str, str]:
    if type(value) is not str or not value or len(value) > 1024:
        raise ValueError("cursor must be bounded canonical text")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(value + padding)
        data = json.loads(raw.decode("utf-8"))
        if (
            type(data) is not dict
            or set(data) != {"execution_id", "received_at", "version"}
            or data["version"] != 1
        ):
            raise ValueError
        execution_id = _require_id(data["execution_id"], "cursor execution_id")
        received_at = format_utc_timestamp(
            _parse_utc(data["received_at"], "cursor received_at")
        )
        if _cursor_encode(received_at, execution_id) != value:
            raise ValueError
        return received_at, execution_id
    except (
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        OpsTraceStoreError,
    ) as exc:
        raise ValueError("cursor is invalid") from exc


class SQLiteOpsTraceWriter:
    def __init__(
        self,
        path: Path,
        key: bytes,
        *,
        busy_timeout_ms: int = _DEFAULT_WRITER_BUSY_TIMEOUT_MS,
    ) -> None:
        self._path = _require_path(path, existing=False)
        self._busy_timeout_ms = _require_busy_timeout(busy_timeout_ms)
        self._cipher = AES256GCMCipher(key)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._path,
                isolation_level=None,
                timeout=self._busy_timeout_ms / 1000,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            self._reject_foreign_schema(connection)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            self._connection = connection
            self._bootstrap_or_verify()
        except (sqlite3.Error, OSError, OpsTraceStoreError):
            self.close()
            raise OpsTraceStoreError("operational trace store unavailable") from None

    @classmethod
    def from_env(
        cls,
        path: Path,
        *,
        busy_timeout_ms: int = _DEFAULT_WRITER_BUSY_TIMEOUT_MS,
    ) -> "SQLiteOpsTraceWriter":
        return cls(path, parse_trace_key_hex(), busy_timeout_ms=busy_timeout_ms)

    def __enter__(self) -> "SQLiteOpsTraceWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            connection, self._connection = self._connection, None
            if connection is not None:
                connection.close()

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise OpsTraceStoreError("operational trace store unavailable")
        return self._connection

    @staticmethod
    def _reject_foreign_schema(connection: sqlite3.Connection) -> None:
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'"
            )
        }
        if objects and "ops_schema" not in objects:
            raise OpsTraceStoreError("operational trace store unavailable")

    def _bootstrap_or_verify(self) -> None:
        connection = self._db()
        exists = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='ops_schema'"
        ).fetchone()
        if exists is None:
            bootstrap = (
                "BEGIN IMMEDIATE;\n"
                + _SCHEMA_SQL
                + "\nINSERT INTO ops_schema(singleton,schema_version,schema_fingerprint) "
                + f"VALUES(1,{SCHEMA_VERSION},'{SCHEMA_FINGERPRINT}');\n"
                + "COMMIT;"
            )
            try:
                connection.executescript(bootstrap)
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        self._verify_schema(connection)

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT schema_version,schema_fingerprint FROM ops_schema WHERE singleton=1"
        ).fetchone()
        if row is None or tuple(row) != (SCHEMA_VERSION, SCHEMA_FINGERPRINT):
            raise OpsTraceStoreError("operational trace store unavailable")
        objects = {
            item[0]
            for item in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'"
            )
        }
        if objects != _EXPECTED_SCHEMA_OBJECTS:
            raise OpsTraceStoreError("operational trace store unavailable")
        # The metadata binds the source DDL while this digest detects in-place
        # ALTER/rewrites that preserve object names.
        if _live_schema_fingerprint(connection) != _EXPECTED_LIVE_SCHEMA_FINGERPRINT:
            raise OpsTraceStoreError("operational trace store unavailable")

    def _transaction(self, operation) -> None:
        with self._lock:
            connection = self._db()
            try:
                connection.execute("BEGIN IMMEDIATE")
                operation(connection)
                connection.execute("COMMIT")
            except OpsTraceConflictError:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            except (sqlite3.Error, OSError, TraceCryptoError) as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise OpsTraceStoreError("operational trace store unavailable") from exc
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def write_execution(self, item: OpsExecution) -> None:
        if type(item) is not OpsExecution:
            raise TypeError("item must be an exact OpsExecution")
        values = _execution_tuple(item)

        def apply(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT execution_id,lead_id,received_at,completed_at,status,"
                "trace_completeness,current_node_id,terminal_reason "
                "FROM executions WHERE execution_id=?",
                (item.execution_id,),
            ).fetchone()
            if row is None:
                if item.current_node_id is not None:
                    raise OpsTraceConflictError("execution current node identity conflict")
                if item.status in {
                    ExecutionStatus.COMPLETED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.MANUAL_REVIEW,
                }:
                    raise OpsTraceConflictError(
                        "terminal execution requires an existing terminal node"
                    )
                connection.execute(
                    "INSERT INTO executions(execution_id,lead_id,received_at,completed_at,"
                    "status,trace_completeness,current_node_id,terminal_reason) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    values,
                )
                return
            current = tuple(row)
            if current == values:
                return
            if current[1] != values[1] or current[2] != values[2] or current[5] != values[5]:
                raise OpsTraceConflictError("execution identity conflict")
            old_status = ExecutionStatus(current[4])
            if old_status in {
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.MANUAL_REVIEW,
            }:
                raise OpsTraceConflictError("terminal execution conflict")
            if (
                old_status is ExecutionStatus.RUNNING
                and item.status is ExecutionStatus.PENDING
                and current[6] is not None
                and item.current_node_id is None
                and item.completed_at is None
                and item.terminal_reason is None
            ):
                # A concurrent recorder may replay the immutable initial execution
                # after another writer has already started its first node.
                return
            allowed = {
                ExecutionStatus.PENDING: {
                    ExecutionStatus.PENDING,
                    ExecutionStatus.RUNNING,
                    ExecutionStatus.COMPLETED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.MANUAL_REVIEW,
                },
                ExecutionStatus.RUNNING: {
                    ExecutionStatus.RUNNING,
                    ExecutionStatus.COMPLETED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.MANUAL_REVIEW,
                },
            }
            if item.status not in allowed[old_status]:
                raise OpsTraceConflictError("execution transition conflict")
            if item.current_node_id is not None:
                node = connection.execute(
                    "SELECT execution_id,status FROM nodes WHERE node_id=?",
                    (item.current_node_id,),
                ).fetchone()
                if node is None or node[0] != item.execution_id:
                    raise OpsTraceConflictError("execution current node identity conflict")
                if item.completed_at is not None and node[1] == "running":
                    raise OpsTraceConflictError(
                        "terminal execution cannot reference a running node"
                    )
                latest = connection.execute(
                    "SELECT node_id FROM nodes WHERE execution_id=? "
                    "ORDER BY ordinal DESC,attempt DESC LIMIT 1",
                    (item.execution_id,),
                ).fetchone()
                if latest is None or latest[0] != item.current_node_id:
                    raise OpsTraceConflictError("execution current node regression")
            elif current[6] is not None:
                raise OpsTraceConflictError("execution current node regression")
            if item.completed_at is not None:
                running_node = connection.execute(
                    "SELECT 1 FROM nodes WHERE execution_id=? AND status='running' LIMIT 1",
                    (item.execution_id,),
                ).fetchone()
                if running_node is not None:
                    raise OpsTraceConflictError(
                        "terminal execution cannot coexist with a running node"
                    )
            connection.execute(
                "UPDATE executions SET completed_at=?,status=?,current_node_id=?,"
                "terminal_reason=? WHERE execution_id=?",
                (values[3], values[4], values[6], values[7], values[0]),
            )

        self._transaction(apply)

    def start_node(self, item: OpsNodeStart) -> None:
        if type(item) is not OpsNodeStart:
            raise TypeError("item must be an exact OpsNodeStart")
        input_summary = _json_text(item.input_summary)
        metadata = _json_text(item.technical_metadata)
        encrypted = (
            None
            if item.input_full is None
            else self._cipher.encrypt(
                canonical_json_bytes(item.input_full),
                execution_id=item.execution_id,
                node_id=item.node_id,
                side="input",
            )
        )

        def apply(connection: sqlite3.Connection) -> None:
            execution = connection.execute(
                "SELECT received_at,status FROM executions WHERE execution_id=?",
                (item.execution_id,),
            ).fetchone()
            if execution is None:
                raise OpsTraceConflictError("execution identity conflict")
            if ExecutionStatus(execution[1]) in {
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.MANUAL_REVIEW,
            }:
                raise OpsTraceConflictError("terminal execution conflict")
            if item.started_at < _parse_utc(execution[0], "received_at"):
                raise OpsTraceConflictError("node time identity conflict")
            if item.parent_node_id is not None:
                parent = connection.execute(
                    "SELECT execution_id,status FROM nodes WHERE node_id=?",
                    (item.parent_node_id,),
                ).fetchone()
                if parent is None or parent[0] != item.execution_id:
                    raise OpsTraceConflictError("parent node identity conflict")
            row = connection.execute(
                "SELECT node_type,ordinal,attempt,parent_node_id,status,started_at,"
                "input_summary_json,input_full_nonce,input_full_ciphertext,"
                "technical_metadata_json FROM nodes WHERE node_id=?",
                (item.node_id,),
            ).fetchone()
            if row is not None:
                same = (
                    row[0] == item.node_type.value
                    and row[1] == item.ordinal
                    and row[2] == item.attempt
                    and row[3] == item.parent_node_id
                    and row[5] == format_utc_timestamp(item.started_at)
                    and row[6] == input_summary
                    and row[9] == metadata
                )
                if row[7] is None:
                    same = same and item.input_full is None
                elif item.input_full is None:
                    same = False
                else:
                    try:
                        existing = self._cipher.decrypt(
                            row[7],
                            row[8],
                            execution_id=item.execution_id,
                            node_id=item.node_id,
                            side="input",
                        )
                        same = same and existing == canonical_json_bytes(item.input_full)
                    except TraceCryptoError:
                        same = False
                if not same:
                    raise OpsTraceConflictError("node identity conflict")
                return
            latest = connection.execute(
                "SELECT ordinal,attempt FROM nodes WHERE execution_id=? "
                "ORDER BY ordinal DESC,attempt DESC LIMIT 1",
                (item.execution_id,),
            ).fetchone()
            if latest is not None and (item.ordinal, item.attempt) <= tuple(latest):
                raise OpsTraceConflictError("node ordinal conflict")
            connection.execute(
                "INSERT INTO nodes(node_id,execution_id,node_type,ordinal,attempt,"
                "parent_node_id,status,started_at,completed_at,input_summary_json,"
                "output_summary_json,input_full_nonce,input_full_ciphertext,"
                "output_full_nonce,output_full_ciphertext,error_json,"
                "technical_metadata_json) VALUES(?,?,?,?,?,?,'running',?,NULL,?,NULL,"
                "?,?,NULL,NULL,NULL,?)",
                (
                    item.node_id,
                    item.execution_id,
                    item.node_type.value,
                    item.ordinal,
                    item.attempt,
                    item.parent_node_id,
                    format_utc_timestamp(item.started_at),
                    input_summary,
                    None if encrypted is None else encrypted.nonce,
                    None if encrypted is None else encrypted.ciphertext,
                    metadata,
                ),
            )
            connection.execute(
                "UPDATE executions SET status='running',current_node_id=? "
                "WHERE execution_id=?",
                (item.node_id, item.execution_id),
            )

        self._transaction(apply)

    def finish_node(self, item: OpsNodeFinish) -> None:
        if type(item) is not OpsNodeFinish:
            raise TypeError("item must be an exact OpsNodeFinish")
        output_summary = _json_text(item.output_summary)
        error = None if item.error is None else _json_text(item.error)
        metadata = _json_text(item.technical_metadata)
        encrypted = (
            None
            if item.output_full is None
            else self._cipher.encrypt(
                canonical_json_bytes(item.output_full),
                execution_id=item.execution_id,
                node_id=item.node_id,
                side="output",
            )
        )

        def apply(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT execution_id,node_type,ordinal,attempt,parent_node_id,status,"
                "started_at,completed_at,output_summary_json,output_full_nonce,"
                "output_full_ciphertext,error_json,technical_metadata_json "
                "FROM nodes WHERE node_id=?",
                (item.node_id,),
            ).fetchone()
            if row is None:
                raise OpsTraceConflictError("node identity conflict")
            identity = (
                row[0] == item.execution_id
                and row[1] == item.node_type.value
                and row[2] == item.ordinal
                and row[3] == item.attempt
                and row[4] == item.parent_node_id
                and row[6] == format_utc_timestamp(item.started_at)
            )
            if not identity:
                raise OpsTraceConflictError("node identity conflict")
            if row[5] != ExecutionStatus.RUNNING.value:
                same = (
                    row[5] == item.status.value
                    and row[7] == format_utc_timestamp(item.completed_at)
                    and row[8] == output_summary
                    and row[11] == error
                    and row[12] == metadata
                )
                if row[9] is None:
                    same = same and item.output_full is None
                elif item.output_full is None:
                    same = False
                else:
                    try:
                        existing = self._cipher.decrypt(
                            row[9],
                            row[10],
                            execution_id=item.execution_id,
                            node_id=item.node_id,
                            side="output",
                        )
                        same = same and existing == canonical_json_bytes(item.output_full)
                    except TraceCryptoError:
                        same = False
                if same:
                    return
                raise OpsTraceConflictError("terminal node conflict")
            connection.execute(
                "UPDATE nodes SET status=?,completed_at=?,output_summary_json=?,"
                "output_full_nonce=?,output_full_ciphertext=?,error_json=?,"
                "technical_metadata_json=? WHERE node_id=? AND status='running'",
                (
                    item.status.value,
                    format_utc_timestamp(item.completed_at),
                    output_summary,
                    None if encrypted is None else encrypted.nonce,
                    None if encrypted is None else encrypted.ciphertext,
                    error,
                    metadata,
                    item.node_id,
                ),
            )

        self._transaction(apply)


class SQLiteOpsTraceReader:
    def __init__(
        self,
        path: Path,
        key: bytes,
        *,
        busy_timeout_ms: int = _DEFAULT_READER_BUSY_TIMEOUT_MS,
    ) -> None:
        self._path = _require_path(path, existing=True)
        self._busy_timeout_ms = _require_busy_timeout(busy_timeout_ms)
        self._cipher = AES256GCMCipher(key)
        self._closed = False
        try:
            with self._connect() as connection:
                SQLiteOpsTraceWriter._verify_schema(connection)
        except (sqlite3.Error, OSError, OpsTraceStoreError):
            raise OpsTraceStoreError("operational trace store unavailable") from None

    @classmethod
    def from_env(
        cls,
        path: Path,
        *,
        busy_timeout_ms: int = _DEFAULT_READER_BUSY_TIMEOUT_MS,
    ) -> "SQLiteOpsTraceReader":
        return cls(path, parse_trace_key_hex(), busy_timeout_ms=busy_timeout_ms)

    def __enter__(self) -> "SQLiteOpsTraceReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True

    @staticmethod
    def _authorizer(
        action: int,
        argument1: str | None,
        argument2: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        denied = {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_CREATE_TEMP_INDEX,
            sqlite3.SQLITE_CREATE_TEMP_TABLE,
            sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
            sqlite3.SQLITE_CREATE_TEMP_VIEW,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_CREATE_VIEW,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_DROP_TEMP_INDEX,
            sqlite3.SQLITE_DROP_TEMP_TABLE,
            sqlite3.SQLITE_DROP_TEMP_TRIGGER,
            sqlite3.SQLITE_DROP_TEMP_VIEW,
            sqlite3.SQLITE_DROP_TRIGGER,
            sqlite3.SQLITE_DROP_VIEW,
            sqlite3.SQLITE_ALTER_TABLE,
            sqlite3.SQLITE_ATTACH,
            sqlite3.SQLITE_DETACH,
            sqlite3.SQLITE_REINDEX,
            sqlite3.SQLITE_ANALYZE,
        }
        if action in denied:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            allowed_read_pragmas = {
                "busy_timeout",
                "foreign_keys",
                "query_only",
                "table_info",
                "index_list",
                "index_info",
            }
            if argument2 is not None or (argument1 or "").casefold() not in allowed_read_pragmas:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise OpsTraceStoreError("operational trace store unavailable")
        connection = sqlite3.connect(
            _connection_uri(self._path),
            uri=True,
            isolation_level=None,
            timeout=self._busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        connection.execute("PRAGMA query_only=ON")
        connection.set_authorizer(self._authorizer)
        return connection

    @staticmethod
    def _stale(
        stored_status: ExecutionStatus,
        started_at: datetime,
        *,
        now: datetime,
        stale_after: timedelta,
    ) -> str:
        if stored_status is ExecutionStatus.RUNNING and now - started_at >= stale_after:
            return "running_stale"
        return stored_status.value

    @staticmethod
    def _time_inputs(
        now: datetime | None, stale_after: timedelta
    ) -> tuple[datetime, timedelta]:
        instant = datetime.now(timezone.utc) if now is None else _require_utc(now, "now")
        if type(stale_after) is not timedelta or stale_after <= timedelta(0):
            raise ValueError("stale_after must be a positive timedelta")
        return instant, stale_after

    def _execution_view(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        now: datetime,
        stale_after: timedelta,
    ) -> OpsExecutionView:
        stored = ExecutionStatus(row[4])
        started = _parse_utc(row[2], "received_at")
        if stored is ExecutionStatus.RUNNING:
            node = connection.execute(
                "SELECT started_at FROM nodes WHERE execution_id=? AND status='running' "
                "ORDER BY ordinal DESC,attempt DESC LIMIT 1",
                (row[0],),
            ).fetchone()
            if node is not None:
                started = _parse_utc(node[0], "node started_at")
        return OpsExecutionView(
            execution_id=row[0],
            lead_id=row[1],
            received_at=_parse_utc(row[2], "received_at"),
            completed_at=None if row[3] is None else _parse_utc(row[3], "completed_at"),
            status=self._stale(stored, started, now=now, stale_after=stale_after),
            stored_status=stored,
            trace_completeness=TraceCompleteness(row[5]),
            current_node_id=row[6],
            terminal_reason=row[7],
        )

    def get_execution(
        self,
        execution_id: str,
        *,
        now: datetime | None = None,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> OpsExecutionView | None:
        _require_id(execution_id, "execution_id")
        instant, threshold = self._time_inputs(now, stale_after)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT execution_id,lead_id,received_at,completed_at,status,"
                    "trace_completeness,current_node_id,terminal_reason "
                    "FROM executions WHERE execution_id=?",
                    (execution_id,),
                ).fetchone()
                return (
                    None
                    if row is None
                    else self._execution_view(
                        connection, row, now=instant, stale_after=threshold
                    )
                )
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise OpsTraceStoreError("operational trace store unavailable") from exc

    def list_executions(
        self,
        *,
        lead_id: str | None = None,
        status: ExecutionStatus | str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        now: datetime | None = None,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> OpsExecutionPage:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_LIMIT:
            raise ValueError("limit is outside the bounded page range")
        if lead_id is not None:
            _require_id(lead_id, "lead_id")
        if status is None:
            status_value = None
        elif type(status) is ExecutionStatus:
            status_value = status.value
        elif status == "running_stale":
            status_value = "running_stale"
        else:
            raise ValueError("status is outside the closed catalog")
        cursor_values = None if cursor is None else _cursor_decode(cursor)
        instant, threshold = self._time_inputs(now, stale_after)
        clauses: list[str] = []
        parameters: list[object] = []
        if lead_id is not None:
            clauses.append("lead_id=?")
            parameters.append(lead_id)
        if status_value not in {None, "running_stale"}:
            clauses.append("status=?")
            parameters.append(status_value)
        elif status_value == "running_stale":
            clauses.append("status='running'")
            stale_cutoff = format_utc_timestamp(instant - threshold)
            clauses.append(
                "COALESCE((SELECT started_at FROM nodes n "
                "WHERE n.execution_id=executions.execution_id AND n.status='running' "
                "ORDER BY ordinal DESC,attempt DESC LIMIT 1), "
                "received_at) <= ?"
            )
            parameters.append(stale_cutoff)
        if status_value == ExecutionStatus.RUNNING.value:
            fresh_cutoff = format_utc_timestamp(instant - threshold)
            clauses.append(
                "COALESCE((SELECT started_at FROM nodes n "
                "WHERE n.execution_id=executions.execution_id AND n.status='running' "
                "ORDER BY ordinal DESC,attempt DESC LIMIT 1), "
                "received_at) > ?"
            )
            parameters.append(fresh_cutoff)
        if cursor_values is not None:
            clauses.append("(received_at < ? OR (received_at = ? AND execution_id < ?))")
            parameters.extend(
                (cursor_values[0], cursor_values[0], cursor_values[1])
            )
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT execution_id,lead_id,received_at,completed_at,status,"
                    "trace_completeness,current_node_id,terminal_reason FROM executions"
                    + where
                    + " ORDER BY received_at DESC,execution_id DESC LIMIT ?",
                    (*parameters, limit + 1),
                ).fetchall()
                views = [
                    self._execution_view(
                        connection, row, now=instant, stale_after=threshold
                    )
                    for row in rows
                ]

                has_more = len(views) > limit
                selected = views[:limit]
                next_cursor = (
                    _cursor_encode(
                        format_utc_timestamp(selected[-1].received_at),
                        selected[-1].execution_id,
                    )
                    if has_more and selected
                    else None
                )
                return OpsExecutionPage(tuple(selected), next_cursor)
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise OpsTraceStoreError("operational trace store unavailable") from exc

    def list_nodes(
        self,
        execution_id: str,
        *,
        limit: int = 256,
        now: datetime | None = None,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> tuple[OpsNodeView, ...]:
        _require_id(execution_id, "execution_id")
        if type(limit) is not int or not 1 <= limit <= MAX_NODE_QUERY_LIMIT:
            raise ValueError("limit is outside the bounded node range")
        instant, threshold = self._time_inputs(now, stale_after)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT node_id,execution_id,node_type,ordinal,attempt,parent_node_id,"
                    "status,started_at,completed_at,input_summary_json,output_summary_json,"
                    "input_full_nonce,output_full_nonce,error_json,technical_metadata_json "
                    "FROM nodes WHERE execution_id=? "
                    "ORDER BY ordinal,attempt,node_id LIMIT ?",
                    (execution_id, limit),
                ).fetchall()
                result = []
                for row in rows:
                    stored = ExecutionStatus(row[6])
                    started = _parse_utc(row[7], "started_at")
                    result.append(
                        OpsNodeView(
                            node_id=row[0],
                            execution_id=row[1],
                            node_type=NodeType(row[2]),
                            ordinal=row[3],
                            attempt=row[4],
                            parent_node_id=row[5],
                            status=self._stale(
                                stored,
                                started,
                                now=instant,
                                stale_after=threshold,
                            ),
                            stored_status=stored,
                            started_at=started,
                            completed_at=(
                                None
                                if row[8] is None
                                else _parse_utc(row[8], "completed_at")
                            ),
                            input_summary=_json_value(row[9]),
                            output_summary=_json_value(row[10], nullable=True),
                            has_full_input=row[11] is not None,
                            has_full_output=row[12] is not None,
                            error=_json_value(row[13], nullable=True),
                            technical_metadata=_json_value(row[14]),
                        )
                    )
                return tuple(result)
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise OpsTraceStoreError("operational trace store unavailable") from exc

    def read_full(
        self,
        execution_id: str,
        node_id: str,
        *,
        side: Literal["input", "output"],
    ) -> OpsFullValue | None:
        _require_id(execution_id, "execution_id")
        _require_node_id(node_id)
        if side not in {"input", "output"}:
            raise ValueError("side must be input or output")
        nonce_column = f"{side}_full_nonce"
        ciphertext_column = f"{side}_full_ciphertext"
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"SELECT {nonce_column},{ciphertext_column} FROM nodes "
                    "WHERE execution_id=? AND node_id=?",
                    (execution_id, node_id),
                ).fetchone()
            if row is None or row[0] is None:
                return None
            try:
                plaintext = self._cipher.decrypt(
                    row[0],
                    row[1],
                    execution_id=execution_id,
                    node_id=node_id,
                    side=side,
                )
                decoded = json.loads(plaintext.decode("utf-8"))
                validate_closed_json(decoded)
                if canonical_json_bytes(decoded) != plaintext:
                    raise ValueError
                return OpsFullValue(decoded)
            except (TraceCryptoError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                opposite_side = "output" if side == "input" else "input"
                try:
                    self._cipher.decrypt(
                        row[0],
                        row[1],
                        execution_id=execution_id,
                        node_id=node_id,
                        side=opposite_side,
                    )
                except TraceCryptoError:
                    raise OpsTraceStoreError(
                        "encrypted trace content unavailable"
                    ) from exc
                raise OpsTraceStoreError("encrypted content unavailable") from exc
        except OpsTraceStoreError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise OpsTraceStoreError("operational trace store unavailable") from exc
