from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from v2_adapters.provider_http import BokunHTTPTransport, ProviderHTTPError


def _dispatch_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "v2-reservation-dispatch-v2",
        "command_id": "cmd:bokun-write-001",
        "operation": "book_activity",
        "offer": {
            "binding": "b" * 64,
            "private_binding": {
                "bokun_product_id": "913372",
                "start_time_id": "3210363",
                "rate_id": "2375672",
                "adult_pricing_category_id": "857489",
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
            "phone_e164": "+55" + "759" + "999" + "9999",
            "country_code": "BR",
            "passengers": [
                {
                    "position": 1,
                    "participant_type": "adult",
                    "full_name": "Carlos Eduardo",
                    "birth_date": "1990-01-02",
                    "gender": "m",
                    "country_code": "BR",
                }
            ],
        },
        "terms": {"payment_method": "stripe", "add_ons": []},
    }
    payload.update(overrides)
    return payload


def _checkout(
    passenger_bookings: tuple[tuple[str, str], ...] = (
        ("passenger-booking-1", "857489"),
    ),
    *,
    amount: str = "300.00",
    activity_booking: str = "activity-booking-1",
) -> dict[str, object]:
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
                "formattedAmount": amount,
                "invoice": {"remainingAmount": amount},
            }
        ],
        "questions": {
            "mainContactDetails": main,
            "activityBookings": [
                {
                    "bookingId": activity_booking,
                    "activityId": "913372",
                    "passengers": [
                        {
                            "bookingId": booking_id,
                            "pricingCategoryId": category_id,
                            "passengerDetails": passenger,
                            "questions": [],
                        }
                        for booking_id, category_id in passenger_bookings
                    ]
                }
            ],
        },
    }


def _group_dispatch_payload() -> dict[str, object]:
    payload = _dispatch_payload()
    offer = payload["offer"]
    customer = payload["customer"]
    assert isinstance(offer, dict) and isinstance(customer, dict)
    private = offer["private_binding"]
    assert isinstance(private, dict)
    offer["private_binding"] = {
        **private,
        "adult_pricing_category_id": "adult-1",
        "child_pricing_category_id": "child-1",
    }
    offer["party"] = {"adults": 2, "children": 1}
    offer["amount"] = "750.00"
    customer["full_name"] = "Pessoa Grupo Um"
    customer["passengers"] = [
        {
            "position": 1,
            "participant_type": "adult",
            "full_name": "Pessoa Grupo Um",
            "birth_date": "1990-01-02",
            "gender": "f",
            "country_code": "BR",
        },
        {
            "position": 2,
            "participant_type": "adult",
            "full_name": "Pessoa Grupo Dois",
            "birth_date": "1992-03-04",
            "gender": "m",
            "country_code": "BR",
        },
        {
            "position": 3,
            "participant_type": "child",
            "full_name": "Pessoa Grupo Três",
            "birth_date": "2016-05-06",
            "gender": "f",
            "country_code": "BR",
        },
    ]
    return payload


def _party_dispatch_payload(adults: int, children: int) -> dict[str, object]:
    payload = _dispatch_payload()
    offer = payload["offer"]
    customer = payload["customer"]
    assert isinstance(offer, dict) and isinstance(customer, dict)
    private = offer["private_binding"]
    assert isinstance(private, dict)
    offer["party"] = {"adults": adults, "children": children}
    offer["amount"] = f"{(Decimal('100') * adults + Decimal('50') * children):.2f}"
    private["adult_pricing_category_id"] = "adult-1"
    if children:
        private["child_pricing_category_id"] = "child-1"
    else:
        private.pop("child_pricing_category_id", None)
    passengers = []
    for position in range(1, adults + children + 1):
        is_adult = position <= adults
        passengers.append(
            {
                "position": position,
                "participant_type": "adult" if is_adult else "child",
                "full_name": f"Pessoa Sintética {position}",
                "birth_date": (
                    f"{1980 + position:04d}-01-02"
                    if is_adult
                    else f"{2010 + position:04d}-01-02"
                ),
                "gender": "f" if position % 2 else "m",
                "country_code": "BR",
            }
        )
    customer["full_name"] = passengers[0]["full_name"]
    customer["passengers"] = passengers
    return payload


