from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from v2_adapters.manychat_profile import ManyChatProfileAdapter, ManyChatProfilePayloadError
from v2_application.turns import validate_productive_proposal
from v2_contracts.model import (
    EffectProposal,
    InvalidModelProposal,
    ModelFact,
    ModelProposal,
    ModelRequest,
)
from v2_contracts.profile import PrivateCustomerBinding


NOW = datetime(2026, 7, 23, 21, 0, tzinfo=timezone.utc)


class ProfileTransport:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def fetch_profile(self, subscriber_id: str) -> dict[str, object]:
        self.calls.append(subscriber_id)
        return self.payload


def _proposal(*, payment_method: str = "pix") -> ModelProposal:
    return ModelProposal(
        source_event_id="event:profile-001",
        intent="inform",
        reply_chunks=("Recebi sua preferência de pagamento.",),
        facts=(ModelFact("payment_method", payment_method),),
        read_requests=(),
        effect_proposals=(),
    )


def _complete_payload() -> dict[str, object]:
    return {
        "subscriber_id": "subscriber-001",
        "full_name": "Pessoa Qualificação",
        "email": "person@example.invalid",
        "phone_e164": "+5511999999999",
        "country_code": "BR",
    }


def test_productive_proposal_rejects_effects_and_closes_payment_method() -> None:
    valid = _proposal()

    assert validate_productive_proposal(valid) is valid
    with pytest.raises(InvalidModelProposal, match="effect proposals"):
        validate_productive_proposal(
            replace(valid, effect_proposals=(EffectProposal("write", {}),))
        )
    with pytest.raises(InvalidModelProposal, match="payment_method"):
        _proposal(payment_method="cash")


def test_profile_adapter_returns_private_binding_without_public_serialization() -> None:
    transport = ProfileTransport(_complete_payload())
    adapter = ManyChatProfileAdapter(transport=transport, ttl=timedelta(minutes=5))

    binding = adapter.read("manychat:subscriber-001", now=NOW)

    assert type(binding) is PrivateCustomerBinding
    assert binding.complete is True
    assert binding.binding_id.startswith("profile-binding:")
    assert len(binding.content_hash) == 64
    assert binding.expires_at == NOW + timedelta(minutes=5)
    assert transport.calls == ["subscriber-001"]
    assert "Pessoa Qualificação" not in repr(binding)
    assert "person@example.invalid" not in repr(binding)
    assert "+5511999999999" not in repr(binding)
    public_request = ModelRequest(
        request_id="model-request:profile-001",
        lead_id="manychat:subscriber-001",
        source_event_id="event:profile-001",
        message="Quero reservar.",
        locale="pt-BR",
        state_version=0,
    )
    assert "Pessoa Qualificação" not in repr(public_request)
    assert "person@example.invalid" not in repr(public_request)


def test_incomplete_profile_is_explicit_and_never_invents_values() -> None:
    payload = {
        "subscriber_id": "subscriber-002",
        "full_name": "Pessoa Sem Email",
        "email": None,
        "phone_e164": "+5511888888888",
        "country_code": "BR",
    }
    binding = ManyChatProfileAdapter(
        transport=ProfileTransport(payload),
        ttl=timedelta(minutes=5),
    ).read("manychat:subscriber-002", now=NOW)

    assert binding.complete is False
    assert binding.email is None
    assert binding.full_name == "Pessoa Sem Email"
    assert binding.phone_e164 == "+5511888888888"


@pytest.mark.parametrize(
    "payload",
    (
        {**_complete_payload(), "subscriber_id": "other-subscriber"},
        {**_complete_payload(), "provider_payload": {"private": "forged"}},
        {**_complete_payload(), "phone_e164": "11999999999"},
    ),
)
def test_profile_adapter_rejects_identity_conflict_open_payload_and_bad_phone(
    payload: dict[str, object],
) -> None:
    adapter = ManyChatProfileAdapter(
        transport=ProfileTransport(payload),
        ttl=timedelta(minutes=5),
    )

    with pytest.raises(ManyChatProfilePayloadError):
        adapter.read("manychat:subscriber-001", now=NOW)
