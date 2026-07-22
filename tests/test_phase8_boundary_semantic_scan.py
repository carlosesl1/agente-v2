"""Phase 8 semantic startup scan over receipts, children, hashes and allocations."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from reservation_boundary.sqlite_store import DataCorruption, SQLiteBoundaryStore
from tests import test_phase8_boundary_atomic_commit as atomic_module


class Phase8BoundarySemanticScanTests(unittest.TestCase):
    def _persist(self, path: Path) -> None:
        owner = atomic_module.Phase8BoundaryAtomicCommitTests(methodName="runTest")
        owner.setUp()
        owner.store.close()
        owner.store = SQLiteBoundaryStore.open_path_v8(path)
        current, token, commit, receipt, artifacts, relays, internal, public = owner._case()
        owner.store.commit_turn_v8(
            expected_version=current.version,
            fencing_token=token,
            commit=commit,
            receipt=receipt,
            artifacts=artifacts,
            command_relays=relays,
            internal_jobs=internal,
            public_rows=public,
            committed_at=receipt.committed_at,
        )
        owner.store.close()

    @staticmethod
    def _tamper(path: Path, sql: str, parameters: tuple[object, ...] = ()) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute(sql, parameters)
            connection.commit()
        finally:
            connection.close()

    def test_exact_store_passes_live_scan_and_reopen_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boundary.sqlite3"
            self._persist(path)
            store = SQLiteBoundaryStore.open_path_v8(path)
            self.addCleanup(store.close)
            scan = getattr(store, "semantic_scan_v8", None)
            self.assertIsNotNone(scan, "v8 store must expose its startup semantic scan")
            self.assertIsNone(scan())

    def test_startup_rejects_each_semantic_tamper_with_exact_ddl_intact(self) -> None:
        mutations = {
            "state_hash": (
                "UPDATE boundary_state SET state_hash=?",
                ("f" * 64,),
            ),
            "receipt_hash": (
                "UPDATE boundary_events SET turn_receipt_hash=?",
                ("f" * 64,),
            ),
            "missing_relay": (
                "DELETE FROM boundary_command_relays",
                (),
            ),
            "artifact_bytes": (
                "UPDATE boundary_turn_artifacts SET artifact_json=? "
                "WHERE artifact_kind='maya_proposal'",
                ('{"data":{},"schema":"phase8-maya-turn-proposal","version":1}',),
            ),
            "public_capability_binding": (
                "UPDATE boundary_public_outbox SET capability_policy_digest=?",
                ("f" * 64,),
            ),
        }
        for name, (sql, parameters) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "boundary.sqlite3"
                self._persist(path)
                self._tamper(path, sql, parameters)
                with self.assertRaisesRegex(DataCorruption, "semantic|receipt|state|child|artifact|allocation"):
                    SQLiteBoundaryStore.open_path_v8(path)


if __name__ == "__main__":
    unittest.main()