def _transport(handler) -> BokunHTTPTransport:
    return BokunHTTPTransport(
        access_key="bokun-access",
        secret_key="bokun-secret",
        product_map={"product:buracao": "913372"},
        base_url="https://api.bokun.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        timestamp=lambda: "2026-07-24 12:00:00",
    )


def test_submit_answers_supported_checkout_fields_even_when_provider_marks_them_optional() -> None:
    checkout = _checkout()
    questions = checkout["questions"]
    assert isinstance(questions, dict)
    main = questions["mainContactDetails"]
    activities = questions["activityBookings"]
    assert isinstance(main, list) and isinstance(activities, list)
    for item in main:
        assert isinstance(item, dict)
        item["required"] = False
    passenger = activities[0]["passengers"][0]["passengerDetails"]
    for item in passenger:
        item["required"] = False

    body = BokunHTTPTransport._submit_body_v2(
        checkout,
        session_id="session:optional-fields",
        activity_booking="activity-booking-1",
        product_id="913372",
        main_contact={
            "firstName": "Carlos",
            "lastName": "Eduardo",
            "email": "carlos@example.invalid",
            "phoneNumber": "+55" + "759" + "999" + "9999",
            "nationality": "BR",
            "language": "pt",
            "dateOfBirth": "1990-01-02",
            "gender": "m",
        },
        passengers=(
            {
                "booking_id": "passenger-booking-1",
                "category_id": "857489",
                "firstName": "Carlos",
                "lastName": "Eduardo",
                "nationality": "BR",
                "dateOfBirth": "1990-01-02",
                "gender": "m",
            },
        ),
        expected_amount=Decimal("300.00"),
    )

    main_answers = {
        item["questionId"]: item["values"][0]
        for item in body["shoppingCart"]["bookingAnswers"]["mainContactDetails"]
    }
    passenger_answers = {
        item["questionId"]: item["values"][0]
        for item in body["shoppingCart"]["bookingAnswers"]["activityBookings"][0]["passengers"][0]["passengerDetails"]
    }
    assert main_answers["email"] == "carlos@example.invalid"
    assert main_answers["phoneNumber"] == "+55" + "759" + "999" + "9999"
    assert main_answers["dateOfBirth"] == "1990-01-02"
    assert main_answers["gender"] == "m"
    assert passenger_answers["dateOfBirth"] == "1990-01-02"
    assert passenger_answers["gender"] == "m"


def test_bokun_v2_submit_booking_id_confirms_without_readback() -> None:
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
                            "date": 1786406400000,
                            "activity": {"id": 913372},
                            "startTime": {"id": 3210363},
                            "rate": {"id": 2375672},
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
                "phoneNumber": "+55" + "759" + "999" + "9999",
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
        raise AssertionError("booking ID confirmation must not depend on read-back")

    result = _transport(handler)(
        "book_activity",
        _dispatch_payload(),
        idempotency_key="idem:bokun-001",
    )

    assert result == {"status": "confirmed", "booking_id": "booking-123"}
    assert [request.method for request in seen] == ["POST", "GET", "POST"]
    assert all(request.headers["X-Bokun-AccessKey"] == "bokun-access" for request in seen)
    assert all(request.headers["X-Bokun-Signature"] for request in seen)


def test_bokun_v2_uses_authenticated_foreign_phone_for_english_locale() -> None:
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
                            "date": 1786406400000,
                            "activity": {"id": 913372},
                            "startTime": {"id": 3210363},
                            "rate": {"id": 2375672},
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
        body = json.loads(request.content)
        contact = {
            item["questionId"]: item["values"][0]
            for item in body["shoppingCart"]["bookingAnswers"][
                "mainContactDetails"
            ]
        }
        assert contact["language"] == "en"
        return httpx.Response(
            200,
            request=request,
            json={"booking": {"bookingId": "booking-en-123"}},
        )

    payload = _dispatch_payload()
    customer = payload["customer"]
    assert isinstance(customer, dict)
    customer["phone_e164"] = "+12025550123"
    customer["country_code"] = "US"
    passengers = customer["passengers"]
    assert isinstance(passengers, list) and isinstance(passengers[0], dict)
    passengers[0]["country_code"] = "US"

    result = _transport(handler)(
        "book_activity",
        payload,
        idempotency_key="idem:bokun-en-001",
    )

    assert result == {"status": "confirmed", "booking_id": "booking-en-123"}
    assert [request.url.params.get("lang") for request in seen] == ["en", "en", "en"]


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


