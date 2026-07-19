from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.generate_phase5_manifest import (
    build_package_manifest,
    build_schema_manifest,
    checksum_paths,
    render_sums,
)
from scripts.run_phase5_faults import FAULT_POINTS
from scripts.run_phase5_mutations import MUTANTS
from scripts.validate_phase5 import (
    POSITIVE_PROPERTY_COUNTERS,
    SAFETY_PROPERTY_COUNTERS,
    check_fault_payloads,
    check_live_execution_claims,
    check_mutation_payload,
    check_package_purity,
    check_property_payload,
)

ROOT = Path(__file__).resolve().parents[1]
PHASE = "phase-05-durable-command-execution"
SEED = 2_026_071_905


def property_payload() -> dict[str, object]:
    cases = 20_000
    report: dict[str, object] = {
        "cases": cases,
        "seed": SEED,
        "cloudbeds_cases": cases // 2,
        "bokun_cases": cases // 2,
        "outcome_counts": {
            "called_no_effect": 2_500,
            "called_unknown": 5_000,
            "effect_confirmed": 10_000,
            "not_called": 2_500,
        },
        "violations": [],
        "passed": True,
    }
    report.update({name: 1 for name in POSITIVE_PROPERTY_COUNTERS})
    report.update(
        {
            "authorized_commands": cases,
            "terminal_commands": cases,
            "summary_outboxes": cases,
            "final_outboxes": cases,
        }
    )
    report.update({name: 0 for name in SAFETY_PROPERTY_COUNTERS})
    return {
        "schema_version": 1,
        "phase": PHASE,
        "mode": "gate",
        "configuration": {
            "cases": cases,
            "minimum_gate_cases": cases,
            "seed": SEED,
        },
        "result": "passed",
        "report": report,
    }


def fault_payloads() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    fault = {
        "schema_version": 1,
        "phase": PHASE,
        "kind": "fault-matrix",
        "configuration": {"seed": SEED, "fault_point_count": len(FAULT_POINTS)},
        "fault_points": list(FAULT_POINTS),
        "result": "passed",
        "violations": 0,
        "schedules": [{"violations": []} for _ in FAULT_POINTS],
    }
    restart = {
        "schema_version": 1,
        "phase": PHASE,
        "kind": "restart-schedules",
        "configuration": {"seed": SEED, "schedules": 2_000},
        "result": "passed",
        "violations": 0,
        "schedules": [{"violations": []} for _ in range(2_000)],
    }
    concurrency = {
        "schema_version": 1,
        "phase": PHASE,
        "kind": "multiprocess-contention",
        "configuration": {"seed": SEED, "rounds": 50},
        "result": "passed",
        "violations": 0,
        "command_rounds": 50,
        "outbox_rounds": 50,
        "command_claim_winners": 50,
        "outbox_claim_winners": 50,
        "partial_transactions": 0,
        "round_results": [
            {
                "kind": kind,
                "winners": 1,
                "winning_tokens": [1],
                "provider_calls": 1 if kind == "command" else 0,
                "partial_transactions": 0,
                "child_errors": 0,
                "nonzero_child_exits": 0,
                "violations": [],
            }
            for _ in range(50)
            for kind in ("command", "outbox")
        ],
    }
    return fault, restart, concurrency


def mutation_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "phase": PHASE,
        "scope": "temporary repository copies only; working tree unchanged",
        "mutant_count": len(MUTANTS),
        "catalog_count": len(MUTANTS),
        "all_killed": True,
        "mutants": [
            {
                "name": mutant.name,
                "path": mutant.path,
                "test": mutant.test,
                "target_count": 1,
                "baseline_exit_code": 0,
                "exit_code": 1,
                "loader_error": False,
                "killed": True,
            }
            for mutant in MUTANTS
        ],
    }


