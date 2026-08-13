from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping, TypeAlias
from urllib.parse import parse_qsl, urlsplit


MAX_SUMMARY_JSON_BYTES = 16 * 1024
MAX_FULL_JSON_BYTES = 256 * 1024
MAX_JSON_DEPTH = 32

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
class _FrozenJSONArray(tuple["FrozenJSONValue", ...]):
    pass


FrozenJSONValue: TypeAlias = (
    JSONScalar | _FrozenJSONArray | Mapping[str, "FrozenJSONValue"]
)


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


class TraceCompleteness(str, Enum):
    COMPLETE_TRACE = "complete_trace"
    PARTIAL_TRACE = "partial_trace"
    LEDGER_ONLY = "ledger_only"


class NodeType(str, Enum):
    MANYCHAT_WEBHOOK = "manychat_webhook"
    ROUTER_VALIDATION = "router_validation"
    INBOX_ACCEPT = "inbox_accept"
    INBOX_CLAIM = "inbox_claim"

    MAYA_REQUEST = "maya_request"
    MAYA_RESPONSE = "maya_response"
    MAYA_CORRECTION = "maya_correction"
    MAYA_REVIEW = "maya_review"

    MAYA_READ_REQUEST = "maya_read_request"
    PROVIDER_READ_REQUEST = "provider_read_request"
    PROVIDER_READ_RESPONSE = "provider_read_response"
    MAYA_OBSERVATION = "maya_observation"

    CONVERSATION_REDUCER = "conversation_reducer"
    TURN_COMMIT = "turn_commit"
    BOUNDARY_RELAY = "boundary_relay"

    CLOUDBEDS_RESERVATION_REQUEST = "cloudbeds_reservation_request"
    CLOUDBEDS_RESERVATION_RESPONSE = "cloudbeds_reservation_response"
    BOKUN_BOOKING_REQUEST = "bokun_booking_request"
    BOKUN_BOOKING_RESPONSE = "bokun_booking_response"

    STRIPE_PRODUCT = "stripe_product"
    STRIPE_PRICE = "stripe_price"
    STRIPE_PAYMENT_LINK = "stripe_payment_link"
    PIX_INSTRUCTION = "pix_instruction"
    WISE_INSTRUCTION = "wise_instruction"
    SETTLEMENT = "settlement"

    PUBLIC_OUTBOX = "public_outbox"
    MANYCHAT_DELIVERY_REQUEST = "manychat_delivery_request"
    MANYCHAT_DELIVERY_RESPONSE = "manychat_delivery_response"
    HANDOFF_REQUEST = "handoff_request"
    HANDOFF_DELIVERY = "handoff_delivery"

    PROVIDER_RECONCILIATION = "provider_reconciliation"
    STRIPE_RECONCILIATION = "stripe_reconciliation"
    MANUAL_REVIEW = "manual_review"


_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "authorization",
        "auth",
        "credential",
        "credentials",
        "token",
        "secret",
        "password",
        "passwd",
        "cookie",
        "header",
        "headers",
        "signature",
    }
)
_FORBIDDEN_CONTACT_DOCUMENT_KEYS = frozenset(
    {
        "name",
        "first_name",
        "last_name",
        "full_name",
        "customer_name",
        "contact_name",
        "email",
        "email_address",
        "phone",
        "phone_number",
        "telephone",
        "mobile",
        "whatsapp",
        "contact",
        "contact_value",
        "address",
        "document",
        "document_number",
        "document_value",
        "passport",
        "passport_number",
        "national_id",
        "tax_id",
        "cpf",
        "cnpj",
        "subscriber_id",
    }
)
_SIGNED_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "credential",
        "key",
        "signature",
        "sig",
        "token",
        "x_amz_credential",
        "x_amz_security_token",
        "x_amz_signature",
        "x_goog_credential",
        "x_goog_signature",
    }
)
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*\S+"),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9_-]+\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_NODE_ID_PATTERN = re.compile(r"[0-9a-f]{64}")


def _normalized_key(key: str) -> str:
    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _validate_key(key: str) -> None:
    normalized = _normalized_key(key)
    parts = frozenset(part for part in normalized.split("_") if part)
    if parts & _FORBIDDEN_KEY_PARTS:
        raise ValueError(f"forbidden key in trace JSON: {key!r}")
    if normalized in _FORBIDDEN_CONTACT_DOCUMENT_KEYS or (
        normalized.startswith("raw_")
        and normalized[4:] in _FORBIDDEN_CONTACT_DOCUMENT_KEYS
    ):
        raise ValueError(f"forbidden key in trace JSON: {key!r}")


def _looks_like_signed_url(value: str) -> bool:
    for candidate in _URL_PATTERN.findall(value):
        parsed = urlsplit(candidate.rstrip(".,);]"))
        if parsed.username is not None or parsed.password is not None:
            return True
        for query_key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            key_prefix = query_key.split("=", 1)[0]
            if _normalized_key(key_prefix) in _SIGNED_QUERY_KEYS:
                return True
    return False


