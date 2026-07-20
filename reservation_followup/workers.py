"""One-shot handoff outbox delivery behind a caller-supplied closed port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol, runtime_checkable

from .handoff import HandoffEffectJob
from .sqlite_store import SQLiteFollowupUnitOfWork
from .types import HandoffReceipt, _require_id


@runtime_checkable
class HandoffDeliveryPort(Protocol):
    delivery_id: str
    delivery_version: int

    def deliver(self, message: HandoffEffectJob) -> HandoffReceipt: ...


class HandoffWorkerDisposition(str, Enum):
    IDLE = "idle"
    DELIVERED = "delivered"
    RETRYABLE_FAILURE = "retryable_failure"


@dataclass(frozen=True, slots=True)
class HandoffWorkerResult:
    disposition: HandoffWorkerDisposition
    message_id: str | None

    def __post_init__(self) -> None:
        if type(self.disposition) is not HandoffWorkerDisposition:
            raise ValueError("disposition must use HandoffWorkerDisposition")
        if self.message_id is not None:
            object.__setattr__(
                self,
                "message_id",
                _require_id(self.message_id, "handoff_worker_result.message_id"),
            )
        if (self.disposition is HandoffWorkerDisposition.IDLE) != (
            self.message_id is None
        ):
            raise ValueError("idle is the only result without a message_id")

    @classmethod
    def idle(cls) -> HandoffWorkerResult:
        return cls(HandoffWorkerDisposition.IDLE, None)

    @classmethod
    def delivered(cls, message_id: str) -> HandoffWorkerResult:
        return cls(HandoffWorkerDisposition.DELIVERED, message_id)

    @classmethod
    def retryable_failure(cls, message_id: str) -> HandoffWorkerResult:
        return cls(HandoffWorkerDisposition.RETRYABLE_FAILURE, message_id)


class HandoffOutboxWorker:
    """Claim and deliver at most one durable handoff effect per invocation."""

    def __init__(
        self,
        *,
        store: SQLiteFollowupUnitOfWork,
        delivery: HandoffDeliveryPort,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        if type(store) is not SQLiteFollowupUnitOfWork:
            raise TypeError("store must be exact SQLiteFollowupUnitOfWork")
        delivery_id = _require_id(delivery.delivery_id, "delivery.delivery_id")
        if type(delivery.delivery_version) is not int or delivery.delivery_version < 1:
            raise ValueError("delivery.delivery_version must be an integer >= 1")
        if not callable(getattr(delivery, "deliver", None)):
            raise TypeError("delivery must expose deliver(message)")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive timedelta")
        self._store = store
        self._delivery = delivery
        self._delivery_id = delivery_id
        self._delivery_version = delivery.delivery_version
        self._worker_id = _require_id(worker_id, "worker_id")
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> HandoffWorkerResult:
        claim = self._store.claim_handoff_outbox(
            worker_id=self._worker_id,
            delivery_id=self._delivery_id,
            delivery_version=self._delivery_version,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return HandoffWorkerResult.idle()
        try:
            receipt = self._delivery.deliver(claim.message)
        except Exception:
            try:
                self._store.release_handoff_outbox(claim, now=now)
            except Exception as release_error:
                raise release_error from None
            return HandoffWorkerResult.retryable_failure(claim.message.effect_id)
        if type(receipt) is not HandoffReceipt:
            raise TypeError("delivery must return exact HandoffReceipt")
        self._store.complete_handoff_outbox(claim, receipt, now=now)
        return HandoffWorkerResult.delivered(claim.message.effect_id)


__all__ = [
    "HandoffDeliveryPort",
    "HandoffWorkerDisposition",
    "HandoffWorkerResult",
    "HandoffOutboxWorker",
]
