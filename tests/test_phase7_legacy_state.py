"""Fail-closed legacy LeadState import into the Phase 7 boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import unittest

from reservation_domain import CollectingState
from reservation_followup import HandoffStatus
from reservation_boundary.legacy_state import import_legacy_state
from reservation_boundary.types import (
    ImportDisposition,
    ImportReason,
    LegacyLeadSnapshot,
)


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


if __name__ == "__main__":
    unittest.main()
