from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

from reservation_boundary.conversation import (
    ConversationProjection,
    ConversationStage,
    DesiredService,
    SourceEventIdentity,
)
from reservation_boundary.reads import SanitizedLookupResult
from v2_application.read_bridge import bridge_availability_observation
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


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
