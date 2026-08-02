"""Parent-owned extraction and prompt redaction for private customer facts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from v2_application.private_customer_facts import (
    canonical_country_code,
    canonical_email,
    canonical_full_name,
)
from v2_contracts.model import ModelFact

_FACT_ORDER = ("full_name", "email", "country_code")
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_PHONE_RE = re.compile(r"(?<!\w)\+\d[\d ()-]{6,20}\d(?!\w)")
_NAME_LABEL_RE = re.compile(
    r"(?:\bmeu\s+nome(?:\s+completo)?\s+(?:é|e)\s+|"
    r"\bnome(?:\s+completo)?\s*[:=]\s*|"
    r"\bmy\s+(?:full\s+)?name\s+is\s+|"
    r"\bfull\s+name\s*[:=]\s*)"
    r"(?P<value>.+?)"
    r"(?=\s*[,;]|\s+(?:meu\s+)?e-?mail\b|\s+email\b|"
    r"\s+e\s+sou\b|\s+pa[ií]s\b|\s+country\b|$)",
    re.IGNORECASE,
)
_EMAIL_LABEL_RE = re.compile(
    r"\b(?:meu\s+)?e-?mail\s*(?:(?:é|e|is)\s+|[:=]\s*)"
    r"(?P<value>.+?)"
    r"(?=\s*[,;]|\s+e\s+sou\b|\s+and\s+(?:i\s+am|i'm)\b|"
    r"\s+pa[ií]s\b|\s+country\b|$)",
    re.IGNORECASE,
)
_COUNTRY_LABEL_RE = re.compile(
    r"(?:\bpa[ií]s\s*(?:(?:é|e|is)\s+|[:=]\s*)|"
    r"\bcountry\s*(?:(?:é|e|is)\s+|[:=]\s*)|"
    r"\bsou\s+d[oea]\s+|"
    r"\b(?:i(?:'m|\s+am)\s+)?from\s+)"
    r"(?P<value>[^,;.]+)",
    re.IGNORECASE,
)
_SEGMENT_RE = re.compile(r"[^,;\n]+")

_COUNTRY_ALIASES = {
    "argentina": "AR",
    "australia": "AU",
    "austria": "AT",
    "belgica": "BE",
    "belgium": "BE",
    "bolivia": "BO",
    "brasil": "BR",
    "brazil": "BR",
    "canada": "CA",
    "chile": "CL",
    "china": "CN",
    "colombia": "CO",
    "denmark": "DK",
    "dinamarca": "DK",
    "estados unidos": "US",
    "espanha": "ES",
    "finland": "FI",
    "finlandia": "FI",
    "france": "FR",
    "franca": "FR",
    "germany": "DE",
    "alemanha": "DE",
    "holanda": "NL",
    "india": "IN",
    "ireland": "IE",
    "irlanda": "IE",
    "italia": "IT",
    "italy": "IT",
    "japan": "JP",
    "japao": "JP",
    "mexico": "MX",
    "netherlands": "NL",
    "new zealand": "NZ",
    "nova zelandia": "NZ",
    "noruega": "NO",
    "norway": "NO",
    "paises baixos": "NL",
    "paraguai": "PY",
    "paraguay": "PY",
    "peru": "PE",
    "portugal": "PT",
    "reino unido": "GB",
    "spain": "ES",
    "suecia": "SE",
    "sweden": "SE",
    "suica": "CH",
    "switzerland": "CH",
    "united kingdom": "GB",
    "united states": "US",
    "uruguai": "UY",
    "uruguay": "UY",
}


@dataclass(frozen=True, slots=True, repr=False)
class PrivateCustomerCollection:
    facts: tuple[ModelFact, ...]
    invalid_fact_names: tuple[str, ...]
    sanitized_message: str
    phone_supplied: bool

    def __post_init__(self) -> None:
        if type(self.facts) is not tuple or any(
            type(item) is not ModelFact or item.name not in _FACT_ORDER
            for item in self.facts
        ):
            raise TypeError("facts must contain exact private ModelFact values")
        if tuple(item.name for item in self.facts) != tuple(
            name for name in _FACT_ORDER if any(item.name == name for item in self.facts)
        ):
            raise ValueError("facts must be unique and canonical order")
        if (
            type(self.invalid_fact_names) is not tuple
            or any(name not in _FACT_ORDER for name in self.invalid_fact_names)
            or self.invalid_fact_names
            != tuple(name for name in _FACT_ORDER if name in self.invalid_fact_names)
        ):
            raise ValueError("invalid_fact_names must be canonical")
        if type(self.sanitized_message) is not str:
            raise TypeError("sanitized_message must be str")
        if type(self.phone_supplied) is not bool:
            raise TypeError("phone_supplied must be bool")


def _alias_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(without_marks.casefold().strip().split())


def _country(value: str) -> str:
    candidate = value.strip().rstrip(".")
    key = _alias_key(candidate)
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    if len(candidate) == 2 and candidate.isalpha():
        return canonical_country_code(candidate)
    raise ValueError("country is not a supported explicit ISO identity")


def _unlabelled_full_name(value: str) -> str:
    canonical = canonical_full_name(value)
    connectors = {"da", "das", "de", "do", "dos", "e", "van", "von"}
    principal_tokens = [
        token
        for token in canonical.split()
        if token.casefold() not in connectors
    ]
    if len(principal_tokens) < 2 or any(
        not token[0].isupper() for token in principal_tokens
    ):
        raise ValueError("unlabelled full name is ambiguous")
    return canonical


def _trimmed_span(message: str, start: int, end: int) -> tuple[int, int, str]:
    while start < end and message[start].isspace():
        start += 1
    while end > start and (message[end - 1].isspace() or message[end - 1] in "."):
        end -= 1
    return start, end, message[start:end]


def _overlaps(start: int, end: int, spans: list[tuple[int, int, str]]) -> bool:
    return any(start < occupied_end and end > occupied_start for occupied_start, occupied_end, _ in spans)


def collect_private_customer_facts(
    message: str,
    *,
    expected_fact_names: tuple[str, ...] = _FACT_ORDER,
) -> PrivateCustomerCollection:
    if type(message) is not str:
        raise TypeError("message must be str")
    if (
        type(expected_fact_names) is not tuple
        or any(name not in _FACT_ORDER for name in expected_fact_names)
        or len(set(expected_fact_names)) != len(expected_fact_names)
    ):
        raise ValueError("expected_fact_names must contain unique private fact names")
    expected = set(expected_fact_names)

    values: dict[str, str] = {}
    invalid: set[str] = set()
    spans: list[tuple[int, int, str]] = []

    def capture(
        name: str,
        start: int,
        end: int,
        raw: str,
        canonicalizer,
    ) -> None:
        if _overlaps(start, end, spans):
            return
        try:
            canonical = canonicalizer(raw)
        except ValueError:
            invalid.add(name)
            spans.append((start, end, f"[invalid private {name} supplied]"))
            values.pop(name, None)
            return
        existing = values.get(name)
        if existing is not None and existing != canonical:
            invalid.add(name)
            values.pop(name, None)
        elif name not in invalid:
            values[name] = canonical
        spans.append((start, end, f"[private {name} supplied]"))

    phone_matches = tuple(_PHONE_RE.finditer(message))
    for match in phone_matches:
        spans.append((match.start(), match.end(), "[phone supplied but not accepted]"))

    for match in _EMAIL_RE.finditer(message):
        capture("email", match.start(), match.end(), match.group(), canonical_email)

    for regex, name, canonicalizer in (
        (_NAME_LABEL_RE, "full_name", canonical_full_name),
        (_EMAIL_LABEL_RE, "email", canonical_email),
        (_COUNTRY_LABEL_RE, "country_code", _country),
    ):
        for match in regex.finditer(message):
            start, end, raw = _trimmed_span(
                message,
                match.start("value"),
                match.end("value"),
            )
            capture(name, start, end, raw, canonicalizer)

    if "," in message or ";" in message or "\n" in message:
        for match in _SEGMENT_RE.finditer(message):
            start, end, raw = _trimmed_span(message, match.start(), match.end())
            if not raw or _overlaps(start, end, spans):
                continue
            if "email" in expected and _EMAIL_RE.fullmatch(raw):
                capture("email", start, end, raw, canonical_email)
                continue
            if "country_code" in expected:
                try:
                    _country(raw)
                except ValueError:
                    pass
                else:
                    capture("country_code", start, end, raw, _country)
                    continue
            if "full_name" not in expected:
                continue
            try:
                _unlabelled_full_name(raw)
            except ValueError:
                continue
            capture("full_name", start, end, raw, _unlabelled_full_name)

    if not spans and message.strip():
        start = len(message) - len(message.lstrip())
        end = len(message.rstrip())
        raw = message[start:end]
        for name, canonicalizer in (
            ("email", canonical_email),
            ("country_code", _country),
            ("full_name", _unlabelled_full_name),
        ):
            if name not in expected:
                continue
            try:
                canonicalizer(raw)
            except ValueError:
                continue
            capture(name, start, end, raw, canonicalizer)
            break

    sanitized = message
    for start, end, marker in sorted(spans, reverse=True):
        sanitized = sanitized[:start] + marker + sanitized[end:]

    facts = tuple(
        ModelFact(name, values[name])
        for name in _FACT_ORDER
        if name in values and name not in invalid
    )
    invalid_names = tuple(name for name in _FACT_ORDER if name in invalid)
    return PrivateCustomerCollection(
        facts=facts,
        invalid_fact_names=invalid_names,
        sanitized_message=sanitized,
        phone_supplied=bool(phone_matches),
    )
