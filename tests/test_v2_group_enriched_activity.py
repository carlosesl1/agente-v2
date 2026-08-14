from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from v2_adapters._provider_common import ProviderReadError
from v2_adapters.bokun import BokunReadAdapter
from v2_adapters.bokun_groups import (
    GroupLookupResult,
    load_activity_group_policy,
)
from v2_contracts.private_offers import PrivateOfferQuery
from v2_contracts.providers import ReadKind, ReadRequest


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
ACTIVITY_DATE = date(2026, 11, 18)


def _policy():
    return load_activity_group_policy(
        ROOT / "config/v2_activity_group_policy.json",
        ROOT / "config/v2_bokun_product_map.json",
    )


class RecordingGroups:
    def __init__(
        self,
        status: str = "matched",
        *,
        participants: int | None = 3,
        events: list[str] | None = None,
        fail: bool = False,
    ) -> None:
        self.status = status
        self.participants = participants if status == "matched" else None
        self.events = events
        self.fail = fail
        self.calls: list[tuple[str, date]] = []

    def lookup(
        self, *, canonical_product_id: str, activity_date: date
    ) -> GroupLookupResult:
        self.calls.append((canonical_product_id, activity_date))
        if self.events is not None:
            self.events.append("groups")
        if self.fail:
            raise RuntimeError("synthetic sheet outage with private row content")
        return GroupLookupResult(
            status=self.status,  # type: ignore[arg-type]
            canonical_product_id=canonical_product_id,
            activity_date=activity_date,
            participant_count=self.participants,
        )


