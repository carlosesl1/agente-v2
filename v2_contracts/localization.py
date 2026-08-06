"""Closed non-PII language selection from a canonical private phone."""

from __future__ import annotations

from enum import Enum
import re


_E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")


class CustomerLanguage(str, Enum):
    PT_BR = "pt-BR"
    EN = "en"


def customer_language_from_phone(phone_e164: str) -> CustomerLanguage:
    if type(phone_e164) is not str or _E164_RE.fullmatch(phone_e164) is None:
        raise ValueError("phone_e164 must be canonical E.164")
    if phone_e164.startswith("+55"):
        return CustomerLanguage.PT_BR
    return CustomerLanguage.EN


__all__ = ["CustomerLanguage", "customer_language_from_phone"]
