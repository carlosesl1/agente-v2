from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from reservation_domain import LookupStatus, Party, SearchQuery, ServiceKind
from reservation_lookup import (
    CloudbedsLookupRequest,
    ReadResponse,
    SelectionRejected,
    revalidate_offer,
)
from reservation_lookup.cloudbeds import CloudbedsReadAdapter

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "phase3" / "cloudbeds"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def query() -> SearchQuery:
    return SearchQuery(
        service=ServiceKind.LODGING,
        start_date=date(2026, 11, 10),
        end_date=date(2026, 11, 12),
        start_time=None,
        party=Party(adults=2, children=1),
    )


def lookup_request() -> CloudbedsLookupRequest:
    return CloudbedsLookupRequest(property_id="property.42", query=query())


class FixtureTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        if not self.responses:
            raise RuntimeError("unexpected fixture request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def adapter_for(available=None, rates=None):
    transport = FixtureTransport(
        [
            ReadResponse(
                status_code=200,
                body=fixture("available-room-types.json") if available is None else available,
            ),
            ReadResponse(
                status_code=200,
                body=fixture("rate-plans.json") if rates is None else rates,
            ),
        ]
    )
    return CloudbedsReadAdapter(transport), transport


class CloudbedsBoundaryTests(unittest.TestCase):
    def test_exact_get_requests_and_positive_normalization(self) -> None:
        adapter, transport = adapter_for()
        result = adapter.lookup(
            lookup_request(),
            observed_at=T0,
            ttl=timedelta(minutes=5),
        )
        self.assertEqual(
            [(item.method, item.path, item.query) for item in transport.requests],
            [
                (
                    "GET",
                    "/api/v1.3/getAvailableRoomTypes",
                    (
                        ("adults", "2"),
                        ("children", "1"),
                        ("detailedRates", "true"),
                        ("endDate", "2026-11-12"),
                        ("propertyID", "property.42"),
                        ("startDate", "2026-11-10"),
                    ),
                ),
                (
                    "GET",
                    "/api/v1.2/getRatePlans",
                    (
                        ("adults", "2"),
                        ("children", "1"),
                        ("detailedRates", "true"),
                        ("endDate", "2026-11-12"),
                        ("propertyID", "property.42"),
                        ("startDate", "2026-11-10"),
                    ),
                ),
            ],
        )
        self.assertEqual(result.evidence.status, LookupStatus.POSITIVE)
        self.assertEqual(result.evidence.observed_at, T0)
        self.assertEqual(result.evidence.expires_at, T0 + timedelta(minutes=5))
        self.assertEqual(len(result.provenance.request_fingerprints), 2)
        self.assertEqual(len(result.provenance.response_hashes), 2)
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertRegex(offer.offer_id, r"^offer:[a-f0-9]{64}$")
        self.assertEqual(
            offer.provider_ref,
            "cloudbeds.property.property.42.room.101.rate.RP1",
        )
        self.assertEqual(offer.public_label, "Quarto nº 2")
        self.assertEqual(offer.total.amount, Decimal("420.00"))
        self.assertEqual(offer.total.currency, "BRL")
        self.assertEqual(offer.lookup_id, result.evidence.lookup_id)
        self.assertNotIn("body", result.__dataclass_fields__)
        self.assertNotIn("raw", result.__dataclass_fields__)

    def test_label_only_change_preserves_offer_id_and_price_change_invalidates(self) -> None:
        first_adapter, _ = adapter_for()
        first = first_adapter.lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        relabeled = fixture("available-room-types.json")
        relabeled["data"][0]["roomTypeName"] = "QUARTO N° 2"
        label_adapter, _ = adapter_for(available=relabeled)
        label_result = label_adapter.lookup(
            lookup_request(),
            observed_at=T0 + timedelta(seconds=1),
            ttl=timedelta(minutes=5),
        )
        self.assertEqual(first.offers[0].offer_id, label_result.offers[0].offer_id)

        repriced = fixture("available-room-types.json")
        repriced["data"][0]["roomRateDetailed"][1]["rate"] = "211.00"
        price_adapter, _ = adapter_for(available=repriced)
        price_result = price_adapter.lookup(
            lookup_request(),
            observed_at=T0 + timedelta(seconds=2),
            ttl=timedelta(minutes=5),
        )
        self.assertNotEqual(first.offers[0].offer_id, price_result.offers[0].offer_id)

    def test_property_id_is_part_of_executable_offer_identity(self) -> None:
        first_adapter, _ = adapter_for()
        second_adapter, _ = adapter_for()
        first = first_adapter.lookup(
            CloudbedsLookupRequest(property_id="property.42", query=query()),
            observed_at=T0,
            ttl=timedelta(minutes=5),
        )
        second = second_adapter.lookup(
            CloudbedsLookupRequest(property_id="property.43", query=query()),
            observed_at=T0,
            ttl=timedelta(minutes=5),
        )

        self.assertNotEqual(first.evidence.lookup_id, second.evidence.lookup_id)
        self.assertNotEqual(first.offers[0].provider_ref, second.offers[0].provider_ref)
        self.assertNotEqual(first.offers[0].offer_id, second.offers[0].offer_id)
        with self.assertRaises(SelectionRejected):
            revalidate_offer(first.offers[0], second, at=T0)

    def test_provider_option_order_does_not_change_result_order_or_snapshot_hash(self) -> None:
        first_body = fixture("available-room-types.json")
        second_room = deepcopy(first_body["data"][0])
        second_room["roomTypeID"] = "102"
        second_room["roomTypeName"] = "Quarto B"
        second_room["roomRateDetailed"][0]["rate"] = "220.00"
        second_room["roomRateDetailed"][1]["rate"] = "220.00"
        first_body["data"].append(second_room)
        reversed_body = deepcopy(first_body)
        reversed_body["data"].reverse()

        left_adapter, _ = adapter_for(available=first_body)
        right_adapter, _ = adapter_for(available=reversed_body)
        left = left_adapter.lookup(lookup_request(), observed_at=T0, ttl=timedelta(minutes=5))
        right = right_adapter.lookup(lookup_request(), observed_at=T0, ttl=timedelta(minutes=5))
        self.assertEqual(left.evidence.snapshot_hash, right.evidence.snapshot_hash)
        self.assertEqual(
            tuple(item.offer_id for item in left.offers),
            tuple(item.offer_id for item in right.offers),
        )

    def test_invalid_ttl_and_clock_fail_before_transport(self) -> None:
        adapter, transport = adapter_for()
        with self.assertRaises(ValueError):
            adapter.lookup(lookup_request(), observed_at=T0, ttl=timedelta(0))
        with self.assertRaises(ValueError):
            adapter.lookup(
                lookup_request(),
                observed_at=T0.replace(tzinfo=None),
                ttl=timedelta(minutes=5),
            )
        with self.assertRaises(ValueError):
            adapter.lookup(
                lookup_request(),
                observed_at=T0,
                ttl=timedelta(minutes=16),
            )
        self.assertEqual(transport.requests, [])


class CloudbedsFailClosedTests(unittest.TestCase):
    def assert_uncertain(self, available, rates=None) -> None:
        adapter, _ = adapter_for(available=available, rates=rates)
        result = adapter.lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        self.assertEqual(result.evidence.status, LookupStatus.UNCERTAIN)
        self.assertEqual(result.offers, ())
        self.assertTrue(result.failures)

    def test_valid_empty_availability_is_negative(self) -> None:
        adapter, _ = adapter_for(available=fixture("no-availability.json"))
        result = adapter.lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        self.assertEqual(result.evidence.status, LookupStatus.NEGATIVE)
        self.assertEqual(result.offers, ())
        self.assertEqual(result.failures, ())

    def test_missing_rate_plan_is_uncertain(self) -> None:
        self.assert_uncertain(fixture("missing-rate-plan.json"))

    def test_partial_stay_is_uncertain(self) -> None:
        body = fixture("available-room-types.json")
        body["data"][0]["roomRateDetailed"].pop()
        self.assert_uncertain(body)

    def test_currency_mismatch_is_uncertain(self) -> None:
        body = fixture("available-room-types.json")
        body["data"][0]["roomRateDetailed"][1]["currency"] = "USD"
        self.assert_uncertain(body)

    def test_non_finite_or_missing_price_is_uncertain(self) -> None:
        for value in ("NaN", "Infinity", None, ""):
            with self.subTest(value=value):
                body = fixture("available-room-types.json")
                body["data"][0]["roomRateDetailed"][0]["rate"] = value
                self.assert_uncertain(body)

    def test_malformed_envelope_and_provider_error_are_uncertain(self) -> None:
        self.assert_uncertain([])
        self.assert_uncertain({"success": False, "data": []})
        adapter = CloudbedsReadAdapter(
            FixtureTransport(
                [
                    ReadResponse(status_code=503, body={"error": "synthetic"}),
                    ReadResponse(status_code=200, body=fixture("rate-plans.json")),
                ]
            )
        )
        result = adapter.lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        self.assertEqual(result.evidence.status, LookupStatus.UNCERTAIN)
        self.assertEqual(result.offers, ())

    def test_transport_exception_is_sanitized_and_fail_closed(self) -> None:
        transport = FixtureTransport(
            [RuntimeError("secret-shaped-provider-message"), ReadResponse(200, {"data": []})]
        )
        result = CloudbedsReadAdapter(transport).lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        self.assertEqual(result.evidence.status, LookupStatus.UNCERTAIN)
        self.assertEqual(result.offers, ())
        self.assertNotIn("secret-shaped-provider-message", repr(result))


if __name__ == "__main__":
    unittest.main()
