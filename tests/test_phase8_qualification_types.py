"""Focused closed qualification contracts for Phase 8."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import hashlib
import importlib
import json
import unittest


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _qualification_module() -> object | None:
    try:
        return importlib.import_module("reservation_boundary.qualification")
    except ModuleNotFoundError as exc:
        if exc.name != "reservation_boundary.qualification":
            raise
        return None


class Phase8QualificationTypeTests(unittest.TestCase):
    def test_behavior_state_snapshot_fields_and_hash_are_closed(self) -> None:
        module = _qualification_module()
        self.assertIsNotNone(module, "qualification contracts must have an owner")
        assert module is not None
        snapshot_type = getattr(module, "BehaviorStateSnapshot", None)
        self.assertIsNotNone(
            snapshot_type,
            "BehaviorStateSnapshot must have an owner",
        )
        assert snapshot_type is not None
        self.assertEqual(
            tuple(field.name for field in fields(snapshot_type)),
            ("schema", "version", "memory_snapshot_hash"),
        )
        snapshot = snapshot_type(
            schema="hermes-memory-state-v1",
            version=3,
            memory_snapshot_hash="a" * 64,
        )
        self.assertEqual(snapshot_type.SCHEMA, "phase8-behavior-state-snapshot")
        self.assertEqual(snapshot_type.VERSION, 1)
        self.assertEqual(snapshot_type.DOMAIN, "phase8-behavior-state-snapshot-v1")
        expected = {
            "schema": "phase8-behavior-state-snapshot",
            "version": 1,
            "data": {
                "memory_snapshot_hash": "a" * 64,
                "schema": "hermes-memory-state-v1",
                "version": 3,
            },
        }
        expected_bytes = json.dumps(
            expected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(snapshot.to_canonical_bytes(), expected_bytes)
        self.assertEqual(
            snapshot.canonical_hash(),
            hashlib.sha256(
                b"phase8-behavior-state-snapshot-v1\x00" + expected_bytes
            ).hexdigest(),
        )
        with self.assertRaises(FrozenInstanceError):
            snapshot.version = 4

    def test_behavior_state_snapshot_rejects_open_or_noncanonical_values(self) -> None:
        module = _qualification_module()
        self.assertIsNotNone(module, "qualification contracts must have an owner")
        assert module is not None
        snapshot_type = getattr(module, "BehaviorStateSnapshot", None)
        self.assertIsNotNone(
            snapshot_type,
            "BehaviorStateSnapshot must have an owner",
        )
        assert snapshot_type is not None
        invalid = (
            ("", 1, "a" * 64),
            ("hermes memory", 1, "a" * 64),
            (1, 1, "a" * 64),
            ("hermes-memory-state-v1", True, "a" * 64),
            ("hermes-memory-state-v1", 0, "a" * 64),
            ("hermes-memory-state-v1", 1, "A" * 64),
            ("hermes-memory-state-v1", 1, "short"),
            ("hermes-memory-state-v1", 1, b"a" * 64),
        )
        for schema, version, memory_snapshot_hash in invalid:
            with self.subTest(
                schema=schema,
                version=version,
                memory_snapshot_hash=memory_snapshot_hash,
            ):
                with self.assertRaises((TypeError, ValueError)):
                    snapshot_type(
                        schema=schema,
                        version=version,
                        memory_snapshot_hash=memory_snapshot_hash,
                    )

    def _terminal_verification_inputs(self) -> dict[str, object]:
        return {
            "qualification_id": "qualification-001",
            "epoch": 1,
            "scenario_id": "scenario-001",
            "scenario_contract_hash": _digest("scenario-contract"),
            "cutoff_sequence": 3,
            "admitted_set_hash": _digest("admitted-set"),
            "admitted_turn_receipt_aggregate_hash": _digest("turn-receipts"),
            "target_ingress_receipt_aggregate_hash": _digest("target-ingress"),
            "provider_effect_outcome_aggregate_hash": _digest("provider-outcomes"),
            "followup_delivery_receipt_aggregate_hash": _digest("followup-delivery"),
            "public_delivery_receipt_aggregate_hash": _digest("public-delivery"),
            "compensation_receipt_aggregate_hash": _digest("empty-compensation-tuple"),
            "final_state_hash": _digest("final-state"),
            "final_economic_hash": _digest("final-economic"),
            "allocation_manifest_hash": _digest("allocation-manifest"),
            "exact_effect_budget_hash": _digest("effect-budget"),
            "previous_qualification_artifact_hash": _digest(
                "previous-qualification-artifact"
            ),
        }

    def test_scenario_terminal_verification_fields_and_hash_are_closed(self) -> None:
        module = _qualification_module()
        self.assertIsNotNone(module, "qualification contracts must have an owner")
        assert module is not None
        receipt_type = getattr(
            module,
            "ScenarioTerminalVerificationReceipt",
            None,
        )
        self.assertIsNotNone(
            receipt_type,
            "ScenarioTerminalVerificationReceipt must have an owner",
        )
        assert receipt_type is not None
        expected_fields = (
            "qualification_id",
            "epoch",
            "scenario_id",
            "scenario_contract_hash",
            "cutoff_sequence",
            "admitted_set_hash",
            "admitted_turn_receipt_aggregate_hash",
            "target_ingress_receipt_aggregate_hash",
            "provider_effect_outcome_aggregate_hash",
            "followup_delivery_receipt_aggregate_hash",
            "public_delivery_receipt_aggregate_hash",
            "compensation_receipt_aggregate_hash",
            "final_state_hash",
            "final_economic_hash",
            "allocation_manifest_hash",
            "exact_effect_budget_hash",
            "previous_qualification_artifact_hash",
        )
        self.assertEqual(
            tuple(field.name for field in fields(receipt_type)),
            expected_fields,
        )
        self.assertEqual(
            module.SCENARIO_TERMINAL_VERIFICATION_DOMAIN,
            "phase8-scenario-terminal-verification-v1",
        )
        self.assertEqual(
            receipt_type.SCHEMA,
            "phase8-scenario-terminal-verification-receipt",
        )
        self.assertEqual(receipt_type.VERSION, 1)
        self.assertEqual(
            receipt_type.DOMAIN,
            module.SCENARIO_TERMINAL_VERIFICATION_DOMAIN,
        )
        values = self._terminal_verification_inputs()
        receipt = receipt_type(**values)
        expected_bytes = json.dumps(
            {
                "schema": receipt_type.SCHEMA,
                "version": receipt_type.VERSION,
                "data": values,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(receipt.to_canonical_bytes(), expected_bytes)
        self.assertEqual(
            receipt.canonical_hash(),
            hashlib.sha256(
                b"phase8-scenario-terminal-verification-v1\x00" + expected_bytes
            ).hexdigest(),
        )

    def test_scenario_terminal_verification_rejects_open_or_nullable_values(self) -> None:
        module = _qualification_module()
        self.assertIsNotNone(module, "qualification contracts must have an owner")
        assert module is not None
        receipt_type = getattr(
            module,
            "ScenarioTerminalVerificationReceipt",
            None,
        )
        self.assertIsNotNone(
            receipt_type,
            "ScenarioTerminalVerificationReceipt must have an owner",
        )
        assert receipt_type is not None
        valid = self._terminal_verification_inputs()
        for field_name, invalid_value in (
            ("qualification_id", "qualification 001"),
            ("epoch", True),
            ("epoch", 0),
            ("scenario_id", ""),
            ("cutoff_sequence", True),
            ("cutoff_sequence", 0),
            ("scenario_contract_hash", "A" * 64),
            ("compensation_receipt_aggregate_hash", None),
            ("previous_qualification_artifact_hash", "short"),
        ):
            with self.subTest(field_name=field_name, invalid_value=invalid_value):
                with self.assertRaises((TypeError, ValueError)):
                    receipt_type(**(valid | {field_name: invalid_value}))


if __name__ == "__main__":
    unittest.main()
