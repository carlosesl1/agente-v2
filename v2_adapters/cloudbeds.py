"""Direct Cloudbeds read adapter with private offer bindings."""

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


class CloudbedsReadAdapter:
    def __init__(self, *, transport, clock, ttl: timedelta) -> None:
        self._transport, self._clock, self._ttl = validated_adapter(
            transport, clock, ttl
        )

    def read(self, request: ReadRequest) -> ReadObservation:
        if type(request) is not ReadRequest:
            raise TypeError("request must be an exact ReadRequest")
        if request.kind is ReadKind.LODGING:
            return self._lodging(request)
        if request.kind is ReadKind.ROOM_DESCRIPTION:
            return self._room_description(request)
        raise TypeError("Cloudbeds adapter supports only lodging reads")

    def resolve(self, query: PrivateOfferQuery) -> PrivateOfferBinding:
        if type(query) is not PrivateOfferQuery or query.service != "lodging":
            raise TypeError("Cloudbeds private resolver requires a lodging query")
        payload = {
            "check_in": query.start_date.isoformat(),
            "check_out": query.end_date.isoformat(),
            "adults": query.adults,
            "children": query.children,
        }
        response = exact_dict(self._transport("lodging", payload), "Cloudbeds response")
        raw_options = response.get("options")
        if type(raw_options) is not list or not raw_options:
            raise ProviderReadError("Cloudbeds response requires at least one option")
        private_options = []
        selected = None
        for raw in raw_options:
            option = exact_dict(raw, "Cloudbeds option")
            if any(option.get(name) != value for name, value in payload.items()):
                raise ProviderReadError("Cloudbeds option failed request binding")
            private = {
                "room_type_id": text(option.get("room_type_id"), "room_type_id"),
                "room_rate_id": text(option.get("room_rate_id"), "room_rate_id"),
            }
            private_options.append(private)
            option_hash = binding_hash(
                {"request_hash": query.request_hash, "provider": private}
            )
            if "offer:" + option_hash == query.offer_id:
                if selected is not None:
                    raise ProviderReadError("Cloudbeds private offer is not unique")
                selected = (option, private)
        set_hash = binding_hash(
            {"request_hash": query.request_hash, "options": private_options}
        )
        if selected is None:
            raise ProviderReadError("Cloudbeds private offer no longer exists")
        option, private = selected
        amount = text(option.get("total_amount"), "total_amount")
        currency = text(option.get("currency"), "currency")
        available_units = option.get("available_units")
        if (
            _AMOUNT_RE.fullmatch(amount) is None
            or _CURRENCY_RE.fullmatch(currency) is None
        ):
            raise ProviderReadError("Cloudbeds amount or currency is not canonical")
        if type(available_units) is not int or available_units < 0:
            raise ProviderReadError("available_units must be non-negative")
        observed_at, expires_at = observed_window(self._clock, self._ttl)
        resolved_query = PrivateOfferQuery(
            service="lodging",
            offer_id=query.offer_id,
            request_hash=query.request_hash,
            binding_hash=set_hash,
            canonical_product_id=None,
            start_date=query.start_date,
            end_date=query.end_date,
            start_time=None,
            adults=payload["adults"],
            children=payload["children"],
            total_amount=amount,
            currency=currency,
            available=available_units > 0,
        )
        return PrivateOfferBinding(
            provider="cloudbeds",
            query=resolved_query,
            observed_at=observed_at,
            expires_at=expires_at,
            provider_fields=tuple(sorted(private.items())),
        )

    def _lodging(self, request: ReadRequest) -> ReadObservation:
        query = {
            "check_in": request.check_in.isoformat(),
            "check_out": request.check_out.isoformat(),
            "adults": request.adults,
            "children": request.children,
        }
        response = exact_dict(self._transport("lodging", query), "Cloudbeds response")
        raw_options = response.get("options")
        if type(raw_options) is not list or not raw_options:
            raise ProviderReadError("Cloudbeds response requires at least one option")
        public_options: list[dict[str, object]] = []
        private_options: list[dict[str, str]] = []
        for raw in raw_options:
            option = exact_dict(raw, "Cloudbeds option")
            if any(option.get(name) != value for name, value in query.items()):
                raise ProviderReadError("Cloudbeds option failed request binding")
            room_type_id = text(option.get("room_type_id"), "room_type_id")
            room_rate_id = text(option.get("room_rate_id"), "room_rate_id")
            private = {
                "room_type_id": room_type_id,
                "room_rate_id": room_rate_id,
            }
            option_binding = binding_hash(
                {"request_hash": request.query_hash(), "provider": private}
            )
            amount = text(option.get("total_amount"), "total_amount")
            currency = text(option.get("currency"), "currency")
            available = option.get("available_units")
            if _AMOUNT_RE.fullmatch(amount) is None:
                raise ProviderReadError("total_amount must be canonical")
            if _CURRENCY_RE.fullmatch(currency) is None:
                raise ProviderReadError("currency must be canonical")
            if type(available) is not int or available < 0:
                raise ProviderReadError("available_units must be non-negative")
            public_options.append(
                {
                    "offer_id": "offer:" + option_binding,
                    "room_public_name": text(
                        option.get("room_public_name"), "room_public_name"
                    ),
                    **query,
                    "total_amount": amount,
                    "currency": currency,
                    "available_units": available,
                }
            )
            private_options.append(private)
        public: dict[str, object] = {"options": public_options}
        if len(public_options) == 1:
            public.update(public_options[0])
        observed_at, expires_at = observed_window(self._clock, self._ttl)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="cloudbeds",
            observed_at=observed_at,
            expires_at=expires_at,
            public_payload=public,
            private_binding_hash=binding_hash(
                {"request_hash": request.query_hash(), "options": private_options}
            ),
        )

    def _room_description(self, request: ReadRequest) -> ReadObservation:
        response = exact_dict(
            self._transport("room_description", {"offer_id": request.offer_id}),
            "Cloudbeds room description",
        )
        public = {
            "offer_id": request.offer_id,
            "description": text(response.get("description"), "description"),
            "amenities": response.get("amenities", []),
        }
        if type(public["amenities"]) is not list or any(
            type(item) is not str for item in public["amenities"]
        ):
            raise ProviderReadError("amenities must be exact strings")
        observed_at, expires_at = observed_window(self._clock, self._ttl)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="cloudbeds",
            observed_at=observed_at,
            expires_at=expires_at,
            public_payload=public,
            private_binding_hash=binding_hash(
                {"request_hash": request.query_hash(), "offer_id": request.offer_id}
            ),
        )


class CloudbedsReservationPort:
    provider = "cloudbeds"

    def __init__(self, transport) -> None:
        if not callable(transport):
            raise TypeError("transport must be callable")
        self._transport = transport

    def execute(self, permit: ProviderDispatchPermit) -> ProviderExecutionResult:
        return reservation_result(
            permit=permit,
            provider=self.provider,
            operation="reserve_lodging",
            reference_field="reservation_id",
            transport=self._transport,
        )


__all__ = ["CloudbedsReadAdapter", "CloudbedsReservationPort"]
