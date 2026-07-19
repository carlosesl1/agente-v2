"""Deterministic public rendering of a canonical commercial draft."""

from __future__ import annotations

from decimal import Decimal
import unicodedata

from reservation_domain import (
    CommercialDraft,
    Money,
    OfferSnapshot,
    Party,
    ServiceKind,
)

from .types import RenderedSummary, SummaryLocale, rendered_summary_hash

RENDERER_ID = "summary-renderer"
RENDERER_VERSION = 1


def _public_text(value: str, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        raise ValueError(f"{field_name} contains a control character")
    normalized = " ".join(normalized.split())
    if not normalized or len(normalized) > 300:
        raise ValueError(f"{field_name} must contain 1..300 public characters")
    return normalized


def _money(value: Money) -> str:
    return f"{value.currency} {format(value.amount, 'f')}"


def _party_line(party: Party, locale: SummaryLocale) -> str:
    if locale is SummaryLocale.PT_BR:
        adults = "adulto" if party.adults == 1 else "adultos"
        children = "criança" if party.children == 1 else "crianças"
        return f"Pessoas: {party.adults} {adults}, {party.children} {children}"
    adults = "adult" if party.adults == 1 else "adults"
    children = "child" if party.children == 1 else "children"
    return f"Guests: {party.adults} {adults}, {party.children} {children}"


def _component_lines(component: OfferSnapshot, locale: SummaryLocale) -> tuple[str, ...]:
    label = _public_text(component.public_label, "public_label")
    if locale is SummaryLocale.PT_BR:
        service = "Hospedagem" if component.service is ServiceKind.LODGING else "Passeio"
        lines = [
            f"{service}: {label}",
            f"Data: {component.start_date.isoformat()}",
        ]
        if component.end_date is not None:
            lines.append(f"Até: {component.end_date.isoformat()}")
        if component.start_time is not None:
            lines.append(f"Horário: {component.start_time}")
        lines.extend((_party_line(component.party, locale), f"Valor: {_money(component.total)}"))
        return tuple(lines)
    service = "Lodging" if component.service is ServiceKind.LODGING else "Activity"
    lines = [
        f"{service}: {label}",
        f"Date: {component.start_date.isoformat()}",
    ]
    if component.end_date is not None:
        lines.append(f"Until: {component.end_date.isoformat()}")
    if component.start_time is not None:
        lines.append(f"Time: {component.start_time}")
    lines.extend((_party_line(component.party, locale), f"Price: {_money(component.total)}"))
    return tuple(lines)


def _customer_lines(draft: CommercialDraft, locale: SummaryLocale) -> tuple[str, ...]:
    customer = draft.customer
    name = _public_text(customer.full_name, "full_name")
    email = _public_text(customer.email, "email")
    phone = _public_text(customer.phone_e164, "phone_e164")
    country = _public_text(customer.country_code, "country_code")
    payment = _public_text(draft.terms.payment_method, "payment_method")
    if locale is SummaryLocale.PT_BR:
        return (
            "Cliente",
            f"Nome: {name}",
            f"E-mail: {email}",
            f"Telefone: {phone}",
            f"País: {country}",
            f"Pagamento: {payment}",
        )
    return (
        "Customer",
        f"Name: {name}",
        f"Email: {email}",
        f"Phone: {phone}",
        f"Country: {country}",
        f"Payment: {payment}",
    )


def _add_on_lines(draft: CommercialDraft, locale: SummaryLocale) -> tuple[str, ...]:
    title = "Adicionais" if locale is SummaryLocale.PT_BR else "Add-ons"
    if not draft.terms.add_ons:
        empty = "- nenhum" if locale is SummaryLocale.PT_BR else "- none"
        return (title, empty)
    unit_word = "cada" if locale is SummaryLocale.PT_BR else "each"
    return (
        title,
        *(
            f"- {_public_text(item.code, 'add_on.code')} × {item.quantity}: "
            f"{_money(item.unit_price)} {unit_word} = {_money(item.total)}"
            for item in draft.terms.add_ons
        ),
    )


def _private_values(draft: CommercialDraft) -> tuple[str, ...]:
    return (
        draft.draft_id,
        draft.subject_signature,
        draft.customer.customer_ref,
        *(
            value
            for component in draft.components
            for value in (
                component.offer_id,
                component.lookup_id,
                component.provider_ref,
            )
        ),
    )


def render_summary(
    draft: CommercialDraft,
    *,
    locale: SummaryLocale,
) -> RenderedSummary:
    """Render all customer-reviewable facts without any provider/effect claim."""

    if type(draft) is not CommercialDraft:
        raise ValueError("draft must be an exact CommercialDraft")
    if type(locale) is not SummaryLocale:
        raise ValueError("locale must use SummaryLocale")

    components = tuple(sorted(draft.components, key=lambda item: item.offer_id))
    component_total = sum((item.total.amount for item in components), Decimal("0.00"))
    add_on_total = sum((item.total.amount for item in draft.terms.add_ons), Decimal("0.00"))
    currency = components[0].total.currency

    lines: list[str] = [
        "Resumo do pedido" if locale is SummaryLocale.PT_BR else "Order summary",
        ("Versão: " if locale is SummaryLocale.PT_BR else "Version: ") + str(draft.version),
    ]
    for component in components:
        lines.extend(("", *_component_lines(component, locale)))
    lines.extend(("", *_customer_lines(draft, locale), "", *_add_on_lines(draft, locale), ""))

    subtotal = Money(amount=component_total, currency=currency)
    additions = Money(amount=add_on_total, currency=currency)
    total = Money(amount=component_total + add_on_total, currency=currency)
    if locale is SummaryLocale.PT_BR:
        lines.extend(
            (
                f"Subtotal das opções: {_money(subtotal)}",
                f"Total de adicionais: {_money(additions)}",
                f"Total do pedido: {_money(total)}",
                "",
                "Nenhuma reserva foi criada. Confirma este resumo ou deseja ajustar algo?",
            )
        )
    else:
        lines.extend(
            (
                f"Options subtotal: {_money(subtotal)}",
                f"Add-ons total: {_money(additions)}",
                f"Order total: {_money(total)}",
                "",
                "No booking has been created. Do you confirm this summary or want to adjust it?",
            )
        )
    content = "\n".join(lines)
    leaked = tuple(value for value in _private_values(draft) if value in content)
    if leaked:
        raise ValueError("rendered summary contains a private domain identifier")
    content_hash = rendered_summary_hash(
        renderer_id=RENDERER_ID,
        renderer_version=RENDERER_VERSION,
        locale=locale,
        draft_id=draft.draft_id,
        draft_version=draft.version,
        subject_signature=draft.subject_signature,
        content=content,
    )
    return RenderedSummary(
        renderer_id=RENDERER_ID,
        renderer_version=RENDERER_VERSION,
        locale=locale,
        draft_id=draft.draft_id,
        draft_version=draft.version,
        subject_signature=draft.subject_signature,
        content=content,
        content_hash=content_hash,
        claim_status="none",
        private_fields=(),
    )


__all__ = ["RENDERER_ID", "RENDERER_VERSION", "render_summary"]
