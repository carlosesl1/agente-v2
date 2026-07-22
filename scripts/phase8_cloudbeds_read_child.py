#!/usr/bin/env python3
"""Ephemeral allowlisted Cloudbeds read child for the Phase 8 sandbox.

This file is sent to the existing Chapada container as the ``python -c`` source.
It exposes exactly one provider operation and emits only a closed public DTO.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re
import sys
from typing import Any

_RESULT_MARKER = b"PHASE8_CLOUDBEDS_RESULT\x00"
_REQUEST_FIELDS = {"kind", "arguments"}
_ARGUMENT_FIELDS = {"check_in", "check_out", "adults", "children"}


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


def _parse_request(payload: bytes) -> dict[str, object]:
    parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    if type(parsed) is not dict or set(parsed) != _REQUEST_FIELDS:
        raise ValueError("request fields mismatch")
    if parsed["kind"] != "lodging_availability":
        raise ValueError("request kind mismatch")
    arguments = parsed["arguments"]
    if type(arguments) is not dict or set(arguments) != _ARGUMENT_FIELDS:
        raise ValueError("argument fields mismatch")
    check_in = _date(arguments["check_in"])
    check_out = _date(arguments["check_out"])
    if date.fromisoformat(check_out) <= date.fromisoformat(check_in):
        raise ValueError("invalid stay interval")
    adults = _integer(arguments["adults"], minimum=1, maximum=20)
    children = _integer(arguments["children"], minimum=0, maximum=20)
    canonical = {
        "arguments": {
            "adults": adults,
            "check_in": check_in,
            "check_out": check_out,
            "children": children,
        },
        "kind": "lodging_availability",
    }
    if _canonical(canonical) != payload:
        raise ValueError("request is not canonical")
    return canonical["arguments"]


def _date(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("invalid date")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("non-canonical date")
    return value


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid integer")
    return value


def _text(value: object, *, maximum: int) -> str | None:
    if type(value) is not str:
        return None
    result = value.strip()
    if not result or len(result) > maximum or "\x00" in result:
        return None
    return result


def _positive_int(value: object) -> int | None:
    if type(value) is bool or value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if 1 <= result <= 10_000 else None


def _money(value: object) -> str | None:
    text = _text(str(value) if value is not None else None, maximum=40)
    if text is None:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount > Decimal("100000000"):
        return None
    return format(amount.quantize(Decimal("0.01")), "f")


def _sanitize_option(
    value: object,
    *,
    request: dict[str, object],
) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    room_name = _text(value.get("room_public_name"), maximum=200)
    if room_name is None:
        return None
    check_in = str(request["check_in"])
    check_out = str(request["check_out"])
    option: dict[str, object] = {
        "adults": int(request["adults"]),
        "check_in": check_in,
        "check_out": check_out,
        "children": int(request["children"]),
        "nights": (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days,
        "price_reliable": False,
        "room_public_name": room_name,
    }
    available_units = _positive_int(value.get("available_units"))
    if available_units is not None:
        option["available_units"] = available_units
    price_reliable = value.get("price_reliable") is True
    amount = _money(value.get("total_amount")) if price_reliable else None
    currency = _text(value.get("currency"), maximum=3)
    if amount is not None and currency is not None and re.fullmatch(r"[A-Z]{3}", currency):
        option["price_reliable"] = True
        option["total_amount"] = amount
        option["currency"] = currency
    return option


def _provider_error() -> dict[str, object]:
    return {
        "availability_confirmed": False,
        "options": [],
        "price_confirmed": False,
        "public_summary": "Não consegui confirmar disponibilidade de hospedagem com segurança agora.",
        "raw_provider_payload_returned": False,
        "schema": "phase8-sandbox-lodging-observation-v1",
        "status": "provider_error",
    }


def _sanitize_result(raw: object, *, request: dict[str, object]) -> dict[str, object]:
    if type(raw) is not str:
        return _provider_error()
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError):
        return _provider_error()
    if type(parsed) is not dict or parsed.get("raw_provider_payload_returned") is not False:
        return _provider_error()
    provider_status = parsed.get("status")
    if provider_status not in {"ok", "no_bookable_options"}:
        return _provider_error()
    options: list[dict[str, object]] = []
    raw_options = parsed.get("options")
    if type(raw_options) is list:
        for item in raw_options:
            option = _sanitize_option(item, request=request)
            if option is not None:
                options.append(option)
            if len(options) == 5:
                break
    if provider_status != "ok" or not options:
        return {
            "availability_confirmed": False,
            "options": [],
            "price_confirmed": False,
            "public_summary": (
                f"Não encontrei opções de hospedagem para {request['check_in']} a "
                f"{request['check_out']} com {request['adults']} adulto(s)."
            ),
            "raw_provider_payload_returned": False,
            "schema": "phase8-sandbox-lodging-observation-v1",
            "status": "no_bookable_options",
        }
    priced = any("total_amount" in option for option in options)
    return {
        "availability_confirmed": True,
        "options": options,
        "price_confirmed": priced,
        "public_summary": (
            f"Encontrei {len(options)} opção(ões) de hospedagem para "
            f"{request['check_in']} a {request['check_out']}."
        ),
        "raw_provider_payload_returned": False,
        "schema": "phase8-sandbox-lodging-observation-v1",
        "status": "ok",
    }


def main() -> int:
    try:
        request = _parse_request(sys.stdin.buffer.read())
    except Exception:
        return 2
    try:
        from tools.cloudbeds_v2_tools import cloudbeds_consultar_hospedagem_v2

        raw = cloudbeds_consultar_hospedagem_v2(
            check_in=str(request["check_in"]),
            check_out=str(request["check_out"]),
            adults=int(request["adults"]),
            children=int(request["children"]),
        )
        observation = _sanitize_result(raw, request=request)
    except Exception:
        observation = _provider_error()
    sys.stdout.buffer.write(_RESULT_MARKER + _canonical(observation))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
