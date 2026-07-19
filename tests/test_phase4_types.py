from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from reservation_confirmation import (
    BoundConfirmation,
    ClassificationContext,
    ClassificationInput,
    DecisionCandidate,
    PreparedSummary,
    RenderedSummary,
    SummaryLocale,
)
from reservation_domain import (
    ConfirmationDecisionKind,
    ConfirmationReceived,
    SummaryRecorded,
)

UTC = timezone.utc
T0 = datetime(2027, 1, 1, 12, 0, tzinfo=UTC)
SIGNATURE = "a" * 64


def rendered_hash(*, content: str, locale: SummaryLocale = SummaryLocale.PT_BR) -> str:
    payload = {
        "content": content,
        "draft_id": "draft:alpha",
        "draft_version": 1,
        "locale": locale.value,
        "renderer_id": "summary-renderer",
        "renderer_version": 1,
        "subject_signature": SIGNATURE,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def valid_rendered() -> RenderedSummary:
    content = "Resumo sintético. Nenhuma reserva foi criada."
    return RenderedSummary(
        renderer_id="summary-renderer",
        renderer_version=1,
        locale=SummaryLocale.PT_BR,
        draft_id="draft:alpha",
        draft_version=1,
        subject_signature=SIGNATURE,
        content=content,
        content_hash=rendered_hash(content=content),
        claim_status="none",
        private_fields=(),
    )


def valid_context() -> ClassificationContext:
    return ClassificationContext(
        workflow_id="workflow:alpha",
        summary_event_id="summary:alpha",
        draft_id="draft:alpha",
        draft_version=1,
        subject_signature=SIGNATURE,
        presented_at=T0,
        locale=SummaryLocale.PT_BR,
        content_hash=valid_rendered().content_hash,
    )


def valid_input() -> ClassificationInput:
    return ClassificationInput(
        source_event_id="source:alpha",
        received_at=T0 + timedelta(seconds=1),
        text="Sim, confirmo.",
        context=valid_context(),
    )


def valid_candidate() -> DecisionCandidate:
    return DecisionCandidate(
        decision=ConfirmationDecisionKind.ACCEPT,
        classifier_id="reference-confirmation",
        classifier_version=1,
        confidence_basis_points=10_000,
        evidence_codes=("accept_explicit",),
    )


class Phase4TypeTests(unittest.TestCase):
    def test_candidate_has_no_commercial_target_fields(self) -> None:
        self.assertEqual(
            {field.name for field in fields(DecisionCandidate)},
            {
                "decision",
                "classifier_id",
                "classifier_version",
                "confidence_basis_points",
                "evidence_codes",
            },
        )

    def test_summary_locale_is_closed(self) -> None:
        self.assertEqual(
            tuple(item.value for item in SummaryLocale),
            ("pt_BR", "en"),
        )

    def test_rendered_summary_recomputes_content_hash(self) -> None:
        rendered = valid_rendered()
        self.assertEqual(rendered.content_hash, rendered_hash(content=rendered.content))
        with self.assertRaisesRegex(ValueError, "content_hash"):
            RenderedSummary(
                renderer_id=rendered.renderer_id,
                renderer_version=rendered.renderer_version,
                locale=rendered.locale,
                draft_id=rendered.draft_id,
                draft_version=rendered.draft_version,
                subject_signature=rendered.subject_signature,
                content=rendered.content,
                content_hash="b" * 64,
                claim_status="none",
                private_fields=(),
            )

    def test_rendered_summary_rejects_claims_private_fields_and_wrong_exact_types(self) -> None:
        rendered = valid_rendered()
        for changes in (
            {"claim_status": "confirmed"},
            {"private_fields": ("provider_ref",)},
            {"renderer_version": True},
            {"draft_version": True},
            {"locale": "pt_BR"},
            {"content": "   "},
        ):
            payload = {
                field.name: getattr(rendered, field.name)
                for field in fields(RenderedSummary)
            }
            payload.update(changes)
            if "content" in changes:
                payload["content_hash"] = rendered_hash(content=changes["content"])
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                RenderedSummary(**payload)

    def test_prepared_summary_binds_rendered_artifact_to_domain_event(self) -> None:
        rendered = valid_rendered()
        event = SummaryRecorded(
            event_id="event:summary:alpha",
            occurred_at=T0,
            summary_event_id="summary:alpha",
            draft_version=rendered.draft_version,
            subject_signature=rendered.subject_signature,
            outbox_message_id="outbox:alpha",
        )
        prepared = PreparedSummary(
            rendered=rendered,
            summary_event_id=event.summary_event_id,
            outbox_message_id=event.outbox_message_id,
            presented_at=T0,
            event=event,
        )
        self.assertEqual(prepared.event, event)

        mismatched = SummaryRecorded(
            event_id="event:summary:beta",
            occurred_at=T0,
            summary_event_id="summary:beta",
            draft_version=rendered.draft_version,
            subject_signature=rendered.subject_signature,
            outbox_message_id="outbox:alpha",
        )
        with self.assertRaisesRegex(ValueError, "bind"):
            PreparedSummary(
                rendered=rendered,
                summary_event_id="summary:alpha",
                outbox_message_id="outbox:alpha",
                presented_at=T0,
                event=mismatched,
            )

    def test_classification_context_rejects_noncanonical_binding(self) -> None:
        context = valid_context()
        self.assertEqual(context.presented_at, T0)
        payload = {
            field.name: getattr(context, field.name)
            for field in fields(ClassificationContext)
        }
        for key, value in (
            ("draft_version", True),
            ("locale", "pt_BR"),
            ("presented_at", datetime(2027, 1, 1, 12, 0)),
            ("content_hash", "x" * 64),
        ):
            mutated = dict(payload)
            mutated[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                ClassificationContext(**mutated)

    def test_classification_input_normalizes_text_and_requires_utc(self) -> None:
        item = ClassificationInput(
            source_event_id="source:alpha",
            received_at=T0 + timedelta(seconds=1),
            text="  Sim,\u00a0 confirmo.  ",
            context=valid_context(),
        )
        self.assertEqual(item.text, "Sim, confirmo.")
        for received_at, text in (
            (datetime(2027, 1, 1, 12, 0), "Sim"),
            (T0, "   "),
            (T0, "sim\x00"),
        ):
            with self.subTest(received_at=received_at, text=text), self.assertRaises(ValueError):
                ClassificationInput(
                    source_event_id="source:alpha",
                    received_at=received_at,
                    text=text,
                    context=None,
                )

    def test_candidate_requires_exact_closed_values(self) -> None:
        candidate = valid_candidate()
        self.assertEqual(candidate.evidence_codes, ("accept_explicit",))
        for changes in (
            {"decision": "accept"},
            {"classifier_version": True},
            {"confidence_basis_points": True},
            {"confidence_basis_points": 10_001},
            {"evidence_codes": ("accept_explicit", "accept_explicit")},
            {"evidence_codes": ("UPPER",)},
        ):
            payload = {
                field.name: getattr(candidate, field.name)
                for field in fields(DecisionCandidate)
            }
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                DecisionCandidate(**payload)

    def test_bound_confirmation_binds_candidate_and_event_or_is_eventless(self) -> None:
        item = valid_input()
        candidate = valid_candidate()
        event = ConfirmationReceived(
            event_id="event:confirmation:alpha",
            occurred_at=item.received_at,
            confirmation_event_id="confirmation:alpha",
            decision=candidate.decision,
            target_draft_version=item.context.draft_version,
            subject_signature=item.context.subject_signature,
        )
        bound = BoundConfirmation(
            input=item,
            candidate=candidate,
            confirmation_event_id=event.confirmation_event_id,
            event=event,
        )
        self.assertEqual(bound.event, event)

        eventless = BoundConfirmation(
            input=ClassificationInput(
                source_event_id="source:none",
                received_at=T0,
                text="Pode fazer.",
                context=None,
            ),
            candidate=DecisionCandidate(
                decision=ConfirmationDecisionKind.AMBIGUOUS,
                classifier_id="boundary",
                classifier_version=1,
                confidence_basis_points=10_000,
                evidence_codes=("context_missing",),
            ),
            confirmation_event_id=None,
            event=None,
        )
        self.assertIsNone(eventless.event)

        with self.assertRaisesRegex(ValueError, "decision"):
            BoundConfirmation(
                input=item,
                candidate=candidate,
                confirmation_event_id="confirmation:wrong",
                event=ConfirmationReceived(
                    event_id="event:confirmation:wrong",
                    occurred_at=item.received_at,
                    confirmation_event_id="confirmation:wrong",
                    decision=ConfirmationDecisionKind.REJECT,
                    target_draft_version=item.context.draft_version,
                    subject_signature=item.context.subject_signature,
                ),
            )


if __name__ == "__main__":
    unittest.main()
