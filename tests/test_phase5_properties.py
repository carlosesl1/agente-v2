from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from reservation_execution import Phase5PropertyReport, run_phase5_properties

ROOT = Path(__file__).resolve().parents[1]
SEED = 2026071905


class Phase5PropertyTests(unittest.TestCase):
    def test_smoke_covers_both_providers_and_all_outcomes(self) -> None:
        report = run_phase5_properties(cases=160, seed=SEED)
        self.assertIsInstance(report, Phase5PropertyReport)
        self.assertEqual(report.cloudbeds_cases + report.bokun_cases, 160)
        self.assertGreater(report.cloudbeds_cases, 0)
        self.assertGreater(report.bokun_cases, 0)
        self.assertEqual(
            set(report.outcome_counts),
            {
                "not_called",
                "called_no_effect",
                "effect_confirmed",
                "called_unknown",
            },
        )
        self.assertEqual(sum(report.outcome_counts.values()), 160)
        self.assertTrue(all(value > 0 for value in report.outcome_counts.values()))
        for field in (
            "authorized_commands",
            "terminal_commands",
            "summary_outboxes",
            "final_outboxes",
            "expired_lease_recoveries",
            "stale_token_rejections",
            "post_fence_unknowns",
            "manual_reviews",
            "delivery_retries",
            "duplicate_probes",
            "conflict_probes",
        ):
            self.assertGreater(getattr(report, field), 0, field)
        for field in (
            "unauthorized_commands",
            "second_commands",
            "second_dispatch_slots",
            "second_provider_calls",
            "unknown_redispatches",
            "outbox_provider_retries",
            "partial_transactions",
            "stale_token_writes",
            "missing_terminals",
            "unexpected_exceptions",
        ):
            self.assertEqual(getattr(report, field), 0, field)
        self.assertEqual(report.violations, ())
        self.assertTrue(report.passed)

    def test_property_runner_is_deterministic_and_starts_cross_phase(self) -> None:
        left = run_phase5_properties(cases=16, seed=17)
        right = run_phase5_properties(cases=16, seed=17)
        self.assertEqual(left.to_dict(), right.to_dict())
        self.assertEqual(left.authorized_commands, 16)
        self.assertEqual(left.terminal_commands, 16)
        self.assertEqual(left.summary_outboxes, 16)
        self.assertEqual(left.final_outboxes, 16)

    def test_property_runner_rejects_invalid_inputs(self) -> None:
        for cases, seed in ((0, 1), (-1, 1), (1, True), (True, 1)):
            with self.subTest(cases=cases, seed=seed):
                with self.assertRaises((TypeError, ValueError)):
                    run_phase5_properties(cases=cases, seed=seed)

    def test_cli_rejects_trivial_gate_and_writes_deterministic_smoke(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-properties-") as directory:
            output = Path(directory) / "result.json"
            trivial = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_phase5_properties.py",
                    "--cases",
                    "19999",
                    "--seed",
                    str(SEED),
                    "--write",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(trivial.returncode, 0)
            self.assertFalse(output.exists())
            self.assertIn("20000", trivial.stderr)

            smoke = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_phase5_properties.py",
                    "--cases",
                    "16",
                    "--seed",
                    str(SEED),
                    "--smoke",
                    "--write",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(smoke.returncode, 0, smoke.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["phase"], "phase-05-durable-command-execution")
            self.assertEqual(payload["mode"], "smoke")
            self.assertEqual(
                payload["configuration"],
                {"cases": 16, "minimum_gate_cases": 20000, "seed": SEED},
            )
            self.assertEqual(payload["result"], "passed")
            self.assertTrue(payload["report"]["passed"])
            self.assertEqual(json.loads(smoke.stdout), payload)


if __name__ == "__main__":
    unittest.main()