def _validate_string_content(value: str) -> None:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("trace JSON strings must be valid UTF-8") from exc
    if _looks_like_signed_url(value):
        raise ValueError("signed URL or provider credential is forbidden in trace JSON")
    if any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS):
        raise ValueError("provider credential is forbidden in trace JSON")


def _validate_json_value(value: object, *, depth: int) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("trace JSON exceeds maximum nesting depth")
    if value is None or type(value) is bool or type(value) is int:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("trace JSON numbers must be finite")
        return
    if type(value) is str:
        _validate_string_content(value)
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("trace JSON object keys must be strings")
            _validate_key(key)
            _validate_string_content(key)
            _validate_json_value(item, depth=depth + 1)
        return
    raise TypeError(f"value of type {type(value).__name__} is outside closed JSON")


def validate_closed_json(value: JSONValue) -> JSONValue:
    """Validate the closed JSON algebra and the trace content denylist."""

    _validate_json_value(value, depth=0)
    return value


def _canonical_json_bytes_after_validation(value: JSONValue) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text.encode("utf-8", errors="strict")


def _thaw_json(value: JSONValue | FrozenJSONValue) -> JSONValue:
    if type(value) is _FrozenJSONArray:
        return [_thaw_json(item) for item in value]
    if type(value) is MappingProxyType:
        return {key: _thaw_json(item) for key, item in value.items()}
    return value  # type: ignore[return-value]


def canonical_json_bytes(value: JSONValue | FrozenJSONValue) -> bytes:
    json_value = _thaw_json(value)
    validate_closed_json(json_value)
    return _canonical_json_bytes_after_validation(json_value)


def canonical_json_text(value: JSONValue | FrozenJSONValue) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _freeze_json(value: JSONValue) -> FrozenJSONValue:
    if type(value) is list:
        return _FrozenJSONArray(_freeze_json(item) for item in value)
    if type(value) is dict:
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    return value  # type: ignore[return-value]


def _validated_frozen_payload(
    value: JSONValue | None,
    *,
    field_name: str,
    byte_limit: int,
) -> FrozenJSONValue | None:
    if value is None:
        return None
    validate_closed_json(value)
    size = len(_canonical_json_bytes_after_validation(value))
    if size > byte_limit:
        raise ValueError(
            f"{field_name} exceeds its {byte_limit}-byte limit ({size} bytes)"
        )
    return _freeze_json(value)


