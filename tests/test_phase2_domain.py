from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from reservation_domain import (
    AddOn,
    AwaitingConfirmationState,
    ConfirmationDecisionKind,
    ConfirmationReceived,
    CustomerFacts,
    DraftAdjusted,
    DraftRequested,
    EconomicTerms,
    ExecutionCertainty,
    ExecutionFinished,
    ExecutionQueuedState,
    ExecutionStarted,
    ExecutingState,
    FailedNoEffectState,
    LookupEvidence,
    LookupRecorded,
    LookupStatus,
    ManualReviewRequested,
    ManualReviewState,
    Money,
    OfferChosen,
    OfferSnapshot,
    OfferedState,
    Party,
    ReadyToSummarizeState,
    ReservationCommand,
    SearchQuery,
    SearchingState,
    ServiceKind,
    StartSearch,
    SucceededState,
    SummaryRecorded,
    TransitionStatus,
    UncertainState,
    WorkflowPhase,
    build_commercial_draft,
    combine_execution_outcomes,
    new_workflow,
    reduce,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def query() -> SearchQuery:
    return SearchQuery(
        service=ServiceKind.LODGING,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 13),
        start_time=None,
        party=Party(adults=1, children=0),
    )


def evidence(*, expires_delta: int = 600) -> LookupEvidence:
    return LookupEvidence(
        lookup_id="lookup-alpha",
        service=ServiceKind.LODGING,
        query_signature=query().signature,
        observed_at=T0 + timedelta(seconds=5),
        expires_at=T0 + timedelta(seconds=expires_delta),
        snapshot_hash="a" * 64,
        status=LookupStatus.POSITIVE,
    )


def offer(*, public_label: str = "Compartilhado número 2") -> OfferSnapshot:
    return OfferSnapshot(
        offer_id="offer-alpha",
        lookup_id="lookup-alpha",
        service=ServiceKind.LODGING,
        provider_ref="provider-room-alpha",
        public_label=public_label,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 13),
        start_time=None,
        party=Party(adults=1, children=0),
        total=Money(amount=Decimal("300.00"), currency="BRL"),
        available=True,
    )


def terms(*, payment_method: str = "card") -> EconomicTerms:
    return EconomicTerms(
        payment_method=payment_method,
        add_ons=(
            AddOn(
                code="breakfast",
                quantity=3,
                unit_price=Money(amount=Decimal("30.00"), currency="BRL"),
            ),
        ),
    )


def customer(*, full_name: str = "Synthetic Person") -> CustomerFacts:
    return CustomerFacts(
        customer_ref="customer-alpha",
        full_name=full_name,
        email="synthetic.person" + chr(64) + "example.invalid",
        phone_e164="+99900000000",
        country_code="ZZ",
    )


