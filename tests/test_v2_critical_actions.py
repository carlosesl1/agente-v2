from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from reservation_domain import (
    AwaitingConfirmationState,
    CustomerFacts,
    EconomicTerms,
    Money,
    OfferSnapshot,
    Party,
    ServiceKind,
    SummaryPresented,
    build_commercial_draft,
    new_workflow,
)
from v2_application.critical_actions import (
    ApprovalMatch,
    CriticalActionDenied,
    CriticalActionDisposition,
    CriticalActionPolicy,
    approval_assertion_matches,
    critical_action_context,
    critical_proposal_digest,
    critical_summary_outbox_id,
    pending_action_context,
)
from v2_contracts.critical_actions import (
    ApprovalBasis,
    CriticalActionKind,
    PendingCriticalActionContext,
)
from v2_contracts.model import InvalidModelProposal, ModelFact, ModelProposal, ModelRequest

NOW = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
TTL = timedelta(minutes=30)


def _enabled_policy(*, valid_until: datetime | None = None) -> CriticalActionPolicy:
    return CriticalActionPolicy(
        frozenset(
            {
                CriticalActionKind.RESERVE_LODGING,
                CriticalActionKind.BOOK_ACTIVITY,
                CriticalActionKind.BOOK_PACKAGE,
                CriticalActionKind.INITIATE_PAYMENT,
            }
        ),
        enabled_payment_methods=frozenset({"stripe", "wise", "pix"}),
        valid_until=valid_until,
    )


def _activity_draft(*, amount: str = "334.95", version: int = 1):
    offer = OfferSnapshot(
        offer_id="offer:" + "a" * 32,
        lookup_id="lookup:product:tour-4ps:" + "b" * 64,
        service=ServiceKind.ACTIVITY,
        provider_ref="c" * 64,
        public_label="Roteiro dos 4Ps",
        start_date=date(2026, 11, 18),
        end_date=None,
        start_time=None,
        party=Party(adults=1, children=0),
        total=Money(amount=Decimal(amount), currency="BRL"),
        available=True,
    )
    return build_commercial_draft(
        draft_id="draft:critical-activity",
        version=version,
        created_at=NOW,
        components=(offer,),
        customer=CustomerFacts(
            customer_ref="profile:critical-customer",
            full_name="Pessoa Fictícia",
            email="pessoa@example.invalid",
            phone_e164="+5575999990000",
            country_code="BR",
            birth_date=date(1990, 1, 2),
            gender="f",
        ),
        terms=EconomicTerms(payment_method="stripe", add_ons=()),
    )


def _context(*, draft=None, presented_at: datetime = NOW):
    return critical_action_context(
        draft or _activity_draft(),
        summary_version=1,
        presented_at=presented_at,
        locale="pt-BR",
        approval_ttl=TTL,
        agency_payment_percentage=20,
        hostel_payment_percentage=100,
        policy=_enabled_policy(),
    )


def _awaiting(*, presented_at: datetime = NOW) -> AwaitingConfirmationState:
    draft = _activity_draft()
    context = _context(draft=draft, presented_at=presented_at)
    digest = critical_proposal_digest(draft, context)
    summary_id = "summary:critical-activity"
    return AwaitingConfirmationState(
        meta=new_workflow(workflow_id="workflow:critical-activity", started_at=NOW).meta,
        draft=draft,
        summary=SummaryPresented(
            summary_event_id=summary_id,
            draft_id=draft.draft_id,
            draft_version=draft.version,
            subject_signature=draft.subject_signature,
            outbox_message_id=critical_summary_outbox_id(summary_id, digest),
            presented_at=presented_at,
        ),
    )


def _confirmation(context: PendingCriticalActionContext) -> ModelProposal:
    return ModelProposal(
        source_event_id="batch:critical-confirmation",
        intent="confirm",
        reply_chunks=("Pode reservar esse passeio e gerar o link do sinal.",),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        confirmed_summary_version=context.summary_version,
        confirmed_action_kinds=context.action_kinds,
        approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
    )


