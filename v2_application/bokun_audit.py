"""Durable GET-only audit of monotonically confirmed Bókun bookings."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Iterator, Protocol

from reservation_domain import ExecutionCertainty, ServiceKind, loads_outcome
from reservation_execution import LedgerStatus
from reservation_execution.sqlite_store import SQLiteUnitOfWork


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_PROVIDER_REF_RE = re.compile(
    r"^bokun\.product\.([A-Za-z0-9_-]+)\.start\.([A-Za-z0-9_-]+)\.rate\.([A-Za-z0-9_-]+)$"
)


class BokunAuditStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    RETRYABLE_NOT_VISIBLE = "retryable_not_visible"
    MATCHED = "matched"
    DIVERGENT = "divergent"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"


class BokunAuditIdentityConflict(RuntimeError):
    """A deterministic task identity is bound to different immutable facts."""


@dataclass(frozen=True, slots=True)
class BokunAuditExpectedFacts:
    product_id: str = field(repr=False)
    provider_start_time_id: str = field(repr=False)
    provider_rate_id: str = field(repr=False)
    activity_date: str
    start_time: str
    adults: int
    children: int
    total: str
    currency: str
    status: str = "confirmed"

    def __post_init__(self) -> None:
        for value, name in (
            (self.product_id, "product_id"),
            (self.provider_start_time_id, "provider_start_time_id"),
            (self.provider_rate_id, "provider_rate_id"),
        ):
            _require_id(value, name)
        if date.fromisoformat(self.activity_date).isoformat() != self.activity_date:
            raise ValueError("activity_date must be canonical ISO date text")
        if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", self.start_time) is None:
            raise ValueError("start_time must be HH:MM")
        if type(self.adults) is not int or self.adults < 1:
            raise ValueError("adults must be a positive exact integer")
        if type(self.children) is not int or self.children < 0:
            raise ValueError("children must be a non-negative exact integer")
        if _amount(self.total) != self.total or Decimal(self.total) <= 0:
            raise ValueError("total must be canonical positive decimal text")
        if re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise ValueError("currency must be canonical")
        if self.status != "confirmed":
            raise ValueError("expected status must be confirmed")


@dataclass(frozen=True, slots=True)
class BokunAuditTask:
    task_id: str
    command_id: str
    booking_id: str = field(repr=False)
    expected: BokunAuditExpectedFacts = field(repr=False)
    max_attempts: int

    def __post_init__(self) -> None:
        _require_id(self.task_id, "task_id")
        _require_id(self.command_id, "command_id")
        _require_id(self.booking_id, "booking_id")
        if type(self.expected) is not BokunAuditExpectedFacts:
            raise TypeError("expected must be exact BokunAuditExpectedFacts")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive exact integer")


@dataclass(frozen=True, slots=True)
class BokunAuditLease:
    owner: str
    acquired_at: datetime
    expires_at: datetime
    fencing_token: int


@dataclass(frozen=True, slots=True)
class BokunAuditSnapshot:
    task: BokunAuditTask = field(repr=False)
    status: BokunAuditStatus
    attempts: int
    lease: BokunAuditLease | None


@dataclass(frozen=True, slots=True)
class BokunAuditProjectionResult:
    inserted: int
    replayed: int
    ignored: int


class BokunBookingGETAuditPort(Protocol):
    def get_booking(self, booking_id: str) -> object: ...


class SQLiteBokunAuditStore:
    """Private single-worker owner for bounded Bókun GET audit state."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("Bókun audit SQLite path must be absolute")
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._closed = False
        try:
            if str(self._connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() != "wal":
                raise RuntimeError("Bókun audit SQLite store requires WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bokun_audit_tasks (
                  task_id TEXT PRIMARY KEY,
                  command_id TEXT NOT NULL UNIQUE,
                  booking_id TEXT NOT NULL,
                  product_id TEXT NOT NULL,
                  provider_start_time_id TEXT NOT NULL,
                  provider_rate_id TEXT NOT NULL,
                  activity_date TEXT NOT NULL,
                  start_time TEXT NOT NULL,
                  adults INTEGER NOT NULL,
                  children INTEGER NOT NULL,
                  total TEXT NOT NULL,
                  currency TEXT NOT NULL,
                  expected_status TEXT NOT NULL,
                  max_attempts INTEGER NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('pending','leased','retryable_not_visible','matched','divergent','attempts_exhausted')),
                  attempts INTEGER NOT NULL,
                  lease_owner TEXT,
                  lease_acquired_at TEXT,
                  lease_expires_at TEXT,
                  fencing_token INTEGER NOT NULL DEFAULT 0
                ) STRICT;
                CREATE INDEX IF NOT EXISTS idx_bokun_audit_claim
                  ON bokun_audit_tasks(status, lease_expires_at, task_id);
                """
            )
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._closed:
            raise RuntimeError("Bókun audit store is closed")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def enqueue(self, task: BokunAuditTask) -> bool:
        if type(task) is not BokunAuditTask:
            raise TypeError("task must be exact BokunAuditTask")
        existing = self._connection.execute(
            "SELECT * FROM bokun_audit_tasks WHERE task_id=?", (task.task_id,)
        ).fetchone()
        if existing is not None:
            if self._snapshot(existing).task != task:
                raise BokunAuditIdentityConflict("Bókun audit task identity diverged")
            return False
        facts = task.expected
        try:
            self._connection.execute(
                "INSERT INTO bokun_audit_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',0,NULL,NULL,NULL,0)",
                (
                    task.task_id,
                    task.command_id,
                    task.booking_id,
                    facts.product_id,
                    facts.provider_start_time_id,
                    facts.provider_rate_id,
                    facts.activity_date,
                    facts.start_time,
                    facts.adults,
                    facts.children,
                    facts.total,
                    facts.currency,
                    facts.status,
                    task.max_attempts,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise BokunAuditIdentityConflict("Bókun audit task identity diverged") from exc
        return True

    def list_tasks(self) -> tuple[BokunAuditSnapshot, ...]:
        rows = self._connection.execute(
            "SELECT * FROM bokun_audit_tasks ORDER BY task_id"
        ).fetchall()
        return tuple(self._snapshot(row) for row in rows)

    def _claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> BokunAuditSnapshot | None:
        _require_id(worker_id, "worker_id")
        _utc(now)
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        expires = now + lease_ttl
        with self._transaction():
            self._connection.execute(
                "UPDATE bokun_audit_tasks SET status='pending',lease_owner=NULL,lease_acquired_at=NULL,lease_expires_at=NULL "
                "WHERE status='leased' AND lease_expires_at<=?",
                (now.isoformat(),),
            )
            row = self._connection.execute(
                "SELECT * FROM bokun_audit_tasks WHERE status IN ('pending','retryable_not_visible') "
                "AND attempts<max_attempts ORDER BY task_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            token = int(row["fencing_token"]) + 1
            updated = self._connection.execute(
                "UPDATE bokun_audit_tasks SET status='leased',lease_owner=?,lease_acquired_at=?,lease_expires_at=?,fencing_token=? "
                "WHERE task_id=? AND status IN ('pending','retryable_not_visible') AND fencing_token=?",
                (
                    worker_id,
                    now.isoformat(),
                    expires.isoformat(),
                    token,
                    row["task_id"],
                    row["fencing_token"],
                ),
            ).rowcount
            if updated != 1:
                raise RuntimeError("Bókun audit claim CAS lost")
            return self._snapshot(
                self._connection.execute(
                    "SELECT * FROM bokun_audit_tasks WHERE task_id=?", (row["task_id"],)
                ).fetchone()
            )

    def _resolve(
        self,
        claim: BokunAuditSnapshot,
        observed: BokunAuditStatus,
    ) -> BokunAuditSnapshot:
        if claim.status is not BokunAuditStatus.LEASED or claim.lease is None:
            raise ValueError("audit resolution requires leased snapshot")
        if observed not in {
            BokunAuditStatus.MATCHED,
            BokunAuditStatus.DIVERGENT,
            BokunAuditStatus.RETRYABLE_NOT_VISIBLE,
        }:
            raise ValueError("audit observation is outside closed outcomes")
        attempts = claim.attempts + 1
        status = observed
        if observed is BokunAuditStatus.RETRYABLE_NOT_VISIBLE and attempts >= claim.task.max_attempts:
            status = BokunAuditStatus.ATTEMPTS_EXHAUSTED
        with self._transaction():
            updated = self._connection.execute(
                "UPDATE bokun_audit_tasks SET status=?,attempts=?,lease_owner=NULL,lease_acquired_at=NULL,lease_expires_at=NULL "
                "WHERE task_id=? AND status='leased' AND lease_owner=? AND fencing_token=?",
                (
                    status.value,
                    attempts,
                    claim.task.task_id,
                    claim.lease.owner,
                    claim.lease.fencing_token,
                ),
            ).rowcount
            if updated != 1:
                raise RuntimeError("Bókun audit resolution CAS lost")
        row = self._connection.execute(
            "SELECT * FROM bokun_audit_tasks WHERE task_id=?",
            (claim.task.task_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Bókun audit task disappeared after resolution")
        return self._snapshot(row)

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> BokunAuditSnapshot:
        status = BokunAuditStatus(row["status"])
        lease = None
        if status is BokunAuditStatus.LEASED:
            lease = BokunAuditLease(
                owner=row["lease_owner"],
                acquired_at=datetime.fromisoformat(row["lease_acquired_at"]),
                expires_at=datetime.fromisoformat(row["lease_expires_at"]),
                fencing_token=row["fencing_token"],
            )
        return BokunAuditSnapshot(
            task=BokunAuditTask(
                task_id=row["task_id"],
                command_id=row["command_id"],
                booking_id=row["booking_id"],
                expected=BokunAuditExpectedFacts(
                    product_id=row["product_id"],
                    provider_start_time_id=row["provider_start_time_id"],
                    provider_rate_id=row["provider_rate_id"],
                    activity_date=row["activity_date"],
                    start_time=row["start_time"],
                    adults=row["adults"],
                    children=row["children"],
                    total=row["total"],
                    currency=row["currency"],
                    status=row["expected_status"],
                ),
                max_attempts=row["max_attempts"],
            ),
            status=status,
            attempts=row["attempts"],
            lease=lease,
        )


class BokunAuditProjector:
    def __init__(
        self,
        *,
        execution: SQLiteUnitOfWork,
        audit_store: SQLiteBokunAuditStore,
        max_attempts: int,
    ) -> None:
        if type(execution) is not SQLiteUnitOfWork:
            raise TypeError("execution must be exact SQLiteUnitOfWork")
        if type(audit_store) is not SQLiteBokunAuditStore:
            raise TypeError("audit_store must be exact SQLiteBokunAuditStore")
        if audit_store.path == execution.path.resolve() or audit_store.path.samefile(execution.path):
            raise ValueError("Bókun audit store must be separate from execution")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._execution = execution
        self._audit_store = audit_store
        self._max_attempts = max_attempts

    def run_once(self) -> BokunAuditProjectionResult:
        inserted = replayed = ignored = 0
        for command, ledger in self._execution.list_outcome_projection_inputs():
            if ledger.status is not LedgerStatus.OUTCOME_RECORDED or ledger.outcome_json is None:
                ignored += 1
                continue
            outcome = loads_outcome(ledger.outcome_json)
            reference = outcome.provider_reference
            if (
                outcome.certainty is not ExecutionCertainty.EFFECT_CONFIRMED
                or type(reference) is not str
                or not reference.startswith("provider:bokun:")
            ):
                ignored += 1
                continue
            activity_components = tuple(
                component
                for component in command.payload.components
                if component.service is ServiceKind.ACTIVITY
            )
            if len(activity_components) != 1:
                ignored += 1
                continue
            component = activity_components[0]
            provider = _PROVIDER_REF_RE.fullmatch(component.provider_ref)
            if (
                component.service is not ServiceKind.ACTIVITY
                or component.start_time is None
                or provider is None
            ):
                ignored += 1
                continue
            booking_id = reference.removeprefix("provider:bokun:")
            try:
                _require_id(booking_id, "booking_id")
            except ValueError:
                ignored += 1
                continue
            task = BokunAuditTask(
                task_id=bokun_audit_task_id(command.command_id),
                command_id=command.command_id,
                booking_id=booking_id,
                expected=BokunAuditExpectedFacts(
                    product_id=provider.group(1),
                    provider_start_time_id=provider.group(2),
                    provider_rate_id=provider.group(3),
                    activity_date=component.start_date.isoformat(),
                    start_time=component.start_time,
                    adults=component.party.adults,
                    children=component.party.children,
                    total=f"{component.total.amount:.2f}",
                    currency=component.total.currency,
                ),
                max_attempts=self._max_attempts,
            )
            if self._audit_store.enqueue(task):
                inserted += 1
            else:
                replayed += 1
        return BokunAuditProjectionResult(inserted, replayed, ignored)


class BokunAuditWorker:
    def __init__(
        self,
        *,
        store: SQLiteBokunAuditStore,
        port: BokunBookingGETAuditPort,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        if type(store) is not SQLiteBokunAuditStore:
            raise TypeError("store must be exact SQLiteBokunAuditStore")
        if not callable(getattr(port, "get_booking", None)):
            raise TypeError("port must expose get_booking")
        self._store = store
        self._port = port
        self._worker_id = _require_id(worker_id, "worker_id")
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> BokunAuditSnapshot | None:
        claim = self._store._claim(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return None
        try:
            payload = self._port.get_booking(claim.task.booking_id)
        except RuntimeError:
            observed = BokunAuditStatus.RETRYABLE_NOT_VISIBLE
        else:
            observed = _validate_observation(payload, claim.task)
        return self._store._resolve(claim, observed)


def bokun_audit_task_id(command_id: str) -> str:
    _require_id(command_id, "command_id")
    return "bokun-audit:" + hashlib.sha256(command_id.encode("utf-8")).hexdigest()


def _validate_observation(payload: object, task: BokunAuditTask) -> BokunAuditStatus:
    if type(payload) is not dict:
        return BokunAuditStatus.RETRYABLE_NOT_VISIBLE
    booking_id = _first(payload, "bookingId", "id", "confirmationCode")
    if booking_id is None:
        return BokunAuditStatus.RETRYABLE_NOT_VISIBLE
    if booking_id != task.booking_id:
        return BokunAuditStatus.DIVERGENT
    status = _first(payload, "status", "bookingStatus")
    if status is None:
        return BokunAuditStatus.RETRYABLE_NOT_VISIBLE
    if status.casefold() not in {"confirmed", "completed", "paid"}:
        return BokunAuditStatus.DIVERGENT
    amount = _amount(payload.get("totalPrice", payload.get("total")))
    if amount is None:
        return BokunAuditStatus.RETRYABLE_NOT_VISIBLE
    expected = task.expected
    if amount != expected.total:
        return BokunAuditStatus.DIVERGENT
    currency = _first(payload, "currency", "currencyCode")
    if currency is not None and currency != expected.currency:
        return BokunAuditStatus.DIVERGENT
    activity = payload.get("activityBooking")
    if activity is None and type(payload.get("activityBookings")) is list:
        values = payload["activityBookings"]
        activity = values[0] if len(values) == 1 else None
    if type(activity) is not dict:
        return BokunAuditStatus.RETRYABLE_NOT_VISIBLE
    required = {
        "activityId": expected.product_id,
        "date": expected.activity_date,
        "startTimeId": expected.provider_start_time_id,
        "rateId": expected.provider_rate_id,
    }
    for name, value in required.items():
        observed = activity.get(name)
        if observed is None:
            return BokunAuditStatus.RETRYABLE_NOT_VISIBLE
        if str(observed) != value:
            return BokunAuditStatus.DIVERGENT
    if "startTime" in activity and activity["startTime"] != expected.start_time:
        return BokunAuditStatus.DIVERGENT
    adults = activity.get("adults")
    children = activity.get("children")
    if adults is not None or children is not None:
        if type(adults) is not int or type(children) is not int:
            return BokunAuditStatus.DIVERGENT
        if (adults, children) != (expected.adults, expected.children):
            return BokunAuditStatus.DIVERGENT
    else:
        participants = activity.get("participants")
        if type(participants) is not int:
            return BokunAuditStatus.RETRYABLE_NOT_VISIBLE
        if participants != expected.adults + expected.children:
            return BokunAuditStatus.DIVERGENT
    return BokunAuditStatus.MATCHED


def _first(payload: dict[str, object], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if type(value) in {str, int} and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text
    return None


def _amount(value: object) -> str | None:
    if type(value) not in {str, int, float} or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
        canonical = parsed.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0 or parsed != canonical:
        return None
    return f"{canonical:.2f}"


def _require_id(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical identifier")
    return value


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be exact UTC")
    return value.astimezone(timezone.utc)


__all__ = [
    "BokunAuditExpectedFacts",
    "BokunAuditIdentityConflict",
    "BokunAuditProjector",
    "BokunAuditSnapshot",
    "BokunAuditStatus",
    "BokunAuditTask",
    "BokunAuditWorker",
    "BokunBookingGETAuditPort",
    "SQLiteBokunAuditStore",
    "bokun_audit_task_id",
]
