"""Durable GET-only audit of confirmed Cloudbeds reservations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Protocol

from reservation_domain import ExecutionCertainty, ServiceKind, loads_outcome
from reservation_execution import LedgerStatus
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from v2_contracts.providers import canonical_cloudbeds_reference


class CloudbedsAuditStatus(str, Enum):
    """Closed durable state machine for one reservation audit."""

    PENDING = "pending"
    LEASED = "leased"
    RETRYABLE_NOT_VISIBLE = "retryable_not_visible"
    MATCHED = "matched"
    DIVERGENT = "divergent"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"


class CloudbedsAuditIdentityConflict(RuntimeError):
    """A deterministic task ID is already bound to different immutable facts."""


@dataclass(frozen=True, slots=True)
class CloudbedsAuditExpectedFacts:
    property_id: str
    start_date: str
    end_date: str
    adults: int
    children: int
    amount: str
    currency: str
    status: str

    def __post_init__(self) -> None:
        _require_text(self.property_id, "property_id")
        _require_date(self.start_date, "start_date")
        _require_date(self.end_date, "end_date")
        if date.fromisoformat(self.end_date) <= date.fromisoformat(self.start_date):
            raise ValueError("audit stay interval must be positive")
        if type(self.adults) is not int or self.adults < 1:
            raise ValueError("audit adults must be a positive exact integer")
        if type(self.children) is not int or self.children < 0:
            raise ValueError("audit children must be a non-negative exact integer")
        if re.fullmatch(r"(?:0|[1-9][0-9]*)\.[0-9]{2}", self.amount) is None:
            raise ValueError("audit amount must be canonical decimal text")
        try:
            parsed_amount = Decimal(self.amount)
        except InvalidOperation as exc:
            raise ValueError("audit amount must be canonical decimal text") from exc
        if not parsed_amount.is_finite() or parsed_amount <= 0:
            raise ValueError("audit amount must be positive")
        if re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise ValueError("audit currency must be canonical")
        if self.status != "confirmed":
            raise ValueError("audit expected status must be confirmed")


@dataclass(frozen=True, slots=True)
class CloudbedsAuditTask:
    task_id: str
    command_id: str
    reservation_id: str = field(repr=False)
    expected: CloudbedsAuditExpectedFacts
    max_attempts: int

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.command_id, "command_id")
        canonical_cloudbeds_reference(self.reservation_id)
        if type(self.expected) is not CloudbedsAuditExpectedFacts:
            raise TypeError("expected must be exact CloudbedsAuditExpectedFacts")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive exact integer")


@dataclass(frozen=True, slots=True)
class CloudbedsAuditLease:
    owner: str
    acquired_at: datetime
    expires_at: datetime
    fencing_token: int

    def __post_init__(self) -> None:
        _require_text(self.owner, "lease owner")
        acquired_at = _utc(self.acquired_at, "lease acquired_at")
        expires_at = _utc(self.expires_at, "lease expires_at")
        if expires_at <= acquired_at:
            raise ValueError("audit lease expiry must follow acquisition")
        if type(self.fencing_token) is not int or self.fencing_token < 1:
            raise ValueError("audit fencing token must be a positive exact integer")


@dataclass(frozen=True, slots=True)
class CloudbedsAuditSnapshot:
    task: CloudbedsAuditTask
    status: CloudbedsAuditStatus
    attempts: int
    lease: CloudbedsAuditLease | None

    def __post_init__(self) -> None:
        if type(self.task) is not CloudbedsAuditTask:
            raise TypeError("task must be exact CloudbedsAuditTask")
        if type(self.status) is not CloudbedsAuditStatus:
            raise TypeError("status must be exact CloudbedsAuditStatus")
        if (
            type(self.attempts) is not int
            or self.attempts < 0
            or self.attempts > self.task.max_attempts
        ):
            raise ValueError("audit attempts are outside the closed budget")
        if self.status is CloudbedsAuditStatus.LEASED:
            if type(self.lease) is not CloudbedsAuditLease:
                raise ValueError("leased audit snapshot requires an exact lease")
        elif self.lease is not None:
            raise ValueError("only a leased audit snapshot may retain a lease")


@dataclass(frozen=True, slots=True)
class CloudbedsAuditProjectionResult:
    inserted: int
    replayed: int
    ignored: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.inserted, self.replayed, self.ignored)
        ):
            raise ValueError("audit projection counters must be non-negative")


class CloudbedsReservationGETAuditPort(Protocol):
    """The auditor's only provider capability."""

    def get_reservation(self, reservation_id: str) -> object:
        """Read one reservation by its persisted principal ID."""


