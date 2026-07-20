"""Atomic SQLite persistence for the independent Phase 6 follow-up workflows.

This module owns only workflow/event persistence and handoff bootstrap outboxes.
Claims, ledgers, receipts, workers, ports, providers, and delivery belong to later
Phase 6 tasks and are deliberately absent here.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from .handoff import (
    HandoffAcknowledged,
    HandoffCancelled,
    HandoffEffectFailed,
    HandoffEffectFailureCode,
    HandoffEffectJob,
    HandoffEffectKind,
    HandoffEvent,
    HandoffRequested,
    HandoffTransition,
    HandoffTransitionReason,
    HandoffTransitionStatus,
    HandoffWorkflow,
    new_handoff,
    reduce_handoff,
)
from .payment import (
    FinancialConfirmationReceived,
    FinancialSummaryRecorded,
    PaymentCancelled,
    PaymentEvent,
    PaymentEvidenceRecorded,
    PaymentExpired,
    PaymentMethodSelected,
    PaymentTransition,
    PaymentTransitionReason,
    PaymentTransitionStatus,
    PaymentWorkflow,
    SettlementFinished,
    SettlementStarted,
    new_payment,
    reduce_payment,
)
from .schema import render_sqlite, schema_contract
from .serialization import from_wire_json, semantic_hash, to_wire_json
from .types import (
    ConfirmedReservationAnchor,
    EffectRequirement,
    HandoffEffectPolicy,
    HandoffOutboxClaim,
    HandoffReceipt,
    PaymentEffectPolicy,
)

_EXPECTED_TABLES = tuple(table.name for table in schema_contract())
_FACTORY_TOKEN = object()
_HANDOFF_EVENT_TYPES = (
    HandoffRequested,
    HandoffAcknowledged,
    HandoffEffectFailed,
    HandoffCancelled,
)
_HANDOFF_EVENT_BY_NAME = {event_type.__name__: event_type for event_type in _HANDOFF_EVENT_TYPES}
_PAYMENT_EVENT_TYPES = (
    PaymentMethodSelected,
    FinancialSummaryRecorded,
    FinancialConfirmationReceived,
    PaymentEvidenceRecorded,
    SettlementStarted,
    SettlementFinished,
    PaymentExpired,
    PaymentCancelled,
)
_PAYMENT_EVENT_BY_NAME = {event_type.__name__: event_type for event_type in _PAYMENT_EVENT_TYPES}
_OPERATIONAL_HANDOFF_EVENTS = (
    HandoffAcknowledged,
    HandoffEffectFailed,
)
_OPERATIONAL_PAYMENT_EVENTS = (
    PaymentEvidenceRecorded,
    SettlementStarted,
    SettlementFinished,
)


class StoreError(RuntimeError):
    """Base class for stable follow-up persistence failures."""


class StoreUnavailable(StoreError):
    """SQLite could not complete an operation."""


class DataCorruption(StoreError):
    """Persisted rows violate the canonical contract."""


class ConcurrencyConflict(StoreError):
    """An optimistic revision is stale."""


class IdentityConflict(StoreError):
    """A durable identity exists with divergent content or ownership."""


class StaleLease(StoreError):
    """A handoff outbox lease is expired, superseded, or divergent."""


class UnsupportedEffect(StoreError):
    """An event belongs to a later operational task."""


class HandoffNotFound(StoreError):
    """A requested handoff workflow does not exist."""


class PaymentNotFound(StoreError):
    """A requested payment workflow does not exist."""


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_id(value: str, field_name: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{field_name} must be exact non-empty text")
    return value


def _require_revision(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("expected_revision must be an integer >= 0")
    return value


def _require_utc_input(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _require_lease_ttl(value: timedelta) -> timedelta:
    if type(value) is not timedelta or value <= timedelta(0):
        raise ValueError("lease_ttl must be a positive timedelta")
    return value


def _handoff_claim_owner(
    worker_id: str,
    delivery_id: str,
    delivery_version: int,
) -> str:
    material = "\x00".join((worker_id, delivery_id, str(delivery_version)))
    return f"handoff-claim:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _handoff_receipt_record(
    claim: HandoffOutboxClaim,
    receipt: HandoffReceipt,
) -> str:
    payload = {
        "schema_version": 1,
        "type": "handoff_receipt_record",
        "data": {
            "claim_owner": _handoff_claim_owner(
                claim.worker_id,
                claim.delivery_id,
                claim.delivery_version,
            ),
            "delivery_attempts": claim.delivery_attempts,
            "delivery_id": claim.delivery_id,
            "delivery_version": claim.delivery_version,
            "fencing_token": claim.fencing_token,
            "lease_acquired_at": claim.lease_acquired_at.isoformat(),
            "lease_expires_at": claim.lease_expires_at.isoformat(),
            "message_payload_hash": semantic_hash(claim.message),
            "receipt_json": to_wire_json(receipt),
            "worker_id": claim.worker_id,
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_time(raw: object, field_name: str) -> datetime:
    if type(raw) is not str:
        raise DataCorruption(f"{field_name} has the wrong SQLite type")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise DataCorruption(f"{field_name} is not an ISO datetime") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
        or parsed.isoformat() != raw
    ):
        raise DataCorruption(f"{field_name} is not canonical UTC")
    return parsed


def _sqlite_error(exc: sqlite3.Error, operation: str) -> StoreError:
    code = getattr(exc, "sqlite_errorcode", None)
    primary_code = code & 0xFF if type(code) is int else None
    if primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return ConcurrencyConflict(f"{operation} could not acquire the SQLite lock")
    if isinstance(exc, sqlite3.IntegrityError) or primary_code in (
        sqlite3.SQLITE_CORRUPT,
        sqlite3.SQLITE_NOTADB,
    ):
        return DataCorruption(f"{operation} detected SQLite corruption")
    return StoreUnavailable(f"{operation} failed in SQLite")


def _schema_statements() -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in render_sqlite().splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip() or len(statements) != len(_EXPECTED_TABLES) + 1:
        raise DataCorruption("generated SQLite schema statement universe is not closed")
    if statements[0].casefold() != "pragma foreign_keys = on;":
        raise DataCorruption("generated SQLite schema lacks its FK preamble")
    return tuple(statements)


def _expected_sql_by_table() -> dict[str, str]:
    statements = _schema_statements()[1:]
    return {
        table_name: statement.removesuffix(";")
        for table_name, statement in zip(_EXPECTED_TABLES, statements, strict=True)
    }


def _handoff_event_time(event: HandoffEvent) -> datetime:
    if type(event) is HandoffRequested:
        return event.requested_at
    if type(event) is HandoffAcknowledged:
        return event.acknowledged_at
    if type(event) is HandoffEffectFailed:
        return event.failed_at
    if type(event) is HandoffCancelled:
        return event.cancelled_at
    raise TypeError("event must be an exact HandoffEvent")


def _handoff_event_id(event: HandoffEvent) -> str:
    if type(event) is HandoffRequested:
        return event.source_event_id
    if type(event) is HandoffAcknowledged:
        return event.receipt_id
    if type(event) is HandoffEffectFailed:
        return event.effect_id
    if type(event) is HandoffCancelled:
        return f"handoff-cancellation:{event.handoff_id}"
    raise TypeError("event must be an exact HandoffEvent")


def _payment_event_time(event: PaymentEvent) -> datetime:
    if type(event) is PaymentMethodSelected:
        return event.selected_at
    if type(event) is FinancialSummaryRecorded:
        return event.recorded_at
    if type(event) is FinancialConfirmationReceived:
        return event.confirmed_at
    if type(event) is PaymentEvidenceRecorded:
        return event.recorded_at
    if type(event) is SettlementStarted:
        return event.started_at
    if type(event) is SettlementFinished:
        return event.finished_at
    if type(event) is PaymentExpired:
        return event.expired_at
    if type(event) is PaymentCancelled:
        return event.cancelled_at
    raise TypeError("event must be an exact PaymentEvent")


def _handoff_noop(state: HandoffWorkflow) -> HandoffTransition:
    return HandoffTransition(
        state=state,
        status=HandoffTransitionStatus.NOOP,
        reason=HandoffTransitionReason.IDENTICAL_REPLAY,
        events=(),
        effect_jobs=(),
    )


def _payment_noop(state: PaymentWorkflow) -> PaymentTransition:
    return PaymentTransition(
        state=state,
        status=PaymentTransitionStatus.NOOP,
        reason=PaymentTransitionReason.IDENTICAL_REPLAY,
        events=(),
        commands=(),
    )


class SQLiteFollowupUnitOfWork:
    """One owned SQLite connection for atomic follow-up workflow writes."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        _factory_token: object,
    ) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise TypeError("SQLiteFollowupUnitOfWork must be created with open()")
        self._connection = connection
        self._closed = False

    @classmethod
    def open(
        cls,
        path_or_connection: Path | str | sqlite3.Connection,
    ) -> "SQLiteFollowupUnitOfWork":
        connection: sqlite3.Connection | None = None
        caller_supplied = type(path_or_connection) is sqlite3.Connection
        caller_configured = False
        original_isolation_level: str | None = None
        original_foreign_keys: int | None = None

        def cleanup_rejected_connection() -> None:
            if connection is None:
                return
            if caller_supplied:
                if not caller_configured:
                    return
                try:
                    if connection.in_transaction:
                        connection.rollback()
                    if original_foreign_keys is not None:
                        connection.execute(
                            f"PRAGMA foreign_keys = {'ON' if original_foreign_keys else 'OFF'}"
                        )
                    connection.isolation_level = original_isolation_level
                except sqlite3.Error:
                    pass
                return
            try:
                connection.close()
            except sqlite3.Error:
                pass

        try:
            if caller_supplied:
                connection = path_or_connection
                if connection.in_transaction:
                    raise ValueError("SQLite connection must not have an open transaction")
                original_isolation_level = connection.isolation_level
                original_foreign_keys = connection.execute(
                    "PRAGMA foreign_keys"
                ).fetchone()[0]
                connection.isolation_level = None
                caller_configured = True
            elif isinstance(path_or_connection, Path):
                if path_or_connection.exists() and not path_or_connection.is_file():
                    raise ValueError("SQLite path must be a file or absent")
                connection = sqlite3.connect(
                    path_or_connection,
                    isolation_level=None,
                    timeout=5.0,
                )
            elif type(path_or_connection) is str:
                if not path_or_connection or "\x00" in path_or_connection:
                    raise ValueError("SQLite path text must be non-empty and canonical")
                connection = sqlite3.connect(
                    path_or_connection,
                    isolation_level=None,
                    timeout=5.0,
                )
            else:
                raise TypeError(
                    "path_or_connection must be Path, str, or sqlite3.Connection"
                )
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
                raise DataCorruption("SQLite foreign keys could not be enabled")
            store = cls(connection, _factory_token=_FACTORY_TOKEN)
            store._initialize_or_validate_schema()
            return store
        except sqlite3.Error as exc:
            cleanup_rejected_connection()
            raise _sqlite_error(exc, "open") from exc
        except BaseException:
            cleanup_rejected_connection()
            raise

    def __enter__(self) -> "SQLiteFollowupUnitOfWork":
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
            raise _sqlite_error(exc, "close") from exc
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreError("SQLiteFollowupUnitOfWork is closed")

    @contextmanager
    def _transaction(self, operation: str = "transaction") -> Iterator[None]:
        self._ensure_open()
        if self._connection.in_transaction:
            raise StoreError("nested transactions are forbidden")
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
                raise _sqlite_error(exc, operation) from exc
            raise

    def _validate_connection_namespace(self) -> None:
        databases = tuple(
            row[1] for row in self._connection.execute("PRAGMA database_list")
        )
        attached = tuple(name for name in databases if name not in ("main", "temp"))
        if attached:
            raise DataCorruption(
                f"SQLite follow-up connection must not attach databases: {attached}"
            )
        temporary_objects = tuple(
            self._connection.execute(
                "SELECT type, name FROM sqlite_temp_master "
                "WHERE name NOT GLOB 'sqlite_*' ORDER BY type, name"
            )
        )
        if temporary_objects:
            raise DataCorruption(
                "SQLite follow-up connection must not contain TEMP schema objects"
            )

    def _table_rows(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            row
            for row in self._connection.execute(
                "SELECT name, sql FROM main.sqlite_master "
                "WHERE type='table' ORDER BY rowid"
            )
            if not row[0].startswith("sqlite_")
        )

    def _initialize_or_validate_schema(self) -> None:
        self._validate_connection_namespace()
        rows = self._table_rows()
        if not rows:
            with self._transaction("initialize_schema"):
                for statement in _schema_statements()[1:]:
                    qualified = statement.replace(
                        "CREATE TABLE ",
                        "CREATE TABLE main.",
                        1,
                    )
                    self._connection.execute(qualified)
            rows = self._table_rows()
        names = tuple(row[0] for row in rows)
        if names != _EXPECTED_TABLES:
            raise DataCorruption(
                f"SQLite table universe mismatch: expected={_EXPECTED_TABLES}, found={names}"
            )
        expected_sql = _expected_sql_by_table()
        for name, actual_sql in rows:
            if actual_sql != expected_sql[name]:
                raise DataCorruption(f"SQLite table definition drift: {name}")
        if self._connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise DataCorruption("SQLite foreign keys are disabled")
        if tuple(
            self._connection.execute(
                "SELECT name FROM main.sqlite_master WHERE type='trigger' ORDER BY name"
            )
        ):
            raise DataCorruption("SQLite follow-up schema must not contain triggers")
        if tuple(self._connection.execute("PRAGMA main.foreign_key_check")):
            raise DataCorruption("SQLite follow-up schema contains foreign key violations")

    def _handoff_workflow_row(self, handoff_id: str):
        return self._connection.execute(
            "SELECT handoff_id, incident_key, revision, status, lead_key_hash, "
            "state_json, state_hash, created_at, updated_at "
            "FROM main.handoff_workflows WHERE handoff_id=?",
            (_require_id(handoff_id, "handoff_id"),),
        ).fetchone()

    def _payment_workflow_row(self, payment_id: str):
        return self._connection.execute(
            "SELECT payment_id, revision, payment_version, economic_signature, "
            "status, state_json, state_hash, created_at, updated_at "
            "FROM main.payment_workflows WHERE payment_id=?",
            (_require_id(payment_id, "payment_id"),),
        ).fetchone()

    @staticmethod
    def _decode_canonical(raw: object, digest: object, expected_type: type, label: str):
        if type(raw) is not str or type(digest) is not str:
            raise DataCorruption(f"{label} bytes/hash have wrong SQLite types")
        if _digest(raw) != digest:
            raise DataCorruption(f"{label} hash mismatch")
        try:
            value = from_wire_json(raw, expected_type)
            canonical = to_wire_json(value)
        except (TypeError, ValueError) as exc:
            raise DataCorruption(f"{label} wire value is invalid") from exc
        if canonical != raw or semantic_hash(value) != digest:
            raise DataCorruption(f"{label} wire value is noncanonical")
        return value

    @staticmethod
    def _decode_handoff_receipt_record(
        raw: object,
        digest: object,
    ) -> tuple[HandoffReceipt, dict[str, object]]:
        if type(raw) is not str or type(digest) is not str or _digest(raw) != digest:
            raise DataCorruption("handoff receipt record bytes/hash are divergent")
        try:
            payload = json.loads(raw)
            canonical = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DataCorruption("handoff receipt record is not valid JSON") from exc
        expected_data = {
            "claim_owner",
            "delivery_attempts",
            "delivery_id",
            "delivery_version",
            "fencing_token",
            "lease_acquired_at",
            "lease_expires_at",
            "message_payload_hash",
            "receipt_json",
            "worker_id",
        }
        if (
            type(payload) is not dict
            or set(payload) != {"schema_version", "type", "data"}
            or payload.get("schema_version") != 1
            or type(payload.get("schema_version")) is not int
            or payload.get("type") != "handoff_receipt_record"
            or type(payload.get("data")) is not dict
            or set(payload["data"]) != expected_data
            or canonical != raw
        ):
            raise DataCorruption("handoff receipt record is noncanonical")
        data = payload["data"]
        if (
            type(data["delivery_attempts"]) is not int
            or data["delivery_attempts"] < 1
            or type(data["fencing_token"]) is not int
            or data["fencing_token"] != data["delivery_attempts"]
            or type(data["delivery_version"]) is not int
            or data["delivery_version"] < 1
            or type(data["delivery_id"]) is not str
            or type(data["claim_owner"]) is not str
            or type(data["worker_id"]) is not str
            or type(data["message_payload_hash"]) is not str
            or type(data["receipt_json"]) is not str
        ):
            raise DataCorruption("handoff receipt record fields have invalid types")
        try:
            _require_id(data["worker_id"], "handoff receipt worker_id")
            _require_id(data["delivery_id"], "handoff receipt delivery_id")
            _require_id(data["claim_owner"], "handoff receipt claim_owner")
        except ValueError as exc:
            raise DataCorruption("handoff receipt record IDs are invalid") from exc
        expected_owner = _handoff_claim_owner(
            data["worker_id"],
            data["delivery_id"],
            data["delivery_version"],
        )
        if data["claim_owner"] != expected_owner:
            raise DataCorruption("handoff receipt claim owner is divergent")
        acquired = _canonical_time(
            data["lease_acquired_at"],
            "handoff receipt lease_acquired_at",
        )
        expires = _canonical_time(
            data["lease_expires_at"],
            "handoff receipt lease_expires_at",
        )
        if expires <= acquired:
            raise DataCorruption("handoff receipt record lease is impossible")
        try:
            receipt = from_wire_json(data["receipt_json"], HandoffReceipt)
            canonical_receipt = to_wire_json(receipt)
        except (TypeError, ValueError) as exc:
            raise DataCorruption("handoff receipt payload is invalid") from exc
        if canonical_receipt != data["receipt_json"]:
            raise DataCorruption("handoff receipt payload is noncanonical")
        return receipt, data

    def _handoff_outbox_jobs(self, handoff_id: str) -> tuple[HandoffEffectJob, ...]:
        rows = tuple(
            self._connection.execute(
                "SELECT message_id, idempotency_key, effect_id, handoff_id, kind, "
                "template_id, payload_json, payload_hash, status, claim_owner, "
                "fencing_token, lease_acquired_at, lease_expires_at, "
                "delivery_attempts, delivered_at, receipt_hash, created_at, updated_at "
                "FROM main.handoff_outbox WHERE handoff_id=? ORDER BY effect_id",
                (handoff_id,),
            )
        )
        jobs: list[HandoffEffectJob] = []
        for row in rows:
            job = self._decode_canonical(
                row[6], row[7], HandoffEffectJob, "handoff outbox payload"
            )
            immutable = (
                job.effect_id,
                job.effect_id,
                job.effect_id,
                handoff_id,
                job.kind.value,
                job.kind.value,
                to_wire_json(job),
                semantic_hash(job),
            )
            if row[:8] != immutable:
                raise DataCorruption("handoff outbox immutable binding is divergent")
            if type(row[10]) is not int or type(row[13]) is not int:
                raise DataCorruption("handoff outbox counters have wrong SQLite types")
            token, attempts = row[10], row[13]
            if token < 0 or attempts < 0 or token != attempts:
                raise DataCorruption("handoff outbox counters are impossible")
            created_at = _canonical_time(row[16], "handoff outbox created_at")
            updated_at = _canonical_time(row[17], "handoff outbox updated_at")
            if created_at != job.created_at or updated_at < created_at:
                raise DataCorruption("handoff outbox chronology is divergent")
            status = row[8]
            if status == "pending":
                valid = (
                    row[9] is None
                    and row[11] is None
                    and row[12] is None
                    and row[14] is None
                    and row[15] is None
                )
            elif status == "leased":
                valid = (
                    type(row[9]) is str
                    and bool(row[9])
                    and token >= 1
                    and attempts >= 1
                    and type(row[11]) is str
                    and type(row[12]) is str
                    and row[14] is None
                    and row[15] is None
                )
                if valid:
                    acquired = _canonical_time(
                        row[11], "handoff outbox lease_acquired_at"
                    )
                    expires = _canonical_time(
                        row[12], "handoff outbox lease_expires_at"
                    )
                    valid = created_at <= acquired < expires and updated_at == acquired
            elif status == "delivered":
                valid = (
                    type(row[9]) is str
                    and bool(row[9])
                    and token >= 1
                    and row[11] is None
                    and row[12] is None
                    and attempts >= 1
                    and type(row[14]) is str
                    and type(row[15]) is str
                )
                if valid:
                    delivered_at = _canonical_time(
                        row[14], "handoff outbox delivered_at"
                    )
                    valid = created_at <= delivered_at <= updated_at
            else:
                valid = False
            if not valid:
                raise DataCorruption("handoff outbox operational state is impossible")

            receipt_row = self._connection.execute(
                "SELECT receipt_id, idempotency_key, message_id, receipt_json, "
                "receipt_hash, delivered_at FROM main.handoff_receipts "
                "WHERE message_id=?",
                (job.effect_id,),
            ).fetchone()
            if status != "delivered":
                if receipt_row is not None:
                    raise DataCorruption("undelivered handoff outbox owns a receipt")
            else:
                if receipt_row is None:
                    raise DataCorruption("delivered handoff outbox lacks its receipt")
                receipt, claim_record = self._decode_handoff_receipt_record(
                    receipt_row[3],
                    receipt_row[4],
                )
                claim_acquired = _canonical_time(
                    claim_record["lease_acquired_at"],
                    "handoff receipt claim acquired_at",
                )
                claim_expires = _canonical_time(
                    claim_record["lease_expires_at"],
                    "handoff receipt claim expires_at",
                )
                if (
                    receipt_row[0] != receipt.receipt_id
                    or receipt_row[1] != receipt.idempotency_key
                    or receipt_row[2] != receipt.message_id
                    or receipt_row[5] != receipt.delivered_at.isoformat()
                    or receipt.message_id != job.effect_id
                    or receipt.idempotency_key != job.effect_id
                    or receipt.delivered_at.isoformat() != row[14]
                    or receipt_row[4] != row[15]
                    or claim_record["claim_owner"] != row[9]
                    or claim_record["message_payload_hash"] != row[7]
                    or claim_record["delivery_id"] != receipt.delivery_id
                    or claim_record["delivery_version"] != receipt.delivery_version
                    or claim_record["fencing_token"] != token
                    or claim_record["delivery_attempts"] != attempts
                    or not claim_acquired <= receipt.delivered_at < claim_expires
                ):
                    raise DataCorruption("handoff receipt binding is divergent")
            jobs.append(job)
        return tuple(jobs)

    @staticmethod
    def _policy_from_jobs(jobs: tuple[HandoffEffectJob, ...]) -> HandoffEffectPolicy:
        kinds = {job.kind.value for job in jobs}
        if len(kinds) != len(jobs) or "customer_acknowledgement" not in kinds:
            raise DataCorruption("handoff outbox job universe is invalid")
        if kinds == {"customer_acknowledgement"}:
            return HandoffEffectPolicy.default_email_disabled()
        if kinds == {"customer_acknowledgement", "internal_email"}:
            return HandoffEffectPolicy(
                queue_state=EffectRequirement.REQUIRED,
                customer_acknowledgement=EffectRequirement.REQUIRED,
                internal_email=EffectRequirement.OPTIONAL,
            )
        raise DataCorruption("handoff outbox contains an unsupported effect kind")

    def _load_handoff(self, handoff_id: str) -> tuple[HandoffWorkflow, int]:
        row = self._handoff_workflow_row(handoff_id)
        if row is None:
            raise HandoffNotFound(f"handoff not found: {handoff_id}")
        state = self._decode_canonical(row[5], row[6], HandoffWorkflow, "handoff state")
        events_rows = tuple(
            self._connection.execute(
                "SELECT event_id, handoff_id, revision, event_type, event_json, "
                "event_hash, occurred_at FROM main.handoff_events "
                "WHERE handoff_id=? ORDER BY revision",
                (handoff_id,),
            )
        )
        if not events_rows or tuple(item[2] for item in events_rows) != tuple(
            range(1, len(events_rows) + 1)
        ):
            raise DataCorruption("handoff event revisions are not contiguous")
        events: list[HandoffEvent] = []
        for event_row in events_rows:
            event_type = _HANDOFF_EVENT_BY_NAME.get(event_row[3])
            if event_type is None:
                raise DataCorruption("handoff event type is outside the closed universe")
            event = self._decode_canonical(
                event_row[4], event_row[5], event_type, "handoff event"
            )
            if (
                event_row[0] != _handoff_event_id(event)
                or event_row[1] != handoff_id
                or event_row[6] != _handoff_event_time(event).isoformat()
            ):
                raise DataCorruption("handoff event metadata is divergent")
            events.append(event)
        if type(events[0]) is not HandoffRequested:
            raise DataCorruption("handoff history does not start with HandoffRequested")
        jobs = self._handoff_outbox_jobs(handoff_id)
        policy = self._policy_from_jobs(jobs)
        opened = new_handoff(events[0], policy)
        replayed = opened.state
        expected_jobs = list(opened.effect_jobs)
        for event in events[1:]:
            try:
                transition = reduce_handoff(replayed, event)
            except (TypeError, ValueError) as exc:
                raise DataCorruption("handoff history cannot be replayed") from exc
            if transition.status not in (
                HandoffTransitionStatus.APPLIED,
                HandoffTransitionStatus.CONFLICT,
            ):
                raise DataCorruption("handoff history contains a reducer no-op/rejection")
            replayed = transition.state
            expected_jobs.extend(transition.effect_jobs)
        if tuple(sorted(jobs, key=lambda job: job.effect_id)) != tuple(
            sorted(expected_jobs, key=lambda job: job.effect_id)
        ):
            raise DataCorruption("handoff outbox does not match reducer jobs")
        acknowledgement_job = next(
            job
            for job in jobs
            if job.kind is HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT
        )
        acknowledgement_receipt = self._connection.execute(
            "SELECT receipt_id, delivered_at FROM main.handoff_receipts WHERE message_id=?",
            (acknowledgement_job.effect_id,),
        ).fetchone()
        acknowledgement = replayed.acknowledgement
        if acknowledgement_receipt is None:
            if acknowledgement is not None:
                raise DataCorruption("handoff acknowledgement lacks its durable receipt")
        elif (
            acknowledgement is None
            or acknowledgement.effect_id != acknowledgement_job.effect_id
            or acknowledgement.receipt_id != acknowledgement_receipt[0]
            or acknowledgement.acknowledged_at.isoformat() != acknowledgement_receipt[1]
        ):
            raise DataCorruption("handoff acknowledgement receipt binding is divergent")
        failure_effect_ids = {failure.effect_id for failure in replayed.effect_failures}
        for job in jobs:
            operational = self._connection.execute(
                "SELECT status, delivery_attempts FROM main.handoff_outbox "
                "WHERE message_id=?",
                (job.effect_id,),
            ).fetchone()
            if (
                operational[0] == "pending"
                and operational[1] > 0
                and job.effect_id not in failure_effect_ids
            ):
                raise DataCorruption(
                    "released handoff outbox lacks its effect-failure history"
                )
            if job.effect_id in failure_effect_ids and operational[1] == 0:
                raise DataCorruption(
                    "handoff effect-failure history lacks a delivery attempt"
                )
        revision = len(events)
        if (
            replayed != state
            or state.policy != policy
            or row[0] != state.request.handoff_id
            or row[1] != state.request.incident_key
            or type(row[2]) is not int
            or row[2] != revision
            or row[3] != state.status.value
            or row[4] != state.request.lead_key_hash
            or row[7] != state.request.requested_at.isoformat()
            or row[8] != _handoff_event_time(events[-1]).isoformat()
        ):
            raise DataCorruption("handoff workflow row disagrees with full replay")
        return state, revision

    def load_handoff(self, handoff_id: str) -> HandoffWorkflow:
        self._ensure_open()
        handoff_id = _require_id(handoff_id, "handoff_id")
        try:
            return self._load_handoff(handoff_id)[0]
        except sqlite3.Error as exc:
            raise _sqlite_error(exc, "load_handoff") from exc

    def _insert_handoff_event(
        self,
        owner_handoff_id: str,
        revision: int,
        event: HandoffEvent,
    ) -> None:
        raw = to_wire_json(event)
        self._connection.execute(
            "INSERT INTO main.handoff_events "
            "(event_id, handoff_id, revision, event_type, event_json, event_hash, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _handoff_event_id(event),
                owner_handoff_id,
                revision,
                type(event).__name__,
                raw,
                semantic_hash(event),
                _handoff_event_time(event).isoformat(),
            ),
        )

    def _insert_handoff_jobs(self, jobs: tuple[HandoffEffectJob, ...]) -> None:
        for job in jobs:
            raw = to_wire_json(job)
            timestamp = job.created_at.isoformat()
            self._connection.execute(
                "INSERT INTO main.handoff_outbox "
                "(message_id, idempotency_key, effect_id, handoff_id, kind, "
                "template_id, payload_json, payload_hash, status, claim_owner, "
                "fencing_token, lease_acquired_at, lease_expires_at, "
                "delivery_attempts, delivered_at, receipt_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, 0, NULL, NULL, "
                "0, NULL, NULL, ?, ?)",
                (
                    job.effect_id,
                    job.effect_id,
                    job.effect_id,
                    job.handoff_id,
                    job.kind.value,
                    job.kind.value,
                    raw,
                    semantic_hash(job),
                    timestamp,
                    timestamp,
                ),
            )

    def open_handoff(
        self,
        request: HandoffRequested,
        policy: HandoffEffectPolicy,
    ) -> HandoffTransition:
        if type(request) is not HandoffRequested:
            raise TypeError("request must be exact HandoffRequested")
        if type(policy) is not HandoffEffectPolicy:
            raise TypeError("policy must be exact HandoffEffectPolicy")
        transition = new_handoff(request, policy)
        with self._transaction("open_handoff"):
            collisions = tuple(
                self._connection.execute(
                    "SELECT handoff_id FROM main.handoff_workflows "
                    "WHERE handoff_id=? OR incident_key=? ORDER BY handoff_id",
                    (request.handoff_id, request.incident_key),
                )
            )
            event_owner = self._connection.execute(
                "SELECT handoff_id FROM main.handoff_events WHERE event_id=?",
                (request.source_event_id,),
            ).fetchone()
            if collisions or event_owner is not None:
                if collisions == ((request.handoff_id,),) and (
                    event_owner is None or event_owner == (request.handoff_id,)
                ):
                    existing, _ = self._load_handoff(request.handoff_id)
                    if existing.request == request and existing.policy == policy:
                        return _handoff_noop(existing)
                raise IdentityConflict("handoff identity already exists with divergent data")
            state = transition.state
            raw = to_wire_json(state)
            timestamp = request.requested_at.isoformat()
            self._connection.execute(
                "INSERT INTO main.handoff_workflows "
                "(handoff_id, incident_key, revision, status, lead_key_hash, state_json, "
                "state_hash, created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    request.handoff_id,
                    request.incident_key,
                    state.status.value,
                    request.lead_key_hash,
                    raw,
                    semantic_hash(state),
                    timestamp,
                    timestamp,
                ),
            )
            self._insert_handoff_event(request.handoff_id, 1, request)
            self._insert_handoff_jobs(transition.effect_jobs)
        return transition

    def apply_handoff(
        self,
        handoff_id: str,
        expected_revision: int,
        event: HandoffEvent,
    ) -> HandoffTransition:
        handoff_id = _require_id(handoff_id, "handoff_id")
        expected_revision = _require_revision(expected_revision)
        if type(event) not in _HANDOFF_EVENT_TYPES:
            raise TypeError("event must be an exact HandoffEvent")
        if type(event) in _OPERATIONAL_HANDOFF_EVENTS:
            raise UnsupportedEffect(
                "handoff delivery event requires Task 7 outbox and receipt persistence"
            )
        with self._transaction("apply_handoff"):
            current, revision = self._load_handoff(handoff_id)
            event_id = _handoff_event_id(event)
            existing_row = self._connection.execute(
                "SELECT handoff_id, event_type, event_json, event_hash FROM main.handoff_events "
                "WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing_row is not None:
                if (
                    existing_row[0] == handoff_id
                    and existing_row[1] == type(event).__name__
                    and existing_row[2] == to_wire_json(event)
                    and existing_row[3] == semantic_hash(event)
                ):
                    return _handoff_noop(current)
                raise IdentityConflict("handoff event identity has divergent data")
            if revision != expected_revision:
                raise ConcurrencyConflict(
                    f"stale handoff revision: expected={expected_revision}, current={revision}"
                )
            transition = reduce_handoff(current, event)
            if transition.status in (
                HandoffTransitionStatus.NOOP,
                HandoffTransitionStatus.REJECTED,
            ):
                return transition
            next_revision = revision + 1
            raw = to_wire_json(transition.state)
            updated_at = _handoff_event_time(event).isoformat()
            updated = self._connection.execute(
                "UPDATE main.handoff_workflows SET revision=?, status=?, state_json=?, "
                "state_hash=?, updated_at=? WHERE handoff_id=? AND revision=?",
                (
                    next_revision,
                    transition.state.status.value,
                    raw,
                    semantic_hash(transition.state),
                    updated_at,
                    handoff_id,
                    revision,
                ),
            )
            if updated.rowcount != 1:
                raise ConcurrencyConflict("handoff optimistic revision was lost")
            self._insert_handoff_event(handoff_id, next_revision, event)
            self._insert_handoff_jobs(transition.effect_jobs)
        return transition

    @staticmethod
    def _claim_job(claim: HandoffOutboxClaim) -> HandoffEffectJob:
        if type(claim) is not HandoffOutboxClaim:
            raise TypeError("claim must be exact HandoffOutboxClaim")
        if type(claim.message) is not HandoffEffectJob:
            raise TypeError("claim message must be exact HandoffEffectJob")
        return claim.message

    def _handoff_outbox_row(self, message_id: str):
        return self._connection.execute(
            "SELECT message_id, idempotency_key, effect_id, handoff_id, kind, "
            "template_id, payload_json, payload_hash, status, claim_owner, "
            "fencing_token, lease_acquired_at, lease_expires_at, "
            "delivery_attempts, delivered_at, receipt_hash, created_at, updated_at "
            "FROM main.handoff_outbox WHERE message_id=?",
            (_require_id(message_id, "message_id"),),
        ).fetchone()

    def _assert_claim_message_binding(
        self,
        claim: HandoffOutboxClaim,
        row,
    ) -> HandoffEffectJob:
        message = self._claim_job(claim)
        if row is None:
            raise StaleLease("handoff outbox message no longer exists")
        persisted = self._decode_canonical(
            row[6], row[7], HandoffEffectJob, "handoff outbox payload"
        )
        expected = (
            message.effect_id,
            message.effect_id,
            message.effect_id,
            message.handoff_id,
            message.kind.value,
            message.kind.value,
            to_wire_json(message),
            semantic_hash(message),
        )
        if persisted != message or row[:8] != expected:
            raise IdentityConflict("handoff claim message binding is divergent")
        return message

    def _assert_live_handoff_claim(
        self,
        claim: HandoffOutboxClaim,
        *,
        now: datetime,
    ) -> tuple[HandoffEffectJob, object]:
        row = self._handoff_outbox_row(self._claim_job(claim).effect_id)
        message = self._assert_claim_message_binding(claim, row)
        owner = _handoff_claim_owner(
            claim.worker_id,
            claim.delivery_id,
            claim.delivery_version,
        )
        if (
            row[8] != "leased"
            or row[9] != owner
            or type(row[10]) is not int
            or row[10] != claim.fencing_token
            or row[11] != claim.lease_acquired_at.isoformat()
            or row[12] != claim.lease_expires_at.isoformat()
            or type(row[13]) is not int
            or row[13] != claim.delivery_attempts
            or claim.fencing_token != claim.delivery_attempts
            or now < claim.lease_acquired_at
            or now >= claim.lease_expires_at
            or row[14] is not None
            or row[15] is not None
        ):
            raise StaleLease("handoff outbox lease is stale, expired, or divergent")
        return message, row

    def _persist_operational_handoff_transition(
        self,
        current: HandoffWorkflow,
        revision: int,
        event: HandoffEvent,
        transition: HandoffTransition,
    ) -> None:
        if (
            transition.status is not HandoffTransitionStatus.APPLIED
            or transition.events != (event,)
            or transition.effect_jobs
        ):
            raise DataCorruption("operational handoff reducer transition is not exact")
        next_revision = revision + 1
        raw = to_wire_json(transition.state)
        cursor = self._connection.execute(
            "UPDATE main.handoff_workflows SET revision=?, status=?, state_json=?, "
            "state_hash=?, updated_at=? WHERE handoff_id=? AND revision=?",
            (
                next_revision,
                transition.state.status.value,
                raw,
                semantic_hash(transition.state),
                _handoff_event_time(event).isoformat(),
                current.request.handoff_id,
                revision,
            ),
        )
        if cursor.rowcount != 1:
            raise ConcurrencyConflict("handoff operational revision was lost")
        self._insert_handoff_event(
            current.request.handoff_id,
            next_revision,
            event,
        )

    def claim_handoff_outbox(
        self,
        *,
        worker_id: str,
        delivery_id: str,
        delivery_version: int,
        now: datetime,
        lease_ttl: timedelta,
    ) -> HandoffOutboxClaim | None:
        worker_id = _require_id(worker_id, "worker_id")
        delivery_id = _require_id(delivery_id, "delivery_id")
        if type(delivery_version) is not int or delivery_version < 1:
            raise ValueError("delivery_version must be an integer >= 1")
        now = _require_utc_input(now, "now")
        lease_ttl = _require_lease_ttl(lease_ttl)
        try:
            expires_at = now + lease_ttl
        except OverflowError as exc:
            raise ValueError("lease_ttl overflows datetime range") from exc
        owner = _handoff_claim_owner(worker_id, delivery_id, delivery_version)
        with self._transaction("claim_handoff_outbox"):
            candidate = self._connection.execute(
                "SELECT message_id, handoff_id FROM main.handoff_outbox "
                "WHERE (status='pending' AND claim_owner IS NULL) "
                "OR (status='leased' AND lease_expires_at<=?) "
                "ORDER BY CASE kind WHEN 'customer_acknowledgement' THEN 0 ELSE 1 END, "
                "created_at, message_id LIMIT 1",
                (now.isoformat(),),
            ).fetchone()
            if candidate is None:
                return None
            self._load_handoff(candidate[1])
            row = self._handoff_outbox_row(candidate[0])
            message = self._decode_canonical(
                row[6], row[7], HandoffEffectJob, "handoff outbox payload"
            )
            created_at = _canonical_time(row[16], "handoff outbox created_at")
            updated_at = _canonical_time(row[17], "handoff outbox updated_at")
            if now < created_at or now < updated_at:
                raise ValueError("claim time cannot predate the handoff outbox state")
            cursor = self._connection.execute(
                "UPDATE main.handoff_outbox SET status='leased', claim_owner=?, "
                "fencing_token=fencing_token+1, lease_acquired_at=?, "
                "lease_expires_at=?, delivery_attempts=delivery_attempts+1, updated_at=? "
                "WHERE message_id=? AND fencing_token=? AND delivery_attempts=? AND "
                "((status='pending' AND claim_owner IS NULL AND lease_acquired_at IS NULL "
                "AND lease_expires_at IS NULL) OR "
                "(status='leased' AND lease_expires_at<=?))",
                (
                    owner,
                    now.isoformat(),
                    expires_at.isoformat(),
                    now.isoformat(),
                    message.effect_id,
                    row[10],
                    row[13],
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflict("handoff outbox claim lost its compare-and-swap")
            updated = self._handoff_outbox_row(message.effect_id)
            return HandoffOutboxClaim(
                message=message,
                worker_id=worker_id,
                delivery_id=delivery_id,
                delivery_version=delivery_version,
                fencing_token=updated[10],
                lease_acquired_at=_canonical_time(
                    updated[11], "handoff outbox lease_acquired_at"
                ),
                lease_expires_at=_canonical_time(
                    updated[12], "handoff outbox lease_expires_at"
                ),
                delivery_attempts=updated[13],
            )

    def release_handoff_outbox(
        self,
        claim: HandoffOutboxClaim,
        *,
        now: datetime,
    ) -> HandoffTransition:
        self._claim_job(claim)
        now = _require_utc_input(now, "now")
        with self._transaction("release_handoff_outbox"):
            current, revision = self._load_handoff(claim.message.handoff_id)
            message, row = self._assert_live_handoff_claim(claim, now=now)
            cursor = self._connection.execute(
                "UPDATE main.handoff_outbox SET status='pending', claim_owner=NULL, "
                "lease_acquired_at=NULL, lease_expires_at=NULL, updated_at=? "
                "WHERE message_id=? AND status='leased' AND claim_owner=? "
                "AND fencing_token=? AND lease_acquired_at=? AND lease_expires_at=? "
                "AND delivery_attempts=?",
                (
                    now.isoformat(),
                    message.effect_id,
                    row[9],
                    claim.fencing_token,
                    claim.lease_acquired_at.isoformat(),
                    claim.lease_expires_at.isoformat(),
                    claim.delivery_attempts,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLease("handoff outbox release lost its live lease")
            existing = next(
                (
                    failure
                    for failure in current.effect_failures
                    if failure.effect_id == message.effect_id
                ),
                None,
            )
            if existing is not None:
                return _handoff_noop(current)
            event = HandoffEffectFailed(
                handoff_id=message.handoff_id,
                incident_key=message.incident_key,
                effect_id=message.effect_id,
                kind=message.kind,
                failure_code=HandoffEffectFailureCode.EFFECT_UNAVAILABLE,
                failed_at=now,
            )
            transition = reduce_handoff(current, event)
            self._persist_operational_handoff_transition(
                current,
                revision,
                event,
                transition,
            )
            return transition

    def complete_handoff_outbox(
        self,
        claim: HandoffOutboxClaim,
        receipt: HandoffReceipt,
        *,
        now: datetime,
    ) -> HandoffTransition:
        self._claim_job(claim)
        if type(receipt) is not HandoffReceipt:
            raise TypeError("receipt must be exact HandoffReceipt")
        now = _require_utc_input(now, "now")
        with self._transaction("complete_handoff_outbox"):
            current, revision = self._load_handoff(claim.message.handoff_id)
            row = self._handoff_outbox_row(claim.message.effect_id)
            message = self._assert_claim_message_binding(claim, row)
            receipt_raw = _handoff_receipt_record(claim, receipt)
            receipt_hash = _digest(receipt_raw)
            existing = self._connection.execute(
                "SELECT receipt_id, idempotency_key, message_id, receipt_json, "
                "receipt_hash, delivered_at FROM main.handoff_receipts "
                "WHERE receipt_id=? OR idempotency_key=? OR message_id=?",
                (receipt.receipt_id, receipt.idempotency_key, receipt.message_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing[0] == receipt.receipt_id
                    and existing[1] == receipt.idempotency_key
                    and existing[2] == receipt.message_id
                    and existing[3] == receipt_raw
                    and existing[4] == receipt_hash
                    and existing[5] == receipt.delivered_at.isoformat()
                    and row[8] == "delivered"
                    and row[14] == receipt.delivered_at.isoformat()
                    and row[15] == receipt_hash
                ):
                    return _handoff_noop(current)
                raise IdentityConflict("handoff receipt identity has divergent data")
            message, row = self._assert_live_handoff_claim(claim, now=now)
            if (
                receipt.message_id != message.effect_id
                or receipt.idempotency_key != message.effect_id
                or receipt.delivery_id != claim.delivery_id
                or receipt.delivery_version != claim.delivery_version
            ):
                raise IdentityConflict("handoff receipt is not bound to its live claim")
            if (
                receipt.delivered_at < message.created_at
                or receipt.delivered_at < claim.lease_acquired_at
                or receipt.delivered_at > now
            ):
                raise ValueError("handoff receipt chronology is invalid")
            cursor = self._connection.execute(
                "UPDATE main.handoff_outbox SET status='delivered', "
                "lease_acquired_at=NULL, lease_expires_at=NULL, delivered_at=?, "
                "receipt_hash=?, updated_at=? WHERE message_id=? AND status='leased' "
                "AND claim_owner=? AND fencing_token=? AND lease_acquired_at=? "
                "AND lease_expires_at=? AND delivery_attempts=?",
                (
                    receipt.delivered_at.isoformat(),
                    receipt_hash,
                    now.isoformat(),
                    message.effect_id,
                    row[9],
                    claim.fencing_token,
                    claim.lease_acquired_at.isoformat(),
                    claim.lease_expires_at.isoformat(),
                    claim.delivery_attempts,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLease("handoff outbox completion lost its live lease")
            self._connection.execute(
                "INSERT INTO main.handoff_receipts "
                "(receipt_id, idempotency_key, message_id, receipt_json, receipt_hash, "
                "delivered_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    receipt.receipt_id,
                    receipt.idempotency_key,
                    receipt.message_id,
                    receipt_raw,
                    receipt_hash,
                    receipt.delivered_at.isoformat(),
                ),
            )
            if message.kind is HandoffEffectKind.INTERNAL_EMAIL:
                return _handoff_noop(current)
            event = HandoffAcknowledged(
                handoff_id=message.handoff_id,
                incident_key=message.incident_key,
                effect_id=message.effect_id,
                receipt_id=receipt.receipt_id,
                acknowledged_at=receipt.delivered_at,
            )
            transition = reduce_handoff(current, event)
            self._persist_operational_handoff_transition(
                current,
                revision,
                event,
                transition,
            )
            return transition

    def _load_payment(self, payment_id: str) -> tuple[PaymentWorkflow, int]:
        row = self._payment_workflow_row(payment_id)
        if row is None:
            raise PaymentNotFound(f"payment not found: {payment_id}")
        state = self._decode_canonical(row[5], row[6], PaymentWorkflow, "payment state")
        event_rows = tuple(
            self._connection.execute(
                "SELECT event_id, payment_id, revision, payment_version, "
                "economic_signature, event_type, event_json, event_hash, occurred_at "
                "FROM main.payment_events WHERE payment_id=? ORDER BY revision",
                (payment_id,),
            )
        )
        if tuple(event_row[2] for event_row in event_rows) != tuple(
            range(1, len(event_rows) + 1)
        ):
            raise DataCorruption("payment event revisions are not contiguous")
        try:
            replayed = new_payment(
                state.subject.confirmed_reservation_anchor,
                state.policy,
            ).state
        except (TypeError, ValueError) as exc:
            raise DataCorruption("payment bootstrap cannot be reconstructed") from exc
        decoded_events: list[PaymentEvent] = []
        for event_row in event_rows:
            event_type = _PAYMENT_EVENT_BY_NAME.get(event_row[5])
            if event_type is None:
                raise DataCorruption("payment event type is outside the closed universe")
            event = self._decode_canonical(
                event_row[6], event_row[7], event_type, "payment event"
            )
            try:
                transition = reduce_payment(replayed, event)
            except (TypeError, ValueError) as exc:
                raise DataCorruption("payment history cannot be replayed") from exc
            if transition.status is not PaymentTransitionStatus.APPLIED:
                raise DataCorruption("payment history contains a reducer no-op")
            if transition.commands:
                raise DataCorruption("Task 6 payment history contains an operational command")
            replayed = transition.state
            if (
                event_row[0] != event.event_id
                or event_row[1] != payment_id
                or event_row[3] != replayed.subject.payment_version
                or event_row[4] != replayed.subject.economic_signature
                or event_row[8] != _payment_event_time(event).isoformat()
            ):
                raise DataCorruption("payment event metadata is divergent")
            decoded_events.append(event)
        revision = len(event_rows)
        updated_at = (
            state.subject.confirmed_reservation_anchor.confirmed_at
            if not decoded_events
            else _payment_event_time(decoded_events[-1])
        )
        if (
            replayed != state
            or tuple(decoded_events) != state.history
            or row[0] != state.subject.payment_id
            or type(row[1]) is not int
            or row[1] != revision
            or row[2] != state.subject.payment_version
            or row[3] != state.subject.economic_signature
            or row[4] != state.status.value
            or row[7] != state.subject.confirmed_reservation_anchor.confirmed_at.isoformat()
            or row[8] != updated_at.isoformat()
        ):
            raise DataCorruption("payment workflow row disagrees with full replay")
        return state, revision

    def load_payment(self, payment_id: str) -> PaymentWorkflow:
        self._ensure_open()
        payment_id = _require_id(payment_id, "payment_id")
        try:
            return self._load_payment(payment_id)[0]
        except sqlite3.Error as exc:
            raise _sqlite_error(exc, "load_payment") from exc

    def open_payment(
        self,
        anchor: ConfirmedReservationAnchor,
        policy: PaymentEffectPolicy,
    ) -> PaymentTransition:
        if type(anchor) is not ConfirmedReservationAnchor:
            raise TypeError("anchor must be exact ConfirmedReservationAnchor")
        if type(policy) is not PaymentEffectPolicy:
            raise TypeError("policy must be exact PaymentEffectPolicy")
        transition = new_payment(anchor, policy)
        state = transition.state
        payment_id = state.subject.payment_id
        with self._transaction("open_payment"):
            existing = self._payment_workflow_row(payment_id)
            if existing is not None:
                persisted, _ = self._load_payment(payment_id)
                if (
                    persisted.subject.confirmed_reservation_anchor == anchor
                    and persisted.policy == policy
                ):
                    return _payment_noop(persisted)
                raise IdentityConflict("payment identity already exists with divergent data")
            raw = to_wire_json(state)
            timestamp = anchor.confirmed_at.isoformat()
            self._connection.execute(
                "INSERT INTO main.payment_workflows "
                "(payment_id, revision, payment_version, economic_signature, status, "
                "state_json, state_hash, created_at, updated_at) "
                "VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payment_id,
                    state.subject.payment_version,
                    state.subject.economic_signature,
                    state.status.value,
                    raw,
                    semantic_hash(state),
                    timestamp,
                    timestamp,
                ),
            )
        return transition

    def _insert_payment_event(
        self,
        payment_id: str,
        revision: int,
        event: PaymentEvent,
        state: PaymentWorkflow,
    ) -> None:
        raw = to_wire_json(event)
        self._connection.execute(
            "INSERT INTO main.payment_events "
            "(event_id, payment_id, revision, payment_version, economic_signature, "
            "event_type, event_json, event_hash, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                payment_id,
                revision,
                state.subject.payment_version,
                state.subject.economic_signature,
                type(event).__name__,
                raw,
                semantic_hash(event),
                _payment_event_time(event).isoformat(),
            ),
        )

    def apply_payment(
        self,
        payment_id: str,
        expected_revision: int,
        event: PaymentEvent,
    ) -> PaymentTransition:
        payment_id = _require_id(payment_id, "payment_id")
        expected_revision = _require_revision(expected_revision)
        if type(event) not in _PAYMENT_EVENT_TYPES:
            raise TypeError("event must be an exact PaymentEvent")
        if type(event) in _OPERATIONAL_PAYMENT_EVENTS:
            raise UnsupportedEffect(
                "payment event requires Task 8/9 claim, ledger, or outbox persistence"
            )
        with self._transaction("apply_payment"):
            current, revision = self._load_payment(payment_id)
            existing_row = self._connection.execute(
                "SELECT payment_id, event_type, event_json, event_hash "
                "FROM main.payment_events WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
            if existing_row is not None:
                if (
                    existing_row[0] == payment_id
                    and existing_row[1] == type(event).__name__
                    and existing_row[2] == to_wire_json(event)
                    and existing_row[3] == semantic_hash(event)
                ):
                    return _payment_noop(current)
                raise IdentityConflict("payment event identity has divergent data")
            if revision != expected_revision:
                raise ConcurrencyConflict(
                    f"stale payment revision: expected={expected_revision}, current={revision}"
                )
            transition = reduce_payment(current, event)
            if transition.commands:
                raise UnsupportedEffect(
                    "payment event requires Task 8/9 claim, ledger, or outbox persistence"
                )
            if transition.status is not PaymentTransitionStatus.APPLIED:
                return transition
            next_revision = revision + 1
            raw = to_wire_json(transition.state)
            updated_at = _payment_event_time(event).isoformat()
            updated = self._connection.execute(
                "UPDATE main.payment_workflows SET revision=?, payment_version=?, "
                "economic_signature=?, status=?, state_json=?, state_hash=?, updated_at=? "
                "WHERE payment_id=? AND revision=?",
                (
                    next_revision,
                    transition.state.subject.payment_version,
                    transition.state.subject.economic_signature,
                    transition.state.status.value,
                    raw,
                    semantic_hash(transition.state),
                    updated_at,
                    payment_id,
                    revision,
                ),
            )
            if updated.rowcount != 1:
                raise ConcurrencyConflict("payment optimistic revision was lost")
            self._insert_payment_event(payment_id, next_revision, event, transition.state)
        return transition


__all__ = [
    "StoreError",
    "StoreUnavailable",
    "DataCorruption",
    "ConcurrencyConflict",
    "IdentityConflict",
    "StaleLease",
    "UnsupportedEffect",
    "HandoffNotFound",
    "PaymentNotFound",
    "SQLiteFollowupUnitOfWork",
]
