from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta, timezone
import inspect
import json
import unittest

from reservation_domain import ExecutionCertainty, ExecutionOutcome
from reservation_followup import (
    EffectRequirement,
    HandoffEffectPolicy,
    HandoffStatus,
    from_wire_json,
    to_wire_json,
)
from reservation_followup.handoff import (
    HandoffAcknowledged,
    HandoffCancellationCode,
    HandoffCancelled,
    HandoffEffectFailed,
    HandoffEffectFailureCode,
    HandoffEffectJob,
    HandoffEffectKind,
    HandoffReasonCode,
    HandoffRequested,
    HandoffTransition,
    HandoffTransitionReason,
    HandoffTransitionStatus,
    HandoffWorkflow,
    PublicHandoffProjection,
    PublicNextAction,
    handoff_transition_matrix,
    new_handoff,
    project_handoff_public_reply,
    reduce_handoff,
)
from tests.phase6_helpers import T0, confirmed_anchor, outcome


def handoff_requested(**changes: object) -> HandoffRequested:
    values: dict[str, object] = {
        "handoff_id": "handoff:synthetic:1",
        "lead_key_hash": "b" * 64,
        "incident_key": "incident:synthetic:1",
        "reason_code": HandoffReasonCode.CUSTOMER_REQUESTED,
        "source_event_id": "source:event:synthetic:1",
        "reservation_anchor": None,
        "requested_at": T0,
    }
    values.update(changes)
    return HandoffRequested(**values)


def optional_email_policy() -> HandoffEffectPolicy:
    return HandoffEffectPolicy(
        queue_state=EffectRequirement.REQUIRED,
        customer_acknowledgement=EffectRequirement.REQUIRED,
        internal_email=EffectRequirement.OPTIONAL,
    )


def active_handoff(
    *,
    request: HandoffRequested | None = None,
    policy: HandoffEffectPolicy | None = None,
) -> HandoffWorkflow:
    return new_handoff(
        request or handoff_requested(),
        policy or HandoffEffectPolicy.default_email_disabled(),
    ).state


def handoff_acknowledged(
    state: HandoffWorkflow,
    **changes: object,
) -> HandoffAcknowledged:
    job = HandoffEffectJob.customer_acknowledgement(state)
    values: dict[str, object] = {
        "handoff_id": state.request.handoff_id,
        "incident_key": state.request.incident_key,
        "effect_id": job.effect_id,
        "receipt_id": "receipt:ack:synthetic:1",
        "acknowledged_at": T0 + timedelta(seconds=2),
    }
    values.update(changes)
    return HandoffAcknowledged(**values)


def handoff_effect_failed(
    state: HandoffWorkflow,
    *,
    kind: HandoffEffectKind = HandoffEffectKind.INTERNAL_EMAIL,
    **changes: object,
) -> HandoffEffectFailed:
    job = (
        HandoffEffectJob.internal_email(state, required=False)
        if kind is HandoffEffectKind.INTERNAL_EMAIL
        else HandoffEffectJob.customer_acknowledgement(state)
    )
    values: dict[str, object] = {
        "handoff_id": state.request.handoff_id,
        "incident_key": state.request.incident_key,
        "effect_id": job.effect_id,
        "kind": kind,
        "failure_code": HandoffEffectFailureCode.EFFECT_UNAVAILABLE,
        "failed_at": T0 + timedelta(seconds=1),
    }
    values.update(changes)
    return HandoffEffectFailed(**values)


def handoff_cancelled(
    state: HandoffWorkflow,
    **changes: object,
) -> HandoffCancelled:
    values: dict[str, object] = {
        "handoff_id": state.request.handoff_id,
        "incident_key": state.request.incident_key,
        "cancellation_code": HandoffCancellationCode.REQUEST_WITHDRAWN,
        "cancelled_at": T0 + timedelta(seconds=3),
    }
    values.update(changes)
    return HandoffCancelled(**values)


