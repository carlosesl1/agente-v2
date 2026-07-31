"""Direct Bókun read adapter restricted to canonical product IDs."""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from typing import Final

from v2_adapters._provider_common import (
    ProviderReadError,
    binding_hash,
    exact_dict,
    observed_window,
    reservation_result,
    text,
    validated_adapter,
)
from v2_contracts.private_offers import PrivateOfferBinding, PrivateOfferQuery
from v2_contracts.providers import (
    ProviderDispatchPermit,
    ProviderExecutionResult,
    ReadKind,
    ReadObservation,
    ReadRequest,
)

_AMOUNT_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")
_CURRENCY_RE: Final = re.compile(r"^[A-Z]{3}$")
_BOOKING_PRIVATE_FIELDS: Final = (
    "bokun_product_id",
    "start_time_id",
    "rate_id",
    "adult_pricing_category_id",
)


def _public_start_time(response: dict[str, object]) -> str | None:
    start_time = response.get("start_time")
    if start_time is None:
        return None
    if type(start_time) is not str or re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", start_time
    ) is None:
        raise ProviderReadError("Bókun public start time is not canonical")
    return start_time


def _private_booking_fields(
    response: dict[str, object], *, children: int
) -> dict[str, str]:
    fields = {
        name: text(response.get(name), name)
        for name in _BOOKING_PRIVATE_FIELDS
    }
    if children:
        fields["child_pricing_category_id"] = text(
            response.get("child_pricing_category_id"),
            "child_pricing_category_id",
        )
    return fields


def _fee_inclusive_public_fields(
    response: dict[str, object], *, total_amount: str, available: bool
) -> dict[str, object]:
    if not available:
        return {}
    base_amount = text(response.get("base_amount"), "base_amount")
    booking_fee = text(
        response.get("booking_fee_amount"), "booking_fee_amount"
    )
    includes_fee = response.get("price_includes_booking_fee")
    if (
        _AMOUNT_RE.fullmatch(base_amount) is None
        or _AMOUNT_RE.fullmatch(booking_fee) is None
        or includes_fee is not True
        or Decimal(base_amount) + Decimal(booking_fee) != Decimal(total_amount)
    ):
        raise ProviderReadError("Bókun fee-inclusive quote is not canonical")
    return {
        "base_amount": base_amount,
        "booking_fee_amount": booking_fee,
        "price_includes_booking_fee": True,
    }


