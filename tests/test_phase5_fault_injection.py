from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.run_phase5_faults import (
    FAULT_POINTS,
    _schedule_violations,
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

        by_point = {item["fault_point"]: item for item in report["schedules"]}
        for point in (
            "after_claim_before_prepare",
            "during_prepare",
            "after_prepare_before_fence",
        ):
            with self.subTest(exact_pre_fence_recovery=point):
                schedule = by_point[point]
                self.assertEqual(schedule["pre_dispatch_released"], 1)
                self.assertEqual(schedule["called_unknown"], 0)
                self.assertEqual(schedule["final_ledger_status"], "outcome_recorded")
                self.assertEqual(schedule["final_fencing_token"], 2)
                self.assertEqual(schedule["final_claim_count"], 2)
                self.assertEqual(schedule["provider_calls"], 1)
                self.assertEqual(schedule["worker_disposition"], "completed")
        for point, expected_provider_calls in (
            ("after_fence_before_dispatch", 0),
            ("during_dispatch", 1),
            ("after_dispatch_before_outcome", 1),
            ("after_outcome_before_state", 1),
            ("after_state_before_outbox", 1),
            ("after_outbox_before_commit", 1),
        ):
            with self.subTest(exact_post_fence_recovery=point):
                schedule = by_point[point]
                self.assertEqual(schedule["pre_dispatch_released"], 0)
                self.assertEqual(schedule["called_unknown"], 1)
                self.assertEqual(schedule["final_ledger_status"], "manual_review")
                self.assertEqual(schedule["final_fencing_token"], 1)
                self.assertEqual(schedule["final_claim_count"], 1)
                self.assertEqual(schedule["dispatch_slots_consumed"], 1)
                self.assertEqual(schedule["provider_calls"], expected_provider_calls)
                self.assertEqual(schedule["followup_worker_disposition"], "idle")
                if point in {"during_dispatch", "after_dispatch_before_outcome"}:
                    self.assertEqual(schedule["provider_calls_setup_baseline"], 0)
                    self.assertEqual(schedule["provider_calls_baseline"], 1)
                    self.assertEqual(schedule["provider_calls_during_recovery"], 0)
                if schedule["mechanism"] == "transaction_trigger":
                    self.assertEqual(schedule["provider_calls_baseline"], 1)
                    self.assertEqual(schedule["provider_calls_during_recovery"], 0)
        for point in ("during_delivery", "after_delivery_before_receipt"):
            with self.subTest(exact_delivery_recovery=point):
                schedule = by_point[point]
                self.assertEqual(schedule["provider_calls_baseline"], 1)
                self.assertEqual(schedule["provider_calls"], 1)
                self.assertEqual(schedule["provider_calls_during_recovery"], 0)
                self.assertEqual(schedule["delivery_calls"], 2)
                self.assertEqual(schedule["recovered_outbox_status"], "delivered")
                self.assertEqual(schedule["recovered_outbox_fencing_token"], 2)
                self.assertEqual(schedule["recovered_outbox_attempts"], 2)
                self.assertTrue(schedule["receipt_persisted"])

    def test_restart_oracle_rejects_permissive_false_greens(self) -> None:
        base = {
            "mechanism": "process_crash",
            "command_count": 1,
            "dispatch_slots_consumed": 1,
            "provider_calls": 0,
            "delivery_calls": 0,
            "partial_transactions": 0,
            "called_unknown_redispatches": 0,
            "child_exit_code": 91,
            "pre_dispatch_released": 0,
            "called_unknown": 0,
            "final_ledger_status": "dispatch_fenced",
            "final_fencing_token": 1,
            "final_claim_count": 1,
            "worker_disposition": None,
            "followup_worker_disposition": None,
            "provider_calls_setup_baseline": 0,
            "provider_calls_baseline": 0,
            "provider_calls_during_recovery": 0,
            "recovered_outbox_status": None,
            "recovered_outbox_fencing_token": None,
            "recovered_outbox_attempts": None,
            "receipt_persisted": False,
        }
        for point in (
            "after_claim_before_prepare",
            "after_fence_before_dispatch",
            "during_delivery",
        ):
            schedule = {**base, "fault_point": point}
            with self.subTest(fault_point=point):
                self.assertTrue(_schedule_violations(schedule))

        rolled_back_outcome = {
            **base,
            "fault_point": "after_outcome_before_state",
            "mechanism": "transaction_trigger",
            "child_exit_code": None,
            "provider_calls": 1,
            "provider_calls_baseline": 1,
            "provider_calls_during_recovery": 0,
        }
        self.assertTrue(_schedule_violations(rolled_back_outcome))

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
