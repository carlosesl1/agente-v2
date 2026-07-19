from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from reservation_execution.sqlite_store import DataCorruption, SQLiteUnitOfWork
from scripts.run_phase5_mutations import MUTANTS, Mutant, _run_one, run_mutants
from scripts.run_phase5_properties import _MIN_GATE_CASES
from tests.phase5_helpers import persist_script, workflow_events

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MUTANTS = {
    "remove_optimistic_revision",
    "accept_divergent_event_hash",
    "commit_command_outside_transaction",
    "remove_unique_idempotency",
    "allow_second_dispatch_slot",
    "ignore_fencing_token",
    "recover_post_fence_as_retry",
    "post_fence_exception_as_not_called",
    "allow_not_called_from_dispatch",
    "redispatch_called_unknown",
    "outbox_failure_requeues_command",
    "mark_delivered_without_receipt",
    "accept_divergent_outcome",
    "accept_tampered_command_hash",
    "accept_tampered_state_hash",
    "skip_manual_review",
    "allow_effect_without_evidence",
    "allow_not_called_provider_reference",
    "reduce_property_gate",
    "remove_required_fault_point",
}


def runtime_digest() -> str:
    digest = hashlib.sha256()
    for directory in (
        "reservation_domain",
        "reservation_execution",
        "scripts",
        "tests",
    ):
        for path in sorted((ROOT / directory).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


class Phase5MutationRunnerTests(unittest.TestCase):
    def test_runner_refuses_baseline_failing_test_as_a_kill(self) -> None:
        mutant = Mutant(
            name="synthetic_baseline_failure",
            path="scripts/run_phase5_properties.py",
            old="_MIN_GATE_CASES = 20_000\n",
            new="_MIN_GATE_CASES = 1\n",
            test="tests.test_phase5_types.NonexistentCase.test_missing",
        )
        result = _run_one(root=ROOT, mutant=mutant)
        self.assertFalse(result["killed"])
        self.assertNotEqual(result["baseline_exit_code"], 0)
        self.assertEqual(result["exit_code"], -1)

    def test_catalog_tests_pass_on_the_unmodified_tree(self) -> None:
        targets = tuple(dict.fromkeys(mutant.test for mutant in MUTANTS))
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", *targets, "-v"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_second_slot_mutant_targets_the_permit_contract(self) -> None:
        mutant = next(
            item for item in MUTANTS if item.name == "allow_second_dispatch_slot"
        )
        self.assertEqual(mutant.path, "reservation_execution/types.py")
        self.assertIn("dispatch_slot", mutant.old)
        self.assertEqual(
            mutant.test,
            "tests.test_phase5_types.Phase5ExecutionTypeTests."
            "test_dispatch_permit_requires_exact_lease_slot_hash_and_utc",
        )

    def test_event_hash_guard_rejects_digest_only_tamper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-event-hash-") as directory:
            path = Path(directory) / "store.db"
            store = SQLiteUnitOfWork.open(path)
            try:
                initial, script = workflow_events(
                    "cloudbeds",
                    workflow_id="workflow:mutation:event-hash",
                )
                store.create_workflow(initial)
                event = script[0][0]
                first = store.apply_event(initial.meta.workflow_id, 0, event)
                connection = sqlite3.connect(path)
                connection.execute(
                    "UPDATE domain_events SET event_hash=? WHERE event_id=?",
                    ("f" * 64, event.event_id),
                )
                connection.commit()
                connection.close()
                with self.assertRaises(DataCorruption):
                    store.apply_event(
                        initial.meta.workflow_id,
                        first.state.meta.revision,
                        event,
                    )
            finally:
                store.close()

    def test_command_hash_guard_rejects_digest_only_tamper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-command-hash-") as directory:
            path = Path(directory) / "store.db"
            store = SQLiteUnitOfWork.open(path)
            try:
                workflow_id = "workflow:mutation:command-hash"
                initial, script = workflow_events(
                    "cloudbeds",
                    workflow_id=workflow_id,
                )
                store.create_workflow(initial)
                final = persist_script(store, workflow_id, script)[-1]
                command_id = final.commands[0].command_id
                connection = sqlite3.connect(path)
                connection.execute(
                    "UPDATE reservation_commands SET command_hash=? "
                    "WHERE command_id=?",
                    ("f" * 64, command_id),
                )
                connection.commit()
                connection.close()
                with self.assertRaises(DataCorruption):
                    store.load_command(command_id)
            finally:
                store.close()

    def test_property_gate_minimum_is_closed(self) -> None:
        self.assertEqual(_MIN_GATE_CASES, 20_000)

    def test_catalog_is_closed_unique_material_and_targets_once(self) -> None:
        names = tuple(mutant.name for mutant in MUTANTS)
        self.assertGreaterEqual(len(names), 20)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(REQUIRED_MUTANTS.issubset(names))
        for mutant in MUTANTS:
            with self.subTest(mutant=mutant.name):
                self.assertIs(type(mutant.name), str)
                self.assertIs(type(mutant.path), str)
                self.assertIs(type(mutant.old), str)
                self.assertIs(type(mutant.new), str)
                self.assertIs(type(mutant.test), str)
                self.assertTrue(mutant.name)
                self.assertTrue(mutant.path)
                self.assertTrue(mutant.old)
                self.assertNotEqual(mutant.old, mutant.new)
                self.assertTrue(mutant.test)
                source = (ROOT / mutant.path).read_text(encoding="utf-8")
                self.assertEqual(source.count(mutant.old), 1)

    def test_runner_kills_selected_mutant_without_touching_working_tree(self) -> None:
        before = runtime_digest()
        report = run_mutants(
            root=ROOT,
            selected_names=("allow_second_dispatch_slot",),
        )
        self.assertTrue(report["all_killed"])
        self.assertEqual(report["mutant_count"], 1)
        self.assertEqual(report["mutants"][0]["name"], "allow_second_dispatch_slot")
        self.assertEqual(report["mutants"][0]["exit_code"], 1)
        self.assertEqual(runtime_digest(), before)

    def test_required_fault_point_mutant_is_hash_seed_independent(self) -> None:
        before = runtime_digest()
        for seed in ("0", "1", "17"):
            with self.subTest(seed=seed):
                environment = dict(os.environ)
                environment["PYTHONHASHSEED"] = seed
                completed = subprocess.run(
                    [
                        sys.executable,
                        "scripts/run_phase5_mutations.py",
                        "--only",
                        "remove_required_fault_point",
                    ],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                report = json.loads(completed.stdout)
                self.assertTrue(report["all_killed"])
                self.assertEqual(report["mutant_count"], 1)
                self.assertEqual(report["mutants"][0]["exit_code"], 1)
        self.assertEqual(runtime_digest(), before)

    def test_cli_rejects_unknown_mutant(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/run_phase5_mutations.py",
                "--only",
                "unknown_mutant",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn('"all_killed": true', completed.stdout)


if __name__ == "__main__":
    unittest.main()
