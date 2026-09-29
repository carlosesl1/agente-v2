"""Controlled HTTP fixtures: inventory facts are not group quotes or bookable offers."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from v2_adapters.cloudbeds import CloudbedsReadAdapter
from v2_adapters.hermes_model import _choice_projection
from v2_adapters.provider_http import CloudbedsHTTPTransport, ProviderHTTPError
from v2_application.conversation import ConversationReductionError, _selected_payload
from v2_contracts.providers import ReadKind, ReadRequest

QUERY = {
    "check_in": "2026-11-08",
    "check_out": "2026-11-11",
    "adults": 3,
    "children": 0,
}
ROOMS = [
    {
        "roomTypeID": "double",
        "roomTypeName": "Privativo A",
        "roomsAvailable": 1,
        "maxGuests": "2",
        "roomTypeDescription": "Uma cama de casal.",
        "roomRateID": "r-a",
        "roomRate": 450,
    },
    {
        "roomTypeID": "single",
        "roomTypeName": "Privativo B",
        "roomsAvailable": 1,
        "maxGuests": "1",
        "roomTypeDescription": "Uma cama solteiro.",
        "roomRateID": "r-b",
        "roomRate": 300,
    },
    {
        "roomTypeID": "shared",
        "roomTypeName": "Compartilhado misto",
        "roomsAvailable": 2,
        "maxGuests": "4",
        "roomTypeDescription": "Camas individuais.",
        "roomRateID": "r-c",
        "roomRate": 150,
    },
]
CATALOG = [
    {
        "roomTypeID": r["roomTypeID"],
        "isPrivate": r["roomTypeID"] != "shared",
        "maxGuests": int(r["maxGuests"]),
    }
    for r in ROOMS
]


def harness(*, rooms=None, catalog=None, direct=None, status=200, body=None):
    seen = []

    def handler(req):
        seen.append(req)
        assert req.method == "GET"
        if req.url.path.endswith("getRatePlans"):
            return httpx.Response(
                200, json={"success": False, "message": "No rate found"}
            )
        if req.url.path.endswith("getRoomTypes"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": deepcopy(CATALOG if catalog is None else catalog),
                },
            )
        assert req.url.path.endswith("getAvailableRoomTypes")
        if body is not None:
            return httpx.Response(status, json=body)
        selected = (
            (ROOMS if rooms is None else rooms)
            if req.url.params.get("adults") == "1"
            else (direct or [])
        )
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {
                        "propertyID": "property-1",
                        "propertyCurrency": {"currencyCode": "BRL"},
                        "propertyRooms": deepcopy(selected),
                    }
                ]
                if selected
                else [],
            },
        )

    transport = CloudbedsHTTPTransport(
        api_key="fixture",
        property_id="property-1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter = CloudbedsReadAdapter(
        transport=transport,
        clock=SimpleNamespace(now=lambda: datetime(2026, 9, 29, tzinfo=UTC)),
        ttl=timedelta(minutes=5),
    )
    return transport, adapter, seen


def read(adapter, adults=3, children=0):
    return adapter.read(
        ReadRequest(
            request_id="read:party",
            kind=ReadKind.LODGING,
            check_in=date(2026, 11, 8),
            check_out=date(2026, 11, 11),
            adults=adults,
            children=children,
        )
    )


def test_empty_exact_occupancy_does_not_call_unused_failing_rates():
    transport, _, seen = harness()
    assert transport("lodging", QUERY) == {"options": []}
    assert [r.url.path for r in seen] == ["/api/v1.3/getAvailableRoomTypes"]


def test_group_observation_includes_different_items_without_group_quote_or_choices():
    _, adapter, seen = harness()
    result = read(adapter)
    assert result.public_payload["options"] == []
    inventory = result.public_payload["unit_inventory"]
    assert (
        inventory["check_in"] == QUERY["check_in"]
        and inventory["check_out"] == QUERY["check_out"]
    )
    assert inventory["requested_party"] == {"adults": 3, "children": 0}
    assert inventory["probe_party"] == {"adults": 1, "children": 0}
    assert inventory["allocation_status"] == "not_quoted"
    units = inventory["units"]
    assert [
        (u["is_private"], u["available_units"], u["max_guests_per_private_unit"])
        for u in units
    ] == [(True, 1, 2), (True, 1, 1), (False, 2, None)]
    assert len({u["inventory_ref"] for u in units}) == 3
    assert all("total_amount" not in u and "offer_id" not in u for u in units)
    payloads, choices = _choice_projection((result,))
    assert not choices and payloads[0]["unit_inventory"] == inventory
    with pytest.raises(ConversationReductionError):
        _selected_payload(result, units[0]["inventory_ref"])
    probe = next(
        r
        for r in seen
        if r.url.path.endswith("getAvailableRoomTypes")
        and r.url.params["adults"] == "1"
    )
    assert probe.url.params["children"] == "0"
    assert probe.url.params["includeSharedRooms"] == "true"
    assert probe.url.params["rooms"] == "1"
    assert probe.url.params["detailedRates"] == "false"


def test_inventory_does_not_sum_same_units_across_rate_variants():
    rooms = deepcopy(ROOMS)
    duplicate = dict(rooms[0], roomRateID="other-rate", roomRate=999)
    _, adapter, _ = harness(rooms=rooms + [duplicate])
    units = read(adapter).public_payload["unit_inventory"]["units"]
    assert len(units) == 3 and units[0]["available_units"] == 1


@pytest.mark.parametrize(
    "key,value", [("roomsAvailable", 2), ("maxGuests", "9"), ("roomTypeName", "Other")]
)
def test_conflicting_duplicate_inventory_is_not_published(key, value):
    rooms = deepcopy(ROOMS)
    duplicate = dict(rooms[0], **{key: value})
    _, adapter, _ = harness(rooms=rooms + [duplicate])
    with pytest.raises(ProviderHTTPError, match="inventory"):
        read(adapter)


@pytest.mark.parametrize(
    "key,value",
    [
        ("roomsAvailable", True),
        ("roomsAvailable", -1),
        ("maxGuests", None),
        ("roomTypeName", 42),
    ],
)
def test_invalid_inventory_fields_are_not_sold_out(key, value):
    rooms = deepcopy(ROOMS)
    rooms[0][key] = value
    _, adapter, _ = harness(rooms=rooms)
    with pytest.raises(ProviderHTTPError, match="inventory"):
        read(adapter)


def test_unknown_private_shared_classification_is_not_guessed_from_name():
    _, adapter, _ = harness(catalog=[])
    with pytest.raises(ProviderHTTPError, match="inventory"):
        read(adapter)


def test_zero_stock_is_not_counted_and_empty_inventory_is_legitimate():
    rooms = [dict(r, roomsAvailable=0) for r in ROOMS]
    _, adapter, _ = harness(rooms=rooms)
    assert read(adapter).public_payload["unit_inventory"]["units"] == []
    _, adapter, _ = harness(rooms=[])
    assert read(adapter).public_payload["unit_inventory"]["units"] == []


def test_children_are_preserved_in_request_not_assumed_covered_by_adult_probe():
    _, adapter, _ = harness()
    inventory = read(adapter, adults=2, children=1).public_payload["unit_inventory"]
    assert inventory["requested_party"] == {"adults": 2, "children": 1}
    assert inventory["probe_party"] == {"adults": 1, "children": 0}
    assert inventory["allocation_status"] == "not_quoted"


@pytest.mark.parametrize(
    "status,body",
    [(401, {"error": "auth"}), (200, {"success": False, "message": "backend failure"})],
)
def test_real_provider_errors_still_raise(status, body):
    transport, _, _ = harness(status=status, body=body)
    with pytest.raises(ProviderHTTPError):
        transport("lodging", QUERY)


@pytest.mark.parametrize(
    "body",
    [
        {"success": True},
        {"success": True, "data": {}},
        {"success": True, "data": [None]},
    ],
)
def test_malformed_availability_is_not_a_legitimate_empty_result(body):
    transport, _, _ = harness(body=body)
    with pytest.raises(ProviderHTTPError):
        transport("lodging", QUERY)


def test_public_inventory_rejects_changed_dates_and_injected_offer_authority():
    from v2_adapters._provider_common import ProviderReadError
    from v2_adapters.cloudbeds_inventory import public_unit_inventory

    _, adapter, _ = harness()
    inventory = read(adapter).public_payload["unit_inventory"]
    mutated = deepcopy(inventory)
    mutated["check_in"] = "2026-11-09"
    with pytest.raises(ProviderReadError):
        public_unit_inventory(mutated, query=QUERY)
    mutated = deepcopy(inventory)
    mutated["units"][0]["offer_id"] = "offer:forged"
    with pytest.raises(ProviderReadError):
        public_unit_inventory(mutated, query=QUERY)


def test_empty_single_item_quote_with_inventory_completes_real_turn_executor():
    import test_v2_turn_executor as h

    from v2_adapters.cloudbeds import CloudbedsReadAdapter
    from v2_application.reads import V2ReadService
    from v2_contracts.model import ModelFact, ModelProposal

    store = h.SQLiteBoundaryStore.open_memory_v8()
    transport, _, seen = harness()
    adapter = CloudbedsReadAdapter(
        transport=transport, clock=h.FixedClock(), ttl=timedelta(minutes=5)
    )
    request = ReadRequest(
        request_id="read:party-executor",
        kind=ReadKind.LODGING,
        check_in=date(2026, 11, 8),
        check_out=date(2026, 11, 11),
        adults=3,
        children=0,
    )
    initial = ModelProposal(
        source_event_id=h.BATCH.batch_id,
        intent="inform",
        reply_chunks=(),
        facts=(
            ModelFact("service", "hostel"),
            ModelFact("adults", 3),
            ModelFact("children", 0),
        ),
        read_requests=(request,),
        effect_proposals=(),
    )
    final = ModelProposal(
        source_event_id=h.BATCH.batch_id,
        intent="inform",
        reply_chunks=(
            "Podemos considerar dois privativos, com duas pessoas em um e uma no outro; aceita essa divisão?",
        ),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )
    model = h.FakeAuditedModel(store, [initial, final])
    h._install_public_authority(store)
    executor = h._executor(
        store=store,
        model=model,
        profile=h.FakeProfile(store),
        reads=V2ReadService({ReadKind.LODGING: adapter}),
    )
    try:
        result = executor.execute(h.BATCH)
        assert result.reply_chunks == final.reply_chunks
        assert result.receipt.command_rows == result.receipt.relay_rows == ()
        assert len(model.calls) == 2
        payload = model.calls[1].observations[0].public_payload
        assert payload["options"] == [] and len(payload["unit_inventory"]["units"]) == 3
        projection = store.load_latest_conversation_projection(h.BATCH.lead_id)
        assert {f.name: f.value.value for f in projection.facts}["adults"] == 3
        assert len(seen) == 3
    finally:
        store.close()


def test_resolver_style_read_and_single_person_remain_free_of_inventory_calls():
    transport, adapter, seen = harness(direct=[ROOMS[0]])
    result = transport("lodging", QUERY)
    assert "unit_inventory" not in result
    assert len(seen) == 1
    seen.clear()
    result = read(adapter, adults=1)
    assert "unit_inventory" not in result.public_payload
    assert len(seen) == 1
