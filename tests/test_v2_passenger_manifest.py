from __future__ import annotations

import json
from datetime import date

import pytest

from reservation_boundary import (
    ConversationProjection,
    ConversationStage,
    StringSlot,
    TypedFact,
)
from reservation_domain import PassengerFacts, Party
from v2_adapters.hermes_model import _proposal, _request_wire
from v2_application.passengers import (
    PassengerManifestConflict,
    complete_manifest,
    manifest_status,
    merge_manifest,
)
from v2_application.turn_executor import _state_model_facts
from v2_contracts.model import ModelRequest
from v2_contracts.passengers import PassengerInput, PassengerManifestStatus


def _input(
    position: int,
    participant_type: str,
    *,
    full_name: str | None,
    birth_date: date | None,
    gender: str | None,
    country_code: str | None,
) -> PassengerInput:
    return PassengerInput(
        position=position,
        participant_type=participant_type,
        full_name=full_name,
        birth_date=birth_date,
        gender=gender,
        country_code=country_code,
    )


def test_v6_parser_accepts_closed_partial_passenger_updates() -> None:
    payload = {
        "schema": "v2-model-proposal-v6",
        "source_event_id": "batch:passenger-v6",
        "intent": "inform",
        "reply_chunks": ["Ainda preciso da data de nascimento do passageiro 2."],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "confirmed_summary_version": None,
        "target_offer_ids": [],
        "confirmed_action_kinds": [],
        "approval_basis": None,
        "selection_requested": False,
        "pending_disposition": None,
        "passengers": [
            {
                "position": 2,
                "participant_type": "child",
                "full_name": "Pessoa Sintética Dois",
                "birth_date": None,
                "gender": None,
                "country_code": "BR",
            }
        ],
    }

    parsed = _proposal(json.dumps(payload, ensure_ascii=False).encode(), "batch:passenger-v6")

    assert parsed.passengers == (
        _input(
            2,
            "child",
            full_name="Pessoa Sintética Dois",
            birth_date=None,
            gender=None,
            country_code="BR",
        ),
    )


@pytest.mark.parametrize(
    "mutation",
    (
        {"position": 0},
        {"participant_type": "senior"},
        {"unknown": "value"},
    ),
)
def test_v6_parser_rejects_invalid_or_open_passenger_updates(
    mutation: dict[str, object],
) -> None:
    passenger: dict[str, object] = {
        "position": 1,
        "participant_type": "adult",
        "full_name": "Pessoa Sintética",
        "birth_date": "1990-01-02",
        "gender": "f",
        "country_code": "BR",
    }
    passenger.update(mutation)
    payload = {
        "schema": "v2-model-proposal-v6",
        "source_event_id": "batch:passenger-invalid",
        "intent": "inform",
        "reply_chunks": ["Vou revisar os dados."],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "confirmed_summary_version": None,
        "target_offer_ids": [],
        "confirmed_action_kinds": [],
        "approval_basis": None,
        "selection_requested": False,
        "pending_disposition": None,
        "passengers": [passenger],
    }

    with pytest.raises(ValueError):
        _proposal(
            json.dumps(payload, ensure_ascii=False).encode(),
            "batch:passenger-invalid",
        )


