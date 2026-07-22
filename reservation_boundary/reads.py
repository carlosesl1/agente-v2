"""Closed typed read-request contracts for the Phase 8 boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import re
from typing import ClassVar, Final, TypeAlias

from reservation_boundary.conversation import SourceEventIdentity
from reservation_boundary.types import (
    ActivityDescriptionArguments,
    ActivityReadArguments,
    FaqReadArguments,
    LodgingReadArguments,
    RoomDescriptionArguments,
)


_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_LOCALE_RE: Final = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
READ_REQUEST_DOMAIN: Final = "phase8-read-request-v1"

ReadArguments: TypeAlias = (
    FaqReadArguments
    | LodgingReadArguments
    | RoomDescriptionArguments
    | ActivityReadArguments
    | ActivityDescriptionArguments
)

_TOOL_ARGUMENT_TYPES: Final = {
    "cerebro_consultar": FaqReadArguments,
    "cloudbeds_consultar_hospedagem_v2": LodgingReadArguments,
    "cloudbeds_descrever_quartos": RoomDescriptionArguments,
    "bokun_consultar_passeio_v2": ActivityReadArguments,
    "bokun_consultar_descricao": ActivityDescriptionArguments,
}


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


def _argument_data(arguments: ReadArguments) -> dict[str, object]:
    if type(arguments) is FaqReadArguments:
        return {"query": arguments.query, "locale": arguments.locale}
    if type(arguments) is LodgingReadArguments:
        return {
            "check_in": arguments.check_in.isoformat(),
            "check_out": arguments.check_out.isoformat(),
            "adults": arguments.adults,
            "children": arguments.children,
        }
    if type(arguments) is RoomDescriptionArguments:
        return {"room_offer_id": arguments.room_offer_id}
    if type(arguments) is ActivityReadArguments:
        return {
            "activity_id": arguments.activity_id,
            "activity_date": arguments.activity_date.isoformat(),
            "participants": arguments.participants,
        }
    if type(arguments) is ActivityDescriptionArguments:
        return {"activity_id": arguments.activity_id}
    raise TypeError("unsupported Phase 8 read arguments")


@dataclass(frozen=True, slots=True)
class Phase8ToolReadRequest:
    tool_name: str
    arguments: ReadArguments
    lead_key_hash: str
    aggregate_turn_id: str
    source_event: SourceEventIdentity
    deadline_at: datetime
    locale: str
    projection_hash: str

    SCHEMA: ClassVar[str] = "phase8-tool-read-request"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-tool-read-request-v1"

    def __post_init__(self) -> None:
        if type(self.tool_name) is not str:
            raise TypeError("Phase8ToolReadRequest.tool_name must be an exact string")
        expected = _TOOL_ARGUMENT_TYPES.get(self.tool_name)
        if expected is None:
            raise ValueError("Phase8ToolReadRequest.tool_name is outside the read catalog")
        if type(self.arguments) is not expected:
            raise TypeError("Phase8ToolReadRequest arguments do not match tool_name")
        _require_sha256(self.lead_key_hash, "Phase8ToolReadRequest.lead_key_hash")
        _require_identifier(
            self.aggregate_turn_id,
            "Phase8ToolReadRequest.aggregate_turn_id",
        )
        if type(self.source_event) is not SourceEventIdentity:
            raise TypeError("Phase8ToolReadRequest.source_event must be exact")
        _require_utc(self.deadline_at, "Phase8ToolReadRequest.deadline_at")
        if type(self.locale) is not str or _LOCALE_RE.fullmatch(self.locale) is None:
            raise ValueError("Phase8ToolReadRequest.locale must be canonical")
        _require_sha256(self.projection_hash, "Phase8ToolReadRequest.projection_hash")
        if (
            type(self.arguments) is FaqReadArguments
            and self.arguments.locale != self.locale
        ):
            raise ValueError("FAQ argument locale must equal request locale")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "tool_name": self.tool_name,
                "arguments": {
                    "type": type(self.arguments).__name__,
                    "data": _argument_data(self.arguments),
                },
                "lead_key_hash": self.lead_key_hash,
                "aggregate_turn_id": self.aggregate_turn_id,
                "source_event": json.loads(
                    self.source_event.to_canonical_bytes().decode("utf-8")
                ),
                "deadline_at": self.deadline_at.isoformat(),
                "locale": self.locale,
                "projection_hash": self.projection_hash,
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    def read_request_hash(self) -> str:
        return hashlib.sha256(
            READ_REQUEST_DOMAIN.encode("ascii")
            + b"\x00"
            + self.to_canonical_bytes()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class LegacyGenesisReadRequest:
    lead_key_hash: str
    aggregate_turn_id: str
    source_event: SourceEventIdentity
    deadline_at: datetime
    legacy_source: str

    SCHEMA: ClassVar[str] = "phase8-legacy-genesis-read-request"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-legacy-genesis-read-request-v1"
    LEGACY_SOURCE: ClassVar[str] = "chapada_leads_legacy_v1"

    def __post_init__(self) -> None:
        _require_sha256(self.lead_key_hash, "LegacyGenesisReadRequest.lead_key_hash")
        _require_identifier(
            self.aggregate_turn_id,
            "LegacyGenesisReadRequest.aggregate_turn_id",
        )
        if type(self.source_event) is not SourceEventIdentity:
            raise TypeError("LegacyGenesisReadRequest.source_event must be exact")
        _require_utc(self.deadline_at, "LegacyGenesisReadRequest.deadline_at")
        if self.legacy_source != self.LEGACY_SOURCE:
            raise ValueError("LegacyGenesisReadRequest.legacy_source is not supported")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "lead_key_hash": self.lead_key_hash,
                "aggregate_turn_id": self.aggregate_turn_id,
                "source_event": json.loads(
                    self.source_event.to_canonical_bytes().decode("utf-8")
                ),
                "deadline_at": self.deadline_at.isoformat(),
                "legacy_source": self.legacy_source,
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    def read_request_hash(self) -> str:
        return hashlib.sha256(
            READ_REQUEST_DOMAIN.encode("ascii")
            + b"\x00"
            + self.to_canonical_bytes()
        ).hexdigest()


Phase8ReadRequest: TypeAlias = Phase8ToolReadRequest | LegacyGenesisReadRequest


__all__ = (
    "LegacyGenesisReadRequest",
    "Phase8ReadRequest",
    "Phase8ToolReadRequest",
    "READ_REQUEST_DOMAIN",
    "ReadArguments",
)
