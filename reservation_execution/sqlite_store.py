"""Durable SQLite workflow/event persistence with optimistic revision control."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Iterator

from reservation_domain import (
    EVENT_TYPES,
    STATE_TYPES,
    Event,
    ReservationCommand,
    State,
    SummaryRecorded,
    Transition,
    TransitionStatus,
    dumps_event,
    dumps_state,
    loads_event,
    loads_state,
    reduce,
)

from .schema import SCHEMA_VERSION, render_sqlite, schema_contract, schema_hash
from .types import OutboxMessage

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_EXPECTED_TABLES = tuple(table.name for table in schema_contract())
_FACTORY_TOKEN = object()


class StoreError(RuntimeError):
    """Base class for durable store failures."""


class StoreUnavailable(StoreError):
    """SQLite could not complete an operation without claiming domain conflict."""


class DataCorruption(StoreError):
    """Persisted bytes or metadata violate the canonical contract."""


class ConcurrencyConflict(StoreError):
    """The caller's optimistic revision is stale."""


class IdentityConflict(StoreError):
    """A durable identity already exists with divergent content or ownership."""


class UnsupportedEffect(StoreError):
    """The reducer produced an effect owned by a later implementation task."""


class WorkflowNotFound(StoreError):
    """The requested workflow does not exist."""


@dataclass(frozen=True, slots=True)
class PersistedTransition:
    state: State
    status: TransitionStatus
    reason: str
    commands: tuple[ReservationCommand, ...]
    duplicate: bool = False

    @classmethod
    def from_domain(
        cls,
        transition: Transition,
        *,
        duplicate: bool = False,
    ) -> "PersistedTransition":
        if type(transition) is not Transition:
            raise TypeError("transition must be the exact Transition type")
        return cls(
            state=transition.state,
            status=transition.status,
            reason=transition.reason,
            commands=transition.commands,
            duplicate=duplicate,
        )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_id(value: str, field_name: str) -> str:
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be an exact opaque identifier")
    return value


def _canonical_utc(value: str, field_name: str) -> datetime:
    if type(value) is not str:
        raise DataCorruption(f"{field_name} has the wrong SQLite type")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DataCorruption(f"{field_name} is not an ISO datetime") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
        or parsed.isoformat() != value
    ):
        raise DataCorruption(f"{field_name} is not canonical UTC")
    return parsed


def _sqlite_store_error(exc: sqlite3.Error, operation: str) -> StoreError:
    detail = str(exc).casefold()
    if isinstance(exc, sqlite3.IntegrityError):
        return DataCorruption(f"{operation} violated SQLite integrity")
    if isinstance(exc, sqlite3.OperationalError) and (
        "locked" in detail or "busy" in detail
    ):
        return ConcurrencyConflict(f"{operation} could not acquire the SQLite lock")
    return StoreUnavailable(f"{operation} failed in SQLite")


def _schema_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise DataCorruption("generated SQLite schema contains an incomplete statement")
    if len(statements) != len(_EXPECTED_TABLES):
        raise DataCorruption("generated SQLite schema statement count is not closed")
    return tuple(statements)