class Phase6HandoffContractTests(unittest.TestCase):
    def test_closed_enums_and_dto_fields_are_exact(self) -> None:
        expected_enums = {
            HandoffReasonCode: (
                "customer_requested",
                "safety_review",
                "provider_uncertain",
                "operational_review",
            ),
            HandoffEffectKind: (
                "customer_acknowledgement",
                "internal_email",
            ),
            HandoffEffectFailureCode: (
                "effect_unavailable",
                "effect_rejected",
                "effect_unknown",
            ),
            HandoffCancellationCode: (
                "request_withdrawn",
                "operator_cancelled",
            ),
            HandoffTransitionStatus: (
                "applied",
                "noop",
                "conflict",
                "rejected",
            ),
            HandoffTransitionReason: (
                "handoff_opened",
                "identical_replay",
                "divergent_incident",
                "acknowledgement_recorded",
                "effect_failure_recorded",
                "handoff_cancelled",
                "event_not_applicable",
            ),
            PublicNextAction: ("wait_for_human", "no_action"),
        }
        for enum_type, expected_values in expected_enums.items():
            with self.subTest(enum_type=enum_type.__name__):
                self.assertEqual(
                    tuple(member.value for member in enum_type),
                    expected_values,
                )
                self.assertEqual(len(enum_type.__members__), len(expected_values))

        expected_fields = {
            HandoffRequested: (
                "handoff_id",
                "lead_key_hash",
                "incident_key",
                "reason_code",
                "source_event_id",
                "reservation_anchor",
                "requested_at",
            ),
            HandoffAcknowledged: (
                "handoff_id",
                "incident_key",
                "effect_id",
                "receipt_id",
                "acknowledged_at",
            ),
            HandoffEffectFailed: (
                "handoff_id",
                "incident_key",
                "effect_id",
                "kind",
                "failure_code",
                "failed_at",
            ),
            HandoffCancelled: (
                "handoff_id",
                "incident_key",
                "cancellation_code",
                "cancelled_at",
            ),
            HandoffEffectJob: (
                "effect_id",
                "handoff_id",
                "incident_key",
                "kind",
                "required",
                "created_at",
            ),
            HandoffWorkflow: (
                "request",
                "policy",
                "status",
                "queue_active",
                "acknowledgement",
                "effect_failures",
                "cancellation",
                "conflicting_request",
            ),
            HandoffTransition: (
                "state",
                "status",
                "reason",
                "events",
                "effect_jobs",
            ),
            PublicHandoffProjection: (
                "public_text",
                "next_action",
                "reservation_outcome",
            ),
        }
        for dto_type, expected_names in expected_fields.items():
            with self.subTest(dto_type=dto_type.__name__):
                self.assertEqual(
                    tuple(field.name for field in fields(dto_type)),
                    expected_names,
                )

    def test_request_validates_exact_ids_hash_reason_time_and_optional_anchor(self) -> None:
        anchored = handoff_requested(reservation_anchor=confirmed_anchor())
        self.assertIs(type(anchored.reservation_anchor), type(confirmed_anchor()))

        offset = timezone(timedelta(hours=-3))
        normalized = handoff_requested(requested_at=T0.astimezone(offset))
        self.assertEqual(normalized.requested_at, T0)
        self.assertIs(normalized.requested_at.tzinfo, timezone.utc)

        invalid_changes = (
            {"handoff_id": "x"},
            {"handoff_id": 123},
            {"handoff_id": " handoff:synthetic:1 "},
            {"lead_key_hash": "B" * 64},
            {"lead_key_hash": "b" * 63},
            {"incident_key": "x"},
            {"reason_code": HandoffReasonCode.CUSTOMER_REQUESTED.value},
            {"source_event_id": object()},
            {"reservation_anchor": object()},
            {"requested_at": T0.replace(tzinfo=None)},
            {"requested_at": "2027-02-01T12:00:00+00:00"},
            {
                "reservation_anchor": confirmed_anchor(),
                "requested_at": T0 - timedelta(microseconds=1),
            },
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                handoff_requested(**changes)

    def test_events_validate_exact_closed_fields(self) -> None:
        state = active_handoff(policy=optional_email_policy())
        valid_ack = handoff_acknowledged(state)
        valid_failure = handoff_effect_failed(state)
        valid_cancel = handoff_cancelled(state)
        self.assertEqual(valid_ack.handoff_id, state.request.handoff_id)
        self.assertIs(valid_failure.kind, HandoffEffectKind.INTERNAL_EMAIL)
        self.assertIs(
            valid_cancel.cancellation_code,
            HandoffCancellationCode.REQUEST_WITHDRAWN,
        )

        invalid_factories = (
            lambda: handoff_acknowledged(state, receipt_id="x"),
            lambda: handoff_acknowledged(
                state,
                acknowledged_at=T0.replace(tzinfo=None),
            ),
            lambda: handoff_effect_failed(
                state,
                kind=HandoffEffectKind.INTERNAL_EMAIL.value,
            ),
            lambda: handoff_effect_failed(
                state,
                failure_code=HandoffEffectFailureCode.EFFECT_UNAVAILABLE.value,
            ),
            lambda: handoff_cancelled(
                state,
                cancellation_code=HandoffCancellationCode.REQUEST_WITHDRAWN.value,
            ),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory), self.assertRaises(ValueError):
                factory()

    def test_all_handoff_dtos_are_frozen_and_slotted(self) -> None:
        state = active_handoff(policy=optional_email_policy())
        values = (
            state.request,
            handoff_acknowledged(state),
            handoff_effect_failed(state),
            handoff_cancelled(state),
            HandoffEffectJob.customer_acknowledgement(state),
            state,
            new_handoff(state.request, state.policy),
            project_handoff_public_reply(state, None),
        )
        for value in values:
            with self.subTest(dto=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, fields(value)[0].name, "changed")


class Phase6HandoffReducerTests(unittest.TestCase):
    def test_email_disabled_still_opens_queue_and_customer_ack(self) -> None:
        event = handoff_requested()
        transition = new_handoff(
            event,
            HandoffEffectPolicy.default_email_disabled(),
        )
        self.assertEqual(
            transition.state.status,
            HandoffStatus.ACKNOWLEDGEMENT_PENDING,
        )
        self.assertEqual(
            [job.kind for job in transition.effect_jobs],
            [HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT],
        )
        self.assertTrue(transition.state.queue_active)
        self.assertEqual(transition.events, (event,))
        self.assertIs(transition.status, HandoffTransitionStatus.APPLIED)
        self.assertIs(transition.reason, HandoffTransitionReason.HANDOFF_OPENED)
        self.assertTrue(transition.effect_jobs[0].required)

    def test_optional_email_is_second_nonblocking_job(self) -> None:
        transition = new_handoff(handoff_requested(), optional_email_policy())
        self.assertEqual(
            tuple(job.kind for job in transition.effect_jobs),
            (
                HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT,
                HandoffEffectKind.INTERNAL_EMAIL,
            ),
        )
        self.assertEqual(
            tuple(job.required for job in transition.effect_jobs),
            (True, False),
        )
        self.assertTrue(transition.state.queue_active)
        self.assertIs(
            transition.state.status,
            HandoffStatus.ACKNOWLEDGEMENT_PENDING,
        )

    def test_effect_jobs_are_deterministic_and_policy_bounded(self) -> None:
        state = active_handoff(policy=optional_email_policy())
        first = HandoffEffectJob.customer_acknowledgement(state)
        second = HandoffEffectJob.customer_acknowledgement(state)
        email = HandoffEffectJob.internal_email(state, required=False)
        self.assertEqual(first, second)
        self.assertNotEqual(first.effect_id, email.effect_id)
        self.assertEqual(first.created_at, state.request.requested_at)
        self.assertEqual(email.created_at, state.request.requested_at)
        with self.assertRaises(ValueError):
            HandoffEffectJob.internal_email(state, required=True)
        with self.assertRaises(ValueError):
            HandoffEffectJob.internal_email(active_handoff(), required=False)

    def test_new_handoff_rejects_subclasses_and_raw_contracts(self) -> None:
        class InventedRequest(HandoffRequested):
            pass

        valid = handoff_requested()
        invented = InventedRequest(
            **{field.name: getattr(valid, field.name) for field in fields(valid)}
        )
        with self.assertRaises(TypeError):
            new_handoff(invented, HandoffEffectPolicy.default_email_disabled())
        with self.assertRaises(TypeError):
            new_handoff(valid, {"internal_email": "disabled"})

    def test_identical_requested_replay_is_exact_noop(self) -> None:
        state = active_handoff()
        transition = reduce_handoff(state, state.request)
        self.assertIs(transition.state, state)
        self.assertEqual(transition.events, ())
        self.assertEqual(transition.effect_jobs, ())
        self.assertIs(transition.status, HandoffTransitionStatus.NOOP)
        self.assertIs(transition.reason, HandoffTransitionReason.IDENTICAL_REPLAY)

    def test_divergent_incident_conflicts_without_overwriting_original(self) -> None:
        state = active_handoff()
        divergent = handoff_requested(
            handoff_id="handoff:synthetic:2",
            source_event_id="source:event:synthetic:2",
            reason_code=HandoffReasonCode.OPERATIONAL_REVIEW,
            requested_at=T0 + timedelta(seconds=1),
        )
        transition = reduce_handoff(state, divergent)
        self.assertIs(transition.status, HandoffTransitionStatus.CONFLICT)
        self.assertIs(transition.reason, HandoffTransitionReason.DIVERGENT_INCIDENT)
        self.assertIs(transition.state.status, HandoffStatus.MANUAL_REVIEW)
        self.assertTrue(transition.state.queue_active)
        self.assertEqual(transition.state.request, state.request)
        self.assertEqual(transition.state.conflicting_request, divergent)
        self.assertEqual(transition.events, (divergent,))

        replay = reduce_handoff(transition.state, divergent)
        self.assertIs(replay.state, transition.state)
        self.assertIs(replay.status, HandoffTransitionStatus.NOOP)

        second_divergent = handoff_requested(
            handoff_id="handoff:synthetic:3",
            source_event_id="source:event:synthetic:3",
            reason_code=HandoffReasonCode.SAFETY_REVIEW,
            requested_at=T0 + timedelta(seconds=2),
        )
        with self.assertRaises(ValueError):
            reduce_handoff(transition.state, second_divergent)
        self.assertEqual(transition.state.conflicting_request, divergent)

    def test_same_handoff_id_with_divergent_payload_also_conflicts(self) -> None:
        state = active_handoff()
        divergent = handoff_requested(
            incident_key="incident:synthetic:other",
            source_event_id="source:event:synthetic:other",
        )
        transition = reduce_handoff(state, divergent)
        self.assertIs(transition.status, HandoffTransitionStatus.CONFLICT)
        self.assertEqual(transition.state.request, state.request)
        self.assertEqual(transition.state.conflicting_request, divergent)

    def test_unrelated_request_fails_closed(self) -> None:
        state = active_handoff()
        unrelated = handoff_requested(
            handoff_id="handoff:synthetic:other",
            incident_key="incident:synthetic:other",
            source_event_id="source:event:synthetic:other",
        )
        with self.assertRaises(ValueError):
            reduce_handoff(state, unrelated)

    def test_acknowledgement_receipt_advances_state_and_replays_exactly(self) -> None:
        state = active_handoff()
        event = handoff_acknowledged(state)
        transition = reduce_handoff(state, event)
        self.assertIs(transition.status, HandoffTransitionStatus.APPLIED)
        self.assertIs(
            transition.reason,
            HandoffTransitionReason.ACKNOWLEDGEMENT_RECORDED,
        )
        self.assertIs(transition.state.status, HandoffStatus.ACKNOWLEDGED)
        self.assertTrue(transition.state.queue_active)
        self.assertEqual(transition.state.acknowledgement, event)
        self.assertEqual(transition.events, (event,))
        replay = reduce_handoff(transition.state, event)
        self.assertIs(replay.state, transition.state)
        self.assertIs(replay.status, HandoffTransitionStatus.NOOP)

        divergent = replace(event, receipt_id="receipt:ack:synthetic:other")
        with self.assertRaises(ValueError):
            reduce_handoff(transition.state, divergent)

    def test_optional_email_failure_is_isolated_from_queue_and_ack(self) -> None:
        state = active_handoff(policy=optional_email_policy())
        event = handoff_effect_failed(state)
        transition = reduce_handoff(state, event)
        self.assertIs(transition.status, HandoffTransitionStatus.APPLIED)
        self.assertIs(
            transition.reason,
            HandoffTransitionReason.EFFECT_FAILURE_RECORDED,
        )
        self.assertIs(
            transition.state.status,
            HandoffStatus.ACKNOWLEDGEMENT_PENDING,
        )
        self.assertTrue(transition.state.queue_active)
        self.assertIsNone(transition.state.acknowledgement)
        self.assertEqual(transition.state.effect_failures, (event,))

        acknowledged = reduce_handoff(
            transition.state,
            handoff_acknowledged(transition.state),
        )
        self.assertIs(acknowledged.state.status, HandoffStatus.ACKNOWLEDGED)
        self.assertTrue(acknowledged.state.queue_active)
        self.assertEqual(acknowledged.state.effect_failures, (event,))

    def test_required_ack_failure_enters_review_but_never_closes_queue(self) -> None:
        state = active_handoff()
        failure = handoff_effect_failed(
            state,
            kind=HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT,
        )
        failed = reduce_handoff(state, failure)
        self.assertIs(failed.state.status, HandoffStatus.MANUAL_REVIEW)
        self.assertTrue(failed.state.queue_active)
        self.assertIsNone(failed.state.acknowledgement)

        receipt = handoff_acknowledged(failed.state)
        acknowledged = reduce_handoff(failed.state, receipt)
        self.assertIs(acknowledged.state.status, HandoffStatus.ACKNOWLEDGED)
        self.assertTrue(acknowledged.state.queue_active)
        self.assertEqual(acknowledged.state.effect_failures, (failure,))

    def test_late_required_ack_failure_cannot_regress_acknowledged_state(self) -> None:
        pending = active_handoff()
        acknowledged = reduce_handoff(
            pending,
            handoff_acknowledged(pending),
        ).state
        failure = handoff_effect_failed(
            acknowledged,
            kind=HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT,
        )
        transition = reduce_handoff(acknowledged, failure)
        self.assertIs(transition.state.status, HandoffStatus.ACKNOWLEDGED)
        self.assertEqual(transition.state.acknowledgement, acknowledged.acknowledgement)
        self.assertEqual(transition.state.effect_failures, (failure,))
        self.assertTrue(transition.state.queue_active)

    def test_acknowledgement_does_not_clear_an_unresolved_incident_conflict(self) -> None:
        pending = active_handoff()
        conflict = handoff_requested(
            handoff_id="handoff:synthetic:conflict-ack",
            source_event_id="source:event:synthetic:conflict-ack",
            reason_code=HandoffReasonCode.OPERATIONAL_REVIEW,
            requested_at=T0 + timedelta(seconds=1),
        )
        manual = reduce_handoff(pending, conflict).state
        transition = reduce_handoff(
            manual,
            handoff_acknowledged(
                manual,
                acknowledged_at=T0 + timedelta(seconds=2),
            ),
        )
        self.assertIs(transition.state.status, HandoffStatus.MANUAL_REVIEW)
        self.assertEqual(transition.state.conflicting_request, conflict)
        self.assertIsNotNone(transition.state.acknowledgement)
        self.assertTrue(transition.state.queue_active)

    def test_failure_for_disabled_or_divergent_effect_fails_closed(self) -> None:
        enabled = active_handoff(policy=optional_email_policy())
        email_failure = handoff_effect_failed(enabled)
        disabled = active_handoff()
        with self.assertRaises(ValueError):
            reduce_handoff(disabled, email_failure)

        failed = reduce_handoff(enabled, email_failure).state
        divergent = replace(
            email_failure,
            failure_code=HandoffEffectFailureCode.EFFECT_REJECTED,
        )
        with self.assertRaises(ValueError):
            reduce_handoff(failed, divergent)

    def test_cancellation_closes_queue_and_has_idempotent_receipt(self) -> None:
        state = active_handoff()
        event = handoff_cancelled(state)
        transition = reduce_handoff(state, event)
        self.assertIs(transition.state.status, HandoffStatus.CANCELLED)
        self.assertFalse(transition.state.queue_active)
        self.assertEqual(transition.state.cancellation, event)
        self.assertEqual(transition.events, (event,))
        replay = reduce_handoff(transition.state, event)
        self.assertIs(replay.state, transition.state)
        self.assertIs(replay.status, HandoffTransitionStatus.NOOP)

        divergent = replace(
            event,
            cancellation_code=HandoffCancellationCode.OPERATOR_CANCELLED,
        )
        with self.assertRaises(ValueError):
            reduce_handoff(transition.state, divergent)

    def test_state_event_matrix_is_literal_exact_and_bilateral(self) -> None:
        matrix = handoff_transition_matrix()
        expected = {
            "requested": {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "reject",
                "handoff_effect_failed": "reject",
                "handoff_cancelled": "apply_cancellation",
            },
            "active": {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "apply_acknowledgement",
                "handoff_effect_failed": "record_effect_failure",
                "handoff_cancelled": "apply_cancellation",
            },
            "acknowledgement_pending": {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "apply_acknowledgement",
                "handoff_effect_failed": "record_effect_failure",
                "handoff_cancelled": "apply_cancellation",
            },
            "acknowledged": {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "apply_acknowledgement",
                "handoff_effect_failed": "record_effect_failure",
                "handoff_cancelled": "apply_cancellation",
            },
            "manual_review": {
                "handoff_requested": "evaluate_request",
                "handoff_acknowledged": "apply_acknowledgement",
                "handoff_effect_failed": "record_effect_failure",
                "handoff_cancelled": "apply_cancellation",
            },
            "completed": {
                "handoff_requested": "replay_only",
                "handoff_acknowledged": "replay_only",
                "handoff_effect_failed": "replay_only",
                "handoff_cancelled": "reject",
            },
            "cancelled": {
                "handoff_requested": "replay_only",
                "handoff_acknowledged": "replay_only",
                "handoff_effect_failed": "replay_only",
                "handoff_cancelled": "apply_cancellation",
            },
        }
        self.assertEqual(matrix, expected)
        self.assertEqual(set(matrix), {status.value for status in HandoffStatus})
        self.assertTrue(all(len(row) == 4 for row in matrix.values()))
        self.assertEqual(sum(len(row) for row in matrix.values()), 28)

    def test_known_but_inapplicable_matrix_pair_is_rejected_without_recording(self) -> None:
        opened = active_handoff()
        requested = replace(
            opened,
            status=HandoffStatus.REQUESTED,
            queue_active=False,
        )
        transition = reduce_handoff(requested, handoff_acknowledged(requested))
        self.assertIs(transition.state, requested)
        self.assertIs(transition.status, HandoffTransitionStatus.REJECTED)
        self.assertIs(
            transition.reason,
            HandoffTransitionReason.EVENT_NOT_APPLICABLE,
        )
        self.assertEqual(transition.events, ())
        self.assertEqual(transition.effect_jobs, ())

    def test_unknown_subclass_and_raw_dict_events_fail_closed(self) -> None:
        state = active_handoff()
        valid = handoff_acknowledged(state)

        class InventedAcknowledged(HandoffAcknowledged):
            pass

        invented = InventedAcknowledged(
            **{field.name: getattr(valid, field.name) for field in fields(valid)}
        )
        for event in (invented, {"type": "handoff_acknowledged"}, object()):
            with self.subTest(event=type(event).__name__), self.assertRaises(TypeError):
                reduce_handoff(state, event)

        class InventedWorkflow(HandoffWorkflow):
            pass

        invented_state = InventedWorkflow(
            **{field.name: getattr(state, field.name) for field in fields(state)}
        )
        with self.assertRaises(TypeError):
            reduce_handoff(invented_state, valid)

    def test_reducer_api_has_no_lead_text_or_lexical_routing_input(self) -> None:
        self.assertEqual(tuple(inspect.signature(reduce_handoff).parameters), ("state", "event"))
        self.assertEqual(
            tuple(inspect.signature(new_handoff).parameters),
            ("event", "policy"),
        )
        self.assertEqual(
            tuple(inspect.signature(project_handoff_public_reply).parameters),
            (
                "state",
                "reservation_outcome",
                "stale_confirmation_question",
                "stale_missing_slots_question",
                "prior_followup_text",
            ),
        )


class Phase6HandoffProjectionTests(unittest.TestCase):
    def test_terminal_handoff_suppresses_stale_confirmation_and_missing_slots(self) -> None:
        projection = project_handoff_public_reply(
            active_handoff(),
            reservation_outcome=None,
            stale_confirmation_question="confirmar novamente?",
            stale_missing_slots_question="qual data e quantas pessoas?",
            prior_followup_text="pergunta antiga que não pode reaparecer",
        )
        self.assertNotIn("confirm", projection.public_text.casefold())
        self.assertNotIn("qual data", projection.public_text.casefold())
        self.assertNotIn("pergunta antiga", projection.public_text.casefold())
        self.assertEqual(projection.next_action, PublicNextAction.WAIT_FOR_HUMAN)
        self.assertIsNone(projection.reservation_outcome)

    def test_provider_outcome_precedes_terminal_handoff_without_private_fields(self) -> None:
        state = active_handoff(policy=optional_email_policy())
        canonical = outcome()
        projection = project_handoff_public_reply(state, canonical)
        self.assertIs(projection.reservation_outcome, canonical)
        self.assertTrue(projection.public_text.startswith("A reserva foi criada."))
        self.assertLess(
            projection.public_text.index("A reserva foi criada."),
            projection.public_text.index("atendimento"),
        )
        private_values = (
            state.request.handoff_id,
            state.request.lead_key_hash,
            state.request.incident_key,
            state.request.reason_code.value,
            state.request.source_event_id,
            canonical.command_id,
            canonical.provider_reference,
            canonical.evidence[0],
        )
        for private in private_values:
            with self.subTest(private=private):
                self.assertNotIn(private, projection.public_text)

    def test_projection_uses_only_canonical_certainty_for_reservation_sentence(self) -> None:
        state = active_handoff()
        expectations = {
            ExecutionCertainty.NOT_CALLED: "A reserva não foi criada.",
            ExecutionCertainty.CALLED_NO_EFFECT: "A reserva não foi criada.",
            ExecutionCertainty.EFFECT_CONFIRMED: "A reserva foi criada.",
            ExecutionCertainty.CALLED_UNKNOWN: "Ainda não sabemos se a reserva foi criada.",
        }
        for certainty, sentence in expectations.items():
            canonical = outcome(certainty=certainty)
            projection = project_handoff_public_reply(state, canonical)
            with self.subTest(certainty=certainty):
                self.assertTrue(projection.public_text.startswith(sentence))
                self.assertIs(projection.reservation_outcome, canonical)
                self.assertNotIn(canonical.normalized_status, projection.public_text)

    def test_confirmed_anchor_is_canonical_when_explicit_outcome_is_absent(self) -> None:
        anchor = confirmed_anchor()
        state = active_handoff(
            request=handoff_requested(reservation_anchor=anchor),
        )
        projection = project_handoff_public_reply(state, None)
        self.assertEqual(projection.reservation_outcome, anchor.reservation_outcome)
        self.assertTrue(projection.public_text.startswith("A reserva foi criada."))

    def test_absent_outcome_and_anchor_do_not_invent_outcome_dto(self) -> None:
        projection = project_handoff_public_reply(active_handoff(), None)
        self.assertIsNone(projection.reservation_outcome)
        self.assertTrue(
            projection.public_text.startswith(
                "Não há informação canônica sobre uma reserva neste atendimento."
            )
        )
        self.assertNotIn("reserva não foi criada", projection.public_text.casefold())

    def test_projection_rejects_outcome_subclass_and_anchor_divergence(self) -> None:
        valid = outcome()

        class InventedOutcome(ExecutionOutcome):
            pass

        invented = InventedOutcome(
            command_id=valid.command_id,
            certainty=valid.certainty,
            normalized_status=valid.normalized_status,
            provider_reference=valid.provider_reference,
            evidence=valid.evidence,
        )
        with self.assertRaises(ValueError):
            project_handoff_public_reply(active_handoff(), invented)

        anchor = confirmed_anchor()
        anchored_state = active_handoff(
            request=handoff_requested(reservation_anchor=anchor),
        )
        with self.assertRaises(ValueError):
            project_handoff_public_reply(
                anchored_state,
                outcome(certainty=ExecutionCertainty.CALLED_NO_EFFECT),
            )

    def test_cancelled_handoff_has_deterministic_no_action_projection(self) -> None:
        state = active_handoff()
        cancelled = reduce_handoff(state, handoff_cancelled(state)).state
        first = project_handoff_public_reply(cancelled, None)
        second = project_handoff_public_reply(cancelled, None)
        self.assertEqual(first, second)
        self.assertIs(first.next_action, PublicNextAction.NO_ACTION)
        self.assertIn("atendimento humano foi cancelado", first.public_text)


class Phase6HandoffSerializationTests(unittest.TestCase):
    def test_persistable_and_public_handoff_dtos_round_trip_bilaterally(self) -> None:
        opened = new_handoff(handoff_requested(), optional_email_policy())
        state = opened.state
        failed_event = handoff_effect_failed(state)
        failed_state = reduce_handoff(state, failed_event).state
        acknowledged_event = handoff_acknowledged(failed_state)
        acknowledged_state = reduce_handoff(failed_state, acknowledged_event).state
        cancelled_event = handoff_cancelled(acknowledged_state)
        cancelled_state = reduce_handoff(acknowledged_state, cancelled_event).state
        projection = project_handoff_public_reply(acknowledged_state, outcome())
        values = (
            state.request,
            acknowledged_event,
            failed_event,
            cancelled_event,
            *opened.effect_jobs,
            state,
            failed_state,
            acknowledged_state,
            cancelled_state,
            projection,
        )
        for value in values:
            with self.subTest(dto=type(value).__name__):
                wire = to_wire_json(value)
                decoded = from_wire_json(wire, type(value))
                self.assertEqual(decoded, value)
                self.assertIs(type(decoded), type(value))

    def test_handoff_wire_schema_rejects_unknown_nested_fields_and_raw_enums(self) -> None:
        state = active_handoff(policy=optional_email_policy())
        original = json.loads(to_wire_json(state))
        mutations = []

        unknown_request = json.loads(json.dumps(original))
        unknown_request["data"]["request"]["lead_text"] = "route me"
        mutations.append(unknown_request)

        raw_status = json.loads(json.dumps(original))
        raw_status["data"]["status"] = "ACKNOWLEDGED"
        mutations.append(raw_status)

        raw_reason = json.loads(json.dumps(original))
        raw_reason["data"]["request"]["reason_code"] = "please_handoff"
        mutations.append(raw_reason)

        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                from_wire_json(json.dumps(mutation), HandoffWorkflow)

    def test_workflow_wire_reapplies_effect_bindings_and_policy(self) -> None:
        pending = active_handoff(policy=optional_email_policy())
        acknowledged = reduce_handoff(
            pending,
            handoff_acknowledged(pending),
        ).state
        invalid_receipt = json.loads(to_wire_json(acknowledged))
        invalid_receipt["data"]["acknowledgement"]["effect_id"] = (
            "handoff-effect:synthetic:divergent"
        )

        failed = reduce_handoff(
            pending,
            handoff_effect_failed(pending),
        ).state
        disabled_failure = json.loads(to_wire_json(failed))
        disabled_failure["data"]["policy"]["internal_email"] = "disabled"

        for mutation in (invalid_receipt, disabled_failure):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                from_wire_json(json.dumps(mutation), HandoffWorkflow)

    def test_workflow_wire_rejects_status_history_and_chronology_mismatches(self) -> None:
        pending = active_handoff(policy=optional_email_policy())
        conflict_event = handoff_requested(
            handoff_id="handoff:synthetic:conflict",
            source_event_id="source:event:synthetic:conflict",
            reason_code=HandoffReasonCode.OPERATIONAL_REVIEW,
            requested_at=T0 + timedelta(seconds=1),
        )
        conflicted = reduce_handoff(pending, conflict_event).state
        ack_failure = handoff_effect_failed(
            pending,
            kind=HandoffEffectKind.CUSTOMER_ACKNOWLEDGEMENT,
        )
        failed = reduce_handoff(pending, ack_failure).state
        cancelled = reduce_handoff(pending, handoff_cancelled(pending)).state

        mutations = []
        pending_with_conflict = json.loads(to_wire_json(conflicted))
        pending_with_conflict["data"]["status"] = "acknowledgement_pending"
        mutations.append(pending_with_conflict)

        pending_with_required_failure = json.loads(to_wire_json(failed))
        pending_with_required_failure["data"]["status"] = "acknowledgement_pending"
        mutations.append(pending_with_required_failure)

        requested_with_cancellation = json.loads(to_wire_json(cancelled))
        requested_with_cancellation["data"]["status"] = "requested"
        mutations.append(requested_with_cancellation)

        active_with_cancellation = json.loads(to_wire_json(cancelled))
        active_with_cancellation["data"]["status"] = "active"
        active_with_cancellation["data"]["queue_active"] = True
        mutations.append(active_with_cancellation)

        acknowledged = reduce_handoff(
            pending,
            handoff_acknowledged(
                pending,
                acknowledged_at=T0 + timedelta(seconds=2),
            ),
        ).state
        cancelled_after_ack = reduce_handoff(
            acknowledged,
            handoff_cancelled(
                acknowledged,
                cancelled_at=T0 + timedelta(seconds=3),
            ),
        ).state
        cancellation_before_ack = json.loads(to_wire_json(cancelled_after_ack))
        cancellation_before_ack["data"]["acknowledgement"]["acknowledged_at"] = (
            (T0 + timedelta(seconds=4)).isoformat()
        )
        mutations.append(cancellation_before_ack)

        completed_with_cancellation = json.loads(to_wire_json(cancelled))
        completed_with_cancellation["data"]["status"] = "completed"
        mutations.append(completed_with_cancellation)

        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                from_wire_json(json.dumps(mutation), HandoffWorkflow)

    def test_public_projection_constructor_and_wire_reject_arbitrary_text(self) -> None:
        private_texts = (
            "b" * 64,
            "handoff:synthetic:private",
            HandoffReasonCode.CUSTOMER_REQUESTED.value,
            "texto arbitrário fornecido pelo caller",
        )
        for public_text in private_texts:
            with self.subTest(public_text=public_text), self.assertRaises(ValueError):
                PublicHandoffProjection(
                    public_text=public_text,
                    next_action=PublicNextAction.WAIT_FOR_HUMAN,
                    reservation_outcome=None,
                )

        valid = project_handoff_public_reply(active_handoff(), None)
        mutation = json.loads(to_wire_json(valid))
        mutation["data"]["public_text"] = "texto arbitrário fornecido pelo wire"
        with self.assertRaises(ValueError):
            from_wire_json(json.dumps(mutation), PublicHandoffProjection)

    def test_ephemeral_transition_and_subclasses_are_outside_wire_registry(self) -> None:
        transition = new_handoff(
            handoff_requested(),
            HandoffEffectPolicy.default_email_disabled(),
        )

        class InventedRequest(HandoffRequested):
            pass

        request = transition.state.request
        invented = InventedRequest(
            **{field.name: getattr(request, field.name) for field in fields(request)}
        )
        for value in (transition, invented, {"type": "handoff_requested"}):
            with self.subTest(value=type(value).__name__), self.assertRaises(TypeError):
                to_wire_json(value)
        with self.assertRaises(TypeError):
            from_wire_json(to_wire_json(request), InventedRequest)


if __name__ == "__main__":
    unittest.main()
