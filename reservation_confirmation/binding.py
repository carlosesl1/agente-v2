"""Trusted binding from an untrusted classification to a domain event."""

from __future__ import annotations

from datetime import datetime
import hashlib

from reservation_domain import (
    AwaitingConfirmationState,
    ConfirmationDecisionKind,
    ConfirmationReceived,
)

from .classifier import ConfirmationClassifier, classify_safely
from .presentation import _summary_artifact_ids
from .renderer import render_summary
from .types import (
    BoundConfirmation,
    ClassificationContext,
    ClassificationInput,
    DecisionCandidate,
    SummaryLocale,
)


def _ambiguous(code: str) -> DecisionCandidate:
    return DecisionCandidate(
        decision=ConfirmationDecisionKind.AMBIGUOUS,
        classifier_id="confirmation-binder",
        classifier_version=1,
        confidence_basis_points=10_000,
        evidence_codes=(code,),
    )


def classification_context(
    state: AwaitingConfirmationState,
    *,
    locale: SummaryLocale,
    content_hash: str,
) -> ClassificationContext:
    """Rebuild and verify the exact summary artifact persisted by the reducer."""

    if type(state) is not AwaitingConfirmationState:
        raise ValueError("state must be exact AwaitingConfirmationState")
    if type(locale) is not SummaryLocale:
        raise ValueError("locale must be exact SummaryLocale")
    if type(content_hash) is not str:
        raise ValueError("summary artifact content hash must be a string")
    rendered = render_summary(state.draft, locale=locale)
    if rendered.content_hash != content_hash:
        raise ValueError("summary artifact content hash mismatch")
    summary_event_id, outbox_message_id, _ = _summary_artifact_ids(
        workflow_id=state.meta.workflow_id,
        draft=state.draft,
        rendered=rendered,
    )
    if (
        state.summary.summary_event_id != summary_event_id
        or state.summary.outbox_message_id != outbox_message_id
    ):
        raise ValueError("summary artifact identity mismatch")
    return ClassificationContext(
        workflow_id=state.meta.workflow_id,
        summary_event_id=state.summary.summary_event_id,
        draft_id=state.draft.draft_id,
        draft_version=state.draft.version,
        subject_signature=state.draft.subject_signature,
        presented_at=state.summary.presented_at,
        locale=locale,
        content_hash=content_hash,
    )


def _event_identity(context: ClassificationContext, source_event_id: str) -> str:
    material = "\x1f".join(
        (
            "confirmation-binding-v1",
            context.workflow_id,
            context.summary_event_id,
            source_event_id,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def classify_and_bind(
    state: AwaitingConfirmationState | None,
    *,
    source_event_id: str,
    received_at: datetime,
    text: str,
    locale: SummaryLocale,
    content_hash: str | None,
    classifier: ConfirmationClassifier,
) -> BoundConfirmation:
    """Classify text and bind only to target data recomputed from trusted state."""

    if state is None or type(state) is not AwaitingConfirmationState:
        item = ClassificationInput(
            source_event_id=source_event_id,
            received_at=received_at,
            text=text,
            context=None,
        )
        return BoundConfirmation(
            input=item,
            candidate=_ambiguous("context_missing"),
            confirmation_event_id=None,
            event=None,
        )
    try:
        context = classification_context(
            state,
            locale=locale,
            content_hash=content_hash if content_hash is not None else "",
        )
    except ValueError as exc:
        item = ClassificationInput(
            source_event_id=source_event_id,
            received_at=received_at,
            text=text,
            context=None,
        )
        code = (
            "content_hash_mismatch"
            if "hash" in str(exc)
            else "summary_artifact_mismatch"
        )
        return BoundConfirmation(
            input=item,
            candidate=_ambiguous(code),
            confirmation_event_id=None,
            event=None,
        )

    item = ClassificationInput(
        source_event_id=source_event_id,
        received_at=received_at,
        text=text,
        context=context,
    )
    if received_at <= context.presented_at:
        return BoundConfirmation(
            input=item,
            candidate=_ambiguous("confirmation_not_posterior"),
            confirmation_event_id=None,
            event=None,
        )
    candidate = classify_safely(classifier, item)
    boundary_failures = {"classifier_error", "classifier_invalid_result"}
    if boundary_failures.intersection(candidate.evidence_codes):
        return BoundConfirmation(
            input=item,
            candidate=candidate,
            confirmation_event_id=None,
            event=None,
        )

    digest = _event_identity(context, source_event_id)
    decision_digest = hashlib.sha256(
        f"{digest}|{candidate.decision.value}".encode("utf-8")
    ).hexdigest()
    confirmation_event_id = f"confirmation:{decision_digest}"
    event = ConfirmationReceived(
        event_id=f"event:confirmation:{digest}",
        occurred_at=received_at,
        confirmation_event_id=confirmation_event_id,
        decision=candidate.decision,
        target_draft_version=context.draft_version,
        subject_signature=context.subject_signature,
    )
    return BoundConfirmation(
        input=item,
        candidate=candidate,
        confirmation_event_id=confirmation_event_id,
        event=event,
    )


__all__ = ["classification_context", "classify_and_bind"]
