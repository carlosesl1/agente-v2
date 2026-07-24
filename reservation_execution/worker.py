"""One-shot durable command worker with no delivery or provider implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from reservation_domain import ExecutionCertainty, ExecutionOutcome

from .adapter import ExecutionAdapter, FencedExecutionAdapter, PreparationFailure
from .sqlite_store import PersistedTransition, SQLiteUnitOfWork
from .types import PreparationDisposition


class WorkerDisposition(str, Enum):
    IDLE = "idle"
    PREPARATION_REQUEUED = "preparation_requeued"
    PREPARATION_TERMINAL = "preparation_terminal"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class WorkerResult:
    disposition: WorkerDisposition
    transition: PersistedTransition | None = None
    preparation: PreparationDisposition | None = None

    @property
    def idle(self) -> bool:
        return self.disposition is WorkerDisposition.IDLE

    @classmethod
    def from_preparation(cls, disposition: PreparationDisposition) -> "WorkerResult":
        if type(disposition) is not PreparationDisposition:
            raise TypeError("disposition must be the exact PreparationDisposition type")
        worker_disposition = (
            WorkerDisposition.PREPARATION_REQUEUED
            if disposition is PreparationDisposition.REQUEUED
            else WorkerDisposition.PREPARATION_TERMINAL
        )
        return cls(
            disposition=worker_disposition,
            preparation=disposition,
        )

    @classmethod
    def completed(cls, transition: PersistedTransition) -> "WorkerResult":
        if type(transition) is not PersistedTransition:
            raise TypeError("transition must be the exact PersistedTransition type")
        return cls(
            disposition=WorkerDisposition.COMPLETED,
            transition=transition,
        )


class CommandWorker:
    """Claim and execute at most one already-authorized durable command."""

    def __init__(
        self,
        *,
        store: SQLiteUnitOfWork,
        adapter: ExecutionAdapter,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        if type(store) is not SQLiteUnitOfWork:
            raise TypeError("store must be the exact SQLiteUnitOfWork type")
        if not isinstance(adapter, ExecutionAdapter):
            raise TypeError("adapter must implement ExecutionAdapter")
        self._store = store
        self._adapter = adapter
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> WorkerResult:
        claim = self._store.claim_command(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return WorkerResult(disposition=WorkerDisposition.IDLE)
        try:
            block = self._store.preparation_block(claim.command.command_id)
            if block is not None:
                reason, evidence = block
                raise PreparationFailure(reason, False, (evidence,))
            request = self._adapter.prepare(claim.command)
        except PreparationFailure as failure:
            return WorkerResult.from_preparation(
                self._store.release_preparation_failure(claim, failure, now=now)
            )
        permit = self._store.fence_dispatch(claim, request, now=now)
        try:
            if isinstance(self._adapter, FencedExecutionAdapter):
                outcome = self._adapter.dispatch_fenced(
                    permit,
                    request,
                    idempotency_key=claim.command.idempotency_key,
                )
            else:
                outcome = self._adapter.dispatch(
                    request,
                    idempotency_key=claim.command.idempotency_key,
                )
            if (
                type(outcome) is not ExecutionOutcome
                or outcome.command_id != claim.command.command_id
            ):
                raise ValueError("adapter returned an outcome for another command")
        except Exception:
            outcome = claim.command.outcome(
                certainty=ExecutionCertainty.CALLED_UNKNOWN,
                normalized_status="dispatch_exception",
                evidence=(permit.request_hash,),
            )
        if outcome.certainty is ExecutionCertainty.NOT_CALLED:
            outcome = claim.command.outcome(
                certainty=ExecutionCertainty.CALLED_UNKNOWN,
                normalized_status="invalid_post_fence_not_called",
                evidence=(permit.request_hash,),
            )
        return WorkerResult.completed(
            self._store.record_outcome(permit, outcome, now=now)
        )


__all__ = ["CommandWorker", "WorkerDisposition", "WorkerResult"]
