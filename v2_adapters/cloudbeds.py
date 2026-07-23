"""Direct Cloudbeds read adapter with private offer bindings."""

from __future__ import annotations

from datetime import timedelta
import re
from typing import Final

from v2_adapters._provider_common import (
    ProviderReadError,
    binding_hash,
    exact_dict,
    observed_window,
    text,
    validated_adapter,
)
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest


_AMOUNT_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")
_CURRENCY_RE: Final = re.compile(r"^[A-Z]{3}$")


class CloudbedsReadAdapter:
    def __init__(self, *, transport, clock, ttl: timedelta) -> None:
        self._transport, self._clock, self._ttl = validated_adapter(transport, clock, ttl)

    def read(self, request: ReadRequest) -> ReadObservation:
        if type(request) is not ReadRequest:
            raise TypeError("request must be an exact ReadRequest")
        if request.kind is ReadKind.LODGING:
            return self._lodging(request)
        if request.kind is ReadKind.ROOM_DESCRIPTION:
            return self._room_description(request)
        raise TypeError("Cloudbeds adapter supports only lodging reads")

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
                {"request_hash": request.canonical_hash(), "provider": private}
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
                    "offer_id": "offer:" + option_binding[:32],
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
                {"request_hash": request.canonical_hash(), "options": private_options}
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
                {"request_hash": request.canonical_hash(), "offer_id": request.offer_id}
            ),
        )


__all__ = ["CloudbedsReadAdapter"]
