from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from reservation_domain import (
    LookupEvidence,
    LookupStatus,
    Money,
    OfferSnapshot,
    Party,
    SearchQuery,
    ServiceKind,
)
from reservation_lookup import (
    BokunLookupRequest,
    CloudbedsLookupRequest,
    LookupFailure,
    LookupProvenance,
    LookupResult,
    ProviderKind,
    ReadRequest,
    ReadResponse,
    lookup_id_for,
    offer_id_for,
    request_fingerprint,
    response_hash,
    snapshot_hash_for,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def lodging_query() -> SearchQuery:
    return SearchQuery(
        service=ServiceKind.LODGING,
        start_date=date(2026, 11, 10),
        end_date=date(2026, 11, 12),
        start_time=None,
        party=Party(adults=2, children=1),
    )


def activity_query() -> SearchQuery:
    return SearchQuery(
        service=ServiceKind.ACTIVITY,
        start_date=date(2026, 11, 11),
        end_date=None,
        start_time="07:30",
        party=Party(adults=2, children=0),
    )


def lodging_offer(**changes) -> OfferSnapshot:
    base = OfferSnapshot(
        offer_id="offer:placeholder",
        lookup_id="lookup:placeholder",
        service=ServiceKind.LODGING,
        provider_ref="cloudbeds.room.101.rate.standard",
        public_label="Quarto nº 2",
        start_date=date(2026, 11, 10),
        end_date=date(2026, 11, 12),
        start_time=None,
        party=Party(adults=2, children=1),
        total=Money(amount=Decimal("420.00"), currency="BRL"),
        available=True,
    )
    return replace(base, **changes)


class ReadBoundaryTypeTests(unittest.TestCase):
    def test_read_request_is_get_only_relative_and_canonical(self) -> None:
        request = ReadRequest(
            method="GET",
            path="/api/v1.3/getAvailableRoomTypes",
            query=(("children", "0"), ("adults", "2")),
        )
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.query, (("adults", "2"), ("children", "0")))
        with self.assertRaises(ValueError):
            ReadRequest(method="POST", path="/write", query=())
        with self.assertRaises(ValueError):
            ReadRequest(method="GET", path="https://provider.invalid/x", query=())
        with self.assertRaises(ValueError):
            ReadRequest(method="GET", path="api/no-leading-slash", query=())
        with self.assertRaises(ValueError):
            ReadRequest(method="GET", path="/x", query=(("a", "1"), ("a", "2")))

    def test_request_fingerprint_is_order_independent_for_query(self) -> None:
        left = ReadRequest(method="GET", path="/x", query=(("a", "1"), ("b", "2")))
        right = ReadRequest(method="GET", path="/x", query=(("b", "2"), ("a", "1")))
        self.assertEqual(request_fingerprint(left), request_fingerprint(right))
        self.assertNotEqual(
            request_fingerprint(left),
            request_fingerprint(ReadRequest(method="GET", path="/y", query=left.query)),
        )

    def test_provider_requests_require_the_closed_service_and_internal_id(self) -> None:
        cloudbeds = CloudbedsLookupRequest(
            property_id="property.42",
            query=lodging_query(),
        )
        bokun = BokunLookupRequest(
            product_id="913776",
            query=activity_query(),
        )
        self.assertEqual(cloudbeds.property_id, "property.42")
        self.assertEqual(bokun.product_id, "913776")
        with self.assertRaises(ValueError):
            CloudbedsLookupRequest(property_id="property.42", query=activity_query())
        with self.assertRaises(ValueError):
            BokunLookupRequest(product_id="Mixila 1D", query=activity_query())
        with self.assertRaises(ValueError):
            BokunLookupRequest(product_id="913776", query=lodging_query())

    def test_response_and_provenance_require_exact_types_and_hashes(self) -> None:
        response = ReadResponse(status_code=200, body={"data": []})
        digest = response_hash(response)
        provenance = LookupProvenance(
            provider=ProviderKind.CLOUDBEDS,
            request_fingerprints=("a" * 64, "b" * 64),
            response_hashes=(digest, "c" * 64),
        )
        self.assertEqual(provenance.provider, ProviderKind.CLOUDBEDS)
        with self.assertRaises(ValueError):
            ReadResponse(status_code=True, body={})
        with self.assertRaises(ValueError):
            LookupProvenance(
                provider=ProviderKind.CLOUDBEDS,
                request_fingerprints=("not-a-hash",),
                response_hashes=(digest,),
            )


