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


def test_query_hash_is_stable_across_new_request_id_but_request_hash_is_not() -> None:
    reread = replace(LODGING_REQUEST, request_id="read-lodging-002")

    assert reread.canonical_hash() != LODGING_REQUEST.canonical_hash()
    assert reread.query_hash() == LODGING_REQUEST.query_hash()


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
            "pricing_category_id": "category-private-001",
            "product_public_name": "Buracão",
            "total_amount": provider_state["amount"],
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
        party=Party(adults=ACTIVITY_REQUEST.participants, children=0),
        total=Money(amount=Decimal("400.00"), currency="BRL"),
        available=True,
    )
    resolver = PrivateOfferBindingResolver({ServiceKind.ACTIVITY: adapter})

    binding = resolver.resolve(component, now=NOW)
    assert binding.private_payload() == {
        "bokun_product_id": "bokun-private-001",
        "pricing_category_id": "category-private-001",
        "rate_id": "rate-private-001",
        "start_time_id": "start-private-001",
    }

    provider_state["amount"] = "401.00"
    with pytest.raises(PrivateBindingMismatch, match="commercial binding"):
        resolver.resolve(component, now=NOW)
