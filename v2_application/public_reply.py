from __future__ import annotations

import unicodedata
from dataclasses import replace
from datetime import date

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
    negative_markers = (" nao ", " not ", "indisponivel", "unavailable")
    if any(marker in f" {text} " for marker in negative_markers):
        return False
    positive_markers = ("disponivel", "available")
    if len(groups) == 1 and any(marker in text for marker in positive_markers):
        return True

    def group_is_grounded(payloads: tuple[dict[str, object], ...]) -> bool:
        for payload in payloads:
            anchors = (
                payload.get("room_public_name"),
                payload.get("product_public_name"),
                payload.get("total_amount"),
            )
            for anchor in anchors:
                if type(anchor) is not str or not anchor:
                    continue
                folded_anchor = _fold_public_text(anchor)
                if folded_anchor in text:
                    return True
                if any(
                    len(token) >= 4 and token in text
                    for token in folded_anchor.split()
                ):
                    return True
        return False

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
        chunk = _render_positive_payload(payloads[0], locale=locale)
        if chunk is None:
            return ()
        chunks.append(chunk)
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
