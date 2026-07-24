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
    """Provider read failed without exposing credentials or raw response data."""


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


class CloudbedsHTTPTransport:
    """Call Cloudbeds v1.3/v1.2 read endpoints and return the closed V2 DTO."""

    def __init__(
        self,
        *,
        api_key: str,
        property_id: str,
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

    def __call__(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "lodging":
            return self._lodging(payload)
        if operation == "room_description":
            return self._room_description(payload)
        raise ProviderHTTPError("unsupported Cloudbeds read operation")

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
    ) -> None:
        if not access_key or not secret_key:
            raise ValueError("Bókun read credentials are required")
        if not product_map or any(not key or not value for key, value in product_map.items()):
            raise ValueError("Bókun canonical product map is required")
        if not base_url.startswith("https://"):
            raise ValueError("Bókun base URL must use HTTPS")
        self._access_key = access_key
        self._secret_key = secret_key.encode()
        self._products = dict(product_map)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client or httpx.Client()
        self._timestamp = timestamp or (
            lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        )

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

    def __call__(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
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
        participants = int(payload.get("participants") or 0)
        if participants < 1:
            raise ProviderHTTPError("Bókun participants must be positive")
        selected = next(
            (
                item
                for item in _items(availability)
                if self._available(item, participants)
            ),
            None,
        )
        amount = self._participant_total(selected or {}, participants)
        if amount is None:
            amount = _first_amount(meta, "price", "amount", "totalAmount", "total")
        if amount is None:
            amount = Decimal("0")
        currency = self._option_currency(selected or {}, meta)
        return {
            "product_id": canonical_id,
            "bokun_product_id": provider_id,
            "product_public_name": self._title(meta) or canonical_id,
            "total_amount": f"{amount:.2f}",
            "currency": currency,
            "available": selected is not None,
        }

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
