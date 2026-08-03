from __future__ import annotations

from dataclasses import replace
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from reservation_boundary import (
    BoundaryState,
    ConversationProjection,
    ConversationStage,
    DateSlot,
    StringSlot,
    TypedFact,
)
from reservation_domain import CustomerFacts, PassengerFacts, effective_passengers
from reservation_domain.serialization import _decode_dataclass, _encode
from reservation_domain.signature import canonical_subject, subject_signature
from reservation_domain.types import EconomicTerms, Money, OfferSnapshot, Party, ServiceKind
from v2_adapters.hermes_model import _request_wire
from v2_application.conversation import V2ConversationReducer
from v2_application.turn_executor import _private_customer_fact_names, _state_model_facts
from v2_contracts.model import (
    InvalidModelProposal,
    ModelFact,
    ModelProposal,
    ModelRequest,
)
from v2_contracts.profile import PrivateCustomerBinding

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def _profile_without_contact_fields() -> PrivateCustomerBinding:
    return PrivateCustomerBinding(
        binding_id="profile-binding:missing",
        content_hash="a" * 64,
        full_name=None,
        email=None,
        phone_e164=None,
        country_code=None,
        observed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
        complete=False,
    )


def _proposal(event_id: str, facts: tuple[ModelFact, ...]) -> ModelProposal:
    return ModelProposal(
        source_event_id=event_id,
        intent="inform",
        reply_chunks=("Vou guardar esses dados para a reserva.",),
        facts=facts,
        read_requests=(),
        effect_proposals=(),
    )


def test_private_customer_facts_are_presence_markers_not_model_state() -> None:
    facts = (
        ModelFact("full_name", "Carlos Eduardo"),
        ModelFact("email", "carlos@example.invalid"),
        ModelFact("phone_e164", "+5571999999999"),
        ModelFact("country_code", "BR"),
        ModelFact("birth_date", date(1990, 1, 2)),
        ModelFact("gender", "m"),
    )
    request = ModelRequest(
        request_id="request:customer-state",
        lead_id="manychat:1873018537",
        source_event_id="event:customer-state",
        message="Pode continuar",
        locale="pt-BR",
        state_version=3,
        private_customer_fact_names=tuple(item.name for item in facts),
    )

    wire = json.loads(_request_wire(request, "closed prompt"))
    user = json.loads(wire["messages"][0][1])

    assert "state_facts" not in user
    assert user["private_customer_fact_names"] == [
        "full_name",
        "email",
        "phone_e164",
        "country_code",
        "birth_date",
        "gender",
    ]
    serialized = json.dumps(user, ensure_ascii=False)
    assert "Carlos Eduardo" not in serialized
    assert "carlos@example.invalid" not in serialized
    assert "1990-01-02" not in serialized


def test_model_request_rejects_private_values_in_state_facts() -> None:
    with pytest.raises(InvalidModelProposal, match="private"):
        ModelRequest(
            request_id="request:private-state",
            lead_id="manychat:private-state",
            source_event_id="event:private-state",
            message="Pode continuar",
            locale="pt-BR",
            state_version=3,
            state_facts=(ModelFact("email", "carlos@example.invalid"),),
        )


def test_executor_exposes_only_private_fact_presence() -> None:
    projection = ConversationProjection(
        stage=ConversationStage.RECEPTIONIST,
        desired_services=(),
        locale="pt-BR",
        facts=(
            TypedFact("service", StringSlot("agency"), "a" * 64),
            TypedFact("full_name", StringSlot("Carlos Eduardo"), "b" * 64),
            TypedFact("email", StringSlot("carlos@example.invalid"), "c" * 64),
            TypedFact("birth_date", DateSlot(date(1990, 1, 2)), "d" * 64),
            TypedFact("gender", StringSlot("m"), "e" * 64),
        ),
        reservation_execution_projection=None,
    )

    assert _state_model_facts(projection) == (ModelFact("service", "agency"),)
    assert _private_customer_fact_names(projection) == (
        "birth_date",
        "gender",
    )


