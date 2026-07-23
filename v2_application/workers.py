"""V2 reservation worker facade over the durable Phase 5 command ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from reservation_domain import ExecutionCertainty
from reservation_execution import CommandWorker, PreparationDisposition, WorkerDisposition
from reservation_execution.sqlite_store import PersistedTransition, SQLiteUnitOfWork
from v2_application.reservations import (
    RoutingReservationExecutionAdapter,
    V2ReservationExecutionAdapter,
)
from v2_contracts.ports import CommercialEffectGuard


class V2WorkerDisposition(str, Enum):
    IDLE = "idle"
    PREPARATION_REQUEUED = "preparation_requeued"
    NOT_CALLED = "not_called"
    CALLED_NO_EFFECT = "called_no_effect"
    EFFECT_CONFIRMED = "effect_confirmed"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class V2WorkerResult:
    disposition: V2WorkerDisposition
    transition: PersistedTransition | None = None


class V2ReservationWorker:
    def __init__(
        self,
        *,
        store: SQLiteUnitOfWork,
        adapters: tuple[V2ReservationExecutionAdapter, ...],
        effect_guard: CommercialEffectGuard,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        router = RoutingReservationExecutionAdapter(adapters, effect_guard)
        self._worker = CommandWorker(
            store=store,
            adapter=router,
            worker_id=worker_id,
            lease_ttl=lease_ttl,
        )

    def run_once(self, *, now: datetime) -> V2WorkerResult:
        result = self._worker.run_once(now=now)
        if result.disposition is WorkerDisposition.IDLE:
            return V2WorkerResult(V2WorkerDisposition.IDLE)
        if result.disposition is WorkerDisposition.PREPARATION_REQUEUED:
            return V2WorkerResult(V2WorkerDisposition.PREPARATION_REQUEUED)
        if result.disposition is WorkerDisposition.PREPARATION_TERMINAL:
            if result.preparation is not PreparationDisposition.TERMINAL_NOT_CALLED:
                raise RuntimeError("terminal preparation result has an invalid disposition")
            return V2WorkerResult(V2WorkerDisposition.NOT_CALLED)
        transition = result.transition
        if transition is None:
            raise RuntimeError("completed worker result lacks a transition")
        outcome = getattr(transition.state, "outcome", None)
        if outcome is None:
            raise RuntimeError("completed reservation state lacks an outcome")
        disposition = {
            ExecutionCertainty.NOT_CALLED: V2WorkerDisposition.NOT_CALLED,
            ExecutionCertainty.CALLED_NO_EFFECT: V2WorkerDisposition.CALLED_NO_EFFECT,
            ExecutionCertainty.EFFECT_CONFIRMED: V2WorkerDisposition.EFFECT_CONFIRMED,
            ExecutionCertainty.CALLED_UNKNOWN: V2WorkerDisposition.MANUAL_REVIEW,
        }[outcome.certainty]
        return V2WorkerResult(disposition, transition)


__all__ = ["V2ReservationWorker", "V2WorkerDisposition", "V2WorkerResult"]
