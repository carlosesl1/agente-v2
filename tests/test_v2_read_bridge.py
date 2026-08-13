from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import pytest

from reservation_boundary.conversation import (
    ConversationProjection,
    ConversationStage,
    DesiredService,
    SourceEventIdentity,
)
from reservation_boundary.reads import SanitizedLookupResult
from v2_application.read_bridge import ReadBridgeError, bridge_availability_observation
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def _lodging_observation(
    request: ReadRequest,
    *,
    available_units: int,
    explicit_available: bool | None = None,
) -> ReadObservation:
    option: dict[str, object] = {
        "offer_id": "offer:" + "5" * 64,
        "room_public_name": "Suite Casal",
        "check_in": "2026-09-14",
        "check_out": "2026-09-15",
        "adults": 1,
        "children": 1,
        "total_amount": "150.00",
        "currency": "BRL",
        "available_units": available_units,
    }
    if explicit_available is not None:
        option["available"] = explicit_available
    return ReadObservation(
        request_hash=request.canonical_hash(),
        provider="cloudbeds",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        public_payload={"options": [option]},
        private_binding_hash="6" * 64,
    )


def test_cloudbeds_available_units_survive_authenticated_read_bridge() -> None:
    request = ReadRequest(
        request_id="read:cloudbeds-units",
        kind=ReadKind.LODGING,
        check_in=date(2026, 9, 14),
        check_out=date(2026, 9, 15),
        adults=1,
        children=1,
    )
    projection = ConversationProjection(
        stage=ConversationStage.HOSTEL,
        desired_services=(DesiredService.HOSTEL,),
        locale="pt-BR",
        facts=(),
        reservation_execution_projection=None,
    )

    bridged = bridge_availability_observation(
        request,
        _lodging_observation(request, available_units=3),
        lead_id="lead:cloudbeds-units",
        aggregate_turn_id="turn:cloudbeds-units",
        source_event=SourceEventIdentity("event:cloudbeds-units", "7" * 64),
        deadline_at=NOW + timedelta(minutes=1),
        locale="pt-BR",
        projection=projection,
        frame_commitment_hash="8" * 64,
    )

    result = SanitizedLookupResult.from_canonical_bytes(bridged.typed_result_bytes)
    assert result.status.value == "positive"
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.public_label == "Suite Casal"
    assert offer.start_date == date(2026, 9, 14)
    assert offer.end_date == date(2026, 9, 15)
    assert (offer.adults, offer.children) == (1, 1)
    assert str(offer.total_amount) == "150.00"
    assert offer.currency == "BRL"


def test_cloudbeds_conflicting_availability_claims_fail_closed() -> None:
    request = ReadRequest(
        request_id="read:cloudbeds-conflict",
        kind=ReadKind.LODGING,
        check_in=date(2026, 9, 14),
        check_out=date(2026, 9, 15),
        adults=1,
        children=1,
    )
    projection = ConversationProjection(
        stage=ConversationStage.HOSTEL,
        desired_services=(DesiredService.HOSTEL,),
        locale="pt-BR",
        facts=(),
        reservation_execution_projection=None,
    )

    with pytest.raises(ReadBridgeError, match="availability"):
        bridge_availability_observation(
            request,
            _lodging_observation(
                request,
                available_units=3,
                explicit_available=False,
            ),
            lead_id="lead:cloudbeds-conflict",
            aggregate_turn_id="turn:cloudbeds-conflict",
            source_event=SourceEventIdentity("event:cloudbeds-conflict", "9" * 64),
            deadline_at=NOW + timedelta(minutes=1),
            locale="pt-BR",
            projection=projection,
            frame_commitment_hash="a" * 64,
        )


