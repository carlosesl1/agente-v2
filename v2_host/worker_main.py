"""Small capability-isolated worker cycles for the standalone V2 process."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import os
import time
from typing import Protocol


class WorkerQueue(str, Enum):
    INBOX = "inbox"
    RESERVATION = "reservation"
    PAYMENT_INITIATION = "payment_initiation"
    SETTLEMENT = "settlement"
    POST_PAYMENT = "post_payment"
    PUBLIC_DELIVERY = "public_delivery"
    RECONCILIATION = "reconciliation"


class OneShotWorker(Protocol):
    def run_once(self, *, now: datetime) -> object: ...


@dataclass(frozen=True, slots=True)
class WorkerCycleItem:
    queue: WorkerQueue
    failed: bool
    result: object | None

    def __post_init__(self) -> None:
        if type(self.queue) is not WorkerQueue:
            raise TypeError("queue must be exact WorkerQueue")
        if type(self.failed) is not bool:
            raise TypeError("failed must be exact bool")
        if self.failed and self.result is not None:
            raise ValueError("failed worker item cannot expose a result")


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
    """Run at most one claim per queue; one failure grants no other capability."""

    def __init__(self, workers: Mapping[WorkerQueue, OneShotWorker]) -> None:
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
        self._workers = normalized

    @property
    def workers(self) -> Mapping[WorkerQueue, OneShotWorker]:
        return dict(self._workers)

    def run_once(self, *, now: datetime) -> WorkerCycleReport:
        if (
            type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
        ):
            raise ValueError("now must be an exact UTC datetime")
        items: list[WorkerCycleItem] = []
        for queue in WorkerQueue:
            try:
                result = self._workers[queue].run_once(now=now)
            except Exception:
                items.append(WorkerCycleItem(queue=queue, failed=True, result=None))
            else:
                items.append(WorkerCycleItem(queue=queue, failed=False, result=result))
        return WorkerCycleReport(tuple(items))


def build_worker_cycle(
    container: object, workers: Mapping[WorkerQueue, OneShotWorker]
) -> WorkerCycle:
    from v2_host.composition import V2Container, V2Role

    if type(container) is not V2Container:
        raise TypeError("container must be exact V2Container")
    if container.role is not V2Role.WORKER:
        raise ValueError("worker cycle requires the worker role")
    if container.readiness().status != "ready":
        raise RuntimeError("worker container is not ready")
    return WorkerCycle(workers)


def _load_worker_factory(path: str):
    if path == "v2_host.qualification_workers:build_worker_set":
        from v2_host.qualification_workers import build_worker_set

        return build_worker_set
    raise ValueError("V2_WORKER_FACTORY is outside the closed factory allowlist")


def main() -> None:
    from v2_host.composition import V2Container, V2Role
    from v2_host.settings import V2Settings

    settings = V2Settings.from_env()
    factory_path = os.environ.get("V2_WORKER_FACTORY", "")
    factory = _load_worker_factory(factory_path)
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        workers = factory(container=container, settings=settings)
        cycle = build_worker_cycle(container, workers)
        raw_interval = os.environ.get("V2_WORKER_INTERVAL_SECONDS", "0.25")
        try:
            interval = float(raw_interval)
        except ValueError as exc:
            raise ValueError("V2_WORKER_INTERVAL_SECONDS must be numeric") from exc
        if interval <= 0 or interval > 60:
            raise ValueError("V2_WORKER_INTERVAL_SECONDS must be in (0, 60]")
        while True:
            cycle.run_once(now=datetime.now(timezone.utc))
            time.sleep(interval)
    finally:
        container.close()


__all__ = [
    "OneShotWorker",
    "WorkerCycle",
    "WorkerCycleItem",
    "WorkerCycleReport",
    "WorkerQueue",
    "build_worker_cycle",
    "main",
]


if __name__ == "__main__":
    main()
