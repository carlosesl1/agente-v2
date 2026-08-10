from __future__ import annotations

import json
from types import SimpleNamespace
import unicodedata
from datetime import date, datetime, timedelta, timezone

import pytest

from v2_adapters.hermes_model import (
    _CONFIRMATION_REVIEW_SYSTEM_PROMPT,
    _PROTOCOL_REPAIR_SUFFIX,
    _confirmation_review,
    _confirmation_review_wire,
    _proposal,
    _proposal_from_confirmation_review,
    _request_wire,
    HermesModelAdapter,
)
from v2_contracts.confirmation_review import (
    ContextualConfirmationDecision,
    ContextualConfirmationReview,
)
from v2_contracts.critical_actions import (
    ApprovalBasis,
    CriticalActionKind,
    PendingCriticalActionContext,
)
from v2_contracts.model import (
    ConsultationHistoryEntry,
    ConversationExchange,
    InvalidModelProposal,
    ModelFact,
    ModelRequest,
)
from v2_contracts.providers import ReadKind, ReadObservation, ReadRequest


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


def test_v7_parser_requires_and_normalizes_typed_clarification_question() -> None:
    payload = {
        "schema": "v2-model-proposal-v7",
        "source_event_id": "batch:typed-question-001",
        "intent": "inform",
        "reply_chunks": [
            "I found one option.",
            "Who will be the reservation holder?",
        ],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": None,
        "confirmed_action_kinds": [],
        "approval_basis": None,
        "selection_requested": False,
        "pending_disposition": None,
        "passengers": [],
        "clarification_question": "  Who will be the reservation holder?  ",
    }

    parsed = _proposal(
        json.dumps(payload).encode(),
        "batch:typed-question-001",
    )

    assert parsed.clarification_question == "Who will be the reservation holder?"


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
        private_customer_fact_names=(
            "full_name",
            "phone_e164",
        ),
        private_profile_complete=True,
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    user = json.loads(envelope["messages"][0][1])

    assert user["private_profile_complete"] is True
    assert user["private_customer_fact_names"] == ["full_name", "phone_e164"]
    assert "presence-only" in envelope["system_prompt"]
    assert "Never output phone_e164 from conversational text" in envelope[
        "system_prompt"
    ]
    assert "CURRENT-TURN COMMERCIAL PROGRESSION" in envelope["system_prompt"]
    assert '"one adult" or "1 adulto"' in envelope["system_prompt"]
    assert user["handoff_status"] is None
    assert user["confirmation_review_required"] is False
    assert user["selection_review_required"] is False
    assert user["active_execution_status"] is None
    assert user["recap_reuse_required"] is False
    assert "identity is a write-boundary requirement" in envelope["system_prompt"]
    serialized = json.dumps(user, ensure_ascii=False)
    for forbidden in (
        "Pessoa Qualificação",
        "person@example.invalid",
        "+551",
        "profile-binding:",
        "content_hash",
    ):
        assert forbidden not in serialized


def test_recent_dialogue_is_transport_only_context_before_complete_current_request() -> None:
    request = ModelRequest(
        request_id="request:recent-dialogue-wire",
        lead_id="manychat:recent-dialogue-wire",
        source_event_id="batch:recent-dialogue-wire",
        message="And can I pay with Wise?",
        locale="en-US",
        state_version=2,
        recent_dialogue=(
            ConversationExchange(
                "I need a private room from 18 to 20 November for one adult.",
                ("I found a private room.", "Would you like its details?"),
            ),
            ConversationExchange(
                "Yes, and I am not Brazilian.",
                ("I can continue in English.",),
            ),
        ),
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))

    assert envelope["messages"][:-1] == [
        [
            "user",
            "I need a private room from 18 to 20 November for one adult.",
        ],
        [
            "assistant",
            "I found a private room.\n\nWould you like its details?",
        ],
        ["user", "Yes, and I am not Brazilian."],
        ["assistant", "I can continue in English."],
    ]
    current = json.loads(envelope["messages"][-1][1])
    assert envelope["messages"][-1][0] == "user"
    assert current["message"] == "And can I pay with Wise?"
    assert "recent_dialogue" not in current


