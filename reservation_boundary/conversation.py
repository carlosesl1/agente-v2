"""Closed immutable conversation contracts for the Phase 8 boundary."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import re
from typing import ClassVar, Final

from reservation_boundary.types import NormalizedMessage


_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def _require_identifier(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must use the closed identifier alphabet")
    return value


def _require_sha256(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _require_exact_bytes(value: object, name: str) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{name} must be exact bytes")
    if not value:
        raise ValueError(f"{name} must be non-empty")
    return value


def _require_exact_int(value: object, name: str, *, minimum: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime:
        raise TypeError(f"{name} must be an exact datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value


def _canonical_envelope(*, schema: str, version: int, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": schema, "version": version, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SourceEventIdentity:
    """Canonical identity of one source event contributing to an aggregate turn."""

    source_event_id: str
    source_event_hash: str

    SCHEMA: ClassVar[str] = "phase8-source-event-identity"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-source-event-identity-v1"

    def __post_init__(self) -> None:
        _require_identifier(self.source_event_id, "SourceEventIdentity.source_event_id")
        _require_sha256(self.source_event_hash, "SourceEventIdentity.source_event_hash")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "source_event_id": self.source_event_id,
                "source_event_hash": self.source_event_hash,
            },
        )

    def canonical_hash(self) -> str:
        preimage = self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        return hashlib.sha256(preimage).hexdigest()


@dataclass(frozen=True, slots=True)
class MayaTurnRequest:
    """Capability-free input passed from the coordinator to one Maya attempt."""

    boundary_state_bytes: bytes
    state_version: int
    state_hash: str
    normalized_message: NormalizedMessage
    aggregate_turn_id: str
    source_events: tuple[SourceEventIdentity, ...]
    lead_key_hash: str
    private_delivery_binding_hash: str
    deadline_at: datetime
    behavior_profile_fingerprint: str

    SCHEMA: ClassVar[str] = "phase8-maya-turn-request"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-maya-turn-request-v1"

    def __post_init__(self) -> None:
        state_bytes = _require_exact_bytes(
            self.boundary_state_bytes,
            "MayaTurnRequest.boundary_state_bytes",
        )
        _require_exact_int(
            self.state_version,
            "MayaTurnRequest.state_version",
            minimum=0,
        )
        state_hash = _require_sha256(self.state_hash, "MayaTurnRequest.state_hash")
        if hashlib.sha256(state_bytes).hexdigest() != state_hash:
            raise ValueError("MayaTurnRequest.state_hash must bind boundary_state_bytes")
        if type(self.normalized_message) is not NormalizedMessage:
            raise TypeError(
                "MayaTurnRequest.normalized_message must be an exact NormalizedMessage"
            )
        _require_identifier(
            self.aggregate_turn_id,
            "MayaTurnRequest.aggregate_turn_id",
        )
        if type(self.source_events) is not tuple:
            raise TypeError("MayaTurnRequest.source_events must be an exact tuple")
        if not self.source_events:
            raise ValueError("MayaTurnRequest.source_events must be non-empty")
        for source_event in self.source_events:
            if type(source_event) is not SourceEventIdentity:
                raise TypeError(
                    "MayaTurnRequest.source_events must contain exact "
                    "SourceEventIdentity members"
                )
        source_event_ids = tuple(
            source_event.source_event_id for source_event in self.source_events
        )
        if len(source_event_ids) != len(set(source_event_ids)):
            raise ValueError("MayaTurnRequest.source_events must have unique event IDs")
        _require_sha256(self.lead_key_hash, "MayaTurnRequest.lead_key_hash")
        _require_sha256(
            self.private_delivery_binding_hash,
            "MayaTurnRequest.private_delivery_binding_hash",
        )
        _require_utc(self.deadline_at, "MayaTurnRequest.deadline_at")
        _require_sha256(
            self.behavior_profile_fingerprint,
            "MayaTurnRequest.behavior_profile_fingerprint",
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "boundary_state_bytes": base64.b64encode(
                    self.boundary_state_bytes
                ).decode("ascii"),
                "state_version": self.state_version,
                "state_hash": self.state_hash,
                "normalized_message": {
                    "text": self.normalized_message.text,
                    "locale": self.normalized_message.locale,
                },
                "aggregate_turn_id": self.aggregate_turn_id,
                "source_events": [
                    json.loads(source_event.to_canonical_bytes().decode("utf-8"))
                    for source_event in self.source_events
                ],
                "lead_key_hash": self.lead_key_hash,
                "private_delivery_binding_hash": self.private_delivery_binding_hash,
                "deadline_at": self.deadline_at.isoformat(),
                "behavior_profile_fingerprint": self.behavior_profile_fingerprint,
            },
        )

    def canonical_hash(self) -> str:
        preimage = self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        return hashlib.sha256(preimage).hexdigest()


__all__ = ("MayaTurnRequest", "SourceEventIdentity")
