"""Strict versioned JSON serialization for the pure domain."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
import json
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

from .types import (
    EVENT_TYPES,
    SCHEMA_VERSION,
    STATE_TYPES,
    DomainEvent,
    Event,
    ReservationCommand,
    State,
    WorkflowState,
    validate_state_consistency,
)

_STATE_BY_TAG = {item.TYPE: item for item in STATE_TYPES}
_EVENT_BY_TAG = {item.TYPE: item for item in EVENT_TYPES}
_TOP_LEVEL_KEYS = {"schema_version", "type", "data"}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _encode(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"unsupported serialized value: {type(value).__name__}")


def _decode_dataclass(cls: type, value: Any):
    if not isinstance(value, dict):
        raise ValueError(f"{cls.__name__} data must be an object")
    expected = {field.name for field in fields(cls)}
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"{cls.__name__} fields mismatch; missing={missing}, unknown={unknown}"
        )
    hints = get_type_hints(cls)
    return cls(
        **{
            field.name: _decode_value(hints[field.name], value[field.name])
            for field in fields(cls)
        }
    )


def _decode_value(annotation: Any, value: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError("tuple field must be a JSON array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode_value(args[0], item) for item in value)
        if len(args) != len(value):
            raise ValueError("fixed tuple length mismatch")
        return tuple(_decode_value(item_type, item) for item_type, item in zip(args, value))
    if origin in {Union, types.UnionType}:
        if value is None and type(None) in args:
            return None
        failures: list[str] = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _decode_value(option, value)
            except (TypeError, ValueError) as exc:
                failures.append(str(exc))
        raise ValueError(f"value does not match union: {failures}")
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {annotation.__name__}: {value!r}") from exc
    if annotation is datetime:
        if not isinstance(value, str):
            raise ValueError("datetime field must be a string")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid ISO datetime") from exc
        if (
            parsed.tzinfo is None
            or parsed.utcoffset() != timedelta(0)
            or parsed.isoformat() != value
        ):
            raise ValueError("datetime field must use canonical UTC ISO format")
        return parsed
    if annotation is date:
        if not isinstance(value, str):
            raise ValueError("date field must be a string")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid ISO date") from exc
        if parsed.isoformat() != value:
            raise ValueError("date field must use canonical ISO format")
        return parsed
    if annotation is Decimal:
        if not isinstance(value, str):
            raise ValueError("decimal field must be a string")
        try:
            parsed = Decimal(value)
        except Exception as exc:
            raise ValueError("invalid decimal") from exc
        if not parsed.is_finite() or format(parsed, "f") != value:
            raise ValueError("decimal field must use canonical finite format")
        return parsed
    if is_dataclass(annotation):
        return _decode_dataclass(annotation, value)
    if annotation is str:
        if not isinstance(value, str):
            raise ValueError("string field has wrong type")
        return value
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("integer field has wrong type")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise ValueError("boolean field has wrong type")
        return value
    raise TypeError(f"unsupported field annotation: {annotation!r}")


def _dumps(value: Any, type_tag: str) -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "type": type_tag,
            "data": _encode(value),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _loads_payload(raw: str) -> tuple[str, dict[str, Any]]:
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("serialized payload must be an object")
    if set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("serialized payload has missing or unknown fields")
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("unsupported schema_version")
    type_tag = payload["type"]
    if not isinstance(type_tag, str):
        raise ValueError("type tag must be a string")
    data = payload["data"]
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    return type_tag, data


def dumps_state(state: State) -> str:
    if type(state) not in STATE_TYPES:
        raise TypeError("state must be an exact closed-universe state type")
    validate_state_consistency(state)
    return _dumps(state, state.TYPE)


def loads_state(raw: str) -> State:
    type_tag, data = _loads_payload(raw)
    cls = _STATE_BY_TAG.get(type_tag)
    if cls is None:
        raise ValueError(f"unknown state type: {type_tag}")
    state = _decode_dataclass(cls, data)
    validate_state_consistency(state)
    return state


def dumps_event(event: Event) -> str:
    if type(event) not in EVENT_TYPES:
        raise TypeError("event must be an exact closed-universe event type")
    return _dumps(event, event.TYPE)


def loads_event(raw: str) -> Event:
    type_tag, data = _loads_payload(raw)
    cls = _EVENT_BY_TAG.get(type_tag)
    if cls is None:
        raise ValueError(f"unknown event type: {type_tag}")
    return _decode_dataclass(cls, data)


def dumps_command(command: ReservationCommand) -> str:
    if type(command) is not ReservationCommand:
        raise TypeError("command must be the exact ReservationCommand type")
    return _dumps(command, command.TYPE)


def loads_command(raw: str) -> ReservationCommand:
    type_tag, data = _loads_payload(raw)
    if type_tag != ReservationCommand.TYPE:
        raise ValueError(f"unknown command type: {type_tag}")
    return _decode_dataclass(ReservationCommand, data)
