"""Fail-closed legacy LeadState import into the Phase 7 boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from reservation_domain import (
    CollectingState,
    CommercialDraft,
    ExecutionQueuedState,
    SelectedState,
    ServiceKind,
    SucceededState,
    UncertainState,
    dumps_outcome,
    dumps_state,
)
from reservation_confirmation import SummaryLocale, render_summary
from reservation_followup import (
    BusinessUnit,
    ConfirmedReservationAnchor,
    HandoffStatus,
    PaymentStatus,
    PaymentWorkflow,
    new_payment,
    to_wire_json as to_phase6_wire_json,
)
from reservation_boundary.legacy_state import import_legacy_state
from reservation_boundary.serialization import (
    from_wire_json as from_boundary_wire_json,
    to_wire_json as to_boundary_wire_json,
)
from reservation_boundary.types import (
    ImportDisposition,
    ImportReason,
    LegacyLeadSnapshot,
)
from tests.phase6_helpers import payment_effect_policy
from tests.test_phase2_serialization import all_domain_samples, complete_flow


UTC = timezone.utc
T0 = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
ROOT_FIELDS = {
    "phone",
    "subscriber_id",
    "lead_key",
    "language",
    "is_foreign",
    "ai_status",
    "stage",
    "desired_services",
    "missing_slots",
    "memory_long",
    "hostel_reservations",
    "agency_bookings",
    "metadata",
}


def _canonical(fields: dict[str, object]) -> str:
    return json.dumps(
        fields,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def snapshot(**changes: object) -> LegacyLeadSnapshot:
    fields: dict[str, object] = {
        "phone": "+5500000000000",
        "subscriber_id": "subscriber-synthetic-001",
        "lead_key": "lead-synthetic-001",
        "language": "pt-BR",
        "is_foreign": False,
        "ai_status": "active",
        "stage": "new",
        "desired_services": [],
        "missing_slots": [],
        "memory_long": "",
        "hostel_reservations": [],
        "agency_bookings": [],
        "metadata": {
            "workflow_id": "workflow-synthetic-001",
            "state_updated_at": T0.isoformat(),
        },
    }
    fields.update(changes)
    self_check = set(fields)
    if self_check != ROOT_FIELDS:
        raise AssertionError((sorted(ROOT_FIELDS - self_check), sorted(self_check - ROOT_FIELDS)))
    canonical = _canonical(fields)
    return LegacyLeadSnapshot(
        schema_version=1,
        source="chapada-leads-hermes",
        raw_fields=fields,
        canonical_json=canonical,
        snapshot_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def handoff_metadata(**changes: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "handoff_id": "handoff:synthetic:001",
        "incident_key": "incident:synthetic:001",
        "handoff_source_event_id": "event:synthetic:handoff:001",
        "handoff_requested_at": T0.isoformat(),
        "handoff_reason_code": "customer_requested",
    }
    metadata.update(changes)
    return metadata


def _offer_ids(state: object) -> tuple[str, ...]:
    if isinstance(state, SelectedState):
        return (state.offer.offer_id,)
    if hasattr(state, "draft"):
        return tuple(item.offer_id for item in state.draft.components)
    if hasattr(state, "command"):
        return tuple(item.offer_id for item in state.command.payload.components)
    return ()


def advanced_metadata(state: object, **changes: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "workflow_id": state.meta.workflow_id,
        "state_updated_at": state.meta.last_event_at.isoformat(),
        "phase2_workflow_wire": dumps_state(state),
    }
    offer_ids = _offer_ids(state)
    if len(offer_ids) == 1:
        metadata["selected_offer_id"] = offer_ids[0]
    elif offer_ids:
        metadata["selected_offer_ids"] = list(offer_ids)
    if hasattr(state, "draft"):
        metadata["summary_version"] = state.draft.version
        metadata["confirmation_signature"] = state.draft.subject_signature
        metadata["rendered_summary_hash"] = render_summary(
            state.draft,
            locale=SummaryLocale.PT_BR,
        ).content_hash
    elif hasattr(state, "command"):
        metadata["summary_version"] = state.command.draft_version
        metadata["confirmation_signature"] = state.command.subject_signature
        draft = CommercialDraft(
            draft_id=state.command.draft_id,
            version=state.command.draft_version,
            created_at=state.command.created_at,
            components=state.command.payload.components,
            customer=state.command.payload.customer,
            terms=state.command.payload.terms,
            subject_signature=state.command.subject_signature,
        )
        metadata["rendered_summary_hash"] = render_summary(
            draft,
            locale=SummaryLocale.PT_BR,
        ).content_hash
    metadata.update(changes)
    return metadata


def _succeeded_state() -> SucceededState:
    states, _, _ = all_domain_samples()
    return next(state for state in states if isinstance(state, SucceededState))


def _uncertain_state() -> UncertainState:
    states, _, _ = all_domain_samples()
    return next(state for state in states if isinstance(state, UncertainState))


def payment_fixture(
    *,
    target_id: str = "reservation-serializer",
) -> tuple[LegacyLeadSnapshot, SucceededState, PaymentWorkflow, dict[str, object]]:
    state = _succeeded_state()
    outcome = state.outcome
    anchor = ConfirmedReservationAnchor(
        reservation_workflow_id=state.meta.workflow_id,
        reservation_command_id=state.command.command_id,
        reservation_subject_signature=state.command.subject_signature,
        reservation_outcome_hash=hashlib.sha256(
            dumps_outcome(outcome).encode("utf-8")
        ).hexdigest(),
        reservation_outcome=outcome,
        provider_reference=outcome.provider_reference,
        service=ServiceKind.ACTIVITY,
        business_unit=BusinessUnit.AGENCY,
        payment_target_id=target_id,
        amount_minor=55000,
        currency="BRL",
        receiver_profile_id="receiver:agency:synthetic:001",
        confirmed_at=state.meta.last_event_at,
        payment_deadline=state.meta.last_event_at + timedelta(days=2),
    )
    payment = new_payment(anchor, payment_effect_policy()).state
    reservation = {
        "id": "reservation-serializer",
        "service": "agency",
        "status": "confirmed",
        "amount_due": 550.0,
        "currency": "BRL",
        "created_at": state.meta.last_event_at.isoformat(),
        "payment_expires_at": anchor.payment_deadline.isoformat(),
        "payment_status": "pending",
        "payment_method": "",
        "payment_confirmed_at": "",
    }
    metadata = advanced_metadata(
        state,
        phase6_payment_wires=[to_phase6_wire_json(payment)],
    )
    value = snapshot(
        stage="payment_pending",
        agency_bookings=[reservation],
        metadata=metadata,
    )
    return value, state, payment, reservation


class Phase7LegacyStateTests(unittest.TestCase):
    def test_collecting_state_migrates_without_authorization(self) -> None:
        result = import_legacy_state(snapshot())
        self.assertIs(result.disposition, ImportDisposition.MIGRATED)
        self.assertIs(result.reason, ImportReason.NONE)
        self.assertIsNotNone(result.state)
        self.assertIsInstance(result.state.workflow, CollectingState)
        self.assertIsNone(result.state.handoff)
        self.assertEqual(result.state.payments, ())
        self.assertEqual(result.state.processed_event_ids, ())

    def test_public_name_never_reconstructs_offer_or_product_id(self) -> None:
        result = import_legacy_state(
            snapshot(
                stage="fechamento",
                metadata={
                    "workflow_id": "workflow-synthetic-001",
                    "state_updated_at": T0.isoformat(),
                    "room_name": "Suíte casal",
                    "activity_name": "Cachoeira bonita",
                },
            )
        )
        self.assertIs(result.disposition, ImportDisposition.MANUAL_REVIEW)
        self.assertIsNone(result.state)

    def test_conflicting_canonical_identity_is_rejected(self) -> None:
        result = import_legacy_state(
            snapshot(
                metadata={
                    "workflow_id": "workflow-synthetic-001",
                    "state_updated_at": T0.isoformat(),
                    "selected_offer_id": "offer-001",
                    "offer_id": "offer-002",
                }
            )
        )
        self.assertIs(result.disposition, ImportDisposition.REJECTED)
        self.assertIs(result.reason, ImportReason.CONFLICTING_IDENTITY)
        self.assertIsNone(result.state)

    def test_handoff_has_terminal_precedence_over_stale_advanced_metadata(self) -> None:
        metadata = handoff_metadata(
            selected_offer_id="offer-stale-001",
            summary_version=2,
            confirmation_signature="a" * 64,
        )
        result = import_legacy_state(
            snapshot(stage="handoff", ai_status="paused", metadata=metadata)
        )
        self.assertIs(result.disposition, ImportDisposition.MIGRATED)
        self.assertIsNone(result.state.workflow)
        self.assertIsNotNone(result.state.handoff)
        self.assertIs(result.state.handoff.status, HandoffStatus.ACKNOWLEDGEMENT_PENDING)
        self.assertTrue(result.state.handoff.queue_active)
        self.assertEqual(
            result.state.processed_event_ids,
            ("event:synthetic:handoff:001",),
        )

    def test_collecting_requires_explicit_workflow_and_timestamp_provenance(self) -> None:
        for metadata in (
            {},
            {"workflow_id": "workflow-synthetic-001"},
            {"state_updated_at": T0.isoformat()},
            {
                "workflow_id": "workflow-synthetic-001",
                "state_updated_at": "2026-07-20T12:00:00",
            },
        ):
            with self.subTest(metadata=metadata):
                result = import_legacy_state(snapshot(metadata=metadata))
                self.assertIs(result.disposition, ImportDisposition.MANUAL_REVIEW)
                self.assertIs(result.reason, ImportReason.MISSING_PROVENANCE)
                self.assertIsNone(result.state)

    def test_stage_matrix_is_closed(self) -> None:
        for stage in ("new", "hostel", "agencia"):
            with self.subTest(stage=stage):
                self.assertIs(
                    import_legacy_state(snapshot(stage=stage)).disposition,
                    ImportDisposition.MIGRATED,
                )
        for stage in ("fechamento", "no_reply"):
            with self.subTest(stage=stage):
                self.assertIs(
                    import_legacy_state(snapshot(stage=stage)).disposition,
                    ImportDisposition.MANUAL_REVIEW,
                )
        result = import_legacy_state(snapshot(stage="unknown-stage"))
        self.assertIs(result.disposition, ImportDisposition.REJECTED)
        self.assertIs(result.reason, ImportReason.UNSUPPORTED_STAGE)

    def test_invalid_identity_schema_shape_and_snapshot_integrity_fail_closed(self) -> None:
        invalid_identity = import_legacy_state(snapshot(lead_key=""))
        self.assertIs(invalid_identity.disposition, ImportDisposition.REJECTED)
        self.assertIs(invalid_identity.reason, ImportReason.MISSING_IDENTITY)

        valid = snapshot()
        bad_schema = LegacyLeadSnapshot(
            schema_version=2,
            source=valid.source,
            raw_fields=valid.raw_fields,
            canonical_json=valid.canonical_json,
            snapshot_hash=valid.snapshot_hash,
        )
        self.assertIs(
            import_legacy_state(bad_schema).reason,
            ImportReason.UNSUPPORTED_SCHEMA,
        )
        bad_hash = LegacyLeadSnapshot(
            schema_version=1,
            source=valid.source,
            raw_fields=valid.raw_fields,
            canonical_json=valid.canonical_json,
            snapshot_hash="f" * 64,
        )
        malformed = import_legacy_state(bad_hash)
        self.assertIs(malformed.disposition, ImportDisposition.REJECTED)
        self.assertIs(malformed.reason, ImportReason.MALFORMED)

    def test_reservations_and_advanced_identity_are_not_silently_downgraded(self) -> None:
        result = import_legacy_state(
            snapshot(
                hostel_reservations=[{"id": "reservation-001", "service": "hostel"}],
            )
        )
        self.assertIs(result.disposition, ImportDisposition.MANUAL_REVIEW)
        self.assertIs(result.reason, ImportReason.MISSING_PROVENANCE)
        self.assertIsNone(result.state)

    def test_import_is_deterministic_and_does_not_mutate_snapshot(self) -> None:
        source = snapshot(stage="hostel")
        before = source.raw_fields
        first = import_legacy_state(source)
        second = import_legacy_state(source)
        self.assertEqual(first, second)
        self.assertIs(source.raw_fields, before)
        self.assertEqual(hash(first), hash(second))

    def test_selected_state_preserves_only_opaque_offer_identity(self) -> None:
        states, _, _ = complete_flow()
        selected = states[3]
        self.assertIsInstance(selected, SelectedState)
        value = snapshot(stage="fechamento", metadata=advanced_metadata(selected))
        first = import_legacy_state(value)
        second = import_legacy_state(value)
        self.assertIs(first.disposition, ImportDisposition.MIGRATED)
        self.assertEqual(first, second)
        self.assertEqual(first.state.workflow, selected)
        self.assertEqual(first.state.workflow.offer.offer_id, "offer-serializer")
        self.assertEqual(first.state.payments, ())

    def test_confirmed_state_preserves_signature_without_emitting_new_command(self) -> None:
        states, _, command = complete_flow()
        queued = states[-1]
        self.assertIsInstance(queued, ExecutionQueuedState)
        value = snapshot(stage="fechamento", metadata=advanced_metadata(queued))
        result = import_legacy_state(value)
        self.assertIs(result.disposition, ImportDisposition.MIGRATED)
        self.assertEqual(result.state.workflow, queued)
        self.assertEqual(result.state.workflow.command, command)
        self.assertEqual(
            result.state.workflow.command.subject_signature,
            result.state.workflow.confirmation.subject_signature,
        )
        self.assertEqual(result.state.payments, ())

        mismatch = import_legacy_state(
            snapshot(
                stage="fechamento",
                metadata=advanced_metadata(
                    queued,
                    rendered_summary_hash="f" * 64,
                ),
            )
        )
        self.assertIs(mismatch.disposition, ImportDisposition.REJECTED)
        self.assertIs(mismatch.reason, ImportReason.INCONSISTENT_CONFIRMATION)

    def test_payment_workflow_preserves_confirmed_anchor_and_binding(self) -> None:
        value, state, payment, _ = payment_fixture()
        result = import_legacy_state(value)
        self.assertIs(result.disposition, ImportDisposition.MIGRATED)
        self.assertEqual(result.state.workflow, state)
        self.assertEqual(result.state.payments, (payment,))
        self.assertIs(payment.status, PaymentStatus.AWAITING_METHOD)
        self.assertEqual(
            result.state.payments[0].subject.confirmed_reservation_anchor,
            payment.subject.confirmed_reservation_anchor,
        )
        wire = to_boundary_wire_json(result.state)
        self.assertEqual(
            from_boundary_wire_json(wire, type(result.state)),
            result.state,
        )

    def test_duplicate_subject_and_payment_anchor_mismatch_are_rejected(self) -> None:
        _, state, payment, reservation = payment_fixture()
        metadata = advanced_metadata(
            state,
            phase6_payment_wires=[to_phase6_wire_json(payment)],
        )
        duplicate = import_legacy_state(
            snapshot(
                stage="payment_pending",
                agency_bookings=[reservation, reservation],
                metadata=metadata,
            )
        )
        self.assertIs(duplicate.disposition, ImportDisposition.REJECTED)
        self.assertIs(duplicate.reason, ImportReason.AMBIGUOUS_IDENTITY)

        mismatched, _, _, _ = payment_fixture(target_id="different-target")
        mismatch = import_legacy_state(mismatched)
        self.assertIs(mismatch.disposition, ImportDisposition.REJECTED)
        self.assertIs(mismatch.reason, ImportReason.UNVERIFIED_PAYMENT)

    def test_missing_wire_and_unknown_historical_outcome_route_to_review(self) -> None:
        missing = import_legacy_state(
            snapshot(
                stage="fechamento",
                metadata={
                    "workflow_id": "workflow-serializer",
                    "state_updated_at": T0.isoformat(),
                    "selected_offer_id": "offer-serializer",
                    "confirmation_signature": "a" * 64,
                },
            )
        )
        self.assertIs(missing.disposition, ImportDisposition.MANUAL_REVIEW)
        self.assertIs(missing.reason, ImportReason.MISSING_PROVENANCE)

        uncertain = _uncertain_state()
        unknown = import_legacy_state(
            snapshot(
                stage="payment_pending",
                metadata=advanced_metadata(uncertain),
            )
        )
        self.assertIs(unknown.disposition, ImportDisposition.MANUAL_REVIEW)
        self.assertIs(unknown.reason, ImportReason.UNKNOWN_HISTORICAL_OUTCOME)
        self.assertIsNone(unknown.state)


if __name__ == "__main__":
    unittest.main()
