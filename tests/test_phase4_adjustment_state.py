from __future__ import annotations

from datetime import timedelta
import unittest

from reservation_domain import (
    AwaitingAdjustmentState,
    AwaitingConfirmationState,
    ConfirmationDecisionKind,
    ConfirmationReceived,
    DraftAdjusted,
    ExecutionQueuedState,
    ReadyToSummarizeState,
    SummaryRecorded,
    TransitionStatus,
    WorkflowPhase,
    dumps_state,
    loads_state,
    reduce,
    transition_matrix,
)
from tests.test_phase2_domain import (
    T0,
    customer,
    event_time,
    reach_awaiting_state,
    terms,
)


def adjustment_event(state: AwaitingConfirmationState, *, seconds: int = 10):
    return ConfirmationReceived(
        event_id=f"event-adjust-{seconds}",
        occurred_at=event_time(seconds),
        confirmation_event_id=f"confirmation-adjust-{seconds}",
        decision=ConfirmationDecisionKind.ADJUST,
        target_draft_version=state.draft.version,
        subject_signature=state.draft.subject_signature,
    )


def old_accept(state: AwaitingAdjustmentState, *, seconds: int = 11):
    return ConfirmationReceived(
        event_id=f"event-old-accept-{seconds}",
        occurred_at=event_time(seconds),
        confirmation_event_id=f"confirmation-old-accept-{seconds}",
        decision=ConfirmationDecisionKind.ACCEPT,
        target_draft_version=state.draft.version,
        subject_signature=state.draft.subject_signature,
    )


