"""Phase 8 atomic settlement target-ingress ownership."""

from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from reservation_boundary import effects
from reservation_followup.payment import (
    FinancialConfirmationReceived,
    FinancialSummaryRecorded,
    PaymentEvidenceRecorded,
    PaymentMethod,
    PaymentMethodSelected,
    PaymentSettlementCommand,
    PaymentWorkflow,
    financial_summary_hash,
    new_payment,
    reduce_payment,
)
from reservation_followup.serialization import semantic_hash, to_wire_json
from reservation_followup.sqlite_store import DataCorruption, SQLiteFollowupUnitOfWork
from tests.phase6_helpers import (
    T0,
    confirmed_anchor,
    payment_effect_policy,
    payment_evidence_trust,
    pix_visual_evidence,
)


class Phase8SettlementTargetIngressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="phase8-settlement-target-")
        self.path = Path(self.temporary.name) / "followup.db"
        self.source_hash = "7" * 64

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _bundle(self):
        anchor = confirmed_anchor()
        policy = payment_effect_policy()
        state = new_payment(anchor, policy).state
        events = []
        commands = []

        method = PaymentMethodSelected(
            event_id="payment:event:phase8:method:1",
            payment_id=state.subject.payment_id,
            method=PaymentMethod.PIX,
            selected_at=T0 + timedelta(seconds=1),
        )
        transition = reduce_payment(state, method)
        state = transition.state
        events.append(method)

        summary = FinancialSummaryRecorded(
            event_id="payment:event:phase8:summary:1",
            subject=state.subject,
            summary_hash=financial_summary_hash(state.subject),
            recorded_at=T0 + timedelta(seconds=2),
        )
        transition = reduce_payment(state, summary)
        state = transition.state
        events.append(summary)

        confirmation = FinancialConfirmationReceived(
            event_id="payment:event:phase8:confirmation:1",
            payment_id=state.subject.payment_id,
            payment_version=state.subject.payment_version,
            economic_signature=state.subject.economic_signature,
            summary_hash=summary.summary_hash,
            confirmation_id="payment:confirmation:phase8:1",
            confirmed_at=T0 + timedelta(seconds=3),
        )
        transition = reduce_payment(state, confirmation)
        state = transition.state
        events.append(confirmation)

        evidence = PaymentEvidenceRecorded(
            event_id="payment:event:phase8:evidence:1",
            payment_id=state.subject.payment_id,
            payment_version=state.subject.payment_version,
            economic_signature=state.subject.economic_signature,
            evidence=pix_visual_evidence(observed_at=T0 + timedelta(seconds=4)),
            trust=payment_evidence_trust(),
            recorded_at=T0 + timedelta(seconds=4),
        )
        transition = reduce_payment(state, evidence)
        state = transition.state
        events.append(evidence)
        commands.extend(transition.commands)
        self.assertEqual(len(commands), 1)

        fields = {
            "workflow_anchor": to_wire_json(anchor).encode("utf-8"),
            "policy": to_wire_json(policy).encode("utf-8"),
            "payment_history": tuple(to_wire_json(event).encode("utf-8") for event in events),
            "evidence": (to_wire_json(evidence.evidence).encode("utf-8"),),
            "payment_command": to_wire_json(commands[0]).encode("utf-8"),
            "expected_final_state": to_wire_json(state).encode("utf-8"),
            "expected_final_state_hash": semantic_hash(state),
            "qualification_id": None,
            "scenario_id": None,
            "immutable_generation": None,
            "allocation_id": None,
        }
        preimage_data = {
            key: (
                base64.b64encode(value).decode("ascii")
                if type(value) is bytes
                else [base64.b64encode(item).decode("ascii") for item in value]
                if type(value) is tuple
                else value
            )
            for key, value in fields.items()
        }
        preimage = json.dumps(
            {
                "data": preimage_data,
                "schema": "phase8-settlement-relay-bundle-preimage",
                "version": 1,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        artifact_hash = hashlib.sha256(
            b"phase8-settlement-relay-bundle-v1\0" + preimage
        ).hexdigest()
        return effects.SettlementRelayBundle(**fields, artifact_hash=artifact_hash), state, commands[0]

    def _derive(self, bundle) -> str:
        return effects.target_operation_id(
            effects.InternalJobKind.HANDOFF,
            bundle.artifact_hash,
            self.source_hash,
        )

    def _accept(self, store, operation_id: str, bundle):
        accept = getattr(store, "accept_boundary_settlement", None)
        self.assertIsNotNone(accept, "Phase 6 v2 store must own settlement ingress")
        assert accept is not None
        return accept(
            operation_id=operation_id,
            source_turn_receipt_hash=self.source_hash,
            bundle=bundle,
        )

    def test_public_signature_is_exact(self) -> None:
        method = getattr(SQLiteFollowupUnitOfWork, "accept_boundary_settlement", None)
        self.assertIsNotNone(method)
        assert method is not None
        signature = inspect.signature(method)
        self.assertEqual(
            tuple(signature.parameters),
            ("self", "operation_id", "source_turn_receipt_hash", "bundle"),
        )
        for name in ("operation_id", "source_turn_receipt_hash", "bundle"):
            self.assertIs(signature.parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_full_replay_is_atomic_and_duplicate_receipt_is_exact(self) -> None:
        bundle, expected_state, command = self._bundle()
        operation_id = self._derive(bundle)
        with SQLiteFollowupUnitOfWork.open_v2(self.path) as store:
            first = self._accept(store, operation_id, bundle)
            self.assertEqual(store.load_payment(expected_state.subject.payment_id), expected_state)
            self.assertEqual(first.target_result_hash, semantic_hash(expected_state))
            for table, expected in (
                ("payment_boundary_ingress_receipts", 1),
                ("payment_commands", 1),
                ("payment_ledger", 1),
            ):
                self.assertEqual(
                    store._connection.execute(f"SELECT count(*) FROM {table}").fetchone(),
                    (expected,),
                )
            self.assertEqual(
                store._connection.execute(
                    "SELECT command_json FROM payment_commands WHERE settlement_command_id=?",
                    (command.settlement_command_id,),
                ).fetchone(),
                (to_wire_json(command),),
            )
        with SQLiteFollowupUnitOfWork.open_v2(self.path) as reopened:
            replay = self._accept(reopened, operation_id, bundle)
            self.assertEqual(replay.to_canonical_bytes(), first.to_canonical_bytes())

    def test_wrong_operation_or_evidence_writes_nothing(self) -> None:
        bundle, _, _ = self._bundle()
        with SQLiteFollowupUnitOfWork.open_v2(self.path) as store:
            with self.assertRaises(ValueError):
                self._accept(store, "f" * 64, bundle)
            wrong_evidence = b'{"wrong":true}'
            fields = {
                "workflow_anchor": bundle.workflow_anchor,
                "policy": bundle.policy,
                "payment_history": bundle.payment_history,
                "evidence": (wrong_evidence,),
                "payment_command": bundle.payment_command,
                "expected_final_state": bundle.expected_final_state,
                "expected_final_state_hash": bundle.expected_final_state_hash,
                "qualification_id": None,
                "scenario_id": None,
                "immutable_generation": None,
                "allocation_id": None,
            }
            data = {
                key: (
                    base64.b64encode(value).decode("ascii")
                    if type(value) is bytes
                    else [base64.b64encode(item).decode("ascii") for item in value]
                    if type(value) is tuple
                    else value
                )
                for key, value in fields.items()
            }
            preimage = json.dumps(
                {
                    "data": data,
                    "schema": "phase8-settlement-relay-bundle-preimage",
                    "version": 1,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            wrong = effects.SettlementRelayBundle(
                **fields,
                artifact_hash=hashlib.sha256(
                    b"phase8-settlement-relay-bundle-v1\0" + preimage
                ).hexdigest(),
            )
            with self.assertRaises(DataCorruption):
                self._accept(store, self._derive(wrong), wrong)
            self.assertEqual(
                store._connection.execute("SELECT count(*) FROM payment_workflows").fetchone(),
                (0,),
            )

    def test_fault_rolls_back_domain_command_ledger_and_receipt(self) -> None:
        bundle, _, _ = self._bundle()
        operation_id = self._derive(bundle)

        def fault(stage: str) -> None:
            if stage == "after_domain_before_receipt":
                raise RuntimeError("synthetic settlement ingress fault")

        with SQLiteFollowupUnitOfWork.open_v2(self.path) as store:
            store._phase8_settlement_fault_hook = fault
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                self._accept(store, operation_id, bundle)
            for table in (
                "payment_workflows",
                "payment_events",
                "payment_commands",
                "payment_ledger",
                "payment_boundary_ingress_receipts",
            ):
                self.assertEqual(
                    store._connection.execute(f"SELECT count(*) FROM {table}").fetchone(),
                    (0,),
                )


if __name__ == "__main__":
    unittest.main()
