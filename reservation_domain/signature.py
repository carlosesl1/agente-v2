"""Canonical commercial identity and monotonic outcome algebra."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Iterable

from .types import (
    CommercialDraft,
    CustomerFacts,
    EconomicTerms,
    ExecutionCertainty,
    OfferSnapshot,
    ReservationOperation,
)


def _money_payload(value) -> dict[str, str]:
    return {
        "amount": format(value.amount, "f"),
        "currency": value.currency,
    }


def _offer_payload(offer: OfferSnapshot) -> dict[str, object]:
    """Project an offer onto execution-relevant identity.

    Presentation and provenance fields (`public_label`, `lookup_id`) are
    deliberately excluded. Freshness is checked before the offer becomes a
    draft; the signed subject covers what the eventual provider can act on.
    """

    return {
        "offer_id": offer.offer_id,
        "service": offer.service.value,
        "provider_ref": offer.provider_ref,
        "start_date": offer.start_date.isoformat(),
        "end_date": offer.end_date.isoformat() if offer.end_date else None,
        "start_time": offer.start_time,
        "party": {
            "adults": offer.party.adults,
            "children": offer.party.children,
        },
        "total": _money_payload(offer.total),
        "available": offer.available,
    }


def _terms_payload(terms: EconomicTerms) -> dict[str, object]:
    return {
        "payment_method": terms.payment_method,
        "add_ons": [
            {
                "code": item.code,
                "quantity": item.quantity,
                "unit_price": _money_payload(item.unit_price),
            }
            for item in sorted(terms.add_ons, key=lambda value: (value.code, value.quantity))
        ],
    }


def canonical_subject(
    *,
    components: Iterable[OfferSnapshot],
    customer: CustomerFacts,
    terms: EconomicTerms,
) -> dict[str, object]:
    ordered = tuple(sorted(components, key=lambda item: item.offer_id))
    if not ordered:
        raise ValueError("canonical subject requires components")
    return {
        "components": [_offer_payload(item) for item in ordered],
        "customer": {
            "customer_ref": customer.customer_ref,
            "full_name": customer.full_name,
            "email": customer.email,
            "phone_e164": customer.phone_e164,
            "country_code": customer.country_code,
        },
        "terms": _terms_payload(terms),
    }


def subject_signature(
    *,
    components: Iterable[OfferSnapshot],
    customer: CustomerFacts,
    terms: EconomicTerms,
) -> str:
    return hashlib.sha256(
        json.dumps(
            canonical_subject(components=components, customer=customer, terms=terms),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_commercial_draft(
    *,
    draft_id: str,
    version: int,
    created_at: datetime,
    components: tuple[OfferSnapshot, ...],
    customer: CustomerFacts,
    terms: EconomicTerms,
) -> CommercialDraft:
    return CommercialDraft(
        draft_id=draft_id,
        version=version,
        created_at=created_at,
        components=components,
        customer=customer,
        terms=terms,
        subject_signature=subject_signature(
            components=components,
            customer=customer,
            terms=terms,
        ),
    )


def operation_for_components(
    components: Iterable[OfferSnapshot],
) -> ReservationOperation:
    services = {item.service for item in components}
    if len(services) > 1:
        return ReservationOperation.RESERVE_PACKAGE
    service = next(iter(services), None)
    if service is None:
        raise ValueError("operation requires components")
    if service.value == "lodging":
        return ReservationOperation.RESERVE_LODGING
    return ReservationOperation.BOOK_ACTIVITY


def command_identity(
    *,
    workflow_id: str,
    draft_id: str,
    draft_version: int,
    signature: str,
    operation: ReservationOperation,
) -> tuple[str, str]:
    raw = "|".join(
        (
            workflow_id,
            draft_id,
            str(draft_version),
            signature,
            operation.value,
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"cmd:{digest[:32]}", f"idem:{digest}"


def combine_execution_outcomes(
    outcomes: Iterable[ExecutionCertainty],
) -> ExecutionCertainty:
    """Aggregate certainty monotonically, preserving the most critical leaf."""

    values = tuple(outcomes)
    if not values:
        return ExecutionCertainty.NOT_CALLED
    if ExecutionCertainty.CALLED_UNKNOWN in values:
        return ExecutionCertainty.CALLED_UNKNOWN
    if ExecutionCertainty.EFFECT_CONFIRMED in values:
        return ExecutionCertainty.EFFECT_CONFIRMED
    if ExecutionCertainty.CALLED_NO_EFFECT in values:
        return ExecutionCertainty.CALLED_NO_EFFECT
    return ExecutionCertainty.NOT_CALLED
