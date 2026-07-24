from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from v2_adapters.hermes_model import HermesModelAdapter
from v2_contracts.model import InvalidModelProposal, ModelFact, ModelProposal, ModelRequest


def _base_proposal(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "source_event_id": "event:package-contract",
        "intent": "select",
        "reply_chunks": ("Encontrei as duas opções.",),
        "facts": (
            ModelFact("language", "pt-BR"),
            ModelFact("service", "package"),
            ModelFact("start_date", date(2026, 8, 10)),
            ModelFact("end_date", date(2026, 8, 12)),
            ModelFact("activity_date", date(2026, 8, 11)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
            ModelFact("payment_method", "stripe"),
        ),
        "read_requests": (),
        "effect_proposals": (),
        "target_offer_id": None,
        "target_offer_ids": ("offer:lodging-public", "offer:activity-public"),
        "confirmed_summary_version": None,
    }
    values.update(overrides)
    return values


def test_package_model_proposal_accepts_exactly_two_public_offer_ids() -> None:
    proposal = ModelProposal(**_base_proposal())

    assert proposal.target_offer_id is None
    assert proposal.target_offer_ids == (
        "offer:lodging-public",
        "offer:activity-public",
    )


@pytest.mark.parametrize(
    "targets",
    [
        (),
        ("offer:only-one",),
        ("offer:duplicate", "offer:duplicate"),
        ("product:buracao", "offer:lodging-public"),
        ("room-private-001", "offer:activity-public"),
    ],
)
def test_package_model_proposal_rejects_partial_duplicate_or_private_targets(
    targets: tuple[str, ...],
) -> None:
    with pytest.raises(InvalidModelProposal):
        ModelProposal(**_base_proposal(target_offer_ids=targets))


def test_model_cannot_mix_single_and_package_targets() -> None:
    with pytest.raises(InvalidModelProposal, match="target"):
        ModelProposal(
            **_base_proposal(target_offer_id="offer:third-public")
        )


def test_hermes_adapter_parses_closed_v2_package_response() -> None:
    raw = {
        "schema": "v2-model-proposal-v2",
        "source_event_id": "event:package-contract",
        "intent": "select",
        "reply_chunks": ["Encontrei as duas opções."],
        "facts": [
            {"name": "language", "value": "pt-BR"},
            {"name": "service", "value": "package"},
            {"name": "start_date", "value": "2026-08-10"},
            {"name": "end_date", "value": "2026-08-12"},
            {"name": "activity_date", "value": "2026-08-11"},
            {"name": "adults", "value": 2},
            {"name": "children", "value": 0},
            {"name": "payment_method", "value": "stripe"},
        ],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": ["offer:lodging-public", "offer:activity-public"],
        "confirmed_summary_version": None,
    }
    encoded = json.dumps(raw, sort_keys=True).encode()

    def run(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=b"child-log\nPHASE8_RESULT\x00" + encoded,
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("python", "child.py"),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"t" * 32,
        run=run,
        environ={},
    )
    result = adapter.complete(
        ModelRequest(
            request_id="request:package-contract",
            lead_id="manychat:1873018537",
            source_event_id="event:package-contract",
            message="Quero o pacote",
            locale="pt-BR",
            state_version=0,
        )
    )

    assert result.target_offer_ids == (
        "offer:lodging-public",
        "offer:activity-public",
    )
    assert result.effect_proposals == ()