class SQLiteUnitOfWork:
    """One durable SQLite connection and its atomic workflow operations."""

    def __init__(
        self,
        path: Path,
        connection: sqlite3.Connection,
        *,
        _factory_token: object,
    ):
        if _factory_token is not _FACTORY_TOKEN:
            raise TypeError("SQLiteUnitOfWork must be created with open()")
        self._path = path
        self._connection = connection
        self._closed = False

    @classmethod
    def open(cls, path: Path) -> "SQLiteUnitOfWork":
        if not isinstance(path, Path):
            raise TypeError("path must be a pathlib.Path")
        if path.exists() and not path.is_file():
            raise ValueError("SQLite path must be a file or absent")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                path,
                isolation_level=None,
                timeout=5.0,
            )
            connection.execute("PRAGMA foreign_keys = ON")
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            connection.execute("PRAGMA synchronous = FULL")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise DataCorruption("SQLite foreign keys could not be enabled")
            if str(mode).casefold() != "wal":
                raise DataCorruption("SQLite WAL mode could not be enabled")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise DataCorruption("SQLite FULL synchronous mode could not be enabled")
            store = cls(path, connection, _factory_token=_FACTORY_TOKEN)
            store._initialize_or_validate_schema()
            return store
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise _sqlite_store_error(exc, "open") from exc
        except BaseException:
            if connection is not None:
                connection.close()
            raise

    @property
    def path(self) -> Path:
        return self._path

    def __enter__(self) -> "SQLiteUnitOfWork":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._connection.in_transaction:
                self._connection.rollback()
            self._connection.close()
        except sqlite3.Error as exc:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
            self._closed = True
            raise _sqlite_store_error(exc, "close") from exc
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreError("SQLiteUnitOfWork is closed")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._ensure_open()
        if self._connection.in_transaction:
            raise StoreError("nested SQLiteUnitOfWork transactions are forbidden")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            yield
            self._connection.commit()
        except BaseException as exc:
            rollback_error: sqlite3.Error | None = None
            try:
                if self._connection.in_transaction:
                    self._connection.rollback()
            except sqlite3.Error as rollback_exc:
                rollback_error = rollback_exc
            if rollback_error is not None:
                raise StoreUnavailable(
                    "SQLite transaction failed and rollback was not possible"
                ) from rollback_error
            if isinstance(exc, sqlite3.Error):
                raise _sqlite_store_error(exc, "transaction") from exc
            raise

    def _table_names(self) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        )

    def _initialize_or_validate_schema(self) -> None:
        names = self._table_names()
        if not names:
            with self._transaction():
                for statement in _schema_statements(render_sqlite()):
                    self._connection.execute(statement)
                applied_at = datetime.now(timezone.utc).isoformat()
                self._connection.execute(
                    "INSERT INTO schema_migrations "
                    "(version, schema_hash, applied_at) VALUES (?, ?, ?)",
                    (SCHEMA_VERSION, schema_hash("sqlite"), applied_at),
                )
        elif names != _EXPECTED_TABLES:
            raise DataCorruption(
                f"SQLite table universe mismatch: expected={_EXPECTED_TABLES}, found={names}"
            )
        self._validate_migration()

    def _validate_migration(self) -> None:
        try:
            rows = tuple(
                self._connection.execute(
                    "SELECT version, schema_hash FROM schema_migrations ORDER BY version"
                )
            )
        except sqlite3.DatabaseError as exc:
            raise DataCorruption("schema_migrations cannot be read") from exc
        expected = ((SCHEMA_VERSION, schema_hash("sqlite")),)
        if rows != expected:
            raise DataCorruption(
                f"SQLite migration mismatch: expected={expected}, found={rows}"
            )

    def create_workflow(self, state: State) -> None:
        if type(state) not in STATE_TYPES:
            raise TypeError("state must be an exact closed-universe state type")
        if (
            state.meta.revision != 0
            or state.meta.seen_event_ids
            or state.meta.seen_event_hashes
            or state.meta.command_ids
        ):
            raise ValueError(
                "create_workflow requires revision 0 with zero events and commands"
            )
        serialized = dumps_state(state)
        digest = _sha256_text(serialized)
        with self._transaction():
            row = self._workflow_row(state.meta.workflow_id)
            if row is not None:
                current = self._state_from_row(row)
                if current != state or row[3] != serialized or row[4] != digest:
                    raise IdentityConflict(
                        "workflow identity already exists with divergent state"
                    )
                return
            created_at = state.meta.last_event_at.isoformat()
            self._connection.execute(
                "INSERT INTO workflows "
                "(workflow_id, revision, state_type, state_json, state_hash, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    state.meta.workflow_id,
                    state.meta.revision,
                    state.TYPE,
                    serialized,
                    digest,
                    created_at,
                    created_at,
                ),
            )

    def load_workflow(self, workflow_id: str) -> State:
        self._ensure_open()
        workflow_id = _require_id(workflow_id, "workflow_id")
        try:
            row = self._workflow_row(workflow_id)
        except sqlite3.Error as exc:
            raise _sqlite_store_error(exc, "load_workflow") from exc
        if row is None:
            raise WorkflowNotFound(f"workflow not found: {workflow_id}")
        return self._state_from_row(row)

    def _workflow_row(self, workflow_id: str):
        workflow_id = _require_id(workflow_id, "workflow_id")
        return self._connection.execute(
            "SELECT workflow_id, revision, state_type, state_json, state_hash, "
            "created_at, updated_at FROM workflows WHERE workflow_id=?",
            (workflow_id,),
        ).fetchone()

    def _state_from_row(self, row) -> State:
        workflow_id, revision, state_type, raw, digest, created_at, updated_at = row
        if type(raw) is not str or type(digest) is not str:
            raise DataCorruption("workflow state bytes/hash have wrong SQLite types")
        if _sha256_text(raw) != digest:
            raise DataCorruption("workflow state hash mismatch")
        try:
            state = loads_state(raw)
            canonical = dumps_state(state)
        except (TypeError, ValueError) as exc:
            raise DataCorruption("workflow state serialization is invalid") from exc
        if canonical != raw:
            raise DataCorruption("workflow state serialization is noncanonical")
        if (
            state.meta.workflow_id != workflow_id
            or state.meta.revision != revision
            or state.TYPE != state_type
        ):
            raise DataCorruption("workflow row metadata disagrees with serialized state")
        created = _canonical_utc(created_at, "workflow.created_at")
        updated = _canonical_utc(updated_at, "workflow.updated_at")
        if updated != state.meta.last_event_at:
            raise DataCorruption("workflow updated_at disagrees with serialized state")
        if created > updated or (revision == 0 and created != updated):
            raise DataCorruption("workflow created_at is inconsistent with its revision")
        return state

    def apply_event(
        self,
        workflow_id: str,
        expected_revision: int,
        event: Event,
        *,
        outbox: tuple[OutboxMessage, ...] = (),
    ) -> PersistedTransition:
        workflow_id = _require_id(workflow_id, "workflow_id")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be an integer >= 0")
        if type(event) not in EVENT_TYPES:
            raise TypeError("event must be an exact closed-universe event type")
        if type(outbox) is not tuple or any(
            type(message) is not OutboxMessage for message in outbox
        ):
            raise TypeError("outbox must be a tuple of exact OutboxMessage values")

        with self._transaction():
            row = self._workflow_row(workflow_id)
            if row is None:
                raise WorkflowNotFound(f"workflow not found: {workflow_id}")
            current = self._state_from_row(row)
            if type(event) is SummaryRecorded:
                raise UnsupportedEffect(
                    "summary outbox persistence is owned by Task 5"
                )
            if outbox:
                raise UnsupportedEffect(
                    "outbox persistence is owned by Task 5 and cannot be discarded"
                )
            existing = self._event_row(event.event_id)
            if existing is not None:
                return self._resolve_duplicate(
                    existing,
                    workflow_id=workflow_id,
                    event=event,
                    current=current,
                )
            if current.meta.revision != expected_revision:
                raise ConcurrencyConflict(
                    f"expected revision {expected_revision}, "
                    f"found {current.meta.revision}"
                )
            transition = reduce(current, event)
            if transition.commands:
                raise UnsupportedEffect(
                    "command persistence is owned by Task 5 and cannot be discarded"
                )
            if transition.state.meta.revision != current.meta.revision + 1:
                raise DataCorruption("reducer transition did not advance exactly one revision")
            self._insert_event(
                workflow_id,
                event,
                transition.state.meta.revision,
            )
            self._update_state_compare_and_swap(current, transition.state)
            return PersistedTransition.from_domain(transition)

    def _event_row(self, event_id: str):
        event_id = _require_id(event_id, "event_id")
        return self._connection.execute(
            "SELECT event_id, workflow_id, revision, occurred_at, event_type, "
            "event_json, event_hash FROM domain_events WHERE event_id=?",
            (event_id,),
        ).fetchone()

    def _verified_event(self, row) -> Event:
        event_id, _, revision, occurred_at, event_type, raw, digest = row
        if type(revision) is not int or revision < 1:
            raise DataCorruption("event revision is invalid")
        if type(raw) is not str or type(digest) is not str:
            raise DataCorruption("event bytes/hash have wrong SQLite types")
        if _sha256_text(raw) != digest:
            raise DataCorruption("event hash mismatch")
        try:
            event = loads_event(raw)
            canonical = dumps_event(event)
        except (TypeError, ValueError) as exc:
            raise DataCorruption("event serialization is invalid") from exc
        if canonical != raw:
            raise DataCorruption("event serialization is noncanonical")
        if (
            event.event_id != event_id
            or event.TYPE != event_type
            or event.occurred_at.isoformat() != occurred_at
        ):
            raise DataCorruption("event row metadata disagrees with serialized event")
        return event

    def _resolve_duplicate(
        self,
        row,
        *,
        workflow_id: str,
        event: Event,
        current: State,
    ) -> PersistedTransition:
        existing = self._verified_event(row)
        existing_workflow_id = row[1]
        existing_revision = row[2]
        provided_raw = dumps_event(event)
        if existing_workflow_id != workflow_id:
            raise IdentityConflict("event identity belongs to a different workflow")
        if existing != event or row[6] != _sha256_text(provided_raw):
            raise IdentityConflict("event identity already exists with divergent payload")
        if not 1 <= existing_revision <= current.meta.revision:
            raise DataCorruption("event revision is outside the workflow revision")
        try:
            index = current.meta.seen_event_ids.index(event.event_id)
        except ValueError as exc:
            raise DataCorruption("event row is absent from workflow event history") from exc
        if existing_revision != index + 1:
            raise DataCorruption("event row revision disagrees with workflow history order")
        if current.meta.seen_event_hashes[index] != row[6]:
            raise DataCorruption("event row hash disagrees with workflow event history")
        return PersistedTransition(
            state=current,
            status=TransitionStatus.IGNORED,
            reason="duplicate_event",
            commands=(),
            duplicate=True,
        )

    def _insert_event(
        self,
        workflow_id: str,
        event: Event,
        revision: int,
    ) -> None:
        raw = dumps_event(event)
        self._connection.execute(
            "INSERT INTO domain_events "
            "(event_id, workflow_id, revision, occurred_at, event_type, "
            "event_json, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                workflow_id,
                revision,
                event.occurred_at.isoformat(),
                event.TYPE,
                raw,
                _sha256_text(raw),
            ),
        )

    def _update_state_compare_and_swap(self, current: State, next_state: State) -> None:
        current_raw = dumps_state(current)
        next_raw = dumps_state(next_state)
        cursor = self._connection.execute(
            "UPDATE workflows SET revision=?, state_type=?, state_json=?, "
            "state_hash=?, updated_at=? "
            "WHERE workflow_id=? AND revision=? AND state_hash=?",
            (
                next_state.meta.revision,
                next_state.TYPE,
                next_raw,
                _sha256_text(next_raw),
                next_state.meta.last_event_at.isoformat(),
                current.meta.workflow_id,
                current.meta.revision,
                _sha256_text(current_raw),
            ),
        )
        if cursor.rowcount != 1:
            raise ConcurrencyConflict("workflow compare-and-swap did not update one row")


__all__ = [
    "SQLiteUnitOfWork",
    "PersistedTransition",
    "StoreError",
    "StoreUnavailable",
    "DataCorruption",
    "ConcurrencyConflict",
    "IdentityConflict",
    "UnsupportedEffect",
    "WorkflowNotFound",
]
