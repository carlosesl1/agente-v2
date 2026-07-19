"""Closed pure projections for Phase 6 payment outcomes."""

from __future__ import annotations

from .types import PaymentStatus, SettlementCertainty


def project_settlement_outcome(
    outcome: object,
    *,
    dispatch_fenced: bool,
) -> PaymentStatus:
    """Project a validated settlement outcome without performing any effect."""

    from .payment import SettlementOutcome

    if type(outcome) is not SettlementOutcome:
        raise TypeError("outcome must be the exact SettlementOutcome type")
    if type(dispatch_fenced) is not bool:
        raise TypeError("dispatch_fenced must be an exact bool")
    if outcome.certainty is SettlementCertainty.SETTLED:
        return PaymentStatus.PAID
    if outcome.certainty is SettlementCertainty.NOT_DISPATCHED:
        return PaymentStatus.MANUAL_REVIEW if dispatch_fenced else PaymentStatus.RETRYABLE
    if outcome.certainty in (
        SettlementCertainty.DISPATCHED_NO_EFFECT,
        SettlementCertainty.PARTIAL_SETTLEMENT,
        SettlementCertainty.DISPATCHED_UNKNOWN,
    ):
        return PaymentStatus.MANUAL_REVIEW
    raise ValueError("unsupported settlement outcome certainty")  # pragma: no cover


__all__ = ["project_settlement_outcome"]
