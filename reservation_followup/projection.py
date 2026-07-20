"""Closed pure projections for Phase 6 payment outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .payment import SettlementOutcome, _revalidate_settlement_outcome
from .types import (
    EffectRequirement,
    PaymentEffectPolicy,
    PaymentStatus,
    SettlementCertainty,
)


class PaymentEffectKind(str, Enum):
    PAID_STATE_TRANSITION = "paid_state_transition"
    CUSTOMER_PAYMENT_CONFIRMATION = "customer_payment_confirmation"
    INTERNAL_PAYMENT_EMAIL = "internal_payment_email"
    BOOKING_FORM = "booking_form"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class PaymentEffectJob:
    kind: PaymentEffectKind
    required: bool
    outcome: SettlementOutcome

    def __post_init__(self) -> None:
        if type(self.kind) is not PaymentEffectKind:
            raise ValueError("payment effect kind must be exact")
        if type(self.required) is not bool:
            raise ValueError("payment effect required must be exact bool")
        clean = _revalidate_settlement_outcome(self.outcome)
        if clean != self.outcome:
            raise ValueError("payment effect outcome is noncanonical")
        object.__setattr__(self, "outcome", clean)
        if clean.certainty is SettlementCertainty.SETTLED:
            if self.kind is PaymentEffectKind.MANUAL_REVIEW:
                raise ValueError("settled outcome cannot create manual review effect")
            if self.kind in (
                PaymentEffectKind.PAID_STATE_TRANSITION,
                PaymentEffectKind.CUSTOMER_PAYMENT_CONFIRMATION,
            ) and not self.required:
                raise ValueError("core settled effects must be required")
            return
        if self.kind is not PaymentEffectKind.MANUAL_REVIEW or not self.required:
            raise ValueError("non-settled outcome requires one required manual review effect")


def required_payment_effects(
    outcome: SettlementOutcome,
    policy: PaymentEffectPolicy,
) -> tuple[PaymentEffectJob, ...]:
    """Project immutable jobs; delivery and receipts remain a separate worker."""

    if type(policy) is not PaymentEffectPolicy:
        raise TypeError("policy must be exact PaymentEffectPolicy")
    clean = _revalidate_settlement_outcome(outcome)
    if clean != outcome:
        raise ValueError("outcome must be canonical")
    if clean.certainty is not SettlementCertainty.SETTLED:
        return (
            PaymentEffectJob(
                kind=PaymentEffectKind.MANUAL_REVIEW,
                required=True,
                outcome=clean,
            ),
        )
    jobs = [
        PaymentEffectJob(PaymentEffectKind.PAID_STATE_TRANSITION, True, clean),
        PaymentEffectJob(PaymentEffectKind.CUSTOMER_PAYMENT_CONFIRMATION, True, clean),
    ]
    if policy.internal_payment_email is EffectRequirement.OPTIONAL:
        jobs.append(
            PaymentEffectJob(PaymentEffectKind.INTERNAL_PAYMENT_EMAIL, False, clean)
        )
    if policy.booking_form is not EffectRequirement.DISABLED:
        jobs.append(
            PaymentEffectJob(
                PaymentEffectKind.BOOKING_FORM,
                policy.booking_form is EffectRequirement.REQUIRED,
                clean,
            )
        )
    return tuple(jobs)


def project_settlement_outcome(
    outcome: object,
    *,
    dispatch_fenced: bool,
) -> PaymentStatus:
    """Project a validated settlement outcome without performing any effect."""

    if type(outcome) is not SettlementOutcome:
        raise TypeError("outcome must be the exact SettlementOutcome type")
    clean_outcome = _revalidate_settlement_outcome(outcome)
    if type(dispatch_fenced) is not bool:
        raise TypeError("dispatch_fenced must be an exact bool")
    if clean_outcome.certainty is SettlementCertainty.SETTLED:
        return PaymentStatus.PAID
    if clean_outcome.certainty is SettlementCertainty.NOT_DISPATCHED:
        return PaymentStatus.MANUAL_REVIEW if dispatch_fenced else PaymentStatus.RETRYABLE
    if clean_outcome.certainty in (
        SettlementCertainty.DISPATCHED_NO_EFFECT,
        SettlementCertainty.PARTIAL_SETTLEMENT,
        SettlementCertainty.DISPATCHED_UNKNOWN,
    ):
        return PaymentStatus.MANUAL_REVIEW
    raise ValueError("unsupported settlement outcome certainty")  # pragma: no cover


__all__ = [
    "PaymentEffectKind",
    "PaymentEffectJob",
    "required_payment_effects",
    "project_settlement_outcome",
]
