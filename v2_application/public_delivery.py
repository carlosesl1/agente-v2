"""Direct fenced delivery from the Phase 8 public outbox."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol

from reservation_boundary.public_dispatch import (
    PublicAcceptanceReceipt,
    PublicDispatchClaim,
)
from v2_contracts.channel import (
    PublicChannelAcceptance,
    PublicDeliveryNotCalled,
    PublicDeliveryRejected,
    PublicDeliveryUnknown,
)
from v2_application.completion import (
    PublicDeliveryDisposition,
    PublicDeliveryWorker,
    PublicOutboxStore,
)
from v2_ops.contracts import NodeType
from v2_ops.recording import NullOpsRecorder, OpsRecorder, record_effect_boundary


class BoundaryPublicDisposition(str, Enum):
    IDLE = "idle"
    ACCEPTED = "accepted"
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

    def complete_public_acceptance(
        self,
        claim: PublicDispatchClaim,
        receipt: PublicAcceptanceReceipt,
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
    def send(self, claim: PublicDispatchClaim) -> PublicChannelAcceptance: ...


class BoundaryPublicDeliveryWorker:
    def __init__(
        self,
        *,
        boundary: BoundaryPublicStore,
        delivery: PublicDeliveryPort,
        worker_id: str,
        lease_ttl: timedelta,
        ops_recorder: OpsRecorder | None = None,
        effect_trace_resolver: object | None = None,
        ops_full_content: bool = False,
    ) -> None:
        required = (
            "claim_public_delivery",
            "fence_public_delivery",
            "complete_public_acceptance",
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
        if type(ops_full_content) is not bool:
            raise TypeError("ops_full_content must be an exact bool")
        if effect_trace_resolver is not None and not callable(
            getattr(effect_trace_resolver, "effect_trace_for_public_row", None)
        ):
            raise TypeError("effect_trace_resolver must resolve public rows")
        self._boundary = boundary
        self._delivery = delivery
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl
        self._ops_recorder = NullOpsRecorder() if ops_recorder is None else ops_recorder
        self._effect_trace_resolver = effect_trace_resolver
        self._ops_full_content = ops_full_content

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
            context = None
            try:
                if self._effect_trace_resolver is not None:
                    context = self._effect_trace_resolver.effect_trace_for_public_row(
                        claim.public_row_id
                    )
            except BaseException:
                context = None
            if context is None:
                acceptance = self._delivery.send(claim)
            else:
                acceptance = record_effect_boundary(
                    self._ops_recorder,
                    execution_id=context.execution_id,
                    node_type=NodeType.MANYCHAT_DELIVERY_REQUEST,
                    response_node_type=NodeType.MANYCHAT_DELIVERY_RESPONSE,
                    input_value=claim,
                    call=lambda: self._delivery.send(claim),
                    started_at=lambda: now,
                    completed_at=lambda: max(datetime.now(now.tzinfo), now),
                    full_content=self._ops_full_content,
                    technical_metadata={"trace_stage": "manychat_delivery_request"},
                )
        except PublicDeliveryRejected:
            self._boundary.mark_public_delivery_manual_review(claim, now=now)
            return BoundaryPublicDisposition.MANUAL_REVIEW
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
            if type(acceptance) is not PublicChannelAcceptance:
                raise TypeError("channel returned non-canonical acceptance evidence")
            receipt = PublicAcceptanceReceipt(
                public_row_id=claim.public_row_id,
                idempotency_key=claim.idempotency_key,
                acceptance=acceptance,
                accepted_at=now,
            )
            self._boundary.complete_public_acceptance(claim, receipt, now=now)
        except BaseException:
            self._boundary.mark_public_delivery_manual_review(claim, now=now)
            return BoundaryPublicDisposition.MANUAL_REVIEW
        return BoundaryPublicDisposition.ACCEPTED


class CombinedPublicDeliveryWorker:
    """Drain boundary replies first, then completion rows, behind one live gate."""

    def __init__(
        self,
        *,
        boundary: BoundaryPublicStore,
        completion: PublicOutboxStore,
        delivery: PublicDeliveryPort,
        effect_guard: object,
        worker_id: str,
        lease_ttl: timedelta,
        ops_recorder: OpsRecorder | None = None,
        effect_trace_resolver: object | None = None,
        ops_full_content: bool = False,
    ) -> None:
        if not callable(getattr(effect_guard, "allows_workflow", None)):
            raise TypeError("effect_guard must expose allows_workflow")
        self._boundary = BoundaryPublicDeliveryWorker(
            boundary=boundary,
            delivery=delivery,
            worker_id=worker_id + ":boundary",
            lease_ttl=lease_ttl,
            ops_recorder=ops_recorder,
            effect_trace_resolver=effect_trace_resolver,
            ops_full_content=ops_full_content,
        )
        self._completion = PublicDeliveryWorker(
            store=completion,
            delivery=delivery,
            worker_id=worker_id + ":completion",
            lease_ttl=lease_ttl,
        )
        self._effect_guard = effect_guard

    def run_once(self, *, now: datetime):
        if not self._effect_guard.allows_workflow("manychat-public-delivery"):
            return BoundaryPublicDisposition.IDLE
        boundary = self._boundary.run_once(now=now)
        if boundary is not BoundaryPublicDisposition.IDLE:
            return boundary
        completion = self._completion.run_once(now=now)
        if completion is PublicDeliveryDisposition.IDLE:
            return BoundaryPublicDisposition.IDLE
        return completion


__all__ = [
    "BoundaryPublicDeliveryWorker",
    "BoundaryPublicDisposition",
    "CombinedPublicDeliveryWorker",
]