class OpaqueIdentityTests(unittest.TestCase):
    def test_label_and_lookup_provenance_do_not_change_offer_id(self) -> None:
        original = lodging_offer()
        relabeled = replace(
            original,
            public_label="QUARTO N° 2",
            lookup_id="lookup:another-observation",
        )
        self.assertEqual(
            offer_id_for(provider=ProviderKind.CLOUDBEDS, offer=original),
            offer_id_for(provider=ProviderKind.CLOUDBEDS, offer=relabeled),
        )

    def test_every_executable_offer_mutation_changes_offer_id(self) -> None:
        original = lodging_offer()
        original_id = offer_id_for(provider=ProviderKind.CLOUDBEDS, offer=original)
        mutations = (
            replace(original, provider_ref="cloudbeds.room.102.rate.standard"),
            replace(original, service=ServiceKind.ACTIVITY),
            replace(original, start_date=date(2026, 11, 11)),
            replace(original, end_date=date(2026, 11, 13)),
            replace(original, start_time="08:00"),
            replace(original, party=Party(adults=3, children=1)),
            replace(
                original,
                total=Money(amount=Decimal("421.00"), currency="BRL"),
            ),
            replace(
                original,
                total=Money(amount=Decimal("420.00"), currency="USD"),
            ),
            replace(original, available=False),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertNotEqual(
                    original_id,
                    offer_id_for(provider=ProviderKind.CLOUDBEDS, offer=mutation),
                )
        self.assertNotEqual(
            original_id,
            offer_id_for(provider=ProviderKind.BOKUN, offer=original),
        )

    def test_snapshot_hash_is_canonical_for_keys_and_response_order(self) -> None:
        first = ReadResponse(
            status_code=200,
            body={"data": [{"b": 2, "a": 1}]},
        )
        same_first = ReadResponse(
            status_code=200,
            body={"data": [{"a": 1, "b": 2}]},
        )
        second = ReadResponse(status_code=200, body={"data": [{"id": "rate.1"}]})
        self.assertEqual(
            snapshot_hash_for((first, second)),
            snapshot_hash_for((second, same_first)),
        )
        changed = ReadResponse(status_code=200, body={"data": [{"id": "rate.2"}]})
        self.assertNotEqual(
            snapshot_hash_for((first, second)),
            snapshot_hash_for((first, changed)),
        )

    def test_lookup_id_binds_provider_query_time_and_responses(self) -> None:
        hashes = ("a" * 64, "b" * 64)
        value = lookup_id_for(
            provider=ProviderKind.CLOUDBEDS,
            query=lodging_query(),
            observed_at=T0,
            response_hashes=hashes,
        )
        self.assertRegex(value, r"^lookup:[a-f0-9]{64}$")
        self.assertNotEqual(
            value,
            lookup_id_for(
                provider=ProviderKind.CLOUDBEDS,
                query=lodging_query(),
                observed_at=T0 + timedelta(seconds=1),
                response_hashes=hashes,
            ),
        )
        self.assertNotEqual(
            value,
            lookup_id_for(
                provider=ProviderKind.BOKUN,
                query=lodging_query(),
                observed_at=T0,
                response_hashes=hashes,
            ),
        )


class LookupResultContractTests(unittest.TestCase):
    def evidence(self, status: LookupStatus, snapshot_hash: str) -> LookupEvidence:
        return LookupEvidence(
            lookup_id="lookup:contract-result",
            service=ServiceKind.LODGING,
            query_signature=lodging_query().signature,
            observed_at=T0,
            expires_at=T0 + timedelta(minutes=5),
            snapshot_hash=snapshot_hash,
            status=status,
        )

    def provenance(self) -> LookupProvenance:
        return LookupProvenance(
            provider=ProviderKind.CLOUDBEDS,
            request_fingerprints=("a" * 64, "b" * 64),
            response_hashes=("c" * 64, "d" * 64),
        )

    def test_positive_result_requires_matching_complete_offers(self) -> None:
        base = lodging_offer(lookup_id="lookup:contract-result")
        offer = replace(
            base,
            offer_id=offer_id_for(provider=ProviderKind.CLOUDBEDS, offer=base),
        )
        provenance = self.provenance()
        result = LookupResult(
            query=lodging_query(),
            evidence=self.evidence(LookupStatus.POSITIVE, provenance.snapshot_hash),
            provenance=provenance,
            offers=(offer,),
        )
        self.assertEqual(result.offers, (offer,))
        with self.assertRaises(ValueError):
            LookupResult(
                query=lodging_query(),
                evidence=self.evidence(LookupStatus.POSITIVE, provenance.snapshot_hash),
                provenance=provenance,
                offers=(),
            )

    def test_negative_and_uncertain_results_fail_closed(self) -> None:
        provenance = self.provenance()
        negative = LookupResult(
            query=lodging_query(),
            evidence=self.evidence(LookupStatus.NEGATIVE, provenance.snapshot_hash),
            provenance=provenance,
            offers=(),
        )
        self.assertEqual(negative.failures, ())
        uncertain = LookupResult(
            query=lodging_query(),
            evidence=self.evidence(LookupStatus.UNCERTAIN, provenance.snapshot_hash),
            provenance=provenance,
            offers=(),
            failures=(LookupFailure(code="provider_error", detail="http_non_2xx"),),
        )
        self.assertTrue(uncertain.failures)
        with self.assertRaises(ValueError):
            LookupResult(
                query=lodging_query(),
                evidence=self.evidence(LookupStatus.NEGATIVE, provenance.snapshot_hash),
                provenance=provenance,
                offers=(),
                failures=uncertain.failures,
            )


if __name__ == "__main__":
    unittest.main()
