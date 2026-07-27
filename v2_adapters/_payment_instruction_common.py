"""Shared exact amount rendering for non-card payment instructions."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def percentages(values: dict[str, int] | None, profiles: set[str]) -> dict[str, int]:
    result = {profile: 100 for profile in profiles} if values is None else dict(values)
    if type(values) not in (dict, type(None)) or set(result) != profiles:
        raise ValueError("payment percentages must bind every receiver profile")
    if any(type(value) is not int or not 1 <= value <= 100 for value in result.values()):
        raise ValueError("payment percentages must be exact integers from 1 to 100")
    return result


def instruction_amount(amount_minor: int, currency: str, percentage: int) -> str:
    due_minor = int(
        (
            Decimal(amount_minor) * Decimal(percentage) / Decimal(100)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if due_minor < 1:
        raise ValueError("payment percentage produced no payable minor units")
    major = Decimal(due_minor) / Decimal(100)
    if currency == "BRL":
        rendered = f"{major:.2f}".replace(".", ",")
        return f"Valor desta etapa: R$ {rendered}."
    return f"Valor desta etapa: {currency} {major:.2f}."


__all__ = ["instruction_amount", "percentages"]