def test_bokun_cart_binding_divergence_is_called_no_effect() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
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
                                "bookingId": "wrong-passenger",
                                "pricingCategoryId": "wrong-category",
                            }
                        ],
                    }
                ],
            },
        )

    result = _transport(handler)(
        "book_activity",
        _dispatch_payload(),
        idempotency_key="idem:bokun-cart-diverged",
    )

    assert result == {"status": "no_effect"}
    assert len(seen) == 1


def test_bokun_checkout_binding_divergence_is_called_no_effect() -> None:
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
        return httpx.Response(
            200,
            request=request,
            json=_checkout(
                (("different-passenger", "857489"),),
            ),
        )

    result = _transport(handler)(
        "book_activity",
        _dispatch_payload(),
        idempotency_key="idem:bokun-checkout-diverged",
    )

    assert result == {"status": "no_effect"}
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


def test_bokun_submit_conflict_is_ambiguous_and_never_read_back() -> None:
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
        return httpx.Response(409, request=request, json={"success": False})

    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        _transport(handler)(
            "book_activity",
            _dispatch_payload(),
            idempotency_key="idem:bokun-submit-conflict",
        )

    assert len(seen) == 3


@pytest.mark.parametrize(
    ("status", "submit_payload"),
    (
        (409, {"booking": {"bookingId": "booking-on-non-success"}}),
        (422, {"booking": {"bookingId": "booking-on-non-success"}}),
        (
            200,
            {
                "success": False,
                "booking": {"bookingId": "booking-on-explicit-failure"},
            },
        ),
        (
            200,
            {
                "booking": {
                    "bookingId": "booking-on-nested-explicit-failure",
                    "success": False,
                }
            },
        ),
    ),
)
def test_bokun_submit_contradictory_booking_id_evidence_is_ambiguous(
    status: int,
    submit_payload: dict[str, object],
) -> None:
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
        if len(seen) == 3:
            return httpx.Response(
                status,
                request=request,
                json=submit_payload,
            )
        raise AssertionError("ambiguous submit must not be read back or retried")

    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        _transport(handler)(
            "book_activity",
            _dispatch_payload(),
            idempotency_key=f"idem:bokun-contradictory-id:{status}",
        )

    assert len(seen) == 3


def test_bokun_multi_passenger_cart_checkout_submit_by_booking_id() -> None:
    seen: list[httpx.Request] = []
    category_rows = (
        ("adult-booking-1", "adult-1"),
        ("adult-booking-2", "adult-1"),
        ("child-booking-1", "child-1"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        call = len(seen)
        if call == 1:
            body = json.loads(request.content)
            assert body["pricingCategoryBookings"] == [
                {"pricingCategoryId": "adult-1"},
                {"pricingCategoryId": "adult-1"},
                {"pricingCategoryId": "child-1"},
            ]
            session = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            return httpx.Response(
                200,
                request=request,
                json={
                    "uuid": session,
                    "activityBookings": [
                        {
                            "bookingId": "activity-group-1",
                            "activityId": "913372",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": booking_id,
                                    "pricingCategoryId": category_id,
                                }
                                for booking_id, category_id in category_rows
                            ],
                        }
                    ],
                },
            )
        if call == 2:
            return httpx.Response(
                200,
                request=request,
                json=_checkout(
                    category_rows,
                    amount="750.00",
                    activity_booking="activity-group-1",
                ),
            )
        if call == 3:
            body = json.loads(request.content)
            submitted = body["shoppingCart"]["bookingAnswers"][
                "activityBookings"
            ][0]["passengers"]
            assert [item["bookingId"] for item in submitted] == [
                item[0] for item in category_rows
            ]
            assert [item["pricingCategoryId"] for item in submitted] == [
                item[1] for item in category_rows
            ]
            return httpx.Response(
                200,
                request=request,
                json={"booking": {"bookingId": "booking-group-123"}},
            )
        raise AssertionError("booking ID confirmation must not depend on read-back")

    result = _transport(handler)(
        "book_activity",
        _group_dispatch_payload(),
        idempotency_key="idem:bokun-multi",
    )

    assert result == {"status": "confirmed", "booking_id": "booking-group-123"}
    assert len(seen) == 3


