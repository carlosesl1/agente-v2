"""Focused fault/restart/contention harness integrity."""

from __future__ import annotations

from dataclasses import replace
import unittest

from reservation_boundary.faults import (
    CONTENTION_DOMAINS,
    CONTENTION_ROUNDS_PER_DOMAIN,
    RESTART_SCHEDULES,
    FaultReport,
    run_fault_matrix,
)


class Phase7FaultHarnessTests(unittest.TestCase):
    def test_focused_run_exercises_real_fault_restart_and_contention_paths(self) -> None:
        report = run_fault_matrix(focused=True)
        self.assertTrue(report.passed)
        self.assertGreaterEqual(len(report.faults), 4)
        self.assertEqual(report.restart_schedules, 10)
        self.assertEqual(report.contention_rows, 2 * len(CONTENTION_DOMAINS))
        self.assertTrue(all(row.passed for row in report.faults))
        self.assertEqual(len({row.name for row in report.faults}), len(report.faults))

    def test_integral_mode_requires_frozen_tree_before_work(self) -> None:
        with self.assertRaises(RuntimeError):
            run_fault_matrix(focused=False)
        with self.assertRaises(RuntimeError):
            run_fault_matrix(
                focused=False,
                frozen_tree="a" * 40,
                current_tree="b" * 40,
            )

    def test_integral_constants_are_exact(self) -> None:
        self.assertEqual(RESTART_SCHEDULES, 2_000)
        self.assertEqual(
            CONTENTION_DOMAINS,
            ("genesis", "event", "command", "outbox"),
        )
        self.assertEqual(CONTENTION_ROUNDS_PER_DOMAIN, 50)

    def test_report_totals_are_derived(self) -> None:
        report = run_fault_matrix(focused=True)
        with self.assertRaises(ValueError):
            replace(report, passed=False)
        with self.assertRaises((TypeError, ValueError)):
            FaultReport(report.faults, True, 10, True, 9)


if __name__ == "__main__":
    unittest.main()
