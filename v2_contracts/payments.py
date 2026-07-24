"""Neutral payment-initiation contracts; none of these values claim settlement."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Final
from urllib.parse import urlparse


_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_CURRENCY_RE: Final = re.compile(r"^[A-Z]{3}$")
_HASH_RE: Final = re.compile(r"^[a-f0-9]{64}$")
_COUNTRY_RE: Final = re.compile(r"^[A-Z]{2}$")


class BusinessUnit(str, Enum):
    HOSTEL = "hostel"
    AGENCY = "agency"


class DueKind(str, Enum):
    PREPAYMENT = "prepayment"
    DUE_AT_CHECKIN = "due_at_checkin"


class PaymentMethod(str, Enum):
    STRIPE = "stripe"
    WISE = "wise"
    PIX = "pix"


def _id(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical opaque identifier")
    return value


def _money(amount_minor: object, currency: object) -> None:
    if type(amount_minor) is not int or amount_minor < 1:
        raise ValueError("amount_minor must be an exact positive integer")
    if type(currency) is not str or _CURRENCY_RE.fullmatch(currency) is None:
        raise ValueError("currency must be an uppercase three-letter code")


@dataclass(frozen=True, slots=True)
class PaymentObligation:
    payment_id: str
    reservation_anchor_id: str
    business_unit: BusinessUnit
    amount_minor: int
    currency: str
    due_kind: DueKind
    economic_version: int
    receiver_profile_id: str

    def __post_init__(self) -> None:
        _id(self.payment_id, "payment_id")
        _id(self.reservation_anchor_id, "reservation_anchor_id")
        if type(self.business_unit) is not BusinessUnit:
            raise TypeError("business_unit must be exact BusinessUnit")
        _money(self.amount_minor, self.currency)
        if type(self.due_kind) is not DueKind:
            raise TypeError("due_kind must be exact DueKind")
        if type(self.economic_version) is not int or self.economic_version < 1:
            raise ValueError("economic_version must be an exact positive integer")
        _id(self.receiver_profile_id, "receiver_profile_id")


@dataclass(frozen=True, slots=True)
class ReservationPaymentContext:
    payment_id: str
    reservation_anchor_id: str
    business_unit: BusinessUnit
    amount_minor: int
    currency: str
    receiver_profile_id: str
    guest_country_code: str
    economic_version: int = 1

    def __post_init__(self) -> None:
        _id(self.payment_id, "payment_id")
        _id(self.reservation_anchor_id, "reservation_anchor_id")
        if type(self.business_unit) is not BusinessUnit:
            raise TypeError("business_unit must be exact BusinessUnit")
        _money(self.amount_minor, self.currency)
        _id(self.receiver_profile_id, "receiver_profile_id")
        if type(self.guest_country_code) is not str or _COUNTRY_RE.fullmatch(
            self.guest_country_code
        ) is None:
            raise ValueError("guest_country_code must be two uppercase letters")
        if type(self.economic_version) is not int or self.economic_version < 1:
            raise ValueError("economic_version must be an exact positive integer")


@dataclass(frozen=True, slots=True)
class PaymentSelection:
    obligation: PaymentObligation
    method: PaymentMethod

    def __post_init__(self) -> None:
        if type(self.obligation) is not PaymentObligation:
            raise TypeError("obligation must be exact PaymentObligation")
        if type(self.method) is not PaymentMethod:
            raise TypeError("method must be exact PaymentMethod")


@dataclass(frozen=True, slots=True)
class StripeLinkRequest:
    payment_id: str
    reservation_anchor_id: str
    account_profile_id: str
    amount_minor: int
    currency: str
    economic_version: int
    idempotency_key: str
    subscriber_fingerprint: str = ""
    payment_percentage: int = 100
    business_unit: BusinessUnit = BusinessUnit.HOSTEL

    def __post_init__(self) -> None:
        _id(self.payment_id, "payment_id")
        _id(self.reservation_anchor_id, "reservation_anchor_id")
        _id(self.account_profile_id, "account_profile_id")
        _money(self.amount_minor, self.currency)
        if type(self.economic_version) is not int or self.economic_version < 1:
            raise ValueError("economic_version must be an exact positive integer")
        _id(self.idempotency_key, "idempotency_key")
        if self.subscriber_fingerprint and (
            type(self.subscriber_fingerprint) is not str
            or _HASH_RE.fullmatch(self.subscriber_fingerprint) is None
        ):
            raise ValueError("subscriber_fingerprint must be empty or SHA-256")
        if (
            type(self.payment_percentage) is not int
            or not 1 <= self.payment_percentage <= 100
        ):
            raise ValueError("payment_percentage must be an exact integer from 1 to 100")
        if type(self.business_unit) is not BusinessUnit:
            raise TypeError("business_unit must be exact BusinessUnit")


@dataclass(frozen=True, slots=True)
class StripePaymentLink:
    payment_id: str
    reservation_anchor_id: str
    account_profile_id: str
    economic_version: int
    public_url: str
    provider_reference_fingerprint: str
    receipt_hash: str
    settled: bool = False

    def __post_init__(self) -> None:
        _id(self.payment_id, "payment_id")
        _id(self.reservation_anchor_id, "reservation_anchor_id")
        _id(self.account_profile_id, "account_profile_id")
        if type(self.economic_version) is not int or self.economic_version < 1:
            raise ValueError("economic_version must be an exact positive integer")
        parsed = urlparse(self.public_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("public_url must be an absolute HTTPS URL")
        if type(self.provider_reference_fingerprint) is not str or _HASH_RE.fullmatch(
            self.provider_reference_fingerprint
        ) is None:
            raise ValueError("provider_reference_fingerprint must be SHA-256")
        if type(self.receipt_hash) is not str or _HASH_RE.fullmatch(self.receipt_hash) is None:
            raise ValueError("receipt_hash must be SHA-256")
        if self.settled is not False:
            raise ValueError("payment initiation can never claim settlement")


@dataclass(frozen=True, slots=True)
class PaymentInstruction:
    payment_id: str
    reservation_anchor_id: str
    method: PaymentMethod
    receiver_profile_id: str
    economic_version: int
    public_text: str
    settled: bool = False

    def __post_init__(self) -> None:
        _id(self.payment_id, "payment_id")
        _id(self.reservation_anchor_id, "reservation_anchor_id")
        if self.method not in (PaymentMethod.WISE, PaymentMethod.PIX):
            raise ValueError("instruction method must be Wise or Pix")
        _id(self.receiver_profile_id, "receiver_profile_id")
        if type(self.economic_version) is not int or self.economic_version < 1:
            raise ValueError("economic_version must be an exact positive integer")
        if type(self.public_text) is not str or not self.public_text.strip():
            raise ValueError("public_text must be exact non-empty text")
        if self.settled is not False:
            raise ValueError("payment instruction can never claim settlement")


PaymentMethodOffer = StripePaymentLink | PaymentInstruction


@dataclass(frozen=True, slots=True)
class PaymentPlan:
    obligation: PaymentObligation
    payment_effects: tuple[PaymentMethod, ...]

    def __post_init__(self) -> None:
        if type(self.obligation) is not PaymentObligation:
            raise TypeError("obligation must be exact PaymentObligation")
        if type(self.payment_effects) is not tuple or any(
            type(item) is not PaymentMethod for item in self.payment_effects
        ):
            raise TypeError("payment_effects must contain exact PaymentMethod values")
        if self.obligation.due_kind is DueKind.DUE_AT_CHECKIN and self.payment_effects:
            raise ValueError("due-at-checkin plan cannot initiate payment effects")


__all__ = [
    "BusinessUnit",
    "DueKind",
    "PaymentInstruction",
    "PaymentMethod",
    "PaymentMethodOffer",
    "PaymentObligation",
    "PaymentPlan",
    "PaymentSelection",
    "ReservationPaymentContext",
    "StripeLinkRequest",
    "StripePaymentLink",
]