def test_manychat_presence_markers_include_only_effectively_valid_fresh_fields() -> None:
    profile = PrivateCustomerBinding(
        binding_id="profile-binding:invalid-presence-markers",
        content_hash="f" * 64,
        full_name="Mononym",
        email="@example.invalid",
        phone_e164="".join(("+1", "202", "555", "0196")),
        country_code="ZZ",
        observed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        complete=True,
    )

    assert _private_customer_fact_names(
        ConversationProjection(
            stage=ConversationStage.RECEPTIONIST,
            desired_services=(),
            locale="pt-BR",
            facts=(),
            reservation_execution_projection=None,
        ),
        profile=profile,
        now=NOW,
    ) == ("phone_e164",)

    expired = replace(
        profile,
        observed_at=NOW - timedelta(minutes=6),
        expires_at=NOW,
    )
    assert _private_customer_fact_names(
        ConversationProjection(
            stage=ConversationStage.RECEPTIONIST,
            desired_services=(),
            locale="pt-BR",
            facts=(),
            reservation_execution_projection=None,
        ),
        profile=expired,
        now=NOW,
    ) == ()

    valid_but_expired = PrivateCustomerBinding(
        binding_id="profile-binding:expired-presence-markers",
        content_hash="e" * 64,
        full_name="Pessoa Marcador Silva",
        email="marker.person@example.invalid",
        phone_e164="".join(("+1", "202", "555", "0197")),
        country_code="BR",
        observed_at=NOW - timedelta(minutes=6),
        expires_at=NOW,
        complete=True,
    )
    legacy_projection = ConversationProjection(
        stage=ConversationStage.RECEPTIONIST,
        desired_services=(),
        locale="pt-BR",
        facts=(
            TypedFact(
                "phone_e164",
                StringSlot("".join(("+1", "202", "555", "0188"))),
                "9" * 64,
            ),
        ),
        reservation_execution_projection=None,
    )
    assert _private_customer_fact_names(
        legacy_projection,
        profile=valid_but_expired,
        now=NOW,
    ) == ()


def test_reducer_accumulates_user_supplied_customer_facts_when_profile_is_empty() -> None:
    state = BoundaryState(7, "manychat:1873018537", 0, None, None, (), ())
    projection = ConversationProjection(
        stage=ConversationStage.RECEPTIONIST,
        desired_services=(),
        locale="pt-BR",
        facts=(),
        reservation_execution_projection=None,
    )
    first = V2ConversationReducer().reduce(
        state=state,
        projection=projection,
        proposal=_proposal(
            "event:customer-part-1",
            (
                ModelFact("full_name", "Carlos Eduardo"),
                ModelFact("email", "carlos@example.invalid"),
                ModelFact("phone_e164", "+5571999999999"),
            ),
        ),
        profile=_profile_without_contact_fields(),
        reads=(),
        fact_commitment_hash="b" * 64,
        now=NOW,
    )

    assert first.public_reply.kind == "inform"
    assert first.public_reply.chunks == (
        "Vou guardar esses dados para a reserva.",
    )
    assert tuple(item.name for item in first.projection.facts) == (
        "full_name",
        "email",
        "phone_e164",
    )

    second = V2ConversationReducer().reduce(
        state=first.next_state,
        projection=first.projection,
        proposal=_proposal(
            "event:customer-part-2",
            (
                ModelFact("country_code", "BR"),
                ModelFact("birth_date", date(1990, 1, 2)),
                ModelFact("gender", "m"),
            ),
        ),
        profile=_profile_without_contact_fields(),
        reads=(),
        fact_commitment_hash="c" * 64,
        now=NOW + timedelta(seconds=1),
    )

    assert second.public_reply.kind == "inform"
    assert tuple(item.name for item in second.projection.facts) == (
        "full_name",
        "email",
        "phone_e164",
        "country_code",
        "birth_date",
        "gender",
    )


def test_customer_birth_date_and_gender_are_execution_bound_but_legacy_shape_stays_valid() -> None:
    legacy = CustomerFacts(
        customer_ref="profile:legacy",
        full_name="Carlos Eduardo",
        email="carlos@example.invalid",
        phone_e164="+5571999999999",
        country_code="BR",
    )
    enriched = CustomerFacts(
        customer_ref="profile:enriched",
        full_name="Carlos Eduardo",
        email="carlos@example.invalid",
        phone_e164="+5571999999999",
        country_code="BR",
        birth_date=date(1990, 1, 2),
        gender="m",
    )

    legacy_wire = _encode(legacy)
    enriched_wire = _encode(enriched)
    assert set(legacy_wire) == {
        "customer_ref",
        "full_name",
        "email",
        "phone_e164",
        "country_code",
    }
    assert enriched_wire["birth_date"] == "1990-01-02"
    assert enriched_wire["gender"] == "m"
    assert _decode_dataclass(CustomerFacts, legacy_wire) == legacy
    assert _decode_dataclass(CustomerFacts, enriched_wire) == enriched

    offer = OfferSnapshot(
        offer_id="offer:customer-bound",
        lookup_id="lookup:customer-bound",
        service=ServiceKind.ACTIVITY,
        provider_ref="provider:customer-bound",
        public_label="Buracão",
        start_date=date(2026, 8, 11),
        end_date=None,
        start_time=None,
        party=Party(1, 0),
        total=Money("300.00", "BRL"),
        available=True,
    )
    subject = canonical_subject(
        components=(offer,),
        customer=enriched,
        terms=EconomicTerms("stripe"),
    )
    assert subject["customer"]["birth_date"] == "1990-01-02"
    assert subject["customer"]["gender"] == "m"


