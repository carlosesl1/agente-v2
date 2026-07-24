"""Provider-free inbound channel contracts for the Agente V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
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
