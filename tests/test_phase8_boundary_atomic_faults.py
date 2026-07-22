"""Phase 8 boundary-specific rollback matrix for the atomic turn commit."""

from __future__ import annotations

import unittest

from tests import test_phase8_boundary_atomic_commit as atomic_module


EXPECTED_STAGES = (
    "before_begin",
    "after_begin",
    "before_event_lookup",
    "after_event_lookup",
    "before_previous_receipt_lookup",
    "after_previous_receipt_lookup",
    "before_state_lookup",
    "after_state_lookup",
    "before_state_update",
    "after_state_update",
    "before_event_insert",
    "after_event_insert",
    "before_source_insert_0",
    "after_source_insert_0",
    "before_artifact_insert_0",
    "after_artifact_insert_0",
    "before_artifact_insert_1",
    "after_artifact_insert_1",
    "before_command_insert_0",
    "after_command_insert_0",
    "before_relay_insert_0",
    "after_relay_insert_0",
    "before_internal_outbox_insert_0",
    "after_internal_outbox_insert_0",
    "before_allocation_cas_0",
    "after_allocation_cas_0",
    "before_public_outbox_insert_0",
    "after_public_outbox_insert_0",
    "before_commit",
)


class InjectedFault(RuntimeError):
    pass


class Phase8BoundaryAtomicFaultTests(unittest.TestCase):
    def _prepared(self):
        owner = atomic_module.Phase8BoundaryAtomicCommitTests(methodName="runTest")
        owner.setUp()
        self.addCleanup(owner.store.close)
        current, token, commit, receipt, artifacts, relays, internal, public = owner._case()
        kwargs = {
            "expected_version": current.version,
            "fencing_token": token,
            "commit": commit,
            "receipt": receipt,
            "artifacts": artifacts,
            "command_relays": relays,
            "internal_jobs": internal,
            "public_rows": public,
            "committed_at": receipt.committed_at,
        }
        return owner.store, kwargs

    @staticmethod
    def _snapshot(store) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
        return tuple(
            (
                table,
                tuple(store._connection.execute(f"SELECT * FROM {table} ORDER BY rowid")),
            )
            for table in atomic_module.TABLES
        )

    def test_fault_hook_exposes_every_statement_boundary_and_before_commit(self) -> None:
        store, kwargs = self._prepared()
        observed: list[str] = []
        store.commit_turn_v8(**kwargs, fault_hook=observed.append)
        self.assertEqual(tuple(observed), EXPECTED_STAGES)
        self.assertEqual(len(observed), len(set(observed)))

    def test_each_injected_fault_rolls_back_every_row_and_allocation_cas(self) -> None:
        for stage in EXPECTED_STAGES:
            with self.subTest(stage=stage):
                store, kwargs = self._prepared()
                before = self._snapshot(store)

                def inject(observed: str) -> None:
                    if observed == stage:
                        raise InjectedFault(stage)

                with self.assertRaisesRegex(InjectedFault, stage):
                    store.commit_turn_v8(**kwargs, fault_hook=inject)
                self.assertEqual(self._snapshot(store), before)
                self.assertFalse(store._connection.in_transaction)

                persisted = store.commit_turn_v8(**kwargs)
                self.assertEqual(persisted, kwargs["receipt"])
                counts = tuple(
                    store._connection.execute(
                        f"SELECT count(*) FROM {table}"
                    ).fetchone()[0]
                    for table in atomic_module.TABLES
                )
                self.assertEqual(counts, (1, 1, 1, 2, 1, 1, 1, 1, 2, 1, 0))


if __name__ == "__main__":
    unittest.main()