def _activity_offer(party: Party) -> OfferSnapshot:
    return OfferSnapshot(
        offer_id="offer:passenger-bound",
        lookup_id="lookup:passenger-bound",
        service=ServiceKind.ACTIVITY,
        provider_ref="provider:passenger-bound",
        public_label="Passeio sintético",
        start_date=date(2026, 8, 11),
        end_date=None,
        start_time="08:00",
        party=party,
        total=Money("750.00", "BRL"),
        available=True,
    )


def _passenger(position: int, participant_type: str, full_name: str) -> PassengerFacts:
    return PassengerFacts(
        position=position,
        participant_type=participant_type,
        full_name=full_name,
        birth_date=date(1990 + position, 1, 2),
        gender="f" if position % 2 else "m",
        country_code="BR",
    )


def _group_customer(passengers: tuple[PassengerFacts, ...]) -> CustomerFacts:
    return CustomerFacts(
        customer_ref="profile:passenger-group",
        full_name="Pessoa Sintética Um",
        email="group@example.invalid",
        phone_e164="+5571999999999",
        country_code="BR",
        passengers=passengers,
    )


def test_passenger_manifest_round_trips_and_is_bound_to_subject_signature() -> None:
    passengers = (
        _passenger(1, "adult", "Pessoa Sintética Um"),
        _passenger(2, "adult", "Pessoa Sintética Dois"),
        _passenger(3, "child", "Pessoa Sintética Três"),
    )
    customer = _group_customer(passengers)
    wire = _encode(customer)

    assert _decode_dataclass(CustomerFacts, wire) == customer
    assert wire["passengers"][2] == {
        "position": 3,
        "participant_type": "child",
        "full_name": "Pessoa Sintética Três",
        "birth_date": "1993-01-02",
        "gender": "f",
        "country_code": "BR",
    }
    subject = canonical_subject(
        components=(_activity_offer(Party(2, 1)),),
        customer=customer,
        terms=EconomicTerms("stripe"),
    )
    assert subject["customer"]["passengers"] == wire["passengers"]

    corrected = _group_customer(
        (
            passengers[0],
            PassengerFacts(
                position=2,
                participant_type="adult",
                full_name="Pessoa Sintética Dois",
                birth_date=date(1992, 1, 3),
                gender="m",
                country_code="BR",
            ),
            passengers[2],
        )
    )
    assert subject_signature(
        components=(_activity_offer(Party(2, 1)),),
        customer=customer,
        terms=EconomicTerms("stripe"),
    ) != subject_signature(
        components=(_activity_offer(Party(2, 1)),),
        customer=corrected,
        terms=EconomicTerms("stripe"),
    )


def test_legacy_customer_wire_stays_unchanged_when_manifest_is_empty() -> None:
    customer = CustomerFacts(
        customer_ref="profile:legacy-passenger",
        full_name="Pessoa Sintética",
        email="legacy-passenger@example.invalid",
        phone_e164="+5571999999998",
        country_code="BR",
        birth_date=date(1990, 1, 2),
        gender="f",
    )

    assert "passengers" not in _encode(customer)
    assert "passengers" not in canonical_subject(
        components=(_activity_offer(Party(1, 0)),),
        customer=customer,
        terms=EconomicTerms("stripe"),
    )["customer"]
    assert effective_passengers(customer, Party(1, 0)) == (
        PassengerFacts(1, "adult", "Pessoa Sintética", date(1990, 1, 2), "f", "BR"),
    )


def test_effective_passengers_fail_closed_for_missing_or_divergent_group_manifest() -> None:
    customer = _group_customer(
        (
            _passenger(1, "adult", "Pessoa Sintética Um"),
            _passenger(2, "child", "Pessoa Sintética Dois"),
        )
    )

    assert effective_passengers(customer, Party(1, 1)) == customer.passengers
    with pytest.raises(ValueError, match="does not match party"):
        effective_passengers(customer, Party(2, 0))
    with pytest.raises(ValueError, match="explicit passenger manifest"):
        effective_passengers(
            CustomerFacts(
                customer_ref="profile:no-group-manifest",
                full_name="Pessoa Sintética",
                email="no-group-manifest@example.invalid",
                phone_e164="+5571999999997",
                country_code="BR",
                birth_date=date(1990, 1, 2),
                gender="f",
            ),
            Party(2, 0),
        )


def test_passenger_manifest_rejects_non_contiguous_positions_and_unknown_type() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        _group_customer(
            (
                _passenger(1, "adult", "Pessoa Sintética Um"),
                _passenger(3, "adult", "Pessoa Sintética Três"),
            )
        )
    with pytest.raises(ValueError, match="participant_type"):
        _passenger(1, "senior", "Pessoa Sintética")
