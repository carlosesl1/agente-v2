from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from reservation_domain import LookupStatus, Party, SearchQuery, ServiceKind
from reservation_lookup import BokunLookupRequest, ReadResponse
from reservation_lookup.bokun import BokunReadAdapter

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "phase3" / "bokun"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def query(*, start_time=None) -> SearchQuery:
    return SearchQuery(
        service=ServiceKind.ACTIVITY,
        start_date=date(2026, 11, 11),
        end_date=None,
        start_time=start_time,
        party=Party(adults=2, children=0),
    )


def lookup_request(*, start_time=None) -> BokunLookupRequest:
    return BokunLookupRequest(product_id="913776", query=query(start_time=start_time))


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


def adapter_for(activity=None, availabilities=None):
    transport = FixtureTransport(
        [
            ReadResponse(
                status_code=200,
                body=fixture("activity.json") if activity is None else activity,
            ),
            ReadResponse(
                status_code=200,
                body=(
                    fixture("availabilities.json")
                    if availabilities is None
                    else availabilities
                ),
            ),
        ]
    )
    return BokunReadAdapter(transport), transport


class BokunBoundaryTests(unittest.TestCase):
    def test_exact_get_requests_and_positive_normalization(self) -> None:
        adapter, transport = adapter_for()
        result = adapter.lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        self.assertEqual(
            [(item.method, item.path, item.query) for item in transport.requests],
            [
                (
                    "GET",
                    "/activity.json/913776",
                    (("currency", "BRL"), ("lang", "pt_BR")),
                ),
                (
                    "GET",
                    "/activity.json/913776/availabilities",
                    (
                        ("currency", "BRL"),
                        ("end", "2026-11-11"),
                        ("start", "2026-11-11"),
                    ),
                ),
            ],
        )
        self.assertEqual(result.evidence.status, LookupStatus.POSITIVE)
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(
            offer.provider_ref,
            "bokun.product.913776.start.3210363.rate.RATE1",
        )
        self.assertEqual(offer.public_label, "Mixila 1D — 2026-11-11 07:30")
        self.assertEqual(offer.start_date, date(2026, 11, 11))
        self.assertIsNone(offer.end_date)
        self.assertEqual(offer.start_time, "07:30")
        self.assertEqual(offer.total.amount, Decimal("1300.00"))
        self.assertRegex(offer.offer_id, r"^offer:[a-f0-9]{64}$")
        self.assertEqual(offer.lookup_id, result.evidence.lookup_id)

    def test_multiple_start_times_are_offers_when_query_has_no_time(self) -> None:
        body = fixture("availabilities.json")
        second = deepcopy(body["data"][0])
        second["startTimeId"] = "3210364"
        second["startTime"] = "13:30"
        second["totalAmount"] = "1400.00"
        body["data"].append(second)
        adapter, _ = adapter_for(availabilities=body)
        result = adapter.lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        self.assertEqual(result.evidence.status, LookupStatus.POSITIVE)
        self.assertEqual({offer.start_time for offer in result.offers}, {"07:30", "13:30"})

    def test_specific_start_time_filters_other_valid_options(self) -> None:
        body = fixture("availabilities.json")
        second = deepcopy(body["data"][0])
        second["startTimeId"] = "3210364"
        second["startTime"] = "13:30"
        body["data"].append(second)
        adapter, _ = adapter_for(availabilities=body)
        result = adapter.lookup(
            lookup_request(start_time="13:30"),
            observed_at=T0,
            ttl=timedelta(minutes=5),
        )
        self.assertEqual(len(result.offers), 1)
        self.assertEqual(result.offers[0].start_time, "13:30")

    def test_label_only_change_preserves_id_and_price_change_invalidates(self) -> None:
        original_adapter, _ = adapter_for()
        original = original_adapter.lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        relabeled = fixture("activity.json")
        relabeled["title"] = "MIXILA N° 1"
        relabeled_adapter, _ = adapter_for(activity=relabeled)
        relabeled_result = relabeled_adapter.lookup(
            lookup_request(),
            observed_at=T0 + timedelta(seconds=1),
            ttl=timedelta(minutes=5),
        )
        self.assertEqual(original.offers[0].offer_id, relabeled_result.offers[0].offer_id)

        repriced = fixture("availabilities.json")
        repriced["data"][0]["totalAmount"] = "1301.00"
        repriced_adapter, _ = adapter_for(availabilities=repriced)
        repriced_result = repriced_adapter.lookup(
            lookup_request(),
            observed_at=T0 + timedelta(seconds=2),
            ttl=timedelta(minutes=5),
        )
        self.assertNotEqual(original.offers[0].offer_id, repriced_result.offers[0].offer_id)

    def test_provider_option_order_does_not_change_snapshot_or_offer_order(self) -> None:
        body = fixture("availabilities.json")
        second = deepcopy(body["data"][0])
        second["startTimeId"] = "3210364"
        second["startTime"] = "13:30"
        body["data"].append(second)
        reversed_body = deepcopy(body)
        reversed_body["data"].reverse()
        left_adapter, _ = adapter_for(availabilities=body)
        right_adapter, _ = adapter_for(availabilities=reversed_body)
        left = left_adapter.lookup(lookup_request(), observed_at=T0, ttl=timedelta(minutes=5))
        right = right_adapter.lookup(lookup_request(), observed_at=T0, ttl=timedelta(minutes=5))
        self.assertEqual(left.evidence.snapshot_hash, right.evidence.snapshot_hash)
        self.assertEqual(
            tuple(offer.offer_id for offer in left.offers),
            tuple(offer.offer_id for offer in right.offers),
        )


