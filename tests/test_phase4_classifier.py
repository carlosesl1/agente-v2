from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import runtime_checkable
import unittest

from reservation_confirmation import (
    ClassificationContext,
    ClassificationInput,
    ConfirmationClassifier,
    DecisionCandidate,
    ReferenceConfirmationClassifier,
    SummaryLocale,
    classify_safely,
)
from reservation_domain import ConfirmationDecisionKind

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "phase4" / "confirmation-corpus.json"
UTC = timezone.utc
T0 = datetime(2027, 2, 1, 12, 0, tzinfo=UTC)


def context(locale: SummaryLocale = SummaryLocale.PT_BR) -> ClassificationContext:
    return ClassificationContext(
        workflow_id="workflow:classifier",
        summary_event_id="summary:classifier",
        draft_id="draft:classifier",
        draft_version=2,
        subject_signature="a" * 64,
        presented_at=T0,
        locale=locale,
        content_hash="b" * 64,
    )


def classification_input(
    text: str = "Sim, confirmo.",
    *,
    locale: SummaryLocale = SummaryLocale.PT_BR,
    has_context: bool = True,
) -> ClassificationInput:
    return ClassificationInput(
        source_event_id="source:classifier",
        received_at=T0 + timedelta(seconds=1),
        text=text,
        context=context(locale) if has_context else None,
    )


class RaisingClassifier:
    def classify(self, item: ClassificationInput) -> DecisionCandidate:
        raise RuntimeError("synthetic classifier failure")


class WrongTypeClassifier:
    def classify(self, item: ClassificationInput):
        return {"decision": "accept"}


class ClassifierTests(unittest.TestCase):
    def test_protocol_is_runtime_checkable_and_reference_conforms(self) -> None:
        self.assertTrue(getattr(ConfirmationClassifier, "_is_runtime_protocol", False))
        self.assertIsInstance(ReferenceConfirmationClassifier(), ConfirmationClassifier)
        self.assertIsInstance(RaisingClassifier(), ConfirmationClassifier)

    def test_complete_synthetic_corpus(self) -> None:
        payload = json.loads(CORPUS.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertIs(payload["synthetic"], True)
        cases = payload["cases"]
        self.assertGreaterEqual(len(cases), 24)
        self.assertEqual(len({item["case_id"] for item in cases}), len(cases))
        self.assertEqual({item["locale"] for item in cases}, {"pt_BR", "en"})
        self.assertEqual(
            {item["category"] for item in cases},
            {"explicit", "colloquial", "contextual", "negative", "adjust", "ambiguous"},
        )
        classifier = ReferenceConfirmationClassifier()
        for row in cases:
            locale = SummaryLocale(row["locale"])
            item = classification_input(
                row["text"],
                locale=locale,
                has_context=row["has_context"],
            )
            candidate = classifier.classify(item)
            with self.subTest(case_id=row["case_id"]):
                self.assertIs(
                    candidate.decision,
                    ConfirmationDecisionKind(row["decision"]),
                )
                self.assertEqual(candidate.classifier_id, "reference-confirmation")
                self.assertEqual(candidate.classifier_version, 1)
                self.assertGreater(candidate.confidence_basis_points, 0)
                if not row["has_context"]:
                    self.assertIn("context_missing", candidate.evidence_codes)
                elif row["category"] in {"explicit", "colloquial", "contextual"}:
                    self.assertIn(f"accept_{row['category']}", candidate.evidence_codes)
                elif row["category"] == "negative":
                    self.assertIn("reject_explicit", candidate.evidence_codes)
                elif row["category"] == "adjust":
                    self.assertIn("adjust_explicit", candidate.evidence_codes)
                else:
                    self.assertIn("mixed_or_unknown", candidate.evidence_codes)

    def test_nfkc_case_and_punctuation_variants_are_deterministic(self) -> None:
        classifier = ReferenceConfirmationClassifier()
        variants = (
            "  SIM, CONFIRMO EXATAMENTE ESSE RESUMO!!!  ",
            "Ｓｉｍ， confirmo exatamente esse resumo.",
            "Sim confirmo exatamente esse resumo",
        )
        decisions = {
            classifier.classify(classification_input(value)).decision
            for value in variants
        }
        self.assertEqual(decisions, {ConfirmationDecisionKind.ACCEPT})

    def test_negative_is_not_misread_as_acceptance(self) -> None:
        classifier = ReferenceConfirmationClassifier()
        for text in ("Não confirmo.", "Não, não pode seguir.", "Do not confirm."):
            with self.subTest(text=text):
                candidate = classifier.classify(classification_input(text))
                self.assertIsNot(candidate.decision, ConfirmationDecisionKind.ACCEPT)

    def test_mixed_signals_fail_closed(self) -> None:
        classifier = ReferenceConfirmationClassifier()
        for text in (
            "Sim, mas não confirme ainda.",
            "Yes, but do not book it yet.",
            "Pode seguir, mas troque para cartão.",
        ):
            with self.subTest(text=text):
                candidate = classifier.classify(classification_input(text))
                self.assertIs(candidate.decision, ConfirmationDecisionKind.AMBIGUOUS)
                self.assertIn("mixed_or_unknown", candidate.evidence_codes)

    def test_context_is_required_even_for_explicit_acceptance(self) -> None:
        candidate = ReferenceConfirmationClassifier().classify(
            classification_input("Sim, confirmo.", has_context=False)
        )
        self.assertIs(candidate.decision, ConfirmationDecisionKind.AMBIGUOUS)
        self.assertEqual(candidate.evidence_codes, ("context_missing",))

    def test_classify_safely_turns_exception_and_wrong_type_into_ambiguous(self) -> None:
        for classifier, code in (
            (RaisingClassifier(), "classifier_error"),
            (WrongTypeClassifier(), "classifier_invalid_result"),
        ):
            with self.subTest(classifier=type(classifier).__name__):
                candidate = classify_safely(classifier, classification_input())
                self.assertIs(candidate.decision, ConfirmationDecisionKind.AMBIGUOUS)
                self.assertIn(code, candidate.evidence_codes)
                self.assertNotEqual(candidate.classifier_id, "reference-confirmation")


if __name__ == "__main__":
    unittest.main()
