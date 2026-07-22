"""Focused package/wire closeout checks for the Phase 8 Task 1 surface."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib
import unittest

import reservation_boundary as boundary
from reservation_boundary import conversation, effects, qualification, reads


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Phase8WireV8Tests(unittest.TestCase):
    def test_package_metadata_and_public_runtime_version_are_0_8_0(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["version"], "0.8.0")
        self.assertEqual(boundary.__version__, "0.8.0")

    def test_task1_phase8_exports_are_exact_owner_objects(self) -> None:
        expected = {
            "SourceEventIdentity": conversation.SourceEventIdentity,
            "ConversationProjection": conversation.ConversationProjection,
            "ReservationExecutionProjection": conversation.ReservationExecutionProjection,
            "MayaTurnRequest": conversation.MayaTurnRequest,
            "MayaIntentClosure": conversation.MayaIntentClosure,
            "MayaTurnClosure": conversation.MayaTurnClosure,
            "TranscriptCommitment": conversation.TranscriptCommitment,
            "CapabilityPolicy": conversation.CapabilityPolicy,
            "FoundSnapshot": reads.FoundSnapshot,
            "ProvenAbsent": reads.ProvenAbsent,
            "LegacyUnavailable": reads.LegacyUnavailable,
            "ReadObservation": reads.ReadObservation,
            "ReservationRelayBundle": effects.ReservationRelayBundle,
            "SettlementRelayBundle": effects.SettlementRelayBundle,
            "BehaviorStateSnapshot": qualification.BehaviorStateSnapshot,
            "ScenarioTerminalVerificationReceipt": (
                qualification.ScenarioTerminalVerificationReceipt
            ),
        }
        for name, owner_object in expected.items():
            with self.subTest(name=name):
                self.assertIs(getattr(boundary, name), owner_object)
                self.assertIn(name, boundary.__all__)

    def test_authenticated_registries_remain_structurally_complete(self) -> None:
        facts = json.loads(
            (FIXTURES / "phase8_facts_reads_wire_v1.json").read_text(encoding="utf-8")
        )
        remaining_path = FIXTURES / "phase8_remaining_wire_registry_v1.json"
        remaining_bytes = remaining_path.read_bytes()
        remaining = json.loads(remaining_bytes)

        self.assertEqual(len(facts["examples"]), 45)
        self.assertEqual(len(facts["auxiliary_preimages"]), 18)
        self.assertEqual(len(remaining["enums"]), 60)
        self.assertEqual(len(remaining["external_contracts"]), 11)
        contracts = tuple(
            contract
            for family in remaining["families"].values()
            for contract in family
        )
        self.assertEqual(len(contracts), 39)
        self.assertEqual(
            tuple(item["name"] for item in remaining["known_answer_catalog"]),
            tuple(contract["name"] for contract in contracts),
        )
        self.assertTrue(remaining_bytes.endswith(b"\n"))
        self.assertEqual(remaining_bytes.count(b"\n"), 1)


if __name__ == "__main__":
    unittest.main()
