"""Closed immutable conversation contracts for the Phase 8 boundary."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
import re
from typing import ClassVar, Final

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
        _require_exact_int(self.sequence, "NormalizedToolProposal.sequence", minimum=0)
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
        _require_exact_int(self.sequence, "LearningProposal.sequence", minimum=0)
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
