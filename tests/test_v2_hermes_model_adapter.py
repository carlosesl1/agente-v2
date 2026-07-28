from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone

import pytest

from v2_adapters.hermes_model import _proposal, _request_wire
from v2_contracts.critical_actions import (
    ApprovalBasis,
    CriticalActionKind,
    PendingCriticalActionContext,
)
from v2_contracts.model import InvalidModelProposal, ModelRequest


def test_model_public_reply_chunks_are_nfkc_normalized_before_boundary_validation() -> None:
    raw_text = "Opc\u0327a\u0303o econo\u0302mica disponi\u0301vel."
    assert raw_text != unicodedata.normalize("NFKC", raw_text)
    payload = {
        "schema": "v2-model-proposal-v2",
        "source_event_id": "batch:nfkc-model-reply-001",
        "intent": "inform",
        "reply_chunks": [f"  {raw_text}  "],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": None,
    }

    proposal = _proposal(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "batch:nfkc-model-reply-001",
    )

    assert proposal.reply_chunks == (
        unicodedata.normalize("NFKC", raw_text),
    )
    assert proposal.reply_chunks[0] == unicodedata.normalize(
        "NFKC", proposal.reply_chunks[0]
    )


def _pending_action() -> PendingCriticalActionContext:
    return PendingCriticalActionContext(
        summary_version=1,
        action_kinds=(
            CriticalActionKind.BOOK_ACTIVITY,
            CriticalActionKind.INITIATE_PAYMENT,
        ),
        public_summary=(
            "Só para confirmar: vou reservar o passeio e gerar o link do sinal. "
            "Posso fazer essa reserva?"
        ),
        expires_at=datetime(2026, 7, 28, 6, 30, tzinfo=timezone.utc),
    )


def test_pending_action_wire_is_public_only() -> None:
    request = ModelRequest(
        request_id="request:pending-action-wire",
        lead_id="manychat:pending-action-wire",
        source_event_id="batch:pending-action-wire",
        message="Pode reservar esse passeio e gerar o link.",
        locale="pt-BR",
        state_version=1,
        pending_action=_pending_action(),
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    user = json.loads(envelope["messages"][0][1])

    assert user["pending_action"] == {
        "summary_version": 1,
        "action_kinds": ["book_activity", "initiate_payment"],
        "public_summary": _pending_action().public_summary,
        "expires_at": "2026-07-28T06:30:00+00:00",
    }
    serialized = json.dumps(user, ensure_ascii=False)
    for forbidden in (
        "offer:",
        "product:",
        "provider_ref",
        "subject_signature",
        "binding",
        "profile:",
        "example.invalid",
    ):
        assert forbidden not in serialized


def test_v3_parser_binds_natural_confirmation_to_exact_action_scope() -> None:
    payload = {
        "schema": "v2-model-proposal-v3",
        "source_event_id": "batch:natural-confirmation",
        "intent": "confirm",
        "reply_chunks": ["Perfeito, vou seguir com essa reserva."],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": 1,
        "confirmed_action_kinds": ["book_activity", "initiate_payment"],
        "approval_basis": "contextual_reference",
    }

    proposal = _proposal(
        json.dumps(payload, ensure_ascii=False).encode(),
        "batch:natural-confirmation",
    )

    assert proposal.confirmed_action_kinds == (
        CriticalActionKind.BOOK_ACTIVITY,
        CriticalActionKind.INITIATE_PAYMENT,
    )
    assert proposal.approval_basis is ApprovalBasis.CONTEXTUAL_REFERENCE

    payload["confirmed_action_kinds"] = ["book_activity", "cancel_reservation"]
    changed = _proposal(
        json.dumps(payload, ensure_ascii=False).encode(),
        "batch:natural-confirmation",
    )
    assert changed.confirmed_action_kinds != proposal.confirmed_action_kinds

    payload["confirmed_action_kinds"] = ["unknown_action"]
    with pytest.raises(InvalidModelProposal, match="critical action"):
        _proposal(
            json.dumps(payload, ensure_ascii=False).encode(),
            "batch:natural-confirmation",
        )


def test_legacy_schema_cannot_confirm_a_pending_critical_action() -> None:
    payload = {
        "schema": "v2-model-proposal-v2",
        "source_event_id": "batch:legacy-confirmation",
        "intent": "confirm",
        "reply_chunks": ["Confirmado."],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": 1,
    }
    with pytest.raises(InvalidModelProposal, match="approval assertion"):
        _proposal(
            json.dumps(payload, ensure_ascii=False).encode(),
            "batch:legacy-confirmation",
        )