class BokunFailClosedTests(unittest.TestCase):
    def assert_uncertain(self, *, activity=None, availabilities=None) -> None:
        adapter, _ = adapter_for(activity=activity, availabilities=availabilities)
        result = adapter.lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        self.assertEqual(result.evidence.status, LookupStatus.UNCERTAIN)
        self.assertEqual(result.offers, ())
        self.assertTrue(result.failures)

    def test_metadata_product_id_mismatch_is_uncertain(self) -> None:
        self.assert_uncertain(activity=fixture("mismatched-activity.json"))

    def test_valid_empty_or_unavailable_options_are_negative(self) -> None:
        cases = [fixture("no-availability.json")]
        for key, value in (
            ("available", False),
            ("soldOut", True),
            ("unavailable", True),
            ("availabilityCount", 0),
            ("date", "2026-11-12"),
        ):
            body = fixture("availabilities.json")
            body["data"][0][key] = value
            cases.append(body)
        for body in cases:
            with self.subTest(body=body):
                adapter, _ = adapter_for(availabilities=body)
                result = adapter.lookup(
                    lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
                )
                self.assertEqual(result.evidence.status, LookupStatus.NEGATIVE)
                self.assertEqual(result.offers, ())

    def test_missing_executable_fields_are_uncertain(self) -> None:
        for key in (
            "startTimeId",
            "startTime",
            "totalAmount",
            "currency",
            "defaultRateId",
        ):
            with self.subTest(key=key):
                body = fixture("availabilities.json")
                del body["data"][0][key]
                self.assert_uncertain(availabilities=body)

    def test_non_finite_price_and_wrong_currency_are_uncertain(self) -> None:
        for key, value in (
            ("totalAmount", "NaN"),
            ("totalAmount", "Infinity"),
            ("currency", "USD"),
        ):
            with self.subTest(key=key, value=value):
                body = fixture("availabilities.json")
                body["data"][0][key] = value
                self.assert_uncertain(availabilities=body)

    def test_malformed_envelopes_and_http_error_are_uncertain(self) -> None:
        self.assert_uncertain(activity=[])
        self.assert_uncertain(availabilities=[])
        self.assert_uncertain(availabilities={"success": False, "data": []})
        transport = FixtureTransport(
            [
                ReadResponse(status_code=503, body={"error": "synthetic"}),
                ReadResponse(status_code=200, body=fixture("availabilities.json")),
            ]
        )
        result = BokunReadAdapter(transport).lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        self.assertEqual(result.evidence.status, LookupStatus.UNCERTAIN)
        self.assertEqual(result.offers, ())

    def test_transport_exception_is_sanitized(self) -> None:
        transport = FixtureTransport(
            [RuntimeError("secret-shaped-provider-message"), ReadResponse(200, {"data": []})]
        )
        result = BokunReadAdapter(transport).lookup(
            lookup_request(), observed_at=T0, ttl=timedelta(minutes=5)
        )
        self.assertEqual(result.evidence.status, LookupStatus.UNCERTAIN)
        self.assertEqual(result.offers, ())
        self.assertNotIn("secret-shaped-provider-message", repr(result))


if __name__ == "__main__":
    unittest.main()
