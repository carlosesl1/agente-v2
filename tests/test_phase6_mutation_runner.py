from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_phase6_mutations import (
    MUTANTS,
    MUTANT_CLASSES,
    Mutant,
    _ClassifiedRun,
    _ProcessResult,
    _classify_test_run,
    _replace_target,
    _run_one,
    _run_test,
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
                    "error_types": [],
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
                        "error_types": ["unittest.loader_error"],
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
                        "error_types": [],
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

    def test_runtime_import_error_and_duplicate_json_keys_never_kill(self) -> None:
        prefix = "__PHASE6_TEST_RESULT__"
        runtime_import = _ProcessResult(
            1,
            prefix
            + json.dumps(
                {
                    "loader_error": False,
                    "tests_run": 1,
                    "failures": 0,
                    "errors": 1,
                    "error_types": ["builtins.ImportError"],
                    "successful": False,
                }
            ),
            False,
        )
        classified = _classify_test_run(runtime_import)
        self.assertEqual(classified.verdict, "infrastructure_error")
        self.assertFalse(classified.killed)
        self.assertIn("ImportError", classified.error or "")

        duplicate_key = _ProcessResult(
            1,
            prefix
            + '{"loader_error":false,"tests_run":1,"failures":1,'
            '"errors":0,"error_types":[],"successful":true,"successful":false}',
            False,
        )
        classified = _classify_test_run(duplicate_key)
        self.assertEqual(classified.verdict, "invalid_protocol")
        self.assertFalse(classified.killed)
        self.assertIn("duplicate", classified.error or "")

    def test_real_runtime_import_is_non_killing_but_subtest_behavior_error_kills(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-mutation-protocol-") as directory:
            root = Path(directory)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_protocol_probe.py").write_text(
                "import unittest\n"
                "class ProtocolProbe(unittest.TestCase):\n"
                "    def test_runtime_import(self):\n"
                "        raise ImportError('synthetic runtime import')\n"
                "    def test_subtest_behavior_error(self):\n"
                "        with self.subTest(case='material'):\n"
                "            raise TypeError('synthetic material error')\n",
                encoding="utf-8",
            )
            _, runtime_import = _run_test(
                root=root,
                test=(
                    "tests.test_protocol_probe.ProtocolProbe."
                    "test_runtime_import"
                ),
            )
            _, behavior_error = _run_test(
                root=root,
                test=(
                    "tests.test_protocol_probe.ProtocolProbe."
                    "test_subtest_behavior_error"
                ),
            )
        self.assertEqual(runtime_import.verdict, "infrastructure_error")
        self.assertFalse(runtime_import.killed)
        self.assertEqual(behavior_error.verdict, "test_failure")
        self.assertTrue(behavior_error.killed)

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

    def test_shared_copy_is_restored_after_timeout_before_next_mutant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-mutation-restore-") as directory:
            copy_root = Path(directory)
            target = copy_root / "target.py"
            target.write_text("alpha\nbeta\n", encoding="utf-8")
            baseline = _ClassifiedRun(
                "baseline_green", False, False, 1, 0, 0, None
            )
            first = Mutant(
                "handoff_policy",
                "timeout-mutant",
                "target.py",
                "beta",
                "gamma",
                "tests.synthetic.test_timeout",
            )
            second = Mutant(
                "handoff_policy",
                "next-mutant",
                "target.py",
                "beta",
                "delta",
                "tests.synthetic.test_next",
            )
            timeout = (
                _ProcessResult(-2, "", True),
                _ClassifiedRun("timeout", False, False, 0, 0, 0, "test timed out"),
            )
            killed = (
                _ProcessResult(1, "", False),
                _ClassifiedRun("test_failure", True, False, 1, 1, 0, None),
            )
            with patch(
                "scripts.run_phase6_mutations._run_test",
                side_effect=(timeout, killed),
            ):
                first_result = _run_one(
                    copy_root=copy_root,
                    mutant=first,
                    baseline=baseline,
                )
                self.assertFalse(first_result["killed"])
                self.assertEqual(target.read_bytes(), b"alpha\nbeta\n")
                second_result = _run_one(
                    copy_root=copy_root,
                    mutant=second,
                    baseline=baseline,
                )
            self.assertEqual(second_result["target_count"], 1)
            self.assertTrue(second_result["killed"])
            self.assertEqual(target.read_bytes(), b"alpha\nbeta\n")

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
