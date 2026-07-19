from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import unittest

from scripts.run_phase4_mutations import MUTANTS, run_mutants

ROOT = Path(__file__).resolve().parents[1]


def tracked_runtime_digest() -> str:
    digest = hashlib.sha256()
    for directory in (
        "reservation_domain",
        "reservation_confirmation",
        "scripts",
        "tests",
    ):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


class Phase4MutationRunnerTests(unittest.TestCase):
    def test_catalog_is_closed_unique_and_covers_critical_boundaries(self) -> None:
        names = tuple(mutant.name for mutant in MUTANTS)
        self.assertGreaterEqual(len(names), 14)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(
            {mutant.path for mutant in MUTANTS},
            {
                "reservation_confirmation/binding.py",
                "reservation_confirmation/classifier.py",
                "reservation_confirmation/presentation.py",
                "reservation_confirmation/renderer.py",
                "reservation_domain/reducer.py",
            },
        )
        for required in (
            "allow_same_timestamp_confirmation",
            "trust_wrong_content_hash",
            "trust_tampered_summary_artifact",
            "keep_summary_armed_after_adjustment",
            "allow_noop_adjustment_version",
            "accept_stale_draft_version",
            "emit_event_after_classifier_failure",
            "remove_private_identifier_guard",
        ):
            self.assertIn(required, names)

    def test_runner_kills_mutant_in_temp_copy_without_touching_repository(self) -> None:
        before = tracked_runtime_digest()
        report = run_mutants(
            root=ROOT,
            selected_names=("allow_same_timestamp_confirmation",),
        )
        self.assertTrue(report["all_killed"])
        self.assertEqual(report["mutant_count"], 1)
        self.assertGreater(report["mutants"][0]["exit_code"], 0)
        self.assertEqual(tracked_runtime_digest(), before)

    def test_cli_rejects_unknown_mutant(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/run_phase4_mutations.py",
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
