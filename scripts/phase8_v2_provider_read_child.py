#!/usr/bin/env python3
"""Ephemeral Cloudbeds/Bókun read child for the effect-denied Phase 8 sandbox."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import sys

_RESULT_MARKER = b"PHASE8_V2_READ_RESULT\x00"
_EFFECT_GATES = (
    "V2_ENABLE_CLOUDBEDS_WRITES",
    "V2_ENABLE_BOKUN_WRITES",
    "V2_ENABLE_STRIPE_LINKS",
    "V2_ENABLE_MANYCHAT_DELIVERY",
)
_PRODUCT_ID_RE = re.compile(r"^product:[a-z0-9][a-z0-9._-]{0,127}$")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _text(value: object, *, maximum: int) -> str | None:
    if type(value) is not str:
        return None
    result = value.strip()
    if not result or result != value or len(result) > maximum or "\x00" in result:
        return None
    return result


def _iso_date(value: object) -> str:
    text = _text(value, maximum=10)
    if text is None:
        raise ValueError("invalid date")
    parsed = date.fromisoformat(text)
    if parsed.isoformat() != text:
        raise ValueError("date is not canonical")
    return text


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid integer")
    return value


def _money(value: object) -> str | None:
    text = _text(value, maximum=40)
    if text is None:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("100000000"):
        return None
    normalized = format(amount.quantize(Decimal("0.01")), "f")
    return normalized if normalized == text else None


def _validate_environment(source: Mapping[str, str]) -> None:
    if source.get("V2_RUNTIME_MODE") != "dark_read_only":
        raise ValueError("V2 read child requires dark_read_only mode")
    for name in _EFFECT_GATES:
        if source.get(name, "false").strip().casefold() not in {"false", "0"}:
            raise ValueError("V2 read child requires every effect gate closed")


def _parse_request(payload: bytes) -> dict[str, object]:
    parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    if type(parsed) is not dict or set(parsed) != {"kind", "arguments"}:
        raise ValueError("request fields mismatch")
    kind = parsed["kind"]
    arguments = parsed["arguments"]
    if type(arguments) is not dict:
        raise ValueError("request arguments must be an object")
    if kind == "lodging_availability":
        if set(arguments) != {"check_in", "check_out", "adults", "children"}:
            raise ValueError("lodging argument fields mismatch")
        check_in = _iso_date(arguments["check_in"])
        check_out = _iso_date(arguments["check_out"])
        if date.fromisoformat(check_out) <= date.fromisoformat(check_in):
            raise ValueError("invalid stay interval")
        closed_arguments = {
            "adults": _integer(arguments["adults"], minimum=1, maximum=20),
            "check_in": check_in,
            "check_out": check_out,
            "children": _integer(arguments["children"], minimum=0, maximum=20),
        }
    elif kind == "activity_availability":
        if set(arguments) != {"product_id", "activity_date", "participants"}:
            raise ValueError("activity argument fields mismatch")
        product_id = _text(arguments["product_id"], maximum=136)
        if product_id is None or _PRODUCT_ID_RE.fullmatch(product_id) is None:
            raise ValueError("activity product ID is not canonical")
        closed_arguments = {
            "activity_date": _iso_date(arguments["activity_date"]),
            "participants": _integer(arguments["participants"], minimum=1, maximum=20),
            "product_id": product_id,
        }
    else:
        raise ValueError("request kind is outside the closed set")
    closed = {"arguments": closed_arguments, "kind": kind}
    if _canonical(closed) != payload:
        raise ValueError("request is not canonical")
    return closed


def _currency(value: object) -> str | None:
    text = _text(value, maximum=3)
    return text if text is not None and re.fullmatch(r"[A-Z]{3}", text) else None


def _provider_error(request: dict[str, object]) -> dict[str, object]:
    arguments = request["arguments"]
    if request["kind"] == "activity_availability":
        return {
            "activity_date": arguments["activity_date"],
            "availability_confirmed": False,
            "currency": None,
            "participants": arguments["participants"],
            "price_confirmed": False,
            "product_public_name": "Passeio",
            "public_summary": "Não consegui confirmar o passeio com segurança agora.",
            "raw_provider_payload_returned": False,
            "schema": "phase8-sandbox-activity-observation-v1",
            "status": "provider_error",
            "total_amount": None,
        }
    return {
        "availability_confirmed": False,
        "options": [],
        "price_confirmed": False,
        "public_summary": "Não consegui confirmar a hospedagem com segurança agora.",
        "raw_provider_payload_returned": False,
        "schema": "phase8-sandbox-lodging-observation-v1",
        "status": "provider_error",
    }


def _sanitize_lodging_option(
    value: object,
    *,
    arguments: dict[str, object],
) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    name = _text(value.get("room_public_name"), maximum=200)
    if name is None:
        return None
    check_in = str(arguments["check_in"])
    check_out = str(arguments["check_out"])
    option: dict[str, object] = {
        "adults": arguments["adults"],
        "check_in": check_in,
        "check_out": check_out,
        "children": arguments["children"],
        "nights": (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days,
        "price_reliable": False,
        "room_public_name": name,
    }
    units = value.get("available_units")
    if type(units) is int and 1 <= units <= 10_000:
        option["available_units"] = units
    amount = _money(value.get("total_amount"))
    currency = _currency(value.get("currency"))
    if amount is not None and currency is not None:
        option["price_reliable"] = True
        option["total_amount"] = amount
        option["currency"] = currency
    return option


def _sanitize_result(
    raw: object,
    *,
    request: dict[str, object],
) -> dict[str, object]:
    if type(raw) is not dict:
        return _provider_error(request)
    arguments = request["arguments"]
    if request["kind"] == "activity_availability":
        name = _text(raw.get("product_public_name"), maximum=200) or "Passeio"
        available = raw.get("available") is True
        amount = _money(raw.get("total_amount")) if available else None
        currency = _currency(raw.get("currency")) if amount is not None else None
        priced = amount is not None and currency is not None
        return {
            "activity_date": arguments["activity_date"],
            "availability_confirmed": available,
            "currency": currency if priced else None,
            "participants": arguments["participants"],
            "price_confirmed": priced,
            "product_public_name": name,
            "public_summary": (
                f"Encontrei disponibilidade para {name} em {arguments['activity_date']}."
                if available
                else f"Não encontrei disponibilidade para {name} em {arguments['activity_date']}."
            ),
            "raw_provider_payload_returned": False,
            "schema": "phase8-sandbox-activity-observation-v1",
            "status": "ok" if available else "no_bookable_options",
            "total_amount": amount if priced else None,
        }
    raw_options = raw.get("options")
    options: list[dict[str, object]] = []
    if type(raw_options) is list:
        for item in raw_options:
            option = _sanitize_lodging_option(item, arguments=arguments)
            if option is not None:
                options.append(option)
            if len(options) == 5:
                break
    if not options:
        return {
            "availability_confirmed": False,
            "options": [],
            "price_confirmed": False,
            "public_summary": (
                f"Não encontrei hospedagem para {arguments['check_in']} a "
                f"{arguments['check_out']}."
            ),
            "raw_provider_payload_returned": False,
            "schema": "phase8-sandbox-lodging-observation-v1",
            "status": "no_bookable_options",
        }
    return {
        "availability_confirmed": True,
        "options": options,
        "price_confirmed": any("total_amount" in option for option in options),
        "public_summary": (
            f"Encontrei {len(options)} opção(ões) de hospedagem para "
            f"{arguments['check_in']} a {arguments['check_out']}."
        ),
        "raw_provider_payload_returned": False,
        "schema": "phase8-sandbox-lodging-observation-v1",
        "status": "ok",
    }


def _product_map(raw: str) -> dict[str, str]:
    parsed = json.loads(raw, object_pairs_hook=_unique_object)
    if type(parsed) is not dict or not parsed:
        raise ValueError("Bókun product map is unavailable")
    result: dict[str, str] = {}
    for key, value in parsed.items():
        if (
            type(key) is not str
            or _PRODUCT_ID_RE.fullmatch(key) is None
            or type(value) is not str
            or not value
            or value != value.strip()
        ):
            raise ValueError("Bókun product map is invalid")
        result[key] = value
    return result


def _provider_read(request: dict[str, object]) -> dict[str, object]:
    from v2_adapters.provider_http import BokunHTTPTransport, CloudbedsHTTPTransport

    arguments = request["arguments"]
    if request["kind"] == "lodging_availability":
        transport = CloudbedsHTTPTransport(
            api_key=os.environ["V2_CLOUDBEDS_API_KEY"],
            property_id=os.environ["V2_CLOUDBEDS_PROPERTY_ID"],
            base_url=os.environ.get("V2_CLOUDBEDS_BASE_URL", "https://api.cloudbeds.com"),
            timeout_seconds=15.0,
        )
        return transport("lodging", dict(arguments))
    products = _product_map(os.environ["V2_BOKUN_PRODUCT_MAP_JSON"])
    if arguments["product_id"] not in products:
        raise ValueError("requested Bókun product ID is outside the configured catalog")
    transport = BokunHTTPTransport(
        access_key=os.environ["V2_BOKUN_ACCESS_KEY"],
        secret_key=os.environ["V2_BOKUN_SECRET_KEY"],
        product_map=products,
        base_url=os.environ.get("V2_BOKUN_BASE_URL", "https://api.bokun.io"),
        timeout_seconds=15.0,
    )
    return transport("activity", dict(arguments))


def _request_hash(request: dict[str, object]) -> str:
    return hashlib.sha256(
        b"phase8-v2-read-request-v1\x00" + _canonical(request)
    ).hexdigest()


def main() -> int:
    try:
        _validate_environment(os.environ)
        request = _parse_request(sys.stdin.buffer.read())
    except Exception:
        return 2
    try:
        raw = _provider_read(request)
        observation = _sanitize_result(raw, request=request)
    except Exception:
        observation = _provider_error(request)
    result = {
        "observation": observation,
        "request_hash": _request_hash(request),
    }
    sys.stdout.buffer.write(_RESULT_MARKER + _canonical(result))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
