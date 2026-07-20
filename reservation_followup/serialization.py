"""Duplicate-safe canonical wire JSON for Phase 6 shared contracts."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
import hashlib
import json
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

from reservation_domain import ExecutionCertainty, ExecutionOutcome, ServiceKind

from .payment import (
    FinancialConfirmationReceived,
    FinancialSummaryRecorded,
    PaymentCancelled,
    PaymentEvidenceRecorded,
    PaymentEvidenceTrust,
    PaymentExpired,
    PaymentMethodSelected,
    PaymentSettlementCommand,
    PaymentWorkflow,
    PixProofStatus,
    PixVisualEvidence,
    SettlementFinished,
    SettlementOperation,
    SettlementOutcome,
    SettlementStarted,
    StripeEventType,
    VerifiedPaymentEvidence,
    VerifiedStripeEvent,
    VerifiedWiseCredit,
)
from .handoff import (
    HandoffAcknowledged,
    HandoffCancellationCode,
    HandoffCancelled,
    HandoffEffectFailed,
    HandoffEffectFailureCode,
    HandoffEffectJob,
    HandoffEffectKind,
    HandoffReasonCode,
    HandoffRequested,
    HandoffWorkflow,
    PublicHandoffProjection,
    PublicNextAction,
)
from .projection import PaymentEffectJob, PaymentEffectKind
from .types import (
    BusinessUnit,
    ConfirmedReservationAnchor,
    EffectRequirement,
    HandoffEffectPolicy,
    HandoffReceipt,
    HandoffStatus,
    PaymentEffectPolicy,
    PaymentMethod,
    PaymentReceipt,
    PaymentStatus,
    PaymentSubject,
    SettlementCertainty,
)

SCHEMA_VERSION = 1
_TOP_LEVEL_KEYS = {"schema_version", "type", "data"}
_TYPE_TAGS = {
    ConfirmedReservationAnchor: "confirmed_reservation_anchor",
    HandoffEffectPolicy: "handoff_effect_policy",
    PaymentEffectPolicy: "payment_effect_policy",
    PaymentSubject: "payment_subject",
    HandoffRequested: "handoff_requested",
    HandoffAcknowledged: "handoff_acknowledged",
    HandoffEffectFailed: "handoff_effect_failed",
    HandoffCancelled: "handoff_cancelled",
    HandoffEffectJob: "handoff_effect_job",
    HandoffWorkflow: "handoff_workflow",
    HandoffReceipt: "handoff_receipt",
    PublicHandoffProjection: "public_handoff_projection",
    PixVisualEvidence: "pix_visual_evidence",
    VerifiedWiseCredit: "verified_wise_credit",
    VerifiedStripeEvent: "verified_stripe_event",
    PaymentMethodSelected: "payment_method_selected",
    FinancialSummaryRecorded: "financial_summary_recorded",
    FinancialConfirmationReceived: "financial_confirmation_received",
    PaymentEvidenceRecorded: "payment_evidence_recorded",
    SettlementStarted: "settlement_started",
    SettlementFinished: "settlement_finished",
    PaymentExpired: "payment_expired",
    PaymentCancelled: "payment_cancelled",
    PaymentSettlementCommand: "payment_settlement_command",
    SettlementOutcome: "settlement_outcome",
    PaymentWorkflow: "payment_workflow",
    PaymentEffectJob: "payment_effect_job",
    PaymentReceipt: "payment_receipt",
}
_NESTED_DATACLASSES = frozenset(
    (*_TYPE_TAGS, ExecutionOutcome, PaymentEvidenceTrust, VerifiedPaymentEvidence)
)
_ENUM_TYPES = frozenset(
    (
        BusinessUnit,
        PaymentMethod,
        PaymentStatus,
        SettlementCertainty,
        SettlementOperation,
        EffectRequirement,
        HandoffStatus,
        ServiceKind,
        ExecutionCertainty,
        HandoffReasonCode,
        HandoffEffectKind,
        HandoffEffectFailureCode,
        HandoffCancellationCode,
        PublicNextAction,
        PixProofStatus,
        StripeEventType,
        PaymentEffectKind,
    )
)


@lru_cache(maxsize=None)
def _cached_type_hints(cls: type) -> types.MappingProxyType[str, Any]:
    return types.MappingProxyType(get_type_hints(cls))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _encode_dataclass(cls: type, value: Any) -> dict[str, Any]:
    if type(value) is not cls:
        raise ValueError(f"value must be the exact {cls.__name__} type")
    hints = _cached_type_hints(cls)
    encoded = {
        field.name: _encode_value(hints[field.name], getattr(value, field.name))
        for field in fields(cls)
    }
    reconstructed = _decode_dataclass(cls, encoded)
    if reconstructed != value:
        raise ValueError(f"{cls.__name__} contains noncanonical field values")
    return encoded


def _encode_value(annotation: Any, value: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is tuple:
        if type(value) is not tuple:
            raise ValueError("tuple field must be an exact tuple")
        if len(args) == 2 and args[1] is Ellipsis:
            return [_encode_value(args[0], item) for item in value]
        if len(args) != len(value):
            raise ValueError("fixed tuple length mismatch")
        return [
            _encode_value(item_type, item)
            for item_type, item in zip(args, value, strict=True)
        ]
    if origin in {Union, types.UnionType}:
        if value is None and type(None) in args:
            return None
        failures: list[str] = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _encode_value(option, value)
            except (TypeError, ValueError) as exc:
                failures.append(str(exc))
        raise ValueError(f"value does not match union: {failures}")
    if annotation in _ENUM_TYPES:
        if type(value) is not annotation:
            raise ValueError(f"{annotation.__name__} field has wrong type")
        return value.value
    if annotation is datetime:
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
        ):
            raise ValueError("datetime value must be canonical UTC")
        return value.isoformat()
    if annotation in _NESTED_DATACLASSES:
        return _encode_dataclass(annotation, value)
    if annotation is str:
        if type(value) is not str:
            raise ValueError("string field has wrong type")
        return value
    if annotation is int:
        if type(value) is not int:
            raise ValueError("integer field has wrong type")
        return value
    if annotation is bool:
        if type(value) is not bool:
            raise ValueError("boolean field has wrong type")
        return value
    if is_dataclass(value):
        raise TypeError(f"unsupported serialized dataclass: {type(value).__name__}")
    raise TypeError(f"unsupported field annotation: {annotation!r}")


def _decode_dataclass(cls: type, value: Any) -> Any:
    if type(value) is not dict:
        raise ValueError(f"{cls.__name__} data must be an object")
    expected = {field.name for field in fields(cls)}
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"{cls.__name__} fields mismatch; missing={missing}, unknown={unknown}"
        )
    hints = _cached_type_hints(cls)
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
    if annotation in _ENUM_TYPES:
        if type(value) is not str:
            raise ValueError(f"{annotation.__name__} field must be a string")
        try:
            member = annotation(value)
        except ValueError as exc:
            raise ValueError(f"invalid {annotation.__name__}: {value!r}") from exc
        if member.value != value:
            raise ValueError(f"noncanonical {annotation.__name__}: {value!r}")
        return member
    if annotation is datetime:
        if type(value) is not str:
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
    if annotation in _NESTED_DATACLASSES:
        return _decode_dataclass(annotation, value)
    if annotation is str:
        if type(value) is not str:
            raise ValueError("string field has wrong type")
        return value
    if annotation is int:
        if type(value) is not int:
            raise ValueError("integer field has wrong type")
        return value
    if annotation is bool:
        if type(value) is not bool:
            raise ValueError("boolean field has wrong type")
        return value
    raise TypeError(f"unsupported field annotation: {annotation!r}")


def to_wire_json(value: object) -> str:
    """Serialize an exact shared DTO to deterministic schema-versioned JSON."""

    value_type = type(value)
    type_tag = _TYPE_TAGS.get(value_type)
    if type_tag is None:
        raise TypeError("value must be an exact Phase 6 shared contract type")
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


def from_wire_json(text: str, expected_type: type) -> Any:
    """Decode one exact shared DTO, rejecting all schema or type drift."""

    expected_tag = _TYPE_TAGS.get(expected_type)
    if expected_tag is None:
        raise TypeError("expected_type must be an exact Phase 6 shared contract type")
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
    if type(payload) is not dict:
        raise ValueError("wire payload must be an object")
    if set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("wire payload has missing or unknown fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported schema_version")
    if type(payload["type"]) is not str or payload["type"] != expected_tag:
        raise ValueError("wire type does not match expected_type")
    return _decode_dataclass(expected_type, payload["data"])


def semantic_hash(value: object) -> str:
    """Return the lowercase SHA-256 digest of canonical wire JSON."""

    return hashlib.sha256(to_wire_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "to_wire_json",
    "from_wire_json",
    "semantic_hash",
]
