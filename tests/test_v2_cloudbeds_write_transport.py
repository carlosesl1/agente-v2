from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from v2_adapters.provider_http import CloudbedsHTTPTransport, ProviderHTTPError


def _dispatch_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "v2-reservation-dispatch-v1",
        "command_id": "cmd:cloudbeds-write-001",
        "operation": "reserve_lodging",
        "offer": {
            "binding": "b" * 64,
            "private_binding": {
                "room_type_id": "room-type-1",
                "room_rate_id": "room-rate-1",
            },
            "offer_id": "offer:public-001",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "start_time": None,
            "party": {"adults": 2, "children": 0},
            "amount": "450.00",
            "currency": "BRL",
        },
        "customer": {
            "customer_ref": "profile:carlos",
            "full_name": "Carlos Eduardo",
            "email": "carlos@example.invalid",
            "phone_e164": "+5571999999999",
            "country_code": "BR",
        },
        "terms": {"payment_method": "stripe", "add_ons": []},
    }
    payload.update(overrides)
    return payload


def _transport(handler) -> CloudbedsHTTPTransport:
    return CloudbedsHTTPTransport(
        api_key="cloudbeds-secret",
        property_id="property-1",
        source_id="source-1",
        base_url="https://api.cloudbeds.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_cloudbeds_write_posts_closed_form_then_requires_matching_readback() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            form = parse_qs(request.content.decode())
            assert form["propertyID"] == ["property-1"]
            assert form["sourceID"] == ["source-1"]
            assert form["startDate"] == ["2026-08-10"]
            assert form["endDate"] == ["2026-08-12"]
            assert form["guestFirstName"] == ["Carlos"]
            assert form["guestLastName"] == ["Eduardo"]
            assert form["rooms"] == [
                '[{"roomTypeID":"room-type-1","quantity":1}]'
            ]
            assert form["adults"] == [
                '[{"roomTypeID":"room-type-1","quantity":2}]'
            ]
            assert form["children"] == [
                '[{"roomTypeID":"room-type-1","quantity":0}]'
            ]
            assert form["paymentMethod"] == ["credit_card"]
            assert request.headers["X-Idempotency-Key"] == "idem:cloudbeds-001"
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "reservationID": "reservation-123"},
            )
        assert request.url.path.endswith("/api/v1.1/getReservation")
        assert parse_qs(request.url.query.decode())["reservationID"] == [
            "reservation-123"
        ]
        return httpx.Response(
            200,
            request=request,
            json={"success": True, "data": {"reservationID": "reservation-123"}},
        )

    result = _transport(handler)(
        "reserve_lodging",
        _dispatch_payload(),
        idempotency_key="idem:cloudbeds-001",
    )

    assert result == {"status": "confirmed", "reservation_id": "reservation-123"}
    assert [request.method for request in seen] == ["POST", "GET"]
    assert all(
        request.headers["Authorization"] == "Bearer cloudbeds-secret"
        for request in seen
    )


def test_cloudbeds_write_rejects_closed_4xx_without_readback() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            422,
            request=request,
            json={"success": False, "message": "invalid reservation"},
        )

    result = _transport(handler)(
        "reserve_lodging",
        _dispatch_payload(),
        idempotency_key="idem:cloudbeds-rejected",
    )

    assert result == {"status": "rejected"}
    assert len(seen) == 1


def test_cloudbeds_write_readback_mismatch_is_unknown_not_confirmed() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "reservationID": "reservation-123"},
            )
        return httpx.Response(
            200,
            request=request,
            json={"success": True, "data": {"reservationID": "different"}},
        )

    with pytest.raises(ProviderHTTPError, match="read-back"):
        _transport(handler)(
            "reserve_lodging",
            _dispatch_payload(),
            idempotency_key="idem:cloudbeds-unknown",
        )
    assert len(seen) == 2


def test_cloudbeds_write_rejects_unknown_dispatch_fields_before_http() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500, request=request)

    with pytest.raises(ProviderHTTPError, match="fields"):
        _transport(handler)(
            "reserve_lodging",
            _dispatch_payload(unauthorized=True),
            idempotency_key="idem:cloudbeds-invalid",
        )
    assert seen == []


def test_cloudbeds_write_ambiguous_5xx_never_reports_rejected_or_confirmed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, json={"message": "unavailable"})

    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        _transport(handler)(
            "reserve_lodging",
            _dispatch_payload(),
            idempotency_key="idem:cloudbeds-503",
        )
