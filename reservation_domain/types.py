"""Closed, immutable value objects, states and events for the reservation domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re
from typing import ClassVar, TypeAlias

SCHEMA_VERSION = 1
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")


def _require_id(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return normalized


def _require_hash(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return normalized


def _require_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_date(value: date, field_name: str) -> None:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError(f"{field_name} must be a date")


def _require_enum(value, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field_name} must be a {enum_type.__name__}")


def _stable_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class ServiceKind(str, Enum):
    LODGING = "lodging"
    ACTIVITY = "activity"


class LookupStatus(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNCERTAIN = "uncertain"


class ConfirmationDecisionKind(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    ADJUST = "adjust"
    AMBIGUOUS = "ambiguous"


class ExecutionCertainty(str, Enum):
    NOT_CALLED = "not_called"
    CALLED_NO_EFFECT = "called_no_effect"
    EFFECT_CONFIRMED = "effect_confirmed"
    CALLED_UNKNOWN = "called_unknown"


class TransitionStatus(str, Enum):
    APPLIED = "applied"
    IGNORED = "ignored"
    REJECTED = "rejected"


class WorkflowPhase(str, Enum):
    COLLECTING = "collecting"
    SEARCHING = "searching"
    OFFERED = "offered"
    SELECTED = "selected"
    READY_TO_SUMMARIZE = "ready_to_summarize"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_ADJUSTMENT = "awaiting_adjustment"
    EXECUTION_QUEUED = "execution_queued"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED_BEFORE_PROVIDER = "failed_before_provider"
    FAILED_NO_EFFECT = "failed_no_effect"
    UNCERTAIN = "uncertain"
    MANUAL_REVIEW = "manual_review"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReservationOperation(str, Enum):
    RESERVE_LODGING = "reserve_lodging"
    BOOK_ACTIVITY = "book_activity"
    RESERVE_PACKAGE = "reserve_package"


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        try:
            normalized = Decimal(str(self.amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("amount must be a decimal") from exc
        if not normalized.is_finite() or normalized < 0:
            raise ValueError("amount must be finite and non-negative")
        currency = str(self.currency or "").strip().upper()
        if not _CURRENCY_RE.fullmatch(currency):
            raise ValueError("currency must be an ISO-style three-letter code")
        object.__setattr__(self, "amount", normalized)
        object.__setattr__(self, "currency", currency)


@dataclass(frozen=True, slots=True)
class Party:
    adults: int
    children: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.adults, bool) or not isinstance(self.adults, int) or self.adults < 1:
            raise ValueError("adults must be an integer >= 1")
        if (
            isinstance(self.children, bool)
            or not isinstance(self.children, int)
            or self.children < 0
        ):
            raise ValueError("children must be an integer >= 0")


@dataclass(frozen=True, slots=True)
class CustomerFacts:
    customer_ref: str
    full_name: str
    email: str
    phone_e164: str
    country_code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "customer_ref",
            _require_id(self.customer_ref, "customer_ref"),
        )
        name = " ".join(str(self.full_name or "").split())
        if not name or len(name) > 200:
            raise ValueError("full_name must contain 1..200 normalized characters")
        email = str(self.email or "").strip().lower()
        if (
            len(email) > 254
            or email.count("@") != 1
            or any(char.isspace() for char in email)
            or email.startswith("@")
            or email.endswith("@")
        ):
            raise ValueError("email must be a normalized address")
        phone = str(self.phone_e164 or "").strip()
        if not _PHONE_RE.fullmatch(phone):
            raise ValueError("phone_e164 must use E.164 format")
        country = str(self.country_code or "").strip().upper()
        if not _COUNTRY_RE.fullmatch(country):
            raise ValueError("country_code must contain two letters")
        object.__setattr__(self, "full_name", name)
        object.__setattr__(self, "email", email)
        object.__setattr__(self, "phone_e164", phone)
        object.__setattr__(self, "country_code", country)


@dataclass(frozen=True, slots=True)
class AddOn:
    code: str
    quantity: int
    unit_price: Money

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_id(self.code, "add_on.code"))
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity < 1:
            raise ValueError("add_on.quantity must be an integer >= 1")

    @property
    def total(self) -> Money:
        return Money(
            amount=self.unit_price.amount * self.quantity,
            currency=self.unit_price.currency,
        )


@dataclass(frozen=True, slots=True)
class EconomicTerms:
    payment_method: str
    add_ons: tuple[AddOn, ...] = ()

    def __post_init__(self) -> None:
        payment = str(self.payment_method or "").strip().lower()
        if not _ID_RE.fullmatch(payment):
            raise ValueError("payment_method must be a canonical identifier")
        ordered = tuple(sorted(self.add_ons, key=lambda item: (item.code, item.quantity)))
        codes = [item.code for item in ordered]
        if len(codes) != len(set(codes)):
            raise ValueError("add_on codes must be unique")
        currencies = {item.unit_price.currency for item in ordered}
        if len(currencies) > 1:
            raise ValueError("all add_ons must use one currency")
        object.__setattr__(self, "payment_method", payment)
        object.__setattr__(self, "add_ons", ordered)


@dataclass(frozen=True, slots=True)
class SearchQuery:
    service: ServiceKind
    start_date: date
    end_date: date | None
    start_time: str | None
    party: Party

    def __post_init__(self) -> None:
        _require_enum(self.service, ServiceKind, "search_query.service")
        _require_date(self.start_date, "search_query.start_date")
        if self.end_date is not None:
            _require_date(self.end_date, "search_query.end_date")
        if self.service is ServiceKind.LODGING:
            if self.end_date is None or self.end_date <= self.start_date:
                raise ValueError("lodging query requires end_date after start_date")
        elif self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date cannot precede start_date")
        if self.start_time is not None:
            normalized = str(self.start_time).strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", normalized):
                raise ValueError("start_time must use HH:MM")
            object.__setattr__(self, "start_time", normalized)

    @property
    def signature(self) -> str:
        return _stable_hash(
            {
                "service": self.service.value,
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat() if self.end_date else None,
                "start_time": self.start_time,
                "party": {
                    "adults": self.party.adults,
                    "children": self.party.children,
                },
            }
        )


@dataclass(frozen=True, slots=True)
class LookupEvidence:
    lookup_id: str
    service: ServiceKind
    query_signature: str
    observed_at: datetime
    expires_at: datetime
    snapshot_hash: str
    status: LookupStatus

    def __post_init__(self) -> None:
        _require_enum(self.service, ServiceKind, "lookup_evidence.service")
        _require_enum(self.status, LookupStatus, "lookup_evidence.status")
        object.__setattr__(self, "lookup_id", _require_id(self.lookup_id, "lookup_id"))
        object.__setattr__(
            self,
            "query_signature",
            _require_hash(self.query_signature, "query_signature"),
        )
        object.__setattr__(
            self,
            "snapshot_hash",
            _require_hash(self.snapshot_hash, "snapshot_hash"),
        )
        observed = _require_utc(self.observed_at, "observed_at")
        expires = _require_utc(self.expires_at, "expires_at")
        if expires <= observed:
            raise ValueError("expires_at must be after observed_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)

    def is_fresh(self, at: datetime) -> bool:
        instant = _require_utc(at, "at")
        return self.observed_at <= instant < self.expires_at


@dataclass(frozen=True, slots=True)
class OfferSnapshot:
    offer_id: str
    lookup_id: str
    service: ServiceKind
    provider_ref: str
    public_label: str
    start_date: date
    end_date: date | None
    start_time: str | None
    party: Party
    total: Money
    available: bool

    def __post_init__(self) -> None:
        _require_enum(self.service, ServiceKind, "offer.service")
        _require_date(self.start_date, "offer.start_date")
        if self.end_date is not None:
            _require_date(self.end_date, "offer.end_date")
        if not isinstance(self.available, bool):
            raise ValueError("offer.available must be boolean")
        object.__setattr__(self, "offer_id", _require_id(self.offer_id, "offer_id"))
        object.__setattr__(self, "lookup_id", _require_id(self.lookup_id, "lookup_id"))
        object.__setattr__(
            self,
            "provider_ref",
            _require_id(self.provider_ref, "provider_ref"),
        )
        label = str(self.public_label or "").strip()
        if not label:
            raise ValueError("public_label is required for presentation")
        object.__setattr__(self, "public_label", label)
        if self.service is ServiceKind.LODGING:
            if self.end_date is None or self.end_date <= self.start_date:
                raise ValueError("lodging offer requires an end_date after start_date")
        elif self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date cannot precede start_date")
        if self.start_time is not None:
            normalized = str(self.start_time).strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", normalized):
                raise ValueError("start_time must use HH:MM")
            object.__setattr__(self, "start_time", normalized)


def _validate_commercial_components(
    components: tuple[OfferSnapshot, ...],
    terms: EconomicTerms,
) -> None:
    if any(not component.available for component in components):
        raise ValueError("commercial components must be available")
    currencies = {component.total.currency for component in components}
    if len(currencies) != 1:
        raise ValueError("commercial components must use one currency")
    add_on_currencies = {item.unit_price.currency for item in terms.add_ons}
    if add_on_currencies and add_on_currencies != currencies:
        raise ValueError("add_on currency must match commercial components")


@dataclass(frozen=True, slots=True)
class CommercialDraft:
    draft_id: str
    version: int
    created_at: datetime
    components: tuple[OfferSnapshot, ...]
    customer: CustomerFacts
    terms: EconomicTerms
    subject_signature: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "draft_id", _require_id(self.draft_id, "draft_id"))
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("draft.version must be an integer >= 1")
        object.__setattr__(self, "created_at", _require_utc(self.created_at, "created_at"))
        ordered = tuple(sorted(self.components, key=lambda item: item.offer_id))
        if not ordered:
            raise ValueError("draft requires at least one component")
        if len({item.offer_id for item in ordered}) != len(ordered):
            raise ValueError("draft component offer_ids must be unique")
        _validate_commercial_components(ordered, self.terms)
        object.__setattr__(self, "components", ordered)
        object.__setattr__(
            self,
            "subject_signature",
            _require_hash(self.subject_signature, "subject_signature"),
        )
        from .signature import subject_signature as calculate_subject_signature

        expected = calculate_subject_signature(
            components=ordered,
            customer=self.customer,
            terms=self.terms,
        )
        if self.subject_signature != expected:
            raise ValueError("draft subject_signature does not match canonical subject")


@dataclass(frozen=True, slots=True)
class SummaryPresented:
    summary_event_id: str
    draft_id: str
    draft_version: int
    subject_signature: str
    outbox_message_id: str
    presented_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "summary_event_id",
            _require_id(self.summary_event_id, "summary_event_id"),
        )
        object.__setattr__(self, "draft_id", _require_id(self.draft_id, "draft_id"))
        if self.draft_version < 1:
            raise ValueError("summary draft_version must be >= 1")
        object.__setattr__(
            self,
            "subject_signature",
            _require_hash(self.subject_signature, "subject_signature"),
        )
        object.__setattr__(
            self,
            "outbox_message_id",
            _require_id(self.outbox_message_id, "outbox_message_id"),
        )
        object.__setattr__(
            self,
            "presented_at",
            _require_utc(self.presented_at, "presented_at"),
        )


@dataclass(frozen=True, slots=True)
class ConfirmationRecord:
    confirmation_event_id: str
    decision: ConfirmationDecisionKind
    target_draft_version: int
    subject_signature: str
    decided_at: datetime

    def __post_init__(self) -> None:
        _require_enum(
            self.decision,
            ConfirmationDecisionKind,
            "confirmation.decision",
        )
        object.__setattr__(
            self,
            "confirmation_event_id",
            _require_id(self.confirmation_event_id, "confirmation_event_id"),
        )
        if self.target_draft_version < 1:
            raise ValueError("target_draft_version must be >= 1")
        object.__setattr__(
            self,
            "subject_signature",
            _require_hash(self.subject_signature, "subject_signature"),
        )
        object.__setattr__(self, "decided_at", _require_utc(self.decided_at, "decided_at"))


@dataclass(frozen=True, slots=True)
class CommandPayload:
    components: tuple[OfferSnapshot, ...]
    customer: CustomerFacts
    terms: EconomicTerms

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.components, key=lambda item: item.offer_id))
        if not ordered:
            raise ValueError("command payload requires components")
        _validate_commercial_components(ordered, self.terms)
        object.__setattr__(self, "components", ordered)


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    command_id: str
    certainty: ExecutionCertainty
    normalized_status: str
    provider_reference: str | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_enum(self.certainty, ExecutionCertainty, "outcome.certainty")
        object.__setattr__(self, "command_id", _require_id(self.command_id, "command_id"))
        status = str(self.normalized_status or "").strip().lower()
        if not _ID_RE.fullmatch(status):
            raise ValueError("normalized_status must be a canonical identifier")
        object.__setattr__(self, "normalized_status", status)
        if self.provider_reference is not None:
            object.__setattr__(
                self,
                "provider_reference",
                _require_id(self.provider_reference, "provider_reference"),
            )
        normalized_evidence = tuple(sorted({_require_hash(item, "evidence") for item in self.evidence}))
        object.__setattr__(self, "evidence", normalized_evidence)
        if self.certainty is ExecutionCertainty.EFFECT_CONFIRMED and not self.provider_reference:
            raise ValueError("effect_confirmed requires provider_reference")


@dataclass(frozen=True, slots=True)
class ReservationCommand:
    TYPE: ClassVar[str] = "reservation_command"

    command_id: str
    idempotency_key: str
    workflow_id: str
    draft_id: str
    draft_version: int
    subject_signature: str
    operation: ReservationOperation
    payload: CommandPayload
    created_at: datetime

    def __post_init__(self) -> None:
        _require_enum(self.operation, ReservationOperation, "command.operation")
        object.__setattr__(self, "command_id", _require_id(self.command_id, "command_id"))
        object.__setattr__(
            self,
            "idempotency_key",
            _require_id(self.idempotency_key, "idempotency_key"),
        )
        object.__setattr__(self, "workflow_id", _require_id(self.workflow_id, "workflow_id"))
        object.__setattr__(self, "draft_id", _require_id(self.draft_id, "draft_id"))
        if self.draft_version < 1:
            raise ValueError("command draft_version must be >= 1")
        object.__setattr__(
            self,
            "subject_signature",
            _require_hash(self.subject_signature, "subject_signature"),
        )
        object.__setattr__(self, "created_at", _require_utc(self.created_at, "created_at"))
        from .signature import (
            command_identity,
            operation_for_components,
            subject_signature as calculate_subject_signature,
        )

        expected_signature = calculate_subject_signature(
            components=self.payload.components,
            customer=self.payload.customer,
            terms=self.payload.terms,
        )
        if self.subject_signature != expected_signature:
            raise ValueError("command signature does not match canonical payload")
        expected_operation = operation_for_components(self.payload.components)
        if self.operation is not expected_operation:
            raise ValueError("command operation does not match component services")
        expected_id, expected_key = command_identity(
            workflow_id=self.workflow_id,
            draft_id=self.draft_id,
            draft_version=self.draft_version,
            signature=self.subject_signature,
            operation=self.operation,
        )
        if self.command_id != expected_id or self.idempotency_key != expected_key:
            raise ValueError("command identity is not deterministic for its subject")

    def outcome(
        self,
        *,
        certainty: ExecutionCertainty,
        normalized_status: str,
        provider_reference: str | None = None,
        evidence: tuple[str, ...] = (),
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            command_id=self.command_id,
            certainty=certainty,
            normalized_status=normalized_status,
            provider_reference=provider_reference,
            evidence=evidence,
        )


@dataclass(frozen=True, slots=True)
class StateMeta:
    workflow_id: str
    revision: int
    last_event_at: datetime
    seen_event_ids: tuple[str, ...] = ()
    seen_event_hashes: tuple[str, ...] = ()
    command_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", _require_id(self.workflow_id, "workflow_id"))
        if self.revision < 0:
            raise ValueError("revision must be >= 0")
        object.__setattr__(
            self,
            "last_event_at",
            _require_utc(self.last_event_at, "last_event_at"),
        )
        if len(set(self.seen_event_ids)) != len(self.seen_event_ids):
            raise ValueError("seen_event_ids must be unique")
        if self.revision != len(self.seen_event_ids):
            raise ValueError("revision must equal the number of processed event IDs")
        if len(self.seen_event_hashes) != len(self.seen_event_ids):
            raise ValueError("every processed event ID must have a payload hash")
        if len(set(self.command_ids)) != len(self.command_ids):
            raise ValueError("command_ids must be unique")
        if len(self.command_ids) > 1:
            raise ValueError("a workflow may contain at most one command ID")
        for value in self.seen_event_ids:
            _require_id(value, "seen_event_id")
        for value in self.seen_event_hashes:
            _require_hash(value, "seen_event_hash")
        for value in self.command_ids:
            _require_id(value, "command_id")


class WorkflowState:
    TYPE: ClassVar[str]
    PHASE: ClassVar[WorkflowPhase]
    meta: StateMeta

    @property
    def phase(self) -> WorkflowPhase:
        return self.PHASE

    @property
    def command_ids(self) -> tuple[str, ...]:
        return self.meta.command_ids


def _offer_matches_query(offer: OfferSnapshot, query: SearchQuery) -> bool:
    return bool(
        offer.service is query.service
        and offer.start_date == query.start_date
        and offer.end_date == query.end_date
        and offer.start_time == query.start_time
        and offer.party == query.party
    )


def _validate_offer_evidence(
    query: SearchQuery,
    evidence: LookupEvidence,
    offers: tuple[OfferSnapshot, ...],
) -> None:
    if (
        evidence.status is not LookupStatus.POSITIVE
        or evidence.service is not query.service
        or evidence.query_signature != query.signature
    ):
        raise ValueError("offer state evidence does not match its query")
    if any(
        not offer.available
        or offer.lookup_id != evidence.lookup_id
        or offer.service is not evidence.service
        or not _offer_matches_query(offer, query)
        for offer in offers
    ):
        raise ValueError("offer state contains an offer outside its evidence/query")


def _validate_summary_binding(
    draft: CommercialDraft,
    summary: SummaryPresented,
) -> None:
    if (
        summary.draft_id != draft.draft_id
        or summary.draft_version != draft.version
        or summary.subject_signature != draft.subject_signature
        or summary.presented_at < draft.created_at
    ):
        raise ValueError("summary does not bind to the current commercial draft")


def _validate_authorization_binding(
    draft: CommercialDraft,
    summary: SummaryPresented,
    confirmation: ConfirmationRecord,
    command: ReservationCommand,
) -> None:
    _validate_summary_binding(draft, summary)
    if (
        confirmation.decision is not ConfirmationDecisionKind.ACCEPT
        or confirmation.target_draft_version != draft.version
        or confirmation.subject_signature != draft.subject_signature
        or confirmation.decided_at <= summary.presented_at
    ):
        raise ValueError("confirmation does not authorize the presented draft")
    if (
        command.draft_id != draft.draft_id
        or command.draft_version != draft.version
        or command.subject_signature != draft.subject_signature
        or command.payload.components != draft.components
        or command.payload.customer != draft.customer
        or command.payload.terms != draft.terms
        or command.created_at != confirmation.decided_at
    ):
        raise ValueError("command does not bind to the authorized draft")


def _validate_outcome_binding(
    command: ReservationCommand,
    outcome: ExecutionOutcome,
    certainty: ExecutionCertainty,
) -> None:
    if outcome.command_id != command.command_id or outcome.certainty is not certainty:
        raise ValueError("execution outcome does not match command/state certainty")


def _canonical_reason(value: str) -> str:
    reason = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(reason):
        raise ValueError("state reason must be a canonical identifier")
    return reason


@dataclass(frozen=True, slots=True)
class CollectingState(WorkflowState):
    TYPE: ClassVar[str] = "collecting"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.COLLECTING
    meta: StateMeta


@dataclass(frozen=True, slots=True)
class SearchingState(WorkflowState):
    TYPE: ClassVar[str] = "searching"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.SEARCHING
    meta: StateMeta
    query: SearchQuery


@dataclass(frozen=True, slots=True)
class OfferedState(WorkflowState):
    TYPE: ClassVar[str] = "offered"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.OFFERED
    meta: StateMeta
    query: SearchQuery
    evidence: LookupEvidence
    offers: tuple[OfferSnapshot, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.offers, key=lambda item: item.offer_id))
        if not ordered:
            raise ValueError("offered state requires offers")
        if len({item.offer_id for item in ordered}) != len(ordered):
            raise ValueError("offered state offer_ids must be unique")
        _validate_offer_evidence(self.query, self.evidence, ordered)
        object.__setattr__(self, "offers", ordered)


@dataclass(frozen=True, slots=True)
class SelectedState(WorkflowState):
    TYPE: ClassVar[str] = "selected"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.SELECTED
    meta: StateMeta
    query: SearchQuery
    evidence: LookupEvidence
    offer: OfferSnapshot

    def __post_init__(self) -> None:
        _validate_offer_evidence(self.query, self.evidence, (self.offer,))


@dataclass(frozen=True, slots=True)
class ReadyToSummarizeState(WorkflowState):
    TYPE: ClassVar[str] = "ready_to_summarize"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.READY_TO_SUMMARIZE
    meta: StateMeta
    draft: CommercialDraft


@dataclass(frozen=True, slots=True)
class AwaitingConfirmationState(WorkflowState):
    TYPE: ClassVar[str] = "awaiting_confirmation"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.AWAITING_CONFIRMATION
    meta: StateMeta
    draft: CommercialDraft
    summary: SummaryPresented

    def __post_init__(self) -> None:
        _validate_summary_binding(self.draft, self.summary)


@dataclass(frozen=True, slots=True)
class AwaitingAdjustmentState(WorkflowState):
    TYPE: ClassVar[str] = "awaiting_adjustment"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.AWAITING_ADJUSTMENT
    meta: StateMeta
    draft: CommercialDraft
    summary: SummaryPresented
    decision: ConfirmationRecord

    def __post_init__(self) -> None:
        _validate_summary_binding(self.draft, self.summary)
        if (
            self.decision.decision is not ConfirmationDecisionKind.ADJUST
            or self.decision.target_draft_version != self.draft.version
            or self.decision.subject_signature != self.draft.subject_signature
            or self.decision.decided_at <= self.summary.presented_at
        ):
            raise ValueError("adjustment decision does not bind to presented draft")


@dataclass(frozen=True, slots=True)
class ExecutionQueuedState(WorkflowState):
    TYPE: ClassVar[str] = "execution_queued"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.EXECUTION_QUEUED
    meta: StateMeta
    draft: CommercialDraft
    summary: SummaryPresented
    confirmation: ConfirmationRecord
    command: ReservationCommand

    def __post_init__(self) -> None:
        _validate_authorization_binding(
            self.draft,
            self.summary,
            self.confirmation,
            self.command,
        )


@dataclass(frozen=True, slots=True)
class ExecutingState(WorkflowState):
    TYPE: ClassVar[str] = "executing"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.EXECUTING
    meta: StateMeta
    draft: CommercialDraft
    summary: SummaryPresented
    confirmation: ConfirmationRecord
    command: ReservationCommand
    attempt: int

    def __post_init__(self) -> None:
        _validate_authorization_binding(
            self.draft,
            self.summary,
            self.confirmation,
            self.command,
        )
        if self.attempt != 1:
            raise ValueError("Phase 2 permits exactly one execution attempt")


@dataclass(frozen=True, slots=True)
class SucceededState(WorkflowState):
    TYPE: ClassVar[str] = "succeeded"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.SUCCEEDED
    meta: StateMeta
    command: ReservationCommand
    outcome: ExecutionOutcome

    def __post_init__(self) -> None:
        _validate_outcome_binding(
            self.command,
            self.outcome,
            ExecutionCertainty.EFFECT_CONFIRMED,
        )


@dataclass(frozen=True, slots=True)
class FailedBeforeProviderState(WorkflowState):
    TYPE: ClassVar[str] = "failed_before_provider"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.FAILED_BEFORE_PROVIDER
    meta: StateMeta
    command: ReservationCommand
    outcome: ExecutionOutcome

    def __post_init__(self) -> None:
        _validate_outcome_binding(
            self.command,
            self.outcome,
            ExecutionCertainty.NOT_CALLED,
        )


@dataclass(frozen=True, slots=True)
class FailedNoEffectState(WorkflowState):
    TYPE: ClassVar[str] = "failed_no_effect"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.FAILED_NO_EFFECT
    meta: StateMeta
    command: ReservationCommand
    outcome: ExecutionOutcome

    def __post_init__(self) -> None:
        _validate_outcome_binding(
            self.command,
            self.outcome,
            ExecutionCertainty.CALLED_NO_EFFECT,
        )


@dataclass(frozen=True, slots=True)
class UncertainState(WorkflowState):
    TYPE: ClassVar[str] = "uncertain"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.UNCERTAIN
    meta: StateMeta
    command: ReservationCommand
    outcome: ExecutionOutcome

    def __post_init__(self) -> None:
        _validate_outcome_binding(
            self.command,
            self.outcome,
            ExecutionCertainty.CALLED_UNKNOWN,
        )


@dataclass(frozen=True, slots=True)
class ManualReviewState(WorkflowState):
    TYPE: ClassVar[str] = "manual_review"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.MANUAL_REVIEW
    meta: StateMeta
    command: ReservationCommand
    outcome: ExecutionOutcome
    reason: str

    def __post_init__(self) -> None:
        _validate_outcome_binding(
            self.command,
            self.outcome,
            ExecutionCertainty.CALLED_UNKNOWN,
        )
        object.__setattr__(self, "reason", _canonical_reason(self.reason))


@dataclass(frozen=True, slots=True)
class CancelledState(WorkflowState):
    TYPE: ClassVar[str] = "cancelled"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.CANCELLED
    meta: StateMeta
    previous_phase: WorkflowPhase
    reason: str

    def __post_init__(self) -> None:
        _require_enum(self.previous_phase, WorkflowPhase, "previous_phase")
        object.__setattr__(self, "reason", _canonical_reason(self.reason))


@dataclass(frozen=True, slots=True)
class ExpiredState(WorkflowState):
    TYPE: ClassVar[str] = "expired"
    PHASE: ClassVar[WorkflowPhase] = WorkflowPhase.EXPIRED
    meta: StateMeta
    previous_phase: WorkflowPhase
    reason: str

    def __post_init__(self) -> None:
        _require_enum(self.previous_phase, WorkflowPhase, "previous_phase")
        object.__setattr__(self, "reason", _canonical_reason(self.reason))


State: TypeAlias = (
    CollectingState
    | SearchingState
    | OfferedState
    | SelectedState
    | ReadyToSummarizeState
    | AwaitingConfirmationState
    | AwaitingAdjustmentState
    | ExecutionQueuedState
    | ExecutingState
    | SucceededState
    | FailedBeforeProviderState
    | FailedNoEffectState
    | UncertainState
    | ManualReviewState
    | CancelledState
    | ExpiredState
)


def validate_state_consistency(state: State) -> None:
    """Validate cross-object invariants required at persistence boundaries."""

    command_states = (
        ExecutionQueuedState,
        ExecutingState,
        SucceededState,
        FailedBeforeProviderState,
        FailedNoEffectState,
        UncertainState,
        ManualReviewState,
    )
    if isinstance(state, command_states):
        if state.command.workflow_id != state.meta.workflow_id:
            raise ValueError("state command belongs to a different workflow")
        if state.meta.command_ids != (state.command.command_id,):
            raise ValueError("command-bearing state must record exactly its command ID")
    elif state.meta.command_ids:
        raise ValueError("pre-command or non-command state cannot record command IDs")
    internal_times: list[datetime] = []
    if isinstance(
        state,
        (ReadyToSummarizeState, AwaitingConfirmationState, AwaitingAdjustmentState),
    ):
        internal_times.append(state.draft.created_at)
    if isinstance(state, AwaitingAdjustmentState):
        internal_times.extend((state.summary.presented_at, state.decision.decided_at))
    if isinstance(state, (ExecutionQueuedState, ExecutingState)):
        internal_times.extend(
            (
                state.draft.created_at,
                state.summary.presented_at,
                state.confirmation.decided_at,
                state.command.created_at,
            )
        )
    if isinstance(
        state,
        (
            SucceededState,
            FailedBeforeProviderState,
            FailedNoEffectState,
            UncertainState,
            ManualReviewState,
        ),
    ):
        internal_times.append(state.command.created_at)
    if internal_times and max(internal_times) > state.meta.last_event_at:
        raise ValueError("state metadata predates an embedded domain record")


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    TYPE: ClassVar[str]
    event_id: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_id(self.event_id, "event_id"))
        object.__setattr__(self, "occurred_at", _require_utc(self.occurred_at, "occurred_at"))


@dataclass(frozen=True, slots=True, kw_only=True)
class StartSearch(DomainEvent):
    TYPE: ClassVar[str] = "start_search"
    query: SearchQuery


@dataclass(frozen=True, slots=True, kw_only=True)
class LookupRecorded(DomainEvent):
    TYPE: ClassVar[str] = "lookup_recorded"
    evidence: LookupEvidence
    offers: tuple[OfferSnapshot, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class OfferChosen(DomainEvent):
    TYPE: ClassVar[str] = "offer_chosen"
    offer_id: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        object.__setattr__(self, "offer_id", _require_id(self.offer_id, "offer_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftRequested(DomainEvent):
    TYPE: ClassVar[str] = "draft_requested"
    draft_id: str
    customer: CustomerFacts
    terms: EconomicTerms

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        object.__setattr__(self, "draft_id", _require_id(self.draft_id, "draft_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftAdjusted(DomainEvent):
    TYPE: ClassVar[str] = "draft_adjusted"
    customer: CustomerFacts
    terms: EconomicTerms


@dataclass(frozen=True, slots=True, kw_only=True)
class SummaryRecorded(DomainEvent):
    TYPE: ClassVar[str] = "summary_recorded"
    summary_event_id: str
    draft_version: int
    subject_signature: str
    outbox_message_id: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        object.__setattr__(
            self,
            "summary_event_id",
            _require_id(self.summary_event_id, "summary_event_id"),
        )
        if self.draft_version < 1:
            raise ValueError("draft_version must be >= 1")
        object.__setattr__(
            self,
            "subject_signature",
            _require_hash(self.subject_signature, "subject_signature"),
        )
        object.__setattr__(
            self,
            "outbox_message_id",
            _require_id(self.outbox_message_id, "outbox_message_id"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationReceived(DomainEvent):
    TYPE: ClassVar[str] = "confirmation_received"
    confirmation_event_id: str
    decision: ConfirmationDecisionKind
    target_draft_version: int
    subject_signature: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        _require_enum(
            self.decision,
            ConfirmationDecisionKind,
            "confirmation.decision",
        )
        object.__setattr__(
            self,
            "confirmation_event_id",
            _require_id(self.confirmation_event_id, "confirmation_event_id"),
        )
        if self.target_draft_version < 1:
            raise ValueError("target_draft_version must be >= 1")
        object.__setattr__(
            self,
            "subject_signature",
            _require_hash(self.subject_signature, "subject_signature"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionStarted(DomainEvent):
    TYPE: ClassVar[str] = "execution_started"
    command_id: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        object.__setattr__(self, "command_id", _require_id(self.command_id, "command_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionFinished(DomainEvent):
    TYPE: ClassVar[str] = "execution_finished"
    command_id: str
    outcome: ExecutionOutcome

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        object.__setattr__(self, "command_id", _require_id(self.command_id, "command_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ManualReviewRequested(DomainEvent):
    TYPE: ClassVar[str] = "manual_review_requested"
    reason: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        reason = str(self.reason or "").strip().lower()
        if not _ID_RE.fullmatch(reason):
            raise ValueError("reason must be a canonical identifier")
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkflowCancelled(DomainEvent):
    TYPE: ClassVar[str] = "workflow_cancelled"
    reason: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        reason = str(self.reason or "").strip().lower()
        if not _ID_RE.fullmatch(reason):
            raise ValueError("reason must be a canonical identifier")
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkflowExpired(DomainEvent):
    TYPE: ClassVar[str] = "workflow_expired"
    reason: str

    def __post_init__(self) -> None:
        DomainEvent.__post_init__(self)
        reason = str(self.reason or "").strip().lower()
        if not _ID_RE.fullmatch(reason):
            raise ValueError("reason must be a canonical identifier")
        object.__setattr__(self, "reason", reason)


Event: TypeAlias = (
    StartSearch
    | LookupRecorded
    | OfferChosen
    | DraftRequested
    | DraftAdjusted
    | SummaryRecorded
    | ConfirmationReceived
    | ExecutionStarted
    | ExecutionFinished
    | ManualReviewRequested
    | WorkflowCancelled
    | WorkflowExpired
)


STATE_TYPES = (
    CollectingState,
    SearchingState,
    OfferedState,
    SelectedState,
    ReadyToSummarizeState,
    AwaitingConfirmationState,
    AwaitingAdjustmentState,
    ExecutionQueuedState,
    ExecutingState,
    SucceededState,
    FailedBeforeProviderState,
    FailedNoEffectState,
    UncertainState,
    ManualReviewState,
    CancelledState,
    ExpiredState,
)

EVENT_TYPES = (
    StartSearch,
    LookupRecorded,
    OfferChosen,
    DraftRequested,
    DraftAdjusted,
    SummaryRecorded,
    ConfirmationReceived,
    ExecutionStarted,
    ExecutionFinished,
    ManualReviewRequested,
    WorkflowCancelled,
    WorkflowExpired,
)

__all__ = [
    "SCHEMA_VERSION",
    "ServiceKind",
    "LookupStatus",
    "ConfirmationDecisionKind",
    "ExecutionCertainty",
    "TransitionStatus",
    "WorkflowPhase",
    "ReservationOperation",
    "Money",
    "Party",
    "CustomerFacts",
    "AddOn",
    "EconomicTerms",
    "SearchQuery",
    "LookupEvidence",
    "OfferSnapshot",
    "CommercialDraft",
    "SummaryPresented",
    "ConfirmationRecord",
    "CommandPayload",
    "ExecutionOutcome",
    "ReservationCommand",
    "StateMeta",
    "WorkflowState",
    "CollectingState",
    "SearchingState",
    "OfferedState",
    "SelectedState",
    "ReadyToSummarizeState",
    "AwaitingConfirmationState",
    "AwaitingAdjustmentState",
    "ExecutionQueuedState",
    "ExecutingState",
    "SucceededState",
    "FailedBeforeProviderState",
    "FailedNoEffectState",
    "UncertainState",
    "ManualReviewState",
    "CancelledState",
    "ExpiredState",
    "State",
    "validate_state_consistency",
    "DomainEvent",
    "StartSearch",
    "LookupRecorded",
    "OfferChosen",
    "DraftRequested",
    "DraftAdjusted",
    "SummaryRecorded",
    "ConfirmationReceived",
    "ExecutionStarted",
    "ExecutionFinished",
    "ManualReviewRequested",
    "WorkflowCancelled",
    "WorkflowExpired",
    "Event",
    "STATE_TYPES",
    "EVENT_TYPES",
]
