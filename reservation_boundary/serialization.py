"""Duplicate-safe canonical JSON for Phase 7 boundary envelopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from functools import lru_cache
import hashlib
import json
import math
import types
from typing import Final, Union, get_args, get_origin, get_type_hints

from reservation_domain import (
    ReservationCommand,
    STATE_TYPES,
    WorkflowState,
    dumps_command,
    dumps_state,
    loads_command,
    loads_state,
)
from reservation_execution import OutboxMessage
from reservation_followup import (
    HandoffWorkflow,
    PaymentSettlementCommand,
    PaymentWorkflow,
    from_wire_json as from_phase6_wire_json,
    to_wire_json as to_phase6_wire_json,
)

from reservation_boundary.types import (
    BoundaryCommit,
    BoundaryState,
    ConversationIntent,
    ImportResult,
    IntentRequest,
    KernelDecision,
    LegacyLeadSnapshot,
    PUBLIC_TYPES as BOUNDARY_TYPES,
    ToolDispatchRequest,
    TypedFact,
    TurnEnvelope,
    TurnLease,
    TurnPlan,
    VersionedBoundaryState,
)


SCHEMA_VERSION: Final = 1
_TOP_LEVEL_KEYS: Final = frozenset(("schema_version", "type", "data"))
PUBLIC_TYPES: Final = {
    "LegacyLeadSnapshot": LegacyLeadSnapshot,
    "ImportResult": ImportResult,
    "BoundaryState": BoundaryState,
    "ConversationIntent": ConversationIntent,
    "IntentRequest": IntentRequest,
    "ToolDispatchRequest": ToolDispatchRequest,
    "KernelDecision": KernelDecision,
    "TurnLease": TurnLease,
    "VersionedBoundaryState": VersionedBoundaryState,
    "BoundaryCommit": BoundaryCommit,
    "TurnEnvelope": TurnEnvelope,
    "TurnPlan": TurnPlan,
}
_TYPE_TAGS: Final = {
    LegacyLeadSnapshot: "legacy_lead_snapshot",
    ImportResult: "import_result",
    BoundaryState: "boundary_state",
    ConversationIntent: "conversation_intent",
    IntentRequest: "intent_request",
    ToolDispatchRequest: "tool_dispatch_request",
    KernelDecision: "kernel_decision",
    TurnLease: "turn_lease",
    VersionedBoundaryState: "versioned_boundary_state",
    BoundaryCommit: "boundary_commit",
    TurnEnvelope: "turn_envelope",
    TurnPlan: "turn_plan",
}
_TAG_TYPES: Final = {tag: cls for cls, tag in _TYPE_TAGS.items()}
_BOUNDARY_DATACLASSES: Final = frozenset(
    item for item in BOUNDARY_TYPES if is_dataclass(item)
)
_BOUNDARY_BY_TAG: Final = {item.__name__: item for item in _BOUNDARY_DATACLASSES}
_PHASE6_TYPES: Final = frozenset(
    (HandoffWorkflow, PaymentWorkflow, PaymentSettlementCommand)
)
_PHASE6_TAGS: Final = {
    HandoffWorkflow: "phase6_handoff_workflow",
    PaymentWorkflow: "phase6_payment_workflow",
    PaymentSettlementCommand: "phase6_payment_settlement_command",
}


@lru_cache(maxsize=None)
def _hints(cls: type[object]) -> types.MappingProxyType[str, object]:
    return types.MappingProxyType(get_type_hints(cls))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _tagged(type_tag: str, *, data: object | None = None, wire: str | None = None) -> dict[str, object]:
    if (data is None) == (wire is None):
        raise ValueError("tagged values require exactly one of data or wire")
    return {"$type": type_tag, "data": data} if wire is None else {"$type": type_tag, "wire": wire}


def _encode_dataclass(cls: type[object], value: object) -> dict[str, object]:
    if type(value) is not cls:
        raise ValueError(f"value must be the exact {cls.__name__} type")
    hints = _hints(cls)
    if cls is TypedFact:
        if value.frame_commitment_hash is not None:
            raise ValueError("Phase 8 TypedFact cannot be downgraded to Phase 7 wire")
        encoded = {
            "name": _encode_value(hints["name"], value.name),
            "value": _encode_value(hints["value"], value.value),
        }
        reconstructed = _decode_dataclass(cls, encoded)
        if reconstructed != value:
            raise ValueError("TypedFact contains noncanonical Phase 7 field values")
        return encoded
    encoded = {
        field.name: _encode_value(hints[field.name], getattr(value, field.name))
        for field in fields(cls)
    }
    reconstructed = _decode_dataclass(cls, encoded)
    if reconstructed != value:
        raise ValueError(f"{cls.__name__} contains noncanonical field values")
    return encoded


def _encode_value(annotation: object, value: object) -> object:
    if value is None:
        return None
    value_type = type(value)
    if value_type in STATE_TYPES:
        return _tagged("reservation_domain_state", wire=dumps_state(value))
    if value_type is ReservationCommand:
        return _tagged("reservation_domain_command", wire=dumps_command(value))
    if value_type in _PHASE6_TYPES:
        return _tagged(_PHASE6_TAGS[value_type], wire=to_phase6_wire_json(value))
    if value_type is OutboxMessage:
        return _tagged(
            "phase5_outbox_message",
            data=_encode_dataclass(OutboxMessage, value),
        )
    if value_type in _BOUNDARY_DATACLASSES:
        return _tagged(value_type.__name__, data=_encode_dataclass(value_type, value))
    if isinstance(value, Enum):
        return value.value
    if value_type is datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be timezone-aware UTC")
        return value.isoformat()
    if value_type is date:
        return value.isoformat()
    if value_type is tuple:
        return [_encode_value(object, item) for item in value]
    if isinstance(value, Mapping):
        encoded: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("mapping key must be an exact string")
            encoded[key] = _encode_value(object, item)
        return encoded
    if value_type in (str, int, bool):
        return value
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError("float must be finite")
        return value
    raise TypeError(f"unsupported boundary wire value: {value_type.__name__}")


def _decode_tagged(value: object, expected_tag: str, *, key: str) -> object:
    if type(value) is not dict or set(value) != {"$type", key}:
        raise ValueError(f"{expected_tag} must use the exact tagged {key} envelope")
    if value["$type"] != expected_tag:
        raise ValueError(f"nested type mismatch: expected {expected_tag!r}")
    return value[key]


def _decode_dataclass(cls: type[object], value: object) -> object:
    if type(value) is not dict:
        raise ValueError(f"{cls.__name__} data must be an object")
    expected = {"name", "value"} if cls is TypedFact else {field.name for field in fields(cls)}
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{cls.__name__} fields mismatch; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )
    hints = _hints(cls)
    try:
        if cls is TypedFact:
            return TypedFact(
                _decode_value(hints["name"], value["name"]),
                _decode_value(hints["value"], value["value"]),
            )
        return cls(
            **{
                field.name: _decode_value(hints[field.name], value[field.name])
                for field in fields(cls)
            }
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {cls.__name__}") from exc


def _decode_union(options: tuple[object, ...], value: object) -> object:
    if value is None and type(None) in options:
        return None
    successes: list[object] = []
    failures: list[str] = []
    for option in options:
        if option is type(None):
            continue
        try:
            successes.append(_decode_value(option, value))
        except (TypeError, ValueError) as exc:
            failures.append(str(exc))
    if len(successes) != 1:
        raise ValueError(
            f"value must match exactly one closed union member; "
            f"matches={len(successes)}, failures={failures}"
        )
    return successes[0]


def _decode_json_value(value: object) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON float must be finite")
        return value
    if type(value) is list:
        return tuple(_decode_json_value(item) for item in value)
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("JSON object keys must be exact strings")
        return {key: _decode_json_value(item) for key, item in value.items()}
    raise ValueError(f"unsupported JSON value type: {type(value).__name__}")


def _decode_value(annotation: object, value: object) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is tuple:
        if type(value) is not list:
            raise ValueError("tuple field must be a JSON array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode_value(args[0], item) for item in value)
        if len(args) != len(value):
            raise ValueError("fixed tuple length mismatch")
        return tuple(
            _decode_value(item_type, item)
            for item_type, item in zip(args, value, strict=True)
        )
    if origin in {Union, types.UnionType}:
        return _decode_union(args, value)
    if origin in {Mapping, dict}:
        if type(value) is not dict:
            raise ValueError("mapping field must be a JSON object")
        key_type, value_type = args
        if key_type is not str:
            raise TypeError("only string-key mappings are supported")
        return {key: _decode_value(value_type, item) for key, item in value.items()}
    if annotation is WorkflowState:
        wire = _decode_tagged(value, "reservation_domain_state", key="wire")
        if type(wire) is not str:
            raise ValueError("domain state wire must be text")
        return loads_state(wire)
    if annotation is ReservationCommand:
        wire = _decode_tagged(value, "reservation_domain_command", key="wire")
        if type(wire) is not str:
            raise ValueError("domain command wire must be text")
        return loads_command(wire)
    if annotation in _PHASE6_TYPES:
        wire = _decode_tagged(value, _PHASE6_TAGS[annotation], key="wire")
        if type(wire) is not str:
            raise ValueError("Phase 6 wire must be text")
        return from_phase6_wire_json(wire, annotation)
    if annotation is OutboxMessage:
        data = _decode_tagged(value, "phase5_outbox_message", key="data")
        return _decode_dataclass(OutboxMessage, data)
    if annotation in _BOUNDARY_DATACLASSES:
        data = _decode_tagged(value, annotation.__name__, key="data")
        return _decode_dataclass(annotation, data)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(value) is not str:
            raise ValueError(f"{annotation.__name__} must be a string")
        try:
            member = annotation(value)
        except ValueError as exc:
            raise ValueError(f"invalid {annotation.__name__}") from exc
        if member.value != value:
            raise ValueError(f"noncanonical {annotation.__name__}")
        return member
    if annotation is datetime:
        if type(value) is not str:
            raise ValueError("datetime must be a string")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid datetime") from exc
        if (
            parsed.tzinfo is None
            or parsed.utcoffset() != timedelta(0)
            or parsed.isoformat() != value
        ):
            raise ValueError("datetime must use canonical UTC ISO format")
        return parsed
    if annotation is date:
        if type(value) is not str:
            raise ValueError("date must be a string")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid date") from exc
        if parsed.isoformat() != value:
            raise ValueError("date must use canonical ISO format")
        return parsed
    if annotation is str:
        if type(value) is not str:
            raise ValueError("string field has wrong exact type")
        return value
    if annotation is int:
        if type(value) is not int:
            raise ValueError("integer field has wrong exact type")
        return value
    if annotation is bool:
        if type(value) is not bool:
            raise ValueError("boolean field has wrong exact type")
        return value
    if annotation is float:
        if type(value) is not float or not math.isfinite(value):
            raise ValueError("float field has wrong exact type")
        return value
    if annotation is object:
        return _decode_json_value(value)
    raise TypeError(f"unsupported closed boundary annotation: {annotation!r}")


def to_wire_json(value: object) -> str:
    """Serialize one exact public Phase 7 envelope to canonical JSON."""

    value_type = type(value)
    type_tag = _TYPE_TAGS.get(value_type)
    if type_tag is None:
        raise TypeError("value must be an exact Phase 7 public envelope type")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "type": type_tag,
        "data": _encode_dataclass(value_type, value),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def from_wire_json(text: str, expected_type: type[object]) -> object:
    """Decode one exact public envelope and reject all wire drift."""

    expected_tag = _TYPE_TAGS.get(expected_type)
    if expected_tag is None:
        raise TypeError("expected_type must be an exact Phase 7 public envelope type")
    if type(text) is not str:
        raise ValueError("wire JSON must be text")
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("invalid wire JSON") from exc
    if type(payload) is not dict or set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("wire payload has missing or unknown fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    if type(payload["type"]) is not str or payload["type"] != expected_tag:
        raise ValueError("wire type does not match expected_type")
    decoded = _decode_dataclass(expected_type, payload["data"])
    if to_wire_json(decoded) != text:
        raise ValueError("wire JSON is valid but noncanonical")
    return decoded


def semantic_hash(value: object) -> str:
    """Return the SHA-256 of the exact canonical public envelope."""

    return hashlib.sha256(to_wire_json(value).encode("utf-8")).hexdigest()


__all__ = ("PUBLIC_TYPES", "from_wire_json", "semantic_hash", "to_wire_json")
