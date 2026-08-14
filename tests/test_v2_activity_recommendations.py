from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from v2_adapters._provider_common import ProviderReadError
from v2_adapters.activity_recommendations import (
    ActivityRecommendationReadAdapter,
    CandidateQuery,
    plan_recommendation_candidates,
)
from v2_adapters.bokun import BokunReadAdapter
from v2_adapters.bokun_groups import (
    GroupDateCandidate,
    GroupLookupResult,
    load_activity_group_policy,
)
from v2_adapters.group_enriched_activity import GroupEnrichedActivityReadAdapter
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest


START = date(2026, 9, 10)


def _group(product_id: str, day: int, participants: int = 3) -> GroupDateCandidate:
    return GroupDateCandidate(product_id, date(2026, 9, day), participants)


def test_planning_orders_formed_groups_before_frequent_candidates_deterministically() -> None:
    planned = plan_recommendation_candidates(
        formed_groups=(
            _group("product:marimbus", 11),
            _group("product:marimbus", 10),
        ),
        period_start=START,
        period_end=START + timedelta(days=2),
        max_provider_reads=20,
    )

    assert planned == (
        CandidateQuery("product:marimbus", date(2026, 9, 10), source="formed_group"),
        CandidateQuery("product:marimbus", date(2026, 9, 11), source="formed_group"),
        CandidateQuery("product:tour-4ps", date(2026, 9, 10), source="frequent"),
        CandidateQuery("product:pati-3d", date(2026, 9, 10), source="frequent"),
        CandidateQuery("product:tour-4ps", date(2026, 9, 11), source="frequent"),
        CandidateQuery("product:tour-4ps", date(2026, 9, 12), source="frequent"),
    )


def test_planning_deduplicates_frequent_products_already_backed_by_groups() -> None:
    planned = plan_recommendation_candidates(
        formed_groups=(
            _group("product:tour-4ps", 10),
            _group("product:pati-3d", 10),
        ),
        period_start=START,
        period_end=START,
        max_provider_reads=10,
    )

    assert planned == (
        CandidateQuery("product:pati-3d", START, source="formed_group"),
        CandidateQuery("product:tour-4ps", START, source="formed_group"),
    )


def test_planning_excludes_pati_starts_that_do_not_fit_three_days() -> None:
    planned = plan_recommendation_candidates(
        formed_groups=(),
        period_start=START,
        period_end=START + timedelta(days=2),
        max_provider_reads=20,
    )

    assert planned == (
        CandidateQuery("product:tour-4ps", START, source="frequent"),
        CandidateQuery("product:pati-3d", START, source="frequent"),
        CandidateQuery("product:tour-4ps", START + timedelta(days=1), source="frequent"),
        CandidateQuery("product:tour-4ps", START + timedelta(days=2), source="frequent"),
    )


def test_planning_accepts_exactly_fourteen_calendar_dates_and_rejects_longer() -> None:
    planned = plan_recommendation_candidates(
        formed_groups=(),
        period_start=START,
        period_end=START + timedelta(days=13),
        max_provider_reads=64,
    )
    assert len(planned) == 26

    with pytest.raises(ValueError, match="period"):
        plan_recommendation_candidates(
            formed_groups=(),
            period_start=START,
            period_end=START + timedelta(days=14),
            max_provider_reads=64,
        )


def test_planning_rejects_formed_group_overflow_instead_of_truncating() -> None:
    with pytest.raises(ValueError, match="provider-read"):
        plan_recommendation_candidates(
            formed_groups=(
                _group("product:marimbus", 10),
                _group("product:buracao", 10),
                _group("product:sossego", 10),
            ),
            period_start=START,
            period_end=START,
            max_provider_reads=2,
        )


def test_planning_stops_frequent_fanout_at_provider_read_cap() -> None:
    planned = plan_recommendation_candidates(
        formed_groups=(_group("product:marimbus", 10),),
        period_start=START,
        period_end=START + timedelta(days=2),
        max_provider_reads=3,
    )

    assert planned == (
        CandidateQuery("product:marimbus", START, source="formed_group"),
        CandidateQuery("product:tour-4ps", START, source="frequent"),
        CandidateQuery("product:pati-3d", START, source="frequent"),
    )


@pytest.mark.parametrize(
    "kwargs",
    (
        {"canonical_product_id": "not-canonical"},
        {"activity_date": "2026-09-10"},
        {"source": "profile_score"},
    ),
)
def test_planning_candidate_query_is_frozen_closed_and_exact(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "canonical_product_id": "product:marimbus",
        "activity_date": START,
        "source": "formed_group",
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError)):
        CandidateQuery(**values)  # type: ignore[arg-type]

    candidate = CandidateQuery("product:marimbus", START, source="formed_group")
    with pytest.raises(FrozenInstanceError):
        candidate.source = "frequent"  # type: ignore[misc]
    assert not hasattr(candidate, "__dict__")


