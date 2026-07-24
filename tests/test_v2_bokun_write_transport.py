from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from v2_adapters.provider_http import BokunHTTPTransport, ProviderHTTPError


def _dispatch_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "v2-reservation-dispatch-v1",
        "command_id": "cmd:bokun-write-001",
        "operation": "book_activity",
        "offer": {
            "binding": "b" * 64,
            "private_binding": {
                "bokun_product_id": "913372",
                "start_time_id": "3210363",
                "rate_id": "2375672",
                "pricing_category_id": "857489",
            },
            "offer_id": "offer:public-bokun-001",
            "start_date": "2026-08-11",
            "end_date": None,
            "start_time": "07:30",
            "party": {"adults": 1, "children": 0},
            "amount": "300.00",
            "currency": "BRL",
        },
        "customer": {
            "customer_ref": "profile:carlos",
            "full_name": "Carlos Eduardo",
            "email": "carlos@example.invalid",
            "phone_e164": "+5571999999999",
            "country_code": "BR",
            "birth_date": "1990-01-02",
            "gender": "m",
        },
        "terms": {"payment_method": "stripe", "add_ons": []},
    }
    payload.update(overrides)
    return payload


def _checkout() -> dict[str, object]:
    main = [
        {"questionId": name, "required": True}
        for name in (
            "firstName",
            "lastName",
            "email",
            "phoneNumber",
            "nationality",
            "language",
            "dateOfBirth",
            "gender",
        )
    ]
    passenger = [
        {"questionId": name, "required": True}
        for name in (
            "firstName",
            "lastName",
            "nationality",
            "dateOfBirth",
            "gender",
        )
    ]
    return {
        "options": [
            {
                "formattedAmount": "R$ 300,00",
                "invoice": {"remainingAmountAsText": "R$ 300,00"},
            }
        ],
        "questions": {
            "mainContactDetails": main,
            "activityBookings": [
                {
                    "passengers": [
                        {"passengerDetails": passenger, "questions": []}
                    ]
                }
            ],
        },
    }


def _transport(handler) -> BokunHTTPTransport:
    return BokunHTTPTransport(
        access_key="bokun-access",
        secret_key="bokun-secret",
        product_map={"product:buracao": "913372"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-24 12:00:00",
    )


def test_bokun_write_cart_checkout_submit_and_readback_are_one_fenced_call() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        call = len(seen)
        if call == 1:
            assert request.method == "POST"
            assert request.url.path.startswith("/shopping-cart.json/session/")
            body = json.loads(request.content)
            assert body == {
                "activityId": "913372",
                "date": "2026-08-11",
                "startTimeId": "3210363",
                "rateId": "2375672",
                "pricingCategoryBookings": [
                    {"pricingCategoryId": "857489"}
                ],
            }
            assert request.headers["X-Idempotency-Key"] == "idem:bokun-001:cart"
            session = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            return httpx.Response(
                200,
                request=request,
                json={
                    "uuid": session,
                    "activityBookings": [
                        {
                            "bookingId": "activity-booking-1",
                            "activityId": "913372",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": "passenger-booking-1",
                                    "pricingCategoryId": "857489",
                                }
                            ],
                        }
                    ],
                },
            )
        if call == 2:
            assert request.method == "GET"
            assert request.url.path.startswith("/checkout.json/options/shopping-cart/")
            return httpx.Response(200, request=request, json=_checkout())
        if call == 3:
            assert request.method == "POST"
            assert request.url.path == "/checkout.json/submit"
            assert request.headers["X-Idempotency-Key"] == "idem:bokun-001:submit"
            body = json.loads(request.content)
            assert body["paymentMethod"] == "RESERVE_FOR_EXTERNAL_PAYMENT"
            assert body["sendNotificationToMainContact"] is False
            contact = {
                item["questionId"]: item["values"][0]
                for item in body["shoppingCart"]["bookingAnswers"][
                    "mainContactDetails"
                ]
            }
            assert contact == {
                "firstName": "Carlos",
                "lastName": "Eduardo",
                "email": "carlos@example.invalid",
                "phoneNumber": "+5571999999999",
                "nationality": "BR",
                "language": "pt",
                "dateOfBirth": "1990-01-02",
                "gender": "m",
            }
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "request-envelope-123",
                    "booking": {
                        "bookingId": "booking-123",
                        "confirmationCode": "BK-123",
                        "status": "PENDING",
                    }
                },
            )
        assert call == 4
        assert request.method == "GET"
        assert request.url.path == "/booking.json/booking/booking-123"
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "readback-envelope-123",
                "booking": {
                    "bookingId": "booking-123",
                    "confirmationCode": "BK-123",
                    "status": "PENDING",
                }
            },
        )

    result = _transport(handler)(
        "book_activity",
        _dispatch_payload(),
        idempotency_key="idem:bokun-001",
    )

    assert result == {"status": "confirmed", "booking_id": "booking-123"}
    assert [request.method for request in seen] == ["POST", "GET", "POST", "GET"]
    assert all(request.headers["X-Bokun-AccessKey"] == "bokun-access" for request in seen)
    assert all(request.headers["X-Bokun-Signature"] for request in seen)


def test_bokun_cart_uncertainty_stops_the_sequence() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        _transport(handler)(
            "book_activity",
            _dispatch_payload(),
            idempotency_key="idem:bokun-cart-timeout",
        )
    assert len(seen) == 1


def test_bokun_checkout_uncertainty_never_submits() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            session = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            return httpx.Response(
                200,
                request=request,
                json={
                    "uuid": session,
                    "activityBookings": [
                        {
                            "bookingId": "activity-booking-1",
                            "activityId": "913372",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": "passenger-booking-1",
                                    "pricingCategoryId": "857489",
                                }
                            ],
                        }
                    ],
                },
            )
        return httpx.Response(503, request=request, json={"message": "unavailable"})

    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        _transport(handler)(
            "book_activity",
            _dispatch_payload(),
            idempotency_key="idem:bokun-checkout-503",
        )
    assert len(seen) == 2


def test_bokun_submit_rejection_is_no_booking_and_never_read_back() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            session = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            return httpx.Response(
                200,
                request=request,
                json={
                    "uuid": session,
                    "activityBookings": [
                        {
                            "bookingId": "activity-booking-1",
                            "activityId": "913372",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": "passenger-booking-1",
                                    "pricingCategoryId": "857489",
                                }
                            ],
                        }
                    ],
                },
            )
        if len(seen) == 2:
            return httpx.Response(200, request=request, json=_checkout())
        return httpx.Response(422, request=request, json={"success": False})

    result = _transport(handler)(
        "book_activity",
        _dispatch_payload(),
        idempotency_key="idem:bokun-submit-rejected",
    )

    assert result == {"status": "rejected"}
    assert len(seen) == 3


def test_bokun_multi_passenger_fails_before_cart_instead_of_inventing_people() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500, request=request)

    payload = _dispatch_payload()
    payload["offer"] = {
        **payload["offer"],
        "party": {"adults": 2, "children": 0},
    }

    with pytest.raises(ProviderHTTPError, match="one passenger"):
        _transport(handler)(
            "book_activity",
            payload,
            idempotency_key="idem:bokun-multi",
        )
    assert seen == []


def test_bokun_private_execution_binding_is_required_before_http() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500, request=request)

    payload = _dispatch_payload()
    payload["offer"] = {
        **payload["offer"],
        "private_binding": {"bokun_product_id": "913372"},
    }

    with pytest.raises(ProviderHTTPError, match="private binding"):
        _transport(handler)(
            "book_activity",
            payload,
            idempotency_key="idem:bokun-private",
        )
    assert seen == []
