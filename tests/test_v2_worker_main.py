from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path

import pytest

from v2_host.composition import _validate_worker_heartbeat_payload
from v2_host.worker_main import (
    SQLiteWorkerOwnershipLease,
    WorkerCycle,
    WorkerFailureReason,
    WorkerHealthResult,
    WorkerQueue,
    _log_cycle_failures,
    _write_heartbeat,
)


NOW = datetime(2026, 7, 23, 18, 0, tzinfo=timezone.utc)


class Runner:
    def __init__(self, name: str, log: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.log = log
        self.fail = fail
        self.calls = 0

    def run_once(self, *, now):
        self.calls += 1
        self.log.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        return f"{self.name}:ok"


def configured_workers(*, failed: WorkerQueue | None = None):
    log: list[str] = []
    workers = {
        queue: Runner(queue.value, log, fail=queue is failed)
        for queue in WorkerQueue
    }
    return log, workers


def test_worker_cycle_runs_each_queue_once_in_closed_order() -> None:
    log, workers = configured_workers()

    report = WorkerCycle(workers).run_once(now=NOW)

    assert log == [queue.value for queue in WorkerQueue]
    assert all(worker.calls == 1 for worker in workers.values())
    assert tuple(item.queue for item in report.items) == tuple(WorkerQueue)
    assert all(item.failed is False for item in report.items)


def test_worker_cycle_isolates_one_queue_failure_without_retrying_it() -> None:
    log, workers = configured_workers(failed=WorkerQueue.SETTLEMENT)

    report = WorkerCycle(workers).run_once(now=NOW)

    assert log == [queue.value for queue in WorkerQueue]
    settlement = next(
        item for item in report.items if item.queue is WorkerQueue.SETTLEMENT
    )
    assert settlement.failed is True
    assert settlement.result is None
    assert workers[WorkerQueue.SETTLEMENT].calls == 1
    assert workers[WorkerQueue.PUBLIC_DELIVERY].calls == 1
    assert workers[WorkerQueue.RECONCILIATION].calls == 1


def test_worker_heartbeat_publishes_effective_public_ingress_capacity(
    tmp_path: Path,
) -> None:
    _, workers = configured_workers()
    report = WorkerCycle(workers).run_once(now=NOW)
    path = tmp_path / "worker-heartbeat.json"

    _write_heartbeat(
        path,
        report,
        now=NOW,
        public_ingress_reason=None,
        public_turn_capacity=3,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "v2-worker-heartbeat-v2"
    assert payload["observed_at"] == NOW.isoformat()
    assert payload["status"] == "healthy"
    assert payload["failed_queues"] == []
    assert payload["public_ingress_ready"] is True
    assert payload["public_ingress_reason"] is None
    assert payload["public_turn_capacity"] == 3
    assert set(payload["queues"]) == {queue.value for queue in WorkerQueue}
    assert all(
        value["status"] == "healthy" and value["failure_fingerprint"] is None
        for value in payload["queues"].values()
    )
    _validate_worker_heartbeat_payload(payload)

    payload["queues"]["settlement"]["failure_fingerprint"] = "a" * 64
    with pytest.raises(ValueError, match="healthy queue"):
        _validate_worker_heartbeat_payload(payload)


def test_queue_failure_is_redacted_fingerprinted_and_backed_off(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = (
        "TOP_SECRET_PROVIDER_RESPONSE phone=+5575999999999 "
        "url=https://provider.invalid/private?id=booking-secret "
        "provider_id=booking-secret"
    )
    log, workers = configured_workers()
    workers[WorkerQueue.SETTLEMENT] = Runner(
        WorkerQueue.SETTLEMENT.value,
        log,
        fail=True,
    )
    workers[WorkerQueue.SETTLEMENT].name = secret
    cycle = WorkerCycle(
        workers,
        failure_fingerprint_key=b"k" * 32,
        base_backoff=timedelta(seconds=2),
        max_backoff=timedelta(seconds=8),
        jitter=lambda _queue, _count, _seconds: 0.0,
    )

    first = cycle.run_once(now=NOW)
    skipped = cycle.run_once(now=NOW + timedelta(seconds=1))
    retried = cycle.run_once(now=NOW + timedelta(seconds=2))
    first_item = next(
        item for item in first.items if item.queue is WorkerQueue.SETTLEMENT
    )
    skipped_item = next(
        item for item in skipped.items if item.queue is WorkerQueue.SETTLEMENT
    )
    retried_item = next(
        item for item in retried.items if item.queue is WorkerQueue.SETTLEMENT
    )

    assert first_item.status.value == "failed"
    assert first_item.reason_code == "worker_exception"
    assert first_item.consecutive_failures == 1
    assert first_item.backoff_seconds == 2.0
    assert skipped_item.status.value == "backoff"
    assert skipped_item.attempted is False
    assert retried_item.consecutive_failures == 2
    assert retried_item.backoff_seconds == 4.0
    assert first_item.failure_fingerprint == retried_item.failure_fingerprint
    assert workers[WorkerQueue.SETTLEMENT].calls == 2

    caplog.set_level(logging.ERROR, logger="v2.worker")
    _log_cycle_failures(first)
    heartbeat = tmp_path / "worker-heartbeat.json"
    _write_heartbeat(heartbeat, first, now=NOW)
    serialized = heartbeat.read_text(encoding="utf-8") + caplog.text
    assert secret not in serialized
    assert "+5575999999999" not in serialized
    assert "provider.invalid" not in serialized
    assert "booking-secret" not in serialized
    assert "RuntimeError" not in serialized


def test_success_after_backoff_resets_consecutive_failure_state() -> None:
    log, workers = configured_workers(failed=WorkerQueue.SETTLEMENT)
    cycle = WorkerCycle(
        workers,
        failure_fingerprint_key=b"k" * 32,
        base_backoff=timedelta(seconds=1),
        max_backoff=timedelta(seconds=4),
        jitter=lambda _queue, _count, _seconds: 0.0,
    )
    failed = cycle.run_once(now=NOW)
    workers[WorkerQueue.SETTLEMENT].fail = False
    recovered = cycle.run_once(now=NOW + timedelta(seconds=1))
    item = next(
        value for value in recovered.items if value.queue is WorkerQueue.SETTLEMENT
    )

    assert next(
        value for value in failed.items if value.queue is WorkerQueue.SETTLEMENT
    ).consecutive_failures == 1
    assert item.status.value == "healthy"
    assert item.consecutive_failures == 0
    assert item.last_failure_at == NOW
    assert item.last_success_at == NOW + timedelta(seconds=1)


def test_terminal_reconciliation_result_degrades_without_exception(
    tmp_path: Path,
) -> None:
    class DivergentRunner(Runner):
        def run_once(self, *, now):
            self.calls += 1
            self.log.append(self.name)
            return WorkerHealthResult.degraded(
                reason=WorkerFailureReason.CLOUDBEDS_AUDIT_DIVERGENT,
                result={"status": "divergent"},
            )

    log, workers = configured_workers()
    workers[WorkerQueue.RECONCILIATION] = DivergentRunner("reconciliation", log)
    report = WorkerCycle(
        workers,
        failure_fingerprint_key=b"k" * 32,
        jitter=lambda _queue, _count, _seconds: 0.0,
    ).run_once(now=NOW)
    item = next(
        value for value in report.items if value.queue is WorkerQueue.RECONCILIATION
    )

    assert item.status.value == "failed"
    assert item.reason_code == "cloudbeds_audit_divergent"
    assert item.result is None
    assert item.consecutive_failures == 1
    assert workers[WorkerQueue.RECONCILIATION].calls == 1
    heartbeat = tmp_path / "semantic-degradation.json"
    _write_heartbeat(heartbeat, report, now=NOW)
    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    assert payload["status"] == "degraded"
    assert payload["queues"]["reconciliation"]["reason_code"] == (
        "cloudbeds_audit_divergent"
    )
    _validate_worker_heartbeat_payload(payload)


def test_sqlite_worker_ownership_lock_is_exclusive_and_releasable(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "v2-worker-owner.lock").resolve()
    first = SQLiteWorkerOwnershipLease(path)
    second = SQLiteWorkerOwnershipLease(path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="already held"):
            second.acquire()
    finally:
        first.close()

    second.acquire()
    second.close()