def test_mixed_activity_party_survives_authenticated_read_bridge() -> None:
    request = ReadRequest(
        request_id="read:mixed-party",
        kind=ReadKind.ACTIVITY,
        product_id="product:tour-4ps",
        activity_date=date(2026, 11, 18),
        adults=2,
        children=1,
    )
    observation = ReadObservation(
        request_hash=request.canonical_hash(),
        provider="bokun",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        public_payload={
            "offer_id": "offer:" + "1" * 64,
            "product_public_name": "Roteiro dos 4Ps",
            "start_time": "08:30",
            "total_amount": "974.40",
            "currency": "BRL",
            "available": True,
            "group_status": "matched",
            "existing_group": True,
            "group_participants": 4,
            "solo_group_booking": False,
        },
        private_binding_hash="2" * 64,
    )
    projection = ConversationProjection(
        stage=ConversationStage.AGENCY,
        desired_services=(DesiredService.AGENCY,),
        locale="pt-BR",
        facts=(),
        reservation_execution_projection=None,
    )

    bridged = bridge_availability_observation(
        request,
        observation,
        lead_id="lead:mixed-party",
        aggregate_turn_id="turn:mixed-party",
        source_event=SourceEventIdentity("event:mixed-party", "3" * 64),
        deadline_at=NOW + timedelta(minutes=1),
        locale="pt-BR",
        projection=projection,
        frame_commitment_hash="4" * 64,
    )

    wire = json.loads(bridged.request_bytes)
    assert wire["data"]["tool_name"] == "bokun_consultar_passeio_grupo_v2"
    assert wire["data"]["arguments"] == {
        "type": "ActivityGroupReadArguments",
        "data": {
            "activity_id": "product:tour-4ps",
            "activity_date": "2026-11-18",
            "adults": 2,
            "children": 1,
        },
    }
    result = SanitizedLookupResult.from_canonical_bytes(bridged.typed_result_bytes)
    assert result.offers[0].adults == 2
    assert result.offers[0].children == 1
    assert result.offers[0].start_time == time(8, 30)
    assert result.offers[0].group_status == "matched"
    assert result.offers[0].existing_group is True
    assert result.offers[0].group_participants == 4
    assert result.offers[0].solo_group_booking is False
    assert json.loads(result.offers[0].to_canonical_bytes())["version"] == 2


def test_bridge_projects_only_closed_group_context_without_sheet_url_or_raw_rows() -> None:
    request = ReadRequest(
        request_id="read:group-public-projection",
        kind=ReadKind.ACTIVITY,
        product_id="product:buracao",
        activity_date=date(2026, 11, 18),
        adults=1,
        children=0,
    )
    source_url = "https://sheets.example.invalid/private.csv"
    raw_line = "18/11/2026,Nome Privado,Buracão,3"
    observation = ReadObservation(
        request_hash=request.canonical_hash(),
        provider="bokun",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        public_payload={
            "offer_id": "offer:" + "b" * 64,
            "product_public_name": "Buracão",
            "start_time": "08:00",
            "total_amount": "334.95",
            "currency": "BRL",
            "available": True,
            "group_status": "matched",
            "existing_group": True,
            "group_participants": 3,
            "solo_group_booking": True,
            "source_url": source_url,
            "raw_rows": [raw_line],
        },
        private_binding_hash="c" * 64,
    )
    projection = ConversationProjection(
        stage=ConversationStage.AGENCY,
        desired_services=(DesiredService.AGENCY,),
        locale="pt-BR",
        facts=(),
        reservation_execution_projection=None,
    )

    bridged = bridge_availability_observation(
        request,
        observation,
        lead_id="lead:group-public-projection",
        aggregate_turn_id="turn:group-public-projection",
        source_event=SourceEventIdentity("event:group-public-projection", "d" * 64),
        deadline_at=NOW + timedelta(minutes=1),
        locale="pt-BR",
        projection=projection,
        frame_commitment_hash="e" * 64,
    )

    result = SanitizedLookupResult.from_canonical_bytes(bridged.typed_result_bytes)
    offer = result.offers[0]
    assert (
        offer.group_status,
        offer.existing_group,
        offer.group_participants,
        offer.solo_group_booking,
    ) == ("matched", True, 3, True)
    assert source_url.encode() not in bridged.typed_result_bytes
    assert raw_line.encode() not in bridged.typed_result_bytes
    assert b"source_url" not in bridged.typed_result_bytes
    assert b"raw_rows" not in bridged.typed_result_bytes


