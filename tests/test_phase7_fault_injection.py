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
        self.assertTrue(hasattr(report, "contention_details"))
        details = report.contention_details
        self.assertEqual(len(details), report.contention_rows)
        for domain in CONTENTION_DOMAINS:
            rows = tuple(row for row in details if row.domain == domain)
            self.assertEqual(len(rows), 2)
            self.assertEqual(tuple(row.round_index for row in rows), (0, 1))
            self.assertTrue(all(row.contenders == 2 for row in rows))
            self.assertTrue(all(row.winners == 1 for row in rows))
            self.assertTrue(all(row.conflicts == 1 for row in rows))
            self.assertTrue(all(row.passed for row in rows))
            self.assertTrue(all(row.detail_hash for row in rows))
        self.assertTrue(all(row.state_rows == 1 for row in details))
        self.assertTrue(
            all(
                row.event_rows == (0 if row.domain == "genesis" else 1)
                and row.command_rows == (1 if row.domain == "command" else 0)
                and row.outbox_rows == (1 if row.domain == "outbox" else 0)
                for row in details
            )
        )
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
        with self.assertRaises(ValueError):
            replace(report.contention_details[0], detail_hash="0" * 64)
        forged_catalog = report.contention_details[:-1] + (report.contention_details[0],)
        with self.assertRaises(ValueError):
            replace(report, contention_details=forged_catalog)


if __name__ == "__main__":
    unittest.main()
