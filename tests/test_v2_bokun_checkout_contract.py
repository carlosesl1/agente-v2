"""Checkout regression through real V2 transport; all HTTP is simulated."""
from __future__ import annotations

import json

import httpx
import pytest

from tests.test_v2_bokun_write_transport import (
    _checkout,
    _party_dispatch_payload,
    _transport,
)


def _run_checkout(checkout: dict[str, object]):
    seen: list[httpx.Request] = []
    payload = _party_dispatch_payload(2, 0)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            assert request.method == "POST"
            assert request.url.path.endswith("/activity")
            body = json.loads(request.content)
            assert body == {
                "activityId": "913372",
                "date": "2026-08-11",
                "startTimeId": "3210363",
                "rateId": "2375672",
                "pricingCategoryBookings": [
                    {"pricingCategoryId": "adult-1"},
                    {"pricingCategoryId": "adult-1"},
                ],
            }
            session = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            return httpx.Response(200, json={
                "uuid": session,
                "activityBookings": [{
                    "bookingId": "activity-booking-1",
                    "date": 1786406400000,
                    "activity": {"id": 913372},
                    "startTime": {"id": 3210363},
                    "rate": {"id": 2375672},
                    "pricingCategoryBookings": [
                        {"bookingId": "passenger-booking-1", "pricingCategoryId": "adult-1"},
                        {"bookingId": "passenger-booking-2", "pricingCategoryId": "adult-1"},
                    ],
                }],
            })
        if len(seen) == 2:
            assert request.method == "GET"
            assert request.url.path.startswith("/checkout.json/options/shopping-cart/")
            return httpx.Response(200, json=checkout)
        assert len(seen) == 3
        assert request.method == "POST"
        assert request.url.path == "/checkout.json/submit"
        assert request.headers["X-Idempotency-Key"] == "idem:nested-checkout:submit"
        body = json.loads(request.content)
        assert body["paymentMethod"] == "RESERVE_FOR_EXTERNAL_PAYMENT"
        assert body["sendNotificationToMainContact"] is False
        answers = body["shoppingCart"]["bookingAnswers"]["activityBookings"][0]["passengers"]
        assert [row["bookingId"] for row in answers] == [
            "passenger-booking-1", "passenger-booking-2",
        ]
        for index, row in enumerate(answers):
            details = {entry["questionId"]: entry["values"][0] for entry in row["passengerDetails"]}
            passenger = payload["customer"]["passengers"][index]
            assert details["dateOfBirth"] == passenger["birth_date"]
        return httpx.Response(200, json={"booking": {"bookingId": "booking-nested-2pax"}})

    result = _transport(handler)("book_activity", payload, idempotency_key="idem:nested-checkout")
    return result, seen


def _two_passenger_checkout():
    return _checkout(
        (("passenger-booking-1", "adult-1"), ("passenger-booking-2", "adult-1")),
        amount="200.00",
    )


def test_nested_option_binds_amount_and_two_passenger_submit_once() -> None:
    checkout = _two_passenger_checkout()
    checkout["options"].insert(0, {
        "formattedAmount": "999.00",
        "invoice": {"remainingAmount": "999.00"},
        "paymentMethods": {"allowedMethods": ["CUSTOMER_FULL_PAYMENT"]},
    })

    result, seen = _run_checkout(checkout)

    assert result == {"status": "confirmed", "booking_id": "booking-nested-2pax"}
    assert [request.method for request in seen] == ["POST", "GET", "POST"]


@pytest.mark.parametrize("top_level_decoy", [False, True])
def test_checkout_without_external_option_records_local_cause_without_submit(
    top_level_decoy: bool,
) -> None:
    checkout = _two_passenger_checkout()
    option = checkout["options"][0]
    option["paymentMethods"] = {"allowedMethods": ["CUSTOMER_FULL_PAYMENT"]}
    if top_level_decoy:
        option["allowedMethods"] = ["RESERVE_FOR_EXTERNAL_PAYMENT"]

    result, seen = _run_checkout(checkout)

    assert [request.method for request in seen] == ["POST", "GET"]
    assert result == {
        "status": "no_effect",
        "reason": "checkout_external_payment_unavailable",
    }


def test_nested_option_does_not_borrow_another_options_lower_amount() -> None:
    checkout = _two_passenger_checkout()
    selected = checkout["options"][0]
    selected["formattedAmount"] = "250.00"
    selected["invoice"] = {"remainingAmount": "250.00"}
    checkout["options"].insert(0, {
        "formattedAmount": "200.00",
        "invoice": {"remainingAmount": "200.00"},
        "paymentMethods": {"allowedMethods": ["CUSTOMER_FULL_PAYMENT"]},
    })

    result, seen = _run_checkout(checkout)

    assert result == {"status": "no_effect"}
    assert [request.method for request in seen] == ["POST", "GET"]
