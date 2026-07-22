"""Closed typed read-request contracts for the Phase 8 boundary."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re
import unicodedata
from typing import ClassVar, Final, TypeAlias

from reservation_boundary.conversation import (
    ConversationProjection,
    ConversationStage,
    DesiredService,
    ReservationExecutionProjection,
    SourceEventIdentity,
)
from reservation_boundary.types import (
    ActivityDescriptionArguments,
    ActivityReadArguments,
    DateSlot,
    FaqReadArguments,
    IntegerSlot,
    LodgingReadArguments,
    RoomDescriptionArguments,
    StringSlot,
    TypedFact,
)
from reservation_domain import Party, SearchQuery, ServiceKind


_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_LOCALE_RE: Final = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_GENESIS_ID_RE: Final = re.compile(r"^genesis:[0-9a-f]{64}$")
_READ_EVIDENCE_ID_RE: Final = re.compile(r"^read-evidence:[0-9a-f]{64}$")
_OFFER_ID_RE: Final = re.compile(r"^offer:[0-9a-f]{64}$")
_LOOKUP_ID_RE: Final = re.compile(r"^lookup:[0-9a-f]{64}$")
_CURRENCY_RE: Final = re.compile(r"^[A-Z]{3}$")
_DECIMAL_2_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")
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


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_canonical_envelope(payload: bytes, name: str) -> dict[str, object]:
    if type(payload) is not bytes or not payload:
        raise TypeError(f"{name} must be non-empty exact bytes")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {item}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be canonical UTF-8 JSON") from exc
    if type(value) is not dict or set(value) != {"schema", "version", "data"}:
        raise ValueError(f"{name} envelope fields mismatch")
    if type(value["schema"]) is not str or type(value["version"]) is not int:
        raise ValueError(f"{name} envelope identity has wrong exact type")
    if type(value["data"]) is not dict:
        raise ValueError(f"{name} data must be an object")
    if _canonical_envelope(
        schema=value["schema"],
        version=value["version"],
        data=value["data"],
    ) != payload:
        raise ValueError(f"{name} must use exact canonical JSON")
    return value


def _decode_base64(value: object, name: str) -> bytes:
    if type(value) is not str:
        raise ValueError(f"{name} must be a Base64 string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must use standard Base64") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{name} must use canonical standard Base64")
    if not decoded:
        raise ValueError(f"{name} must not decode to empty bytes")
    return decoded


def _encode_optional_bytes(value: bytes | None) -> str | None:
    return base64.b64encode(value).decode("ascii") if value is not None else None


def _decode_optional_bytes(value: object, name: str) -> bytes | None:
    return None if value is None else _decode_base64(value, name)


def _parse_utc(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{name} must be text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be canonical UTC datetime") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat() != value
    ):
        raise ValueError(f"{name} must be canonical UTC datetime")
    return parsed


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


class GenesisStatus(str, Enum):
    FOUND = "found"
    PROVEN_ABSENT = "proven_absent"
    UNAVAILABLE = "unavailable"


class LegacyUnavailableReason(str, Enum):
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    MALFORMED = "malformed"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    IDENTITY_CONFLICT = "identity_conflict"


@dataclass(frozen=True, slots=True)
class LegacyGenesisReceipt:
    receipt_id: str
    request_hash: str
    lead_key_hash: str
    status: GenesisStatus
    source_generation: int | None
    source_watermark_hash: str | None
    matched_row_count: int | None
    source_snapshot_hash: str | None
    projection_hash: str | None
    failure_reason: LegacyUnavailableReason | None
    failure_evidence_hash: str | None
    completed_at: datetime

    SCHEMA: ClassVar[str] = "phase8-legacy-genesis-receipt"
    ID_PREIMAGE_SCHEMA: ClassVar[str] = "phase8-legacy-genesis-receipt-id-preimage"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-legacy-genesis-receipt-v1"
    RECEIPT_ID_DOMAIN: ClassVar[str] = "phase8-legacy-genesis-receipt-id-v1"
    WATERMARK_DOMAIN: ClassVar[str] = "phase8-legacy-watermark-v1"
    SNAPSHOT_DOMAIN: ClassVar[str] = "phase8-legacy-snapshot-v1"
    FAILURE_DOMAIN: ClassVar[str] = "phase8-legacy-genesis-failure-v1"

    def __post_init__(self) -> None:
        if type(self.receipt_id) is not str or _GENESIS_ID_RE.fullmatch(self.receipt_id) is None:
            raise ValueError("LegacyGenesisReceipt.receipt_id must be canonical")
        _require_sha256(self.request_hash, "LegacyGenesisReceipt.request_hash")
        _require_sha256(self.lead_key_hash, "LegacyGenesisReceipt.lead_key_hash")
        if type(self.status) is not GenesisStatus:
            raise TypeError("LegacyGenesisReceipt.status must be exact")
        _require_utc(self.completed_at, "LegacyGenesisReceipt.completed_at")

        if self.status in {GenesisStatus.FOUND, GenesisStatus.PROVEN_ABSENT}:
            if type(self.source_generation) is not int or self.source_generation < 1:
                raise ValueError("successful genesis requires source_generation >= 1")
            _require_sha256(
                self.source_watermark_hash,
                "LegacyGenesisReceipt.source_watermark_hash",
            )
            if type(self.matched_row_count) is not int:
                raise TypeError("matched_row_count must be an exact integer")
            expected_rows = 1 if self.status is GenesisStatus.FOUND else 0
            if self.matched_row_count != expected_rows:
                raise ValueError("matched_row_count does not match genesis status")
            if self.failure_reason is not None or self.failure_evidence_hash is not None:
                raise ValueError("successful genesis cannot carry failure evidence")
            if self.status is GenesisStatus.FOUND:
                _require_sha256(
                    self.source_snapshot_hash,
                    "LegacyGenesisReceipt.source_snapshot_hash",
                )
                _require_sha256(
                    self.projection_hash,
                    "LegacyGenesisReceipt.projection_hash",
                )
            elif self.source_snapshot_hash is not None or self.projection_hash is not None:
                raise ValueError("proven_absent cannot carry snapshot or projection")
        else:
            if any(
                value is not None
                for value in (
                    self.source_generation,
                    self.source_watermark_hash,
                    self.matched_row_count,
                    self.source_snapshot_hash,
                    self.projection_hash,
                )
            ):
                raise ValueError("unavailable genesis cannot carry successful scan evidence")
            if type(self.failure_reason) is not LegacyUnavailableReason:
                raise TypeError("unavailable genesis requires an exact failure reason")
            _require_sha256(
                self.failure_evidence_hash,
                "LegacyGenesisReceipt.failure_evidence_hash",
            )

        expected_id = "genesis:" + hashlib.sha256(
            self.RECEIPT_ID_DOMAIN.encode("ascii")
            + b"\x00"
            + self.id_preimage_bytes()
        ).hexdigest()
        if self.receipt_id != expected_id:
            raise ValueError("LegacyGenesisReceipt.receipt_id does not bind its fields")

    def _data_without_id(self) -> dict[str, object]:
        return {
            "request_hash": self.request_hash,
            "lead_key_hash": self.lead_key_hash,
            "status": self.status.value,
            "source_generation": self.source_generation,
            "source_watermark_hash": self.source_watermark_hash,
            "matched_row_count": self.matched_row_count,
            "source_snapshot_hash": self.source_snapshot_hash,
            "projection_hash": self.projection_hash,
            "failure_reason": (
                self.failure_reason.value if self.failure_reason is not None else None
            ),
            "failure_evidence_hash": self.failure_evidence_hash,
            "completed_at": self.completed_at.isoformat(),
        }

    def id_preimage_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.ID_PREIMAGE_SCHEMA,
            version=self.VERSION,
            data=self._data_without_id(),
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={"receipt_id": self.receipt_id} | self._data_without_id(),
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "LegacyGenesisReceipt":
        envelope = _load_canonical_envelope(payload, "LegacyGenesisReceipt")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("LegacyGenesisReceipt envelope identity mismatch")
        data = envelope["data"]
        expected = {
            "receipt_id",
            "request_hash",
            "lead_key_hash",
            "status",
            "source_generation",
            "source_watermark_hash",
            "matched_row_count",
            "source_snapshot_hash",
            "projection_hash",
            "failure_reason",
            "failure_evidence_hash",
            "completed_at",
        }
        if set(data) != expected:
            raise ValueError("LegacyGenesisReceipt fields mismatch")
        if type(data["status"]) is not str:
            raise ValueError("LegacyGenesisReceipt.status must be text")
        try:
            status = GenesisStatus(data["status"])
            failure_reason = (
                LegacyUnavailableReason(data["failure_reason"])
                if data["failure_reason"] is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("LegacyGenesisReceipt enum value is invalid") from exc
        receipt = cls(
            receipt_id=data["receipt_id"],
            request_hash=data["request_hash"],
            lead_key_hash=data["lead_key_hash"],
            status=status,
            source_generation=data["source_generation"],
            source_watermark_hash=data["source_watermark_hash"],
            matched_row_count=data["matched_row_count"],
            source_snapshot_hash=data["source_snapshot_hash"],
            projection_hash=data["projection_hash"],
            failure_reason=failure_reason,
            failure_evidence_hash=data["failure_evidence_hash"],
            completed_at=_parse_utc(data["completed_at"], "completed_at"),
        )
        if receipt.to_canonical_bytes() != payload:
            raise ValueError("LegacyGenesisReceipt is not byte-canonical")
        return receipt


@dataclass(frozen=True, slots=True)
class LegacyGenesisEvidenceRecord:
    receipt_bytes: bytes
    source_watermark_bytes: bytes | None
    source_snapshot_bytes: bytes | None
    failure_evidence_bytes: bytes | None

    SCHEMA: ClassVar[str] = "phase8-legacy-genesis-evidence-record"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-legacy-genesis-evidence-record-v1"

    def __post_init__(self) -> None:
        receipt = LegacyGenesisReceipt.from_canonical_bytes(self.receipt_bytes)
        present = (
            self.source_watermark_bytes is not None,
            self.source_snapshot_bytes is not None,
            self.failure_evidence_bytes is not None,
        )
        expected = {
            GenesisStatus.FOUND: (True, True, False),
            GenesisStatus.PROVEN_ABSENT: (True, False, False),
            GenesisStatus.UNAVAILABLE: (False, False, True),
        }[receipt.status]
        if present != expected:
            raise ValueError("LegacyGenesisEvidenceRecord status matrix mismatch")

        if self.source_watermark_bytes is not None:
            watermark = _load_canonical_envelope(
                self.source_watermark_bytes,
                "source_watermark_bytes",
            )
            if (
                watermark["schema"] != "phase8-legacy-source-watermark"
                or watermark["version"] != 1
                or set(watermark["data"])
                != {"source", "source_generation", "transaction_snapshot_id"}
            ):
                raise ValueError("legacy source watermark contract mismatch")
            data = watermark["data"]
            if data["source"] != LegacyGenesisReadRequest.LEGACY_SOURCE:
                raise ValueError("legacy source watermark source mismatch")
            if type(data["source_generation"]) is not int or data["source_generation"] < 1:
                raise ValueError("legacy source watermark generation is invalid")
            _require_identifier(
                data["transaction_snapshot_id"],
                "transaction_snapshot_id",
            )
            if data["source_generation"] != receipt.source_generation:
                raise ValueError("legacy source watermark generation mismatch")
            if hashlib.sha256(
                LegacyGenesisReceipt.WATERMARK_DOMAIN.encode("ascii")
                + b"\x00"
                + self.source_watermark_bytes
            ).hexdigest() != receipt.source_watermark_hash:
                raise ValueError("legacy source watermark hash mismatch")

        if self.source_snapshot_bytes is not None:
            snapshot = _load_canonical_envelope(
                self.source_snapshot_bytes,
                "source_snapshot_bytes",
            )
            if (
                snapshot["schema"] != "phase8-legacy-snapshot-evidence"
                or snapshot["version"] != 1
                or set(snapshot["data"])
                != {
                    "source",
                    "source_generation",
                    "source_watermark_hash",
                    "lead_key_hash",
                    "matched_row_count",
                    "projection_bytes",
                    "projection_hash",
                }
            ):
                raise ValueError("legacy snapshot evidence contract mismatch")
            data = snapshot["data"]
            if (
                data["source"] != LegacyGenesisReadRequest.LEGACY_SOURCE
                or data["source_generation"] != receipt.source_generation
                or data["source_watermark_hash"] != receipt.source_watermark_hash
                or data["lead_key_hash"] != receipt.lead_key_hash
                or data["matched_row_count"] != 1
                or data["projection_hash"] != receipt.projection_hash
            ):
                raise ValueError("legacy snapshot evidence binding mismatch")
            projection_bytes = _decode_base64(data["projection_bytes"], "projection_bytes")
            projection = _load_canonical_envelope(projection_bytes, "projection_bytes")
            if projection["schema"] != "phase8-conversation-projection" or projection["version"] != 1:
                raise ValueError("legacy snapshot projection identity mismatch")
            if hashlib.sha256(
                b"phase8-conversation-projection-v1\x00" + projection_bytes
            ).hexdigest() != data["projection_hash"]:
                raise ValueError("legacy snapshot projection hash mismatch")
            if hashlib.sha256(
                LegacyGenesisReceipt.SNAPSHOT_DOMAIN.encode("ascii")
                + b"\x00"
                + self.source_snapshot_bytes
            ).hexdigest() != receipt.source_snapshot_hash:
                raise ValueError("legacy snapshot evidence hash mismatch")

        if self.failure_evidence_bytes is not None:
            failure = _load_canonical_envelope(
                self.failure_evidence_bytes,
                "failure_evidence_bytes",
            )
            if (
                failure["schema"] != "phase8-legacy-failure-evidence"
                or failure["version"] != 1
                or set(failure["data"])
                != {
                    "source",
                    "request_hash",
                    "lead_key_hash",
                    "failure_reason",
                    "attempt_count",
                    "observed_at",
                }
            ):
                raise ValueError("legacy failure evidence contract mismatch")
            data = failure["data"]
            if (
                data["source"] != LegacyGenesisReadRequest.LEGACY_SOURCE
                or data["request_hash"] != receipt.request_hash
                or data["lead_key_hash"] != receipt.lead_key_hash
                or data["failure_reason"] != receipt.failure_reason.value
                or type(data["attempt_count"]) is not int
                or data["attempt_count"] < 1
                or _parse_utc(data["observed_at"], "observed_at") != receipt.completed_at
            ):
                raise ValueError("legacy failure evidence binding mismatch")
            if hashlib.sha256(
                LegacyGenesisReceipt.FAILURE_DOMAIN.encode("ascii")
                + b"\x00"
                + self.failure_evidence_bytes
            ).hexdigest() != receipt.failure_evidence_hash:
                raise ValueError("legacy failure evidence hash mismatch")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "receipt_bytes": base64.b64encode(self.receipt_bytes).decode("ascii"),
                "source_watermark_bytes": _encode_optional_bytes(
                    self.source_watermark_bytes
                ),
                "source_snapshot_bytes": _encode_optional_bytes(
                    self.source_snapshot_bytes
                ),
                "failure_evidence_bytes": _encode_optional_bytes(
                    self.failure_evidence_bytes
                ),
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "LegacyGenesisEvidenceRecord":
        envelope = _load_canonical_envelope(payload, "LegacyGenesisEvidenceRecord")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("LegacyGenesisEvidenceRecord envelope identity mismatch")
        data = envelope["data"]
        expected = {
            "receipt_bytes",
            "source_watermark_bytes",
            "source_snapshot_bytes",
            "failure_evidence_bytes",
        }
        if set(data) != expected:
            raise ValueError("LegacyGenesisEvidenceRecord fields mismatch")
        record = cls(
            receipt_bytes=_decode_base64(data["receipt_bytes"], "receipt_bytes"),
            source_watermark_bytes=_decode_optional_bytes(
                data["source_watermark_bytes"],
                "source_watermark_bytes",
            ),
            source_snapshot_bytes=_decode_optional_bytes(
                data["source_snapshot_bytes"],
                "source_snapshot_bytes",
            ),
            failure_evidence_bytes=_decode_optional_bytes(
                data["failure_evidence_bytes"],
                "failure_evidence_bytes",
            ),
        )
        if record.to_canonical_bytes() != payload:
            raise ValueError("LegacyGenesisEvidenceRecord is not byte-canonical")
        return record


_PUBLIC_READ_POLICY: Final = {
    "forbidden_patterns": {
        "br_phone": r"(?<![0-9])(?:\+?55[\s.-]?)?(?:\(?[1-9][0-9]\)?[\s.-]?)?(?:9[0-9]{4}|[2-8][0-9]{3})[\s.-]?[0-9]{4}(?![0-9])",
        "control": r"[\u0000-\u0008\u000b-\u001f\u007f]",
        "cpf": r"(?<![0-9])(?:[0-9]{3}[.\s-]?){2}[0-9]{3}[-.\s]?[0-9]{2}(?![0-9])",
        "e164": r"(?<![0-9])\+[1-9][0-9]{7,14}(?![0-9])",
        "email": r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]{1,64}@[A-Z0-9.-]+\.[A-Z]{2,63}(?![A-Z0-9._%+-])",
        "html": r"<[A-Za-z!/][^>]*>",
        "markdown_link": r"!?\[[^\]]*\]\([^)]+\)",
        "pan": r"(?<![0-9])(?:[0-9][ -]?){12,18}[0-9](?![0-9])",
        "provider_ref": r"(?:cloudbeds\.property\.|bokun\.product\.)",
        "random_payment_key": r"(?i)(?<![0-9A-F])[0-9A-F]{8}-[0-9A-F]{4}-[1-5][0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}(?![0-9A-F])",
        "secret_marker": r"(?i)\b(?:api[_-]?key|access[_-]?token|bearer)\b\s*[:=]",
        "url": r"(?i)(?:https?://|www\.)\S+",
    },
    "limits": {"knowledge_codepoints": 4096, "label_codepoints": 256},
    "normalization": {
        "double_ascii_space": "forbidden",
        "line_ending": "LF",
        "surrounding_whitespace": "forbidden",
        "tab": "forbidden",
        "unicode": "NFKC",
    },
    "schema": "phase8-public-read-sanitization-policy",
    "version": 1,
}
PUBLIC_READ_POLICY_ID: Final = "public-read-v1"
PUBLIC_READ_POLICY_DOMAIN: Final = "phase8-public-read-sanitization-policy-v1"
PUBLIC_READ_POLICY_BYTES: Final = json.dumps(
    _PUBLIC_READ_POLICY,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
PUBLIC_READ_POLICY_HASH: Final = hashlib.sha256(
    PUBLIC_READ_POLICY_DOMAIN.encode("ascii")
    + b"\x00"
    + PUBLIC_READ_POLICY_BYTES
).hexdigest()
if PUBLIC_READ_POLICY_HASH != "2a3f36953a7d1020df4d3d5f2471df8767be4f99f23413f9effc38d03ac7b637":
    raise RuntimeError("public read policy identity drift")
_PUBLIC_READ_PATTERNS: Final = tuple(
    re.compile(pattern)
    for pattern in _PUBLIC_READ_POLICY["forbidden_patterns"].values()
)


def validate_public_text(value: object, *, limit: int) -> str:
    if type(value) is not str or not value:
        raise ValueError("public text must be a non-empty exact string")
    if unicodedata.normalize("NFKC", value) != value:
        raise ValueError("public text must already use NFKC")
    if value != value.strip() or "\r" in value or "\t" in value or "  " in value:
        raise ValueError("public text normalization mismatch")
    if len(value) > limit:
        raise ValueError("public text exceeds its code-point limit")
    if any(pattern.search(value) for pattern in _PUBLIC_READ_PATTERNS):
        raise ValueError("public text contains forbidden private or active content")
    return value


class ReadEvidenceDisposition(str, Enum):
    PUBLIC_SAFE = "public_safe"
    PRIVATE_ONLY = "private_only"


@dataclass(frozen=True, slots=True)
class ReadEvidenceReceipt:
    receipt_id: str
    request_hash: str
    result_content_hash: str
    source_evidence_hash: str
    policy_id: str
    policy_hash: str
    disposition: ReadEvidenceDisposition
    observed_at: datetime
    expires_at: datetime

    SCHEMA: ClassVar[str] = "phase8-read-evidence-receipt"
    ID_PREIMAGE_SCHEMA: ClassVar[str] = "phase8-read-evidence-receipt-id-preimage"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-read-evidence-receipt-v1"
    RECEIPT_ID_DOMAIN: ClassVar[str] = "phase8-read-evidence-receipt-id-v1"
    SOURCE_EVIDENCE_DOMAIN: ClassVar[str] = "phase8-read-source-evidence-v1"
    RESULT_CONTENT_DOMAIN: ClassVar[str] = "phase8-read-result-content-v1"

    def __post_init__(self) -> None:
        if (
            type(self.receipt_id) is not str
            or _READ_EVIDENCE_ID_RE.fullmatch(self.receipt_id) is None
        ):
            raise ValueError("ReadEvidenceReceipt.receipt_id must be canonical")
        _require_sha256(self.request_hash, "ReadEvidenceReceipt.request_hash")
        _require_sha256(
            self.result_content_hash,
            "ReadEvidenceReceipt.result_content_hash",
        )
        _require_sha256(
            self.source_evidence_hash,
            "ReadEvidenceReceipt.source_evidence_hash",
        )
        if self.policy_id != PUBLIC_READ_POLICY_ID or self.policy_hash != PUBLIC_READ_POLICY_HASH:
            raise ValueError("ReadEvidenceReceipt policy identity mismatch")
        if type(self.disposition) is not ReadEvidenceDisposition:
            raise TypeError("ReadEvidenceReceipt.disposition must be exact")
        _require_utc(self.observed_at, "ReadEvidenceReceipt.observed_at")
        _require_utc(self.expires_at, "ReadEvidenceReceipt.expires_at")
        if self.expires_at <= self.observed_at:
            raise ValueError("ReadEvidenceReceipt.expires_at must be later than observed_at")
        expected_id = "read-evidence:" + hashlib.sha256(
            self.RECEIPT_ID_DOMAIN.encode("ascii")
            + b"\x00"
            + self.id_preimage_bytes()
        ).hexdigest()
        if self.receipt_id != expected_id:
            raise ValueError("ReadEvidenceReceipt.receipt_id does not bind its fields")

    def _data_without_id(self) -> dict[str, object]:
        return {
            "request_hash": self.request_hash,
            "result_content_hash": self.result_content_hash,
            "source_evidence_hash": self.source_evidence_hash,
            "policy_id": self.policy_id,
            "policy_hash": self.policy_hash,
            "disposition": self.disposition.value,
            "observed_at": self.observed_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    def id_preimage_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.ID_PREIMAGE_SCHEMA,
            version=self.VERSION,
            data=self._data_without_id(),
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={"receipt_id": self.receipt_id} | self._data_without_id(),
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "ReadEvidenceReceipt":
        envelope = _load_canonical_envelope(payload, "ReadEvidenceReceipt")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("ReadEvidenceReceipt envelope identity mismatch")
        data = envelope["data"]
        expected = {
            "receipt_id",
            "request_hash",
            "result_content_hash",
            "source_evidence_hash",
            "policy_id",
            "policy_hash",
            "disposition",
            "observed_at",
            "expires_at",
        }
        if set(data) != expected or type(data["disposition"]) is not str:
            raise ValueError("ReadEvidenceReceipt fields mismatch")
        try:
            disposition = ReadEvidenceDisposition(data["disposition"])
        except ValueError as exc:
            raise ValueError("ReadEvidenceReceipt disposition is invalid") from exc
        receipt = cls(
            receipt_id=data["receipt_id"],
            request_hash=data["request_hash"],
            result_content_hash=data["result_content_hash"],
            source_evidence_hash=data["source_evidence_hash"],
            policy_id=data["policy_id"],
            policy_hash=data["policy_hash"],
            disposition=disposition,
            observed_at=_parse_utc(data["observed_at"], "observed_at"),
            expires_at=_parse_utc(data["expires_at"], "expires_at"),
        )
        if receipt.to_canonical_bytes() != payload:
            raise ValueError("ReadEvidenceReceipt is not byte-canonical")
        return receipt


class KnowledgeSource(str, Enum):
    FAQ = "faq"
    LODGING_DESCRIPTION = "lodging_description"
    ACTIVITY_DESCRIPTION = "activity_description"


@dataclass(frozen=True, slots=True)
class SanitizedKnowledgeResult:
    request_hash: str
    source: KnowledgeSource
    subject_id: str | None
    locale: str
    answer_text: str
    evidence_receipt: ReadEvidenceReceipt

    SCHEMA: ClassVar[str] = "phase8-sanitized-knowledge-result"
    CONTENT_PREIMAGE_SCHEMA: ClassVar[str] = (
        "phase8-sanitized-knowledge-result-content-preimage"
    )
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-sanitized-knowledge-result-v1"

    def __post_init__(self) -> None:
        _require_sha256(self.request_hash, "SanitizedKnowledgeResult.request_hash")
        if type(self.source) is not KnowledgeSource:
            raise TypeError("SanitizedKnowledgeResult.source must be exact")
        if self.source is KnowledgeSource.FAQ:
            if self.subject_id is not None:
                raise ValueError("FAQ knowledge result must have null subject_id")
        else:
            _require_identifier(
                self.subject_id,
                "SanitizedKnowledgeResult.subject_id",
            )
        if type(self.locale) is not str or _LOCALE_RE.fullmatch(self.locale) is None:
            raise ValueError("SanitizedKnowledgeResult.locale must be canonical")
        validate_public_text(self.answer_text, limit=4096)
        if type(self.evidence_receipt) is not ReadEvidenceReceipt:
            raise TypeError("SanitizedKnowledgeResult.evidence_receipt must be exact")
        if self.evidence_receipt.request_hash != self.request_hash:
            raise ValueError("knowledge result request hash mismatch")
        expected_content_hash = hashlib.sha256(
            ReadEvidenceReceipt.RESULT_CONTENT_DOMAIN.encode("ascii")
            + b"\x00"
            + self.content_preimage_bytes()
        ).hexdigest()
        if self.evidence_receipt.result_content_hash != expected_content_hash:
            raise ValueError("knowledge result content hash mismatch")

    def _content_data(self) -> dict[str, object]:
        return {
            "request_hash": self.request_hash,
            "source": self.source.value,
            "subject_id": self.subject_id,
            "locale": self.locale,
            "answer_text": self.answer_text,
        }

    def content_preimage_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.CONTENT_PREIMAGE_SCHEMA,
            version=self.VERSION,
            data=self._content_data(),
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data=self._content_data()
            | {
                "evidence_receipt": json.loads(
                    self.evidence_receipt.to_canonical_bytes().decode("utf-8")
                )
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "SanitizedKnowledgeResult":
        envelope = _load_canonical_envelope(payload, "SanitizedKnowledgeResult")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("SanitizedKnowledgeResult envelope identity mismatch")
        data = envelope["data"]
        expected = {
            "request_hash",
            "source",
            "subject_id",
            "locale",
            "answer_text",
            "evidence_receipt",
        }
        if set(data) != expected or type(data["source"]) is not str:
            raise ValueError("SanitizedKnowledgeResult fields mismatch")
        try:
            source = KnowledgeSource(data["source"])
        except ValueError as exc:
            raise ValueError("SanitizedKnowledgeResult source is invalid") from exc
        nested = data["evidence_receipt"]
        if type(nested) is not dict or set(nested) != {"schema", "version", "data"}:
            raise ValueError("knowledge evidence receipt envelope mismatch")
        receipt_bytes = _canonical_envelope(
            schema=nested["schema"],
            version=nested["version"],
            data=nested["data"],
        )
        result = cls(
            request_hash=data["request_hash"],
            source=source,
            subject_id=data["subject_id"],
            locale=data["locale"],
            answer_text=data["answer_text"],
            evidence_receipt=ReadEvidenceReceipt.from_canonical_bytes(receipt_bytes),
        )
        if result.to_canonical_bytes() != payload:
            raise ValueError("SanitizedKnowledgeResult is not byte-canonical")
        return result


class ReadService(str, Enum):
    LODGING = "lodging"
    ACTIVITY = "activity"


class SanitizedLookupStatus(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNCERTAIN = "uncertain"


class LookupFailureCode(str, Enum):
    TRANSPORT_ERROR = "transport_error"
    HTTP_ERROR = "http_error"
    SCHEMA_ERROR = "schema_error"


def _require_exact_date(value: object, name: str) -> date:
    if type(value) is not date:
        raise TypeError(f"{name} must be an exact date")
    return value


def _parse_date(value: object, name: str) -> date:
    if type(value) is not str:
        raise ValueError(f"{name} must be canonical date text")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be canonical date text") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be canonical date text")
    return parsed


def _require_minute_time(value: object, name: str) -> time:
    if type(value) is not time:
        raise TypeError(f"{name} must be an exact time")
    if value.tzinfo is not None or value.second != 0 or value.microsecond != 0:
        raise ValueError(f"{name} must use canonical HH:MM time")
    return value


def _parse_minute_time(value: object, name: str) -> time:
    if type(value) is not str or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
        raise ValueError(f"{name} must use canonical HH:MM time")
    return time.fromisoformat(value)


def _require_decimal_2(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be an exact Decimal")
    text = format(value, "f")
    if _DECIMAL_2_RE.fullmatch(text) is None or value <= 0:
        raise ValueError(f"{name} must be a positive two-decimal amount")
    return value


def _parse_decimal_2(value: object, name: str) -> Decimal:
    if type(value) is not str or _DECIMAL_2_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be canonical two-decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be canonical two-decimal text") from exc
    return _require_decimal_2(parsed, name)


def _nested_contract_bytes(value: object, name: str) -> bytes:
    if type(value) is not dict or set(value) != {"schema", "version", "data"}:
        raise ValueError(f"{name} envelope mismatch")
    if type(value["schema"]) is not str or type(value["version"]) is not int:
        raise ValueError(f"{name} envelope identity has wrong exact type")
    if type(value["data"]) is not dict:
        raise ValueError(f"{name} data must be an object")
    return _canonical_envelope(
        schema=value["schema"],
        version=value["version"],
        data=value["data"],
    )


@dataclass(frozen=True, slots=True)
class SanitizedOffer:
    offer_id: str
    service: ReadService
    public_label: str
    start_date: date
    end_date: date | None
    start_time: time | None
    adults: int
    children: int
    total_amount: Decimal
    currency: str

    SCHEMA: ClassVar[str] = "phase8-sanitized-offer"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-sanitized-offer-v1"

    def __post_init__(self) -> None:
        if type(self.offer_id) is not str or _OFFER_ID_RE.fullmatch(self.offer_id) is None:
            raise ValueError("SanitizedOffer.offer_id must be canonical")
        if type(self.service) is not ReadService:
            raise TypeError("SanitizedOffer.service must be exact")
        validate_public_text(self.public_label, limit=256)
        _require_exact_date(self.start_date, "SanitizedOffer.start_date")
        if self.end_date is not None:
            _require_exact_date(self.end_date, "SanitizedOffer.end_date")
        if self.start_time is not None:
            _require_minute_time(self.start_time, "SanitizedOffer.start_time")
        if type(self.adults) is not int or self.adults < 1:
            raise ValueError("SanitizedOffer.adults must be an exact integer >= 1")
        if type(self.children) is not int or self.children < 0:
            raise ValueError("SanitizedOffer.children must be an exact integer >= 0")
        _require_decimal_2(self.total_amount, "SanitizedOffer.total_amount")
        if type(self.currency) is not str or _CURRENCY_RE.fullmatch(self.currency) is None:
            raise ValueError("SanitizedOffer.currency must be canonical")
        if self.service is ReadService.LODGING:
            if self.end_date is None or self.end_date <= self.start_date:
                raise ValueError("lodging offer requires end_date after start_date")
        elif self.end_date is not None:
            raise ValueError("activity offer requires null end_date")

    def _data(self) -> dict[str, object]:
        return {
            "offer_id": self.offer_id,
            "service": self.service.value,
            "public_label": self.public_label,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat() if self.end_date is not None else None,
            "start_time": (
                self.start_time.strftime("%H:%M") if self.start_time is not None else None
            ),
            "adults": self.adults,
            "children": self.children,
            "total_amount": format(self.total_amount, "f"),
            "currency": self.currency,
        }

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data=self._data(),
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "SanitizedOffer":
        envelope = _load_canonical_envelope(payload, "SanitizedOffer")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("SanitizedOffer envelope identity mismatch")
        data = envelope["data"]
        expected = {
            "offer_id",
            "service",
            "public_label",
            "start_date",
            "end_date",
            "start_time",
            "adults",
            "children",
            "total_amount",
            "currency",
        }
        if set(data) != expected or type(data["service"]) is not str:
            raise ValueError("SanitizedOffer fields mismatch")
        try:
            service = ReadService(data["service"])
        except ValueError as exc:
            raise ValueError("SanitizedOffer service is invalid") from exc
        offer = cls(
            offer_id=data["offer_id"],
            service=service,
            public_label=data["public_label"],
            start_date=_parse_date(data["start_date"], "start_date"),
            end_date=(
                _parse_date(data["end_date"], "end_date")
                if data["end_date"] is not None
                else None
            ),
            start_time=(
                _parse_minute_time(data["start_time"], "start_time")
                if data["start_time"] is not None
                else None
            ),
            adults=data["adults"],
            children=data["children"],
            total_amount=_parse_decimal_2(data["total_amount"], "total_amount"),
            currency=data["currency"],
        )
        if offer.to_canonical_bytes() != payload:
            raise ValueError("SanitizedOffer is not byte-canonical")
        return offer


@dataclass(frozen=True, slots=True)
class SanitizedLookupResult:
    request_hash: str
    service: ReadService
    status: SanitizedLookupStatus
    query_signature: str
    lookup_id: str
    observed_at: datetime
    expires_at: datetime
    snapshot_hash: str
    offers: tuple[SanitizedOffer, ...]
    failure_codes: tuple[LookupFailureCode, ...]
    evidence_receipt: ReadEvidenceReceipt

    SCHEMA: ClassVar[str] = "phase8-sanitized-lookup-result"
    CONTENT_PREIMAGE_SCHEMA: ClassVar[str] = (
        "phase8-sanitized-lookup-result-content-preimage"
    )
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-sanitized-lookup-result-v1"

    def __post_init__(self) -> None:
        _require_sha256(self.request_hash, "SanitizedLookupResult.request_hash")
        if type(self.service) is not ReadService:
            raise TypeError("SanitizedLookupResult.service must be exact")
        if type(self.status) is not SanitizedLookupStatus:
            raise TypeError("SanitizedLookupResult.status must be exact")
        _require_sha256(self.query_signature, "SanitizedLookupResult.query_signature")
        if type(self.lookup_id) is not str or _LOOKUP_ID_RE.fullmatch(self.lookup_id) is None:
            raise ValueError("SanitizedLookupResult.lookup_id must be canonical")
        _require_utc(self.observed_at, "SanitizedLookupResult.observed_at")
        _require_utc(self.expires_at, "SanitizedLookupResult.expires_at")
        if self.expires_at <= self.observed_at:
            raise ValueError("lookup expires_at must be later than observed_at")
        _require_sha256(self.snapshot_hash, "SanitizedLookupResult.snapshot_hash")
        if type(self.offers) is not tuple or any(type(item) is not SanitizedOffer for item in self.offers):
            raise TypeError("SanitizedLookupResult.offers must be an exact offer tuple")
        if tuple(sorted(self.offers, key=lambda item: item.offer_id)) != self.offers:
            raise ValueError("SanitizedLookupResult.offers must be sorted")
        offer_ids = tuple(item.offer_id for item in self.offers)
        if len(set(offer_ids)) != len(offer_ids):
            raise ValueError("SanitizedLookupResult.offer IDs must be unique")
        if any(item.service is not self.service for item in self.offers):
            raise ValueError("SanitizedLookupResult offer service mismatch")
        if type(self.failure_codes) is not tuple or any(
            type(item) is not LookupFailureCode for item in self.failure_codes
        ):
            raise TypeError("failure_codes must be an exact enum tuple")
        if tuple(sorted(self.failure_codes, key=lambda item: item.value)) != self.failure_codes:
            raise ValueError("failure_codes must be sorted")
        if len(set(self.failure_codes)) != len(self.failure_codes):
            raise ValueError("failure_codes must be unique")
        expected_cardinality = {
            SanitizedLookupStatus.POSITIVE: (bool(self.offers), not self.failure_codes),
            SanitizedLookupStatus.NEGATIVE: (not self.offers, not self.failure_codes),
            SanitizedLookupStatus.UNCERTAIN: (not self.offers, bool(self.failure_codes)),
        }[self.status]
        if expected_cardinality != (True, True):
            raise ValueError("lookup status cardinality mismatch")
        if type(self.evidence_receipt) is not ReadEvidenceReceipt:
            raise TypeError("SanitizedLookupResult.evidence_receipt must be exact")
        if self.evidence_receipt.request_hash != self.request_hash:
            raise ValueError("lookup result request hash mismatch")
        if (
            self.status is SanitizedLookupStatus.UNCERTAIN
            and self.evidence_receipt.disposition is not ReadEvidenceDisposition.PRIVATE_ONLY
        ):
            raise ValueError("uncertain lookup evidence must be private_only")
        expected_content_hash = hashlib.sha256(
            ReadEvidenceReceipt.RESULT_CONTENT_DOMAIN.encode("ascii")
            + b"\x00"
            + self.content_preimage_bytes()
        ).hexdigest()
        if self.evidence_receipt.result_content_hash != expected_content_hash:
            raise ValueError("lookup result content hash mismatch")

    def _content_data(self) -> dict[str, object]:
        return {
            "request_hash": self.request_hash,
            "service": self.service.value,
            "status": self.status.value,
            "query_signature": self.query_signature,
            "lookup_id": self.lookup_id,
            "observed_at": self.observed_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "snapshot_hash": self.snapshot_hash,
            "offers": [
                json.loads(item.to_canonical_bytes().decode("utf-8"))
                for item in self.offers
            ],
            "failure_codes": [item.value for item in self.failure_codes],
        }

    def content_preimage_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.CONTENT_PREIMAGE_SCHEMA,
            version=self.VERSION,
            data=self._content_data(),
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data=self._content_data()
            | {
                "evidence_receipt": json.loads(
                    self.evidence_receipt.to_canonical_bytes().decode("utf-8")
                )
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "SanitizedLookupResult":
        envelope = _load_canonical_envelope(payload, "SanitizedLookupResult")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("SanitizedLookupResult envelope identity mismatch")
        data = envelope["data"]
        expected = {
            "request_hash",
            "service",
            "status",
            "query_signature",
            "lookup_id",
            "observed_at",
            "expires_at",
            "snapshot_hash",
            "offers",
            "failure_codes",
            "evidence_receipt",
        }
        if (
            set(data) != expected
            or type(data["service"]) is not str
            or type(data["status"]) is not str
            or type(data["offers"]) is not list
            or type(data["failure_codes"]) is not list
        ):
            raise ValueError("SanitizedLookupResult fields mismatch")
        try:
            service = ReadService(data["service"])
            status = SanitizedLookupStatus(data["status"])
            failure_codes = tuple(LookupFailureCode(item) for item in data["failure_codes"])
        except (TypeError, ValueError) as exc:
            raise ValueError("SanitizedLookupResult enum value is invalid") from exc
        result = cls(
            request_hash=data["request_hash"],
            service=service,
            status=status,
            query_signature=data["query_signature"],
            lookup_id=data["lookup_id"],
            observed_at=_parse_utc(data["observed_at"], "observed_at"),
            expires_at=_parse_utc(data["expires_at"], "expires_at"),
            snapshot_hash=data["snapshot_hash"],
            offers=tuple(
                SanitizedOffer.from_canonical_bytes(
                    _nested_contract_bytes(item, "offer")
                )
                for item in data["offers"]
            ),
            failure_codes=failure_codes,
            evidence_receipt=ReadEvidenceReceipt.from_canonical_bytes(
                _nested_contract_bytes(data["evidence_receipt"], "evidence_receipt")
            ),
        )
        if result.to_canonical_bytes() != payload:
            raise ValueError("SanitizedLookupResult is not byte-canonical")
        return result


def _decode_typed_fact(payload: bytes) -> TypedFact:
    envelope = _load_canonical_envelope(payload, "TypedFact")
    if envelope["schema"] != TypedFact.SCHEMA or envelope["version"] != TypedFact.VERSION:
        raise ValueError("TypedFact envelope identity mismatch")
    data = envelope["data"]
    if set(data) != {"name", "value", "frame_commitment_hash"}:
        raise ValueError("TypedFact fields mismatch")
    tagged = data["value"]
    if type(tagged) is not dict or set(tagged) != {"kind", "value"}:
        raise ValueError("TypedFact value envelope mismatch")
    kind = tagged["kind"]
    if kind == "string":
        slot = StringSlot(tagged["value"])
    elif kind == "integer":
        slot = IntegerSlot(tagged["value"])
    elif kind == "date":
        slot = DateSlot(_parse_date(tagged["value"], "TypedFact.value"))
    else:
        raise ValueError("TypedFact value kind is invalid")
    fact = TypedFact(
        name=data["name"],
        value=slot,
        frame_commitment_hash=data["frame_commitment_hash"],
    )
    if fact.to_canonical_bytes() != payload:
        raise ValueError("TypedFact is not byte-canonical")
    return fact


def _decode_execution_projection(payload: bytes) -> ReservationExecutionProjection:
    envelope = _load_canonical_envelope(payload, "ReservationExecutionProjection")
    if (
        envelope["schema"] != ReservationExecutionProjection.SCHEMA
        or envelope["version"] != ReservationExecutionProjection.VERSION
    ):
        raise ValueError("ReservationExecutionProjection identity mismatch")
    data = envelope["data"]
    if set(data) != {
        "reservation_relay_bundle_bytes",
        "reservation_relay_bundle_hash",
    }:
        raise ValueError("ReservationExecutionProjection fields mismatch")
    projection = ReservationExecutionProjection(
        reservation_relay_bundle_bytes=_decode_base64(
            data["reservation_relay_bundle_bytes"],
            "reservation_relay_bundle_bytes",
        ),
        reservation_relay_bundle_hash=data["reservation_relay_bundle_hash"],
    )
    if projection.to_canonical_bytes() != payload:
        raise ValueError("ReservationExecutionProjection is not byte-canonical")
    return projection


def _decode_conversation_projection(payload: bytes) -> ConversationProjection:
    envelope = _load_canonical_envelope(payload, "ConversationProjection")
    if (
        envelope["schema"] != ConversationProjection.SCHEMA
        or envelope["version"] != ConversationProjection.VERSION
    ):
        raise ValueError("ConversationProjection identity mismatch")
    data = envelope["data"]
    if set(data) != {
        "stage",
        "desired_services",
        "locale",
        "facts",
        "reservation_execution_projection",
    }:
        raise ValueError("ConversationProjection fields mismatch")
    if type(data["desired_services"]) is not list or type(data["facts"]) is not list:
        raise ValueError("ConversationProjection tuple fields must be arrays")
    try:
        stage = ConversationStage(data["stage"])
        desired_services = tuple(DesiredService(item) for item in data["desired_services"])
    except (TypeError, ValueError) as exc:
        raise ValueError("ConversationProjection enum value is invalid") from exc
    execution_value = data["reservation_execution_projection"]
    projection = ConversationProjection(
        stage=stage,
        desired_services=desired_services,
        locale=data["locale"],
        facts=tuple(
            _decode_typed_fact(_nested_contract_bytes(item, "fact"))
            for item in data["facts"]
        ),
        reservation_execution_projection=(
            _decode_execution_projection(
                _nested_contract_bytes(execution_value, "reservation_execution_projection")
            )
            if execution_value is not None
            else None
        ),
    )
    if projection.to_canonical_bytes() != payload:
        raise ValueError("ConversationProjection is not byte-canonical")
    return projection


@dataclass(frozen=True, slots=True)
class FoundSnapshot:
    genesis_receipt: LegacyGenesisReceipt
    projection: ConversationProjection

    SCHEMA: ClassVar[str] = "phase8-found-snapshot"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-found-snapshot-v1"

    def __post_init__(self) -> None:
        if type(self.genesis_receipt) is not LegacyGenesisReceipt:
            raise TypeError("FoundSnapshot.genesis_receipt must be exact")
        if self.genesis_receipt.status is not GenesisStatus.FOUND:
            raise ValueError("FoundSnapshot requires a found genesis receipt")
        if type(self.projection) is not ConversationProjection:
            raise TypeError("FoundSnapshot.projection must be exact")
        if self.projection.canonical_hash() != self.genesis_receipt.projection_hash:
            raise ValueError("FoundSnapshot projection hash mismatch")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "genesis_receipt": json.loads(
                    self.genesis_receipt.to_canonical_bytes().decode("utf-8")
                ),
                "projection": json.loads(
                    self.projection.to_canonical_bytes().decode("utf-8")
                ),
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "FoundSnapshot":
        envelope = _load_canonical_envelope(payload, "FoundSnapshot")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("FoundSnapshot identity mismatch")
        data = envelope["data"]
        if set(data) != {"genesis_receipt", "projection"}:
            raise ValueError("FoundSnapshot fields mismatch")
        result = cls(
            genesis_receipt=LegacyGenesisReceipt.from_canonical_bytes(
                _nested_contract_bytes(data["genesis_receipt"], "genesis_receipt")
            ),
            projection=_decode_conversation_projection(
                _nested_contract_bytes(data["projection"], "projection")
            ),
        )
        if result.to_canonical_bytes() != payload:
            raise ValueError("FoundSnapshot is not byte-canonical")
        return result


@dataclass(frozen=True, slots=True)
class ProvenAbsent:
    genesis_receipt: LegacyGenesisReceipt

    SCHEMA: ClassVar[str] = "phase8-proven-absent"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-proven-absent-v1"

    def __post_init__(self) -> None:
        if type(self.genesis_receipt) is not LegacyGenesisReceipt:
            raise TypeError("ProvenAbsent.genesis_receipt must be exact")
        if self.genesis_receipt.status is not GenesisStatus.PROVEN_ABSENT:
            raise ValueError("ProvenAbsent requires a proven_absent genesis receipt")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "genesis_receipt": json.loads(
                    self.genesis_receipt.to_canonical_bytes().decode("utf-8")
                )
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "ProvenAbsent":
        return _decode_single_genesis_result(payload, cls, GenesisStatus.PROVEN_ABSENT)


@dataclass(frozen=True, slots=True)
class LegacyUnavailable:
    genesis_receipt: LegacyGenesisReceipt

    SCHEMA: ClassVar[str] = "phase8-legacy-unavailable"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-legacy-unavailable-v1"

    def __post_init__(self) -> None:
        if type(self.genesis_receipt) is not LegacyGenesisReceipt:
            raise TypeError("LegacyUnavailable.genesis_receipt must be exact")
        if self.genesis_receipt.status is not GenesisStatus.UNAVAILABLE:
            raise ValueError("LegacyUnavailable requires an unavailable genesis receipt")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "genesis_receipt": json.loads(
                    self.genesis_receipt.to_canonical_bytes().decode("utf-8")
                )
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "LegacyUnavailable":
        return _decode_single_genesis_result(payload, cls, GenesisStatus.UNAVAILABLE)


def _decode_single_genesis_result(
    payload: bytes,
    result_type: type[ProvenAbsent] | type[LegacyUnavailable],
    expected_status: GenesisStatus,
) -> ProvenAbsent | LegacyUnavailable:
    envelope = _load_canonical_envelope(payload, result_type.__name__)
    if envelope["schema"] != result_type.SCHEMA or envelope["version"] != result_type.VERSION:
        raise ValueError(f"{result_type.__name__} identity mismatch")
    data = envelope["data"]
    if set(data) != {"genesis_receipt"}:
        raise ValueError(f"{result_type.__name__} fields mismatch")
    receipt = LegacyGenesisReceipt.from_canonical_bytes(
        _nested_contract_bytes(data["genesis_receipt"], "genesis_receipt")
    )
    if receipt.status is not expected_status:
        raise ValueError(f"{result_type.__name__} receipt status mismatch")
    result = result_type(receipt)
    if result.to_canonical_bytes() != payload:
        raise ValueError(f"{result_type.__name__} is not byte-canonical")
    return result


Phase8ReadRequest: TypeAlias = Phase8ToolReadRequest | LegacyGenesisReadRequest
SanitizedReadResult: TypeAlias = (
    FoundSnapshot
    | ProvenAbsent
    | LegacyUnavailable
    | SanitizedKnowledgeResult
    | SanitizedLookupResult
)


def _decode_source_event(value: object) -> SourceEventIdentity:
    payload = _nested_contract_bytes(value, "source_event")
    envelope = _load_canonical_envelope(payload, "SourceEventIdentity")
    if (
        envelope["schema"] != SourceEventIdentity.SCHEMA
        or envelope["version"] != SourceEventIdentity.VERSION
        or set(envelope["data"]) != {"source_event_id", "source_event_hash"}
    ):
        raise ValueError("SourceEventIdentity contract mismatch")
    event = SourceEventIdentity(
        source_event_id=envelope["data"]["source_event_id"],
        source_event_hash=envelope["data"]["source_event_hash"],
    )
    if event.to_canonical_bytes() != payload:
        raise ValueError("SourceEventIdentity is not byte-canonical")
    return event


def _decode_read_arguments(value: object, tool_name: object) -> ReadArguments:
    if type(value) is not dict or set(value) != {"type", "data"}:
        raise ValueError("read arguments tagged value mismatch")
    tag = value["type"]
    data = value["data"]
    if type(tag) is not str or type(data) is not dict:
        raise ValueError("read arguments tagged value has wrong exact types")
    expected = _TOOL_ARGUMENT_TYPES.get(tool_name)
    if expected is None or tag != expected.__name__:
        raise ValueError("read arguments tag/tool pair mismatch")
    if expected is FaqReadArguments:
        if set(data) != {"query", "locale"}:
            raise ValueError("FaqReadArguments fields mismatch")
        return FaqReadArguments(query=data["query"], locale=data["locale"])
    if expected is LodgingReadArguments:
        if set(data) != {"check_in", "check_out", "adults", "children"}:
            raise ValueError("LodgingReadArguments fields mismatch")
        return LodgingReadArguments(
            check_in=_parse_date(data["check_in"], "check_in"),
            check_out=_parse_date(data["check_out"], "check_out"),
            adults=data["adults"],
            children=data["children"],
        )
    if expected is RoomDescriptionArguments:
        if set(data) != {"room_offer_id"}:
            raise ValueError("RoomDescriptionArguments fields mismatch")
        return RoomDescriptionArguments(room_offer_id=data["room_offer_id"])
    if expected is ActivityReadArguments:
        if set(data) != {"activity_id", "activity_date", "participants"}:
            raise ValueError("ActivityReadArguments fields mismatch")
        return ActivityReadArguments(
            activity_id=data["activity_id"],
            activity_date=_parse_date(data["activity_date"], "activity_date"),
            participants=data["participants"],
        )
    if expected is ActivityDescriptionArguments:
        if set(data) != {"activity_id"}:
            raise ValueError("ActivityDescriptionArguments fields mismatch")
        return ActivityDescriptionArguments(activity_id=data["activity_id"])
    raise ValueError("unsupported read arguments")


def _decode_read_request(payload: bytes) -> Phase8ReadRequest:
    envelope = _load_canonical_envelope(payload, "Phase8ReadRequest")
    data = envelope["data"]
    if envelope["schema"] == Phase8ToolReadRequest.SCHEMA:
        if envelope["version"] != Phase8ToolReadRequest.VERSION or set(data) != {
            "tool_name",
            "arguments",
            "lead_key_hash",
            "aggregate_turn_id",
            "source_event",
            "deadline_at",
            "locale",
            "projection_hash",
        }:
            raise ValueError("Phase8ToolReadRequest fields mismatch")
        request: Phase8ReadRequest = Phase8ToolReadRequest(
            tool_name=data["tool_name"],
            arguments=_decode_read_arguments(data["arguments"], data["tool_name"]),
            lead_key_hash=data["lead_key_hash"],
            aggregate_turn_id=data["aggregate_turn_id"],
            source_event=_decode_source_event(data["source_event"]),
            deadline_at=_parse_utc(data["deadline_at"], "deadline_at"),
            locale=data["locale"],
            projection_hash=data["projection_hash"],
        )
    elif envelope["schema"] == LegacyGenesisReadRequest.SCHEMA:
        if envelope["version"] != LegacyGenesisReadRequest.VERSION or set(data) != {
            "lead_key_hash",
            "aggregate_turn_id",
            "source_event",
            "deadline_at",
            "legacy_source",
        }:
            raise ValueError("LegacyGenesisReadRequest fields mismatch")
        request = LegacyGenesisReadRequest(
            lead_key_hash=data["lead_key_hash"],
            aggregate_turn_id=data["aggregate_turn_id"],
            source_event=_decode_source_event(data["source_event"]),
            deadline_at=_parse_utc(data["deadline_at"], "deadline_at"),
            legacy_source=data["legacy_source"],
        )
    else:
        raise ValueError("request schema is outside Phase8ReadRequest")
    if request.to_canonical_bytes() != payload:
        raise ValueError("Phase8ReadRequest is not byte-canonical")
    return request


def _decode_sanitized_result(payload: bytes) -> SanitizedReadResult:
    envelope = _load_canonical_envelope(payload, "SanitizedReadResult")
    result_types: dict[str, type[object]] = {
        FoundSnapshot.SCHEMA: FoundSnapshot,
        ProvenAbsent.SCHEMA: ProvenAbsent,
        LegacyUnavailable.SCHEMA: LegacyUnavailable,
        SanitizedKnowledgeResult.SCHEMA: SanitizedKnowledgeResult,
        SanitizedLookupResult.SCHEMA: SanitizedLookupResult,
    }
    result_type = result_types.get(envelope["schema"])
    if result_type is None:
        raise ValueError("result schema is outside SanitizedReadResult")
    result = result_type.from_canonical_bytes(payload)
    if not isinstance(
        result,
        (
            FoundSnapshot,
            ProvenAbsent,
            LegacyUnavailable,
            SanitizedKnowledgeResult,
            SanitizedLookupResult,
        ),
    ):
        raise TypeError("decoded read result has an impossible type")
    return result


def _validate_request_result_equality(
    request: Phase8ReadRequest,
    result: SanitizedReadResult,
) -> None:
    if type(request) is LegacyGenesisReadRequest:
        if type(result) not in {FoundSnapshot, ProvenAbsent, LegacyUnavailable}:
            raise ValueError("genesis request requires a genesis result")
        if (
            result.genesis_receipt.request_hash != request.read_request_hash()
            or result.genesis_receipt.lead_key_hash != request.lead_key_hash
        ):
            raise ValueError("genesis request/result binding mismatch")
        return
    if type(request) is not Phase8ToolReadRequest:
        raise TypeError("request must be an exact Phase8ReadRequest")
    if type(result) is SanitizedKnowledgeResult:
        expected = {
            "cerebro_consultar": (KnowledgeSource.FAQ, None),
            "cloudbeds_descrever_quartos": (
                KnowledgeSource.LODGING_DESCRIPTION,
                request.arguments.room_offer_id
                if type(request.arguments) is RoomDescriptionArguments
                else None,
            ),
            "bokun_consultar_descricao": (
                KnowledgeSource.ACTIVITY_DESCRIPTION,
                request.arguments.activity_id
                if type(request.arguments) is ActivityDescriptionArguments
                else None,
            ),
        }.get(request.tool_name)
        if (
            expected is None
            or result.source is not expected[0]
            or result.subject_id != expected[1]
            or result.locale != request.locale
        ):
            raise ValueError("knowledge request/result equality mismatch")
        return
    if type(result) is not SanitizedLookupResult:
        raise ValueError("tool request requires its exact sanitized result class")
    if request.tool_name == "cloudbeds_consultar_hospedagem_v2" and type(
        request.arguments
    ) is LodgingReadArguments:
        args = request.arguments
        service = ReadService.LODGING
        query = SearchQuery(
            service=ServiceKind.LODGING,
            start_date=args.check_in,
            end_date=args.check_out,
            start_time=None,
            party=Party(args.adults, args.children),
        )
        offers_match = all(
            offer.start_date == args.check_in
            and offer.end_date == args.check_out
            and offer.start_time is None
            and offer.adults == args.adults
            and offer.children == args.children
            for offer in result.offers
        )
    elif request.tool_name == "bokun_consultar_passeio_v2" and type(
        request.arguments
    ) is ActivityReadArguments:
        args = request.arguments
        service = ReadService.ACTIVITY
        query = SearchQuery(
            service=ServiceKind.ACTIVITY,
            start_date=args.activity_date,
            end_date=None,
            start_time=None,
            party=Party(args.participants, 0),
        )
        offers_match = all(
            offer.start_date == args.activity_date
            and offer.end_date is None
            and offer.start_time is None
            and offer.adults == args.participants
            and offer.children == 0
            for offer in result.offers
        )
    else:
        raise ValueError("lookup result does not match the request tool")
    if (
        result.service is not service
        or result.query_signature != query.signature
        or not offers_match
    ):
        raise ValueError("lookup request/result equality mismatch")


class ReadObservationStatus(str, Enum):
    FOUND_SNAPSHOT = "found_snapshot"
    PROVEN_ABSENT = "proven_absent"
    LEGACY_UNAVAILABLE = "legacy_unavailable"
    ANSWERED = "answered"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ReadObservation:
    request_bytes: bytes
    request_hash: str
    status: ReadObservationStatus
    typed_result_bytes: bytes
    result_hash: str
    derived_facts: tuple[TypedFact, ...]
    safe_for_public_claims: bool
    frame_commitment_hash: str

    SCHEMA: ClassVar[str] = "phase8-read-observation"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-read-observation-v1"

    def __post_init__(self) -> None:
        if type(self.request_bytes) is not bytes or not self.request_bytes:
            raise TypeError("ReadObservation.request_bytes must be non-empty exact bytes")
        if type(self.typed_result_bytes) is not bytes or not self.typed_result_bytes:
            raise TypeError("ReadObservation.typed_result_bytes must be non-empty exact bytes")
        request = _decode_read_request(self.request_bytes)
        result = _decode_sanitized_result(self.typed_result_bytes)
        _require_sha256(self.request_hash, "ReadObservation.request_hash")
        _require_sha256(self.result_hash, "ReadObservation.result_hash")
        if request.read_request_hash() != self.request_hash:
            raise ValueError("ReadObservation.request_hash mismatch")
        if result.canonical_hash() != self.result_hash:
            raise ValueError("ReadObservation.result_hash mismatch")
        _validate_request_result_equality(request, result)
        if type(self.status) is not ReadObservationStatus:
            raise TypeError("ReadObservation.status must be exact")
        if type(self.safe_for_public_claims) is not bool:
            raise TypeError("safe_for_public_claims must be an exact bool")
        if type(result) is FoundSnapshot:
            expected_status, expected_safe = ReadObservationStatus.FOUND_SNAPSHOT, False
        elif type(result) is ProvenAbsent:
            expected_status, expected_safe = ReadObservationStatus.PROVEN_ABSENT, False
        elif type(result) is LegacyUnavailable:
            expected_status, expected_safe = ReadObservationStatus.LEGACY_UNAVAILABLE, False
        elif type(result) is SanitizedKnowledgeResult:
            expected_status = ReadObservationStatus.ANSWERED
            expected_safe = (
                result.evidence_receipt.disposition is ReadEvidenceDisposition.PUBLIC_SAFE
            )
        else:
            expected_status = {
                SanitizedLookupStatus.POSITIVE: ReadObservationStatus.POSITIVE,
                SanitizedLookupStatus.NEGATIVE: ReadObservationStatus.NEGATIVE,
                SanitizedLookupStatus.UNCERTAIN: ReadObservationStatus.UNCERTAIN,
            }[result.status]
            expected_safe = (
                result.status is not SanitizedLookupStatus.UNCERTAIN
                and result.evidence_receipt.disposition is ReadEvidenceDisposition.PUBLIC_SAFE
            )
        if self.status is not expected_status or self.safe_for_public_claims is not expected_safe:
            raise ValueError("ReadObservation status/public-safety matrix mismatch")
        _require_sha256(
            self.frame_commitment_hash,
            "ReadObservation.frame_commitment_hash",
        )
        if type(self.derived_facts) is not tuple or any(
            type(item) is not TypedFact for item in self.derived_facts
        ):
            raise TypeError("derived_facts must be an exact TypedFact tuple")
        order = {
            "language": 0,
            "service": 1,
            "start_date": 2,
            "end_date": 3,
            "adults": 4,
            "children": 5,
        }
        positions = tuple(order[item.name] for item in self.derived_facts)
        if len(set(positions)) != len(positions) or positions != tuple(sorted(positions)):
            raise ValueError("derived_facts must be unique and catalog-ordered")
        if any(
            item.frame_commitment_hash != self.frame_commitment_hash
            for item in self.derived_facts
        ):
            raise ValueError("derived fact frame commitment mismatch")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "request_bytes": base64.b64encode(self.request_bytes).decode("ascii"),
                "request_hash": self.request_hash,
                "status": self.status.value,
                "typed_result_bytes": base64.b64encode(self.typed_result_bytes).decode(
                    "ascii"
                ),
                "result_hash": self.result_hash,
                "derived_facts": [
                    json.loads(item.to_canonical_bytes().decode("utf-8"))
                    for item in self.derived_facts
                ],
                "safe_for_public_claims": self.safe_for_public_claims,
                "frame_commitment_hash": self.frame_commitment_hash,
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "ReadObservation":
        envelope = _load_canonical_envelope(payload, "ReadObservation")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("ReadObservation identity mismatch")
        data = envelope["data"]
        expected = {
            "request_bytes",
            "request_hash",
            "status",
            "typed_result_bytes",
            "result_hash",
            "derived_facts",
            "safe_for_public_claims",
            "frame_commitment_hash",
        }
        if set(data) != expected or type(data["derived_facts"]) is not list:
            raise ValueError("ReadObservation fields mismatch")
        try:
            status = ReadObservationStatus(data["status"])
        except (TypeError, ValueError) as exc:
            raise ValueError("ReadObservation status is invalid") from exc
        observation = cls(
            request_bytes=_decode_base64(data["request_bytes"], "request_bytes"),
            request_hash=data["request_hash"],
            status=status,
            typed_result_bytes=_decode_base64(
                data["typed_result_bytes"],
                "typed_result_bytes",
            ),
            result_hash=data["result_hash"],
            derived_facts=tuple(
                _decode_typed_fact(_nested_contract_bytes(item, "derived_fact"))
                for item in data["derived_facts"]
            ),
            safe_for_public_claims=data["safe_for_public_claims"],
            frame_commitment_hash=data["frame_commitment_hash"],
        )
        if observation.to_canonical_bytes() != payload:
            raise ValueError("ReadObservation is not byte-canonical")
        return observation


__all__ = (
    "GenesisStatus",
    "FoundSnapshot",
    "KnowledgeSource",
    "LookupFailureCode",
    "LegacyGenesisEvidenceRecord",
    "LegacyGenesisReadRequest",
    "LegacyGenesisReceipt",
    "LegacyUnavailable",
    "LegacyUnavailableReason",
    "PUBLIC_READ_POLICY_BYTES",
    "PUBLIC_READ_POLICY_DOMAIN",
    "PUBLIC_READ_POLICY_HASH",
    "PUBLIC_READ_POLICY_ID",
    "Phase8ReadRequest",
    "Phase8ToolReadRequest",
    "ProvenAbsent",
    "READ_REQUEST_DOMAIN",
    "ReadEvidenceDisposition",
    "ReadEvidenceReceipt",
    "ReadObservation",
    "ReadObservationStatus",
    "ReadArguments",
    "ReadService",
    "SanitizedKnowledgeResult",
    "SanitizedLookupResult",
    "SanitizedLookupStatus",
    "SanitizedOffer",
    "SanitizedReadResult",
    "validate_public_text",
)
