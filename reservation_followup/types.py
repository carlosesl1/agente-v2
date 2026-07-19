"""Closed, immutable shared contracts for Phase 6 follow-up workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re

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
    "ConfirmedReservationAnchor",
    "HandoffEffectPolicy",
    "PaymentEffectPolicy",
    "PaymentSubject",
]
