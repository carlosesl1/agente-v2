from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from reservation_lookup.properties import run_lookup_properties

ROOT = Path(__file__).resolve().parents[1]


class Phase3PropertyTests(unittest.TestCase):
    def test_property_runner_exercises_both_authorization_directions(self) -> None:
        report = run_lookup_properties(cases=250, seed=20260718)
        self.assertEqual(report.cases, 250)
        self.assertEqual(report.positive_authorizations, 250)
        self.assertEqual(report.label_equivalence_cases, 250)
        self.assertEqual(report.executable_mutation_cases, 250)
        self.assertEqual(report.expired_cases, 250)
        self.assertEqual(report.zero_match_cases, 250)
        self.assertEqual(report.multiple_match_cases, 250)
        self.assertEqual(sum(report.mutation_counts.values()), 250)
        self.assertTrue(all(value > 0 for value in report.mutation_counts.values()))
        self.assertEqual(report.false_authorizations, 0)
        self.assertEqual(report.missed_invalidations, 0)
        self.assertEqual(report.unexpected_exceptions, 0)
        self.assertEqual(report.violations, ())

    def test_property_runner_is_deterministic_for_seed(self) -> None:
        left = run_lookup_properties(cases=75, seed=17)
        right = run_lookup_properties(cases=75, seed=17)
        self.assertEqual(left.to_dict(), right.to_dict())

    def test_cli_rejects_trivial_gate_and_allows_explicit_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            trivial = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_phase3_properties.py",
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
                    "scripts/run_phase3_properties.py",
                    "--cases",
                    "10",
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
            self.assertEqual(payload["report"]["cases"], 10)


if __name__ == "__main__":
    unittest.main()
