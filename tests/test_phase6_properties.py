from __future__ import annotations

import ast
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import MappingProxyType
import unittest

from reservation_followup.properties import (
    BUSINESS_UNIT_KEYS,
    FOLLOWUP_MODES,
    PAYMENT_METHOD_KEYS,
    POSITIVE_COUNTERS,
    SAFETY_COUNTERS,
    SERVICE_KEYS,
    FollowupPropertyAudit,
    FollowupPropertyReport,
    FollowupPropertyRow,
    run_followup_properties,
)
from scripts.run_phase6_properties import (
    _partition_ranges,
    run_sharded_followup_properties,
)

ROOT = Path(__file__).resolve().parents[1]
SEED = 2026071906


class Phase6PropertyTests(unittest.TestCase):
    def test_closed_catalog_and_bilateral_counter_contract_are_exact(self) -> None:
        self.assertEqual(
            POSITIVE_COUNTERS,
            (
                "handoff_cases",
                "payment_cases",
                "email_disabled_cases",
                "method_switches",
                "economic_version_changes",
                "pix_cases",
                "wise_cases",
                "stripe_cases",
                "evidence_conflicts",
                "pre_fence_recoveries",
                "post_fence_manual_reviews",
                "required_effect_deliveries",
                "optional_effect_failures",
            ),
        )
        self.assertEqual(
            SAFETY_COUNTERS,
            (
                "reservation_commands_after_anchor",
                "handoff_email_failures_do_not_block_required",
                "second_settlement_commands",
                "second_dispatch_slots",
                "proof_reuses",
                "outbox_settlement_retries",
                "unknown_automatic_retries",
                "partial_transactions",
                "wrong_target_settlements",
            ),
        )
        self.assertEqual(SERVICE_KEYS, ("lodging", "activity"))
        self.assertEqual(BUSINESS_UNIT_KEYS, ("hostel", "agency"))
        self.assertEqual(PAYMENT_METHOD_KEYS, ("pix", "wise", "stripe"))
        self.assertEqual(len(FOLLOWUP_MODES), 16)
        self.assertEqual(len(set(FOLLOWUP_MODES)), len(FOLLOWUP_MODES))
        self.assertEqual(sum(mode.startswith("handoff_") for mode in FOLLOWUP_MODES), 8)
        self.assertEqual(sum(mode.startswith("payment_") for mode in FOLLOWUP_MODES), 8)

    def test_smoke_is_nonvacuous_balanced_and_reconstructs_every_total_from_rows(self) -> None:
        report = run_followup_properties(cases=160, seed=SEED)
        self.assertIsInstance(report, FollowupPropertyReport)
        self.assertEqual(report.cases, 160)
        self.assertEqual(report.start, 0)
        self.assertEqual(len(report.rows), 160)
        self.assertEqual(tuple(row.index for row in report.rows), tuple(range(160)))
        self.assertEqual(report.handoff_cases, 80)
        self.assertEqual(report.payment_cases, 80)
        self.assertEqual(report.handoff_cases + report.payment_cases, report.cases)
        self.assertEqual(set(row.mode for row in report.rows), set(FOLLOWUP_MODES))
        for field_name in POSITIVE_COUNTERS:
            expected = sum(row.positive[field_name] for row in report.rows)
            self.assertEqual(getattr(report, field_name), expected, field_name)
            self.assertGreater(expected, 0, field_name)
        for field_name in SAFETY_COUNTERS:
            expected = sum(row.safety[field_name] for row in report.rows)
            self.assertEqual(getattr(report, field_name), expected, field_name)
            self.assertEqual(expected, 0, field_name)
        self.assertEqual(
            report.service_counts,
            MappingProxyType(
                {
                    key: sum(row.service == key for row in report.rows)
                    for key in SERVICE_KEYS
                }
            ),
        )
        self.assertEqual(
            report.business_unit_counts,
            MappingProxyType(
                {
                    key: sum(row.business_unit == key for row in report.rows)
                    for key in BUSINESS_UNIT_KEYS
                }
            ),
        )
        self.assertEqual(
            report.method_counts,
            MappingProxyType(
                {
                    key: sum(row.payment_method == key for row in report.rows)
                    for key in PAYMENT_METHOD_KEYS
                }
            ),
        )
        self.assertTrue(all(value > 0 for value in report.service_counts.values()))
        self.assertTrue(all(value > 0 for value in report.business_unit_counts.values()))
        self.assertTrue(all(value > 0 for value in report.method_counts.values()))
        payment_rows = tuple(row for row in report.rows if row.case_kind == "payment")
        self.assertTrue(all(row.reservation_path_confirmed for row in payment_rows))
        self.assertTrue(all(row.reservation_command_id for row in payment_rows))
        self.assertTrue(all(row.reservation_outcome_hash for row in payment_rows))
        self.assertEqual(report.violations, ())
        self.assertTrue(report.passed)

    def test_passed_is_derived_and_hollow_or_divergent_rows_fail_closed(self) -> None:
        report = run_followup_properties(cases=16, seed=SEED)
        self.assertTrue(report.passed)
        method_switch_index = next(
            index
            for index, row in enumerate(report.rows)
            if row.mode == "payment_wise_method_switch"
        )
        method_switch_row = report.rows[method_switch_index]
        hollow_positive = dict(method_switch_row.positive)
        hollow_positive["method_switches"] = 0
        hollow = replace(
            method_switch_row,
            positive=MappingProxyType(hollow_positive),
        )
        hollow_rows = list(report.rows)
        hollow_rows[method_switch_index] = hollow
        hollow_report = FollowupPropertyReport(
            start=report.start,
            cases=report.cases,
            seed=report.seed,
            rows=tuple(hollow_rows),
            audits=report.audits,
            violations=report.violations,
        )
        self.assertFalse(hollow_report.passed)
        first = report.rows[0]
        unsafe = replace(
            first,
            safety=MappingProxyType(
                {
                    **dict(first.safety),
                    "reservation_commands_after_anchor": 1,
                }
            ),
        )
        unsafe_report = FollowupPropertyReport(
            start=report.start,
            cases=report.cases,
            seed=report.seed,
            rows=(unsafe, *report.rows[1:]),
            audits=report.audits,
            violations=report.violations,
        )
        self.assertFalse(unsafe_report.passed)
        with self.assertRaises(ValueError):
            FollowupPropertyReport(
                start=report.start,
                cases=report.cases,
                seed=report.seed,
                rows=report.rows[:-1],
                audits=report.audits,
                violations=(),
            )

    def test_sharding_is_nonoverlapping_and_case_rows_match_direct_global_indexes(self) -> None:
        ranges = _partition_ranges(cases=20_000, shard_cases=250)
        self.assertEqual(ranges[0], (0, 250))
        self.assertEqual(ranges[-1], (19_750, 250))
        self.assertEqual(sum(count for _, count in ranges), 20_000)
        self.assertEqual(tuple(start for start, _ in ranges), tuple(range(0, 20_000, 250)))
        direct = run_followup_properties(cases=16, seed=SEED)
        sharded = run_sharded_followup_properties(
            cases=16,
            seed=SEED,
            max_workers=2,
            shard_cases=8,
        )
        self.assertEqual(
            tuple(row.to_dict() for row in sharded.rows),
            tuple(row.to_dict() for row in direct.rows),
        )
        self.assertEqual(sharded.to_dict()["counters"], direct.to_dict()["counters"])
        self.assertEqual(sharded.violations, direct.violations)
        self.assertTrue(sharded.passed)
        self.assertEqual(tuple((a.start, a.cases) for a in sharded.audits), ((0, 8), (8, 8)))
        self.assertTrue(all(a.quick_check == "ok" for a in sharded.audits))
        self.assertTrue(all(a.foreign_key_violations == 0 for a in sharded.audits))

    def test_runner_is_deterministic_and_independent_of_pythonhashseed(self) -> None:
        left = run_followup_properties(cases=16, seed=17)
        right = run_followup_properties(cases=16, seed=17)
        self.assertEqual(left.to_dict(), right.to_dict())
        outputs = []
        for hash_seed in ("1", "777"):
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_phase6_properties.py",
                    "--cases",
                    "16",
                    "--seed",
                    "17",
                    "--smoke",
                ],
                cwd=ROOT,
                env={**os.environ, "PYTHONHASHSEED": hash_seed},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            outputs.append(json.loads(completed.stdout))
        self.assertEqual(outputs[0], outputs[1])

    def test_cli_rejects_trivial_gate_and_writes_reconstructible_smoke(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-properties-test-") as directory:
            output = Path(directory) / "result.json"
            trivial = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_phase6_properties.py",
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
                    "scripts/run_phase6_properties.py",
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
            self.assertEqual(payload["phase"], "phase-06-handoff-and-payments")
            self.assertEqual(payload["mode"], "smoke")
            self.assertEqual(
                payload["configuration"],
                {"cases": 16, "minimum_gate_cases": 20000, "seed": SEED},
            )
            self.assertEqual(payload["result"], "passed")
            self.assertEqual(len(payload["report"]["rows"]), 16)
            self.assertTrue(payload["report"]["passed"])
            self.assertEqual(json.loads(smoke.stdout), payload)

    def test_package_is_capability_free_and_cli_owns_process_sharding(self) -> None:
        properties_path = ROOT / "reservation_followup" / "properties.py"
        package_tree = ast.parse(properties_path.read_text(encoding="utf-8"))
        forbidden = {
            "concurrent",
            "multiprocessing",
            "subprocess",
            "socket",
            "requests",
            "httpx",
            "urllib",
        }
        imports = set()
        for node in ast.walk(package_tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertFalse(imports & forbidden)
        cli_tree = ast.parse(
            (ROOT / "scripts" / "run_phase6_properties.py").read_text(encoding="utf-8")
        )
        self.assertTrue(
            any(
                isinstance(node, ast.ImportFrom)
                and node.module == "concurrent.futures"
                for node in ast.walk(cli_tree)
            )
        )

    def test_invalid_exact_inputs_and_row_protocol_fail_closed(self) -> None:
        for cases, seed in ((0, 1), (-1, 1), (1, True), (True, 1)):
            with self.subTest(cases=cases, seed=seed), self.assertRaises(
                (TypeError, ValueError)
            ):
                run_followup_properties(cases=cases, seed=seed)
        report = run_followup_properties(cases=16, seed=SEED)
        first = report.rows[0]
        first_values = {
            field: getattr(first, field) for field in first.__dataclass_fields__
        }
        for changes in (
            {"positive": {}},
            {"safety": {}},
            {"mode": "unknown"},
            {"index": True},
            {"reservation_path_confirmed": 1},
        ):
            values = {**first_values, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                FollowupPropertyRow(**values)
        audit = report.audits[0]
        with self.assertRaises(ValueError):
            FollowupPropertyAudit(
                start=audit.start,
                cases=audit.cases,
                quick_check="not-ok",
                foreign_key_violations=0,
                deep_audits=audit.deep_audits,
            )


if __name__ == "__main__":
    unittest.main()
