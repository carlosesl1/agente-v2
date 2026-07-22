"""Phase 8 atomic handoff target-ingress ownership."""

from __future__ import annotations

from datetime import timedelta
import base64
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from reservation_boundary import effects
from reservation_followup.handoff import (
    HandoffCancellationCode,
    HandoffCancelled,
    new_handoff,
    reduce_handoff,
)
from reservation_followup.serialization import semantic_hash, to_wire_json
from reservation_followup.sqlite_store import DataCorruption, SQLiteFollowupUnitOfWork
from tests.phase6_helpers import T0, handoff_requested, optional_email_policy


def _canonical(schema: str, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"schema": schema, "version": 1, "data": data},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _bundle(*, final_hash: str | None = None) -> tuple[object, object]:
    request = handoff_requested()
    policy = optional_email_policy()
    cancelled = HandoffCancelled(
        handoff_id=request.handoff_id,
        incident_key=request.incident_key,
        cancellation_code=HandoffCancellationCode.REQUEST_WITHDRAWN,
        cancelled_at=T0 + timedelta(seconds=1),
    )
    state = reduce_handoff(new_handoff(request, policy).state, cancelled).state
    expected_hash = final_hash or semantic_hash(state)
    request_bytes = to_wire_json(request).encode()
    policy_bytes = to_wire_json(policy).encode()
    history_bytes = (to_wire_json(cancelled).encode(),)
    preimage = _canonical(
        "phase8-handoff-relay-bundle-preimage",
        {
            "request_bytes": base64.b64encode(request_bytes).decode("ascii"),
            "policy_bytes": base64.b64encode(policy_bytes).decode("ascii"),
            "history_bytes": [
                base64.b64encode(history_bytes[0]).decode("ascii")
            ],
            "expected_final_state_hash": expected_hash,
        },
    )
    bundle = effects.HandoffRelayBundle(
        request_bytes=request_bytes,
        policy_bytes=policy_bytes,
        history_bytes=history_bytes,
        expected_final_state_hash=expected_hash,
        artifact_hash=hashlib.sha256(
            b"phase8-handoff-relay-bundle-v1\x00" + preimage
        ).hexdigest(),
    )
    return bundle, state


class Phase8HandoffTargetIngressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "followup-v2.sqlite3"
        self.source_hash = "5" * 64

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _derive(self, bundle: object) -> str:
        derive = getattr(effects, "target_operation_id", None)
        self.assertIsNotNone(derive, "target operation ID must have one owner")
        assert derive is not None
        return derive(
            effects.InternalJobKind.HANDOFF,
            bundle.artifact_hash,
            self.source_hash,
        )

    def _apply(self, store, operation_id: str, bundle: object):
        apply = getattr(store, "accept_boundary_handoff", None)
        self.assertIsNotNone(apply, "Phase 6 v2 store must own handoff ingress")
        assert apply is not None
        return apply(
            operation_id=operation_id,
            bundle=bundle,
            source_turn_receipt_hash=self.source_hash,
        )

    def test_public_accept_signature_is_exact(self) -> None:
        method = getattr(SQLiteFollowupUnitOfWork, "accept_boundary_handoff", None)
        self.assertIsNotNone(method, "handoff ingress must expose its exact API")
        assert method is not None
        signature = inspect.signature(method)
        self.assertEqual(
            tuple(signature.parameters),
            ("self", "operation_id", "source_turn_receipt_hash", "bundle"),
        )
        for name in ("operation_id", "source_turn_receipt_hash", "bundle"):
            self.assertIs(
                signature.parameters[name].kind,
                inspect.Parameter.KEYWORD_ONLY,
            )

    def test_operation_id_is_domain_separated_and_binds_the_complete_tuple(self) -> None:
        bundle, _ = _bundle()
        operation_id = self._derive(bundle)
        self.assertRegex(operation_id, r"^[0-9a-f]{64}$")
        self.assertEqual(operation_id, self._derive(bundle))
        derive = effects.target_operation_id
        self.assertNotEqual(
            operation_id,
            derive(
                effects.InternalJobKind.LEARNING,
                bundle.artifact_hash,
                self.source_hash,
            ),
        )
        self.assertNotEqual(
            operation_id,
            derive(
                effects.InternalJobKind.HANDOFF,
                "a" * 64,
                self.source_hash,
            ),
        )
        with self.assertRaises(TypeError):
            derive("handoff", bundle.artifact_hash, self.source_hash)

    def test_atomic_apply_replays_full_history_and_exact_duplicate_returns_receipt(self) -> None:
        bundle, expected_state = _bundle()
        operation_id = self._derive(bundle)
        with SQLiteFollowupUnitOfWork.open_v2(self.path) as store:
            first = self._apply(store, operation_id, bundle)
            self.assertEqual(
                store.load_handoff(expected_state.request.handoff_id),
                expected_state,
            )
            self.assertEqual(first.operation_id, operation_id)
            self.assertEqual(first.artifact_hash, bundle.artifact_hash)
            self.assertEqual(first.source_turn_receipt_hash, self.source_hash)
            self.assertEqual(first.target_result_hash, semantic_hash(expected_state))
            self.assertEqual(
                store._connection.execute(
                    "SELECT count(*) FROM handoff_boundary_ingress_receipts"
                ).fetchone(),
                (1,),
            )
        with SQLiteFollowupUnitOfWork.open_v2(self.path) as reopened:
            replay = self._apply(reopened, operation_id, bundle)
            self.assertEqual(replay.to_canonical_bytes(), first.to_canonical_bytes())

    def test_wrong_operation_or_final_hash_fails_before_any_domain_write(self) -> None:
        bundle, _ = _bundle()
        with SQLiteFollowupUnitOfWork.open_v2(self.path) as store:
            with self.assertRaises(ValueError):
                self._apply(store, "f" * 64, bundle)
            divergent_bundle, _ = _bundle(final_hash="2" * 64)
            with self.assertRaises(DataCorruption):
                self._apply(store, self._derive(divergent_bundle), divergent_bundle)
            for table in (
                "handoff_workflows",
                "handoff_events",
                "handoff_boundary_ingress_receipts",
            ):
                self.assertEqual(
                    store._connection.execute(
                        f"SELECT count(*) FROM {table}"
                    ).fetchone(),
                    (0,),
                )

    def test_fault_before_receipt_rolls_back_domain_and_persisted_divergence_fails(self) -> None:
        bundle, _ = _bundle()
        operation_id = self._derive(bundle)

        def fault(stage: str) -> None:
            if stage == "after_domain_before_receipt":
                raise RuntimeError("synthetic target ingress fault")

        with SQLiteFollowupUnitOfWork.open_v2(self.path) as store:
            store._phase8_handoff_fault_hook = fault
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                self._apply(store, operation_id, bundle)
            self.assertEqual(
                store._connection.execute(
                    "SELECT count(*) FROM handoff_workflows"
                ).fetchone(),
                (0,),
            )
            self.assertEqual(
                store._connection.execute(
                    "SELECT count(*) FROM handoff_boundary_ingress_receipts"
                ).fetchone(),
                (0,),
            )
            store._phase8_handoff_fault_hook = None
            self._apply(store, operation_id, bundle)
            store._connection.execute(
                "UPDATE handoff_boundary_ingress_receipts "
                "SET target_result_hash=? WHERE operation_id=?",
                ("9" * 64, operation_id),
            )
            with self.assertRaises(DataCorruption):
                self._apply(store, operation_id, bundle)


if __name__ == "__main__":
    unittest.main()
