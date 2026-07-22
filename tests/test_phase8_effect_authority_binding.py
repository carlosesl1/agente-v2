"""Phase 8 ingress-to-allocation binding is atomic and exact."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from reservation_boundary import effects
from reservation_domain import loads_command
from reservation_execution.sqlite_store import DataCorruption as ExecutionCorruption
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.serialization import semantic_hash
from reservation_followup.sqlite_store import DataCorruption as FollowupCorruption
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from tests.test_phase8_effect_authority import Phase8AuthorityContractTests, UTC6
from tests.test_phase8_reservation_target_ingress import Phase8ReservationTargetIngressTests
from tests.test_phase8_settlement_target_ingress import Phase8SettlementTargetIngressTests


def _artifact_hash(domain: str, schema: str, data: dict[str, object]) -> str:
    preimage = json.dumps(
        {"data": data, "schema": schema, "version": 1},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + preimage).hexdigest()


def _reservation_e2e(bundle, allocation_id: str):
    data = bundle._preimage_data() | {
        "qualification_id": "qualification-e2e-1",
        "scenario_id": "scenario-e2e-1",
        "immutable_generation": 1,
        "allocation_id": allocation_id,
    }
    return effects.ReservationRelayBundle(
        genesis_state=bundle.genesis_state,
        phase5_events=bundle.phase5_events,
        summary_outboxes=bundle.summary_outboxes,
        expected_final_state=bundle.expected_final_state,
        expected_final_state_hash=bundle.expected_final_state_hash,
        command_ledger_seed=bundle.command_ledger_seed,
        qualification_id="qualification-e2e-1",
        scenario_id="scenario-e2e-1",
        immutable_generation=1,
        allocation_id=allocation_id,
        artifact_hash=_artifact_hash(
            effects.RESERVATION_RELAY_DOMAIN,
            effects.ReservationRelayBundle.PREIMAGE_SCHEMA,
            data,
        ),
    )


def _settlement_e2e(bundle, allocation_id: str):
    data = bundle._preimage_data() | {
        "qualification_id": "qualification-e2e-1",
        "scenario_id": "scenario-e2e-1",
        "immutable_generation": 1,
        "allocation_id": allocation_id,
    }
    return effects.SettlementRelayBundle(
        workflow_anchor=bundle.workflow_anchor,
        policy=bundle.policy,
        payment_history=bundle.payment_history,
        evidence=bundle.evidence,
        payment_command=bundle.payment_command,
        expected_final_state=bundle.expected_final_state,
        expected_final_state_hash=bundle.expected_final_state_hash,
        qualification_id="qualification-e2e-1",
        scenario_id="scenario-e2e-1",
        immutable_generation=1,
        allocation_id=allocation_id,
        artifact_hash=_artifact_hash(
            effects.SETTLEMENT_RELAY_DOMAIN,
            effects.SettlementRelayBundle.PREIMAGE_SCHEMA,
            data,
        ),
    )


class Phase8AuthorityBindingTests(unittest.TestCase):
    def _reservation_bundle(self):
        helper = Phase8ReservationTargetIngressTests("test_atomic_full_replay_and_duplicate_receipt_are_exact")
        helper.setUp()
        try:
            return helper._bundle()[0]
        finally:
            helper.doCleanups()
            helper.temporary = None

    def _settlement_bundle(self):
        helper = Phase8SettlementTargetIngressTests("test_full_replay_is_atomic_and_duplicate_receipt_is_exact")
        helper.setUp()
        try:
            bundle, state, command = helper._bundle()
            return bundle, state, command
        finally:
            helper.tearDown()
            helper.temporary = None

    def _row(self, target: str, family: str, allocation_id: str, target_hash: str):
        helper = Phase8AuthorityContractTests("test_known_answer_row_manifest_and_installation_receipt")
        return replace(
            helper._target_row(target, family, allocation_id),
            target_binding_hash=target_hash,
        )

    def _manifest(self, row):
        helper = Phase8AuthorityContractTests("test_known_answer_row_manifest_and_installation_receipt")
        return helper._manifest(row)

    def test_reservation_allocation_absence_rejects_and_exact_bind_is_atomic(self) -> None:
        base = self._reservation_bundle()
        command = loads_command(base.command_ledger_seed.decode("utf-8"))
        allocation_id = "allocation-reservation-bind-1"
        bundle = _reservation_e2e(base, allocation_id)
        target_hash = hashlib.sha256(
            b"phase8-authority-target-binding-v1\0" + command.command_id.encode("utf-8")
        ).hexdigest()
        manifest = self._manifest(
            self._row(
                "reservation_e2e_effect_authority",
                "reservation",
                allocation_id,
                target_hash,
            )
        )
        source_hash = "7" * 64
        operation_id = effects.target_operation_id(
            effects.InternalJobKind.HANDOFF,
            bundle.artifact_hash,
            source_hash,
        )
        with tempfile.TemporaryDirectory(prefix="phase8-reservation-bind-") as root:
            missing = Path(root) / "missing.db"
            with SQLiteUnitOfWork.open_v6(missing) as store:
                with self.assertRaises(ExecutionCorruption):
                    store.accept_boundary_reservation(
                        operation_id=operation_id,
                        source_turn_receipt_hash=source_hash,
                        bundle=bundle,
                    )
                self.assertEqual(store._connection.execute("SELECT count(*) FROM workflows").fetchone(), (0,))
            path = Path(root) / "bound.db"
            with SQLiteUnitOfWork.open_v6(path) as store:
                store.install_e2e_reservation_allocations(
                    operation_id="6" * 64,
                    manifest=manifest,
                    installed_at=UTC6,
                )
                first = store.accept_boundary_reservation(
                    operation_id=operation_id,
                    source_turn_receipt_hash=source_hash,
                    bundle=bundle,
                )
                row = store._connection.execute(
                    "SELECT state, bound_subject_id, revision FROM "
                    "reservation_e2e_effect_authority WHERE allocation_id=?",
                    (allocation_id,),
                ).fetchone()
                self.assertEqual(row, ("bound", command.command_id, 1))
                self.assertEqual(
                    store._connection.execute(
                        "SELECT qualification_id, epoch, scenario_id, allocation_id, "
                        "authority_row_hash FROM reservation_boundary_ingress_receipts"
                    ).fetchone(),
                    (
                        bundle.qualification_id,
                        bundle.immutable_generation,
                        bundle.scenario_id,
                        bundle.allocation_id,
                        manifest.rows[0].canonical_hash(),
                    ),
                )
                replay = store.accept_boundary_reservation(
                    operation_id=operation_id,
                    source_turn_receipt_hash=source_hash,
                    bundle=bundle,
                )
                self.assertEqual(replay.to_canonical_bytes(), first.to_canonical_bytes())
                self.assertEqual(
                    store._connection.execute(
                        "SELECT state, revision FROM reservation_e2e_effect_authority "
                        "WHERE allocation_id=?",
                        (allocation_id,),
                    ).fetchone(),
                    ("bound", 1),
                )

    def test_settlement_allocation_absence_rejects_and_exact_bind_is_atomic(self) -> None:
        base, _, command = self._settlement_bundle()
        allocation_id = "allocation-payment-bind-1"
        bundle = _settlement_e2e(base, allocation_id)
        target_hash = hashlib.sha256(
            b"phase8-authority-target-binding-v1\0"
            + command.settlement_command_id.encode("utf-8")
        ).hexdigest()
        manifest = self._manifest(
            self._row(
                "followup_e2e_effect_authority",
                "payment",
                allocation_id,
                target_hash,
            )
        )
        source_hash = "7" * 64
        operation_id = effects.target_operation_id(
            effects.InternalJobKind.HANDOFF,
            bundle.artifact_hash,
            source_hash,
        )
        with tempfile.TemporaryDirectory(prefix="phase8-settlement-bind-") as root:
            path = Path(root) / "followup.db"
            with SQLiteFollowupUnitOfWork.open_v2(path) as store:
                with self.assertRaises(FollowupCorruption):
                    store.accept_boundary_settlement(
                        operation_id=operation_id,
                        source_turn_receipt_hash=source_hash,
                        bundle=bundle,
                    )
                store.install_e2e_followup_allocations(
                    operation_id="6" * 64,
                    manifest=manifest,
                    installed_at=UTC6,
                )
                receipt = store.accept_boundary_settlement(
                    operation_id=operation_id,
                    source_turn_receipt_hash=source_hash,
                    bundle=bundle,
                )
                self.assertEqual(receipt.target_result_hash, semantic_hash(command)[:0] + bundle.expected_final_state_hash)
                self.assertEqual(
                    store._connection.execute(
                        "SELECT state, bound_subject_id, revision FROM "
                        "followup_e2e_effect_authority WHERE allocation_id=?",
                        (allocation_id,),
                    ).fetchone(),
                    ("bound", command.settlement_command_id, 1),
                )
                self.assertEqual(
                    store._connection.execute(
                        "SELECT qualification_id, epoch, scenario_id, allocation_id, "
                        "authority_row_hash FROM payment_boundary_ingress_receipts"
                    ).fetchone(),
                    (
                        bundle.qualification_id,
                        bundle.immutable_generation,
                        bundle.scenario_id,
                        bundle.allocation_id,
                        manifest.rows[0].canonical_hash(),
                    ),
                )


if __name__ == "__main__":
    unittest.main()