class RecordingBokunTransport:
    def __init__(
        self,
        *,
        events: list[str] | None = None,
        fail: bool = False,
    ) -> None:
        self.events = events
        self.fail = fail
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(
        self, operation: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((operation, dict(payload)))
        if self.events is not None:
            self.events.append("bokun")
        if self.fail:
            raise ProviderReadError("synthetic Bókun failure")
        if payload.get("availability_only") is True:
            return {
                "product_id": payload["product_id"],
                "bokun_product_id": "913372",
                "product_public_name": "Buracão",
                "total_amount": "0.00",
                "currency": "BRL",
                "available": False,
            }
        expected_rate = payload.get("expected_rate_id")
        expected_category = payload.get("expected_adult_category_id")
        is_exact = expected_rate is not None or expected_category is not None
        return {
            "product_id": payload["product_id"],
            "bokun_product_id": (
                payload["expected_bokun_product_id"] if is_exact else "913372"
            ),
            "start_time_id": "start-buracao",
            "start_time": "07:30",
            "rate_id": expected_rate if is_exact else "normal-rate",
            "adult_pricing_category_id": (
                expected_category if is_exact else "normal-adult"
            ),
            "product_public_name": "Buracão",
            "base_amount": "300.00",
            "booking_fee_amount": "4.50",
            "total_amount": "304.50",
            "currency": "BRL",
            "price_includes_booking_fee": True,
            "available": True,
        }


def _bokun(transport: RecordingBokunTransport) -> BokunReadAdapter:
    return BokunReadAdapter(
        transport=transport,
        clock=SimpleNamespace(now=lambda: NOW),
        ttl=timedelta(minutes=5),
    )


def _request(*, participants: int, product_id: str = "product:buracao") -> ReadRequest:
    return ReadRequest(
        request_id=f"read:{product_id.removeprefix('product:')}:{participants}",
        kind=ReadKind.ACTIVITY,
        product_id=product_id,
        activity_date=ACTIVITY_DATE,
        participants=participants,
        locale="pt-BR",
    )


def _query(request: ReadRequest, observation) -> PrivateOfferQuery:
    payload = observation.public_payload
    return PrivateOfferQuery(
        service="activity",
        offer_id=payload["offer_id"],
        request_hash=request.query_hash(),
        binding_hash=observation.private_binding_hash,
        canonical_product_id=request.product_id,
        start_date=request.activity_date,
        end_date=None,
        start_time=payload.get("start_time"),
        adults=request.activity_party()[0],
        children=request.activity_party()[1],
        total_amount=payload["total_amount"],
        currency=payload["currency"],
        available=payload["available"],
    )


def _composite(*, bokun, groups_source, policy):
    from v2_adapters.group_enriched_activity import (
        GroupEnrichedActivityReadAdapter,
    )

    return GroupEnrichedActivityReadAdapter(
        bokun=bokun,
        groups_source=groups_source,
        policy=policy,
    )


@pytest.mark.parametrize(
    ("status", "group_participants", "existing_group"),
    (
        ("matched", 4, True),
        ("not_matched", None, False),
        ("unavailable", None, False),
    ),
)
def test_two_plus_composes_group_first_and_preserves_bokun_offer(
    status: str,
    group_participants: int | None,
    existing_group: bool,
) -> None:
    events: list[str] = []
    groups = RecordingGroups(
        status,
        participants=group_participants,
        events=events,
    )
    transport = RecordingBokunTransport(events=events)
    bokun = _bokun(transport)
    request = _request(participants=2)
    baseline = bokun.read(request)
    transport.calls.clear()
    events.clear()

    observation = _composite(
        bokun=bokun,
        groups_source=groups,
        policy=_policy(),
    ).read(request)

    assert events == ["groups", "bokun"]
    assert groups.calls == [("product:buracao", ACTIVITY_DATE)]
    assert len(transport.calls) == 1
    assert observation.request_hash == baseline.request_hash
    assert observation.observed_at == baseline.observed_at
    assert observation.expires_at == baseline.expires_at
    assert observation.private_binding_hash == baseline.private_binding_hash
    for field in (
        "offer_id",
        "product_id",
        "activity_date",
        "adults",
        "children",
        "participants",
        "product_public_name",
        "start_time",
        "total_amount",
        "currency",
        "available",
        "base_amount",
        "booking_fee_amount",
        "price_includes_booking_fee",
    ):
        assert observation.public_payload[field] == baseline.public_payload[field]
    assert observation.public_payload | {} == {
        **baseline.public_payload,
        "group_status": status,
        "existing_group": existing_group,
        "group_participants": group_participants,
        "solo_group_booking": False,
    }


def test_group_exception_degrades_but_bokun_exception_remains_a_read_failure() -> None:
    request = _request(participants=2)
    transport = RecordingBokunTransport()
    degraded = _composite(
        bokun=_bokun(transport),
        groups_source=RecordingGroups(fail=True),
        policy=_policy(),
    ).read(request)

    assert degraded.public_payload["group_status"] == "unavailable"
    assert degraded.public_payload["available"] is True

    events: list[str] = []
    with pytest.raises(ProviderReadError, match="Bókun"):
        _composite(
            bokun=_bokun(RecordingBokunTransport(events=events, fail=True)),
            groups_source=RecordingGroups(events=events),
            policy=_policy(),
        ).read(request)
    assert events == ["groups", "bokun"]


@pytest.mark.parametrize("status", ("not_matched", "unavailable"))
def test_restricted_solo_miss_inspects_bokun_without_quote_or_private_selector(
    status: str,
) -> None:
    transport = RecordingBokunTransport()
    request = _request(participants=1)

    observation = _composite(
        bokun=_bokun(transport),
        groups_source=RecordingGroups(status),
        policy=_policy(),
    ).read(request)

    assert len(transport.calls) == 1
    operation, payload = transport.calls[0]
    assert operation == "activity"
    assert payload["availability_only"] is True
    assert "expected_rate_id" not in payload
    assert "expected_adult_category_id" not in payload
    assert observation.public_payload == {
        "product_id": "product:buracao",
        "activity_date": ACTIVITY_DATE.isoformat(),
        "adults": 1,
        "children": 0,
        "participants": 1,
        "product_public_name": "Buracão",
        "total_amount": "0.00",
        "currency": "BRL",
        "available": False,
        "group_status": status,
        "existing_group": False,
        "group_participants": None,
        "solo_group_booking": False,
    }
    assert not any(
        name in observation.public_payload
        for name in (
            "bokun_product_id",
            "rate_id",
            "adult_pricing_category_id",
            "expected_rate_id",
        )
    )


def test_restricted_solo_match_uses_exact_closed_selection_and_actual_binding() -> None:
    transport = RecordingBokunTransport()
    request = _request(participants=1)
    policy = _policy()
    solo = policy.solo_policy("product:buracao")
    assert solo is not None

    observation = _composite(
        bokun=_bokun(transport),
        groups_source=RecordingGroups("matched", participants=5),
        policy=policy,
    ).read(request)

    assert transport.calls == [
        (
            "activity",
            {
                "product_id": "product:buracao",
                "activity_date": ACTIVITY_DATE.isoformat(),
                "adults": 1,
                "children": 0,
                "quote_scope": request.query_hash(),
                "locale": "pt-BR",
                "expected_bokun_product_id": solo.bokun_product_id,
                "expected_rate_id": solo.rate_id,
                "expected_adult_category_id": solo.adult_category_id,
                "ignore_minimum_participants": True,
            },
        )
    ]
    assert observation.public_payload["available"] is True
    assert observation.public_payload["group_status"] == "matched"
    assert observation.public_payload["existing_group"] is True
    assert observation.public_payload["group_participants"] == 5
    assert observation.public_payload["solo_group_booking"] is True
    assert observation.public_payload["offer_id"].startswith("offer:")
    expected_private_names = {
        "adult_pricing_category_id",
        "bokun_product_id",
        "rate_id",
        "start_time_id",
    }

    query = _query(request, observation)
    binding = _composite(
        bokun=_bokun(transport),
        groups_source=RecordingGroups("matched", participants=5),
        policy=policy,
    ).resolve(query)

    assert binding.query == query
    assert set(binding.private_payload()) == expected_private_names
    assert binding.private_payload() == {
        "adult_pricing_category_id": solo.adult_category_id,
        "bokun_product_id": solo.bokun_product_id,
        "rate_id": solo.rate_id,
        "start_time_id": "start-buracao",
    }
    assert request.query_hash() == query.request_hash


def test_non_policy_solo_keeps_normal_bokun_path_with_group_enrichment() -> None:
    transport = RecordingBokunTransport()
    request = _request(participants=1, product_id="product:tour-4ps")

    observation = _composite(
        bokun=_bokun(transport),
        groups_source=RecordingGroups("not_matched"),
        policy=_policy(),
    ).read(request)

    payload = transport.calls[0][1]
    assert "availability_only" not in payload
    assert "expected_rate_id" not in payload
    assert observation.public_payload["available"] is True
    assert observation.public_payload["group_status"] == "not_matched"
    assert observation.public_payload["solo_group_booking"] is False


def test_restricted_solo_resolve_rechecks_group_and_blocks_disappearance() -> None:
    transport = RecordingBokunTransport()
    request = _request(participants=1)
    policy = _policy()
    observation = _composite(
        bokun=_bokun(transport),
        groups_source=RecordingGroups("matched"),
        policy=policy,
    ).read(request)
    calls_before_resolve = len(transport.calls)

    with pytest.raises(ProviderReadError, match="group"):
        _composite(
            bokun=_bokun(transport),
            groups_source=RecordingGroups("unavailable"),
            policy=policy,
        ).resolve(_query(request, observation))

    assert len(transport.calls) == calls_before_resolve


def test_two_plus_resolve_relooks_group_but_keeps_normal_private_catalog() -> None:
    transport = RecordingBokunTransport()
    request = _request(participants=2)
    policy = _policy()
    observation = _composite(
        bokun=_bokun(transport),
        groups_source=RecordingGroups("matched"),
        policy=policy,
    ).read(request)
    groups = RecordingGroups("unavailable")

    binding = _composite(
        bokun=_bokun(transport),
        groups_source=groups,
        policy=policy,
    ).resolve(_query(request, observation))

    assert groups.calls == [("product:buracao", ACTIVITY_DATE)]
    assert "expected_rate_id" not in transport.calls[-1][1]
    assert binding.query == _query(request, observation)
    assert set(binding.private_payload()) == {
        "adult_pricing_category_id",
        "bokun_product_id",
        "rate_id",
        "start_time_id",
    }


def test_ordinary_catalog_remains_open_for_non_recommended_product_and_binding() -> None:
    transport = RecordingBokunTransport()
    groups = RecordingGroups("not_matched")
    request = _request(participants=2, product_id="product:sossego")
    adapter = _composite(
        bokun=_bokun(transport),
        groups_source=groups,
        policy=_policy(),
    )

    observation = adapter.read(request)
    binding = adapter.resolve(_query(request, observation))

    assert groups.calls == [
        ("product:sossego", ACTIVITY_DATE),
        ("product:sossego", ACTIVITY_DATE),
    ]
    assert len(transport.calls) == 2
    assert observation.public_payload["available"] is True
    assert observation.public_payload["group_status"] == "not_matched"
    assert observation.public_payload["product_id"] == "product:sossego"
    assert binding.query == _query(request, observation)
    assert set(binding.private_payload()) == {
        "adult_pricing_category_id",
        "bokun_product_id",
        "rate_id",
        "start_time_id",
    }


@pytest.mark.parametrize(
    ("participants", "status", "expected_mode"),
    (
        (2, "matched", "ordinary"),
        (2, "not_matched", "ordinary"),
        (2, "unavailable", "ordinary"),
        (1, "matched", "selected"),
        (1, "not_matched", "availability_only"),
        (1, "unavailable", "availability_only"),
    ),
)
def test_read_with_group_context_applies_existing_policy_without_group_source_io(
    participants: int,
    status: str,
    expected_mode: str,
) -> None:
    groups = RecordingGroups(fail=True)
    transport = RecordingBokunTransport()
    request = _request(participants=participants)
    context = GroupLookupResult(
        status=status,  # type: ignore[arg-type]
        canonical_product_id=request.product_id,
        activity_date=request.activity_date,
        participant_count=4 if status == "matched" else None,
    )

    observation = _composite(
        bokun=_bokun(transport),
        groups_source=groups,
        policy=_policy(),
    ).read_with_group_context(request, group=context)

    assert groups.calls == []
    assert observation.public_payload["group_status"] == status
    payload = transport.calls[0][1]
    if expected_mode == "selected":
        assert payload["ignore_minimum_participants"] is True
        assert "expected_rate_id" in payload
    elif expected_mode == "availability_only":
        assert payload["availability_only"] is True
        assert "expected_rate_id" not in payload
    else:
        assert "availability_only" not in payload
        assert "expected_rate_id" not in payload


def test_read_with_group_context_rejects_mismatched_or_unsanitized_context() -> None:
    groups = RecordingGroups(fail=True)
    transport = RecordingBokunTransport()
    request = _request(participants=2)
    mismatched = GroupLookupResult(
        status="matched",
        canonical_product_id="product:marimbus",
        activity_date=request.activity_date,
        participant_count=2,
    )
    adapter = _composite(
        bokun=_bokun(transport), groups_source=groups, policy=_policy()
    )

    with pytest.raises(ValueError, match="group context"):
        adapter.read_with_group_context(request, group=mismatched)
    assert groups.calls == []
    assert transport.calls == []


def test_composite_rejects_non_activity_read_and_non_activity_resolve() -> None:
    adapter = _composite(
        bokun=_bokun(RecordingBokunTransport()),
        groups_source=RecordingGroups(),
        policy=_policy(),
    )
    with pytest.raises(TypeError, match="ACTIVITY"):
        adapter.read(
            ReadRequest(
                request_id="read:description",
                kind=ReadKind.ACTIVITY_DESCRIPTION,
                product_id="product:buracao",
            )
        )
