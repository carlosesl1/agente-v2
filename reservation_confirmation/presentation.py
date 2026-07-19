"""Prepare a deterministic summary artifact and its domain event."""

from __future__ import annotations

from datetime import datetime
import hashlib

from reservation_domain import CommercialDraft, ReadyToSummarizeState, SummaryRecorded
from reservation_domain.types import _require_utc

from .renderer import render_summary
from .types import PreparedSummary, RenderedSummary, SummaryLocale


def _artifact_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _summary_artifact_ids(
    *,
    workflow_id: str,
    draft: CommercialDraft,
    rendered: RenderedSummary,
) -> tuple[str, str, str]:
    identity_parts = (
        workflow_id,
        draft.draft_id,
        str(draft.version),
        draft.subject_signature,
        rendered.locale.value,
        str(rendered.renderer_version),
        rendered.content_hash,
    )
    return (
        _artifact_id("summary", *identity_parts),
        _artifact_id("outbox", *identity_parts),
        _artifact_id("event", "summary_recorded", *identity_parts),
    )


def prepare_summary(
    state: ReadyToSummarizeState,
    *,
    locale: SummaryLocale,
    presented_at: datetime,
) -> PreparedSummary:
    """Create the exact public artifact and SummaryRecorded event as one bundle."""

    if type(state) is not ReadyToSummarizeState:
        raise ValueError("state must be an exact ReadyToSummarizeState")
    if type(locale) is not SummaryLocale:
        raise ValueError("locale must use SummaryLocale")
    instant = _require_utc(presented_at, "presented_at")
    if instant < state.draft.created_at:
        raise ValueError("summary presentation predates commercial draft")
    rendered = render_summary(state.draft, locale=locale)
    summary_event_id, outbox_message_id, domain_event_id = _summary_artifact_ids(
        workflow_id=state.meta.workflow_id,
        draft=state.draft,
        rendered=rendered,
    )
    event = SummaryRecorded(
        event_id=domain_event_id,
        occurred_at=instant,
        summary_event_id=summary_event_id,
        draft_version=state.draft.version,
        subject_signature=state.draft.subject_signature,
        outbox_message_id=outbox_message_id,
    )
    return PreparedSummary(
        rendered=rendered,
        summary_event_id=summary_event_id,
        outbox_message_id=outbox_message_id,
        presented_at=instant,
        event=event,
    )


__all__ = ["prepare_summary"]
