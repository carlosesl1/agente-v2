from __future__ import annotations

from datetime import datetime, timedelta, timezone

from v2_application.public_reply import (
    apply_positive_grounding,
    grounded_positive_reply,
    proposal_grounds_positive_observation,
)
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


def _proposal(text: str) -> ModelProposal:
    return ModelProposal(
        source_event_id="batch:public-reply-grounding",
        intent="inform",
        reply_chunks=(text,),
        facts=(),
        read_requests=(),
        effect_proposals=(),
    )


def test_real_cloudbeds_options_use_available_units_without_available_boolean() -> None:
    lodging = _observation(
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

    assert grounded_positive_reply((lodging,), locale="pt-BR") == (
        "Encontrei Suíte Real disponível de 10/09/2026 a 12/09/2026 "
        "por BRL 440.00. Nada foi reservado.",
    )


def test_negative_claim_never_counts_as_positive_grounding_even_with_offer_name() -> None:
    lodging = _observation(
        "cloudbeds",
        {
            "offer_id": "offer:" + "2" * 64,
            "room_public_name": "Suíte Contradição",
            "check_in": "2026-09-10",
            "check_out": "2026-09-12",
            "adults": 1,
            "children": 0,
            "total_amount": "440.00",
            "currency": "BRL",
            "available_units": 1,
        },
        "2",
    )

    assert (
        proposal_grounds_positive_observation(
            _proposal("A Suíte Contradição não está disponível."),
            (lodging,),
        )
        is False
    )


def test_package_grounding_requires_every_positive_observation() -> None:
    lodging = _observation(
        "cloudbeds",
        {
            "options": [
                {
                    "offer_id": "offer:" + "3" * 64,
                    "room_public_name": "Quarto privativo",
                    "check_in": "2026-09-10",
                    "check_out": "2026-09-12",
                    "adults": 2,
                    "children": 0,
                    "total_amount": "580.00",
                    "currency": "BRL",
                    "available_units": 2,
                }
            ]
        },
        "3",
    )
    activity = _observation(
        "bokun",
        {
            "offer_id": "offer:" + "4" * 64,
            "product_id": "product:buracao",
            "product_public_name": "Cachoeira do Buracão",
            "activity_date": "2026-09-12",
            "adults": 2,
            "children": 0,
            "total_amount": "700.00",
            "currency": "BRL",
            "available": True,
        },
        "4",
    )

    assert (
        proposal_grounds_positive_observation(
            _proposal("O Buracão está disponível por BRL 700.00."),
            (lodging, activity),
        )
        is False
    )
    assert grounded_positive_reply((lodging, activity), locale="pt-BR") == (
        "Encontrei Quarto privativo disponível de 10/09/2026 a 12/09/2026 "
        "por BRL 580.00. Nada foi reservado.",
        "Encontrei Cachoeira do Buracão disponível em 12/09/2026 por "
        "BRL 700.00. Nada foi reservado.",
    )


def test_historical_english_package_reply_is_preserved_when_it_grounds_both_reads() -> (
    None
):
    lodging = _observation(
        "cloudbeds",
        {
            "options": [
                {
                    "offer_id": "offer:" + "5" * 64,
                    "room_public_name": "Compartilhado Misto",
                    "check_in": "2026-12-16",
                    "check_out": "2026-12-18",
                    "adults": 1,
                    "children": 0,
                    "total_amount": "90.00",
                    "currency": "BRL",
                    "available_units": 1,
                },
                {
                    "offer_id": "offer:" + "6" * 64,
                    "room_public_name": "Suíte Casal",
                    "check_in": "2026-12-16",
                    "check_out": "2026-12-18",
                    "adults": 1,
                    "children": 0,
                    "total_amount": "200.00",
                    "currency": "BRL",
                    "available_units": 1,
                },
            ]
        },
        "5",
    )
    activity = _observation(
        "bokun",
        {
            "offer_id": "offer:" + "7" * 64,
            "product_id": "product:tour-4ps",
            "product_public_name": "Roteiro dos 4Ps",
            "activity_date": "2026-12-17",
            "adults": 1,
            "children": 0,
            "total_amount": "334.95",
            "currency": "BRL",
            "available": True,
        },
        "7",
    )
    proposal = _proposal(
        "The 4Ps tour is available on December 17 for BRL 334.95. "
        "Accommodation is available from December 16 to 18, with shared "
        "rooms from BRL 90 and private rooms from BRL 200. Nothing was booked."
    )

    grounded = apply_positive_grounding(
        proposal,
        (lodging, activity),
        locale="en",
        force=False,
    )

    assert grounded.reply_chunks == proposal.reply_chunks


def test_no_booking_notice_is_not_misread_as_portuguese_unavailability() -> None:
    activity = _observation(
        "bokun",
        {
            "offer_id": "offer:" + "8" * 64,
            "product_id": "product:tour-4ps",
            "product_public_name": "Roteiro dos 4Ps",
            "activity_date": "2026-12-03",
            "adults": 1,
            "children": 0,
            "total_amount": "334.95",
            "currency": "BRL",
            "available": True,
        },
        "8",
    )
    proposal = _proposal(
        "O Roteiro dos 4Ps está disponível em 03/12/2026 por BRL 334,95. "
        "Ainda não fiz nenhuma reserva."
    )

    grounded = apply_positive_grounding(
        proposal,
        (activity,),
        locale="pt-BR",
        force=False,
    )

    assert grounded.reply_chunks == proposal.reply_chunks


def test_unrelated_same_amount_does_not_ground_an_omitted_package_component() -> None:
    lodging = _observation(
        "cloudbeds",
        {
            "offer_id": "offer:" + "9" * 64,
            "room_public_name": "Suíte Alpha",
            "check_in": "2026-12-16",
            "check_out": "2026-12-18",
            "total_amount": "90.00",
            "currency": "BRL",
            "available_units": 1,
        },
        "9",
    )
    activity = _observation(
        "bokun",
        {
            "offer_id": "offer:" + "a" * 64,
            "product_public_name": "Roteiro dos 4Ps",
            "activity_date": "2026-12-17",
            "total_amount": "334.95",
            "currency": "BRL",
            "available": True,
        },
        "a",
    )

    assert (
        proposal_grounds_positive_observation(
            _proposal(
                "The 4Ps tour is available for BRL 334.95. "
                "The unrelated transfer costs BRL 90. Nothing was booked."
            ),
            (lodging, activity),
        )
        is False
    )


def test_single_positive_group_still_requires_a_bound_offer_anchor() -> None:
    activity = _observation(
        "bokun",
        {
            "offer_id": "offer:" + "b" * 64,
            "product_public_name": "Roteiro dos 4Ps",
            "activity_date": "2026-12-17",
            "total_amount": "334.95",
            "currency": "BRL",
            "available": True,
        },
        "b",
    )

    assert (
        proposal_grounds_positive_observation(
            _proposal("Another tour is available. Nothing was booked."),
            (activity,),
        )
        is False
    )


def _single_lodging_for_amount_grounding() -> ReadObservation:
    return _observation(
        "cloudbeds",
        {
            "offer_id": "offer:" + "c" * 64,
            "room_public_name": "Suíte Alpha",
            "check_in": "2026-12-16",
            "check_out": "2026-12-18",
            "total_amount": "90.00",
            "currency": "BRL",
            "available_units": 1,
        },
        "c",
    )


def test_amount_anchor_must_share_a_clause_with_its_domain() -> None:
    lodging = _single_lodging_for_amount_grounding()

    assert (
        proposal_grounds_positive_observation(
            _proposal(
                "Accommodation is available. The unrelated transfer costs BRL 90."
            ),
            (lodging,),
        )
        is False
    )


def test_amount_anchor_rejects_numeric_substrings() -> None:
    lodging = _single_lodging_for_amount_grounding()

    assert (
        proposal_grounds_positive_observation(
            _proposal("Accommodation is available for BRL 190.00."),
            (lodging,),
        )
        is False
    )


def test_amount_anchor_rejects_an_intervening_foreign_domain_in_same_clause() -> None:
    lodging = _single_lodging_for_amount_grounding()

    assert (
        proposal_grounds_positive_observation(
            _proposal(
                "Accommodation is available, and the unrelated transfer costs BRL 90."
            ),
            (lodging,),
        )
        is False
    )
