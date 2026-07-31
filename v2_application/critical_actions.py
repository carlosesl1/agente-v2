"""Runtime-owned critical-action approval policy and public rendering."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum

from reservation_domain import (
    AwaitingConfirmationState,
    CommercialDraft,
    Party,
    ServiceKind,
)
from v2_contracts.critical_actions import (
    ApprovalBasis,
    CriticalActionKind,
    PendingCriticalActionContext,
)
from v2_contracts.model import ModelProposal


class CriticalActionDisposition(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class CriticalActionDenied(ValueError):
    """The runtime policy does not grant this semantic capability."""


class ApprovalMatch(str, Enum):
    MATCH = "match"
    ABSENT = "absent"
    STALE_VERSION = "stale_version"
    ACTION_SCOPE_MISMATCH = "action_scope_mismatch"
    BASIS_MISMATCH = "basis_mismatch"
    EXPIRED = "expired"
    UNBOUND = "unbound"


@dataclass(frozen=True, slots=True)
class CriticalActionPolicy:
    enabled: frozenset[CriticalActionKind]
    enabled_payment_methods: frozenset[str] = frozenset()
    activity_participant_limit: int | None = None
    valid_until: datetime | None = None
    kill_switch_engaged: bool = False

    def __post_init__(self) -> None:
        if type(self.enabled) is not frozenset or any(
            type(item) is not CriticalActionKind for item in self.enabled
        ):
            raise TypeError("enabled must be an exact CriticalActionKind frozenset")
        if type(self.enabled_payment_methods) is not frozenset or any(
            type(item) is not str or item not in {"stripe", "wise", "pix"}
            for item in self.enabled_payment_methods
        ):
            raise TypeError(
                "enabled_payment_methods must be an exact closed string frozenset"
            )
        if self.activity_participant_limit is not None and (
            type(self.activity_participant_limit) is not int
            or isinstance(self.activity_participant_limit, bool)
            or self.activity_participant_limit < 1
        ):
            raise ValueError(
                "activity_participant_limit must be a positive exact integer or None"
            )
        if self.valid_until is not None:
            _utc(self.valid_until, "valid_until")
        if type(self.kill_switch_engaged) is not bool:
            raise TypeError("kill_switch_engaged must be an exact bool")

    @classmethod
    def default(cls) -> "CriticalActionPolicy":
        return cls(frozenset(), frozenset(), kill_switch_engaged=True)

    def classify(
        self,
        action: CriticalActionKind | None,
        *,
        payment_method: str | None = None,
        now: datetime | None = None,
    ) -> CriticalActionDisposition:
        if action is None:
            return CriticalActionDisposition.ALLOW
        if type(action) is not CriticalActionKind:
            raise TypeError("action must be an exact CriticalActionKind or None")
        if now is not None:
            _utc(now, "now")
        if self.kill_switch_engaged:
            return CriticalActionDisposition.DENY
        if self.valid_until is not None and (
            now is None or now >= self.valid_until
        ):
            return CriticalActionDisposition.DENY
        if action in self.enabled:
            if action is CriticalActionKind.INITIATE_PAYMENT and (
                type(payment_method) is not str
                or payment_method not in self.enabled_payment_methods
            ):
                return CriticalActionDisposition.DENY
            return CriticalActionDisposition.ASK
        return CriticalActionDisposition.DENY


def _utc(value: datetime, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{name} must be an exact UTC datetime")
    return value


def _percentage(value: int, name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 100:
        raise ValueError(f"{name} must be an exact integer from 1 to 100")
    return value


def _money(amount: Decimal, currency: str) -> str:
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, cents = f"{rounded:.2f}".split(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    symbols = {"BRL": "R$", "EUR": "€", "USD": "US$"}
    prefix = symbols.get(currency, currency)
    return f"{prefix} {grouped},{cents}"


def _date(value) -> str:
    return value.strftime("%d/%m/%Y")


def _people(count: int) -> str:
    return "1 pessoa" if count == 1 else f"{count} pessoas"


def _party(party: Party) -> str:
    if party.children == 0:
        return _people(party.adults)
    adults = "1 adulto" if party.adults == 1 else f"{party.adults} adultos"
    children = "1 criança" if party.children == 1 else f"{party.children} crianças"
    return f"{adults} e {children}"


def _payment_amount(total: Decimal, percentage: int) -> Decimal:
    return (total * Decimal(percentage) / Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _actions(draft: CommercialDraft) -> tuple[CriticalActionKind, ...]:
    services = tuple(item.service for item in draft.components)
    if services == (ServiceKind.LODGING,):
        reservation = CriticalActionKind.RESERVE_LODGING
    elif services == (ServiceKind.ACTIVITY,):
        reservation = CriticalActionKind.BOOK_ACTIVITY
    elif set(services) == {ServiceKind.LODGING, ServiceKind.ACTIVITY} and len(services) == 2:
        reservation = CriticalActionKind.BOOK_PACKAGE
    else:  # guarded again by CommercialDraft, kept fail-closed at this boundary
        raise ValueError("draft component scope is not a supported critical action")
    return tuple(
        sorted(
            (reservation, CriticalActionKind.INITIATE_PAYMENT),
            key=lambda item: item.value,
        )
    )


def _payment_text(
    *,
    method: str,
    amount: Decimal,
    currency: str,
    percentage: int,
    unit: ServiceKind,
) -> str:
    rendered = _money(_payment_amount(amount, percentage), currency)
    is_signal = unit is ServiceKind.ACTIVITY and percentage < 100
    label = f"sinal de {rendered}" if is_signal else f"pagamento de {rendered}"
    if method == "stripe":
        return f"gerar o link do {label} no cartão"
    if method == "pix":
        return f"enviar as instruções para o {label} por Pix"
    if method == "wise":
        return f"enviar as instruções para o {label} pela Wise"
    raise ValueError("draft payment method is outside the closed catalog")


def _money_en(amount: Decimal, currency: str) -> str:
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{currency} {rounded:,.2f}"


def _date_en(value) -> str:
    months = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    return f"{months[value.month - 1]} {value.day}, {value.year}"


def _people_en(count: int) -> str:
    return "1 person" if count == 1 else f"{count} people"


def _party_en(party: Party) -> str:
    if party.children == 0:
        return _people_en(party.adults)
    adults = "1 adult" if party.adults == 1 else f"{party.adults} adults"
    children = "1 child" if party.children == 1 else f"{party.children} children"
    return f"{adults} and {children}"


def _payment_text_en(
    *,
    method: str,
    amount: Decimal,
    currency: str,
    percentage: int,
    unit: ServiceKind,
) -> str:
    rendered = _money_en(_payment_amount(amount, percentage), currency)
    is_deposit = unit is ServiceKind.ACTIVITY and percentage < 100
    label = f"the {rendered} deposit" if is_deposit else f"the {rendered} payment"
    if method == "stripe":
        return f"generate the card link for {label}"
    if method == "pix":
        return f"send the Pix instructions for {label}"
    if method == "wise":
        return f"send the Wise instructions for {label}"
    raise ValueError("draft payment method is outside the closed catalog")


def _public_summary_en(
    draft: CommercialDraft,
    *,
    agency_payment_percentage: int,
    hostel_payment_percentage: int,
) -> str:
    method = draft.terms.payment_method
    components = draft.components
    if len(components) == 1:
        component = components[0]
        total = _money_en(component.total.amount, component.total.currency)
        if component.service is ServiceKind.ACTIVITY:
            effect = (
                f"I’ll book {component.public_label} on {_date_en(component.start_date)} "
                f"for {_party_en(component.party)}, at a final total of {total} including the booking fee"
            )
            payment = _payment_text_en(
                method=method,
                amount=component.total.amount,
                currency=component.total.currency,
                percentage=agency_payment_percentage,
                unit=ServiceKind.ACTIVITY,
            )
        else:
            if component.end_date is None:
                raise ValueError("lodging critical summary requires checkout")
            effect = (
                f"I’ll book {component.public_label} from {_date_en(component.start_date)} "
                f"to {_date_en(component.end_date)} for {_party_en(component.party)}, "
                f"at a final total of {total}"
            )
            payment = _payment_text_en(
                method=method,
                amount=component.total.amount,
                currency=component.total.currency,
                percentage=hostel_payment_percentage,
                unit=ServiceKind.LODGING,
            )
        return (
            f"Just to confirm: {effect}, and then {payment}. "
            "May I make this booking?"
        )

    lodging = next(item for item in components if item.service is ServiceKind.LODGING)
    activity = next(item for item in components if item.service is ServiceKind.ACTIVITY)
    if lodging.end_date is None:
        raise ValueError("package lodging summary requires checkout")
    lodging_total = _money_en(lodging.total.amount, lodging.total.currency)
    activity_total = _money_en(activity.total.amount, activity.total.currency)
    lodging_payment = _money_en(
        _payment_amount(lodging.total.amount, hostel_payment_percentage),
        lodging.total.currency,
    )
    activity_payment = _money_en(
        _payment_amount(activity.total.amount, agency_payment_percentage),
        activity.total.currency,
    )
    if method == "stripe":
        payment = (
            f"generate the card links: {lodging_payment} for the lodging and "
            f"a {activity_payment} deposit for the tour"
        )
    else:
        channel = "Pix" if method == "pix" else "Wise"
        payment = (
            f"send the {channel} payment instructions for {lodging_payment} for the lodging "
            f"and a {activity_payment} deposit for the tour"
        )
    return (
        "Just to confirm: I’ll book "
        f"{lodging.public_label} from {_date_en(lodging.start_date)} "
        f"to {_date_en(lodging.end_date)} for {_party_en(lodging.party)}, "
        f"at a total of {lodging_total}, and {activity.public_label} on "
        f"{_date_en(activity.start_date)} for {_party_en(activity.party)}, "
        f"at a final total of {activity_total} including the booking fee; "
        f"then I’ll {payment}. May I make these bookings?"
    )


def _public_summary(
    draft: CommercialDraft,
    *,
    locale: str,
    agency_payment_percentage: int,
    hostel_payment_percentage: int,
) -> str:
    if type(locale) is not str or not locale:
        raise ValueError("locale must be non-empty exact text")
    folded_locale = locale.casefold()
    if folded_locale.startswith("en"):
        return _public_summary_en(
            draft,
            agency_payment_percentage=agency_payment_percentage,
            hostel_payment_percentage=hostel_payment_percentage,
        )
    if not folded_locale.startswith("pt"):
        raise ValueError("critical approval renderer supports pt or en locale only")
    method = draft.terms.payment_method
    components = draft.components
    if len(components) == 1:
        component = components[0]
        total = _money(component.total.amount, component.total.currency)
        if component.service is ServiceKind.ACTIVITY:
            effect = (
                f"vou reservar o {component.public_label} em {_date(component.start_date)} "
                f"para {_party(component.party)}, pelo total final de {total} já com a taxa"
            )
            payment = _payment_text(
                method=method,
                amount=component.total.amount,
                currency=component.total.currency,
                percentage=agency_payment_percentage,
                unit=ServiceKind.ACTIVITY,
            )
        else:
            if component.end_date is None:
                raise ValueError("lodging critical summary requires checkout")
            effect = (
                f"vou reservar {component.public_label} de {_date(component.start_date)} "
                f"a {_date(component.end_date)} para {_party(component.party)}, "
                f"pelo total final de {total}"
            )
            payment = _payment_text(
                method=method,
                amount=component.total.amount,
                currency=component.total.currency,
                percentage=hostel_payment_percentage,
                unit=ServiceKind.LODGING,
            )
        return (
            f"Só para confirmar: {effect}, e depois {payment}. "
            "Posso fazer essa reserva?"
        )

    lodging = next(
        item for item in components if item.service is ServiceKind.LODGING
    )
    activity = next(
        item for item in components if item.service is ServiceKind.ACTIVITY
    )
    if lodging.end_date is None:
        raise ValueError("package lodging summary requires checkout")
    lodging_total = _money(lodging.total.amount, lodging.total.currency)
    activity_total = _money(activity.total.amount, activity.total.currency)
    lodging_payment = _money(
        _payment_amount(lodging.total.amount, hostel_payment_percentage),
        lodging.total.currency,
    )
    activity_payment = _money(
        _payment_amount(activity.total.amount, agency_payment_percentage),
        activity.total.currency,
    )
    if method == "stripe":
        payment = (
            f"gerar os links no cartão: {lodging_payment} pela hospedagem e "
            f"sinal de {activity_payment} pelo passeio"
        )
    else:
        payment = (
            f"enviar as instruções de pagamento para {lodging_payment} pela hospedagem "
            f"e sinal de {activity_payment} pelo passeio via "
            f"{'Pix' if method == 'pix' else 'Wise'}"
        )
    return (
        "Só para confirmar: vou reservar "
        f"{lodging.public_label} de {_date(lodging.start_date)} a {_date(lodging.end_date)} "
        f"para {_party(lodging.party)}, pelo total de {lodging_total}, e "
        f"{activity.public_label} em {_date(activity.start_date)} para "
        f"{_party(activity.party)}, pelo total final de {activity_total} já com a taxa; "
        f"depois vou {payment}. Posso fazer essas reservas?"
    )


def critical_action_context(
    draft: CommercialDraft,
    *,
    summary_version: int,
    presented_at: datetime,
    locale: str,
    approval_ttl: timedelta,
    agency_payment_percentage: int,
    hostel_payment_percentage: int,
    policy: CriticalActionPolicy,
) -> PendingCriticalActionContext:
    if type(draft) is not CommercialDraft:
        raise TypeError("draft must be an exact CommercialDraft")
    instant = _utc(presented_at, "presented_at")
    if type(approval_ttl) is not timedelta or approval_ttl <= timedelta(0):
        raise ValueError("approval_ttl must be a positive exact timedelta")
    agency = _percentage(agency_payment_percentage, "agency_payment_percentage")
    hostel = _percentage(hostel_payment_percentage, "hostel_payment_percentage")
    if type(policy) is not CriticalActionPolicy:
        raise TypeError("policy must be an exact CriticalActionPolicy")
    actions = _actions(draft)
    if not critical_action_scope_available(draft, policy=policy, now=instant):
        raise CriticalActionDenied("critical action scope is denied by runtime policy")
    return PendingCriticalActionContext(
        summary_version=summary_version,
        action_kinds=actions,
        public_summary=_public_summary(
            draft,
            locale=locale,
            agency_payment_percentage=agency,
            hostel_payment_percentage=hostel,
        ),
        expires_at=instant + approval_ttl,
    )


def critical_action_scope_available(
    draft: CommercialDraft,
    *,
    policy: CriticalActionPolicy,
    now: datetime,
) -> bool:
    if type(draft) is not CommercialDraft:
        raise TypeError("draft must be an exact CommercialDraft")
    if type(policy) is not CriticalActionPolicy:
        raise TypeError("policy must be an exact CriticalActionPolicy")
    instant = _utc(now, "now")
    if policy.activity_participant_limit is not None and any(
        component.service is ServiceKind.ACTIVITY
        and component.party.adults + component.party.children
        > policy.activity_participant_limit
        for component in draft.components
    ):
        return False
    return all(
        policy.classify(
            action,
            payment_method=(
                draft.terms.payment_method
                if action is CriticalActionKind.INITIATE_PAYMENT
                else None
            ),
            now=instant,
        )
        is CriticalActionDisposition.ASK
        for action in _actions(draft)
    )


def critical_proposal_digest(
    draft: CommercialDraft,
    context: PendingCriticalActionContext,
) -> str:
    if type(draft) is not CommercialDraft:
        raise TypeError("draft must be an exact CommercialDraft")
    if type(context) is not PendingCriticalActionContext:
        raise TypeError("context must be an exact PendingCriticalActionContext")
    payload = json.dumps(
        {
            "draft_id": draft.draft_id,
            "draft_version": draft.version,
            "subject_signature": draft.subject_signature,
            "summary_version": context.summary_version,
            "actions": [item.value for item in context.action_kinds],
            "public_summary": context.public_summary,
            "expires_at": context.expires_at.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(b"maya-critical-action-proposal-v1\x00" + payload).hexdigest()


def critical_summary_outbox_id(summary_event_id: str, proposal_digest: str) -> str:
    if type(summary_event_id) is not str or not summary_event_id:
        raise ValueError("summary_event_id must be non-empty exact text")
    if (
        type(proposal_digest) is not str
        or len(proposal_digest) != 64
        or any(char not in "0123456789abcdef" for char in proposal_digest)
    ):
        raise ValueError("proposal_digest must be a lowercase SHA-256")
    digest = hashlib.sha256(
        b"maya-critical-summary-outbox-v1\x00"
        + summary_event_id.encode("utf-8")
        + b"\x00"
        + proposal_digest.encode("ascii")
    ).hexdigest()
    return "outbox:" + digest[:32]


def pending_action_context(
    workflow: AwaitingConfirmationState,
    *,
    locale: str,
    approval_ttl: timedelta,
    agency_payment_percentage: int,
    hostel_payment_percentage: int,
    policy: CriticalActionPolicy,
) -> PendingCriticalActionContext | None:
    if type(workflow) is not AwaitingConfirmationState:
        raise TypeError("workflow must be an exact AwaitingConfirmationState")
    try:
        context = critical_action_context(
            workflow.draft,
            summary_version=workflow.summary.draft_version,
            presented_at=workflow.summary.presented_at,
            locale=locale,
            approval_ttl=approval_ttl,
            agency_payment_percentage=agency_payment_percentage,
            hostel_payment_percentage=hostel_payment_percentage,
            policy=policy,
        )
    except (TypeError, ValueError):
        return None
    digest = critical_proposal_digest(workflow.draft, context)
    expected = critical_summary_outbox_id(
        workflow.summary.summary_event_id,
        digest,
    )
    if workflow.summary.outbox_message_id != expected:
        return None
    return context


def approval_assertion_matches(
    *,
    workflow: AwaitingConfirmationState,
    pending_action: PendingCriticalActionContext | None,
    proposal: ModelProposal,
    now: datetime,
) -> ApprovalMatch:
    if type(workflow) is not AwaitingConfirmationState:
        raise TypeError("workflow must be an exact AwaitingConfirmationState")
    instant = _utc(now, "now")
    if pending_action is None or proposal.intent != "confirm":
        return ApprovalMatch.ABSENT
    if type(pending_action) is not PendingCriticalActionContext:
        raise TypeError("pending_action must be exact PendingCriticalActionContext or None")
    if instant >= pending_action.expires_at:
        return ApprovalMatch.EXPIRED
    if (
        pending_action.summary_version != workflow.draft.version
        or proposal.confirmed_summary_version != pending_action.summary_version
    ):
        return ApprovalMatch.STALE_VERSION
    if proposal.confirmed_action_kinds != pending_action.action_kinds:
        return ApprovalMatch.ACTION_SCOPE_MISMATCH
    if proposal.approval_basis is not ApprovalBasis.CONTEXTUAL_REFERENCE:
        return ApprovalMatch.BASIS_MISMATCH
    return ApprovalMatch.MATCH


__all__ = [
    "ApprovalMatch",
    "CriticalActionDenied",
    "CriticalActionDisposition",
    "CriticalActionPolicy",
    "approval_assertion_matches",
    "critical_action_context",
    "critical_action_scope_available",
    "critical_proposal_digest",
    "critical_summary_outbox_id",
    "pending_action_context",
]
