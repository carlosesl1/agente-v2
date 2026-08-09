from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation

from v2_contracts.model import ModelProposal
from v2_contracts.providers import ReadObservation


def _fold_public_text(value: str) -> str:
    return " ".join(
        "".join(
            character
            for character in unicodedata.normalize("NFKD", value.casefold())
            if not unicodedata.combining(character)
        ).split()
    )


def _public_date(value: object, *, locale: str) -> str | None:
    if type(value) is not str:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if locale.casefold().startswith("en"):
        return parsed.strftime("%Y-%m-%d")
    return parsed.strftime("%d/%m/%Y")


def _payload_is_positive(payload: dict[str, object]) -> bool:
    if payload.get("available") is True:
        return True
    available_units = payload.get("available_units")
    return type(available_units) is int and available_units > 0


def _positive_payloads(
    observation: ReadObservation,
) -> tuple[dict[str, object], ...]:
    payload = observation.public_payload
    options = payload.get("options")
    if type(options) is list:
        return tuple(
            item for item in options if type(item) is dict and _payload_is_positive(item)
        )
    return (payload,) if _payload_is_positive(payload) else ()


def _positive_observation_groups(
    observations: tuple[ReadObservation, ...],
) -> tuple[tuple[dict[str, object], ...], ...]:
    return tuple(
        payloads
        for observation in observations
        if (payloads := _positive_payloads(observation))
    )


def proposal_grounds_positive_observation(
    proposal: ModelProposal,
    observations: tuple[ReadObservation, ...],
) -> bool:
    groups = _positive_observation_groups(observations)
    if not groups:
        return False
    text = _fold_public_text(" ".join(proposal.reply_chunks))
    availability_contradictions = (
        r"\bnao\s+(?:esta\s+)?disponivel\b",
        r"\bnao\s+ha\s+disponibilidade\b",
        r"\bindisponivel\b",
        r"\bsem\s+disponibilidade\b",
        r"\besgotad[oa]s?\b",
        r"\bnot\s+available\b",
        r"\bno\s+availability\b",
        r"\bunavailable\b",
        r"\bsold\s+out\b",
    )
    if any(re.search(pattern, text) for pattern in availability_contradictions):
        return False
    positive_markers = ("disponivel", "disponiveis", "available")
    if not any(marker in text for marker in positive_markers):
        return False

    def anchors_text(payload: dict[str, object]) -> bool:
        anchors = (
            payload.get("room_public_name"),
            payload.get("product_public_name"),
        )
        for anchor in anchors:
            if type(anchor) is not str or not anchor:
                continue
            folded_anchor = _fold_public_text(anchor)
            if folded_anchor in text:
                return True
            if any(
                len(token) >= 4
                and re.search(rf"(?<!\\w){re.escape(token)}(?!\\w)", text)
                for token in folded_anchor.split()
            ):
                return True
        return False

    def amount_grounds_payload(payload: dict[str, object]) -> bool:
        if type(payload.get("room_public_name")) is str:
            domain_pattern = re.compile(
                r"\b(?:accommodations?|lodgings?|hostels?|rooms?|hospedagem|"
                r"quartos?|suites?|dorms?|dormitorios?)\b"
            )
            competing_pattern = re.compile(
                r"\b(?:activities|activity|tours?|passeios?|roteiros?|cachoeiras?|"
                r"trilhas?|transfers?|transportes?|traslados?)\b"
            )
        elif type(payload.get("product_public_name")) is str:
            domain_pattern = re.compile(
                r"\b(?:activities|activity|tours?|passeios?|roteiros?|cachoeiras?|"
                r"trilhas?)\b"
            )
            competing_pattern = re.compile(
                r"\b(?:accommodations?|lodgings?|hostels?|rooms?|hospedagem|"
                r"quartos?|suites?|dorms?|dormitorios?|transfers?|transportes?|"
                r"traslados?)\b"
            )
        else:
            return False
        amount = payload.get("total_amount")
        currency = payload.get("currency")
        if not isinstance(amount, str) or not isinstance(currency, str):
            return False
        try:
            decimal_amount = Decimal(amount)
        except InvalidOperation:
            return False
        fixed = f"{decimal_amount:.2f}"
        compact = format(decimal_amount.normalize(), "f")
        anchors = (
            f"{currency} {fixed}",
            f"{currency} {fixed.replace('.', ',')}",
            f"{currency} {compact}",
            f"{currency} {compact.replace('.', ',')}",
        )
        price_connector = re.compile(
            r"\b(?:from|for|por|a\s+partir\s+de|at|"
            r"total(?:\s+(?:de|of))?|valor(?:\s+de)?|price(?:\s+of)?)\s*$"
        )
        for anchor in anchors:
            folded_anchor = _fold_public_text(anchor)
            pattern = re.compile(
                rf"(?<![\d.,]){re.escape(folded_anchor)}(?!\d|[.,]\d)"
            )
            for match in pattern.finditer(text):
                clause_start = max(
                    text.rfind(delimiter, 0, match.start())
                    for delimiter in (".", "!", "?", ";", "\n")
                )
                prefix = text[clause_start + 1 : match.start()]
                domain_matches = tuple(domain_pattern.finditer(prefix))
                if not domain_matches:
                    continue
                relation = prefix[domain_matches[-1].end() :]
                if competing_pattern.search(relation) is not None:
                    continue
                if price_connector.search(relation) is not None:
                    return True
        return False

    def group_is_grounded(payloads: tuple[dict[str, object], ...]) -> bool:
        return all(
            anchors_text(payload) or amount_grounds_payload(payload)
            for payload in payloads
        )

    return all(group_is_grounded(payloads) for payloads in groups)


