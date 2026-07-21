"""Fenced single-write SQLite store for Phase 7 boundary state."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Protocol

from reservation_domain import ReservationCommand, dumps_command
from reservation_execution import OutboxMessage
from reservation_followup import (
    PaymentSettlementCommand,
    to_wire_json as to_phase6_wire_json,
)

from reservation_boundary.schema import TABLE_NAMES, render_sqlite
from reservation_boundary.serialization import from_wire_json, semantic_hash, to_wire_json
from reservation_boundary.types import (
    BoundaryCommit,
    BoundaryState,
    ImportDisposition,
    ImportResult,
    LegacyLeadSnapshot,
    VersionedBoundaryState,
)


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_FACTORY_TOKEN = object()


class BoundaryStoreError(RuntimeError):
    """Base class for exact boundary persistence failures."""


class ConcurrencyConflict(BoundaryStoreError):
    """State version or fencing token is stale."""


class IdentityConflict(BoundaryStoreError):
    """A durable identity was reused with divergent canonical bytes."""


class DataCorruption(BoundaryStoreError):
    """Persisted bytes violate their canonical hash or type."""


class StateNotFound(BoundaryStoreError):
    """No typed boundary state exists for the requested lead."""


class LegacyStateReadPort(Protocol):
    """Read-only legacy port; single-write is structural, not conventional."""

    def read_snapshot(self, lead_key: str) -> LegacyLeadSnapshot | None: ...


def _require_id(value: object, field_name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be an exact opaque identifier")
    return value


def _require_hash(value: object, field_name: str) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return value


def _require_int(value: object, field_name: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field_name} must be an exact integer >= {minimum}")
    return value


def _utc_text(value: object, field_name: str) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be an exact UTC datetime")
    text = value.isoformat()
    if datetime.fromisoformat(text) != value:
        raise ValueError(f"{field_name} must be canonical UTC")
    return text


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _command_record(command: object) -> tuple[str, str, str]:
    if type(command) is ReservationCommand:
        wire = dumps_command(command)
        return command.command_id, "reservation", wire
    if type(command) is PaymentSettlementCommand:
        wire = to_phase6_wire_json(command)
        return command.settlement_command_id, "payment_settlement", wire
    raise TypeError("command must be an exact BoundaryCommand member")


class SQLiteBoundaryStore:
    """One in-memory-capable SQLite boundary unit of work."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        _factory_token: object,
    ) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise TypeError("SQLiteBoundaryStore must be created by a factory")
        self._connection = connection
        self._closed = False

    @classmethod
    def open_memory(cls) -> "SQLiteBoundaryStore":
        connection = sqlite3.connect(":memory:", isolation_level=None, timeout=5.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(render_sqlite())
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise DataCorruption("SQLite foreign keys are disabled")
            return cls(connection, _factory_token=_FACTORY_TOKEN)
        except BaseException:
            connection.close()
            raise

    @classmethod
    def open_path(cls, path: Path) -> "SQLiteBoundaryStore":
        if not isinstance(path, Path):
            raise TypeError("path must be an exact pathlib.Path")
        if path.exists() and not path.is_file():
            raise ValueError("SQLite path must be a file or absent")
        connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            connection.execute("PRAGMA synchronous = FULL")
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not names:
                connection.executescript(render_sqlite())
                names = set(TABLE_NAMES)
            if names != set(TABLE_NAMES):
                raise DataCorruption("SQLite table universe is not exact")
            strict = {
                row[1]: row[5]
                for row in connection.execute("PRAGMA table_list")
                if row[1] in names
            }
            if strict != {name: 1 for name in TABLE_NAMES}:
                raise DataCorruption("SQLite tables are not all STRICT")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise DataCorruption("SQLite foreign keys are disabled")
            if str(mode).casefold() != "wal":
                raise DataCorruption("SQLite WAL mode is unavailable")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise DataCorruption("SQLite synchronous mode is not FULL")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise DataCorruption("SQLite foreign key violations exist")
            return cls(connection, _factory_token=_FACTORY_TOKEN)
        except BaseException:
            connection.close()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLiteBoundaryStore is closed")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._ensure_open()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def _versioned_from_row(
        self,
        row: tuple[object, ...],
        *,
        expected_lead_key: str,
    ) -> VersionedBoundaryState:
        version, state_json, state_hash = row
        if type(version) is not int or type(state_json) is not str or type(state_hash) is not str:
            raise DataCorruption("boundary_state row has wrong SQLite types")
        try:
            state = from_wire_json(state_json, BoundaryState)
        except (TypeError, ValueError) as exc:
            raise DataCorruption("boundary state wire is invalid") from exc
        if (
            state.lead_key != expected_lead_key
            or state.version != version
            or semantic_hash(state) != state_hash
        ):
            raise DataCorruption("boundary state identity/hash/version does not bind")
        return VersionedBoundaryState(state, version, state_hash)

    def _load_state_in_transaction(self, lead_key: str) -> VersionedBoundaryState:
        row = self._connection.execute(
            "SELECT version, state_json, state_hash FROM boundary_state WHERE lead_key=?",
            (lead_key,),
        ).fetchone()
        if row is None:
            raise StateNotFound(lead_key)
        return self._versioned_from_row(row, expected_lead_key=lead_key)

    def load_state(self, lead_key: str) -> VersionedBoundaryState:
        self._ensure_open()
        exact_lead_key = _require_id(lead_key, "lead_key")
        return self._load_state_in_transaction(exact_lead_key)

    def event_hash(self, lead_key: str, event_id: str) -> str | None:
        self._ensure_open()
        exact_lead_key = _require_id(lead_key, "lead_key")
        exact_event_id = _require_id(event_id, "event_id")
        row = self._connection.execute(
            "SELECT event_hash FROM boundary_events WHERE lead_key=? AND event_id=?",
            (exact_lead_key, exact_event_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return _require_hash(row[0], "stored event_hash")
        except ValueError as exc:
            raise DataCorruption("stored event hash is invalid") from exc

    def import_genesis(
        self,
        snapshot: LegacyLeadSnapshot,
        result: ImportResult,
        *,
        claimed_at: datetime,
    ) -> VersionedBoundaryState:
        if type(snapshot) is not LegacyLeadSnapshot:
            raise TypeError("snapshot must be the exact LegacyLeadSnapshot type")
        if type(result) is not ImportResult:
            raise TypeError("result must be the exact ImportResult type")
        if result.disposition is not ImportDisposition.MIGRATED or result.state is None:
            raise ValueError("only a migrated ImportResult can create genesis")
        if result.state.version != 0:
            raise ValueError("genesis boundary state must have version zero")
        lead_key = _require_id(result.state.lead_key, "lead_key")
        if snapshot.raw_fields["lead_key"] != lead_key:
            raise IdentityConflict("snapshot lead_key does not bind imported state")
        snapshot_hash = _require_hash(snapshot.snapshot_hash, "snapshot_hash")
        state_json = to_wire_json(result.state)
        state_hash = semantic_hash(result.state)
        instant = _utc_text(claimed_at, "claimed_at")

        try:
            with self._transaction():
                claim = self._connection.execute(
                    "SELECT snapshot_hash, state_hash FROM legacy_import_claims WHERE lead_key=?",
                    (lead_key,),
                ).fetchone()
                if claim is not None:
                    if claim != (snapshot_hash, state_hash):
                        raise IdentityConflict("legacy genesis claim diverged")
                    return self._load_state_in_transaction(lead_key)
                if self._connection.execute(
                    "SELECT 1 FROM boundary_state WHERE lead_key=?", (lead_key,)
                ).fetchone() is not None:
                    raise IdentityConflict("boundary state exists without matching import claim")
                self._connection.execute(
                    "INSERT INTO boundary_state "
                    "(lead_key, version, state_json, state_hash, fencing_token, created_at, updated_at) "
                    "VALUES (?, 0, ?, ?, 0, ?, ?)",
                    (lead_key, state_json, state_hash, instant, instant),
                )
                self._connection.execute(
                    "INSERT INTO legacy_import_claims "
                    "(lead_key, snapshot_hash, disposition, state_hash, claimed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        lead_key,
                        snapshot_hash,
                        result.disposition.value,
                        state_hash,
                        instant,
                    ),
                )
                return VersionedBoundaryState(result.state, 0, state_hash)
        except sqlite3.IntegrityError as exc:
            raise IdentityConflict("genesis violated durable identity") from exc

    def acquire_fence(self, lead_key: str) -> tuple[VersionedBoundaryState, int]:
        exact_lead_key = _require_id(lead_key, "lead_key")
        with self._transaction():
            row = self._connection.execute(
                "SELECT version, state_json, state_hash, fencing_token "
                "FROM boundary_state WHERE lead_key=?",
                (exact_lead_key,),
            ).fetchone()
            if row is None:
                raise StateNotFound(exact_lead_key)
            token = _require_int(row[3], "stored fencing_token", minimum=0) + 1
            updated = self._connection.execute(
                "UPDATE boundary_state SET fencing_token=? "
                "WHERE lead_key=? AND fencing_token=?",
                (token, exact_lead_key, row[3]),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("fencing token changed concurrently")
            return (
                self._versioned_from_row(
                    row[:3],
                    expected_lead_key=exact_lead_key,
                ),
                token,
            )

    def commit(
        self,
        *,
        event_id: str,
        event_hash: str,
        expected_version: int,
        fencing_token: int,
        commit: BoundaryCommit,
        committed_at: datetime,
        fault_hook: Callable[[str], None] | None = None,
    ) -> VersionedBoundaryState:
        exact_event_id = _require_id(event_id, "event_id")
        exact_event_hash = _require_hash(event_hash, "event_hash")
        expected = _require_int(expected_version, "expected_version", minimum=0)
        token = _require_int(fencing_token, "fencing_token", minimum=1)
        if type(commit) is not BoundaryCommit:
            raise TypeError("commit must be the exact BoundaryCommit type")
        if commit.facts:
            raise ValueError("facts must be reduced into state before persistence")
        if fault_hook is not None and not callable(fault_hook):
            raise TypeError("fault_hook must be callable or None")
        to_wire_json(commit)
        lead_key = _require_id(commit.state.lead_key, "commit.state.lead_key")
        instant = _utc_text(committed_at, "committed_at")
        state_json = to_wire_json(commit.state)
        state_hash = semantic_hash(commit.state)

        def fault(stage: str) -> None:
            if fault_hook is not None:
                fault_hook(stage)

        try:
            with self._transaction():
                existing_event = self._connection.execute(
                    "SELECT event_hash FROM boundary_events WHERE lead_key=? AND event_id=?",
                    (lead_key, exact_event_id),
                ).fetchone()
                if existing_event is not None:
                    if existing_event[0] != exact_event_hash:
                        raise IdentityConflict("event_id was reused with divergent hash")
                    return self._load_state_in_transaction(lead_key)
                if commit.state.version != expected + 1:
                    raise ValueError("commit state version must equal expected_version + 1")
                row = self._connection.execute(
                    "SELECT version, fencing_token FROM boundary_state WHERE lead_key=?",
                    (lead_key,),
                ).fetchone()
                if row is None:
                    raise StateNotFound(lead_key)
                if row != (expected, token):
                    raise ConcurrencyConflict("state version or fencing token is stale")
                updated = self._connection.execute(
                    "UPDATE boundary_state SET version=?, state_json=?, state_hash=?, updated_at=? "
                    "WHERE lead_key=? AND version=? AND fencing_token=?",
                    (
                        commit.state.version,
                        state_json,
                        state_hash,
                        instant,
                        lead_key,
                        expected,
                        token,
                    ),
                ).rowcount
                if updated != 1:
                    raise ConcurrencyConflict("state CAS lost")
                fault("after_state_update")
                self._connection.execute(
                    "INSERT INTO boundary_events "
                    "(lead_key, event_id, event_hash, state_version, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        lead_key,
                        exact_event_id,
                        exact_event_hash,
                        commit.state.version,
                        instant,
                    ),
                )
                fault("after_event_insert")
                for command in commit.commands:
                    command_id, command_type, command_json = _command_record(command)
                    if type(command) is ReservationCommand:
                        if commit.state.workflow is None or command.workflow_id != commit.state.workflow.meta.workflow_id:
                            raise IdentityConflict("reservation command does not bind boundary workflow")
                    self._connection.execute(
                        "INSERT INTO boundary_commands "
                        "(command_id, lead_key, event_id, command_type, command_json, command_hash, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            command_id,
                            lead_key,
                            exact_event_id,
                            command_type,
                            command_json,
                            _sha(command_json),
                            instant,
                        ),
                    )
                    fault("after_command_insert")
                for message in commit.outbox:
                    if type(message) is not OutboxMessage:
                        raise TypeError("outbox must contain exact OutboxMessage values")
                    self._connection.execute(
                        "INSERT INTO boundary_outbox "
                        "(message_id, lead_key, event_id, kind, payload_json, payload_hash, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            message.message_id,
                            lead_key,
                            exact_event_id,
                            message.kind.value,
                            message.canonical_payload,
                            message.payload_hash,
                            instant,
                        ),
                    )
                    fault("after_outbox_insert")
                return VersionedBoundaryState(
                    commit.state,
                    commit.state.version,
                    state_hash,
                )
        except sqlite3.IntegrityError as exc:
            raise IdentityConflict("boundary commit violated durable identity") from exc


__all__ = (
    "BoundaryStoreError",
    "ConcurrencyConflict",
    "DataCorruption",
    "IdentityConflict",
    "LegacyStateReadPort",
    "SQLiteBoundaryStore",
    "StateNotFound",
)
