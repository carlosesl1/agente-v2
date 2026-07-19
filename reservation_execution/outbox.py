"""One-shot durable outbox delivery with a caller-supplied transport port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol, runtime_checkable

from .sqlite_store import SQLiteUnitOfWork
from .types import DeliveryReceipt, OutboxMessage, _require_id


@runtime_checkable
class DeliveryPort(Protocol):
    delivery_id: str
    delivery_version: int

    def deliver(self, message: OutboxMessage) -> DeliveryReceipt: ...


class OutboxWorkerDisposition(str, Enum):
    IDLE = "idle"
    DELIVERED = "delivered"
    RETRYABLE_FAILURE = "retryable_failure"


@dataclass(frozen=True, slots=True)
class OutboxWorkerResult:
    disposition: OutboxWorkerDisposition
    message_id: str | None

    def __post_init__(self) -> None:
        if type(self.disposition) is not OutboxWorkerDisposition:
            raise ValueError("disposition must use OutboxWorkerDisposition")
        if self.message_id is not None:
            object.__setattr__(
                self,
                "message_id",
                _require_id(self.message_id, "outbox_worker_result.message_id"),
            )
        if (self.disposition is OutboxWorkerDisposition.IDLE) != (
            self.message_id is None
        ):
            raise ValueError("idle is the only result without a message_id")

    @classmethod
    def idle(cls) -> OutboxWorkerResult:
        return cls(OutboxWorkerDisposition.IDLE, None)

    @classmethod
    def delivered(cls, message_id: str) -> OutboxWorkerResult:
        return cls(OutboxWorkerDisposition.DELIVERED, message_id)

    @classmethod
    def retryable_failure(cls, message_id: str) -> OutboxWorkerResult:
        return cls(OutboxWorkerDisposition.RETRYABLE_FAILURE, message_id)


class OutboxWorker:
    """Claim and deliver at most one durable message per invocation."""

    def __init__(
        self,
        *,
        store: SQLiteUnitOfWork,
        delivery: DeliveryPort,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        if type(store) is not SQLiteUnitOfWork:
            raise TypeError("store must be the exact SQLiteUnitOfWork type")
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
        self._worker_id = _require_id(worker_id, "worker_id")
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> OutboxWorkerResult:
        claim = self._store.claim_outbox(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return OutboxWorkerResult.idle()
        try:
            receipt = self._delivery.deliver(claim.message)
        except Exception:
            self._store.release_outbox(claim, now=now)
            return OutboxWorkerResult.retryable_failure(claim.message.message_id)
        self._store.complete_outbox(claim, receipt, now=now)
        return OutboxWorkerResult.delivered(claim.message.message_id)


__all__ = [
    "DeliveryPort",
    "OutboxWorkerDisposition",
    "OutboxWorkerResult",
    "OutboxWorker",
]
