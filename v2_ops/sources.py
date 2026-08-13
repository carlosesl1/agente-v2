from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
import stat

from reservation_boundary.schema import (
    SCHEMA_VERSION_V8,
    expected_sqlite_v8_schema_fingerprint,
    sqlite_v8_schema_fingerprint,
)
from reservation_boundary.sqlite_store import BoundaryStoreError, SQLiteBoundaryStore
from reservation_domain import dumps_command, loads_command
from reservation_execution.schema import (
    PHASE5_V6_TABLES,
    SCHEMA_VERSION_V6,
    schema_hash_v6,
)


_MAX_ROWS = 10_000
_HASH_LENGTH = 64
_PAYMENT_PROJECTION_SCHEMA = """
CREATE TABLE payment_initiations (
  initiation_id TEXT PRIMARY KEY,
  selection_json BLOB NOT NULL,
  selection_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','fenced','completed','manual_review')),
  claim_owner TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TEXT,
  dispatch_slots INTEGER NOT NULL DEFAULT 0 CHECK(dispatch_slots IN (0,1)),
  result_json BLOB,
  result_hash TEXT,
  updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE stripe_step_receipts (
  initiation_id TEXT NOT NULL,
  step TEXT NOT NULL CHECK(step IN ('product','price','payment_link')),
  status TEXT NOT NULL CHECK(status IN ('intent','accepted')),
  journal_owner TEXT NOT NULL,
  journal_fencing_token INTEGER NOT NULL CHECK(journal_fencing_token>=1),
  receipt_json BLOB NOT NULL,
  receipt_hash TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(initiation_id,step),
  FOREIGN KEY(initiation_id) REFERENCES payment_initiations(initiation_id)
) STRICT;
CREATE TABLE stripe_reconciliations (
  initiation_id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK(status IN ('pending','claimed','matched','manual_review')),
  claim_owner TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts IN (0,1)),
  recovery_pending INTEGER NOT NULL DEFAULT 0 CHECK(recovery_pending IN (0,1)),
  updated_at TEXT NOT NULL,
  FOREIGN KEY(initiation_id) REFERENCES payment_initiations(initiation_id)
) STRICT;
"""


class ProjectionSourceError(RuntimeError):
    """Sanitized source authentication or read failure."""


@dataclass(frozen=True, slots=True)
class BoundaryCommandMilestone:
    command_id: str
    command_type: str
    command_hash: str
    created_at: datetime
    source_turn_receipt_hash: str
    payment_id: str | None


@dataclass(frozen=True, slots=True)
class BoundaryPublicMilestone:
    public_row_id: str
    chunk_index: int
    status: str
    dispatch_slots_consumed: int
    delivery_receipt_hash: str | None
    created_at: datetime
    updated_at: datetime
    source_turn_receipt_hash: str


@dataclass(frozen=True, slots=True)
class BoundaryExecutionProjection:
    execution_id: str
    lead_id: str
    aggregate_turn_id: str
    source_event_hash: str
    source_turn_receipt_hash: str
    occurred_at: datetime
    turn_receipt_hash: str
    commands: tuple[BoundaryCommandMilestone, ...]
    public_rows: tuple[BoundaryPublicMilestone, ...]
    degraded_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutionLedgerMilestone:
    command_id: str
    command_hash: str
    status: str
    dispatch_request_hash: str | None
    dispatch_fenced_at: datetime | None
    outcome_hash: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentLedgerMilestone:
    payment_id: str
    initiation_id: str
    selection_hash: str
    status: str
    dispatch_slots: int
    result_hash: str | None
    updated_at: datetime


def _utc(value: object) -> datetime:
    if type(value) is not str:
        raise ProjectionSourceError("projection source unavailable")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProjectionSourceError("projection source unavailable") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectionSourceError("projection source unavailable")
    return parsed.astimezone(timezone.utc)


