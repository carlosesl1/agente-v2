"""Runtime-owned composition of activity groups with executable Bókun reads."""

from __future__ import annotations

from v2_adapters._provider_common import ProviderReadError
from v2_adapters.bokun import BokunReadAdapter
from v2_adapters.bokun_groups import (
    ActivityGroupPolicy,
    GroupLookupResult,
)
from v2_contracts.private_offers import PrivateOfferBinding, PrivateOfferQuery
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest


class GroupEnrichedActivityReadAdapter:
    """Expose one activity read while coordinating two read-only sources."""

    def __init__(
        self,
        *,
        bokun: BokunReadAdapter,
        groups_source: object,
        policy: ActivityGroupPolicy,
    ) -> None:
        if type(bokun) is not BokunReadAdapter:
            raise TypeError("composite activity adapter requires exact BokunReadAdapter")
        if type(policy) is not ActivityGroupPolicy:
            raise TypeError("composite activity adapter requires exact group policy")
        if not hasattr(groups_source, "lookup"):
            raise TypeError("composite activity adapter requires a group lookup source")
        self._bokun = bokun
        self._groups = groups_source
        self._policy = policy

    def _group_lookup(
        self, *, canonical_product_id: str, activity_date
    ) -> GroupLookupResult:
        try:
            result = self._groups.lookup(
                canonical_product_id=canonical_product_id,
                activity_date=activity_date,
            )
        except Exception:
            return GroupLookupResult(
                status="unavailable",
                canonical_product_id=canonical_product_id,
                activity_date=activity_date,
                participant_count=None,
            )
        if type(result) is not GroupLookupResult or (
            result.canonical_product_id != canonical_product_id
            or result.activity_date != activity_date
        ):
            return GroupLookupResult(
                status="unavailable",
                canonical_product_id=canonical_product_id,
                activity_date=activity_date,
                participant_count=None,
            )
        return result

    @staticmethod
    def _enrich(
        observation: ReadObservation,
        groups: GroupLookupResult,
        *,
        solo_group_booking: bool,
    ) -> ReadObservation:
        public = dict(observation.public_payload)
        public.update(
            {
                "group_status": groups.status,
                "existing_group": groups.status == "matched",
                "group_participants": groups.participant_count,
                "solo_group_booking": solo_group_booking,
            }
        )
        return ReadObservation(
            request_hash=observation.request_hash,
            provider=observation.provider,
            observed_at=observation.observed_at,
            expires_at=observation.expires_at,
            public_payload=public,
            private_binding_hash=observation.private_binding_hash,
        )

    def read(self, request: ReadRequest) -> ReadObservation:
        if type(request) is not ReadRequest or request.kind is not ReadKind.ACTIVITY:
            raise TypeError("group-enriched adapter supports only ACTIVITY reads")
        assert request.product_id is not None and request.activity_date is not None
        groups = self._group_lookup(
            canonical_product_id=request.product_id,
            activity_date=request.activity_date,
        )
        adults, children = request.activity_party()
        solo_policy = self._policy.solo_policy(request.product_id)
        restricted_solo = adults + children == 1 and solo_policy is not None
        if restricted_solo and groups.status != "matched":
            observation = self._bokun.read_availability_only(request)
            return self._enrich(observation, groups, solo_group_booking=False)
        if restricted_solo:
            assert solo_policy is not None
            observation = self._bokun.read_with_selection(
                request,
                expected_bokun_product_id=solo_policy.bokun_product_id,
                expected_rate_id=solo_policy.rate_id,
                expected_adult_category_id=solo_policy.adult_category_id,
            )
            return self._enrich(observation, groups, solo_group_booking=True)
        observation = self._bokun.read(request)
        return self._enrich(observation, groups, solo_group_booking=False)

    def resolve(self, query: PrivateOfferQuery) -> PrivateOfferBinding:
        if type(query) is not PrivateOfferQuery or query.service != "activity":
            raise TypeError("group-enriched private resolver requires ACTIVITY")
        assert query.canonical_product_id is not None
        groups = self._group_lookup(
            canonical_product_id=query.canonical_product_id,
            activity_date=query.start_date,
        )
        solo_policy = self._policy.solo_policy(query.canonical_product_id)
        restricted_solo = query.adults + query.children == 1 and solo_policy is not None
        if restricted_solo:
            if groups.status != "matched":
                raise ProviderReadError("activity group revalidation is unavailable")
            assert solo_policy is not None
            return self._bokun.resolve_with_selection(
                query,
                expected_bokun_product_id=solo_policy.bokun_product_id,
                expected_rate_id=solo_policy.rate_id,
                expected_adult_category_id=solo_policy.adult_category_id,
            )
        return self._bokun.resolve(query)


__all__ = ["GroupEnrichedActivityReadAdapter"]
