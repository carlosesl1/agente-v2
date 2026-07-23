"""Closed immutable effect and relay contracts for the Phase 8 boundary."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import ClassVar, Final

from reservation_followup.handoff import (
    HandoffAcknowledged,
    HandoffCancelled,
    HandoffEffectFailed,
    HandoffRequested,
)
from reservation_followup.serialization import from_wire_json, to_wire_json
from reservation_followup.types import HandoffEffectPolicy

RESERVATION_RELAY_DOMAIN: Final = "phase8-reservation-relay-bundle-v1"
SETTLEMENT_RELAY_DOMAIN: Final = "phase8-settlement-relay-bundle-v1"
HANDOFF_RELAY_DOMAIN: Final = "phase8-handoff-relay-bundle-v1"
TARGET_OPERATION_RECEIPT_DOMAIN: Final = "phase8-target-operation-receipt-v1"
TARGET_OPERATION_ID_DOMAIN: Final = "phase8-target-operation-id-v1"

_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def _require_bytes(value: object, name: str) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{name} must be exact bytes")
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _require_bytes_tuple(value: object, name: str) -> tuple[bytes, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be an exact tuple")
    for item in value:
        _require_bytes(item, f"{name} item")
    return value


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


def _require_generation(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    if value < 1:
        raise ValueError(f"{name} must be >= 1")
    return value


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _canonical_envelope(*, schema: str, version: int, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": schema, "version": version, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_object_bytes(data: dict[str, object]) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _load_canonical_envelope(payload: bytes, owner: str) -> dict[str, object]:
    _require_bytes(payload, owner)

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{owner} has a duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"{owner} has non-finite JSON: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{owner} must be canonical UTF-8 JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{owner} envelope must be an object")
    return value


def _unb64(value: object, name: str) -> bytes:
    if type(value) is not str:
        raise TypeError(f"{name} must be base64 text")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"{name} must be canonical base64") from exc
    if _b64(decoded) != value or not decoded:
        raise ValueError(f"{name} must be non-empty canonical base64")
    return decoded


def _utc_text(value: object, name: str) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise TypeError(f"{name} must be an exact UTC datetime")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_utc(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise TypeError(f"{name} must be canonical UTC text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(f"{name} must be canonical UTC text") from exc
    if _utc_text(parsed, name) != value:
        raise ValueError(f"{name} must be canonical UTC text")
    return parsed


@dataclass(frozen=True, slots=True)
class ReservationRelayBundle:
    """Canonical Phase 5 full-replay bundle with backlink-independent identity."""

    genesis_state: bytes
    phase5_events: tuple[bytes, ...]
    summary_outboxes: tuple[bytes, ...]
    expected_final_state: bytes
    expected_final_state_hash: str
    command_ledger_seed: bytes
    qualification_id: str | None
    scenario_id: str | None
    immutable_generation: int | None
    allocation_id: str | None
    artifact_hash: str

    SCHEMA: ClassVar[str] = "phase8-reservation-relay-bundle"
    PREIMAGE_SCHEMA: ClassVar[str] = "phase8-reservation-relay-bundle-preimage"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = RESERVATION_RELAY_DOMAIN

    def __post_init__(self) -> None:
        _require_bytes(self.genesis_state, "ReservationRelayBundle.genesis_state")
        _require_bytes_tuple(
            self.phase5_events,
            "ReservationRelayBundle.phase5_events",
        )
        _require_bytes_tuple(
            self.summary_outboxes,
            "ReservationRelayBundle.summary_outboxes",
        )
        _require_bytes(
            self.expected_final_state,
            "ReservationRelayBundle.expected_final_state",
        )
        _require_sha256(
            self.expected_final_state_hash,
            "ReservationRelayBundle.expected_final_state_hash",
        )
        if (
            hashlib.sha256(self.expected_final_state).hexdigest()
            != self.expected_final_state_hash
        ):
            raise ValueError(
                "expected_final_state_hash does not authenticate expected_final_state"
            )
        _require_bytes(
            self.command_ledger_seed,
            "ReservationRelayBundle.command_ledger_seed",
        )

        e2e_values = (
            self.qualification_id,
            self.scenario_id,
            self.immutable_generation,
            self.allocation_id,
        )
        present = tuple(value is not None for value in e2e_values)
        if any(present) and not all(present):
            raise ValueError("E2E relay fields must be all-null or all-present")
        if all(present):
            _require_identifier(
                self.qualification_id,
                "ReservationRelayBundle.qualification_id",
            )
            _require_identifier(
                self.scenario_id,
                "ReservationRelayBundle.scenario_id",
            )
            _require_generation(
                self.immutable_generation,
                "ReservationRelayBundle.immutable_generation",
            )
            _require_identifier(
                self.allocation_id,
                "ReservationRelayBundle.allocation_id",
            )

        _require_sha256(self.artifact_hash, "ReservationRelayBundle.artifact_hash")
        expected_artifact_hash = hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.artifact_preimage_bytes()
        ).hexdigest()
        if self.artifact_hash != expected_artifact_hash:
            raise ValueError(
                "artifact_hash does not authenticate relay bundle preimage"
            )

    def _preimage_data(self) -> dict[str, object]:
        return {
            "genesis_state": _b64(self.genesis_state),
            "phase5_events": [_b64(value) for value in self.phase5_events],
            "summary_outboxes": [_b64(value) for value in self.summary_outboxes],
            "expected_final_state": _b64(self.expected_final_state),
            "expected_final_state_hash": self.expected_final_state_hash,
            "command_ledger_seed": _b64(self.command_ledger_seed),
            "qualification_id": self.qualification_id,
            "scenario_id": self.scenario_id,
            "immutable_generation": self.immutable_generation,
            "allocation_id": self.allocation_id,
        }

    def artifact_preimage_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.PREIMAGE_SCHEMA,
            version=self.VERSION,
            data=self._preimage_data(),
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data=self._preimage_data() | {"artifact_hash": self.artifact_hash},
        )

    @classmethod
    def create(
        cls,
        *,
        genesis_state: bytes,
        phase5_events: tuple[bytes, ...],
        summary_outboxes: tuple[bytes, ...],
        expected_final_state: bytes,
        command_ledger_seed: bytes,
        qualification_id: str | None = None,
        scenario_id: str | None = None,
        immutable_generation: int | None = None,
        allocation_id: str | None = None,
    ) -> ReservationRelayBundle:
        final_hash = hashlib.sha256(expected_final_state).hexdigest()
        data = {
            "genesis_state": _b64(genesis_state),
            "phase5_events": [_b64(value) for value in phase5_events],
            "summary_outboxes": [_b64(value) for value in summary_outboxes],
            "expected_final_state": _b64(expected_final_state),
            "expected_final_state_hash": final_hash,
            "command_ledger_seed": _b64(command_ledger_seed),
            "qualification_id": qualification_id,
            "scenario_id": scenario_id,
            "immutable_generation": immutable_generation,
            "allocation_id": allocation_id,
        }
        preimage = _canonical_envelope(
            schema=cls.PREIMAGE_SCHEMA,
            version=cls.VERSION,
            data=data,
        )
        artifact_hash = hashlib.sha256(
            cls.DOMAIN.encode("ascii") + b"\x00" + preimage
        ).hexdigest()
        return cls(
            genesis_state=genesis_state,
            phase5_events=phase5_events,
            summary_outboxes=summary_outboxes,
            expected_final_state=expected_final_state,
            expected_final_state_hash=final_hash,
            command_ledger_seed=command_ledger_seed,
            qualification_id=qualification_id,
            scenario_id=scenario_id,
            immutable_generation=immutable_generation,
            allocation_id=allocation_id,
            artifact_hash=artifact_hash,
        )

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> ReservationRelayBundle:
        envelope = _load_canonical_envelope(payload, cls.__name__)
        if set(envelope) != {"schema", "version", "data"}:
            raise ValueError("ReservationRelayBundle envelope fields mismatch")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("ReservationRelayBundle identity mismatch")
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
        if type(data) is not dict or set(data) != expected:
            raise ValueError("ReservationRelayBundle fields mismatch")
        events = data["phase5_events"]
        outboxes = data["summary_outboxes"]
        if type(events) is not list or type(outboxes) is not list:
            raise TypeError("ReservationRelayBundle event/outbox fields must be arrays")
        bundle = cls(
            genesis_state=_unb64(data["genesis_state"], "genesis_state"),
            phase5_events=tuple(_unb64(item, "phase5_events item") for item in events),
            summary_outboxes=tuple(
                _unb64(item, "summary_outboxes item") for item in outboxes
            ),
            expected_final_state=_unb64(
                data["expected_final_state"], "expected_final_state"
            ),
            expected_final_state_hash=data["expected_final_state_hash"],
            command_ledger_seed=_unb64(
                data["command_ledger_seed"], "command_ledger_seed"
            ),
            qualification_id=data["qualification_id"],
            scenario_id=data["scenario_id"],
            immutable_generation=data["immutable_generation"],
            allocation_id=data["allocation_id"],
            artifact_hash=data["artifact_hash"],
        )
        if bundle.to_canonical_bytes() != payload:
            raise ValueError("ReservationRelayBundle is not byte-canonical")
        return bundle


@dataclass(frozen=True, slots=True)
class CommandRelayClaim:
    relay_id: str
    command_id: str
    bundle_bytes: bytes
    bundle_hash: str
    source_turn_receipt_hash: str
    target_operation_id: str
    worker_id: str
    fencing_token: int
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.relay_id, "relay_id"),
            (self.command_id, "command_id"),
            (self.worker_id, "worker_id"),
        ):
            _require_identifier(value, f"CommandRelayClaim.{name}")
        for value, name in (
            (self.bundle_hash, "bundle_hash"),
            (self.source_turn_receipt_hash, "source_turn_receipt_hash"),
            (self.target_operation_id, "target_operation_id"),
        ):
            _require_sha256(value, f"CommandRelayClaim.{name}")
        _require_bytes(self.bundle_bytes, "CommandRelayClaim.bundle_bytes")
        if type(self.fencing_token) is not int or self.fencing_token < 1:
            raise ValueError("CommandRelayClaim.fencing_token must be >= 1")
        _utc_text(self.lease_expires_at, "CommandRelayClaim.lease_expires_at")
        bundle = ReservationRelayBundle.from_canonical_bytes(self.bundle_bytes)
        if bundle.artifact_hash != self.bundle_hash:
            raise ValueError("CommandRelayClaim bundle bytes/hash diverged")


@dataclass(frozen=True, slots=True)
class SettlementRelayBundle:
    """Canonical Phase 6 settlement bundle with full replay material."""

    workflow_anchor: bytes
    policy: bytes
    payment_history: tuple[bytes, ...]
    evidence: tuple[bytes, ...]
    payment_command: bytes
    expected_final_state: bytes
    expected_final_state_hash: str
    qualification_id: str | None
    scenario_id: str | None
    immutable_generation: int | None
    allocation_id: str | None
    artifact_hash: str

    SCHEMA: ClassVar[str] = "phase8-settlement-relay-bundle"
    PREIMAGE_SCHEMA: ClassVar[str] = "phase8-settlement-relay-bundle-preimage"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = SETTLEMENT_RELAY_DOMAIN

    def __post_init__(self) -> None:
        _require_bytes(
            self.workflow_anchor,
            "SettlementRelayBundle.workflow_anchor",
        )
        _require_bytes(self.policy, "SettlementRelayBundle.policy")
        _require_bytes_tuple(
            self.payment_history,
            "SettlementRelayBundle.payment_history",
        )
        _require_bytes_tuple(self.evidence, "SettlementRelayBundle.evidence")
        _require_bytes(
            self.payment_command,
            "SettlementRelayBundle.payment_command",
        )
        _require_bytes(
            self.expected_final_state,
            "SettlementRelayBundle.expected_final_state",
        )
        _require_sha256(
            self.expected_final_state_hash,
            "SettlementRelayBundle.expected_final_state_hash",
        )
        if (
            hashlib.sha256(self.expected_final_state).hexdigest()
            != self.expected_final_state_hash
        ):
            raise ValueError(
                "expected_final_state_hash does not authenticate expected_final_state"
            )

        e2e_values = (
            self.qualification_id,
            self.scenario_id,
            self.immutable_generation,
            self.allocation_id,
        )
        present = tuple(value is not None for value in e2e_values)
        if any(present) and not all(present):
            raise ValueError("E2E relay fields must be all-null or all-present")
        if all(present):
            _require_identifier(
                self.qualification_id,
                "SettlementRelayBundle.qualification_id",
            )
            _require_identifier(
                self.scenario_id,
                "SettlementRelayBundle.scenario_id",
            )
            _require_generation(
                self.immutable_generation,
                "SettlementRelayBundle.immutable_generation",
            )
            _require_identifier(
                self.allocation_id,
                "SettlementRelayBundle.allocation_id",
            )

        _require_sha256(self.artifact_hash, "SettlementRelayBundle.artifact_hash")
        expected_artifact_hash = hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.artifact_preimage_bytes()
        ).hexdigest()
        if self.artifact_hash != expected_artifact_hash:
            raise ValueError(
                "artifact_hash does not authenticate relay bundle preimage"
            )

    def _preimage_data(self) -> dict[str, object]:
        return {
            "workflow_anchor": _b64(self.workflow_anchor),
            "policy": _b64(self.policy),
            "payment_history": [_b64(value) for value in self.payment_history],
            "evidence": [_b64(value) for value in self.evidence],
            "payment_command": _b64(self.payment_command),
            "expected_final_state": _b64(self.expected_final_state),
            "expected_final_state_hash": self.expected_final_state_hash,
            "qualification_id": self.qualification_id,
            "scenario_id": self.scenario_id,
            "immutable_generation": self.immutable_generation,
            "allocation_id": self.allocation_id,
        }

    def artifact_preimage_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.PREIMAGE_SCHEMA,
            version=self.VERSION,
            data=self._preimage_data(),
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data=self._preimage_data() | {"artifact_hash": self.artifact_hash},
        )


@dataclass(frozen=True, slots=True)
class HandoffRelayBundle:
    """Canonical Phase 6 handoff replay bundle without source backlink."""

    request_bytes: bytes
    policy_bytes: bytes
    history_bytes: tuple[bytes, ...]
    expected_final_state_hash: str
    artifact_hash: str

    SCHEMA: ClassVar[str] = "phase8-handoff-relay-bundle"
    PREIMAGE_SCHEMA: ClassVar[str] = "phase8-handoff-relay-bundle-preimage"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = HANDOFF_RELAY_DOMAIN

    def __post_init__(self) -> None:
        _require_bytes(self.request_bytes, "HandoffRelayBundle.request_bytes")
        _require_bytes(self.policy_bytes, "HandoffRelayBundle.policy_bytes")
        _require_bytes_tuple(self.history_bytes, "HandoffRelayBundle.history_bytes")
        request = from_wire_json(
            self.request_bytes.decode("utf-8"),
            HandoffRequested,
        )
        policy = from_wire_json(
            self.policy_bytes.decode("utf-8"),
            HandoffEffectPolicy,
        )
        if type(request) is not HandoffRequested:
            raise ValueError("request_bytes must encode exact HandoffRequested")
        if type(policy) is not HandoffEffectPolicy:
            raise ValueError("policy_bytes must encode exact HandoffEffectPolicy")
        if to_wire_json(request).encode("utf-8") != self.request_bytes:
            raise ValueError("request_bytes must be byte-canonical")
        if to_wire_json(policy).encode("utf-8") != self.policy_bytes:
            raise ValueError("policy_bytes must be byte-canonical")
        history_types = (HandoffAcknowledged, HandoffEffectFailed, HandoffCancelled)
        for payload in self.history_bytes:
            event = None
            for expected_type in history_types:
                try:
                    event = from_wire_json(payload.decode("utf-8"), expected_type)
                    break
                except ValueError:
                    continue
            if event is None or type(event) not in history_types:
                raise ValueError("history_bytes contains a non-history handoff event")
            if to_wire_json(event).encode("utf-8") != payload:
                raise ValueError("history_bytes member must be byte-canonical")
        _require_sha256(
            self.expected_final_state_hash,
            "HandoffRelayBundle.expected_final_state_hash",
        )
        _require_sha256(self.artifact_hash, "HandoffRelayBundle.artifact_hash")
        expected = hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.artifact_preimage_bytes()
        ).hexdigest()
        if self.artifact_hash != expected:
            raise ValueError(
                "artifact_hash does not authenticate handoff relay preimage"
            )

    def _preimage_data(self) -> dict[str, object]:
        return {
            "request_bytes": _b64(self.request_bytes),
            "policy_bytes": _b64(self.policy_bytes),
            "history_bytes": [_b64(value) for value in self.history_bytes],
            "expected_final_state_hash": self.expected_final_state_hash,
        }

    def artifact_preimage_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.PREIMAGE_SCHEMA,
            version=self.VERSION,
            data=self._preimage_data(),
        )

    def to_canonical_bytes(self) -> bytes:
        return _canonical_envelope(
            schema=self.SCHEMA,
            version=self.VERSION,
            data=self._preimage_data() | {"artifact_hash": self.artifact_hash},
        )

    @classmethod
    def create(
        cls,
        *,
        request_bytes: bytes,
        policy_bytes: bytes,
        history_bytes: tuple[bytes, ...],
        expected_final_state_hash: str,
    ) -> HandoffRelayBundle:
        preimage = _canonical_envelope(
            schema=cls.PREIMAGE_SCHEMA,
            version=cls.VERSION,
            data={
                "request_bytes": _b64(request_bytes),
                "policy_bytes": _b64(policy_bytes),
                "history_bytes": [_b64(value) for value in history_bytes],
                "expected_final_state_hash": expected_final_state_hash,
            },
        )
        artifact_hash = hashlib.sha256(
            cls.DOMAIN.encode("ascii") + b"\x00" + preimage
        ).hexdigest()
        return cls(
            request_bytes=request_bytes,
            policy_bytes=policy_bytes,
            history_bytes=history_bytes,
            expected_final_state_hash=expected_final_state_hash,
            artifact_hash=artifact_hash,
        )

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> HandoffRelayBundle:
        envelope = _load_canonical_envelope(payload, cls.__name__)
        if set(envelope) != {"schema", "version", "data"}:
            raise ValueError("HandoffRelayBundle envelope fields mismatch")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("HandoffRelayBundle identity mismatch")
        data = envelope["data"]
        if type(data) is not dict or set(data) != {
            "request_bytes",
            "policy_bytes",
            "history_bytes",
            "expected_final_state_hash",
            "artifact_hash",
        }:
            raise ValueError("HandoffRelayBundle fields mismatch")
        history = data["history_bytes"]
        if type(history) is not list:
            raise TypeError("HandoffRelayBundle.history_bytes must be an array")
        bundle = cls(
            request_bytes=_unb64(data["request_bytes"], "request_bytes"),
            policy_bytes=_unb64(data["policy_bytes"], "policy_bytes"),
            history_bytes=tuple(_unb64(item, "history_bytes item") for item in history),
            expected_final_state_hash=data["expected_final_state_hash"],
            artifact_hash=data["artifact_hash"],
        )
        if bundle.to_canonical_bytes() != payload:
            raise ValueError("HandoffRelayBundle is not byte-canonical")
        return bundle


class InternalJobKind(str, Enum):
    HANDOFF = "handoff"
    LEARNING = "learning"


@dataclass(frozen=True, slots=True)
class InternalRelayClaim:
    job_id: str
    job_kind: InternalJobKind
    artifact_bytes: bytes
    artifact_hash: str
    source_turn_receipt_hash: str
    target_operation_id: str
    worker_id: str
    fencing_token: int
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.job_id, "job_id"),
            (self.worker_id, "worker_id"),
        ):
            _require_identifier(value, f"InternalRelayClaim.{name}")
        if type(self.job_kind) is not InternalJobKind:
            raise TypeError("InternalRelayClaim.job_kind must be exact InternalJobKind")
        for value, name in (
            (self.artifact_hash, "artifact_hash"),
            (self.source_turn_receipt_hash, "source_turn_receipt_hash"),
            (self.target_operation_id, "target_operation_id"),
        ):
            _require_sha256(value, f"InternalRelayClaim.{name}")
        _require_bytes(self.artifact_bytes, "InternalRelayClaim.artifact_bytes")
        if type(self.fencing_token) is not int or self.fencing_token < 1:
            raise ValueError("InternalRelayClaim.fencing_token must be >= 1")
        _utc_text(self.lease_expires_at, "InternalRelayClaim.lease_expires_at")
        if self.job_kind is InternalJobKind.HANDOFF:
            bundle = HandoffRelayBundle.from_canonical_bytes(self.artifact_bytes)
            if bundle.artifact_hash != self.artifact_hash:
                raise ValueError("InternalRelayClaim handoff bytes/hash diverged")


def target_operation_id(
    job_kind: InternalJobKind,
    artifact_hash: str,
    source_turn_receipt_hash: str,
) -> str:
    if type(job_kind) is not InternalJobKind:
        raise TypeError("job_kind must be exact InternalJobKind")
    _require_sha256(artifact_hash, "artifact_hash")
    _require_sha256(source_turn_receipt_hash, "source_turn_receipt_hash")
    preimage = _canonical_envelope(
        schema="phase8-target-operation-id-preimage",
        version=1,
        data={
            "job_kind": job_kind.value,
            "artifact_hash": artifact_hash,
            "source_turn_receipt_hash": source_turn_receipt_hash,
        },
    )
    return hashlib.sha256(
        TARGET_OPERATION_ID_DOMAIN.encode("ascii") + b"\0" + preimage
    ).hexdigest()


def phase5_outbox_seed_bytes(message: object) -> bytes:
    from reservation_execution import OutboxMessage

    if type(message) is not OutboxMessage:
        raise TypeError("message must be the exact Phase 5 OutboxMessage type")
    return _canonical_object_bytes(
        {
            "canonical_payload": message.canonical_payload,
            "command_id": message.command_id,
            "created_at": message.created_at.isoformat(),
            "idempotency_key": message.idempotency_key,
            "kind": message.kind.value,
            "message_id": message.message_id,
            "payload_hash": message.payload_hash,
            "template_id": message.template_id,
            "workflow_id": message.workflow_id,
        }
    )


def phase5_outbox_from_seed_bytes(payload: bytes):
    from reservation_execution import OutboxKind, OutboxMessage

    if type(payload) is not bytes or not payload:
        raise TypeError("Phase 5 outbox seed must be non-empty exact bytes")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Phase 5 outbox seed has duplicate JSON keys")
            result[key] = value
        return result

    try:
        decoded = payload.decode("utf-8")
        data = json.loads(
            decoded,
            object_pairs_hook=unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"Phase 5 outbox seed has non-finite JSON: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("Phase 5 outbox seed is invalid JSON") from exc
    expected = {
        "canonical_payload",
        "command_id",
        "created_at",
        "idempotency_key",
        "kind",
        "message_id",
        "payload_hash",
        "template_id",
        "workflow_id",
    }
    if type(data) is not dict or set(data) != expected:
        raise ValueError("Phase 5 outbox seed fields mismatch")
    try:
        message = OutboxMessage(
            message_id=data["message_id"],
            idempotency_key=data["idempotency_key"],
            workflow_id=data["workflow_id"],
            command_id=data["command_id"],
            kind=OutboxKind(data["kind"]),
            template_id=data["template_id"],
            canonical_payload=data["canonical_payload"],
            payload_hash=data["payload_hash"],
            created_at=datetime.fromisoformat(data["created_at"]),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Phase 5 outbox seed is invalid") from exc
    if phase5_outbox_seed_bytes(message) != payload:
        raise ValueError("Phase 5 outbox seed is noncanonical")
    return message


@dataclass(frozen=True, slots=True)
class TargetOperationReceipt:
    """Canonical target-owned atomic operation receipt."""

    operation_id: str
    job_kind: InternalJobKind
    artifact_hash: str
    source_turn_receipt_hash: str
    target_commit_hash: str
    target_result_hash: str
    committed_at: datetime

    SCHEMA: ClassVar[str] = "phase8-target-operation-receipt"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = TARGET_OPERATION_RECEIPT_DOMAIN

    def __post_init__(self) -> None:
        for name in (
            "operation_id",
            "artifact_hash",
            "source_turn_receipt_hash",
            "target_commit_hash",
            "target_result_hash",
        ):
            _require_sha256(getattr(self, name), f"TargetOperationReceipt.{name}")
        if type(self.job_kind) is not InternalJobKind:
            raise TypeError(
                "TargetOperationReceipt.job_kind must be exact InternalJobKind"
            )
        _utc_text(self.committed_at, "TargetOperationReceipt.committed_at")

    def _data(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "job_kind": self.job_kind.value,
            "artifact_hash": self.artifact_hash,
            "source_turn_receipt_hash": self.source_turn_receipt_hash,
            "target_commit_hash": self.target_commit_hash,
            "target_result_hash": self.target_result_hash,
            "committed_at": _utc_text(
                self.committed_at,
                "TargetOperationReceipt.committed_at",
            ),
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
    def from_canonical_bytes(cls, payload: bytes) -> TargetOperationReceipt:
        envelope = _load_canonical_envelope(payload, cls.__name__)
        if set(envelope) != {"schema", "version", "data"}:
            raise ValueError("TargetOperationReceipt envelope fields mismatch")
        if envelope["schema"] != cls.SCHEMA or envelope["version"] != cls.VERSION:
            raise ValueError("TargetOperationReceipt identity mismatch")
        data = envelope["data"]
        expected = {
            "operation_id",
            "job_kind",
            "artifact_hash",
            "source_turn_receipt_hash",
            "target_commit_hash",
            "target_result_hash",
            "committed_at",
        }
        if type(data) is not dict or set(data) != expected:
            raise ValueError("TargetOperationReceipt fields mismatch")
        try:
            job_kind = InternalJobKind(data["job_kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError("TargetOperationReceipt job_kind is unknown") from exc
        receipt = cls(
            operation_id=data["operation_id"],
            job_kind=job_kind,
            artifact_hash=data["artifact_hash"],
            source_turn_receipt_hash=data["source_turn_receipt_hash"],
            target_commit_hash=data["target_commit_hash"],
            target_result_hash=data["target_result_hash"],
            committed_at=_parse_utc(data["committed_at"], "committed_at"),
        )
        if receipt.to_canonical_bytes() != payload:
            raise ValueError("TargetOperationReceipt is not byte-canonical")
        return receipt


__all__ = (
    "HANDOFF_RELAY_DOMAIN",
    "RESERVATION_RELAY_DOMAIN",
    "SETTLEMENT_RELAY_DOMAIN",
    "TARGET_OPERATION_ID_DOMAIN",
    "TARGET_OPERATION_RECEIPT_DOMAIN",
    "HandoffRelayBundle",
    "InternalJobKind",
    "ReservationRelayBundle",
    "SettlementRelayBundle",
    "TargetOperationReceipt",
    "target_operation_id",
)
