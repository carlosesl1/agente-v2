"""Model-agnostic confirmation classifier contract and deterministic reference."""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable
import unicodedata

from reservation_domain import ConfirmationDecisionKind

from .types import ClassificationInput, DecisionCandidate


def _canonical_phrase(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _tokens(value: str) -> tuple[str, ...]:
    canonical = _canonical_phrase(value)
    return tuple(canonical.split()) if canonical else ()


def _contains(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    width = len(phrase)
    return bool(
        width
        and any(tokens[index : index + width] == phrase for index in range(len(tokens) - width + 1))
    )


_EXPLICIT = {
    "sim confirmo exatamente esse resumo",
    "confirmo exatamente esse resumo",
    "confirmo esse resumo",
    "sim confirmo",
    "yes i confirm this exact summary",
    "i confirm this exact summary",
    "yes i confirm this summary",
    "i confirm this summary",
}
_COLLOQUIAL = {
    "fechado pode seguir",
    "tudo certo pode seguir",
    "sounds good go ahead",
    "all good go ahead",
}
_CONTEXTUAL = {
    "pode fazer",
    "pode seguir",
    "go ahead",
    "you can do it",
}
_NEGATIVE = {
    "não confirme",
    "nao confirme",
    "não confirmo",
    "nao confirmo",
    "não reserve isso",
    "nao reserve isso",
    "não pode seguir",
    "nao pode seguir",
    "do not book it",
    "do not confirm",
    "cancel it",
}
_ADJUST = {
    "troque para cartão",
    "troque para cartao",
    "quero ajustar a data",
    "quero ajustar",
    "mude a data",
    "change it to card",
    "i want to adjust the date",
    "i want to adjust",
    "change the date",
}


@runtime_checkable
class ConfirmationClassifier(Protocol):
    def classify(self, item: ClassificationInput) -> DecisionCandidate: ...


def _candidate(
    decision: ConfirmationDecisionKind,
    evidence_code: str,
    *,
    confidence: int,
    classifier_id: str = "reference-confirmation",
    classifier_version: int = 1,
) -> DecisionCandidate:
    return DecisionCandidate(
        decision=decision,
        classifier_id=classifier_id,
        classifier_version=classifier_version,
        confidence_basis_points=confidence,
        evidence_codes=(evidence_code,),
    )


def _signals(item: ClassificationInput) -> tuple[set[str], str]:
    canonical = _canonical_phrase(item.text)
    tokens = tuple(canonical.split())
    signals: set[str] = set()

    negative_patterns = (
        ("não", "confirme"),
        ("nao", "confirme"),
        ("não", "confirmo"),
        ("nao", "confirmo"),
        ("não", "reserve"),
        ("nao", "reserve"),
        ("não", "pode", "seguir"),
        ("nao", "pode", "seguir"),
        ("do", "not"),
        ("not", "yet"),
        ("cancel",),
    )
    if canonical in _NEGATIVE or any(_contains(tokens, pattern) for pattern in negative_patterns):
        signals.add("reject")

    adjust_patterns = (
        ("troque",),
        ("ajustar",),
        ("mude",),
        ("change",),
        ("adjust",),
    )
    if canonical in _ADJUST or any(_contains(tokens, pattern) for pattern in adjust_patterns):
        signals.add("adjust")

    accept_kind = ""
    if canonical in _EXPLICIT:
        signals.add("accept")
        accept_kind = "explicit"
    elif canonical in _COLLOQUIAL:
        signals.add("accept")
        accept_kind = "colloquial"
    elif canonical in _CONTEXTUAL:
        signals.add("accept")
        accept_kind = "contextual"
    else:
        starts_positive = bool(tokens and tokens[0] in {"sim", "yes"})
        unnegated_confirm = any(
            token in {"confirmo", "confirm"}
            and (index == 0 or tokens[index - 1] not in {"não", "nao", "not"})
            for index, token in enumerate(tokens)
        )
        contextual_prefix = tokens[:2] in {
            ("pode", "fazer"),
            ("pode", "seguir"),
            ("go", "ahead"),
        }
        if starts_positive or unnegated_confirm or contextual_prefix:
            signals.add("accept")
            accept_kind = "explicit" if starts_positive or unnegated_confirm else "contextual"
    return signals, accept_kind


class ReferenceConfirmationClassifier:
    classifier_id = "reference-confirmation"
    classifier_version = 1

    def classify(self, item: ClassificationInput) -> DecisionCandidate:
        if type(item) is not ClassificationInput:
            raise ValueError("item must be an exact ClassificationInput")
        if item.context is None:
            return _candidate(
                ConfirmationDecisionKind.AMBIGUOUS,
                "context_missing",
                confidence=10_000,
            )
        signals, accept_kind = _signals(item)
        if len(signals) != 1:
            return _candidate(
                ConfirmationDecisionKind.AMBIGUOUS,
                "mixed_or_unknown",
                confidence=8_000,
            )
        signal = next(iter(signals))
        if signal == "adjust":
            return _candidate(
                ConfirmationDecisionKind.ADJUST,
                "adjust_explicit",
                confidence=10_000,
            )
        if signal == "reject":
            return _candidate(
                ConfirmationDecisionKind.REJECT,
                "reject_explicit",
                confidence=10_000,
            )
        return _candidate(
            ConfirmationDecisionKind.ACCEPT,
            f"accept_{accept_kind or 'contextual'}",
            confidence=10_000,
        )


def classify_safely(
    classifier: ConfirmationClassifier,
    item: ClassificationInput,
) -> DecisionCandidate:
    """Convert classifier failure or contract violation to deterministic ambiguity."""

    try:
        candidate = classifier.classify(item)
    except Exception:
        return _candidate(
            ConfirmationDecisionKind.AMBIGUOUS,
            "classifier_error",
            confidence=10_000,
            classifier_id="classifier-boundary",
        )
    if type(candidate) is not DecisionCandidate:
        return _candidate(
            ConfirmationDecisionKind.AMBIGUOUS,
            "classifier_invalid_result",
            confidence=10_000,
            classifier_id="classifier-boundary",
        )
    return candidate


__all__ = [
    "ConfirmationClassifier",
    "ReferenceConfirmationClassifier",
    "classify_safely",
]
