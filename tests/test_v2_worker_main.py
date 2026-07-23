from __future__ import annotations

from datetime import datetime, timezone

from v2_host.worker_main import WorkerCycle, WorkerQueue


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
