"""Phase 8 exact aggregate replay identity and divergence closure."""

from __future__ import annotations

from dataclasses import replace
import json
import unittest

from reservation_boundary.conversation import SourceEventIdentity
from reservation_boundary.sqlite_store import IdentityConflict, TurnReceipt
from tests import test_phase8_boundary_atomic_commit as atomic_module


class Phase8BoundaryReplayTests(unittest.TestCase):
    def _prepared(self):
        owner = atomic_module.Phase8BoundaryAtomicCommitTests(methodName="runTest")
        owner.setUp()
        self.addCleanup(owner.store.close)
        values = owner._case()
        current, token, commit, receipt, artifacts, relays, internal, public = values
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
    def _counts(store) -> tuple[int, ...]:
        return tuple(
            store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in atomic_module.TABLES
        )

    def test_identical_aggregate_retry_returns_exact_receipt_without_new_rows(self) -> None:
        store, kwargs = self._prepared()
        first = store.commit_turn_v8(**kwargs)
        before = self._counts(store)
        second = store.commit_turn_v8(**kwargs)
        self.assertEqual(second, first)
        self.assertEqual(second.to_canonical_bytes(), first.to_canonical_bytes())
        self.assertEqual(self._counts(store), before)
        self.assertEqual(before, (1, 1, 1, 4, 1, 1, 1, 1, 2, 1, 0))

    def test_same_aggregate_with_divergent_receipt_is_identity_conflict(self) -> None:
        store, kwargs = self._prepared()
        store.commit_turn_v8(**kwargs)
        before = self._counts(store)
        receipt: TurnReceipt = kwargs["receipt"]
        divergent = replace(
            receipt,
            source_events=(SourceEventIdentity("event-source-1", "f" * 64),),
            artifact_hash="",
        )
        with self.assertRaises(IdentityConflict):
            store.commit_turn_v8(**(kwargs | {"receipt": divergent}))
        self.assertEqual(self._counts(store), before)

    def test_same_receipt_with_divergent_artifact_bytes_is_identity_conflict(self) -> None:
        store, kwargs = self._prepared()
        store.commit_turn_v8(**kwargs)
        before = self._counts(store)
        artifacts = kwargs["artifacts"]
        divergent_bytes = json.dumps(
            {
                "schema": "phase8-maya-turn-proposal",
                "version": 1,
                "data": {
                    "aggregate_turn_id": "turn-atomic-1",
                    "closure_hash": "f" * 64,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        divergent_artifacts = (
            replace(artifacts[0], canonical_bytes=divergent_bytes),
            artifacts[1],
        )
        with self.assertRaises(IdentityConflict):
            store.commit_turn_v8(**(kwargs | {"artifacts": divergent_artifacts}))
        self.assertEqual(self._counts(store), before)


if __name__ == "__main__":
    unittest.main()
