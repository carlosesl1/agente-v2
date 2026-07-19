"""Closed immutable operational DTOs for durable command execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any

from reservation_domain import ReservationCommand, ReservationOperation

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_PREPARATION_FAILURES = 3


def _require_id(value: str, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be an opaque identifier")
    normalized = value.strip()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return normalized


def _require_hash(value: str, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    if not _HASH_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _require_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_int_at_least(value: int, field_name: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    return value


def _require_enum(value: Enum, enum_type: type[Enum], field_name: str) -> None:
    if type(value) is not enum_type:
        raise ValueError(f"{field_name} must be a {enum_type.__name__}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _canonical_json_object(value: str, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a canonical JSON object string")
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be valid canonical JSON") from exc
    if type(parsed) is not dict:
        raise ValueError(f"{field_name} root must be a JSON object")
    try:
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        canonical.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"{field_name} must be valid canonical JSON") from exc
    if canonical != value:
        raise ValueError(f"{field_name} must use canonical JSON serialization")
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LedgerStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DISPATCH_FENCED = "dispatch_fenced"
    OUTCOME_RECORDED = "outcome_recorded"
    MANUAL_REVIEW = "manual_review"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    DELIVERED = "delivered"


class OutboxKind(str, Enum):
    SUMMARY_PRESENTED = "summary_presented"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED_NO_EFFECT = "execution_failed_no_effect"
    EXECUTION_NOT_CALLED = "execution_not_called"
    EXECUTION_MANUAL_REVIEW = "execution_manual_review"


class PreparationDisposition(str, Enum):
    REQUEUED = "requeued"
    TERMINAL_NOT_CALLED = "terminal_not_called"


@dataclass(frozen=True, slots=True)
class Lease:
    owner: str
    fencing_token: int
    acquired_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", _require_id(self.owner, "lease.owner"))
        _require_int_at_least(self.fencing_token, "lease.fencing_token", 1)
        acquired_at = _require_utc(self.acquired_at, "lease.acquired_at")
        expires_at = _require_utc(self.expires_at, "lease.expires_at")
        if expires_at <= acquired_at:
            raise ValueError("lease.expires_at must be after lease.acquired_at")
        object.__setattr__(self, "acquired_at", acquired_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class CommandClaim:
    command: ReservationCommand
    workflow_revision: int
    lease: Lease
    claim_count: int
    preparation_failures: int

    def __post_init__(self) -> None:
        if type(self.command) is not ReservationCommand:
            raise ValueError("command must be the exact ReservationCommand type")
        _require_int_at_least(self.workflow_revision, "workflow_revision", 0)
        if type(self.lease) is not Lease:
            raise ValueError("lease must be the exact Lease type")
        _require_int_at_least(self.claim_count, "claim_count", 1)
        failures = _require_int_at_least(
            self.preparation_failures,
            "preparation_failures",
            0,
        )
        if failures > _MAX_PREPARATION_FAILURES:
            raise ValueError(
                f"preparation_failures must be <= {_MAX_PREPARATION_FAILURES}"
            )


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    command_id: str
    idempotency_key: str
    operation: ReservationOperation
    canonical_payload: str
    payload_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_id",
            _require_id(self.command_id, "dispatch_request.command_id"),
        )
        object.__setattr__(
            self,
            "idempotency_key",
            _require_id(
                self.idempotency_key,
                "dispatch_request.idempotency_key",
            ),
        )
        _require_enum(
            self.operation,
            ReservationOperation,
            "dispatch_request.operation",
        )
        canonical_payload = _canonical_json_object(
            self.canonical_payload,
            "dispatch_request.canonical_payload",
        )
        payload_hash = _require_hash(
            self.payload_hash,
            "dispatch_request.payload_hash",
        )
        if payload_hash != _sha256_text(canonical_payload):
            raise ValueError(
                "dispatch_request.payload_hash does not match canonical_payload"
            )
        object.__setattr__(self, "canonical_payload", canonical_payload)
        object.__setattr__(self, "payload_hash", payload_hash)

    @classmethod
    def from_command(
        cls,
        command: ReservationCommand,
        canonical_payload: str,
    ) -> DispatchRequest:
        if type(command) is not ReservationCommand:
            raise ValueError("command must be the exact ReservationCommand type")
        canonical_payload = _canonical_json_object(
            canonical_payload,
            "dispatch_request.canonical_payload",
        )
        return cls(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            operation=command.operation,
            canonical_payload=canonical_payload,
            payload_hash=_sha256_text(canonical_payload),
        )


@dataclass(frozen=True, slots=True)
class DispatchPermit:
    command_id: str
    lease: Lease
    dispatch_slot: int
    request_hash: str
    fenced_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_id",
            _require_id(self.command_id, "dispatch_permit.command_id"),
        )
        if type(self.lease) is not Lease:
            raise ValueError("lease must be the exact Lease type")
        if type(self.dispatch_slot) is not int or self.dispatch_slot != 1:
            raise ValueError("dispatch_slot must be exactly 1")
        object.__setattr__(
            self,
            "request_hash",
            _require_hash(self.request_hash, "dispatch_permit.request_hash"),
        )
        object.__setattr__(
            self,
            "fenced_at",
            _require_utc(self.fenced_at, "dispatch_permit.fenced_at"),
        )


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    message_id: str
    idempotency_key: str
    workflow_id: str
    command_id: str | None
    kind: OutboxKind
    template_id: str
    canonical_payload: str
    payload_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "message_id",
            _require_id(self.message_id, "outbox_message.message_id"),
        )
        object.__setattr__(
            self,
            "idempotency_key",
            _require_id(
                self.idempotency_key,
                "outbox_message.idempotency_key",
            ),
        )
        object.__setattr__(
            self,
            "workflow_id",
            _require_id(self.workflow_id, "outbox_message.workflow_id"),
        )
        if self.command_id is not None:
            object.__setattr__(
                self,
                "command_id",
                _require_id(self.command_id, "outbox_message.command_id"),
            )
        _require_enum(self.kind, OutboxKind, "outbox_message.kind")
        object.__setattr__(
            self,
            "template_id",
            _require_id(self.template_id, "outbox_message.template_id"),
        )
        canonical_payload = _canonical_json_object(
            self.canonical_payload,
            "outbox_message.canonical_payload",
        )
        payload_hash = _require_hash(
            self.payload_hash,
            "outbox_message.payload_hash",
        )
        if payload_hash != _sha256_text(canonical_payload):
            raise ValueError(
                "outbox_message.payload_hash does not match canonical_payload"
            )
        object.__setattr__(self, "canonical_payload", canonical_payload)
        object.__setattr__(self, "payload_hash", payload_hash)
        object.__setattr__(
            self,
            "created_at",
            _require_utc(self.created_at, "outbox_message.created_at"),
        )


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    message_id: str
    delivery_reference: str
    receipt_hash: str
    delivered_at: datetime

    def __post_init__(self) -> None:
        message_id = _require_id(self.message_id, "delivery_receipt.message_id")
        delivery_reference = _require_id(
            self.delivery_reference,
            "delivery_receipt.delivery_reference",
        )
        delivered_at = _require_utc(
            self.delivered_at,
            "delivery_receipt.delivered_at",
        )
        receipt_hash = _require_hash(
            self.receipt_hash,
            "delivery_receipt.receipt_hash",
        )
        material = json.dumps(
            {
                "message_id": message_id,
                "delivery_reference": delivery_reference,
                "delivered_at": delivered_at.isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if receipt_hash != _sha256_text(material):
            raise ValueError(
                "delivery_receipt.receipt_hash does not match canonical receipt"
            )
        object.__setattr__(self, "message_id", message_id)
        object.__setattr__(self, "delivery_reference", delivery_reference)
        object.__setattr__(self, "receipt_hash", receipt_hash)
        object.__setattr__(self, "delivered_at", delivered_at)


__all__ = [
    "LedgerStatus",
    "OutboxStatus",
    "OutboxKind",
    "PreparationDisposition",
    "Lease",
    "CommandClaim",
    "DispatchRequest",
    "DispatchPermit",
    "OutboxMessage",
    "DeliveryReceipt",
]
