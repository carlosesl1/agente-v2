from __future__ import annotations

from datetime import datetime, timedelta, timezone

from v2_application.public_reply import apply_positive_grounding, grounded_positive_reply
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


def test_real_cloudbeds_options_use_available_units_without_available_boolean() -> None:
    assert grounded_positive_reply((_lodging(),), locale="pt-BR") == (
        "Encontrei Suíte Real disponível de 10/09/2026 a 12/09/2026 "
        "por BRL 440.00. Nada foi reservado.",
    )


def test_package_grounding_renders_each_provider_group_in_provider_order() -> None:
    assert grounded_positive_reply((_lodging(), _activity()), locale="en") == (
        "I found Suíte Real available from 2026-09-10 to 2026-09-12 "
        "for BRL 440.00. Nothing was booked.\n"
        "I found Cachoeira do Buracão available on 2026-09-12 for "
        "BRL 350.00. Nothing was booked.",
    )


def test_grounding_replaces_untrusted_commercial_draft_but_preserves_typed_question() -> None:
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

    assert grounded.reply_chunks == (
        "I found Suíte Real available from 2026-09-10 to 2026-09-12 "
        "for BRL 440.00. Nothing was booked.",
        "Will you be the reservation holder?",
    )
    assert grounded.clarification_question == proposal.clarification_question


def test_grounding_decision_does_not_depend_on_draft_words_or_force_flag() -> None:
    first = apply_positive_grounding(
        _proposal("available Suíte Real BRL 440.00"),
        (_lodging(),),
        locale="en",
        force=False,
    )
    second = apply_positive_grounding(
        _proposal("semantically unrelated text"),
        (_lodging(),),
        locale="en",
        force=True,
    )

    assert first.reply_chunks == second.reply_chunks


def test_no_positive_observation_leaves_proposal_unchanged() -> None:
    proposal = _proposal("I need one more detail.")
    negative = _observation(
        "bokun",
        {"available": False, "product_public_name": "Cachoeira do Buracão"},
        "3",
    )

    assert (
        apply_positive_grounding(proposal, (negative,), locale="en", force=False)
        == proposal
    )
