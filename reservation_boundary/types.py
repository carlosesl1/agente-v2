"""Closed immutable contracts for the Phase 7 runtime boundary."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
import hashlib
import json
import math
import re
from typing import ClassVar, Final, TypeAlias, get_args

from reservation_domain import ReservationCommand, STATE_TYPES, WorkflowState
from reservation_execution import OutboxMessage
from reservation_followup import HandoffWorkflow, PaymentSettlementCommand, PaymentWorkflow


_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_LOCALE_RE: Final = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_DECIMAL_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*|-[1-9][0-9]*)\.[0-9]{2}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY_RE: Final = re.compile(r"^[A-Z]{3}$")
_BOUNDARY_SCHEMA_VERSION: Final = 7


def _require_exact_str(value: object, name: str, *, identifier: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty without surrounding whitespace")
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value):
        raise ValueError(f"{name} contains a forbidden control character")
    if identifier and _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must use the closed identifier alphabet")
    return value


def _require_exact_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_exact_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be an exact boolean")
    return value


def _require_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime:
        raise TypeError(f"{name} must be an exact datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value


def _require_exact_tuple(value: object, name: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be an exact tuple")
    return value


def _require_exact_members(
    value: object,
    name: str,
    allowed: tuple[type[object], ...],
) -> tuple[object, ...]:
    items = _require_exact_tuple(value, name)
    for item in items:
        if type(item) not in allowed:
            raise TypeError(f"{name} contains an unsupported exact type: {type(item).__name__}")
    return items


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_closed_envelope(
    payload: bytes,
    name: str,
    *,
    schema: str,
    version: int,
    fields: frozenset[str],
) -> dict[str, object]:
    if type(payload) is not bytes:
        raise TypeError(f"{name} payload must be exact bytes")
    if not payload:
        raise ValueError(f"{name} payload must be non-empty")
    try:
        envelope = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} payload must be canonical UTF-8 JSON") from exc
    if type(envelope) is not dict or set(envelope) != {"schema", "version", "data"}:
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
    canonical = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if canonical != payload:
        raise ValueError(f"{name} payload must use exact canonical JSON")
    return data


class FrozenDict(Mapping[str, object]):
    """Small recursively immutable and hashable mapping used by legacy snapshots."""

    __slots__ = ("_items", "_mapping", "_hash")

    def __init__(self, source: Mapping[str, object]) -> None:
        if not isinstance(source, Mapping):
            raise TypeError("frozen JSON object must be a mapping")
        items: list[tuple[str, object]] = []
        for key, value in source.items():
            if type(key) is not str:
                raise TypeError("frozen JSON object keys must be exact strings")
            items.append((key, _freeze_json(value)))
        items.sort(key=lambda pair: pair[0])
        self._items = tuple(items)
        self._mapping = dict(items)
        self._hash = hash(self._items)

    def __getitem__(self, key: str) -> object:
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return f"FrozenDict({dict(self._items)!r})"


def _freeze_json(value: object) -> object:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("legacy JSON floats must be finite")
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if type(value) in (list, tuple):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(f"unsupported legacy JSON type: {type(value).__name__}")


class ImportDisposition(str, Enum):
    MIGRATED = "migrated"
    MANUAL_REVIEW = "manual_review"
    REJECTED = "rejected"


class DispatchKind(str, Enum):
    READ = "read"
    COMMAND = "command"
    STATE_COMMIT = "state_commit"


class DivergenceSeverity(str, Enum):
    EQUIVALENT = "equivalent"
    NONCRITICAL = "noncritical"
    CRITICAL = "critical"


class ConversationIntentKind(str, Enum):
    INFORM = "inform"
    SELECT = "select"
    ADJUST = "adjust"
    CONFIRM = "confirm"
    REQUEST_HANDOFF = "request_handoff"
    TOOL_REQUEST = "tool_request"


class ImportReason(str, Enum):
    NONE = "none"
    MALFORMED = "malformed"
    MISSING_IDENTITY = "missing_identity"
    AMBIGUOUS_IDENTITY = "ambiguous_identity"
    CONFLICTING_IDENTITY = "conflicting_identity"
    MISSING_PROVENANCE = "missing_provenance"
    UNSUPPORTED_STAGE = "unsupported_stage"
    INCONSISTENT_SELECTION = "inconsistent_selection"
    INCONSISTENT_CONFIRMATION = "inconsistent_confirmation"
    UNVERIFIED_PAYMENT = "unverified_payment"
    UNKNOWN_HISTORICAL_OUTCOME = "unknown_historical_outcome"
    UNSUPPORTED_SCHEMA = "unsupported_schema"


class TurnPlanReason(str, Enum):
    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    MANUAL_REVIEW = "manual_review"


class CommandMigrationDisposition(str, Enum):
    RESERVATION = "reservation"
    PAYMENT_SETTLEMENT = "payment_settlement"
    BLOCKED_UNMIGRATED = "blocked_unmigrated"


@dataclass(frozen=True, slots=True)
class StringSlot:
    value: str

    def __post_init__(self) -> None:
        _require_exact_str(self.value, "StringSlot.value")


@dataclass(frozen=True, slots=True)
class IntegerSlot:
    value: int

    def __post_init__(self) -> None:
        _require_exact_int(self.value, "IntegerSlot.value", minimum=0)


@dataclass(frozen=True, slots=True)
class DecimalSlot:
    value: str

    def __post_init__(self) -> None:
        value = _require_exact_str(self.value, "DecimalSlot.value")
        if _DECIMAL_RE.fullmatch(value) is None:
            raise ValueError("DecimalSlot.value must be a canonical two-decimal string")


@dataclass(frozen=True, slots=True)
class DateSlot:
    value: date

    def __post_init__(self) -> None:
        if type(self.value) is not date:
            raise TypeError("DateSlot.value must be an exact date")


@dataclass(frozen=True, slots=True)
class DateTimeSlot:
    value: datetime

    def __post_init__(self) -> None:
        _require_utc(self.value, "DateTimeSlot.value")


@dataclass(frozen=True, slots=True)
class BooleanSlot:
    value: bool

    def __post_init__(self) -> None:
        _require_exact_bool(self.value, "BooleanSlot.value")


SlotValue: TypeAlias = (
    StringSlot | IntegerSlot | DecimalSlot | DateSlot | DateTimeSlot | BooleanSlot
)
_SLOT_TYPES: Final = get_args(SlotValue)


@dataclass(frozen=True, slots=True)
class TypedFact:
    SCHEMA: ClassVar[str] = "phase8-typed-fact"
    VERSION: ClassVar[int] = 1
    DOMAIN: ClassVar[str] = "phase8-typed-fact-v1"

    name: str
    value: SlotValue
    frame_commitment_hash: str | None = None

    def __post_init__(self) -> None:
        _require_exact_str(self.name, "TypedFact.name", identifier=True)
        if type(self.value) not in _SLOT_TYPES:
            raise TypeError("TypedFact.value must be an exact SlotValue variant")
        if self.frame_commitment_hash is None:
            return
        if (
            type(self.frame_commitment_hash) is not str
            or _SHA256_RE.fullmatch(self.frame_commitment_hash) is None
        ):
            raise ValueError("TypedFact.frame_commitment_hash must be lowercase SHA-256")

        expected_types: dict[str, type[object]] = {
            "language": StringSlot,
            "service": StringSlot,
            "start_date": DateSlot,
            "end_date": DateSlot,
            "activity_date": DateSlot,
            "adults": IntegerSlot,
            "children": IntegerSlot,
            "payment_method": StringSlot,
        }
        expected = expected_types.get(self.name)
        if expected is None:
            raise ValueError("TypedFact.name is outside the Phase 8 fact catalog")
        if type(self.value) is not expected:
            raise TypeError("TypedFact.value does not match its Phase 8 fact name")
        if self.name == "language" and _LOCALE_RE.fullmatch(self.value.value) is None:
            raise ValueError("language fact must use a canonical locale")
        if self.name == "service" and self.value.value not in (
            "hostel",
            "agency",
            "package",
        ):
            raise ValueError("service fact must be hostel, agency or package")
        if self.name == "adults" and self.value.value < 1:
            raise ValueError("adults fact must be >= 1")
        if self.name == "payment_method" and self.value.value not in (
            "stripe",
            "wise",
            "pix",
        ):
            raise ValueError("payment_method fact is outside the closed catalog")

    def to_canonical_bytes(self) -> bytes:
        if self.frame_commitment_hash is None:
            raise ValueError("legacy TypedFact cannot serialize as Phase 8")
        if type(self.value) is StringSlot:
            kind = "string"
            value: object = self.value.value
        elif type(self.value) is IntegerSlot:
            kind = "integer"
            value = self.value.value
        elif type(self.value) is DateSlot:
            kind = "date"
            value = self.value.value.isoformat()
        else:  # pragma: no cover - constructor closes the v8 catalog
            raise TypeError("unsupported Phase 8 TypedFact slot")
        return json.dumps(
            {
                "schema": self.SCHEMA,
                "version": self.VERSION,
                "data": {
                    "name": self.name,
                    "value": {"kind": kind, "value": value},
                    "frame_commitment_hash": self.frame_commitment_hash,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            self.DOMAIN.encode("ascii") + b"\0" + self.to_canonical_bytes()
        ).hexdigest()

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "TypedFact":
        data = _load_closed_envelope(
            payload,
            "TypedFact",
            schema=cls.SCHEMA,
            version=cls.VERSION,
            fields=frozenset(("name", "value", "frame_commitment_hash")),
        )
        tagged = data["value"]
        if type(tagged) is not dict or set(tagged) != {"kind", "value"}:
            raise ValueError("TypedFact.value fields mismatch")
        kind = tagged["kind"]
        if type(kind) is not str:
            raise ValueError("TypedFact.value.kind must be an exact string")
        if kind == "string":
            slot: SlotValue = StringSlot(tagged["value"])
        elif kind == "integer":
            slot = IntegerSlot(tagged["value"])
        elif kind == "date":
            raw_date = tagged["value"]
            if type(raw_date) is not str:
                raise ValueError("TypedFact date value must be an exact string")
            try:
                parsed_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise ValueError("TypedFact date value is invalid") from exc
            if parsed_date.isoformat() != raw_date:
                raise ValueError("TypedFact date value must be canonical")
            slot = DateSlot(parsed_date)
        else:
            raise ValueError("TypedFact.value.kind is invalid")
        fact = cls(
            name=data["name"],
            value=slot,
            frame_commitment_hash=data["frame_commitment_hash"],
        )
        if fact.to_canonical_bytes() != payload:
            raise ValueError("TypedFact payload is not byte-canonical")
        return fact


@dataclass(frozen=True, slots=True)
class FaqReadArguments:
    query: str
    locale: str

    def __post_init__(self) -> None:
        _require_exact_str(self.query, "FaqReadArguments.query")
        _require_locale(self.locale)


@dataclass(frozen=True, slots=True)
class LodgingReadArguments:
    check_in: date
    check_out: date
    adults: int
    children: int = 0

    def __post_init__(self) -> None:
        _require_stay(self.check_in, self.check_out)
        _require_exact_int(self.adults, "LodgingReadArguments.adults", minimum=1)
        _require_exact_int(self.children, "LodgingReadArguments.children", minimum=0)


@dataclass(frozen=True, slots=True)
class RoomDescriptionArguments:
    room_offer_id: str

    def __post_init__(self) -> None:
        _require_exact_str(self.room_offer_id, "RoomDescriptionArguments.room_offer_id", identifier=True)


@dataclass(frozen=True, slots=True)
class ActivityReadArguments:
    activity_id: str
    activity_date: date
    participants: int

    def __post_init__(self) -> None:
        _require_exact_str(self.activity_id, "ActivityReadArguments.activity_id", identifier=True)
        if type(self.activity_date) is not date:
            raise TypeError("ActivityReadArguments.activity_date must be an exact date")
        _require_exact_int(self.participants, "ActivityReadArguments.participants", minimum=1)


@dataclass(frozen=True, slots=True)
class ActivityDescriptionArguments:
    activity_id: str

    def __post_init__(self) -> None:
        _require_exact_str(self.activity_id, "ActivityDescriptionArguments.activity_id", identifier=True)


@dataclass(frozen=True, slots=True)
class LodgingReservationArguments:
    offer_id: str
    summary_version: int
    confirmation_signature: str

    def __post_init__(self) -> None:
        _require_command_binding(self.offer_id, self.summary_version, self.confirmation_signature)


@dataclass(frozen=True, slots=True)
class ActivityReservationArguments:
    offer_id: str
    summary_version: int
    confirmation_signature: str

    def __post_init__(self) -> None:
        _require_command_binding(self.offer_id, self.summary_version, self.confirmation_signature)


@dataclass(frozen=True, slots=True)
class LodgingPaymentArguments:
    anchor_id: str
    evidence_id: str
    amount: DecimalSlot
    currency: str
    receiver_profile_id: str
    proof_status: str

    def __post_init__(self) -> None:
        _require_payment_arguments(
            self.anchor_id,
            self.evidence_id,
            self.amount,
            self.currency,
            self.receiver_profile_id,
            self.proof_status,
        )


@dataclass(frozen=True, slots=True)
class ActivityPaymentArguments:
    anchor_id: str
    evidence_id: str
    amount: DecimalSlot
    currency: str
    receiver_profile_id: str
    proof_status: str

    def __post_init__(self) -> None:
        _require_payment_arguments(
            self.anchor_id,
            self.evidence_id,
            self.amount,
            self.currency,
            self.receiver_profile_id,
            self.proof_status,
        )


@dataclass(frozen=True, slots=True)
class WiseVerificationArguments:
    anchor_id: str
    evidence_id: str

    def __post_init__(self) -> None:
        _require_exact_str(self.anchor_id, "WiseVerificationArguments.anchor_id", identifier=True)
        _require_exact_str(self.evidence_id, "WiseVerificationArguments.evidence_id", identifier=True)


@dataclass(frozen=True, slots=True)
class StripeLinkArguments:
    anchor_id: str
    amount: DecimalSlot
    currency: str

    def __post_init__(self) -> None:
        _require_exact_str(self.anchor_id, "StripeLinkArguments.anchor_id", identifier=True)
        if type(self.amount) is not DecimalSlot:
            raise TypeError("StripeLinkArguments.amount must be an exact DecimalSlot")
        _require_currency(self.currency)


@dataclass(frozen=True, slots=True)
class StateCommitArguments:
    facts: tuple[TypedFact, ...]

    def __post_init__(self) -> None:
        _require_exact_members(self.facts, "StateCommitArguments.facts", (TypedFact,))
        _require_unique_fact_names(self.facts)


ToolArguments: TypeAlias = (
    FaqReadArguments
    | LodgingReadArguments
    | RoomDescriptionArguments
    | ActivityReadArguments
    | ActivityDescriptionArguments
    | LodgingReservationArguments
    | ActivityReservationArguments
    | LodgingPaymentArguments
    | ActivityPaymentArguments
    | WiseVerificationArguments
    | StripeLinkArguments
    | StateCommitArguments
)
_TOOL_ARGUMENT_TYPES: Final = get_args(ToolArguments)
BoundaryCommand: TypeAlias = ReservationCommand | PaymentSettlementCommand
_BOUNDARY_COMMAND_TYPES: Final = get_args(BoundaryCommand)


def _require_locale(locale: object) -> str:
    value = _require_exact_str(locale, "locale")
    if _LOCALE_RE.fullmatch(value) is None:
        raise ValueError("locale must be xx or xx-YY")
    return value


def _require_stay(check_in: object, check_out: object) -> None:
    if type(check_in) is not date or type(check_out) is not date:
        raise TypeError("stay dates must be exact dates")
    if check_out <= check_in:
        raise ValueError("check_out must be after check_in")


def _require_command_binding(offer_id: object, version: object, signature: object) -> None:
    _require_exact_str(offer_id, "offer_id", identifier=True)
    _require_exact_int(version, "summary_version", minimum=1)
    value = _require_exact_str(signature, "confirmation_signature")
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("confirmation_signature must be a lowercase SHA-256")


def _require_currency(currency: object) -> str:
    value = _require_exact_str(currency, "currency")
    if _CURRENCY_RE.fullmatch(value) is None:
        raise ValueError("currency must be a three-letter uppercase code")
    return value


def _require_payment_arguments(
    anchor_id: object,
    evidence_id: object,
    amount: object,
    currency: object,
    receiver_profile_id: object,
    proof_status: object,
) -> None:
    _require_exact_str(anchor_id, "anchor_id", identifier=True)
    _require_exact_str(evidence_id, "evidence_id", identifier=True)
    if type(amount) is not DecimalSlot:
        raise TypeError("amount must be an exact DecimalSlot")
    _require_currency(currency)
    _require_exact_str(receiver_profile_id, "receiver_profile_id", identifier=True)
    _require_exact_str(proof_status, "proof_status", identifier=True)


def _require_unique_fact_names(facts: tuple[TypedFact, ...]) -> None:
    names = tuple(fact.name for fact in facts)
    if len(names) != len(set(names)):
        raise ValueError("fact names must be unique")


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    text: str
    locale: str

    def __post_init__(self) -> None:
        _require_exact_str(self.text, "NormalizedMessage.text")
        _require_locale(self.locale)


@dataclass(frozen=True, slots=True)
class LegacyLeadSnapshot:
    schema_version: int
    source: str
    raw_fields: Mapping[str, object]
    canonical_json: str
    snapshot_hash: str

    def __post_init__(self) -> None:
        _require_exact_int(self.schema_version, "LegacyLeadSnapshot.schema_version", minimum=1)
        _require_exact_str(self.source, "LegacyLeadSnapshot.source", identifier=True)
        if not isinstance(self.raw_fields, Mapping):
            raise TypeError("LegacyLeadSnapshot.raw_fields must be a mapping")
        object.__setattr__(self, "raw_fields", FrozenDict(self.raw_fields))
        _require_exact_str(self.canonical_json, "LegacyLeadSnapshot.canonical_json")
        digest = _require_exact_str(self.snapshot_hash, "LegacyLeadSnapshot.snapshot_hash")
        if _SHA256_RE.fullmatch(digest) is None:
            raise ValueError("LegacyLeadSnapshot.snapshot_hash must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class BoundaryState:
    schema_version: int
    lead_key: str
    version: int
    workflow: WorkflowState | None
    handoff: HandoffWorkflow | None
    payments: tuple[PaymentWorkflow, ...]
    processed_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_exact_int(self.schema_version, "BoundaryState.schema_version", minimum=1)
        if self.schema_version != _BOUNDARY_SCHEMA_VERSION:
            raise ValueError(
                f"BoundaryState.schema_version must equal {_BOUNDARY_SCHEMA_VERSION}"
            )
        _require_exact_str(self.lead_key, "BoundaryState.lead_key", identifier=True)
        _require_exact_int(self.version, "BoundaryState.version", minimum=0)
        if self.workflow is not None and type(self.workflow) not in STATE_TYPES:
            raise TypeError("BoundaryState.workflow must be an exact STATE_TYPES member or None")
        if self.handoff is not None and type(self.handoff) is not HandoffWorkflow:
            raise TypeError("BoundaryState.handoff must be an exact HandoffWorkflow or None")
        _require_exact_members(self.payments, "BoundaryState.payments", (PaymentWorkflow,))
        events = _require_exact_tuple(self.processed_event_ids, "BoundaryState.processed_event_ids")
        for event_id in events:
            _require_exact_str(event_id, "processed_event_id", identifier=True)
        if len(events) != len(set(events)):
            raise ValueError("processed_event_ids must be unique")


@dataclass(frozen=True, slots=True)
class ImportResult:
    disposition: ImportDisposition
    state: BoundaryState | None
    reason: ImportReason

    def __post_init__(self) -> None:
        if type(self.disposition) is not ImportDisposition:
            raise TypeError("ImportResult.disposition must be an exact ImportDisposition")
        if type(self.reason) is not ImportReason:
            raise TypeError("ImportResult.reason must be an exact ImportReason")
        if self.disposition is ImportDisposition.MIGRATED:
            if type(self.state) is not BoundaryState or self.reason is not ImportReason.NONE:
                raise ValueError("migrated import requires state and reason none")
        elif self.state is not None or self.reason is ImportReason.NONE:
            raise ValueError("non-migrated import requires no state and a concrete reason")


@dataclass(frozen=True, slots=True)
class ConversationIntent:
    kind: ConversationIntentKind
    source_event_id: str
    tool_name: str | None = None
    facts: tuple[TypedFact, ...] = ()
    target_offer_id: str | None = None
    confirmed_summary_version: int | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ConversationIntentKind:
            raise TypeError("ConversationIntent.kind must be exact")
        _require_exact_str(self.source_event_id, "ConversationIntent.source_event_id", identifier=True)
        _require_exact_members(self.facts, "ConversationIntent.facts", (TypedFact,))
        _require_unique_fact_names(self.facts)
        if self.kind is ConversationIntentKind.TOOL_REQUEST:
            _require_exact_str(self.tool_name, "ConversationIntent.tool_name", identifier=True)
        elif self.tool_name is not None:
            raise ValueError("tool_name is allowed only for tool_request")
        if self.kind is ConversationIntentKind.SELECT:
            _require_exact_str(self.target_offer_id, "ConversationIntent.target_offer_id", identifier=True)
        elif self.target_offer_id is not None:
            raise ValueError("target_offer_id is allowed only for select")
        if self.kind is ConversationIntentKind.CONFIRM:
            _require_exact_int(
                self.confirmed_summary_version,
                "ConversationIntent.confirmed_summary_version",
                minimum=1,
            )
        elif self.confirmed_summary_version is not None:
            raise ValueError("confirmed_summary_version is allowed only for confirm")


@dataclass(frozen=True, slots=True)
class IntentRequest:
    state: BoundaryState
    message: NormalizedMessage
    source_event_id: str
    deadline: datetime

    def __post_init__(self) -> None:
        if type(self.state) is not BoundaryState or type(self.message) is not NormalizedMessage:
            raise TypeError("IntentRequest state/message must be exact boundary types")
        _require_exact_str(self.source_event_id, "IntentRequest.source_event_id", identifier=True)
        _require_utc(self.deadline, "IntentRequest.deadline")


@dataclass(frozen=True, slots=True)
class ToolDispatchRequest:
    tool_name: str
    arguments: ToolArguments
    lead_key: str
    event_id: str
    deadline: datetime
    alias_depth: int = 0

    def __post_init__(self) -> None:
        _require_exact_str(self.tool_name, "ToolDispatchRequest.tool_name", identifier=True)
        if type(self.arguments) not in _TOOL_ARGUMENT_TYPES:
            raise TypeError("ToolDispatchRequest.arguments must be an exact ToolArguments variant")
        _require_exact_str(self.lead_key, "ToolDispatchRequest.lead_key", identifier=True)
        _require_exact_str(self.event_id, "ToolDispatchRequest.event_id", identifier=True)
        _require_utc(self.deadline, "ToolDispatchRequest.deadline")
        _require_exact_int(self.alias_depth, "ToolDispatchRequest.alias_depth", minimum=0)


@dataclass(frozen=True, slots=True)
class KernelDecision:
    state: BoundaryState
    commands: tuple[BoundaryCommand, ...]
    outbox: tuple[OutboxMessage, ...]
    read_requests: tuple[ToolDispatchRequest, ...]
    facts: tuple[TypedFact, ...]

    def __post_init__(self) -> None:
        if type(self.state) is not BoundaryState:
            raise TypeError("KernelDecision.state must be an exact BoundaryState")
        _require_exact_members(self.commands, "KernelDecision.commands", _BOUNDARY_COMMAND_TYPES)
        _require_exact_members(self.outbox, "KernelDecision.outbox", (OutboxMessage,))
        _require_exact_members(self.read_requests, "KernelDecision.read_requests", (ToolDispatchRequest,))
        _require_exact_members(self.facts, "KernelDecision.facts", (TypedFact,))
        _require_unique_fact_names(self.facts)


@dataclass(frozen=True, slots=True)
class BoundaryCommit:
    state: BoundaryState
    commands: tuple[BoundaryCommand, ...]
    outbox: tuple[OutboxMessage, ...]
    facts: tuple[TypedFact, ...]

    def __post_init__(self) -> None:
        if type(self.state) is not BoundaryState:
            raise TypeError("BoundaryCommit.state must be an exact BoundaryState")
        _require_exact_members(self.commands, "BoundaryCommit.commands", _BOUNDARY_COMMAND_TYPES)
        _require_exact_members(self.outbox, "BoundaryCommit.outbox", (OutboxMessage,))
        _require_exact_members(self.facts, "BoundaryCommit.facts", (TypedFact,))
        _require_unique_fact_names(self.facts)


@dataclass(frozen=True, slots=True)
class TurnEnvelope:
    lead_key: str
    event_id: str
    message: NormalizedMessage
    received_at: datetime
    deadline: datetime

    def __post_init__(self) -> None:
        _require_exact_str(self.lead_key, "TurnEnvelope.lead_key", identifier=True)
        _require_exact_str(self.event_id, "TurnEnvelope.event_id", identifier=True)
        if type(self.message) is not NormalizedMessage:
            raise TypeError("TurnEnvelope.message must be an exact NormalizedMessage")
        received = _require_utc(self.received_at, "TurnEnvelope.received_at")
        deadline = _require_utc(self.deadline, "TurnEnvelope.deadline")
        if deadline <= received:
            raise ValueError("TurnEnvelope.deadline must be after received_at")


@dataclass(frozen=True, slots=True)
class TurnPlan:
    state: BoundaryState
    public_messages: tuple[NormalizedMessage, ...]
    commands: tuple[BoundaryCommand, ...]
    outbox: tuple[OutboxMessage, ...]
    deduplicated: bool
    reason: TurnPlanReason

    def __post_init__(self) -> None:
        if type(self.state) is not BoundaryState:
            raise TypeError("TurnPlan.state must be an exact BoundaryState")
        _require_exact_members(self.public_messages, "TurnPlan.public_messages", (NormalizedMessage,))
        _require_exact_members(self.commands, "TurnPlan.commands", _BOUNDARY_COMMAND_TYPES)
        _require_exact_members(self.outbox, "TurnPlan.outbox", (OutboxMessage,))
        _require_exact_bool(self.deduplicated, "TurnPlan.deduplicated")
        if type(self.reason) is not TurnPlanReason:
            raise TypeError("TurnPlan.reason must be an exact TurnPlanReason")
        if self.deduplicated != (self.reason is TurnPlanReason.DUPLICATE):
            raise ValueError("deduplicated flag and reason duplicate must agree")


@dataclass(frozen=True, slots=True)
class VersionedBoundaryState:
    state: BoundaryState
    version: int
    semantic_hash: str

    def __post_init__(self) -> None:
        if type(self.state) is not BoundaryState:
            raise TypeError("VersionedBoundaryState.state must be exact")
        version = _require_exact_int(self.version, "VersionedBoundaryState.version", minimum=0)
        if version != self.state.version:
            raise ValueError("VersionedBoundaryState.version must equal state.version")
        digest = _require_exact_str(self.semantic_hash, "VersionedBoundaryState.semantic_hash")
        if _SHA256_RE.fullmatch(digest) is None:
            raise ValueError("semantic_hash must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class TurnLease:
    lead_key: str
    token: int
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_exact_str(self.lead_key, "TurnLease.lead_key", identifier=True)
        _require_exact_int(self.token, "TurnLease.token", minimum=1)
        _require_utc(self.expires_at, "TurnLease.expires_at")


PUBLIC_TYPES: Final = tuple(
    sorted(
        (
            ActivityDescriptionArguments,
            ActivityPaymentArguments,
            ActivityReadArguments,
            ActivityReservationArguments,
            BooleanSlot,
            BoundaryCommit,
            BoundaryState,
            CommandMigrationDisposition,
            ConversationIntent,
            ConversationIntentKind,
            DateSlot,
            DateTimeSlot,
            DecimalSlot,
            DispatchKind,
            DivergenceSeverity,
            FaqReadArguments,
            ImportDisposition,
            ImportReason,
            ImportResult,
            IntegerSlot,
            IntentRequest,
            KernelDecision,
            LegacyLeadSnapshot,
            LodgingPaymentArguments,
            LodgingReadArguments,
            LodgingReservationArguments,
            NormalizedMessage,
            RoomDescriptionArguments,
            StateCommitArguments,
            StringSlot,
            StripeLinkArguments,
            ToolDispatchRequest,
            TurnEnvelope,
            TurnLease,
            TurnPlan,
            TurnPlanReason,
            TypedFact,
            VersionedBoundaryState,
            WiseVerificationArguments,
        ),
        key=lambda item: item.__name__,
    )
)


__all__ = (
    "ActivityDescriptionArguments",
    "ActivityPaymentArguments",
    "ActivityReadArguments",
    "ActivityReservationArguments",
    "BooleanSlot",
    "BoundaryCommand",
    "BoundaryCommit",
    "BoundaryState",
    "CommandMigrationDisposition",
    "ConversationIntent",
    "ConversationIntentKind",
    "DateSlot",
    "DateTimeSlot",
    "DecimalSlot",
    "DispatchKind",
    "DivergenceSeverity",
    "FaqReadArguments",
    "ImportDisposition",
    "ImportReason",
    "ImportResult",
    "IntegerSlot",
    "IntentRequest",
    "KernelDecision",
    "LegacyLeadSnapshot",
    "LodgingPaymentArguments",
    "LodgingReadArguments",
    "LodgingReservationArguments",
    "NormalizedMessage",
    "PUBLIC_TYPES",
    "RoomDescriptionArguments",
    "SlotValue",
    "StateCommitArguments",
    "StringSlot",
    "StripeLinkArguments",
    "ToolArguments",
    "ToolDispatchRequest",
    "TurnEnvelope",
    "TurnLease",
    "TurnPlan",
    "TurnPlanReason",
    "TypedFact",
    "VersionedBoundaryState",
    "WiseVerificationArguments",
)
