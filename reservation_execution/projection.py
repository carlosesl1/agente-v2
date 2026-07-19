"""Pure durable projections shared by the SQLite execution store and workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from reservation_confirmation import (
    PreparedSummary,
    RenderedSummary,
    SummaryLocale,
    prepare_summary,
    render_summary,
)
from reservation_domain import (
    CommercialDraft,
    ExecutionCertainty,
    ExecutionOutcome,
    ReadyToSummarizeState,
    ReservationCommand,
    SummaryRecorded,
)

from .types import LedgerStatus, OutboxKind, OutboxMessage

_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_SUMMARY_PAYLOAD_KEYS = {
    "claim_status",
    "content",
    "content_hash",
    "draft_id",
    "draft_version",
    "locale",
    "renderer_id",
    "renderer_version",
    "subject_signature",
}


def _require_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must already be UTC")
    return value.astimezone(timezone.utc)


def _require_hash(value: str | None, field_name: str) -> str | None:
    if value is not None and (type(value) is not str or not _HASH_RE.fullmatch(value)):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _require_counter(value: int, field_name: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        suffix = f"..{maximum}" if maximum is not None else " or greater"
        raise ValueError(f"{field_name} must be an integer in 0{suffix}")
    return value


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    command_id: str
    status: LedgerStatus
    claim_owner: str | None
    fencing_token: int
    lease_acquired_at: datetime | None
    lease_expires_at: datetime | None
    claim_count: int
    preparation_failures: int
    dispatch_slots_consumed: int
    dispatch_request_hash: str | None
    dispatch_fenced_at: datetime | None
    outcome_json: str | None
    outcome_hash: str | None
    updated_at: datetime

    def __post_init__(self) -> None:
        if type(self.command_id) is not str or not _ID_RE.fullmatch(self.command_id):
            raise ValueError("command_id must be an opaque identifier")
        if type(self.status) is not LedgerStatus:
            raise ValueError("status must use LedgerStatus")
        if self.claim_owner is not None and (
            type(self.claim_owner) is not str or not _ID_RE.fullmatch(self.claim_owner)
        ):
            raise ValueError("claim_owner must be an opaque identifier or None")
        _require_counter(self.fencing_token, "fencing_token")
        _require_counter(self.claim_count, "claim_count")
        _require_counter(self.preparation_failures, "preparation_failures", 3)
        _require_counter(self.dispatch_slots_consumed, "dispatch_slots_consumed", 1)
        for field_name in (
            "lease_acquired_at",
            "lease_expires_at",
            "dispatch_fenced_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_utc(value, field_name)
        _require_utc(self.updated_at, "updated_at")
        _require_hash(self.dispatch_request_hash, "dispatch_request_hash")
        _require_hash(self.outcome_hash, "outcome_hash")
        lease_empty = (
            self.claim_owner is None
            and self.lease_acquired_at is None
            and self.lease_expires_at is None
        )
        lease_full = (
            self.claim_owner is not None
            and self.lease_acquired_at is not None
            and self.lease_expires_at is not None
        )
        if not (lease_empty or lease_full):
            raise ValueError("ledger lease tuple must be empty or complete")
        if lease_full and (
            self.fencing_token < 1 or self.lease_expires_at <= self.lease_acquired_at
        ):
            raise ValueError("active ledger lease is invalid")
        dispatch_empty = (
            self.dispatch_slots_consumed == 0
            and self.dispatch_request_hash is None
            and self.dispatch_fenced_at is None
        )
        dispatch_full = (
            self.dispatch_slots_consumed == 1
            and self.dispatch_request_hash is not None
            and self.dispatch_fenced_at is not None
        )
        if not (dispatch_empty or dispatch_full):
            raise ValueError("ledger dispatch tuple is inconsistent")
        if (self.outcome_json is None) != (self.outcome_hash is None):
            raise ValueError("ledger outcome tuple is inconsistent")
        if self.outcome_json is not None and type(self.outcome_json) is not str:
            raise ValueError("ledger outcome_json must be a string or None")
        outcome_present = self.outcome_json is not None
        status_valid = {
            LedgerStatus.QUEUED: (
                self.claim_owner is None
                and self.dispatch_slots_consumed == 0
                and not outcome_present
            ),
            LedgerStatus.PREPARING: (
                self.claim_owner is not None
                and self.claim_count >= 1
                and self.dispatch_slots_consumed == 0
                and not outcome_present
            ),
            LedgerStatus.DISPATCH_FENCED: (
                self.claim_owner is not None
                and self.claim_count >= 1
                and self.dispatch_slots_consumed == 1
                and not outcome_present
            ),
            LedgerStatus.OUTCOME_RECORDED: (
                self.claim_owner is None and outcome_present
            ),
            LedgerStatus.MANUAL_REVIEW: (
                self.claim_owner is None
                and self.dispatch_slots_consumed == 1
                and outcome_present
            ),
        }[self.status]
        if not status_valid:
            raise ValueError("ledger status matrix is inconsistent")


def _rendered_summary_payload(rendered: RenderedSummary) -> str:
    return json.dumps(
        {
            "claim_status": rendered.claim_status,
            "content": rendered.content,
            "content_hash": rendered.content_hash,
            "draft_id": rendered.draft_id,
            "draft_version": rendered.draft_version,
            "locale": rendered.locale.value,
            "renderer_id": rendered.renderer_id,
            "renderer_version": rendered.renderer_version,
            "subject_signature": rendered.subject_signature,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def summary_payload(prepared: PreparedSummary) -> str:
    """Serialize the exact public Phase 4 artifact as canonical JSON."""

    if type(prepared) is not PreparedSummary:
        raise TypeError("prepared must be the exact PreparedSummary type")
    return _rendered_summary_payload(prepared.rendered)


def summary_outbox_message(
    *,
    workflow_id: str,
    prepared: PreparedSummary,
) -> OutboxMessage:
    """Project a prepared Phase 4 summary into its deterministic durable message."""

    payload = summary_payload(prepared)
    return OutboxMessage(
        message_id=prepared.outbox_message_id,
        idempotency_key=prepared.outbox_message_id,
        workflow_id=workflow_id,
        command_id=None,
        kind=OutboxKind.SUMMARY_PRESENTED,
        template_id="reservation.summary.v1",
        canonical_payload=payload,
        payload_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        created_at=prepared.presented_at,
    )


def project_preparation_failure_outbox(
    command: ReservationCommand,
    outcome: ExecutionOutcome,
    *,
    created_at: datetime,
) -> OutboxMessage:
    """Project a proven pre-dispatch failure without provider/private material."""

    if type(command) is not ReservationCommand:
        raise TypeError("command must be the exact ReservationCommand type")
    if type(outcome) is not ExecutionOutcome:
        raise TypeError("outcome must be the exact ExecutionOutcome type")
    created_at = _require_utc(created_at, "created_at")
    if (
        outcome.command_id != command.command_id
        or outcome.certainty is not ExecutionCertainty.NOT_CALLED
        or outcome.provider_reference is not None
    ):
        raise ValueError("preparation projection requires matching not_called outcome")
    template_id = "reservation.execution.not_called.v1"
    payload = json.dumps(
        {
            "certainty": outcome.certainty.value,
            "status": outcome.normalized_status,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    identity = "|".join(
        (command.command_id, outcome.certainty.value, template_id)
    ).encode("utf-8")
    message_id = f"outbox:{hashlib.sha256(identity).hexdigest()}"
    return OutboxMessage(
        message_id=message_id,
        idempotency_key=message_id,
        workflow_id=command.workflow_id,
        command_id=command.command_id,
        kind=OutboxKind.EXECUTION_NOT_CALLED,
        template_id=template_id,
        canonical_payload=payload,
        payload_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        created_at=created_at,
    )


def validate_summary_outbox(
    state: ReadyToSummarizeState,
    event: SummaryRecorded,
    message: OutboxMessage,
) -> OutboxMessage:
    """Recompute and return the exact message authorized by a summary event."""

    if type(state) is not ReadyToSummarizeState:
        raise ValueError("summary state must be the exact ReadyToSummarizeState")
    if type(event) is not SummaryRecorded:
        raise ValueError("summary event must be the exact SummaryRecorded type")
    if type(message) is not OutboxMessage:
        raise ValueError("summary message must be the exact OutboxMessage type")
    try:
        payload = json.loads(message.canonical_payload)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("summary payload is not valid JSON") from exc
    if type(payload) is not dict or set(payload) != _SUMMARY_PAYLOAD_KEYS:
        raise ValueError("summary payload fields do not match the Phase 4 artifact")
    try:
        locale = SummaryLocale(payload["locale"])
    except (TypeError, ValueError) as exc:
        raise ValueError("summary payload locale is invalid") from exc
    prepared = prepare_summary(
        state,
        locale=locale,
        presented_at=event.occurred_at,
    )
    if prepared.event != event:
        raise ValueError("summary event does not match the recomputed Phase 4 artifact")
    return validate_summary_outbox_for_draft(
        workflow_id=state.meta.workflow_id,
        draft=state.draft,
        event=event,
        message=message,
    )


def validate_summary_outbox_for_draft(
    *,
    workflow_id: str,
    draft: CommercialDraft,
    event: SummaryRecorded,
    message: OutboxMessage,
) -> OutboxMessage:
    """Recompute immutable summary bytes when the matching draft is available."""

    if type(draft) is not CommercialDraft:
        raise ValueError("draft must be the exact CommercialDraft type")
    if (
        event.draft_version != draft.version
        or event.subject_signature != draft.subject_signature
    ):
        raise ValueError("summary event does not bind to the supplied draft")
    try:
        payload = json.loads(message.canonical_payload)
        if type(payload) is not dict or set(payload) != _SUMMARY_PAYLOAD_KEYS:
            raise ValueError("summary payload fields do not match the Phase 4 artifact")
        locale = SummaryLocale(payload["locale"])
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        raise ValueError("summary payload cannot select a closed locale") from exc
    rendered = render_summary(draft, locale=locale)
    canonical_payload = _rendered_summary_payload(rendered)
    expected = OutboxMessage(
        message_id=event.outbox_message_id,
        idempotency_key=event.outbox_message_id,
        workflow_id=workflow_id,
        command_id=None,
        kind=OutboxKind.SUMMARY_PRESENTED,
        template_id="reservation.summary.v1",
        canonical_payload=canonical_payload,
        payload_hash=hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
        created_at=event.occurred_at,
    )
    if message != expected:
        raise ValueError("summary outbox does not match the recomputed Phase 4 artifact")
    return expected


__all__ = [
    "LedgerSnapshot",
    "summary_payload",
    "summary_outbox_message",
    "project_preparation_failure_outbox",
    "validate_summary_outbox",
    "validate_summary_outbox_for_draft",
]
