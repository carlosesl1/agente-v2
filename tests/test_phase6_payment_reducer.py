from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
import json
import unittest

from reservation_followup import (
    EffectRequirement,
    PaymentEffectPolicy,
    PaymentMethod,
    PaymentStatus,
    SettlementCertainty,
    from_wire_json,
    to_wire_json,
)
from reservation_followup.payment import (
    FinancialConfirmationReceived,
    FinancialSummaryRecorded,
    PaymentCancelled,
    PaymentEvidenceRecorded,
    PaymentExpired,
    PaymentMethodSelected,
    PaymentSettlementCommand,
    PaymentEventAction,
    PaymentTransition,
    PaymentTransitionReason,
    PaymentTransitionStatus,
    PaymentWorkflow,
    SettlementFinished,
    SettlementOperation,
    SettlementOutcome,
    SettlementStarted,
    financial_summary_hash,
    new_payment,
    payment_transition_matrix,
    reduce_payment,
)
from reservation_followup.projection import project_settlement_outcome
from reservation_followup.types import PaymentSubject
from tests.phase6_helpers import T0, confirmed_anchor
from tests.test_phase6_payment import (
    pix_evidence,
    stripe_event,
    trust_policy,
    wise_credit,
)


def payment_policy() -> PaymentEffectPolicy:
    return PaymentEffectPolicy(
        paid_state_transition=EffectRequirement.REQUIRED,
        customer_payment_confirmation=EffectRequirement.REQUIRED,
        internal_payment_email=EffectRequirement.DISABLED,
        booking_form=EffectRequirement.DISABLED,
    )


def initial_payment() -> PaymentWorkflow:
    return new_payment(confirmed_anchor(), payment_policy()).state


def selected_payment(method: PaymentMethod = PaymentMethod.PIX) -> PaymentWorkflow:
    state = initial_payment()
    return reduce_payment(
        state,
        PaymentMethodSelected(
            event_id="payment:event:method:synthetic:1",
            payment_id=state.subject.payment_id,
            method=method,
            selected_at=T0 + timedelta(seconds=1),
        ),
    ).state


def summarized_payment(
    method: PaymentMethod = PaymentMethod.PIX,
    *,
    subject: PaymentSubject | None = None,
) -> PaymentWorkflow:
    state = selected_payment(method)
    summary_subject = state.subject if subject is None else subject
    return reduce_payment(
        state,
        FinancialSummaryRecorded(
            event_id="payment:event:summary:synthetic:1",
            subject=summary_subject,
            summary_hash=financial_summary_hash(summary_subject),
            recorded_at=T0 + timedelta(seconds=2),
        ),
    ).state


def confirmed_payment(method: PaymentMethod = PaymentMethod.PIX) -> PaymentWorkflow:
    state = summarized_payment(method)
    assert state.summary is not None
    return reduce_payment(
        state,
        FinancialConfirmationReceived(
            event_id="payment:event:confirmation:synthetic:1",
            payment_id=state.subject.payment_id,
            payment_version=state.subject.payment_version,
            economic_signature=state.subject.economic_signature,
            summary_hash=state.summary.summary_hash,
            confirmation_id="payment:confirmation:synthetic:1",
            confirmed_at=T0 + timedelta(seconds=3),
        ),
    ).state


def evidence_recorded(state: PaymentWorkflow) -> PaymentEvidenceRecorded:
    return PaymentEvidenceRecorded(
        event_id="payment:event:evidence:synthetic:1",
        payment_id=state.subject.payment_id,
        payment_version=state.subject.payment_version,
        economic_signature=state.subject.economic_signature,
        evidence=pix_evidence(),
        trust=trust_policy(),
        recorded_at=T0 + timedelta(seconds=4),
    )


def queued_payment() -> tuple[PaymentWorkflow, PaymentSettlementCommand]:
    state = confirmed_payment()
    transition = reduce_payment(state, evidence_recorded(state))
    return transition.state, transition.commands[0]


def settlement_started(
    state: PaymentWorkflow,
    command: PaymentSettlementCommand,
) -> SettlementStarted:
    return SettlementStarted(
        event_id="payment:event:settlement-started:synthetic:1",
        payment_id=state.subject.payment_id,
        payment_version=state.subject.payment_version,
        economic_signature=state.subject.economic_signature,
        settlement_command_id=command.settlement_command_id,
        idempotency_key=command.idempotency_key,
        started_at=T0 + timedelta(seconds=5),
    )


