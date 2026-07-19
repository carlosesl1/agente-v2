from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import inspect
import unittest

from reservation_confirmation import (
    ReferenceConfirmationClassifier,
    SummaryLocale,
    classification_context,
    classify_and_bind,
    prepare_summary,
)
from reservation_domain import (
    AwaitingAdjustmentState,
    AwaitingConfirmationState,
    CancelledState,
    ConfirmationDecisionKind,
    DraftRequested,
    EconomicTerms,
    ExecutionQueuedState,
    LookupRecorded,
    OfferChosen,
    ReadyToSummarizeState,
    StartSearch,
    TransitionStatus,
    CustomerFacts,
    new_workflow,
    reduce,
)
from tests.test_phase3_bokun_adapter import (
    T0 as BOKUN_T0,
    adapter_for as bokun_adapter_for,
    lookup_request as bokun_lookup_request,
)
from tests.test_phase3_cloudbeds_adapter import (
    T0 as CLOUDBEDS_T0,
    adapter_for as cloudbeds_adapter_for,
    lookup_request as cloudbeds_lookup_request,
)
from tests.test_phase4_classifier import RaisingClassifier


def reach_awaiting(provider: str, locale: SummaryLocale):
    if provider == "cloudbeds":
        observed_at = CLOUDBEDS_T0
        adapter, _ = cloudbeds_adapter_for()
        lookup = adapter.lookup(
            cloudbeds_lookup_request(),
            observed_at=observed_at,
            ttl=timedelta(minutes=5),
        )
    elif provider == "bokun":
        observed_at = BOKUN_T0
        adapter, _ = bokun_adapter_for()
        lookup = adapter.lookup(
            bokun_lookup_request(),
            observed_at=observed_at,
            ttl=timedelta(minutes=5),
        )
    else:
        raise ValueError(provider)

    state = new_workflow(
        workflow_id=f"workflow:replay:{provider}:{locale.value}",
        started_at=observed_at - timedelta(seconds=1),
    )
    state = reduce(
        state,
        StartSearch(
            event_id=f"event:replay:{provider}:search",
            occurred_at=observed_at,
            query=lookup.query,
        ),
    ).state
    state = reduce(
        state,
        LookupRecorded(
            event_id=f"event:replay:{provider}:lookup",
            occurred_at=observed_at + timedelta(seconds=1),
            evidence=lookup.evidence,
            offers=lookup.offers,
        ),
    ).state
    state = reduce(
        state,
        OfferChosen(
            event_id=f"event:replay:{provider}:choice",
            occurred_at=observed_at + timedelta(seconds=2),
            offer_id=lookup.offers[0].offer_id,
        ),
    ).state
    state = reduce(
        state,
        DraftRequested(
            event_id=f"event:replay:{provider}:draft",
            occurred_at=observed_at + timedelta(seconds=3),
            draft_id=f"draft:replay:{provider}",
            customer=CustomerFacts(
                customer_ref=f"customer:replay:{provider}",
                full_name="Synthetic Replay Person",
                email=f"synthetic.replay.{provider}@example.invalid",
                phone_e164="+99900000002",
                country_code="ZZ",
            ),
            terms=EconomicTerms(payment_method="card"),
        ),
    ).state
    assert isinstance(state, ReadyToSummarizeState)
    prepared = prepare_summary(
        state,
        locale=locale,
        presented_at=observed_at + timedelta(seconds=4),
    )
    state = reduce(state, prepared.event).state
    assert isinstance(state, AwaitingConfirmationState)
    return state, prepared