@pytest.mark.parametrize(
    "group_context",
    (
        {
            "group_status": "matched",
            "existing_group": False,
            "group_participants": 3,
            "solo_group_booking": False,
        },
        {
            "group_status": "not_matched",
            "existing_group": True,
            "group_participants": None,
            "solo_group_booking": False,
        },
        {
            "group_status": "unavailable",
            "existing_group": False,
            "group_participants": 1,
            "solo_group_booking": False,
        },
        {
            "group_status": "matched",
            "existing_group": True,
            "group_participants": 2,
            "solo_group_booking": True,
        },
    ),
)
def test_bridge_rejects_contradictory_activity_group_context(
    group_context: dict[str, object],
) -> None:
    request = ReadRequest(
        request_id="read:contradictory-group-context",
        kind=ReadKind.ACTIVITY,
        product_id="product:buracao",
        activity_date=date(2026, 11, 18),
        adults=2,
        children=0,
    )
    observation = ReadObservation(
        request_hash=request.canonical_hash(),
        provider="bokun",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        public_payload={
            "offer_id": "offer:" + "f" * 64,
            "product_public_name": "Buracão",
            "total_amount": "669.90",
            "currency": "BRL",
            "available": True,
        }
        | group_context,
        private_binding_hash="1" * 64,
    )
    projection = ConversationProjection(
        stage=ConversationStage.AGENCY,
        desired_services=(DesiredService.AGENCY,),
        locale="pt-BR",
        facts=(),
        reservation_execution_projection=None,
    )

    with pytest.raises(ReadBridgeError, match="offer"):
        bridge_availability_observation(
            request,
            observation,
            lead_id="lead:contradictory-group-context",
            aggregate_turn_id="turn:contradictory-group-context",
            source_event=SourceEventIdentity(
                "event:contradictory-group-context", "2" * 64
            ),
            deadline_at=NOW + timedelta(minutes=1),
            locale="pt-BR",
            projection=projection,
            frame_commitment_hash="3" * 64,
        )


def test_bridge_rejects_solo_group_booking_outside_closed_product_policy() -> None:
    request = ReadRequest(
        request_id="read:solo-outside-policy",
        kind=ReadKind.ACTIVITY,
        product_id="product:tour-4ps",
        activity_date=date(2026, 11, 18),
        adults=1,
        children=0,
    )
    observation = ReadObservation(
        request_hash=request.canonical_hash(),
        provider="bokun",
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        public_payload={
            "offer_id": "offer:" + "4" * 64,
            "product_public_name": "Roteiro dos 4Ps",
            "total_amount": "334.95",
            "currency": "BRL",
            "available": True,
            "group_status": "matched",
            "existing_group": True,
            "group_participants": 2,
            "solo_group_booking": True,
        },
        private_binding_hash="5" * 64,
    )
    projection = ConversationProjection(
        stage=ConversationStage.AGENCY,
        desired_services=(DesiredService.AGENCY,),
        locale="pt-BR",
        facts=(),
        reservation_execution_projection=None,
    )

    with pytest.raises(ReadBridgeError, match="solo"):
        bridge_availability_observation(
            request,
            observation,
            lead_id="lead:solo-outside-policy",
            aggregate_turn_id="turn:solo-outside-policy",
            source_event=SourceEventIdentity("event:solo-outside-policy", "6" * 64),
            deadline_at=NOW + timedelta(minutes=1),
            locale="pt-BR",
            projection=projection,
            frame_commitment_hash="7" * 64,
        )


def test_lodging_bridge_rejects_group_context_even_when_values_are_null() -> None:
    request = ReadRequest(
        request_id="read:lodging-group-context",
        kind=ReadKind.LODGING,
        check_in=date(2026, 9, 14),
        check_out=date(2026, 9, 15),
        adults=1,
        children=1,
    )
    observation = _lodging_observation(request, available_units=1)
    observation.public_payload["options"][0].update(
        {
            "group_status": None,
            "existing_group": None,
            "group_participants": None,
            "solo_group_booking": None,
        }
    )
    projection = ConversationProjection(
        stage=ConversationStage.HOSTEL,
        desired_services=(DesiredService.HOSTEL,),
        locale="pt-BR",
        facts=(),
        reservation_execution_projection=None,
    )

    with pytest.raises(ReadBridgeError, match="lodging.*group"):
        bridge_availability_observation(
            request,
            observation,
            lead_id="lead:lodging-group-context",
            aggregate_turn_id="turn:lodging-group-context",
            source_event=SourceEventIdentity("event:lodging-group-context", "8" * 64),
            deadline_at=NOW + timedelta(minutes=1),
            locale="pt-BR",
            projection=projection,
            frame_commitment_hash="9" * 64,
        )
