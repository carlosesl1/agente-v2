"""Focused package/wire closeout checks for the Phase 8 Task 1 surface."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tomllib
import unittest

import reservation_boundary as boundary
from reservation_boundary import conversation, effects, qualification, reads
from reservation_boundary import types as boundary_types


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _duplicate_data_key(payload: bytes) -> bytes:
    envelope = json.loads(payload)
    data = envelope["data"]
    first_key = sorted(data)[0]

    def pair(key: str) -> str:
        return (
            json.dumps(key, ensure_ascii=False)
            + ":"
            + _canonical_json(data[key]).decode("utf-8")
        )

    data_text = "{" + pair(first_key) + "," + ",".join(
        pair(key) for key in sorted(data)
    ) + "}"
    return (
        "{\"data\":"
        + data_text
        + ",\"schema\":"
        + json.dumps(envelope["schema"], ensure_ascii=False)
        + ",\"version\":"
        + json.dumps(envelope["version"])
        + "}"
    ).encode("utf-8")


def _common_contract_instances() -> tuple[tuple[str, object], ...]:
    examples = json.loads(
        (FIXTURES / "phase8_facts_reads_wire_v1.json").read_text(encoding="utf-8")
    )["examples"]
    execution_data = json.loads(
        examples["reservation_execution_projection.present"]["canonical_utf8"]
    )["data"]
    execution = conversation.ReservationExecutionProjection(
        reservation_relay_bundle_bytes=base64.b64decode(
            execution_data["reservation_relay_bundle_bytes"],
            validate=True,
        ),
        reservation_relay_bundle_hash=execution_data["reservation_relay_bundle_hash"],
    )
    fact = boundary_types.TypedFact(
        "language",
        boundary_types.StringSlot("pt-BR"),
        "1" * 64,
    )
    projection = conversation.ConversationProjection(
        stage=conversation.ConversationStage.HOSTEL,
        desired_services=(conversation.DesiredService.HOSTEL,),
        locale="pt-BR",
        facts=(fact,),
        reservation_execution_projection=execution,
    )
    source_event = conversation.SourceEventIdentity("manychat:event-001", "2" * 64)
    state_bytes = b'{"state":"canonical"}'
    request = conversation.MayaTurnRequest(
        boundary_state_bytes=state_bytes,
        state_version=7,
        state_hash=hashlib.sha256(state_bytes).hexdigest(),
        normalized_message=boundary_types.NormalizedMessage("hello", "en"),
        aggregate_turn_id="turn-001",
        source_events=(source_event,),
        lead_key_hash="3" * 64,
        private_delivery_binding_hash="4" * 64,
        deadline_at=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
        behavior_profile_fingerprint="5" * 64,
    )
    intent = conversation.MayaIntentClosure(
        boundary_types.ConversationIntentKind.SELECT,
        "offer-001",
        None,
        False,
    )
    closure = conversation.MayaTurnClosure(
        aggregate_turn_id="turn-001",
        intent_closure=intent,
        public_text="Escolha registrada.",
        route=conversation.PublicRoute.HOSTEL,
        reply_type=conversation.PublicReplyType.ANSWER,
        final_seq=3,
        expected_prefix_mac="6" * 64,
        ephemeral_session_id="session-001",
        zero_requests_in_flight=True,
    )
    transcript = conversation.TranscriptCommitment(
        direction=conversation.TranscriptDirection.CHILD_TO_PARENT,
        kind=conversation.TranscriptKind.READ,
        sequence=1,
        request_id="request-001",
        request_hash="7" * 64,
        response_hash="8" * 64,
        previous_frame_commitment="9" * 64,
    )
    capability_rows = (
        (conversation.Capability.LEGACY_READ, conversation.CapabilityDisposition.READ_ONLY),
        (conversation.Capability.MAYA_INFERENCE, conversation.CapabilityDisposition.EXECUTE),
        (conversation.Capability.PROVIDER_READ, conversation.CapabilityDisposition.READ_ONLY),
        (conversation.Capability.TURN_COMMIT, conversation.CapabilityDisposition.EXECUTE),
        (conversation.Capability.RELAY_ENQUEUE, conversation.CapabilityDisposition.EXECUTE),
        (conversation.Capability.PROVIDER_WRITE, conversation.CapabilityDisposition.DENIED),
        (conversation.Capability.FOLLOWUP_DELIVERY, conversation.CapabilityDisposition.DENIED),
        (conversation.Capability.PUBLIC_DELIVERY, conversation.CapabilityDisposition.DENIED),
        (conversation.Capability.LEARNING_WRITE, conversation.CapabilityDisposition.DENIED),
    )
    worker_rows = (
        (conversation.Worker.TURN_COORDINATOR, conversation.WorkerMode.ACTIVE),
        (conversation.Worker.COMMAND_RELAY_WORKER, conversation.WorkerMode.ACTIVE),
        (conversation.Worker.INTERNAL_JOB_WORKER, conversation.WorkerMode.ACTIVE),
        (conversation.Worker.PROVIDER_EFFECT_WORKER, conversation.WorkerMode.DISABLED),
        (conversation.Worker.FOLLOWUP_DELIVERY_WORKER, conversation.WorkerMode.DISABLED),
        (conversation.Worker.PUBLIC_DELIVERY_WORKER, conversation.WorkerMode.DISABLED),
        (conversation.Worker.LEARNING_WORKER, conversation.WorkerMode.DISABLED),
        (conversation.Worker.RECONCILIATION_WORKER, conversation.WorkerMode.SHADOW),
        (conversation.Worker.QUALIFICATION_CONTROLLER, conversation.WorkerMode.DISABLED),
    )
    policy = conversation.CapabilityPolicy(
        capability_matrix=capability_rows,
        worker_modes=worker_rows,
        guard_semantics=tuple(conversation.GuardSemantic),
    )
    snapshot = qualification.BehaviorStateSnapshot(
        schema="hermes-memory-state-v1",
        version=3,
        memory_snapshot_hash="a" * 64,
    )
    return (
        ("TypedFact", fact),
        ("ReservationExecutionProjection", execution),
        ("ConversationProjection", projection),
        ("SourceEventIdentity", source_event),
        ("MayaTurnRequest", request),
        ("MayaIntentClosure", intent),
        ("MayaTurnClosure", closure),
        ("TranscriptCommitment", transcript),
        ("CapabilityPolicy", policy),
        ("BehaviorStateSnapshot", snapshot),
    )


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

    def test_common_contract_decoders_round_trip_byte_exact(self) -> None:
        for name, instance in _common_contract_instances():
            with self.subTest(name=name):
                payload = instance.to_canonical_bytes()
                decoded = type(instance).from_canonical_bytes(payload)
                self.assertEqual(decoded, instance)
                self.assertEqual(decoded.to_canonical_bytes(), payload)

    def test_common_contract_decoders_reject_hostile_envelopes_and_fields(self) -> None:
        for name, instance in _common_contract_instances():
            decoder = type(instance).from_canonical_bytes
            payload = instance.to_canonical_bytes()
            envelope = json.loads(payload)
            with self.subTest(name=name, hostile="non-bytes"):
                with self.assertRaises((TypeError, ValueError)):
                    decoder(bytearray(payload))
            with self.subTest(name=name, hostile="invalid-utf8"):
                with self.assertRaises(ValueError):
                    decoder(payload + b"\xff")
            with self.subTest(name=name, hostile="duplicate-data-key"):
                with self.assertRaises(ValueError):
                    decoder(_duplicate_data_key(payload))

            hostile_envelopes = []
            unknown_top = json.loads(payload)
            unknown_top["unknown"] = None
            hostile_envelopes.append(("unknown-top", unknown_top))
            wrong_schema = json.loads(payload)
            wrong_schema["schema"] = "phase8-wrong-contract"
            hostile_envelopes.append(("wrong-schema", wrong_schema))
            bool_version = json.loads(payload)
            bool_version["version"] = True
            hostile_envelopes.append(("bool-version", bool_version))
            data_list = json.loads(payload)
            data_list["data"] = []
            hostile_envelopes.append(("data-list", data_list))
            for hostile_name, hostile in hostile_envelopes:
                with self.subTest(name=name, hostile=hostile_name):
                    with self.assertRaises(ValueError):
                        decoder(_canonical_json(hostile))

            first_field = sorted(envelope["data"])[0]
            missing_field = json.loads(payload)
            del missing_field["data"][first_field]
            unknown_field = json.loads(payload)
            unknown_field["data"]["unknown"] = None
            for hostile_name, hostile in (
                ("missing-field", missing_field),
                ("unknown-field", unknown_field),
            ):
                with self.subTest(name=name, hostile=hostile_name):
                    with self.assertRaises(ValueError):
                        decoder(_canonical_json(hostile))

    def test_common_contract_decoders_reject_nested_type_confusion(self) -> None:
        nested_contract = json.loads(
            conversation.SourceEventIdentity("nested-event", "b" * 64).to_canonical_bytes()
        )
        confused_fields = {
            "TypedFact": ("value", nested_contract),
            "ReservationExecutionProjection": (
                "reservation_relay_bundle_bytes",
                nested_contract,
            ),
            "ConversationProjection": ("facts", [nested_contract]),
            "SourceEventIdentity": ("source_event_hash", nested_contract),
            "MayaTurnRequest": ("normalized_message", nested_contract),
            "MayaIntentClosure": ("kind", nested_contract),
            "MayaTurnClosure": ("intent_closure", nested_contract),
            "TranscriptCommitment": ("direction", nested_contract),
            "CapabilityPolicy": ("capability_matrix", [[nested_contract, "denied"]]),
            "BehaviorStateSnapshot": ("memory_snapshot_hash", nested_contract),
        }
        for name, instance in _common_contract_instances():
            envelope = json.loads(instance.to_canonical_bytes())
            field, confused_value = confused_fields[name]
            envelope["data"][field] = confused_value
            with self.subTest(name=name):
                with self.assertRaises((TypeError, ValueError)):
                    type(instance).from_canonical_bytes(_canonical_json(envelope))

    def test_common_contract_hashes_are_domain_separated_and_cross_type_closed(self) -> None:
        instances = _common_contract_instances()
        domains: set[str] = set()
        hashes: set[str] = set()
        payloads: set[bytes] = set()
        for index, (name, instance) in enumerate(instances):
            payload = instance.to_canonical_bytes()
            expected_hash = hashlib.sha256(
                type(instance).DOMAIN.encode("ascii") + b"\x00" + payload
            ).hexdigest()
            with self.subTest(name=name, property="domain-hash"):
                self.assertEqual(instance.canonical_hash(), expected_hash)
            domains.add(type(instance).DOMAIN)
            hashes.add(instance.canonical_hash())
            payloads.add(payload)

            other_payload = instances[(index + 1) % len(instances)][1].to_canonical_bytes()
            with self.subTest(name=name, property="cross-type-decode"):
                with self.assertRaises(ValueError):
                    type(instance).from_canonical_bytes(other_payload)

        self.assertEqual(len(domains), len(instances))
        self.assertEqual(len(hashes), len(instances))
        self.assertEqual(len(payloads), len(instances))


if __name__ == "__main__":
    unittest.main()