def test_pending_action_context_is_closed_public_and_canonical() -> None:
    context = PendingCriticalActionContext(
        summary_version=3,
        action_kinds=(
            CriticalActionKind.INITIATE_PAYMENT,
            CriticalActionKind.BOOK_ACTIVITY,
        ),
        public_summary="Vou reservar o passeio e gerar o link de pagamento.",
        expires_at=NOW + TTL,
    )

    assert context.action_kinds == (
        CriticalActionKind.BOOK_ACTIVITY,
        CriticalActionKind.INITIATE_PAYMENT,
    )
    assert not hasattr(context, "offer_id")
    assert not hasattr(context, "subject_signature")
    assert not hasattr(context, "provider_ref")

    with pytest.raises(ValueError, match="unique"):
        replace(
            context,
            action_kinds=(
                CriticalActionKind.BOOK_ACTIVITY,
                CriticalActionKind.BOOK_ACTIVITY,
            ),
        )
    with pytest.raises(ValueError, match="UTC"):
        replace(context, expires_at=datetime(2026, 7, 28, 6, 30))


def test_model_confirmation_requires_bound_action_scope_and_basis() -> None:
    with pytest.raises(InvalidModelProposal, match="approval assertion"):
        ModelProposal(
            source_event_id="batch:approval-missing",
            intent="confirm",
            reply_chunks=("Pode seguir.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
        )

    context = _context()
    proposal = _confirmation(context)
    assert proposal.confirmed_action_kinds == context.action_kinds
    assert proposal.approval_basis is ApprovalBasis.CONTEXTUAL_REFERENCE

    with pytest.raises(InvalidModelProposal, match="only for confirm"):
        ModelProposal(
            source_event_id="batch:approval-on-inform",
            intent="inform",
            reply_chunks=("Tudo bem.",),
            facts=(),
            read_requests=(),
            effect_proposals=(),
            confirmed_action_kinds=context.action_kinds,
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        )

    with pytest.raises(InvalidModelProposal, match="material facts"):
        ModelProposal(
            source_event_id="batch:approval-with-material-change",
            intent="confirm",
            reply_chunks=("Pode seguir, mas agora são duas pessoas.",),
            facts=(ModelFact("adults", 2),),
            read_requests=(),
            effect_proposals=(),
            confirmed_summary_version=1,
            confirmed_action_kinds=(
                CriticalActionKind.BOOK_ACTIVITY,
                CriticalActionKind.INITIATE_PAYMENT,
            ),
            approval_basis=ApprovalBasis.CONTEXTUAL_REFERENCE,
        )


def test_model_request_accepts_only_exact_pending_action_context() -> None:
    context = _context()
    request = ModelRequest(
        request_id="request:critical-action",
        lead_id="manychat:critical-action",
        source_event_id="batch:critical-action",
        message="Pode reservar esse passeio e gerar o link.",
        locale="pt-BR",
        state_version=1,
        pending_action=context,
    )
    assert request.pending_action is context

    with pytest.raises(InvalidModelProposal, match="pending_action"):
        replace(request, pending_action={"summary_version": 1})


def test_policy_is_runtime_owned_with_deny_ask_allow_precedence() -> None:
    policy = CriticalActionPolicy(
        enabled=frozenset(
            {
                CriticalActionKind.BOOK_ACTIVITY,
                CriticalActionKind.INITIATE_PAYMENT,
            }
        ),
        enabled_payment_methods=frozenset({"stripe"}),
    )

    assert policy.classify(None) is CriticalActionDisposition.ALLOW
    assert (
        policy.classify(CriticalActionKind.BOOK_ACTIVITY, now=NOW)
        is CriticalActionDisposition.ASK
    )
    assert (
        policy.classify(CriticalActionKind.CANCEL_RESERVATION, now=NOW)
        is CriticalActionDisposition.DENY
    )
    assert (
        policy.classify(
            CriticalActionKind.INITIATE_PAYMENT,
            payment_method="stripe",
            now=NOW,
        )
        is CriticalActionDisposition.ASK
    )
    assert (
        policy.classify(
            CriticalActionKind.INITIATE_PAYMENT,
            payment_method="wise",
            now=NOW,
        )
        is CriticalActionDisposition.DENY
    )
    assert (
        CriticalActionPolicy.default().classify(
            CriticalActionKind.BOOK_ACTIVITY,
            now=NOW,
        )
        is CriticalActionDisposition.DENY
    )
    expired = _enabled_policy(valid_until=NOW)
    assert (
        expired.classify(CriticalActionKind.BOOK_ACTIVITY, now=NOW)
        is CriticalActionDisposition.DENY
    )
    wise_only = CriticalActionPolicy(
        frozenset(
            {
                CriticalActionKind.BOOK_ACTIVITY,
                CriticalActionKind.INITIATE_PAYMENT,
            }
        ),
        enabled_payment_methods=frozenset({"wise"}),
    )
    with pytest.raises(CriticalActionDenied, match="runtime policy"):
        critical_action_context(
            _activity_draft(),
            summary_version=1,
            presented_at=NOW,
            locale="pt-BR",
            approval_ttl=TTL,
            agency_payment_percentage=20,
            hostel_payment_percentage=100,
            policy=wise_only,
        )


def test_bokun_summary_explains_exact_effect_fee_and_rounded_deposit() -> None:
    context = _context()

    assert context.action_kinds == (
        CriticalActionKind.BOOK_ACTIVITY,
        CriticalActionKind.INITIATE_PAYMENT,
    )
    assert context.public_summary == (
        "Só para confirmar: vou reservar o Roteiro dos 4Ps em 18/11/2026 "
        "para 1 pessoa, pelo total final de R$ 334,95 já com a taxa, e depois "
        "gerar o link do sinal de R$ 66,99 no cartão. Posso fazer essa reserva?"
    )
    forbidden = (
        "BRL",
        "stripe",
        "offer:",
        "product:",
        "subject_signature",
        "quote_scope",
    )
    assert not any(item in context.public_summary for item in forbidden)
    assert context.expires_at == NOW + TTL


def test_digest_and_binding_change_for_every_material_proposal_change() -> None:
    draft = _activity_draft()
    context = _context(draft=draft)
    digest = critical_proposal_digest(draft, context)

    changed_amount = _activity_draft(amount="335.00")
    changed_context = _context(draft=changed_amount)
    assert critical_proposal_digest(changed_amount, changed_context) != digest
    assert critical_proposal_digest(
        draft,
        replace(context, expires_at=context.expires_at + timedelta(seconds=1)),
    ) != digest
    assert critical_proposal_digest(
        draft,
        replace(
            context,
            action_kinds=(CriticalActionKind.BOOK_ACTIVITY,),
            public_summary="Vou reservar somente o passeio. Posso fazer essa reserva?",
        ),
    ) != digest

    outbox = critical_summary_outbox_id("summary:critical", digest)
    assert outbox.startswith("outbox:")
    assert outbox != critical_summary_outbox_id("summary:other", digest)


def test_pending_context_and_approval_match_require_current_bound_unexpired_summary() -> None:
    workflow = _awaiting()
    context = pending_action_context(
        workflow,
        locale="pt-BR",
        approval_ttl=TTL,
        agency_payment_percentage=20,
        hostel_payment_percentage=100,
        policy=_enabled_policy(),
    )
    assert context is not None
    proposal = _confirmation(context)

    assert approval_assertion_matches(
        workflow=workflow,
        pending_action=context,
        proposal=proposal,
        now=NOW + timedelta(minutes=1),
    ) is ApprovalMatch.MATCH
    assert approval_assertion_matches(
        workflow=workflow,
        pending_action=context,
        proposal=proposal,
        now=context.expires_at,
    ) is ApprovalMatch.EXPIRED
    assert approval_assertion_matches(
        workflow=workflow,
        pending_action=context,
        proposal=replace(
            proposal,
            confirmed_action_kinds=(CriticalActionKind.BOOK_ACTIVITY,),
        ),
        now=NOW + timedelta(minutes=1),
    ) is ApprovalMatch.ACTION_SCOPE_MISMATCH

    unbound = replace(
        workflow,
        summary=replace(workflow.summary, outbox_message_id="outbox:wrong-binding"),
    )
    assert pending_action_context(
        unbound,
        locale="pt-BR",
        approval_ttl=TTL,
        agency_payment_percentage=20,
        hostel_payment_percentage=100,
        policy=_enabled_policy(),
    ) is None