@pytest.mark.parametrize(
    ("adults", "children"),
    ((1, 0), (2, 0), (1, 1), (4, 2)),
)
def test_supported_parties_preserve_count_end_to_end(
    adults: int,
    children: int,
) -> None:
    payload = _party_dispatch_payload(adults, children)
    seen: list[httpx.Request] = []
    expected_categories = ["adult-1"] * adults + ["child-1"] * children
    offer = payload["offer"]
    assert isinstance(offer, dict)
    amount = offer["amount"]
    assert isinstance(amount, str)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        call = len(seen)
        rows = tuple(
            (f"passenger-{position}", category_id)
            for position, category_id in enumerate(expected_categories, 1)
        )
        if call == 1:
            body = json.loads(request.content)
            assert [
                item["pricingCategoryId"]
                for item in body["pricingCategoryBookings"]
            ] == expected_categories
            session = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            return httpx.Response(
                200,
                request=request,
                json={
                    "uuid": session,
                    "activityBookings": [
                        {
                            "bookingId": "activity-matrix",
                            "activityId": "913372",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": booking_id,
                                    "pricingCategoryId": category_id,
                                }
                                for booking_id, category_id in rows
                            ],
                        }
                    ],
                },
            )
        if call == 2:
            return httpx.Response(
                200,
                request=request,
                json=_checkout(
                    rows,
                    amount=amount,
                    activity_booking="activity-matrix",
                ),
            )
        if call == 3:
            body = json.loads(request.content)
            details = body["shoppingCart"]["bookingAnswers"][
                "activityBookings"
            ][0]["passengers"]
            assert len(details) == adults + children
            assert [item["bookingId"] for item in details] == [
                f"passenger-{position}"
                for position in range(1, adults + children + 1)
            ]
            return httpx.Response(
                200,
                request=request,
                json={"booking": {"bookingId": "booking-matrix"}},
            )
        raise AssertionError("booking ID confirmation must not depend on read-back")

    result = _transport(handler)(
        "book_activity",
        payload,
        idempotency_key=f"idem:matrix:{adults}:{children}",
    )

    assert result == {"status": "confirmed", "booking_id": "booking-matrix"}
    assert len(seen) == 3


def test_same_idempotency_key_preserves_session_and_passenger_order() -> None:
    payload = _party_dispatch_payload(2, 1)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(422, request=request, json={"reason": "synthetic"})

    transport = _transport(handler)
    first = transport(
        "book_activity",
        payload,
        idempotency_key="idem:stable-group",
    )
    second = transport(
        "book_activity",
        payload,
        idempotency_key="idem:stable-group",
    )

    assert first == second == {"status": "no_effect"}
    assert len(seen) == 2
    assert seen[0].url == seen[1].url
    assert json.loads(seen[0].content) == json.loads(seen[1].content)
    assert seen[0].headers["X-Idempotency-Key"] == seen[1].headers[
        "X-Idempotency-Key"
    ]


def test_bokun_group_requires_child_category_before_any_http() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500, request=request)

    payload = _group_dispatch_payload()
    offer = payload["offer"]
    assert isinstance(offer, dict)
    private = offer["private_binding"]
    assert isinstance(private, dict)
    private.pop("child_pricing_category_id")

    with pytest.raises(ProviderHTTPError, match="private binding"):
        _transport(handler)(
            "book_activity",
            payload,
            idempotency_key="idem:bokun-missing-child-category",
        )

    assert seen == []