def _ro_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _require_materialized_wal_source(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise ProjectionSourceError("projection source unavailable") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProjectionSourceError("projection source unavailable")


class SQLiteBoundaryProjectionSource:
    """Bounded authenticated reader for causally proven boundary V8 rows."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 250) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("boundary projection path must be absolute")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 30_000:
            raise ValueError("busy_timeout_ms must be bounded exact int")
        _require_materialized_wal_source(path)
        self._path = path
        self._closed = False
        try:
            store = SQLiteBoundaryStore.open_readonly_v8(path)
            connection = store._connection
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            connection.execute("PRAGMA query_only=ON")
            connection.set_authorizer(self._authorizer)
            self._store = store
            self._connection = connection
            self._verify_schema()
        except (
            OSError,
            sqlite3.Error,
            BoundaryStoreError,
            ProjectionSourceError,
            ValueError,
        ):
            try:
                store.close()
            except UnboundLocalError:
                pass
            raise ProjectionSourceError("projection source unavailable") from None

    def __enter__(self) -> "SQLiteBoundaryProjectionSource":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._store.close()
            self._closed = True

    @property
    def query_only(self) -> bool:
        if self._closed:
            raise ProjectionSourceError("projection source unavailable")
        return self._connection.execute("PRAGMA query_only").fetchone()[0] == 1

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
            pragma = (argument1 or "").casefold()
            allowed = (
                pragma in {"busy_timeout", "query_only"} and argument2 is None
            ) or (
                pragma == "table_info" and argument2 == "boundary_event_sources"
            )
            if not allowed:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _verify_schema(self) -> None:
        if (
            sqlite_v8_schema_fingerprint(self._connection)
            != expected_sqlite_v8_schema_fingerprint()
        ):
            raise ProjectionSourceError("projection source unavailable")
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(boundary_event_sources)")
        }
        if {
            "lead_key",
            "aggregate_turn_id",
            "source_index",
            "source_event_id",
            "source_event_hash",
            "source_turn_receipt_hash",
        } - columns:
            raise ProjectionSourceError("projection source unavailable")
        if SCHEMA_VERSION_V8 != 8:
            raise ProjectionSourceError("projection source unavailable")

    @staticmethod
    def _limit(value: int) -> int:
        if type(value) is not int or not 1 <= value <= _MAX_ROWS:
            raise ValueError("projection limit must be from 1 to 10000")
        return value

    def primary_executions(self, *, limit: int) -> tuple[BoundaryExecutionProjection, ...]:
        if self._closed:
            raise ProjectionSourceError("projection source unavailable")
        bounded = self._limit(limit)
        try:
            roots = self._connection.execute(
                "SELECT s.lead_key,s.aggregate_turn_id,s.source_event_id,"
                "s.source_event_hash,s.source_turn_receipt_hash,e.occurred_at,"
                "e.turn_receipt_hash FROM boundary_event_sources s "
                "JOIN boundary_events e ON e.lead_key=s.lead_key "
                "AND e.aggregate_turn_id=s.aggregate_turn_id "
                "AND e.turn_receipt_hash=s.source_turn_receipt_hash "
                "WHERE s.source_index=0 ORDER BY e.occurred_at,s.source_event_id LIMIT ?",
                (bounded,),
            ).fetchall()
            projected: list[BoundaryExecutionProjection] = []
            for root in roots:
                receipt = root[4]
                commands_all = self._connection.execute(
                    "SELECT command_id,command_type,command_json,command_hash,created_at,"
                    "source_turn_receipt_hash FROM boundary_commands "
                    "WHERE lead_key=? AND aggregate_turn_id=? ORDER BY created_at,command_id",
                    (root[0], root[1]),
                ).fetchall()
                public_all = self._connection.execute(
                    "SELECT public_row_id,chunk_index,status,dispatch_slots_consumed,"
                    "delivery_receipt_hash,created_at,updated_at,source_turn_receipt_hash "
                    "FROM boundary_public_outbox WHERE lead_key=? AND aggregate_turn_id=? "
                    "ORDER BY chunk_index,public_row_id",
                    (root[0], root[1]),
                ).fetchall()
                reasons: list[str] = []
                commands = tuple(
                    self._command_milestone(row)
                    for row in commands_all
                    if row[5] == receipt
                )
                if len(commands) != len(commands_all):
                    reasons.append("command_receipt_mismatch")
                public_rows = tuple(
                    BoundaryPublicMilestone(
                        public_row_id=row[0],
                        chunk_index=row[1],
                        status=row[2],
                        dispatch_slots_consumed=row[3],
                        delivery_receipt_hash=row[4],
                        created_at=_utc(row[5]),
                        updated_at=_utc(row[6]),
                        source_turn_receipt_hash=row[7],
                    )
                    for row in public_all
                    if row[7] == receipt
                )
                if len(public_rows) != len(public_all):
                    reasons.append("public_receipt_mismatch")
                projected.append(
                    BoundaryExecutionProjection(
                        execution_id=root[2],
                        lead_id=root[0],
                        aggregate_turn_id=root[1],
                        source_event_hash=root[3],
                        source_turn_receipt_hash=receipt,
                        occurred_at=_utc(root[5]),
                        turn_receipt_hash=root[6],
                        commands=commands,
                        public_rows=public_rows,
                        degraded_reasons=tuple(reasons),
                    )
                )
            return tuple(projected)
        except sqlite3.Error as exc:
            raise ProjectionSourceError("projection source unavailable") from exc

    @staticmethod
    def _command_milestone(row: sqlite3.Row) -> BoundaryCommandMilestone:
        raw = row[2]
        digest = row[3]
        if (
            type(raw) is not str
            or type(digest) is not str
            or hashlib.sha256(raw.encode()).hexdigest() != digest
        ):
            raise ProjectionSourceError("projection source unavailable")
        payment_id = None
        if row[1] == "reservation":
            try:
                command = loads_command(raw)
                if dumps_command(command) != raw or command.command_id != row[0]:
                    raise ValueError("command identity mismatch")
                service = command.payload.components[0].service.value
                unit = {"lodging": "hostel", "activity": "agency"}[service]
                material = f"{command.command_id}\x00{unit}".encode()
                payment_id = "payment:" + hashlib.sha256(material).hexdigest()[:32]
            except (KeyError, TypeError, ValueError) as exc:
                raise ProjectionSourceError("projection source unavailable") from exc
        return BoundaryCommandMilestone(
            command_id=row[0],
            command_type=row[1],
            command_hash=digest,
            created_at=_utc(row[4]),
            source_turn_receipt_hash=row[5],
            payment_id=payment_id,
        )


class _SQLiteProjectionSource:
    _required_columns: dict[str, frozenset[str]] = {}

    def __init__(self, path: Path, *, busy_timeout_ms: int = 250) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("projection path must be absolute")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 30_000:
            raise ValueError("busy_timeout_ms must be bounded exact int")
        _require_materialized_wal_source(path)
        self._closed = False
        try:
            connection = sqlite3.connect(
                _ro_uri(path),
                uri=True,
                isolation_level=None,
                timeout=busy_timeout_ms / 1000,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            connection.execute("PRAGMA query_only=ON")
            self._connection = connection
            self._verify_schema()
            connection.set_authorizer(self._authorizer)
        except (OSError, sqlite3.Error, ProjectionSourceError):
            try:
                connection.close()
            except UnboundLocalError:
                pass
            raise ProjectionSourceError("projection source unavailable") from None

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    @property
    def query_only(self) -> bool:
        return self._connection.execute("PRAGMA query_only").fetchone()[0] == 1

    @staticmethod
    def _authorizer(
        action: int,
        argument1: str | None,
        argument2: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action in {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_ALTER_TABLE,
            sqlite3.SQLITE_ATTACH,
            sqlite3.SQLITE_DETACH,
        }:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and not (
            (argument1 or "").casefold() in {"busy_timeout", "query_only"}
            and argument2 is None
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _verify_schema(self) -> None:
        tables = {
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        if set(self._required_columns) - tables:
            raise ProjectionSourceError("projection source unavailable")
        for table, required in self._required_columns.items():
            columns = {
                row[1]
                for row in self._connection.execute(f"PRAGMA table_info({table})")
            }
            if required - columns:
                raise ProjectionSourceError("projection source unavailable")


class SQLiteExecutionProjectionSource(_SQLiteProjectionSource):
    _required_columns = {
        "reservation_commands": frozenset({"command_id", "command_hash"}),
        "execution_ledger": frozenset(
            {
                "command_id",
                "status",
                "dispatch_request_hash",
                "dispatch_fenced_at",
                "outcome_hash",
                "updated_at",
            }
        ),
    }

    def _verify_schema(self) -> None:
        tables = tuple(
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        )
        if tables != PHASE5_V6_TABLES:
            raise ProjectionSourceError("projection source unavailable")
        migrations = tuple(
            tuple(row)
            for row in self._connection.execute(
                "SELECT version,schema_hash FROM schema_migrations ORDER BY version"
            )
        )
        if migrations != ((SCHEMA_VERSION_V6, schema_hash_v6()),):
            raise ProjectionSourceError("projection source unavailable")
        explicit_objects = tuple(
            self._connection.execute(
                "SELECT type,name FROM sqlite_schema WHERE type!='table' "
                "AND sql IS NOT NULL ORDER BY type,name"
            )
        )
        temporary_objects = tuple(
            self._connection.execute(
                "SELECT type,name FROM sqlite_temp_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        )
        if explicit_objects or temporary_objects:
            raise ProjectionSourceError("projection source unavailable")
        if tuple(self._connection.execute("PRAGMA foreign_key_check")):
            raise ProjectionSourceError("projection source unavailable")

    def for_command(
        self,
        *,
        command_id: str,
        command_hash: str,
    ) -> ExecutionLedgerMilestone | None:
        try:
            rows = self._connection.execute(
                "SELECT c.command_id,c.command_hash,l.status,l.dispatch_request_hash,"
                "l.dispatch_fenced_at,l.outcome_hash,l.updated_at "
                "FROM reservation_commands c JOIN execution_ledger l "
                "ON l.command_id=c.command_id WHERE c.command_id=? AND c.command_hash=?",
                (command_id, command_hash),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProjectionSourceError("projection source unavailable") from exc
        if len(rows) > 1:
            raise ProjectionSourceError("projection source unavailable")
        if not rows:
            return None
        row = rows[0]
        return ExecutionLedgerMilestone(
            command_id=row[0],
            command_hash=row[1],
            status=row[2],
            dispatch_request_hash=row[3],
            dispatch_fenced_at=None if row[4] is None else _utc(row[4]),
            outcome_hash=row[5],
            updated_at=_utc(row[6]),
        )


class SQLitePaymentProjectionSource(_SQLiteProjectionSource):
    _required_columns = {
        "payment_initiations": frozenset(
            {
                "initiation_id",
                "selection_json",
                "selection_hash",
                "status",
                "dispatch_slots",
                "result_hash",
                "updated_at",
            }
        )
    }

    def _verify_schema(self) -> None:
        expected = sqlite3.connect(":memory:")
        try:
            expected.executescript(_PAYMENT_PROJECTION_SCHEMA)
            expected_rows = tuple(
                expected.execute(
                    "SELECT name,sql FROM sqlite_schema WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
        finally:
            expected.close()
        actual_rows = tuple(
            tuple(row)
            for row in self._connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        if actual_rows != expected_rows:
            raise ProjectionSourceError("projection source unavailable")
        if tuple(self._connection.execute("PRAGMA foreign_key_check")):
            raise ProjectionSourceError("projection source unavailable")

    def for_payment(self, payment_id: str) -> PaymentLedgerMilestone | None:
        try:
            rows = self._connection.execute(
                "SELECT initiation_id,selection_json,selection_hash,status,"
                "dispatch_slots,result_hash,updated_at FROM payment_initiations "
                "ORDER BY initiation_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProjectionSourceError("projection source unavailable") from exc
        matches: list[PaymentLedgerMilestone] = []
        for row in rows:
            raw = row[1]
            if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != row[2]:
                raise ProjectionSourceError("projection source unavailable")
            try:
                value = json.loads(raw)
                obligation = value["obligation"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ProjectionSourceError("projection source unavailable") from exc
            if type(value) is not dict or set(value) != {"method", "obligation"}:
                raise ProjectionSourceError("projection source unavailable")
            if type(obligation) is not dict or obligation.get("payment_id") != payment_id:
                continue
            matches.append(
                PaymentLedgerMilestone(
                    payment_id=payment_id,
                    initiation_id=row[0],
                    selection_hash=row[2],
                    status=row[3],
                    dispatch_slots=row[4],
                    result_hash=row[5],
                    updated_at=_utc(row[6]),
                )
            )
            if len(matches) > 1:
                raise ProjectionSourceError("projection source unavailable")
        return matches[0] if matches else None


__all__ = [
    "BoundaryCommandMilestone",
    "BoundaryExecutionProjection",
    "BoundaryPublicMilestone",
    "ExecutionLedgerMilestone",
    "PaymentLedgerMilestone",
    "ProjectionSourceError",
    "SQLiteBoundaryProjectionSource",
    "SQLiteExecutionProjectionSource",
    "SQLitePaymentProjectionSource",
]
