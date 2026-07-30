"""Parent-owned canonical passenger manifest handling for Maya V2."""

from __future__ import annotations

from datetime import date
import json
from typing import Final

from reservation_boundary import ConversationProjection, StringSlot, TypedFact
from reservation_domain import PassengerFacts, Party
from v2_contracts.passengers import (
    PASSENGER_FIELD_ORDER,
    PassengerInput,
    PassengerManifestStatus,
)


_MANIFEST_SCHEMA: Final = "v2-passenger-manifest-v1"
_MANIFEST_FIELDS: Final = frozenset(("schema", "adults", "children", "passengers"))
_PASSENGER_FIELDS: Final = frozenset(
    ("position", "participant_type", *PASSENGER_FIELD_ORDER)
)
_FACT_ORDER: Final = {
    "language": 0,
    "service": 1,
    "product_id": 2,
    "start_date": 3,
    "end_date": 4,
    "activity_date": 5,
    "adults": 6,
    "children": 7,
    "payment_method": 8,
    "full_name": 9,
    "email": 10,
    "phone_e164": 11,
    "country_code": 12,
    "birth_date": 13,
    "gender": 14,
    "passenger_manifest": 15,
    "critical_outcome": 16,
}


class PassengerManifestConflict(ValueError):
    """A new update contradicted an already captured private value."""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate passenger manifest key: {key}")
        result[key] = value
    return result


def _expected_type(position: int, party: Party) -> str:
    return "adult" if position <= party.adults else "child"


def _blank_manifest(party: Party) -> dict[str, object]:
    return {
        "schema": _MANIFEST_SCHEMA,
        "adults": party.adults,
        "children": party.children,
        "passengers": [
            {
                "position": position,
                "participant_type": _expected_type(position, party),
                "full_name": None,
                "birth_date": None,
                "gender": None,
                "country_code": None,
            }
            for position in range(1, party.adults + party.children + 1)
        ],
    }


