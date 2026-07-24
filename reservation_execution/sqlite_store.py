"""Durable SQLite workflow/event persistence with optimistic revision control."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterator

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
from .schema import (
    PHASE5_V6_TABLES,
    SCHEMA_VERSION,
    SCHEMA_VERSION_V6,
    render_sqlite,
    render_sqlite_v6,
    schema_contract,
    schema_hash,
    schema_hash_v6,
)
from .types import (
    CommandClaim,
    DeliveryReceipt,
    DispatchPermit,
    DispatchRequest,
    Lease,
    LedgerStatus,
    OutboxClaim,
    OutboxKind,
    OutboxMessage,
    OutboxSnapshot,
    OutboxStatus,
    PreparationDisposition,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_EXPECTED_TABLES = tuple(table.name for table in schema_contract())
_EXPECTED_TABLES_V6 = PHASE5_V6_TABLES
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


def _schema_statements(
    sql: str,
    *,
    expected_count: int = len(_EXPECTED_TABLES),
) -> tuple[str, ...]:
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
    if len(statements) != expected_count:
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
        _schema_version: int = SCHEMA_VERSION,
    ):
        if _factory_token is not _FACTORY_TOKEN:
            raise TypeError("SQLiteUnitOfWork must be created with open()")
        if _schema_version not in (SCHEMA_VERSION, SCHEMA_VERSION_V6):
            raise ValueError("unsupported execution schema version")
        self._path = path
        self._connection = connection
        self._schema_version = _schema_version
        self._phase8_reservation_fault_hook: Callable[[str], None] | None = None
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

    @classmethod
    def open_v6(cls, path: Path) -> "SQLiteUnitOfWork":
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
            store = cls(
                path,
                connection,
                _factory_token=_FACTORY_TOKEN,
                _schema_version=SCHEMA_VERSION_V6,
            )
            store._initialize_or_validate_schema_v6()
            return store
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise _sqlite_store_error(exc, "open_v6") from exc
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

    def _initialize_or_validate_schema_v6(self) -> None:
        names = self._table_names()
        if not names:
            with self._transaction("initialize_schema_v6"):
                for statement in _schema_statements(
                    render_sqlite_v6(),
                    expected_count=len(_EXPECTED_TABLES_V6),
                ):
                    self._connection.execute(statement)
                self._connection.execute(
                    "INSERT INTO schema_migrations "
                    "(version, schema_hash, applied_at) VALUES (?, ?, ?)",
                    (
                        SCHEMA_VERSION_V6,
                        schema_hash_v6(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            names = self._table_names()
        if names != _EXPECTED_TABLES_V6:
            raise DataCorruption(
                "SQLite v6 table universe mismatch: "
                f"expected={_EXPECTED_TABLES_V6}, found={names}"
            )
        expected_statements = _schema_statements(
            render_sqlite_v6(),
            expected_count=len(_EXPECTED_TABLES_V6),
        )
        expected_sql = {
            name: statement.removesuffix(";")
            for name, statement in zip(
                _EXPECTED_TABLES_V6,
                expected_statements,
                strict=True,
            )
        }
        actual_rows = tuple(
            self._connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        )
        if tuple(name for name, _ in actual_rows) != _EXPECTED_TABLES_V6:
            raise DataCorruption("SQLite v6 table order is divergent")
        for name, actual_sql in actual_rows:
            if actual_sql != expected_sql[name]:
                raise DataCorruption(f"SQLite v6 table definition drift: {name}")
        explicit_objects = tuple(
            self._connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE type != 'table' AND sql IS NOT NULL ORDER BY type, name"
            )
        )
        if explicit_objects:
            raise DataCorruption(
                f"SQLite v6 schema has unexpected objects: {explicit_objects}"
            )
        temporary_objects = tuple(
            self._connection.execute(
                "SELECT type, name FROM sqlite_temp_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        )
        if temporary_objects:
            raise DataCorruption("SQLite v6 TEMP schema is not empty")
        migrations = tuple(
            self._connection.execute(
                "SELECT version, schema_hash FROM schema_migrations ORDER BY version"
            )
        )
        expected_migrations = ((SCHEMA_VERSION_V6, schema_hash_v6()),)
        if migrations != expected_migrations:
            raise DataCorruption(
                "SQLite v6 migration identity mismatch: "
                f"expected={expected_migrations}, found={migrations}"
            )
        if tuple(self._connection.execute("PRAGMA foreign_key_check")):
            raise DataCorruption("SQLite v6 schema contains foreign key violations")

    def install_e2e_reservation_allocations(
        self,
        *,
        operation_id: str,
        manifest: object,
        installed_at: datetime,
    ):
        from reservation_boundary.authority import (
            AllocationInstallationReceipt,
            EffectAllocationRow,
            ExactEffectAllocationManifest,
            InstallationHeaderState,
            InstallationStatus,
            InstallationTarget,
        )

        self._ensure_open()
        if self._schema_version != SCHEMA_VERSION_V6:
            raise DataCorruption("reservation authority install requires Phase 5 v6")
        if type(manifest) is not ExactEffectAllocationManifest:
            raise TypeError("manifest must be exact ExactEffectAllocationManifest")
        rows = tuple(
            row
            for row in manifest.rows
            if row.installation_target
            is InstallationTarget.RESERVATION_E2E_EFFECT_AUTHORITY
        )
        if not rows or any(type(row) is not EffectAllocationRow for row in rows):
            raise DataCorruption("reservation target manifest is empty or invalid")
        generation_ids = tuple(sorted({row.generation_id for row in rows}))
        row_hashes = tuple(row.canonical_hash() for row in rows)
        aggregate_hash = hashlib.sha256(
            b"phase8-installed-allocation-aggregate-v1\0"
            + b"".join(bytes.fromhex(value) for value in row_hashes)
        ).hexdigest()

        def make_receipt(timestamp: datetime) -> AllocationInstallationReceipt:
            return AllocationInstallationReceipt(
                operation_id=operation_id,
                installation_target=(
                    InstallationTarget.RESERVATION_E2E_EFFECT_AUTHORITY
                ),
                qualification_id=manifest.qualification_id,
                epoch=manifest.epoch,
                contract_hash=manifest.contract_hash,
                effect_authorization_binding_hash=(
                    manifest.effect_authorization_binding_hash
                ),
                manifest_hash=manifest.canonical_hash(),
                generation_ids=generation_ids,
                installed_row_hashes=row_hashes,
                allocation_count=len(rows),
                installed_allocation_aggregate_hash=aggregate_hash,
                header_state=InstallationHeaderState.OPEN,
                status=InstallationStatus.INSTALLED,
                installed_at=timestamp,
            )

        candidate = make_receipt(installed_at)
        with self._transaction("install_e2e_reservation_allocations"):
            existing_headers = self._connection.execute(
                "SELECT generation_id, state, installation_operation_id, "
                "installation_receipt_json, installation_receipt_hash, installed_at, "
                "manifest_hash FROM reservation_e2e_effect_authority "
                "WHERE qualification_id=? AND epoch=? AND row_kind='generation_header' "
                "ORDER BY generation_id",
                (manifest.qualification_id, manifest.epoch),
            ).fetchall()
            if existing_headers:
                if (
                    len(existing_headers) != len(generation_ids)
                    or tuple(row[0] for row in existing_headers) != generation_ids
                    or any(row[1] != "open" for row in existing_headers)
                ):
                    raise DataCorruption("reservation generation is closed or divergent")
                stored_at = datetime.fromisoformat(existing_headers[0][5])
                stored = make_receipt(stored_at)
                if any(
                    row[2] != operation_id
                    or row[3] != stored.to_canonical_bytes().decode("utf-8")
                    or row[4] != stored.canonical_hash()
                    or row[5] != stored_at.isoformat()
                    or row[6] != manifest.canonical_hash()
                    for row in existing_headers
                ):
                    raise DataCorruption("persisted reservation installation diverges")
                persisted_allocations = self._connection.execute(
                    "SELECT allocation_id, allocation_hash, state FROM "
                    "reservation_e2e_effect_authority WHERE qualification_id=? "
                    "AND epoch=? AND row_kind='allocation' ORDER BY allocation_ordinal, "
                    "allocation_id",
                    (manifest.qualification_id, manifest.epoch),
                ).fetchall()
                expected_allocations = [
                    (row.allocation_id, row.canonical_hash(), "available") for row in rows
                ]
                if persisted_allocations != expected_allocations:
                    raise DataCorruption("persisted reservation allocation set diverges")
                return stored
            partial = self._connection.execute(
                "SELECT count(*) FROM reservation_e2e_effect_authority "
                "WHERE qualification_id=? AND epoch=?",
                (manifest.qualification_id, manifest.epoch),
            ).fetchone()[0]
            if partial:
                raise DataCorruption("partial reservation authority installation exists")

            receipt_json = candidate.to_canonical_bytes().decode("utf-8")
            receipt_hash = candidate.canonical_hash()
            installed_text = installed_at.isoformat()
            for scenario_id, generation_id in sorted(
                {(row.scenario_id, row.generation_id) for row in rows}
            ):
                self._connection.execute(
                    "INSERT INTO reservation_e2e_effect_authority "
                    "(row_kind, installation_target, qualification_id, epoch, scenario_id, "
                    "contract_hash, effect_authorization_binding_hash, manifest_hash, "
                    "generation_id, allocation_id, allocation_ordinal, allocation_hash, "
                    "effect_family, effect_kind, effect_role, effect_scope_hash, "
                    "workflow_scope_hash, channel_scope_hash, target_binding_hash, "
                    "message_ordinal, activation_parent_kind, activation_parent_id, "
                    "activation_parent_hash, state, bound_subject_id, bound_subject_hash, "
                    "child_decision_receipt_json, child_decision_receipt_hash, revision, "
                    "installation_operation_id, installation_receipt_json, "
                    "installation_receipt_hash, installed_allocation_aggregate_hash, "
                    "installed_at, closed_at) VALUES "
                    "('generation_header', 'reservation_e2e_effect_authority', ?, ?, ?, "
                    "?, ?, ?, ?, '__header__', NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
                    "NULL, NULL, NULL, NULL, NULL, NULL, 'open', NULL, NULL, NULL, NULL, 0, "
                    "?, ?, ?, ?, ?, NULL)",
                    (
                        manifest.qualification_id,
                        manifest.epoch,
                        scenario_id,
                        manifest.contract_hash,
                        manifest.effect_authorization_binding_hash,
                        manifest.canonical_hash(),
                        generation_id,
                        operation_id,
                        receipt_json,
                        receipt_hash,
                        aggregate_hash,
                        installed_text,
                    ),
                )
            for row in rows:
                self._connection.execute(
                    "INSERT INTO reservation_e2e_effect_authority "
                    "(row_kind, installation_target, qualification_id, epoch, scenario_id, "
                    "contract_hash, effect_authorization_binding_hash, manifest_hash, "
                    "generation_id, allocation_id, allocation_ordinal, allocation_hash, "
                    "effect_family, effect_kind, effect_role, effect_scope_hash, "
                    "workflow_scope_hash, channel_scope_hash, target_binding_hash, "
                    "message_ordinal, activation_parent_kind, activation_parent_id, "
                    "activation_parent_hash, state, bound_subject_id, bound_subject_hash, "
                    "child_decision_receipt_json, child_decision_receipt_hash, revision, "
                    "installation_operation_id, installation_receipt_json, "
                    "installation_receipt_hash, installed_allocation_aggregate_hash, "
                    "installed_at, closed_at) VALUES "
                    "('allocation', 'reservation_e2e_effect_authority', ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', NULL, NULL, "
                    "NULL, NULL, 0, NULL, NULL, NULL, NULL, ?, NULL)",
                    (
                        row.qualification_id,
                        row.epoch,
                        row.scenario_id,
                        row.contract_hash,
                        row.effect_authorization_binding_hash,
                        manifest.canonical_hash(),
                        row.generation_id,
                        row.allocation_id,
                        row.allocation_ordinal,
                        row.canonical_hash(),
                        row.effect_family.value,
                        row.effect_kind.value,
                        row.effect_role.value,
                        row.effect_scope_hash,
                        row.workflow_scope_hash,
                        row.channel_scope_hash,
                        row.target_binding_hash,
                        row.message_ordinal,
                        row.activation_parent_kind.value,
                        row.activation_parent_id,
                        row.activation_parent_hash,
                        installed_text,
                    ),
                )
            return candidate

    def close_e2e_reservation_generation(
        self,
        *,
        qualification_id: str,
        epoch: int,
        scenario_id: str,
        generation_id: str,
        contract_hash: str,
        effect_authorization_binding_hash: str,
        manifest_hash: str,
        closed_at: datetime,
    ) -> None:
        self._ensure_open()
        if self._schema_version != SCHEMA_VERSION_V6:
            raise DataCorruption("reservation generation close requires Phase 5 v6")
        with self._transaction("close_e2e_reservation_generation"):
            existing = self._connection.execute(
                "SELECT state FROM reservation_e2e_effect_authority WHERE "
                "qualification_id=? AND epoch=? AND scenario_id=? AND generation_id=? "
                "AND allocation_id='__header__'",
                (qualification_id, epoch, scenario_id, generation_id),
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    "INSERT INTO reservation_e2e_effect_authority "
                    "(row_kind, installation_target, qualification_id, epoch, scenario_id, "
                    "contract_hash, effect_authorization_binding_hash, manifest_hash, "
                    "generation_id, allocation_id, allocation_ordinal, allocation_hash, "
                    "effect_family, effect_kind, effect_role, effect_scope_hash, "
                    "workflow_scope_hash, channel_scope_hash, target_binding_hash, "
                    "message_ordinal, activation_parent_kind, activation_parent_id, "
                    "activation_parent_hash, state, bound_subject_id, bound_subject_hash, "
                    "child_decision_receipt_json, child_decision_receipt_hash, revision, "
                    "installation_operation_id, installation_receipt_json, "
                    "installation_receipt_hash, installed_allocation_aggregate_hash, "
                    "installed_at, closed_at) VALUES "
                    "('generation_header', 'reservation_e2e_effect_authority', ?, ?, ?, ?, "
                    "?, ?, ?, '__header__', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
                    "NULL, NULL, NULL, NULL, NULL, 'closed', NULL, NULL, NULL, NULL, 0, NULL, "
                    "NULL, NULL, NULL, ?, ?)",
                    (
                        qualification_id,
                        epoch,
                        scenario_id,
                        contract_hash,
                        effect_authorization_binding_hash,
                        manifest_hash,
                        generation_id,
                        closed_at.isoformat(),
                        closed_at.isoformat(),
                    ),
                )
                return
            if existing[0] == "closed":
                return
            raise DataCorruption("installed reservation generation requires dependency close")

    def accept_boundary_reservation(
        self,
        *,
        operation_id: str,
        source_turn_receipt_hash: str,
        bundle: object,
    ):
        return self._accept_boundary_reservation(
            operation_id=operation_id,
            source_turn_receipt_hash=source_turn_receipt_hash,
            bundle=bundle,
            committed_at=datetime.now(timezone.utc),
            fault_hook=self._phase8_reservation_fault_hook,
        )

    def _accept_boundary_reservation(
        self,
        *,
        operation_id: str,
        source_turn_receipt_hash: str,
        bundle: object,
        committed_at: datetime,
        fault_hook: Callable[[str], None] | None,
    ):
        from reservation_boundary.effects import (
            InternalJobKind,
            ReservationRelayBundle,
            TargetOperationReceipt,
            phase5_outbox_from_seed_bytes,
            target_operation_id,
        )

        self._ensure_open()
        if self._schema_version != SCHEMA_VERSION_V6:
            raise DataCorruption("reservation ingress requires the exact Phase 5 v6 root")
        if type(bundle) is not ReservationRelayBundle:
            raise TypeError("bundle must be the exact ReservationRelayBundle type")
        expected_operation_id = target_operation_id(
            InternalJobKind.HANDOFF,
            bundle.artifact_hash,
            source_turn_receipt_hash,
        )
        if operation_id != expected_operation_id:
            raise ValueError("operation_id does not match the reservation relay tuple")
        if fault_hook is not None and not callable(fault_hook):
            raise TypeError("reservation ingress fault hook must be callable or None")

        try:
            genesis_text = bundle.genesis_state.decode("utf-8")
            genesis = loads_state(genesis_text)
            if dumps_state(genesis).encode("utf-8") != bundle.genesis_state:
                raise ValueError("genesis state bytes are noncanonical")
            events = tuple(
                loads_event(payload.decode("utf-8"))
                for payload in bundle.phase5_events
            )
            if any(
                dumps_event(event).encode("utf-8") != payload
                for event, payload in zip(events, bundle.phase5_events)
            ):
                raise ValueError("Phase 5 event bytes are noncanonical")
            outboxes = tuple(
                phase5_outbox_from_seed_bytes(payload)
                for payload in bundle.summary_outboxes
            )
            command = loads_command(bundle.command_ledger_seed.decode("utf-8"))
            commands = (command,)
            if (
                dumps_command(command).encode("utf-8")
                != bundle.command_ledger_seed
            ):
                raise ValueError("command ledger seed bytes are noncanonical")
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            raise DataCorruption("reservation relay contains invalid Phase 5 bytes") from exc
        if (
            genesis.meta.revision != 0
            or genesis.meta.seen_event_ids
            or genesis.meta.seen_event_hashes
            or genesis.meta.command_ids
        ):
            raise DataCorruption("reservation relay genesis is not revision zero")

        state = genesis
        outbox_index = 0
        command_index = 0
        steps: list[tuple[State, Event, Transition, tuple[OutboxMessage, ...]]] = []
        for event in events:
            current = state
            if type(event) is SummaryRecorded:
                if outbox_index >= len(outboxes):
                    raise DataCorruption("summary event is missing its outbox seed")
                event_outbox = (outboxes[outbox_index],)
                outbox_index += 1
                try:
                    validate_summary_outbox(current, event, event_outbox[0])
                except (TypeError, ValueError) as exc:
                    raise DataCorruption("summary outbox seed is invalid") from exc
            else:
                event_outbox = ()
            transition = reduce(current, event)
            if transition.state.meta.revision != current.meta.revision + 1:
                raise DataCorruption("reservation relay event did not advance one revision")
            for command in transition.commands:
                if command_index >= len(commands) or commands[command_index] != command:
                    raise DataCorruption("reservation command seed diverges from replay")
                command_index += 1
            steps.append((current, event, transition, event_outbox))
            state = transition.state
        if outbox_index != len(outboxes) or command_index != len(commands):
            raise DataCorruption("reservation relay contains unowned target seeds")

        final_json = dumps_state(state)
        target_result_hash = _sha256_text(final_json)
        if (
            final_json.encode("utf-8") != bundle.expected_final_state
            or target_result_hash != bundle.expected_final_state_hash
        ):
            raise DataCorruption("reservation relay final state mismatch")
        bundle_json = bundle.to_canonical_bytes().decode("utf-8")
        commit_preimage = json.dumps(
            {
                "artifact_hash": bundle.artifact_hash,
                "operation_id": operation_id,
                "target_result_hash": target_result_hash,
                "workflow_id": state.meta.workflow_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        target_commit_hash = hashlib.sha256(
            b"phase8-reservation-target-commit-v1\0" + commit_preimage
        ).hexdigest()
        receipt = TargetOperationReceipt(
            operation_id=operation_id,
            job_kind=InternalJobKind.HANDOFF,
            artifact_hash=bundle.artifact_hash,
            source_turn_receipt_hash=source_turn_receipt_hash,
            target_commit_hash=target_commit_hash,
            target_result_hash=target_result_hash,
            committed_at=committed_at,
        )
        receipt_json = receipt.to_canonical_bytes().decode("utf-8")
        receipt_hash = receipt.canonical_hash()
        committed_at_text = receipt.committed_at.isoformat()

        def trip(stage: str) -> None:
            if fault_hook is not None:
                fault_hook(stage)

        authority_receipt = (None, None, None, None, None, None)
        with self._transaction("accept_boundary_reservation"):
            existing = self._connection.execute(
                "SELECT source_turn_receipt_hash, artifact_hash, bundle_json, "
                "target_commit_hash, target_result_hash, receipt_json, receipt_hash, "
                "qualification_id, epoch, scenario_id, generation_id, allocation_id, "
                "authority_row_hash, committed_at FROM "
                "reservation_boundary_ingress_receipts WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                try:
                    stored = TargetOperationReceipt.from_canonical_bytes(
                        existing[5].encode("utf-8")
                    )
                except (AttributeError, TypeError, ValueError) as exc:
                    raise DataCorruption("persisted reservation receipt is invalid") from exc
                authority_values = existing[7:13]
                if bundle.qualification_id is None:
                    if authority_values != (None, None, None, None, None, None):
                        raise DataCorruption("non-E2E reservation receipt has authority tuple")
                else:
                    if authority_values[:3] != (
                        bundle.qualification_id,
                        bundle.immutable_generation,
                        bundle.scenario_id,
                    ) or authority_values[4] != bundle.allocation_id:
                        raise DataCorruption("reservation receipt authority tuple diverges")
                    bound_row = self._connection.execute(
                        "SELECT generation_id, allocation_hash, state, bound_subject_id "
                        "FROM reservation_e2e_effect_authority WHERE qualification_id=? "
                        "AND epoch=? AND scenario_id=? AND allocation_id=?",
                        (
                            bundle.qualification_id,
                            bundle.immutable_generation,
                            bundle.scenario_id,
                            bundle.allocation_id,
                        ),
                    ).fetchone()
                    if (
                        bound_row is None
                        or bound_row[0] != authority_values[3]
                        or bound_row[1] != authority_values[5]
                        or bound_row[2:] != ("bound", commands[0].command_id)
                    ):
                        raise DataCorruption("bound reservation authority row diverges")
                expected_row = (
                    stored.source_turn_receipt_hash,
                    stored.artifact_hash,
                    bundle_json,
                    stored.target_commit_hash,
                    stored.target_result_hash,
                    stored.to_canonical_bytes().decode("utf-8"),
                    stored.canonical_hash(),
                    *authority_values,
                    stored.committed_at.isoformat(),
                )
                if (
                    existing != expected_row
                    or stored.operation_id != operation_id
                    or stored.job_kind is not InternalJobKind.HANDOFF
                    or stored.source_turn_receipt_hash != source_turn_receipt_hash
                    or stored.artifact_hash != bundle.artifact_hash
                    or stored.target_commit_hash != target_commit_hash
                    or stored.target_result_hash != target_result_hash
                ):
                    raise DataCorruption("persisted reservation receipt diverges")
                return stored
            if self._workflow_row(genesis.meta.workflow_id) is not None:
                raise IdentityConflict("reservation relay workflow identity already exists")

            if bundle.qualification_id is not None:
                command = commands[0]
                target_binding_hash = hashlib.sha256(
                    b"phase8-authority-target-binding-v1\0"
                    + command.command_id.encode("utf-8")
                ).hexdigest()
                authority = self._connection.execute(
                    "SELECT generation_id, state, effect_family, target_binding_hash, "
                    "allocation_hash, revision FROM reservation_e2e_effect_authority "
                    "WHERE qualification_id=? AND epoch=? AND scenario_id=? "
                    "AND allocation_id=? AND row_kind='allocation'",
                    (
                        bundle.qualification_id,
                        bundle.immutable_generation,
                        bundle.scenario_id,
                        bundle.allocation_id,
                    ),
                ).fetchone()
                if (
                    authority is None
                    or authority[1] != "available"
                    or authority[2] != "reservation"
                    or authority[3] != target_binding_hash
                ):
                    raise DataCorruption("reservation E2E allocation is absent or unavailable")
                header = self._connection.execute(
                    "SELECT state FROM reservation_e2e_effect_authority WHERE "
                    "qualification_id=? AND epoch=? AND scenario_id=? AND generation_id=? "
                    "AND allocation_id='__header__'",
                    (
                        bundle.qualification_id,
                        bundle.immutable_generation,
                        bundle.scenario_id,
                        authority[0],
                    ),
                ).fetchone()
                if header != ("open",):
                    raise DataCorruption("reservation E2E generation is not open")
                authority_receipt = (
                    bundle.qualification_id,
                    bundle.immutable_generation,
                    bundle.scenario_id,
                    authority[0],
                    bundle.allocation_id,
                    authority[4],
                )
                bound = self._connection.execute(
                    "UPDATE reservation_e2e_effect_authority SET state='bound', "
                    "bound_subject_id=?, bound_subject_hash=?, revision=revision+1 "
                    "WHERE qualification_id=? AND epoch=? AND scenario_id=? "
                    "AND allocation_id=? AND state='available' AND revision=?",
                    (
                        command.command_id,
                        _sha256_text(dumps_command(command)),
                        bundle.qualification_id,
                        bundle.immutable_generation,
                        bundle.scenario_id,
                        bundle.allocation_id,
                        authority[5],
                    ),
                )
                if bound.rowcount != 1:
                    raise ConcurrencyConflict("reservation E2E allocation bind CAS was lost")

            trip("before_domain")
            genesis_json = dumps_state(genesis)
            genesis_hash = _sha256_text(genesis_json)
            created_at = genesis.meta.last_event_at.isoformat()
            self._connection.execute(
                "INSERT INTO workflows "
                "(workflow_id, revision, state_type, state_json, state_hash, "
                "created_at, updated_at) VALUES (?, 0, ?, ?, ?, ?, ?)",
                (
                    genesis.meta.workflow_id,
                    genesis.TYPE,
                    genesis_json,
                    genesis_hash,
                    created_at,
                    created_at,
                ),
            )
            for current, event, transition, event_outbox in steps:
                self._insert_event(
                    genesis.meta.workflow_id,
                    event,
                    transition.state.meta.revision,
                )
                self._update_state_compare_and_swap(current, transition.state)
                for command in transition.commands:
                    self._insert_immutable_command(command)
                    self._insert_initial_ledger(command)
                for message in event_outbox:
                    self._insert_outbox(message)
            trip("after_domain_before_receipt")
            self._connection.execute(
                "INSERT INTO reservation_boundary_ingress_receipts "
                "(operation_id, source_turn_receipt_hash, artifact_hash, bundle_json, "
                "target_commit_hash, target_result_hash, receipt_json, receipt_hash, "
                "qualification_id, epoch, scenario_id, generation_id, allocation_id, "
                "authority_row_hash, committed_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    source_turn_receipt_hash,
                    bundle.artifact_hash,
                    bundle_json,
                    target_commit_hash,
                    target_result_hash,
                    receipt_json,
                    receipt_hash,
                    *authority_receipt,
                    committed_at_text,
                ),
            )
            trip("after_receipt_before_commit")
            return receipt

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
        if snapshot.status in (
            LedgerStatus.PREPARING,
            LedgerStatus.DISPATCH_FENCED,
        ):
            if (
                snapshot.lease_acquired_at is None
                or snapshot.lease_expires_at is None
                or not (
                    snapshot.lease_acquired_at
                    <= snapshot.updated_at
                    < snapshot.lease_expires_at
                )
            ):
                raise DataCorruption("active execution lease timeline is impossible")
        if snapshot.status is LedgerStatus.DISPATCH_FENCED and (
            snapshot.dispatch_fenced_at is None
            or snapshot.dispatch_fenced_at != snapshot.updated_at
            or snapshot.lease_acquired_at is None
            or snapshot.dispatch_fenced_at < snapshot.lease_acquired_at
        ):
            raise DataCorruption("dispatch fence timeline is impossible")
        if (
            snapshot.dispatch_fenced_at is not None
            and snapshot.dispatch_fenced_at > snapshot.updated_at
        ):
            raise DataCorruption("dispatch fence timestamp exceeds ledger update")
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

    def _outbox_snapshot_from_row(self, row) -> OutboxSnapshot:
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
            if row[9] is None:
                lease = None
            else:
                owner = _require_id(row[9], "outbox.claim_owner")
                if owner != row[9]:
                    raise ValueError("outbox claim owner is noncanonical")
                lease = Lease(
                    owner=owner,
                    fencing_token=row[10],
                    acquired_at=_canonical_utc(
                        row[11],
                        "outbox.lease_acquired_at",
                    ),
                    expires_at=_canonical_utc(
                        row[12],
                        "outbox.lease_expires_at",
                    ),
                )
            snapshot = OutboxSnapshot(
                message=message,
                status=status,
                lease=lease,
                fencing_token=row[10],
                delivery_attempts=row[13],
                delivered_at=(
                    None
                    if row[14] is None
                    else _canonical_utc(row[14], "outbox.delivered_at")
                ),
                receipt_hash=row[15],
                updated_at=_canonical_utc(row[17], "outbox.updated_at"),
            )
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
        if snapshot.status is OutboxStatus.LEASED and (
            snapshot.lease is None
            or snapshot.lease.acquired_at != snapshot.updated_at
            or snapshot.updated_at >= snapshot.lease.expires_at
        ):
            raise DataCorruption("leased outbox timeline is invalid")
        if message.command_id is not None:
            command = self.load_command(message.command_id)
            if command.workflow_id != message.workflow_id:
                raise DataCorruption("outbox command belongs to another workflow")
        return snapshot

    def _outbox_from_row(self, row) -> OutboxMessage:
        return self._outbox_snapshot_from_row(row).message

    def load_outbox_snapshot(self, message_id: str) -> OutboxSnapshot:
        self._ensure_open()
        message_id = _require_id(message_id, "message_id")
        try:
            row = self._outbox_row(message_id)
            if row is None:
                raise OutboxNotFound(f"outbox message not found: {message_id}")
            snapshot = self._outbox_snapshot_from_row(row)
            if snapshot.message.kind is OutboxKind.SUMMARY_PRESENTED:
                self._verify_summary_outbox_projection(snapshot.message)
            else:
                self._verify_execution_outbox_projection(snapshot.message)
            return snapshot
        except sqlite3.Error as exc:
            raise _sqlite_store_error(exc, "load_outbox_snapshot") from exc

    def load_outbox(self, message_id: str) -> OutboxMessage:
        return self.load_outbox_snapshot(message_id).message

    @staticmethod
    def _assert_live_outbox_claim(
        claim: OutboxClaim,
        snapshot: OutboxSnapshot,
        *,
        now: datetime,
    ) -> None:
        if (
            snapshot.status is not OutboxStatus.LEASED
            or snapshot.message != claim.message
            or snapshot.lease != claim.lease
            or snapshot.fencing_token != claim.lease.fencing_token
            or snapshot.delivery_attempts != claim.delivery_attempts
            or snapshot.updated_at != claim.lease.acquired_at
            or now < snapshot.updated_at
            or now >= claim.lease.expires_at
        ):
            raise StaleLease("outbox claim is stale, expired, or divergent")

    def claim_outbox(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> OutboxClaim | None:
        worker_id = _require_id(worker_id, "worker_id")
        now = _require_utc_input(now, "now")
        lease_ttl = _require_lease_ttl(lease_ttl)
        try:
            expires_at = now + lease_ttl
        except OverflowError as exc:
            raise ValueError("lease_ttl overflows datetime range") from exc
        with self._transaction("claim_outbox"):
            candidate = self._connection.execute(
                "SELECT message_id FROM outbox_messages "
                "WHERE status IN ('pending', 'leased') "
                "AND (status='pending' OR lease_expires_at<=?) "
                "ORDER BY created_at, message_id LIMIT 1",
                (now.isoformat(),),
            ).fetchone()
            if candidate is None:
                return None
            snapshot = self.load_outbox_snapshot(candidate[0])
            if now < snapshot.message.created_at or now < snapshot.updated_at:
                raise ValueError("outbox claim time cannot predate durable message state")
            if snapshot.status is OutboxStatus.PENDING:
                predicate = (
                    "status='pending' AND claim_owner IS NULL "
                    "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL"
                )
                predicate_values: tuple[object, ...] = ()
            elif (
                snapshot.status is OutboxStatus.LEASED
                and snapshot.lease is not None
                and snapshot.lease.expires_at <= now
            ):
                predicate = (
                    "status='leased' AND claim_owner=? "
                    "AND lease_acquired_at=? AND lease_expires_at=?"
                )
                predicate_values = (
                    snapshot.lease.owner,
                    snapshot.lease.acquired_at.isoformat(),
                    snapshot.lease.expires_at.isoformat(),
                )
            else:
                raise DataCorruption("eligible outbox row has an impossible lease state")
            token = snapshot.fencing_token + 1
            attempts = snapshot.delivery_attempts + 1
            if token != attempts:
                raise DataCorruption("outbox token and delivery attempts diverged")
            cursor = self._connection.execute(
                "UPDATE outbox_messages SET status='leased', claim_owner=?, "
                "fencing_token=?, lease_acquired_at=?, lease_expires_at=?, "
                "delivery_attempts=?, updated_at=? WHERE message_id=? AND "
                + predicate
                + " AND fencing_token=? AND delivery_attempts=? AND updated_at=? "
                "AND delivered_at IS NULL AND receipt_hash IS NULL",
                (
                    worker_id,
                    token,
                    now.isoformat(),
                    expires_at.isoformat(),
                    attempts,
                    now.isoformat(),
                    snapshot.message.message_id,
                    *predicate_values,
                    snapshot.fencing_token,
                    snapshot.delivery_attempts,
                    snapshot.updated_at.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflict("outbox claim lost its compare-and-swap")
            persisted = self.load_outbox_snapshot(snapshot.message.message_id)
            lease = Lease(
                owner=worker_id,
                fencing_token=token,
                acquired_at=now,
                expires_at=expires_at,
            )
            if (
                persisted.status is not OutboxStatus.LEASED
                or persisted.lease != lease
                or persisted.delivery_attempts != attempts
                or persisted.message != snapshot.message
            ):
                raise DataCorruption("persisted outbox claim projection is invalid")
            return OutboxClaim(
                message=persisted.message,
                lease=lease,
                delivery_attempts=attempts,
            )

    def complete_outbox(
        self,
        claim: OutboxClaim,
        receipt: DeliveryReceipt,
        *,
        now: datetime,
    ) -> OutboxSnapshot:
        if type(claim) is not OutboxClaim:
            raise TypeError("claim must be the exact OutboxClaim type")
        if type(receipt) is not DeliveryReceipt:
            raise TypeError("receipt must be the exact DeliveryReceipt type")
        if receipt.message_id != claim.message.message_id:
            raise IdentityConflict("delivery receipt belongs to another outbox message")
        now = _require_utc_input(now, "now")
        if receipt.delivered_at > now:
            raise ValueError("receipt.delivered_at cannot exceed completion time")
        with self._transaction("complete_outbox"):
            snapshot = self.load_outbox_snapshot(claim.message.message_id)
            if snapshot.message != claim.message:
                raise IdentityConflict("outbox claim contains divergent message bytes")
            if snapshot.status is OutboxStatus.DELIVERED:
                if (
                    snapshot.receipt_hash == receipt.receipt_hash
                    and snapshot.delivered_at == receipt.delivered_at
                ):
                    return snapshot
                raise IdentityConflict("delivered outbox has a divergent receipt")
            self._assert_live_outbox_claim(claim, snapshot, now=now)
            if receipt.delivered_at < claim.lease.acquired_at:
                raise ValueError("receipt.delivered_at predates the delivery claim")
            cursor = self._connection.execute(
                "UPDATE outbox_messages SET status='delivered', claim_owner=NULL, "
                "lease_acquired_at=NULL, lease_expires_at=NULL, delivered_at=?, "
                "receipt_hash=?, updated_at=? WHERE message_id=? "
                "AND status='leased' AND claim_owner=? AND fencing_token=? "
                "AND lease_acquired_at=? AND lease_expires_at=? "
                "AND delivery_attempts=? AND updated_at=? "
                "AND lease_expires_at>? AND delivered_at IS NULL "
                "AND receipt_hash IS NULL",
                (
                    receipt.delivered_at.isoformat(),
                    receipt.receipt_hash,
                    now.isoformat(),
                    claim.message.message_id,
                    claim.lease.owner,
                    claim.lease.fencing_token,
                    claim.lease.acquired_at.isoformat(),
                    claim.lease.expires_at.isoformat(),
                    claim.delivery_attempts,
                    claim.lease.acquired_at.isoformat(),
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLease("outbox completion lost its compare-and-swap")
            persisted = self.load_outbox_snapshot(claim.message.message_id)
            if (
                persisted.status is not OutboxStatus.DELIVERED
                or persisted.receipt_hash != receipt.receipt_hash
                or persisted.delivered_at != receipt.delivered_at
                or persisted.lease is not None
            ):
                raise DataCorruption("persisted outbox receipt projection is invalid")
            return persisted

    def release_outbox(
        self,
        claim: OutboxClaim,
        *,
        now: datetime,
    ) -> OutboxSnapshot:
        if type(claim) is not OutboxClaim:
            raise TypeError("claim must be the exact OutboxClaim type")
        now = _require_utc_input(now, "now")
        with self._transaction("release_outbox"):
            snapshot = self.load_outbox_snapshot(claim.message.message_id)
            self._assert_live_outbox_claim(claim, snapshot, now=now)
            cursor = self._connection.execute(
                "UPDATE outbox_messages SET status='pending', claim_owner=NULL, "
                "lease_acquired_at=NULL, lease_expires_at=NULL, updated_at=? "
                "WHERE message_id=? AND status='leased' AND claim_owner=? "
                "AND fencing_token=? AND lease_acquired_at=? "
                "AND lease_expires_at=? AND delivery_attempts=? AND updated_at=? "
                "AND lease_expires_at>? AND delivered_at IS NULL "
                "AND receipt_hash IS NULL",
                (
                    now.isoformat(),
                    claim.message.message_id,
                    claim.lease.owner,
                    claim.lease.fencing_token,
                    claim.lease.acquired_at.isoformat(),
                    claim.lease.expires_at.isoformat(),
                    claim.delivery_attempts,
                    claim.lease.acquired_at.isoformat(),
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLease("outbox release lost its compare-and-swap")
            persisted = self.load_outbox_snapshot(claim.message.message_id)
            if (
                persisted.status is not OutboxStatus.PENDING
                or persisted.lease is not None
                or persisted.fencing_token != claim.lease.fencing_token
                or persisted.delivery_attempts != claim.delivery_attempts
            ):
                raise DataCorruption("released outbox projection is invalid")
            return persisted

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

    def assert_execution_consistency(self) -> None:
        """Fail closed unless every execution projection agrees end to end."""

        with self._transaction("assert_execution_consistency"):
            workflow_ids = tuple(
                row[0]
                for row in self._connection.execute(
                    "SELECT workflow_id FROM workflows ORDER BY workflow_id"
                )
            )
            command_rows = tuple(
                self._connection.execute(
                    "SELECT command_id, workflow_id FROM reservation_commands "
                    "ORDER BY command_id"
                )
            )
            command_ids = {row[0] for row in command_rows}
            ledger_ids = {
                row[0]
                for row in self._connection.execute(
                    "SELECT command_id FROM execution_ledger"
                )
            }
            if len(command_ids) != len(command_rows) or command_ids != ledger_ids:
                raise DataCorruption("command and ledger identity sets disagree")
            commands_by_workflow: dict[str, list[str]] = {}
            for command_id, workflow_id in command_rows:
                commands_by_workflow.setdefault(workflow_id, []).append(command_id)

            represented: set[str] = set()
            for workflow_id in workflow_ids:
                state = self._replay_workflow_history(workflow_id)
                summary_ids: list[str] = []
                execution_started = 0
                for event_row in self._connection.execute(
                    "SELECT event_id, workflow_id, revision, occurred_at, event_type, "
                    "event_json, event_hash FROM domain_events "
                    "WHERE workflow_id=? AND event_type IN "
                    "('summary_recorded', 'execution_started') ORDER BY revision",
                    (workflow_id,),
                ):
                    event = self._verified_event(event_row)
                    if type(event) is SummaryRecorded:
                        summary_ids.append(event.outbox_message_id)
                    elif type(event) is ExecutionStarted:
                        execution_started += 1
                    else:
                        raise DataCorruption(
                            "execution consistency query returned an unexpected event"
                        )
                actual_summary_ids = tuple(
                    row[0]
                    for row in self._connection.execute(
                        "SELECT message_id FROM outbox_messages "
                        "WHERE workflow_id=? AND kind='summary_presented' "
                        "ORDER BY message_id",
                        (workflow_id,),
                    )
                )
                if (
                    len(summary_ids) != len(set(summary_ids))
                    or tuple(sorted(summary_ids)) != actual_summary_ids
                ):
                    raise DataCorruption(
                        "summary events and durable outbox cardinality disagree"
                    )
                for message_id in actual_summary_ids:
                    self.load_outbox(message_id)

                durable_ids = commands_by_workflow.get(workflow_id, [])
                if not state.meta.command_ids:
                    if durable_ids or execution_started != 0:
                        raise DataCorruption(
                            "workflow without command metadata owns execution history"
                        )
                    continue
                if (
                    len(state.meta.command_ids) != 1
                    or durable_ids != [state.meta.command_ids[0]]
                ):
                    raise DataCorruption(
                        "workflow command metadata disagrees with durable commands"
                    )
                command = self.load_command(state.meta.command_ids[0])
                ledger = self.load_ledger(command.command_id)
                represented.add(command.command_id)
                never_started = type(state) is ExecutionQueuedState
                if (
                    ledger.fencing_token != ledger.claim_count
                    or ledger.preparation_failures > ledger.claim_count
                    or (
                        never_started
                        and (
                            execution_started != 0
                            or ledger.fencing_token != 0
                            or ledger.claim_count != 0
                            or ledger.preparation_failures != 0
                        )
                    )
                    or (
                        not never_started
                        and (execution_started != 1 or ledger.claim_count < 1)
                    )
                    or (
                        type(state) is FailedBeforeProviderState
                        and ledger.preparation_failures < 1
                    )
                ):
                    raise DataCorruption(
                        "execution claim counters violate their event history"
                    )
                if getattr(state, "command", None) != command:
                    raise DataCorruption(
                        "workflow state does not embed its authorized command"
                    )
                expected_request_hash = DispatchRequest.from_command(
                    command,
                    dumps_command(command),
                ).payload_hash
                if (
                    ledger.dispatch_slots_consumed == 1
                    and ledger.dispatch_request_hash != expected_request_hash
                ):
                    raise DataCorruption(
                        "dispatch fence does not bind the canonical command request"
                    )
                if (
                    ledger.dispatch_fenced_at is not None
                    and ledger.dispatch_fenced_at > ledger.updated_at
                ):
                    raise DataCorruption("dispatch fence timestamp exceeds ledger update")

                if type(state) is ExecutionQueuedState:
                    valid_state = ledger.status is LedgerStatus.QUEUED
                elif type(state) is ExecutingState:
                    valid_state = ledger.status in (
                        LedgerStatus.QUEUED,
                        LedgerStatus.PREPARING,
                        LedgerStatus.DISPATCH_FENCED,
                    )
                elif type(state) is FailedBeforeProviderState:
                    valid_state = (
                        ledger.status is LedgerStatus.OUTCOME_RECORDED
                        and ledger.dispatch_slots_consumed == 0
                    )
                elif type(state) is SucceededState:
                    valid_state = (
                        ledger.status is LedgerStatus.OUTCOME_RECORDED
                        and ledger.dispatch_slots_consumed == 1
                    )
                elif type(state) is FailedNoEffectState:
                    valid_state = (
                        ledger.status is LedgerStatus.OUTCOME_RECORDED
                        and ledger.dispatch_slots_consumed == 1
                    )
                elif type(state) is ManualReviewState:
                    valid_state = (
                        ledger.status is LedgerStatus.MANUAL_REVIEW
                        and ledger.dispatch_slots_consumed == 1
                    )
                elif type(state) is UncertainState:
                    valid_state = False
                else:
                    valid_state = False
                if not valid_state:
                    raise DataCorruption(
                        "workflow execution state disagrees with its ledger status"
                    )

                outcome_present = ledger.outcome_json is not None
                if outcome_present:
                    outcome = loads_outcome(ledger.outcome_json)
                    if not self._terminal_state_matches_outcome(
                        state,
                        command,
                        outcome,
                    ):
                        raise DataCorruption(
                            "terminal workflow state disagrees with durable outcome"
                        )
                execution_messages = tuple(
                    row[0]
                    for row in self._connection.execute(
                        "SELECT message_id FROM outbox_messages WHERE command_id=? "
                        "ORDER BY message_id",
                        (command.command_id,),
                    )
                )
                if len(execution_messages) != (1 if outcome_present else 0):
                    raise DataCorruption(
                        "execution outcome and outbox cardinality disagree"
                    )
                for message_id in execution_messages:
                    self.load_outbox(message_id)

            if represented != command_ids:
                raise DataCorruption("durable command is absent from its workflow state")
            for row in self._connection.execute(
                "SELECT message_id FROM outbox_messages ORDER BY message_id"
            ):
                self.load_outbox(row[0])

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

    def release_expired_pre_dispatch(self, *, now: datetime) -> int:
        """Release every expired pre-fence lease without changing domain state."""

        now = _require_utc_input(now, "now")
        released = 0
        with self._transaction("release_expired_pre_dispatch"):
            candidates = tuple(
                row[0]
                for row in self._connection.execute(
                    "SELECT command_id FROM execution_ledger "
                    "WHERE status='preparing' AND dispatch_slots_consumed=0 "
                    "AND outcome_json IS NULL AND lease_expires_at<=? "
                    "ORDER BY command_id",
                    (now.isoformat(),),
                )
            )
            for command_id in candidates:
                command = self.load_command(command_id)
                ledger = self.load_ledger(command.command_id)
                state = self.load_workflow(command.workflow_id)
                if (
                    type(state) is not ExecutingState
                    or state.command != command
                    or state.meta.command_ids != (command.command_id,)
                    or ledger.status is not LedgerStatus.PREPARING
                    or ledger.dispatch_slots_consumed != 0
                    or ledger.outcome_json is not None
                    or ledger.claim_owner is None
                    or ledger.lease_acquired_at is None
                    or ledger.lease_expires_at is None
                    or not (
                        ledger.lease_acquired_at
                        <= ledger.updated_at
                        < ledger.lease_expires_at
                    )
                    or now < ledger.updated_at
                    or now < ledger.lease_expires_at
                ):
                    raise DataCorruption(
                        "expired pre-dispatch lease has an impossible projection"
                    )
                cursor = self._connection.execute(
                    "UPDATE execution_ledger SET status='queued', "
                    "claim_owner=NULL, lease_acquired_at=NULL, "
                    "lease_expires_at=NULL, updated_at=? "
                    "WHERE command_id=? AND status='preparing' "
                    "AND claim_owner=? AND fencing_token=? "
                    "AND lease_acquired_at=? AND lease_expires_at=? "
                    "AND updated_at=? AND claim_count=? "
                    "AND preparation_failures=? "
                    "AND lease_expires_at<=? AND dispatch_slots_consumed=0 "
                    "AND outcome_json IS NULL AND outcome_hash IS NULL",
                    (
                        now.isoformat(),
                        command.command_id,
                        ledger.claim_owner,
                        ledger.fencing_token,
                        ledger.lease_acquired_at.isoformat(),
                        ledger.lease_expires_at.isoformat(),
                        ledger.updated_at.isoformat(),
                        ledger.claim_count,
                        ledger.preparation_failures,
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflict(
                        "expired pre-dispatch release lost its compare-and-swap"
                    )
                updated = self.load_ledger(command.command_id)
                if (
                    updated.status is not LedgerStatus.QUEUED
                    or updated.claim_owner is not None
                    or updated.dispatch_slots_consumed != 0
                    or updated.fencing_token != ledger.fencing_token
                    or updated.claim_count != ledger.claim_count
                ):
                    raise DataCorruption(
                        "released pre-dispatch ledger projection is invalid"
                    )
                released += 1
        return released

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
                "AND NOT EXISTS (SELECT 1 FROM reservation_commands AS sibling "
                "JOIN execution_ledger AS sibling_ledger "
                "ON sibling_ledger.command_id=sibling.command_id "
                "WHERE sibling.draft_id=command.draft_id "
                "AND sibling.draft_version=command.draft_version "
                "AND sibling.command_id<>command.command_id "
                "AND sibling_ledger.status IN ('preparing','dispatch_fenced')) "
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

    def list_outcome_projection_inputs(
        self,
    ) -> tuple[tuple[ReservationCommand, LedgerSnapshot], ...]:
        """Return one consistent read-only snapshot for downstream projection."""

        self._ensure_open()
        self._connection.execute("BEGIN")
        try:
            command_ids = tuple(
                row[0]
                for row in self._connection.execute(
                    "SELECT command.command_id FROM reservation_commands AS command "
                    "JOIN execution_ledger AS ledger "
                    "ON ledger.command_id=command.command_id "
                    "ORDER BY command.draft_id,command.draft_version,command.command_id"
                )
            )
            result = tuple(
                (self.load_command(command_id), self.load_ledger(command_id))
                for command_id in command_ids
            )
            self._connection.execute("COMMIT")
            return result
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def preparation_block(self, command_id: str) -> tuple[str, str] | None:
        """Return a pre-fence package stop reason and durable peer evidence."""

        command = self.load_command(command_id)
        row = self._connection.execute(
            "SELECT sibling_ledger.outcome_hash "
            "FROM reservation_commands AS sibling "
            "JOIN execution_ledger AS sibling_ledger "
            "ON sibling_ledger.command_id=sibling.command_id "
            "WHERE sibling.draft_id=? AND sibling.draft_version=? "
            "AND sibling.command_id<>? AND sibling_ledger.status='manual_review' "
            "LIMIT 1",
            (command.draft_id, command.draft_version, command.command_id),
        ).fetchone()
        return None if row is None else ("package_peer_manual_review", row[0])

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

    def mark_expired_fenced_unknown(self, *, now: datetime) -> int:
        """Promote expired dispatch fences to manual review without redispatch."""

        now = _require_utc_input(now, "now")
        reconciled = 0
        with self._transaction("mark_expired_fenced_unknown"):
            candidates = tuple(
                row[0]
                for row in self._connection.execute(
                    "SELECT command_id FROM execution_ledger "
                    "WHERE status='dispatch_fenced' "
                    "AND dispatch_slots_consumed=1 "
                    "AND outcome_json IS NULL AND lease_expires_at<=? "
                    "ORDER BY command_id",
                    (now.isoformat(),),
                )
            )
            for command_id in candidates:
                command = self.load_command(command_id)
                ledger = self.load_ledger(command.command_id)
                state = self.load_workflow(command.workflow_id)
                expected_request = DispatchRequest.from_command(
                    command,
                    dumps_command(command),
                )
                if (
                    type(state) is not ExecutingState
                    or state.command != command
                    or state.meta.command_ids != (command.command_id,)
                    or ledger.status is not LedgerStatus.DISPATCH_FENCED
                    or ledger.dispatch_slots_consumed != 1
                    or ledger.dispatch_request_hash != expected_request.payload_hash
                    or ledger.dispatch_fenced_at is None
                    or ledger.claim_owner is None
                    or ledger.lease_acquired_at is None
                    or ledger.lease_expires_at is None
                    or not (
                        ledger.lease_acquired_at
                        <= ledger.dispatch_fenced_at
                        == ledger.updated_at
                        < ledger.lease_expires_at
                    )
                    or ledger.outcome_json is not None
                    or now < ledger.updated_at
                    or now < ledger.lease_expires_at
                ):
                    raise DataCorruption(
                        "expired dispatch fence has an impossible projection"
                    )
                outcome = command.outcome(
                    certainty=ExecutionCertainty.CALLED_UNKNOWN,
                    normalized_status="dispatch_outcome_unknown_after_expiry",
                    evidence=(ledger.dispatch_request_hash,),
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
                if self._event_row(finished.event_id) is not None:
                    raise DataCorruption(
                        "expired dispatch fence already has its outcome event"
                    )
                finished_transition = reduce(state, finished)
                if (
                    finished_transition.status is not TransitionStatus.APPLIED
                    or type(finished_transition.state) is not UncertainState
                    or finished_transition.commands
                ):
                    raise DataCorruption(
                        "reconciled unknown did not produce uncertain state"
                    )
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
                if self._event_row(review.event_id) is not None:
                    raise DataCorruption(
                        "expired dispatch fence already has its review event"
                    )
                final_transition = reduce(finished_transition.state, review)
                if (
                    final_transition.status is not TransitionStatus.APPLIED
                    or type(final_transition.state) is not ManualReviewState
                    or final_transition.commands
                ):
                    raise DataCorruption(
                        "reconciled unknown did not produce mandatory manual review"
                    )
                message = project_outcome_outbox(
                    command,
                    outcome,
                    created_at=now,
                )
                self._insert_event(
                    command.workflow_id,
                    finished,
                    finished_transition.state.meta.revision,
                )
                self._insert_event(
                    command.workflow_id,
                    review,
                    final_transition.state.meta.revision,
                )
                self._update_state_compare_and_swap(state, final_transition.state)
                cursor = self._connection.execute(
                    "UPDATE execution_ledger SET status='manual_review', "
                    "claim_owner=NULL, lease_acquired_at=NULL, "
                    "lease_expires_at=NULL, outcome_json=?, outcome_hash=?, "
                    "updated_at=? WHERE command_id=? "
                    "AND status='dispatch_fenced' AND claim_owner=? "
                    "AND fencing_token=? AND lease_acquired_at=? "
                    "AND lease_expires_at=? AND updated_at=? "
                    "AND claim_count=? AND preparation_failures=? "
                    "AND lease_expires_at<=? "
                    "AND dispatch_slots_consumed=1 "
                    "AND dispatch_request_hash=? AND dispatch_fenced_at=? "
                    "AND outcome_json IS NULL AND outcome_hash IS NULL",
                    (
                        raw_outcome,
                        outcome_hash,
                        now.isoformat(),
                        command.command_id,
                        ledger.claim_owner,
                        ledger.fencing_token,
                        ledger.lease_acquired_at.isoformat(),
                        ledger.lease_expires_at.isoformat(),
                        ledger.updated_at.isoformat(),
                        ledger.claim_count,
                        ledger.preparation_failures,
                        now.isoformat(),
                        ledger.dispatch_request_hash,
                        ledger.dispatch_fenced_at.isoformat(),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflict(
                        "expired dispatch reconciliation lost its compare-and-swap"
                    )
                self._insert_outbox(message)
                persisted = self.load_ledger(command.command_id)
                if (
                    persisted.status is not LedgerStatus.MANUAL_REVIEW
                    or persisted.dispatch_slots_consumed != 1
                    or persisted.outcome_json != raw_outcome
                    or persisted.outcome_hash != outcome_hash
                    or persisted.claim_owner is not None
                ):
                    raise DataCorruption(
                        "reconciled dispatch outcome projection is invalid"
                    )
                reconciled += 1
        return reconciled

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
