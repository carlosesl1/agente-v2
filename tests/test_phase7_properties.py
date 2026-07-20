"""Deterministic Phase 7 property harness integrity."""

from __future__ import annotations

from dataclasses import replace
import ast
from pathlib import Path
import unittest

from reservation_boundary.properties import (
    PROPERTY_CASES,
    PROPERTY_SEED,
    PropertyReport,
    run_property_sequences,
)


ROOT = Path(__file__).resolve().parents[1]


class Phase7PropertyHarnessTests(unittest.TestCase):
    def test_small_run_is_deterministic_nonvacuous_and_reconstructs_totals(self) -> None:
        first = run_property_sequences(seed=PROPERTY_SEED, cases=25)
        second = run_property_sequences(seed=PROPERTY_SEED, cases=25)
        self.assertEqual(first, second)
        self.assertTrue(first.passed)
        self.assertEqual(first.total, 25)
        self.assertEqual(first.total, len(first.rows))
        self.assertEqual(sum(count for _, count in first.scenario_counts), 25)
        self.assertGreaterEqual(len(first.scenario_counts), 4)
        self.assertEqual(len({row.row_hash for row in first.rows}), 25)

    def test_seed_changes_rows_but_not_closed_scenarios(self) -> None:
        first = run_property_sequences(seed=PROPERTY_SEED, cases=25)
        second = run_property_sequences(seed=PROPERTY_SEED + 1, cases=25)
        self.assertNotEqual(first.rows, second.rows)
        self.assertEqual(
            {name for name, _ in first.scenario_counts},
            {name for name, _ in second.scenario_counts},
        )

    def test_integral_counts_require_exact_frozen_tree_pair(self) -> None:
        with self.assertRaises(RuntimeError):
            run_property_sequences(seed=PROPERTY_SEED, cases=PROPERTY_CASES)
        with self.assertRaises(RuntimeError):
            run_property_sequences(
                seed=PROPERTY_SEED,
                cases=PROPERTY_CASES,
                frozen_tree="a" * 40,
                current_tree="b" * 40,
            )

    def test_report_rejects_forged_totals_and_mutated_rows(self) -> None:
        report = run_property_sequences(seed=PROPERTY_SEED, cases=8)
        with self.assertRaises(ValueError):
            replace(report, total=9)
        row = report.rows[0]
        object.__setattr__(row, "passed", False)
        with self.assertRaises(ValueError):
            PropertyReport.from_rows(report.seed, report.rows)

    def test_package_harness_has_no_process_environment_or_network_import(self) -> None:
        source = ROOT / "reservation_boundary/properties.py"
        tree = ast.parse(source.read_text())
        modules = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        forbidden = ("os", "subprocess", "socket", "urllib", "requests", "httpx")
        self.assertEqual([name for name in modules if name.startswith(forbidden)], [])


if __name__ == "__main__":
    unittest.main()
