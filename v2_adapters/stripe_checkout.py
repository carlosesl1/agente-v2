"""Deterministic non-PII Product copy for Stripe Checkout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json

from v2_contracts.localization import CustomerLanguage
from v2_contracts.payments import (
    BusinessUnit,
    CheckoutService,
    StripeLinkRequest,
)

_NAME_LIMIT = 120
_DESCRIPTION_LIMIT = 500
_SERVICE_LABEL = {
    CustomerLanguage.PT_BR: {
        CheckoutService.LODGING: "Hospedagem",
        CheckoutService.ACTIVITY: "Passeio",
    },
    CustomerLanguage.EN: {
        CheckoutService.LODGING: "Accommodation",
        CheckoutService.ACTIVITY: "Tour",
    },
}
_SERVICE_UNIT = {
    CheckoutService.LODGING: BusinessUnit.HOSTEL,
    CheckoutService.ACTIVITY: BusinessUnit.AGENCY,
}
_EN_MONTH = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(frozen=True, slots=True)
class StripeProductPresentation:
    name: str
    description: str
    details_sha256: str


def stripe_product_presentation(
    request: StripeLinkRequest,
) -> StripeProductPresentation:
    if type(request) is not StripeLinkRequest:
        raise TypeError("request must be exact StripeLinkRequest")
    details = request.display_details
    if details is None:
        raise ValueError("Stripe checkout requires display_details")
    language = details.customer_language
    if language is None:
        raise ValueError("Stripe checkout requires customer_language")
    if _SERVICE_UNIT[details.service] is not request.business_unit:
        raise ValueError("checkout service does not match payment business unit")
    expected_minor = (
        details.reservation_total_minor * request.payment_percentage + 50
    ) // 100
    if expected_minor != request.amount_minor:
        raise ValueError("checkout payable amount diverges from reservation percentage")

    suffix = _payment_suffix(language, request.payment_percentage)
    prefix = _package_prefix(language) if details.package_component else ""
    name = _bounded_name(prefix, details.public_label, suffix)

    party = _party_text(
        language=language,
        service=details.service,
        adults=details.adults,
        children=details.children,
    )
    schedule = _schedule_text(
        language=language,
        service=details.service,
        start_date=details.start_date,
        end_date=details.end_date,
        start_time=details.start_time,
    )
    remaining = ""
    if request.payment_percentage < 100:
        remaining_minor = details.reservation_total_minor - request.amount_minor
        remaining_label = {
            CustomerLanguage.PT_BR: "Saldo restante",
            CustomerLanguage.EN: "Remaining balance",
        }[language]
        remaining = (
            f" • {remaining_label} "
            f"{_money_text(remaining_minor, request.currency, language)}"
        )
    total_label = {
        CustomerLanguage.PT_BR: "Total",
        CustomerLanguage.EN: "Total",
    }[language]
    pay_now_label = {
        CustomerLanguage.PT_BR: "Pagar agora",
        CustomerLanguage.EN: "Pay now",
    }[language]
    description = (
        f"{_SERVICE_LABEL[language][details.service]} • {schedule} • {party} • "
        f"{total_label} "
        f"{_money_text(details.reservation_total_minor, request.currency, language)} • "
        f"{pay_now_label} "
        f"{_money_text(request.amount_minor, request.currency, language)} "
        f"({request.payment_percentage}%){remaining}"
    )
    if len(description) > _DESCRIPTION_LIMIT:
        raise ValueError(
            f"Stripe Product description exceeds {_DESCRIPTION_LIMIT} characters"
        )

    hash_payload = {
        "amount_minor": request.amount_minor,
        "currency": request.currency,
        "display_details": {
            "adults": details.adults,
            "children": details.children,
            "customer_language": language.value,
            "end_date": details.end_date.isoformat() if details.end_date else None,
            "package_component": details.package_component,
            "public_label": details.public_label,
            "reservation_total_minor": details.reservation_total_minor,
            "service": details.service.value,
            "start_date": details.start_date.isoformat(),
            "start_time": details.start_time,
        },
        "payment_percentage": request.payment_percentage,
        "product_description": description,
        "product_name": name,
    }
    canonical = json.dumps(
        hash_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return StripeProductPresentation(
        name=name,
        description=description,
        details_sha256=hashlib.sha256(
            b"v2-stripe-product-presentation-v2\0" + canonical
        ).hexdigest(),
    )


def _payment_suffix(language: CustomerLanguage, percentage: int) -> str:
    if percentage == 100:
        return {
            CustomerLanguage.PT_BR: " — Pagamento integral",
            CustomerLanguage.EN: " — Full payment",
        }[language]
    label = {
        CustomerLanguage.PT_BR: "Sinal",
        CustomerLanguage.EN: "Deposit",
    }[language]
    return f" — {label} {percentage}%"


def _package_prefix(language: CustomerLanguage) -> str:
    return {
        CustomerLanguage.PT_BR: "Pacote — ",
        CustomerLanguage.EN: "Package — ",
    }[language]


def _bounded_name(prefix: str, label: str, suffix: str) -> str:
    available = _NAME_LIMIT - len(prefix) - len(suffix)
    if available < 2:
        raise ValueError("Stripe Product name framing exceeds its safe limit")
    if len(label) > available:
        label = label[: available - 1].rstrip() + "…"
    name = prefix + label + suffix
    if len(name) > _NAME_LIMIT:
        raise RuntimeError("Stripe Product name bound was not preserved")
    return name


def _schedule_text(
    *,
    language: CustomerLanguage,
    service: CheckoutService,
    start_date: date,
    end_date: date | None,
    start_time: str | None,
) -> str:
    if service is CheckoutService.LODGING:
        if end_date is None:
            raise ValueError("lodging checkout requires end_date")
        return (
            f"Check-in {_date_text(start_date, language)} • "
            f"Check-out {_date_text(end_date, language)}"
        )
    schedule = _date_text(start_date, language)
    if start_time is not None:
        preposition = {
            CustomerLanguage.PT_BR: "às",
            CustomerLanguage.EN: "at",
        }[language]
        schedule += f" {preposition} {start_time}"
    return schedule


def _party_text(
    *,
    language: CustomerLanguage,
    service: CheckoutService,
    adults: int,
    children: int,
) -> str:
    if language is CustomerLanguage.PT_BR:
        adult_label = "adulto" if adults == 1 else "adultos"
        child_label = "criança" if children == 1 else "crianças"
        guest_label = "hóspede" if adults == 1 else "hóspedes"
    else:
        adult_label = "adult" if adults == 1 else "adults"
        child_label = "child" if children == 1 else "children"
        guest_label = "guest" if adults == 1 else "guests"
    if children:
        return f"{adults} {adult_label} + {children} {child_label}"
    if service is CheckoutService.LODGING:
        return f"{adults} {guest_label}"
    return f"{adults} {adult_label}"


def _date_text(value: date, language: CustomerLanguage) -> str:
    if language is CustomerLanguage.PT_BR:
        return value.strftime("%d/%m/%Y")
    return f"{value.day:02d} {_EN_MONTH[value.month]} {value.year:04d}"


def _money_text(
    minor: int,
    currency: str,
    language: CustomerLanguage,
) -> str:
    major, cents = divmod(minor, 100)
    if language is CustomerLanguage.PT_BR:
        grouped = f"{major:,}".replace(",", ".")
        separator = " "
        decimal = ","
    else:
        grouped = f"{major:,}"
        separator = ""
        decimal = "."
    prefix = "R$" if currency == "BRL" else currency
    return f"{prefix}{separator}{grouped}{decimal}{cents:02d}"


__all__ = ["StripeProductPresentation", "stripe_product_presentation"]