def test_bokun_cart_rejects_duplicate_passenger_booking_ids() -> None:
    passengers = (
        {
            "category_id": "adult-1",
            "firstName": "Pessoa",
            "lastName": "Um",
            "nationality": "BR",
            "dateOfBirth": "1990-01-02",
            "gender": "f",
            "full_name": "Pessoa Um",
        },
        {
            "category_id": "child-1",
            "firstName": "Pessoa",
            "lastName": "Dois",
            "nationality": "BR",
            "dateOfBirth": "2016-05-06",
            "gender": "m",
            "full_name": "Pessoa Dois",
        },
    )
    cart = {
        "uuid": "session:group",
        "activityBookings": [
            {
                "bookingId": "activity-group",
                "activityId": "913372",
                "pricingCategoryBookings": [
                    {
                        "bookingId": "duplicate-booking",
                        "pricingCategoryId": "adult-1",
                    },
                    {
                        "bookingId": "duplicate-booking",
                        "pricingCategoryId": "child-1",
                    },
                ],
            }
        ],
    }

    with pytest.raises(ProviderHTTPError, match="passenger binding"):
        BokunHTTPTransport._cart_bindings_v2(
            cart,
            session_id="session:group",
            product_id="913372",
            activity_date="2026-08-11",
            start_time_id="start-1",
            rate_id="rate-1",
            passengers=passengers,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("date", "2026-08-12"),
        ("startTimeId", "start-stale"),
        ("rateId", "rate-stale"),
    ),
)
def test_bokun_reservation_cart_rejects_returned_offer_binding_divergence(
    field: str,
    value: str,
) -> None:
    activity = {
        "bookingId": "activity-booking-1",
        "activityId": "913372",
        "date": "2026-08-11",
        "startTimeId": "start-1",
        "rateId": "rate-1",
        "pricingCategoryBookings": [
            {
                "bookingId": "passenger-booking-1",
                "pricingCategoryId": "857489",
            }
        ],
    }
    activity[field] = value
    cart = {"uuid": "session:bound", "activityBookings": [activity]}
    passenger = {
        "category_id": "857489",
        "firstName": "Pessoa",
        "lastName": "Um",
        "nationality": "BR",
        "dateOfBirth": "1990-01-02",
        "gender": "f",
        "full_name": "Pessoa Um",
    }

    with pytest.raises(ProviderHTTPError, match="offer binding"):
        BokunHTTPTransport._cart_bindings_v2(
            cart,
            session_id="session:bound",
            product_id="913372",
            activity_date="2026-08-11",
            start_time_id="start-1",
            rate_id="rate-1",
            passengers=(passenger,),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("date", "2026-08-12"),
        ("startTimeId", "start-stale"),
        ("rateId", "rate-stale"),
    ),
)
def test_bokun_quote_cart_rejects_returned_offer_binding_divergence(
    field: str,
    value: str,
) -> None:
    activity = {
        "bookingId": "activity-booking-1",
        "activityId": "913372",
        "date": "2026-08-11",
        "startTimeId": "start-1",
        "rateId": "rate-1",
        "pricingCategoryBookings": [
            {
                "bookingId": "passenger-booking-1",
                "pricingCategoryId": "857489",
            }
        ],
    }
    activity[field] = value
    cart = {"uuid": "session:quote", "activityBookings": [activity]}

    with pytest.raises(ProviderHTTPError, match="offer binding"):
        BokunHTTPTransport._validate_quote_cart(
            cart,
            session_id="session:quote",
            product_id="913372",
            activity_date="2026-08-11",
            start_time_id="start-1",
            rate_id="rate-1",
            category_ids=("857489",),
        )


@pytest.mark.parametrize(
    "conflicting_fields",
    (
        {"activityDate": "2026-08-12"},
        {"start_time_id": "start-stale"},
        {"startTime": {"id": "start-stale"}},
        {"rate_id": "rate-stale"},
        {"rate": {"id": "rate-stale"}},
    ),
)
def test_bokun_cart_rejects_conflicting_offer_aliases(
    conflicting_fields: dict[str, object],
) -> None:
    activity = {
        "bookingId": "activity-booking-1",
        "activityId": "913372",
        "date": "2026-08-11",
        "startTimeId": "start-1",
        "rateId": "rate-1",
        "pricingCategoryBookings": [
            {
                "bookingId": "passenger-booking-1",
                "pricingCategoryId": "857489",
            }
        ],
        **conflicting_fields,
    }
    cart = {"uuid": "session:bound", "activityBookings": [activity]}
    passenger = {
        "category_id": "857489",
        "firstName": "Pessoa",
        "lastName": "Um",
        "nationality": "BR",
        "dateOfBirth": "1990-01-02",
        "gender": "f",
        "full_name": "Pessoa Um",
    }

    with pytest.raises(ProviderHTTPError, match="offer binding"):
        BokunHTTPTransport._cart_bindings_v2(
            cart,
            session_id="session:bound",
            product_id="913372",
            activity_date="2026-08-11",
            start_time_id="start-1",
            rate_id="rate-1",
            passengers=(passenger,),
        )


