"""Phase 8 TurnReceipt atomic-store owner contract."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from reservation_boundary.conversation import PublicReplyChunk, SourceEventIdentity
from reservation_boundary import sqlite_store


T0 = datetime(1970, 1, 1, tzinfo=timezone.utc)


class Phase8TurnReceiptTests(unittest.TestCase):
    def _type(self):
        receipt_type = getattr(sqlite_store, "TurnReceipt", None)
        self.assertIsNotNone(receipt_type, "TurnReceipt must be owned by the v8 atomic store")
        return receipt_type

    def _kwargs(self, *, e2e: bool = False, later: bool = False) -> dict[str, object]:
        chunk = PublicReplyChunk("turn-1", 0, "Olá", "1" * 64)
        return {
            "aggregate_turn_id": "turn-1",
            "event_hash": "0" * 64,
            "source_events": (SourceEventIdentity("event-1", "1" * 64),),
            "maya_proposal_hash": "2" * 64,
            "kernel_decision_hash": "3" * 64,
            "read_observations": (),
            "committed_state_version": 1,
            "committed_state_hash": "4" * 64,
            "public_chunks": (
                ("public-row-1", 0, chunk.to_canonical_bytes(), chunk.canonical_hash()),
            ),
            "command_rows": (),
            "relay_rows": (),
            "internal_outbox_rows": (),
            "uds_transcript_mac": "6" * 64,
            "uds_final_seq": 1,
            "structural_graph_digest": "7" * 64,
            "capability_policy_digest": "8" * 64,
            "effective_stage_binding_digest": "9" * 64,
            "behavior_state_snapshot_digest": "a" * 64,
            "qualification_id": "qualification-1" if e2e else None,
            "admission_sequence": 1 if e2e else None,
            "admission_revision": 1 if e2e else None,
            "commit_fence_token": 1 if e2e else None,
            "allocation_manifest_hash": "b" * 64 if e2e else None,
            "immutable_generation": 1 if e2e else None,
            "allocation_ids": ("allocation-1",) if e2e else None,
            "committed_at": T0,
            "previous_turn_receipt_hash": "c" * 64 if later else None,
        }

    def _receipt(self, *, e2e: bool = False, later: bool = False):
        receipt_type = self._type()
        create = getattr(receipt_type, "create", None)
        self.assertIsNotNone(create, "TurnReceipt.create must derive artifact_hash")
        return create(**self._kwargs(e2e=e2e, later=later))

    def test_receipt_exact_fields_schema_version_domain_and_kat(self) -> None:
        receipt_type = self._type()
        receipt = self._receipt()
        self.assertEqual(
            tuple(field.name for field in fields(receipt_type)),
            (
                "aggregate_turn_id", "event_hash", "source_events",
                "maya_proposal_hash", "kernel_decision_hash", "read_observations",
                "committed_state_version", "committed_state_hash", "public_chunks",
                "command_rows", "relay_rows", "internal_outbox_rows",
                "uds_transcript_mac", "uds_final_seq", "structural_graph_digest",
                "capability_policy_digest", "effective_stage_binding_digest",
                "behavior_state_snapshot_digest", "qualification_id",
                "admission_sequence", "admission_revision", "commit_fence_token",
                "allocation_manifest_hash", "immutable_generation", "allocation_ids",
                "committed_at", "previous_turn_receipt_hash", "artifact_hash",
            ),
        )
        self.assertEqual(receipt_type.SCHEMA, "phase8-turn-receipt")
        self.assertEqual(receipt_type.VERSION, 1)
        self.assertEqual(receipt_type.DOMAIN, "phase8-turn-receipt-v1")
        self.assertEqual(receipt_type.PREIMAGE_SCHEMA, "phase8-turn-receipt-artifact-preimage")
        self.assertEqual(
            receipt.artifact_hash,
            "0692d9473a9ab06e639409e838290465d94a64a2c3a50b622dd45bedd978767b",
        )
        self.assertEqual(
            receipt.canonical_hash(),
            "9952e97c65c59ca3ff59c347c47351672ecd3d570bf311f50023863cfbb3bc3a",
        )
        self.assertEqual(
            receipt_type.from_canonical_bytes(receipt.to_canonical_bytes()),
            receipt,
        )

    def test_receipt_first_later_and_e2e_nullable_matrices_are_closed(self) -> None:
        receipt_type = self._type()
        self.assertIsNone(self._receipt().previous_turn_receipt_hash)
        self.assertEqual(self._receipt(later=True).previous_turn_receipt_hash, "c" * 64)
        self.assertEqual(self._receipt(e2e=True).allocation_ids, ("allocation-1",))

        base = self._kwargs()
        for field_name, invalid in (
            ("qualification_id", "qualification-1"),
            ("admission_sequence", 1),
            ("admission_revision", 1),
            ("commit_fence_token", 1),
            ("allocation_manifest_hash", "b" * 64),
            ("immutable_generation", 1),
            ("allocation_ids", ("allocation-1",)),
        ):
            with self.subTest(field=field_name), self.assertRaises(ValueError):
                receipt_type.create(**(base | {field_name: invalid}))
        for invalid in ((), ("allocation-1", "allocation-1"), ["allocation-1"]):
            with self.subTest(allocation_ids=invalid), self.assertRaises((TypeError, ValueError)):
                receipt_type.create(**(self._kwargs(e2e=True) | {"allocation_ids": invalid}))
        for field_name in (
            "admission_sequence", "admission_revision", "commit_fence_token",
            "immutable_generation", "committed_state_version", "uds_final_seq",
        ):
            with self.subTest(field=field_name), self.assertRaises((TypeError, ValueError)):
                receipt_type.create(**(self._kwargs(e2e=True) | {field_name: True}))
        with self.assertRaises(ValueError):
            receipt_type.create(
                **(
                    base
                    | {
                        "committed_at": T0.astimezone(
                            timezone(timedelta(hours=1))
                        )
                    }
                )
            )

    def test_receipt_child_counts_ids_hashes_and_order_are_exact(self) -> None:
        receipt_type = self._type()
        base = self._kwargs()
        duplicate_source = SourceEventIdentity("event-1", "d" * 64)
        with self.assertRaises(ValueError):
            receipt_type.create(**(base | {"source_events": base["source_events"] + (duplicate_source,)}))
        with self.assertRaises(ValueError):
            receipt_type.create(**(base | {"source_events": ()}))

        chunk0 = base["public_chunks"][0]
        chunk1_value = PublicReplyChunk("turn-1", 1, "Mundo", "1" * 64)
        chunk1 = (
            "public-row-2", 1, chunk1_value.to_canonical_bytes(),
            chunk1_value.canonical_hash(),
        )
        self.assertEqual(
            tuple(row[1] for row in receipt_type.create(
                **(base | {"public_chunks": (chunk0, chunk1)})
            ).public_chunks),
            (0, 1),
        )
        for rows in (
            (("public-row-2", 2, chunk1[2], chunk1[3]),),
            (("public-row-1", 1, chunk1[2], chunk1[3]),),
            (("public-row-2", 1, chunk1[2] + b" ", chunk1[3]),),
            (("public-row-2", 1, chunk1[2], "e" * 64),),
        ):
            with self.subTest(rows=rows), self.assertRaises((TypeError, ValueError)):
                receipt_type.create(**(base | {"public_chunks": rows}))

        for field_name in ("command_rows", "relay_rows", "internal_outbox_rows"):
            with self.subTest(field=field_name), self.assertRaises(ValueError):
                receipt_type.create(
                    **(base | {field_name: (("row-1", "d" * 64), ("row-1", "d" * 64))})
                )

    def test_receipt_decoder_rejects_hostile_noncanonical_bytes(self) -> None:
        receipt_type = self._type()
        receipt = self._receipt()
        payload = receipt.to_canonical_bytes()
        decoded = json.loads(payload)

        hostile: list[bytes] = []
        hostile.append(payload + b" ")
        unknown = json.loads(payload)
        unknown["data"]["unknown"] = 1
        hostile.append(json.dumps(unknown, sort_keys=True, separators=(",", ":")).encode())
        wrong_hash = json.loads(payload)
        wrong_hash["data"]["artifact_hash"] = "f" * 64
        hostile.append(json.dumps(wrong_hash, sort_keys=True, separators=(",", ":")).encode())
        needle = b'"event_hash":"' + b"0" * 64 + b'"'
        hostile.append(payload.replace(needle, needle + b"," + needle, 1))
        wrong_schema = json.loads(payload)
        wrong_schema["schema"] = "phase8-other"
        hostile.append(json.dumps(wrong_schema, sort_keys=True, separators=(",", ":")).encode())

        for candidate in hostile:
            with self.subTest(candidate=candidate[-80:]), self.assertRaises((TypeError, ValueError)):
                receipt_type.from_canonical_bytes(candidate)

        with self.assertRaises(ValueError):
            replace(receipt, artifact_hash="f" * 64)
        self.assertEqual(decoded["data"]["committed_at"], "1970-01-01T00:00:00.000000Z")


if __name__ == "__main__":
    unittest.main()
