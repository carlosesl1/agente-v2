"""Focused closed qualification contracts for Phase 8."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import hashlib
import importlib
import json
import unittest


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


if __name__ == "__main__":
    unittest.main()