def event_time(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def reach_ready_state():
    state = new_workflow(workflow_id="workflow-alpha", started_at=T0)
    state = reduce(
        state,
        StartSearch(event_id="event-search", occurred_at=event_time(1), query=query()),
    ).state
    state = reduce(
        state,
        LookupRecorded(
            event_id="event-lookup",
            occurred_at=event_time(6),
            evidence=evidence(),
            offers=(offer(),),
        ),
    ).state
    state = reduce(
        state,
        OfferChosen(
            event_id="event-choice",
            occurred_at=event_time(7),
            offer_id="offer-alpha",
        ),
    ).state
    transition = reduce(
        state,
        DraftRequested(
            event_id="event-draft",
            occurred_at=event_time(8),
            draft_id="draft-alpha",
            customer=customer(),
            terms=terms(),
        ),
    )
    assert isinstance(transition.state, ReadyToSummarizeState)
    return transition.state


def reach_awaiting_state():
    state = reach_ready_state()
    transition = reduce(
        state,
        SummaryRecorded(
            event_id="event-summary",
            occurred_at=event_time(9),
            summary_event_id="summary-alpha",
            draft_version=state.draft.version,
            subject_signature=state.draft.subject_signature,
            outbox_message_id="outbox-summary-alpha",
        ),
    )
    assert isinstance(transition.state, AwaitingConfirmationState)
    return transition.state


def reach_queued_state():
    state = reach_awaiting_state()
    transition = reduce(
        state,
        ConfirmationReceived(
            event_id="event-confirm",
            occurred_at=event_time(10),
            confirmation_event_id="confirmation-alpha",
            decision=ConfirmationDecisionKind.ACCEPT,
            target_draft_version=state.draft.version,
            subject_signature=state.draft.subject_signature,
        ),
    )
    assert isinstance(transition.state, ExecutionQueuedState)
    return transition


class ReducerContractTests(unittest.TestCase):
    def test_valid_flow_emits_exactly_one_command_after_posterior_confirmation(self) -> None:
        transition = reach_queued_state()
        self.assertEqual(transition.status, TransitionStatus.APPLIED)
        self.assertEqual(len(transition.commands), 1)
        command = transition.commands[0]
        self.assertIsInstance(command, ReservationCommand)
        self.assertEqual(command.draft_version, transition.state.draft.version)
        self.assertEqual(
            command.subject_signature,
            transition.state.summary.subject_signature,
        )

    def test_summary_without_confirmation_emits_no_command(self) -> None:
        state = reach_awaiting_state()
        self.assertEqual(state.phase, WorkflowPhase.AWAITING_CONFIRMATION)
        self.assertFalse(state.command_ids)

    def test_confirmation_without_summary_is_safe(self) -> None:
        state = reach_ready_state()
        transition = reduce(
            state,
            ConfirmationReceived(
                event_id="event-early-confirm",
                occurred_at=event_time(9),
                confirmation_event_id="confirmation-early",
                decision=ConfirmationDecisionKind.ACCEPT,
                target_draft_version=state.draft.version,
                subject_signature=state.draft.subject_signature,
            ),
        )
        self.assertEqual(transition.status, TransitionStatus.IGNORED)
        self.assertFalse(transition.commands)
        self.assertIsInstance(transition.state, ReadyToSummarizeState)

    def test_confirmation_must_be_strictly_after_summary(self) -> None:
        state = reach_awaiting_state()
        transition = reduce(
            state,
            ConfirmationReceived(
                event_id="event-same-time-confirm",
                occurred_at=state.summary.presented_at,
                confirmation_event_id="confirmation-same-time",
                decision=ConfirmationDecisionKind.ACCEPT,
                target_draft_version=state.draft.version,
                subject_signature=state.draft.subject_signature,
            ),
        )
        self.assertEqual(transition.status, TransitionStatus.REJECTED)
        self.assertFalse(transition.commands)

    def test_mismatched_version_or_signature_never_emits_command(self) -> None:
        state = reach_awaiting_state()
        bad_version = reduce(
            state,
            ConfirmationReceived(
                event_id="event-wrong-version",
                occurred_at=event_time(10),
                confirmation_event_id="confirmation-wrong-version",
                decision=ConfirmationDecisionKind.ACCEPT,
                target_draft_version=state.draft.version + 1,
                subject_signature=state.draft.subject_signature,
            ),
        )
        self.assertEqual(bad_version.status, TransitionStatus.REJECTED)
        self.assertFalse(bad_version.commands)

        bad_signature = reduce(
            bad_version.state,
            ConfirmationReceived(
                event_id="event-wrong-signature",
                occurred_at=event_time(11),
                confirmation_event_id="confirmation-wrong-signature",
                decision=ConfirmationDecisionKind.ACCEPT,
                target_draft_version=state.draft.version,
                subject_signature="f" * 64,
            ),
        )
        self.assertEqual(bad_signature.status, TransitionStatus.REJECTED)
        self.assertFalse(bad_signature.commands)

    def test_duplicate_and_second_confirmation_do_not_reemit_command(self) -> None:
        transition = reach_queued_state()
        queued = transition.state
        duplicate_event = ConfirmationReceived(
            event_id="event-confirm",
            occurred_at=event_time(10),
            confirmation_event_id="confirmation-alpha",
            decision=ConfirmationDecisionKind.ACCEPT,
            target_draft_version=queued.draft.version,
            subject_signature=queued.draft.subject_signature,
        )
        duplicate = reduce(queued, duplicate_event)
        self.assertEqual(duplicate.status, TransitionStatus.IGNORED)
        self.assertEqual(duplicate.state, queued)
        self.assertFalse(duplicate.commands)

        second = reduce(
            queued,
            replace(
                duplicate_event,
                event_id="event-confirm-again",
                occurred_at=event_time(11),
                confirmation_event_id="confirmation-again",
            ),
        )
        self.assertEqual(second.status, TransitionStatus.IGNORED)
        self.assertFalse(second.commands)
        self.assertEqual(second.state.command_ids, queued.command_ids)

    def test_conflicting_payload_with_reused_event_id_is_rejected(self) -> None:
        state = new_workflow(workflow_id="workflow-conflict", started_at=T0)
        original = StartSearch(
            event_id="event-conflicting-duplicate",
            occurred_at=event_time(1),
            query=query(),
        )
        state = reduce(state, original).state
        conflict = replace(
            original,
            query=replace(query(), party=Party(adults=2, children=0)),
        )
        transition = reduce(state, conflict)
        self.assertEqual(transition.status, TransitionStatus.REJECTED)
        self.assertEqual(transition.reason, "conflicting_duplicate_event")
        self.assertEqual(transition.state, state)
        self.assertFalse(transition.commands)

    def test_stale_lookup_cannot_offer_or_select(self) -> None:
        state = new_workflow(workflow_id="workflow-stale", started_at=T0)
        state = reduce(
            state,
            StartSearch(event_id="stale-search", occurred_at=event_time(1), query=query()),
        ).state
        self.assertIsInstance(state, SearchingState)
        transition = reduce(
            state,
            LookupRecorded(
                event_id="stale-lookup",
                occurred_at=event_time(10),
                evidence=evidence(expires_delta=10),
                offers=(offer(),),
            ),
        )
        self.assertEqual(transition.status, TransitionStatus.REJECTED)
        self.assertIsInstance(transition.state, SearchingState)
        self.assertFalse(transition.commands)

    def test_activity_occurrence_must_fall_inside_query_window(self) -> None:
        activity_query = SearchQuery(
            service=ServiceKind.ACTIVITY,
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 17),
            start_time=None,
            party=Party(adults=1, children=0),
        )

        def lookup_transition(*, workflow: str, occurrence: date):
            state = new_workflow(workflow_id=workflow, started_at=T0)
            state = reduce(
                state,
                StartSearch(
                    event_id=f"{workflow}:search",
                    occurred_at=event_time(1),
                    query=activity_query,
                ),
            ).state
            return reduce(
                state,
                LookupRecorded(
                    event_id=f"{workflow}:lookup",
                    occurred_at=event_time(6),
                    evidence=LookupEvidence(
                        lookup_id=f"{workflow}:lookup-id",
                        service=ServiceKind.ACTIVITY,
                        query_signature=activity_query.signature,
                        observed_at=event_time(5),
                        expires_at=event_time(600),
                        snapshot_hash="b" * 64,
                        status=LookupStatus.POSITIVE,
                    ),
                    offers=(
                        OfferSnapshot(
                            offer_id=f"{workflow}:offer",
                            lookup_id=f"{workflow}:lookup-id",
                            service=ServiceKind.ACTIVITY,
                            provider_ref=f"{workflow}:provider-ref",
                            public_label="Synthetic activity occurrence",
                            start_date=occurrence,
                            end_date=None,
                            start_time="07:30",
                            party=activity_query.party,
                            total=Money(amount=Decimal("100.00"), currency="BRL"),
                            available=True,
                        ),
                    ),
                ),
            )

        inside = lookup_transition(
            workflow="workflow-activity-inside",
            occurrence=date(2026, 9, 14),
        )
        self.assertEqual(inside.status, TransitionStatus.APPLIED)
        self.assertIsInstance(inside.state, OfferedState)

        outside = lookup_transition(
            workflow="workflow-activity-outside",
            occurrence=date(2026, 9, 18),
        )
        self.assertEqual(outside.status, TransitionStatus.REJECTED)
        self.assertIsInstance(outside.state, SearchingState)

    def test_offer_is_chosen_only_by_opaque_offer_id(self) -> None:
        state = new_workflow(workflow_id="workflow-choice", started_at=T0)
        state = reduce(
            state,
            StartSearch(event_id="choice-search", occurred_at=event_time(1), query=query()),
        ).state
        state = reduce(
            state,
            LookupRecorded(
                event_id="choice-lookup",
                occurred_at=event_time(6),
                evidence=evidence(),
                offers=(
                    offer(public_label="Mesmo nome público"),
                    replace(
                        offer(public_label="Mesmo nome público"),
                        offer_id="offer-beta",
                        provider_ref="provider-room-beta",
                    ),
                ),
            ),
        ).state
        self.assertIsInstance(state, OfferedState)
        selected = reduce(
            state,
            OfferChosen(
                event_id="choice-event",
                occurred_at=event_time(7),
                offer_id="offer-beta",
            ),
        ).state
        self.assertEqual(selected.offer.offer_id, "offer-beta")

    def test_out_of_order_event_is_rejected_without_command(self) -> None:
        state = reach_awaiting_state()
        transition = reduce(
            state,
            DraftAdjusted(
                event_id="late-adjustment",
                occurred_at=event_time(2),
                customer=customer(),
                terms=terms(payment_method="cash"),
            ),
        )
        self.assertEqual(transition.status, TransitionStatus.REJECTED)
        self.assertFalse(transition.commands)
        self.assertIsInstance(transition.state, AwaitingConfirmationState)

    def test_called_unknown_is_monotonic_and_requires_manual_review(self) -> None:
        queued = reach_queued_state().state
        executing = reduce(
            queued,
            ExecutionStarted(
                event_id="execution-started",
                occurred_at=event_time(11),
                command_id=queued.command.command_id,
            ),
        ).state
        self.assertIsInstance(executing, ExecutingState)
        combined = combine_execution_outcomes(
            (
                ExecutionCertainty.EFFECT_CONFIRMED,
                ExecutionCertainty.CALLED_UNKNOWN,
                ExecutionCertainty.NOT_CALLED,
            )
        )
        self.assertEqual(combined, ExecutionCertainty.CALLED_UNKNOWN)
        outcome = executing.command.outcome(
            certainty=combined,
            normalized_status="provider_result_unknown",
        )
        uncertain = reduce(
            executing,
            ExecutionFinished(
                event_id="execution-finished",
                occurred_at=event_time(12),
                command_id=executing.command.command_id,
                outcome=outcome,
            ),
        ).state
        self.assertIsInstance(uncertain, UncertainState)
        reviewed = reduce(
            uncertain,
            ManualReviewRequested(
                event_id="manual-review",
                occurred_at=event_time(13),
                reason="reconciliation_required",
            ),
        ).state
        self.assertIsInstance(reviewed, ManualReviewState)

    def test_called_without_effect_has_distinct_typed_state(self) -> None:
        queued = reach_queued_state().state
        executing = reduce(
            queued,
            ExecutionStarted(
                event_id="execution-start-no-effect",
                occurred_at=event_time(11),
                command_id=queued.command.command_id,
            ),
        ).state
        outcome = executing.command.outcome(
            certainty=ExecutionCertainty.CALLED_NO_EFFECT,
            normalized_status="called_without_effect",
        )
        failed = reduce(
            executing,
            ExecutionFinished(
                event_id="execution-no-effect",
                occurred_at=event_time(12),
                command_id=executing.command.command_id,
                outcome=outcome,
            ),
        ).state
        self.assertIsInstance(failed, FailedNoEffectState)

    def test_confirmed_execution_reaches_succeeded(self) -> None:
        queued = reach_queued_state().state
        executing = reduce(
            queued,
            ExecutionStarted(
                event_id="execution-start-success",
                occurred_at=event_time(11),
                command_id=queued.command.command_id,
            ),
        ).state
        outcome = executing.command.outcome(
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
            normalized_status="reservation_created",
            provider_reference="reservation-alpha",
        )
        succeeded = reduce(
            executing,
            ExecutionFinished(
                event_id="execution-success",
                occurred_at=event_time(12),
                command_id=executing.command.command_id,
                outcome=outcome,
            ),
        ).state
        self.assertIsInstance(succeeded, SucceededState)


