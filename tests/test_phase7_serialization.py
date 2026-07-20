"""Strict canonical wire codec for Phase 7 boundary envelopes."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest

from reservation_domain import new_workflow
from reservation_boundary.serialization import (
    PUBLIC_TYPES,
    from_wire_json,
    semantic_hash,
    to_wire_json,
)
from reservation_boundary.types import (
    BoundaryCommit,
    BoundaryState,
    ConversationIntent,
    ConversationIntentKind,
    FaqReadArguments,
    ImportDisposition,
    ImportReason,
    ImportResult,
    IntentRequest,
    KernelDecision,
    LegacyLeadSnapshot,
    NormalizedMessage,
    ToolDispatchRequest,
    TurnEnvelope,
    TurnLease,
    TurnPlan,
    TurnPlanReason,
    VersionedBoundaryState,
)
from tests.phase7_helpers import DEADLINE, NOW, raw_legacy_fields


def boundary_state() -> BoundaryState:
    return BoundaryState(
        schema_version=7,
        lead_key="lead-synthetic-001",
        version=0,
        workflow=new_workflow(workflow_id="workflow-001", started_at=NOW),
        handoff=None,
        payments=(),
        processed_event_ids=("event-001",),
    )


def public_contract_examples() -> tuple[object, ...]:
    state = boundary_state()
    message = NormalizedMessage("hello", "en")
    dispatch = ToolDispatchRequest(
        "cerebro_consultar",
        FaqReadArguments("What is included?", "en"),
        state.lead_key,
        "event-001",
        DEADLINE,
    )
    intent = ConversationIntent(
        ConversationIntentKind.INFORM,
        "event-001",
    )
    return (
        LegacyLeadSnapshot(
            schema_version=1,
            source="chapada-leads-hermes",
            raw_fields=raw_legacy_fields(),
            canonical_json='{"lead_key":"lead-synthetic-001"}',
            snapshot_hash="a" * 64,
        ),
        ImportResult(ImportDisposition.MIGRATED, state, ImportReason.NONE),
        state,
        intent,
        IntentRequest(state, message, "event-001", DEADLINE),
        dispatch,
        KernelDecision(state, (), (), (dispatch,), ()),
        TurnLease(state.lead_key, 1, DEADLINE),
        VersionedBoundaryState(state, 0, "b" * 64),
        BoundaryCommit(state, (), (), ()),
        TurnEnvelope(state.lead_key, "event-001", message, NOW, DEADLINE),
        TurnPlan(state, (message,), (), (), False, TurnPlanReason.COMPLETED),
    )


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class Phase7SerializationTests(unittest.TestCase):
    def test_public_registry_is_exact(self) -> None:
        self.assertEqual(
            tuple(PUBLIC_TYPES),
            (
                "LegacyLeadSnapshot",
                "ImportResult",
                "BoundaryState",
                "ConversationIntent",
                "IntentRequest",
                "ToolDispatchRequest",
                "KernelDecision",
                "TurnLease",
                "VersionedBoundaryState",
                "BoundaryCommit",
                "TurnEnvelope",
                "TurnPlan",
            ),
        )

    def test_round_trip_is_byte_stable_for_every_public_contract(self) -> None:
        examples = public_contract_examples()
        self.assertEqual(tuple(type(value).__name__ for value in examples), tuple(PUBLIC_TYPES))
        for value in examples:
            with self.subTest(type=type(value).__name__):
                encoded = to_wire_json(value)
                decoded = from_wire_json(encoded, type(value))
                self.assertEqual(type(decoded), type(value))
                self.assertEqual(decoded, value)
                self.assertEqual(to_wire_json(decoded), encoded)
                self.assertEqual(semantic_hash(value), hashlib.sha256(encoded.encode()).hexdigest())

    def test_duplicate_unknown_missing_and_bool_as_int_fail_closed(self) -> None:
        lease = TurnLease("lead-1", 1, DEADLINE)
        canonical = json.loads(to_wire_json(lease))
        data = canonical["data"]
        self.assertIsInstance(data, dict)

        unknown = json.loads(to_wire_json(lease))
        unknown["data"]["unexpected"] = "value"
        missing = json.loads(to_wire_json(lease))
        del missing["data"]["lead_key"]
        bool_as_int = json.loads(to_wire_json(lease))
        bool_as_int["data"]["token"] = True
        bad_schema = json.loads(to_wire_json(lease))
        bad_schema["schema_version"] = True
        duplicate = (
            '{"data":{},"schema_version":1,"schema_version":1,'
            '"type":"turn_lease"}'
        )
        for payload in (
            duplicate,
            _canonical(unknown),
            _canonical(missing),
            _canonical(bool_as_int),
            _canonical(bad_schema),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    from_wire_json(payload, TurnLease)

    def test_noncanonical_json_wrong_expected_type_and_subclass_fail_closed(self) -> None:
        lease = TurnLease("lead-1", 1, DEADLINE)
        encoded = to_wire_json(lease)
        with self.assertRaises(ValueError):
            from_wire_json(json.dumps(json.loads(encoded)), TurnLease)
        with self.assertRaises(ValueError):
            from_wire_json(encoded, TurnEnvelope)

        class DerivedLease(TurnLease):
            pass

        with self.assertRaises(TypeError):
            to_wire_json(DerivedLease("lead-1", 1, DEADLINE))

    def test_nested_type_tag_and_union_mismatch_fail_closed(self) -> None:
        request = public_contract_examples()[5]
        self.assertIsInstance(request, ToolDispatchRequest)
        payload = json.loads(to_wire_json(request))
        payload["data"]["arguments"]["$type"] = "StateCommitArguments"
        with self.assertRaises(ValueError):
            from_wire_json(_canonical(payload), ToolDispatchRequest)

    def test_tampered_state_is_not_normalized(self) -> None:
        state = boundary_state()
        payload = json.loads(to_wire_json(state))
        payload["data"]["version"] = 1
        tampered = _canonical(payload)
        decoded = from_wire_json(tampered, BoundaryState)
        self.assertEqual(decoded.version, 1)
        self.assertNotEqual(semantic_hash(decoded), semantic_hash(state))
        self.assertEqual(from_wire_json(to_wire_json(replace(decoded)), BoundaryState), decoded)


if __name__ == "__main__":
    unittest.main()
