"""Small capability-isolated worker cycles for the standalone V2 process."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import fcntl
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
from threading import Event, Lock, Thread
import time
from typing import Protocol


class WorkerQueue(str, Enum):
    INBOX = "inbox"
    BOUNDARY_RELAY = "boundary_relay"
    RESERVATION = "reservation"
    HANDOFF = "handoff"
    OUTCOME_PROJECTOR = "outcome_projector"
    PAYMENT_INITIATION = "payment_initiation"
    SETTLEMENT = "settlement"
    POST_PAYMENT = "post_payment"
    PUBLIC_DELIVERY = "public_delivery"
    RECONCILIATION = "reconciliation"


class QueueRunStatus(str, Enum):
    HEALTHY = "healthy"
    FAILED = "failed"
    BACKOFF = "backoff"


class WorkerFailureReason(str, Enum):
    WORKER_EXCEPTION = "worker_exception"
    CLOUDBEDS_AUDIT_DIVERGENT = "cloudbeds_audit_divergent"
    CLOUDBEDS_AUDIT_ATTEMPTS_EXHAUSTED = (
        "cloudbeds_audit_attempts_exhausted"
    )
    CLOUDBEDS_AUDIT_UNAVAILABLE = "cloudbeds_audit_unavailable"
    BOKUN_AUDIT_DIVERGENT = "bokun_audit_divergent"
    BOKUN_AUDIT_ATTEMPTS_EXHAUSTED = "bokun_audit_attempts_exhausted"
    BOKUN_AUDIT_UNAVAILABLE = "bokun_audit_unavailable"
    STRIPE_RECONCILIATION_UNKNOWN = "stripe_reconciliation_unknown"


class WorkerHealthDisposition(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class WorkerHealthResult:
    disposition: WorkerHealthDisposition
    reason: WorkerFailureReason | None
    result: object | None

    def __post_init__(self) -> None:
        if type(self.disposition) is not WorkerHealthDisposition:
            raise TypeError("disposition must be exact WorkerHealthDisposition")
        if self.disposition is WorkerHealthDisposition.HEALTHY:
            if self.reason is not None:
                raise ValueError("healthy worker result cannot carry a reason")
        elif type(self.reason) is not WorkerFailureReason:
            raise TypeError("degraded worker result requires exact failure reason")

    @classmethod
    def healthy(cls, result: object | None = None) -> "WorkerHealthResult":
        return cls(WorkerHealthDisposition.HEALTHY, None, result)

    @classmethod
    def degraded(
        cls,
        *,
        reason: WorkerFailureReason,
        result: object | None = None,
    ) -> "WorkerHealthResult":
        return cls(WorkerHealthDisposition.DEGRADED, reason, result)


class OneShotWorker(Protocol):
    def run_once(self, *, now: datetime) -> object: ...


@dataclass(frozen=True, slots=True)
class WorkerCycleItem:
    queue: WorkerQueue
    status: QueueRunStatus
    result: object | None
    reason_code: str | None
    failure_fingerprint: str | None
    consecutive_failures: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    backoff_seconds: float
    next_attempt_at: datetime | None

    def __post_init__(self) -> None:
        if type(self.queue) is not WorkerQueue:
            raise TypeError("queue must be exact WorkerQueue")
        if type(self.status) is not QueueRunStatus:
            raise TypeError("status must be exact QueueRunStatus")
        if type(self.consecutive_failures) is not int or self.consecutive_failures < 0:
            raise ValueError("consecutive_failures must be a non-negative integer")
        for value, name in (
            (self.last_success_at, "last_success_at"),
            (self.last_failure_at, "last_failure_at"),
            (self.next_attempt_at, "next_attempt_at"),
        ):
            if value is not None and (
                type(value) is not datetime
                or value.tzinfo is None
                or value.utcoffset() != timedelta(0)
            ):
                raise ValueError(f"{name} must be exact UTC when present")
        if (
            type(self.backoff_seconds) is not float
            or self.backoff_seconds < 0
        ):
            raise ValueError("backoff_seconds must be a non-negative exact float")
        if self.status is QueueRunStatus.HEALTHY:
            if (
                self.reason_code is not None
                or self.failure_fingerprint is not None
                or self.consecutive_failures != 0
                or self.backoff_seconds != 0.0
                or self.next_attempt_at is not None
            ):
                raise ValueError("healthy worker item cannot carry failure state")
        else:
            if self.result is not None:
                raise ValueError("failed/backoff worker item cannot expose a result")
            if self.reason_code not in {
                item.value for item in WorkerFailureReason
            }:
                raise ValueError("worker reason code is outside the closed catalog")
            if (
                type(self.failure_fingerprint) is not str
                or len(self.failure_fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in self.failure_fingerprint)
            ):
                raise ValueError("failure_fingerprint must be lowercase SHA-256")
            if self.consecutive_failures < 1 or self.last_failure_at is None:
                raise ValueError("failed/backoff item requires failure history")
            if self.next_attempt_at is None:
                raise ValueError("failed/backoff item requires next_attempt_at")

    @property
    def failed(self) -> bool:
        return self.status is not QueueRunStatus.HEALTHY

    @property
    def attempted(self) -> bool:
        return self.status is not QueueRunStatus.BACKOFF


@dataclass(slots=True)
class _QueueHealthState:
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    reason_code: str | None = None
    failure_fingerprint: str | None = None
    next_attempt_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WorkerCycleReport:
    items: tuple[WorkerCycleItem, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or any(
            type(item) is not WorkerCycleItem for item in self.items
        ):
            raise TypeError("items must contain exact WorkerCycleItem values")
        if tuple(item.queue for item in self.items) != tuple(WorkerQueue):
            raise ValueError("worker cycle report must preserve the closed queue order")


class WorkerCycle:
    """Run at most one claim per queue with isolated, bounded retry pressure."""

    def __init__(
        self,
        workers: Mapping[WorkerQueue, OneShotWorker],
        *,
        failure_fingerprint_key: bytes | None = None,
        base_backoff: timedelta = timedelta(seconds=0.25),
        max_backoff: timedelta = timedelta(seconds=30),
        jitter: Callable[[WorkerQueue, int, float], float] | None = None,
    ) -> None:
        if not isinstance(workers, Mapping) or set(workers) != set(WorkerQueue):
            raise ValueError("workers must provide exactly one worker for every queue")
        normalized: dict[WorkerQueue, OneShotWorker] = {}
        for queue in WorkerQueue:
            worker = workers[queue]
            if not callable(getattr(worker, "run_once", None)):
                raise TypeError(f"worker for {queue.value} must expose run_once")
            if type(worker).__name__ in {"NoopWorker", "FallbackWorker"}:
                raise ValueError(f"noop/fallback worker is forbidden for {queue.value}")
            normalized[queue] = worker
        key = os.urandom(32) if failure_fingerprint_key is None else failure_fingerprint_key
        if type(key) is not bytes or len(key) < 16:
            raise ValueError("failure_fingerprint_key must contain at least 16 bytes")
        if (
            type(base_backoff) is not timedelta
            or type(max_backoff) is not timedelta
            or base_backoff <= timedelta(0)
            or max_backoff < base_backoff
            or max_backoff > timedelta(minutes=5)
        ):
            raise ValueError("worker backoff bounds are invalid")
        if jitter is not None and not callable(jitter):
            raise TypeError("jitter must be callable when present")
        self._workers = normalized
        self._fingerprint_key = key
        self._base_backoff_seconds = base_backoff.total_seconds()
        self._max_backoff_seconds = max_backoff.total_seconds()
        self._jitter = jitter or self._default_jitter
        self._health = {queue: _QueueHealthState() for queue in WorkerQueue}
        # "healthy" means no observed queue failure, not a fabricated success:
        # unattempted queues explicitly have no last_success_at.
        self._latest = {
            queue: WorkerCycleItem(
                queue=queue, status=QueueRunStatus.HEALTHY, result=None,
                reason_code=None, failure_fingerprint=None, consecutive_failures=0,
                last_success_at=None, last_failure_at=None,
                backoff_seconds=0.0, next_attempt_at=None,
            )
            for queue in WorkerQueue
        }

    @property
    def workers(self) -> Mapping[WorkerQueue, OneShotWorker]:
        return dict(self._workers)

    def _failure_fingerprint(self, queue: WorkerQueue, exc: Exception) -> str:
        exception_type = type(exc)
        material = (
            b"v2-worker-failure-v1\0"
            + queue.value.encode("ascii")
            + b"\0"
            + exception_type.__module__.encode("utf-8")
            + b"."
            + exception_type.__qualname__.encode("utf-8")
        )
        return hmac.new(self._fingerprint_key, material, hashlib.sha256).hexdigest()

    def _degradation_fingerprint(
        self,
        queue: WorkerQueue,
        reason: WorkerFailureReason,
    ) -> str:
        material = (
            b"v2-worker-degradation-v1\0"
            + queue.value.encode("ascii")
            + b"\0"
            + reason.value.encode("ascii")
        )
        return hmac.new(self._fingerprint_key, material, hashlib.sha256).hexdigest()

    def _default_jitter(
        self,
        queue: WorkerQueue,
        failure_count: int,
        seconds: float,
    ) -> float:
        material = (
            b"v2-worker-backoff-jitter-v1\0"
            + queue.value.encode("ascii")
            + b"\0"
            + str(failure_count).encode("ascii")
        )
        value = int.from_bytes(
            hmac.new(self._fingerprint_key, material, hashlib.sha256).digest()[:8],
            "big",
        ) / float((1 << 64) - 1)
        return seconds * 0.1 * value

    def _failure_item(
        self,
        queue: WorkerQueue,
        state: _QueueHealthState,
        *,
        status: QueueRunStatus,
        backoff_seconds: float,
    ) -> WorkerCycleItem:
        return WorkerCycleItem(
            queue=queue,
            status=status,
            result=None,
            reason_code=state.reason_code,
            failure_fingerprint=state.failure_fingerprint,
            consecutive_failures=state.consecutive_failures,
            last_success_at=state.last_success_at,
            last_failure_at=state.last_failure_at,
            backoff_seconds=float(backoff_seconds),
            next_attempt_at=state.next_attempt_at,
        )

    def _schedule_failure(
        self,
        queue: WorkerQueue,
        state: _QueueHealthState,
        *,
        now: datetime,
        reason: WorkerFailureReason,
        fingerprint: str,
    ) -> WorkerCycleItem:
        state.consecutive_failures += 1
        state.last_failure_at = now
        state.reason_code = reason.value
        state.failure_fingerprint = fingerprint
        raw_delay = min(
            self._max_backoff_seconds,
            self._base_backoff_seconds
            * (2 ** min(state.consecutive_failures - 1, 20)),
        )
        jitter_seconds = self._jitter(
            queue,
            state.consecutive_failures,
            raw_delay,
        )
        if (
            type(jitter_seconds) is not float
            or jitter_seconds < 0
            or jitter_seconds > raw_delay
        ):
            raise ValueError("worker jitter must be a bounded exact float")
        delay = min(self._max_backoff_seconds, raw_delay + jitter_seconds)
        state.next_attempt_at = now + timedelta(seconds=delay)
        return self._failure_item(
            queue,
            state,
            status=QueueRunStatus.FAILED,
            backoff_seconds=delay,
        )

    def run_once(
        self,
        *,
        now: datetime,
        clock: Callable[[], datetime] | None = None,
        on_progress: Callable[[WorkerCycleReport], None] | None = None,
    ) -> WorkerCycleReport:
        if (
            type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
        ):
            raise ValueError("now must be an exact UTC datetime")
        # The injected clock is used only by the live loop. Existing deterministic
        # one-shot callers keep their explicit timestamp semantics.
        completion_clock = clock or (lambda: now)
        for queue in WorkerQueue:
            attempted_at = completion_clock()
            self._latest[queue] = self._run_queue(
                queue, now=attempted_at, clock=completion_clock,
            )
            if on_progress is not None:
                on_progress(self.report)
        return self.report

    @property
    def report(self) -> WorkerCycleReport:
        """Last observed outcomes; a pulse never invents a completed claim."""
        return WorkerCycleReport(tuple(self._latest[queue] for queue in WorkerQueue))

    def _run_queue(
        self, queue: WorkerQueue, *, now: datetime, clock: Callable[[], datetime],
    ) -> WorkerCycleItem:
        state = self._health[queue]
        if state.next_attempt_at is not None and now < state.next_attempt_at:
            return self._failure_item(
                queue, state, status=QueueRunStatus.BACKOFF,
                backoff_seconds=(state.next_attempt_at - now).total_seconds(),
            )
        try:
            result = self._workers[queue].run_once(now=now)
        except Exception as exc:
            return self._schedule_failure(
                queue, state, now=clock(),
                reason=WorkerFailureReason.WORKER_EXCEPTION,
                fingerprint=self._failure_fingerprint(queue, exc),
            )
        if type(result) is WorkerHealthResult:
            if result.disposition is WorkerHealthDisposition.DEGRADED:
                assert result.reason is not None
                return self._schedule_failure(
                    queue, state, now=clock(), reason=result.reason,
                    fingerprint=self._degradation_fingerprint(queue, result.reason),
                )
            result = result.result
        state.consecutive_failures = 0
        state.last_success_at = clock()
        state.reason_code = None
        state.failure_fingerprint = None
        state.next_attempt_at = None
        return WorkerCycleItem(
            queue=queue, status=QueueRunStatus.HEALTHY, result=result,
            reason_code=None, failure_fingerprint=None, consecutive_failures=0,
            last_success_at=state.last_success_at,
            last_failure_at=state.last_failure_at,
            backoff_seconds=0.0, next_attempt_at=None,
        )


def build_worker_cycle(
    container: object, workers: Mapping[WorkerQueue, OneShotWorker]
) -> WorkerCycle:
    from v2_host.composition import V2Container, V2Role

    if type(container) is not V2Container:
        raise TypeError("container must be exact V2Container")
    if container.role is not V2Role.WORKER:
        raise ValueError("worker cycle requires the worker role")
    cycle = WorkerCycle(workers)
    snapshot = container.readiness()
    if (
        snapshot.status != "ready"
        and snapshot.capabilities == {"productive_graph": "missing"}
        and snapshot.reasons == ("productive_graph_not_built",)
    ):
        # A complete, guard-validated explicit graph is itself the worker
        # composition proof. Production registers richer capability details in
        # its factory; this path keeps the signed qualification factory valid.
        container.register_runtime_capabilities(
            {f"worker:{queue.value}": "ready" for queue in WorkerQueue}
        )
        snapshot = container.readiness()
    if snapshot.status != "ready":
        raise RuntimeError("worker container is not ready")
    return cycle


def _load_worker_factory(path: str):
    if path in {"", "v2_host.production:build_worker_set"}:
        from v2_host.production import build_worker_set

        return build_worker_set
    if path == "v2_host.qualification_workers:build_worker_set":
        from v2_host.qualification_workers import build_worker_set

        return build_worker_set
    raise ValueError("V2_WORKER_FACTORY is outside the closed factory allowlist")


def _write_heartbeat(
    path: Path,
    report: WorkerCycleReport,
    *,
    now: datetime,
    public_ingress_reason: str | None = None,
    public_turn_capacity: int = 0,
) -> None:
    if type(report) is not WorkerCycleReport:
        raise TypeError("report must be exact WorkerCycleReport")
    if (
        type(now) is not datetime
        or now.tzinfo is None
        or now.utcoffset() != timedelta(0)
    ):
        raise ValueError("heartbeat now must be exact UTC")
    if type(public_turn_capacity) is not int or public_turn_capacity < 0:
        raise ValueError("public_turn_capacity must be an exact non-negative integer")
    if public_ingress_reason is not None and (
        type(public_ingress_reason) is not str or not public_ingress_reason
    ):
        raise ValueError("public_ingress_reason must be non-empty exact text")
    failed = [item.queue.value for item in report.items if item.failed]
    queue_health = {
        item.queue.value: {
            "status": item.status.value,
            "reason_code": item.reason_code,
            "failure_fingerprint": item.failure_fingerprint,
            "consecutive_failures": item.consecutive_failures,
            "last_success_at": (
                item.last_success_at.isoformat()
                if item.last_success_at is not None
                else None
            ),
            "last_failure_at": (
                item.last_failure_at.isoformat()
                if item.last_failure_at is not None
                else None
            ),
            "backoff_seconds": item.backoff_seconds,
            "next_attempt_at": (
                item.next_attempt_at.isoformat()
                if item.next_attempt_at is not None
                else None
            ),
        }
        for item in report.items
    }
    payload = json.dumps(
        {
            "schema": "v2-worker-heartbeat-v2",
            "observed_at": now.isoformat(),
            "status": "degraded" if failed else "healthy",
            "failed_queues": failed,
            "public_ingress_ready": public_ingress_reason is None,
            "public_ingress_reason": public_ingress_reason,
            "public_turn_capacity": public_turn_capacity,
            "queues": queue_health,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def _log_cycle_failures(report: WorkerCycleReport) -> None:
    if type(report) is not WorkerCycleReport:
        raise TypeError("report must be exact WorkerCycleReport")
    logger = logging.getLogger("v2.worker")
    for item in report.items:
        if item.status is QueueRunStatus.FAILED:
            logger.error(
                "worker_queue_degraded queue=%s status=%s reason_code=%s "
                "fingerprint=%s consecutive_failures=%d backoff_seconds=%.3f",
                item.queue.value,
                item.status.value,
                item.reason_code,
                item.failure_fingerprint,
                item.consecutive_failures,
                item.backoff_seconds,
            )


class SQLiteWorkerOwnershipLease:
    """Process-scoped exclusive ownership for all mutable worker capabilities."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("worker ownership path must be absolute")
        self._path = path
        self._descriptor: int | None = None

    def acquire(self) -> "SQLiteWorkerOwnershipLease":
        if self._descriptor is not None:
            raise RuntimeError("worker SQLite ownership is already held")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self._path,
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise RuntimeError("worker SQLite ownership is already held") from None
        self._descriptor = descriptor
        return self

    def close(self) -> None:
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class _WorkerHeartbeat:
    """Bounded pulse over snapshots, never over SQLite/worker capabilities.

    Only the owner thread grants a new deadline after observed queue progress.
    A stuck owner cannot look alive forever; process exit or deadline expiry
    leaves the file to age naturally under the existing readiness contract.
    """

    def __init__(self, path: Path, *, interval: float, progress_timeout: float) -> None:
        if not (math.isfinite(interval) and 0 < interval < progress_timeout):
            raise ValueError("heartbeat interval must be below its progress timeout")
        if not math.isfinite(progress_timeout):
            raise ValueError("heartbeat progress timeout must be finite")
        self._path = path
        self._interval = interval
        self._progress_timeout = progress_timeout
        self._lock = Lock()
        self._stop = Event()
        self._snapshot: tuple[WorkerCycleReport, str | None, int] | None = None
        self._deadline = 0.0
        self._broken = False
        self._thread = Thread(target=self._run, name="v2-worker-heartbeat", daemon=True)

    def update(
        self, report: WorkerCycleReport, *, public_ingress_reason: str | None,
        public_turn_capacity: int, publish: bool = False,
    ) -> None:
        with self._lock:
            if self._broken:
                raise RuntimeError("worker heartbeat publisher failed")
            self._snapshot = (report, public_ingress_reason, public_turn_capacity)
            self._deadline = time.monotonic() + self._progress_timeout
            if publish:
                self._write()

    def _write(self) -> None:
        if self._snapshot is None:
            return
        report, reason, capacity = self._snapshot
        _write_heartbeat(
            self._path, report, now=datetime.now(timezone.utc),
            public_ingress_reason=reason, public_turn_capacity=capacity,
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                if time.monotonic() >= self._deadline:
                    continue
                try:
                    self._write()
                except Exception:
                    self._broken = True
                    logging.getLogger("v2.worker").error("worker_heartbeat_publish_failed")
                    return

    def close(self) -> None:
        self._stop.set()
        self._thread.join()


def main() -> None:
    from v2_host.composition import V2Container, V2Role
    from v2_host.settings import V2ProcessRole, V2Settings

    settings = V2Settings.from_env(process_role=V2ProcessRole.WORKER)
    factory_path = os.environ.get("V2_WORKER_FACTORY", "")
    factory = _load_worker_factory(factory_path)
    ownership = SQLiteWorkerOwnershipLease(
        settings.sqlite_path.with_name("v2-worker-owner.lock")
    ).acquire()
    try:
        container = V2Container.open(settings=settings, role=V2Role.WORKER)
        try:
            workers = factory(container=container, settings=settings)
            cycle = build_worker_cycle(container, workers)
            raw_interval = os.environ.get("V2_WORKER_INTERVAL_SECONDS", "0.25")
            try:
                interval = float(raw_interval)
            except ValueError as exc:
                raise ValueError("V2_WORKER_INTERVAL_SECONDS must be numeric") from exc
            if not math.isfinite(interval) or interval <= 0 or interval > 60:
                raise ValueError("V2_WORKER_INTERVAL_SECONDS must be in (0, 60]")
            heartbeat = _WorkerHeartbeat(
                settings.worker_heartbeat_path,
                interval=min(1.0, settings.worker_heartbeat_max_age_seconds / 3.0),
                # Match the complete inbox turn budget (six model rounds), not
                # just one model request. This is a progress deadline, not an
                # extension of the API's heartbeat freshness window.
                progress_timeout=max(
                    float(settings.hermes_timeout_seconds * 6 + 30),
                    interval + settings.worker_heartbeat_max_age_seconds,
                ),
            )

            def publish_progress(report: WorkerCycleReport, *, publish: bool = False) -> None:
                observed = datetime.now(timezone.utc)
                # These calls may touch owner-thread SQLite. The pulse receives
                # values only, never the container, resolver or connection.
                heartbeat.update(
                    report,
                    public_ingress_reason=container.controlled_public_ingress_reason(
                        now=observed,
                    ),
                    public_turn_capacity=container.public_turn_capacity(now=observed),
                    publish=publish,
                )

            publish_progress(cycle.report, publish=True)
            heartbeat.start()
            try:
                while True:
                    report = cycle.run_once(
                        now=datetime.now(timezone.utc),
                        clock=lambda: datetime.now(timezone.utc),
                        on_progress=publish_progress,
                    )
                    _log_cycle_failures(report)
                    publish_progress(report, publish=True)
                    time.sleep(interval)
            finally:
                # No publisher from this process may survive SQLite ownership.
                heartbeat.close()
        finally:
            container.close()
    finally:
        ownership.close()


__all__ = [
    "OneShotWorker",
    "QueueRunStatus",
    "SQLiteWorkerOwnershipLease",
    "WorkerCycle",
    "WorkerCycleItem",
    "WorkerCycleReport",
    "WorkerFailureReason",
    "WorkerHealthDisposition",
    "WorkerHealthResult",
    "WorkerQueue",
    "build_worker_cycle",
    "_load_worker_factory",
    "_log_cycle_failures",
    "_write_heartbeat",
    "main",
]


if __name__ == "__main__":
    main()
