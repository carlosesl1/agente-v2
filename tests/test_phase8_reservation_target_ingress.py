"""Phase 8 atomic reservation target-ingress ownership."""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from reservation_boundary import effects
from reservation_domain import dumps_command, dumps_event, dumps_state, reduce
from reservation_execution.sqlite_store import DataCorruption, SQLiteUnitOfWork
from tests.phase5_helpers import workflow_events


class Phase8ReservationTargetIngressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase8-reservation-target-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "phase5-v6.db"
        self.source_hash = "7" * 64

    def _bundle(self):
        encode_outbox = getattr(effects, "phase5_outbox_seed_bytes", None)
        self.assertIsNotNone(
            encode_outbox,
            "ReservationRelayBundle must own the canonical Phase 5 outbox seed codec",
        )
        assert encode_outbox is not None
        initial, script = workflow_events(
            "cloudbeds",
            workflow_id="workflow:phase8-reservation-ingress",
        )
        state = initial
        event_bytes: list[bytes] = []
        outbox_bytes: list[bytes] = []
        command_bytes: list[bytes] = []
        for event, outbox in script:
            transition = reduce(state, event)
            state = transition.state
            event_bytes.append(dumps_event(event).encode("utf-8"))
            outbox_bytes.extend(encode_outbox(message) for message in outbox)
            command_bytes.extend(
                dumps_command(command).encode("utf-8")
                for command in transition.commands
            )
        self.assertEqual(len(command_bytes), 1)
        expected_final_state = dumps_state(state).encode("utf-8")
        expected_hash = hashlib.sha256(expected_final_state).hexdigest()
        preimage = {
            "genesis_state": base64.b64encode(
                dumps_state(initial).encode("utf-8")
            ).decode("ascii"),
            "phase5_events": [
                base64.b64encode(value).decode("ascii") for value in event_bytes
            ],
            "summary_outboxes": [
                base64.b64encode(value).decode("ascii") for value in outbox_bytes
            ],
            "expected_final_state": base64.b64encode(expected_final_state).decode("ascii"),
            "expected_final_state_hash": expected_hash,
            "command_ledger_seed": base64.b64encode(command_bytes[0]).decode("ascii"),
            "qualification_id": None,
            "scenario_id": None,
            "immutable_generation": None,
            "allocation_id": None,
        }
        artifact_preimage = json.dumps(
            {
                "data": preimage,
                "schema": "phase8-reservation-relay-bundle-preimage",
                "version": 1,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        artifact_hash = hashlib.sha256(
            b"phase8-reservation-relay-bundle-v1\0" + artifact_preimage
        ).hexdigest()
        return (
            effects.ReservationRelayBundle(
                genesis_state=dumps_state(initial).encode("utf-8"),
                phase5_events=tuple(event_bytes),
                summary_outboxes=tuple(outbox_bytes),
                expected_final_state=expected_final_state,
                expected_final_state_hash=expected_hash,
                command_ledger_seed=command_bytes[0],
                qualification_id=None,
                scenario_id=None,
                immutable_generation=None,
                allocation_id=None,
                artifact_hash=artifact_hash,
            ),
            state,
            tuple(outbox_bytes),
            tuple(command_bytes),
        )

    def _derive(self, bundle) -> str:
        derive = getattr(effects, "target_operation_id", None)
        self.assertIsNotNone(derive)
        assert derive is not None
        return derive(
            effects.InternalJobKind.HANDOFF,
            bundle.artifact_hash,
            self.source_hash,
        )

    def _accept(self, store, operation_id: str, bundle):
        accept = getattr(store, "accept_boundary_reservation", None)
        self.assertIsNotNone(accept, "Phase 5 v6 store must own reservation ingress")
        assert accept is not None
        return accept(
            operation_id=operation_id,
            source_turn_receipt_hash=self.source_hash,
            bundle=bundle,
        )

    def test_public_signature_and_outbox_seed_codec_are_exact(self) -> None:
        method = getattr(SQLiteUnitOfWork, "accept_boundary_reservation", None)
        self.assertIsNotNone(method)
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
        bundle, _, outbox_bytes, _ = self._bundle()
        self.assertEqual(len(outbox_bytes), 1)
        parsed = json.loads(outbox_bytes[0])
        self.assertEqual(
            set(parsed),
            {
                "canonical_payload",
                "command_id",
                "created_at",
                "idempotency_key",
                "kind",
                "message_id",
                "payload_hash",
                "template_id",
                "workflow_id",
            },
        )
        self.assertEqual(
            json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
            outbox_bytes[0],
        )
        self.assertEqual(
            hashlib.sha256(bundle.expected_final_state).hexdigest(),
            bundle.expected_final_state_hash,
        )

    def test_atomic_full_replay_and_duplicate_receipt_are_exact(self) -> None:
        bundle, expected_state, outbox_bytes, command_bytes = self._bundle()
        operation_id = self._derive(bundle)
        with SQLiteUnitOfWork.open_v6(self.path) as store:
            first = self._accept(store, operation_id, bundle)
            self.assertEqual(store.load_workflow(expected_state.meta.workflow_id), expected_state)
            self.assertEqual(first.job_kind, effects.InternalJobKind.HANDOFF)
            self.assertEqual(first.target_result_hash, bundle.expected_final_state_hash)
            self.assertEqual(
                store._connection.execute(
                    "SELECT count(*) FROM reservation_boundary_ingress_receipts"
                ).fetchone(),
                (1,),
            )
            self.assertEqual(
                store._connection.execute("SELECT count(*) FROM outbox_messages").fetchone(),
                (len(outbox_bytes),),
            )
            self.assertEqual(
                store._connection.execute("SELECT count(*) FROM execution_ledger").fetchone(),
                (len(command_bytes),),
            )
        with SQLiteUnitOfWork.open_v6(self.path) as reopened:
            replay = self._accept(reopened, operation_id, bundle)
            self.assertEqual(replay.to_canonical_bytes(), first.to_canonical_bytes())

    def test_wrong_operation_and_wrong_final_hash_write_nothing(self) -> None:
        bundle, _, _, _ = self._bundle()
        with SQLiteUnitOfWork.open_v6(self.path) as store:
            with self.assertRaises(ValueError):
                self._accept(store, "f" * 64, bundle)
            self.assertEqual(
                store._connection.execute("SELECT count(*) FROM workflows").fetchone(),
                (0,),
            )
            wrong_final_state = b'{"wrong":true}'
            wrong_hash = hashlib.sha256(wrong_final_state).hexdigest()
            wrong_preimage = bundle._preimage_data() | {
                "expected_final_state": base64.b64encode(wrong_final_state).decode("ascii"),
                "expected_final_state_hash": wrong_hash,
            }
            wrong_artifact_preimage = json.dumps(
                {
                    "data": wrong_preimage,
                    "schema": "phase8-reservation-relay-bundle-preimage",
                    "version": 1,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            wrong_artifact = hashlib.sha256(
                b"phase8-reservation-relay-bundle-v1\0" + wrong_artifact_preimage
            ).hexdigest()
            wrong = effects.ReservationRelayBundle(
                genesis_state=bundle.genesis_state,
                phase5_events=bundle.phase5_events,
                summary_outboxes=bundle.summary_outboxes,
                expected_final_state=wrong_final_state,
                expected_final_state_hash=wrong_hash,
                command_ledger_seed=bundle.command_ledger_seed,
                qualification_id=None,
                scenario_id=None,
                immutable_generation=None,
                allocation_id=None,
                artifact_hash=wrong_artifact,
            )
            with self.assertRaises(DataCorruption):
                self._accept(store, self._derive(wrong), wrong)
            self.assertEqual(
                store._connection.execute("SELECT count(*) FROM workflows").fetchone(),
                (0,),
            )

    def test_fault_rolls_back_all_target_rows(self) -> None:
        bundle, _, _, _ = self._bundle()
        operation_id = self._derive(bundle)

        def fault(stage: str) -> None:
            if stage == "after_domain_before_receipt":
                raise RuntimeError("synthetic reservation ingress fault")

        with SQLiteUnitOfWork.open_v6(self.path) as store:
            store._phase8_reservation_fault_hook = fault
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                self._accept(store, operation_id, bundle)
            for table in (
                "workflows",
                "domain_events",
                "reservation_commands",
                "execution_ledger",
                "outbox_messages",
                "reservation_boundary_ingress_receipts",
            ):
                self.assertEqual(
                    store._connection.execute(
                        f"SELECT count(*) FROM {table}"
                    ).fetchone(),
                    (0,),
                )


if __name__ == "__main__":
    unittest.main()