def _load(manifest_json: str, party: Party) -> dict[str, object]:
    if type(manifest_json) is not str or not manifest_json:
        raise ValueError("passenger manifest must be non-empty exact JSON")
    try:
        decoded = json.loads(manifest_json, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("passenger manifest must be valid JSON") from exc
    if _canonical(decoded) != manifest_json:
        raise ValueError("passenger manifest must use canonical JSON")
    if type(decoded) is not dict or set(decoded) != _MANIFEST_FIELDS:
        raise ValueError("passenger manifest fields mismatch")
    if decoded["schema"] != _MANIFEST_SCHEMA:
        raise ValueError("passenger manifest schema mismatch")
    if decoded["adults"] != party.adults or decoded["children"] != party.children:
        raise ValueError("passenger manifest party mismatch")
    passengers = decoded["passengers"]
    total = party.adults + party.children
    if type(passengers) is not list or len(passengers) != total:
        raise ValueError("passenger manifest cardinality mismatch")
    for expected_position, passenger in enumerate(passengers, start=1):
        if type(passenger) is not dict or set(passenger) != _PASSENGER_FIELDS:
            raise ValueError("passenger manifest entry fields mismatch")
        if (
            passenger["position"] != expected_position
            or passenger["participant_type"]
            != _expected_type(expected_position, party)
        ):
            raise ValueError("passenger manifest position or type mismatch")
        for name in PASSENGER_FIELD_ORDER:
            value = passenger[name]
            if value is not None and type(value) is not str:
                raise ValueError("passenger manifest material values must be text or null")
        raw_birth = passenger["birth_date"]
        try:
            if raw_birth is not None and date.fromisoformat(raw_birth).isoformat() != raw_birth:
                raise ValueError("non-canonical birth date")
        except ValueError as exc:
            raise ValueError("passenger manifest birth_date is invalid") from exc
        if passenger["gender"] not in (None, "m", "f"):
            raise ValueError("passenger manifest gender is invalid")
        country = passenger["country_code"]
        if country is not None and (
            len(country) != 2 or not country.isascii() or not country.isupper()
        ):
            raise ValueError("passenger manifest country_code is invalid")
        name = passenger["full_name"]
        if name is not None and (not name or name != " ".join(name.split())):
            raise ValueError("passenger manifest full_name is invalid")
    return decoded


def merge_manifest(
    existing_json: str | None,
    updates: tuple[PassengerInput, ...],
    party: Party,
    *,
    allow_replacement: bool = False,
) -> str:
    """Merge partial updates into one canonical private manifest.

    Replacement is reserved for an explicit parent-authorized adjustment that
    revokes any pending proposal before a fresh summary can be signed.
    """

    if type(party) is not Party:
        raise TypeError("party must be an exact Party")
    if type(updates) is not tuple or any(
        type(item) is not PassengerInput for item in updates
    ):
        raise TypeError("updates must be an exact PassengerInput tuple")
    if type(allow_replacement) is not bool:
        raise TypeError("allow_replacement must be an exact bool")
    positions = tuple(item.position for item in updates)
    if len(positions) != len(set(positions)):
        raise ValueError("passenger updates must have unique positions")
    current = _blank_manifest(party)
    if existing_json is not None:
        if type(existing_json) is not str:
            raise TypeError("existing manifest must be exact JSON text or None")
        try:
            current = _load(existing_json, party)
        except ValueError as exc:
            if "party mismatch" not in str(exc):
                raise
    passengers = current["passengers"]
    assert type(passengers) is list
    total = party.adults + party.children
    for update in updates:
        if update.position > total:
            raise ValueError("passenger update position exceeds party")
        expected_type = _expected_type(update.position, party)
        if update.participant_type != expected_type:
            raise ValueError("passenger update type does not match party position")
        row = passengers[update.position - 1]
        assert type(row) is dict
        for name in PASSENGER_FIELD_ORDER:
            value = getattr(update, name)
            if type(value) is date:
                value = value.isoformat()
            if value is None:
                continue
            prior = row[name]
            if prior is not None and prior != value:
                if not allow_replacement:
                    raise PassengerManifestConflict(
                        f"passenger update conflicts at position {update.position} field {name}"
                    )
            row[name] = value
    canonical = _canonical(current)
    _load(canonical, party)
    return canonical


def manifest_status(
    manifest_json: str,
    party: Party,
) -> PassengerManifestStatus:
    decoded = _load(manifest_json, party)
    passengers = decoded["passengers"]
    assert type(passengers) is list
    complete: list[int] = []
    missing: list[tuple[int, tuple[str, ...]]] = []
    for row in passengers:
        assert type(row) is dict
        absent = tuple(name for name in PASSENGER_FIELD_ORDER if row[name] is None)
        position = row["position"]
        assert type(position) is int
        if absent:
            missing.append((position, absent))
        else:
            complete.append(position)
    return PassengerManifestStatus(
        required_adults=party.adults,
        required_children=party.children,
        complete_positions=tuple(complete),
        missing_by_position=tuple(missing),
    )


def complete_manifest(
    manifest_json: str,
    party: Party,
) -> tuple[PassengerFacts, ...] | None:
    decoded = _load(manifest_json, party)
    status = manifest_status(manifest_json, party)
    if status.missing_by_position:
        return None
    passengers = decoded["passengers"]
    assert type(passengers) is list
    result: list[PassengerFacts] = []
    for row in passengers:
        assert type(row) is dict
        birth = row["birth_date"]
        assert type(birth) is str
        result.append(
            PassengerFacts(
                position=row["position"],
                participant_type=row["participant_type"],
                full_name=row["full_name"],
                birth_date=date.fromisoformat(birth),
                gender=row["gender"],
                country_code=row["country_code"],
            )
        )
    return tuple(result)


def projection_manifest_json(
    projection: ConversationProjection,
) -> str | None:
    if type(projection) is not ConversationProjection:
        raise TypeError("projection must be an exact ConversationProjection")
    matches = tuple(
        item for item in projection.facts if item.name == "passenger_manifest"
    )
    if not matches:
        return None
    if len(matches) != 1 or type(matches[0].value) is not StringSlot:
        raise ValueError("projection passenger manifest fact is invalid")
    return matches[0].value.value


def projection_manifest_party(
    projection: ConversationProjection,
) -> Party | None:
    manifest_json = projection_manifest_json(projection)
    if manifest_json is None:
        return None
    try:
        decoded = json.loads(manifest_json, object_pairs_hook=_unique_object)
        if type(decoded) is not dict:
            raise ValueError("passenger manifest root is invalid")
        party = Party(decoded.get("adults"), decoded.get("children"))
        _load(manifest_json, party)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("projection passenger manifest party is invalid") from exc
    return party


def merge_projection_manifest(
    projection: ConversationProjection,
    updates: tuple[PassengerInput, ...],
    party: Party | None,
    *,
    frame_commitment_hash: str,
    allow_replacement: bool = False,
) -> ConversationProjection:
    """Persist a canonical manifest without exposing it as model state facts."""

    if type(projection) is not ConversationProjection:
        raise TypeError("projection must be an exact ConversationProjection")
    if party is not None and type(party) is not Party:
        raise TypeError("party must be an exact Party or None")
    if type(allow_replacement) is not bool:
        raise TypeError("allow_replacement must be an exact bool")
    existing_json = projection_manifest_json(projection)
    if party is None:
        if updates:
            raise PassengerManifestConflict(
                "passenger updates require a complete activity party"
            )
        return projection
    if existing_json is None and not updates:
        return projection
    merged_json = merge_manifest(
        existing_json,
        updates,
        party,
        allow_replacement=allow_replacement,
    )
    facts = {item.name: item for item in projection.facts}
    facts["passenger_manifest"] = TypedFact(
        "passenger_manifest",
        StringSlot(merged_json),
        frame_commitment_hash,
    )
    return ConversationProjection(
        stage=projection.stage,
        desired_services=projection.desired_services,
        locale=projection.locale,
        facts=tuple(sorted(facts.values(), key=lambda item: _FACT_ORDER[item.name])),
        reservation_execution_projection=projection.reservation_execution_projection,
    )


def projection_manifest_status(
    projection: ConversationProjection,
    party: Party | None,
) -> PassengerManifestStatus | None:
    if party is None:
        return None
    manifest_json = projection_manifest_json(projection)
    if manifest_json is None:
        manifest_json = merge_manifest(None, (), party)
    return manifest_status(manifest_json, party)


def projection_passengers(
    projection: ConversationProjection,
    party: Party,
) -> tuple[PassengerFacts, ...] | None:
    manifest_json = projection_manifest_json(projection)
    if manifest_json is None:
        return None
    return complete_manifest(manifest_json, party)


__all__ = [
    "PassengerManifestConflict",
    "complete_manifest",
    "manifest_status",
    "merge_manifest",
    "merge_projection_manifest",
    "projection_manifest_json",
    "projection_manifest_party",
    "projection_manifest_status",
    "projection_passengers",
]
