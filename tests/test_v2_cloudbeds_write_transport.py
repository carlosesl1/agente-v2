from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs

import httpx
import pytest

from v2_adapters.provider_http import CloudbedsHTTPTransport, ProviderHTTPError


ROOM_TYPE_ID = "room-type-1"
ROOM_RATE_ID = "room-rate-1"
RESERVATION_ID = "reservation-123"
START_DATE = "2026-08-10"
END_DATE = "2026-08-12"
TOTAL = "450.00"


def _dispatch_payload(
    *,
    adults: int = 2,
    children: int = 0,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "v2-reservation-dispatch-v1",
        "command_id": "cmd:cloudbeds-write-001",
        "operation": "reserve_lodging",
        "offer": {
            "binding": "b" * 64,
            "private_binding": {
                "room_type_id": ROOM_TYPE_ID,
                "room_rate_id": ROOM_RATE_ID,
            },
            "offer_id": "offer:public-001",
            "start_date": START_DATE,
            "end_date": END_DATE,
            "start_time": None,
            "party": {"adults": adults, "children": children},
            "amount": TOTAL,
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


def _availability(
    *,
    room_type_id: str = ROOM_TYPE_ID,
    room_rate_id: str = ROOM_RATE_ID,
    currency: str = "BRL",
    rooms_available: int = 2,
    daily_rates: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "success": True,
        "data": [
            {
                "propertyCurrency": {"currencyCode": currency},
                "propertyRooms": [
                    {
                        "roomTypeID": room_type_id,
                        "roomRateID": room_rate_id,
                        "roomTypeName": "Quarto controlado",
                        "roomsAvailable": rooms_available,
                        "maxGuests": 4,
                        "roomRateDetailed": daily_rates
                        if daily_rates is not None
                        else [
                            {
                                "date": "2026-08-10",
                                "rate": "225.00",
                                "roomsAvailable": rooms_available,
                            },
                            {
                                "date": "2026-08-11",
                                "rate": "225.00",
                                "roomsAvailable": rooms_available,
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _availability_with_alias(field: str, value: str) -> dict[str, object]:
    payload = _availability()
    payload["data"][0]["propertyRooms"][0][field] = value
    return payload


def _readback(
    *,
    adults: int = 2,
    children: int = 0,
) -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "reservationID": RESERVATION_ID,
            "startDate": START_DATE,
            "endDate": END_DATE,
            "total": TOTAL,
            "unassigned": [
                {
                    "roomTypeID": ROOM_TYPE_ID,
                    "startDate": START_DATE,
                    "endDate": END_DATE,
                    "adults": str(adults),
                    "children": str(children),
                    "dailyRates": [
                        {"date": "2026-08-10", "rate": "225.00"},
                        {"date": "2026-08-11", "rate": "225.00"},
                    ],
                    "roomTotal": TOTAL,
                }
            ],
        },
    }


def _transport(handler) -> CloudbedsHTTPTransport:
    return CloudbedsHTTPTransport(
        api_key="cloudbeds-secret",
        property_id="property-1",
        source_id="source-1",
        base_url="https://api.cloudbeds.invalid",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _success_handler(
    seen: list[httpx.Request],
    *,
    adults: int,
    children: int,
):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/api/v1.3/getAvailableRoomTypes"):
            query = parse_qs(request.url.query.decode())
            assert query == {
                "propertyID": ["property-1"],
                "startDate": [START_DATE],
                "endDate": [END_DATE],
                "adults": [str(adults)],
                "children": [str(children)],
                "detailedRates": ["true"],
            }
            return httpx.Response(200, request=request, json=_availability())
        if request.method == "POST":
            form = parse_qs(request.content.decode())
            assert request.url.path.endswith("/api/v1.1/postReservation")
            assert form["propertyID"] == ["property-1"]
            assert form["sourceID"] == ["source-1"]
            assert form["startDate"] == [START_DATE]
            assert form["endDate"] == [END_DATE]
            assert form["guestFirstName"] == ["Carlos"]
            assert form["guestLastName"] == ["Eduardo"]
            assert form["rooms"] == [
                f'[{ {"roomTypeID": ROOM_TYPE_ID, "quantity": 1} }]'.replace("'", '"').replace(" ", "")
            ]
            assert form["adults"] == [
                f'[{ {"roomTypeID": ROOM_TYPE_ID, "quantity": adults} }]'.replace("'", '"').replace(" ", "")
            ]
            assert form["children"] == [
                f'[{ {"roomTypeID": ROOM_TYPE_ID, "quantity": children} }]'.replace("'", '"').replace(" ", "")
            ]
            assert form["paymentMethod"] == ["credit_card"]
            assert request.headers["X-Idempotency-Key"] == "idem:cloudbeds-001"
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "reservationID": RESERVATION_ID},
            )
        assert request.url.path.endswith("/api/v1.3/getReservation")
        assert parse_qs(request.url.query.decode())["reservationID"] == [
            RESERVATION_ID
        ]
        return httpx.Response(
            200,
            request=request,
            json=_readback(adults=adults, children=children),
        )

    return handler


@pytest.mark.parametrize(("adults", "children"), ((1, 0), (2, 0), (2, 1)))
def test_cloudbeds_single_room_party_is_revalidated_submitted_and_read_back(
    adults: int,
    children: int,
) -> None:
    seen: list[httpx.Request] = []

    result = _transport(_success_handler(seen, adults=adults, children=children))(
        "reserve_lodging",
        _dispatch_payload(adults=adults, children=children),
        idempotency_key="idem:cloudbeds-001",
    )

    assert result == {"status": "confirmed", "reservation_id": RESERVATION_ID}
    assert [request.method for request in seen] == ["GET", "POST", "GET"]
    assert all(
        request.headers["Authorization"] == "Bearer cloudbeds-secret"
        for request in seen
    )
    assert sum(request.method == "POST" for request in seen) == 1


@pytest.mark.parametrize(
    ("status_code", "submit_payload"),
    (
        (422, {"success": False, "reservationID": RESERVATION_ID}),
        (409, {"success": True, "reservationID": RESERVATION_ID}),
        (500, {"success": True, "reservationID": RESERVATION_ID}),
        (200, {"success": False, "reservationID": RESERVATION_ID}),
        (
            200,
            {
                "success": True,
                "data": {
                    "reservation": {
                        "success": False,
                        "reservationID": RESERVATION_ID,
                    }
                },
            },
        ),
        (200, {"success": True}),
        (
            200,
            {
                "success": True,
                "reservationID": RESERVATION_ID,
                "data": {"reservationID": "reservation-conflict"},
            },
        ),
        (
            200,
            {
                "success": True,
                "reservationID": None,
                "data": {"reservationID": RESERVATION_ID},
            },
        ),
    ),
)
def test_cloudbeds_bad_submit_evidence_is_unknown_without_readback_or_retry(
    status_code: int,
    submit_payload: dict[str, object],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            assert request.url.path.endswith("/api/v1.3/getAvailableRoomTypes")
            return httpx.Response(200, request=request, json=_availability())
        assert request.method == "POST"
        return httpx.Response(status_code, request=request, json=submit_payload)

    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        _transport(handler)(
            "reserve_lodging",
            _dispatch_payload(),
            idempotency_key="idem:cloudbeds-bad-submit",
        )

    assert [request.method for request in seen] == ["GET", "POST"]
    assert sum(request.method == "POST" for request in seen) == 1


@pytest.mark.parametrize(
    "mutate",
    (
        lambda body: body["data"].update(reservationID="different"),
        lambda body: body["data"].update(startDate="2026-08-09"),
        lambda body: body["data"].update(endDate="2026-08-13"),
        lambda body: body["data"].update(total="451.00"),
        lambda body: body["data"].update(totalAmount="451.00"),
        lambda body: body["data"]["unassigned"][0].update(
            roomTypeID="different-room"
        ),
        lambda body: body["data"]["unassigned"][0].update(
            roomTypeId="different-room"
        ),
        lambda body: body["data"]["unassigned"][0].update(
            startDate="2026-08-09"
        ),
        lambda body: body["data"]["unassigned"][0].update(
            endDate="2026-08-13"
        ),
        lambda body: body["data"]["unassigned"][0].update(adults="1"),
        lambda body: body["data"]["unassigned"][0].update(children="1"),
        lambda body: body["data"]["unassigned"][0].update(roomTotal="451.00"),
        lambda body: body["data"]["unassigned"][0]["dailyRates"].pop(),
        lambda body: body["data"]["unassigned"][0]["dailyRates"][0].update(
            rate="224.00"
        ),
        lambda body: body.update(success=False),
        lambda body: body["data"].update(unassigned=[]),
        lambda body: body["data"].update(
            assigned=[deepcopy(body["data"]["unassigned"][0])]
        ),
        lambda body: body["data"].update(assigned=["malformed-room"]),
    ),
)
def test_cloudbeds_readback_mismatch_is_unknown_after_exactly_one_submit(
    mutate,
) -> None:
    seen: list[httpx.Request] = []
    readback = _readback()
    mutate(readback)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/api/v1.3/getAvailableRoomTypes"):
            return httpx.Response(200, request=request, json=_availability())
        if request.method == "POST":
            return httpx.Response(
                200,
                request=request,
                json={"success": True, "reservationID": RESERVATION_ID},
            )
        assert request.url.path.endswith("/api/v1.3/getReservation")
        return httpx.Response(200, request=request, json=readback)

    with pytest.raises(ProviderHTTPError, match="read-back"):
        _transport(handler)(
            "reserve_lodging",
            _dispatch_payload(),
            idempotency_key="idem:cloudbeds-readback-mismatch",
        )

    assert [request.method for request in seen] == ["GET", "POST", "GET"]
    assert sum(request.method == "POST" for request in seen) == 1


@pytest.mark.parametrize(
    "availability",
    (
        _availability(room_rate_id="different-rate"),
        _availability(room_type_id="different-room"),
        _availability(currency="USD"),
        _availability(rooms_available=0),
        _availability_with_alias("ratePlanID", "rate-other"),
        _availability_with_alias("roomTypeId", "room-other"),
        _availability(
            daily_rates=[
                {
                    "date": "2026-08-10",
                    "rate": "225.00",
                    "roomsAvailable": 2,
                }
            ]
        ),
        _availability(
            daily_rates=[
                {
                    "date": "2026-08-10",
                    "rate": "226.00",
                    "roomsAvailable": 2,
                },
                {
                    "date": "2026-08-11",
                    "rate": "225.00",
                    "roomsAvailable": 2,
                },
            ]
        ),
    ),
)
def test_cloudbeds_stale_or_mismatched_rate_fails_before_submit(
    availability: dict[str, object],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        return httpx.Response(200, request=request, json=availability)

    with pytest.raises(ProviderHTTPError, match="revalidation"):
        _transport(handler)(
            "reserve_lodging",
            _dispatch_payload(),
            idempotency_key="idem:cloudbeds-stale-rate",
        )

    assert [request.method for request in seen] == ["GET"]


@pytest.mark.parametrize(("target", "field"), (("offer", "room_allocations"), ("customer", "guests")))
def test_cloudbeds_multi_room_and_individual_guests_are_rejected_before_http(
    target: str,
    field: str,
) -> None:
    seen: list[httpx.Request] = []
    payload = _dispatch_payload()
    payload[target][field] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500, request=request)

    with pytest.raises(ProviderHTTPError, match="fields"):
        _transport(handler)(
            "reserve_lodging",
            payload,
            idempotency_key="idem:cloudbeds-unsupported-scope",
        )

    assert seen == []


def test_cloudbeds_write_timeout_is_unknown_without_retry() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, request=request, json=_availability())
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with pytest.raises(ProviderHTTPError, match="ambiguous"):
        _transport(handler)(
            "reserve_lodging",
            _dispatch_payload(),
            idempotency_key="idem:cloudbeds-timeout",
        )

    assert [request.method for request in seen] == ["GET", "POST"]
    assert sum(request.method == "POST" for request in seen) == 1
