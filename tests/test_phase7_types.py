"""Closed immutable boundary type contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from typing import get_args
import unittest

from reservation_domain import ReservationCommand, new_workflow
from reservation_followup import PaymentSettlementCommand
from reservation_boundary.types import (
    ActivityDescriptionArguments,
    ActivityPaymentArguments,
    ActivityReadArguments,
    ActivityReservationArguments,
    BooleanSlot,
    BoundaryCommand,
    BoundaryState,
    CommandMigrationDisposition,
    ConversationIntentKind,
    DateSlot,
    DateTimeSlot,
    DecimalSlot,
    DispatchKind,
    DivergenceSeverity,
    FaqReadArguments,
    ImportDisposition,
    ImportReason,
    ImportResult,
    IntegerSlot,
    LegacyLeadSnapshot,
    LodgingPaymentArguments,
    LodgingReadArguments,
    LodgingReservationArguments,
    NormalizedMessage,
    PUBLIC_TYPES,
    RoomDescriptionArguments,
    StateCommitArguments,
    StripeLinkArguments,
    StringSlot,
    ToolArguments,
    TurnEnvelope,
    TurnLease,
    TurnPlanReason,
    TypedFact,
    WiseVerificationArguments,
)
from tests.phase7_helpers import DEADLINE, NOW, raw_legacy_fields


class Phase7TypeContractTests(unittest.TestCase):
    def test_enum_values_are_closed_and_exact(self) -> None:
        self.assertEqual(
            tuple(item.value for item in ImportDisposition),
            ("migrated", "manual_review", "rejected"),
        )
        self.assertEqual(
            tuple(item.value for item in DispatchKind),
            ("read", "command", "state_commit"),
        )
        self.assertEqual(
            tuple(item.value for item in DivergenceSeverity),
            ("equivalent", "noncritical", "critical"),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationIntentKind),
            ("inform", "select", "adjust", "confirm", "request_handoff", "tool_request"),
        )
        self.assertEqual(
            tuple(item.value for item in CommandMigrationDisposition),
            ("reservation", "payment_settlement", "blocked_unmigrated"),
        )
        self.assertEqual(
            tuple(item.value for item in TurnPlanReason),
            ("completed", "duplicate", "deadline_exceeded", "manual_review"),
        )

    def test_integer_and_boolean_types_are_exact(self) -> None:
        with self.assertRaises(TypeError):
            IntegerSlot(True)
        with self.assertRaises(TypeError):
            BoundaryState(
                schema_version=True,
                lead_key="lead-1",
                version=0,
                workflow=None,
                handoff=None,
                payments=(),
                processed_event_ids=(),
            )
        with self.assertRaises(TypeError):
            TurnLease("lead-1", True, DEADLINE)
        self.assertEqual(IntegerSlot(2).value, 2)
        self.assertIs(BooleanSlot(False).value, False)

    def test_slot_values_are_canonical_and_temporally_strict(self) -> None:
        self.assertEqual(DecimalSlot("12.50").value, "12.50")
        for invalid in ("12.5", "012.50", "-0.00", "nan", 12.5):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    DecimalSlot(invalid)  # type: ignore[arg-type]
        self.assertEqual(DateSlot(date(2026, 7, 20)).value, date(2026, 7, 20))
        with self.assertRaises(TypeError):
            DateSlot(NOW)  # type: ignore[arg-type]
        self.assertEqual(DateTimeSlot(NOW).value, NOW)
        with self.assertRaises(ValueError):
            DateTimeSlot(NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            DateTimeSlot(NOW.astimezone(timezone(timedelta(hours=-3))))

    def test_legacy_snapshot_deep_detaches_freezes_and_hashes(self) -> None:
        source = raw_legacy_fields()
        snapshot = LegacyLeadSnapshot(
            schema_version=1,
            source="chapada-leads-hermes",
            raw_fields=source,
            canonical_json='{"lead_key":"lead-synthetic-001"}',
            snapshot_hash="a" * 64,
        )
        source["stage"] = "mutated"
        source_metadata = source["metadata"]
        self.assertIsInstance(source_metadata, dict)
        source_metadata["selected_offer_id"] = "mutated"  # type: ignore[index]
        self.assertEqual(snapshot.raw_fields["stage"], "hostel")
        self.assertEqual(
            snapshot.raw_fields["metadata"]["selected_offer_id"],  # type: ignore[index]
            "offer-001",
        )
        with self.assertRaises(TypeError):
            snapshot.raw_fields["stage"] = "forbidden"  # type: ignore[index]
        self.assertIsInstance(hash(snapshot), int)

    def test_import_result_rejects_impossible_combinations(self) -> None:
        state = BoundaryState(
            schema_version=7,
            lead_key="lead-1",
            version=0,
            workflow=None,
            handoff=None,
            payments=(),
            processed_event_ids=(),
        )
        migrated = ImportResult(ImportDisposition.MIGRATED, state, ImportReason.NONE)
        self.assertEqual(migrated.state, state)
        with self.assertRaises(ValueError):
            ImportResult(ImportDisposition.MIGRATED, None, ImportReason.NONE)
        with self.assertRaises(ValueError):
            ImportResult(ImportDisposition.MANUAL_REVIEW, state, ImportReason.MALFORMED)
        with self.assertRaises(ValueError):
            ImportResult(ImportDisposition.REJECTED, None, ImportReason.NONE)

    def test_boundary_state_accepts_only_exact_closed_universe_states(self) -> None:
        workflow = new_workflow(workflow_id="workflow-1", started_at=NOW)
        state = BoundaryState(
            schema_version=7,
            lead_key="lead-1",
            version=0,
            workflow=workflow,
            handoff=None,
            payments=(),
            processed_event_ids=(),
        )
        self.assertIs(state.workflow, workflow)
        with self.assertRaises(TypeError):
            BoundaryState(
                schema_version=7,
                lead_key="lead-1",
                version=0,
                workflow=object(),  # type: ignore[arg-type]
                handoff=None,
                payments=(),
                processed_event_ids=(),
            )

    def test_boundary_command_union_is_exact(self) -> None:
        self.assertEqual(
            frozenset(get_args(BoundaryCommand)),
            frozenset((ReservationCommand, PaymentSettlementCommand)),
        )

    def test_tool_arguments_union_and_state_commit_are_closed(self) -> None:
        expected = {
            FaqReadArguments,
            LodgingReadArguments,
            RoomDescriptionArguments,
            ActivityReadArguments,
            ActivityDescriptionArguments,
            LodgingReservationArguments,
            ActivityReservationArguments,
            LodgingPaymentArguments,
            ActivityPaymentArguments,
            WiseVerificationArguments,
            StripeLinkArguments,
            StateCommitArguments,
        }
        self.assertEqual(set(get_args(ToolArguments)), expected)
        fact = TypedFact("guest_count", IntegerSlot(2))
        commit = StateCommitArguments((fact,))
        self.assertEqual(commit.facts, (fact,))
        with self.assertRaises(TypeError):
            StateCommitArguments([fact])  # type: ignore[arg-type]

    def test_turn_deadline_and_lease_are_strict_utc(self) -> None:
        message = NormalizedMessage("hello", "en")
        envelope = TurnEnvelope("lead-1", "event-1", message, NOW, DEADLINE)
        self.assertEqual(envelope.deadline, DEADLINE)
        self.assertIsInstance(hash(envelope), int)
        with self.assertRaises(ValueError):
            TurnEnvelope("lead-1", "event-1", message, NOW, NOW)
        with self.assertRaises(ValueError):
            TurnEnvelope(
                "lead-1",
                "event-1",
                message,
                NOW,
                DEADLINE.replace(tzinfo=None),
            )
        lease = TurnLease("lead-1", 1, DEADLINE)
        with self.assertRaises(FrozenInstanceError):
            lease.token = 2  # type: ignore[misc]

    def test_public_type_registry_is_unique_closed_and_stable(self) -> None:
        names = tuple(item.__name__ for item in PUBLIC_TYPES)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names, tuple(sorted(names)))
        for required in (
            "BoundaryState",
            "ConversationIntent",
            "ImportResult",
            "KernelDecision",
            "LegacyLeadSnapshot",
            "ToolDispatchRequest",
            "TurnEnvelope",
            "TurnPlan",
        ):
            self.assertIn(required, names)

    def test_text_fields_reject_empty_controls_and_wrong_exact_types(self) -> None:
        self.assertEqual(StringSlot("value").value, "value")
        with self.assertRaises(ValueError):
            StringSlot("")
        with self.assertRaises(ValueError):
            NormalizedMessage("hello\u0000", "en")
        with self.assertRaises(TypeError):
            TypedFact("guest_count", 2)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