def outcome(
    certainty: SettlementCertainty,
    **changes: object,
) -> SettlementOutcome:
    defaults: dict[SettlementCertainty, dict[str, object]] = {
        SettlementCertainty.NOT_DISPATCHED: {
            "payment_registered": False,
            "reservation_target_confirmed": False,
            "provider_reference_fingerprint": None,
            "requires_reconciliation": False,
            "claim_evidence": (),
        },
        SettlementCertainty.DISPATCHED_NO_EFFECT: {
            "payment_registered": False,
            "reservation_target_confirmed": False,
            "provider_reference_fingerprint": "d" * 64,
            "requires_reconciliation": True,
            "claim_evidence": ("e" * 64,),
        },
        SettlementCertainty.SETTLED: {
            "payment_registered": True,
            "reservation_target_confirmed": True,
            "provider_reference_fingerprint": "d" * 64,
            "requires_reconciliation": False,
            "claim_evidence": ("e" * 64,),
        },
        SettlementCertainty.PARTIAL_SETTLEMENT: {
            "payment_registered": True,
            "reservation_target_confirmed": False,
            "provider_reference_fingerprint": "d" * 64,
            "requires_reconciliation": True,
            "claim_evidence": ("e" * 64,),
        },
        SettlementCertainty.DISPATCHED_UNKNOWN: {
            "payment_registered": False,
            "reservation_target_confirmed": False,
            "provider_reference_fingerprint": None,
            "requires_reconciliation": True,
            "claim_evidence": ("e" * 64,),
        },
    }
    values = defaults[certainty] | changes
    return SettlementOutcome(certainty=certainty, **values)


def settlement_finished(
    state: PaymentWorkflow,
    command: PaymentSettlementCommand,
    result: SettlementOutcome,
    *,
    event_id: str = "payment:event:settlement-finished:synthetic:1",
) -> SettlementFinished:
    return SettlementFinished(
        event_id=event_id,
        payment_id=state.subject.payment_id,
        payment_version=state.subject.payment_version,
        economic_signature=state.subject.economic_signature,
        settlement_command_id=command.settlement_command_id,
        outcome=result,
        finished_at=T0 + timedelta(seconds=6),
    )


