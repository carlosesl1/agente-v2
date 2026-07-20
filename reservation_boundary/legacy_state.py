"""Fail-closed staged importer for the observed legacy LeadState schema."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import hashlib
import json
import re
from typing import Final

from reservation_domain import new_workflow
from reservation_followup import (
    HandoffEffectPolicy,
    HandoffReasonCode,
    HandoffRequested,
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
    )
)
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


def _has_advanced_state(fields: Mapping[str, object], metadata: Mapping[str, object]) -> bool:
    if fields["hostel_reservations"] or fields["agency_bookings"]:
        return True
    if fields["desired_services"] or fields["missing_slots"] or fields["memory_long"]:
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
        if stage in _COLLECTING_STAGES:
            return _collecting(fields, metadata)
        if stage in _MANUAL_STAGES:
            _review(ImportReason.MISSING_PROVENANCE)
        _fail(ImportReason.UNSUPPORTED_STAGE)
    except _ClassifiedInput as exc:
        return _result(exc)


__all__ = ("import_legacy_state",)
