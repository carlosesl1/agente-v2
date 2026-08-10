from __future__ import annotations

import json
from types import SimpleNamespace
import unicodedata
from datetime import date, datetime, timedelta, timezone

import pytest

import v2_adapters.hermes_model as hermes_model_module
import v2_contracts.model as model_contracts
from v2_adapters.hermes_model import (
    _CONFIRMATION_REVIEW_REPAIR_SUFFIX,
    _CONFIRMATION_REVIEW_SYSTEM_PROMPT,
    _PROTOCOL_REPAIR_SUFFIX,
    _confirmation_review_wire,
    _proposal,
    _request_wire,
    HermesModelAdapter,
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


def test_v7_parser_preserves_exact_reply_chunks_for_matching_clarification() -> None:
    payload = {
        "schema": "v2-model-proposal-v7",
        "source_event_id": "batch:typed-question-preserve",
        "intent": "inform",
        "reply_chunks": [
            "Dados recebidos sem repeti-los.",
            "Haverá alguma criança no grupo?",
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
        "clarification_question": "Haverá alguma criança no grupo?",
    }

    turn = _proposal(
        json.dumps(payload, ensure_ascii=False).encode(),
        "batch:typed-question-preserve",
    )

    assert turn.reply_chunks == (
        "Dados recebidos sem repeti-los.",
        "Haverá alguma criança no grupo?",
    )
    assert turn.clarification_question == "Haverá alguma criança no grupo?"


def test_v7_parser_normalizes_matching_clarification_without_collapsing_chunks() -> (
    None
):
    raw_question = "Qual e\u0301 a pro\u0301xima data disponi\u0301vel?"
    normalized_question = unicodedata.normalize("NFKC", raw_question)
    assert raw_question != normalized_question
    payload = {
        "schema": "v2-model-proposal-v7",
        "source_event_id": "batch:typed-question-normalized",
        "intent": "inform",
        "reply_chunks": [
            "Contexto anterior preservado.",
            f"  {raw_question}\n",
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
        "clarification_question": f"\t{raw_question}  ",
    }

    turn = _proposal(
        json.dumps(payload, ensure_ascii=False).encode(),
        "batch:typed-question-normalized",
    )

    assert turn.reply_chunks == (
        "Contexto anterior preservado.",
        normalized_question,
    )
    assert turn.clarification_question == normalized_question


def test_clarification_mismatch_invokes_protocol_repair_and_parser_rejects() -> None:
    request = ModelRequest(
        request_id="request:typed-question-mismatch",
        lead_id="manychat:typed-question-mismatch",
        source_event_id="batch:typed-question-mismatch",
        message="Somos dois adultos.",
        locale="pt-BR",
        state_version=0,
    )

    def payload(*, clarification_question: str) -> bytes:
        return json.dumps(
            {
                "schema": "v2-model-proposal-v7",
                "source_event_id": request.source_event_id,
                "intent": "inform",
                "reply_chunks": [
                    "Dados recebidos sem repeti-los.",
                    "Haverá alguma criança no grupo?",
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
                "clarification_question": clarification_question,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    mismatched = payload(clarification_question="Qual é a idade da criança?")
    repaired = payload(clarification_question="Haverá alguma criança no grupo?")
    prompts: list[str] = []
    responses = [mismatched, repaired]

    def run(command, **kwargs):
        envelope = json.loads(kwargs["input"])
        prompts.append(envelope["system_prompt"])
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"typed-question-mismatch-key-0001",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(request)

    assert len(prompts) == 2
    assert _PROTOCOL_REPAIR_SUFFIX not in prompts[0]
    assert _PROTOCOL_REPAIR_SUFFIX in prompts[1]
    assert turn.proposal.reply_chunks == (
        "Dados recebidos sem repeti-los.",
        "Haverá alguma criança no grupo?",
    )
    assert turn.proposal.clarification_question == "Haverá alguma criança no grupo?"
    with pytest.raises(
        InvalidModelProposal,
        match="clarification_question must be an exact reply chunk",
    ):
        _proposal(mismatched, request.source_event_id)


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


def test_public_reply_correction_wire_is_closed_public_and_terminal() -> None:
    reason_type = model_contracts.PublicReplyCorrectionReason
    observed_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    reasons = tuple(
        sorted(
            (
                reason_type.PRIVATE_VALUE_EXPOSURE,
                reason_type.UNSUPPORTED_OBSERVATION_CLAIM,
            ),
            key=lambda item: item.value,
        )
    )
    request = ModelRequest(
        request_id="request:public-reply-correction-wire",
        lead_id="manychat:public-reply-correction-wire",
        source_event_id="batch:public-reply-correction-wire",
        message="Continue o atendimento.",
        locale="pt-BR",
        state_version=4,
        observations=(
            ReadObservation(
                request_hash="a" * 64,
                provider="cloudbeds",
                observed_at=observed_at,
                expires_at=observed_at + timedelta(minutes=5),
                public_payload={"available": False},
                private_binding_hash="b" * 64,
            ),
        ),
        state_facts=(ModelFact("service", "hostel"),),
        private_customer_fact_names=("full_name", "email"),
        public_reply_correction_reasons=reasons,
    )

    envelope = json.loads(_request_wire(request, "Closed prompt."))
    current = json.loads(envelope["messages"][-1][1])
    expected_suffix = """PUBLIC REPLY CORRECTION
The previous candidate could not be published for the listed closed reasons.
You, Maya, must write the corrected customer-facing reply.
Do not repeat private values. Do not request another read after observations.
Do not strengthen operational status beyond exact receipts.
Return one valid v2-model-proposal-v7 frame. The parent will not rewrite it."""

    assert set(envelope) == {"system_prompt", "messages"}
    assert current["public_reply_correction_reasons"] == [
        item.value for item in reasons
    ]
    assert current["state_facts"] == [{"name": "service", "value": "hostel"}]
    assert current["observations"] == [
        {
            "request_hash": "a" * 64,
            "provider": "cloudbeds",
            "observed_at": observed_at.isoformat(),
            "expires_at": (observed_at + timedelta(minutes=5)).isoformat(),
            "public_payload": {"available": False},
        }
    ]
    assert hermes_model_module._PUBLIC_REPLY_CORRECTION_SUFFIX == expected_suffix
    assert envelope["system_prompt"].endswith(expected_suffix)
    assert envelope["system_prompt"].count("PUBLIC REPLY CORRECTION") == 1
    serialized = envelope["messages"][-1][1]
    assert "private_binding_hash" not in serialized
    assert "b" * 64 not in serialized

    ordinary_request = ModelRequest(
        request_id="request:without-public-reply-correction",
        lead_id="manychat:without-public-reply-correction",
        source_event_id="batch:without-public-reply-correction",
        message="Continue o atendimento.",
        locale="pt-BR",
        state_version=0,
    )
    ordinary = json.loads(_request_wire(ordinary_request, "Closed."))
    ordinary_current = json.loads(ordinary["messages"][-1][1])
    assert ordinary_current["public_reply_correction_reasons"] == []
    assert "PUBLIC REPLY CORRECTION" not in ordinary["system_prompt"]


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


def _confirmation_proposal_payload(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "schema": "v2-model-proposal-v7",
        "source_event_id": "batch:contextual-confirmation-review",
        "intent": "confirm",
        "reply_chunks": [
            "Confirmação entendida exatamente como você descreveu.",
            "Vou seguir somente com o resumo pendente.",
        ],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": 1,
        "confirmed_action_kinds": ["book_activity", "initiate_payment"],
        "approval_basis": "contextual_reference",
        "selection_requested": False,
        "pending_disposition": None,
        "passengers": [],
        "clarification_question": None,
    }
    payload.update(overrides)
    if payload["schema"] == "v2-model-proposal-v6":
        payload.pop("clarification_question")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _decision_only_review_payload(decision: str = "approve") -> bytes:
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


def test_confirmation_review_wire_is_minimal_public_only_and_requests_full_v7() -> None:
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
    assert "v2-model-proposal-v7" in envelope["system_prompt"]
    assert "reply_chunks" in envelope["system_prompt"]
    assert "v2-contextual-confirmation-review-v1" not in envelope["system_prompt"]
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


@pytest.mark.parametrize(
    ("overrides", "reply_chunks", "expected_intent", "expected_disposition"),
    (
        (
            {},
            (
                "Confirmação entendida exatamente como você descreveu.",
                "Vou seguir somente com o resumo pendente.",
            ),
            "confirm",
            None,
        ),
        (
            {
                "intent": "adjust",
                "reply_chunks": [
                    "Entendi a mudança e não vou usar o resumo anterior.",
                ],
                "confirmed_summary_version": None,
                "confirmed_action_kinds": [],
                "approval_basis": None,
                "pending_disposition": "revoke",
            },
            ("Entendi a mudança e não vou usar o resumo anterior.",),
            "adjust",
            "revoke",
        ),
        (
            {
                "intent": "inform",
                "reply_chunks": [
                    "Ainda preciso que você esclareça se aprova todo o resumo.",
                ],
                "confirmed_summary_version": None,
                "confirmed_action_kinds": [],
                "approval_basis": None,
            },
            ("Ainda preciso que você esclareça se aprova todo o resumo.",),
            "inform",
            None,
        ),
    ),
)
def test_confirmation_review_returns_exact_maya_authored_bound_v7_proposal(
    overrides: dict[str, object],
    reply_chunks: tuple[str, ...],
    expected_intent: str,
    expected_disposition: str | None,
) -> None:
    response = _confirmation_proposal_payload(**overrides)
    responses = [response]
    captured: list[bytes] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("confirmation review attempted an unexpected additional child call")
        assert command == ("python", "-m", "v2_host.hermes_child")
        captured.append(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
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

    assert responses == []
    assert turn.proposal.intent == expected_intent
    assert turn.proposal.reply_chunks == reply_chunks
    assert turn.proposal.pending_disposition == expected_disposition
    assert turn.proposal.facts == ()
    assert turn.proposal.read_requests == ()
    assert turn.proposal.effect_proposals == ()
    assert turn.proposal.target_offer_id is None
    assert turn.proposal.target_offer_ids == ()
    assert turn.proposal.selection_requested is False
    assert turn.proposal.passengers == ()
    if expected_intent == "confirm":
        assert turn.proposal.confirmed_summary_version == (
            request.pending_action.summary_version
        )
        assert turn.proposal.confirmed_action_kinds == request.pending_action.action_kinds
        assert turn.proposal.approval_basis is ApprovalBasis.CONTEXTUAL_REFERENCE
    else:
        assert turn.proposal.confirmed_summary_version is None
        assert turn.proposal.confirmed_action_kinds == ()
        assert turn.proposal.approval_basis is None
    assert len(turn.frames) == 1
    assert turn.frames[0].response_bytes == response
    assert turn.frames[0].stdin_bytes == captured[0]
    assert turn.closure.ephemeral_session_id.startswith("uds:")
    envelope = json.loads(captured[0])
    assert envelope["system_prompt"] == _CONFIRMATION_REVIEW_SYSTEM_PROMPT
    serialized = captured[0].decode()
    assert "general proposal prompt must not be used for review" not in serialized
    assert "private-lead-should-not-cross-review-wire" not in serialized
    assert "must-not-cross" not in serialized


@pytest.mark.parametrize(
    "invalid_overrides",
    (
        {"source_event_id": "batch:wrong-source"},
        {"schema": "v2-model-proposal-v6"},
        {
            "intent": "request_handoff",
            "confirmed_summary_version": None,
            "confirmed_action_kinds": [],
            "approval_basis": None,
        },
        {"confirmed_summary_version": 2},
        {"confirmed_action_kinds": ["book_activity", "cancel_reservation"]},
        {"approval_basis": None},
        {
            "intent": "adjust",
            "confirmed_summary_version": None,
            "confirmed_action_kinds": [],
            "approval_basis": None,
            "pending_disposition": "preserve",
        },
    ),
    ids=(
        "source",
        "legacy-schema",
        "intent",
        "summary-version",
        "action-kinds",
        "approval-basis",
        "pending-disposition",
    ),
)
def test_confirmation_review_repairs_structurally_unbound_proposal_once(
    invalid_overrides: dict[str, object],
) -> None:
    invalid = _confirmation_proposal_payload(**invalid_overrides)
    repaired_chunks = (
        "Aprovação compreendida pela Maya para o resumo exato.",
        "Vou seguir apenas com essas ações.",
    )
    repaired = _confirmation_proposal_payload(reply_chunks=list(repaired_chunks))
    responses = [invalid, repaired]
    prompts: list[str] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("confirmation review attempted an unexpected third child call")
        envelope = json.loads(kwargs["input"])
        prompts.append(envelope["system_prompt"])
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    request = _confirmation_review_request()
    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="unused-general-prompt",
        timeout=10,
        transcript_key=b"review-binding-repair-key-00000001",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(request)

    assert responses == []
    assert len(prompts) == 2
    assert _CONFIRMATION_REVIEW_REPAIR_SUFFIX not in prompts[0]
    assert _CONFIRMATION_REVIEW_REPAIR_SUFFIX in prompts[1]
    assert len(turn.frames) == 2
    assert [frame.response_bytes for frame in turn.frames] == [invalid, repaired]
    assert turn.closure.ephemeral_session_id.startswith("uds:")
    assert turn.proposal.reply_chunks == repaired_chunks
    assert turn.proposal.confirmed_summary_version == request.pending_action.summary_version
    assert turn.proposal.confirmed_action_kinds == request.pending_action.action_kinds
    assert turn.proposal.approval_basis is ApprovalBasis.CONTEXTUAL_REFERENCE
    assert turn.proposal.pending_disposition is None


def test_decision_only_confirmation_review_cannot_bypass_full_v7_validation() -> None:
    legacy = _decision_only_review_payload()
    repaired_chunks = ("A Maya confirmou o resumo integral sem alterar seus termos.",)
    repaired = _confirmation_proposal_payload(reply_chunks=list(repaired_chunks))
    responses = [legacy, repaired]

    def run(command, **kwargs):
        if not responses:
            pytest.fail("confirmation review attempted an unexpected third child call")
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="unused-general-prompt",
        timeout=10,
        transcript_key=b"review-legacy-bypass-key-000000001",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(_confirmation_review_request())

    assert responses == []
    assert [frame.response_bytes for frame in turn.frames] == [legacy, repaired]
    assert turn.proposal.reply_chunks == repaired_chunks
    assert turn.closure.ephemeral_session_id.startswith("uds:")


def test_invalid_confirmation_reviews_fail_closed_after_bounded_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_frames = [
        _confirmation_proposal_payload(confirmed_summary_version=2),
        _confirmation_proposal_payload(
            confirmed_action_kinds=["book_activity", "cancel_reservation"]
        ),
    ]
    responses = list(invalid_frames)
    prompts: list[str] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("confirmation review attempted an unexpected third child call")
        envelope = json.loads(kwargs["input"])
        prompts.append(envelope["system_prompt"])
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    def forbid_deterministic_proposal(*args, **kwargs):
        pytest.fail("confirmation exhaustion attempted deterministic proposal construction")

    monkeypatch.setattr(
        HermesModelAdapter,
        "_fallback_proposal",
        staticmethod(forbid_deterministic_proposal),
        raising=False,
    )
    monkeypatch.setattr(
        HermesModelAdapter,
        "_recursive_read_fallback",
        staticmethod(forbid_deterministic_proposal),
        raising=False,
    )
    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="unused-general-prompt",
        timeout=10,
        transcript_key=b"invalid-review-transcript-key-0001",
        run=run,
        environ={},
    )
    attempted_frames = []
    original_attempt = adapter._attempt

    def audited_attempt(request, *, stdin_bytes, decode=None):
        turn, frame = original_attempt(
            request,
            stdin_bytes=stdin_bytes,
            decode=decode,
        )
        attempted_frames.append(frame)
        return turn, frame

    monkeypatch.setattr(adapter, "_attempt", audited_attempt)

    with pytest.raises(InvalidModelProposal) as captured:
        adapter.complete_audited(_confirmation_review_request())

    assert type(captured.value) is InvalidModelProposal
    assert str(captured.value) == "model proposal remained invalid after bounded attempts"
    assert responses == []
    assert len(prompts) == 2
    assert _CONFIRMATION_REVIEW_REPAIR_SUFFIX not in prompts[0]
    assert _CONFIRMATION_REVIEW_REPAIR_SUFFIX in prompts[1]
    assert [frame.response_bytes for frame in attempted_frames] == invalid_frames
    assert all(
        json.loads(frame.response_bytes)["effect_proposals"] == []
        for frame in attempted_frames
    )


def test_ordinary_invalid_proposals_fail_closed_after_one_protocol_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = ModelRequest(
        request_id="request:ordinary-bounded-exhaustion",
        lead_id="manychat:ordinary-bounded-exhaustion",
        source_event_id="batch:ordinary-bounded-exhaustion",
        message="Continue o atendimento.",
        locale="pt-BR",
        state_version=0,
    )
    invalid_frames = [
        _versioned_proposal_payload(
            7,
            "batch:wrong-source-first",
            reply_chunks=("Primeiro frame inválido escrito pelo modelo.",),
            facts=({"name": "service", "value": "activity"},),
        ),
        _versioned_proposal_payload(
            7,
            "batch:wrong-source-repair",
            reply_chunks=("Segundo frame inválido escrito pelo modelo.",),
            facts=({"name": "service", "value": "activity"},),
        ),
    ]
    responses = list(invalid_frames)
    seen: list[tuple[str, dict[str, object]]] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("ordinary exhaustion attempted an unexpected third child call")
        envelope = json.loads(kwargs["input"])
        current = json.loads(envelope["messages"][-1][1])
        seen.append((envelope["system_prompt"], current))
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    def forbid_deterministic_proposal(*args, **kwargs):
        pytest.fail("ordinary exhaustion attempted deterministic proposal construction")

    monkeypatch.setattr(
        HermesModelAdapter,
        "_fallback_proposal",
        staticmethod(forbid_deterministic_proposal),
        raising=False,
    )
    monkeypatch.setattr(
        HermesModelAdapter,
        "_recursive_read_fallback",
        staticmethod(forbid_deterministic_proposal),
        raising=False,
    )
    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"ordinary-exhaustion-transcript-key-01",
        run=run,
        environ={},
    )
    attempted_frames = []
    original_attempt = adapter._attempt

    def audited_attempt(request, *, stdin_bytes, decode=None):
        turn, frame = original_attempt(
            request,
            stdin_bytes=stdin_bytes,
            decode=decode,
        )
        attempted_frames.append(frame)
        return turn, frame

    monkeypatch.setattr(adapter, "_attempt", audited_attempt)

    with pytest.raises(InvalidModelProposal) as captured:
        adapter.complete_audited(request)

    assert type(captured.value) is InvalidModelProposal
    assert str(captured.value) == "model proposal remained invalid after bounded attempts"
    assert responses == []
    assert len(seen) == 2
    assert [_PROTOCOL_REPAIR_SUFFIX in prompt for prompt, _ in seen] == [False, True]
    assert [frame.response_bytes for frame in attempted_frames] == invalid_frames
    assert all(
        json.loads(frame.response_bytes)["effect_proposals"] == []
        for frame in attempted_frames
    )
    for _, current in seen:
        assert current["progress_review_required"] is False
        assert current["confirmation_review_required"] is False
        assert current["selection_review_required"] is False
        assert current["recap_reuse_required"] is False


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


def test_progress_review_has_one_attempt_after_initial_protocol_repair() -> None:
    request = ModelRequest(
        request_id="request:progress-review-budget",
        lead_id="manychat:progress-review-budget",
        source_event_id="batch:progress-review-budget",
        message="I need a private room for two nights.",
        locale="en",
        state_version=0,
    )
    no_op = json.dumps(
        {
            "schema": "v2-model-proposal-v7",
            "source_event_id": request.source_event_id,
            "intent": "inform",
            "reply_chunks": ["I am ready to help."],
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
            "clarification_question": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    responses = [b"{}", no_op, b"{}"]
    review_flags: list[bool] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("progress review attempted an unexpected nested repair call")
        envelope = json.loads(kwargs["input"])
        user = json.loads(envelope["messages"][-1][1])
        review_flags.append(user["progress_review_required"])
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"x" * 32,
        run=run,
        environ={},
    )

    with pytest.raises(InvalidModelProposal) as captured:
        adapter.complete_audited(request)

    assert type(captured.value) is InvalidModelProposal
    assert str(captured.value) == "model proposal remained invalid after bounded attempts"
    assert responses == []
    assert review_flags == [False, False, True]


def _versioned_proposal_payload(
    schema_version: int,
    source_event_id: str,
    *,
    reply_chunks: tuple[str, ...],
    facts: tuple[dict[str, str], ...] = (),
) -> bytes:
    payload: dict[str, object] = {
        "schema": f"v2-model-proposal-v{schema_version}",
        "source_event_id": source_event_id,
        "intent": "inform",
        "reply_chunks": list(reply_chunks),
        "facts": list(facts),
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "confirmed_summary_version": None,
    }
    if schema_version >= 2:
        payload["target_offer_ids"] = []
    if schema_version >= 3:
        payload["confirmed_action_kinds"] = []
        payload["approval_basis"] = None
    if schema_version >= 4:
        payload["selection_requested"] = False
    if schema_version >= 5:
        payload["pending_disposition"] = None
    if schema_version >= 6:
        payload["passengers"] = []
    if schema_version >= 7:
        payload["clarification_question"] = None
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


@pytest.mark.parametrize("legacy_schema_version", range(1, 7))
def test_public_reply_correction_rejects_each_legacy_schema_and_repairs_with_v7(
    legacy_schema_version: int,
) -> None:
    reason_type = model_contracts.PublicReplyCorrectionReason
    request = ModelRequest(
        request_id=f"request:correction-legacy-v{legacy_schema_version}",
        lead_id=f"manychat:correction-legacy-v{legacy_schema_version}",
        source_event_id=f"batch:correction-legacy-v{legacy_schema_version}",
        message="Corrija a resposta mantendo a autoria da Maya.",
        locale="pt-BR",
        state_version=6,
        public_reply_correction_reasons=(reason_type.PRIVATE_VALUE_EXPOSURE,),
    )
    legacy = _versioned_proposal_payload(
        legacy_schema_version,
        request.source_event_id,
        reply_chunks=(f"Resposta legada v{legacy_schema_version} não publicável.",),
    )
    repaired_chunks = (
        "Resposta corrigida pela Maya sem repetir dados privados.",
        "Posso continuar ajudando por aqui.",
    )
    repaired = _versioned_proposal_payload(
        7,
        request.source_event_id,
        reply_chunks=repaired_chunks,
    )
    responses = [legacy, repaired]
    seen: list[tuple[str, dict[str, object]]] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("public reply correction attempted an unexpected third child call")
        envelope = json.loads(kwargs["input"])
        current = json.loads(envelope["messages"][-1][1])
        seen.append((envelope["system_prompt"], current))
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"legacy-correction-transcript-key-01",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(request)

    assert len(seen) == 2
    assert responses == []
    assert len(turn.frames) == 2
    assert [frame.response_bytes for frame in turn.frames] == [legacy, repaired]
    assert [_PROTOCOL_REPAIR_SUFFIX in prompt for prompt, _ in seen] == [False, True]
    assert turn.closure.ephemeral_session_id.startswith("uds:")
    assert not turn.closure.ephemeral_session_id.startswith("deterministic:")
    assert turn.proposal.source_event_id == request.source_event_id
    assert turn.proposal.reply_chunks == repaired_chunks
    assert turn.proposal.effect_proposals == ()
    assert seen[0][1] == seen[1][1]
    assert [current["request_id"] for _, current in seen] == [request.request_id] * 2
    assert [current["public_reply_correction_reasons"] for _, current in seen] == [
        ["private_value_exposure"],
        ["private_value_exposure"],
    ]
    for _, current in seen:
        assert current["progress_review_required"] is False
        assert current["confirmation_review_required"] is False
        assert current["selection_review_required"] is False
        assert current["recap_reuse_required"] is False


@pytest.mark.parametrize("legacy_schema_version", range(1, 7))
def test_ordinary_request_accepts_each_legacy_proposal_schema(
    legacy_schema_version: int,
) -> None:
    request = ModelRequest(
        request_id=f"request:ordinary-legacy-v{legacy_schema_version}",
        lead_id=f"manychat:ordinary-legacy-v{legacy_schema_version}",
        source_event_id=f"batch:ordinary-legacy-v{legacy_schema_version}",
        message="Quero informações sobre o passeio.",
        locale="pt-BR",
        state_version=6,
    )
    reply_chunks = (f"Resposta comum no schema v{legacy_schema_version}.",)
    response = _versioned_proposal_payload(
        legacy_schema_version,
        request.source_event_id,
        reply_chunks=reply_chunks,
        facts=({"name": "service", "value": "activity"},),
    )
    responses = [response]
    seen: list[dict[str, object]] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("ordinary legacy proposal attempted an unexpected child call")
        envelope = json.loads(kwargs["input"])
        seen.append(json.loads(envelope["messages"][-1][1]))
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"ordinary-legacy-transcript-key-0001",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(request)

    assert len(seen) == 1
    assert responses == []
    assert seen[0]["public_reply_correction_reasons"] == []
    assert len(turn.frames) == 1
    assert turn.frames[0].response_bytes == response
    assert turn.closure.ephemeral_session_id.startswith("uds:")
    assert turn.proposal.reply_chunks == reply_chunks
    assert turn.proposal.facts == (ModelFact("service", "activity"),)


def test_public_reply_correction_rejects_two_legacy_frames_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason_type = model_contracts.PublicReplyCorrectionReason
    request = ModelRequest(
        request_id="request:correction-two-legacy-frames",
        lead_id="manychat:correction-two-legacy-frames",
        source_event_id="batch:correction-two-legacy-frames",
        message="Corrija a resposta sem substituir a voz da Maya.",
        locale="pt-BR",
        state_version=6,
        public_reply_correction_reasons=(reason_type.PRIVATE_VALUE_EXPOSURE,),
    )
    legacy_frames = [
        _versioned_proposal_payload(
            1,
            request.source_event_id,
            reply_chunks=("Primeira resposta legada não publicável.",),
        ),
        _versioned_proposal_payload(
            6,
            request.source_event_id,
            reply_chunks=("Segunda resposta legada não publicável.",),
        ),
    ]
    responses = list(legacy_frames)
    returned_frames: list[bytes] = []
    seen: list[tuple[str, dict[str, object]]] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("public reply correction attempted an unexpected third child call")
        envelope = json.loads(kwargs["input"])
        current = json.loads(envelope["messages"][-1][1])
        seen.append((envelope["system_prompt"], current))
        response = responses.pop(0)
        returned_frames.append(response)
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + response,
            stderr=b"",
        )

    def forbid_deterministic_proposal(*args, **kwargs):
        pytest.fail("legacy correction exhaustion attempted a deterministic proposal")

    monkeypatch.setattr(
        HermesModelAdapter,
        "_fallback_proposal",
        staticmethod(forbid_deterministic_proposal),
        raising=False,
    )
    monkeypatch.setattr(
        HermesModelAdapter,
        "_recursive_read_fallback",
        staticmethod(forbid_deterministic_proposal),
        raising=False,
    )
    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"two-legacy-correction-key-00000001",
        run=run,
        environ={},
    )

    with pytest.raises(InvalidModelProposal) as captured:
        adapter.complete_audited(request)

    assert type(captured.value) is InvalidModelProposal
    assert str(captured.value) == "model proposal remained invalid after bounded attempts"
    assert len(seen) == 2
    assert responses == []
    assert returned_frames == legacy_frames
    assert all(
        not frame.startswith(b"V2_DETERMINISTIC_FALLBACK")
        for frame in returned_frames
    )
    assert all(json.loads(frame)["effect_proposals"] == [] for frame in returned_frames)
    assert [_PROTOCOL_REPAIR_SUFFIX in prompt for prompt, _ in seen] == [False, True]
    assert seen[0][1] == seen[1][1]
    assert [current["request_id"] for _, current in seen] == [request.request_id] * 2
    assert [current["public_reply_correction_reasons"] for _, current in seen] == [
        ["private_value_exposure"],
        ["private_value_exposure"],
    ]
    for _, current in seen:
        assert current["progress_review_required"] is False
        assert current["confirmation_review_required"] is False
        assert current["selection_review_required"] is False
        assert current["recap_reuse_required"] is False


def test_public_reply_correction_has_one_protocol_repair_and_no_nested_review() -> None:
    reason_type = model_contracts.PublicReplyCorrectionReason
    request = ModelRequest(
        request_id="request:bounded-public-reply-correction",
        lead_id="manychat:bounded-public-reply-correction",
        source_event_id="batch:bounded-public-reply-correction",
        message="Continue o atendimento sem expor dados privados.",
        locale="pt-BR",
        state_version=5,
        public_reply_correction_reasons=(reason_type.PRIVATE_VALUE_EXPOSURE,),
    )
    repaired = json.dumps(
        {
            "schema": "v2-model-proposal-v7",
            "source_event_id": request.source_event_id,
            "intent": "inform",
            "reply_chunks": ["Posso continuar o atendimento sem repetir esses dados."],
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
            "clarification_question": None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    responses = [b"{}", repaired]
    seen: list[tuple[str, dict[str, object]]] = []

    def run(command, **kwargs):
        envelope = json.loads(kwargs["input"])
        current = json.loads(envelope["messages"][-1][1])
        seen.append((envelope["system_prompt"], current))
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"bounded-correction-transcript-key-01",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(request)

    correction_suffix = hermes_model_module._PUBLIC_REPLY_CORRECTION_SUFFIX
    assert len(seen) == 2
    assert len(turn.frames) == 2
    assert turn.proposal.reply_chunks == (
        "Posso continuar o atendimento sem repetir esses dados.",
    )
    assert _PROTOCOL_REPAIR_SUFFIX not in seen[0][0]
    assert _PROTOCOL_REPAIR_SUFFIX in seen[1][0]
    assert all(prompt.endswith(correction_suffix) for prompt, _ in seen)
    assert [current["request_id"] for _, current in seen] == [request.request_id] * 2
    assert [current["public_reply_correction_reasons"] for _, current in seen] == [
        ["private_value_exposure"],
        ["private_value_exposure"],
    ]
    for _, current in seen:
        assert current["progress_review_required"] is False
        assert current["confirmation_review_required"] is False
        assert current["selection_review_required"] is False
        assert current["recap_reuse_required"] is False


def test_public_reply_correction_repairs_recursive_read_after_observation() -> None:
    reason_type = model_contracts.PublicReplyCorrectionReason
    repeated_read = ReadRequest(
        request_id="read:child-correction-repeat",
        kind=ReadKind.ACTIVITY_DESCRIPTION,
        product_id="product:sossego",
    )
    observed_at = datetime.fromisoformat("2026-08-10T15:00:00+00:00")
    request = ModelRequest(
        request_id="request:child-correction-recursive-read",
        lead_id="manychat:child-correction-recursive-read",
        source_event_id="batch:child-correction-recursive-read",
        message="Corrija a resposta usando somente a observação existente.",
        locale="pt-BR",
        state_version=6,
        observations=(
            ReadObservation(
                request_hash=repeated_read.canonical_hash(),
                provider="cerebro",
                observed_at=observed_at,
                expires_at=observed_at + timedelta(minutes=5),
                public_payload={
                    "answer": "A trilha exige preparo e acompanhamento de guia.",
                    "sources": ["activity_description"],
                },
                private_binding_hash="a" * 64,
            ),
        ),
        public_reply_correction_reasons=(
            reason_type.RECURSIVE_READ_AFTER_OBSERVATION,
        ),
    )

    def payload(
        *,
        reply_chunks: tuple[str, ...],
        read_requests: list[dict[str, object]],
    ) -> bytes:
        return json.dumps(
            {
                "schema": "v2-model-proposal-v7",
                "source_event_id": request.source_event_id,
                "intent": "inform",
                "reply_chunks": list(reply_chunks),
                "facts": [],
                "read_requests": read_requests,
                "effect_proposals": [],
                "target_offer_id": None,
                "target_offer_ids": [],
                "confirmed_summary_version": None,
                "confirmed_action_kinds": [],
                "approval_basis": None,
                "selection_requested": False,
                "pending_disposition": None,
                "passengers": [],
                "clarification_question": None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    recursive = payload(
        reply_chunks=("Vou consultar novamente a descrição do passeio.",),
        read_requests=[
            {
                "request_id": repeated_read.request_id,
                "kind": repeated_read.kind.value,
                "product_id": repeated_read.product_id,
            }
        ],
    )
    repaired_chunks = (
        "A trilha exige preparo e acompanhamento de guia.",
        "Posso ajudar com outra dúvida sobre o passeio.",
    )
    repaired = payload(reply_chunks=repaired_chunks, read_requests=[])
    responses = [recursive, repaired]
    seen: list[tuple[str, dict[str, object]]] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("public reply correction attempted an unexpected third child call")
        envelope = json.loads(kwargs["input"])
        current = json.loads(envelope["messages"][-1][1])
        seen.append((envelope["system_prompt"], current))
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"recursive-correction-transcript-key-01",
        run=run,
        environ={},
    )

    turn = adapter.complete_audited(request)

    assert len(seen) == 2, (
        f"expected one repair call, got {len(seen)} child call(s); "
        f"session={turn.closure.ephemeral_session_id}; "
        f"chunks={turn.proposal.reply_chunks!r}"
    )
    assert responses == []
    assert len(turn.frames) == 2
    assert [frame.response_bytes for frame in turn.frames] == [recursive, repaired]
    assert [_PROTOCOL_REPAIR_SUFFIX in prompt for prompt, _ in seen] == [False, True]
    assert turn.closure.ephemeral_session_id.startswith("uds:")
    assert not turn.closure.ephemeral_session_id.startswith("deterministic:")
    assert turn.proposal.reply_chunks == repaired_chunks
    assert turn.proposal.read_requests == ()
    assert [current["request_id"] for _, current in seen] == [request.request_id] * 2
    assert [current["public_reply_correction_reasons"] for _, current in seen] == [
        ["recursive_read_after_observation"],
        ["recursive_read_after_observation"],
    ]
    for _, current in seen:
        assert current["progress_review_required"] is False
        assert current["confirmation_review_required"] is False
        assert current["selection_review_required"] is False
        assert current["recap_reuse_required"] is False


def test_public_reply_correction_fails_closed_after_two_invalid_frames() -> None:
    reason_type = model_contracts.PublicReplyCorrectionReason
    request = ModelRequest(
        request_id="request:child-correction-invalid-frames",
        lead_id="manychat:child-correction-invalid-frames",
        source_event_id="batch:child-correction-invalid-frames",
        message="Corrija a resposta sem substituir a voz da Maya.",
        locale="pt-BR",
        state_version=6,
        public_reply_correction_reasons=(reason_type.PRIVATE_VALUE_EXPOSURE,),
    )
    responses = [b"{}", b"{}"]
    seen: list[tuple[str, dict[str, object]]] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("public reply correction attempted an unexpected third child call")
        envelope = json.loads(kwargs["input"])
        current = json.loads(envelope["messages"][-1][1])
        seen.append((envelope["system_prompt"], current))
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"invalid-correction-transcript-key-001",
        run=run,
        environ={},
    )

    published = None
    failure: InvalidModelProposal | None = None
    try:
        published = adapter.complete_audited(request)
    except InvalidModelProposal as exc:
        failure = exc

    assert len(seen) == 2
    assert responses == []
    assert [_PROTOCOL_REPAIR_SUFFIX in prompt for prompt, _ in seen] == [False, True]
    assert [current["request_id"] for _, current in seen] == [request.request_id] * 2
    for _, current in seen:
        assert current["progress_review_required"] is False
        assert current["confirmation_review_required"] is False
        assert current["selection_review_required"] is False
        assert current["recap_reuse_required"] is False
    assert published is None or (
        not published.closure.ephemeral_session_id.startswith("deterministic:")
        and all(
            not frame.stdout_bytes.startswith(b"V2_DETERMINISTIC_FALLBACK")
            for frame in published.frames
        )
    )
    assert published is None
    assert type(failure) is InvalidModelProposal
    assert str(failure) == "model proposal remained invalid after bounded attempts"


def test_current_observation_rejects_recursive_read_and_keeps_maya_repair() -> None:
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
    recursive = json.dumps(
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
    repaired_chunks = (
        "As duas opções consultadas estão indisponíveis nessas datas.",
        "Nada foi reservado; posso ajudar a avaliar outras datas.",
    )
    repaired = _versioned_proposal_payload(
        7,
        request.source_event_id,
        reply_chunks=repaired_chunks,
    )
    responses = [recursive, repaired]
    prompts: list[str] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("recursive-read repair attempted an unexpected third child call")
        envelope = json.loads(kwargs["input"])
        prompts.append(envelope["system_prompt"])
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
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

    assert responses == []
    assert len(prompts) == 2
    assert [_PROTOCOL_REPAIR_SUFFIX in prompt for prompt in prompts] == [False, True]
    assert turn.proposal.intent == "inform"
    assert turn.proposal.reply_chunks == repaired_chunks
    assert turn.proposal.read_requests == ()
    assert turn.proposal.selection_requested is False
    assert turn.proposal.effect_proposals == ()
    assert len(turn.frames) == 2
    assert [frame.response_bytes for frame in turn.frames] == [recursive, repaired]
    assert turn.closure.ephemeral_session_id.startswith("uds:")
    assert not turn.closure.ephemeral_session_id.startswith("deterministic:")


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


def test_recursive_read_repair_repetition_fails_closed_without_adapter_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    recursive: dict[str, object] = {
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
    first = json.dumps(
        recursive,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    recursive["schema"] = "v2-model-proposal-v7"
    recursive["reply_chunks"] = [
        "Ainda quero repetir a consulta em vez de usar a observação."
    ]
    recursive["clarification_question"] = None
    repeated_repair = json.dumps(
        recursive,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    invalid_frames = [first, repeated_repair]
    responses = list(invalid_frames)
    prompts: list[str] = []

    def run(command, **kwargs):
        if not responses:
            pytest.fail("recursive-read exhaustion attempted an unexpected third child call")
        envelope = json.loads(kwargs["input"])
        prompts.append(envelope["system_prompt"])
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + responses.pop(0),
            stderr=b"",
        )

    def forbid_deterministic_proposal(*args, **kwargs):
        pytest.fail("recursive read attempted deterministic proposal construction")

    monkeypatch.setattr(
        HermesModelAdapter,
        "_fallback_proposal",
        staticmethod(forbid_deterministic_proposal),
        raising=False,
    )
    monkeypatch.setattr(
        HermesModelAdapter,
        "_recursive_read_fallback",
        staticmethod(forbid_deterministic_proposal),
        raising=False,
    )
    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="closed prompt",
        timeout=10,
        transcript_key=b"recursive-information-fallback-key-001",
        run=run,
        environ={},
    )
    attempted_frames = []
    original_attempt = adapter._attempt

    def audited_attempt(request, *, stdin_bytes, decode=None):
        turn, frame = original_attempt(
            request,
            stdin_bytes=stdin_bytes,
            decode=decode,
        )
        attempted_frames.append(frame)
        return turn, frame

    monkeypatch.setattr(adapter, "_attempt", audited_attempt)

    with pytest.raises(InvalidModelProposal) as captured:
        adapter.complete_audited(request)

    assert type(captured.value) is InvalidModelProposal
    assert str(captured.value) == "model proposal remained invalid after bounded attempts"
    assert responses == []
    assert len(prompts) == 2
    assert [_PROTOCOL_REPAIR_SUFFIX in prompt for prompt in prompts] == [False, True]
    assert [frame.response_bytes for frame in attempted_frames] == invalid_frames
    assert all(
        json.loads(frame.response_bytes)["read_requests"]
        for frame in attempted_frames
    )
    assert all(
        json.loads(frame.response_bytes)["effect_proposals"] == []
        for frame in attempted_frames
    )
