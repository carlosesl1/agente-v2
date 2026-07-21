"""Fail-closed staged importer for the observed legacy LeadState schema."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Final

from reservation_domain import (
    AwaitingConfirmationState,
    CommercialDraft,
    ExecutionCertainty,
    ExecutionQueuedState,
    ReadyToSummarizeState,
    SelectedState,
    ServiceKind,
    SucceededState,
    UncertainState,
    dumps_outcome,
    loads_state,
    new_workflow,
)
from reservation_confirmation import SummaryLocale, render_summary
from reservation_followup import (
    BusinessUnit,
    HandoffEffectPolicy,
    HandoffReasonCode,
    HandoffRequested,
    PaymentStatus,
    PaymentWorkflow,
    from_wire_json as from_phase6_wire_json,
    new_handoff,
)

from reservation_boundary.types import (
    BoundaryState,
    ImportDisposition,
    ImportReason,
    ImportResult,
    LegacyLeadSnapshot,
)


_SOURCE: Final = "chapada-leads-hermes"
_ROOT_FIELDS: Final = frozenset(
    (
        "phone",
        "subscriber_id",
        "lead_key",
        "language",
        "is_foreign",
        "ai_status",
        "stage",
        "desired_services",
        "missing_slots",
        "memory_long",
        "hostel_reservations",
        "agency_bookings",
        "metadata",
    )
)
_COLLECTING_STAGES: Final = frozenset(("new", "hostel", "agencia"))
_MANUAL_STAGES: Final = frozenset(("fechamento", "no_reply"))
_HANDOFF_STAGES: Final = frozenset(("handoff", "recepcionista"))
_ADVANCED_STAGES: Final = frozenset(("fechamento", "payment_pending", "completed"))
_ADVANCED_STATE_TYPES: Final = frozenset(
    (
        SelectedState,
        ReadyToSummarizeState,
        AwaitingConfirmationState,
        ExecutionQueuedState,
        SucceededState,
    )
)
_ADVANCED_KEYS: Final = frozenset(
    (
        "selected_offer_id",
        "offer_id",
        "canonical_offer_id",
        "summary_version",
        "confirmed_summary_version",
        "confirmation_signature",
        "rendered_summary_hash",
        "reservation_status",
        "payment_status",
        "phase6_payment_wires",
    )
)
_ACTIVE_RESERVATION_STATUSES: Final = frozenset(
    (
        "active",
        "confirmed",
        "reserved",
        "reservation_confirmed",
        "payment_pending",
        "pending_payment",
    )
)
_UNPAID_PAYMENT_STATUSES: Final = frozenset(("", "pending"))
_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


class _ClassifiedInput(ValueError):
    def __init__(self, disposition: ImportDisposition, reason: ImportReason) -> None:
        super().__init__(reason.value)
        self.disposition = disposition
        self.reason = reason


def _fail(reason: ImportReason) -> None:
    raise _ClassifiedInput(ImportDisposition.REJECTED, reason)


def _review(reason: ImportReason) -> None:
    raise _ClassifiedInput(ImportDisposition.MANUAL_REVIEW, reason)


def _result(exc: _ClassifiedInput) -> ImportResult:
    return ImportResult(exc.disposition, None, exc.reason)


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    if value is None or type(value) in (str, int, bool, float):
        return value
    _fail(ImportReason.MALFORMED)


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise _ClassifiedInput(
            ImportDisposition.REJECTED,
            ImportReason.MALFORMED,
        ) from exc


def _exact_text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        _fail(ImportReason.MALFORMED)
    if not allow_empty and (not value or value != value.strip()):
        _fail(ImportReason.MALFORMED)
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value):
        _fail(ImportReason.MALFORMED)
    return value


def _identifier(value: object, *, missing_reason: ImportReason) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(missing_reason)
    return value


def _exact_string_tuple(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        _fail(ImportReason.MALFORMED)
    result = []
    for item in value:
        result.append(_exact_text(item, "tuple item"))
    return tuple(result)


def _exact_mapping_tuple(value: object) -> tuple[Mapping[str, object], ...]:
    if type(value) is not tuple:
        _fail(ImportReason.MALFORMED)
    if any(not isinstance(item, Mapping) for item in value):
        _fail(ImportReason.MALFORMED)
    return value


def _utc(value: object) -> datetime:
    text = _exact_text(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _ClassifiedInput(
            ImportDisposition.MANUAL_REVIEW,
            ImportReason.MISSING_PROVENANCE,
        ) from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat() != text
    ):
        _review(ImportReason.MISSING_PROVENANCE)
    return parsed


def _validated_fields(snapshot: LegacyLeadSnapshot) -> Mapping[str, object]:
    if type(snapshot) is not LegacyLeadSnapshot:
        raise TypeError("snapshot must be the exact LegacyLeadSnapshot type")
    if snapshot.schema_version != 1 or snapshot.source != _SOURCE:
        _fail(ImportReason.UNSUPPORTED_SCHEMA)
    thawed = _thaw(snapshot.raw_fields)
    if type(thawed) is not dict or set(thawed) != _ROOT_FIELDS:
        _fail(ImportReason.MALFORMED)
    canonical = _canonical(thawed)
    if canonical != snapshot.canonical_json:
        _fail(ImportReason.MALFORMED)
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != snapshot.snapshot_hash:
        _fail(ImportReason.MALFORMED)

    _exact_text(thawed["phone"], "phone")
    _exact_text(thawed["subscriber_id"], "subscriber_id")
    _identifier(thawed["lead_key"], missing_reason=ImportReason.MISSING_IDENTITY)
    _exact_text(thawed["language"], "language")
    if thawed["is_foreign"] is not None and type(thawed["is_foreign"]) is not bool:
        _fail(ImportReason.MALFORMED)
    _exact_text(thawed["ai_status"], "ai_status")
    _exact_text(thawed["stage"], "stage")
    _exact_string_tuple(tuple(thawed["desired_services"]) if type(thawed["desired_services"]) is list else thawed["desired_services"])
    _exact_string_tuple(tuple(thawed["missing_slots"]) if type(thawed["missing_slots"]) is list else thawed["missing_slots"])
    _exact_text(thawed["memory_long"], "memory_long", allow_empty=True)
    _exact_mapping_tuple(tuple(thawed["hostel_reservations"]) if type(thawed["hostel_reservations"]) is list else thawed["hostel_reservations"])
    _exact_mapping_tuple(tuple(thawed["agency_bookings"]) if type(thawed["agency_bookings"]) is list else thawed["agency_bookings"])
    if type(thawed["metadata"]) is not dict:
        _fail(ImportReason.MALFORMED)
    return thawed


def _metadata(fields: Mapping[str, object]) -> Mapping[str, object]:
    value = fields["metadata"]
    if not isinstance(value, Mapping):
        _fail(ImportReason.MALFORMED)
    return value


def _validate_canonical_identity(metadata: Mapping[str, object]) -> None:
    values: list[str] = []
    for key in ("selected_offer_id", "offer_id", "canonical_offer_id"):
        if key not in metadata or metadata[key] in (None, ""):
            continue
        values.append(_identifier(metadata[key], missing_reason=ImportReason.MISSING_IDENTITY))
    if len(set(values)) > 1:
        _fail(ImportReason.CONFLICTING_IDENTITY)


@dataclass(frozen=True, slots=True)
class _LegacyReservation:
    target_id: str
    service: str
    status: str
    amount_minor: int
    currency: str
    created_at: datetime | None
    payment_deadline: datetime | None
    payment_status: str
    payment_method: str
    payment_confirmed_at: datetime | None


def _optional_utc(value: object) -> datetime | None:
    text = _exact_text(value, "timestamp", allow_empty=True)
    return None if text == "" else _utc(text)


def _amount_minor(value: object) -> int:
    if type(value) not in (str, int, float):
        _fail(ImportReason.MALFORMED)
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise _ClassifiedInput(
            ImportDisposition.REJECTED,
            ImportReason.MALFORMED,
        ) from exc
    scaled = amount * 100
    if not amount.is_finite() or amount < 0 or scaled != scaled.to_integral_value():
        _fail(ImportReason.MALFORMED)
    return int(scaled)


def _reservation_refs(fields: Mapping[str, object]) -> tuple[_LegacyReservation, ...]:
    expected = {
        "id",
        "service",
        "status",
        "amount_due",
        "currency",
        "created_at",
        "payment_expires_at",
        "payment_status",
        "payment_method",
        "payment_confirmed_at",
    }
    result: list[_LegacyReservation] = []
    for source in ("hostel_reservations", "agency_bookings"):
        raw_items = fields[source]
        if type(raw_items) is not list:
            _fail(ImportReason.MALFORMED)
        expected_service = "hostel" if source == "hostel_reservations" else "agency"
        for raw in raw_items:
            if type(raw) is not dict or set(raw) != expected:
                _fail(ImportReason.MALFORMED)
            target_id = _identifier(raw["id"], missing_reason=ImportReason.MISSING_IDENTITY)
            service = _exact_text(raw["service"], "reservation.service")
            if service != expected_service:
                _fail(ImportReason.CONFLICTING_IDENTITY)
            result.append(
                _LegacyReservation(
                    target_id=target_id,
                    service=service,
                    status=_exact_text(raw["status"], "reservation.status"),
                    amount_minor=_amount_minor(raw["amount_due"]),
                    currency=_exact_text(raw["currency"], "reservation.currency"),
                    created_at=_optional_utc(raw["created_at"]),
                    payment_deadline=_optional_utc(raw["payment_expires_at"]),
                    payment_status=_exact_text(
                        raw["payment_status"],
                        "reservation.payment_status",
                        allow_empty=True,
                    ),
                    payment_method=_exact_text(
                        raw["payment_method"],
                        "reservation.payment_method",
                        allow_empty=True,
                    ),
                    payment_confirmed_at=_optional_utc(raw["payment_confirmed_at"]),
                )
            )
    target_ids = tuple(item.target_id for item in result)
    if len(set(target_ids)) != len(target_ids):
        _fail(ImportReason.AMBIGUOUS_IDENTITY)
    return tuple(result)


def _state_offer_ids(state: object) -> tuple[str, ...]:
    if type(state) is SelectedState:
        return (state.offer.offer_id,)
    if type(state) in {
        ReadyToSummarizeState,
        AwaitingConfirmationState,
        ExecutionQueuedState,
    }:
        return tuple(item.offer_id for item in state.draft.components)
    if type(state) is SucceededState:
        return tuple(item.offer_id for item in state.command.payload.components)
    return ()


def _state_draft(state: object) -> CommercialDraft | None:
    if type(state) in {
        ReadyToSummarizeState,
        AwaitingConfirmationState,
        ExecutionQueuedState,
    }:
        return state.draft
    if type(state) is SucceededState:
        return CommercialDraft(
            draft_id=state.command.draft_id,
            version=state.command.draft_version,
            created_at=state.command.created_at,
            components=state.command.payload.components,
            customer=state.command.payload.customer,
            terms=state.command.payload.terms,
            subject_signature=state.command.subject_signature,
        )
    return None


def _summary_locale(language: object) -> SummaryLocale:
    text = _exact_text(language, "language")
    if text in {"pt", "pt-BR", "pt_BR"}:
        return SummaryLocale.PT_BR
    if text in {"en", "en-US", "en_US"}:
        return SummaryLocale.EN
    _review(ImportReason.MISSING_PROVENANCE)


def _bind_state_metadata(
    state: object,
    metadata: Mapping[str, object],
    language: object,
) -> None:
    workflow_id = _identifier(
        metadata.get("workflow_id"),
        missing_reason=ImportReason.MISSING_PROVENANCE,
    )
    if workflow_id != state.meta.workflow_id:
        _fail(ImportReason.CONFLICTING_IDENTITY)
    if "state_updated_at" not in metadata:
        _review(ImportReason.MISSING_PROVENANCE)
    if _utc(metadata["state_updated_at"]) != state.meta.last_event_at:
        _fail(ImportReason.INCONSISTENT_CONFIRMATION)

    offer_ids = _state_offer_ids(state)
    if len(offer_ids) == 1:
        selected = _identifier(
            metadata.get("selected_offer_id"),
            missing_reason=ImportReason.MISSING_PROVENANCE,
        )
        if selected != offer_ids[0]:
            _fail(ImportReason.INCONSISTENT_SELECTION)
    elif offer_ids:
        raw_ids = metadata.get("selected_offer_ids")
        if type(raw_ids) is not list:
            _review(ImportReason.MISSING_PROVENANCE)
        selected_ids = tuple(
            _identifier(item, missing_reason=ImportReason.MISSING_PROVENANCE)
            for item in raw_ids
        )
        if selected_ids != offer_ids:
            _fail(ImportReason.INCONSISTENT_SELECTION)

    if type(state) in {
        ReadyToSummarizeState,
        AwaitingConfirmationState,
        ExecutionQueuedState,
    }:
        expected_version = state.draft.version
        expected_signature = state.draft.subject_signature
    elif type(state) is SucceededState:
        expected_version = state.command.draft_version
        expected_signature = state.command.subject_signature
    else:
        return
    version = metadata.get("summary_version")
    signature = metadata.get("confirmation_signature")
    if type(version) is not int or type(version) is bool:
        _review(ImportReason.MISSING_PROVENANCE)
    if version != expected_version or signature != expected_signature:
        _fail(ImportReason.INCONSISTENT_CONFIRMATION)
    if type(state) is ReadyToSummarizeState:
        return
    draft = _state_draft(state)
    if draft is None:
        _fail(ImportReason.INCONSISTENT_CONFIRMATION)
    rendered_hash = metadata.get("rendered_summary_hash")
    if type(rendered_hash) is not str:
        _review(ImportReason.MISSING_PROVENANCE)
    expected_rendered_hash = render_summary(
        draft,
        locale=_summary_locale(language),
    ).content_hash
    if rendered_hash != expected_rendered_hash:
        _fail(ImportReason.INCONSISTENT_CONFIRMATION)


def _expected_business_unit(service: ServiceKind) -> BusinessUnit:
    return BusinessUnit.AGENCY if service is ServiceKind.ACTIVITY else BusinessUnit.HOSTEL


def _payment_workflows(
    metadata: Mapping[str, object],
    state: SucceededState,
    reservations: tuple[_LegacyReservation, ...],
) -> tuple[PaymentWorkflow, ...]:
    raw_wires = metadata.get("phase6_payment_wires")
    if raw_wires is None:
        if reservations and any(item.amount_minor > 0 for item in reservations):
            _review(ImportReason.MISSING_PROVENANCE)
        return ()
    if type(raw_wires) is not list:
        _fail(ImportReason.UNVERIFIED_PAYMENT)
    payments: list[PaymentWorkflow] = []
    for raw_wire in raw_wires:
        if type(raw_wire) is not str:
            _fail(ImportReason.UNVERIFIED_PAYMENT)
        try:
            payment = from_phase6_wire_json(raw_wire, PaymentWorkflow)
        except (TypeError, ValueError) as exc:
            raise _ClassifiedInput(
                ImportDisposition.REJECTED,
                ImportReason.UNVERIFIED_PAYMENT,
            ) from exc
        payments.append(payment)
    payment_ids = tuple(item.subject.payment_id for item in payments)
    target_ids = tuple(item.subject.payment_target_id for item in payments)
    if len(set(payment_ids)) != len(payment_ids) or len(set(target_ids)) != len(target_ids):
        _fail(ImportReason.AMBIGUOUS_IDENTITY)
    if len(payments) != len(reservations):
        _fail(ImportReason.UNVERIFIED_PAYMENT)

    outcome_hash = hashlib.sha256(dumps_outcome(state.outcome).encode("utf-8")).hexdigest()
    by_target = {item.target_id: item for item in reservations}
    for payment in payments:
        anchor = payment.subject.confirmed_reservation_anchor
        reservation = by_target.get(anchor.payment_target_id)
        if reservation is None:
            _fail(ImportReason.UNVERIFIED_PAYMENT)
        service = state.command.payload.components[0].service
        if (
            reservation.status not in _ACTIVE_RESERVATION_STATUSES
            or reservation.created_at != anchor.confirmed_at
            or reservation.payment_status
            not in (*_UNPAID_PAYMENT_STATUSES, "paid")
        ):
            _fail(ImportReason.UNVERIFIED_PAYMENT)
        paid = reservation.payment_status == "paid"
        expected_amount = 0 if paid else anchor.amount_minor
        expected_method = (
            "" if payment.subject.method is None else payment.subject.method.value
        )
        if (
            anchor.reservation_workflow_id != state.meta.workflow_id
            or anchor.reservation_command_id != state.command.command_id
            or anchor.reservation_subject_signature != state.command.subject_signature
            or anchor.reservation_outcome_hash != outcome_hash
            or anchor.reservation_outcome != state.outcome
            or anchor.provider_reference != state.outcome.provider_reference
            or anchor.service is not service
            or anchor.business_unit is not _expected_business_unit(service)
            or anchor.payment_target_id != reservation.target_id
            or anchor.payment_target_id != state.outcome.provider_reference
            or expected_amount != reservation.amount_minor
            or anchor.currency != reservation.currency
            or anchor.payment_deadline != reservation.payment_deadline
            or reservation.payment_method != expected_method
            or (
                paid
                and (
                    payment.settlement_finish is None
                    or reservation.payment_confirmed_at
                    != payment.settlement_finish.finished_at
                )
            )
            or (not paid and reservation.payment_confirmed_at is not None)
        ):
            _fail(ImportReason.UNVERIFIED_PAYMENT)
        if reservation.service != (
            "agency" if service is ServiceKind.ACTIVITY else "hostel"
        ):
            _fail(ImportReason.UNVERIFIED_PAYMENT)
        if paid != (payment.status is PaymentStatus.PAID):
            _fail(ImportReason.UNVERIFIED_PAYMENT)
    return tuple(payments)


def _advanced(fields: Mapping[str, object], metadata: Mapping[str, object]) -> ImportResult:
    raw_wire = metadata.get("phase2_workflow_wire")
    if type(raw_wire) is not str:
        _review(ImportReason.MISSING_PROVENANCE)
    try:
        state = loads_state(raw_wire)
    except (TypeError, ValueError) as exc:
        raise _ClassifiedInput(
            ImportDisposition.REJECTED,
            ImportReason.INCONSISTENT_SELECTION,
        ) from exc
    if type(state) is UncertainState:
        _review(ImportReason.UNKNOWN_HISTORICAL_OUTCOME)
    if type(state) not in _ADVANCED_STATE_TYPES:
        _review(ImportReason.MISSING_PROVENANCE)
    _bind_state_metadata(state, metadata, fields["language"])
    reservations = _reservation_refs(fields)
    payments: tuple[PaymentWorkflow, ...] = ()
    if type(state) is SucceededState:
        if state.outcome.certainty is not ExecutionCertainty.EFFECT_CONFIRMED:
            _review(ImportReason.UNKNOWN_HISTORICAL_OUTCOME)
        if len(reservations) != 1:
            _review(ImportReason.MISSING_PROVENANCE)
        if reservations[0].target_id != state.outcome.provider_reference:
            _fail(ImportReason.CONFLICTING_IDENTITY)
        payments = _payment_workflows(metadata, state, reservations)
    elif reservations or "phase6_payment_wires" in metadata:
        _fail(ImportReason.INCONSISTENT_CONFIRMATION)
    boundary = BoundaryState(
        schema_version=7,
        lead_key=fields["lead_key"],
        version=0,
        workflow=state,
        handoff=None,
        payments=payments,
        processed_event_ids=state.meta.seen_event_ids,
    )
    return ImportResult(ImportDisposition.MIGRATED, boundary, ImportReason.NONE)


def _has_advanced_state(fields: Mapping[str, object], metadata: Mapping[str, object]) -> bool:
    if fields["hostel_reservations"] or fields["agency_bookings"]:
        return True
    if fields["desired_services"] or fields["missing_slots"] or fields["memory_long"]:
        return True
    if "phase6_payment_wires" in metadata:
        return True
    return any(key in metadata and metadata[key] not in (None, "", (), []) for key in _ADVANCED_KEYS)


def _collecting(fields: Mapping[str, object], metadata: Mapping[str, object]) -> ImportResult:
    if fields["ai_status"] != "active" or _has_advanced_state(fields, metadata):
        _review(ImportReason.MISSING_PROVENANCE)
    if "workflow_id" not in metadata or "state_updated_at" not in metadata:
        _review(ImportReason.MISSING_PROVENANCE)
    workflow_id = _identifier(
        metadata["workflow_id"],
        missing_reason=ImportReason.MISSING_PROVENANCE,
    )
    started_at = _utc(metadata["state_updated_at"])
    state = BoundaryState(
        schema_version=7,
        lead_key=fields["lead_key"],
        version=0,
        workflow=new_workflow(workflow_id=workflow_id, started_at=started_at),
        handoff=None,
        payments=(),
        processed_event_ids=(),
    )
    return ImportResult(ImportDisposition.MIGRATED, state, ImportReason.NONE)


def _handoff(fields: Mapping[str, object], metadata: Mapping[str, object]) -> ImportResult:
    required = (
        "handoff_id",
        "incident_key",
        "handoff_source_event_id",
        "handoff_requested_at",
        "handoff_reason_code",
    )
    if any(key not in metadata for key in required):
        _review(ImportReason.MISSING_PROVENANCE)
    handoff_id = _identifier(metadata["handoff_id"], missing_reason=ImportReason.MISSING_PROVENANCE)
    incident_key = _identifier(metadata["incident_key"], missing_reason=ImportReason.MISSING_PROVENANCE)
    source_event_id = _identifier(
        metadata["handoff_source_event_id"],
        missing_reason=ImportReason.MISSING_PROVENANCE,
    )
    requested_at = _utc(metadata["handoff_requested_at"])
    reason_text = _exact_text(metadata["handoff_reason_code"], "handoff_reason_code")
    try:
        reason = HandoffReasonCode(reason_text)
    except ValueError as exc:
        raise _ClassifiedInput(
            ImportDisposition.REJECTED,
            ImportReason.MALFORMED,
        ) from exc
    request = HandoffRequested(
        handoff_id=handoff_id,
        lead_key_hash=hashlib.sha256(fields["lead_key"].encode("utf-8")).hexdigest(),
        incident_key=incident_key,
        reason_code=reason,
        source_event_id=source_event_id,
        reservation_anchor=None,
        requested_at=requested_at,
    )
    handoff = new_handoff(request, HandoffEffectPolicy.default_email_disabled()).state
    state = BoundaryState(
        schema_version=7,
        lead_key=fields["lead_key"],
        version=0,
        workflow=None,
        handoff=handoff,
        payments=(),
        processed_event_ids=(source_event_id,),
    )
    return ImportResult(ImportDisposition.MIGRATED, state, ImportReason.NONE)


def import_legacy_state(snapshot: LegacyLeadSnapshot) -> ImportResult:
    """Classify and import one immutable legacy snapshot without inference."""

    try:
        fields = _validated_fields(snapshot)
        metadata = _metadata(fields)
        _validate_canonical_identity(metadata)
        stage = fields["stage"]
        if stage in _HANDOFF_STAGES:
            return _handoff(fields, metadata)
        if "phase2_workflow_wire" in metadata:
            return _advanced(fields, metadata)
        if stage in _ADVANCED_STAGES:
            _review(ImportReason.MISSING_PROVENANCE)
        if stage in _COLLECTING_STAGES:
            return _collecting(fields, metadata)
        if stage in _MANUAL_STAGES:
            _review(ImportReason.MISSING_PROVENANCE)
        _fail(ImportReason.UNSUPPORTED_STAGE)
    except _ClassifiedInput as exc:
        return _result(exc)


__all__ = ("import_legacy_state",)
