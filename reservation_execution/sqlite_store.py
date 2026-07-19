"""Durable SQLite workflow/event persistence with optimistic revision control."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Iterator

from reservation_domain import (
    AwaitingConfirmationState,
    EVENT_TYPES,
    STATE_TYPES,
    Event,
    ExecutingState,
    ExecutionCertainty,
    ExecutionFinished,
    ExecutionOutcome,
    ExecutionQueuedState,
    ExecutionStarted,
    FailedBeforeProviderState,
    FailedNoEffectState,
    ManualReviewState,
    ManualReviewRequested,
    ReservationCommand,
    State,
    SummaryRecorded,
    SucceededState,
    Transition,
    TransitionStatus,
    UncertainState,
    dumps_command,
    dumps_event,
    dumps_outcome,
    dumps_state,
    loads_command,
    loads_event,
    loads_outcome,
    loads_state,
    new_workflow,
    reduce,
)

from .adapter import PreparationFailure
from .projection import (
    LedgerSnapshot,
    project_outcome_outbox,
    project_preparation_failure_outbox,
    validate_summary_outbox,
)
from .schema import SCHEMA_VERSION, render_sqlite, schema_contract, schema_hash
from .types import (
    CommandClaim,
    DispatchPermit,
    DispatchRequest,
    Lease,
    LedgerStatus,
    OutboxKind,
    OutboxMessage,
    OutboxStatus,
    PreparationDisposition,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_EXPECTED_TABLES = tuple(table.name for table in schema_contract())
_FACTORY_TOKEN = object()
_MAX_PREPARATION_FAILURES = 3


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
    """An operational event must use its specialized atomic store method."""


class StaleLease(StoreError):
    """A lease owner/token is absent, expired, or no longer current."""


class DispatchAlreadyFenced(StoreError):
    """The command has already consumed its only durable dispatch slot."""


class WorkflowNotFound(StoreError):
    """The requested workflow does not exist."""


class CommandNotFound(StoreError):
    """The requested authorized command does not exist."""


class OutboxNotFound(StoreError):
    """The requested durable outbox message does not exist."""


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


def _require_utc_input(value: datetime, field_name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be an exact UTC datetime")
    return value.astimezone(timezone.utc)


def _require_lease_ttl(value: timedelta) -> timedelta:
    if type(value) is not timedelta or value <= timedelta(0):
        raise ValueError("lease_ttl must be a positive timedelta")
    return value


def _derived_id(prefix: str, *parts: str) -> str:
    material = "|".join(parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(material).hexdigest()}"


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
    def _transaction(self, operation: str = "transaction") -> Iterator[None]:
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
                    f"{operation} failed and rollback was not possible"
                ) from rollback_error
            if isinstance(exc, sqlite3.Error):
                raise _sqlite_store_error(exc, operation) from exc
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

    def _command_row(self, command_id: str):
        command_id = _require_id(command_id, "command_id")
        return self._connection.execute(
            "SELECT command_id, idempotency_key, workflow_id, draft_id, "
            "draft_version, subject_signature, operation, command_json, "
            "command_hash, created_at FROM reservation_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()

    def _command_from_row(self, row) -> ReservationCommand:
        (
            command_id,
            idempotency_key,
            workflow_id,
            draft_id,
            draft_version,
            subject_signature,
            operation,
            raw,
            digest,
            created_at,
        ) = row
        if type(raw) is not str or type(digest) is not str:
            raise DataCorruption("command bytes/hash have wrong SQLite types")
        if _sha256_text(raw) != digest:
            raise DataCorruption("command hash mismatch")
        try:
            command = loads_command(raw)
            canonical = dumps_command(command)
        except (TypeError, ValueError) as exc:
            raise DataCorruption("command serialization is invalid") from exc
        if canonical != raw:
            raise DataCorruption("command serialization is noncanonical")
        created = _canonical_utc(created_at, "command.created_at")
        if (
            command.command_id != command_id
            or command.idempotency_key != idempotency_key
            or command.workflow_id != workflow_id
            or command.draft_id != draft_id
            or command.draft_version != draft_version
            or command.subject_signature != subject_signature
            or command.operation.value != operation
            or command.created_at != created
        ):
            raise DataCorruption("command row metadata disagrees with serialized command")
        return command

    def load_command(self, command_id: str) -> ReservationCommand:
        self._ensure_open()
        command_id = _require_id(command_id, "command_id")
        try:
            row = self._command_row(command_id)
        except sqlite3.Error as exc:
            raise _sqlite_store_error(exc, "load_command") from exc
        if row is None:
            raise CommandNotFound(f"command not found: {command_id}")
        return self._command_from_row(row)

    def _ledger_row(self, command_id: str):
        command_id = _require_id(command_id, "command_id")
        return self._connection.execute(
            "SELECT command_id, status, claim_owner, fencing_token, "
            "lease_acquired_at, lease_expires_at, claim_count, "
            "preparation_failures, dispatch_slots_consumed, "
            "dispatch_request_hash, dispatch_fenced_at, outcome_json, "
            "outcome_hash, updated_at FROM execution_ledger WHERE command_id=?",
            (command_id,),
        ).fetchone()

    def load_ledger(self, command_id: str) -> LedgerSnapshot:
        self._ensure_open()
        command = self.load_command(command_id)
        try:
            row = self._ledger_row(command.command_id)
        except sqlite3.Error as exc:
            raise _sqlite_store_error(exc, "load_ledger") from exc
        if row is None:
            raise DataCorruption("authorized command has no execution ledger")
        try:
            snapshot = LedgerSnapshot(
                command_id=row[0],
                status=LedgerStatus(row[1]),
                claim_owner=row[2],
                fencing_token=row[3],
                lease_acquired_at=(
                    None
                    if row[4] is None
                    else _canonical_utc(row[4], "ledger.lease_acquired_at")
                ),
                lease_expires_at=(
                    None
                    if row[5] is None
                    else _canonical_utc(row[5], "ledger.lease_expires_at")
                ),
                claim_count=row[6],
                preparation_failures=row[7],
                dispatch_slots_consumed=row[8],
                dispatch_request_hash=row[9],
                dispatch_fenced_at=(
                    None
                    if row[10] is None
                    else _canonical_utc(row[10], "ledger.dispatch_fenced_at")
                ),
                outcome_json=row[11],
                outcome_hash=row[12],
                updated_at=_canonical_utc(row[13], "ledger.updated_at"),
            )
        except (TypeError, ValueError) as exc:
            raise DataCorruption("execution ledger row is invalid") from exc
        if snapshot.command_id != command.command_id:
            raise DataCorruption("execution ledger belongs to another command")
        if snapshot.updated_at < command.created_at:
            raise DataCorruption("execution ledger predates its command")
        if (
            snapshot.status is LedgerStatus.QUEUED
            and snapshot.claim_count == 0
            and snapshot.updated_at != command.created_at
        ):
            raise DataCorruption("initial queued ledger timestamp disagrees with command")
        if snapshot.outcome_json is not None:
            if _sha256_text(snapshot.outcome_json) != snapshot.outcome_hash:
                raise DataCorruption("execution outcome hash mismatch")
            try:
                outcome = loads_outcome(snapshot.outcome_json)
                canonical_outcome = dumps_outcome(outcome)
            except (TypeError, ValueError) as exc:
                raise DataCorruption("execution outcome serialization is invalid") from exc
            if canonical_outcome != snapshot.outcome_json:
                raise DataCorruption("execution outcome serialization is noncanonical")
            if outcome.command_id != command.command_id:
                raise DataCorruption("execution outcome belongs to another command")
            if snapshot.dispatch_slots_consumed == 0:
                if (
                    snapshot.status is not LedgerStatus.OUTCOME_RECORDED
                    or outcome.certainty is not ExecutionCertainty.NOT_CALLED
                ):
                    raise DataCorruption(
                        "pre-dispatch outcome certainty/status matrix is invalid"
                    )
            else:
                expected_status = (
                    LedgerStatus.MANUAL_REVIEW
                    if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN
                    else LedgerStatus.OUTCOME_RECORDED
                )
                if (
                    outcome.certainty is ExecutionCertainty.NOT_CALLED
                    or snapshot.status is not expected_status
                ):
                    raise DataCorruption(
                        "post-dispatch outcome certainty/status matrix is invalid"
                    )
        return snapshot

    def _outbox_row(self, message_id: str):
        message_id = _require_id(message_id, "message_id")
        return self._connection.execute(
            "SELECT message_id, idempotency_key, workflow_id, command_id, kind, "
            "template_id, payload_json, payload_hash, status, claim_owner, "
            "fencing_token, lease_acquired_at, lease_expires_at, "
            "delivery_attempts, delivered_at, receipt_hash, created_at, "
            "updated_at FROM outbox_messages WHERE message_id=?",
            (message_id,),
        ).fetchone()

    def _outbox_from_row(self, row) -> OutboxMessage:
        try:
            message = OutboxMessage(
                message_id=row[0],
                idempotency_key=row[1],
                workflow_id=row[2],
                command_id=row[3],
                kind=OutboxKind(row[4]),
                template_id=row[5],
                canonical_payload=row[6],
                payload_hash=row[7],
                created_at=_canonical_utc(row[16], "outbox.created_at"),
            )
            status = OutboxStatus(row[8])
        except (TypeError, ValueError) as exc:
            raise DataCorruption("outbox message row is invalid") from exc
        immutable = (
            message.message_id,
            message.idempotency_key,
            message.workflow_id,
            message.command_id,
            message.kind.value,
            message.template_id,
            message.canonical_payload,
            message.payload_hash,
            message.created_at.isoformat(),
        )
        persisted = (*row[:8], row[16])
        if immutable != persisted:
            raise DataCorruption("outbox immutable SQL bytes were normalized or changed")
        if type(row[10]) is not int or type(row[13]) is not int:
            raise DataCorruption("outbox counters have wrong SQLite types")
        optional_times = tuple(
            None if row[index] is None else _canonical_utc(row[index], field_name)
            for index, field_name in (
                (11, "outbox.lease_acquired_at"),
                (12, "outbox.lease_expires_at"),
                (14, "outbox.delivered_at"),
            )
        )
        updated_at = _canonical_utc(row[17], "outbox.updated_at")
        if updated_at < message.created_at:
            raise DataCorruption("outbox updated_at predates created_at")
        if status is OutboxStatus.PENDING and (
            row[9] is not None
            or optional_times[0] is not None
            or optional_times[1] is not None
            or optional_times[2] is not None
            or row[15] is not None
        ):
            raise DataCorruption("pending outbox message has lease or receipt state")
        if row[10] < 0 or row[13] < 0:
            raise DataCorruption("outbox counters are negative")
        if message.command_id is not None:
            command = self.load_command(message.command_id)
            if command.workflow_id != message.workflow_id:
                raise DataCorruption("outbox command belongs to another workflow")
        return message

    def load_outbox(self, message_id: str) -> OutboxMessage:
        self._ensure_open()
        message_id = _require_id(message_id, "message_id")
        try:
            row = self._outbox_row(message_id)
            if row is None:
                raise OutboxNotFound(f"outbox message not found: {message_id}")
            message = self._outbox_from_row(row)
            if message.kind is OutboxKind.SUMMARY_PRESENTED:
                self._verify_summary_outbox_projection(message)
            else:
                self._verify_execution_outbox_projection(message)
            return message
        except sqlite3.Error as exc:
            raise _sqlite_store_error(exc, "load_outbox") from exc

    def _replay_workflow_history(
        self,
        workflow_id: str,
        *,
        before_revision: int | None = None,
    ) -> State:
        row = self._workflow_row(workflow_id)
        if row is None:
            raise DataCorruption("event history references a missing workflow")
        current = self._state_from_row(row)
        if before_revision is not None and (
            type(before_revision) is not int
            or not 1 <= before_revision <= current.meta.revision
        ):
            raise DataCorruption("historical target revision is outside the workflow")
        state: State = new_workflow(
            workflow_id=workflow_id,
            started_at=_canonical_utc(row[5], "workflow.created_at"),
        )
        rows = tuple(
            self._connection.execute(
                "SELECT event_id, workflow_id, revision, occurred_at, event_type, "
                "event_json, event_hash FROM domain_events "
                "WHERE workflow_id=? ORDER BY revision",
                (workflow_id,),
            )
        )
        if len(rows) != current.meta.revision:
            raise DataCorruption("workflow event row count disagrees with revision")
        historical: State | None = None
        for expected_revision, event_row in enumerate(rows, start=1):
            if event_row[1] != workflow_id or event_row[2] != expected_revision:
                raise DataCorruption(
                    "workflow event history has a revision gap or owner mismatch"
                )
            event = self._verified_event(event_row)
            index = expected_revision - 1
            if (
                current.meta.seen_event_ids[index] != event.event_id
                or current.meta.seen_event_hashes[index] != event_row[6]
            ):
                raise DataCorruption(
                    "workflow metadata disagrees with durable event history"
                )
            if expected_revision == before_revision:
                historical = state
            transition = reduce(state, event)
            if transition.state.meta.revision != expected_revision:
                raise DataCorruption(
                    "historical reducer replay did not advance one revision"
                )
            state = transition.state
        if state != current:
            raise DataCorruption("workflow state diverges from full reducer replay")
        if before_revision is not None:
            if historical is None:
                raise DataCorruption("historical target revision was not found")
            return historical
        return state

    def _historical_state_before_revision(
        self,
        workflow_id: str,
        revision: int,
    ) -> State:
        return self._replay_workflow_history(
            workflow_id,
            before_revision=revision,
        )

    def _verify_summary_outbox_projection(self, message: OutboxMessage) -> None:
        rows = tuple(
            self._connection.execute(
                "SELECT event_id, workflow_id, revision, occurred_at, event_type, "
                "event_json, event_hash FROM domain_events "
                "WHERE workflow_id=? AND event_type='summary_recorded' ORDER BY revision",
                (message.workflow_id,),
            )
        )
        matched: tuple[SummaryRecorded, int] | None = None
        for row in rows:
            event = self._verified_event(row)
            if type(event) is SummaryRecorded and event.outbox_message_id == message.message_id:
                if matched is not None:
                    raise DataCorruption("multiple summary events reference one outbox message")
                matched = (event, row[2])
        if matched is None:
            raise DataCorruption("summary outbox has no owning SummaryRecorded event")
        event, revision = matched
        historical = self._historical_state_before_revision(
            message.workflow_id,
            revision,
        )
        try:
            validate_summary_outbox(historical, event, message)
        except (TypeError, ValueError) as exc:
            raise DataCorruption(
                "summary outbox diverges from its historical Phase 4 artifact"
            ) from exc

    def _verify_execution_outcome_history(
        self,
        command: ReservationCommand,
        outcome: ExecutionOutcome,
        ledger: LedgerSnapshot,
    ) -> None:
        state = self._replay_workflow_history(command.workflow_id)
        if (
            not self._terminal_state_matches_outcome(state, command, outcome)
            or state.meta.last_event_at != ledger.updated_at
        ):
            raise DataCorruption("execution outcome disagrees with terminal workflow state")
        rows = tuple(
            self._connection.execute(
                "SELECT event_id, workflow_id, revision, occurred_at, event_type, "
                "event_json, event_hash FROM domain_events WHERE workflow_id=? "
                "AND event_type IN ('execution_finished', 'manual_review_requested') "
                "ORDER BY revision",
                (command.workflow_id,),
            )
        )
        verified = tuple((self._verified_event(row), row[2]) for row in rows)
        expected_count = (
            2 if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN else 1
        )
        if len(verified) != expected_count:
            raise DataCorruption("execution outcome has the wrong terminal event count")
        finished, finished_revision = verified[0]
        expected_finished_id = (
            _derived_id("event", "preparation_not_called", command.command_id)
            if outcome.certainty is ExecutionCertainty.NOT_CALLED
            else _derived_id(
                "event",
                "execution_outcome",
                command.command_id,
                ledger.outcome_hash,
            )
        )
        expected_finished_revision = state.meta.revision - (expected_count - 1)
        if (
            type(finished) is not ExecutionFinished
            or finished.event_id != expected_finished_id
            or finished.command_id != command.command_id
            or finished.outcome != outcome
            or finished.occurred_at != ledger.updated_at
            or finished_revision != expected_finished_revision
        ):
            raise DataCorruption("execution outcome has no exact owning finished event")
        if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN:
            review, review_revision = verified[1]
            expected_review_id = _derived_id(
                "event",
                "manual_review",
                command.command_id,
                ledger.outcome_hash,
            )
            if (
                type(review) is not ManualReviewRequested
                or review.event_id != expected_review_id
                or review.reason != "provider_effect_uncertain"
                or review.occurred_at != ledger.updated_at
                or review_revision != state.meta.revision
                or state.reason != review.reason
            ):
                raise DataCorruption("uncertain outcome has no exact manual-review event")

    def _verify_execution_outbox_projection(self, message: OutboxMessage) -> None:
        if message.command_id is None:
            raise DataCorruption("execution outbox requires an authorized command")
        command = self.load_command(message.command_id)
        ledger = self.load_ledger(command.command_id)
        if (
            message.workflow_id != command.workflow_id
            or ledger.outcome_json is None
            or ledger.outcome_hash is None
            or ledger.updated_at != message.created_at
        ):
            raise DataCorruption("execution outbox has no matching durable outcome")
        try:
            outcome = loads_outcome(ledger.outcome_json)
            self._verify_execution_outcome_history(command, outcome, ledger)
            if ledger.dispatch_slots_consumed == 0:
                if (
                    ledger.status is not LedgerStatus.OUTCOME_RECORDED
                    or outcome.certainty is not ExecutionCertainty.NOT_CALLED
                ):
                    raise ValueError("preparation outcome matrix is invalid")
                expected = project_preparation_failure_outbox(
                    command,
                    outcome,
                    created_at=message.created_at,
                )
            else:
                if (
                    outcome.certainty is ExecutionCertainty.NOT_CALLED
                    or ledger.status
                    not in (LedgerStatus.OUTCOME_RECORDED, LedgerStatus.MANUAL_REVIEW)
                ):
                    raise ValueError("post-fence outcome matrix is invalid")
                expected = project_outcome_outbox(
                    command,
                    outcome,
                    created_at=message.created_at,
                )
        except (TypeError, ValueError) as exc:
            raise DataCorruption("execution outbox projection is invalid") from exc
        if message != expected:
            raise DataCorruption("execution outbox diverges from its durable outcome")

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
        if type(event) in (ExecutionStarted, ExecutionFinished, ManualReviewRequested):
            raise UnsupportedEffect(
                "operational event requires a specialized atomic store method"
            )
        if type(outbox) is not tuple or any(
            type(message) is not OutboxMessage for message in outbox
        ):
            raise TypeError("outbox must be a tuple of exact OutboxMessage values")

        with self._transaction():
            row = self._workflow_row(workflow_id)
            if row is None:
                raise WorkflowNotFound(f"workflow not found: {workflow_id}")
            current = self._state_from_row(row)
            existing = self._event_row(event.event_id)
            if existing is not None:
                self._validate_duplicate_outbox_shape(event, outbox)
                result = self._resolve_duplicate(
                    existing,
                    workflow_id=workflow_id,
                    event=event,
                    current=current,
                )
                self._validate_duplicate_outbox(event, outbox)
                self._assert_current_command_projection(current)
                return result
            if current.meta.revision != expected_revision:
                raise ConcurrencyConflict(
                    f"expected revision {expected_revision}, "
                    f"found {current.meta.revision}"
                )
            validated_outbox = self._validate_new_outbox(current, event, outbox)
            transition = reduce(current, event)
            if type(event) is SummaryRecorded:
                self._validate_applied_summary_transition(
                    event,
                    validated_outbox[0],
                    transition,
                )
            if len(transition.commands) > 1:
                raise DataCorruption("reducer emitted more than one authorized command")
            if transition.state.meta.revision != current.meta.revision + 1:
                raise DataCorruption("reducer transition did not advance exactly one revision")
            self._validate_transition_commands(current, transition)
            self._insert_event(
                workflow_id,
                event,
                transition.state.meta.revision,
            )
            self._update_state_compare_and_swap(current, transition.state)
            for command in transition.commands:
                self._insert_immutable_command(command)
                self._insert_initial_ledger(command)
            for message in validated_outbox:
                self._insert_outbox(message)
            return PersistedTransition.from_domain(transition)

    def _validate_new_outbox(
        self,
        current: State,
        event: Event,
        outbox: tuple[OutboxMessage, ...],
    ) -> tuple[OutboxMessage, ...]:
        if type(event) is SummaryRecorded:
            if len(outbox) != 1:
                raise ValueError("SummaryRecorded requires exactly one outbox message")
            try:
                validated = validate_summary_outbox(current, event, outbox[0])
            except (TypeError, ValueError) as exc:
                raise IdentityConflict("summary outbox does not match event/artifact") from exc
            return (validated,)
        if outbox:
            raise ValueError("caller-provided outbox is only allowed for SummaryRecorded")
        return ()

    def _validate_applied_summary_transition(
        self,
        event: SummaryRecorded,
        message: OutboxMessage,
        transition: Transition,
    ) -> None:
        if (
            transition.status is not TransitionStatus.APPLIED
            or type(transition.state) is not AwaitingConfirmationState
        ):
            raise IdentityConflict(
                "SummaryRecorded must produce an applied summary transition"
            )
        summary = transition.state.summary
        if (
            summary.summary_event_id != event.summary_event_id
            or summary.outbox_message_id != message.message_id
            or summary.draft_version != event.draft_version
            or summary.subject_signature != event.subject_signature
            or summary.presented_at != message.created_at
        ):
            raise DataCorruption("applied summary state disagrees with event/outbox")

    def _validate_duplicate_outbox(
        self,
        event: Event,
        outbox: tuple[OutboxMessage, ...],
    ) -> None:
        if type(event) is SummaryRecorded:
            if len(outbox) != 1:
                raise ValueError("SummaryRecorded requires exactly one outbox message")
            candidate = outbox[0]
            if candidate.message_id != event.outbox_message_id:
                raise IdentityConflict("summary replay references another outbox identity")
            try:
                persisted = self.load_outbox(event.outbox_message_id)
            except OutboxNotFound as exc:
                raise DataCorruption("summary event has no durable outbox message") from exc
            if candidate != persisted:
                raise IdentityConflict("summary replay contains divergent outbox content")
        elif outbox:
            raise ValueError("caller-provided outbox is only allowed for SummaryRecorded")

    def _validate_duplicate_outbox_shape(
        self,
        event: Event,
        outbox: tuple[OutboxMessage, ...],
    ) -> None:
        if type(event) is SummaryRecorded:
            if len(outbox) != 1:
                raise ValueError("SummaryRecorded requires exactly one outbox message")
        elif outbox:
            raise ValueError("caller-provided outbox is only allowed for SummaryRecorded")

    def _validate_transition_commands(
        self,
        current: State,
        transition: Transition,
    ) -> None:
        before = current.meta.command_ids
        after = transition.state.meta.command_ids
        if not transition.commands:
            if after != before:
                raise DataCorruption("state command IDs changed without a reducer command")
            return
        command = transition.commands[0]
        if type(command) is not ReservationCommand:
            raise DataCorruption("reducer command has an unknown type")
        if command.workflow_id != current.meta.workflow_id:
            raise DataCorruption("reducer command belongs to another workflow")
        if before or after != (command.command_id,):
            raise DataCorruption("reducer command disagrees with next state identity")
        state_command = getattr(transition.state, "command", None)
        if state_command != command:
            raise DataCorruption("next state does not embed the authorized command")

    def _assert_current_command_projection(self, current: State) -> None:
        if not current.meta.command_ids:
            return
        if len(current.meta.command_ids) != 1:
            raise DataCorruption("workflow contains more than one command identity")
        command = self.load_command(current.meta.command_ids[0])
        if getattr(current, "command", None) != command:
            raise DataCorruption("workflow state command disagrees with durable command")
        self.load_ledger(command.command_id)

    def _claim_from_projection(
        self,
        *,
        command: ReservationCommand,
        state: State,
        ledger: LedgerSnapshot,
    ) -> CommandClaim:
        if (
            ledger.status is not LedgerStatus.PREPARING
            or ledger.claim_owner is None
            or ledger.lease_acquired_at is None
            or ledger.lease_expires_at is None
        ):
            raise DataCorruption("claim projection requires a complete preparing lease")
        return CommandClaim(
            command=command,
            workflow_revision=state.meta.revision,
            lease=Lease(
                owner=ledger.claim_owner,
                fencing_token=ledger.fencing_token,
                acquired_at=ledger.lease_acquired_at,
                expires_at=ledger.lease_expires_at,
            ),
            claim_count=ledger.claim_count,
            preparation_failures=ledger.preparation_failures,
        )

    def _assert_live_preparation_claim(
        self,
        claim: CommandClaim,
        *,
        now: datetime,
    ) -> tuple[ReservationCommand, LedgerSnapshot, State]:
        command = self.load_command(claim.command.command_id)
        ledger = self.load_ledger(command.command_id)
        state = self.load_workflow(command.workflow_id)
        lease = claim.lease
        if command != claim.command or state.meta.revision != claim.workflow_revision:
            raise StaleLease("claim command or workflow revision is no longer current")
        if (
            type(state) is not ExecutingState
            or state.command != command
            or state.meta.command_ids != (command.command_id,)
        ):
            raise DataCorruption(
                "live preparation claim requires exact executing workflow command"
            )
        if (
            ledger.status is not LedgerStatus.PREPARING
            or ledger.dispatch_slots_consumed != 0
            or ledger.claim_owner != lease.owner
            or ledger.fencing_token != lease.fencing_token
            or ledger.lease_acquired_at != lease.acquired_at
            or ledger.lease_expires_at != lease.expires_at
            or ledger.claim_count != claim.claim_count
            or ledger.preparation_failures != claim.preparation_failures
            or now < ledger.updated_at
            or now < lease.acquired_at
            or now >= lease.expires_at
        ):
            raise StaleLease("preparation lease is stale or expired")
        return command, ledger, state

    def claim_command(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> CommandClaim | None:
        worker_id = _require_id(worker_id, "worker_id")
        now = _require_utc_input(now, "now")
        lease_ttl = _require_lease_ttl(lease_ttl)
        try:
            expires_at = now + lease_ttl
        except OverflowError as exc:
            raise ValueError("lease_ttl overflows datetime range") from exc
        with self._transaction("claim_command"):
            candidate = self._connection.execute(
                "SELECT ledger.command_id FROM execution_ledger AS ledger "
                "JOIN reservation_commands AS command "
                "ON command.command_id=ledger.command_id "
                "WHERE ledger.status IN ('queued', 'preparing') "
                "AND ledger.dispatch_slots_consumed=0 "
                "AND (ledger.claim_owner IS NULL OR ledger.lease_expires_at<=?) "
                "ORDER BY command.created_at, ledger.command_id LIMIT 1",
                (now.isoformat(),),
            ).fetchone()
            if candidate is None:
                return None
            command = self.load_command(candidate[0])
            ledger = self.load_ledger(command.command_id)
            state = self.load_workflow(command.workflow_id)
            if now < command.created_at or now < ledger.updated_at:
                raise ValueError("claim time cannot predate command or ledger state")
            if getattr(state, "command", None) != command:
                raise DataCorruption("claim workflow does not embed its durable command")
            if type(state) not in (ExecutionQueuedState, ExecutingState):
                raise DataCorruption("eligible ledger has a non-claimable workflow state")
            cursor = self._connection.execute(
                "UPDATE execution_ledger SET status='preparing', claim_owner=?, "
                "fencing_token=fencing_token+1, lease_acquired_at=?, "
                "lease_expires_at=?, claim_count=claim_count+1, updated_at=? "
                "WHERE command_id=? AND fencing_token=? AND claim_count=? "
                "AND dispatch_slots_consumed=0 AND "
                "((status='queued' AND claim_owner IS NULL) OR "
                "(status='preparing' AND lease_expires_at<=?))",
                (
                    worker_id,
                    now.isoformat(),
                    expires_at.isoformat(),
                    now.isoformat(),
                    command.command_id,
                    ledger.fencing_token,
                    ledger.claim_count,
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflict("eligible command claim lost its compare-and-swap")
            if type(state) is ExecutionQueuedState:
                event = ExecutionStarted(
                    event_id=_derived_id("event", "execution_started", command.command_id),
                    occurred_at=now,
                    command_id=command.command_id,
                )
                if self._event_row(event.event_id) is not None:
                    raise DataCorruption("queued workflow already has an execution-start event")
                transition = reduce(state, event)
                if (
                    transition.status is not TransitionStatus.APPLIED
                    or type(transition.state) is not ExecutingState
                    or transition.commands
                ):
                    raise DataCorruption("claim did not produce the exact executing transition")
                self._insert_event(
                    command.workflow_id,
                    event,
                    transition.state.meta.revision,
                )
                self._update_state_compare_and_swap(state, transition.state)
                state = transition.state
            updated = self.load_ledger(command.command_id)
            return self._claim_from_projection(
                command=command,
                state=state,
                ledger=updated,
            )

    def renew_command_lease(
        self,
        claim: CommandClaim,
        *,
        now: datetime,
        lease_ttl: timedelta,
    ) -> CommandClaim:
        if type(claim) is not CommandClaim:
            raise TypeError("claim must be the exact CommandClaim type")
        now = _require_utc_input(now, "now")
        lease_ttl = _require_lease_ttl(lease_ttl)
        try:
            expires_at = now + lease_ttl
        except OverflowError as exc:
            raise ValueError("lease_ttl overflows datetime range") from exc
        with self._transaction("renew_command_lease"):
            command, ledger, state = self._assert_live_preparation_claim(claim, now=now)
            if expires_at <= claim.lease.expires_at:
                raise ValueError("renewed lease must extend the current expiry")
            cursor = self._connection.execute(
                "UPDATE execution_ledger SET lease_expires_at=?, updated_at=? "
                "WHERE command_id=? AND status='preparing' AND claim_owner=? "
                "AND fencing_token=? AND lease_acquired_at=? AND lease_expires_at=? "
                "AND dispatch_slots_consumed=0",
                (
                    expires_at.isoformat(),
                    now.isoformat(),
                    command.command_id,
                    claim.lease.owner,
                    claim.lease.fencing_token,
                    claim.lease.acquired_at.isoformat(),
                    claim.lease.expires_at.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLease("preparation lease changed during renewal")
            updated = self.load_ledger(command.command_id)
            if updated.claim_count != ledger.claim_count:
                raise DataCorruption("renewal changed claim count")
            return self._claim_from_projection(
                command=command,
                state=state,
                ledger=updated,
            )

    def fence_dispatch(
        self,
        claim: CommandClaim,
        request: DispatchRequest,
        *,
        now: datetime,
    ) -> DispatchPermit:
        if type(claim) is not CommandClaim:
            raise TypeError("claim must be the exact CommandClaim type")
        if type(request) is not DispatchRequest:
            raise TypeError("request must be the exact DispatchRequest type")
        now = _require_utc_input(now, "now")
        with self._transaction("fence_dispatch"):
            command = self.load_command(claim.command.command_id)
            ledger = self.load_ledger(command.command_id)
            if ledger.dispatch_slots_consumed == 1:
                if (
                    ledger.claim_owner == claim.lease.owner
                    and ledger.fencing_token == claim.lease.fencing_token
                ):
                    raise DispatchAlreadyFenced(
                        "command already consumed its durable dispatch slot"
                    )
                raise StaleLease("dispatch slot belongs to another lease")
            command, _, _ = self._assert_live_preparation_claim(claim, now=now)
            expected = DispatchRequest.from_command(command, dumps_command(command))
            if request != expected:
                raise ValueError("dispatch request diverges from the authorized command")
            cursor = self._connection.execute(
                "UPDATE execution_ledger SET status='dispatch_fenced', "
                "dispatch_slots_consumed=1, dispatch_request_hash=?, "
                "dispatch_fenced_at=?, updated_at=? "
                "WHERE command_id=? AND status='preparing' AND claim_owner=? "
                "AND fencing_token=? AND lease_expires_at>? "
                "AND dispatch_slots_consumed=0",
                (
                    request.payload_hash,
                    now.isoformat(),
                    now.isoformat(),
                    command.command_id,
                    claim.lease.owner,
                    claim.lease.fencing_token,
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLease("dispatch fence lost its lease compare-and-swap")
            persisted = self.load_ledger(command.command_id)
            if (
                persisted.status is not LedgerStatus.DISPATCH_FENCED
                or persisted.dispatch_slots_consumed != 1
                or persisted.dispatch_request_hash != request.payload_hash
                or persisted.dispatch_fenced_at != now
            ):
                raise DataCorruption("dispatch fence projection is inconsistent")
            return DispatchPermit(
                command_id=command.command_id,
                lease=claim.lease,
                dispatch_slot=1,
                request_hash=request.payload_hash,
                fenced_at=now,
            )

    def release_preparation_failure(
        self,
        claim: CommandClaim,
        failure: PreparationFailure,
        *,
        now: datetime,
    ) -> PreparationDisposition:
        if type(claim) is not CommandClaim:
            raise TypeError("claim must be the exact CommandClaim type")
        if type(failure) is not PreparationFailure:
            raise TypeError("failure must be the exact PreparationFailure type")
        now = _require_utc_input(now, "now")
        with self._transaction("release_preparation_failure"):
            command, ledger, state = self._assert_live_preparation_claim(claim, now=now)
            failures = ledger.preparation_failures + 1
            if failures > _MAX_PREPARATION_FAILURES:
                raise DataCorruption("preparation failure budget was already exhausted")
            if failure.retryable and failures < _MAX_PREPARATION_FAILURES:
                cursor = self._connection.execute(
                    "UPDATE execution_ledger SET status='queued', claim_owner=NULL, "
                    "lease_acquired_at=NULL, lease_expires_at=NULL, "
                    "preparation_failures=?, updated_at=? "
                    "WHERE command_id=? AND status='preparing' AND claim_owner=? "
                    "AND fencing_token=? AND lease_expires_at>? "
                    "AND dispatch_slots_consumed=0",
                    (
                        failures,
                        now.isoformat(),
                        command.command_id,
                        claim.lease.owner,
                        claim.lease.fencing_token,
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleLease("preparation release lost its lease compare-and-swap")
                return PreparationDisposition.REQUEUED

            outcome = command.outcome(
                certainty=ExecutionCertainty.NOT_CALLED,
                normalized_status=failure.reason,
                evidence=failure.evidence,
            )
            event = ExecutionFinished(
                event_id=_derived_id(
                    "event",
                    "preparation_not_called",
                    command.command_id,
                ),
                occurred_at=now,
                command_id=command.command_id,
                outcome=outcome,
            )
            transition = reduce(state, event)
            if (
                transition.status is not TransitionStatus.APPLIED
                or type(transition.state) is not FailedBeforeProviderState
                or transition.commands
            ):
                raise DataCorruption(
                    "terminal preparation failure did not produce failed-before-provider"
                )
            raw_outcome = dumps_outcome(outcome)
            message = project_preparation_failure_outbox(
                command,
                outcome,
                created_at=now,
            )
            self._insert_event(
                command.workflow_id,
                event,
                transition.state.meta.revision,
            )
            self._update_state_compare_and_swap(state, transition.state)
            cursor = self._connection.execute(
                "UPDATE execution_ledger SET status='outcome_recorded', "
                "claim_owner=NULL, lease_acquired_at=NULL, lease_expires_at=NULL, "
                "preparation_failures=?, outcome_json=?, outcome_hash=?, updated_at=? "
                "WHERE command_id=? AND status='preparing' AND claim_owner=? "
                "AND fencing_token=? AND lease_expires_at>? "
                "AND dispatch_slots_consumed=0 AND outcome_json IS NULL",
                (
                    failures,
                    raw_outcome,
                    _sha256_text(raw_outcome),
                    now.isoformat(),
                    command.command_id,
                    claim.lease.owner,
                    claim.lease.fencing_token,
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLease("terminal preparation failure lost its lease compare-and-swap")
            self._insert_outbox(message)
            return PreparationDisposition.TERMINAL_NOT_CALLED

    @staticmethod
    def _terminal_state_matches_outcome(
        state: State,
        command: ReservationCommand,
        outcome: ExecutionOutcome,
    ) -> bool:
        expected_type = {
            ExecutionCertainty.EFFECT_CONFIRMED: SucceededState,
            ExecutionCertainty.CALLED_NO_EFFECT: FailedNoEffectState,
            ExecutionCertainty.CALLED_UNKNOWN: ManualReviewState,
            ExecutionCertainty.NOT_CALLED: FailedBeforeProviderState,
        }.get(outcome.certainty)
        return (
            expected_type is not None
            and type(state) is expected_type
            and state.command == command
            and state.outcome == outcome
            and state.meta.command_ids == (command.command_id,)
        )

    def _assert_live_dispatch_permit(
        self,
        permit: DispatchPermit,
        *,
        now: datetime,
    ) -> tuple[ReservationCommand, LedgerSnapshot, ExecutingState]:
        command = self.load_command(permit.command_id)
        ledger = self.load_ledger(command.command_id)
        state = self.load_workflow(command.workflow_id)
        expected_request = DispatchRequest.from_command(command, dumps_command(command))
        if (
            ledger.status is not LedgerStatus.DISPATCH_FENCED
            or ledger.outcome_json is not None
            or ledger.claim_owner != permit.lease.owner
            or ledger.fencing_token != permit.lease.fencing_token
            or ledger.lease_acquired_at != permit.lease.acquired_at
            or ledger.lease_expires_at != permit.lease.expires_at
            or ledger.dispatch_slots_consumed != permit.dispatch_slot
            or ledger.dispatch_request_hash != permit.request_hash
            or ledger.dispatch_fenced_at != permit.fenced_at
            or permit.request_hash != expected_request.payload_hash
            or ledger.updated_at > now
            or permit.fenced_at > now
            or ledger.lease_expires_at is None
            or now >= ledger.lease_expires_at
        ):
            raise StaleLease("dispatch permit is no longer current")
        if (
            type(state) is not ExecutingState
            or state.command != command
            or state.meta.command_ids != (command.command_id,)
        ):
            raise DataCorruption(
                "live dispatch permit requires exact executing workflow command"
            )
        return command, ledger, state

    def _resolve_duplicate_outcome(
        self,
        permit: DispatchPermit,
        outcome: ExecutionOutcome,
        ledger: LedgerSnapshot,
        *,
        now: datetime,
    ) -> PersistedTransition:
        if (
            permit.lease.fencing_token != ledger.fencing_token
            or permit.request_hash != ledger.dispatch_request_hash
            or permit.fenced_at != ledger.dispatch_fenced_at
            or ledger.dispatch_slots_consumed != 1
            or now < ledger.updated_at
        ):
            raise StaleLease("completed outcome belongs to another dispatch permit")
        raw_outcome = dumps_outcome(outcome)
        if (
            ledger.outcome_json != raw_outcome
            or ledger.outcome_hash != _sha256_text(raw_outcome)
        ):
            raise IdentityConflict("completed command already has a divergent outcome")
        command = self.load_command(permit.command_id)
        state = self.load_workflow(command.workflow_id)
        expected_status = (
            LedgerStatus.MANUAL_REVIEW
            if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN
            else LedgerStatus.OUTCOME_RECORDED
        )
        if (
            ledger.status is not expected_status
            or not self._terminal_state_matches_outcome(state, command, outcome)
        ):
            raise DataCorruption("completed outcome projection is inconsistent")
        message = project_outcome_outbox(
            command,
            outcome,
            created_at=ledger.updated_at,
        )
        if self.load_outbox(message.message_id) != message:
            raise DataCorruption("completed outcome outbox is inconsistent")
        return PersistedTransition(
            state=state,
            status=TransitionStatus.APPLIED,
            reason="execution_outcome_duplicate",
            commands=(),
            duplicate=True,
        )

    def record_outcome(
        self,
        permit: DispatchPermit,
        outcome: ExecutionOutcome,
        *,
        now: datetime,
    ) -> PersistedTransition:
        if type(permit) is not DispatchPermit:
            raise TypeError("permit must be the exact DispatchPermit type")
        if type(outcome) is not ExecutionOutcome:
            raise TypeError("outcome must be the exact ExecutionOutcome type")
        if outcome.command_id != permit.command_id:
            raise ValueError("outcome command does not match dispatch permit")
        if outcome.certainty is ExecutionCertainty.NOT_CALLED:
            raise ValueError("post-fence not_called outcome is forbidden")
        now = _require_utc_input(now, "now")
        with self._transaction("record_outcome"):
            existing = self.load_ledger(permit.command_id)
            if existing.outcome_json is not None:
                return self._resolve_duplicate_outcome(
                    permit,
                    outcome,
                    existing,
                    now=now,
                )
            command, ledger, state = self._assert_live_dispatch_permit(
                permit,
                now=now,
            )
            raw_outcome = dumps_outcome(outcome)
            outcome_hash = _sha256_text(raw_outcome)
            finished = ExecutionFinished(
                event_id=_derived_id(
                    "event",
                    "execution_outcome",
                    command.command_id,
                    outcome_hash,
                ),
                occurred_at=now,
                command_id=command.command_id,
                outcome=outcome,
            )
            finished_transition = reduce(state, finished)
            expected_first_type = {
                ExecutionCertainty.EFFECT_CONFIRMED: SucceededState,
                ExecutionCertainty.CALLED_NO_EFFECT: FailedNoEffectState,
                ExecutionCertainty.CALLED_UNKNOWN: UncertainState,
            }[outcome.certainty]
            if (
                finished_transition.status is not TransitionStatus.APPLIED
                or type(finished_transition.state) is not expected_first_type
                or finished_transition.commands
            ):
                raise DataCorruption("execution outcome did not produce its terminal state")
            final_transition = finished_transition
            events: list[tuple[Event, int]] = [
                (finished, finished_transition.state.meta.revision)
            ]
            if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN:
                review = ManualReviewRequested(
                    event_id=_derived_id(
                        "event",
                        "manual_review",
                        command.command_id,
                        outcome_hash,
                    ),
                    occurred_at=now,
                    reason="provider_effect_uncertain",
                )
                final_transition = reduce(finished_transition.state, review)
                if (
                    final_transition.status is not TransitionStatus.APPLIED
                    or type(final_transition.state) is not ManualReviewState
                    or final_transition.commands
                ):
                    raise DataCorruption(
                        "uncertain outcome did not produce mandatory manual review"
                    )
                events.append((review, final_transition.state.meta.revision))
            message = project_outcome_outbox(
                command,
                outcome,
                created_at=now,
            )
            for event, revision in events:
                self._insert_event(command.workflow_id, event, revision)
            self._update_state_compare_and_swap(state, final_transition.state)
            final_status = (
                LedgerStatus.MANUAL_REVIEW
                if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN
                else LedgerStatus.OUTCOME_RECORDED
            )
            cursor = self._connection.execute(
                "UPDATE execution_ledger SET status=?, claim_owner=NULL, "
                "lease_acquired_at=NULL, lease_expires_at=NULL, "
                "outcome_json=?, outcome_hash=?, updated_at=? "
                "WHERE command_id=? AND status='dispatch_fenced' "
                "AND claim_owner=? AND fencing_token=? "
                "AND lease_acquired_at=? AND lease_expires_at=? "
                "AND lease_expires_at>? AND dispatch_slots_consumed=1 "
                "AND dispatch_request_hash=? AND dispatch_fenced_at=? "
                "AND outcome_json IS NULL AND outcome_hash IS NULL",
                (
                    final_status.value,
                    raw_outcome,
                    outcome_hash,
                    now.isoformat(),
                    command.command_id,
                    permit.lease.owner,
                    permit.lease.fencing_token,
                    permit.lease.acquired_at.isoformat(),
                    permit.lease.expires_at.isoformat(),
                    now.isoformat(),
                    permit.request_hash,
                    permit.fenced_at.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLease("record outcome lost its dispatch permit compare-and-swap")
            self._insert_outbox(message)
            persisted = self.load_ledger(command.command_id)
            if (
                persisted.status is not final_status
                or persisted.outcome_json != raw_outcome
                or persisted.outcome_hash != outcome_hash
                or persisted.claim_owner is not None
                or persisted.lease_acquired_at is not None
                or persisted.lease_expires_at is not None
            ):
                raise DataCorruption("persisted outcome ledger is inconsistent")
            return PersistedTransition.from_domain(final_transition)

    def _insert_immutable_command(self, command: ReservationCommand) -> None:
        raw = dumps_command(command)
        existing = self._connection.execute(
            "SELECT command_id FROM reservation_commands "
            "WHERE command_id=? OR idempotency_key=? OR workflow_id=?",
            (command.command_id, command.idempotency_key, command.workflow_id),
        ).fetchone()
        if existing is not None:
            raise IdentityConflict("authorized command identity already exists")
        self._connection.execute(
            "INSERT INTO reservation_commands "
            "(command_id, idempotency_key, workflow_id, draft_id, draft_version, "
            "subject_signature, operation, command_json, command_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                command.command_id,
                command.idempotency_key,
                command.workflow_id,
                command.draft_id,
                command.draft_version,
                command.subject_signature,
                command.operation.value,
                raw,
                _sha256_text(raw),
                command.created_at.isoformat(),
            ),
        )

    def _insert_initial_ledger(self, command: ReservationCommand) -> None:
        if self._ledger_row(command.command_id) is not None:
            raise IdentityConflict("authorized command already has an execution ledger")
        now = command.created_at.isoformat()
        self._connection.execute(
            "INSERT INTO execution_ledger "
            "(command_id, status, claim_owner, fencing_token, lease_acquired_at, "
            "lease_expires_at, claim_count, preparation_failures, "
            "dispatch_slots_consumed, dispatch_request_hash, dispatch_fenced_at, "
            "outcome_json, outcome_hash, updated_at) "
            "VALUES (?, ?, NULL, 0, NULL, NULL, 0, 0, 0, NULL, NULL, NULL, NULL, ?)",
            (command.command_id, LedgerStatus.QUEUED.value, now),
        )

    def _insert_outbox(self, message: OutboxMessage) -> None:
        existing = self._connection.execute(
            "SELECT message_id FROM outbox_messages "
            "WHERE message_id=? OR idempotency_key=?",
            (message.message_id, message.idempotency_key),
        ).fetchone()
        if existing is not None:
            raise IdentityConflict("outbox identity already exists")
        instant = message.created_at.isoformat()
        self._connection.execute(
            "INSERT INTO outbox_messages "
            "(message_id, idempotency_key, workflow_id, command_id, kind, "
            "template_id, payload_json, payload_hash, status, claim_owner, "
            "fencing_token, lease_acquired_at, lease_expires_at, "
            "delivery_attempts, delivered_at, receipt_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL, NULL, 0, NULL, "
            "NULL, ?, ?)",
            (
                message.message_id,
                message.idempotency_key,
                message.workflow_id,
                message.command_id,
                message.kind.value,
                message.template_id,
                message.canonical_payload,
                message.payload_hash,
                OutboxStatus.PENDING.value,
                instant,
                instant,
            ),
        )

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
    "StaleLease",
    "DispatchAlreadyFenced",
    "WorkflowNotFound",
    "CommandNotFound",
    "OutboxNotFound",
]