def test_bokun_checkout_rejects_passenger_booking_mismatch() -> None:
    checkout = _checkout(
        (("unexpected-booking", "adult-1"),),
        amount="300.00",
    )
    passenger = {
        "booking_id": "expected-booking",
        "category_id": "adult-1",
        "firstName": "Pessoa",
        "lastName": "Um",
        "nationality": "BR",
        "dateOfBirth": "1990-01-02",
        "gender": "f",
        "full_name": "Pessoa Um",
    }

    with pytest.raises(ProviderHTTPError, match="binding diverged"):
        BokunHTTPTransport._submit_body_v2(
            checkout,
            session_id="session:group",
            activity_booking="activity-group",
            product_id="913372",
            main_contact={
                "firstName": "Pessoa",
                "lastName": "Um",
                "email": "person@example.invalid",
                "phoneNumber": "+55" + "759" + "999" + "9999",
                "nationality": "BR",
                "language": "pt",
                "dateOfBirth": "1990-01-02",
                "gender": "f",
            },
            passengers=(passenger,),
            expected_amount=Decimal("300.00"),
        )


def test_bokun_checkout_rejects_activity_booking_mismatch() -> None:
    checkout = _checkout(
        (("passenger-booking-1", "adult-1"),),
        activity_booking="unexpected-activity",
    )
    passenger = {
        "booking_id": "passenger-booking-1",
        "category_id": "adult-1",
        "firstName": "Pessoa",
        "lastName": "Um",
        "nationality": "BR",
        "dateOfBirth": "1990-01-02",
        "gender": "f",
        "full_name": "Pessoa Um",
    }

    with pytest.raises(ProviderHTTPError, match="activity binding"):
        BokunHTTPTransport._submit_body_v2(
            checkout,
            session_id="session:group",
            activity_booking="expected-activity",
            product_id="913372",
            main_contact={
                "firstName": "Pessoa",
                "lastName": "Um",
                "email": "person@example.invalid",
                "phoneNumber": "+55" + "759" + "999" + "9999",
                "nationality": "BR",
                "language": "pt",
                "dateOfBirth": "1990-01-02",
                "gender": "f",
            },
            passengers=(passenger,),
            expected_amount=Decimal("300.00"),
        )


@pytest.mark.parametrize(
    ("returned_date", "returned_categories"),
    (
        ("2026-08-12", ("adult-1", "adult-1", "child-1")),
        ("2026-08-11", ("adult-1", "child-1")),
        ("2026-08-11", ("adult-1", "adult-1", "adult-1")),
    ),
)
def test_bokun_readback_rejects_date_or_party_divergence(
    returned_date: str,
    returned_categories: tuple[str, ...],
) -> None:
    readback = {
        "booking": {
            "bookingId": "booking-group-123",
            "activityBookings": [
                {
                    "activityId": "913372",
                    "date": returned_date,
                    "pricingCategoryBookings": [
                        {"pricingCategoryId": category_id}
                        for category_id in returned_categories
                    ],
                }
            ],
        }
    }

    with pytest.raises(ProviderHTTPError, match="read-back"):
        BokunHTTPTransport._validate_booking_readback_v2(
            readback,
            booking_id="booking-group-123",
            product_id="913372",
            activity_date="2026-08-11",
            category_ids=("adult-1", "adult-1", "child-1"),
            expected_amount=Decimal("750.00"),
        )


@pytest.mark.parametrize(
    "booking_fields",
    (
        {"status": "CANCELLED"},
        {"totalPrice": "999.00"},
    ),
)
def test_bokun_readback_rejects_status_or_amount_divergence(
    booking_fields: dict[str, str],
) -> None:
    readback = {
        "booking": {
            "bookingId": "booking-group-123",
            **booking_fields,
            "activityBookings": [
                {
                    "activityId": "913372",
                    "date": "2026-08-11",
                    "pricingCategoryBookings": [
                        {"pricingCategoryId": "adult-1"},
                    ],
                }
            ],
        }
    }

    with pytest.raises(ProviderHTTPError, match="read-back"):
        BokunHTTPTransport._validate_booking_readback_v2(
            readback,
            booking_id="booking-group-123",
            product_id="913372",
            activity_date="2026-08-11",
            category_ids=("adult-1",),
            expected_amount=Decimal("300.00"),
        )


