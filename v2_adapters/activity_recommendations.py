"""Bounded informational activity recommendation reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import re
from typing import Final, Literal

from v2_adapters._provider_common import ProviderReadError, observed_window
from v2_adapters.bokun_groups import (
    ActivityGroupPolicy,
    BokunGroupsSource,
    GroupDateCandidate,
    GroupLookupResult,
)
from v2_adapters.group_enriched_activity import GroupEnrichedActivityReadAdapter
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest


_PRODUCT_RE: Final = re.compile(r"^product:[a-z0-9]+(?:-[a-z0-9]+)*$")
_FOUR_PS: Final = "product:tour-4ps"
_PATI_3D: Final = "product:pati-3d"
_FREQUENT_PRODUCTS: Final = frozenset({_FOUR_PS, _PATI_3D})
_DURATION_DAYS: Final = {
    "product:tour-2ms": 1,
    "product:tour-4ps": 1,
    "product:aguas-claras": 1,
    "product:buracao": 1,
    "product:mixila-1d": 1,
    "product:fumaca-por-cima": 1,
    "product:mandassaia-lapao": 1,
    "product:marimbus": 1,
    "product:sossego": 1,
    "product:mixila-2d": 2,
    "product:pati-2d": 2,
    "product:fumaca-por-baixo-3d": 3,
    "product:pati-3d": 3,
    "product:pati-3d-english": 3,
    "product:pati-4d": 4,
    "product:pati-5d": 5,
}
CandidateSource = Literal["formed_group", "frequent"]


@dataclass(frozen=True, slots=True)
class CandidateQuery:
    canonical_product_id: str
    activity_date: date
    source: CandidateSource

    def __post_init__(self) -> None:
        if (
            type(self.canonical_product_id) is not str
            or _PRODUCT_RE.fullmatch(self.canonical_product_id) is None
        ):
            raise ValueError("candidate product ID is invalid")
        if type(self.activity_date) is not date:
            raise TypeError("candidate activity_date must be an exact date")
        if self.source not in ("formed_group", "frequent"):
            raise ValueError("candidate source is invalid")


def plan_recommendation_candidates(
    *,
    formed_groups: tuple[GroupDateCandidate, ...],
    period_start: date,
    period_end: date,
    max_provider_reads: int = 64,
) -> tuple[CandidateQuery, ...]:
    """Plan deterministic formed-group-first reads without suitability decisions."""

    if (
        type(period_start) is not date
        or type(period_end) is not date
        or period_end < period_start
        or (period_end - period_start).days >= 14
    ):
        raise ValueError("recommendation period is invalid")
    if type(max_provider_reads) is not int or max_provider_reads < 1:
        raise ValueError("max_provider_reads must be a positive exact integer")
    if type(formed_groups) is not tuple or any(
        type(candidate) is not GroupDateCandidate for candidate in formed_groups
    ):
        raise TypeError("formed_groups must contain exact group candidates")

    formed_pairs = {
        (candidate.canonical_product_id, candidate.activity_date)
        for candidate in formed_groups
        if period_start <= candidate.activity_date <= period_end
    }
    formed = tuple(
        CandidateQuery(product_id, activity_date, source="formed_group")
        for product_id, activity_date in sorted(
            formed_pairs, key=lambda pair: (pair[1], pair[0])
        )
    )
    if len(formed) > max_provider_reads:
        raise ValueError("formed groups exceed the provider-read cap")

    planned = list(formed)
    seen = set(formed_pairs)
    day = period_start
    while day <= period_end and len(planned) < max_provider_reads:
        for product_id in (_FOUR_PS, _PATI_3D):
            if product_id == _PATI_3D and day + timedelta(days=2) > period_end:
                continue
            pair = (product_id, day)
            if pair not in seen:
                planned.append(CandidateQuery(*pair, source="frequent"))
                seen.add(pair)
                if len(planned) == max_provider_reads:
                    break
        day += timedelta(days=1)
    return tuple(planned)


def _candidate_projection(
    observation: ReadObservation,
    *,
    request: ReadRequest,
    group: GroupLookupResult,
) -> dict[str, object] | None:
    if observation.request_hash != request.canonical_hash() or observation.provider != "bokun":
        raise ValueError("candidate observation is not bound to its request")
    public = observation.public_payload
    expected_date = request.activity_date.isoformat()
    if (
        public.get("product_id") != request.product_id
        or public.get("activity_date") != expected_date
        or public.get("group_status") != group.status
        or public.get("existing_group") is not (group.status == "matched")
    ):
        raise ValueError("candidate observation binding is invalid")
    available = public.get("available")
    if type(available) is not bool:
        raise ValueError("candidate availability is invalid")
    if not available:
        return None
    product_name = public.get("product_public_name")
    amount = public.get("total_amount")
    currency = public.get("currency")
    if (
        type(product_name) is not str
        or not product_name
        or product_name != product_name.strip()
        or type(amount) is not str
        or re.fullmatch(r"(?:0|[1-9][0-9]*)\.[0-9]{2}", amount) is None
        or type(currency) is not str
        or re.fullmatch(r"[A-Z]{3}", currency) is None
    ):
        raise ValueError("candidate public quote is invalid")
    assert request.product_id is not None and request.activity_date is not None
    return {
        "product_id": request.product_id,
        "product_public_name": product_name,
        "activity_date": request.activity_date.isoformat(),
        "duration_days": _DURATION_DAYS[request.product_id],
        "total_amount": amount,
        "currency": currency,
        "group_status": group.status,
        "existing_group": group.status == "matched",
        "frequent_alternative": request.product_id in _FREQUENT_PRODUCTS,
    }


def _aggregate_evidence_hash(
    request_hash: str, candidate_hashes: tuple[str, ...]
) -> str:
    evidence = b"\0".join(
        value.encode("ascii") for value in (request_hash, *candidate_hashes)
    )
    return hashlib.sha256(
        b"v2-activity-recommendation-evidence-v1\0" + evidence
    ).hexdigest()


class ActivityRecommendationReadAdapter:
    """Compose one bounded group discovery with informational Bókun fan-out."""

    def __init__(
        self,
        *,
        groups: BokunGroupsSource,
        policy: ActivityGroupPolicy,
        activity: GroupEnrichedActivityReadAdapter,
        clock: object,
        ttl: timedelta,
        max_group_candidates: int = 24,
        max_provider_reads: int = 64,
    ) -> None:
        if not callable(getattr(groups, "discover", None)):
            raise TypeError("recommendation adapter requires a group discovery source")
        if type(policy) is not ActivityGroupPolicy:
            raise TypeError("recommendation adapter requires an exact group policy")
        if not callable(getattr(activity, "read_with_group_context", None)):
            raise TypeError("recommendation adapter requires a source-free activity reader")
        if not hasattr(clock, "now"):
            raise TypeError("recommendation clock must implement now")
        if type(ttl) is not timedelta or ttl <= timedelta(0):
            raise ValueError("recommendation ttl must be a positive exact timedelta")
        if type(max_group_candidates) is not int or max_group_candidates < 1:
            raise ValueError("max_group_candidates must be a positive exact integer")
        if type(max_provider_reads) is not int or max_provider_reads < 1:
            raise ValueError("max_provider_reads must be a positive exact integer")
        self._groups = groups
        self._policy = policy
        self._activity = activity
        self._clock = clock
        self._ttl = ttl
        self._max_group_candidates = max_group_candidates
        self._max_provider_reads = max_provider_reads

    def read(self, request: ReadRequest) -> ReadObservation:
        if (
            type(request) is not ReadRequest
            or request.kind is not ReadKind.ACTIVITY_RECOMMENDATION
        ):
            raise TypeError("recommendation adapter supports only ACTIVITY_RECOMMENDATION")
        assert request.period_start is not None and request.period_end is not None
        assert request.adults is not None and request.children is not None
        try:
            discovered = self._groups.discover(
                period_start=request.period_start,
                period_end=request.period_end,
                max_candidates=self._max_group_candidates,
            )
        except Exception:
            discovered = None
        if discovered is not None and (
            type(discovered) is not tuple
            or any(type(item) is not GroupDateCandidate for item in discovered)
        ):
            raise ValueError("group discovery returned an invalid result")
        formed_groups = discovered or ()
        planned = plan_recommendation_candidates(
            formed_groups=formed_groups,
            period_start=request.period_start,
            period_end=request.period_end,
            max_provider_reads=self._max_provider_reads,
        )
        matched = {
            (item.canonical_product_id, item.activity_date): GroupLookupResult(
                status="matched",
                canonical_product_id=item.canonical_product_id,
                activity_date=item.activity_date,
                participant_count=item.group_participants,
            )
            for item in formed_groups
        }
        source_status = "unavailable" if discovered is None else "available"
        candidates: list[dict[str, object]] = []
        evidence_hashes: list[str] = []
        for index, query in enumerate(planned):
            child_request = ReadRequest(
                request_id=f"{request.request_id}:candidate:{index}",
                kind=ReadKind.ACTIVITY,
                product_id=query.canonical_product_id,
                activity_date=query.activity_date,
                adults=request.adults,
                children=request.children,
                locale=request.locale,
            )
            group = matched.get((query.canonical_product_id, query.activity_date))
            if group is None:
                group = GroupLookupResult(
                    status="unavailable" if discovered is None else "not_matched",
                    canonical_product_id=query.canonical_product_id,
                    activity_date=query.activity_date,
                    participant_count=None,
                )
            try:
                observation = self._activity.read_with_group_context(
                    child_request, group=group
                )
            except ProviderReadError:
                continue
            if type(observation) is not ReadObservation:
                raise TypeError("candidate activity reader returned an invalid observation")
            evidence_hashes.append(observation.private_binding_hash)
            projection = _candidate_projection(
                observation,
                request=child_request,
                group=group,
            )
            if projection is not None:
                candidates.append(projection)

        observed_at, expires_at = observed_window(self._clock, self._ttl)
        request_hash = request.canonical_hash()
        return ReadObservation(
            request_hash=request_hash,
            provider="bokun",
            observed_at=observed_at,
            expires_at=expires_at,
            public_payload={
                "recommendation_period": {
                    "start": request.period_start.isoformat(),
                    "end": request.period_end.isoformat(),
                },
                "party": {"adults": request.adults, "children": request.children},
                "group_source_status": source_status,
                "candidates": candidates,
                "candidate_count": len(candidates),
            },
            private_binding_hash=_aggregate_evidence_hash(
                request_hash, tuple(evidence_hashes)
            ),
        )


__all__ = [
    "ActivityRecommendationReadAdapter",
    "CandidateQuery",
    "plan_recommendation_candidates",
]