class ValueObjectContractTests(unittest.TestCase):
    def test_non_finite_money_and_runtime_type_confusion_fail_closed(self) -> None:
        for raw in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    Money(amount=Decimal(raw), currency="BRL")
        with self.assertRaises(ValueError):
            replace(query(), service="lodging")
        with self.assertRaises(ValueError):
            replace(offer(), available="yes")


class SignatureContractTests(unittest.TestCase):
    def draft(self, *, selected_offer=None, selected_customer=None, selected_terms=None):
        return build_commercial_draft(
            draft_id="draft-signature",
            version=1,
            created_at=T0,
            components=(selected_offer or offer(),),
            customer=selected_customer or customer(),
            terms=selected_terms or terms(),
        )

    def test_public_label_provenance_and_input_order_do_not_change_signature(self) -> None:
        second_add_on = AddOn(
            code="equipment",
            quantity=1,
            unit_price=Money(amount=Decimal("20.00"), currency="BRL"),
        )
        two_terms = EconomicTerms(
            payment_method="card",
            add_ons=(*terms().add_ons, second_add_on),
        )
        activity = OfferSnapshot(
            offer_id="offer-activity",
            lookup_id="lookup-activity",
            service=ServiceKind.ACTIVITY,
            provider_ref="provider-activity",
            public_label="Passeio sintético",
            start_date=date(2026, 9, 12),
            end_date=None,
            start_time="08:00",
            party=Party(adults=1, children=0),
            total=Money(amount=Decimal("200.00"), currency="BRL"),
            available=True,
        )
        original = build_commercial_draft(
            draft_id="draft-ordering",
            version=1,
            created_at=T0,
            components=(offer(), activity),
            customer=customer(),
            terms=two_terms,
        )
        reordered = build_commercial_draft(
            draft_id="draft-ordering",
            version=1,
            created_at=T0,
            components=(
                activity,
                replace(
                    offer(),
                    public_label="Outro texto público",
                    lookup_id="lookup-new-provenance",
                ),
            ),
            customer=customer(),
            terms=EconomicTerms(
                payment_method="card",
                add_ons=tuple(reversed(two_terms.add_ons)),
            ),
        )
        self.assertEqual(original.subject_signature, reordered.subject_signature)

    def test_every_execution_relevant_mutation_changes_signature(self) -> None:
        base_offer = offer()
        base_terms = terms()
        original = self.draft().subject_signature
        mutations = (
            replace(base_offer, offer_id="offer-beta"),
            replace(base_offer, provider_ref="provider-room-beta"),
            replace(base_offer, start_date=date(2026, 9, 11)),
            replace(base_offer, end_date=date(2026, 9, 14)),
            replace(base_offer, start_time="08:00"),
            replace(base_offer, party=Party(adults=2, children=0)),
            replace(base_offer, party=Party(adults=1, children=1)),
            replace(
                base_offer,
                total=Money(amount=Decimal("301.00"), currency="BRL"),
            ),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                self.assertNotEqual(
                    original,
                    self.draft(selected_offer=mutated).subject_signature,
                )
        for invalid_offer in (
            replace(base_offer, available=False),
            replace(
                base_offer,
                total=Money(amount=Decimal("300.00"), currency="USD"),
            ),
        ):
            with self.subTest(invalid_offer=invalid_offer):
                with self.assertRaises(ValueError):
                    self.draft(selected_offer=invalid_offer)
        self.assertNotEqual(
            original,
            self.draft(selected_terms=replace(base_terms, payment_method="cash")).subject_signature,
        )
        base_customer = customer()
        customer_mutations = (
            replace(base_customer, customer_ref="customer-beta"),
            replace(base_customer, full_name="Another Synthetic Person"),
            replace(
                base_customer,
                email="another.synthetic" + chr(64) + "example.invalid",
            ),
            replace(base_customer, phone_e164="+99900000001"),
            replace(base_customer, country_code="XY"),
        )
        for changed_customer in customer_mutations:
            with self.subTest(changed_customer=changed_customer):
                self.assertNotEqual(
                    original,
                    self.draft(selected_customer=changed_customer).subject_signature,
                )
        for changed_add_on in (
            replace(base_terms.add_ons[0], code="equipment"),
            replace(base_terms.add_ons[0], quantity=4),
            replace(
                base_terms.add_ons[0],
                unit_price=Money(amount=Decimal("31.00"), currency="BRL"),
            ),
        ):
            with self.subTest(changed_add_on=changed_add_on):
                self.assertNotEqual(
                    original,
                    self.draft(
                        selected_terms=EconomicTerms(
                            payment_method="card",
                            add_ons=(changed_add_on,),
                        )
                    ).subject_signature,
                )


if __name__ == "__main__":
    unittest.main()
