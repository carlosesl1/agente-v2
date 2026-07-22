"""Focused closed effect and relay contracts for Phase 8."""

from __future__ import annotations

import base64
from dataclasses import fields
import hashlib
import importlib
import json
import unittest


def _effects_module() -> object | None:
    try:
        return importlib.import_module("reservation_boundary.effects")
    except ModuleNotFoundError as exc:
        if exc.name != "reservation_boundary.effects":
            raise
        return None


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _preimage_bytes(
    *,
    genesis_state: bytes,
    phase5_events: tuple[bytes, ...],
    summary_outboxes: tuple[bytes, ...],
    expected_final_state: bytes,
    expected_final_state_hash: str,
    command_ledger_seed: bytes,
    qualification_id: str | None,
    scenario_id: str | None,
    immutable_generation: int | None,
    allocation_id: str | None,
) -> bytes:
    return json.dumps(
        {
            "schema": "phase8-reservation-relay-bundle-preimage",
            "version": 1,
            "data": {
                "genesis_state": _b64(genesis_state),
                "phase5_events": [_b64(value) for value in phase5_events],
                "summary_outboxes": [
                    _b64(value) for value in summary_outboxes
                ],
                "expected_final_state": _b64(expected_final_state),
                "expected_final_state_hash": expected_final_state_hash,
                "command_ledger_seed": _b64(command_ledger_seed),
                "qualification_id": qualification_id,
                "scenario_id": scenario_id,
                "immutable_generation": immutable_generation,
                "allocation_id": allocation_id,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _settlement_preimage_bytes(
    *,
    workflow_anchor: bytes,
    policy: bytes,
    payment_history: tuple[bytes, ...],
    evidence: tuple[bytes, ...],
    payment_command: bytes,
    expected_final_state: bytes,
    expected_final_state_hash: str,
    qualification_id: str | None,
    scenario_id: str | None,
    immutable_generation: int | None,
    allocation_id: str | None,
) -> bytes:
    return json.dumps(
        {
            "schema": "phase8-settlement-relay-bundle-preimage",
            "version": 1,
            "data": {
                "workflow_anchor": _b64(workflow_anchor),
                "policy": _b64(policy),
                "payment_history": [_b64(value) for value in payment_history],
                "evidence": [_b64(value) for value in evidence],
                "payment_command": _b64(payment_command),
                "expected_final_state": _b64(expected_final_state),
                "expected_final_state_hash": expected_final_state_hash,
                "qualification_id": qualification_id,
                "scenario_id": scenario_id,
                "immutable_generation": immutable_generation,
                "allocation_id": allocation_id,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class Phase8EffectTypeTests(unittest.TestCase):
    def _bundle_inputs(self, *, e2e: bool) -> dict[str, object]:
        inputs: dict[str, object] = {
            "genesis_state": b'{"revision":0}',
            "phase5_events": (b'{"event":1}', b'{"event":2}'),
            "summary_outboxes": (b'{"summary":1}',),
            "expected_final_state": b'{"revision":2}',
            "expected_final_state_hash": hashlib.sha256(
                b'{"revision":2}'
            ).hexdigest(),
            "command_ledger_seed": b'{"command":"reserve"}',
            "qualification_id": "qualification-001" if e2e else None,
            "scenario_id": "scenario-001" if e2e else None,
            "immutable_generation": 1 if e2e else None,
            "allocation_id": "allocation-001" if e2e else None,
        }
        inputs["artifact_hash"] = hashlib.sha256(
            b"phase8-reservation-relay-bundle-v1\x00"
            + _preimage_bytes(**inputs)
        ).hexdigest()
        return inputs

    def test_reservation_relay_bundle_fields_preimage_and_hash_are_closed(self) -> None:
        module = _effects_module()
        self.assertIsNotNone(module, "effect contracts must have an owner")
        assert module is not None
        bundle_type = getattr(module, "ReservationRelayBundle", None)
        self.assertIsNotNone(bundle_type, "ReservationRelayBundle must have an owner")
        assert bundle_type is not None
        self.assertEqual(
            tuple(field.name for field in fields(bundle_type)),
            (
                "genesis_state",
                "phase5_events",
                "summary_outboxes",
                "expected_final_state",
                "expected_final_state_hash",
                "command_ledger_seed",
                "qualification_id",
                "scenario_id",
                "immutable_generation",
                "allocation_id",
                "artifact_hash",
            ),
        )
        self.assertEqual(
            module.RESERVATION_RELAY_DOMAIN,
            "phase8-reservation-relay-bundle-v1",
        )
        self.assertEqual(bundle_type.SCHEMA, "phase8-reservation-relay-bundle")
        self.assertEqual(bundle_type.VERSION, 1)
        self.assertEqual(bundle_type.DOMAIN, module.RESERVATION_RELAY_DOMAIN)

        inputs = self._bundle_inputs(e2e=True)
        bundle = bundle_type(**inputs)
        expected_preimage = _preimage_bytes(
            **{key: value for key, value in inputs.items() if key != "artifact_hash"}
        )
        self.assertEqual(bundle.artifact_preimage_bytes(), expected_preimage)
        self.assertEqual(
            bundle.artifact_hash,
            hashlib.sha256(
                b"phase8-reservation-relay-bundle-v1\x00" + expected_preimage
            ).hexdigest(),
        )
        wire = json.loads(bundle.to_canonical_bytes())
        self.assertEqual(wire["schema"], bundle_type.SCHEMA)
        self.assertEqual(wire["version"], bundle_type.VERSION)
        self.assertEqual(wire["data"]["artifact_hash"], bundle.artifact_hash)
        self.assertNotIn("source_turn_receipt_hash", wire["data"])

    def test_reservation_relay_bundle_rejects_partial_e2e_or_divergent_hash(self) -> None:
        module = _effects_module()
        self.assertIsNotNone(module, "effect contracts must have an owner")
        assert module is not None
        bundle_type = getattr(module, "ReservationRelayBundle", None)
        self.assertIsNotNone(bundle_type, "ReservationRelayBundle must have an owner")
        assert bundle_type is not None

        bundle_type(**self._bundle_inputs(e2e=False))
        valid = self._bundle_inputs(e2e=True)
        for field_name in (
            "qualification_id",
            "scenario_id",
            "immutable_generation",
            "allocation_id",
        ):
            partial = dict(valid)
            partial[field_name] = None
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValueError):
                    bundle_type(**partial)

        invalid = dict(valid)
        invalid["artifact_hash"] = "f" * 64
        with self.assertRaises(ValueError):
            bundle_type(**invalid)
        with self.assertRaises(TypeError):
            bundle_type(**(valid | {"phase5_events": [b"not-a-tuple"]}))
        with self.assertRaises(TypeError):
            bundle_type(**(valid | {"genesis_state": bytearray(b"mutable")}))
        with self.assertRaises(TypeError):
            bundle_type(**(valid | {"immutable_generation": True}))
        with self.assertRaises(TypeError):
            bundle_type(**(valid | {"source_turn_receipt_hash": "a" * 64}))

    def _settlement_inputs(self, *, e2e: bool) -> dict[str, object]:
        inputs: dict[str, object] = {
            "workflow_anchor": b'{"workflow":"payment-001"}',
            "policy": b'{"currency":"BRL"}',
            "payment_history": (b'{"event":1}', b'{"event":2}'),
            "evidence": (b'{"evidence":"proof-001"}',),
            "payment_command": b'{"command":"settle"}',
            "expected_final_state": b'{"status":"queued"}',
            "expected_final_state_hash": hashlib.sha256(
                b'{"status":"queued"}'
            ).hexdigest(),
            "qualification_id": "qualification-001" if e2e else None,
            "scenario_id": "scenario-001" if e2e else None,
            "immutable_generation": 1 if e2e else None,
            "allocation_id": "allocation-001" if e2e else None,
        }
        inputs["artifact_hash"] = hashlib.sha256(
            b"phase8-settlement-relay-bundle-v1\x00"
            + _settlement_preimage_bytes(**inputs)
        ).hexdigest()
        return inputs

    def test_settlement_relay_bundle_fields_preimage_and_hash_are_closed(self) -> None:
        module = _effects_module()
        self.assertIsNotNone(module, "effect contracts must have an owner")
        assert module is not None
        bundle_type = getattr(module, "SettlementRelayBundle", None)
        self.assertIsNotNone(bundle_type, "SettlementRelayBundle must have an owner")
        assert bundle_type is not None
        self.assertEqual(
            tuple(field.name for field in fields(bundle_type)),
            (
                "workflow_anchor",
                "policy",
                "payment_history",
                "evidence",
                "payment_command",
                "expected_final_state",
                "expected_final_state_hash",
                "qualification_id",
                "scenario_id",
                "immutable_generation",
                "allocation_id",
                "artifact_hash",
            ),
        )
        self.assertEqual(
            module.SETTLEMENT_RELAY_DOMAIN,
            "phase8-settlement-relay-bundle-v1",
        )
        self.assertEqual(bundle_type.SCHEMA, "phase8-settlement-relay-bundle")
        self.assertEqual(bundle_type.VERSION, 1)
        self.assertEqual(bundle_type.DOMAIN, module.SETTLEMENT_RELAY_DOMAIN)

        inputs = self._settlement_inputs(e2e=True)
        bundle = bundle_type(**inputs)
        expected_preimage = _settlement_preimage_bytes(
            **{key: value for key, value in inputs.items() if key != "artifact_hash"}
        )
        self.assertEqual(bundle.artifact_preimage_bytes(), expected_preimage)
        self.assertEqual(
            bundle.artifact_hash,
            hashlib.sha256(
                b"phase8-settlement-relay-bundle-v1\x00" + expected_preimage
            ).hexdigest(),
        )
        wire = json.loads(bundle.to_canonical_bytes())
        self.assertEqual(wire["data"]["artifact_hash"], bundle.artifact_hash)
        self.assertNotIn("source_turn_receipt_hash", wire["data"])

    def test_settlement_relay_bundle_rejects_partial_e2e_or_divergent_hash(self) -> None:
        module = _effects_module()
        self.assertIsNotNone(module, "effect contracts must have an owner")
        assert module is not None
        bundle_type = getattr(module, "SettlementRelayBundle", None)
        self.assertIsNotNone(bundle_type, "SettlementRelayBundle must have an owner")
        assert bundle_type is not None

        bundle_type(**self._settlement_inputs(e2e=False))
        valid = self._settlement_inputs(e2e=True)
        for field_name in (
            "qualification_id",
            "scenario_id",
            "immutable_generation",
            "allocation_id",
        ):
            partial = dict(valid)
            partial[field_name] = None
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValueError):
                    bundle_type(**partial)
        with self.assertRaises(ValueError):
            bundle_type(**(valid | {"artifact_hash": "f" * 64}))
        with self.assertRaises(TypeError):
            bundle_type(**(valid | {"payment_history": [b"not-a-tuple"]}))
        with self.assertRaises(TypeError):
            bundle_type(**(valid | {"policy": bytearray(b"mutable")}))
        with self.assertRaises(TypeError):
            bundle_type(**(valid | {"immutable_generation": True}))


if __name__ == "__main__":
    unittest.main()
