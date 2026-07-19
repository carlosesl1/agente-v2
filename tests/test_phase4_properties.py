from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from reservation_confirmation import Phase4PropertyReport, run_phase4_properties

ROOT = Path(__file__).resolve().parents[1]


class Phase4PropertyTests(unittest.TestCase):
    def test_properties_cover_authorization_and_fail_closed_directions(self) -> None:
        report = run_phase4_properties(cases=264, seed=20260719)
        self.assertIsInstance(report, Phase4PropertyReport)
        self.assertEqual(report.cases, 264)
        self.assertEqual(sum(report.locale_counts.values()), 264)
        self.assertEqual(set(report.locale_counts), {"pt_BR", "en"})
        self.assertTrue(all(value > 0 for value in report.locale_counts.values()))
        self.assertEqual(sum(report.decision_counts.values()), 264)
        self.assertEqual(
            set(report.decision_counts),
            {"accept", "reject", "adjust", "ambiguous"},
        )
        self.assertTrue(all(value > 0 for value in report.decision_counts.values()))
        self.assertEqual(report.cloudbeds_cases + report.bokun_cases, 264)
        self.assertEqual(report.pt_cases + report.en_cases, 264)
        required_positive = (
            "cloudbeds_cases",
            "bokun_cases",
            "explicit_cases",
            "colloquial_cases",
            "contextual_cases",
            "negative_cases",
            "ambiguous_cases",
            "adjust_cases",
            "deterministic_summaries",
            "private_field_safe_summaries",
            "posterior_accept_commands",
            "same_time_rejections",
            "stale_version_rejections",
            "context_free_rejections",
            "adjustment_disarms",
            "semantic_version_increments",
            "noop_adjustment_rejections",
            "duplicate_zero_additional",
            "classifier_error_rejections",
        )
        for field in required_positive:
            self.assertGreater(getattr(report, field), 0, field)
        self.assertEqual(report.false_commands, 0)
        self.assertEqual(report.missing_required_commands, 0)
        self.assertGreater(report.authorized_accepts, 0)
        self.assertEqual(report.commands_emitted, report.authorized_accepts)
        self.assertGreater(report.duplicate_probes, 0)
        self.assertGreater(report.adjustment_probes, 0)
        self.assertGreater(report.context_failure_probes, 0)
        self.assertGreater(report.artifact_tamper_probes, 0)
        self.assertGreater(report.classifier_failure_probes, 0)
        self.assertEqual(report.premature_commands, 0)
        self.assertEqual(report.second_commands, 0)
        self.assertEqual(report.duplicate_reemissions, 0)
        self.assertEqual(report.stale_confirmation_acceptances, 0)
        self.assertEqual(report.adjustment_disarm_failures, 0)
        self.assertEqual(report.context_failure_events, 0)
        self.assertEqual(report.unexpected_exceptions, 0)
        self.assertEqual(report.violations, ())
        self.assertTrue(report.passed)

    def test_property_runner_is_deterministic_for_seed(self) -> None:
        left = run_phase4_properties(cases=48, seed=17)
        right = run_phase4_properties(cases=48, seed=17)
        self.assertEqual(left.to_dict(), right.to_dict())

    def test_property_runner_rejects_invalid_inputs(self) -> None:
        for cases, seed in ((0, 1), (-1, 1), (1, True)):
            with self.subTest(cases=cases, seed=seed):
                with self.assertRaises(ValueError):
                    run_phase4_properties(cases=cases, seed=seed)

    def test_cli_rejects_trivial_gate_and_allows_explicit_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            trivial = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_phase4_properties.py",
                    "--cases",
                    "1",
                    "--seed",
                    "1",
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

            smoke = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_phase4_properties.py",
                    "--cases",
                    "24",
                    "--seed",
                    "1",
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
            self.assertEqual(payload["mode"], "smoke")
            self.assertEqual(payload["result"], "passed")
            self.assertEqual(payload["configuration"]["minimum_gate_cases"], 50_000)
            self.assertEqual(payload["report"]["cases"], 24)


if __name__ == "__main__":
    unittest.main()
