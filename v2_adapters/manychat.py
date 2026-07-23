"""Narrow ManyChat ingress parser for sanitized webhook payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Final, Protocol

from v2_contracts.channel import (
    InboundEvent,
    PublicDeliveryNotCalled,
    PublicDeliveryUnknown,
)


class ManyChatPayloadError(ValueError):
    """Raised when a ManyChat payload cannot become a safe V2 event."""


class ManyChatTransportNotCalled(RuntimeError):
    """Transport proved that no HTTP request reached ManyChat."""


@dataclass(frozen=True, slots=True)
class ManyChatTransportResponse:
    provider_message_id: str

    def __post_init__(self) -> None:
        if (
            type(self.provider_message_id) is not str
            or not self.provider_message_id.strip()
            or self.provider_message_id != self.provider_message_id.strip()
        ):
            raise ValueError("provider_message_id must be canonical non-empty text")


class ManyChatTransport(Protocol):
    def send_text(
        self,
        *,
        subscriber_id: str,
        text: str,
        idempotency_key: str,
    ) -> ManyChatTransportResponse: ...


class PublicMessageClaim(Protocol):
    outbox_id: str
    lead_id: str
    text: str


class ManyChatDeliveryAdapter:
    """Turn one fenced public outbox claim into one narrow ManyChat send."""

    def __init__(self, transport: ManyChatTransport) -> None:
        if not callable(getattr(transport, "send_text", None)):
            raise TypeError("transport must expose send_text")
        self._transport = transport

    def send(self, claim: PublicMessageClaim) -> str:
        outbox_id = getattr(claim, "outbox_id", None)
        lead_id = getattr(claim, "lead_id", None)
        text = getattr(claim, "text", None)
        if type(outbox_id) is not str or not outbox_id:
            raise PublicDeliveryNotCalled("claim lacks a stable outbox identity")
        if type(lead_id) is not str or not lead_id.startswith("manychat:"):
            raise PublicDeliveryNotCalled("claim is not bound to a ManyChat lead")
        subscriber_id = lead_id.removeprefix("manychat:")
        if not subscriber_id or type(text) is not str or not text.strip():
            raise PublicDeliveryNotCalled("claim lacks a sendable subscriber or text")
        try:
            response = self._transport.send_text(
                subscriber_id=subscriber_id,
                text=text,
                idempotency_key=outbox_id,
            )
        except ManyChatTransportNotCalled as exc:
            raise PublicDeliveryNotCalled(str(exc)) from exc
        except Exception as exc:
            raise PublicDeliveryUnknown("ManyChat delivery outcome is unknown") from exc
        if type(response) is not ManyChatTransportResponse:
            raise PublicDeliveryUnknown("ManyChat returned an invalid delivery response")
        return response.provider_message_id


_MISSING: Final = object()


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _first_text(*values: object) -> str:
    for value in values:
        if type(value) is str and value.strip():
            return value.strip()
        if type(value) is int and value >= 0:
            return str(value)
    return ""


def _canonical_payload(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ManyChatPayloadError("payload must be canonical JSON data") from exc


def _require_received_at(received_at: object) -> datetime:
    if (
        type(received_at) is not datetime
        or received_at.tzinfo is None
        or received_at.utcoffset() is None
    ):
        raise ManyChatPayloadError("received_at must be timezone-aware")
    return received_at.astimezone(timezone.utc)


def _occurred_at(value: object, received_at: datetime) -> datetime:
    if value is None:
        return received_at
    if type(value) in {int, float} and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ManyChatPayloadError("occurred_at timestamp is invalid") from exc
    if type(value) is not str or not value.strip():
        raise ManyChatPayloadError("occurred_at must be ISO-8601 text or epoch seconds")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ManyChatPayloadError("occurred_at must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ManyChatPayloadError("occurred_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_manychat_payload(
    payload: Mapping[str, object],
    received_at: datetime,
) -> InboundEvent:
    """Normalize one payload without importing any legacy runtime component."""

    if not isinstance(payload, Mapping):
        raise ManyChatPayloadError("ManyChat payload must be a JSON object")
    received = _require_received_at(received_at)
    message = _mapping(payload.get("message"))
    subscriber = _mapping(payload.get("subscriber"))
    contact = _mapping(payload.get("contact"))

    event_id = _first_text(
        payload.get("event_id"),
        payload.get("message_id"),
        payload.get("messageId"),
        message.get("id"),
        message.get("message_id"),
    )
    if not event_id:
        raise ManyChatPayloadError("payload requires stable event identity")

    subscriber_id = _first_text(
        payload.get("subscriber_id"),
        payload.get("subscriberId"),
        subscriber.get("id"),
        payload.get("contact_id"),
        payload.get("contactId"),
        contact.get("id"),
    )
    if not subscriber_id:
        raise ManyChatPayloadError("payload requires stable contact identity")

    conversation_id = _first_text(
        payload.get("conversation_id"),
        payload.get("conversationId"),
        message.get("conversation_id"),
        subscriber_id,
    )
    raw_message = payload.get("message", _MISSING)
    text = _first_text(
        payload.get("text"),
        raw_message if type(raw_message) is str else None,
        message.get("text"),
        message.get("content"),
    )
    media = _mapping(payload.get("media"))
    media_url = _first_text(
        payload.get("media_url"),
        payload.get("mediaUrl"),
        message.get("media_url"),
        media.get("url"),
    ) or None
    media_type = _first_text(
        payload.get("media_type"),
        payload.get("mediaType"),
        message.get("media_type"),
        media.get("type"),
    ) or None
    if not text and media_url is None:
        raise ManyChatPayloadError("payload requires message text or media")

    occurred = _occurred_at(
        payload.get(
            "occurred_at",
            payload.get("timestamp", message.get("timestamp")),
        ),
        received,
    )
    payload_hash = hashlib.sha256(_canonical_payload(payload)).hexdigest()
    try:
        return InboundEvent(
            event_id=event_id,
            lead_id=f"manychat:{subscriber_id}",
            subscriber_id=subscriber_id,
            conversation_id=conversation_id,
            text=text,
            media_url=media_url,
            media_type=media_type,
            occurred_at=occurred,
            payload_hash=payload_hash,
        )
    except (TypeError, ValueError) as exc:
        raise ManyChatPayloadError(str(exc)) from exc
