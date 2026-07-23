"""Small capability-isolated worker cycles for the standalone V2 process."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
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
            normalized[queue] = worker
        self._workers = normalized

    def run_once(self, *, now: datetime) -> WorkerCycleReport:
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
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


__all__ = [
    "OneShotWorker",
    "WorkerCycle",
    "WorkerCycleItem",
    "WorkerCycleReport",
    "WorkerQueue",
]
