from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.run_phase6_mutations import (
    MUTANTS,
    MUTANT_CLASSES,
    Mutant,
    _ProcessResult,
    _classify_test_run,
    _replace_target,
    run_mutants,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CLASSES = (
    "handoff_policy",
    "handoff_precedence",
    "payment_bootstrap",
    "method_separation",
    "global_claim",
    "amount_receiver_validation",
    "dispatch_slot",
    "post_fence_retry",
    "outbox_isolation",
    "paid_monotonicity",
    "config_closure",
    "divergent_replay",
)


def scoped_digest() -> str:
    digest = hashlib.sha256()
    for directory in ("reservation_followup", "scripts", "tests", "schemas"):
        for path in sorted((ROOT / directory).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


class Phase6MutationRunnerTests(unittest.TestCase):
    def test_mutant_contract_rejects_empty_equal_absolute_and_empty_sql_replacement(self) -> None:
        valid = {
            "mutation_class": "handoff_policy",
            "name": "synthetic",
            "path": "reservation_followup/handoff.py",
            "old": "old material",
            "new": "new material",
            "test": "tests.test_phase6_handoff.Phase6HandoffReducerTests.test_email_disabled_still_opens_queue_and_customer_ack",
        }
        for field in valid:
            with self.subTest(empty=field), self.assertRaises(ValueError):
                Mutant(**{**valid, field: ""})
        with self.assertRaises(ValueError):
            Mutant(**{**valid, "new": valid["old"]})
        with self.assertRaises(ValueError):
            Mutant(**{**valid, "path": "/tmp/escape.py"})
        with self.assertRaises(ValueError):
            Mutant(**{**valid, "path": "schemas/phase6/sqlite.sql", "new": ""})

    def test_structured_protocol_rejects_every_false_kill(self) -> None:
        prefix = "__PHASE6_TEST_RESULT__"
        killed = _ProcessResult(
            exit_code=1,
            stdout=prefix
            + json.dumps(
                {
                    "loader_error": False,
                    "tests_run": 1,
                    "failures": 1,
                    "errors": 0,
                    "successful": False,
                }
            ),
            timed_out=False,
        )
        self.assertEqual(_classify_test_run(killed).verdict, "test_failure")
        cases = {
            "baseline_failure": _ProcessResult(1, killed.stdout, False),
            "loader_error": _ProcessResult(
                1,
                prefix
                + json.dumps(
                    {
                        "loader_error": True,
                        "tests_run": 0,
                        "failures": 0,
                        "errors": 1,
                        "successful": False,
                    }
                ),
                False,
            ),
            "timeout": _ProcessResult(-2, "", True),
            "missing_protocol": _ProcessResult(1, "ordinary failure", False),
            "duplicate_protocol": _ProcessResult(1, killed.stdout + "\n" + killed.stdout, False),
            "invalid_json": _ProcessResult(1, prefix + "{", False),
            "zero_tests": _ProcessResult(
                1,
                prefix
                + json.dumps(
                    {
                        "loader_error": False,
                        "tests_run": 0,
                        "failures": 1,
                        "errors": 0,
                        "successful": False,
                    }
                ),
                False,
            ),
        }
        for name, result in cases.items():
            with self.subTest(name=name):
                classified = _classify_test_run(result, baseline=name == "baseline_failure")
                self.assertNotEqual(classified.verdict, "test_failure")
                self.assertFalse(classified.killed)
                self.assertIsNotNone(classified.error)

    def test_replace_target_requires_exactly_one_material_occurrence_and_restores(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-mutation-contract-") as directory:
            path = Path(directory) / "target.py"
            path.write_text("alpha\nbeta\n", encoding="utf-8")
            mutant = Mutant(
                mutation_class="handoff_policy",
                name="synthetic",
                path="target.py",
                old="beta",
                new="gamma",
                test="tests.test_phase6_handoff.Phase6HandoffReducerTests.test_email_disabled_still_opens_queue_and_customer_ack",
            )
            original = _replace_target(root=Path(directory), mutant=mutant)
            self.assertEqual(original, b"alpha\nbeta\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "alpha\ngamma\n")
            path.write_bytes(original)
            self.assertEqual(path.read_bytes(), original)
            for old in ("missing", "alpha"):
                if old == "alpha":
                    path.write_text("alpha\nalpha\n", encoding="utf-8")
                invalid = Mutant(
                    mutation_class="handoff_policy",
                    name=f"synthetic-{old}",
                    path="target.py",
                    old=old,
                    new="delta",
                    test=mutant.test,
                )
                with self.subTest(old=old), self.assertRaises(ValueError):
                    _replace_target(root=Path(directory), mutant=invalid)

    def test_catalog_is_closed_material_and_targets_once(self) -> None:
        self.assertEqual(MUTANT_CLASSES, EXPECTED_CLASSES)
        self.assertEqual(tuple(mutant.mutation_class for mutant in MUTANTS), EXPECTED_CLASSES)
        self.assertEqual(len(MUTANTS), 12)
        self.assertEqual(len({mutant.name for mutant in MUTANTS}), 12)
        for mutant in MUTANTS:
            with self.subTest(mutant=mutant.name):
                source = (ROOT / mutant.path).read_text(encoding="utf-8")
                self.assertEqual(source.count(mutant.old), 1)
                self.assertNotEqual(mutant.old, mutant.new)
                self.assertTrue(mutant.test.startswith("tests.test_phase6_"))

    def test_selected_material_mutant_is_killed_without_touching_worktree(self) -> None:
        before = scoped_digest()
        report = run_mutants(
            root=ROOT,
            selected_names=("allow_second_dispatch_slot",),
        )
        self.assertTrue(report["all_killed"])
        self.assertEqual(report["mutant_count"], 1)
        self.assertEqual(report["baseline_runs"], 1)
        self.assertEqual(report["mutants"][0]["target_count"], 1)
        self.assertEqual(report["mutants"][0]["baseline_exit_code"], 0)
        self.assertGreater(report["mutants"][0]["exit_code"], 0)
        self.assertFalse(report["mutants"][0]["loader_error"])
        self.assertIsNone(report["mutants"][0]["error"])
        self.assertEqual(scoped_digest(), before)

    def test_baseline_cache_runs_duplicate_test_once(self) -> None:
        test_name = MUTANTS[0].test
        same_test = tuple(mutant.name for mutant in MUTANTS if mutant.test == test_name)
        if len(same_test) < 2:
            same_test = (MUTANTS[0].name, MUTANTS[0].name)
        report = run_mutants(root=ROOT, selected_names=same_test)
        self.assertEqual(report["baseline_runs"], 1)

    def test_cli_is_hashseed_independent_for_one_material_mutant(self) -> None:
        import subprocess
        import sys

        before = scoped_digest()
        outputs = []
        for seed in ("1", "777"):
            environment = dict(os.environ)
            environment["PYTHONHASHSEED"] = seed
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "scripts/run_phase6_mutations.py",
                    "--only",
                    "allow_second_dispatch_slot",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            outputs.append(json.loads(completed.stdout))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(scoped_digest(), before)


if __name__ == "__main__":
    unittest.main()
