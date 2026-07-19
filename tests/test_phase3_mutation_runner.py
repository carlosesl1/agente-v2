from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import unittest

from scripts.run_phase3_mutations import MUTANTS, run_mutants

ROOT = Path(__file__).resolve().parents[1]


def tracked_runtime_digest() -> str:
    digest = hashlib.sha256()
    for directory in ("reservation_domain", "reservation_lookup", "scripts", "tests"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


class MutationRunnerTests(unittest.TestCase):
    def test_mutant_catalog_is_closed_unique_and_complete(self) -> None:
        names = tuple(mutant.name for mutant in MUTANTS)
        self.assertEqual(len(names), 13)
        self.assertEqual(len(set(names)), 13)
        self.assertEqual(
            names[-2:],
            (
                "remove_response_deep_freeze",
                "remove_provider_ref_namespace_binding",
            ),
        )

    def test_runner_kills_mutant_in_temp_copy_without_touching_repository(self) -> None:
        before = tracked_runtime_digest()
        report = run_mutants(
            root=ROOT,
            selected_names=("remove_response_deep_freeze",),
        )
        self.assertTrue(report["all_killed"])
        self.assertEqual(report["mutant_count"], 1)
        self.assertEqual(report["mutants"][0]["exit_code"], 1)
        self.assertEqual(tracked_runtime_digest(), before)

    def test_cli_rejects_unknown_mutant(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/run_phase3_mutations.py",
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
