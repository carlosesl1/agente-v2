"""Narrow ManyChat ingress parser for sanitized webhook payloads."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from typing import Final

from v2_contracts.channel import InboundEvent


class ManyChatPayloadError(ValueError):
    """Raised when a ManyChat payload cannot become a safe V2 event."""


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
