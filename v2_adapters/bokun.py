"""Direct Bókun read adapter restricted to canonical product IDs."""

from __future__ import annotations

import re
from datetime import timedelta
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
            "participants": query.adults,
        }
        response = exact_dict(self._transport("activity", payload), "Bókun response")
        if response.get("product_id") not in (None, query.canonical_product_id):
            raise ProviderReadError("Bókun response failed canonical product binding")
        provider_product_id = text(response.get("bokun_product_id"), "bokun_product_id")
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
        private_hash = binding_hash(
            {
                "request_hash": query.request_hash,
                "bokun_product_id": provider_product_id,
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
            start_time=None,
            adults=query.adults,
            children=0,
            total_amount=amount,
            currency=currency,
            available=available,
        )
        return PrivateOfferBinding(
            provider="bokun",
            query=resolved_query,
            observed_at=observed_at,
            expires_at=expires_at,
            provider_fields=(("bokun_product_id", provider_product_id),),
        )

    def _activity(self, request: ReadRequest) -> ReadObservation:
        query = {
            "product_id": request.product_id,
            "activity_date": request.activity_date.isoformat(),
            "participants": request.participants,
        }
        response = exact_dict(self._transport("activity", query), "Bókun response")
        if response.get("product_id") not in (None, request.product_id):
            raise ProviderReadError("Bókun response failed canonical product binding")
        provider_product_id = text(response.get("bokun_product_id"), "bokun_product_id")
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
        private_hash = binding_hash(
            {
                "request_hash": request.query_hash(),
                "bokun_product_id": provider_product_id,
            }
        )
        public = {
            **query,
            "offer_id": "offer:" + private_hash,
            "product_public_name": text(
                response.get("product_public_name"), "product_public_name"
            ),
            "total_amount": amount,
            "currency": currency,
            "available": available,
        }
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
