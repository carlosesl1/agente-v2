from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from reservation_boundary import BoundaryState, ConversationProjection, ConversationStage
from reservation_domain import CustomerFacts
from reservation_domain.serialization import _decode_dataclass, _encode
from reservation_domain.signature import canonical_subject
from reservation_domain.types import EconomicTerms, Money, OfferSnapshot, Party, ServiceKind
from v2_adapters.hermes_model import _request_wire
from v2_application.conversation import V2ConversationReducer
from v2_contracts.model import ModelFact, ModelProposal, ModelRequest
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


def test_closed_customer_facts_validate_and_round_trip_as_model_state() -> None:
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
        state_facts=facts,
    )

    wire = json.loads(_request_wire(request, "closed prompt"))
    user = json.loads(wire["messages"][0][1])

    assert user["state_facts"] == [
        {"name": "full_name", "value": "Carlos Eduardo"},
        {"name": "email", "value": "carlos@example.invalid"},
        {"name": "phone_e164", "value": "+5571999999999"},
        {"name": "country_code", "value": "BR"},
        {"name": "birth_date", "value": "1990-01-02"},
        {"name": "gender", "value": "m"},
    ]


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

    assert first.public_reply.kind == "profile_completion"
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