@pytest.mark.parametrize(
    "conflicting_fields",
    (
        {"status": "PENDING", "bookingStatus": "CANCELLED"},
        {"totalPrice": "300.00", "totalAmount": "999.00"},
        {
            "totalPrice": {"amount": "300.00", "currency": "BRL"},
            "totalAmount": {"amount": "300.00", "currency": "USD"},
        },
    ),
)
def test_bokun_readback_rejects_conflicting_status_and_amount_aliases(
    conflicting_fields: dict[str, object],
) -> None:
    readback = {
        "booking": {
            "bookingId": "booking-group-123",
            **conflicting_fields,
            "activityBookings": [
                {
                    "activityId": "913372",
                    "date": "2026-08-11",
                    "pricingCategoryBookings": [
                        {"pricingCategoryId": "adult-1"},
                    ],
                }
            ],
        }
    }

    with pytest.raises(ProviderHTTPError, match="read-back"):
        BokunHTTPTransport._validate_booking_readback_v2(
            readback,
            booking_id="booking-group-123",
            product_id="913372",
            activity_date="2026-08-11",
            category_ids=("adult-1",),
            expected_amount=Decimal("300.00"),
        )


def test_bokun_readback_requires_status_base_total_and_currency() -> None:
    readback = {
        "booking": {
            "bookingId": "booking-group-123",
            "activityBookings": [
                {
                    "activityId": "913372",
                    "date": "2026-08-11",
                    "pricingCategoryBookings": [
                        {"pricingCategoryId": "adult-1"},
                    ],
                }
            ],
        }
    }

    with pytest.raises(ProviderHTTPError, match="read-back"):
        BokunHTTPTransport._validate_booking_readback_v2(
            readback,
            booking_id="booking-group-123",
            product_id="913372",
            activity_date="2026-08-11",
            category_ids=("adult-1",),
            expected_amount=Decimal("304.50"),
        )


def test_bokun_readback_distinguishes_base_price_from_fee_inclusive_due() -> None:
    readback = {
        "booking": {
            "bookingId": "booking-group-123",
            "status": "PENDING",
            "totalPrice": 300.0,
            "totalDue": 304.5,
            "currency": "BRL",
            "invoice": {"currency": "BRL"},
            "activityBookings": [
                {
                    "activityId": "913372",
                    "date": "2026-08-11",
                    "pricingCategoryBookings": [
                        {"pricingCategoryId": "adult-1"},
                    ],
                }
            ],
        }
    }

    BokunHTTPTransport._validate_booking_readback_v2(
        readback,
        booking_id="booking-group-123",
        product_id="913372",
        activity_date="2026-08-11",
        category_ids=("adult-1",),
        expected_base_amount=Decimal("300.00"),
        expected_amount=Decimal("304.50"),
        expected_currency="BRL",
    )


def test_bokun_readback_rejects_conflicting_booking_ids_across_payload_tree() -> None:
    readback = {
        "booking": {
            "bookingId": "booking-primary",
            "activityBookings": [
                {
                    "bookingId": "activity-booking-1",
                    "activityId": "913372",
                    "date": "2026-08-11",
                    "pricingCategoryBookings": [
                        {
                            "bookingId": "passenger-booking-1",
                            "pricingCategoryId": "adult-1",
                        }
                    ],
                }
            ],
        },
        "shadow": {"bookingId": "booking-conflict"},
    }

    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        BokunHTTPTransport._validate_booking_readback_v2(
            readback,
            booking_id="booking-primary",
            product_id="913372",
            activity_date="2026-08-11",
            category_ids=("adult-1",),
            expected_amount=Decimal("300.00"),
        )


def test_bokun_booking_reference_rejects_conflicting_aliases() -> None:
    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        BokunHTTPTransport._booking_reference(
            {
                "booking": {
                    "bookingId": "booking-primary",
                    "booking_id": "booking-conflict",
                }
            }
        )


