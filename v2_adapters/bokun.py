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
        return self._resolve(query)

    def resolve_with_selection(
        self,
        query: PrivateOfferQuery,
        *,
        expected_bokun_product_id: str,
        expected_rate_id: str,
        expected_adult_category_id: str,
    ) -> PrivateOfferBinding:
        return self._resolve(
            query,
            expected_bokun_product_id=expected_bokun_product_id,
            expected_rate_id=expected_rate_id,
            expected_adult_category_id=expected_adult_category_id,
            ignore_minimum_participants=True,
        )

    def _resolve(
        self,
        query: PrivateOfferQuery,
        *,
        expected_bokun_product_id: str | None = None,
        expected_rate_id: str | None = None,
        expected_adult_category_id: str | None = None,
        ignore_minimum_participants: bool = False,
    ) -> PrivateOfferBinding:
        if type(query) is not PrivateOfferQuery or query.service != "activity":
            raise TypeError("Bókun private resolver requires an activity query")
        selection = self._selection_payload(
            adults=query.adults,
            children=query.children,
            expected_bokun_product_id=expected_bokun_product_id,
            expected_rate_id=expected_rate_id,
            expected_adult_category_id=expected_adult_category_id,
            ignore_minimum_participants=ignore_minimum_participants,
        )
        payload = {
            "product_id": query.canonical_product_id,
            "activity_date": query.start_date.isoformat(),
            "adults": query.adults,
            "children": query.children,
            "quote_scope": query.request_hash,
            **selection,
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
        return self._activity_with_options(request)

    def read_availability_only(self, request: ReadRequest) -> ReadObservation:
        return self._activity_with_options(request, availability_only=True)

    def read_for_recommendation(self, request: ReadRequest) -> ReadObservation:
        """Inspect current GET-backed availability and price without creating an offer."""

        return self._activity_with_options(request, recommendation_only=True)

    def read_for_recommendation_with_selection(
        self,
        request: ReadRequest,
        *,
        expected_bokun_product_id: str,
        expected_rate_id: str,
        expected_adult_category_id: str,
    ) -> ReadObservation:
        """Validate the closed solo exception from GETs without creating an offer."""

        return self._activity_with_options(
            request,
            recommendation_only=True,
            expected_bokun_product_id=expected_bokun_product_id,
            expected_rate_id=expected_rate_id,
            expected_adult_category_id=expected_adult_category_id,
            ignore_minimum_participants=True,
        )

    def read_with_selection(
        self,
        request: ReadRequest,
        *,
        expected_bokun_product_id: str,
        expected_rate_id: str,
        expected_adult_category_id: str,
    ) -> ReadObservation:
        return self._activity_with_options(
            request,
            expected_bokun_product_id=expected_bokun_product_id,
            expected_rate_id=expected_rate_id,
            expected_adult_category_id=expected_adult_category_id,
            ignore_minimum_participants=True,
        )

    @staticmethod
    def _selection_payload(
        *,
        adults: int,
        children: int,
        expected_bokun_product_id: str | None,
        expected_rate_id: str | None,
        expected_adult_category_id: str | None,
        ignore_minimum_participants: bool,
    ) -> dict[str, object]:
        values = (
            expected_bokun_product_id,
            expected_rate_id,
            expected_adult_category_id,
        )
        selected = any(value is not None for value in values)
        if not selected:
            if ignore_minimum_participants:
                raise TypeError("minimum bypass requires an exact Bókun selection")
            return {}
        if (
            adults != 1
            or children != 0
            or not ignore_minimum_participants
            or any(type(value) is not str or not value for value in values)
        ):
            raise TypeError("exact Bókun selection is restricted to one adult")
        return {
            "expected_bokun_product_id": expected_bokun_product_id,
            "expected_rate_id": expected_rate_id,
            "expected_adult_category_id": expected_adult_category_id,
            "ignore_minimum_participants": True,
        }

    def _activity_with_options(
        self,
        request: ReadRequest,
        *,
        availability_only: bool = False,
        recommendation_only: bool = False,
        expected_bokun_product_id: str | None = None,
        expected_rate_id: str | None = None,
        expected_adult_category_id: str | None = None,
        ignore_minimum_participants: bool = False,
    ) -> ReadObservation:
        adults, children = request.activity_party()
        selection = self._selection_payload(
            adults=adults,
            children=children,
            expected_bokun_product_id=expected_bokun_product_id,
            expected_rate_id=expected_rate_id,
            expected_adult_category_id=expected_adult_category_id,
            ignore_minimum_participants=ignore_minimum_participants,
        )
        query = {
            "product_id": request.product_id,
            "activity_date": request.activity_date.isoformat(),
            "adults": adults,
            "children": children,
            "quote_scope": request.query_hash(),
            **selection,
        }
        if availability_only:
            if selection:
                raise TypeError("availability-only inspection forbids Bókun selection")
            query["availability_only"] = True
        if availability_only and recommendation_only:
            raise TypeError("Bókun inspection modes are mutually exclusive")
        if request.locale is not None:
            query["locale"] = request.locale
        operation = "activity_inspection" if recommendation_only else "activity"
        response = exact_dict(self._transport(operation, query), "Bókun response")
        if response.get("product_id") not in (None, request.product_id):
            raise ProviderReadError("Bókun response failed canonical product binding")
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
        if availability_only:
            if available:
                raise ProviderReadError("Bókun availability-only inspection cannot offer")
            private_hash = binding_hash(
                {
                    "request_hash": request.query_hash(),
                    "availability_only": "true",
                }
            )
            observed_at, expires_at = observed_window(self._clock, self._ttl)
            return ReadObservation(
                request_hash=request.canonical_hash(),
                provider="bokun",
                observed_at=observed_at,
                expires_at=expires_at,
                public_payload={
                    "product_id": request.product_id,
                    "activity_date": request.activity_date.isoformat(),
                    "adults": adults,
                    "children": children,
                    "participants": adults + children,
                    "product_public_name": text(
                        response.get("product_public_name"), "product_public_name"
                    ),
                    "total_amount": amount,
                    "currency": currency,
                    "available": False,
                },
                private_binding_hash=private_hash,
            )
        if recommendation_only:
            start_time = _public_start_time(response)
            public = {
                "product_id": request.product_id,
                "activity_date": request.activity_date.isoformat(),
                "adults": adults,
                "children": children,
                "participants": adults + children,
                "product_public_name": text(
                    response.get("product_public_name"), "product_public_name"
                ),
                "total_amount": amount,
                "currency": currency,
                "available": available,
            }
            if start_time is not None:
                public["start_time"] = start_time
            evidence_hash = binding_hash(
                {
                    "request_hash": request.query_hash(),
                    "recommendation_inspection": "true",
                    "total_amount": amount,
                    "currency": currency,
                    "available": available,
                    "start_time": start_time,
                }
            )
            observed_at, expires_at = observed_window(self._clock, self._ttl)
            return ReadObservation(
                request_hash=request.canonical_hash(),
                provider="bokun",
                observed_at=observed_at,
                expires_at=expires_at,
                public_payload=public,
                private_binding_hash=evidence_hash,
            )
        private = _private_booking_fields(response, children=children)
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
        query = {"product_id": request.product_id}
        if request.locale is not None:
            query["locale"] = request.locale
        response = exact_dict(
            self._transport("activity_description", query),
            "Bókun description",
        )
        provider_product_id = text(response.get("bokun_product_id"), "bokun_product_id")
        age_guidance = response.get("age_guidance")
        suitability_guidance = response.get("suitability_guidance")
        public = {
            "product_id": request.product_id,
            "product_public_name": text(
                response.get("product_public_name"), "product_public_name"
            ),
            "description": text(response.get("description"), "description"),
            "age_guidance": (
                None if age_guidance is None else text(age_guidance, "age_guidance")
            ),
            "suitability_guidance": (
                None
                if suitability_guidance is None
                else text(suitability_guidance, "suitability_guidance")
            ),
            "grounding_review_required": True,
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
