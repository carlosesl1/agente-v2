"""Informational Cloudbeds unit inventory, separate from priced/selectable offers."""

from __future__ import annotations

from v2_adapters._provider_common import ProviderReadError, binding_hash

_ERROR = "Cloudbeds inventory response is invalid or ambiguous"


def _integer(value: object, *, minimum: int) -> int:
    if type(value) is str and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or value < minimum:
        raise ProviderReadError(_ERROR)
    return value


def _text(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ProviderReadError(_ERROR)
    return value


def _rows(response: object) -> list[dict]:
    if type(response) is not dict or type(response.get("data")) is not list:
        raise ProviderReadError(_ERROR)
    rows = response["data"]
    if any(type(row) is not dict for row in rows):
        raise ProviderReadError(_ERROR)
    return rows


def normalize_unit_inventory(
    available: object, catalog: object, *, property_id: str, query: dict
) -> dict:
    """Project native facts; never allocate a party or calculate a group price."""
    classifications = {}
    for row in _rows(catalog):
        room_type = _text(row.get("roomTypeID"))
        private = row.get("isPrivate")
        if type(private) is not bool:
            raise ProviderReadError(_ERROR)
        if room_type in classifications and classifications[room_type] != private:
            raise ProviderReadError(_ERROR)
        classifications[room_type] = private
    units = {}
    for property_row in _rows(available):
        if str(property_row.get("propertyID")) != property_id:
            raise ProviderReadError(_ERROR)
        rooms = property_row.get("propertyRooms")
        if type(rooms) is not list:
            raise ProviderReadError(_ERROR)
        for room in rooms:
            if type(room) is not dict:
                raise ProviderReadError(_ERROR)
            room_type = _text(room.get("roomTypeID"))
            if room_type not in classifications:
                raise ProviderReadError(_ERROR)
            private = classifications[room_type]
            count = _integer(room.get("roomsAvailable"), minimum=0)
            capacity = _integer(room.get("maxGuests"), minimum=1)
            description = room.get("roomTypeDescription", "")
            if type(description) is not str:
                raise ProviderReadError(_ERROR)
            unit = {
                "inventory_ref": "inventory:"
                + binding_hash(
                    {
                        "property": property_id,
                        "room_type": room_type,
                    }
                ),
                "room_public_name": _text(room.get("roomTypeName")),
                "description": description,
                "is_private": private,
                "available_units": count,
                "provider_max_guests": capacity,
                # Shared maxGuests can refer to the whole dorm, not each bed.
                "max_guests_per_private_unit": capacity if private else None,
            }
            if room_type in units and units[room_type] != unit:
                raise ProviderReadError(_ERROR)
            units[room_type] = unit
    return {
        "check_in": query["check_in"],
        "check_out": query["check_out"],
        "requested_party": {"adults": query["adults"], "children": query["children"]},
        "probe_party": {"adults": 1, "children": 0},
        "allocation_status": "not_quoted",
        "units": [unit for unit in units.values() if unit["available_units"] > 0],
    }


def public_unit_inventory(value: object, *, query: dict) -> dict:
    """Validate the closed informational DTO and its stay/party binding."""
    fields = {
        "check_in",
        "check_out",
        "requested_party",
        "probe_party",
        "allocation_status",
        "units",
    }
    if type(value) is not dict or set(value) != fields:
        raise ProviderReadError(_ERROR)
    if (
        value["check_in"] != query["check_in"]
        or value["check_out"] != query["check_out"]
        or value["requested_party"]
        != {"adults": query["adults"], "children": query["children"]}
        or value["probe_party"] != {"adults": 1, "children": 0}
        or value["allocation_status"] != "not_quoted"
        or type(value["units"]) is not list
    ):
        raise ProviderReadError(_ERROR)
    seen = set()
    unit_fields = {
        "inventory_ref",
        "room_public_name",
        "description",
        "is_private",
        "available_units",
        "provider_max_guests",
        "max_guests_per_private_unit",
    }
    for unit in value["units"]:
        if type(unit) is not dict or set(unit) != unit_fields:
            raise ProviderReadError(_ERROR)
        ref = _text(unit["inventory_ref"])
        if not ref.startswith("inventory:") or ref in seen:
            raise ProviderReadError(_ERROR)
        seen.add(ref)
        _text(unit["room_public_name"])
        if type(unit["description"]) is not str or type(unit["is_private"]) is not bool:
            raise ProviderReadError(_ERROR)
        for key in ("available_units", "provider_max_guests"):
            if type(unit[key]) is not int or unit[key] < 1:
                raise ProviderReadError(_ERROR)
        capacity = unit["max_guests_per_private_unit"]
        if (
            unit["is_private"]
            and (type(capacity) is not int or capacity != unit["provider_max_guests"])
        ) or (not unit["is_private"] and capacity is not None):
            raise ProviderReadError(_ERROR)
    return value
