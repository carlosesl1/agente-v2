from __future__ import annotations

from datetime import datetime, timedelta, timezone

from v2_application.public_reply import (
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
