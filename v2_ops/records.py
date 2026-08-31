from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
from typing import Iterable, Mapping, Sequence
from urllib.parse import quote


_SOURCE_NAMES = (
    "inbox.sqlite3",
    "v2-bokun-audit.sqlite3",
    "v2-boundary.sqlite3",
    "v2-cloudbeds-audit.sqlite3",
    "v2-execution.sqlite3",
    "v2-followup.sqlite3",
    "v2-payment-initiation.sqlite3",
    "v2-private-customer.sqlite3",
    "v2-public-outbox.sqlite3",
)

# Every identifier below is static application code. No table or column name comes
# from a request, environment value, database row, or JSON payload.
_SOURCE_TABLES: dict[str, dict[str, tuple[tuple[str, str], ...]]] = {
    "inbox.sqlite3": {
        "inbound_events": (
            ("event_id", "TEXT"),
            ("lead_id", "TEXT"),
            ("occurred_at", "TEXT"),
            ("status", "TEXT"),
            ("completed_at", "TEXT"),
        ),
    },
    "v2-private-customer.sqlite3": {
        "private_customer_facts": (
            ("lead_id", "TEXT"),
            ("fact_name", "TEXT"),
            ("private_value", "TEXT"),
            ("revision", "INTEGER"),
            ("persisted_at", "TEXT"),
        ),
        "private_dialogue_turns": (
            ("lead_id", "TEXT"),
            ("source_turn_id", "TEXT"),
            ("customer_message", "TEXT"),
            ("assistant_reply_chunks_json", "TEXT"),
            ("committed_at", "TEXT"),
        ),
        "private_passenger_manifests": (
            ("lead_id", "TEXT"),
            ("fact_json", "TEXT"),
            ("revision", "INTEGER"),
            ("persisted_at", "TEXT"),
        ),
    },
    "v2-boundary.sqlite3": {
        "boundary_state": (
            ("lead_key", "TEXT"),
            ("state_json", "TEXT"),
            ("updated_at", "TEXT"),
        ),
        "boundary_commands": (
            ("command_id", "TEXT"),
            ("lead_key", "TEXT"),
            ("command_type", "TEXT"),
            ("command_json", "TEXT"),
            ("created_at", "TEXT"),
        ),
        "boundary_public_outbox": (
            ("public_row_id", "TEXT"),
            ("lead_key", "TEXT"),
            ("chunk_index", "INTEGER"),
            ("chunk_json", "TEXT"),
            ("status", "TEXT"),
            ("updated_at", "TEXT"),
        ),
    },
    "v2-execution.sqlite3": {
        "reservation_commands": (
            ("command_id", "TEXT"),
            ("workflow_id", "TEXT"),
            ("draft_id", "TEXT"),
            ("draft_version", "INTEGER"),
            ("operation", "TEXT"),
            ("command_json", "TEXT"),
            ("created_at", "TEXT"),
        ),
        "workflows": (
            ("workflow_id", "TEXT"),
            ("state_type", "TEXT"),
            ("state_json", "TEXT"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
        ),
        "execution_ledger": (
            ("command_id", "TEXT"),
            ("status", "TEXT"),
            ("outcome_json", "TEXT"),
            ("updated_at", "TEXT"),
        ),
    },
    "v2-payment-initiation.sqlite3": {
        "payment_initiations": (
            ("initiation_id", "TEXT"),
            ("selection_json", "BLOB"),
            ("status", "TEXT"),
            ("updated_at", "TEXT"),
        ),
        "stripe_reconciliations": (
            ("initiation_id", "TEXT"),
            ("status", "TEXT"),
            ("updated_at", "TEXT"),
        ),
        "stripe_step_receipts": (
            ("initiation_id", "TEXT"),
            ("step", "TEXT"),
            ("status", "TEXT"),
            ("updated_at", "TEXT"),
        ),
    },
    "v2-followup.sqlite3": {
        "payment_workflows": (
            ("payment_id", "TEXT"),
            ("status", "TEXT"),
            ("state_json", "TEXT"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
        ),
        "payment_events": (
            ("event_id", "TEXT"),
            ("payment_id", "TEXT"),
            ("event_type", "TEXT"),
            ("occurred_at", "TEXT"),
        ),
        "payment_commands": (
            ("settlement_command_id", "TEXT"),
            ("payment_id", "TEXT"),
            ("operation", "TEXT"),
            ("created_at", "TEXT"),
        ),
        "payment_ledger": (
            ("settlement_command_id", "TEXT"),
            ("payment_id", "TEXT"),
            ("status", "TEXT"),
            ("outcome_certainty", "TEXT"),
            ("outcome_json", "TEXT"),
            ("outcome_recorded_at", "TEXT"),
            ("updated_at", "TEXT"),
        ),
        "payment_receipts": (
            ("receipt_id", "TEXT"),
            ("message_id", "TEXT"),
            ("delivered_at", "TEXT"),
        ),
        "handoff_workflows": (
            ("handoff_id", "TEXT"),
            ("incident_key", "TEXT"),
            ("status", "TEXT"),
            ("state_json", "TEXT"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
        ),
        "handoff_events": (
            ("event_id", "TEXT"),
            ("handoff_id", "TEXT"),
            ("event_type", "TEXT"),
            ("occurred_at", "TEXT"),
        ),
        "handoff_receipts": (
            ("receipt_id", "TEXT"),
            ("message_id", "TEXT"),
            ("delivered_at", "TEXT"),
        ),
    },
    "v2-bokun-audit.sqlite3": {
        "bokun_audit_tasks": (
            ("task_id", "TEXT"),
            ("command_id", "TEXT"),
            ("booking_id", "TEXT"),
            ("status", "TEXT"),
        ),
    },
    "v2-cloudbeds-audit.sqlite3": {
        "cloudbeds_audit_tasks": (
            ("task_id", "TEXT"),
            ("command_id", "TEXT"),
            ("reservation_id", "TEXT"),
            ("status", "TEXT"),
        ),
    },
    "v2-public-outbox.sqlite3": {
        "public_outbox": (
            ("outbox_id", "TEXT"),
            ("lead_id", "TEXT"),
            ("chunk_index", "INTEGER"),
            ("text", "TEXT"),
            ("status", "TEXT"),
            ("updated_at", "TEXT"),
            ("author", "TEXT"),
        ),
    },
}


class RecordsSourceError(RuntimeError):
    """Sanitized failure while reading the fixed commercial SQLite family."""

    def __init__(self) -> None:
        super().__init__("records source unavailable")


@dataclass(frozen=True, slots=True)
class ExecutionLink:
    execution_id: str
    lead_id: str
    received_at: datetime
    completed_at: datetime | None
    status: str

    def __post_init__(self) -> None:
        for name in ("execution_id", "lead_id", "status"):
            _require_text(getattr(self, name), name, maximum=256)
        object.__setattr__(self, "received_at", _require_utc(self.received_at, "received_at"))
        if self.completed_at is not None:
            object.__setattr__(
                self,
                "completed_at",
                _require_utc(self.completed_at, "completed_at"),
            )


@dataclass(frozen=True, slots=True)
class RecordsSummary:
    lead_count: int
    execution_count: int
    reservation_count: int
    confirmed_reservation_count: int
    payment_initiation_count: int
    settled_payment_count: int
    handoff_count: int
    payment_receipt_count: int
    handoff_receipt_count: int


@dataclass(frozen=True, slots=True)
class LeadSummary:
    lead_id: str
    first_activity_at: str | None
    last_activity_at: str | None
    state_code: str
    state_label: str
    fact_count: int
    dialogue_turn_count: int
    passenger_manifest_count: int
    inbound_count: int
    public_reply_count: int
    execution_count: int
    reservation_count: int
    payment_count: int
    payment_initiation_count: int
    settled_payment_count: int
    handoff_count: int
    latest_execution_status: str | None


@dataclass(frozen=True, slots=True)
class CustomerFactRecord:
    lead_id: str
    name: str
    value: str
    revision: int
    persisted_at: str


@dataclass(frozen=True, slots=True)
class DialogueTurnRecord:
    lead_id: str
    source_turn_id: str
    customer_message: str
    assistant_reply_chunks: tuple[str, ...]
    committed_at: str


@dataclass(frozen=True, slots=True)
class PassengerManifestRecord:
    lead_id: str
    manifest_json: str
    revision: int
    persisted_at: str


@dataclass(frozen=True, slots=True)
class PassengerRecord:
    position: int
    participant_type: str
    full_name: str | None
    birth_date: str | None
    gender: str | None
    country_code: str | None


@dataclass(frozen=True, slots=True)
class PassengerManifestSummary:
    revision: int
    persisted_at: str
    adults: int
    children: int
    passengers: tuple[PassengerRecord, ...]


@dataclass(frozen=True, slots=True)
class InboundEventRecord:
    event_id: str
    lead_id: str
    status: str
    occurred_at: str
    completed_at: str | None
    payload_exposed: bool = False


@dataclass(frozen=True, slots=True)
class PublicReplyRecord:
    reply_id: str
    lead_id: str
    source: str
    chunk_index: int
    text: str
    status: str
    author: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ReservationCustomer:
    customer_ref: str | None
    full_name: str | None
    email: str | None
    phone_e164: str | None
    country_code: str | None
    birth_date: str | None
    gender: str | None


@dataclass(frozen=True, slots=True)
class ReservationComponent:
    service: str
    public_label: str
    start_date: str
    start_time: str | None
    end_date: str | None
    adults: int
    children: int
    available: bool
    lookup_id: str | None
    offer_id: str | None
    provider_ref: str | None
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class ReservationRecord:
    lead_id: str | None
    command_id: str
    workflow_id: str
    draft_id: str
    draft_version: int
    operation: str
    status_code: str
    status_label: str
    certainty: str | None
    normalized_status: str | None
    provider_reference: str | None
    bokun_booking_id: str | None
    cloudbeds_reservation_id: str | None
    total_minor: int | None
    currency: str | None
    payment_method: str | None
    customer: ReservationCustomer
    components: tuple[ReservationComponent, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PaymentStepRecord:
    step: str
    status: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    record_id: str
    lead_id: str | None
    phase: str
    payment_id: str
    initiation_id: str | None
    settlement_command_id: str | None
    reservation_anchor_id: str | None
    method: str | None
    amount_due_minor: int | None
    amount_paid_minor: int | None
    currency: str | None
    due_kind: str | None
    status_code: str
    status_label: str
    settled: bool
    workflow_status: str | None
    ledger_status: str | None
    outcome_certainty: str | None
    reconciliation_status: str | None
    payment_link_prepared: bool
    steps: tuple[PaymentStepRecord, ...]
    settled_at: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class HandoffRecord:
    lead_id: str | None
    handoff_id: str
    incident_key: str
    reason_code: str | None
    status_code: str
    status_label: str
    event_count: int
    receipt_count: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class RecordsSnapshot:
    generated_at: datetime
    leads: tuple[LeadSummary, ...]
    reservations: tuple[ReservationRecord, ...]
    payments: tuple[PaymentRecord, ...]
    handoffs: tuple[HandoffRecord, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class LeadDetail:
    generated_at: datetime
    summary: LeadSummary
    facts: tuple[CustomerFactRecord, ...]
    dialogue_turns: tuple[DialogueTurnRecord, ...]
    passenger_manifests: tuple[PassengerManifestSummary, ...]
    inbound_events: tuple[InboundEventRecord, ...]
    public_replies: tuple[PublicReplyRecord, ...]
    reservations: tuple[ReservationRecord, ...]
    payments: tuple[PaymentRecord, ...]
    handoffs: tuple[HandoffRecord, ...]
    executions: tuple[ExecutionLink, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class _ProjectedRecords:
    summary: RecordsSummary
    leads: tuple[LeadSummary, ...]
    facts: tuple[CustomerFactRecord, ...]
    dialogue_turns: tuple[DialogueTurnRecord, ...]
    passenger_manifests: tuple[PassengerManifestRecord, ...]
    inbound_events: tuple[InboundEventRecord, ...]
    public_replies: tuple[PublicReplyRecord, ...]
    reservations: tuple[ReservationRecord, ...]
    payments: tuple[PaymentRecord, ...]
    handoffs: tuple[HandoffRecord, ...]


@dataclass(frozen=True, slots=True)
class _RawSources:
    rows: Mapping[tuple[str, str], tuple[sqlite3.Row, ...]]

    def table(self, source: str, table: str) -> tuple[sqlite3.Row, ...]:
        return self.rows[(source, table)]


def _require_text(value: object, name: str, *, maximum: int = 4096) -> str:
    if type(value) is not str or not value or len(value) > maximum or "\x00" in value:
        raise ValueError(f"invalid {name}")
    return value


def _optional_text(value: object, name: str, *, maximum: int = 4096) -> str | None:
    if value is None:
        return None
    return _require_text(value, name, maximum=maximum)


def _optional_identifier(value: object, name: str, *, maximum: int = 256) -> str | None:
    if type(value) is not str:
        raise ValueError(f"invalid {name}")
    if not value.strip():
        return None
    if value != value.strip():
        raise ValueError(f"invalid {name}")
    return _require_text(value, name, maximum=maximum)


def _require_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"invalid {name}")
    return value


def _require_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"invalid {name}")
    return value


def _require_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"invalid {name}")
    return value.astimezone(timezone.utc)


def _timestamp(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _require_text(value, name, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"invalid {name}")
    return parsed.astimezone(timezone.utc).isoformat()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite number: {value}")


def _json_value(raw: object, name: str) -> object:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    text = _require_text(raw, name, maximum=1_048_576)
    return json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_nonfinite,
    )


def _json_object(raw: object, name: str) -> dict[str, object]:
    value = _json_value(raw, name)
    if type(value) is not dict:
        raise ValueError(f"invalid {name}")
    return value


def _object(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"invalid {name}")
    return value


def _array(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise ValueError(f"invalid {name}")
    return value


def _wire_data(payload: dict[str, object], name: str, *, expected_type: str | None = None) -> dict[str, object]:
    if set(payload) != {"schema_version", "type", "data"}:
        raise ValueError(f"invalid {name}")
    if payload["schema_version"] != 1:
        raise ValueError(f"invalid {name}")
    wire_type = _require_text(payload["type"], f"{name}.type", maximum=128)
    if expected_type is not None and wire_type != expected_type:
        raise ValueError(f"invalid {name}")
    return _object(payload["data"], f"{name}.data")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def passenger_manifest_summary(value: PassengerManifestRecord) -> PassengerManifestSummary:
    try:
        decoded = _json_object(value.manifest_json, "passenger manifest")
        if (
            set(decoded) != {"schema", "adults", "children", "passengers"}
            or decoded["schema"] != "v2-passenger-manifest-v1"
            or type(decoded["adults"]) is not int
            or type(decoded["children"]) is not int
            or decoded["adults"] < 1
            or decoded["children"] < 0
            or type(decoded["passengers"]) is not list
            or len(decoded["passengers"]) != decoded["adults"] + decoded["children"]
        ):
            raise ValueError("passenger manifest contract")
        passengers: list[PassengerRecord] = []
        expected_fields = {
            "position",
            "participant_type",
            "full_name",
            "birth_date",
            "gender",
            "country_code",
        }
        for expected_position, passenger in enumerate(decoded["passengers"], start=1):
            if (
                type(passenger) is not dict
                or set(passenger) != expected_fields
                or passenger["position"] != expected_position
                or passenger["participant_type"] not in {"adult", "child"}
                or any(
                    passenger[name] is not None and type(passenger[name]) is not str
                    for name in ("full_name", "birth_date", "gender", "country_code")
                )
            ):
                raise ValueError("passenger contract")
            passengers.append(
                PassengerRecord(
                    position=passenger["position"],
                    participant_type=passenger["participant_type"],
                    full_name=passenger["full_name"],
                    birth_date=passenger["birth_date"],
                    gender=passenger["gender"],
                    country_code=passenger["country_code"],
                )
            )
        return PassengerManifestSummary(
            revision=value.revision,
            persisted_at=value.persisted_at,
            adults=decoded["adults"],
            children=decoded["children"],
            passengers=tuple(passengers),
        )
    except RecordsSourceError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise RecordsSourceError() from exc


def passenger_manifest_public(
    value: PassengerManifestRecord | PassengerManifestSummary,
) -> dict[str, object]:
    summary = (
        passenger_manifest_summary(value)
        if type(value) is PassengerManifestRecord
        else value
    )
    if type(summary) is not PassengerManifestSummary:
        raise RecordsSourceError()
    return {
        "revision": summary.revision,
        "persisted_at": summary.persisted_at,
        "adults": summary.adults,
        "children": summary.children,
        "passengers": [
            {
                "position": passenger.position,
                "participant_type": passenger.participant_type,
                "full_name": passenger.full_name,
                "birth_date": passenger.birth_date,
                "gender": passenger.gender,
                "country_code": passenger.country_code,
            }
            for passenger in summary.passengers
        ],
    }


def _minor_amount(value: object, name: str) -> int:
    text = _require_text(value, name, maximum=64)
    try:
        decimal_value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {name}") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValueError(f"invalid {name}")
    minor = decimal_value * 100
    if minor != minor.to_integral_value():
        raise ValueError(f"invalid {name}")
    return int(minor)


def _latest(*values: str | None) -> str | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _reply_text(payload: dict[str, object]) -> str:
    data = payload.get("data")
    if type(data) is dict and type(data.get("text")) is str:
        return _require_text(data["text"], "reply text", maximum=65536)
    if type(payload.get("text")) is str:
        return _require_text(payload["text"], "reply text", maximum=65536)
    raise ValueError("invalid reply text")


def _status_label(code: str, labels: Mapping[str, str], fallback_prefix: str) -> str:
    return labels.get(code, f"{fallback_prefix}: {code}")


class SQLiteRecordsReader:
    """Closed, projection-only reader for the active commercial SQLite family."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("records root must be an absolute pathlib.Path")
        self._root = root

    @property
    def allowed_files(self) -> frozenset[str]:
        return frozenset(_SOURCE_NAMES)

    def snapshot(
        self,
        *,
        executions: Sequence[ExecutionLink] = (),
        generated_at: datetime,
        limit: int = 200,
    ) -> RecordsSnapshot:
        try:
            links = self._execution_links(executions)
            self._validate_generated_at_and_limit(generated_at, limit)
            projected = self._consistent_projection(links)
            return RecordsSnapshot(
                generated_at=generated_at,
                leads=projected.leads[:limit],
                reservations=projected.reservations[:limit],
                payments=projected.payments[:limit],
                handoffs=projected.handoffs[:limit],
                truncated=any(
                    len(values) > limit
                    for values in (
                        projected.leads,
                        projected.reservations,
                        projected.payments,
                        projected.handoffs,
                    )
                ),
            )
        except RecordsSourceError:
            raise
        except (
            OSError,
            sqlite3.Error,
            TypeError,
            ValueError,
            UnicodeError,
            InvalidOperation,
        ) as exc:
            raise RecordsSourceError() from exc

    def lead_detail(
        self,
        lead_id: str,
        *,
        executions: Sequence[ExecutionLink] = (),
        generated_at: datetime,
        limit: int = 200,
    ) -> LeadDetail | None:
        try:
            exact_lead_id = _require_text(lead_id, "lead id", maximum=256)
            links = self._execution_links(executions)
            self._validate_generated_at_and_limit(generated_at, limit)
            projected = self._consistent_projection(links)
            summary = next(
                (value for value in projected.leads if value.lead_id == exact_lead_id),
                None,
            )
            if summary is None:
                return None

            def matching(values: Sequence[object]) -> tuple[object, ...]:
                return tuple(
                    value
                    for value in values
                    if getattr(value, "lead_id", None) == exact_lead_id
                )

            facts = matching(projected.facts)
            turns = matching(projected.dialogue_turns)
            raw_manifests = matching(projected.passenger_manifests)
            if any(type(value) is not PassengerManifestRecord for value in raw_manifests):
                raise ValueError("invalid passenger manifest projection")
            manifests = tuple(
                passenger_manifest_summary(value)
                for value in raw_manifests
            )
            inbound = matching(projected.inbound_events)
            replies = matching(projected.public_replies)
            reservations = matching(projected.reservations)
            payments = matching(projected.payments)
            handoffs = matching(projected.handoffs)
            exact_executions = tuple(
                value for value in links if value.lead_id == exact_lead_id
            )
            collections = (
                facts,
                turns,
                manifests,
                inbound,
                replies,
                reservations,
                payments,
                handoffs,
                exact_executions,
            )
            return LeadDetail(
                generated_at=generated_at,
                summary=summary,
                facts=tuple(facts[:limit]),
                dialogue_turns=tuple(turns[:limit]),
                passenger_manifests=tuple(manifests[:limit]),
                inbound_events=tuple(inbound[:limit]),
                public_replies=tuple(replies[:limit]),
                reservations=tuple(reservations[:limit]),
                payments=tuple(payments[:limit]),
                handoffs=tuple(handoffs[:limit]),
                executions=exact_executions[:limit],
                truncated=any(len(values) > limit for values in collections),
            )
        except RecordsSourceError:
            raise
        except (
            OSError,
            sqlite3.Error,
            TypeError,
            ValueError,
            UnicodeError,
            InvalidOperation,
        ) as exc:
            raise RecordsSourceError() from exc

    @staticmethod
    def _execution_links(executions: Sequence[ExecutionLink]) -> tuple[ExecutionLink, ...]:
        links = tuple(executions)
        if any(type(link) is not ExecutionLink for link in links):
            raise ValueError("execution links must be exact ExecutionLink values")
        return links

    @staticmethod
    def _validate_generated_at_and_limit(generated_at: datetime, limit: int) -> None:
        if type(generated_at) is not datetime or generated_at.tzinfo is not timezone.utc:
            raise ValueError("generated_at must be an exact UTC datetime")
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise ValueError("limit is outside the closed range")

    def _consistent_projection(
        self,
        links: tuple[ExecutionLink, ...],
    ) -> _ProjectedRecords:
        for _attempt in range(3):
            before_token = self.change_token()
            raw = self._read_sources()
            after_token = self.change_token()
            if before_token == after_token:
                return self._project(raw, links)
        raise RecordsSourceError()

    def change_token(self) -> str:
        try:
            fingerprint = self._source_fingerprint()
            return hashlib.sha256(
                b"v2-ops-records-files-v1\0"
                + _canonical_json(fingerprint).encode("utf-8")
            ).hexdigest()
        except RecordsSourceError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise RecordsSourceError() from exc

    def _validated_root(self) -> Path:
        root = self._root
        if root.is_symlink() or not root.exists() or not root.is_dir():
            raise RecordsSourceError()
        resolved = root.resolve(strict=True)
        if resolved != root:
            raise RecordsSourceError()
        return resolved

    def _validated_source_path(self, path: Path, *, required: bool) -> os.stat_result | None:
        root = self._validated_root()
        if path.parent != root:
            raise RecordsSourceError()
        if not os.path.lexists(path):
            if required:
                raise RecordsSourceError()
            return None
        if path.is_symlink():
            raise RecordsSourceError()
        details = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(details.st_mode) or path.resolve(strict=True) != path:
            raise RecordsSourceError()
        return details

    def _source_fingerprint(self) -> tuple[tuple[object, ...], ...]:
        root = self._validated_root()
        values: list[tuple[object, ...]] = []
        for source_name in _SOURCE_NAMES:
            source = root / source_name
            details = self._validated_source_path(source, required=True)
            if details is None:
                raise RecordsSourceError()
            values.append(
                (
                    source_name,
                    details.st_dev,
                    details.st_ino,
                    details.st_size,
                    details.st_mtime_ns,
                )
            )
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{source}{suffix}")
                sidecar_details = self._validated_source_path(sidecar, required=False)
                if sidecar_details is not None:
                    values.append(
                        (
                            f"{source_name}{suffix}",
                            sidecar_details.st_dev,
                            sidecar_details.st_ino,
                            sidecar_details.st_size,
                            sidecar_details.st_mtime_ns,
                        )
                    )
        return tuple(values)

    def _connection(self, path: Path, *, source: bool) -> sqlite3.Connection:
        if source:
            if self._validated_source_path(path, required=True) is None:
                raise RecordsSourceError()
        elif path.is_symlink() or not path.is_file():
            raise RecordsSourceError()
        uri = f"file:{quote(str(path), safe='/')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.25)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA busy_timeout=250")
            query_only = connection.execute("PRAGMA query_only").fetchone()
            if query_only is None or query_only[0] != 1:
                raise RecordsSourceError()
            attached = connection.execute("PRAGMA database_list").fetchall()
            if len(attached) != 1 or Path(attached[0][2]).resolve(strict=True) != path:
                raise RecordsSourceError()
            return connection
        except BaseException:
            connection.close()
            raise

    def _copy_sources(self, destination: Path) -> None:
        root = self._validated_root()
        for source_name in _SOURCE_NAMES:
            source = root / source_name
            if self._validated_source_path(source, required=True) is None:
                raise RecordsSourceError()
            shutil.copyfile(source, destination / source_name)
            wal = Path(f"{source}-wal")
            if self._validated_source_path(wal, required=False) is not None:
                shutil.copyfile(wal, destination / f"{source_name}-wal")
            self._validated_source_path(Path(f"{source}-shm"), required=False)
            if os.path.lexists(Path(f"{source}-journal")):
                raise RecordsSourceError()

    def _read_sources(self) -> _RawSources:
        rows: dict[tuple[str, str], tuple[sqlite3.Row, ...]] = {}
        with tempfile.TemporaryDirectory(prefix="v2-ops-records-") as directory:
            copied_root = Path(directory)
            self._copy_sources(copied_root)
            for source_name in _SOURCE_NAMES:
                path = copied_root / source_name
                connection = self._connection(path, source=False)
                try:
                    connection.execute("BEGIN")
                    for table, expected_columns in _SOURCE_TABLES[source_name].items():
                        object_rows = connection.execute(
                            "SELECT type FROM sqlite_schema WHERE name=?",
                            (table,),
                        ).fetchall()
                        if len(object_rows) != 1 or object_rows[0][0] != "table":
                            raise RecordsSourceError()
                        live_rows = connection.execute(
                            f'PRAGMA table_info("{table}")'
                        ).fetchall()
                        live = {str(row[1]): str(row[2]).upper() for row in live_rows}
                        if any(
                            live.get(name) != declared for name, declared in expected_columns
                        ):
                            raise RecordsSourceError()
                        column_sql = ",".join(
                            f'"{name}"' for name, _ in expected_columns
                        )
                        selected = connection.execute(
                            f'SELECT {column_sql} FROM "{table}"'
                        ).fetchall()
                        rows[(source_name, table)] = tuple(selected)
                finally:
                    if connection.in_transaction:
                        connection.rollback()
                    connection.close()
        return _RawSources(rows=rows)

    def _project(
        self,
        raw: _RawSources,
        executions: tuple[ExecutionLink, ...],
    ) -> _ProjectedRecords:
        facts = self._facts(raw)
        turns = self._dialogue_turns(raw)
        manifests = self._passenger_manifests(raw)
        inbound = self._inbound_events(raw)
        boundary_states = self._boundary_states(raw)
        replies = self._public_replies(raw)
        reservations = self._reservations(raw)
        payments = self._payments(raw, reservations)
        handoffs = self._handoffs(raw, boundary_states)
        leads = self._leads(
            facts=facts,
            turns=turns,
            manifests=manifests,
            inbound=inbound,
            boundary_states=boundary_states,
            replies=replies,
            reservations=reservations,
            payments=payments,
            handoffs=handoffs,
            executions=executions,
            raw=raw,
        )
        summary = RecordsSummary(
            lead_count=len(leads),
            execution_count=len(executions),
            reservation_count=len(reservations),
            confirmed_reservation_count=sum(
                item.status_code == "confirmed" for item in reservations
            ),
            payment_initiation_count=sum(
                item.phase == "initiation" for item in payments
            ),
            settled_payment_count=sum(
                item.phase == "settlement" and item.settled for item in payments
            ),
            handoff_count=len(handoffs),
            payment_receipt_count=len(
                raw.table("v2-followup.sqlite3", "payment_receipts")
            ),
            handoff_receipt_count=len(
                raw.table("v2-followup.sqlite3", "handoff_receipts")
            ),
        )
        return _ProjectedRecords(
            summary=summary,
            leads=leads,
            facts=facts,
            dialogue_turns=turns,
            passenger_manifests=manifests,
            inbound_events=inbound,
            public_replies=replies,
            reservations=reservations,
            payments=payments,
            handoffs=handoffs,
        )

    def _facts(self, raw: _RawSources) -> tuple[CustomerFactRecord, ...]:
        result: list[CustomerFactRecord] = []
        for row in raw.table("v2-private-customer.sqlite3", "private_customer_facts"):
            result.append(
                CustomerFactRecord(
                    lead_id=_require_text(row["lead_id"], "fact lead_id", maximum=256),
                    name=_require_text(row["fact_name"], "fact name", maximum=128),
                    value=_require_text(row["private_value"], "fact value", maximum=65536),
                    revision=_require_int(row["revision"], "fact revision", minimum=1),
                    persisted_at=_timestamp(row["persisted_at"], "fact persisted_at"),
                )
            )
        return tuple(sorted(result, key=lambda item: (item.lead_id, item.name)))

    def _dialogue_turns(self, raw: _RawSources) -> tuple[DialogueTurnRecord, ...]:
        result: list[DialogueTurnRecord] = []
        for row in raw.table("v2-private-customer.sqlite3", "private_dialogue_turns"):
            chunks = _array(
                _json_value(row["assistant_reply_chunks_json"], "assistant reply chunks"),
                "assistant reply chunks",
            )
            result.append(
                DialogueTurnRecord(
                    lead_id=_require_text(row["lead_id"], "turn lead_id", maximum=256),
                    source_turn_id=_require_text(
                        row["source_turn_id"], "source turn id", maximum=256
                    ),
                    customer_message=_require_text(
                        row["customer_message"], "customer message", maximum=131072
                    ),
                    assistant_reply_chunks=tuple(
                        _require_text(chunk, "assistant reply chunk", maximum=65536)
                        for chunk in chunks
                    ),
                    committed_at=_timestamp(row["committed_at"], "turn committed_at"),
                )
            )
        return tuple(
            sorted(result, key=lambda item: (item.committed_at, item.lead_id, item.source_turn_id))
        )

    def _passenger_manifests(self, raw: _RawSources) -> tuple[PassengerManifestRecord, ...]:
        result: list[PassengerManifestRecord] = []
        for row in raw.table("v2-private-customer.sqlite3", "private_passenger_manifests"):
            payload = _json_value(row["fact_json"], "passenger manifest")
            result.append(
                PassengerManifestRecord(
                    lead_id=_require_text(row["lead_id"], "manifest lead_id", maximum=256),
                    manifest_json=_canonical_json(payload),
                    revision=_require_int(row["revision"], "manifest revision", minimum=1),
                    persisted_at=_timestamp(row["persisted_at"], "manifest persisted_at"),
                )
            )
        return tuple(sorted(result, key=lambda item: (item.lead_id, item.persisted_at)))

    def _inbound_events(self, raw: _RawSources) -> tuple[InboundEventRecord, ...]:
        result = tuple(
            InboundEventRecord(
                event_id=_require_text(row["event_id"], "inbound event id", maximum=256),
                lead_id=_require_text(row["lead_id"], "inbound lead_id", maximum=256),
                status=_require_text(row["status"], "inbound status", maximum=64),
                occurred_at=_timestamp(row["occurred_at"], "inbound occurred_at"),
                completed_at=_timestamp(
                    row["completed_at"], "inbound completed_at", optional=True
                ),
            )
            for row in raw.table("inbox.sqlite3", "inbound_events")
        )
        return tuple(sorted(result, key=lambda item: (item.occurred_at, item.event_id)))

    def _boundary_states(self, raw: _RawSources) -> dict[str, tuple[dict[str, object], str]]:
        result: dict[str, tuple[dict[str, object], str]] = {}
        for row in raw.table("v2-boundary.sqlite3", "boundary_state"):
            lead_id = _require_text(row["lead_key"], "boundary lead_key", maximum=256)
            if lead_id in result:
                raise ValueError("duplicate boundary lead")
            payload = _json_object(row["state_json"], "boundary state")
            data = _wire_data(payload, "boundary state", expected_type="boundary_state")
            embedded = _require_text(data.get("lead_key"), "boundary embedded lead", maximum=256)
            if embedded != lead_id:
                raise ValueError("boundary lead mismatch")
            result[lead_id] = (
                data,
                _timestamp(row["updated_at"], "boundary updated_at"),
            )
        return result

    def _public_replies(self, raw: _RawSources) -> tuple[PublicReplyRecord, ...]:
        result: list[PublicReplyRecord] = []
        for row in raw.table("v2-boundary.sqlite3", "boundary_public_outbox"):
            payload = _json_object(row["chunk_json"], "boundary public chunk")
            result.append(
                PublicReplyRecord(
                    reply_id=_require_text(
                        row["public_row_id"], "boundary public row id", maximum=256
                    ),
                    lead_id=_require_text(
                        row["lead_key"], "boundary public lead", maximum=256
                    ),
                    source="boundary_public_outbox",
                    chunk_index=_require_int(
                        row["chunk_index"], "boundary public chunk index"
                    ),
                    text=_reply_text(payload),
                    status=_require_text(
                        row["status"], "boundary public status", maximum=64
                    ),
                    author="maya",
                    updated_at=_timestamp(
                        row["updated_at"], "boundary public updated_at"
                    ),
                )
            )
        for row in raw.table("v2-public-outbox.sqlite3", "public_outbox"):
            result.append(
                PublicReplyRecord(
                    reply_id=_require_text(row["outbox_id"], "public outbox id", maximum=256),
                    lead_id=_require_text(row["lead_id"], "public lead", maximum=256),
                    source="public_outbox",
                    chunk_index=_require_int(row["chunk_index"], "public chunk index"),
                    text=_require_text(row["text"], "public text", maximum=65536),
                    status=_require_text(row["status"], "public status", maximum=64),
                    author=_require_text(row["author"], "public author", maximum=64),
                    updated_at=_timestamp(row["updated_at"], "public updated_at"),
                )
            )
        return tuple(
            sorted(
                result,
                key=lambda item: (item.updated_at, item.lead_id, item.source, item.chunk_index, item.reply_id),
            )
        )

    def _reservation_command_data(
        self,
        raw_json: object,
        *,
        expected_command_id: str,
    ) -> dict[str, object]:
        payload = (
            raw_json
            if type(raw_json) is dict
            else _json_object(raw_json, "reservation command")
        )
        data = _wire_data(payload, "reservation command", expected_type="reservation_command")
        if _require_text(data.get("command_id"), "command id", maximum=256) != expected_command_id:
            raise ValueError("reservation command id mismatch")
        return data

    def _reservation_component(self, raw: object) -> ReservationComponent:
        component = _object(raw, "reservation component")
        party = _object(component.get("party"), "reservation party")
        total = _object(component.get("total"), "reservation total")
        return ReservationComponent(
            service=_require_text(component.get("service"), "service", maximum=128),
            public_label=_require_text(
                component.get("public_label"), "public label", maximum=1024
            ),
            start_date=_require_text(component.get("start_date"), "start date", maximum=32),
            start_time=_optional_text(component.get("start_time"), "start time", maximum=32),
            end_date=_optional_text(component.get("end_date"), "end date", maximum=32),
            adults=_require_int(party.get("adults"), "adults"),
            children=_require_int(party.get("children"), "children"),
            available=_require_bool(component.get("available"), "available"),
            lookup_id=_optional_text(component.get("lookup_id"), "lookup id", maximum=256),
            offer_id=_optional_text(component.get("offer_id"), "offer id", maximum=256),
            provider_ref=_optional_text(
                component.get("provider_ref"), "provider ref", maximum=256
            ),
            amount_minor=_minor_amount(total.get("amount"), "reservation amount"),
            currency=_require_text(total.get("currency"), "reservation currency", maximum=3),
        )

    def _reservations(self, raw: _RawSources) -> tuple[ReservationRecord, ...]:
        boundary: dict[str, tuple[str, dict[str, object], str]] = {}
        for row in raw.table("v2-boundary.sqlite3", "boundary_commands"):
            command_id = _require_text(row["command_id"], "boundary command id", maximum=256)
            command_type = _require_text(
                row["command_type"], "boundary command type", maximum=128
            )
            if command_type != "reservation":
                continue
            payload = _json_object(row["command_json"], "boundary command")
            if payload.get("type") != "reservation_command":
                raise ValueError("reservation command discriminator mismatch")
            data = self._reservation_command_data(
                payload, expected_command_id=command_id
            )
            if command_id in boundary:
                raise ValueError("duplicate boundary command")
            boundary[command_id] = (
                _require_text(row["lead_key"], "boundary command lead", maximum=256),
                data,
                _timestamp(row["created_at"], "boundary command created_at"),
            )

        execution: dict[str, tuple[dict[str, object], sqlite3.Row]] = {}
        for row in raw.table("v2-execution.sqlite3", "reservation_commands"):
            command_id = _require_text(row["command_id"], "execution command id", maximum=256)
            data = self._reservation_command_data(
                row["command_json"], expected_command_id=command_id
            )
            if command_id in execution:
                raise ValueError("duplicate execution command")
            execution[command_id] = (data, row)

        workflows: dict[str, tuple[dict[str, object], sqlite3.Row]] = {}
        for row in raw.table("v2-execution.sqlite3", "workflows"):
            workflow_id = _require_text(row["workflow_id"], "workflow id", maximum=256)
            payload = _json_object(row["state_json"], "reservation workflow")
            data = _wire_data(payload, "reservation workflow")
            workflows[workflow_id] = (data, row)

        ledger: dict[str, tuple[dict[str, object] | None, sqlite3.Row]] = {}
        for row in raw.table("v2-execution.sqlite3", "execution_ledger"):
            command_id = _require_text(row["command_id"], "ledger command id", maximum=256)
            outcome = None
            if row["outcome_json"] is not None:
                payload = _json_object(row["outcome_json"], "reservation outcome")
                outcome = _wire_data(
                    payload, "reservation outcome", expected_type="execution_outcome"
                )
                if _require_text(
                    outcome.get("command_id"), "outcome command id", maximum=256
                ) != command_id:
                    raise ValueError("outcome command mismatch")
            ledger[command_id] = (outcome, row)

        bokun: dict[str, str] = {}
        for row in raw.table("v2-bokun-audit.sqlite3", "bokun_audit_tasks"):
            command_id = _require_text(row["command_id"], "Bokun command id", maximum=256)
            booking_id = _optional_identifier(
                row["booking_id"], "Bokun booking id", maximum=256
            )
            if booking_id is None:
                continue
            if command_id in bokun and bokun[command_id] != booking_id:
                raise ValueError("ambiguous Bokun booking id")
            bokun[command_id] = booking_id

        cloudbeds: dict[str, str] = {}
        for row in raw.table("v2-cloudbeds-audit.sqlite3", "cloudbeds_audit_tasks"):
            command_id = _require_text(
                row["command_id"], "Cloudbeds command id", maximum=256
            )
            reservation_id = _optional_identifier(
                row["reservation_id"], "Cloudbeds reservation id", maximum=256
            )
            if reservation_id is None:
                continue
            if command_id in cloudbeds and cloudbeds[command_id] != reservation_id:
                raise ValueError("ambiguous Cloudbeds reservation id")
            cloudbeds[command_id] = reservation_id

        result: list[ReservationRecord] = []
        for command_id in sorted(set(boundary) | set(execution)):
            lead_id: str | None = None
            boundary_data: dict[str, object] | None = None
            boundary_created: str | None = None
            if command_id in boundary:
                lead_id, boundary_data, boundary_created = boundary[command_id]
            execution_data: dict[str, object] | None = None
            execution_row: sqlite3.Row | None = None
            if command_id in execution:
                execution_data, execution_row = execution[command_id]
            if boundary_data is not None and execution_data is not None and boundary_data != execution_data:
                raise ValueError("reservation command copies diverge")
            data = execution_data if execution_data is not None else boundary_data
            if data is None:
                raise ValueError("reservation command unavailable")

            workflow_id = _require_text(data.get("workflow_id"), "workflow id", maximum=256)
            draft_id = _require_text(data.get("draft_id"), "draft id", maximum=256)
            draft_version = _require_int(data.get("draft_version"), "draft version", minimum=1)
            operation = _require_text(data.get("operation"), "operation", maximum=128)
            if execution_row is not None:
                if (
                    execution_row["workflow_id"] != workflow_id
                    or execution_row["draft_id"] != draft_id
                    or execution_row["draft_version"] != draft_version
                    or execution_row["operation"] != operation
                ):
                    raise ValueError("reservation command columns diverge")
            payload = _object(data.get("payload"), "reservation payload")
            components = tuple(
                self._reservation_component(item)
                for item in _array(payload.get("components"), "reservation components")
            )
            if not components:
                raise ValueError("reservation lacks components")
            customer_raw = _object(payload.get("customer"), "reservation customer")
            customer = ReservationCustomer(
                customer_ref=_optional_text(
                    customer_raw.get("customer_ref"), "customer reference", maximum=512
                ),
                full_name=_optional_text(customer_raw.get("full_name"), "full name", maximum=512),
                email=_optional_text(customer_raw.get("email"), "email", maximum=512),
                phone_e164=_optional_text(
                    customer_raw.get("phone_e164"), "phone", maximum=64
                ),
                country_code=_optional_text(
                    customer_raw.get("country_code"), "country code", maximum=8
                ),
                birth_date=_optional_text(
                    customer_raw.get("birth_date"), "birth date", maximum=32
                ),
                gender=_optional_text(customer_raw.get("gender"), "gender", maximum=64),
            )
            terms = _object(payload.get("terms"), "reservation terms")
            payment_method = _optional_text(
                terms.get("payment_method"), "payment method", maximum=64
            )

            outcome: dict[str, object] | None = None
            workflow_updated: str | None = None
            if workflow_id in workflows:
                workflow_data, workflow_row = workflows[workflow_id]
                workflow_updated = _timestamp(
                    workflow_row["updated_at"], "workflow updated_at"
                )
                candidate = workflow_data.get("outcome")
                if candidate is not None:
                    outcome = _object(candidate, "workflow outcome")
            ledger_updated: str | None = None
            if command_id in ledger:
                ledger_outcome, ledger_row = ledger[command_id]
                ledger_updated = _timestamp(ledger_row["updated_at"], "ledger updated_at")
                if ledger_outcome is not None:
                    if outcome is not None and outcome != ledger_outcome:
                        raise ValueError("reservation outcomes diverge")
                    outcome = ledger_outcome

            certainty = None
            normalized_status = None
            provider_reference = None
            if outcome is not None:
                certainty = _require_text(outcome.get("certainty"), "certainty", maximum=64)
                normalized_status = _require_text(
                    outcome.get("normalized_status"), "normalized status", maximum=64
                )
                provider_reference = _optional_text(
                    outcome.get("provider_reference"), "provider reference", maximum=256
                )
            if certainty == "effect_confirmed" and normalized_status == "confirmed":
                status_code = "confirmed"
                status_label = "Confirmada"
            elif command_id in ledger:
                ledger_status = _require_text(
                    ledger[command_id][1]["status"], "reservation ledger status", maximum=64
                )
                if ledger_status in {"outcome_recorded", "manual_review"}:
                    status_code = "outcome_recorded"
                    status_label = "Outcome registrado"
                else:
                    status_code = "preparing"
                    status_label = "Em preparação"
            else:
                status_code = "preparing"
                status_label = "Em preparação"

            currencies = {component.currency for component in components}
            currency = next(iter(currencies)) if len(currencies) == 1 else None
            total_minor = (
                sum(component.amount_minor for component in components)
                if currency is not None
                else None
            )
            created_at = _latest(
                _timestamp(execution_row["created_at"], "command created_at")
                if execution_row is not None
                else None,
                boundary_created,
            )
            if created_at is None:
                raise ValueError("reservation created_at unavailable")
            updated_at = _latest(created_at, workflow_updated, ledger_updated)
            if updated_at is None:
                raise ValueError("reservation updated_at unavailable")
            result.append(
                ReservationRecord(
                    lead_id=lead_id,
                    command_id=command_id,
                    workflow_id=workflow_id,
                    draft_id=draft_id,
                    draft_version=draft_version,
                    operation=operation,
                    status_code=status_code,
                    status_label=status_label,
                    certainty=certainty,
                    normalized_status=normalized_status,
                    provider_reference=provider_reference,
                    bokun_booking_id=bokun.get(command_id),
                    cloudbeds_reservation_id=cloudbeds.get(command_id),
                    total_minor=total_minor,
                    currency=currency,
                    payment_method=payment_method,
                    customer=customer,
                    components=components,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
        return tuple(sorted(result, key=lambda item: (item.updated_at, item.command_id), reverse=True))

    def _payments(
        self,
        raw: _RawSources,
        reservations: tuple[ReservationRecord, ...],
    ) -> tuple[PaymentRecord, ...]:
        anchors: dict[str, ReservationRecord] = {}
        for reservation in reservations:
            for anchor in (
                reservation.command_id,
                reservation.workflow_id,
                reservation.draft_id,
            ):
                existing = anchors.get(anchor)
                if existing is not None and existing.command_id != reservation.command_id:
                    raise ValueError("ambiguous reservation anchor")
                anchors[anchor] = reservation

        steps: dict[str, list[PaymentStepRecord]] = defaultdict(list)
        for row in raw.table("v2-payment-initiation.sqlite3", "stripe_step_receipts"):
            initiation_id = _require_text(row["initiation_id"], "step initiation id", maximum=256)
            steps[initiation_id].append(
                PaymentStepRecord(
                    step=_require_text(row["step"], "payment step", maximum=64),
                    status=_require_text(row["status"], "payment step status", maximum=64),
                    updated_at=_timestamp(row["updated_at"], "payment step updated_at"),
                )
            )
        reconciliations: dict[str, tuple[str, str]] = {}
        for row in raw.table("v2-payment-initiation.sqlite3", "stripe_reconciliations"):
            initiation_id = _require_text(
                row["initiation_id"], "reconciliation initiation id", maximum=256
            )
            reconciliations[initiation_id] = (
                _require_text(row["status"], "reconciliation status", maximum=64),
                _timestamp(row["updated_at"], "reconciliation updated_at"),
            )

        result: list[PaymentRecord] = []
        initiation_by_payment: dict[str, PaymentRecord] = {}
        for row in raw.table("v2-payment-initiation.sqlite3", "payment_initiations"):
            initiation_id = _require_text(row["initiation_id"], "initiation id", maximum=256)
            selection = _json_object(row["selection_json"], "payment selection")
            method = _require_text(selection.get("method"), "payment method", maximum=64)
            obligation = _object(selection.get("obligation"), "payment obligation")
            payment_id = _require_text(obligation.get("payment_id"), "payment id", maximum=256)
            anchor_id = _require_text(
                obligation.get("reservation_anchor_id"), "reservation anchor id", maximum=256
            )
            amount_due = _require_int(
                obligation.get("amount_minor"), "payment amount", minimum=1
            )
            currency = _require_text(obligation.get("currency"), "payment currency", maximum=3)
            due_kind = _require_text(obligation.get("due_kind"), "payment due kind", maximum=64)
            status = _require_text(row["status"], "initiation status", maximum=64)
            ordered_steps = tuple(
                sorted(steps.get(initiation_id, ()), key=lambda item: (item.updated_at, item.step))
            )
            link_ready = status == "completed" and any(
                item.step == "payment_link"
                and item.status in {"accepted", "completed", "succeeded"}
                for item in ordered_steps
            )
            status_code = "link_ready" if link_ready else status
            labels = {
                "link_ready": "Link de pagamento preparado",
                "completed": "Iniciação concluída",
                "pending": "Iniciação pendente",
                "failed": "Iniciação falhou",
            }
            reconciliation = reconciliations.get(initiation_id)
            updated_at = _latest(
                _timestamp(row["updated_at"], "initiation updated_at"),
                reconciliation[1] if reconciliation is not None else None,
                *(step.updated_at for step in ordered_steps),
            )
            if updated_at is None:
                raise ValueError("payment updated_at unavailable")
            reservation = anchors.get(anchor_id)
            payment = PaymentRecord(
                record_id=initiation_id,
                lead_id=reservation.lead_id if reservation is not None else None,
                phase="initiation",
                payment_id=payment_id,
                initiation_id=initiation_id,
                settlement_command_id=None,
                reservation_anchor_id=anchor_id,
                method=method,
                amount_due_minor=amount_due,
                amount_paid_minor=None,
                currency=currency,
                due_kind=due_kind,
                status_code=status_code,
                status_label=_status_label(status_code, labels, "Iniciação"),
                settled=False,
                workflow_status=None,
                ledger_status=None,
                outcome_certainty=None,
                reconciliation_status=reconciliation[0] if reconciliation is not None else None,
                payment_link_prepared=link_ready,
                steps=ordered_steps,
                settled_at=None,
                updated_at=updated_at,
            )
            if payment_id in initiation_by_payment:
                raise ValueError("duplicate payment initiation identity")
            initiation_by_payment[payment_id] = payment
            result.append(payment)

        workflows: dict[str, tuple[dict[str, object], sqlite3.Row]] = {}
        for row in raw.table("v2-followup.sqlite3", "payment_workflows"):
            payment_id = _require_text(row["payment_id"], "workflow payment id", maximum=256)
            payload = _json_object(row["state_json"], "payment workflow")
            data = _wire_data(payload, "payment workflow", expected_type="payment_workflow")
            subject = _object(data.get("subject"), "payment subject")
            if _require_text(subject.get("payment_id"), "subject payment id", maximum=256) != payment_id:
                raise ValueError("payment workflow identity mismatch")
            workflows[payment_id] = (data, row)

        ledgers: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in raw.table("v2-followup.sqlite3", "payment_ledger"):
            payment_id = _require_text(row["payment_id"], "ledger payment id", maximum=256)
            ledgers[payment_id].append(row)

        for payment_id in sorted(workflows):
            data, workflow_row = workflows[payment_id]
            subject = _object(data.get("subject"), "payment subject")
            amount_due = _require_int(subject.get("amount_minor"), "settlement amount", minimum=1)
            currency = _require_text(subject.get("currency"), "settlement currency", maximum=3)
            method = _optional_text(subject.get("method"), "settlement method", maximum=64)
            anchor = _object(
                subject.get("confirmed_reservation_anchor"), "confirmed reservation anchor"
            )
            anchor_candidates = (
                _optional_text(
                    anchor.get("reservation_command_id"), "anchor command id", maximum=256
                ),
                _optional_text(
                    anchor.get("reservation_workflow_id"), "anchor workflow id", maximum=256
                ),
            )
            reservation = next(
                (anchors[item] for item in anchor_candidates if item is not None and item in anchors),
                None,
            )
            initiation = initiation_by_payment.get(payment_id)
            lead_id = (
                reservation.lead_id
                if reservation is not None
                else initiation.lead_id if initiation is not None else None
            )
            rows = ledgers.get(payment_id) or [None]
            for ledger_row in rows:
                workflow_status = _require_text(
                    workflow_row["status"], "payment workflow status", maximum=64
                )
                settlement_command_id = None
                outcome: dict[str, object] | None = None
                certainty = None
                settled_at = None
                ledger_updated = None
                ledger_status = workflow_status
                if ledger_row is not None:
                    settlement_command_id = _require_text(
                        ledger_row["settlement_command_id"],
                        "settlement command id",
                        maximum=256,
                    )
                    ledger_status = _require_text(
                        ledger_row["status"], "settlement ledger status", maximum=64
                    )
                    ledger_updated = _timestamp(
                        ledger_row["updated_at"], "settlement updated_at"
                    )
                    certainty = _optional_text(
                        ledger_row["outcome_certainty"], "settlement certainty", maximum=64
                    )
                    if ledger_row["outcome_json"] is not None:
                        payload = _json_object(
                            ledger_row["outcome_json"], "settlement outcome"
                        )
                        outcome = _wire_data(
                            payload, "settlement outcome", expected_type="settlement_outcome"
                        )
                        embedded = _require_text(
                            outcome.get("certainty"), "outcome certainty", maximum=64
                        )
                        if certainty is not None and certainty != embedded:
                            raise ValueError("settlement certainty mismatch")
                        certainty = embedded
                settled = False
                amount_paid = None
                if outcome is not None:
                    registered = _require_bool(
                        outcome.get("payment_registered"), "payment registered"
                    )
                    target_confirmed = _require_bool(
                        outcome.get("reservation_target_confirmed"),
                        "reservation target confirmed",
                    )
                    settled = certainty == "settled" and registered and target_confirmed
                    if settled:
                        settled_at = _timestamp(
                            ledger_row["outcome_recorded_at"],
                            "settlement outcome recorded_at",
                        )
                status_code = (
                    "settled"
                    if settled
                    else "outcome_recorded" if outcome is not None else "preparing"
                )
                labels = {
                    "settled": "Pagamento liquidado",
                    "outcome_recorded": "Outcome de liquidação registrado",
                    "preparing": "Liquidação em preparação",
                    "partial_settlement": "Liquidação parcial",
                    "dispatched_unknown": "Liquidação incerta",
                    "dispatched_no_effect": "Despachado sem efeito confirmado",
                    "not_dispatched": "Não despachado",
                    "queued": "Liquidação na fila",
                    "dispatch_fenced": "Liquidação despachada",
                    "awaiting_method": "Aguardando método",
                }
                updated_at = _latest(
                    _timestamp(workflow_row["updated_at"], "payment workflow updated_at"),
                    ledger_updated,
                )
                if updated_at is None:
                    raise ValueError("settlement updated_at unavailable")
                result.append(
                    PaymentRecord(
                        record_id=settlement_command_id or payment_id,
                        lead_id=lead_id,
                        phase="settlement",
                        payment_id=payment_id,
                        initiation_id=None,
                        settlement_command_id=settlement_command_id,
                        reservation_anchor_id=anchor_candidates[0] or anchor_candidates[1],
                        method=method,
                        amount_due_minor=amount_due,
                        amount_paid_minor=amount_paid,
                        currency=currency,
                        due_kind=None,
                        status_code=status_code,
                        status_label=_status_label(status_code, labels, "Liquidação"),
                        settled=settled,
                        workflow_status=workflow_status,
                        ledger_status=ledger_status if ledger_row is not None else None,
                        outcome_certainty=certainty,
                        reconciliation_status=None,
                        payment_link_prepared=False,
                        steps=(),
                        settled_at=settled_at,
                        updated_at=updated_at,
                    )
                )
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    item.updated_at,
                    item.payment_id,
                    item.phase,
                    item.initiation_id or item.settlement_command_id or "",
                ),
                reverse=True,
            )
        )

    def _handoffs(
        self,
        raw: _RawSources,
        boundary_states: Mapping[str, tuple[dict[str, object], str]],
    ) -> tuple[HandoffRecord, ...]:
        lead_by_handoff: dict[str, str] = {}
        for lead_id, (state, _updated_at) in boundary_states.items():
            candidate = state.get("handoff")
            handoff_id: str | None = None
            if type(candidate) is dict and candidate.get("handoff_id") is not None:
                handoff_id = _require_text(
                    candidate.get("handoff_id"), "boundary handoff id", maximum=256
                )
            elif state.get("handoff_id") is not None:
                handoff_id = _require_text(
                    state.get("handoff_id"), "boundary handoff id", maximum=256
                )
            if handoff_id is not None:
                existing = lead_by_handoff.get(handoff_id)
                if existing is not None and existing != lead_id:
                    raise ValueError("ambiguous handoff identity")
                lead_by_handoff[handoff_id] = lead_id

        event_counts: dict[str, int] = defaultdict(int)
        for row in raw.table("v2-followup.sqlite3", "handoff_events"):
            handoff_id = _require_text(row["handoff_id"], "handoff event id", maximum=256)
            event_counts[handoff_id] += 1

        result: list[HandoffRecord] = []
        labels = {
            "open": "Aberto",
            "acknowledged": "Reconhecido",
            "cancelled": "Cancelado",
            "failed": "Falhou",
        }
        for row in raw.table("v2-followup.sqlite3", "handoff_workflows"):
            handoff_id = _require_text(row["handoff_id"], "handoff id", maximum=256)
            incident_key = _require_text(row["incident_key"], "incident key", maximum=256)
            payload = _json_object(row["state_json"], "handoff workflow")
            data = _wire_data(payload, "handoff workflow", expected_type="handoff_workflow")
            request = _object(data.get("request"), "handoff request")
            if (
                _require_text(request.get("handoff_id"), "request handoff id", maximum=256)
                != handoff_id
                or _require_text(
                    request.get("incident_key"), "request incident key", maximum=256
                )
                != incident_key
            ):
                raise ValueError("handoff identity mismatch")
            status = _require_text(row["status"], "handoff status", maximum=64)
            embedded_status = _require_text(
                data.get("status"), "embedded handoff status", maximum=64
            )
            if status != embedded_status:
                raise ValueError("handoff status mismatch")
            result.append(
                HandoffRecord(
                    lead_id=lead_by_handoff.get(handoff_id),
                    handoff_id=handoff_id,
                    incident_key=incident_key,
                    reason_code=_optional_text(
                        request.get("reason_code"), "handoff reason", maximum=64
                    ),
                    status_code=status,
                    status_label=_status_label(status, labels, "Handoff"),
                    event_count=event_counts.get(handoff_id, 0),
                    receipt_count=0,
                    created_at=_timestamp(row["created_at"], "handoff created_at"),
                    updated_at=_timestamp(row["updated_at"], "handoff updated_at"),
                )
            )
        return tuple(sorted(result, key=lambda item: (item.updated_at, item.handoff_id), reverse=True))

    def _leads(
        self,
        *,
        facts: tuple[CustomerFactRecord, ...],
        turns: tuple[DialogueTurnRecord, ...],
        manifests: tuple[PassengerManifestRecord, ...],
        inbound: tuple[InboundEventRecord, ...],
        boundary_states: Mapping[str, tuple[dict[str, object], str]],
        replies: tuple[PublicReplyRecord, ...],
        reservations: tuple[ReservationRecord, ...],
        payments: tuple[PaymentRecord, ...],
        handoffs: tuple[HandoffRecord, ...],
        executions: tuple[ExecutionLink, ...],
        raw: _RawSources,
    ) -> tuple[LeadSummary, ...]:
        lead_ids: set[str] = set(boundary_states)
        activity: dict[str, list[str]] = defaultdict(list)

        def record(lead_id: str, timestamp: str | None) -> None:
            lead_ids.add(lead_id)
            if timestamp is not None:
                activity[lead_id].append(timestamp)

        for fact in facts:
            record(fact.lead_id, fact.persisted_at)
        for turn in turns:
            record(turn.lead_id, turn.committed_at)
        for manifest in manifests:
            record(manifest.lead_id, manifest.persisted_at)
        for event in inbound:
            record(event.lead_id, event.completed_at or event.occurred_at)
        for lead_id, (_state, updated_at) in boundary_states.items():
            record(lead_id, updated_at)
        for reply in replies:
            record(reply.lead_id, reply.updated_at)
        for reservation in reservations:
            if reservation.lead_id is not None:
                record(reservation.lead_id, reservation.updated_at)
        for payment in payments:
            if payment.lead_id is not None:
                record(payment.lead_id, payment.updated_at)
        for handoff in handoffs:
            if handoff.lead_id is not None:
                record(handoff.lead_id, handoff.updated_at)
        for execution in executions:
            record(
                execution.lead_id,
                (execution.completed_at or execution.received_at).isoformat(),
            )
        for row in raw.table("v2-boundary.sqlite3", "boundary_commands"):
            record(
                _require_text(row["lead_key"], "boundary command lead", maximum=256),
                _timestamp(row["created_at"], "boundary command created_at"),
            )

        def count(values: Iterable[object], lead_id: str) -> int:
            return sum(getattr(value, "lead_id", None) == lead_id for value in values)

        result: list[LeadSummary] = []
        for lead_id in sorted(lead_ids):
            lead_reservations = tuple(
                item for item in reservations if item.lead_id == lead_id
            )
            lead_payments = tuple(item for item in payments if item.lead_id == lead_id)
            lead_handoffs = tuple(item for item in handoffs if item.lead_id == lead_id)
            lead_executions = tuple(
                item for item in executions if item.lead_id == lead_id
            )
            if lead_handoffs:
                state_code, state_label = "handoff_registered", "Handoff registrado"
            elif any(
                item.phase == "settlement" and item.status_code == "settled"
                for item in lead_payments
            ):
                state_code, state_label = "payment_settled", "Pagamento liquidado"
            elif any(item.status_code == "confirmed" for item in lead_reservations):
                state_code, state_label = "reservation_confirmed", "Reserva confirmada"
            elif lead_reservations:
                state_code, state_label = "reservation_preparing", "Reserva em preparação"
            elif any(item.payment_link_prepared for item in lead_payments):
                state_code, state_label = "payment_link_ready", "Link de pagamento preparado"
            elif any(
                (
                    count(facts, lead_id),
                    count(turns, lead_id),
                    count(manifests, lead_id),
                    count(inbound, lead_id),
                    count(replies, lead_id),
                )
            ) or lead_id in boundary_states:
                state_code, state_label = "service_recorded", "Atendimento registrado"
            else:
                state_code, state_label = "execution_only", "Somente execução"
            result.append(
                LeadSummary(
                    lead_id=lead_id,
                    first_activity_at=min(activity[lead_id]) if activity[lead_id] else None,
                    last_activity_at=max(activity[lead_id]) if activity[lead_id] else None,
                    state_code=state_code,
                    state_label=state_label,
                    fact_count=count(facts, lead_id),
                    dialogue_turn_count=count(turns, lead_id),
                    passenger_manifest_count=count(manifests, lead_id),
                    inbound_count=count(inbound, lead_id),
                    public_reply_count=count(replies, lead_id),
                    execution_count=sum(link.lead_id == lead_id for link in executions),
                    reservation_count=len(lead_reservations),
                    payment_count=len(lead_payments),
                    payment_initiation_count=sum(
                        item.phase == "initiation" for item in lead_payments
                    ),
                    settled_payment_count=sum(
                        item.phase == "settlement" and item.status_code == "settled"
                        for item in lead_payments
                    ),
                    handoff_count=len(lead_handoffs),
                    latest_execution_status=(
                        None
                        if not lead_executions
                        else max(
                            lead_executions,
                            key=lambda item: (
                                item.completed_at or item.received_at,
                                item.execution_id,
                            ),
                        ).status
                    ),
                )
            )
        result.sort(key=lambda item: item.lead_id)
        result.sort(key=lambda item: item.last_activity_at or "", reverse=True)
        return tuple(result)