def _validate_required_text(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if "\x00" in value:
        raise ValueError(f"{field_name} may not contain NUL")
    _validate_string_content(value)
    if len(value.encode("utf-8")) > 256:
        raise ValueError(f"{field_name} exceeds its 256-byte limit")
    return value


def _validate_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_required_text(value, field_name=field_name)


def _validate_utc_timestamp(value: object, *, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError(f"{field_name} must be an exact UTC datetime")
    return value


def format_utc_timestamp(value: datetime) -> str:
    timestamp = _validate_utc_timestamp(value, field_name="timestamp")
    return timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_node_id(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _NODE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a deterministic SHA-256 node ID")
    return value


def deterministic_node_id(
    *,
    execution_id: str,
    node_type: NodeType,
    ordinal: int,
    attempt: int,
) -> str:
    _validate_required_text(execution_id, field_name="execution_id")
    if not isinstance(node_type, NodeType):
        raise TypeError("node_type must be a NodeType")
    if type(ordinal) is not int or ordinal < 1:
        raise ValueError("ordinal must be >= 1")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("attempt must be >= 1")
    identity: JSONValue = [execution_id, node_type.value, ordinal, attempt]
    payload = canonical_json_bytes(identity)
    return hashlib.sha256(b"v2-ops-node-id:v1\0" + payload).hexdigest()


@dataclass(frozen=True, slots=True)
class OpsExecution:
    execution_id: str
    lead_id: str
    received_at: datetime
    status: ExecutionStatus = ExecutionStatus.PENDING
    trace_completeness: TraceCompleteness = TraceCompleteness.COMPLETE_TRACE
    current_node_id: str | None = None
    completed_at: datetime | None = None
    terminal_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_required_text(self.execution_id, field_name="execution_id")
        _validate_required_text(self.lead_id, field_name="lead_id")
        _validate_utc_timestamp(self.received_at, field_name="received_at")
        if type(self.status) is not ExecutionStatus:
            raise TypeError("status must be an ExecutionStatus")
        if type(self.trace_completeness) is not TraceCompleteness:
            raise TypeError("trace_completeness must be a TraceCompleteness")
        _validate_node_id(self.current_node_id, field_name="current_node_id")
        _validate_optional_text(self.terminal_reason, field_name="terminal_reason")
        if self.completed_at is not None:
            _validate_utc_timestamp(self.completed_at, field_name="completed_at")
            if self.completed_at < self.received_at:
                raise ValueError("completed_at cannot be before received_at")
        terminal = self.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.MANUAL_REVIEW,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("completed_at presence must match terminal status")


@dataclass(frozen=True, slots=True)
class OpsNodeStart:
    execution_id: str
    node_type: NodeType
    ordinal: int
    started_at: datetime
    attempt: int = 1
    parent_node_id: str | None = None
    input_summary: FrozenJSONValue = field(default_factory=dict)
    input_full: FrozenJSONValue | None = None
    technical_metadata: FrozenJSONValue = field(default_factory=dict)
    status: ExecutionStatus = field(default=ExecutionStatus.RUNNING, init=False)
    node_id: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_required_text(self.execution_id, field_name="execution_id")
        if type(self.node_type) is not NodeType:
            raise TypeError("node_type must be a NodeType")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ValueError("ordinal must be >= 1")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        _validate_utc_timestamp(self.started_at, field_name="started_at")
        _validate_node_id(self.parent_node_id, field_name="parent_node_id")
        object.__setattr__(
            self,
            "input_summary",
            _validated_frozen_payload(
                self.input_summary,  # type: ignore[arg-type]
                field_name="input_summary",
                byte_limit=MAX_SUMMARY_JSON_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "input_full",
            _validated_frozen_payload(
                self.input_full,  # type: ignore[arg-type]
                field_name="input_full",
                byte_limit=MAX_FULL_JSON_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "technical_metadata",
            _validated_frozen_payload(
                self.technical_metadata,  # type: ignore[arg-type]
                field_name="technical_metadata",
                byte_limit=MAX_SUMMARY_JSON_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "node_id",
            deterministic_node_id(
                execution_id=self.execution_id,
                node_type=self.node_type,
                ordinal=self.ordinal,
                attempt=self.attempt,
            ),
        )


@dataclass(frozen=True, slots=True)
class OpsNodeFinish:
    execution_id: str
    node_type: NodeType
    ordinal: int
    started_at: datetime
    completed_at: datetime
    status: ExecutionStatus
    attempt: int = 1
    parent_node_id: str | None = None
    output_summary: FrozenJSONValue = field(default_factory=dict)
    output_full: FrozenJSONValue | None = None
    error: FrozenJSONValue | None = None
    technical_metadata: FrozenJSONValue = field(default_factory=dict)
    node_id: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_required_text(self.execution_id, field_name="execution_id")
        if type(self.node_type) is not NodeType:
            raise TypeError("node_type must be a NodeType")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ValueError("ordinal must be >= 1")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        _validate_utc_timestamp(self.started_at, field_name="started_at")
        _validate_utc_timestamp(self.completed_at, field_name="completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be before started_at")
        if type(self.status) is not ExecutionStatus:
            raise TypeError("status must be an ExecutionStatus")
        if self.status not in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.MANUAL_REVIEW,
        }:
            raise ValueError("finished node status must be terminal")
        _validate_node_id(self.parent_node_id, field_name="parent_node_id")
        object.__setattr__(
            self,
            "output_summary",
            _validated_frozen_payload(
                self.output_summary,  # type: ignore[arg-type]
                field_name="output_summary",
                byte_limit=MAX_SUMMARY_JSON_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "output_full",
            _validated_frozen_payload(
                self.output_full,  # type: ignore[arg-type]
                field_name="output_full",
                byte_limit=MAX_FULL_JSON_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "error",
            _validated_frozen_payload(
                self.error,  # type: ignore[arg-type]
                field_name="error",
                byte_limit=MAX_SUMMARY_JSON_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "technical_metadata",
            _validated_frozen_payload(
                self.technical_metadata,  # type: ignore[arg-type]
                field_name="technical_metadata",
                byte_limit=MAX_SUMMARY_JSON_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "node_id",
            deterministic_node_id(
                execution_id=self.execution_id,
                node_type=self.node_type,
                ordinal=self.ordinal,
                attempt=self.attempt,
            ),
        )

    @classmethod
    def from_start(
        cls,
        start: OpsNodeStart,
        *,
        status: ExecutionStatus,
        completed_at: datetime,
        output_summary: JSONValue = None,
        output_full: JSONValue | None = None,
        error: JSONValue | None = None,
        technical_metadata: JSONValue = None,
    ) -> OpsNodeFinish:
        if not isinstance(start, OpsNodeStart):
            raise TypeError("start must be an OpsNodeStart")
        return cls(
            execution_id=start.execution_id,
            node_type=start.node_type,
            ordinal=start.ordinal,
            started_at=start.started_at,
            completed_at=completed_at,
            status=status,
            attempt=start.attempt,
            parent_node_id=start.parent_node_id,
            output_summary={} if output_summary is None else output_summary,
            output_full=output_full,
            error=error,
            technical_metadata=(
                {} if technical_metadata is None else technical_metadata
            ),
        )


def validate_monotonic_ordinals(
    nodes: Iterable[OpsNodeStart | OpsNodeFinish],
) -> None:
    previous_ordinal = 0
    execution_id: str | None = None
    for node in nodes:
        if not isinstance(node, (OpsNodeStart, OpsNodeFinish)):
            raise TypeError("nodes must contain only operational node DTOs")
        if execution_id is None:
            execution_id = node.execution_id
        elif node.execution_id != execution_id:
            raise ValueError("all nodes must belong to the same execution")
        if node.ordinal <= previous_ordinal:
            raise ValueError("node ordinals must be strictly increasing")
        previous_ordinal = node.ordinal
