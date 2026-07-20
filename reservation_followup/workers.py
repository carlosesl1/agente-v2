"""One-shot follow-up workers behind caller-supplied closed ports."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol, runtime_checkable

from .handoff import HandoffEffectJob
from .payment import PaymentSettlementCommand, SettlementOutcome
from .sqlite_store import SQLiteFollowupUnitOfWork
from .types import (
    HandoffReceipt,
    PaymentOutboxClaim,
    PaymentReceipt,
    PreDispatchReleaseDisposition,
    SettlementCertainty,
    SettlementPermit,
    _require_id,
)


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
            pass
        else:
            if type(receipt) is not HandoffReceipt:
                raise TypeError("delivery must return exact HandoffReceipt")
            self._store.complete_handoff_outbox(claim, receipt, now=now)
            return HandoffWorkerResult.delivered(claim.message.effect_id)
        try:
            self._store.release_handoff_outbox(claim, now=now)
        except Exception as release_error:
            raise release_error from None
        return HandoffWorkerResult.retryable_failure(claim.message.effect_id)


@runtime_checkable
class PaymentEffectDeliveryPort(Protocol):
    delivery_id: str
    delivery_version: int

    def deliver(self, claim: PaymentOutboxClaim) -> PaymentReceipt: ...


class PaymentOutboxWorkerDisposition(str, Enum):
    IDLE = "idle"
    DELIVERED = "delivered"
    RETRYABLE_FAILURE = "retryable_failure"


@dataclass(frozen=True, slots=True)
class PaymentOutboxWorkerResult:
    disposition: PaymentOutboxWorkerDisposition
    message_id: str | None

    def __post_init__(self) -> None:
        if type(self.disposition) is not PaymentOutboxWorkerDisposition:
            raise ValueError("disposition must use PaymentOutboxWorkerDisposition")
        if self.message_id is not None:
            object.__setattr__(
                self,
                "message_id",
                _require_id(self.message_id, "payment_outbox_worker_result.message_id"),
            )
        if (self.disposition is PaymentOutboxWorkerDisposition.IDLE) != (
            self.message_id is None
        ):
            raise ValueError("idle is the only result without a message_id")

    @classmethod
    def idle(cls) -> PaymentOutboxWorkerResult:
        return cls(PaymentOutboxWorkerDisposition.IDLE, None)


class PaymentOutboxWorker:
    """Claim and deliver at most one already-persisted payment effect."""

    def __init__(
        self,
        *,
        store: SQLiteFollowupUnitOfWork,
        delivery: PaymentEffectDeliveryPort,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        if type(store) is not SQLiteFollowupUnitOfWork:
            raise TypeError("store must be exact SQLiteFollowupUnitOfWork")
        delivery_id = _require_id(delivery.delivery_id, "delivery.delivery_id")
        if type(delivery.delivery_version) is not int or delivery.delivery_version < 1:
            raise ValueError("delivery.delivery_version must be an integer >= 1")
        if not callable(getattr(delivery, "deliver", None)):
            raise TypeError("delivery must expose deliver(claim)")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive timedelta")
        self._store = store
        self._delivery = delivery
        self._delivery_id = delivery_id
        self._delivery_version = delivery.delivery_version
        self._worker_id = _require_id(worker_id, "worker_id")
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> PaymentOutboxWorkerResult:
        claim = self._store.claim_payment_outbox(
            worker_id=self._worker_id,
            delivery_id=self._delivery_id,
            delivery_version=self._delivery_version,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return PaymentOutboxWorkerResult.idle()
        try:
            receipt = self._delivery.deliver(claim)
            if type(receipt) is not PaymentReceipt:
                raise TypeError("delivery must return exact PaymentReceipt")
        except Exception:
            try:
                self._store.release_payment_outbox(claim, now=now)
            except Exception as release_error:
                raise release_error from None
            return PaymentOutboxWorkerResult(
                PaymentOutboxWorkerDisposition.RETRYABLE_FAILURE,
                claim.message_id,
            )
        self._store.complete_payment_outbox(claim, receipt, now=now)
        return PaymentOutboxWorkerResult(
            PaymentOutboxWorkerDisposition.DELIVERED,
            claim.message_id,
        )


class SettlementPreparationError(RuntimeError):
    """Base class for a proven pre-dispatch preparation failure."""


class RetryableSettlementPreparationError(SettlementPreparationError):
    """Preparation failed before dispatch and may consume another finite claim."""


class TerminalSettlementPreparationError(SettlementPreparationError):
    """Preparation proved that no dispatch occurred and must stop automatically."""


@runtime_checkable
class SettlementPort(Protocol):
    settlement_id: str
    settlement_version: int

    def prepare(self, request: PaymentSettlementCommand) -> str: ...

    def dispatch(self, permit: SettlementPermit) -> SettlementOutcome: ...


class SettlementWorkerDisposition(str, Enum):
    IDLE = "idle"
    PREPARATION_REQUEUED = "preparation_requeued"
    PREPARATION_TERMINAL = "preparation_terminal"
    SETTLED = "settled"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class SettlementWorkerResult:
    disposition: SettlementWorkerDisposition
    settlement_command_id: str | None

    def __post_init__(self) -> None:
        if type(self.disposition) is not SettlementWorkerDisposition:
            raise ValueError("disposition must use SettlementWorkerDisposition")
        if self.settlement_command_id is not None:
            object.__setattr__(
                self,
                "settlement_command_id",
                _require_id(
                    self.settlement_command_id,
                    "settlement_worker_result.settlement_command_id",
                ),
            )
        if (self.disposition is SettlementWorkerDisposition.IDLE) != (
            self.settlement_command_id is None
        ):
            raise ValueError("idle is the only settlement result without a command")

    @classmethod
    def idle(cls) -> "SettlementWorkerResult":
        return cls(SettlementWorkerDisposition.IDLE, None)


def _dispatched_unknown(request_hash: str) -> SettlementOutcome:
    return SettlementOutcome(
        certainty=SettlementCertainty.DISPATCHED_UNKNOWN,
        payment_registered=False,
        reservation_target_confirmed=False,
        provider_reference_fingerprint=None,
        requires_reconciliation=True,
        claim_evidence=(request_hash,),
    )


def _canonical_dispatch_outcome(
    candidate: object,
    *,
    request_hash: str,
) -> SettlementOutcome:
    if type(candidate) is not SettlementOutcome:
        return _dispatched_unknown(request_hash)
    try:
        clean = SettlementOutcome(
            **{field.name: getattr(candidate, field.name) for field in fields(SettlementOutcome)}
        )
    except Exception:
        return _dispatched_unknown(request_hash)
    if clean != candidate or clean.certainty is SettlementCertainty.NOT_DISPATCHED:
        return _dispatched_unknown(request_hash)
    if request_hash not in clean.claim_evidence:
        return _dispatched_unknown(request_hash)
    return clean


class PaymentSettlementWorker:
    """Prepare, permanently fence, and dispatch at most one settlement."""

    def __init__(
        self,
        *,
        store: SQLiteFollowupUnitOfWork,
        settlement: SettlementPort,
        worker_id: str,
        lease_ttl: timedelta,
    ) -> None:
        if type(store) is not SQLiteFollowupUnitOfWork:
            raise TypeError("store must be exact SQLiteFollowupUnitOfWork")
        self._settlement_id = _require_id(
            settlement.settlement_id,
            "settlement.settlement_id",
        )
        if (
            type(settlement.settlement_version) is not int
            or settlement.settlement_version < 1
        ):
            raise ValueError("settlement.settlement_version must be an integer >= 1")
        if not callable(getattr(settlement, "prepare", None)):
            raise TypeError("settlement must expose prepare(request)")
        if not callable(getattr(settlement, "dispatch", None)):
            raise TypeError("settlement must expose dispatch(permit)")
        if type(lease_ttl) is not timedelta or lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be a positive timedelta")
        self._store = store
        self._settlement = settlement
        self._settlement_version = settlement.settlement_version
        self._worker_id = _require_id(worker_id, "worker_id")
        self._lease_ttl = lease_ttl

    def run_once(self, *, now: datetime) -> SettlementWorkerResult:
        claim = self._store.claim_settlement(
            worker_id=self._worker_id,
            now=now,
            lease_ttl=self._lease_ttl,
        )
        if claim is None:
            return SettlementWorkerResult.idle()

        preparation_retryable: bool | None = None
        request: object = None
        try:
            request = self._settlement.prepare(claim.command)
        except RetryableSettlementPreparationError:
            preparation_retryable = True
        except TerminalSettlementPreparationError:
            preparation_retryable = False
        except Exception:
            preparation_retryable = True

        if preparation_retryable is None and (
            type(request) is not str or request != claim.command.canonical_payload
        ):
            preparation_retryable = False

        if preparation_retryable is not None:
            disposition = self._store.release_pre_dispatch_settlement(
                claim,
                retryable=preparation_retryable,
                now=now,
            )
            worker_disposition = (
                SettlementWorkerDisposition.PREPARATION_REQUEUED
                if disposition is PreDispatchReleaseDisposition.REQUEUED
                else SettlementWorkerDisposition.PREPARATION_TERMINAL
            )
            return SettlementWorkerResult(
                worker_disposition,
                claim.command.settlement_command_id,
            )

        permit = self._store.fence_settlement(claim, request, now=now)
        dispatched: object = None
        dispatch_failed = False
        try:
            dispatched = self._settlement.dispatch(permit)
        except Exception:
            dispatch_failed = True
        outcome = (
            _dispatched_unknown(permit.request_hash)
            if dispatch_failed
            else _canonical_dispatch_outcome(
                dispatched,
                request_hash=permit.request_hash,
            )
        )
        self._store.record_settlement_outcome(
            claim,
            permit,
            outcome,
            now=now,
        )
        disposition = (
            SettlementWorkerDisposition.SETTLED
            if outcome.certainty is SettlementCertainty.SETTLED
            else SettlementWorkerDisposition.MANUAL_REVIEW
        )
        return SettlementWorkerResult(
            disposition,
            claim.command.settlement_command_id,
        )


__all__ = [
    "HandoffDeliveryPort",
    "HandoffWorkerDisposition",
    "HandoffWorkerResult",
    "HandoffOutboxWorker",
    "SettlementPreparationError",
    "RetryableSettlementPreparationError",
    "TerminalSettlementPreparationError",
    "SettlementPort",
    "SettlementWorkerDisposition",
    "SettlementWorkerResult",
    "PaymentSettlementWorker",
]
