from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.run_phase5_faults import (
    FAULT_POINTS,
    run_fault_matrix,
    run_restart_schedules,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_phase5_faults.py"
SEED = 2026071905
EXPECTED_FAULT_POINTS = (
    "before_event",
    "after_event_before_state",
    "after_state_before_command",
    "after_command_before_ledger",
    "after_ledger_before_commit",
    "after_commit_before_claim",
    "after_claim_before_prepare",
    "during_prepare",
    "after_prepare_before_fence",
    "after_fence_before_dispatch",
    "during_dispatch",
    "after_dispatch_before_outcome",
    "after_outcome_before_state",
    "after_state_before_outbox",
    "after_outbox_before_commit",
    "during_delivery",
    "after_delivery_before_receipt",
)


class Phase5FaultInjectionTests(unittest.TestCase):
    def test_fault_point_manifest_is_closed_and_exact(self) -> None:
        self.assertEqual(FAULT_POINTS, EXPECTED_FAULT_POINTS)
        self.assertEqual(len(set(FAULT_POINTS)), 17)

    def test_all_fault_points_survive_rollback_or_crash_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-fault-matrix-") as directory:
            report = run_fault_matrix(seed=SEED, workdir=Path(directory))

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["phase"], "phase-05-durable-command-execution")
        self.assertEqual(tuple(report["fault_points"]), EXPECTED_FAULT_POINTS)
        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["violations"], 0)
        self.assertEqual(len(report["schedules"]), 17)
        for schedule in report["schedules"]:
            with self.subTest(fault_point=schedule["fault_point"]):
                self.assertLessEqual(schedule["command_count"], 1)
                self.assertLessEqual(schedule["dispatch_slots_consumed"], 1)
                self.assertLessEqual(schedule["provider_calls"], 1)
                self.assertEqual(schedule["partial_transactions"], 0)
                self.assertEqual(schedule["called_unknown_redispatches"], 0)
                if schedule["mechanism"] == "transaction_trigger":
                    self.assertIsNone(schedule["child_exit_code"])
                else:
                    self.assertEqual(schedule["mechanism"], "process_crash")
                    self.assertEqual(schedule["child_exit_code"], 91)

    def test_restart_schedules_are_deterministic_and_safe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-restarts-a-") as first_dir:
            first = run_restart_schedules(
                seed=SEED,
                schedules=8,
                workdir=Path(first_dir),
            )
        with tempfile.TemporaryDirectory(prefix="phase5-restarts-b-") as second_dir:
            second = run_restart_schedules(
                seed=SEED,
                schedules=8,
                workdir=Path(second_dir),
            )

        self.assertEqual(first, second)
        self.assertEqual(first["result"], "passed")
        self.assertEqual(first["configuration"]["schedules"], 8)
        self.assertEqual(first["violations"], 0)
        for schedule in first["schedules"]:
            self.assertLessEqual(schedule["command_count"], 1)
            self.assertLessEqual(schedule["dispatch_slots_consumed"], 1)
            self.assertLessEqual(schedule["provider_calls"], 1)
            self.assertEqual(schedule["partial_transactions"], 0)
            self.assertEqual(schedule["called_unknown_redispatches"], 0)
            self.assertEqual(schedule["child_exit_code"], 91)

    def test_gate_mode_rejects_below_minimums(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-fault-gate-") as directory:
            base = Path(directory)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--seed",
                    str(SEED),
                    "--restart-schedules",
                    "1999",
                    "--contention-rounds",
                    "49",
                    "--write-fault-matrix",
                    str(base / "faults.json"),
                    "--write-restart",
                    str(base / "restarts.json"),
                    "--write-concurrency",
                    str(base / "concurrency.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("restart-schedules must be at least 2000", completed.stderr)
        self.assertIn("contention-rounds must be at least 50", completed.stderr)

    def test_smoke_cli_writes_three_passed_envelopes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-fault-cli-") as directory:
            base = Path(directory)
            paths = {
                "faults": base / "faults.json",
                "restart": base / "restart.json",
                "concurrency": base / "concurrency.json",
            }
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--seed",
                    str(SEED),
                    "--restart-schedules",
                    "8",
                    "--contention-rounds",
                    "2",
                    "--smoke",
                    "--write-fault-matrix",
                    str(paths["faults"]),
                    "--write-restart",
                    str(paths["restart"]),
                    "--write-concurrency",
                    str(paths["concurrency"]),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            envelopes = {name: json.loads(path.read_text()) for name, path in paths.items()}

        self.assertEqual(tuple(envelopes["faults"]["fault_points"]), FAULT_POINTS)
        self.assertEqual(envelopes["restart"]["configuration"]["schedules"], 8)
        self.assertEqual(envelopes["concurrency"]["configuration"]["rounds"], 2)
        for envelope in envelopes.values():
            self.assertEqual(envelope["result"], "passed")
            self.assertEqual(envelope["violations"], 0)


if __name__ == "__main__":
    unittest.main()
