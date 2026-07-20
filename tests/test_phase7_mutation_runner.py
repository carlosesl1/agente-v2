"""Closed Phase 7 mutation probe catalog."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from reservation_boundary.faults import MUTANT_COUNT
from reservation_boundary.mutations import MUTANTS, run_mutations


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (
    "id_inference",
    "dual_write",
    "bool_as_int",
    "stale_confirmation",
    "command_in_turn",
    "alias_escalation",
    "deadline_write",
    "cas_bypass",
    "comparator_downgrade",
    "plugin_business_guard",
    "process_execution",
    "duplicate_json",
)


class Phase7MutationHarnessTests(unittest.TestCase):
    def test_catalog_is_closed_unique_and_every_mutant_has_one_owner(self) -> None:
        self.assertEqual(MUTANT_COUNT, 12)
        self.assertEqual(tuple(item.name for item in MUTANTS), EXPECTED)
        self.assertEqual(len({item.name for item in MUTANTS}), 12)
        self.assertTrue(all(item.owner.startswith("tests.test_phase7_") for item in MUTANTS))
        self.assertTrue(all(item.probe_name for item in MUTANTS))

    def test_focused_run_kills_first_six_without_subprocess(self) -> None:
        report = run_mutations(focused=True)
        self.assertTrue(report.passed)
        self.assertEqual(report.total, 6)
        self.assertEqual(report.killed, 6)
        self.assertEqual(report.survived, 0)
        self.assertEqual(tuple(row.name for row in report.rows), EXPECTED[:6])

    def test_integral_run_requires_frozen_tree(self) -> None:
        with self.assertRaises(RuntimeError):
            run_mutations(focused=False)

    def test_runner_has_no_eval_exec_or_process_import(self) -> None:
        source = ROOT / "reservation_boundary/mutations.py"
        tree = ast.parse(source.read_text())
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(calls & {"eval", "exec", "compile"})
        modules = [
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        self.assertNotIn("subprocess", modules)


if __name__ == "__main__":
    unittest.main()
