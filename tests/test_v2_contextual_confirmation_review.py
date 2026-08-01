from __future__ import annotations

from dataclasses import replace

import pytest

from v2_contracts.confirmation_review import (
    ContextualConfirmationDecision,
    ContextualConfirmationReview,
    InvalidContextualConfirmationReview,
)


def test_review_contract_is_closed_and_exact() -> None:
    review = ContextualConfirmationReview(
        source_event_id="batch:contextual-review-001",
        decision=ContextualConfirmationDecision.APPROVE,
    )

    assert review.source_event_id == "batch:contextual-review-001"
    assert review.decision is ContextualConfirmationDecision.APPROVE
    assert tuple(ContextualConfirmationDecision) == (
        ContextualConfirmationDecision.APPROVE,
        ContextualConfirmationDecision.REJECT,
        ContextualConfirmationDecision.ADJUST,
        ContextualConfirmationDecision.UNCERTAIN,
    )


@pytest.mark.parametrize(
    ("source_event_id", "decision"),
    (
        (" batch:leading-space", ContextualConfirmationDecision.APPROVE),
        ("batch:trailing-space ", ContextualConfirmationDecision.APPROVE),
        ("batch:contains space", ContextualConfirmationDecision.APPROVE),
        ("", ContextualConfirmationDecision.APPROVE),
        ("batch:raw-decision", "approve"),
        ("batch:unknown-decision", object()),
    ),
)
def test_review_contract_rejects_noncanonical_identity_or_raw_decision(
    source_event_id: str,
    decision: object,
) -> None:
    with pytest.raises(InvalidContextualConfirmationReview):
        ContextualConfirmationReview(  # type: ignore[arg-type]
            source_event_id=source_event_id,
            decision=decision,
        )


def test_review_contract_is_frozen() -> None:
    review = ContextualConfirmationReview(
        source_event_id="batch:frozen-review",
        decision=ContextualConfirmationDecision.UNCERTAIN,
    )

    changed = replace(review, decision=ContextualConfirmationDecision.REJECT)
    assert changed.decision is ContextualConfirmationDecision.REJECT
    with pytest.raises((AttributeError, TypeError)):
        review.decision = ContextualConfirmationDecision.APPROVE  # type: ignore[misc]
