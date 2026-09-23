"""Read-only service context derived from canonical operation stores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from v2_contracts.channel import PublicMessageAuthor
from v2_contracts.payments import (
    PaymentInstruction,
    PaymentSelection,
    StripePaymentLink,
)


@dataclass(frozen=True, slots=True)
class PaymentInitiationContext:
    initiation_id: str
    selection: PaymentSelection
    status: str
    dispatch_started: bool
    offer: StripePaymentLink | PaymentInstruction | None

    def __post_init__(self) -> None:
        if type(self.selection) is not PaymentSelection:
            raise TypeError("payment context requires exact selection")
        if self.status not in {"queued", "fenced", "completed", "manual_review"}:
            raise ValueError("invalid payment initiation status")
        if type(self.dispatch_started) is not bool:
            raise TypeError("dispatch_started must be bool")
        if (self.status == "completed") != (self.offer is not None):
            raise ValueError("completed initiation requires its offer")
        if self.offer is not None:
            if type(self.offer) not in (StripePaymentLink, PaymentInstruction):
                raise TypeError("invalid payment offer")
            obligation = self.selection.obligation
            if (
                self.offer.payment_id,
                self.offer.reservation_anchor_id,
                self.offer.economic_version,
            ) != (
                obligation.payment_id,
                obligation.reservation_anchor_id,
                obligation.economic_version,
            ):
                raise ValueError("payment offer is not bound to obligation")


@dataclass(frozen=True, slots=True)
class ExecutedOffer:
    offer_id: str
    service: str
    provider_ref: str
    public_label: str
    start_date: date
    end_date: date | None
    start_time: str | None
    adults: int
    children: int
    amount: str
    currency: str


@dataclass(frozen=True, slots=True)
class ComponentOutcome:
    command_id: str
    certainty: str
    normalized_status: str
    provider_reference: str | None

    def __post_init__(self) -> None:
        if self.certainty not in {
            "not_called",
            "called_no_effect",
            "effect_confirmed",
            "called_unknown",
        }:
            raise ValueError("invalid execution certainty")


@dataclass(frozen=True, slots=True)
class PaymentSettlementContext:
    payment_id: str
    economic_version: int
    amount_minor: int
    currency: str
    method: str | None
    status: str
    certainty: str | None


@dataclass(frozen=True, slots=True)
class ProviderReservationStatus:
    """A fresh provider read, separate from creation and settlement ledgers."""

    source_status: str
    observed_at: datetime
    reservation_status: str | None = None
    payment_status: str | None = None
    paid_amount: str | None = None
    balance_due: str | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if self.source_status not in {"observed", "unavailable"}:
            raise ValueError("invalid provider status source")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("provider observation requires an aware datetime")
        if (self.source_status == "observed") != bool(self.reservation_status):
            raise ValueError("observed source requires native reservation status")
        if self.source_status == "unavailable" and any(
            value is not None for value in (
                self.payment_status, self.paid_amount, self.balance_due, self.currency
            )
        ):
            raise ValueError("unavailable status cannot carry financial claims")


@dataclass(frozen=True, slots=True)
class ExecutionComponentContext:
    command_id: str | None
    draft_id: str
    draft_version: int
    offer: ExecutedOffer
    ledger_status: str
    outcome: ComponentOutcome | None
    payment_id: str | None = None
    payment_initiation_status: str = "unavailable"
    payments: tuple[PaymentInitiationContext, ...] = ()
    settlement_status: str = "unavailable"
    settlements: tuple[PaymentSettlementContext, ...] = ()
    reservation_status: ProviderReservationStatus | None = None

    def __post_init__(self) -> None:
        if type(self.offer) is not ExecutedOffer:
            raise TypeError("component requires exact offer")
        if self.outcome is not None:
            if (
                type(self.outcome) is not ComponentOutcome
                or self.outcome.command_id != self.command_id
            ):
                raise ValueError("outcome must belong to the component command")
        if self.payment_initiation_status not in {
            "unavailable",
            "not_recorded",
            "recorded",
        }:
            raise ValueError("invalid payment source status")
        if type(self.payments) is not tuple or any(
            type(p) is not PaymentInitiationContext for p in self.payments
        ):
            raise TypeError("payments must be exact contexts")
        if bool(self.payments) != (self.payment_initiation_status == "recorded"):
            raise ValueError("payment source status disagrees with records")
        if self.settlement_status not in {"unavailable", "not_recorded", "recorded"}:
            raise ValueError("invalid settlement source status")
        if type(self.settlements) is not tuple or any(
            type(s) is not PaymentSettlementContext for s in self.settlements
        ):
            raise TypeError("settlements must contain exact contracts")
        if bool(self.settlements) != (self.settlement_status == "recorded"):
            raise ValueError("settlement source status disagrees with records")
        if any(
            p.selection.obligation.payment_id != self.payment_id for p in self.payments
        ):
            raise ValueError("payment must belong to component")


@dataclass(frozen=True, slots=True)
class OperationalMessage:
    outbox_id: str
    release_id: str
    source_message_id: str
    chunk_index: int
    text: str
    author: PublicMessageAuthor
    status: str
    updated_at: datetime

    def __post_init__(self) -> None:
        if type(self.text) is not str or not self.text.strip():
            raise ValueError("operational text must be nonempty")
        if type(self.author) is not PublicMessageAuthor:
            raise TypeError("message author must be exact")
        if self.status not in {
            "pending",
            "leased",
            "manual_review",
            "accepted_by_manychat",
            "dispatch_fenced",
            "cancelled",
        }:
            raise ValueError("invalid communication status")


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    status: str | None = None
    components: tuple[ExecutionComponentContext, ...] = ()
    messages: tuple[OperationalMessage, ...] = ()
