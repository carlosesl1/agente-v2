"""Direct authenticated HTTP transports for standalone V2 provider adapters.

Only read/profile calls are used by the dark-read-only runtime.  The ManyChat
send method exists for the separately gated public-delivery worker; composing it
does not grant that capability.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import hashlib
import hmac
import json
import re
from pathlib import Path
import unicodedata
from urllib.parse import urlencode

import httpx
import yaml

from v2_adapters._provider_common import binding_hash
from v2_adapters.manychat import ManyChatTransportResponse, ManyChatTransportNotCalled
from v2_contracts.providers import ReadKind, ReadRequest


class ProviderHTTPError(RuntimeError):
    """Provider call failed without exposing credentials or raw response data."""


def _text(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if type(value) in {int, float}:
        return str(value)
    return None


def _first(mapping: Mapping[str, object], *names: str) -> str | None:
    for name in names:
        value = _text(mapping.get(name))
        if value is not None:
            return value
    return None


def _consistent_text_alias(
    mapping: Mapping[str, object],
    names: tuple[str, ...],
    *,
    error: str,
    nested: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> str | None:
    values: list[str] = []
    for name in names:
        if name not in mapping:
            continue
        value = _text(mapping[name])
        if value is None:
            raise ProviderHTTPError(error)
        values.append(value)
    for container_name, nested_names in nested:
        if container_name not in mapping:
            continue
        container = mapping[container_name]
        if not isinstance(container, Mapping):
            raise ProviderHTTPError(error)
        for name in nested_names:
            if name not in container:
                continue
            value = _text(container[name])
            if value is None:
                raise ProviderHTTPError(error)
            values.append(value)
    if len(set(values)) > 1:
        raise ProviderHTTPError(error)
    return values[0] if values else None


def _integer(mapping: Mapping[str, object], *names: str) -> int | None:
    for name in names:
        value = mapping.get(name)
        if type(value) is int:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _amount(value: object) -> Decimal | None:
    if isinstance(value, Mapping):
        value = value.get("amount", value.get("value"))
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        result = Decimal(str(value).replace("R$", "").replace("BRL", "").replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return result


def _first_amount(mapping: Mapping[str, object], *names: str) -> Decimal | None:
    for name in names:
        result = _amount(mapping.get(name))
        if result is not None:
            return result
    return None


def _currency(value: object) -> str:
    candidate = _text(value) or "BRL"
    candidate = candidate.upper()
    if re.fullmatch(r"[A-Z]{3}", candidate) is None:
        raise ProviderHTTPError("provider returned a non-canonical currency")
    return candidate


def _items(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for name in ("data", "roomTypes", "room_types", "results", "items", "availabilities"):
        nested = payload.get(name)
        if isinstance(nested, list):
            return [dict(item) for item in nested if isinstance(item, Mapping)]
        if isinstance(nested, Mapping):
            nested_items = _items(nested)
            if nested_items:
                return nested_items
    return [dict(payload)] if payload else []


def _json_response(response: httpx.Response, *, provider: str) -> object:
    try:
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        status = getattr(response, "status_code", None)
        suffix = f" status={status}" if type(status) is int else ""
        raise ProviderHTTPError(f"{provider} HTTP response failed{suffix}") from exc


def _exact_object(
    value: object,
    *,
    fields: frozenset[str],
    name: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise ProviderHTTPError(f"{name} fields mismatch")
    return value


def _cloudbeds_reference(value: object) -> str | None:
    if isinstance(value, Mapping):
        direct = _first(value, "reservationID", "reservationId", "reservation_id")
        if direct is not None:
            return direct
        for nested in value.values():
            found = _cloudbeds_reference(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _cloudbeds_reference(nested)
            if found is not None:
                return found
    return None


class CloudbedsHTTPTransport:
    """Call Cloudbeds v1.3/v1.2 read endpoints and return the closed V2 DTO."""

    def __init__(
        self,
        *,
        api_key: str,
        property_id: str,
        source_id: str = "",
        base_url: str = "https://api.cloudbeds.com",
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key or not property_id:
            raise ValueError("Cloudbeds read credentials are required")
        if not base_url.startswith("https://"):
            raise ValueError("Cloudbeds base URL must use HTTPS")
        self._api_key = api_key
        self._property_id = property_id
        self._source_id = source_id
        self._base_url = re.sub(r"/api/v\d+(?:\.\d+)?/?$", "", base_url.rstrip("/"))
        self._timeout = timeout_seconds
        self._client = client or httpx.Client()
        self._offer_room_types: dict[str, str] = {}

    def __repr__(self) -> str:
        return "CloudbedsHTTPTransport(auth=bearer)"

    def _get(self, path: str, params: Mapping[str, object]) -> object:
        try:
            response = self._client.get(
                self._base_url + path,
                headers={"Authorization": f"Bearer {self._api_key}"},
                params={key: value for key, value in params.items() if value is not None},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderHTTPError("Cloudbeds HTTP request failed") from exc
        result = _json_response(response, provider="Cloudbeds")
        if isinstance(result, Mapping) and result.get("success") is False:
            raise ProviderHTTPError("Cloudbeds provider reported an unsuccessful read")
        return result

    def __call__(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        if operation == "lodging":
            if idempotency_key is not None:
                raise ProviderHTTPError("Cloudbeds read forbids an idempotency key")
            return self._lodging(payload)
        if operation == "room_description":
            if idempotency_key is not None:
                raise ProviderHTTPError("Cloudbeds read forbids an idempotency key")
            return self._room_description(payload)
        if operation == "reserve_lodging":
            return self._reserve_lodging(payload, idempotency_key=idempotency_key)
        raise ProviderHTTPError("unsupported Cloudbeds operation")

    def _reserve_lodging(
        self,
        payload: dict[str, object],
        *,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        if not self._source_id:
            raise ProviderHTTPError("Cloudbeds write source is not configured")
        if (
            type(idempotency_key) is not str
            or not idempotency_key
            or "\x00" in idempotency_key
        ):
            raise ProviderHTTPError("Cloudbeds write requires an idempotency key")
        dispatch = _exact_object(
            payload,
            fields=frozenset(
                ("schema", "command_id", "operation", "offer", "customer", "terms")
            ),
            name="Cloudbeds dispatch",
        )
        if (
            dispatch["schema"] != "v2-reservation-dispatch-v1"
            or dispatch["operation"] != "reserve_lodging"
            or type(dispatch["command_id"]) is not str
        ):
            raise ProviderHTTPError("Cloudbeds dispatch identity mismatch")
        offer = _exact_object(
            dispatch["offer"],
            fields=frozenset(
                (
                    "binding",
                    "private_binding",
                    "offer_id",
                    "start_date",
                    "end_date",
                    "start_time",
                    "party",
                    "amount",
                    "currency",
                )
            ),
            name="Cloudbeds offer",
        )
        private = _exact_object(
            offer["private_binding"],
            fields=frozenset(("room_type_id", "room_rate_id")),
            name="Cloudbeds private binding",
        )
        party = _exact_object(
            offer["party"],
            fields=frozenset(("adults", "children")),
            name="Cloudbeds party",
        )
        customer = _exact_object(
            dispatch["customer"],
            fields=frozenset(
                (
                    "customer_ref",
                    "full_name",
                    "email",
                    "phone_e164",
                    "country_code",
                )
            ),
            name="Cloudbeds customer",
        )
        terms = _exact_object(
            dispatch["terms"],
            fields=frozenset(("payment_method", "add_ons")),
            name="Cloudbeds terms",
        )
        start_date = _text(offer["start_date"])
        end_date = _text(offer["end_date"])
        try:
            check_in = date.fromisoformat(start_date or "")
            check_out = date.fromisoformat(end_date or "")
        except ValueError as exc:
            raise ProviderHTTPError("Cloudbeds stay dates are invalid") from exc
        if check_out <= check_in or offer["start_time"] is not None:
            raise ProviderHTTPError("Cloudbeds stay interval is invalid")
        adults = party["adults"]
        children = party["children"]
        if (
            type(adults) is not int
            or adults < 1
            or type(children) is not int
            or children < 0
        ):
            raise ProviderHTTPError("Cloudbeds party is invalid")
        room_type_id = _text(private["room_type_id"])
        room_rate_id = _text(private["room_rate_id"])
        if not room_type_id or not room_rate_id:
            raise ProviderHTTPError("Cloudbeds private binding is incomplete")
        amount = _amount(offer["amount"])
        currency = _currency(offer["currency"])
        if (
            amount is None
            or type(offer["amount"]) is not str
            or offer["amount"] != f"{amount:.2f}"
            or currency != "BRL"
        ):
            raise ProviderHTTPError("Cloudbeds amount/currency is invalid")
        full_name = _text(customer["full_name"])
        if full_name is None:
            raise ProviderHTTPError("Cloudbeds guest name is missing")
        name_parts = full_name.split()
        if len(name_parts) < 2:
            raise ProviderHTTPError("Cloudbeds guest name requires first and last name")
        email = _text(customer["email"])
        phone = _text(customer["phone_e164"])
        country = _text(customer["country_code"])
        if (
            email is None
            or email.count("@") != 1
            or phone is None
            or re.fullmatch(r"\+[1-9][0-9]{7,14}", phone) is None
            or country is None
            or re.fullmatch(r"[A-Z]{2}", country) is None
        ):
            raise ProviderHTTPError("Cloudbeds guest fields are invalid")
        if terms["add_ons"] != []:
            raise ProviderHTTPError("Cloudbeds reservation write does not accept add-ons")
        payment_method = {
            "stripe": "credit_card",
            "wise": "cash",
            "pix": "cash",
        }.get(terms["payment_method"])
        if payment_method is None:
            raise ProviderHTTPError("Cloudbeds payment method is invalid")
        def compact(value: object) -> str:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )

        form = {
            "propertyID": self._property_id,
            "sourceID": self._source_id,
            "startDate": start_date,
            "endDate": end_date,
            "guestFirstName": name_parts[0],
            "guestLastName": " ".join(name_parts[1:]),
            "guestEmail": email,
            "guestPhone": phone,
            "guestCountry": country,
            "rooms": compact([{"roomTypeID": room_type_id, "quantity": 1}]),
            "adults": compact([{"roomTypeID": room_type_id, "quantity": adults}]),
            "children": compact(
                [{"roomTypeID": room_type_id, "quantity": children}]
            ),
            "paymentMethod": payment_method,
        }
        try:
            response = self._client.post(
                self._base_url + "/api/v1.1/postReservation",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "X-Idempotency-Key": idempotency_key,
                },
                data=form,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderHTTPError("Cloudbeds write result is ambiguous") from exc
        try:
            response_payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            if 400 <= response.status_code < 500 and response.status_code != 409:
                return {"status": "rejected"}
            raise ProviderHTTPError("Cloudbeds write result is ambiguous") from exc
        reservation_id = _cloudbeds_reference(response_payload)
        if reservation_id is None:
            if 400 <= response.status_code < 500 and response.status_code != 409:
                return {"status": "rejected"}
            if (
                200 <= response.status_code < 300
                and isinstance(response_payload, Mapping)
                and response_payload.get("success") is False
            ):
                return {"status": "rejected"}
            raise ProviderHTTPError("Cloudbeds write result is ambiguous")
        readback = self._get(
            "/api/v1.1/getReservation",
            {
                "propertyID": self._property_id,
                "reservationID": reservation_id,
            },
        )
        if _cloudbeds_reference(readback) != reservation_id:
            raise ProviderHTTPError("Cloudbeds write read-back did not match")
        return {"status": "confirmed", "reservation_id": reservation_id}

    def _lodging(self, payload: dict[str, object]) -> dict[str, object]:
        query = {
            "check_in": payload.get("check_in"),
            "check_out": payload.get("check_out"),
            "adults": payload.get("adults"),
            "children": payload.get("children"),
        }
        params = {
            "propertyID": self._property_id,
            "startDate": query["check_in"],
            "endDate": query["check_out"],
            "adults": query["adults"],
            "children": query["children"],
            "detailedRates": "true",
        }
        available = self._get("/api/v1.3/getAvailableRoomTypes", params)
        self._get("/api/v1.2/getRatePlans", params)
        options: list[dict[str, object]] = []
        request = ReadRequest(
            request_id="cloudbeds-transport",
            kind=ReadKind.LODGING,
            check_in=date.fromisoformat(str(query["check_in"])),
            check_out=date.fromisoformat(str(query["check_out"])),
            adults=int(query["adults"]),
            children=int(query["children"]),
        )
        room_items: list[dict[str, object]] = []
        for candidate in _items(available):
            nested = candidate.get("propertyRooms")
            property_currency = candidate.get("propertyCurrency")
            inherited_currency = (
                _first(property_currency, "currencyCode", "code")
                if isinstance(property_currency, Mapping)
                else None
            )
            if isinstance(nested, list):
                for room in nested:
                    if isinstance(room, Mapping):
                        normalized_room = dict(room)
                        if inherited_currency is not None:
                            normalized_room.setdefault("currencyCode", inherited_currency)
                        room_items.append(normalized_room)
            else:
                room_items.append(candidate)
        for item in room_items:
            room_type_id = _first(item, "roomTypeID", "roomTypeId", "room_type_id", "id")
            room_rate_id = _first(item, "roomRateID", "roomRateId", "room_rate_id", "ratePlanID", "ratePlanId")
            public_name = _first(item, "roomTypeName", "roomName", "room_type_name", "name")
            available_units = _integer(item, "roomsAvailable", "availableRooms", "quantityAvailable", "available")
            total = _first_amount(item, "totalRate", "total", "roomTypeTotal", "grandTotal", "price", "rate", "roomRate")
            daily = item.get("roomRateDetailed") or item.get("rateDetailed") or item.get("dailyRates")
            if isinstance(daily, list):
                amounts = [
                    _first_amount(row, "rate", "roomRate", "price", "amount", "total")
                    for row in daily
                    if isinstance(row, Mapping)
                ]
                present = [amount for amount in amounts if amount is not None]
                if present:
                    total = sum(present, Decimal("0"))
            if not room_type_id or not room_rate_id or not public_name or total is None:
                continue
            if available_units is None:
                available_units = 1
            if available_units < 1:
                continue
            private = {"room_type_id": room_type_id, "room_rate_id": room_rate_id}
            offer_id = "offer:" + binding_hash(
                {"request_hash": request.query_hash(), "provider": private}
            )
            self._offer_room_types[offer_id] = room_type_id
            options.append(
                {
                    **query,
                    **private,
                    "room_public_name": public_name,
                    "total_amount": f"{total:.2f}",
                    "currency": _currency(item.get("currency") or item.get("currencyCode")),
                    "available_units": available_units,
                }
            )
        return {"options": options}

    def _room_description(self, payload: dict[str, object]) -> dict[str, object]:
        offer_id = str(payload.get("offer_id") or "")
        room_type_id = self._offer_room_types.get(offer_id)
        if room_type_id is None:
            raise ProviderHTTPError("Cloudbeds offer binding is not present in this runtime")
        response = self._get("/api/v1.3/getRoomTypes", {"roomTypeIDs": room_type_id})
        selected = next(
            (
                item
                for item in _items(response)
                if _first(item, "roomTypeID", "roomTypeId", "room_type_id", "id") == room_type_id
            ),
            None,
        )
        if selected is None:
            raise ProviderHTTPError("Cloudbeds room type no longer exists")
        description = _first(selected, "roomTypeDescription", "roomDescription", "description") or "Descrição indisponível"
        raw_amenities = selected.get("amenities") or selected.get("roomTypeFeatures") or []
        amenities = []
        if isinstance(raw_amenities, list):
            for item in raw_amenities:
                value = _first(item, "name", "title") if isinstance(item, Mapping) else _text(item)
                if value:
                    amenities.append(value)
        return {"description": description, "amenities": amenities[:30]}


class BokunHTTPTransport:
    """Call Bókun's signed read API using canonical internal product IDs."""

    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        product_map: Mapping[str, str],
        base_url: str = "https://api.bokun.io",
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
        timestamp: Callable[[], str] | None = None,
        quote_checkout_enabled: bool = False,
    ) -> None:
        if not access_key or not secret_key:
            raise ValueError("Bókun read credentials are required")
        if not product_map or any(not key or not value for key, value in product_map.items()):
            raise ValueError("Bókun canonical product map is required")
        if not base_url.startswith("https://"):
            raise ValueError("Bókun base URL must use HTTPS")
        if type(quote_checkout_enabled) is not bool:
            raise TypeError("Bókun quote checkout gate must be an exact bool")
        self._access_key = access_key
        self._secret_key = secret_key.encode()
        self._products = dict(product_map)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client or httpx.Client()
        self._timestamp = timestamp or (
            lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        )
        self._quote_checkout_enabled = quote_checkout_enabled

    def __repr__(self) -> str:
        return "BokunHTTPTransport(auth=hmac-sha1)"

    def _get(self, path: str) -> object:
        timestamp = self._timestamp()
        canonical = f"{timestamp}{self._access_key}GET{path}"
        signature = base64.b64encode(
            hmac.new(self._secret_key, canonical.encode(), hashlib.sha1).digest()
        ).decode("ascii")
        try:
            response = self._client.get(
                self._base_url + path,
                headers={
                    "X-Bokun-AccessKey": self._access_key,
                    "X-Bokun-Date": timestamp,
                    "X-Bokun-Signature": signature,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderHTTPError("Bókun HTTP request failed") from exc
        return _json_response(response, provider="Bókun")

    def _write_request(
        self,
        *,
        method: str,
        path: str,
        idempotency_key: str,
        json_body: dict[str, object] | None = None,
        allow_rejection: bool = False,
    ) -> tuple[int, object]:
        timestamp = self._timestamp()
        canonical = f"{timestamp}{self._access_key}{method}{path}"
        signature = base64.b64encode(
            hmac.new(self._secret_key, canonical.encode(), hashlib.sha1).digest()
        ).decode("ascii")
        try:
            response = self._client.request(
                method,
                self._base_url + path,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Bokun-AccessKey": self._access_key,
                    "X-Bokun-Date": timestamp,
                    "X-Bokun-Signature": signature,
                    "X-Idempotency-Key": idempotency_key,
                },
                json=json_body,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderHTTPError("Bókun write result is ambiguous") from exc
        try:
            result = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderHTTPError("Bókun write result is ambiguous") from exc
        if response.status_code >= 500 or (
            response.status_code >= 400 and not allow_rejection
        ):
            raise ProviderHTTPError("Bókun write result is ambiguous")
        return response.status_code, result

    def __call__(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        if operation == "book_activity":
            if payload.get("schema") == "v2-reservation-dispatch-v2":
                return self._book_activity_v2(
                    payload,
                    idempotency_key=idempotency_key,
                )
            return self._book_activity(payload, idempotency_key=idempotency_key)
        if idempotency_key is not None:
            raise ProviderHTTPError("Bókun read forbids an idempotency key")
        canonical_id = str(payload.get("product_id") or "")
        provider_id = self._products.get(canonical_id)
        if provider_id is None:
            raise ProviderHTTPError("Bókun canonical product ID is not configured")
        meta_path = f"/activity.json/{provider_id}?lang=pt_BR&currency=BRL"
        metadata = self._get(meta_path)
        meta = dict(metadata) if isinstance(metadata, Mapping) else (_items(metadata)[0] if _items(metadata) else {})
        if operation == "activity_description":
            return {
                "bokun_product_id": provider_id,
                "product_public_name": self._title(meta) or canonical_id,
                "description": _first(meta, "description", "descriptionText", "excerpt", "summary") or "Descrição indisponível",
            }
        if operation != "activity":
            raise ProviderHTTPError("unsupported Bókun read operation")
        activity_date = str(payload.get("activity_date") or "")
        query = urlencode({"start": activity_date, "end": activity_date, "currency": "BRL"})
        availability = self._get(f"/activity.json/{provider_id}/availabilities?{query}")
        raw_participants = payload.get("participants")
        raw_adults = payload.get("adults")
        raw_children = payload.get("children")
        if raw_participants is not None:
            if raw_adults is not None or raw_children is not None:
                raise ProviderHTTPError("Bókun read party shape is ambiguous")
            adults = int(raw_participants)
            children = 0
        else:
            if type(raw_adults) is not int or type(raw_children) is not int:
                raise ProviderHTTPError("Bókun read party composition is invalid")
            adults = raw_adults
            children = raw_children
        participants = adults + children
        if adults < 1 or children < 0:
            raise ProviderHTTPError("Bókun read party composition is invalid")
        selected = next(
            (
                item
                for item in _items(availability)
                if self._available(item, participants)
            ),
            None,
        )
        if selected is not None:
            private, amount = self._activity_booking_fields(
                selected,
                meta=meta,
                adults=adults,
                children=children,
            )
        else:
            private, amount = {}, None
        if amount is None:
            amount = _first_amount(meta, "price", "amount", "totalAmount", "total")
        if amount is None:
            amount = Decimal("0")
        currency = self._option_currency(selected or {}, meta)
        result = {
            "product_id": canonical_id,
            "bokun_product_id": provider_id,
            "product_public_name": self._title(meta) or canonical_id,
            "total_amount": f"{amount:.2f}",
            "currency": currency,
            "available": selected is not None and self._quote_checkout_enabled,
            **private,
        }
        if selected is None or not self._quote_checkout_enabled:
            return result
        quote_scope = _text(payload.get("quote_scope"))
        if quote_scope is None or re.fullmatch(r"[0-9a-f]{64}", quote_scope) is None:
            raise ProviderHTTPError("Bókun quote scope is invalid")
        total = self._quote_checkout_total(
            quote_scope=quote_scope,
            product_id=provider_id,
            activity_date=activity_date,
            adults=adults,
            children=children,
            private=private,
            base_amount=amount,
        )
        fee = (total - amount).quantize(Decimal("0.01"))
        return {
            **result,
            "base_amount": f"{amount:.2f}",
            "booking_fee_amount": f"{fee:.2f}",
            "total_amount": f"{total:.2f}",
            "price_includes_booking_fee": True,
            "available": True,
        }

    def _quote_checkout_total(
        self,
        *,
        quote_scope: str,
        product_id: str,
        activity_date: str,
        adults: int,
        children: int,
        private: Mapping[str, str],
        base_amount: Decimal,
    ) -> Decimal:
        session_id = "v2-quote-" + hashlib.sha256(
            quote_scope.encode()
        ).hexdigest()[:32]
        quote_key = "quote:" + quote_scope
        cart_session_path = (
            f"/shopping-cart.json/session/{session_id}"
            "?lang=pt_BR&currency=BRL"
        )
        status, cart_payload = self._write_request(
            method="GET",
            path=cart_session_path,
            idempotency_key=quote_key + ":probe",
            allow_rejection=True,
        )
        if status == 404 or (
            status == 200
            and self._quote_cart_is_empty(
                cart_payload,
                session_id=session_id,
            )
        ):
            cart_path = (
                f"/shopping-cart.json/session/{session_id}/activity"
                "?lang=pt_BR&currency=BRL"
            )
            cart_body = {
                "activityId": product_id,
                "date": activity_date,
                "startTimeId": private["start_time_id"],
                "rateId": private["rate_id"],
                "pricingCategoryBookings": self._pricing_category_bookings(
                    private,
                    adults=adults,
                    children=children,
                ),
            }
            _, cart_payload = self._write_request(
                method="POST",
                path=cart_path,
                idempotency_key=quote_key + ":cart",
                json_body=cart_body,
            )
        elif not 200 <= status < 300:
            raise ProviderHTTPError("Bókun quote cart probe was rejected")
        self._validate_quote_cart(
            cart_payload,
            session_id=session_id,
            product_id=product_id,
            activity_date=activity_date,
            start_time_id=private["start_time_id"],
            rate_id=private["rate_id"],
            category_ids=tuple(
                item["pricingCategoryId"]
                for item in self._pricing_category_bookings(
                    private,
                    adults=adults,
                    children=children,
                )
            ),
        )
        checkout_path = (
            f"/checkout.json/options/shopping-cart/{session_id}"
            "?lang=pt_BR&currency=BRL"
        )
        _, checkout_payload = self._write_request(
            method="GET",
            path=checkout_path,
            idempotency_key=quote_key + ":checkout",
        )
        total = self._checkout_amount(checkout_payload)
        if total is None or total < base_amount:
            raise ProviderHTTPError("Bókun quote checkout total is invalid")
        return total.quantize(Decimal("0.01"))

    @staticmethod
    def _pricing_category_bookings(
        private: Mapping[str, str],
        *,
        adults: int,
        children: int,
    ) -> list[dict[str, str]]:
        if adults < 1 or children < 0:
            raise ProviderHTTPError("Bókun pricing party is invalid")
        adult_category = private.get("adult_pricing_category_id")
        child_category = private.get("child_pricing_category_id")
        if not adult_category or (children and not child_category):
            raise ProviderHTTPError("Bókun pricing category binding is incomplete")
        category_ids = [adult_category] * adults
        if child_category is not None:
            category_ids.extend([child_category] * children)
        return [
            {"pricingCategoryId": category_id}
            for category_id in category_ids
        ]

    @staticmethod
    def _quote_cart_is_empty(
        payload: object,
        *,
        session_id: str,
    ) -> bool:
        cart = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(cart, Mapping):
            cart = payload if isinstance(payload, Mapping) else {}
        returned_session = _first(cart, "uuid", "sessionId", "session_id")
        bookings = tuple(
            cart.get(name)
            for name in (
                "activityBookings",
                "accommodationBookings",
                "routeBookings",
                "giftCardBookings",
            )
        )
        return (
            returned_session == session_id
            and type(cart.get("size")) is int
            and cart.get("size") == 0
            and all(type(items) is list and not items for items in bookings)
        )

    @staticmethod
    def _validate_quote_cart(
        payload: object,
        *,
        session_id: str,
        product_id: str,
        activity_date: str,
        start_time_id: str,
        rate_id: str,
        category_ids: tuple[str, ...],
    ) -> None:
        cart = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(cart, Mapping):
            cart = payload if isinstance(payload, Mapping) else {}
        returned_session = _first(cart, "uuid", "sessionId", "session_id")
        if returned_session is not None and returned_session != session_id:
            raise ProviderHTTPError("Bókun quote cart session identity mismatch")
        activities = cart.get("activityBookings")
        matches = [
            item
            for item in activities
            if isinstance(item, Mapping)
            and BokunHTTPTransport._cart_activity_product_id(item) == product_id
        ] if isinstance(activities, list) else []
        if len(matches) != 1:
            raise ProviderHTTPError("Bókun quote cart activity binding is invalid")
        BokunHTTPTransport._validate_cart_offer_binding(
            matches[0],
            activity_date=activity_date,
            start_time_id=start_time_id,
            rate_id=rate_id,
        )
        pricing = matches[0].get("pricingCategoryBookings")
        passengers = [
            item
            for item in pricing
            if isinstance(item, Mapping)
            and BokunHTTPTransport._cart_pricing_category_id(item) is not None
            and _first(item, "bookingId", "booking_id", "id") is not None
        ] if isinstance(pricing, list) else []
        returned_categories = tuple(
            BokunHTTPTransport._cart_pricing_category_id(item)
            for item in passengers
        )
        booking_ids = tuple(
            _first(item, "bookingId", "booking_id", "id")
            for item in passengers
        )
        if (
            sorted(returned_categories) != sorted(category_ids)
            or len(booking_ids) != len(set(booking_ids))
        ):
            raise ProviderHTTPError("Bókun quote cart passenger binding is invalid")

    @staticmethod
    def _cart_activity_product_id(item: Mapping[str, object]) -> str | None:
        return _consistent_text_alias(
            item,
            ("activityId", "activity_id", "productId", "product_id"),
            nested=(("activity", ("id", "activityId", "productId")),),
            error="Bókun cart activity binding is invalid",
        )

    @staticmethod
    def _cart_pricing_category_id(item: Mapping[str, object]) -> str | None:
        return _consistent_text_alias(
            item,
            ("pricingCategoryId", "pricing_category_id"),
            nested=(("pricingCategory", ("id", "pricingCategoryId")),),
            error="Bókun cart passenger binding is invalid",
        )

    @staticmethod
    def _validate_cart_offer_binding(
        activity: Mapping[str, object],
        *,
        activity_date: str,
        start_time_id: str,
        rate_id: str,
    ) -> None:
        error = "Bókun cart offer binding diverged"
        returned_date = _consistent_text_alias(
            activity,
            ("date", "activityDate", "startDate"),
            error=error,
        )
        returned_start = _consistent_text_alias(
            activity,
            ("startTimeId", "start_time_id"),
            nested=(("startTime", ("id", "startTimeId")),),
            error=error,
        )
        returned_rate = _consistent_text_alias(
            activity,
            ("rateId", "rate_id"),
            nested=(("rate", ("id", "rateId")),),
            error=error,
        )
        if (
            (returned_date is not None and returned_date != activity_date)
            or (returned_start is not None and returned_start != start_time_id)
            or (returned_rate is not None and returned_rate != rate_id)
        ):
            raise ProviderHTTPError("Bókun cart offer binding diverged")

    def _book_activity_v2(
        self,
        payload: dict[str, object],
        *,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        if (
            type(idempotency_key) is not str
            or not idempotency_key
            or "\x00" in idempotency_key
        ):
            raise ProviderHTTPError("Bókun write requires an idempotency key")
        dispatch = _exact_object(
            payload,
            fields=frozenset(
                ("schema", "command_id", "operation", "offer", "customer", "terms")
            ),
            name="Bókun dispatch v2",
        )
        if (
            dispatch["schema"] != "v2-reservation-dispatch-v2"
            or dispatch["operation"] != "book_activity"
            or type(dispatch["command_id"]) is not str
        ):
            raise ProviderHTTPError("Bókun dispatch v2 identity mismatch")
        offer = _exact_object(
            dispatch["offer"],
            fields=frozenset(
                (
                    "binding",
                    "private_binding",
                    "offer_id",
                    "start_date",
                    "end_date",
                    "start_time",
                    "party",
                    "amount",
                    "currency",
                )
            ),
            name="Bókun offer v2",
        )
        party = _exact_object(
            offer["party"],
            fields=frozenset(("adults", "children")),
            name="Bókun party v2",
        )
        adults = party["adults"]
        children = party["children"]
        if (
            type(adults) is not int
            or type(children) is not int
            or adults < 1
            or children < 0
        ):
            raise ProviderHTTPError("Bókun party v2 is invalid")
        private_fields = {
            "bokun_product_id",
            "start_time_id",
            "rate_id",
            "adult_pricing_category_id",
        }
        if children:
            private_fields.add("child_pricing_category_id")
        private = _exact_object(
            offer["private_binding"],
            fields=frozenset(private_fields),
            name="Bókun private binding v2",
        )
        product_id = _text(private["bokun_product_id"])
        start_time_id = _text(private["start_time_id"])
        rate_id = _text(private["rate_id"])
        adult_category = _text(private["adult_pricing_category_id"])
        child_category = (
            _text(private["child_pricing_category_id"])
            if children
            else None
        )
        if (
            not product_id
            or product_id not in set(self._products.values())
            or not start_time_id
            or not rate_id
            or not adult_category
            or (children and not child_category)
        ):
            raise ProviderHTTPError("Bókun private binding v2 is incomplete")
        customer = _exact_object(
            dispatch["customer"],
            fields=frozenset(
                (
                    "customer_ref",
                    "full_name",
                    "email",
                    "phone_e164",
                    "country_code",
                    "passengers",
                )
            ),
            name="Bókun customer v2",
        )
        terms = _exact_object(
            dispatch["terms"],
            fields=frozenset(("payment_method", "add_ons")),
            name="Bókun terms v2",
        )
        if terms["payment_method"] not in ("stripe", "wise", "pix"):
            raise ProviderHTTPError("Bókun payment method is invalid")
        if terms["add_ons"] != []:
            raise ProviderHTTPError("Bókun reservation write does not accept add-ons")
        main_name = _text(customer["full_name"])
        email = _text(customer["email"])
        phone = _text(customer["phone_e164"])
        country = _text(customer["country_code"])
        main_name_parts = main_name.split() if main_name else []
        if (
            len(main_name_parts) < 2
            or email is None
            or email.count("@") != 1
            or phone is None
            or re.fullmatch(r"\+[1-9][0-9]{7,14}", phone) is None
            or country is None
            or re.fullmatch(r"[A-Z]{2}", country) is None
        ):
            raise ProviderHTTPError("Bókun customer v2 fields are invalid")
        passenger_values = customer["passengers"]
        if not isinstance(passenger_values, list):
            raise ProviderHTTPError("Bókun passenger manifest is invalid")
        passengers: list[dict[str, str]] = []
        for expected_position, value in enumerate(passenger_values, start=1):
            item = _exact_object(
                value,
                fields=frozenset(
                    (
                        "position",
                        "participant_type",
                        "full_name",
                        "birth_date",
                        "gender",
                        "country_code",
                    )
                ),
                name="Bókun passenger",
            )
            participant_type = item["participant_type"]
            full_name = _text(item["full_name"])
            birth_date = _text(item["birth_date"])
            gender = _text(item["gender"])
            passenger_country = _text(item["country_code"])
            name_parts = full_name.split() if full_name else []
            try:
                parsed_birth = date.fromisoformat(birth_date or "")
            except ValueError as exc:
                raise ProviderHTTPError("Bókun passenger birth date is invalid") from exc
            if (
                item["position"] != expected_position
                or participant_type not in ("adult", "child")
                or len(name_parts) < 2
                or parsed_birth.isoformat() != birth_date
                or gender not in ("m", "f")
                or passenger_country is None
                or re.fullmatch(r"[A-Z]{2}", passenger_country) is None
            ):
                raise ProviderHTTPError("Bókun passenger fields are invalid")
            category_id = (
                adult_category if participant_type == "adult" else child_category
            )
            if category_id is None:
                raise ProviderHTTPError("Bókun passenger category is unavailable")
            passengers.append(
                {
                    "category_id": category_id,
                    "firstName": name_parts[0],
                    "lastName": " ".join(name_parts[1:]),
                    "nationality": passenger_country,
                    "dateOfBirth": birth_date,
                    "gender": gender,
                    "full_name": full_name,
                }
            )
        if (
            len(passengers) != adults + children
            or sum(item["category_id"] == adult_category for item in passengers)
            != adults
            or sum(item["category_id"] == child_category for item in passengers)
            != children
        ):
            raise ProviderHTTPError("Bókun passenger manifest diverged from party")
        activity_date = _text(offer["start_date"])
        try:
            parsed_date = date.fromisoformat(activity_date or "")
        except ValueError as exc:
            raise ProviderHTTPError("Bókun activity date is invalid") from exc
        amount = _amount(offer["amount"])
        if (
            parsed_date.isoformat() != activity_date
            or offer["end_date"] is not None
            or amount is None
            or type(offer["amount"]) is not str
            or offer["amount"] != f"{amount:.2f}"
            or _currency(offer["currency"]) != "BRL"
        ):
            raise ProviderHTTPError("Bókun activity amount or interval is invalid")
        session_id = "v2-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        category_bookings = [
            {"pricingCategoryId": item["category_id"]}
            for item in passengers
        ]
        cart_path = (
            f"/shopping-cart.json/session/{session_id}/activity"
            "?lang=pt_BR&currency=BRL"
        )
        cart_status, cart_payload = self._write_request(
            method="POST",
            path=cart_path,
            idempotency_key=idempotency_key + ":cart",
            json_body={
                "activityId": product_id,
                "date": activity_date,
                "startTimeId": start_time_id,
                "rateId": rate_id,
                "pricingCategoryBookings": category_bookings,
            },
            allow_rejection=True,
        )
        if not 200 <= cart_status < 300:
            return {"status": "no_effect"}
        try:
            activity_booking, bound_passengers = self._cart_bindings_v2(
                cart_payload,
                session_id=session_id,
                product_id=product_id,
                activity_date=activity_date,
                start_time_id=start_time_id,
                rate_id=rate_id,
                passengers=tuple(passengers),
            )
        except ProviderHTTPError:
            return {"status": "no_effect"}
        checkout_path = (
            f"/checkout.json/options/shopping-cart/{session_id}"
            "?lang=pt_BR&currency=BRL"
        )
        checkout_status, checkout_payload = self._write_request(
            method="GET",
            path=checkout_path,
            idempotency_key=idempotency_key + ":checkout",
            allow_rejection=True,
        )
        if not 200 <= checkout_status < 300:
            return {"status": "no_effect"}
        main_contact = {
            "firstName": main_name_parts[0],
            "lastName": " ".join(main_name_parts[1:]),
            "email": email,
            "phoneNumber": phone,
            "nationality": country,
            "language": "pt",
        }
        if passengers[0]["full_name"] == main_name:
            main_contact.update(
                {
                    "dateOfBirth": passengers[0]["dateOfBirth"],
                    "gender": passengers[0]["gender"],
                }
            )
        try:
            submit_body = self._submit_body_v2(
                checkout_payload,
                session_id=session_id,
                activity_booking=activity_booking,
                product_id=product_id,
                main_contact=main_contact,
                passengers=bound_passengers,
                expected_amount=amount,
            )
        except ProviderHTTPError:
            return {"status": "no_effect"}
        status, submit_payload = self._write_request(
            method="POST",
            path="/checkout.json/submit?lang=pt_BR&currency=BRL",
            idempotency_key=idempotency_key + ":submit",
            json_body=submit_body,
            allow_rejection=True,
        )
        booking_id = self._booking_reference(submit_payload)
        if booking_id is None:
            if status in {400, 401, 403, 404, 405, 406, 415, 422} or (
                200 <= status < 300
                and isinstance(submit_payload, Mapping)
                and submit_payload.get("success") is False
            ):
                return {"status": "rejected"}
            raise ProviderHTTPError("Bókun write result is ambiguous")
        _, readback = self._write_request(
            method="GET",
            path=(
                f"/booking.json/booking/{booking_id}"
                "?lang=pt_BR&currency=BRL"
            ),
            idempotency_key=idempotency_key + ":readback",
        )
        self._validate_booking_readback_v2(
            readback,
            booking_id=booking_id,
            product_id=product_id,
            activity_date=activity_date,
            category_ids=tuple(item["category_id"] for item in passengers),
            expected_amount=amount,
        )
        return {"status": "confirmed", "booking_id": booking_id}

    @staticmethod
    def _cart_bindings_v2(
        payload: object,
        *,
        session_id: str,
        product_id: str,
        activity_date: str,
        start_time_id: str,
        rate_id: str,
        passengers: tuple[dict[str, str], ...],
    ) -> tuple[str, tuple[dict[str, str], ...]]:
        cart = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(cart, Mapping):
            cart = payload if isinstance(payload, Mapping) else {}
        returned_session = _first(cart, "uuid", "sessionId", "session_id")
        if returned_session is not None and returned_session != session_id:
            raise ProviderHTTPError("Bókun cart session identity mismatch")
        activities = cart.get("activityBookings")
        matches = [
            item
            for item in activities
            if isinstance(item, Mapping)
            and BokunHTTPTransport._cart_activity_product_id(item) == product_id
        ] if isinstance(activities, list) else []
        if len(matches) != 1:
            raise ProviderHTTPError("Bókun cart activity binding is invalid")
        activity = matches[0]
        BokunHTTPTransport._validate_cart_offer_binding(
            activity,
            activity_date=activity_date,
            start_time_id=start_time_id,
            rate_id=rate_id,
        )
        activity_booking = _first(activity, "bookingId", "booking_id", "id")
        pricing = activity.get("pricingCategoryBookings")
        rows = [item for item in pricing if isinstance(item, Mapping)] if isinstance(pricing, list) else []
        returned: dict[str, list[str]] = {}
        booking_ids: list[str] = []
        for row in rows:
            category_id = BokunHTTPTransport._cart_pricing_category_id(row)
            booking_id = _first(row, "bookingId", "booking_id", "id")
            if not category_id or not booking_id:
                raise ProviderHTTPError("Bókun cart passenger binding is invalid")
            returned.setdefault(category_id, []).append(booking_id)
            booking_ids.append(booking_id)
        expected_categories = [item["category_id"] for item in passengers]
        if (
            not activity_booking
            or len(booking_ids) != len(passengers)
            or len(booking_ids) != len(set(booking_ids))
            or sorted(returned) != sorted(set(expected_categories))
            or any(
                len(returned.get(category_id, ()))
                != expected_categories.count(category_id)
                for category_id in set(expected_categories)
            )
        ):
            raise ProviderHTTPError("Bókun cart passenger binding is invalid")
        offsets: dict[str, int] = {}
        bound = []
        for passenger in passengers:
            category_id = passenger["category_id"]
            offset = offsets.get(category_id, 0)
            booking_id = returned[category_id][offset]
            offsets[category_id] = offset + 1
            bound.append({**passenger, "booking_id": booking_id})
        return activity_booking, tuple(bound)

    @staticmethod
    def _submit_body_v2(
        payload: object,
        *,
        session_id: str,
        activity_booking: str,
        product_id: str,
        main_contact: dict[str, str],
        passengers: tuple[dict[str, str], ...],
        expected_amount: Decimal,
    ) -> dict[str, object]:
        checkout = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(checkout, Mapping):
            raise ProviderHTTPError("Bókun checkout fields mismatch")
        options = checkout.get("options")
        option = options[0] if isinstance(options, list) and options else None
        if not isinstance(option, Mapping):
            raise ProviderHTTPError("Bókun checkout lacks an option")
        checkout_amount = BokunHTTPTransport._checkout_amount(checkout)
        if (
            checkout_amount is None
            or checkout_amount.quantize(Decimal("0.01")) != expected_amount
        ):
            raise ProviderHTTPError("Bókun checkout amount diverged after cart")
        questions = checkout.get("questions")
        if not isinstance(questions, Mapping):
            raise ProviderHTTPError("Bókun checkout questions are unavailable")
        main_questions = questions.get("mainContactDetails")
        activities = questions.get("activityBookings")
        if not isinstance(main_questions, list) or not isinstance(activities, list):
            raise ProviderHTTPError("Bókun checkout question shape is invalid")

        def required_ids(values: object) -> tuple[str, ...]:
            if not isinstance(values, list):
                return ()
            result = []
            for item in values:
                if isinstance(item, Mapping) and item.get("required") is True:
                    question_id = _first(item, "questionId", "id")
                    if question_id:
                        result.append(question_id)
            return tuple(result)

        def question_ids(values: object) -> tuple[str, ...]:
            if not isinstance(values, list):
                return ()
            result = []
            for item in values:
                if isinstance(item, Mapping):
                    question_id = _first(item, "questionId", "id")
                    if question_id:
                        result.append(question_id)
            if len(result) != len(set(result)):
                raise ProviderHTTPError("Bókun checkout question IDs are ambiguous")
            return tuple(result)

        def answers(ids: tuple[str, ...], values: Mapping[str, str]):
            return [
                {"questionId": question_id, "values": [values[question_id]]}
                for question_id in ids
            ]

        main_required = required_ids(main_questions)
        if set(main_required) - set(main_contact):
            raise ProviderHTTPError("Bókun checkout requires unsupported customer fields")
        main_answer_ids = tuple(
            question_id
            for question_id in question_ids(main_questions)
            if question_id in main_contact
        )
        activity_matches = [
            activity
            for activity in activities
            if isinstance(activity, Mapping)
            and _first(activity, "bookingId", "booking_id", "id")
            == activity_booking
            and BokunHTTPTransport._cart_activity_product_id(activity)
            == product_id
        ]
        if len(activities) != 1 or len(activity_matches) != 1:
            raise ProviderHTTPError("Bókun checkout activity binding diverged")
        passenger_groups = activity_matches[0].get("passengers")
        if not isinstance(passenger_groups, list) or len(passenger_groups) != len(
            passengers
        ):
            raise ProviderHTTPError("Bókun checkout passenger count diverged")
        groups_by_booking: dict[str, Mapping[str, object]] = {}
        for group in passenger_groups:
            if not isinstance(group, Mapping):
                raise ProviderHTTPError("Bókun checkout passenger shape is invalid")
            booking_id = _first(group, "bookingId", "booking_id", "id")
            category_id = BokunHTTPTransport._cart_pricing_category_id(group)
            if not booking_id or not category_id or booking_id in groups_by_booking:
                raise ProviderHTTPError("Bókun checkout passenger binding is invalid")
            groups_by_booking[booking_id] = group
        passenger_answers = []
        for passenger in passengers:
            booking_id = passenger["booking_id"]
            group = groups_by_booking.get(booking_id)
            if (
                group is None
                or BokunHTTPTransport._cart_pricing_category_id(group)
                != passenger["category_id"]
            ):
                raise ProviderHTTPError("Bókun checkout passenger binding diverged")
            details = group.get("passengerDetails")
            required = required_ids(details)
            values = {
                key: passenger[key]
                for key in (
                    "firstName",
                    "lastName",
                    "nationality",
                    "dateOfBirth",
                    "gender",
                )
            }
            if set(required) - set(values):
                raise ProviderHTTPError(
                    "Bókun checkout requires unsupported passenger fields"
                )
            if required_ids(group.get("questions")):
                raise ProviderHTTPError(
                    "Bókun checkout requires unsupported special answers"
                )
            answer_ids = tuple(
                question_id
                for question_id in question_ids(details)
                if question_id in values
            )
            passenger_answers.append(
                {
                    "bookingId": booking_id,
                    "pricingCategoryId": passenger["category_id"],
                    "passengerDetails": answers(answer_ids, values),
                }
            )
        if set(groups_by_booking) != {
            passenger["booking_id"] for passenger in passengers
        }:
            raise ProviderHTTPError("Bókun checkout passenger binding diverged")
        return {
            "checkoutOption": "CUSTOMER_FULL_PAYMENT",
            "paymentMethod": "RESERVE_FOR_EXTERNAL_PAYMENT",
            "source": "SHOPPING_CART",
            "shoppingCart": {
                "uuid": session_id,
                "bookingAnswers": {
                    "mainContactDetails": answers(main_answer_ids, main_contact),
                    "activityBookings": [
                        {
                            "bookingId": activity_booking,
                            "activityId": product_id,
                            "passengers": passenger_answers,
                        }
                    ],
                },
            },
            "sendNotificationToMainContact": False,
            "showPricesInNotification": False,
        }

    @staticmethod
    def _validate_booking_readback_v2(
        payload: object,
        *,
        booking_id: str,
        product_id: str,
        activity_date: str,
        category_ids: tuple[str, ...],
        expected_amount: Decimal,
    ) -> None:
        candidates: list[Mapping[str, object]] = []

        def visit(value: object) -> None:
            if isinstance(value, Mapping):
                returned_booking_id = _consistent_text_alias(
                    value,
                    ("bookingId", "booking_id"),
                    error="Bókun write read-back did not match",
                )
                if (
                    returned_booking_id == booking_id
                    and isinstance(value.get("activityBookings"), list)
                ):
                    candidates.append(value)
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(payload)
        if len(candidates) != 1:
            raise ProviderHTTPError("Bókun write read-back did not match")
        booking = candidates[0]
        returned_status = _consistent_text_alias(
            booking,
            ("status", "bookingStatus"),
            error="Bókun booking read-back status mismatch",
        )
        if returned_status is not None and returned_status.upper() not in {
            "BOOKED",
            "CONFIRMED",
            "PAID",
            "PENDING",
            "RESERVED",
        }:
            raise ProviderHTTPError("Bókun booking read-back status mismatch")
        amount_values: list[Decimal] = []
        amount_currencies: list[str] = []
        for name in ("totalPrice", "totalAmount", "amount", "totalDue"):
            if name not in booking:
                continue
            raw_amount = booking[name]
            returned_amount = _amount(raw_amount)
            if returned_amount is None:
                raise ProviderHTTPError("Bókun booking read-back amount mismatch")
            amount_values.append(returned_amount.quantize(Decimal("0.01")))
            if isinstance(raw_amount, Mapping) and (
                "currency" in raw_amount or "currencyCode" in raw_amount
            ):
                raw_currency = raw_amount.get("currency") or raw_amount.get(
                    "currencyCode"
                )
                returned_currency = _text(raw_currency)
                if returned_currency is None:
                    raise ProviderHTTPError("Bókun booking read-back amount mismatch")
                amount_currencies.append(returned_currency.upper())
        if (
            any(value != expected_amount for value in amount_values)
            or len(set(amount_values)) > 1
            or any(currency != "BRL" for currency in amount_currencies)
            or len(set(amount_currencies)) > 1
        ):
            raise ProviderHTTPError("Bókun booking read-back amount mismatch")
        activities = booking.get("activityBookings")
        matches = [
            item
            for item in activities
            if isinstance(item, Mapping)
            and BokunHTTPTransport._cart_activity_product_id(item) == product_id
            and _consistent_text_alias(
                item,
                ("date", "activityDate", "startDate"),
                error="Bókun write read-back activity diverged",
            )
            == activity_date
        ] if isinstance(activities, list) else []
        if len(matches) != 1:
            raise ProviderHTTPError("Bókun write read-back activity diverged")
        pricing = matches[0].get("pricingCategoryBookings")
        returned_categories = tuple(
            category_id
            for item in pricing
            if isinstance(item, Mapping)
            and (
                category_id
                := BokunHTTPTransport._cart_pricing_category_id(item)
            ) is not None
        ) if isinstance(pricing, list) else ()
        if (
            len(returned_categories) != len(category_ids)
            or sorted(returned_categories) != sorted(category_ids)
        ):
            raise ProviderHTTPError("Bókun write read-back party diverged")

    def _book_activity(
        self,
        payload: dict[str, object],
        *,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        if (
            type(idempotency_key) is not str
            or not idempotency_key
            or "\x00" in idempotency_key
        ):
            raise ProviderHTTPError("Bókun write requires an idempotency key")
        dispatch = _exact_object(
            payload,
            fields=frozenset(
                ("schema", "command_id", "operation", "offer", "customer", "terms")
            ),
            name="Bókun dispatch",
        )
        if (
            dispatch["schema"] != "v2-reservation-dispatch-v1"
            or dispatch["operation"] != "book_activity"
            or type(dispatch["command_id"]) is not str
        ):
            raise ProviderHTTPError("Bókun dispatch identity mismatch")
        offer = _exact_object(
            dispatch["offer"],
            fields=frozenset(
                (
                    "binding",
                    "private_binding",
                    "offer_id",
                    "start_date",
                    "end_date",
                    "start_time",
                    "party",
                    "amount",
                    "currency",
                )
            ),
            name="Bókun offer",
        )
        private = _exact_object(
            offer["private_binding"],
            fields=frozenset(
                (
                    "bokun_product_id",
                    "start_time_id",
                    "rate_id",
                    "adult_pricing_category_id",
                )
            ),
            name="Bókun private binding",
        )
        party = _exact_object(
            offer["party"],
            fields=frozenset(("adults", "children")),
            name="Bókun party",
        )
        customer = _exact_object(
            dispatch["customer"],
            fields=frozenset(
                (
                    "customer_ref",
                    "full_name",
                    "email",
                    "phone_e164",
                    "country_code",
                    "birth_date",
                    "gender",
                )
            ),
            name="Bókun customer",
        )
        terms = _exact_object(
            dispatch["terms"],
            fields=frozenset(("payment_method", "add_ons")),
            name="Bókun terms",
        )
        if terms["payment_method"] not in ("stripe", "wise", "pix"):
            raise ProviderHTTPError("Bókun payment method is invalid")
        if terms["add_ons"] != []:
            raise ProviderHTTPError("Bókun reservation write does not accept add-ons")
        adults = party["adults"]
        children = party["children"]
        if type(adults) is not int or type(children) is not int:
            raise ProviderHTTPError("Bókun party is invalid")
        if adults + children != 1:
            raise ProviderHTTPError(
                "Bókun canary write currently requires exactly one passenger"
            )
        if children != 0:
            raise ProviderHTTPError("Bókun canary passenger must use the adult category")
        product_id = _text(private["bokun_product_id"])
        start_time_id = _text(private["start_time_id"])
        rate_id = _text(private["rate_id"])
        category_id = _text(private["adult_pricing_category_id"])
        if (
            not product_id
            or product_id not in set(self._products.values())
            or not start_time_id
            or not rate_id
            or not category_id
        ):
            raise ProviderHTTPError("Bókun private binding is incomplete")
        activity_date = _text(offer["start_date"])
        try:
            parsed_date = date.fromisoformat(activity_date or "")
        except ValueError as exc:
            raise ProviderHTTPError("Bókun activity date is invalid") from exc
        if parsed_date.isoformat() != activity_date or offer["end_date"] is not None:
            raise ProviderHTTPError("Bókun activity interval is invalid")
        amount = _amount(offer["amount"])
        if (
            amount is None
            or type(offer["amount"]) is not str
            or offer["amount"] != f"{amount:.2f}"
            or _currency(offer["currency"]) != "BRL"
        ):
            raise ProviderHTTPError("Bókun amount/currency is invalid")
        full_name = _text(customer["full_name"])
        email = _text(customer["email"])
        phone = _text(customer["phone_e164"])
        country = _text(customer["country_code"])
        birth_date = _text(customer["birth_date"])
        gender = _text(customer["gender"])
        name_parts = full_name.split() if full_name else []
        try:
            parsed_birth = date.fromisoformat(birth_date or "")
        except ValueError as exc:
            raise ProviderHTTPError("Bókun customer birth date is invalid") from exc
        if (
            len(name_parts) < 2
            or email is None
            or email.count("@") != 1
            or phone is None
            or re.fullmatch(r"\+[1-9][0-9]{7,14}", phone) is None
            or country is None
            or re.fullmatch(r"[A-Z]{2}", country) is None
            or parsed_birth.isoformat() != birth_date
            or gender not in ("m", "f")
        ):
            raise ProviderHTTPError("Bókun customer fields are invalid")
        session_id = "v2-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        cart_path = (
            f"/shopping-cart.json/session/{session_id}/activity"
            "?lang=pt_BR&currency=BRL"
        )
        cart_body = {
            "activityId": product_id,
            "date": activity_date,
            "startTimeId": start_time_id,
            "rateId": rate_id,
            "pricingCategoryBookings": [
                {"pricingCategoryId": category_id}
            ],
        }
        _, cart_payload = self._write_request(
            method="POST",
            path=cart_path,
            idempotency_key=idempotency_key + ":cart",
            json_body=cart_body,
        )
        activity_booking, passenger_booking = self._cart_bindings(
            cart_payload,
            session_id=session_id,
            product_id=product_id,
            category_id=category_id,
        )
        checkout_path = (
            f"/checkout.json/options/shopping-cart/{session_id}"
            "?lang=pt_BR&currency=BRL"
        )
        _, checkout_payload = self._write_request(
            method="GET",
            path=checkout_path,
            idempotency_key=idempotency_key + ":checkout",
        )
        submit_body = self._submit_body(
            checkout_payload,
            session_id=session_id,
            activity_booking=activity_booking,
            passenger_booking=passenger_booking,
            product_id=product_id,
            category_id=category_id,
            customer={
                "firstName": name_parts[0],
                "lastName": " ".join(name_parts[1:]),
                "email": email,
                "phoneNumber": phone,
                "nationality": country,
                "language": "pt",
                "dateOfBirth": birth_date,
                "gender": gender,
            },
            expected_amount=amount,
        )
        submit_path = "/checkout.json/submit?lang=pt_BR&currency=BRL"
        status, submit_payload = self._write_request(
            method="POST",
            path=submit_path,
            idempotency_key=idempotency_key + ":submit",
            json_body=submit_body,
            allow_rejection=True,
        )
        booking_id = self._booking_reference(submit_payload)
        if booking_id is None:
            if 400 <= status < 500 or (
                isinstance(submit_payload, Mapping)
                and submit_payload.get("success") is False
            ):
                return {"status": "rejected"}
            raise ProviderHTTPError("Bókun write result is ambiguous")
        readback_path = (
            f"/booking.json/booking/{booking_id}?lang=pt_BR&currency=BRL"
        )
        _, readback = self._write_request(
            method="GET",
            path=readback_path,
            idempotency_key=idempotency_key + ":readback",
        )
        if self._booking_reference(readback) != booking_id:
            raise ProviderHTTPError("Bókun write read-back did not match")
        return {"status": "confirmed", "booking_id": booking_id}

    @staticmethod
    def _activity_booking_fields(
        item: Mapping[str, object],
        *,
        meta: Mapping[str, object],
        adults: int,
        children: int,
    ) -> tuple[dict[str, str], Decimal]:
        categories = meta.get("pricingCategories")
        if not isinstance(categories, list):
            raise ProviderHTTPError("Bókun metadata lacks pricing categories")

        def category_id(ticket_category: str) -> str:
            matches = tuple(
                value
                for category in categories
                if isinstance(category, Mapping)
                and (category_name := _text(category.get("ticketCategory"))) is not None
                and category_name.upper() == ticket_category
                and (value := _first(category, "id", "pricingCategoryId"))
            )
            if len(matches) != 1:
                raise ProviderHTTPError(
                    f"Bókun {ticket_category.lower()} pricing category is ambiguous"
                )
            return matches[0]

        adult_category = category_id("ADULT")
        child_category = category_id("CHILD") if children else None
        start_time_id = _first(item, "startTimeId", "start_time_id")
        start_time = item.get("startTime")
        if start_time_id is None and isinstance(start_time, Mapping):
            start_time_id = _first(start_time, "id", "startTimeId")
        rates = item.get("pricesByRate")
        if not start_time_id or not isinstance(rates, list):
            raise ProviderHTTPError("Bókun availability lacks executable booking fields")
        currency_mismatch = False
        for rate in rates:
            if not isinstance(rate, Mapping):
                continue
            rate_id = _first(rate, "activityRateId", "rateId", "id")
            units = rate.get("pricePerCategoryUnit")
            if not rate_id or not isinstance(units, list):
                continue
            by_id = {
                category: unit
                for unit in units
                if isinstance(unit, Mapping)
                and (category := _first(unit, "id", "pricingCategoryId"))
            }
            adult_unit = by_id.get(adult_category)
            child_unit = by_id.get(child_category) if child_category else None
            if adult_unit is None or (children and child_unit is None):
                continue
            adult_currency = BokunHTTPTransport._pricing_unit_currency(adult_unit)
            child_currency = (
                BokunHTTPTransport._pricing_unit_currency(child_unit)
                if isinstance(child_unit, Mapping)
                else adult_currency
            )
            if (
                adult_currency is None
                or child_currency is None
                or child_currency != adult_currency
            ):
                currency_mismatch = True
                continue
            adult_amount = _amount(adult_unit.get("amount"))
            child_amount = (
                _amount(child_unit.get("amount"))
                if isinstance(child_unit, Mapping)
                else Decimal("0")
            )
            if adult_amount is None or child_amount is None:
                continue
            private = {
                "start_time_id": start_time_id,
                "rate_id": rate_id,
                "adult_pricing_category_id": adult_category,
            }
            if child_category is not None:
                private["child_pricing_category_id"] = child_category
            return private, adult_amount * adults + child_amount * children
        if currency_mismatch:
            raise ProviderHTTPError("Bókun pricing category currency mismatch")
        raise ProviderHTTPError("Bókun required pricing category is unavailable")

    @staticmethod
    def _pricing_unit_currency(unit: Mapping[str, object]) -> str | None:
        amount = unit.get("amount")
        if not isinstance(amount, Mapping):
            return None
        value = amount.get("currency") or amount.get("currencyCode")
        return _currency(value) if value is not None else None

    @staticmethod
    def _cart_bindings(
        payload: object,
        *,
        session_id: str,
        product_id: str,
        category_id: str,
    ) -> tuple[str, str]:
        cart = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(cart, Mapping):
            cart = payload if isinstance(payload, Mapping) else {}
        returned_session = _first(cart, "uuid", "sessionId", "session_id")
        if returned_session is not None and returned_session != session_id:
            raise ProviderHTTPError("Bókun cart session identity mismatch")
        activities = cart.get("activityBookings")
        matches = [
            item
            for item in activities
            if isinstance(item, Mapping)
            and BokunHTTPTransport._cart_activity_product_id(item) == product_id
        ] if isinstance(activities, list) else []
        if len(matches) != 1:
            raise ProviderHTTPError("Bókun cart activity binding is invalid")
        activity = matches[0]
        activity_booking = _first(activity, "bookingId", "booking_id", "id")
        pricing = activity.get("pricingCategoryBookings")
        passenger_matches = [
            item
            for item in pricing
            if isinstance(item, Mapping)
            and BokunHTTPTransport._cart_pricing_category_id(item) == category_id
        ] if isinstance(pricing, list) else []
        passenger_booking = (
            _first(passenger_matches[0], "bookingId", "booking_id", "id")
            if len(passenger_matches) == 1
            else None
        )
        if not activity_booking or not passenger_booking:
            raise ProviderHTTPError("Bókun cart passenger binding is invalid")
        return activity_booking, passenger_booking

    @staticmethod
    def _checkout_amount(payload: object) -> Decimal | None:
        checkout = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(checkout, Mapping):
            return None
        options = checkout.get("options")
        option = options[0] if isinstance(options, list) and options else None
        if not isinstance(option, Mapping):
            return None
        invoice = option.get("invoice")
        if isinstance(invoice, Mapping):
            due = _first_amount(
                invoice,
                "remainingAmount",
                "remainingAmountAsText",
                "totalDue",
                "totalDueAsText",
            )
            if due is not None:
                return due
        return _first_amount(
            option,
            "remainingAmount",
            "remainingAmountAsText",
            "totalDue",
            "totalDueAsText",
            "amount",
            "totalPrice",
            "formattedAmount",
        )

    @staticmethod
    def _submit_body(
        payload: object,
        *,
        session_id: str,
        activity_booking: str,
        passenger_booking: str,
        product_id: str,
        category_id: str,
        customer: dict[str, str],
        expected_amount: Decimal,
    ) -> dict[str, object]:
        checkout = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(checkout, Mapping):
            raise ProviderHTTPError("Bókun checkout fields mismatch")
        options = checkout.get("options")
        option = options[0] if isinstance(options, list) and options else None
        if not isinstance(option, Mapping):
            raise ProviderHTTPError("Bókun checkout lacks an option")
        checkout_amount = BokunHTTPTransport._checkout_amount(checkout)
        if checkout_amount is None or checkout_amount.quantize(Decimal("0.01")) != expected_amount:
            raise ProviderHTTPError("Bókun checkout amount diverged after cart")
        questions = checkout.get("questions")
        if not isinstance(questions, Mapping):
            raise ProviderHTTPError("Bókun checkout questions are unavailable")
        main_questions = questions.get("mainContactDetails")
        activities = questions.get("activityBookings")
        if not isinstance(main_questions, list) or not isinstance(activities, list):
            raise ProviderHTTPError("Bókun checkout question shape is invalid")

        def required_ids(values: object) -> tuple[str, ...]:
            if not isinstance(values, list):
                return ()
            result = []
            for item in values:
                if isinstance(item, Mapping) and item.get("required") is True:
                    question_id = _first(item, "questionId", "id")
                    if question_id:
                        result.append(question_id)
            return tuple(result)

        def question_ids(values: object) -> tuple[str, ...]:
            if not isinstance(values, list):
                return ()
            result = []
            for item in values:
                if isinstance(item, Mapping):
                    question_id = _first(item, "questionId", "id")
                    if question_id:
                        result.append(question_id)
            if len(result) != len(set(result)):
                raise ProviderHTTPError("Bókun checkout question IDs are ambiguous")
            return tuple(result)

        main_required = required_ids(main_questions)
        unknown_main = set(main_required) - set(customer)
        if unknown_main:
            raise ProviderHTTPError("Bókun checkout requires unsupported customer fields")
        main_answer_ids = tuple(
            question_id
            for question_id in question_ids(main_questions)
            if question_id in customer
        )
        passenger_question_groups = []
        for activity in activities:
            if isinstance(activity, Mapping) and isinstance(activity.get("passengers"), list):
                passenger_question_groups.extend(activity["passengers"])
        if len(passenger_question_groups) != 1 or not isinstance(
            passenger_question_groups[0], Mapping
        ):
            raise ProviderHTTPError("Bókun checkout passenger count diverged")
        passenger_questions = passenger_question_groups[0]
        passenger_required = required_ids(
            passenger_questions.get("passengerDetails")
        )
        passenger_question_ids = question_ids(
            passenger_questions.get("passengerDetails")
        )
        passenger_values = {
            key: customer[key]
            for key in ("firstName", "lastName", "nationality", "dateOfBirth", "gender")
        }
        if set(passenger_required) - set(passenger_values):
            raise ProviderHTTPError("Bókun checkout requires unsupported passenger fields")
        passenger_answer_ids = tuple(
            question_id
            for question_id in passenger_question_ids
            if question_id in passenger_values
        )
        if required_ids(passenger_questions.get("questions")):
            raise ProviderHTTPError("Bókun checkout requires unsupported special answers")

        def answers(required: tuple[str, ...], values: Mapping[str, str]):
            return [
                {"questionId": question_id, "values": [values[question_id]]}
                for question_id in required
            ]

        return {
            "checkoutOption": "CUSTOMER_FULL_PAYMENT",
            "paymentMethod": "RESERVE_FOR_EXTERNAL_PAYMENT",
            "source": "SHOPPING_CART",
            "shoppingCart": {
                "uuid": session_id,
                "bookingAnswers": {
                    "mainContactDetails": answers(main_answer_ids, customer),
                    "activityBookings": [
                        {
                            "bookingId": activity_booking,
                            "activityId": product_id,
                            "passengers": [
                                {
                                    "bookingId": passenger_booking,
                                    "pricingCategoryId": category_id,
                                    "passengerDetails": answers(
                                        passenger_answer_ids, passenger_values
                                    ),
                                }
                            ],
                        }
                    ],
                },
            },
            "sendNotificationToMainContact": False,
            "showPricesInNotification": False,
        }

    @staticmethod
    def _booking_reference(payload: object) -> str | None:
        if isinstance(payload, Mapping):
            direct = _consistent_text_alias(
                payload,
                ("bookingId", "booking_id"),
                error="Bókun write result is ambiguous",
            )
            if direct:
                return direct
            for value in payload.values():
                found = BokunHTTPTransport._booking_reference(value)
                if found:
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = BokunHTTPTransport._booking_reference(value)
                if found:
                    return found
        return None

    @staticmethod
    def _title(meta: Mapping[str, object]) -> str | None:
        translations = meta.get("translations")
        if isinstance(translations, Mapping):
            pt = translations.get("pt_BR")
            if isinstance(pt, Mapping):
                title = _first(pt, "title", "name")
                if title:
                    return title
        return _first(meta, "title", "name", "displayName")

    @staticmethod
    def _available(item: Mapping[str, object], participants: int) -> bool:
        if item.get("soldOut") is True or item.get("unavailable") is True or item.get("available") is False:
            return False
        units = _integer(
            item,
            "availabilityCount",
            "capacityCount",
            "availability",
            "seatsAvailable",
        )
        return units is None or units >= participants

    @staticmethod
    def _participant_total(item: Mapping[str, object], participants: int) -> Decimal | None:
        rates = item.get("pricesByRate")
        if not isinstance(rates, list):
            return _first_amount(item, "totalAmount", "total", "amount", "price")
        for rate in rates:
            if not isinstance(rate, Mapping):
                continue
            units = rate.get("pricePerCategoryUnit")
            if not isinstance(units, list):
                continue
            for unit in units:
                if not isinstance(unit, Mapping):
                    continue
                unit_amount = _amount(unit.get("amount"))
                if unit_amount is not None:
                    return unit_amount * participants
        return None

    @staticmethod
    def _option_currency(item: Mapping[str, object], meta: Mapping[str, object]) -> str:
        value: object = item.get("currency") or item.get("currencyCode")
        rates = item.get("pricesByRate")
        if not value and isinstance(rates, list):
            for rate in rates:
                units = rate.get("pricePerCategoryUnit") if isinstance(rate, Mapping) else None
                if isinstance(units, list) and units and isinstance(units[0], Mapping):
                    amount_value = units[0].get("amount")
                    if isinstance(amount_value, Mapping):
                        value = amount_value.get("currency")
                        break
        return _currency(value or meta.get("currency") or "BRL")


class ManyChatHTTPTransport:
    """Read subscriber profiles and, only behind the outer gate, send text."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.manychat.com",
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("ManyChat API key is required")
        if not base_url.startswith("https://"):
            raise ValueError("ManyChat base URL must use HTTPS")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client or httpx.Client()

    def __repr__(self) -> str:
        return "ManyChatHTTPTransport(auth=bearer)"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def fetch_profile(self, subscriber_id: str) -> dict[str, object]:
        try:
            response = self._client.get(
                self._base_url + "/fb/subscriber/getInfo",
                headers=self._headers,
                params={"subscriber_id": subscriber_id},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderHTTPError("ManyChat profile request failed") from exc
        payload = _json_response(response, provider="ManyChat")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            raise ProviderHTTPError("ManyChat profile response fields mismatch")
        returned_id = _first(data, "id", "subscriber_id")
        if returned_id != subscriber_id:
            raise ProviderHTTPError("ManyChat subscriber identity mismatch")
        first_name = _first(data, "first_name") or ""
        last_name = _first(data, "last_name") or ""
        full_name = " ".join(part for part in (first_name, last_name) if part) or None
        return {
            "subscriber_id": subscriber_id,
            "full_name": full_name,
            "email": _first(data, "email"),
            "phone_e164": _first(data, "phone", "phone_e164"),
            "country_code": (_first(data, "country", "country_code") or "").upper() or None,
        }

    def send_text(
        self,
        *,
        subscriber_id: str,
        text: str,
        idempotency_key: str,
    ) -> ManyChatTransportResponse:
        body = {
            "subscriber_id": subscriber_id,
            "data": {
                "version": "v2",
                "content": {"messages": [{"type": "text", "text": text}]},
            },
        }
        try:
            response = self._client.post(
                self._base_url + "/fb/sending/sendContent",
                headers={**self._headers, "Idempotency-Key": idempotency_key},
                json=body,
                timeout=self._timeout,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ManyChatTransportNotCalled("ManyChat connection was not established") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("ManyChat delivery outcome is unknown") from exc
        payload = _json_response(response, provider="ManyChat")
        if not isinstance(payload, Mapping) or payload.get("status") not in {"success", "ok"}:
            raise RuntimeError("ManyChat did not confirm delivery")
        provider_id = _first(payload, "request_id", "message_id", "id")
        if provider_id is None:
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
            provider_id = "manychat:" + hashlib.sha256(canonical + idempotency_key.encode()).hexdigest()[:32]
        return ManyChatTransportResponse(provider_id)

    def set_custom_field(
        self,
        *,
        subscriber_id: str,
        field_id: int,
        field_value: str,
        idempotency_key: str,
    ) -> ManyChatTransportResponse:
        return self._effect_post(
            path="/fb/subscriber/setCustomField",
            body={
                "subscriber_id": subscriber_id,
                "field_id": field_id,
                "field_value": field_value,
            },
            idempotency_key=idempotency_key,
        )

    def set_custom_fields(
        self,
        *,
        subscriber_id: str,
        fields: list[dict[str, object]],
        idempotency_key: str,
    ) -> ManyChatTransportResponse:
        return self._effect_post(
            path="/fb/subscriber/setCustomFields",
            body={"subscriber_id": subscriber_id, "fields": fields},
            idempotency_key=idempotency_key,
        )

    def trigger_flow(
        self,
        *,
        subscriber_id: str,
        flow_ns: str,
        idempotency_key: str,
    ) -> ManyChatTransportResponse:
        return self._effect_post(
            path="/fb/sending/sendFlow",
            body={"subscriber_id": subscriber_id, "flow_ns": flow_ns},
            idempotency_key=idempotency_key,
        )

    def add_tag(
        self,
        *,
        subscriber_id: str,
        tag_id: int,
        idempotency_key: str,
    ) -> ManyChatTransportResponse:
        return self._effect_post(
            path="/fb/subscriber/addTag",
            body={"subscriber_id": subscriber_id, "tag_id": tag_id},
            idempotency_key=idempotency_key,
        )

    def _effect_post(
        self,
        *,
        path: str,
        body: dict[str, object],
        idempotency_key: str,
    ) -> ManyChatTransportResponse:
        try:
            response = self._client.post(
                self._base_url + path,
                headers={**self._headers, "Idempotency-Key": idempotency_key},
                json=body,
                timeout=self._timeout,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ManyChatTransportNotCalled(
                "ManyChat connection was not established"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("ManyChat effect outcome is unknown") from exc
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("ManyChat effect response is ambiguous") from exc
        if (
            not 200 <= response.status_code < 300
            or not isinstance(payload, Mapping)
            or payload.get("status") not in {"success", "ok"}
        ):
            raise RuntimeError("ManyChat did not confirm the requested effect")
        provider_id = _first(payload, "request_id", "message_id", "id")
        if provider_id is None:
            canonical = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
            provider_id = "manychat:" + hashlib.sha256(
                canonical + idempotency_key.encode()
            ).hexdigest()[:32]
        return ManyChatTransportResponse(provider_id)


class FileKnowledgeTransport:
    """Fresh, deterministic lookup over the standalone V2 Cérebro data file."""

    _STOPWORDS = frozenset(
        {"a", "as", "de", "do", "da", "e", "em", "o", "os", "para", "por", "que", "um", "uma"}
    )

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("knowledge path must be absolute")
        self._path = path

    def __repr__(self) -> str:
        return "FileKnowledgeTransport(source=v2-cerebro)"

    @staticmethod
    def _normalize(text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text)
        ascii_text = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(ascii_text.lower().split())

    def __call__(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation != "knowledge":
            raise ProviderHTTPError("unsupported knowledge read operation")
        query = _text(payload.get("query"))
        if query is None:
            raise ProviderHTTPError("knowledge query is required")
        try:
            loaded = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ProviderHTTPError("standalone knowledge source is unavailable") from exc
        raw_entries = loaded.get("entries") if isinstance(loaded, Mapping) else None
        if not isinstance(raw_entries, list):
            raise ProviderHTTPError("standalone knowledge source schema mismatch")
        normalized_query = self._normalize(query)
        query_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", normalized_query)
            if len(token) > 1 and token not in self._STOPWORDS
        }
        scored: list[tuple[int, str, str]] = []
        for item in raw_entries:
            if not isinstance(item, Mapping):
                continue
            entry_id = _first(item, "id")
            question = _first(item, "question")
            answer = _first(item, "answer")
            if not entry_id or not question or not answer:
                continue
            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
            haystack = self._normalize(
                " ".join(
                    (
                        _first(item, "topic") or "geral",
                        *(str(tag) for tag in tags),
                        question,
                        answer,
                    )
                )
            )
            haystack_tokens = set(re.findall(r"[a-z0-9]+", haystack))
            score = len(query_tokens & haystack_tokens) * 12
            score += int(
                SequenceMatcher(
                    None,
                    normalized_query,
                    self._normalize(question),
                ).ratio()
                * 35
            )
            if normalized_query in haystack:
                score += 45
            if score >= 12:
                scored.append((score, entry_id, answer))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top = scored[:3]
        if not top:
            return {
                "answer": "Não há fato autenticado no Cérebro para esta pergunta.",
                "sources": [],
            }
        return {
            "answer": "\n\n".join(item[2][:1600] for item in top),
            "sources": [item[1] for item in top],
        }


__all__ = [
    "BokunHTTPTransport",
    "CloudbedsHTTPTransport",
    "FileKnowledgeTransport",
    "ManyChatHTTPTransport",
    "ProviderHTTPError",
]