class BindingReplayTests(unittest.TestCase):
    def test_public_binding_api_has_no_commercial_target_arguments(self) -> None:
        params = set(inspect.signature(classify_and_bind).parameters)
        self.assertNotIn("target_draft_version", params)
        self.assertNotIn("subject_signature", params)
        self.assertNotIn("offer_id", params)
        self.assertNotIn("provider_ref", params)
        self.assertNotIn("operation", params)

    def test_context_is_recomputed_from_exact_persisted_summary_artifact(self) -> None:
        awaiting, prepared = reach_awaiting("cloudbeds", SummaryLocale.PT_BR)
        context = classification_context(
            awaiting,
            locale=SummaryLocale.PT_BR,
            content_hash=prepared.rendered.content_hash,
        )
        self.assertEqual(context.draft_version, awaiting.draft.version)
        self.assertEqual(context.subject_signature, awaiting.draft.subject_signature)
        self.assertEqual(context.summary_event_id, awaiting.summary.summary_event_id)
        with self.assertRaisesRegex(ValueError, "hash"):
            classification_context(
                awaiting,
                locale=SummaryLocale.PT_BR,
                content_hash="f" * 64,
            )
        with self.assertRaisesRegex(ValueError, "artifact"):
            classification_context(
                awaiting,
                locale=SummaryLocale.EN,
                content_hash=prepared.rendered.content_hash,
            )

    def test_valid_contextual_acceptance_creates_one_command_for_both_providers(self) -> None:
        for provider, locale, text in (
            ("cloudbeds", SummaryLocale.PT_BR, "Pode fazer."),
            ("bokun", SummaryLocale.EN, "Go ahead."),
        ):
            awaiting, prepared = reach_awaiting(provider, locale)
            bound = classify_and_bind(
                awaiting,
                source_event_id=f"source:accept:{provider}",
                received_at=awaiting.summary.presented_at + timedelta(seconds=1),
                text=text,
                locale=locale,
                content_hash=prepared.rendered.content_hash,
                classifier=ReferenceConfirmationClassifier(),
            )
            with self.subTest(provider=provider):
                self.assertIsNotNone(bound.event)
                self.assertEqual(
                    bound.event.target_draft_version,
                    awaiting.draft.version,
                )
                self.assertEqual(
                    bound.event.subject_signature,
                    awaiting.draft.subject_signature,
                )
                transition = reduce(awaiting, bound.event)
                self.assertIsInstance(transition.state, ExecutionQueuedState)
                self.assertEqual(len(transition.commands), 1)

    def test_missing_state_wrong_hash_same_time_and_classifier_error_are_eventless(self) -> None:
        awaiting, prepared = reach_awaiting("cloudbeds", SummaryLocale.PT_BR)
        cases = (
            {
                "state": None,
                "received_at": awaiting.summary.presented_at + timedelta(seconds=1),
                "content_hash": None,
                "classifier": ReferenceConfirmationClassifier(),
                "code": "context_missing",
            },
            {
                "state": awaiting,
                "received_at": awaiting.summary.presented_at + timedelta(seconds=1),
                "content_hash": "f" * 64,
                "classifier": ReferenceConfirmationClassifier(),
                "code": "content_hash_mismatch",
            },
            {
                "state": awaiting,
                "received_at": awaiting.summary.presented_at,
                "content_hash": prepared.rendered.content_hash,
                "classifier": ReferenceConfirmationClassifier(),
                "code": "confirmation_not_posterior",
            },
            {
                "state": awaiting,
                "received_at": awaiting.summary.presented_at + timedelta(seconds=1),
                "content_hash": prepared.rendered.content_hash,
                "classifier": RaisingClassifier(),
                "code": "classifier_error",
            },
        )
        for index, case in enumerate(cases):
            bound = classify_and_bind(
                case["state"],
                source_event_id=f"source:eventless:{index}",
                received_at=case["received_at"],
                text="Pode fazer.",
                locale=SummaryLocale.PT_BR,
                content_hash=case["content_hash"],
                classifier=case["classifier"],
            )
            with self.subTest(code=case["code"]):
                self.assertIsNone(bound.event)
                self.assertIsNone(bound.confirmation_event_id)
                self.assertIs(
                    bound.candidate.decision,
                    ConfirmationDecisionKind.AMBIGUOUS,
                )
                self.assertIn(case["code"], bound.candidate.evidence_codes)

    def test_negative_ambiguous_and_adjust_emit_zero_commands(self) -> None:
        cases = (
            ("Não confirme.", CancelledState, ConfirmationDecisionKind.REJECT),
            ("Vou pensar e aviso.", AwaitingConfirmationState, ConfirmationDecisionKind.AMBIGUOUS),
            ("Troque para cartão.", AwaitingAdjustmentState, ConfirmationDecisionKind.ADJUST),
        )
        for index, (text, expected_state, decision) in enumerate(cases):
            awaiting, prepared = reach_awaiting("cloudbeds", SummaryLocale.PT_BR)
            bound = classify_and_bind(
                awaiting,
                source_event_id=f"source:nonaccept:{index}",
                received_at=awaiting.summary.presented_at + timedelta(seconds=1),
                text=text,
                locale=SummaryLocale.PT_BR,
                content_hash=prepared.rendered.content_hash,
                classifier=ReferenceConfirmationClassifier(),
            )
            self.assertIsNotNone(bound.event)
            self.assertIs(bound.candidate.decision, decision)
            transition = reduce(awaiting, bound.event)
            with self.subTest(text=text):
                self.assertIsInstance(transition.state, expected_state)
                self.assertEqual(transition.commands, ())

    def test_duplicate_source_event_never_reemits_command(self) -> None:
        awaiting, prepared = reach_awaiting("cloudbeds", SummaryLocale.PT_BR)
        bound = classify_and_bind(
            awaiting,
            source_event_id="source:duplicate",
            received_at=awaiting.summary.presented_at + timedelta(seconds=1),
            text="Sim, confirmo exatamente esse resumo.",
            locale=SummaryLocale.PT_BR,
            content_hash=prepared.rendered.content_hash,
            classifier=ReferenceConfirmationClassifier(),
        )
        first = reduce(awaiting, bound.event)
        self.assertEqual(len(first.commands), 1)
        duplicate = reduce(first.state, bound.event)
        self.assertEqual(duplicate.status, TransitionStatus.IGNORED)
        self.assertEqual(duplicate.reason, "duplicate_event")
        self.assertEqual(duplicate.commands, ())

    def test_same_source_event_with_different_decision_is_conflicting_duplicate(self) -> None:
        awaiting, prepared = reach_awaiting("cloudbeds", SummaryLocale.PT_BR)
        ambiguous = classify_and_bind(
            awaiting,
            source_event_id="source:conflict",
            received_at=awaiting.summary.presented_at + timedelta(seconds=1),
            text="Vou pensar e aviso.",
            locale=SummaryLocale.PT_BR,
            content_hash=prepared.rendered.content_hash,
            classifier=ReferenceConfirmationClassifier(),
        )
        first = reduce(awaiting, ambiguous.event)
        self.assertIsInstance(first.state, AwaitingConfirmationState)
        accepted = classify_and_bind(
            first.state,
            source_event_id="source:conflict",
            received_at=awaiting.summary.presented_at + timedelta(seconds=1),
            text="Pode fazer.",
            locale=SummaryLocale.PT_BR,
            content_hash=prepared.rendered.content_hash,
            classifier=ReferenceConfirmationClassifier(),
        )
        self.assertEqual(ambiguous.event.event_id, accepted.event.event_id)
        self.assertNotEqual(
            ambiguous.confirmation_event_id,
            accepted.confirmation_event_id,
        )
        conflict = reduce(first.state, accepted.event)
        self.assertEqual(conflict.status, TransitionStatus.REJECTED)
        self.assertEqual(conflict.reason, "conflicting_duplicate_event")
        self.assertEqual(conflict.commands, ())


if __name__ == "__main__":
    unittest.main()