class Phase5CloseoutContractTests(unittest.TestCase):
    def test_schema_and_package_manifests_are_exact_and_deterministic(self) -> None:
        schema = build_schema_manifest()
        self.assertEqual(schema["phase"], PHASE)
        self.assertFalse(schema["postgresql_executed"])
        self.assertEqual(
            tuple(item["dialect"] for item in schema["files"]),
            ("postgresql", "sqlite"),
        )
        for item in schema["files"]:
            path = ROOT / item["path"]
            self.assertEqual(item["bytes"], path.stat().st_size)
            self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

        package = build_package_manifest()
        expected = tuple(
            str(path.relative_to(ROOT))
            for path in sorted((ROOT / "reservation_execution").glob("*.py"))
        )
        self.assertEqual(package["python_file_count"], len(expected))
        self.assertEqual(tuple(item["path"] for item in package["files"]), expected)
        self.assertEqual(build_schema_manifest(), schema)
        self.assertEqual(build_package_manifest(), package)

    def test_checksum_manifest_is_closed_and_excludes_runtime_artifacts(self) -> None:
        paths = checksum_paths()
        relatives = tuple(str(path.relative_to(ROOT)) for path in paths)
        self.assertEqual(len(relatives), len(set(relatives)))
        self.assertIn("scripts/validate_phase5.py", relatives)
        self.assertIn(".github/workflows/phase5.yml", relatives)
        self.assertIn("schemas/phase5/postgresql.sql", relatives)
        self.assertIn("tests/test_phase5_closeout.py", relatives)
        self.assertNotIn("docs/refactor/evidence/phase-05/SHA256SUMS", relatives)
        for relative in relatives:
            lower = relative.lower()
            self.assertFalse(lower.endswith((".db", ".sqlite", ".sqlite3", "-wal", "-shm", ".log")))
        rendered = render_sums()
        self.assertEqual(render_sums(), rendered)
        self.assertEqual(len(rendered.splitlines()), len(paths))

    def test_property_validator_closes_workload_totals_and_safety(self) -> None:
        payload = property_payload()
        failures: list[str] = []
        check_property_payload(failures, payload)
        self.assertEqual(failures, [])

        payload["report"]["wrong_command_claims"] = 1
        failures = []
        check_property_payload(failures, payload)
        self.assertIn("property safety counter must be zero: wrong_command_claims", failures)

        payload = property_payload()
        payload["report"]["outcome_counts"]["effect_confirmed"] -= 1
        failures = []
        check_property_payload(failures, payload)
        self.assertIn("property outcome totals must equal cases", failures)

    def test_fault_validator_closes_manifest_restart_and_contention_oracles(self) -> None:
        fault, restart, concurrency = fault_payloads()
        failures: list[str] = []
        check_fault_payloads(failures, fault, restart, concurrency)
        self.assertEqual(failures, [])

        restart["configuration"]["schedules"] = 1_999
        concurrency["round_results"][0]["provider_calls"] = 2
        failures = []
        check_fault_payloads(failures, fault, restart, concurrency)
        self.assertIn("restart workload must contain exactly 2000 schedules", failures)
        self.assertIn("contention round oracle mismatch", failures)

    def test_mutation_validator_requires_exact_catalog_and_valid_kills(self) -> None:
        payload = mutation_payload()
        failures: list[str] = []
        check_mutation_payload(failures, payload)
        self.assertEqual(failures, [])

        payload["mutants"][0]["loader_error"] = True
        failures = []
        check_mutation_payload(failures, payload)
        self.assertIn("mutation evidence does not match the closed catalog", failures)

    def test_execution_package_has_no_live_capability_or_cross_worker_ownership(self) -> None:
        failures: list[str] = []
        summary = check_package_purity(failures, root=ROOT)
        self.assertEqual(failures, [])
        self.assertEqual(summary["python_files"], 10)
        self.assertEqual(summary["forbidden_imports"], [])
        self.assertEqual(summary["reconciler_capabilities"], [])
        self.assertEqual(summary["outbox_ledger_references"], [])

    def test_purity_validator_rejects_indirect_outbox_ledger_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-purity-probe-") as directory:
            root = Path(directory)
            package = root / "reservation_execution"
            package.mkdir()
            for index in range(9):
                (package / f"module_{index}.py").write_text("", encoding="utf-8")
            (package / "sqlite_store.py").write_text(
                "def complete_outbox():\n"
                "    sql = \"UPDATE execution_ledger SET status='queued'\"\n",
                encoding="utf-8",
            )
            failures: list[str] = []
            summary = check_package_purity(failures, root=root)
            self.assertIn("reservation_execution/sqlite_store.py:complete_outbox", summary["outbox_ledger_references"])
            self.assertTrue(any("outbox API writes commercial ledger" in item for item in failures))

    def test_live_execution_claim_scan_rejects_any_positive_claim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-claim-probe-") as directory:
            evidence = Path(directory)
            (evidence / "claim.json").write_text(
                json.dumps({"postgresql_executed": True}),
                encoding="utf-8",
            )
            failures: list[str] = []
            summary = check_live_execution_claims(failures, evidence=evidence)
            self.assertEqual(summary["positive_claims"], ["claim.json:postgresql_executed"])
            self.assertIn(
                "evidence overclaims live execution: claim.json:postgresql_executed",
                failures,
            )


if __name__ == "__main__":
    unittest.main()