@pytest.mark.parametrize(
    "payload",
    (
        {
            "booking": {"bookingId": "booking-primary"},
            "shadow": {"bookingId": "booking-conflict"},
        },
        {
            "bookingId": "booking-primary",
            "booking": {"bookingId": "booking-conflict"},
        },
    ),
)
def test_bokun_booking_reference_rejects_conflicting_ids_across_payload_tree(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        BokunHTTPTransport._booking_reference(payload)


def test_bokun_booking_reference_accepts_repeated_id_across_payload_tree() -> None:
    assert (
        BokunHTTPTransport._booking_reference(
            {
                "bookingId": "booking-primary",
                "booking": {"booking_id": "booking-primary"},
            }
        )
        == "booking-primary"
    )


def test_bokun_booking_reference_ignores_activity_and_passenger_binding_ids() -> None:
    assert (
        BokunHTTPTransport._booking_reference(
            {
                "booking": {
                    "bookingId": "booking-primary",
                    "activityBookings": [
                        {
                            "bookingId": "activity-booking-1",
                            "activityId": "913372",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": "passenger-booking-1",
                                    "pricingCategoryId": "adult-1",
                                }
                            ],
                        }
                    ],
                }
            }
        )
        == "booking-primary"
    )


def test_bokun_submit_evidence_tracks_failure_only_on_reservation_branches() -> None:
    assert BokunHTTPTransport._booking_submit_evidence(
        {
            "booking": {
                "bookingId": "booking-primary",
                "success": False,
            }
        }
    ) == ("booking-primary", True)
    assert BokunHTTPTransport._booking_submit_evidence(
        {
            "booking": {
                "bookingId": "booking-primary",
                "activityBookings": [
                    {
                        "bookingId": "activity-booking-1",
                        "success": False,
                    }
                ],
            }
        }
    ) == ("booking-primary", False)


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


def test_bokun_submit_uses_fee_inclusive_invoice_due_not_activity_subtotal() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        call = len(seen)
        if call == 1:
            session = request.url.path.split("/session/", 1)[1].split("/", 1)[0]
            return httpx.Response(
                200,
                request=request,
                json={
                    "uuid": session,
                    "activityBookings": [
                        {
                            "bookingId": "activity-booking-fee",
                            "activityId": "913372",
                            "pricingCategoryBookings": [
                                {
                                    "bookingId": "passenger-booking-fee",
                                    "pricingCategoryId": "857489",
                                }
                            ],
                        }
                    ],
                },
            )
        if call == 2:
            checkout = _checkout(
                (("passenger-booking-fee", "857489"),),
                activity_booking="activity-booking-fee",
            )
            checkout["options"][0]["invoice"].pop("remainingAmount")
            checkout["options"][0]["invoice"]["remainingAmountAsText"] = (
                "R$ 304,50"
            )
            return httpx.Response(200, request=request, json=checkout)
        if call == 3:
            return httpx.Response(
                200,
                request=request,
                json={"booking": {"bookingId": "booking-fee-123"}},
            )
        raise AssertionError("booking ID confirmation must not depend on read-back")

    payload = _dispatch_payload()
    payload["offer"] = {**payload["offer"], "amount": "304.50"}

    result = _transport(handler)(
        "book_activity",
        payload,
        idempotency_key="idem:bokun-fee-inclusive",
    )

    assert result == {"status": "confirmed", "booking_id": "booking-fee-123"}
    assert len(seen) == 3


def test_bokun_readback_accepts_distinct_component_booking_ids_below_principal() -> (
    None
):
    readback = {
        "bookingId": "booking-primary",
        "status": "PENDING",
        "totalPrice": 300.0,
        "totalDue": 304.5,
        "currency": "BRL",
        "activityBookings": [
            {
                "bookingId": "activity-booking-1",
                "activityId": "913372",
                "date": "2026-08-11",
                "pricingCategoryBookings": [
                    {
                        "bookingId": "passenger-booking-1",
                        "pricingCategoryId": "adult-1",
                    }
                ],
            }
        ],
    }

    BokunHTTPTransport._validate_booking_readback_v2(
        readback,
        booking_id="booking-primary",
        product_id="913372",
        activity_date="2026-08-11",
        category_ids=("adult-1",),
        expected_base_amount=Decimal("300.00"),
        expected_amount=Decimal("304.50"),
        expected_currency="BRL",
    )