def test_planning_candidate_query_rejects_string_enum_source() -> None:
    class CandidateSourceLookalike(StrEnum):
        FREQUENT = "frequent"

    with pytest.raises(TypeError, match="exact string"):
        CandidateQuery(
            "product:marimbus",
            START,
            source=CandidateSourceLookalike.FREQUENT,  # type: ignore[arg-type]
        )


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
FORBIDDEN_CANDIDATE_KEYS = {
    "offer_id",
    "choice_ref",
    "private_binding_hash",
    "start_time",
    "start_time_id",
    "rate_id",
    "category_id",
    "adult_pricing_category_id",
    "source_url",
    "raw_row",
    "names",
    "comments",
    "guide",
    "guide_data",
}


def _policy():
    return load_activity_group_policy(
        ROOT / "config/v2_activity_group_policy.json",
        ROOT / "config/v2_bokun_product_map.json",
    )


def _recommendation_request(
    *,
    adults: int = 2,
    children: int = 0,
    days: int = 3,
    request_id: str = "read:recommendation:1",
) -> ReadRequest:
    return ReadRequest(
        request_id=request_id,
        kind=ReadKind.ACTIVITY_RECOMMENDATION,
        period_start=START,
        period_end=START + timedelta(days=days - 1),
        adults=adults,
        children=children,
        locale="pt-BR",
    )


