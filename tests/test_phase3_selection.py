from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from reservation_domain import LookupStatus
from reservation_lookup import CloudbedsReadAdapter, CloudbedsLookupRequest, ReadResponse
from reservation_lookup.selection import (
    SelectionErrorCode,
    SelectionRejected,
    revalidate_offer,
    select_offer,
)
from tests.test_phase3_cloudbeds_adapter import lookup_request

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "phase3" / "cloudbeds"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)

    def send(self, request):
        return self.responses.pop(0)


def result_for(*, label=None, price=None, empty=False, status_code=200, at=T0):
    available = fixture("no-availability.json" if empty else "available-room-types.json")
    if label is not None and not empty:
        available["data"][0]["roomTypeName"] = label
    if price is not None and not empty:
        available["data"][0]["roomRateDetailed"][1]["rate"] = price
    transport = Transport(
        [
            ReadResponse(status_code=status_code, body=available),
            ReadResponse(status_code=200, body=fixture("rate-plans.json")),
        ]
    )
    return CloudbedsReadAdapter(transport).lookup(
        lookup_request(), observed_at=at, ttl=timedelta(minutes=5)
    )


class ExactOfferSelectionTests(unittest.TestCase):
    def assert_code(self, expected, callback) -> None:
        with self.assertRaises(SelectionRejected) as caught:
            callback()
        self.assertIs(caught.exception.code, expected)

    def test_exact_offer_id_and_fresh_positive_evidence_select(self) -> None:
        result = result_for()
        selected = select_offer(
            result,
            offer_id=result.offers[0].offer_id,
            at=T0 + timedelta(minutes=1),
        )
        self.assertEqual(selected, result.offers[0])

    def test_label_provider_ref_index_random_and_wrong_types_never_select(self) -> None:
        result = result_for()
        offer = result.offers[0]
        candidates = (
            offer.public_label,
            offer.provider_ref,
            "nº 2",
            "2",
            "offer:" + "f" * 64,
            "",
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assert_code(
                    SelectionErrorCode.OFFER_ID_NOT_FOUND,
                    lambda candidate=candidate: select_offer(
                        result, offer_id=candidate, at=T0
                    ),
                )
        with self.assertRaises(TypeError):
            select_offer(result, offer_id=123, at=T0)

    def test_negative_uncertain_and_expired_lookup_fail_closed(self) -> None:
        positive = result_for()
        negative = result_for(empty=True)
        uncertain = result_for(status_code=503)
        self.assertEqual(negative.evidence.status, LookupStatus.NEGATIVE)
        self.assertEqual(uncertain.evidence.status, LookupStatus.UNCERTAIN)
        for result in (negative, uncertain):
            self.assert_code(
                SelectionErrorCode.LOOKUP_NOT_POSITIVE,
                lambda result=result: select_offer(
                    result,
                    offer_id=positive.offers[0].offer_id,
                    at=T0,
                ),
            )
        self.assert_code(
            SelectionErrorCode.LOOKUP_EXPIRED,
            lambda: select_offer(
                positive,
                offer_id=positive.offers[0].offer_id,
                at=T0 + timedelta(minutes=5, microseconds=1),
            ),
        )

    def test_duplicate_matches_fail_closed_instead_of_choosing_first(self) -> None:
        result = result_for()
        duplicated = replace(result, offers=(result.offers[0], result.offers[0]))
        self.assert_code(
            SelectionErrorCode.OFFER_ID_NOT_UNIQUE,
            lambda: select_offer(
                duplicated,
                offer_id=result.offers[0].offer_id,
                at=T0,
            ),
        )


class RevalidationTests(unittest.TestCase):
    def assert_code(self, expected, callback) -> None:
        with self.assertRaises(SelectionRejected) as caught:
            callback()
        self.assertIs(caught.exception.code, expected)

    def test_label_only_change_remains_selectable(self) -> None:
        previous_result = result_for(label="Quarto nº 2", at=T0)
        fresh_result = result_for(
            label="QUARTO N° 2", at=T0 + timedelta(minutes=1)
        )
        fresh = revalidate_offer(
            previous_result.offers[0],
            fresh_result,
            at=T0 + timedelta(minutes=1),
        )
        self.assertEqual(fresh.offer_id, previous_result.offers[0].offer_id)
        self.assertNotEqual(fresh.public_label, previous_result.offers[0].public_label)

    def test_executable_change_and_disappearance_are_offer_changed(self) -> None:
        previous = result_for(at=T0).offers[0]
        changed_price = result_for(
            price="211.00", at=T0 + timedelta(minutes=1)
        )
        disappeared = result_for(empty=True, at=T0 + timedelta(minutes=1))
        self.assert_code(
            SelectionErrorCode.OFFER_CHANGED,
            lambda: revalidate_offer(
                previous, changed_price, at=T0 + timedelta(minutes=1)
            ),
        )
        self.assert_code(
            SelectionErrorCode.LOOKUP_NOT_POSITIVE,
            lambda: revalidate_offer(
                previous, disappeared, at=T0 + timedelta(minutes=1)
            ),
        )

    def test_duplicate_revalidation_is_offer_changed(self) -> None:
        previous_result = result_for(at=T0)
        fresh = result_for(at=T0 + timedelta(minutes=1))
        duplicated = replace(fresh, offers=(fresh.offers[0], fresh.offers[0]))
        self.assert_code(
            SelectionErrorCode.OFFER_CHANGED,
            lambda: revalidate_offer(
                previous_result.offers[0],
                duplicated,
                at=T0 + timedelta(minutes=1),
            ),
        )


if __name__ == "__main__":
    unittest.main()
