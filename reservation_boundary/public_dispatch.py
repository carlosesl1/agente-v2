"""Authenticated public-dispatch claims and delivery receipts for Phase 8."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from reservation_boundary.conversation import PublicReplyChunk

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_SCHEMA = "phase8-public-delivery-receipt"
_RECEIPT_DOMAIN = b"phase8-public-delivery-receipt-v1\0"


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical identifier")
    return value


def _digest(value: object, name: str) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase sha256 digest")
    return value


def _utc(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value


def _canonical(data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": _RECEIPT_SCHEMA, "version": 1, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class PublicDispatchClaim:
    public_row_id: str
    lead_key: str
    aggregate_turn_id: str
    chunk: PublicReplyChunk
    idempotency_key: str
    target_binding_hash: str
    channel_id: str
    channel_scope: str
    scope_subject_id: str
    authorization_id: str
    allocation_id: str
    immutable_generation: int
    source_turn_receipt_hash: str
    deadline_at: datetime
    worker_id: str
    fencing_token: int
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.public_row_id, "public_row_id"),
            (self.lead_key, "lead_key"),
            (self.aggregate_turn_id, "aggregate_turn_id"),
            (self.idempotency_key, "idempotency_key"),
            (self.channel_id, "channel_id"),
            (self.channel_scope, "channel_scope"),
            (self.scope_subject_id, "scope_subject_id"),
            (self.authorization_id, "authorization_id"),
            (self.allocation_id, "allocation_id"),
            (self.worker_id, "worker_id"),
        ):
            _identifier(value, f"PublicDispatchClaim.{name}")
        for value, name in (
            (self.target_binding_hash, "target_binding_hash"),
            (self.source_turn_receipt_hash, "source_turn_receipt_hash"),
        ):
            _digest(value, f"PublicDispatchClaim.{name}")
        if type(self.chunk) is not PublicReplyChunk:
            raise TypeError("PublicDispatchClaim.chunk must be exact PublicReplyChunk")
        if type(self.immutable_generation) is not int or self.immutable_generation < 1:
            raise ValueError("immutable_generation must be >= 1")
        if type(self.fencing_token) is not int or self.fencing_token < 1:
            raise ValueError("fencing_token must be >= 1")
        _utc(self.deadline_at, "PublicDispatchClaim.deadline_at")
        _utc(self.lease_expires_at, "PublicDispatchClaim.lease_expires_at")

    @property
    def message_id(self) -> str:
        return self.idempotency_key

    @property
    def subscriber_id(self) -> str:
        return self.scope_subject_id

    @property
    def text(self) -> str:
        return self.chunk.text


@dataclass(frozen=True, slots=True)
class PublicDeliveryReceipt:
    public_row_id: str
    idempotency_key: str
    provider_receipt_id: str
    delivered_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.public_row_id, "PublicDeliveryReceipt.public_row_id")
        _identifier(self.idempotency_key, "PublicDeliveryReceipt.idempotency_key")
        _identifier(
            self.provider_receipt_id, "PublicDeliveryReceipt.provider_receipt_id"
        )
        _utc(self.delivered_at, "PublicDeliveryReceipt.delivered_at")

    def to_canonical_bytes(self) -> bytes:
        return _canonical(
            {
                "delivered_at": self.delivered_at.isoformat(),
                "idempotency_key": self.idempotency_key,
                "provider_receipt_id": self.provider_receipt_id,
                "public_row_id": self.public_row_id,
            }
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(_RECEIPT_DOMAIN + self.to_canonical_bytes()).hexdigest()


class PublicDispatchReceiptPort(Protocol):
    def persist_delivery_receipt(self, receipt: PublicDeliveryReceipt) -> None: ...


__all__ = [
    "PublicDeliveryReceipt",
    "PublicDispatchClaim",
    "PublicDispatchReceiptPort",
]
