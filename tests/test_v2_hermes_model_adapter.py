from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone

import pytest

from v2_adapters.hermes_model import (
    _PROTOCOL_REPAIR_SUFFIX,
    _proposal,
    _request_wire,
)
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


def test_protocol_repair_preserves_short_contextual_confirmation_semantics() -> None:
    normalized = _PROTOCOL_REPAIR_SUFFIX.casefold()

    assert "sim" in normalized
    assert "confirmação semântica curta" in normalized
    assert "pending_action" in normalized
    assert "não é aprovação" not in normalized


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


def test_private_profile_completeness_wire_is_boolean_only() -> None:
    request = ModelRequest(
        request_id="request:private-profile-marker",
        lead_id="manychat:private-profile-marker",
        source_event_id="batch:private-profile-marker",
        message="Quero reservar.",
        locale="pt-BR",
        state_version=0,
        private_profile_complete=True,
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    user = json.loads(envelope["messages"][0][1])

    assert user["private_profile_complete"] is True
    assert user["handoff_active"] is False
    assert user["confirmation_review_required"] is False
    assert user["selection_review_required"] is False
    serialized = json.dumps(user, ensure_ascii=False)
    for forbidden in (
        "Pessoa Qualificação",
        "person@example.invalid",
        "+551",
        "profile-binding:",
        "content_hash",
    ):
        assert forbidden not in serialized


def test_refresh_revocation_outcome_is_a_closed_non_private_request_marker() -> None:
    request = ModelRequest(
        request_id="request:refresh-revocation-outcome",
        lead_id="manychat:refresh-revocation-outcome",
        source_event_id="batch:refresh-revocation-outcome",
        message="Foi criada alguma reserva?",
        locale="pt-BR",
        state_version=5,
        critical_outcome="proposal_revoked_after_refresh",
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    user = json.loads(envelope["messages"][0][1])

    assert user["critical_outcome"] == "proposal_revoked_after_refresh"
    serialized = json.dumps(user, ensure_ascii=False)
    assert "example.invalid" not in serialized
    assert "profile-binding:" not in serialized

    with pytest.raises(InvalidModelProposal, match="critical_outcome"):
        ModelRequest(
            request_id="request:invalid-critical-outcome",
            lead_id="manychat:invalid-critical-outcome",
            source_event_id="batch:invalid-critical-outcome",
            message="Status?",
            locale="pt-BR",
            state_version=5,
            critical_outcome="model_may_invent_anything",
        )


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


def test_v4_parser_exposes_structured_selection_request_without_authority() -> None:
    payload = {
        "schema": "v2-model-proposal-v4",
        "source_event_id": "batch:structured-selection",
        "intent": "inform",
        "reply_chunks": ["Vou verificar a oferta atual."],
        "facts": [],
        "read_requests": [
            {
                "request_id": "batch:structured-selection:read:activity",
                "kind": "activity",
                "product_id": "product:tour-4ps",
                "activity_date": "2026-11-18",
                "participants": 1,
            }
        ],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": None,
        "confirmed_action_kinds": [],
        "approval_basis": None,
        "selection_requested": True,
    }

    proposal = _proposal(
        json.dumps(payload, ensure_ascii=False).encode(),
        "batch:structured-selection",
    )
    assert proposal.selection_requested is True
    assert proposal.intent == "inform"
    assert len(proposal.read_requests) == 1

    payload["read_requests"] = []
    with pytest.raises(InvalidModelProposal, match="selection_requested"):
        _proposal(
            json.dumps(payload, ensure_ascii=False).encode(),
            "batch:structured-selection",
        )


def test_v5_parser_distinguishes_preserve_from_revocation() -> None:
    payload = {
        "schema": "v2-model-proposal-v5",
        "source_event_id": "batch:pending-preserve",
        "intent": "adjust",
        "reply_chunks": ["Vou manter o mesmo resumo para você revisar."],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": None,
        "confirmed_action_kinds": [],
        "approval_basis": None,
        "selection_requested": False,
        "pending_disposition": "preserve",
    }
    proposal = _proposal(
        json.dumps(payload, ensure_ascii=False).encode(),
        "batch:pending-preserve",
    )
    assert proposal.intent == "adjust"
    assert proposal.pending_disposition == "preserve"

    payload["intent"] = "inform"
    informational = _proposal(
        json.dumps(payload, ensure_ascii=False).encode(),
        "batch:pending-preserve",
    )
    assert informational.intent == "inform"
    assert informational.pending_disposition is None

    payload["intent"] = "adjust"
    payload["pending_disposition"] = "unknown"
    with pytest.raises(InvalidModelProposal, match="pending_disposition"):
        _proposal(
            json.dumps(payload, ensure_ascii=False).encode(),
            "batch:pending-preserve",
        )


def test_confirmation_review_requires_pending_action() -> None:
    with pytest.raises(InvalidModelProposal, match="pending critical action"):
        ModelRequest(
            request_id="request:review-without-pending",
            lead_id="lead:review-without-pending",
            source_event_id="batch:review-without-pending",
            message="Confirmado.",
            locale="pt-BR",
            state_version=0,
            confirmation_review_required=True,
        )


def test_selection_review_requires_complete_private_profile_marker() -> None:
    with pytest.raises(InvalidModelProposal, match="complete private profile"):
        ModelRequest(
            request_id="request:selection-review-incomplete-profile",
            lead_id="lead:selection-review-incomplete-profile",
            source_event_id="batch:selection-review-incomplete-profile",
            message="Prepare o resumo.",
            locale="pt-BR",
            state_version=0,
            selection_review_required=True,
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