class SQLiteCloudbedsAuditStore:
    """Private, separate SQLite state for bounded Cloudbeds GET audits."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("Cloudbeds audit SQLite path must be absolute")
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._closed = False
        self._connection = sqlite3.connect(path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        try:
            mode = self._connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeError("Cloudbeds audit SQLite store requires WAL mode")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._create_schema()
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def enqueue(self, task: CloudbedsAuditTask) -> bool:
        if type(task) is not CloudbedsAuditTask:
            raise TypeError("task must be exact CloudbedsAuditTask")
        with self._transaction():
            existing = self._select_snapshot(task.task_id)
            if existing is not None:
                if existing.task != task:
                    raise CloudbedsAuditIdentityConflict(
                        "Cloudbeds audit task identity is already bound"
                    )
                return False
            try:
                self._connection.execute(
                    "INSERT INTO cloudbeds_audit_tasks ("
                    "task_id,command_id,reservation_id,property_id,start_date,end_date,"
                    "adults,children,amount,currency,expected_status,max_attempts,"
                    "status,attempts,lease_owner,lease_acquired_at,lease_expires_at,"
                    "fencing_token) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        task.task_id,
                        task.command_id,
                        task.reservation_id,
                        task.expected.property_id,
                        task.expected.start_date,
                        task.expected.end_date,
                        task.expected.adults,
                        task.expected.children,
                        task.expected.amount,
                        task.expected.currency,
                        task.expected.status,
                        task.max_attempts,
                        CloudbedsAuditStatus.PENDING.value,
                        0,
                        None,
                        None,
                        None,
                        None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise CloudbedsAuditIdentityConflict(
                    "Cloudbeds audit task identity is already bound"
                ) from exc
            return True

    def load(self, task_id: str) -> CloudbedsAuditSnapshot:
        _require_text(task_id, "task_id")
        self._ensure_open()
        snapshot = self._select_snapshot(task_id)
        if snapshot is None:
            raise KeyError(task_id)
        return snapshot

    def list_tasks(self) -> tuple[CloudbedsAuditSnapshot, ...]:
        self._ensure_open()
        rows = self._connection.execute(
            "SELECT * FROM cloudbeds_audit_tasks ORDER BY task_id"
        ).fetchall()
        return tuple(self._snapshot(row) for row in rows)

    def _claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> CloudbedsAuditSnapshot | None:
        _require_text(worker_id, "worker_id")
        instant = _utc(now, "now")
        ttl = _lease_ttl(lease_ttl)
        try:
            expires_at = instant + ttl
        except OverflowError as exc:
            raise ValueError("lease_ttl overflows datetime range") from exc
        with self._transaction():
            self._connection.execute(
                "UPDATE cloudbeds_audit_tasks SET status=?,lease_owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,fencing_token=NULL "
                "WHERE status=? AND lease_expires_at<=? AND attempts>=max_attempts",
                (
                    CloudbedsAuditStatus.ATTEMPTS_EXHAUSTED.value,
                    CloudbedsAuditStatus.LEASED.value,
                    instant.isoformat(),
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM cloudbeds_audit_tasks WHERE attempts<max_attempts AND ("
                "status IN (?,?) OR (status=? AND lease_expires_at<=?)) "
                "ORDER BY task_id LIMIT 1",
                (
                    CloudbedsAuditStatus.PENDING.value,
                    CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE.value,
                    CloudbedsAuditStatus.LEASED.value,
                    instant.isoformat(),
                ),
            ).fetchone()
            if row is None:
                return None
            attempts = row["attempts"] + 1
            self._connection.execute(
                "UPDATE cloudbeds_audit_tasks SET status=?,attempts=?,lease_owner=?,"
                "lease_acquired_at=?,lease_expires_at=?,fencing_token=? WHERE task_id=?",
                (
                    CloudbedsAuditStatus.LEASED.value,
                    attempts,
                    worker_id,
                    instant.isoformat(),
                    expires_at.isoformat(),
                    attempts,
                    row["task_id"],
                ),
            )
            claimed = self._select_snapshot(row["task_id"])
            if claimed is None:
                raise RuntimeError("claimed Cloudbeds audit task disappeared")
            return claimed

    def _resolve(
        self,
        claim: CloudbedsAuditSnapshot,
        status: CloudbedsAuditStatus,
    ) -> CloudbedsAuditSnapshot:
        if type(claim) is not CloudbedsAuditSnapshot:
            raise TypeError("claim must be exact CloudbedsAuditSnapshot")
        if claim.status is not CloudbedsAuditStatus.LEASED or claim.lease is None:
            raise ValueError("claim must contain a live audit lease")
        if status not in (
            CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE,
            CloudbedsAuditStatus.MATCHED,
            CloudbedsAuditStatus.DIVERGENT,
        ):
            raise ValueError("audit resolution status is not allowed")
        resolved = (
            CloudbedsAuditStatus.ATTEMPTS_EXHAUSTED
            if status is CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
            and claim.attempts >= claim.task.max_attempts
            else status
        )
        lease = claim.lease
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE cloudbeds_audit_tasks SET status=?,lease_owner=NULL,"
                "lease_acquired_at=NULL,lease_expires_at=NULL,fencing_token=NULL "
                "WHERE task_id=? AND status=? AND attempts=? AND lease_owner=? "
                "AND lease_acquired_at=? AND lease_expires_at=? AND fencing_token=?",
                (
                    resolved.value,
                    claim.task.task_id,
                    CloudbedsAuditStatus.LEASED.value,
                    claim.attempts,
                    lease.owner,
                    lease.acquired_at.isoformat(),
                    lease.expires_at.isoformat(),
                    lease.fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Cloudbeds audit lease changed before resolution")
            snapshot = self._select_snapshot(claim.task.task_id)
            if snapshot is None:
                raise RuntimeError("resolved Cloudbeds audit task disappeared")
            return snapshot

    def _create_schema(self) -> None:
        statuses = ",".join(f"'{item.value}'" for item in CloudbedsAuditStatus)
        self._connection.execute(
            f"""CREATE TABLE IF NOT EXISTS cloudbeds_audit_tasks (
                task_id TEXT PRIMARY KEY NOT NULL,
                command_id TEXT UNIQUE NOT NULL,
                reservation_id TEXT NOT NULL,
                property_id TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                adults INTEGER NOT NULL,
                children INTEGER NOT NULL,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                expected_status TEXT NOT NULL,
                max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
                status TEXT NOT NULL CHECK(status IN ({statuses})),
                attempts INTEGER NOT NULL CHECK(attempts >= 0 AND attempts <= max_attempts),
                lease_owner TEXT,
                lease_acquired_at TEXT,
                lease_expires_at TEXT,
                fencing_token INTEGER,
                CHECK(
                    (status = 'leased' AND lease_owner IS NOT NULL
                     AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL
                     AND fencing_token IS NOT NULL)
                    OR
                    (status <> 'leased' AND lease_owner IS NULL
                     AND lease_acquired_at IS NULL AND lease_expires_at IS NULL
                     AND fencing_token IS NULL)
                )
            ) STRICT"""
        )

    def _select_snapshot(self, task_id: str) -> CloudbedsAuditSnapshot | None:
        row = self._connection.execute(
            "SELECT * FROM cloudbeds_audit_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        return None if row is None else self._snapshot(row)

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> CloudbedsAuditSnapshot:
        status = CloudbedsAuditStatus(row["status"])
        lease = None
        if status is CloudbedsAuditStatus.LEASED:
            lease = CloudbedsAuditLease(
                owner=row["lease_owner"],
                acquired_at=_parse_utc(row["lease_acquired_at"], "lease acquired_at"),
                expires_at=_parse_utc(row["lease_expires_at"], "lease expires_at"),
                fencing_token=row["fencing_token"],
            )
        return CloudbedsAuditSnapshot(
            task=CloudbedsAuditTask(
                task_id=row["task_id"],
                command_id=row["command_id"],
                reservation_id=row["reservation_id"],
                expected=CloudbedsAuditExpectedFacts(
                    property_id=row["property_id"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    adults=row["adults"],
                    children=row["children"],
                    amount=row["amount"],
                    currency=row["currency"],
                    status=row["expected_status"],
                ),
                max_attempts=row["max_attempts"],
            ),
            status=status,
            attempts=row["attempts"],
            lease=lease,
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._ensure_open()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Cloudbeds audit SQLite store is closed")


class CloudbedsAuditProjector:
    """Project only confirmed Cloudbeds private references into audit tasks."""

    def __init__(
        self,
        *,
        execution: SQLiteUnitOfWork,
        audit_store: SQLiteCloudbedsAuditStore,
        property_id: str,
        max_attempts: int,
    ) -> None:
        if type(execution) is not SQLiteUnitOfWork:
            raise TypeError("execution must be exact SQLiteUnitOfWork")
        if type(audit_store) is not SQLiteCloudbedsAuditStore:
            raise TypeError("audit_store must be exact SQLiteCloudbedsAuditStore")
        _require_text(property_id, "property_id")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive exact integer")
        if audit_store.path == execution.path.resolve() or audit_store.path.samefile(
            execution.path
        ):
            raise ValueError("Cloudbeds audit store must be separate from execution")
        self._execution = execution
        self._audit_store = audit_store
        self._property_id = property_id
        self._max_attempts = max_attempts

    def run_once(self) -> CloudbedsAuditProjectionResult:
        inserted = 0
        replayed = 0
        ignored = 0
        prefix = "provider:cloudbeds:"
        for command, ledger in self._execution.list_outcome_projection_inputs():
            if ledger.status is not LedgerStatus.OUTCOME_RECORDED or ledger.outcome_json is None:
                ignored += 1
                continue
            outcome = loads_outcome(ledger.outcome_json)
            reference = outcome.provider_reference
            if (
                outcome.certainty is not ExecutionCertainty.EFFECT_CONFIRMED
                or type(reference) is not str
                or not reference.startswith(prefix)
            ):
                ignored += 1
                continue
            reservation_id = reference.removeprefix(prefix)
            try:
                canonical = canonical_cloudbeds_reference(reservation_id)
            except ValueError:
                ignored += 1
                continue
            component = command.payload.components[0]
            if (
                canonical != reservation_id
                or component.service is not ServiceKind.LODGING
                or component.start_date is None
                or component.end_date is None
            ):
                ignored += 1
                continue
            task = CloudbedsAuditTask(
                task_id=cloudbeds_audit_task_id(command.command_id),
                command_id=command.command_id,
                reservation_id=reservation_id,
                expected=CloudbedsAuditExpectedFacts(
                    property_id=self._property_id,
                    start_date=component.start_date.isoformat(),
                    end_date=component.end_date.isoformat(),
                    adults=component.party.adults,
                    children=component.party.children,
                    amount=f"{component.total.amount:.2f}",
                    currency=component.total.currency,
                    status="confirmed",
                ),
                max_attempts=self._max_attempts,
            )
            if self._audit_store.enqueue(task):
                inserted += 1
            else:
                replayed += 1
        return CloudbedsAuditProjectionResult(inserted, replayed, ignored)


class CloudbedsAuditWorker:
    """Lease one audit task and issue exactly one GET-capability call."""

    def __init__(
        self,
        *,
        store: SQLiteCloudbedsAuditStore,
        port: CloudbedsReservationGETAuditPort,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        if type(store) is not SQLiteCloudbedsAuditStore:
            raise TypeError("store must be exact SQLiteCloudbedsAuditStore")
        if not callable(getattr(port, "get_reservation", None)):
            raise TypeError("port must provide get_reservation")
        _require_text(worker_id, "worker_id")
        _lease_ttl(lease_ttl)
        self._store = store
        self._port = port
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> CloudbedsAuditSnapshot | None:
        claim = self._store._claim(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return None
        try:
            payload = self._port.get_reservation(claim.task.reservation_id)
        except RuntimeError:
            status = CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
        else:
            status = _validate_observation(payload, task=claim.task)
        return self._store._resolve(claim, status)


def cloudbeds_audit_task_id(command_id: str) -> str:
    """Derive one deterministic private task ID from a command ID."""

    value = _require_text(command_id, "command_id")
    return "cloudbeds-audit:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_observation(
    payload: object,
    *,
    task: CloudbedsAuditTask,
) -> CloudbedsAuditStatus:
    if type(payload) is not dict:
        return CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
    data = payload.get("data")
    if payload.get("success") is not True or type(data) is not dict:
        return CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
    reservation_claims: list[str] = []
    for alias in ("reservationID", "reservationId", "reservation_id"):
        if alias not in data:
            continue
        try:
            reservation_claims.append(canonical_cloudbeds_reference(data[alias]))
        except ValueError:
            return CloudbedsAuditStatus.DIVERGENT
    if not reservation_claims:
        return CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
    if (
        len(set(reservation_claims)) != 1
        or reservation_claims[0] != task.reservation_id
    ):
        return CloudbedsAuditStatus.DIVERGENT
    expected = task.expected
    fields: tuple[tuple[str, object], ...] = (
        ("propertyID", expected.property_id),
        ("startDate", expected.start_date),
        ("endDate", expected.end_date),
        ("status", expected.status),
    )
    if any(name not in data for name, _ in fields):
        return CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
    for name, value in fields:
        observed = data[name]
        if type(observed) is not type(value) or observed != value:
            return CloudbedsAuditStatus.DIVERGENT

    if "total" not in data:
        return CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
    observed_amount = _canonical_observed_amount(data["total"])
    if observed_amount is None or observed_amount != expected.amount:
        return CloudbedsAuditStatus.DIVERGENT
    if "currency" in data and (
        type(data["currency"]) is not str or data["currency"] != expected.currency
    ):
        return CloudbedsAuditStatus.DIVERGENT

    party_claims: list[tuple[int, int]] = []
    top_party_fields = ("adults", "children")
    if any(name in data for name in top_party_fields):
        if not all(name in data for name in top_party_fields):
            return CloudbedsAuditStatus.DIVERGENT
        adults = _canonical_observed_count(data["adults"])
        children = _canonical_observed_count(data["children"])
        if adults is None or adults < 1 or children is None:
            return CloudbedsAuditStatus.DIVERGENT
        party_claims.append((adults, children))

    room_fields = ("assigned", "unassigned")
    if any(name in data for name in room_fields):
        if not all(type(data.get(name)) is list for name in room_fields):
            return CloudbedsAuditStatus.DIVERGENT
        adults = 0
        children = 0
        for room in (*data["assigned"], *data["unassigned"]):
            if type(room) is not dict:
                return CloudbedsAuditStatus.DIVERGENT
            room_adults = _canonical_observed_count(room.get("adults"))
            room_children = _canonical_observed_count(room.get("children"))
            if room_adults is None or room_children is None:
                return CloudbedsAuditStatus.DIVERGENT
            adults += room_adults
            children += room_children
        party_claims.append((adults, children))

    if not party_claims:
        return CloudbedsAuditStatus.RETRYABLE_NOT_VISIBLE
    if any(claim != (expected.adults, expected.children) for claim in party_claims):
        return CloudbedsAuditStatus.DIVERGENT
    return CloudbedsAuditStatus.MATCHED


def _canonical_observed_amount(value: object) -> str | None:
    if type(value) not in (str, int, float):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    canonical = amount.quantize(Decimal("0.01"))
    if canonical != amount:
        return None
    return f"{canonical:.2f}"


def _canonical_observed_count(value: object) -> int | None:
    if type(value) is int:
        return value if value >= 0 else None
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None:
        return None
    return int(value)


def _require_text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be non-empty exact text")
    return value


def _require_date(value: object, name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be canonical ISO date text")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be canonical ISO date text") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be canonical ISO date text")
    return value


def _utc(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value.astimezone(timezone.utc)


def _parse_utc(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise RuntimeError(f"persisted {name} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"persisted {name} is invalid") from exc
    return _utc(parsed, name)


def _lease_ttl(value: object) -> timedelta:
    if type(value) is not timedelta or value <= timedelta(0):
        raise ValueError("lease_ttl must be a positive exact timedelta")
    return value


__all__ = [
    "CloudbedsAuditExpectedFacts",
    "CloudbedsAuditIdentityConflict",
    "CloudbedsAuditLease",
    "CloudbedsAuditProjectionResult",
    "CloudbedsAuditProjector",
    "CloudbedsAuditSnapshot",
    "CloudbedsAuditStatus",
    "CloudbedsAuditTask",
    "CloudbedsAuditWorker",
    "CloudbedsReservationGETAuditPort",
    "SQLiteCloudbedsAuditStore",
    "cloudbeds_audit_task_id",
]
