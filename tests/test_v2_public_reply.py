from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v2_application.public_reply import apply_positive_grounding
from v2_contracts.model import ModelProposal
from v2_contracts.providers import ReadObservation

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def _observation(provider: str, payload: dict[str, object], seed: str) -> ReadObservation:
    return ReadObservation(
        request_hash=seed * 64,
        provider=provider,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        public_payload=payload,
        private_binding_hash=("f" if seed != "f" else "e") * 64,
    )


def _proposal(
    text: str,
    *,
    clarification_question: str | None = None,
) -> ModelProposal:
    return ModelProposal(
        source_event_id="batch:public-reply-grounding",
        intent="inform",
        reply_chunks=(
            (text, clarification_question)
            if clarification_question is not None
            else (text,)
        ),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        clarification_question=clarification_question,
    )


def _lodging() -> ReadObservation:
    return _observation(
        "cloudbeds",
        {
            "options": [
                {
                    "offer_id": "offer:" + "1" * 64,
                    "room_public_name": "Suíte Real",
                    "check_in": "2026-09-10",
                    "check_out": "2026-09-12",
                    "adults": 1,
                    "children": 0,
                    "total_amount": "440.00",
                    "currency": "BRL",
                    "available_units": 2,
                }
            ]
        },
        "1",
    )


def _activity() -> ReadObservation:
    return _observation(
        "bokun",
        {
            "offer_id": "offer:" + "2" * 64,
            "product_id": "product:buracao",
            "product_public_name": "Cachoeira do Buracão",
            "activity_date": "2026-09-12",
            "adults": 1,
            "children": 0,
            "total_amount": "350.00",
            "currency": "BRL",
            "available": True,
        },
        "2",
    )


def test_positive_grounding_preserves_exact_model_owned_chunks_and_question() -> None:
    proposal = _proposal(
        "Everything is sold out and a human is already following this.",
        clarification_question="Will you be the reservation holder?",
    )

    grounded = apply_positive_grounding(
        proposal,
        (_lodging(),),
        locale="en",
        force=False,
    )

    assert grounded is proposal
    assert grounded.reply_chunks == proposal.reply_chunks
    assert grounded.clarification_question == proposal.clarification_question


@pytest.mark.parametrize("force", (False, True))
def test_positive_grounding_returns_each_distinct_proposal_unchanged(force: bool) -> None:
    proposal = _proposal("Maya sentinel: semantically unrelated text")

    assert (
        apply_positive_grounding(
            proposal,
            (_lodging(), _activity()),
            locale="en",
            force=force,
        )
        is proposal
    )


def test_no_positive_observation_leaves_proposal_unchanged() -> None:
    proposal = _proposal("I need one more detail.")
    negative = _observation(
        "bokun",
        {"available": False, "product_public_name": "Cachoeira do Buracão"},
        "3",
    )

    assert (
        apply_positive_grounding(proposal, (negative,), locale="en", force=False)
        is proposal
    )


@pytest.mark.parametrize(
    ("proposal", "observations", "force"),
    (
        (object(), (), False),
        (_proposal("Exact type guard."), [], False),
        (_proposal("Exact type guard."), (object(),), False),
        (_proposal("Exact type guard."), (), 1),
    ),
)
def test_positive_grounding_requires_exact_contract_types(
    proposal: object,
    observations: object,
    force: object,
) -> None:
    with pytest.raises(TypeError, match="positive grounding requires exact contracts"):
        apply_positive_grounding(
            proposal,
            observations,
            locale="en",
            force=force,
        )