class RecordingDiscovery:
    def __init__(
        self,
        result: tuple[GroupDateCandidate, ...] | None,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.result = result
        self.events = events
        self.calls: list[tuple[date, date, int]] = []

    def discover(
        self, *, period_start: date, period_end: date, max_candidates: int
    ) -> tuple[GroupDateCandidate, ...] | None:
        self.calls.append((period_start, period_end, max_candidates))
        if self.events is not None:
            self.events.append("groups")
        return self.result


class RecordingActivity:
    def __init__(
        self,
        *,
        events: list[str] | None = None,
        unavailable: set[tuple[str, date]] | None = None,
        errors: dict[tuple[str, date], Exception] | None = None,
    ) -> None:
        self.events = events
        self.unavailable = unavailable or set()
        self.errors = errors or {}
        self.calls: list[tuple[ReadRequest, GroupLookupResult]] = []
        self.ordinary_calls: list[ReadRequest] = []

    def read(self, request: ReadRequest) -> ReadObservation:
        self.ordinary_calls.append(request)
        raise AssertionError("recommendation fan-out must not use ordinary read")

    def read_with_group_context(
        self, request: ReadRequest, *, group: GroupLookupResult
    ) -> ReadObservation:
        self.calls.append((request, group))
        if self.events is not None:
            self.events.append("bokun")
        assert request.product_id is not None and request.activity_date is not None
        key = (request.product_id, request.activity_date)
        error = self.errors.get(key)
        if error is not None:
            raise error
        available = key not in self.unavailable
        private_hash = hashlib.sha256(request.to_canonical_bytes()).hexdigest()
        duration = {
            "product:pati-3d": 3,
            "product:pati-4d": 4,
            "product:pati-5d": 5,
        }.get(request.product_id, 1)
        return ReadObservation(
            request_hash=request.canonical_hash(),
            provider="bokun",
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            public_payload={
                "product_id": request.product_id,
                "product_public_name": request.product_id.removeprefix("product:"),
                "activity_date": request.activity_date.isoformat(),
                "duration_days": duration,
                "total_amount": "300.00",
                "currency": "BRL",
                "available": available,
                "group_status": group.status,
                "existing_group": group.status == "matched",
                "offer_id": "offer:" + private_hash,
                "start_time": "08:00",
            },
            private_binding_hash=private_hash,
        )


def _adapter(*, groups, activity, max_group_candidates=24, max_provider_reads=64):
    return ActivityRecommendationReadAdapter(
        groups=groups,
        policy=_policy(),
        activity=activity,
        clock=SimpleNamespace(now=lambda: NOW),
        ttl=timedelta(minutes=10),
        max_group_candidates=max_group_candidates,
        max_provider_reads=max_provider_reads,
    )


def test_composed_recommendation_discovers_once_then_uses_only_source_free_reads() -> None:
    events: list[str] = []
    groups = RecordingDiscovery((_group("product:marimbus", 10, 4),), events=events)
    activity = RecordingActivity(events=events)
    request = _recommendation_request(adults=2, children=1)

    observation = _adapter(groups=groups, activity=activity).read(request)

    assert groups.calls == [(START, START + timedelta(days=2), 24)]
    assert events[0] == "groups"
    assert events[1:] == ["bokun"] * len(activity.calls)
    assert activity.ordinary_calls == []
    assert [
        (
            call.product_id,
            call.activity_date,
            call.adults,
            call.children,
            call.locale,
            group.status,
        )
        for call, group in activity.calls
    ] == [
        ("product:marimbus", START, 2, 1, "pt-BR", "matched"),
        ("product:tour-4ps", START, 2, 1, "pt-BR", "not_matched"),
        ("product:pati-3d", START, 2, 1, "pt-BR", "not_matched"),
        (
            "product:tour-4ps",
            START + timedelta(days=1),
            2,
            1,
            "pt-BR",
            "not_matched",
        ),
        (
            "product:tour-4ps",
            START + timedelta(days=2),
            2,
            1,
            "pt-BR",
            "not_matched",
        ),
    ]
    assert [call.request_id for call, _ in activity.calls] == [
        f"{request.request_id}:candidate:{index}" for index in range(5)
    ]
    assert observation.request_hash == request.canonical_hash()
    assert observation.provider == "bokun"
    assert observation.observed_at == NOW
    assert observation.expires_at == NOW + timedelta(minutes=10)
    assert observation.public_payload == {
        "recommendation_period": {
            "start": START.isoformat(),
            "end": (START + timedelta(days=2)).isoformat(),
        },
        "party": {"adults": 2, "children": 1},
        "group_source_status": "available",
        "candidates": [
            {
                "product_id": "product:marimbus",
                "product_public_name": "marimbus",
                "activity_date": START.isoformat(),
                "duration_days": 1,
                "total_amount": "300.00",
                "currency": "BRL",
                "group_status": "matched",
                "existing_group": True,
                "frequent_alternative": False,
            },
            {
                "product_id": "product:tour-4ps",
                "product_public_name": "tour-4ps",
                "activity_date": START.isoformat(),
                "duration_days": 1,
                "total_amount": "300.00",
                "currency": "BRL",
                "group_status": "not_matched",
                "existing_group": False,
                "frequent_alternative": True,
            },
            {
                "product_id": "product:pati-3d",
                "product_public_name": "pati-3d",
                "activity_date": START.isoformat(),
                "duration_days": 3,
                "total_amount": "300.00",
                "currency": "BRL",
                "group_status": "not_matched",
                "existing_group": False,
                "frequent_alternative": True,
            },
            {
                "product_id": "product:tour-4ps",
                "product_public_name": "tour-4ps",
                "activity_date": (START + timedelta(days=1)).isoformat(),
                "duration_days": 1,
                "total_amount": "300.00",
                "currency": "BRL",
                "group_status": "not_matched",
                "existing_group": False,
                "frequent_alternative": True,
            },
            {
                "product_id": "product:tour-4ps",
                "product_public_name": "tour-4ps",
                "activity_date": (START + timedelta(days=2)).isoformat(),
                "duration_days": 1,
                "total_amount": "300.00",
                "currency": "BRL",
                "group_status": "not_matched",
                "existing_group": False,
                "frequent_alternative": True,
            },
        ],
        "candidate_count": 5,
    }
    assert all(
        FORBIDDEN_CANDIDATE_KEYS.isdisjoint(candidate)
        for candidate in observation.public_payload["candidates"]
    )
    candidate_hashes = tuple(
        hashlib.sha256(call.to_canonical_bytes()).hexdigest()
        for call, _ in activity.calls
    )
    expected_evidence = hashlib.sha256(
        b"v2-activity-recommendation-evidence-v1\0"
        + b"\0".join(
            value.encode("ascii")
            for value in (request.canonical_hash(), *candidate_hashes)
        )
    ).hexdigest()
    assert observation.private_binding_hash == expected_evidence
    assert observation.private_binding_hash not in candidate_hashes


def test_child_id_preflight_accepts_parent_at_worst_index_boundary() -> None:
    max_provider_reads = 64
    suffix = f":candidate:{max_provider_reads - 1}"
    parent_id = "r" * (256 - len(suffix))
    groups = RecordingDiscovery(())
    activity = RecordingActivity()

    _adapter(
        groups=groups,
        activity=activity,
        max_provider_reads=max_provider_reads,
    ).read(_recommendation_request(days=1, request_id=parent_id))

    assert groups.calls == [(START, START, 24)]
    assert activity.calls[0][0].request_id == f"{parent_id}:candidate:0"


def test_child_id_preflight_rejects_oversized_parent_before_discovery() -> None:
    max_provider_reads = 64
    suffix = f":candidate:{max_provider_reads - 1}"
    parent_id = "r" * (257 - len(suffix))
    groups = RecordingDiscovery(())
    activity = RecordingActivity()

    with pytest.raises(ValueError, match="child request IDs"):
        _adapter(
            groups=groups,
            activity=activity,
            max_provider_reads=max_provider_reads,
        ).read(_recommendation_request(days=1, request_id=parent_id))

    assert groups.calls == []
    assert activity.calls == []


def test_unavailable_group_source_still_projects_available_frequent_candidates() -> None:
    groups = RecordingDiscovery(None)
    activity = RecordingActivity()
    observation = _adapter(groups=groups, activity=activity).read(
        _recommendation_request(adults=2, days=3)
    )

    assert groups.calls == [(START, START + timedelta(days=2), 24)]
    assert observation.public_payload["group_source_status"] == "unavailable"
    assert observation.public_payload["candidate_count"] == 4
    assert all(
        candidate["group_status"] == "unavailable"
        and candidate["existing_group"] is False
        for candidate in observation.public_payload["candidates"]
    )


def test_candidate_unavailability_observation_is_omitted() -> None:
    pati = ("product:pati-3d", START)
    activity = RecordingActivity(unavailable={pati})

    observation = _adapter(
        groups=RecordingDiscovery(()), activity=activity
    ).read(_recommendation_request(days=3))

    assert observation.public_payload["candidate_count"] == 3
    assert {
        (candidate["product_id"], candidate["activity_date"])
        for candidate in observation.public_payload["candidates"]
    } == {
        ("product:tour-4ps", START.isoformat()),
        ("product:tour-4ps", (START + timedelta(days=1)).isoformat()),
        ("product:tour-4ps", (START + timedelta(days=2)).isoformat()),
    }


def test_structural_provider_read_error_fails_the_aggregate() -> None:
    def divergent_product_transport(
        operation: str, payload: dict[str, object]
    ) -> dict[str, object]:
        assert operation == "activity"
        assert payload["product_id"] == "product:tour-4ps"
        return {"product_id": "product:marimbus"}

    bokun = BokunReadAdapter(
        transport=divergent_product_transport,
        clock=SimpleNamespace(now=lambda: NOW),
        ttl=timedelta(minutes=5),
    )
    activity = GroupEnrichedActivityReadAdapter(
        bokun=bokun,
        groups_source=SimpleNamespace(lookup=lambda **_kwargs: None),
        policy=_policy(),
    )
    groups = RecordingDiscovery(())

    with pytest.raises(ProviderReadError, match="canonical product binding"):
        _adapter(groups=groups, activity=activity).read(
            _recommendation_request(days=1)
        )

    assert groups.calls == [(START, START, 24)]


@pytest.mark.parametrize("error", (TypeError("structural"), ValueError("binding")))
def test_structural_or_binding_candidate_errors_fail_the_aggregate(error: Exception) -> None:
    activity = RecordingActivity(errors={("product:tour-4ps", START): error})

    with pytest.raises(type(error), match=str(error)):
        _adapter(groups=RecordingDiscovery(()), activity=activity).read(
            _recommendation_request(days=1)
        )


def test_recommendation_preserves_two_plus_without_groups_but_omits_restricted_solo() -> None:
    two_plus = RecordingActivity()
    available = _adapter(
        groups=RecordingDiscovery((_group("product:marimbus", 10),)),
        activity=two_plus,
    ).read(_recommendation_request(adults=2, days=1))
    assert available.public_payload["candidate_count"] == 2

    solo = RecordingActivity(unavailable={("product:marimbus", START)})
    restricted = _adapter(
        groups=RecordingDiscovery((_group("product:marimbus", 10),)),
        activity=solo,
    ).read(_recommendation_request(adults=1, days=1))
    assert {
        candidate["product_id"]
        for candidate in restricted.public_payload["candidates"]
    } == {"product:tour-4ps"}


def test_formed_group_overflow_fails_before_any_provider_read() -> None:
    activity = RecordingActivity()
    groups = RecordingDiscovery(
        (
            _group("product:marimbus", 10),
            _group("product:buracao", 10),
            _group("product:sossego", 10),
        )
    )

    with pytest.raises(ValueError, match="provider-read"):
        _adapter(groups=groups, activity=activity, max_provider_reads=2).read(
            _recommendation_request(days=1)
        )
    assert len(groups.calls) == 1
    assert activity.calls == []
