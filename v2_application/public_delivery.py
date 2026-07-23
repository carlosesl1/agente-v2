"""Direct fenced delivery from the Phase 8 public outbox."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol

from reservation_boundary.public_dispatch import (
    PublicDeliveryReceipt,
    PublicDispatchClaim,
)
from v2_contracts.channel import PublicDeliveryNotCalled, PublicDeliveryUnknown


class BoundaryPublicDisposition(str, Enum):
    IDLE = "idle"
    DELIVERED = "delivered"
    RETRYABLE_FAILURE = "retryable_failure"
    MANUAL_REVIEW = "manual_review"


class BoundaryPublicStore(Protocol):
    def claim_public_delivery(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> PublicDispatchClaim | None: ...

    def fence_public_delivery(
        self, claim: PublicDispatchClaim, *, now: datetime
    ) -> None: ...

    def complete_public_delivery(
        self,
        claim: PublicDispatchClaim,
        receipt: PublicDeliveryReceipt,
        *,
        now: datetime,
    ) -> None: ...

    def release_public_delivery_not_called(
        self, claim: PublicDispatchClaim, *, now: datetime
    ) -> None: ...

    def mark_public_delivery_manual_review(
        self, claim: PublicDispatchClaim, *, now: datetime
    ) -> None: ...


class PublicDeliveryPort(Protocol):
    def send(self, claim: PublicDispatchClaim) -> str: ...


class BoundaryPublicDeliveryWorker:
    def __init__(
        self,
        *,
        boundary: BoundaryPublicStore,
        delivery: PublicDeliveryPort,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        required = (
            "claim_public_delivery",
            "fence_public_delivery",
            "complete_public_delivery",
            "release_public_delivery_not_called",
            "mark_public_delivery_manual_review",
        )
        if any(not callable(getattr(boundary, name, None)) for name in required):
            raise TypeError(
                "boundary must expose the complete public-delivery protocol"
            )
        if not callable(getattr(delivery, "send", None)):
            raise TypeError("delivery must expose send")
        if type(worker_id) is not str or not worker_id:
            raise ValueError("worker_id must be non-empty text")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        self._boundary = boundary
        self._delivery = delivery
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> BoundaryPublicDisposition:
        claim = self._boundary.claim_public_delivery(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return BoundaryPublicDisposition.IDLE
        if type(claim) is not PublicDispatchClaim:
            raise TypeError("boundary returned a non-canonical public claim")
        try:
            self._boundary.fence_public_delivery(claim, now=now)
        except BaseException:
            self._boundary.mark_public_delivery_manual_review(claim, now=now)
            raise
        try:
            provider_receipt_id = self._delivery.send(claim)
        except PublicDeliveryNotCalled:
            self._boundary.release_public_delivery_not_called(claim, now=now)
            return BoundaryPublicDisposition.RETRYABLE_FAILURE
        except PublicDeliveryUnknown:
            self._boundary.mark_public_delivery_manual_review(claim, now=now)
            return BoundaryPublicDisposition.MANUAL_REVIEW
        except BaseException:
            self._boundary.mark_public_delivery_manual_review(claim, now=now)
            return BoundaryPublicDisposition.MANUAL_REVIEW
        try:
            receipt = PublicDeliveryReceipt(
                public_row_id=claim.public_row_id,
                idempotency_key=claim.idempotency_key,
                provider_receipt_id=provider_receipt_id,
                delivered_at=now,
            )
            self._boundary.complete_public_delivery(claim, receipt, now=now)
        except BaseException:
            self._boundary.mark_public_delivery_manual_review(claim, now=now)
            return BoundaryPublicDisposition.MANUAL_REVIEW
        return BoundaryPublicDisposition.DELIVERED


__all__ = [
    "BoundaryPublicDeliveryWorker",
    "BoundaryPublicDisposition",
]
