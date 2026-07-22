"""Fenced single-write SQLite store for Phase 7 boundary state."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import ClassVar, Protocol

from reservation_domain import ReservationCommand, dumps_command
from reservation_execution import OutboxMessage
from reservation_followup import (
    PaymentSettlementCommand,
    to_wire_json as to_phase6_wire_json,
)

from reservation_boundary.schema import (
    BOUNDARY_V8_TABLES,
    TABLE_NAMES,
    expected_sqlite_v8_schema_fingerprint,
    render_sqlite,
    render_sqlite_v8,
    sqlite_v8_schema_fingerprint,
)
from reservation_boundary.conversation import PublicReplyChunk, SourceEventIdentity
from reservation_boundary.reads import ReadObservation
from reservation_boundary.serialization import from_wire_json, semantic_hash, to_wire_json
from reservation_boundary.types import (
    BoundaryCommit,
    BoundaryState,
    ImportDisposition,
    ImportResult,
    LegacyLeadSnapshot,
    VersionedBoundaryState,
)


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_FACTORY_TOKEN = object()


class BoundaryStoreError(RuntimeError):
    """Base class for exact boundary persistence failures."""


class ConcurrencyConflict(BoundaryStoreError):
    """State version or fencing token is stale."""


class IdentityConflict(BoundaryStoreError):
    """A durable identity was reused with divergent canonical bytes."""


class DataCorruption(BoundaryStoreError):
    """Persisted bytes violate their canonical hash or type."""


class StateNotFound(BoundaryStoreError):
    """No typed boundary state exists for the requested lead."""


class LegacyStateReadPort(Protocol):
    """Read-only legacy port; single-write is structural, not conventional."""

    def read_snapshot(self, lead_key: str) -> LegacyLeadSnapshot | None: ...


def _require_id(value: object, field_name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be an exact opaque identifier")
    return value


def _require_hash(value: object, field_name: str) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return value


def _require_int(value: object, field_name: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field_name} must be an exact integer >= {minimum}")
    return value


def _utc_text(value: object, field_name: str) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be an exact UTC datetime")
    text = value.isoformat()
    if datetime.fromisoformat(text) != value:
        raise ValueError(f"{field_name} must be canonical UTC")
    return text


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _receipt_unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _receipt_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _receipt_load_json(payload: bytes, name: str) -> dict[str, object]:
    if type(payload) is not bytes or not payload:
        raise TypeError(f"{name} must be non-empty exact bytes")
    try:
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_receipt_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} must be strict JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{name} must be a JSON object")
    return value


def _receipt_b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _receipt_b64decode(value: object, name: str) -> bytes:
    if type(value) is not str or not value:
        raise TypeError(f"{name} must be non-empty base64 text")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"{name} must be canonical base64") from exc
    if not decoded or _receipt_b64encode(decoded) != value:
        raise ValueError(f"{name} must be canonical base64")
    return decoded


def _receipt_utc(value: object, name: str) -> str:
    if type(value) is not datetime:
        raise TypeError(f"{name} must be an exact datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}."
        f"{value.microsecond:06d}Z"
    )


def _receipt_parse_utc(value: object, name: str) -> datetime:
    if type(value) is not str or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
        value,
    ) is None:
        raise ValueError(f"{name} must be canonical UTC text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(f"{name} must be canonical UTC text") from exc
    if _receipt_utc(parsed, name) != value:
        raise ValueError(f"{name} must be canonical UTC text")
    return parsed


def _receipt_id(value: object, name: str) -> str:
    if type(value) is not str or re.fullmatch(
        r"[a-z0-9][a-z0-9._:-]{0,127}", value
    ) is None:
        raise ValueError(f"{name} must be a Task 1 ID_TOKEN")
    return value


def _receipt_public_chunk(payload: bytes) -> PublicReplyChunk:
    envelope = _receipt_load_json(payload, "PublicReplyChunk")
    if set(envelope) != {"schema", "version", "data"}:
        raise ValueError("PublicReplyChunk envelope fields mismatch")
    if (
        envelope["schema"] != PublicReplyChunk.SCHEMA
        or envelope["version"] != PublicReplyChunk.VERSION
        or type(envelope["data"]) is not dict
    ):
        raise ValueError("PublicReplyChunk identity mismatch")
    data = envelope["data"]
    if set(data) != {
        "aggregate_turn_id",
        "ordinal",
        "text",
        "source_closure_hash",
    }:
        raise ValueError("PublicReplyChunk fields mismatch")
    chunk = PublicReplyChunk(
        aggregate_turn_id=data["aggregate_turn_id"],
        ordinal=data["ordinal"],
        text=data["text"],
        source_closure_hash=data["source_closure_hash"],
    )
    if chunk.to_canonical_bytes() != payload:
        raise ValueError("PublicReplyChunk is not byte-canonical")
    return chunk


@dataclass(frozen=True, slots=True)
class TurnReceipt:
    """Canonical receipt owned by the v8 atomic boundary store."""

    aggregate_turn_id: str
    event_hash: str
    source_events: tuple[SourceEventIdentity, ...]
    maya_proposal_hash: str
    kernel_decision_hash: str
    read_observations: tuple[tuple[str, bytes, str], ...]
    committed_state_version: int
    committed_state_hash: str
    public_chunks: tuple[tuple[str, int, bytes, str], ...]
    command_rows: tuple[tuple[str, str], ...]
    relay_rows: tuple[tuple[str, str], ...]
    internal_outbox_rows: tuple[tuple[str, str], ...]
    uds_transcript_mac: str
    uds_final_seq: int
    structural_graph_digest: str
    capability_policy_digest: str
    effective_stage_binding_digest: str
    behavior_state_snapshot_digest: str
    qualification_id: str | None
    admission_sequence: int | None
    admission_revision: int | None
    commit_fence_token: int | None
    allocation_manifest_hash: str | None
    immutable_generation: int | None
    allocation_ids: tuple[str, ...] | None
    committed_at: datetime
    previous_turn_receipt_hash: str | None
    artifact_hash: str = ""

    SCHEMA: ClassVar[str] = "phase8-turn-receipt"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-turn-receipt-v1"
    PREIMAGE_SCHEMA: ClassVar[str] = "phase8-turn-receipt-artifact-preimage"

    def __post_init__(self) -> None:
        _receipt_id(self.aggregate_turn_id, "TurnReceipt.aggregate_turn_id")
        for name in (
            "event_hash",
            "maya_proposal_hash",
            "kernel_decision_hash",
            "committed_state_hash",
            "uds_transcript_mac",
            "structural_graph_digest",
            "capability_policy_digest",
            "effective_stage_binding_digest",
            "behavior_state_snapshot_digest",
        ):
            _require_hash(getattr(self, name), f"TurnReceipt.{name}")
        _require_int(
            self.committed_state_version,
            "TurnReceipt.committed_state_version",
            minimum=1,
        )
        _require_int(self.uds_final_seq, "TurnReceipt.uds_final_seq", minimum=1)
        _receipt_utc(self.committed_at, "TurnReceipt.committed_at")
        if self.previous_turn_receipt_hash is not None:
            _require_hash(
                self.previous_turn_receipt_hash,
                "TurnReceipt.previous_turn_receipt_hash",
            )

        if type(self.source_events) is not tuple:
            raise TypeError("TurnReceipt.source_events must be an exact tuple")
        if not self.source_events:
            raise ValueError("TurnReceipt.source_events must be non-empty")
        if any(type(item) is not SourceEventIdentity for item in self.source_events):
            raise TypeError("TurnReceipt.source_events members must be exact")
        source_ids = tuple(item.source_event_id for item in self.source_events)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("TurnReceipt source event IDs must be unique")

        self._validate_read_rows()
        self._validate_public_rows()
        for name in ("command_rows", "relay_rows", "internal_outbox_rows"):
            self._validate_hash_rows(getattr(self, name), name)

        e2e_values = (
            self.qualification_id,
            self.admission_sequence,
            self.admission_revision,
            self.commit_fence_token,
            self.allocation_manifest_hash,
            self.immutable_generation,
            self.allocation_ids,
        )
        if not (all(item is None for item in e2e_values) or all(item is not None for item in e2e_values)):
            raise ValueError("TurnReceipt E2E fields must be all-null or all-present")
        if self.qualification_id is not None:
            _receipt_id(self.qualification_id, "TurnReceipt.qualification_id")
            _require_int(self.admission_sequence, "TurnReceipt.admission_sequence", minimum=1)
            _require_int(self.admission_revision, "TurnReceipt.admission_revision", minimum=1)
            _require_int(self.commit_fence_token, "TurnReceipt.commit_fence_token", minimum=1)
            _require_hash(
                self.allocation_manifest_hash,
                "TurnReceipt.allocation_manifest_hash",
            )
            _require_int(
                self.immutable_generation,
                "TurnReceipt.immutable_generation",
                minimum=1,
            )
            if type(self.allocation_ids) is not tuple or not self.allocation_ids:
                raise TypeError("TurnReceipt.allocation_ids must be a non-empty exact tuple")
            for allocation_id in self.allocation_ids:
                _receipt_id(allocation_id, "TurnReceipt.allocation_id")
            if len(self.allocation_ids) != len(set(self.allocation_ids)):
                raise ValueError("TurnReceipt allocation IDs must be unique")

        expected = hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.artifact_preimage_bytes()
        ).hexdigest()
        if self.artifact_hash == "":
            object.__setattr__(self, "artifact_hash", expected)
        else:
            _require_hash(self.artifact_hash, "TurnReceipt.artifact_hash")
            if self.artifact_hash != expected:
                raise ValueError("TurnReceipt.artifact_hash mismatch")

    def _validate_read_rows(self) -> None:
        if type(self.read_observations) is not tuple:
            raise TypeError("TurnReceipt.read_observations must be an exact tuple")
        row_ids: list[str] = []
        for row in self.read_observations:
            if type(row) is not tuple or len(row) != 3:
                raise TypeError("TurnReceipt read observation row must be an exact triple")
            row_id, payload, artifact_hash = row
            row_ids.append(_receipt_id(row_id, "TurnReceipt.read_observation.row_id"))
            if type(payload) is not bytes or not payload:
                raise TypeError("TurnReceipt read observation bytes must be exact")
            observation = ReadObservation.from_canonical_bytes(payload)
            _require_hash(artifact_hash, "TurnReceipt.read_observation.artifact_hash")
            if observation.canonical_hash() != artifact_hash:
                raise ValueError("TurnReceipt read observation artifact hash mismatch")
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("TurnReceipt read observation row IDs must be unique")

    def _validate_public_rows(self) -> None:
        if type(self.public_chunks) is not tuple:
            raise TypeError("TurnReceipt.public_chunks must be an exact tuple")
        row_ids: list[str] = []
        for expected_ordinal, row in enumerate(self.public_chunks):
            if type(row) is not tuple or len(row) != 4:
                raise TypeError("TurnReceipt public chunk row must be an exact quadruple")
            row_id, ordinal, payload, artifact_hash = row
            row_ids.append(_receipt_id(row_id, "TurnReceipt.public_chunk.row_id"))
            if type(ordinal) is not int or ordinal != expected_ordinal:
                raise ValueError("TurnReceipt public chunk ordinals must be contiguous")
            if type(payload) is not bytes or not payload:
                raise TypeError("TurnReceipt public chunk bytes must be exact")
            chunk = _receipt_public_chunk(payload)
            if chunk.aggregate_turn_id != self.aggregate_turn_id or chunk.ordinal != ordinal:
                raise ValueError("TurnReceipt public chunk turn/ordinal mismatch")
            _require_hash(artifact_hash, "TurnReceipt.public_chunk.artifact_hash")
            if chunk.canonical_hash() != artifact_hash:
                raise ValueError("TurnReceipt public chunk artifact hash mismatch")
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("TurnReceipt public chunk row IDs must be unique")

    @staticmethod
    def _validate_hash_rows(rows: object, name: str) -> None:
        if type(rows) is not tuple:
            raise TypeError(f"TurnReceipt.{name} must be an exact tuple")
        row_ids: list[str] = []
        for row in rows:
            if type(row) is not tuple or len(row) != 2:
                raise TypeError(f"TurnReceipt.{name} rows must be exact pairs")
            row_id, artifact_hash = row
            row_ids.append(_receipt_id(row_id, f"TurnReceipt.{name}.row_id"))
            _require_hash(artifact_hash, f"TurnReceipt.{name}.artifact_hash")
        if len(row_ids) != len(set(row_ids)):
            raise ValueError(f"TurnReceipt.{name} row IDs must be unique")

    def _data(self, *, include_artifact_hash: bool) -> dict[str, object]:
        data: dict[str, object] = {
            "aggregate_turn_id": self.aggregate_turn_id,
            "event_hash": self.event_hash,
            "source_events": [
                {
                    "source_event_id": item.source_event_id,
                    "source_event_hash": item.source_event_hash,
                }
                for item in self.source_events
            ],
            "maya_proposal_hash": self.maya_proposal_hash,
            "kernel_decision_hash": self.kernel_decision_hash,
            "read_observations": [
                [row_id, _receipt_b64encode(payload), artifact_hash]
                for row_id, payload, artifact_hash in self.read_observations
            ],
            "committed_state_version": self.committed_state_version,
            "committed_state_hash": self.committed_state_hash,
            "public_chunks": [
                [row_id, ordinal, _receipt_b64encode(payload), artifact_hash]
                for row_id, ordinal, payload, artifact_hash in self.public_chunks
            ],
            "command_rows": [list(row) for row in self.command_rows],
            "relay_rows": [list(row) for row in self.relay_rows],
            "internal_outbox_rows": [list(row) for row in self.internal_outbox_rows],
            "uds_transcript_mac": self.uds_transcript_mac,
            "uds_final_seq": self.uds_final_seq,
            "structural_graph_digest": self.structural_graph_digest,
            "capability_policy_digest": self.capability_policy_digest,
            "effective_stage_binding_digest": self.effective_stage_binding_digest,
            "behavior_state_snapshot_digest": self.behavior_state_snapshot_digest,
            "qualification_id": self.qualification_id,
            "admission_sequence": self.admission_sequence,
            "admission_revision": self.admission_revision,
            "commit_fence_token": self.commit_fence_token,
            "allocation_manifest_hash": self.allocation_manifest_hash,
            "immutable_generation": self.immutable_generation,
            "allocation_ids": (
                list(self.allocation_ids) if self.allocation_ids is not None else None
            ),
            "committed_at": _receipt_utc(self.committed_at, "TurnReceipt.committed_at"),
            "previous_turn_receipt_hash": self.previous_turn_receipt_hash,
        }
        if include_artifact_hash:
            data["artifact_hash"] = self.artifact_hash
        return data

    def artifact_preimage_bytes(self) -> bytes:
        return _receipt_json(
            {
                "schema": self.PREIMAGE_SCHEMA,
                "version": self.VERSION,
                "data": self._data(include_artifact_hash=False),
            }
        )

    def to_canonical_bytes(self) -> bytes:
        return _receipt_json(
            {
                "schema": self.SCHEMA,
                "version": self.VERSION,
                "data": self._data(include_artifact_hash=True),
            }
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\x00" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def create(cls, **values: object) -> "TurnReceipt":
        if "artifact_hash" in values:
            raise TypeError("TurnReceipt.create derives artifact_hash")
        return cls(**values)

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "TurnReceipt":
        envelope = _receipt_load_json(payload, "TurnReceipt")
        if set(envelope) != {"schema", "version", "data"}:
            raise ValueError("TurnReceipt envelope fields mismatch")
        if (
            envelope["schema"] != cls.SCHEMA
            or envelope["version"] != cls.VERSION
            or type(envelope["data"]) is not dict
        ):
            raise ValueError("TurnReceipt identity mismatch")
        data = envelope["data"]
        expected = {
            "aggregate_turn_id", "event_hash", "source_events",
            "maya_proposal_hash", "kernel_decision_hash", "read_observations",
            "committed_state_version", "committed_state_hash", "public_chunks",
            "command_rows", "relay_rows", "internal_outbox_rows",
            "uds_transcript_mac", "uds_final_seq", "structural_graph_digest",
            "capability_policy_digest", "effective_stage_binding_digest",
            "behavior_state_snapshot_digest", "qualification_id",
            "admission_sequence", "admission_revision", "commit_fence_token",
            "allocation_manifest_hash", "immutable_generation", "allocation_ids",
            "committed_at", "previous_turn_receipt_hash", "artifact_hash",
        }
        if set(data) != expected:
            raise ValueError("TurnReceipt fields mismatch")
        for sequence_name in (
            "source_events", "read_observations", "public_chunks", "command_rows",
            "relay_rows", "internal_outbox_rows",
        ):
            if type(data[sequence_name]) is not list:
                raise TypeError(f"TurnReceipt.{sequence_name} must be an array")
        if data["allocation_ids"] is not None and type(data["allocation_ids"]) is not list:
            raise TypeError("TurnReceipt.allocation_ids must be an array or null")

        source_events: list[SourceEventIdentity] = []
        for item in data["source_events"]:
            if type(item) is not dict or set(item) != {
                "source_event_id", "source_event_hash"
            }:
                raise ValueError("TurnReceipt source event fields mismatch")
            source_events.append(
                SourceEventIdentity(item["source_event_id"], item["source_event_hash"])
            )

        def triples(rows: list[object], name: str) -> tuple[tuple[str, bytes, str], ...]:
            result: list[tuple[str, bytes, str]] = []
            for row in rows:
                if type(row) is not list or len(row) != 3:
                    raise TypeError(f"TurnReceipt.{name} row must be an array triple")
                result.append(
                    (row[0], _receipt_b64decode(row[1], name), row[2])
                )
            return tuple(result)

        def quadruples(
            rows: list[object], name: str
        ) -> tuple[tuple[str, int, bytes, str], ...]:
            result: list[tuple[str, int, bytes, str]] = []
            for row in rows:
                if type(row) is not list or len(row) != 4:
                    raise TypeError(f"TurnReceipt.{name} row must be an array quadruple")
                result.append(
                    (row[0], row[1], _receipt_b64decode(row[2], name), row[3])
                )
            return tuple(result)

        def pairs(rows: list[object], name: str) -> tuple[tuple[str, str], ...]:
            result: list[tuple[str, str]] = []
            for row in rows:
                if type(row) is not list or len(row) != 2:
                    raise TypeError(f"TurnReceipt.{name} row must be an array pair")
                result.append((row[0], row[1]))
            return tuple(result)

        receipt = cls(
            aggregate_turn_id=data["aggregate_turn_id"],
            event_hash=data["event_hash"],
            source_events=tuple(source_events),
            maya_proposal_hash=data["maya_proposal_hash"],
            kernel_decision_hash=data["kernel_decision_hash"],
            read_observations=triples(data["read_observations"], "read_observations"),
            committed_state_version=data["committed_state_version"],
            committed_state_hash=data["committed_state_hash"],
            public_chunks=quadruples(data["public_chunks"], "public_chunks"),
            command_rows=pairs(data["command_rows"], "command_rows"),
            relay_rows=pairs(data["relay_rows"], "relay_rows"),
            internal_outbox_rows=pairs(
                data["internal_outbox_rows"], "internal_outbox_rows"
            ),
            uds_transcript_mac=data["uds_transcript_mac"],
            uds_final_seq=data["uds_final_seq"],
            structural_graph_digest=data["structural_graph_digest"],
            capability_policy_digest=data["capability_policy_digest"],
            effective_stage_binding_digest=data["effective_stage_binding_digest"],
            behavior_state_snapshot_digest=data["behavior_state_snapshot_digest"],
            qualification_id=data["qualification_id"],
            admission_sequence=data["admission_sequence"],
            admission_revision=data["admission_revision"],
            commit_fence_token=data["commit_fence_token"],
            allocation_manifest_hash=data["allocation_manifest_hash"],
            immutable_generation=data["immutable_generation"],
            allocation_ids=(
                tuple(data["allocation_ids"])
                if data["allocation_ids"] is not None
                else None
            ),
            committed_at=_receipt_parse_utc(data["committed_at"], "committed_at"),
            previous_turn_receipt_hash=data["previous_turn_receipt_hash"],
            artifact_hash=data["artifact_hash"],
        )
        if receipt.to_canonical_bytes() != payload:
            raise ValueError("TurnReceipt is not byte-canonical")
        return receipt


def _command_record(command: object) -> tuple[str, str, str]:
    if type(command) is ReservationCommand:
        wire = dumps_command(command)
        return command.command_id, "reservation", wire
    if type(command) is PaymentSettlementCommand:
        wire = to_phase6_wire_json(command)
        return command.settlement_command_id, "payment_settlement", wire
    raise TypeError("command must be an exact BoundaryCommand member")


def _validate_outbox_bindings(commit: BoundaryCommit) -> None:
    workflow_ids: set[str] = set()
    command_workflows: dict[str, str] = {}
    if commit.state.workflow is not None:
        workflow_id = commit.state.workflow.meta.workflow_id
        workflow_ids.add(workflow_id)
    for payment in commit.state.payments:
        workflow_ids.add(payment.meta.workflow_id)
    for command in commit.commands:
        if type(command) is ReservationCommand:
            command_workflows[command.command_id] = command.workflow_id
        elif type(command) is PaymentSettlementCommand:
            matches = tuple(
                payment
                for payment in commit.state.payments
                if payment.subject.payment_id == command.payment_id
            )
            if len(matches) != 1:
                raise IdentityConflict("payment command does not bind one boundary payment")
            command_workflows[command.settlement_command_id] = matches[0].meta.workflow_id
    for message in commit.outbox:
        if type(message) is not OutboxMessage:
            raise TypeError("outbox must contain exact OutboxMessage values")
        if message.workflow_id not in workflow_ids:
            raise IdentityConflict("outbox does not bind a boundary workflow")
        if message.command_id is not None and command_workflows.get(message.command_id) != message.workflow_id:
            raise IdentityConflict("outbox command does not bind its boundary workflow")


def _authenticate_v8_connection(connection: sqlite3.Connection) -> None:
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    if names != set(BOUNDARY_V8_TABLES):
        raise DataCorruption("SQLite v8 table universe is not exact")
    strict = {
        row[1]: row[5]
        for row in connection.execute("PRAGMA table_list")
        if row[1] in names
    }
    if strict != {name: 1 for name in BOUNDARY_V8_TABLES}:
        raise DataCorruption("SQLite v8 tables are not all STRICT")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise DataCorruption("SQLite v8 foreign keys are disabled")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise DataCorruption("SQLite v8 foreign key violations exist")
    if sqlite_v8_schema_fingerprint(connection) != expected_sqlite_v8_schema_fingerprint():
        raise DataCorruption("SQLite v8 DDL fingerprint is not exact")


class SQLiteBoundaryStore:
    """One in-memory-capable SQLite boundary unit of work."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        _factory_token: object,
        _schema_version: int = 7,
    ) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise TypeError("SQLiteBoundaryStore must be created by a factory")
        if type(_schema_version) is not int or _schema_version not in (7, 8):
            raise TypeError("schema version must be exact 7 or 8")
        self._connection = connection
        self._schema_version = _schema_version
        self._closed = False
        self._savepoint_counter = 0

    @classmethod
    def open_memory(cls) -> "SQLiteBoundaryStore":
        connection = sqlite3.connect(":memory:", isolation_level=None, timeout=5.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(render_sqlite())
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise DataCorruption("SQLite foreign keys are disabled")
            return cls(connection, _factory_token=_FACTORY_TOKEN)
        except BaseException:
            connection.close()
            raise

    @classmethod
    def open_memory_v8(cls) -> "SQLiteBoundaryStore":
        connection = sqlite3.connect(":memory:", isolation_level=None, timeout=5.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(render_sqlite_v8())
            _authenticate_v8_connection(connection)
            return cls(
                connection,
                _factory_token=_FACTORY_TOKEN,
                _schema_version=8,
            )
        except BaseException:
            connection.close()
            raise

    @classmethod
    def open_path_v8(cls, path: Path) -> "SQLiteBoundaryStore":
        if not isinstance(path, Path):
            raise TypeError("path must be an exact pathlib.Path")
        if path.exists() and not path.is_file():
            raise ValueError("SQLite v8 path must be a file or absent")
        connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            connection.execute("PRAGMA synchronous = FULL")
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
            if not names:
                connection.executescript(render_sqlite_v8())
            _authenticate_v8_connection(connection)
            if str(mode).casefold() != "wal":
                raise DataCorruption("SQLite v8 WAL mode is unavailable")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise DataCorruption("SQLite v8 synchronous mode is not FULL")
            return cls(
                connection,
                _factory_token=_FACTORY_TOKEN,
                _schema_version=8,
            )
        except BaseException:
            connection.close()
            raise

    @classmethod
    def open_readonly_v8(cls, path: Path) -> "SQLiteBoundaryStore":
        if not isinstance(path, Path):
            raise TypeError("path must be a pathlib.Path")
        if not path.is_file():
            raise ValueError("read-only SQLite v8 path must be an existing file")
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=5.0,
        )
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            _authenticate_v8_connection(connection)
            return cls(
                connection,
                _factory_token=_FACTORY_TOKEN,
                _schema_version=8,
            )
        except BaseException:
            connection.close()
            raise

    @classmethod
    def open_path(cls, path: Path) -> "SQLiteBoundaryStore":
        if not isinstance(path, Path):
            raise TypeError("path must be an exact pathlib.Path")
        if path.exists() and not path.is_file():
            raise ValueError("SQLite path must be a file or absent")
        connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            connection.execute("PRAGMA synchronous = FULL")
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not names:
                connection.executescript(render_sqlite())
                names = set(TABLE_NAMES)
            if names != set(TABLE_NAMES):
                raise DataCorruption("SQLite table universe is not exact")
            strict = {
                row[1]: row[5]
                for row in connection.execute("PRAGMA table_list")
                if row[1] in names
            }
            if strict != {name: 1 for name in TABLE_NAMES}:
                raise DataCorruption("SQLite tables are not all STRICT")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise DataCorruption("SQLite foreign keys are disabled")
            if str(mode).casefold() != "wal":
                raise DataCorruption("SQLite WAL mode is unavailable")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise DataCorruption("SQLite synchronous mode is not FULL")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise DataCorruption("SQLite foreign key violations exist")
            return cls(connection, _factory_token=_FACTORY_TOKEN)
        except BaseException:
            connection.close()
            raise

    @classmethod
    def open_readonly(cls, path: Path) -> "SQLiteBoundaryStore":
        if not isinstance(path, Path):
            raise TypeError("path must be a pathlib.Path")
        if not path.is_file():
            raise ValueError("read-only SQLite path must be an existing file")
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=5.0,
        )
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if names != set(TABLE_NAMES):
                raise DataCorruption("SQLite table universe is not exact")
            strict = {
                row[1]: row[5]
                for row in connection.execute("PRAGMA table_list")
                if row[1] in names
            }
            if strict != {name: 1 for name in TABLE_NAMES}:
                raise DataCorruption("SQLite tables are not all STRICT")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise DataCorruption("SQLite foreign keys are disabled")
            return cls(connection, _factory_token=_FACTORY_TOKEN)
        except BaseException:
            connection.close()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLiteBoundaryStore is closed")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._ensure_open()
        if self._connection.in_transaction:
            self._savepoint_counter += 1
            savepoint = f"boundary_sp_{self._savepoint_counter}"
            self._connection.execute(f"SAVEPOINT {savepoint}")
            try:
                yield
            except BaseException:
                self._connection.execute(f"ROLLBACK TO {savepoint}")
                self._connection.execute(f"RELEASE {savepoint}")
                raise
            else:
                self._connection.execute(f"RELEASE {savepoint}")
            return
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    @contextmanager
    def turn_transaction(
        self,
        *,
        deadline_guard: Callable[[], None],
    ) -> Iterator[None]:
        if not callable(deadline_guard):
            raise TypeError("deadline_guard must be callable")
        with self._transaction():
            deadline_guard()
            yield
            deadline_guard()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def _versioned_from_row(
        self,
        row: tuple[object, ...],
        *,
        expected_lead_key: str,
    ) -> VersionedBoundaryState:
        version, state_json, state_hash = row
        if type(version) is not int or type(state_json) is not str or type(state_hash) is not str:
            raise DataCorruption("boundary_state row has wrong SQLite types")
        try:
            state = from_wire_json(state_json, BoundaryState)
        except (TypeError, ValueError) as exc:
            raise DataCorruption("boundary state wire is invalid") from exc
        if (
            state.lead_key != expected_lead_key
            or state.version != version
            or semantic_hash(state) != state_hash
        ):
            raise DataCorruption("boundary state identity/hash/version does not bind")
        return VersionedBoundaryState(state, version, state_hash)

    def _load_state_in_transaction(self, lead_key: str) -> VersionedBoundaryState:
        row = self._connection.execute(
            "SELECT version, state_json, state_hash FROM boundary_state WHERE lead_key=?",
            (lead_key,),
        ).fetchone()
        if row is None:
            raise StateNotFound(lead_key)
        return self._versioned_from_row(row, expected_lead_key=lead_key)

    def load_state(self, lead_key: str) -> VersionedBoundaryState:
        self._ensure_open()
        exact_lead_key = _require_id(lead_key, "lead_key")
        return self._load_state_in_transaction(exact_lead_key)

    def event_hash(self, lead_key: str, event_id: str) -> str | None:
        self._ensure_open()
        exact_lead_key = _require_id(lead_key, "lead_key")
        exact_event_id = _require_id(event_id, "event_id")
        row = self._connection.execute(
            "SELECT event_hash FROM boundary_events WHERE lead_key=? AND event_id=?",
            (exact_lead_key, exact_event_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return _require_hash(row[0], "stored event_hash")
        except ValueError as exc:
            raise DataCorruption("stored event hash is invalid") from exc

    def command_is_persisted(
        self,
        *,
        lead_key: str,
        event_id: str,
        command: object,
    ) -> bool:
        self._ensure_open()
        exact_lead_key = _require_id(lead_key, "lead_key")
        exact_event_id = _require_id(event_id, "event_id")
        command_id, command_type, command_json = _command_record(command)
        row = self._connection.execute(
            "SELECT command_type, command_json, command_hash FROM boundary_commands "
            "WHERE lead_key=? AND event_id=? AND command_id=?",
            (exact_lead_key, exact_event_id, command_id),
        ).fetchone()
        return row == (command_type, command_json, _sha(command_json))

    def import_genesis(
        self,
        snapshot: LegacyLeadSnapshot,
        result: ImportResult,
        *,
        claimed_at: datetime,
    ) -> VersionedBoundaryState:
        if type(snapshot) is not LegacyLeadSnapshot:
            raise TypeError("snapshot must be the exact LegacyLeadSnapshot type")
        if type(result) is not ImportResult:
            raise TypeError("result must be the exact ImportResult type")
        if result.disposition is not ImportDisposition.MIGRATED or result.state is None:
            raise ValueError("only a migrated ImportResult can create genesis")
        if result.state.version != 0:
            raise ValueError("genesis boundary state must have version zero")
        lead_key = _require_id(result.state.lead_key, "lead_key")
        if snapshot.raw_fields["lead_key"] != lead_key:
            raise IdentityConflict("snapshot lead_key does not bind imported state")
        snapshot_hash = _require_hash(snapshot.snapshot_hash, "snapshot_hash")
        state_json = to_wire_json(result.state)
        state_hash = semantic_hash(result.state)
        instant = _utc_text(claimed_at, "claimed_at")

        try:
            with self._transaction():
                claim = self._connection.execute(
                    "SELECT snapshot_hash, state_hash FROM legacy_import_claims WHERE lead_key=?",
                    (lead_key,),
                ).fetchone()
                if claim is not None:
                    if claim != (snapshot_hash, state_hash):
                        raise IdentityConflict("legacy genesis claim diverged")
                    return self._load_state_in_transaction(lead_key)
                if self._connection.execute(
                    "SELECT 1 FROM boundary_state WHERE lead_key=?", (lead_key,)
                ).fetchone() is not None:
                    raise IdentityConflict("boundary state exists without matching import claim")
                self._connection.execute(
                    "INSERT INTO boundary_state "
                    "(lead_key, version, state_json, state_hash, fencing_token, created_at, updated_at) "
                    "VALUES (?, 0, ?, ?, 0, ?, ?)",
                    (lead_key, state_json, state_hash, instant, instant),
                )
                self._connection.execute(
                    "INSERT INTO legacy_import_claims "
                    "(lead_key, snapshot_hash, disposition, state_hash, claimed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        lead_key,
                        snapshot_hash,
                        result.disposition.value,
                        state_hash,
                        instant,
                    ),
                )
                return VersionedBoundaryState(result.state, 0, state_hash)
        except sqlite3.IntegrityError as exc:
            raise IdentityConflict("genesis violated durable identity") from exc

    def acquire_fence(self, lead_key: str) -> tuple[VersionedBoundaryState, int]:
        exact_lead_key = _require_id(lead_key, "lead_key")
        with self._transaction():
            row = self._connection.execute(
                "SELECT version, state_json, state_hash, fencing_token "
                "FROM boundary_state WHERE lead_key=?",
                (exact_lead_key,),
            ).fetchone()
            if row is None:
                raise StateNotFound(exact_lead_key)
            token = _require_int(row[3], "stored fencing_token", minimum=0) + 1
            updated = self._connection.execute(
                "UPDATE boundary_state SET fencing_token=? "
                "WHERE lead_key=? AND fencing_token=?",
                (token, exact_lead_key, row[3]),
            ).rowcount
            if updated != 1:
                raise ConcurrencyConflict("fencing token changed concurrently")
            return (
                self._versioned_from_row(
                    row[:3],
                    expected_lead_key=exact_lead_key,
                ),
                token,
            )

    def commit(
        self,
        *,
        event_id: str,
        event_hash: str,
        expected_version: int,
        fencing_token: int,
        commit: BoundaryCommit,
        committed_at: datetime,
        fault_hook: Callable[[str], None] | None = None,
    ) -> VersionedBoundaryState:
        exact_event_id = _require_id(event_id, "event_id")
        exact_event_hash = _require_hash(event_hash, "event_hash")
        expected = _require_int(expected_version, "expected_version", minimum=0)
        token = _require_int(fencing_token, "fencing_token", minimum=1)
        if type(commit) is not BoundaryCommit:
            raise TypeError("commit must be the exact BoundaryCommit type")
        if commit.facts:
            raise ValueError("facts must be reduced into state before persistence")
        if fault_hook is not None and not callable(fault_hook):
            raise TypeError("fault_hook must be callable or None")
        to_wire_json(commit)
        lead_key = _require_id(commit.state.lead_key, "commit.state.lead_key")
        instant = _utc_text(committed_at, "committed_at")
        state_json = to_wire_json(commit.state)
        state_hash = semantic_hash(commit.state)
        commit_hash = semantic_hash(commit)
        _validate_outbox_bindings(commit)

        def fault(stage: str) -> None:
            if fault_hook is not None:
                fault_hook(stage)

        try:
            with self._transaction():
                existing_event = self._connection.execute(
                    "SELECT event_hash, commit_hash, state_version FROM boundary_events "
                    "WHERE lead_key=? AND event_id=?",
                    (lead_key, exact_event_id),
                ).fetchone()
                if existing_event is not None:
                    if existing_event != (exact_event_hash, commit_hash, commit.state.version):
                        raise IdentityConflict("event_id replay diverged from the durable commit")
                    return self._load_state_in_transaction(lead_key)
                if commit.state.version != expected + 1:
                    raise ValueError("commit state version must equal expected_version + 1")
                row = self._connection.execute(
                    "SELECT version, fencing_token FROM boundary_state WHERE lead_key=?",
                    (lead_key,),
                ).fetchone()
                if row is None:
                    raise StateNotFound(lead_key)
                if row != (expected, token):
                    raise ConcurrencyConflict("state version or fencing token is stale")
                updated = self._connection.execute(
                    "UPDATE boundary_state SET version=?, state_json=?, state_hash=?, updated_at=? "
                    "WHERE lead_key=? AND version=? AND fencing_token=?",
                    (
                        commit.state.version,
                        state_json,
                        state_hash,
                        instant,
                        lead_key,
                        expected,
                        token,
                    ),
                ).rowcount
                if updated != 1:
                    raise ConcurrencyConflict("state CAS lost")
                fault("after_state_update")
                self._connection.execute(
                    "INSERT INTO boundary_events "
                    "(lead_key, event_id, event_hash, commit_hash, state_version, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        lead_key,
                        exact_event_id,
                        exact_event_hash,
                        commit_hash,
                        commit.state.version,
                        instant,
                    ),
                )
                fault("after_event_insert")
                for command in commit.commands:
                    command_id, command_type, command_json = _command_record(command)
                    if type(command) is ReservationCommand:
                        if commit.state.workflow is None or command.workflow_id != commit.state.workflow.meta.workflow_id:
                            raise IdentityConflict("reservation command does not bind boundary workflow")
                    self._connection.execute(
                        "INSERT INTO boundary_commands "
                        "(command_id, lead_key, event_id, command_type, command_json, command_hash, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            command_id,
                            lead_key,
                            exact_event_id,
                            command_type,
                            command_json,
                            _sha(command_json),
                            instant,
                        ),
                    )
                    fault("after_command_insert")
                for message in commit.outbox:
                    self._connection.execute(
                        "INSERT INTO boundary_outbox "
                        "(message_id, idempotency_key, lead_key, event_id, workflow_id, command_id, "
                        "kind, template_id, payload_json, payload_hash, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            message.message_id,
                            message.idempotency_key,
                            lead_key,
                            exact_event_id,
                            message.workflow_id,
                            message.command_id,
                            message.kind.value,
                            message.template_id,
                            message.canonical_payload,
                            message.payload_hash,
                            _utc_text(message.created_at, "message.created_at"),
                        ),
                    )
                    fault("after_outbox_insert")
                return VersionedBoundaryState(
                    commit.state,
                    commit.state.version,
                    state_hash,
                )
        except sqlite3.IntegrityError as exc:
            raise IdentityConflict("boundary commit violated durable identity") from exc


__all__ = (
    "BoundaryStoreError",
    "ConcurrencyConflict",
    "DataCorruption",
    "IdentityConflict",
    "LegacyStateReadPort",
    "SQLiteBoundaryStore",
    "StateNotFound",
    "TurnReceipt",
)
