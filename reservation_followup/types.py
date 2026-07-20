"""Closed, immutable shared contracts for Phase 6 follow-up workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .handoff import HandoffEffectJob

from reservation_domain import (
    ExecutionCertainty,
    ExecutionOutcome,
    ServiceKind,
    dumps_outcome,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _require_id(value: str, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be an opaque identifier")
    normalized = value.strip()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return normalized


def _require_hash(value: str, field_name: str) -> str:
    if type(value) is not str or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _require_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_enum(value: Enum, enum_type: type[Enum], field_name: str) -> None:
    if type(value) is not enum_type:
        raise ValueError(f"{field_name} must be a {enum_type.__name__}")


def _require_positive_int(value: int, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be an integer >= 1")
    return value


def _require_currency(value: str, field_name: str) -> str:
    if type(value) is not str or not _CURRENCY_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a three-letter uppercase currency")
    return value


def _outcome_hash(outcome: ExecutionOutcome) -> str:
    return hashlib.sha256(dumps_outcome(outcome).encode("utf-8")).hexdigest()


def _economic_signature(
    *,
    amount_minor: int,
    currency: str,
    receiver_profile_id: str,
    business_unit: BusinessUnit,
    payment_target_id: str,
) -> str:
    material = json.dumps(
        {
            "amount_minor": amount_minor,
            "business_unit": business_unit.value,
            "currency": currency,
            "payment_target_id": payment_target_id,
            "receiver_profile_id": receiver_profile_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class BusinessUnit(str, Enum):
    HOSTEL = "hostel"
    AGENCY = "agency"


class PaymentMethod(str, Enum):
    PIX = "pix"
    WISE = "wise"
    STRIPE = "stripe"


class EffectRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    DISABLED = "disabled"


class HandoffStatus(str, Enum):
    REQUESTED = "requested"
    ACTIVE = "active"
    ACKNOWLEDGEMENT_PENDING = "acknowledgement_pending"
    ACKNOWLEDGED = "acknowledged"
    MANUAL_REVIEW = "manual_review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    AWAITING_METHOD = "awaiting_method"
    AWAITING_FINANCIAL_CONFIRMATION = "awaiting_financial_confirmation"
    AWAITING_EVIDENCE = "awaiting_evidence"
    EVIDENCE_VERIFIED = "evidence_verified"
    SETTLEMENT_QUEUED = "settlement_queued"
    SETTLING = "settling"
    PAID = "paid"
    RETRYABLE = "retryable"
    MANUAL_REVIEW = "manual_review"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SettlementCertainty(str, Enum):
    NOT_DISPATCHED = "not_dispatched"
    DISPATCHED_NO_EFFECT = "dispatched_no_effect"
    SETTLED = "settled"
    PARTIAL_SETTLEMENT = "partial_settlement"
    DISPATCHED_UNKNOWN = "dispatched_unknown"


@dataclass(frozen=True, slots=True)
class HandoffOutboxClaim:
    message: HandoffEffectJob
    worker_id: str
    delivery_id: str
    delivery_version: int
    fencing_token: int
    lease_acquired_at: datetime
    lease_expires_at: datetime
    delivery_attempts: int

    def __post_init__(self) -> None:
        from .handoff import HandoffEffectJob

        if type(self.message) is not HandoffEffectJob:
            raise ValueError(
                "handoff_outbox_claim.message must be exact HandoffEffectJob"
            )
        object.__setattr__(
            self,
            "worker_id",
            _require_id(self.worker_id, "handoff_outbox_claim.worker_id"),
        )
        object.__setattr__(
            self,
            "delivery_id",
            _require_id(self.delivery_id, "handoff_outbox_claim.delivery_id"),
        )
        _require_positive_int(
            self.delivery_version,
            "handoff_outbox_claim.delivery_version",
        )
        _require_positive_int(
            self.fencing_token,
            "handoff_outbox_claim.fencing_token",
        )
        _require_positive_int(
            self.delivery_attempts,
            "handoff_outbox_claim.delivery_attempts",
        )
        if self.fencing_token < self.delivery_attempts:
            raise ValueError("handoff outbox fencing token cannot trail attempts")
        acquired_at = _require_utc(
            self.lease_acquired_at,
            "handoff_outbox_claim.lease_acquired_at",
        )
        expires_at = _require_utc(
            self.lease_expires_at,
            "handoff_outbox_claim.lease_expires_at",
        )
        if expires_at <= acquired_at:
            raise ValueError("handoff outbox lease must expire after acquisition")
        object.__setattr__(self, "lease_acquired_at", acquired_at)
        object.__setattr__(self, "lease_expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class HandoffReceipt:
    receipt_id: str
    idempotency_key: str
    message_id: str
    delivery_reference: str
    delivery_id: str
    delivery_version: int
    delivered_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "receipt_id",
            "idempotency_key",
            "message_id",
            "delivery_reference",
            "delivery_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_id(
                    getattr(self, field_name),
                    f"handoff_receipt.{field_name}",
                ),
            )
        _require_positive_int(
            self.delivery_version,
            "handoff_receipt.delivery_version",
        )
        object.__setattr__(
            self,
            "delivered_at",
            _require_utc(self.delivered_at, "handoff_receipt.delivered_at"),
        )

    @classmethod
    def for_message(
        cls,
        message: object,
        *,
        receipt_id: str,
        delivery_reference: str,
        delivery_id: str,
        delivery_version: int,
        delivered_at: datetime,
    ) -> HandoffReceipt:
        from .handoff import HandoffEffectJob

        if type(message) is not HandoffEffectJob:
            raise TypeError("message must be exact HandoffEffectJob")
        return cls(
            receipt_id=receipt_id,
            idempotency_key=message.effect_id,
            message_id=message.effect_id,
            delivery_reference=delivery_reference,
            delivery_id=delivery_id,
            delivery_version=delivery_version,
            delivered_at=delivered_at,
        )


@dataclass(frozen=True, slots=True)
class ConfirmedReservationAnchor:
    reservation_workflow_id: str
    reservation_command_id: str
    reservation_subject_signature: str
    reservation_outcome_hash: str
    reservation_outcome: ExecutionOutcome
    provider_reference: str
    service: ServiceKind
    business_unit: BusinessUnit
    payment_target_id: str
    amount_minor: int
    currency: str
    receiver_profile_id: str
    confirmed_at: datetime
    payment_deadline: datetime | None

    def __post_init__(self) -> None:
        workflow_id = _require_id(
            self.reservation_workflow_id,
            "confirmed_anchor.reservation_workflow_id",
        )
        command_id = _require_id(
            self.reservation_command_id,
            "confirmed_anchor.reservation_command_id",
        )
        subject_signature = _require_hash(
            self.reservation_subject_signature,
            "confirmed_anchor.reservation_subject_signature",
        )
        outcome_hash = _require_hash(
            self.reservation_outcome_hash,
            "confirmed_anchor.reservation_outcome_hash",
        )
        if type(self.reservation_outcome) is not ExecutionOutcome:
            raise ValueError(
                "confirmed_anchor.reservation_outcome must be the exact "
                "ExecutionOutcome type"
            )
        outcome = self.reservation_outcome
        if outcome.certainty is not ExecutionCertainty.EFFECT_CONFIRMED:
            raise ValueError("confirmed anchor requires effect_confirmed outcome")
        if command_id != outcome.command_id:
            raise ValueError("confirmed anchor command does not match outcome")
        provider_reference = _require_id(
            self.provider_reference,
            "confirmed_anchor.provider_reference",
        )
        if not outcome.provider_reference or provider_reference != outcome.provider_reference:
            raise ValueError("confirmed anchor provider reference does not match outcome")
        if outcome_hash != _outcome_hash(outcome):
            raise ValueError("confirmed anchor outcome hash does not match outcome")
        _require_enum(self.service, ServiceKind, "confirmed_anchor.service")
        _require_enum(
            self.business_unit,
            BusinessUnit,
            "confirmed_anchor.business_unit",
        )
        payment_target_id = _require_id(
            self.payment_target_id,
            "confirmed_anchor.payment_target_id",
        )
        _require_positive_int(self.amount_minor, "confirmed_anchor.amount_minor")
        _require_currency(self.currency, "confirmed_anchor.currency")
        receiver_profile_id = _require_id(
            self.receiver_profile_id,
            "confirmed_anchor.receiver_profile_id",
        )
        confirmed_at = _require_utc(
            self.confirmed_at,
            "confirmed_anchor.confirmed_at",
        )
        payment_deadline = (
            None
            if self.payment_deadline is None
            else _require_utc(
                self.payment_deadline,
                "confirmed_anchor.payment_deadline",
            )
        )
        if payment_deadline is not None and payment_deadline <= confirmed_at:
            raise ValueError("confirmed_anchor.payment_deadline must be after confirmed_at")
        object.__setattr__(self, "reservation_workflow_id", workflow_id)
        object.__setattr__(self, "reservation_command_id", command_id)
        object.__setattr__(self, "reservation_subject_signature", subject_signature)
        object.__setattr__(self, "reservation_outcome_hash", outcome_hash)
        object.__setattr__(self, "provider_reference", provider_reference)
        object.__setattr__(self, "payment_target_id", payment_target_id)
        object.__setattr__(self, "receiver_profile_id", receiver_profile_id)
        object.__setattr__(self, "confirmed_at", confirmed_at)
        object.__setattr__(self, "payment_deadline", payment_deadline)


@dataclass(frozen=True, slots=True)
class HandoffEffectPolicy:
    queue_state: EffectRequirement
    customer_acknowledgement: EffectRequirement
    internal_email: EffectRequirement

    def __post_init__(self) -> None:
        if self.queue_state is not EffectRequirement.REQUIRED:
            raise ValueError("queue_state must be required")
        if self.customer_acknowledgement is not EffectRequirement.REQUIRED:
            raise ValueError("customer_acknowledgement must be required")
        if (
            type(self.internal_email) is not EffectRequirement
            or self.internal_email not in (
                EffectRequirement.OPTIONAL,
                EffectRequirement.DISABLED,
            )
        ):
            raise ValueError("internal_email must be optional or disabled")

    @classmethod
    def default_email_disabled(cls) -> HandoffEffectPolicy:
        return cls(
            queue_state=EffectRequirement.REQUIRED,
            customer_acknowledgement=EffectRequirement.REQUIRED,
            internal_email=EffectRequirement.DISABLED,
        )


@dataclass(frozen=True, slots=True)
class PaymentEffectPolicy:
    paid_state_transition: EffectRequirement
    customer_payment_confirmation: EffectRequirement
    internal_payment_email: EffectRequirement
    booking_form: EffectRequirement

    def __post_init__(self) -> None:
        if self.paid_state_transition is not EffectRequirement.REQUIRED:
            raise ValueError("paid_state_transition must be required")
        if self.customer_payment_confirmation is not EffectRequirement.REQUIRED:
            raise ValueError("customer_payment_confirmation must be required")
        if (
            type(self.internal_payment_email) is not EffectRequirement
            or self.internal_payment_email not in (
                EffectRequirement.OPTIONAL,
                EffectRequirement.DISABLED,
            )
        ):
            raise ValueError("internal_payment_email must be optional or disabled")
        if type(self.booking_form) is not EffectRequirement:
            raise ValueError("booking_form must be explicitly classified")


@dataclass(frozen=True, slots=True)
class PaymentSubject:
    payment_id: str
    payment_version: int
    confirmed_reservation_anchor: ConfirmedReservationAnchor
    amount_minor: int
    currency: str
    receiver_profile_id: str
    business_unit: BusinessUnit
    payment_target_id: str
    method: PaymentMethod | None
    economic_signature: str

    @classmethod
    def from_anchor(
        cls,
        anchor: ConfirmedReservationAnchor,
        *,
        payment_id: str,
        method: PaymentMethod | None = None,
        amount_minor: int | None = None,
        currency: str | None = None,
        receiver_profile_id: str | None = None,
        business_unit: BusinessUnit | None = None,
        payment_target_id: str | None = None,
        payment_version: int | None = None,
    ) -> PaymentSubject:
        """Build a financial subject only from a revalidated confirmed anchor."""

        if type(anchor) is not ConfirmedReservationAnchor:
            raise ValueError("anchor must be the exact ConfirmedReservationAnchor type")
        original_anchor = anchor
        if type(anchor.reservation_outcome) is not ExecutionOutcome:
            raise ValueError("anchor outcome must be the exact ExecutionOutcome type")
        if (
            type(anchor.confirmed_at) is not datetime
            or anchor.confirmed_at.utcoffset() != timedelta(0)
            or (
                anchor.payment_deadline is not None
                and (
                    type(anchor.payment_deadline) is not datetime
                    or anchor.payment_deadline.utcoffset() != timedelta(0)
                )
            )
        ):
            raise ValueError("anchor timestamps must be canonical UTC")
        clean_outcome = ExecutionOutcome(
            command_id=anchor.reservation_outcome.command_id,
            certainty=anchor.reservation_outcome.certainty,
            normalized_status=anchor.reservation_outcome.normalized_status,
            provider_reference=anchor.reservation_outcome.provider_reference,
            evidence=anchor.reservation_outcome.evidence,
        )
        anchor = ConfirmedReservationAnchor(
            reservation_workflow_id=anchor.reservation_workflow_id,
            reservation_command_id=anchor.reservation_command_id,
            reservation_subject_signature=anchor.reservation_subject_signature,
            reservation_outcome_hash=anchor.reservation_outcome_hash,
            reservation_outcome=clean_outcome,
            provider_reference=anchor.provider_reference,
            service=anchor.service,
            business_unit=anchor.business_unit,
            payment_target_id=anchor.payment_target_id,
            amount_minor=anchor.amount_minor,
            currency=anchor.currency,
            receiver_profile_id=anchor.receiver_profile_id,
            confirmed_at=anchor.confirmed_at,
            payment_deadline=anchor.payment_deadline,
        )
        if anchor != original_anchor:
            raise ValueError("anchor contains noncanonical values")
        selected_amount = anchor.amount_minor if amount_minor is None else amount_minor
        selected_currency = anchor.currency if currency is None else currency
        selected_receiver = (
            anchor.receiver_profile_id
            if receiver_profile_id is None
            else receiver_profile_id
        )
        selected_unit = anchor.business_unit if business_unit is None else business_unit
        selected_target = (
            anchor.payment_target_id
            if payment_target_id is None
            else payment_target_id
        )
        _require_positive_int(selected_amount, "payment_subject.amount_minor")
        _require_currency(selected_currency, "payment_subject.currency")
        selected_receiver = _require_id(
            selected_receiver,
            "payment_subject.receiver_profile_id",
        )
        _require_enum(
            selected_unit,
            BusinessUnit,
            "payment_subject.business_unit",
        )
        selected_target = _require_id(
            selected_target,
            "payment_subject.payment_target_id",
        )
        if method is not None:
            _require_enum(method, PaymentMethod, "payment_subject.method")
        economics_changed = (
            selected_amount != anchor.amount_minor
            or selected_currency != anchor.currency
            or selected_receiver != anchor.receiver_profile_id
            or selected_unit is not anchor.business_unit
            or selected_target != anchor.payment_target_id
        )
        expected_version = 2 if economics_changed else 1
        selected_version = expected_version
        if payment_version is not None:
            _require_positive_int(payment_version, "payment_subject.payment_version")
            if payment_version == 1 and economics_changed:
                raise ValueError("payment_version 1 cannot contain revised economics")
            selected_version = payment_version
        signature = _economic_signature(
            amount_minor=selected_amount,
            currency=selected_currency,
            receiver_profile_id=selected_receiver,
            business_unit=selected_unit,
            payment_target_id=selected_target,
        )
        return cls(
            payment_id=payment_id,
            payment_version=selected_version,
            confirmed_reservation_anchor=anchor,
            amount_minor=selected_amount,
            currency=selected_currency,
            receiver_profile_id=selected_receiver,
            business_unit=selected_unit,
            payment_target_id=selected_target,
            method=method,
            economic_signature=signature,
        )

    def __post_init__(self) -> None:
        payment_id = _require_id(self.payment_id, "payment_subject.payment_id")
        _require_positive_int(self.payment_version, "payment_subject.payment_version")
        if type(self.confirmed_reservation_anchor) is not ConfirmedReservationAnchor:
            raise ValueError(
                "payment_subject.confirmed_reservation_anchor must be the exact "
                "ConfirmedReservationAnchor type"
            )
        _require_positive_int(self.amount_minor, "payment_subject.amount_minor")
        _require_currency(self.currency, "payment_subject.currency")
        receiver_profile_id = _require_id(
            self.receiver_profile_id,
            "payment_subject.receiver_profile_id",
        )
        _require_enum(
            self.business_unit,
            BusinessUnit,
            "payment_subject.business_unit",
        )
        payment_target_id = _require_id(
            self.payment_target_id,
            "payment_subject.payment_target_id",
        )
        if self.method is not None:
            _require_enum(self.method, PaymentMethod, "payment_subject.method")
        economic_signature = _require_hash(
            self.economic_signature,
            "payment_subject.economic_signature",
        )
        expected_signature = _economic_signature(
            amount_minor=self.amount_minor,
            currency=self.currency,
            receiver_profile_id=receiver_profile_id,
            business_unit=self.business_unit,
            payment_target_id=payment_target_id,
        )
        if economic_signature != expected_signature:
            raise ValueError(
                "payment_subject.economic_signature does not match canonical economics"
            )
        anchor = self.confirmed_reservation_anchor
        economics_match_anchor = (
            self.amount_minor == anchor.amount_minor
            and self.currency == anchor.currency
            and receiver_profile_id == anchor.receiver_profile_id
            and self.business_unit is anchor.business_unit
            and payment_target_id == anchor.payment_target_id
        )
        if economics_match_anchor and self.payment_version == 2:
            raise ValueError(
                "payment_version 2 requires economics revised from the anchor"
            )
        if not economics_match_anchor and self.payment_version == 1:
            raise ValueError("revised economics require payment_version >= 2")
        object.__setattr__(self, "payment_id", payment_id)
        object.__setattr__(self, "receiver_profile_id", receiver_profile_id)
        object.__setattr__(self, "payment_target_id", payment_target_id)
        object.__setattr__(self, "economic_signature", economic_signature)


__all__ = [
    "BusinessUnit",
    "PaymentMethod",
    "EffectRequirement",
    "HandoffStatus",
    "PaymentStatus",
    "SettlementCertainty",
    "HandoffOutboxClaim",
    "HandoffReceipt",
    "ConfirmedReservationAnchor",
    "HandoffEffectPolicy",
    "PaymentEffectPolicy",
    "PaymentSubject",
]
