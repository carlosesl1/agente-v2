"""Deterministic non-PII Product copy for Stripe Checkout."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from v2_contracts.payments import (
    BusinessUnit,
    CheckoutService,
    StripeLinkRequest,
)

_NAME_LIMIT = 120
_DESCRIPTION_LIMIT = 500
_SERVICE_LABEL = {
    CheckoutService.LODGING: "Hospedagem / Accommodation",
    CheckoutService.ACTIVITY: "Passeio / Tour",
}
_PROVIDER_LABEL = {
    CheckoutService.LODGING: "Cloudbeds",
    CheckoutService.ACTIVITY: "Bókun",
}
_SERVICE_UNIT = {
    CheckoutService.LODGING: BusinessUnit.HOSTEL,
    CheckoutService.ACTIVITY: BusinessUnit.AGENCY,
}


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
    if _SERVICE_UNIT[details.service] is not request.business_unit:
        raise ValueError("checkout service does not match payment business unit")
    expected_minor = (
        details.reservation_total_minor * request.payment_percentage + 50
    ) // 100
    if expected_minor != request.amount_minor:
        raise ValueError("checkout payable amount diverges from reservation percentage")

    suffix = (
        " — Pagamento integral"
        if request.payment_percentage == 100
        else f" — Sinal {request.payment_percentage}%"
    )
    prefix = "Pacote — " if details.package_component else ""
    name = _bounded_name(prefix, details.public_label, suffix)

    party = _party_text(
        service=details.service,
        adults=details.adults,
        children=details.children,
    )
    if details.service is CheckoutService.LODGING:
        schedule = (
            f"Check-in {_date_text(details.start_date)} • "
            f"Check-out {_date_text(details.end_date)}"
        )
    else:
        schedule = _date_text(details.start_date)
        if details.start_time is not None:
            schedule += f" às {details.start_time}"
    description = (
        f"{_SERVICE_LABEL[details.service]} • {schedule} • {party} • "
        f"Reserva / Booking {_PROVIDER_LABEL[details.service]} "
        f"{details.provider_reference} • "
        f"Total {_money_text(details.reservation_total_minor, request.currency)} • "
        f"Pagar agora {_money_text(request.amount_minor, request.currency)} "
        f"({request.payment_percentage}%)"
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
            "end_date": details.end_date.isoformat() if details.end_date else None,
            "package_component": details.package_component,
            "provider_reference": details.provider_reference,
            "public_label": details.public_label,
            "reservation_total_minor": details.reservation_total_minor,
            "service": details.service.value,
            "start_date": details.start_date.isoformat(),
            "start_time": details.start_time,
        },
        "payment_percentage": request.payment_percentage,
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
            b"v2-stripe-display-details-v1\0" + canonical
        ).hexdigest(),
    )


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


def _party_text(*, service: CheckoutService, adults: int, children: int) -> str:
    if children:
        adult_label = "adulto" if adults == 1 else "adultos"
        child_label = "criança" if children == 1 else "crianças"
        return f"{adults} {adult_label} + {children} {child_label}"
    if service is CheckoutService.LODGING:
        return f"{adults} " + ("hóspede" if adults == 1 else "hóspedes")
    return f"{adults} " + ("adulto" if adults == 1 else "adultos")


def _date_text(value) -> str:
    return value.strftime("%d/%m/%Y")


def _money_text(minor: int, currency: str) -> str:
    major, cents = divmod(minor, 100)
    grouped = f"{major:,}".replace(",", ".")
    if currency == "BRL":
        return f"R$ {grouped},{cents:02d}"
    return f"{currency} {grouped},{cents:02d}"


__all__ = ["StripeProductPresentation", "stripe_product_presentation"]