def test_handoff_status_wire_preserves_receipt_aware_state() -> None:
    request = ModelRequest(
        request_id="request:handoff-status-wire",
        lead_id="manychat:handoff-status-wire",
        source_event_id="batch:handoff-status-wire",
        message="Has the team received it?",
        locale="en",
        state_version=3,
        handoff_status="acknowledgement_pending",
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    current = json.loads(envelope["messages"][-1][1])

    assert current["handoff_status"] == "acknowledgement_pending"


def test_handoff_status_rejects_unverified_free_form_state() -> None:
    with pytest.raises(InvalidModelProposal, match="handoff_status"):
        ModelRequest(
            request_id="request:handoff-status-invalid",
            lead_id="manychat:handoff-status-invalid",
            source_event_id="batch:handoff-status-invalid",
            message="Has the team received it?",
            locale="en",
            state_version=3,
            handoff_status="human_is_following",
        )


def test_consultation_history_wire_is_public_bounded_and_recap_only() -> None:
    history = ConsultationHistoryEntry(
        observation_hash="a" * 64,
        observed_at=datetime(2026, 8, 4, 2, 30, tzinfo=timezone.utc),
        expires_at=datetime(2026, 8, 4, 2, 35, tzinfo=timezone.utc),
        fresh_at_turn_start=False,
        public_context={
            "service": "activity",
            "status": "negative",
            "query": {
                "product_id": "product:tour-4ps",
                "activity_date": "2026-09-13",
                "adults": 2,
                "children": 0,
            },
            "offers": [],
            "offer_count": 0,
            "offers_truncated": False,
        },
    )
    request = ModelRequest(
        request_id="request:consultation-history-wire",
        lead_id="manychat:consultation-history-wire",
        source_event_id="batch:consultation-history-wire",
        message="Resuma o que você consultou, sem reservar.",
        locale="pt-BR",
        state_version=2,
        consultation_history=(history,),
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    user = json.loads(envelope["messages"][0][1])
    serialized = json.dumps(user, ensure_ascii=False)
    prompt = envelope["system_prompt"].casefold()

    assert user["observations"] == []
    assert user["consultation_history"] == [
        {
            "observed_at": "2026-08-04T02:30:00+00:00",
            "expires_at": "2026-08-04T02:35:00+00:00",
            "fresh_at_turn_start": False,
            "usage": "recap_only",
            "public_context": history.public_context,
        }
    ]
    assert "recap-only" in prompt
    assert "fresh provider read" in prompt
    assert "selection" in prompt
    assert "reservation" in prompt
    for forbidden in (
        history.observation_hash,
        "private_binding_hash",
        "source_evidence_hash",
        "request_hash",
        "offer:",
    ):
        assert forbidden not in serialized


def test_active_execution_and_recap_reuse_are_closed_public_markers() -> None:
    history = ConsultationHistoryEntry(
        observation_hash="b" * 64,
        observed_at=datetime(2026, 8, 4, 2, 30, tzinfo=timezone.utc),
        expires_at=datetime(2026, 8, 4, 2, 35, tzinfo=timezone.utc),
        fresh_at_turn_start=True,
        public_context={
            "service": "lodging",
            "status": "negative",
            "query": {
                "check_in": "2026-09-13",
                "check_out": "2026-09-15",
                "adults": 2,
                "children": 0,
            },
            "offers": [],
            "offer_count": 0,
            "offers_truncated": False,
        },
    )
    request = ModelRequest(
        request_id="request:closed-runtime-markers",
        lead_id="manychat:closed-runtime-markers",
        source_event_id="batch:closed-runtime-markers",
        message="Recapitule sem fazer outra consulta.",
        locale="pt-BR",
        state_version=3,
        consultation_history=(history,),
        active_execution_status="queued",
        recap_reuse_required=True,
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    user = json.loads(envelope["messages"][0][1])
    prompt = envelope["system_prompt"]

    assert user["active_execution_status"] == "queued"
    assert user["recap_reuse_required"] is True
    assert user["observations"] == []
    assert "ACTIVE EXECUTION STATUS" in prompt
    assert "FRESH CONSULTATION REUSE" in prompt
    with pytest.raises(InvalidModelProposal, match="closed request catalog"):
        ModelRequest(
            request_id="request:bad-active-status",
            lead_id="manychat:bad-active-status",
            source_event_id="batch:bad-active-status",
            message="Status.",
            locale="pt-BR",
            state_version=0,
            active_execution_status="finished",
        )


def test_original_private_context_reaches_maya_with_holder_semantics() -> None:
    lead_name = "Ana Titular Silva"
    lead_email = "ana.titular@example.invalid"
    spouse_name = "Beatriz Acompanhante Souza"
    spouse_email = "beatriz.acompanhante@example.invalid"
    typed_phone = "+1" + "202" + "555" + "0168"
    original_message = (
        f"Eu sou {lead_name}, meu e-mail é {lead_email} e sou do Brasil. "
        f"Minha esposa {spouse_name} usa {spouse_email}. "
        f"Meu telefone digitado é {typed_phone}."
    )
    request = ModelRequest(
        request_id="request:original-holder-context",
        lead_id="manychat:original-holder-context",
        source_event_id="batch:original-holder-context",
        message=original_message,
        locale="pt-BR",
        state_version=0,
        private_customer_fact_names=("phone_e164",),
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    user = json.loads(envelope["messages"][0][1])
    prompt = envelope["system_prompt"].casefold()

    assert user["message"] == original_message
    assert "[private" not in user["message"]
    assert lead_email in user["message"]
    assert spouse_email in user["message"]
    assert typed_phone in user["message"]
    assert "reservation holder" in prompt
    assert "spouse" in prompt
    assert "third party" in prompt
    assert "explicitly" in prompt
    assert "phone_e164" in prompt
    assert "do not guess" in prompt
    assert "correction wins" in prompt
    assert "pending_disposition=revoke" in prompt
    assert "bracketed private-field markers" not in prompt


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


def _confirmation_review_request() -> ModelRequest:
    return ModelRequest(
        request_id="request:contextual-confirmation-review",
        lead_id="manychat:private-lead-should-not-cross-review-wire",
        source_event_id="batch:contextual-confirmation-review",
        message="Está certinho como você resumiu; siga com tudo aquilo.",
        locale="pt-BR",
        state_version=7,
        state_facts=(ModelFact("language", "pt-BR"),),
        private_customer_fact_names=("full_name", "email"),
        private_profile_complete=True,
        handoff_status=None,
        pending_action=_pending_action(),
        confirmation_review_required=True,
    )


def _review_payload(decision: str = "approve") -> bytes:
    return json.dumps(
        {
            "schema": "v2-contextual-confirmation-review-v1",
            "source_event_id": "batch:contextual-confirmation-review",
            "decision": decision,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_confirmation_review_wire_is_minimal_and_public_only() -> None:
    envelope = json.loads(
        _confirmation_review_wire(
            _confirmation_review_request(),
            _CONFIRMATION_REVIEW_SYSTEM_PROMPT,
        )
    )
    user = json.loads(envelope["messages"][0][1])

    assert set(user) == {
        "request_id",
        "source_event_id",
        "message",
        "locale",
        "pending_action",
    }
    assert user["pending_action"] == {
        "summary_version": 1,
        "action_kinds": ["book_activity", "initiate_payment"],
        "public_summary": _pending_action().public_summary,
        "expires_at": "2026-07-28T06:30:00+00:00",
    }
    serialized = json.dumps(user, ensure_ascii=False)
    for forbidden in (
        "private-lead-should-not-cross-review-wire",
        "state_facts",
        "private_customer_fact_names",
        "private_profile_complete",
        "handoff_status",
        "observations",
        "passenger_manifest_status",
        "offer:",
        "provider_ref",
        "subject_signature",
    ):
        assert forbidden not in serialized


def test_confirmation_review_parser_requires_exact_closed_schema_and_source() -> None:
    review = _confirmation_review(
        _review_payload(),
        "batch:contextual-confirmation-review",
    )
    assert review == ContextualConfirmationReview(
        source_event_id="batch:contextual-confirmation-review",
        decision=ContextualConfirmationDecision.APPROVE,
    )

    invalid_payloads = (
        b'{"schema":"v2-contextual-confirmation-review-v1",'
        b'"source_event_id":"batch:contextual-confirmation-review",'
        b'"decision":"approve","decision":"uncertain"}',
        _review_payload("unknown"),
        _review_payload().replace(
            b"batch:contextual-confirmation-review",
            b"batch:wrong-source",
        ),
        _review_payload().replace(
            b"v2-contextual-confirmation-review-v1",
            b"v2-contextual-confirmation-review-v2",
        ),
        _review_payload()[:-1] + b',"extra":true}',
    )
    for payload in invalid_payloads:
        with pytest.raises(InvalidModelProposal):
            _confirmation_review(payload, "batch:contextual-confirmation-review")


@pytest.mark.parametrize(
    ("decision", "intent", "pending_disposition"),
    (
        (ContextualConfirmationDecision.APPROVE, "confirm", None),
        (ContextualConfirmationDecision.REJECT, "adjust", "revoke"),
        (ContextualConfirmationDecision.ADJUST, "adjust", "revoke"),
        (ContextualConfirmationDecision.UNCERTAIN, "inform", None),
    ),
)
def test_parent_owns_confirmation_binding(
    decision: ContextualConfirmationDecision,
    intent: str,
    pending_disposition: str | None,
) -> None:
    request = _confirmation_review_request()
    proposal = _proposal_from_confirmation_review(
        request,
        ContextualConfirmationReview(
            source_event_id=request.source_event_id,
            decision=decision,
        ),
    )

    assert proposal.intent == intent
    assert proposal.pending_disposition == pending_disposition
    assert proposal.facts == ()
    assert proposal.read_requests == ()
    assert proposal.effect_proposals == ()
    assert proposal.passengers == ()
    assert proposal.target_offer_id is None
    assert proposal.target_offer_ids == ()
    if decision is ContextualConfirmationDecision.APPROVE:
        assert proposal.confirmed_summary_version == request.pending_action.summary_version
        assert proposal.confirmed_action_kinds == request.pending_action.action_kinds
        assert proposal.approval_basis is ApprovalBasis.CONTEXTUAL_REFERENCE
    else:
        assert proposal.confirmed_summary_version is None
        assert proposal.confirmed_action_kinds == ()
        assert proposal.approval_basis is None


def test_adapter_routes_confirmation_review_through_narrow_audited_wire() -> None:
    captured: list[bytes] = []

    def run(command, **kwargs):
        assert command == ("python", "-m", "v2_host.hermes_child")
        captured.append(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + _review_payload(),
            stderr=b"",
        )

    request = _confirmation_review_request()
    adapter = HermesModelAdapter(
        command=("python", "-m", "v2_host.hermes_child"),
        system_prompt="general proposal prompt must not be used for review",
        timeout=10,
        transcript_key=b"contextual-review-transcript-key-001",
        run=run,
        environ={"PATH": "/usr/bin", "FORBIDDEN_SECRET": "must-not-cross"},
    )

    turn = adapter.complete_audited(request)

    assert turn.proposal.intent == "confirm"
    assert turn.proposal.confirmed_summary_version == request.pending_action.summary_version
    assert turn.proposal.confirmed_action_kinds == request.pending_action.action_kinds
    assert turn.proposal.approval_basis is ApprovalBasis.CONTEXTUAL_REFERENCE
    assert len(turn.frames) == 1
    assert turn.frames[0].stdin_bytes == captured[0]
    envelope = json.loads(captured[0])
    assert envelope["system_prompt"] == _CONFIRMATION_REVIEW_SYSTEM_PROMPT
    serialized = captured[0].decode()
    assert "general proposal prompt must not be used for review" not in serialized
    assert "private-lead-should-not-cross-review-wire" not in serialized
    assert "must-not-cross" not in serialized


def test_invalid_confirmation_reviews_fall_back_to_unbound_inform() -> None:
    attempts = 0

    def run(command, **kwargs):
        nonlocal attempts
        attempts += 1
        response = (
            b'{"schema":"v2-contextual-confirmation-review-v1",'
            b'"source_event_id":"batch:wrong-source","decision":"approve"}'
            if attempts == 1
            else b'{"schema":"v2-contextual-confirmation-review-v1",'
            b'"source_event_id":"batch:contextual-confirmation-review",'
            b'"decision":"unknown"}'
        )
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + response,
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="unused-general-prompt",
        timeout=10,
        transcript_key=b"invalid-review-transcript-key-0001",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(_confirmation_review_request())

    assert attempts == 2
    assert turn.proposal.intent == "inform"
    assert turn.proposal.confirmed_summary_version is None
    assert turn.proposal.confirmed_action_kinds == ()
    assert turn.proposal.approval_basis is None
    assert len(turn.frames) == 3
    assert turn.closure.ephemeral_session_id.startswith(
        "deterministic:protocol-fallback:"
    )


def test_adapter_revises_structural_noop_once_with_same_complete_context() -> None:
    request = ModelRequest(
        request_id="request:progress-review-runtime",
        lead_id="manychat:progress-review-runtime",
        source_event_id="batch:progress-review-runtime",
        message="I need a private room from 18 to 20 November for one adult.",
        locale="en",
        state_version=0,
    )

    def payload(*, reply: str, question: str | None) -> bytes:
        return json.dumps(
            {
                "schema": "v2-model-proposal-v7",
                "source_event_id": request.source_event_id,
                "intent": "inform",
                "reply_chunks": [reply],
                "facts": [],
                "read_requests": [],
                "effect_proposals": [],
                "target_offer_id": None,
                "target_offer_ids": [],
                "confirmed_summary_version": None,
                "confirmed_action_kinds": [],
                "approval_basis": None,
                "selection_requested": False,
                "pending_disposition": None,
                "passengers": [],
                "clarification_question": question,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    responses = [
        payload(reply="I am ready to help.", question=None),
        payload(
            reply="Which year should I use for those November dates?",
            question="Which year should I use for those November dates?",
        ),
    ]
    seen = []

    def run(command, **kwargs):
        envelope = json.loads(kwargs["input"])
        current = json.loads(envelope["messages"][-1][1])
        seen.append(current)
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"progress-review-transcript-key-001",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(request)

    assert [item["progress_review_required"] for item in seen] == [False, True]
    assert [item["message"] for item in seen] == [request.message, request.message]
    assert turn.proposal.clarification_question == (
        "Which year should I use for those November dates?"
    )
    assert len(turn.frames) == 2


def test_current_observation_normalizes_recursive_reads_without_second_inference() -> None:
    lodging_read = ReadRequest(
        request_id="batch:negative-package:read:lodging",
        kind=ReadKind.LODGING,
        check_in=date(2026, 9, 12),
        check_out=date(2026, 9, 15),
        adults=2,
        children=0,
    )
    read = ReadRequest(
        request_id="batch:negative-package:read:activity",
        kind=ReadKind.ACTIVITY,
        product_id="product:tour-4ps",
        activity_date=date(2026, 9, 13),
        participants=2,
    )
    request = ModelRequest(
        request_id="request:negative-package:followup",
        lead_id="manychat:negative-package",
        source_event_id="batch:negative-package",
        message="Quero reservar a hospedagem e o 4Ps.",
        locale="pt-BR",
        state_version=4,
        observations=(
            ReadObservation(
                request_hash=lodging_read.canonical_hash(),
                provider="cloudbeds",
                observed_at=datetime(2026, 8, 4, 7, 3, tzinfo=timezone.utc),
                expires_at=datetime(2026, 8, 4, 7, 8, tzinfo=timezone.utc),
                public_payload={
                    "check_in": "2026-09-12",
                    "check_out": "2026-09-15",
                    "adults": 2,
                    "children": 0,
                    "available": False,
                },
                private_binding_hash="e" * 64,
            ),
            ReadObservation(
                request_hash=read.canonical_hash(),
                provider="bokun",
                observed_at=datetime(2026, 8, 4, 7, 3, tzinfo=timezone.utc),
                expires_at=datetime(2026, 8, 4, 7, 8, tzinfo=timezone.utc),
                public_payload={
                    "product_id": "product:tour-4ps",
                    "activity_date": "2026-09-13",
                    "adults": 2,
                    "children": 0,
                    "participants": 2,
                    "available": False,
                },
                private_binding_hash="f" * 64,
            ),
        ),
    )
    payload = json.dumps(
        {
            "schema": "v2-model-proposal-v6",
            "source_event_id": request.source_event_id,
            "intent": "inform",
            "reply_chunks": [
                "O 4Ps continua indisponível nessa data, então nada foi reservado."
            ],
            "facts": [],
            "read_requests": [
                {
                    "request_id": read.request_id,
                    "kind": "activity",
                    "product_id": read.product_id,
                    "activity_date": read.activity_date.isoformat(),
                    "participants": read.participants,
                }
            ],
            "effect_proposals": [],
            "target_offer_id": None,
            "target_offer_ids": [],
            "confirmed_summary_version": None,
            "confirmed_action_kinds": [],
            "approval_basis": None,
            "selection_requested": True,
            "pending_disposition": None,
            "passengers": [],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    attempts = 0

    def run(command, **kwargs):
        nonlocal attempts
        attempts += 1
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + payload,
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"recursive-read-normalization-key-001",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(request)

    assert attempts == 1
    assert turn.proposal.intent == "inform"
    assert turn.proposal.reply_chunks == (
        "A hospedagem e o passeio solicitados não estão disponíveis para as "
        "datas consultadas. Nada foi reservado. Posso consultar outras datas.",
    )
    assert turn.proposal.read_requests == ()
    assert turn.proposal.selection_requested is False
    assert turn.proposal.effect_proposals == ()
    assert len(turn.frames) == 1
    assert turn.closure.ephemeral_session_id.startswith(
        "deterministic:recursive-read-fallback:"
    )


def test_prompt_routes_known_activity_difficulty_questions_to_activity_description() -> (
    None
):
    request = ModelRequest(
        request_id="request:activity-information-routing",
        lead_id="manychat:activity-information-routing",
        source_event_id="batch:activity-information-routing",
        message=(
            "A Cachoeira do Sossego é adequada para quem não é atleta? "
            "O que devemos levar? Não consulte disponibilidade."
        ),
        locale="pt-BR",
        state_version=0,
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    prompt = envelope["system_prompt"]

    assert "KNOWN ACTIVITY INFORMATION ROUTING" in prompt
    assert "difficulty, duration, preparation, or what to bring" in prompt
    assert "activity_description in the initial frame" in prompt


def test_incomplete_informational_observation_fails_honestly_without_repeat_request() -> (
    None
):
    observed_at = datetime(2026, 8, 6, 19, 25, tzinfo=timezone.utc)
    request = ModelRequest(
        request_id="request:sossego-information-followup",
        lead_id="manychat:sossego-information-followup",
        source_event_id="batch:sossego-information-followup",
        message=(
            "A Cachoeira do Sossego é adequada para quem não é atleta? "
            "O que devemos levar?"
        ),
        locale="pt-BR",
        state_version=0,
        observations=(
            ReadObservation(
                request_hash="a" * 64,
                provider="cerebro",
                observed_at=observed_at,
                expires_at=observed_at + timedelta(minutes=5),
                public_payload={
                    "answer": "Conteúdo geral sem a descrição específica do passeio.",
                    "sources": ["general"],
                },
                private_binding_hash="b" * 64,
            ),
        ),
    )
    recursive = {
        "schema": "v2-model-proposal-v6",
        "source_event_id": request.source_event_id,
        "intent": "inform",
        "reply_chunks": ["Vou consultar a descrição específica do passeio."],
        "facts": [{"name": "product_id", "value": "product:sossego"}],
        "read_requests": [
            {
                "request_id": "read:sossego-description",
                "kind": "activity_description",
                "product_id": "product:sossego",
            }
        ],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": None,
        "confirmed_action_kinds": [],
        "approval_basis": None,
        "selection_requested": False,
        "pending_disposition": None,
        "passengers": [],
    }
    payload = json.dumps(recursive, ensure_ascii=False).encode()

    def run(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + payload,
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"recursive-information-fallback-key-001",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(request)

    assert turn.proposal.reply_chunks == (
        "Não encontrei detalhes verificados suficientes para responder com segurança. "
        "Posso verificar a descrição oficial do passeio em uma nova consulta.",
    )
    assert "repita" not in " ".join(turn.proposal.reply_chunks).casefold()
    assert turn.proposal.read_requests == ()
    assert turn.proposal.effect_proposals == ()