class BokunReadAdapter:
    def __init__(self, *, transport, clock, ttl: timedelta) -> None:
        self._transport, self._clock, self._ttl = validated_adapter(
            transport, clock, ttl
        )

    def read(self, request: ReadRequest) -> ReadObservation:
        if type(request) is not ReadRequest:
            raise TypeError("request must be an exact ReadRequest")
        if request.kind is ReadKind.ACTIVITY:
            return self._activity(request)
        if request.kind is ReadKind.ACTIVITY_DESCRIPTION:
            return self._description(request)
        raise TypeError("Bókun adapter supports only activity reads")

    def resolve(self, query: PrivateOfferQuery) -> PrivateOfferBinding:
        if type(query) is not PrivateOfferQuery or query.service != "activity":
            raise TypeError("Bókun private resolver requires an activity query")
        payload = {
            "product_id": query.canonical_product_id,
            "activity_date": query.start_date.isoformat(),
            "adults": query.adults,
            "children": query.children,
            "quote_scope": query.request_hash,
        }
        if query.start_time is not None:
            payload["start_time"] = query.start_time
        response = exact_dict(self._transport("activity", payload), "Bókun response")
        if response.get("product_id") not in (None, query.canonical_product_id):
            raise ProviderReadError("Bókun response failed canonical product binding")
        private = _private_booking_fields(response, children=query.children)
        amount = text(response.get("total_amount"), "total_amount")
        currency = text(response.get("currency"), "currency")
        available = response.get("available")
        if (
            _AMOUNT_RE.fullmatch(amount) is None
            or _CURRENCY_RE.fullmatch(currency) is None
        ):
            raise ProviderReadError("Bókun amount or currency is not canonical")
        if type(available) is not bool:
            raise ProviderReadError("Bókun available must be an exact bool")
        _fee_inclusive_public_fields(
            response,
            total_amount=amount,
            available=available,
        )
        private_hash = binding_hash(
            {
                "request_hash": query.request_hash,
                **private,
            }
        )
        offer_id = "offer:" + private_hash
        observed_at, expires_at = observed_window(self._clock, self._ttl)
        resolved_query = PrivateOfferQuery(
            service="activity",
            offer_id=offer_id,
            request_hash=query.request_hash,
            binding_hash=private_hash,
            canonical_product_id=query.canonical_product_id,
            start_date=query.start_date,
            end_date=None,
            start_time=_public_start_time(response),
            adults=query.adults,
            children=query.children,
            total_amount=amount,
            currency=currency,
            available=available,
        )
        return PrivateOfferBinding(
            provider="bokun",
            query=resolved_query,
            observed_at=observed_at,
            expires_at=expires_at,
            provider_fields=tuple(sorted(private.items())),
        )

    def _activity(self, request: ReadRequest) -> ReadObservation:
        adults, children = request.activity_party()
        query = {
            "product_id": request.product_id,
            "activity_date": request.activity_date.isoformat(),
            "adults": adults,
            "children": children,
            "quote_scope": request.query_hash(),
        }
        response = exact_dict(self._transport("activity", query), "Bókun response")
        if response.get("product_id") not in (None, request.product_id):
            raise ProviderReadError("Bókun response failed canonical product binding")
        private = _private_booking_fields(response, children=children)
        amount = text(response.get("total_amount"), "total_amount")
        currency = text(response.get("currency"), "currency")
        available = response.get("available")
        if (
            _AMOUNT_RE.fullmatch(amount) is None
            or _CURRENCY_RE.fullmatch(currency) is None
        ):
            raise ProviderReadError("Bókun amount or currency is not canonical")
        if type(available) is not bool:
            raise ProviderReadError("Bókun available must be an exact bool")
        fee_fields = _fee_inclusive_public_fields(
            response,
            total_amount=amount,
            available=available,
        )
        private_hash = binding_hash(
            {
                "request_hash": request.query_hash(),
                **private,
            }
        )
        public = {
            "product_id": request.product_id,
            "activity_date": request.activity_date.isoformat(),
            "adults": adults,
            "children": children,
            "participants": adults + children,
            "offer_id": "offer:" + private_hash,
            "product_public_name": text(
                response.get("product_public_name"), "product_public_name"
            ),
            "total_amount": amount,
            "currency": currency,
            "available": available,
            **fee_fields,
        }
        start_time = _public_start_time(response)
        if start_time is not None:
            public["start_time"] = start_time
        observed_at, expires_at = observed_window(self._clock, self._ttl)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="bokun",
            observed_at=observed_at,
            expires_at=expires_at,
            public_payload=public,
            private_binding_hash=private_hash,
        )

    def _description(self, request: ReadRequest) -> ReadObservation:
        response = exact_dict(
            self._transport("activity_description", {"product_id": request.product_id}),
            "Bókun description",
        )
        provider_product_id = text(response.get("bokun_product_id"), "bokun_product_id")
        public = {
            "product_id": request.product_id,
            "product_public_name": text(
                response.get("product_public_name"), "product_public_name"
            ),
            "description": text(response.get("description"), "description"),
        }
        observed_at, expires_at = observed_window(self._clock, self._ttl)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="bokun",
            observed_at=observed_at,
            expires_at=expires_at,
            public_payload=public,
            private_binding_hash=binding_hash(
                {
                    "request_hash": request.query_hash(),
                    "bokun_product_id": provider_product_id,
                }
            ),
        )


class BokunReservationPort:
    provider = "bokun"

    def __init__(self, transport) -> None:
        if not callable(transport):
            raise TypeError("transport must be callable")
        self._transport = transport

    def execute(self, permit: ProviderDispatchPermit) -> ProviderExecutionResult:
        return reservation_result(
            permit=permit,
            provider=self.provider,
            operation="book_activity",
            reference_field="booking_id",
            transport=self._transport,
        )


__all__ = ["BokunReadAdapter", "BokunReservationPort"]