class AdjustmentStateTests(unittest.TestCase):
    def test_adjustment_decision_disarms_presented_summary(self) -> None:
        awaiting = reach_awaiting_state()
        transition = reduce(awaiting, adjustment_event(awaiting))
        self.assertEqual(transition.status, TransitionStatus.APPLIED)
        self.assertEqual(transition.reason, "lead_requested_adjustment")
        self.assertEqual(transition.commands, ())
        self.assertIsInstance(transition.state, AwaitingAdjustmentState)
        self.assertIs(transition.state.phase, WorkflowPhase.AWAITING_ADJUSTMENT)
        self.assertIs(
            transition.state.decision.decision,
            ConfirmationDecisionKind.ADJUST,
        )
        self.assertEqual(transition.state.summary, awaiting.summary)

    def test_old_acceptance_is_ignored_after_adjustment(self) -> None:
        awaiting = reach_awaiting_state()
        disarmed = reduce(awaiting, adjustment_event(awaiting)).state
        self.assertIsInstance(disarmed, AwaitingAdjustmentState)
        transition = reduce(disarmed, old_accept(disarmed))
        self.assertEqual(transition.status, TransitionStatus.IGNORED)
        self.assertEqual(transition.reason, "event_not_applicable_in_phase")
        self.assertEqual(transition.commands, ())
        self.assertIsInstance(transition.state, AwaitingAdjustmentState)
        self.assertEqual(transition.state.draft, disarmed.draft)
        self.assertEqual(transition.state.summary, disarmed.summary)

    def test_noop_adjustment_does_not_create_new_version(self) -> None:
        awaiting = reach_awaiting_state()
        disarmed = reduce(awaiting, adjustment_event(awaiting)).state
        transition = reduce(
            disarmed,
            DraftAdjusted(
                event_id="event-noop-adjustment",
                occurred_at=event_time(11),
                customer=disarmed.draft.customer,
                terms=disarmed.draft.terms,
            ),
        )
        self.assertEqual(transition.status, TransitionStatus.REJECTED)
        self.assertEqual(transition.reason, "adjustment_did_not_change_subject")
        self.assertEqual(transition.commands, ())
        self.assertIsInstance(transition.state, AwaitingAdjustmentState)
        self.assertEqual(transition.state.draft.version, 1)

    def test_semantic_adjustment_creates_exactly_next_version(self) -> None:
        awaiting = reach_awaiting_state()
        disarmed = reduce(awaiting, adjustment_event(awaiting)).state
        transition = reduce(
            disarmed,
            DraftAdjusted(
                event_id="event-semantic-adjustment",
                occurred_at=event_time(11),
                customer=customer(full_name="Synthetic Person Updated"),
                terms=terms(payment_method="cash"),
            ),
        )
        self.assertEqual(transition.status, TransitionStatus.APPLIED)
        self.assertEqual(transition.reason, "commercial_draft_version_incremented")
        self.assertEqual(transition.commands, ())
        self.assertIsInstance(transition.state, ReadyToSummarizeState)
        self.assertEqual(transition.state.draft.version, awaiting.draft.version + 1)
        self.assertNotEqual(
            transition.state.draft.subject_signature,
            awaiting.draft.subject_signature,
        )

    def test_direct_noop_adjustment_while_awaiting_is_rejected(self) -> None:
        awaiting = reach_awaiting_state()
        transition = reduce(
            awaiting,
            DraftAdjusted(
                event_id="event-direct-noop",
                occurred_at=event_time(10),
                customer=awaiting.draft.customer,
                terms=awaiting.draft.terms,
            ),
        )
        self.assertEqual(transition.status, TransitionStatus.REJECTED)
        self.assertEqual(transition.reason, "adjustment_did_not_change_subject")
        self.assertIsInstance(transition.state, AwaitingConfirmationState)
        self.assertEqual(transition.commands, ())

    def test_new_version_requires_new_summary_and_new_posterior_acceptance(self) -> None:
        first = reach_awaiting_state()
        disarmed = reduce(first, adjustment_event(first)).state
        ready = reduce(
            disarmed,
            DraftAdjusted(
                event_id="event-adjust-v2",
                occurred_at=event_time(11),
                customer=customer(full_name="Synthetic Person V2"),
                terms=terms(payment_method="cash"),
            ),
        ).state
        self.assertIsInstance(ready, ReadyToSummarizeState)
        early_old = reduce(
            ready,
            ConfirmationReceived(
                event_id="event-old-before-v2-summary",
                occurred_at=event_time(12),
                confirmation_event_id="confirmation-old-before-v2-summary",
                decision=ConfirmationDecisionKind.ACCEPT,
                target_draft_version=first.draft.version,
                subject_signature=first.draft.subject_signature,
            ),
        )
        self.assertEqual(early_old.commands, ())
        self.assertIsInstance(early_old.state, ReadyToSummarizeState)

        awaiting_v2 = reduce(
            early_old.state,
            SummaryRecorded(
                event_id="event-summary-v2",
                occurred_at=event_time(13),
                summary_event_id="summary-v2",
                draft_version=ready.draft.version,
                subject_signature=ready.draft.subject_signature,
                outbox_message_id="outbox-v2",
            ),
        ).state
        self.assertIsInstance(awaiting_v2, AwaitingConfirmationState)
        stale = reduce(
            awaiting_v2,
            ConfirmationReceived(
                event_id="event-stale-after-v2-summary",
                occurred_at=event_time(14),
                confirmation_event_id="confirmation-stale-after-v2-summary",
                decision=ConfirmationDecisionKind.ACCEPT,
                target_draft_version=first.draft.version,
                subject_signature=first.draft.subject_signature,
            ),
        )
        self.assertEqual(stale.status, TransitionStatus.REJECTED)
        self.assertEqual(stale.commands, ())

        accepted = reduce(
            stale.state,
            ConfirmationReceived(
                event_id="event-accept-v2",
                occurred_at=event_time(15),
                confirmation_event_id="confirmation-accept-v2",
                decision=ConfirmationDecisionKind.ACCEPT,
                target_draft_version=ready.draft.version,
                subject_signature=ready.draft.subject_signature,
            ),
        )
        self.assertIsInstance(accepted.state, ExecutionQueuedState)
        self.assertEqual(len(accepted.commands), 1)
        self.assertEqual(accepted.commands[0].draft_version, 2)

    def test_adjustment_state_round_trips_strict_serializer(self) -> None:
        awaiting = reach_awaiting_state()
        disarmed = reduce(awaiting, adjustment_event(awaiting)).state
        self.assertEqual(loads_state(dumps_state(disarmed)), disarmed)

    def test_matrix_expands_to_sixteen_states_and_192_pairs(self) -> None:
        matrix = transition_matrix()
        self.assertEqual(len(matrix), 16)
        self.assertEqual(sum(len(row) for row in matrix.values()), 192)
        self.assertEqual(
            matrix["awaiting_adjustment"]["draft_adjusted"],
            "evaluate",
        )
        self.assertEqual(
            matrix["awaiting_adjustment"]["confirmation_received"],
            "ignore",
        )


if __name__ == "__main__":
    unittest.main()