def _render_positive_payload(
    payload: dict[str, object],
    *,
    locale: str,
) -> str | None:
    room_name = payload.get("room_public_name")
    product_name = payload.get("product_public_name")
    amount = payload.get("total_amount")
    currency = payload.get("currency")
    if type(room_name) is str:
        check_in = _public_date(payload.get("check_in"), locale=locale)
        check_out = _public_date(payload.get("check_out"), locale=locale)
        if all(type(item) is str for item in (check_in, check_out, amount, currency)):
            if locale.casefold().startswith("en"):
                return (
                    f"I found {room_name} available from {check_in} to {check_out} "
                    f"for {currency} {amount}. Nothing was booked."
                )
            return (
                f"Encontrei {room_name} disponível de {check_in} a {check_out} "
                f"por {currency} {amount}. Nada foi reservado."
            )
    if type(product_name) is str:
        activity_date = _public_date(payload.get("activity_date"), locale=locale)
        if all(type(item) is str for item in (activity_date, amount, currency)):
            if locale.casefold().startswith("en"):
                return (
                    f"I found {product_name} available on {activity_date} for "
                    f"{currency} {amount}. Nothing was booked."
                )
            return (
                f"Encontrei {product_name} disponível em {activity_date} por "
                f"{currency} {amount}. Nada foi reservado."
            )
    return None


def grounded_positive_reply(
    observations: tuple[ReadObservation, ...],
    *,
    locale: str,
) -> tuple[str, ...]:
    groups = _positive_observation_groups(observations)
    if not groups:
        return ()
    chunks: list[str] = []
    for payloads in groups:
        rendered_group: list[str] = []
        for payload in payloads:
            chunk = _render_positive_payload(payload, locale=locale)
            if chunk is None:
                return ()
            rendered_group.append(chunk)
        chunks.append("\n".join(rendered_group))
    return tuple(chunks)


def apply_positive_grounding(
    proposal: ModelProposal,
    observations: tuple[ReadObservation, ...],
    *,
    locale: str,
    force: bool,
) -> ModelProposal:
    """Anchor a missing/unsafe positive result without changing the semantic plan."""

    if type(proposal) is not ModelProposal or type(force) is not bool:
        raise TypeError("positive grounding requires exact contracts")
    grounded = grounded_positive_reply(observations, locale=locale)
    if not grounded or proposal.intent not in {"inform", "adjust"}:
        return proposal
    if not force and proposal_grounds_positive_observation(proposal, observations):
        return proposal
    return replace(proposal, reply_chunks=grounded)
