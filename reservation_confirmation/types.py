"""Closed immutable contracts for Phase 4 summary and confirmation boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
import unicodedata

from reservation_domain import (
    ConfirmationDecisionKind,
    ConfirmationReceived,
    SummaryRecorded,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _require_exact_id(value: str, field_name: str) -> str:
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return value


def _require_exact_hash(value: str, field_name: str) -> str:
    if type(value) is not str or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _require_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _reject_controls(value: str, field_name: str, *, allow_newline: bool) -> None:
    for char in value:
        category = unicodedata.category(char)
        if category.startswith("C") and not (allow_newline and char == "\n"):
            raise ValueError(f"{field_name} contains a control character")


def _canonical_message(value: str, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    _reject_controls(normalized, field_name, allow_newline=False)
    normalized = " ".join(normalized.split())
    if not normalized or len(normalized) > 2_000:
        raise ValueError(f"{field_name} must contain 1..2000 canonical characters")
    return normalized


def _canonical_content(value: str) -> str:
    if type(value) is not str:
        raise ValueError("content must be a string")
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    _reject_controls(normalized, "content", allow_newline=True)
    lines = [" ".join(line.split()) for line in normalized.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    compact: list[str] = []
    for line in lines:
        if line or (compact and compact[-1]):
            compact.append(line)
    normalized = "\n".join(compact)
    if not normalized or len(normalized) > 12_000:
        raise ValueError("content must contain 1..12000 canonical characters")
    return normalized


def rendered_summary_hash(
    *,
    renderer_id: str,
    renderer_version: int,
    locale: "SummaryLocale",
    draft_id: str,
    draft_version: int,
    subject_signature: str,
    content: str,
) -> str:
    payload = {
        "content": content,
        "draft_id": draft_id,
        "draft_version": draft_version,
        "locale": locale.value,
        "renderer_id": renderer_id,
        "renderer_version": renderer_version,
        "subject_signature": subject_signature,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class SummaryLocale(str, Enum):
    PT_BR = "pt_BR"
    EN = "en"


@dataclass(frozen=True, slots=True)
class RenderedSummary:
    renderer_id: str
    renderer_version: int
    locale: SummaryLocale
    draft_id: str
    draft_version: int
    subject_signature: str
    content: str
    content_hash: str
    claim_status: str
    private_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_exact_id(self.renderer_id, "renderer_id")
        if type(self.renderer_version) is not int or self.renderer_version < 1:
            raise ValueError("renderer_version must be an integer >= 1")
        if type(self.locale) is not SummaryLocale:
            raise ValueError("locale must use SummaryLocale")
        _require_exact_id(self.draft_id, "draft_id")
        if type(self.draft_version) is not int or self.draft_version < 1:
            raise ValueError("draft_version must be an integer >= 1")
        _require_exact_hash(self.subject_signature, "subject_signature")
        content = _canonical_content(self.content)
        object.__setattr__(self, "content", content)
        _require_exact_hash(self.content_hash, "content_hash")
        if type(self.claim_status) is not str or self.claim_status != "none":
            raise ValueError("claim_status must be none")
        if type(self.private_fields) is not tuple or self.private_fields:
            raise ValueError("private_fields must be an empty tuple")
        expected = rendered_summary_hash(
            renderer_id=self.renderer_id,
            renderer_version=self.renderer_version,
            locale=self.locale,
            draft_id=self.draft_id,
            draft_version=self.draft_version,
            subject_signature=self.subject_signature,
            content=content,
        )
        if self.content_hash != expected:
            raise ValueError("content_hash does not match rendered summary")


@dataclass(frozen=True, slots=True)
class PreparedSummary:
    rendered: RenderedSummary
    summary_event_id: str
    outbox_message_id: str
    presented_at: datetime
    event: SummaryRecorded

    def __post_init__(self) -> None:
        if type(self.rendered) is not RenderedSummary:
            raise ValueError("rendered must be an exact RenderedSummary")
        _require_exact_id(self.summary_event_id, "summary_event_id")
        _require_exact_id(self.outbox_message_id, "outbox_message_id")
        presented = _require_utc(self.presented_at, "presented_at")
        object.__setattr__(self, "presented_at", presented)
        if type(self.event) is not SummaryRecorded:
            raise ValueError("event must be an exact SummaryRecorded")
        if (
            self.event.summary_event_id != self.summary_event_id
            or self.event.outbox_message_id != self.outbox_message_id
            or self.event.occurred_at != presented
            or self.event.draft_version != self.rendered.draft_version
            or self.event.subject_signature != self.rendered.subject_signature
        ):
            raise ValueError("prepared summary event does not bind to rendered artifact")


@dataclass(frozen=True, slots=True)
class ClassificationContext:
    workflow_id: str
    summary_event_id: str
    draft_id: str
    draft_version: int
    subject_signature: str
    presented_at: datetime
    locale: SummaryLocale
    content_hash: str

    def __post_init__(self) -> None:
        _require_exact_id(self.workflow_id, "workflow_id")
        _require_exact_id(self.summary_event_id, "summary_event_id")
        _require_exact_id(self.draft_id, "draft_id")
        if type(self.draft_version) is not int or self.draft_version < 1:
            raise ValueError("draft_version must be an integer >= 1")
        _require_exact_hash(self.subject_signature, "subject_signature")
        object.__setattr__(self, "presented_at", _require_utc(self.presented_at, "presented_at"))
        if type(self.locale) is not SummaryLocale:
            raise ValueError("locale must use SummaryLocale")
        _require_exact_hash(self.content_hash, "content_hash")


@dataclass(frozen=True, slots=True)
class ClassificationInput:
    source_event_id: str
    received_at: datetime
    text: str
    context: ClassificationContext | None

    def __post_init__(self) -> None:
        _require_exact_id(self.source_event_id, "source_event_id")
        object.__setattr__(self, "received_at", _require_utc(self.received_at, "received_at"))
        object.__setattr__(self, "text", _canonical_message(self.text, "text"))
        if self.context is not None and type(self.context) is not ClassificationContext:
            raise ValueError("context must be an exact ClassificationContext or None")


@dataclass(frozen=True, slots=True)
class DecisionCandidate:
    decision: ConfirmationDecisionKind
    classifier_id: str
    classifier_version: int
    confidence_basis_points: int
    evidence_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.decision) is not ConfirmationDecisionKind:
            raise ValueError("decision must use ConfirmationDecisionKind")
        _require_exact_id(self.classifier_id, "classifier_id")
        if type(self.classifier_version) is not int or self.classifier_version < 1:
            raise ValueError("classifier_version must be an integer >= 1")
        if (
            type(self.confidence_basis_points) is not int
            or not 0 <= self.confidence_basis_points <= 10_000
        ):
            raise ValueError("confidence_basis_points must be 0..10000")
        if type(self.evidence_codes) is not tuple or not self.evidence_codes:
            raise ValueError("evidence_codes must be a non-empty tuple")
        if any(type(item) is not str or not _CODE_RE.fullmatch(item) for item in self.evidence_codes):
            raise ValueError("evidence_codes must use canonical identifiers")
        ordered = tuple(sorted(self.evidence_codes))
        if len(set(ordered)) != len(ordered):
            raise ValueError("evidence_codes must be unique")
        object.__setattr__(self, "evidence_codes", ordered)


@dataclass(frozen=True, slots=True)
class BoundConfirmation:
    input: ClassificationInput
    candidate: DecisionCandidate
    confirmation_event_id: str | None
    event: ConfirmationReceived | None

    def __post_init__(self) -> None:
        if type(self.input) is not ClassificationInput:
            raise ValueError("input must be an exact ClassificationInput")
        if type(self.candidate) is not DecisionCandidate:
            raise ValueError("candidate must be an exact DecisionCandidate")
        if self.event is None:
            if self.confirmation_event_id is not None:
                raise ValueError("eventless confirmation cannot have confirmation_event_id")
            if self.candidate.decision is not ConfirmationDecisionKind.AMBIGUOUS:
                raise ValueError("eventless confirmation must be ambiguous")
            return
        if type(self.event) is not ConfirmationReceived:
            raise ValueError("event must be an exact ConfirmationReceived or None")
        if self.input.context is None:
            raise ValueError("bound event requires classification context")
        if self.confirmation_event_id is None:
            raise ValueError("bound event requires confirmation_event_id")
        _require_exact_id(self.confirmation_event_id, "confirmation_event_id")
        if self.event.confirmation_event_id != self.confirmation_event_id:
            raise ValueError("confirmation_event_id does not bind to event")
        if self.event.decision is not self.candidate.decision:
            raise ValueError("event decision does not bind to candidate decision")
        if (
            self.event.occurred_at != self.input.received_at
            or self.event.target_draft_version != self.input.context.draft_version
            or self.event.subject_signature != self.input.context.subject_signature
        ):
            raise ValueError("confirmation event does not bind to classification input")


__all__ = [
    "SummaryLocale",
    "RenderedSummary",
    "PreparedSummary",
    "ClassificationContext",
    "ClassificationInput",
    "DecisionCandidate",
    "BoundConfirmation",
    "rendered_summary_hash",
]