class Phase6PaymentReducerContractTests(unittest.TestCase):
    def test_closed_enums_dtos_and_transition_matrix_are_exact(self) -> None:
        self.assertEqual(
            tuple(member.value for member in SettlementOperation),
            ("register_and_confirm",),
        )
        self.assertEqual(
            tuple(member.value for member in PaymentTransitionStatus),
            ("applied", "noop", "rejected", "conflict"),
        )
        self.assertEqual(
            tuple(member.value for member in PaymentTransitionReason),
            (
                "payment_opened",
                "method_selected",
                "financial_summary_recorded",
                "financial_confirmation_recorded",
                "evidence_verified_and_queued",
                "settlement_started",
                "settlement_finished",
                "payment_expired",
                "payment_cancelled",
                "identical_replay",
                "event_not_applicable",
            ),
        )
        expected_fields = {
            PaymentMethodSelected: ("event_id", "payment_id", "method", "selected_at"),
            FinancialSummaryRecorded: (
                "event_id", "subject", "summary_hash", "recorded_at",
            ),
            FinancialConfirmationReceived: (
                "event_id", "payment_id", "payment_version", "economic_signature",
                "summary_hash", "confirmation_id", "confirmed_at",
            ),
            PaymentEvidenceRecorded: (
                "event_id", "payment_id", "payment_version", "economic_signature",
                "evidence", "trust", "recorded_at",
            ),
            SettlementStarted: (
                "event_id", "payment_id", "payment_version", "economic_signature",
                "settlement_command_id", "idempotency_key", "started_at",
            ),
            SettlementFinished: (
                "event_id", "payment_id", "payment_version", "economic_signature",
                "settlement_command_id", "outcome", "finished_at",
            ),
            PaymentExpired: (
                "event_id", "payment_id", "payment_version", "economic_signature",
                "expired_at",
            ),
            PaymentCancelled: (
                "event_id", "payment_id", "payment_version", "economic_signature",
                "cancellation_id", "cancelled_at",
            ),
            PaymentSettlementCommand: (
                "settlement_command_id", "payment_id", "payment_version",
                "economic_signature", "evidence_claim_key", "operation",
                "idempotency_key", "canonical_payload",
            ),
            SettlementOutcome: (
                "certainty", "payment_registered", "reservation_target_confirmed",
                "provider_reference_fingerprint", "requires_reconciliation",
                "claim_evidence",
            ),
            PaymentWorkflow: (
                "subject", "policy", "status", "summary", "confirmation",
                "evidence_record", "verified_evidence", "settlement_command",
                "settlement_start", "settlement_finish", "expiration", "cancellation",
                "history",
            ),
            PaymentTransition: ("state", "status", "reason", "events", "commands"),
        }
        for dto_type, names in expected_fields.items():
            with self.subTest(dto=dto_type.__name__):
                self.assertEqual(tuple(field.name for field in fields(dto_type)), names)
        self.assertEqual(
            payment_transition_matrix(),
            tuple(
                (status, event_type, action)
                for status, actions in (
                    (
                        PaymentStatus.AWAITING_METHOD,
                        (
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.HANDLE,
                        ),
                    ),
                    (
                        PaymentStatus.AWAITING_FINANCIAL_CONFIRMATION,
                        (
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.HANDLE,
                        ),
                    ),
                    (
                        PaymentStatus.AWAITING_EVIDENCE,
                        (
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.HANDLE,
                        ),
                    ),
                    (PaymentStatus.EVIDENCE_VERIFIED, (PaymentEventAction.REJECT,) * 8),
                    (
                        PaymentStatus.SETTLEMENT_QUEUED,
                        (
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                        ),
                    ),
                    (
                        PaymentStatus.SETTLING,
                        (
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REPLAY_ONLY,
                            PaymentEventAction.HANDLE,
                            PaymentEventAction.REJECT,
                            PaymentEventAction.REJECT,
                        ),
                    ),
                    (PaymentStatus.PAID, (PaymentEventAction.TERMINAL_NOOP,) * 8),
                    (PaymentStatus.RETRYABLE, (PaymentEventAction.REPLAY_ONLY,) * 8),
                    (PaymentStatus.MANUAL_REVIEW, (PaymentEventAction.TERMINAL_NOOP,) * 8),
                    (PaymentStatus.EXPIRED, (PaymentEventAction.TERMINAL_NOOP,) * 8),
                    (PaymentStatus.CANCELLED, (PaymentEventAction.TERMINAL_NOOP,) * 8),
                )
                for event_type, action in zip(
                    (
                        PaymentMethodSelected,
                        FinancialSummaryRecorded,
                        FinancialConfirmationReceived,
                        PaymentEvidenceRecorded,
                        SettlementStarted,
                        SettlementFinished,
                        PaymentExpired,
                        PaymentCancelled,
                    ),
                    actions,
                )
            ),
        )

    def test_contracts_are_frozen_slotted_and_reject_forged_verified_wrapper_input(self) -> None:
        state = confirmed_payment()
        event = evidence_recorded(state)
        values = (
            state,
            event,
            outcome(SettlementCertainty.SETTLED),
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, fields(value)[0].name, "changed")
        verified = reduce_payment(state, event).state.verified_evidence
        with self.assertRaises((TypeError, ValueError)):
            PaymentEvidenceRecorded(
                event_id="payment:event:evidence:synthetic:forged",
                payment_id=state.subject.payment_id,
                payment_version=state.subject.payment_version,
                economic_signature=state.subject.economic_signature,
                evidence=verified,
                trust=trust_policy(),
                recorded_at=T0 + timedelta(seconds=4),
            )

    def test_payment_events_command_outcome_and_workflow_have_bilateral_wire(self) -> None:
        initial = initial_payment()
        selected = selected_payment()
        summarized = summarized_payment()
        confirmed = confirmed_payment()
        evidence_event = evidence_recorded(confirmed)
        queued_transition = reduce_payment(confirmed, evidence_event)
        queued = queued_transition.state
        command = queued_transition.commands[0]
        started_event = settlement_started(queued, command)
        settling = reduce_payment(queued, started_event).state
        result = outcome(SettlementCertainty.SETTLED)
        finished_event = settlement_finished(settling, command, result)
        paid = reduce_payment(settling, finished_event).state
        values = (
            initial,
            selected.history[-1],
            summarized.summary,
            confirmed.confirmation,
            evidence_event,
            command,
            started_event,
            result,
            finished_event,
            paid,
        )
        for value in values:
            assert value is not None
            with self.subTest(dto=type(value).__name__):
                wire = to_wire_json(value)
                self.assertEqual(from_wire_json(wire, type(value)), value)

        impossible_projection = json.loads(to_wire_json(paid))
        impossible_projection["data"]["status"] = PaymentStatus.MANUAL_REVIEW.value
        with self.assertRaises(ValueError):
            from_wire_json(json.dumps(impossible_projection), PaymentWorkflow)

        reordered_history = json.loads(to_wire_json(paid))
        history = reordered_history["data"]["history"]
        history[-2], history[-1] = history[-1], history[-2]
        with self.assertRaises(ValueError):
            from_wire_json(json.dumps(reordered_history), PaymentWorkflow)

    def test_impossible_status_without_command_is_rejected_by_constructor(self) -> None:
        state = selected_payment()
        with self.assertRaises(ValueError):
            replace(state, status=PaymentStatus.SETTLEMENT_QUEUED)

    def test_mutated_settled_outcome_cannot_mark_payment_paid(self) -> None:
        queued, command = queued_payment()
        settling = reduce_payment(queued, settlement_started(queued, command)).state
        forged = outcome(SettlementCertainty.SETTLED)
        object.__setattr__(forged, "payment_registered", False)
        with self.assertRaises(ValueError):
            settlement_finished(settling, command, forged)
        with self.assertRaises(ValueError):
            project_settlement_outcome(forged, dispatch_fenced=True)

    def test_wire_reconstruction_rejects_paid_without_dispatch_fence(self) -> None:
        queued, command = queued_payment()
        finish = settlement_finished(
            queued,
            command,
            outcome(SettlementCertainty.SETTLED),
        )
        with self.assertRaises(ValueError):
            replace(
                queued,
                status=PaymentStatus.PAID,
                settlement_finish=finish,
                history=(*queued.history, finish),
            )

    def test_workflow_constructor_rejects_unreachable_status_and_hidden_terminal_history(self) -> None:
        selected = selected_payment()
        with self.assertRaises(ValueError):
            replace(selected, status=PaymentStatus.EVIDENCE_VERIFIED)

        queued, command = queued_payment()
        settling = reduce_payment(queued, settlement_started(queued, command)).state
        cancellation = PaymentCancelled(
            event_id="payment:event:cancelled:forged:after-fence",
            payment_id=settling.subject.payment_id,
            payment_version=settling.subject.payment_version,
            economic_signature=settling.subject.economic_signature,
            cancellation_id="payment:cancellation:forged:after-fence",
            cancelled_at=T0 + timedelta(seconds=6),
        )
        with self.assertRaises(ValueError):
            replace(
                settling,
                status=PaymentStatus.CANCELLED,
                cancellation=cancellation,
                history=(*settling.history, cancellation),
            )
        with self.assertRaises(ValueError):
            replace(queued, history=(*queued.history, cancellation))

    def test_command_payload_rejects_bool_integers_and_divergent_economics(self) -> None:
        _, command = queued_payment()
        base = json.loads(command.canonical_payload)
        mutations = []
        for field_name in ("schema_version", "payment_version", "amount_minor"):
            payload = dict(base)
            payload[field_name] = True
            mutations.append(payload)
        changed_amount = dict(base)
        changed_amount["amount_minor"] = base["amount_minor"] + 1
        mutations.append(changed_amount)
        changed_method = dict(base)
        changed_method["method"] = PaymentMethod.WISE.value
        mutations.append(changed_method)
        for payload in mutations:
            canonical = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                replace(command, canonical_payload=canonical)


