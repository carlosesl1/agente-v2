"""Provider-free inbound channel contracts for the Agente V2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
import re
from typing import Final


_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def _require_id(value: object, field_name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be an exact opaque identifier")
    return value


def _require_text(value: object, field_name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value.strip()):
        qualifier = "exact text" if allow_empty else "non-empty exact text"
        raise ValueError(f"{field_name} must be {qualifier}")
    return value


def _require_optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_utc(value: object, field_name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be an exact UTC datetime")
    return value


class AcceptDisposition(str, Enum):
    """Durable outcome of accepting one channel event identity."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


class PublicDeliveryNotCalled(RuntimeError):
    """Delivery failed with proof that no channel request was attempted."""


class PublicDeliveryRejected(RuntimeError):
    """Delivery is forbidden and must not be retried automatically."""


class PublicDeliveryUnknown(RuntimeError):
    """A channel request may have happened and must not be retried automatically."""


class PublicAcceptanceState(str, Enum):
    """Highest externally justified state after a successful channel API call."""

    ACCEPTED_BY_MANYCHAT = "accepted_by_manychat"


class PublicAcceptanceOperation(str, Enum):
    SEND_CONTENT = "send_content"
    SET_CUSTOM_FIELD = "set_custom_field"
    SET_CUSTOM_FIELDS = "set_custom_fields"
    TRIGGER_FLOW = "trigger_flow"


@dataclass(frozen=True, slots=True)
class PublicChannelAcceptance:
    """Typed API-acceptance evidence; it is deliberately not a delivery receipt."""

    state: PublicAcceptanceState
    operations: tuple[PublicAcceptanceOperation, ...]
    provider_request_ids: tuple[str | None, ...] = field(repr=False)
    dispatch_correlation_ids: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.state) is not PublicAcceptanceState:
            raise TypeError("state must be exact PublicAcceptanceState")
        if type(self.operations) is not tuple or not self.operations or any(
            type(item) is not PublicAcceptanceOperation for item in self.operations
        ):
            raise TypeError("operations must be a non-empty exact operation tuple")
        if (
            type(self.provider_request_ids) is not tuple
            or len(self.provider_request_ids) != len(self.operations)
        ):
            raise TypeError("provider_request_ids must align with operations")
        for value in self.provider_request_ids:
            if value is not None:
                _require_id(value, "provider_request_id")
        if (
            type(self.dispatch_correlation_ids) is not tuple
            or len(self.dispatch_correlation_ids) != len(self.operations)
        ):
            raise TypeError("dispatch_correlation_ids must align with operations")
        for value in self.dispatch_correlation_ids:
            _require_id(value, "dispatch_correlation_id")

    def to_canonical_bytes(self) -> bytes:
        return json.dumps(
            {
                "schema": "v2-public-channel-acceptance",
                "version": 1,
                "data": {
                    "dispatch_correlation_ids": list(
                        self.dispatch_correlation_ids
                    ),
                    "operations": [item.value for item in self.operations],
                    "provider_request_ids": list(self.provider_request_ids),
                    "state": self.state.value,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            b"v2-public-channel-acceptance-v1\0" + self.to_canonical_bytes()
        ).hexdigest()

    @property
    def acceptance_id(self) -> str:
        return "acceptance:" + self.canonical_hash()[:32]

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "PublicChannelAcceptance":
        if type(payload) is not bytes:
            raise TypeError("payload must be exact bytes")
        try:
            value = json.loads(payload.decode("utf-8"))
            if (
                type(value) is not dict
                or value.get("schema") != "v2-public-channel-acceptance"
                or value.get("version") != 1
                or type(value.get("data")) is not dict
            ):
                raise ValueError
            data = value["data"]
            return cls(
                state=PublicAcceptanceState(data["state"]),
                operations=tuple(
                    PublicAcceptanceOperation(item) for item in data["operations"]
                ),
                provider_request_ids=tuple(data["provider_request_ids"]),
                dispatch_correlation_ids=tuple(data["dispatch_correlation_ids"]),
            )
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("public acceptance payload is not canonical") from exc


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """Normalized event accepted from a channel adapter."""

    event_id: str
    lead_id: str
    subscriber_id: str
    conversation_id: str
    text: str
    media_url: str | None
    media_type: str | None
    occurred_at: datetime
    payload_hash: str

    def __post_init__(self) -> None:
        _require_id(self.event_id, "event_id")
        _require_id(self.lead_id, "lead_id")
        _require_id(self.subscriber_id, "subscriber_id")
        _require_id(self.conversation_id, "conversation_id")
        _require_text(self.text, "text", allow_empty=True)
        _require_optional_text(self.media_url, "media_url")
        _require_optional_text(self.media_type, "media_type")
        _require_utc(self.occurred_at, "occurred_at")
        if type(self.payload_hash) is not str or _HASH_RE.fullmatch(self.payload_hash) is None:
            raise ValueError("payload_hash must be a lowercase SHA-256")
        if not self.text.strip() and self.media_url is None:
            raise ValueError("event must contain text or media_url")
        if self.media_type is not None and self.media_url is None:
            raise ValueError("media_type requires media_url")


@dataclass(frozen=True, slots=True)
class InboundBatch:
    """One leased, lead-isolated, ordered group ready for a later turn worker."""

    batch_id: str
    lead_id: str
    subscriber_id: str
    events: tuple[InboundEvent, ...]
    combined_text: str

    def __post_init__(self) -> None:
        _require_id(self.batch_id, "batch_id")
        _require_id(self.lead_id, "lead_id")
        _require_id(self.subscriber_id, "subscriber_id")
        if type(self.events) is not tuple or not self.events:
            raise ValueError("events must be a non-empty exact tuple")
        if any(type(event) is not InboundEvent for event in self.events):
            raise TypeError("events must contain exact InboundEvent values")
        if any(event.lead_id != self.lead_id for event in self.events):
            raise ValueError("events must belong to exactly one lead")
        if any(event.subscriber_id != self.subscriber_id for event in self.events):
            raise ValueError("events must belong to exactly one subscriber")
        ordered = tuple(sorted(self.events, key=lambda event: (event.occurred_at, event.event_id)))
        if ordered != self.events:
            raise ValueError("events must be ordered by occurred_at and event_id")
        expected_text = "\n".join(event.text for event in self.events if event.text.strip())
        if type(self.combined_text) is not str or self.combined_text != expected_text:
            raise ValueError("combined_text must be the canonical event text join")
