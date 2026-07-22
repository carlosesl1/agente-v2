"""Closed typed read-request contracts for the Phase 8 boundary."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
import re
import unicodedata
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
_GENESIS_ID_RE: Final = re.compile(r"^genesis:[0-9a-f]{64}$")
_READ_EVIDENCE_ID_RE: Final = re.compile(r"^read-evidence:[0-9a-f]{64}$")
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


Phase8ReadRequest: TypeAlias = Phase8ToolReadRequest | LegacyGenesisReadRequest


__all__ = (
    "GenesisStatus",
    "KnowledgeSource",
    "LegacyGenesisEvidenceRecord",
    "LegacyGenesisReadRequest",
    "LegacyGenesisReceipt",
    "LegacyUnavailableReason",
    "PUBLIC_READ_POLICY_BYTES",
    "PUBLIC_READ_POLICY_DOMAIN",
    "PUBLIC_READ_POLICY_HASH",
    "PUBLIC_READ_POLICY_ID",
    "Phase8ReadRequest",
    "Phase8ToolReadRequest",
    "READ_REQUEST_DOMAIN",
    "ReadEvidenceDisposition",
    "ReadEvidenceReceipt",
    "ReadArguments",
    "SanitizedKnowledgeResult",
    "validate_public_text",
)
