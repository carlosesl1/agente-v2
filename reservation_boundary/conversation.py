"""Closed immutable conversation contracts for the Phase 8 boundary."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
import re
from typing import TYPE_CHECKING, ClassVar, Final

from reservation_boundary.effects import ReservationRelayBundle
from reservation_boundary.serialization import from_tool_arguments_canonical_json
from reservation_boundary.types import (
    ActivityPaymentArguments,
    ActivityReservationArguments,
    ConversationIntentKind,
    LodgingPaymentArguments,
    LodgingReservationArguments,
    NormalizedMessage,
    TypedFact,
)

if TYPE_CHECKING:
    from reservation_boundary.reads import ReadObservation


_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_ID_TOKEN_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_LOCALE_RE: Final = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")


def _require_identifier(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must use the closed identifier alphabet")
    return value


def _require_id_token(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if _ID_TOKEN_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must use the Task 1 ID_TOKEN alphabet")
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


def _require_exact_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be an exact boolean")
    return value


def _require_text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if not allow_empty and not value:
        raise ValueError(f"{name} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value):
        raise ValueError(f"{name} contains a forbidden control character")
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


def _load_canonical_object(payload: bytes, name: str) -> dict[str, object]:
    _require_exact_bytes(payload, name)
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be canonical UTF-8 JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{name} must decode to an object")
    if _canonical_envelope(
        schema=value.get("schema"),
        version=value.get("version"),
        data=value.get("data"),
    ) != payload:
        raise ValueError(f"{name} must use exact canonical JSON")
    return value


def _load_contract_data(
    payload: bytes,
    name: str,
    *,
    schema: str,
    version: int,
    fields: frozenset[str],
) -> dict[str, object]:
    envelope = _load_canonical_object(payload, name)
    if set(envelope) != {"schema", "version", "data"}:
        raise ValueError(f"{name} envelope fields mismatch")
    if (
        type(envelope["schema"]) is not str
        or type(envelope["version"]) is not int
        or envelope["schema"] != schema
        or envelope["version"] != version
    ):
        raise ValueError(f"{name} envelope identity mismatch")
    data = envelope["data"]
    if type(data) is not dict or set(data) != fields:
        raise ValueError(f"{name} data fields mismatch")
    return data


def _nested_contract_bytes(value: object, name: str) -> bytes:
    if type(value) is not dict:
        raise ValueError(f"{name} must be a nested contract object")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _parse_utc(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{name} must be an exact datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical UTC datetime") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat() != value
    ):
        raise ValueError(f"{name} must be a canonical UTC datetime")
    return parsed


def _decode_base64(value: object, name: str) -> bytes:
    if type(value) is not str:
        raise ValueError(f"{name} must be a Base64 string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must use standard Base64") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{name} must use canonical standard Base64")
    return decoded


def _decode_base64_tuple(value: object, name: str) -> tuple[bytes, ...]:
    if type(value) is not list:
        raise ValueError(f"{name} must be a JSON array")
    return tuple(_decode_base64(item, f"{name} item") for item in value)


def _decode_reservation_relay_bundle(payload: bytes) -> ReservationRelayBundle:
    envelope = _load_canonical_object(payload, "reservation_relay_bundle_bytes")
    if set(envelope) != {"schema", "version", "data"}:
        raise ValueError("reservation relay envelope fields mismatch")
    if (
        envelope["schema"] != ReservationRelayBundle.SCHEMA
        or envelope["version"] != ReservationRelayBundle.VERSION
        or type(envelope["data"]) is not dict
    ):
        raise ValueError("reservation relay envelope identity mismatch")
    data = envelope["data"]
    expected = {
        "genesis_state",
        "phase5_events",
        "summary_outboxes",
        "expected_final_state",
        "expected_final_state_hash",
        "command_ledger_seed",
        "qualification_id",
        "scenario_id",
        "immutable_generation",
        "allocation_id",
        "artifact_hash",
    }
    if set(data) != expected:
        raise ValueError("reservation relay data fields mismatch")
    bundle = ReservationRelayBundle(
        genesis_state=_decode_base64(data["genesis_state"], "genesis_state"),
        phase5_events=_decode_base64_tuple(data["phase5_events"], "phase5_events"),
        summary_outboxes=_decode_base64_tuple(
            data["summary_outboxes"],
            "summary_outboxes",
        ),
        expected_final_state=_decode_base64(
            data["expected_final_state"],
            "expected_final_state",
        ),
        expected_final_state_hash=data["expected_final_state_hash"],
        command_ledger_seed=_decode_base64(
            data["command_ledger_seed"],
            "command_ledger_seed",
        ),
        qualification_id=data["qualification_id"],
        scenario_id=data["scenario_id"],
        immutable_generation=data["immutable_generation"],
        allocation_id=data["allocation_id"],
        artifact_hash=data["artifact_hash"],
    )
    if bundle.to_canonical_bytes() != payload:
        raise ValueError("reservation relay bundle is not byte-canonical")
    return bundle


class ConversationStage(str, Enum):
    RECEPTIONIST = "recepcionista"
    HOSTEL = "hostel"
    AGENCY = "agencia"
    CLOSING = "fechamento"
    HANDOFF = "handoff"
    NO_REPLY = "no_reply"


class DesiredService(str, Enum):
    HOSTEL = "hostel"
    AGENCY = "agency"


@dataclass(frozen=True, slots=True)
class ReservationExecutionProjection:
    reservation_relay_bundle_bytes: bytes
    reservation_relay_bundle_hash: str

    SCHEMA: ClassVar[str] = "phase8-reservation-execution-projection"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-reservation-execution-projection-v1"
    BUNDLE_BINDING_DOMAIN: ClassVar[str] = (
        "phase8-reservation-execution-bundle-binding-v1"
    )

    def __post_init__(self) -> None:
        bundle_bytes = _require_exact_bytes(
            self.reservation_relay_bundle_bytes,
            "ReservationExecutionProjection.reservation_relay_bundle_bytes",
        )
        _decode_reservation_relay_bundle(bundle_bytes)
        bundle_hash = _require_sha256(
            self.reservation_relay_bundle_hash,
            "ReservationExecutionProjection.reservation_relay_bundle_hash",
        )
        expected = hashlib.sha256(
            self.BUNDLE_BINDING_DOMAIN.encode("ascii") + b"\x00" + bundle_bytes
        ).hexdigest()
        if bundle_hash != expected:
            raise ValueError("reservation relay bundle binding hash mismatch")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "reservation_relay_bundle_bytes": base64.b64encode(
                    self.reservation_relay_bundle_bytes
                ).decode("ascii"),
                "reservation_relay_bundle_hash": self.reservation_relay_bundle_hash,
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(
        cls,
        payload: bytes,
    ) -> "ReservationExecutionProjection":
        data = _load_contract_data(
            payload,
            "ReservationExecutionProjection",
            schema=cls.SCHEMA,
            version=cls.VERSION,
            fields=frozenset(
                (
                    "reservation_relay_bundle_bytes",
                    "reservation_relay_bundle_hash",
                )
            ),
        )
        projection = cls(
            reservation_relay_bundle_bytes=_decode_base64(
                data["reservation_relay_bundle_bytes"],
                "reservation_relay_bundle_bytes",
            ),
            reservation_relay_bundle_hash=data["reservation_relay_bundle_hash"],
        )
        if projection.to_canonical_bytes() != payload:
            raise ValueError("ReservationExecutionProjection is not byte-canonical")
        return projection


@dataclass(frozen=True, slots=True)
class ConversationProjection:
    stage: ConversationStage
    desired_services: tuple[DesiredService, ...]
    locale: str
    facts: tuple[TypedFact, ...]
    reservation_execution_projection: ReservationExecutionProjection | None

    SCHEMA: ClassVar[str] = "phase8-conversation-projection"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-conversation-projection-v1"

    def __post_init__(self) -> None:
        if type(self.stage) is not ConversationStage:
            raise TypeError("ConversationProjection.stage must be exact")
        if type(self.desired_services) is not tuple:
            raise TypeError("ConversationProjection.desired_services must be an exact tuple")
        if any(type(item) is not DesiredService for item in self.desired_services):
            raise TypeError("ConversationProjection.desired_services members must be exact")
        service_order = {DesiredService.HOSTEL: 0, DesiredService.AGENCY: 1}
        positions = tuple(service_order[item] for item in self.desired_services)
        if len(positions) != len(set(positions)) or positions != tuple(sorted(positions)):
            raise ValueError("ConversationProjection.desired_services must be unique and ordered")
        if type(self.locale) is not str or _LOCALE_RE.fullmatch(self.locale) is None:
            raise ValueError("ConversationProjection.locale must be canonical")
        if type(self.facts) is not tuple:
            raise TypeError("ConversationProjection.facts must be an exact tuple")
        fact_order = {
            "language": 0,
            "service": 1,
            "start_date": 2,
            "end_date": 3,
            "adults": 4,
            "children": 5,
            "payment_method": 6,
        }
        fact_positions: list[int] = []
        for fact in self.facts:
            if type(fact) is not TypedFact:
                raise TypeError("ConversationProjection.facts members must be exact TypedFact")
            if fact.frame_commitment_hash is None:
                raise ValueError("ConversationProjection rejects legacy TypedFact")
            fact_positions.append(fact_order[fact.name])
        if (
            len(fact_positions) != len(set(fact_positions))
            or tuple(fact_positions) != tuple(sorted(fact_positions))
        ):
            raise ValueError("ConversationProjection.facts must be unique and catalog-ordered")
        if (
            self.reservation_execution_projection is not None
            and type(self.reservation_execution_projection) is not ReservationExecutionProjection
        ):
            raise TypeError("reservation_execution_projection must be exact or null")

    def to_canonical_bytes(self) -> bytes:
        execution = self.reservation_execution_projection
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "stage": self.stage.value,
                "desired_services": [item.value for item in self.desired_services],
                "locale": self.locale,
                "facts": [
                    json.loads(fact.to_canonical_bytes().decode("utf-8"))
                    for fact in self.facts
                ],
                "reservation_execution_projection": (
                    json.loads(execution.to_canonical_bytes().decode("utf-8"))
                    if execution is not None
                    else None
                ),
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "ConversationProjection":
        data = _load_contract_data(
            payload,
            "ConversationProjection",
            schema=cls.SCHEMA,
            version=cls.VERSION,
            fields=frozenset(
                (
                    "stage",
                    "desired_services",
                    "locale",
                    "facts",
                    "reservation_execution_projection",
                )
            ),
        )
        if type(data["desired_services"]) is not list or type(data["facts"]) is not list:
            raise ValueError("ConversationProjection tuple fields must be arrays")
        try:
            stage = ConversationStage(data["stage"])
            desired_services = tuple(
                DesiredService(item) for item in data["desired_services"]
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("ConversationProjection enum value is invalid") from exc
        execution_value = data["reservation_execution_projection"]
        projection = cls(
            stage=stage,
            desired_services=desired_services,
            locale=data["locale"],
            facts=tuple(
                TypedFact.from_canonical_bytes(_nested_contract_bytes(item, "fact"))
                for item in data["facts"]
            ),
            reservation_execution_projection=(
                ReservationExecutionProjection.from_canonical_bytes(
                    _nested_contract_bytes(
                        execution_value,
                        "reservation_execution_projection",
                    )
                )
                if execution_value is not None
                else None
            ),
        )
        if projection.to_canonical_bytes() != payload:
            raise ValueError("ConversationProjection is not byte-canonical")
        return projection


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

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "SourceEventIdentity":
        data = _load_contract_data(
            payload,
            "SourceEventIdentity",
            schema=cls.SCHEMA,
            version=cls.VERSION,
            fields=frozenset(("source_event_id", "source_event_hash")),
        )
        identity = cls(
            source_event_id=data["source_event_id"],
            source_event_hash=data["source_event_hash"],
        )
        if identity.to_canonical_bytes() != payload:
            raise ValueError("SourceEventIdentity is not byte-canonical")
        return identity


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

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "MayaTurnRequest":
        data = _load_contract_data(
            payload,
            "MayaTurnRequest",
            schema=cls.SCHEMA,
            version=cls.VERSION,
            fields=frozenset(
                (
                    "boundary_state_bytes",
                    "state_version",
                    "state_hash",
                    "normalized_message",
                    "aggregate_turn_id",
                    "source_events",
                    "lead_key_hash",
                    "private_delivery_binding_hash",
                    "deadline_at",
                    "behavior_profile_fingerprint",
                )
            ),
        )
        normalized_message = data["normalized_message"]
        if (
            type(normalized_message) is not dict
            or set(normalized_message) != {"text", "locale"}
        ):
            raise ValueError("MayaTurnRequest.normalized_message fields mismatch")
        source_events = data["source_events"]
        if type(source_events) is not list:
            raise ValueError("MayaTurnRequest.source_events must be an array")
        request = cls(
            boundary_state_bytes=_decode_base64(
                data["boundary_state_bytes"],
                "boundary_state_bytes",
            ),
            state_version=data["state_version"],
            state_hash=data["state_hash"],
            normalized_message=NormalizedMessage(
                text=normalized_message["text"],
                locale=normalized_message["locale"],
            ),
            aggregate_turn_id=data["aggregate_turn_id"],
            source_events=tuple(
                SourceEventIdentity.from_canonical_bytes(
                    _nested_contract_bytes(item, "source_event")
                )
                for item in source_events
            ),
            lead_key_hash=data["lead_key_hash"],
            private_delivery_binding_hash=data["private_delivery_binding_hash"],
            deadline_at=_parse_utc(data["deadline_at"], "deadline_at"),
            behavior_profile_fingerprint=data["behavior_profile_fingerprint"],
        )
        if request.to_canonical_bytes() != payload:
            raise ValueError("MayaTurnRequest is not byte-canonical")
        return request


class PublicRoute(str, Enum):
    RECEPTIONIST = "recepcionista"
    HOSTEL = "hostel"
    AGENCY = "agencia"
    CLOSING = "fechamento"
    HANDOFF = "handoff"
    NO_REPLY = "no_reply"


class PublicReplyType(str, Enum):
    ASK_MORE = "ask_more"
    QUALIFY = "qualify"
    ANSWER = "answer"
    HANDOFF = "handoff"
    NO_REPLY = "no_reply"


@dataclass(frozen=True, slots=True)
class PublicReplyChunk:
    """Exact public bytes produced by the deterministic parent splitter/guard."""

    aggregate_turn_id: str
    ordinal: int
    text: str
    source_closure_hash: str

    SCHEMA: ClassVar[str] = "phase8-public-reply-chunk"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-public-reply-chunk-v1"

    def __post_init__(self) -> None:
        _require_id_token(self.aggregate_turn_id, "PublicReplyChunk.aggregate_turn_id")
        _require_exact_int(self.ordinal, "PublicReplyChunk.ordinal", minimum=0)
        # Keep one accepted public-text policy without creating a module import cycle.
        from reservation_boundary.reads import validate_public_text

        validate_public_text(self.text, limit=4096)
        _require_sha256(
            self.source_closure_hash,
            "PublicReplyChunk.source_closure_hash",
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "aggregate_turn_id": self.aggregate_turn_id,
                "ordinal": self.ordinal,
                "text": self.text,
                "source_closure_hash": self.source_closure_hash,
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()


class Capability(str, Enum):
    LEGACY_READ = "legacy_read"
    MAYA_INFERENCE = "maya_inference"
    PROVIDER_READ = "provider_read"
    TURN_COMMIT = "turn_commit"
    RELAY_ENQUEUE = "relay_enqueue"
    PROVIDER_WRITE = "provider_write"
    FOLLOWUP_DELIVERY = "followup_delivery"
    PUBLIC_DELIVERY = "public_delivery"
    LEARNING_WRITE = "learning_write"


class CapabilityDisposition(str, Enum):
    DENIED = "denied"
    READ_ONLY = "read_only"
    PROPOSE_ONLY = "propose_only"
    EXECUTE = "execute"


class Worker(str, Enum):
    TURN_COORDINATOR = "turn_coordinator"
    COMMAND_RELAY_WORKER = "command_relay_worker"
    INTERNAL_JOB_WORKER = "internal_job_worker"
    PROVIDER_EFFECT_WORKER = "provider_effect_worker"
    FOLLOWUP_DELIVERY_WORKER = "followup_delivery_worker"
    PUBLIC_DELIVERY_WORKER = "public_delivery_worker"
    LEARNING_WORKER = "learning_worker"
    RECONCILIATION_WORKER = "reconciliation_worker"
    QUALIFICATION_CONTROLLER = "qualification_controller"


class WorkerMode(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    ACTIVE = "active"


class GuardSemantic(str, Enum):
    FAIL_CLOSED = "fail_closed"
    DEADLINE_BOUNDED = "deadline_bounded"
    IDEMPOTENCY_REQUIRED = "idempotency_required"
    LEASE_FENCED = "lease_fenced"
    OWNER_CHECKED = "owner_checked"


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """Closed stage capability matrix without concrete roots or allowlists."""

    capability_matrix: tuple[tuple[Capability, CapabilityDisposition], ...]
    worker_modes: tuple[tuple[Worker, WorkerMode], ...]
    guard_semantics: tuple[GuardSemantic, ...]

    SCHEMA: ClassVar[str] = "phase8-capability-policy"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-capability-policy-v1"

    def __post_init__(self) -> None:
        if type(self.capability_matrix) is not tuple:
            raise TypeError("CapabilityPolicy.capability_matrix must be an exact tuple")
        expected_capabilities = tuple(Capability)
        if len(self.capability_matrix) != len(expected_capabilities):
            raise ValueError("CapabilityPolicy must contain every capability exactly once")
        for row, expected_capability in zip(
            self.capability_matrix,
            expected_capabilities,
            strict=True,
        ):
            if type(row) is not tuple or len(row) != 2:
                raise TypeError("CapabilityPolicy capability rows must be exact pairs")
            capability, disposition = row
            if type(capability) is not Capability or capability is not expected_capability:
                raise ValueError("CapabilityPolicy capabilities must use enum order")
            if type(disposition) is not CapabilityDisposition:
                raise TypeError("CapabilityPolicy dispositions must be exact")

        if type(self.worker_modes) is not tuple:
            raise TypeError("CapabilityPolicy.worker_modes must be an exact tuple")
        expected_workers = tuple(Worker)
        if len(self.worker_modes) != len(expected_workers):
            raise ValueError("CapabilityPolicy must contain every worker exactly once")
        for row, expected_worker in zip(
            self.worker_modes,
            expected_workers,
            strict=True,
        ):
            if type(row) is not tuple or len(row) != 2:
                raise TypeError("CapabilityPolicy worker rows must be exact pairs")
            worker, mode = row
            if type(worker) is not Worker or worker is not expected_worker:
                raise ValueError("CapabilityPolicy workers must use enum order")
            if type(mode) is not WorkerMode:
                raise TypeError("CapabilityPolicy worker modes must be exact")

        if type(self.guard_semantics) is not tuple:
            raise TypeError("CapabilityPolicy.guard_semantics must be an exact tuple")
        if self.guard_semantics != tuple(GuardSemantic) or any(
            type(item) is not GuardSemantic for item in self.guard_semantics
        ):
            raise ValueError(
                "CapabilityPolicy guard semantics must be complete and enum-ordered"
            )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "capability_matrix": [
                    [capability.value, disposition.value]
                    for capability, disposition in self.capability_matrix
                ],
                "worker_modes": [
                    [worker.value, mode.value] for worker, mode in self.worker_modes
                ],
                "guard_semantics": [item.value for item in self.guard_semantics],
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "CapabilityPolicy":
        data = _load_contract_data(
            payload,
            "CapabilityPolicy",
            schema=cls.SCHEMA,
            version=cls.VERSION,
            fields=frozenset(
                ("capability_matrix", "worker_modes", "guard_semantics")
            ),
        )
        capability_matrix = data["capability_matrix"]
        worker_modes = data["worker_modes"]
        guard_semantics = data["guard_semantics"]
        if (
            type(capability_matrix) is not list
            or type(worker_modes) is not list
            or type(guard_semantics) is not list
        ):
            raise ValueError("CapabilityPolicy tuple fields must be arrays")
        if any(type(row) is not list or len(row) != 2 for row in capability_matrix):
            raise ValueError("CapabilityPolicy capability rows must be pairs")
        if any(type(row) is not list or len(row) != 2 for row in worker_modes):
            raise ValueError("CapabilityPolicy worker rows must be pairs")
        try:
            capability_rows = tuple(
                (Capability(row[0]), CapabilityDisposition(row[1]))
                for row in capability_matrix
            )
            worker_rows = tuple(
                (Worker(row[0]), WorkerMode(row[1])) for row in worker_modes
            )
            guards = tuple(GuardSemantic(item) for item in guard_semantics)
        except (TypeError, ValueError) as exc:
            raise ValueError("CapabilityPolicy enum value is invalid") from exc
        policy = cls(
            capability_matrix=capability_rows,
            worker_modes=worker_rows,
            guard_semantics=guards,
        )
        if policy.to_canonical_bytes() != payload:
            raise ValueError("CapabilityPolicy is not byte-canonical")
        return policy


class NormalizedCommandTool(str, Enum):
    LODGING_RESERVATION = "cloudbeds_criar_reserva_v2"
    ACTIVITY_RESERVATION = "bokun_agendar_passeio_v2"
    LODGING_PAYMENT = "cloudbeds_lancar_pagamento_confirmar_reserva"
    ACTIVITY_PAYMENT = "bokun_lancar_pagamento_confirmar_reserva"


class NormalizedCommandArgumentsType(str, Enum):
    LODGING_RESERVATION = "lodging_reservation"
    ACTIVITY_RESERVATION = "activity_reservation"
    LODGING_PAYMENT = "lodging_payment"
    ACTIVITY_PAYMENT = "activity_payment"


_NORMALIZED_COMMAND_PAIRS: Final = {
    NormalizedCommandTool.LODGING_RESERVATION: (
        NormalizedCommandArgumentsType.LODGING_RESERVATION,
        LodgingReservationArguments,
    ),
    NormalizedCommandTool.ACTIVITY_RESERVATION: (
        NormalizedCommandArgumentsType.ACTIVITY_RESERVATION,
        ActivityReservationArguments,
    ),
    NormalizedCommandTool.LODGING_PAYMENT: (
        NormalizedCommandArgumentsType.LODGING_PAYMENT,
        LodgingPaymentArguments,
    ),
    NormalizedCommandTool.ACTIVITY_PAYMENT: (
        NormalizedCommandArgumentsType.ACTIVITY_PAYMENT,
        ActivityPaymentArguments,
    ),
}


@dataclass(frozen=True, slots=True)
class NormalizedToolProposal:
    """Parent-normalized command proposal without authorization or provider payload."""

    aggregate_turn_id: str
    request_id: str
    sequence: int
    tool_name: NormalizedCommandTool
    arguments_type: NormalizedCommandArgumentsType
    typed_arguments_json: bytes
    request_hash: str
    frame_commitment_hash: str

    SCHEMA: ClassVar[str] = "phase8-normalized-tool-proposal"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-normalized-tool-proposal-v1"

    def __post_init__(self) -> None:
        _require_id_token(
            self.aggregate_turn_id,
            "NormalizedToolProposal.aggregate_turn_id",
        )
        _require_id_token(self.request_id, "NormalizedToolProposal.request_id")
        _require_exact_int(self.sequence, "NormalizedToolProposal.sequence", minimum=1)
        if type(self.tool_name) is not NormalizedCommandTool:
            raise TypeError("NormalizedToolProposal.tool_name must be exact")
        if type(self.arguments_type) is not NormalizedCommandArgumentsType:
            raise TypeError("NormalizedToolProposal.arguments_type must be exact")
        expected_arguments_type, owner_type = _NORMALIZED_COMMAND_PAIRS[self.tool_name]
        if self.arguments_type is not expected_arguments_type:
            raise ValueError("NormalizedToolProposal tool/arguments pair mismatch")
        from_tool_arguments_canonical_json(self.typed_arguments_json, owner_type)
        _require_sha256(self.request_hash, "NormalizedToolProposal.request_hash")
        _require_sha256(
            self.frame_commitment_hash,
            "NormalizedToolProposal.frame_commitment_hash",
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "aggregate_turn_id": self.aggregate_turn_id,
                "request_id": self.request_id,
                "sequence": self.sequence,
                "tool_name": self.tool_name.value,
                "arguments_type": self.arguments_type.value,
                "typed_arguments_json": base64.b64encode(
                    self.typed_arguments_json
                ).decode("ascii"),
                "request_hash": self.request_hash,
                "frame_commitment_hash": self.frame_commitment_hash,
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class LearningProposal:
    """Parent-owned deferred learning proposal with an explicit memory CAS."""

    aggregate_turn_id: str
    request_id: str
    sequence: int
    claim: TypedFact
    expected_memory_version: int
    expected_memory_hash: str
    request_hash: str
    frame_commitment_hash: str

    SCHEMA: ClassVar[str] = "phase8-learning-proposal"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-learning-proposal-v1"

    def __post_init__(self) -> None:
        _require_id_token(self.aggregate_turn_id, "LearningProposal.aggregate_turn_id")
        _require_id_token(self.request_id, "LearningProposal.request_id")
        _require_exact_int(self.sequence, "LearningProposal.sequence", minimum=1)
        if type(self.claim) is not TypedFact:
            raise TypeError("LearningProposal.claim must be an exact TypedFact")
        if self.claim.frame_commitment_hash is None:
            raise ValueError("LearningProposal.claim must use the v8 TypedFact wire")
        _require_exact_int(
            self.expected_memory_version,
            "LearningProposal.expected_memory_version",
            minimum=0,
        )
        _require_sha256(
            self.expected_memory_hash,
            "LearningProposal.expected_memory_hash",
        )
        _require_sha256(self.request_hash, "LearningProposal.request_hash")
        frame_hash = _require_sha256(
            self.frame_commitment_hash,
            "LearningProposal.frame_commitment_hash",
        )
        if self.claim.frame_commitment_hash != frame_hash:
            raise ValueError("LearningProposal claim/frame backlink mismatch")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "aggregate_turn_id": self.aggregate_turn_id,
                "request_id": self.request_id,
                "sequence": self.sequence,
                "claim": json.loads(self.claim.to_canonical_bytes().decode("utf-8")),
                "expected_memory_version": self.expected_memory_version,
                "expected_memory_hash": self.expected_memory_hash,
                "request_hash": self.request_hash,
                "frame_commitment_hash": self.frame_commitment_hash,
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class MayaIntentClosure:
    """Child-owned intent closure with no facts, tools or commands."""

    kind: ConversationIntentKind
    selection: str | None
    confirmation: int | None
    handoff: bool

    SCHEMA: ClassVar[str] = "phase8-maya-intent-closure"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-maya-intent-closure-v1"

    def __post_init__(self) -> None:
        if type(self.kind) is not ConversationIntentKind:
            raise TypeError("MayaIntentClosure.kind must be exact")
        if self.kind is ConversationIntentKind.TOOL_REQUEST:
            raise ValueError("MayaIntentClosure cannot carry a tool-request intent")
        _require_exact_bool(self.handoff, "MayaIntentClosure.handoff")
        if self.kind is ConversationIntentKind.SELECT:
            _require_identifier(self.selection, "MayaIntentClosure.selection")
        elif self.selection is not None:
            raise ValueError("MayaIntentClosure.selection is allowed only for select")
        if self.kind is ConversationIntentKind.CONFIRM:
            _require_exact_int(
                self.confirmation,
                "MayaIntentClosure.confirmation",
                minimum=1,
            )
        elif self.confirmation is not None:
            raise ValueError("MayaIntentClosure.confirmation is allowed only for confirm")
        expects_handoff = self.kind is ConversationIntentKind.REQUEST_HANDOFF
        if self.handoff is not expects_handoff:
            raise ValueError("MayaIntentClosure.handoff must match request_handoff kind")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "kind": self.kind.value,
                "selection": self.selection,
                "confirmation": self.confirmation,
                "handoff": self.handoff,
            },
        )

    def canonical_hash(self) -> str:
        preimage = self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        return hashlib.sha256(preimage).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "MayaIntentClosure":
        data = _load_contract_data(
            payload,
            "MayaIntentClosure",
            schema=cls.SCHEMA,
            version=cls.VERSION,
            fields=frozenset(("kind", "selection", "confirmation", "handoff")),
        )
        try:
            kind = ConversationIntentKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError("MayaIntentClosure.kind is invalid") from exc
        closure = cls(
            kind=kind,
            selection=data["selection"],
            confirmation=data["confirmation"],
            handoff=data["handoff"],
        )
        if closure.to_canonical_bytes() != payload:
            raise ValueError("MayaIntentClosure is not byte-canonical")
        return closure


@dataclass(frozen=True, slots=True)
class MayaTurnClosure:
    """Terminal closure emitted by the isolated Maya child."""

    aggregate_turn_id: str
    intent_closure: MayaIntentClosure
    public_text: str
    route: PublicRoute
    reply_type: PublicReplyType
    final_seq: int
    expected_prefix_mac: str
    ephemeral_session_id: str
    zero_requests_in_flight: bool

    SCHEMA: ClassVar[str] = "phase8-maya-turn-closure"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-maya-turn-closure-v1"

    def __post_init__(self) -> None:
        _require_identifier(self.aggregate_turn_id, "MayaTurnClosure.aggregate_turn_id")
        if type(self.intent_closure) is not MayaIntentClosure:
            raise TypeError(
                "MayaTurnClosure.intent_closure must be an exact MayaIntentClosure"
            )
        if type(self.route) is not PublicRoute:
            raise TypeError("MayaTurnClosure.route must be an exact PublicRoute")
        if type(self.reply_type) is not PublicReplyType:
            raise TypeError(
                "MayaTurnClosure.reply_type must be an exact PublicReplyType"
            )
        is_handoff = self.route is PublicRoute.HANDOFF
        is_no_reply = self.route is PublicRoute.NO_REPLY
        if is_handoff != (self.reply_type is PublicReplyType.HANDOFF):
            raise ValueError("handoff route and reply type must agree")
        if is_no_reply != (self.reply_type is PublicReplyType.NO_REPLY):
            raise ValueError("no_reply route and reply type must agree")
        if not is_handoff and not is_no_reply and self.reply_type in {
            PublicReplyType.HANDOFF,
            PublicReplyType.NO_REPLY,
        }:
            raise ValueError("normal routes require a public reply type")
        intent_requests_handoff = (
            self.intent_closure.kind is ConversationIntentKind.REQUEST_HANDOFF
        )
        if intent_requests_handoff != is_handoff:
            raise ValueError("request_handoff intent and handoff route must agree")
        _require_text(
            self.public_text,
            "MayaTurnClosure.public_text",
            allow_empty=is_no_reply,
        )
        if is_no_reply and self.public_text:
            raise ValueError("no_reply closure must not contain public text")
        _require_exact_int(self.final_seq, "MayaTurnClosure.final_seq", minimum=1)
        _require_sha256(
            self.expected_prefix_mac,
            "MayaTurnClosure.expected_prefix_mac",
        )
        _require_identifier(
            self.ephemeral_session_id,
            "MayaTurnClosure.ephemeral_session_id",
        )
        if not _require_exact_bool(
            self.zero_requests_in_flight,
            "MayaTurnClosure.zero_requests_in_flight",
        ):
            raise ValueError("MayaTurnClosure must declare zero requests in flight")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "aggregate_turn_id": self.aggregate_turn_id,
                "intent_closure": json.loads(
                    self.intent_closure.to_canonical_bytes().decode("utf-8")
                ),
                "public_text": self.public_text,
                "route": self.route.value,
                "reply_type": self.reply_type.value,
                "final_seq": self.final_seq,
                "expected_prefix_mac": self.expected_prefix_mac,
                "ephemeral_session_id": self.ephemeral_session_id,
                "zero_requests_in_flight": self.zero_requests_in_flight,
            },
        )

    def canonical_hash(self) -> str:
        preimage = self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        return hashlib.sha256(preimage).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "MayaTurnClosure":
        data = _load_contract_data(
            payload,
            "MayaTurnClosure",
            schema=cls.SCHEMA,
            version=cls.VERSION,
            fields=frozenset(
                (
                    "aggregate_turn_id",
                    "intent_closure",
                    "public_text",
                    "route",
                    "reply_type",
                    "final_seq",
                    "expected_prefix_mac",
                    "ephemeral_session_id",
                    "zero_requests_in_flight",
                )
            ),
        )
        try:
            route = PublicRoute(data["route"])
            reply_type = PublicReplyType(data["reply_type"])
        except (TypeError, ValueError) as exc:
            raise ValueError("MayaTurnClosure enum value is invalid") from exc
        closure = cls(
            aggregate_turn_id=data["aggregate_turn_id"],
            intent_closure=MayaIntentClosure.from_canonical_bytes(
                _nested_contract_bytes(data["intent_closure"], "intent_closure")
            ),
            public_text=data["public_text"],
            route=route,
            reply_type=reply_type,
            final_seq=data["final_seq"],
            expected_prefix_mac=data["expected_prefix_mac"],
            ephemeral_session_id=data["ephemeral_session_id"],
            zero_requests_in_flight=data["zero_requests_in_flight"],
        )
        if closure.to_canonical_bytes() != payload:
            raise ValueError("MayaTurnClosure is not byte-canonical")
        return closure


@dataclass(frozen=True, slots=True)
class MayaTurnProposal:
    """Parent-owned aggregate built from an accepted child closure and transcript."""

    aggregate_turn_id: str
    intent_closure: MayaIntentClosure
    read_observations: tuple["ReadObservation", ...]
    facts: tuple[TypedFact, ...]
    normalized_tool_proposals: tuple[NormalizedToolProposal, ...]
    learning_proposals: tuple[LearningProposal, ...]
    public_reply_chunks: tuple[PublicReplyChunk, ...]
    maya_turn_closure_hash: str
    final_transcript_commitment_hash: str
    final_seq: int
    final_transcript_mac: str
    runtime_graph_digest: str
    route: PublicRoute
    reply_type: PublicReplyType

    SCHEMA: ClassVar[str] = "phase8-maya-turn-proposal"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-maya-turn-proposal-v1"

    def __post_init__(self) -> None:
        _require_id_token(self.aggregate_turn_id, "MayaTurnProposal.aggregate_turn_id")
        if type(self.intent_closure) is not MayaIntentClosure:
            raise TypeError("MayaTurnProposal.intent_closure must be exact")

        from reservation_boundary.reads import ReadObservation

        if type(self.read_observations) is not tuple or any(
            type(item) is not ReadObservation for item in self.read_observations
        ):
            raise TypeError("MayaTurnProposal.read_observations must be exact")
        read_request_hashes = tuple(item.request_hash for item in self.read_observations)
        read_frame_hashes = tuple(
            item.frame_commitment_hash for item in self.read_observations
        )
        if len(set(read_request_hashes)) != len(read_request_hashes) or len(
            set(read_frame_hashes)
        ) != len(read_frame_hashes):
            raise ValueError("MayaTurnProposal read observations must be unique")

        if type(self.facts) is not tuple or any(
            type(item) is not TypedFact for item in self.facts
        ):
            raise TypeError("MayaTurnProposal.facts must be an exact TypedFact tuple")
        fact_order = {
            "language": 0,
            "service": 1,
            "start_date": 2,
            "end_date": 3,
            "adults": 4,
            "children": 5,
            "payment_method": 6,
        }
        fact_positions = tuple(fact_order[item.name] for item in self.facts)
        if (
            any(item.frame_commitment_hash is None for item in self.facts)
            or len(set(fact_positions)) != len(fact_positions)
            or fact_positions != tuple(sorted(fact_positions))
        ):
            raise ValueError("MayaTurnProposal facts must be v8, unique and ordered")

        if type(self.normalized_tool_proposals) is not tuple or any(
            type(item) is not NormalizedToolProposal
            for item in self.normalized_tool_proposals
        ):
            raise TypeError("MayaTurnProposal normalized tools must be exact")
        if type(self.learning_proposals) is not tuple or any(
            type(item) is not LearningProposal for item in self.learning_proposals
        ):
            raise TypeError("MayaTurnProposal learning proposals must be exact")
        child_proposals = self.normalized_tool_proposals + self.learning_proposals
        if any(item.aggregate_turn_id != self.aggregate_turn_id for item in child_proposals):
            raise ValueError("MayaTurnProposal child turn binding mismatch")
        tool_sequences = tuple(item.sequence for item in self.normalized_tool_proposals)
        learning_sequences = tuple(item.sequence for item in self.learning_proposals)
        if tool_sequences != tuple(sorted(tool_sequences)) or learning_sequences != tuple(
            sorted(learning_sequences)
        ):
            raise ValueError("MayaTurnProposal typed child artifacts must be ordered")
        ordered_child_proposals = tuple(
            sorted(child_proposals, key=lambda item: item.sequence)
        )
        sequences = tuple(item.sequence for item in ordered_child_proposals)
        request_ids = tuple(item.request_id for item in ordered_child_proposals)
        frame_hashes = tuple(
            item.frame_commitment_hash for item in ordered_child_proposals
        )
        if (
            len(set(sequences)) != len(sequences)
            or len(set(request_ids)) != len(request_ids)
            or len(set(frame_hashes)) != len(frame_hashes)
        ):
            raise ValueError("MayaTurnProposal child artifacts must be unique")

        if type(self.public_reply_chunks) is not tuple or any(
            type(item) is not PublicReplyChunk for item in self.public_reply_chunks
        ):
            raise TypeError("MayaTurnProposal public chunks must be exact")
        closure_hash = _require_sha256(
            self.maya_turn_closure_hash,
            "MayaTurnProposal.maya_turn_closure_hash",
        )
        for ordinal, chunk in enumerate(self.public_reply_chunks):
            if (
                chunk.aggregate_turn_id != self.aggregate_turn_id
                or chunk.ordinal != ordinal
                or chunk.source_closure_hash != closure_hash
            ):
                raise ValueError("MayaTurnProposal public chunk binding mismatch")
        _require_sha256(
            self.final_transcript_commitment_hash,
            "MayaTurnProposal.final_transcript_commitment_hash",
        )
        final_seq = _require_exact_int(
            self.final_seq,
            "MayaTurnProposal.final_seq",
            minimum=1,
        )
        if sequences and sequences[-1] >= final_seq:
            raise ValueError("MayaTurnProposal child sequence must precede final frame")
        _require_sha256(
            self.final_transcript_mac,
            "MayaTurnProposal.final_transcript_mac",
        )
        _require_sha256(
            self.runtime_graph_digest,
            "MayaTurnProposal.runtime_graph_digest",
        )
        if type(self.route) is not PublicRoute or type(self.reply_type) is not PublicReplyType:
            raise TypeError("MayaTurnProposal route/reply type must be exact")
        is_handoff = self.route is PublicRoute.HANDOFF
        is_no_reply = self.route is PublicRoute.NO_REPLY
        if is_handoff != (self.reply_type is PublicReplyType.HANDOFF):
            raise ValueError("MayaTurnProposal handoff route/reply mismatch")
        if is_no_reply != (self.reply_type is PublicReplyType.NO_REPLY):
            raise ValueError("MayaTurnProposal no_reply route/reply mismatch")
        if is_no_reply != (not self.public_reply_chunks):
            raise ValueError("MayaTurnProposal no_reply/chunk matrix mismatch")
        intent_handoff = (
            self.intent_closure.kind is ConversationIntentKind.REQUEST_HANDOFF
        )
        if intent_handoff != is_handoff:
            raise ValueError("MayaTurnProposal intent/route handoff mismatch")

    @classmethod
    def from_accepted_closure(
        cls,
        *,
        accepted_closure: MayaTurnClosure,
        read_observations: tuple["ReadObservation", ...],
        facts: tuple[TypedFact, ...],
        normalized_tool_proposals: tuple[NormalizedToolProposal, ...],
        learning_proposals: tuple[LearningProposal, ...],
        public_reply_chunks: tuple[PublicReplyChunk, ...],
        final_transcript_commitment_hash: str,
        final_transcript_mac: str,
        runtime_graph_digest: str,
    ) -> MayaTurnProposal:
        """Build the parent proposal from the exact accepted child closure."""

        if type(accepted_closure) is not MayaTurnClosure:
            raise TypeError("accepted_closure must be exact MayaTurnClosure")
        proposal = cls(
            aggregate_turn_id=accepted_closure.aggregate_turn_id,
            intent_closure=accepted_closure.intent_closure,
            read_observations=read_observations,
            facts=facts,
            normalized_tool_proposals=normalized_tool_proposals,
            learning_proposals=learning_proposals,
            public_reply_chunks=public_reply_chunks,
            maya_turn_closure_hash=accepted_closure.canonical_hash(),
            final_transcript_commitment_hash=final_transcript_commitment_hash,
            final_seq=accepted_closure.final_seq,
            final_transcript_mac=final_transcript_mac,
            runtime_graph_digest=runtime_graph_digest,
            route=accepted_closure.route,
            reply_type=accepted_closure.reply_type,
        )
        proposal.verify_accepted_closure(accepted_closure)
        return proposal

    def verify_accepted_closure(self, accepted_closure: MayaTurnClosure) -> None:
        """Reject a decoded proposal unless it binds the exact accepted closure."""

        if type(accepted_closure) is not MayaTurnClosure:
            raise TypeError("accepted_closure must be exact MayaTurnClosure")
        if (
            self.aggregate_turn_id != accepted_closure.aggregate_turn_id
            or self.intent_closure != accepted_closure.intent_closure
            or self.maya_turn_closure_hash != accepted_closure.canonical_hash()
            or self.final_seq != accepted_closure.final_seq
            or self.route is not accepted_closure.route
            or self.reply_type is not accepted_closure.reply_type
        ):
            raise ValueError("MayaTurnProposal accepted closure binding mismatch")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "aggregate_turn_id": self.aggregate_turn_id,
                "intent_closure": json.loads(
                    self.intent_closure.to_canonical_bytes().decode("utf-8")
                ),
                "read_observations": [
                    json.loads(item.to_canonical_bytes().decode("utf-8"))
                    for item in self.read_observations
                ],
                "facts": [
                    json.loads(item.to_canonical_bytes().decode("utf-8"))
                    for item in self.facts
                ],
                "normalized_tool_proposals": [
                    json.loads(item.to_canonical_bytes().decode("utf-8"))
                    for item in self.normalized_tool_proposals
                ],
                "learning_proposals": [
                    json.loads(item.to_canonical_bytes().decode("utf-8"))
                    for item in self.learning_proposals
                ],
                "public_reply_chunks": [
                    json.loads(item.to_canonical_bytes().decode("utf-8"))
                    for item in self.public_reply_chunks
                ],
                "maya_turn_closure_hash": self.maya_turn_closure_hash,
                "final_transcript_commitment_hash": (
                    self.final_transcript_commitment_hash
                ),
                "final_seq": self.final_seq,
                "final_transcript_mac": self.final_transcript_mac,
                "runtime_graph_digest": self.runtime_graph_digest,
                "route": self.route.value,
                "reply_type": self.reply_type.value,
            },
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()


class TranscriptDirection(str, Enum):
    CHILD_TO_PARENT = "child_to_parent"
    PARENT_TO_CHILD = "parent_to_child"


class TranscriptKind(str, Enum):
    READ = "read"
    STATE_COMMIT = "state_commit"
    LEARNING = "learning"
    COMMAND = "command"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class TranscriptCommitment:
    """Privacy-safe deterministic commitment for one authenticated frame."""

    direction: TranscriptDirection
    kind: TranscriptKind
    sequence: int
    request_id: str
    request_hash: str
    response_hash: str
    previous_frame_commitment: str

    SCHEMA: ClassVar[str] = "phase8-transcript-commitment"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-transcript-commitment-v1"

    def __post_init__(self) -> None:
        if type(self.direction) is not TranscriptDirection:
            raise TypeError(
                "TranscriptCommitment.direction must be an exact TranscriptDirection"
            )
        if type(self.kind) is not TranscriptKind:
            raise TypeError("TranscriptCommitment.kind must be an exact TranscriptKind")
        if (
            self.kind is TranscriptKind.FINAL
            and self.direction is not TranscriptDirection.CHILD_TO_PARENT
        ):
            raise ValueError("FINAL commitments must flow from child to parent")
        _require_exact_int(
            self.sequence,
            "TranscriptCommitment.sequence",
            minimum=1,
        )
        _require_identifier(self.request_id, "TranscriptCommitment.request_id")
        _require_sha256(self.request_hash, "TranscriptCommitment.request_hash")
        _require_sha256(self.response_hash, "TranscriptCommitment.response_hash")
        _require_sha256(
            self.previous_frame_commitment,
            "TranscriptCommitment.previous_frame_commitment",
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data={
                "direction": self.direction.value,
                "kind": self.kind.value,
                "sequence": self.sequence,
                "request_id": self.request_id,
                "request_hash": self.request_hash,
                "response_hash": self.response_hash,
                "previous_frame_commitment": self.previous_frame_commitment,
            },
        )

    def canonical_hash(self) -> str:
        preimage = self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        return hashlib.sha256(preimage).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "TranscriptCommitment":
        data = _load_contract_data(
            payload,
            "TranscriptCommitment",
            schema=cls.SCHEMA,
            version=cls.VERSION,
            fields=frozenset(
                (
                    "direction",
                    "kind",
                    "sequence",
                    "request_id",
                    "request_hash",
                    "response_hash",
                    "previous_frame_commitment",
                )
            ),
        )
        try:
            direction = TranscriptDirection(data["direction"])
            kind = TranscriptKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError("TranscriptCommitment enum value is invalid") from exc
        commitment = cls(
            direction=direction,
            kind=kind,
            sequence=data["sequence"],
            request_id=data["request_id"],
            request_hash=data["request_hash"],
            response_hash=data["response_hash"],
            previous_frame_commitment=data["previous_frame_commitment"],
        )
        if commitment.to_canonical_bytes() != payload:
            raise ValueError("TranscriptCommitment is not byte-canonical")
        return commitment


__all__ = (
    "Capability",
    "CapabilityDisposition",
    "CapabilityPolicy",
    "ConversationProjection",
    "ConversationStage",
    "DesiredService",
    "GuardSemantic",
    "LearningProposal",
    "MayaIntentClosure",
    "MayaTurnClosure",
    "MayaTurnProposal",
    "MayaTurnRequest",
    "NormalizedCommandArgumentsType",
    "NormalizedCommandTool",
    "NormalizedToolProposal",
    "PublicReplyChunk",
    "PublicReplyType",
    "PublicRoute",
    "ReservationExecutionProjection",
    "SourceEventIdentity",
    "TranscriptCommitment",
    "TranscriptDirection",
    "TranscriptKind",
    "Worker",
    "WorkerMode",
)