def test_manifest_merges_incrementally_and_closes_only_at_exact_party() -> None:
    party = Party(2, 1)
    first = merge_manifest(
        None,
        (
            _input(
                1,
                "adult",
                full_name="Pessoa Sintética Um",
                birth_date=date(1990, 1, 2),
                gender="f",
                country_code="BR",
            ),
            _input(
                2,
                "adult",
                full_name="Pessoa Sintética Dois",
                birth_date=None,
                gender=None,
                country_code="BR",
            ),
        ),
        party,
    )
    first_status = manifest_status(first, party)
    assert first_status.complete_positions == (1,)
    assert first_status.missing_by_position == (
        (2, ("birth_date", "gender")),
        (3, ("full_name", "birth_date", "gender", "country_code")),
    )
    assert complete_manifest(first, party) is None

    completed = merge_manifest(
        first,
        (
            _input(
                2,
                "adult",
                full_name=None,
                birth_date=date(1992, 3, 4),
                gender="m",
                country_code=None,
            ),
            _input(
                3,
                "child",
                full_name="Pessoa Sintética Três",
                birth_date=date(2016, 5, 6),
                gender="f",
                country_code="BR",
            ),
        ),
        party,
    )

    assert complete_manifest(completed, party) == (
        PassengerFacts(1, "adult", "Pessoa Sintética Um", date(1990, 1, 2), "f", "BR"),
        PassengerFacts(2, "adult", "Pessoa Sintética Dois", date(1992, 3, 4), "m", "BR"),
        PassengerFacts(3, "child", "Pessoa Sintética Três", date(2016, 5, 6), "f", "BR"),
    )
    assert merge_manifest(completed, (), party) == completed
    assert json.dumps(
        json.loads(completed),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) == completed


def test_manifest_conflict_and_party_change_fail_closed() -> None:
    original = merge_manifest(
        None,
        (
            _input(
                1,
                "adult",
                full_name="Pessoa Original",
                birth_date=date(1990, 1, 2),
                gender="f",
                country_code="BR",
            ),
        ),
        Party(2, 0),
    )

    with pytest.raises(PassengerManifestConflict, match="conflicts"):
        merge_manifest(
            original,
            (
                _input(
                    1,
                    "adult",
                    full_name="Pessoa Alterada",
                    birth_date=None,
                    gender=None,
                    country_code=None,
                ),
            ),
            Party(2, 0),
        )

    reset = merge_manifest(
        original,
        (
            _input(
                1,
                "adult",
                full_name="Nova Party",
                birth_date=None,
                gender=None,
                country_code=None,
            ),
        ),
        Party(3, 0),
    )
    assert "Pessoa Original" not in reset
    assert complete_manifest(reset, Party(3, 0)) is None


def test_model_request_exposes_manifest_progress_without_private_values() -> None:
    status = PassengerManifestStatus(
        required_adults=2,
        required_children=1,
        complete_positions=(1,),
        missing_by_position=(
            (2, ("birth_date", "gender")),
            (3, ("full_name", "birth_date", "gender", "country_code")),
        ),
    )
    request = ModelRequest(
        request_id="request:manifest-status",
        lead_id="manychat:manifest-status",
        source_event_id="batch:manifest-status",
        message="Enviei os dados solicitados.",
        locale="pt-BR",
        state_version=2,
        passenger_manifest_status=status,
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    user = json.loads(envelope["messages"][0][1])
    serialized = json.dumps(user, ensure_ascii=False)

    assert user["passenger_manifest_status"] == {
        "required_adults": 2,
        "required_children": 1,
        "complete_positions": [1],
        "missing_by_position": [
            {"position": 2, "fields": ["birth_date", "gender"]},
            {
                "position": 3,
                "fields": ["full_name", "birth_date", "gender", "country_code"],
            },
        ],
    }
    assert "Pessoa Original" not in serialized
    assert "1990-01-02" not in serialized


def test_private_manifest_fact_round_trips_but_is_not_model_state() -> None:
    manifest = merge_manifest(
        None,
        (
            _input(
                1,
                "adult",
                full_name="Pessoa Privada",
                birth_date=date(1990, 1, 2),
                gender="f",
                country_code="BR",
            ),
        ),
        Party(2, 0),
    )
    fact = TypedFact("passenger_manifest", StringSlot(manifest), "a" * 64)
    projection = ConversationProjection(
        stage=ConversationStage.RECEPTIONIST,
        desired_services=(),
        locale="pt-BR",
        facts=(fact,),
        reservation_execution_projection=None,
    )

    assert TypedFact.from_canonical_bytes(fact.to_canonical_bytes()) == fact
    assert _state_model_facts(projection) == ()