class Phase6PaymentReducerTests(unittest.TestCase):
    def test_new_payment_is_anchor_bounded_and_awaits_method(self) -> None:
        transition = new_payment(confirmed_anchor(), payment_policy())
        self.assertIs(transition.state.status, PaymentStatus.AWAITING_METHOD)
        self.assertIsNone(transition.state.subject.method)
        self.assertEqual(transition.commands, ())
        self.assertIs(transition.reason, PaymentTransitionReason.PAYMENT_OPENED)
        with self.assertRaises((TypeError, ValueError)):
            new_payment(object(), payment_policy())

    def test_method_switch_does_not_change_economics_or_version_and_resets_confirmation(self) -> None:
        state = confirmed_payment(PaymentMethod.PIX)
        before = state.subject
        switched = reduce_payment(
            state,
            PaymentMethodSelected(
                event_id="payment:event:method:synthetic:2",
                payment_id=before.payment_id,
                method=PaymentMethod.WISE,
                selected_at=T0 + timedelta(seconds=4),
            ),
        ).state
        self.assertIs(switched.subject.method, PaymentMethod.WISE)
        self.assertEqual(switched.subject.payment_version, before.payment_version)
        self.assertEqual(switched.subject.economic_signature, before.economic_signature)
        self.assertIsNone(switched.summary)
        self.assertIsNone(switched.confirmation)
        self.assertIs(switched.status, PaymentStatus.AWAITING_FINANCIAL_CONFIRMATION)

    def test_economic_summary_requires_next_version_and_stales_old_confirmation(self) -> None:
        old = confirmed_payment()
        revised_subject = PaymentSubject.from_anchor(
            old.subject.confirmed_reservation_anchor,
            payment_id=old.subject.payment_id,
            method=old.subject.method,
            amount_minor=old.subject.amount_minor + 1,
            payment_version=old.subject.payment_version + 1,
        )
        revised = reduce_payment(
            old,
            FinancialSummaryRecorded(
                event_id="payment:event:summary:synthetic:2",
                subject=revised_subject,
                summary_hash=financial_summary_hash(revised_subject),
                recorded_at=T0 + timedelta(seconds=4),
            ),
        ).state
        self.assertEqual(revised.subject.payment_version, old.subject.payment_version + 1)
        self.assertNotEqual(revised.subject.economic_signature, old.subject.economic_signature)
        self.assertIsNone(revised.confirmation)
        assert old.confirmation is not None
        with self.assertRaises(ValueError):
            reduce_payment(revised, old.confirmation)
        assert old.summary is not None
        with self.assertRaises(ValueError):
            reduce_payment(revised, old.summary)
        skipped = replace(revised_subject, payment_version=revised_subject.payment_version + 1)
        with self.assertRaises(ValueError):
            reduce_payment(
                old,
                FinancialSummaryRecorded(
                    event_id="payment:event:summary:synthetic:skipped",
                    subject=skipped,
                    summary_hash=financial_summary_hash(skipped),
                    recorded_at=T0 + timedelta(seconds=4),
                ),
            )

    def test_verified_evidence_after_financial_confirmation_emits_one_financial_command(self) -> None:
        state = confirmed_payment()
        event = evidence_recorded(state)
        first = reduce_payment(state, event)
        second = reduce_payment(first.state, event)
        self.assertEqual(len(first.commands), 1)
        self.assertEqual(second.commands, ())
        command = first.commands[0]
        self.assertIs(type(command), PaymentSettlementCommand)
        self.assertIs(command.operation, SettlementOperation.REGISTER_AND_CONFIRM)
        self.assertNotIn("reservation_command", command.canonical_payload)
        payload = json.loads(command.canonical_payload)
        self.assertEqual(payload["payment_id"], state.subject.payment_id)
        self.assertEqual(payload["evidence_claim_key"], first.state.verified_evidence.claim_key)
        self.assertIs(first.state.status, PaymentStatus.SETTLEMENT_QUEUED)
        self.assertIs(second.status, PaymentTransitionStatus.NOOP)

    def test_each_method_emits_one_command_and_long_stripe_claim_is_not_false_rejected(self) -> None:
        long_account = "stripe-account:" + "a" * 100
        long_event = "evt_" + "A1B2C3D4E5F6G7H8" * 4
        cases = (
            (PaymentMethod.PIX, pix_evidence(), trust_policy()),
            (PaymentMethod.WISE, wise_credit(), trust_policy()),
            (
                PaymentMethod.STRIPE,
                stripe_event(
                    stripe_account_profile_id=long_account,
                    event_id=long_event,
                ),
                trust_policy(stripe_account_profile_id=long_account),
            ),
        )
        for index, (method, evidence, trust) in enumerate(cases, start=1):
            with self.subTest(method=method):
                state = confirmed_payment(method)
                event = PaymentEvidenceRecorded(
                    event_id=f"payment:event:evidence:method:{index}",
                    payment_id=state.subject.payment_id,
                    payment_version=state.subject.payment_version,
                    economic_signature=state.subject.economic_signature,
                    evidence=evidence,
                    trust=trust,
                    recorded_at=T0 + timedelta(seconds=4),
                )
                transition = reduce_payment(state, event)
                self.assertEqual(len(transition.commands), 1)
                self.assertIs(transition.commands[0].operation, SettlementOperation.REGISTER_AND_CONFIRM)

    def test_evidence_before_confirmation_and_divergent_replay_fail_closed(self) -> None:
        unconfirmed = summarized_payment()
        with self.assertRaises(ValueError):
            reduce_payment(unconfirmed, evidence_recorded(unconfirmed))
        confirmed = confirmed_payment()
        event = evidence_recorded(confirmed)
        queued = reduce_payment(confirmed, event).state
        divergent = replace(event, evidence=pix_evidence(normalized_e2e="E1234567820270201ZYXWVUT9876"))
        with self.assertRaises(ValueError):
            reduce_payment(queued, divergent)

    def test_same_evidence_with_new_event_id_is_noop_without_second_command(self) -> None:
        confirmed = confirmed_payment()
        first_event = evidence_recorded(confirmed)
        queued = reduce_payment(confirmed, first_event).state
        replay = replace(
            first_event,
            event_id="payment:event:evidence:synthetic:replay",
        )
        transition = reduce_payment(queued, replay)
        self.assertIs(transition.status, PaymentTransitionStatus.NOOP)
        self.assertEqual(transition.commands, ())
        self.assertEqual(transition.state, queued)

    def test_settlement_outcome_matrix_is_closed(self) -> None:
        self.assertIs(
            project_settlement_outcome(outcome(SettlementCertainty.SETTLED), dispatch_fenced=True),
            PaymentStatus.PAID,
        )
        self.assertIs(
            project_settlement_outcome(
                outcome(SettlementCertainty.NOT_DISPATCHED), dispatch_fenced=False
            ),
            PaymentStatus.RETRYABLE,
        )
        for certainty in (
            SettlementCertainty.DISPATCHED_NO_EFFECT,
            SettlementCertainty.PARTIAL_SETTLEMENT,
            SettlementCertainty.DISPATCHED_UNKNOWN,
        ):
            with self.subTest(certainty=certainty):
                self.assertIs(
                    project_settlement_outcome(outcome(certainty), dispatch_fenced=True),
                    PaymentStatus.MANUAL_REVIEW,
                )
        invalid = (
            lambda: outcome(SettlementCertainty.SETTLED, payment_registered=False),
            lambda: outcome(
                SettlementCertainty.PARTIAL_SETTLEMENT,
                reservation_target_confirmed=True,
            ),
            lambda: outcome(
                SettlementCertainty.DISPATCHED_UNKNOWN,
                requires_reconciliation=False,
            ),
            lambda: outcome(
                SettlementCertainty.NOT_DISPATCHED,
                payment_registered=True,
            ),
        )
        for factory in invalid:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()

    def test_partial_and_unknown_outcomes_are_manual_and_never_paid(self) -> None:
        for certainty in (
            SettlementCertainty.PARTIAL_SETTLEMENT,
            SettlementCertainty.DISPATCHED_UNKNOWN,
        ):
            with self.subTest(certainty=certainty):
                queued, command = queued_payment()
                settling = reduce_payment(queued, settlement_started(queued, command)).state
                finished = reduce_payment(
                    settling,
                    settlement_finished(settling, command, outcome(certainty)),
                )
                self.assertIs(finished.state.status, PaymentStatus.MANUAL_REVIEW)
                self.assertEqual(finished.commands, ())

    def test_not_dispatched_is_retryable_only_before_dispatch_fence(self) -> None:
        queued, command = queued_payment()
        pre_fence = reduce_payment(
            queued,
            settlement_finished(
                queued,
                command,
                outcome(SettlementCertainty.NOT_DISPATCHED),
            ),
        ).state
        self.assertIs(pre_fence.status, PaymentStatus.RETRYABLE)

        queued, command = queued_payment()
        settling = reduce_payment(queued, settlement_started(queued, command)).state
        post_fence = reduce_payment(
            settling,
            settlement_finished(
                settling,
                command,
                outcome(SettlementCertainty.NOT_DISPATCHED),
            ),
        ).state
        self.assertIs(post_fence.status, PaymentStatus.MANUAL_REVIEW)

    def test_expiration_and_cancellation_apply_only_before_settlement(self) -> None:
        initial = initial_payment()
        deadline = initial.subject.confirmed_reservation_anchor.payment_deadline
        assert deadline is not None
        with self.assertRaises(ValueError):
            reduce_payment(
                initial,
                PaymentExpired(
                    event_id="payment:event:expired:synthetic:early",
                    payment_id=initial.subject.payment_id,
                    payment_version=initial.subject.payment_version,
                    economic_signature=initial.subject.economic_signature,
                    expired_at=deadline - timedelta(microseconds=1),
                ),
            )
        expired = reduce_payment(
            initial,
            PaymentExpired(
                event_id="payment:event:expired:synthetic:applied",
                payment_id=initial.subject.payment_id,
                payment_version=initial.subject.payment_version,
                economic_signature=initial.subject.economic_signature,
                expired_at=deadline,
            ),
        ).state
        self.assertIs(expired.status, PaymentStatus.EXPIRED)

        selected = selected_payment()
        cancelled = reduce_payment(
            selected,
            PaymentCancelled(
                event_id="payment:event:cancelled:synthetic:applied",
                payment_id=selected.subject.payment_id,
                payment_version=selected.subject.payment_version,
                economic_signature=selected.subject.economic_signature,
                cancellation_id="payment:cancellation:synthetic:applied",
                cancelled_at=T0 + timedelta(seconds=2),
            ),
        ).state
        self.assertIs(cancelled.status, PaymentStatus.CANCELLED)
        queued, _ = queued_payment()
        with self.assertRaises(ValueError):
            reduce_payment(
                queued,
                PaymentCancelled(
                    event_id="payment:event:cancelled:synthetic:late",
                    payment_id=queued.subject.payment_id,
                    payment_version=queued.subject.payment_version,
                    economic_signature=queued.subject.economic_signature,
                    cancellation_id="payment:cancellation:synthetic:late",
                    cancelled_at=T0 + timedelta(seconds=5),
                ),
            )

    def test_paid_state_is_monotonic_and_finished_replay_is_idempotent(self) -> None:
        queued, command = queued_payment()
        settling = reduce_payment(queued, settlement_started(queued, command)).state
        event = settlement_finished(
            settling,
            command,
            outcome(SettlementCertainty.SETTLED),
        )
        paid_transition = reduce_payment(settling, event)
        paid = paid_transition.state
        self.assertIs(paid.status, PaymentStatus.PAID)
        replay = reduce_payment(paid, event)
        self.assertIs(replay.status, PaymentTransitionStatus.NOOP)
        self.assertIs(replay.state.status, PaymentStatus.PAID)
        expired = reduce_payment(
            paid,
            PaymentExpired(
                event_id="payment:event:expired:synthetic:1",
                payment_id=paid.subject.payment_id,
                payment_version=paid.subject.payment_version,
                economic_signature=paid.subject.economic_signature,
                expired_at=T0 + timedelta(days=4),
            ),
        )
        cancelled = reduce_payment(
            expired.state,
            PaymentCancelled(
                event_id="payment:event:cancelled:synthetic:1",
                payment_id=paid.subject.payment_id,
                payment_version=paid.subject.payment_version,
                economic_signature=paid.subject.economic_signature,
                cancellation_id="payment:cancellation:synthetic:1",
                cancelled_at=T0 + timedelta(days=4, seconds=1),
            ),
        )
        self.assertIs(expired.state.status, PaymentStatus.PAID)
        self.assertIs(cancelled.state.status, PaymentStatus.PAID)
        self.assertEqual(expired.commands, ())
        self.assertEqual(cancelled.commands, ())

    def test_same_event_id_with_divergent_payload_conflicts_before_state_change(self) -> None:
        state = initial_payment()
        first_event = PaymentMethodSelected(
            event_id="payment:event:method:synthetic:1",
            payment_id=state.subject.payment_id,
            method=PaymentMethod.PIX,
            selected_at=T0 + timedelta(seconds=1),
        )
        applied = reduce_payment(state, first_event).state
        divergent = replace(first_event, method=PaymentMethod.WISE)
        with self.assertRaises(ValueError):
            reduce_payment(applied, divergent)


if __name__ == "__main__":
    unittest.main()
