from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from reservation_domain import Money, OfferSnapshot, Party, ServiceKind
from v2_adapters.bokun import BokunReadAdapter
from v2_adapters.cloudbeds import CloudbedsReadAdapter
from v2_adapters.knowledge import KnowledgeReadAdapter
from v2_application.reads import (
    PrivateBindingMismatch,
    PrivateOfferBindingResolver,
    StaleObservation,
    V2ReadService,
)
from v2_contracts.providers import InvalidReadRequest, ReadKind, ReadRequest

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
LODGING_REQUEST = ReadRequest(
    request_id="read-lodging-001",
    kind=ReadKind.LODGING,
    check_in=date(2026, 8, 10),
    check_out=date(2026, 8, 12),
    adults=2,
    children=0,
)
ACTIVITY_REQUEST = ReadRequest(
    request_id="read-activity-001",
    kind=ReadKind.ACTIVITY,
    product_id="product:buracao-001",
    activity_date=date(2026, 8, 11),
    participants=2,
)
KNOWLEDGE_REQUEST = ReadRequest(
    request_id="read-knowledge-001",
    kind=ReadKind.KNOWLEDGE,
    query="Qual é o horário do café?",
    locale="pt-BR",
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequenceClock:
    def __init__(self, values: tuple[datetime, ...]) -> None:
        self._values = iter(values)

    def now(self) -> datetime:
        return next(self._values)


def test_lodging_read_binds_dates_occupancy_price_and_private_offer_id() -> None:
    calls = []

    def transport(operation, payload):
        calls.append((operation, payload))
        return {
            "options": [
                {
                    "room_public_name": "Suíte Casal",
                    "check_in": "2026-08-10",
                    "check_out": "2026-08-12",
                    "adults": 2,
                    "children": 0,
                    "total_amount": "480.00",
                    "currency": "BRL",
                    "available_units": 1,
                    "room_type_id": "room-private-001",
                    "room_rate_id": "rate-private-001",
                }
            ]
        }

    reads = CloudbedsReadAdapter(
        transport=transport,
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    )

    observation = reads.read(LODGING_REQUEST)

    assert observation.request_hash == LODGING_REQUEST.canonical_hash()
    assert observation.public_payload["total_amount"] == "480.00"
    assert observation.public_payload["room_public_name"] == "Suíte Casal"
    assert "room_type_id" not in observation.public_payload
    assert "room_rate_id" not in observation.public_payload
    assert len(observation.private_binding_hash) == 64
    assert calls == [
        (
            "lodging",
            {
                "check_in": "2026-08-10",
                "check_out": "2026-08-12",
                "adults": 2,
                "children": 0,
            },
        )
    ]


def test_lodging_read_treats_empty_options_as_confirmed_unavailability() -> None:
    reads = CloudbedsReadAdapter(
        transport=lambda operation, payload: {"options": []},
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    )

    observation = reads.read(LODGING_REQUEST)

    assert observation.request_hash == LODGING_REQUEST.canonical_hash()
    assert observation.provider == "cloudbeds"
    assert observation.public_payload == {"options": []}
    assert len(observation.private_binding_hash) == 64


def test_query_hash_is_stable_across_new_request_id_but_request_hash_is_not() -> None:
    reread = replace(LODGING_REQUEST, request_id="read-lodging-002")

    assert reread.canonical_hash() != LODGING_REQUEST.canonical_hash()
    assert reread.query_hash() == LODGING_REQUEST.query_hash()


def test_activity_commercial_query_hash_excludes_presentation_locale() -> None:
    portuguese = replace(
        ACTIVITY_REQUEST,
        request_id="read-activity-pt",
        locale="pt-BR",
        adults=2,
        children=0,
        participants=None,
    )
    english = replace(
        portuguese,
        request_id="read-activity-en",
        locale="en",
    )
    unlocalized = replace(
        portuguese,
        request_id="read-activity-worker",
        locale=None,
    )
    portuguese_knowledge = replace(
        KNOWLEDGE_REQUEST,
        request_id="read-knowledge-pt",
    )
    english_knowledge = replace(
        portuguese_knowledge,
        request_id="read-knowledge-en",
        locale="en",
    )

    assert len(
        {
            portuguese.canonical_hash(),
            english.canonical_hash(),
            unlocalized.canonical_hash(),
        }
    ) == 3
    assert portuguese.query_hash() == english.query_hash() == unlocalized.query_hash()
    assert portuguese_knowledge.query_hash() != english_knowledge.query_hash()


def test_localized_bokun_offer_survives_fresh_worker_composition() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    provider_state = {"rate_id": "rate-private-001"}

    def transport(operation: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((operation, payload))
        return {
            "product_id": payload["product_id"],
            "bokun_product_id": "bokun-private-001",
            "start_time_id": "start-private-001",
            "start_time": "08:30",
            "rate_id": provider_state["rate_id"],
            "adult_pricing_category_id": "category-private-001",
            "product_public_name": "Buracão",
            "base_amount": "400.00",
            "booking_fee_amount": "0.00",
            "total_amount": "400.00",
            "price_includes_booking_fee": True,
            "currency": "BRL",
            "available": True,
        }

    conversation_request = replace(
        ACTIVITY_REQUEST,
        request_id="read-activity-conversation",
        locale="pt-BR",
        adults=2,
        children=0,
        participants=None,
    )
    conversation_adapter = BokunReadAdapter(
        transport=transport,
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    )
    observation = conversation_adapter.read(conversation_request)
    worker_query = replace(
        conversation_request,
        request_id="read-activity-worker",
        locale=None,
    )
    component = OfferSnapshot(
        offer_id=observation.public_payload["offer_id"],
        lookup_id=(
            f"lookup:{conversation_request.product_id}:{worker_query.query_hash()}"
        ),
        service=ServiceKind.ACTIVITY,
        provider_ref=observation.private_binding_hash,
        public_label=observation.public_payload["product_public_name"],
        start_date=conversation_request.activity_date,
        end_date=None,
        start_time=observation.public_payload["start_time"],
        party=Party(*conversation_request.activity_party()),
        total=Money(amount=Decimal("400.00"), currency="BRL"),
        available=True,
    )
    worker_adapter = BokunReadAdapter(
        transport=transport,
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    )

    binding = PrivateOfferBindingResolver(
        {ServiceKind.ACTIVITY: worker_adapter}
    ).resolve(component, now=NOW)

    assert binding.query.binding_hash == observation.private_binding_hash
    assert binding.query.offer_id == observation.public_payload["offer_id"]
    assert binding.private_payload() == {
        "adult_pricing_category_id": "category-private-001",
        "bokun_product_id": "bokun-private-001",
        "rate_id": "rate-private-001",
        "start_time_id": "start-private-001",
    }
    assert len(calls) == 2
    assert calls[0][1]["locale"] == "pt-BR"
    assert "locale" not in calls[1][1]
    assert calls[0][1]["quote_scope"] == calls[1][1]["quote_scope"]

    provider_state["rate_id"] = "rate-private-002"
    with pytest.raises(PrivateBindingMismatch, match="commercial binding"):
        PrivateOfferBindingResolver(
            {ServiceKind.ACTIVITY: worker_adapter}
        ).resolve(component, now=NOW)

    provider_state["rate_id"] = "rate-private-001"
    calls_before_tamper = len(calls)
    for changed in (
        replace(
            component,
            lookup_id=component.lookup_id.replace(
                conversation_request.product_id,
                "not-a-canonical-product",
                1,
            ),
        ),
        replace(
            component,
            lookup_id=component.lookup_id.replace(
                conversation_request.product_id,
                "product:other-activity",
                1,
            ),
        ),
        replace(component, start_date=date(2026, 8, 12)),
        replace(component, party=Party(adults=3, children=0)),
    ):
        with pytest.raises(PrivateBindingMismatch, match="lookup identity"):
            PrivateOfferBindingResolver(
                {ServiceKind.ACTIVITY: worker_adapter}
            ).resolve(changed, now=NOW)
    assert len(calls) == calls_before_tamper


def test_private_reread_accepts_observation_created_after_read_started() -> None:
    def transport(operation, payload):
        assert operation == "lodging"
        return {
            "options": [
                {
                    "room_public_name": "Compartilhado n°2",
                    **payload,
                    "total_amount": "90.00",
                    "currency": "BRL",
                    "available_units": 1,
                    "room_type_id": "room-private-002",
                    "room_rate_id": "rate-private-002",
                }
            ]
        }

    clock = SequenceClock((NOW, NOW + timedelta(seconds=2)))
    adapter = CloudbedsReadAdapter(
        transport=transport,
        clock=clock,
        ttl=timedelta(minutes=5),
    )
    observation = adapter.read(LODGING_REQUEST)
    option = observation.public_payload["options"][0]
    component = OfferSnapshot(
        offer_id=option["offer_id"],
        lookup_id=f"lookup:{LODGING_REQUEST.query_hash()}",
        service=ServiceKind.LODGING,
        provider_ref=observation.private_binding_hash,
        public_label=option["room_public_name"],
        start_date=LODGING_REQUEST.check_in,
        end_date=LODGING_REQUEST.check_out,
        start_time=None,
        party=Party(adults=LODGING_REQUEST.adults, children=LODGING_REQUEST.children),
        total=Money(amount=Decimal("90.00"), currency="BRL"),
        available=True,
    )
    resolver = PrivateOfferBindingResolver({ServiceKind.LODGING: adapter})

    binding = resolver.resolve(component, now=NOW + timedelta(seconds=1))

    assert binding.query.offer_id == component.offer_id
    assert binding.observed_at == NOW + timedelta(seconds=2)
    assert binding.expires_at > NOW + timedelta(seconds=1)


def test_activity_read_requires_canonical_product_id() -> None:
    reads = BokunReadAdapter(
        transport=lambda operation, payload: {},
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    )

    with pytest.raises(InvalidReadRequest, match="canonical product"):
        reads.read(replace(ACTIVITY_REQUEST, product_id="Buracão"))


def test_knowledge_read_cannot_return_provider_credentials() -> None:
    reads = KnowledgeReadAdapter(
        transport=lambda operation, payload: {
            "answer": "O café é servido das 7h às 9h.",
            "sources": ["faq:cafe"],
            "token": "must-not-leak",
            "secret": "must-not-leak",
        },
        clock=FixedClock(),
        ttl=timedelta(minutes=30),
    )

    observation = reads.read(KNOWLEDGE_REQUEST)

    public = json.dumps(observation.public_payload, sort_keys=True)
    assert "token" not in public.lower()
    assert "secret" not in public.lower()
    assert observation.public_payload == {
        "answer": "O café é servido das 7h às 9h.",
        "sources": ["faq:cafe"],
    }


def test_knowledge_read_can_include_sanitized_formed_groups_without_new_read_kind() -> None:
    class Groups:
        def upcoming_groups(self, *, start_date: date, days: int, max_groups: int):
            assert start_date == date(2026, 8, 10)
            assert days == 8
            assert max_groups == 24
            return (
                type("Group", (), {
                    "canonical_product_id": "product:buracao",
                    "activity_date": date(2026, 8, 12),
                    "participant_count": 3,
                })(),
            )

    reads = KnowledgeReadAdapter(
        transport=lambda operation, payload: {
            "answer": "Contexto de passeios.",
            "sources": ["catalogo"],
        },
        groups_source=Groups(),
        clock=FixedClock(),
        ttl=timedelta(minutes=30),
    )

    observation = reads.read(
        replace(
            KNOWLEDGE_REQUEST,
            query="formed-groups:2026-08-10:2026-08-17",
        )
    )

    assert observation.public_payload == {
        "answer": "Contexto de passeios.",
        "sources": ["catalogo"],
        "formed_groups": [
            {
                "product_id": "product:buracao",
                "activity_date": "2026-08-12",
                "participants": 3,
            }
        ],
    }


def test_knowledge_read_keeps_working_when_group_sheet_is_unavailable() -> None:
    class BrokenGroups:
        def upcoming_groups(self, **kwargs):
            raise RuntimeError("private source detail")

    reads = KnowledgeReadAdapter(
        transport=lambda operation, payload: {
            "answer": "O café é servido das 7h às 9h.",
            "sources": ["faq:cafe"],
        },
        groups_source=BrokenGroups(),
        clock=FixedClock(),
        ttl=timedelta(minutes=30),
    )

    observation = reads.read(
        replace(
            KNOWLEDGE_REQUEST,
            query="formed-groups:2026-08-10:2026-08-17",
        )
    )

    assert observation.public_payload["formed_groups"] == []


def test_regular_knowledge_read_does_not_fetch_group_sheet() -> None:
    class Groups:
        def upcoming_groups(self, **kwargs):
            raise AssertionError("ordinary FAQ must not fetch the group sheet")

    reads = KnowledgeReadAdapter(
        transport=lambda operation, payload: {
            "answer": "O café é servido das 7h às 9h.",
            "sources": ["faq:cafe"],
        },
        groups_source=Groups(),
        clock=FixedClock(),
        ttl=timedelta(minutes=30),
    )

    observation = reads.read(KNOWLEDGE_REQUEST)

    assert "formed_groups" not in observation.public_payload


def test_stale_observation_cannot_authorize_selection() -> None:
    adapter = CloudbedsReadAdapter(
        transport=lambda operation, payload: {
            "options": [
                {
                    "room_public_name": "Suíte Casal",
                    "check_in": "2026-08-10",
                    "check_out": "2026-08-12",
                    "adults": 2,
                    "children": 0,
                    "total_amount": "480.00",
                    "currency": "BRL",
                    "available_units": 1,
                    "room_type_id": "room-private-001",
                    "room_rate_id": "rate-private-001",
                }
            ]
        },
        clock=FixedClock(),
        ttl=timedelta(seconds=1),
    )
    observation = adapter.read(LODGING_REQUEST)
    reads = V2ReadService({ReadKind.LODGING: adapter})

    with pytest.raises(StaleObservation):
        reads.accept(observation, now=NOW + timedelta(seconds=2))


def test_bokun_private_reread_resolves_raw_id_and_rejects_changed_terms() -> None:
    provider_state = {"amount": "400.00"}

    def transport(operation, payload):
        assert operation == "activity"
        return {
            **payload,
            "bokun_product_id": "bokun-private-001",
            "start_time_id": "start-private-001",
            "rate_id": "rate-private-001",
            "adult_pricing_category_id": "category-private-001",
            "product_public_name": "Buracão",
            "base_amount": provider_state["amount"],
            "booking_fee_amount": "0.00",
            "total_amount": provider_state["amount"],
            "price_includes_booking_fee": True,
            "currency": "BRL",
            "available": True,
        }

    adapter = BokunReadAdapter(
        transport=transport,
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    )
    observation = adapter.read(ACTIVITY_REQUEST)
    component = OfferSnapshot(
        offer_id=observation.public_payload["offer_id"],
        lookup_id=(
            f"lookup:{ACTIVITY_REQUEST.product_id}:{ACTIVITY_REQUEST.query_hash()}"
        ),
        service=ServiceKind.ACTIVITY,
        provider_ref=observation.private_binding_hash,
        public_label=observation.public_payload["product_public_name"],
        start_date=ACTIVITY_REQUEST.activity_date,
        end_date=None,
        start_time=None,
        party=Party(*ACTIVITY_REQUEST.activity_party()),
        total=Money(amount=Decimal("400.00"), currency="BRL"),
        available=True,
    )
    resolver = PrivateOfferBindingResolver({ServiceKind.ACTIVITY: adapter})

    binding = resolver.resolve(component, now=NOW)
    assert binding.private_payload() == {
        "bokun_product_id": "bokun-private-001",
        "adult_pricing_category_id": "category-private-001",
        "rate_id": "rate-private-001",
        "start_time_id": "start-private-001",
    }

    provider_state["amount"] = "401.00"
    with pytest.raises(PrivateBindingMismatch, match="commercial binding"):
        resolver.resolve(component, now=NOW)


def test_activity_read_carries_parent_owned_locale_to_bokun_transport() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def transport(operation, payload):
        calls.append((operation, payload))
        return {
            "product_id": payload["product_id"],
            "bokun_product_id": "912303",
            "start_time_id": "start-4ps",
            "rate_id": "rate-4ps",
            "adult_pricing_category_id": "857489",
            "product_public_name": "4Ps Tour",
            "base_amount": "330.00",
            "booking_fee_amount": "4.95",
            "total_amount": "334.95",
            "price_includes_booking_fee": True,
            "currency": "BRL",
            "available": True,
        }

    request = replace(ACTIVITY_REQUEST, locale="en")
    BokunReadAdapter(
        transport=transport,
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    ).read(request)

    assert calls[0][1]["locale"] == "en"


def test_bokun_read_exposes_only_fee_inclusive_total_and_binds_quote_scope() -> None:
    calls = []

    def transport(operation, payload):
        calls.append((operation, payload))
        return {
            "product_id": payload["product_id"],
            "bokun_product_id": "912303",
            "start_time_id": "start-4ps",
            "rate_id": "rate-4ps",
            "adult_pricing_category_id": "857489",
            "product_public_name": "Roteiro dos 4Ps",
            "base_amount": "330.00",
            "booking_fee_amount": "4.95",
            "total_amount": "334.95",
            "price_includes_booking_fee": True,
            "currency": "BRL",
            "available": True,
        }

    adapter = BokunReadAdapter(
        transport=transport,
        clock=FixedClock(),
        ttl=timedelta(minutes=5),
    )

    observation = adapter.read(ACTIVITY_REQUEST)

    assert calls == [
        (
            "activity",
            {
                "product_id": ACTIVITY_REQUEST.product_id,
                "activity_date": "2026-08-11",
                "adults": 2,
                "children": 0,
                "quote_scope": ACTIVITY_REQUEST.query_hash(),
            },
        )
    ]
    assert observation.public_payload["total_amount"] == "334.95"
    assert observation.public_payload["base_amount"] == "330.00"
    assert observation.public_payload["booking_fee_amount"] == "4.95"
    assert observation.public_payload["price_includes_booking_fee"] is True
    assert "quote_scope" not in observation.public_payload
