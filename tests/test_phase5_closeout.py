from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import scripts.validate_phase5 as phase5_validator
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
    check_metrics,
    check_mutation_payload,
    check_package_purity,
    check_property_payload,
    check_red_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
PHASE = "phase-05-durable-command-execution"
SEED = 2_026_071_905


def property_payload() -> dict[str, object]:
    return json.loads(
        (ROOT / "docs/refactor/evidence/phase-05/property-result.json").read_text(
            encoding="utf-8"
        )
    )


def fault_payloads() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    base = ROOT / "docs/refactor/evidence/phase-05"
    return tuple(
        json.loads((base / name).read_text(encoding="utf-8"))
        for name in (
            "fault-matrix.json",
            "restart-result.json",
            "concurrency-result.json",
        )
    )


def mutation_payload() -> dict[str, object]:
    return json.loads(
        (ROOT / "docs/refactor/evidence/phase-05/mutation-result.json").read_text(
            encoding="utf-8"
        )
    )


class Phase5CloseoutContractTests(unittest.TestCase):
    def _evidence(self, name: str) -> dict[str, object]:
        return json.loads(
            (ROOT / "docs/refactor/evidence/phase-05" / name).read_text(
                encoding="utf-8"
            )
        )

    def test_property_validator_rejects_hollow_counters_and_non_integer_protocol(self) -> None:
        payload = self._evidence("property-result.json")
        for key in POSITIVE_PROPERTY_COUNTERS:
            if key not in {
                "authorized_commands",
                "terminal_commands",
                "summary_outboxes",
                "final_outboxes",
            }:
                payload["report"][key] = 1
        failures: list[str] = []
        check_property_payload(failures, payload)
        self.assertTrue(failures, "hollow property counters false-greened")

        for bad_version in (True, 1.0, "1", None):
            payload = self._evidence("property-result.json")
            payload["schema_version"] = bad_version
            failures = []
            check_property_payload(failures, payload)
            self.assertTrue(failures, f"invalid schema version accepted: {bad_version!r}")

    def test_fault_validator_rejects_hollow_schedules_duplicate_ids_and_wrong_kinds(self) -> None:
        actual = (
            self._evidence("fault-matrix.json"),
            self._evidence("restart-result.json"),
            self._evidence("concurrency-result.json"),
        )
        mutations = []

        fault, restart, concurrency = copy.deepcopy(actual)
        fault["schedules"] = [{"violations": []} for _ in range(17)]
        mutations.append(("hollow fault schedules", fault, restart, concurrency))

        fault, restart, concurrency = copy.deepcopy(actual)
        restart["schedules"] = [{"violations": []} for _ in range(2_000)]
        mutations.append(("hollow restart schedules", fault, restart, concurrency))

        fault, restart, concurrency = copy.deepcopy(actual)
        restart["schedules"][1] = copy.deepcopy(restart["schedules"][0])
        mutations.append(("duplicate restart identity", fault, restart, concurrency))

        fault, restart, concurrency = copy.deepcopy(actual)
        for row in concurrency["round_results"]:
            row["kind"] = "command"
            row["provider_calls"] = 1
            row.pop("provider_calls_baseline", None)
            row.pop("provider_calls_final", None)
        mutations.append(("all command contention rows", fault, restart, concurrency))

        for label, fault, restart, concurrency in mutations:
            with self.subTest(label=label):
                failures = []
                check_fault_payloads(failures, fault, restart, concurrency)
                self.assertTrue(failures, f"{label} false-greened")

    def test_fault_and_mutation_catalogs_are_independent_of_runner_constants(self) -> None:
        fault = self._evidence("fault-matrix.json")
        restart = self._evidence("restart-result.json")
        concurrency = self._evidence("concurrency-result.json")
        fault["fault_points"] = fault["fault_points"][:-1]
        fault["schedules"] = fault["schedules"][:-1]
        fault["configuration"]["fault_point_count"] = 16
        with patch.object(
            phase5_validator,
            "FAULT_POINTS",
            tuple(fault["fault_points"]),
            create=True,
        ):
            failures: list[str] = []
            check_fault_payloads(failures, fault, restart, concurrency)
        self.assertTrue(failures, "runner-reduced fault catalog false-greened")

        payload = self._evidence("mutation-result.json")
        payload["mutants"] = payload["mutants"][:-1]
        payload["catalog_count"] = 19
        payload["mutant_count"] = 19
        with patch.object(
            phase5_validator,
            "MUTANTS",
            MUTANTS[:-1],
            create=True,
        ):
            failures = []
            check_mutation_payload(failures, payload)
        self.assertTrue(failures, "runner-reduced mutation catalog false-greened")

    def test_manifests_and_purity_scan_recursive_code_and_all_mutation_targets(self) -> None:
        relatives = {
            str(path.relative_to(ROOT)) for path in checksum_paths()
        }
        self.assertTrue({mutant.path for mutant in MUTANTS}.issubset(relatives))

        with tempfile.TemporaryDirectory(prefix="phase5-recursive-package-") as directory:
            root = Path(directory)
            package = root / "reservation_execution"
            nested = package / "nested"
            nested.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (nested / "capability.py").write_text("import requests\n", encoding="utf-8")
            manifest = build_package_manifest(root=root)
            self.assertEqual(
                [item["path"] for item in manifest["files"]],
                [
                    "reservation_execution/__init__.py",
                    "reservation_execution/nested/capability.py",
                ],
            )
            failures: list[str] = []
            check_package_purity(failures, root=root)
            self.assertTrue(
                any("external capability imports" in item for item in failures),
                failures,
            )

    def test_purity_validator_follows_outbox_call_graph_to_neutral_helper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-outbox-callgraph-") as directory:
            root = Path(directory)
            package = root / "reservation_execution"
            package.mkdir()
            for index in range(9):
                (package / f"module_{index}.py").write_text("", encoding="utf-8")
            (package / "sqlite_store.py").write_text(
                "def complete_outbox():\n"
                "    _write_record()\n\n"
                "def _write_record():\n"
                "    sql = 'UPDATE ' + 'execution_ledger SET status=queued'\n",
                encoding="utf-8",
            )
            failures: list[str] = []
            summary = check_package_purity(failures, root=root)
            self.assertIn(
                "reservation_execution/sqlite_store.py:complete_outbox->_write_record",
                summary["outbox_ledger_references"],
            )

    def test_live_claim_scan_is_recursive_and_rejects_aliases_in_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-claim-alias-") as directory:
            evidence = Path(directory)
            nested = evidence / "nested"
            nested.mkdir()
            (nested / "claim.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "phase": PHASE,
                        "postgresql_live": True,
                    }
                ),
                encoding="utf-8",
            )
            (nested / "claim.md").write_text(
                "Docker executed: yes\nSupabase run: true\n",
                encoding="utf-8",
            )
            failures: list[str] = []
            summary = check_live_execution_claims(failures, evidence=evidence)
            self.assertGreaterEqual(len(summary["positive_claims"]), 3)
            self.assertTrue(failures)

    def test_workflow_validator_rejects_required_commands_hidden_in_comments(self) -> None:
        markers = (
            "name: phase-5-durable-execution",
            "timeout-minutes: 15",
            "permissions:",
            "contents: read",
            "--cases 20000",
            "--restart-schedules 2000",
            "--contention-rounds 50",
            "generate_phase5_manifest.py --check",
            "validate_phase5.py",
            "git diff --check",
        )
        with tempfile.TemporaryDirectory(prefix="phase5-comment-workflow-") as directory:
            root = Path(directory)
            workflow = root / ".github/workflows/phase5.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: inert\non: workflow_dispatch\njobs: {}\n"
                + "".join(f"# {marker}\n" for marker in markers),
                encoding="utf-8",
            )
            failures: list[str] = []
            with patch.object(phase5_validator, "ROOT", root):
                phase5_validator.check_workflow(failures)
            self.assertTrue(failures, "comment-only workflow false-greened")

    def test_metrics_require_exact_integer_schema_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-metrics-version-") as directory:
            evidence = Path(directory)
            for name in ("validation-result.json", "performance-result.json"):
                payload = self._evidence(name)
                payload.pop("schema_version", None)
                (evidence / name).write_text(json.dumps(payload), encoding="utf-8")
            failures: list[str] = []
            with patch.object(phase5_validator, "EVIDENCE", evidence):
                phase5_validator.check_metrics(failures)
            self.assertTrue(failures, "metrics without schema version false-greened")

    def test_metrics_reject_missing_extra_and_false_zero_live_fields(self) -> None:
        names = ("validation-result.json", "performance-result.json")
        originals = {name: self._evidence(name) for name in names}
        mutations = []
        for value in (False, 0.0, "0"):
            payloads = copy.deepcopy(originals)
            payloads["validation-result.json"]["network_calls"] = value
            mutations.append((f"network_calls={value!r}", payloads))
        payloads = copy.deepcopy(originals)
        payloads["validation-result.json"].pop("live_provider_calls")
        mutations.append(("missing live_provider_calls", payloads))
        payloads = copy.deepcopy(originals)
        payloads["performance-result.json"]["unexpected"] = 0
        mutations.append(("extra performance key", payloads))
        for value in (0, 0.0, "false"):
            payloads = copy.deepcopy(originals)
            payloads["performance-result.json"]["postgresql_executed"] = value
            mutations.append((f"postgresql_executed={value!r}", payloads))

        for label, payloads in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="phase5-metrics-schema-"
            ) as directory:
                evidence = Path(directory)
                for name, payload in payloads.items():
                    (evidence / name).write_text(json.dumps(payload), encoding="utf-8")
                failures: list[str] = []
                with patch.object(phase5_validator, "EVIDENCE", evidence):
                    check_metrics(failures)
                self.assertTrue(failures, f"{label} false-greened")

    def test_red_evidence_rejects_missing_extra_and_false_integer_fields(self) -> None:
        source = ROOT / "docs/refactor/evidence/phase-05"
        originals = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in source.glob("red-result-*.json")
        }
        mutations = []
        for value in (False, 0.0, "0"):
            payloads = copy.deepcopy(originals)
            payloads["red-result-closeout.json"]["network_calls"] = value
            mutations.append((f"top network_calls={value!r}", payloads))
        payloads = copy.deepcopy(originals)
        payloads["red-result-closeout.json"].pop("network_calls")
        mutations.append(("missing top network_calls", payloads))
        payloads = copy.deepcopy(originals)
        payloads["red-result-closeout.json"]["unexpected"] = 0
        mutations.append(("extra top key", payloads))
        payloads = copy.deepcopy(originals)
        payloads["red-result-closeout.json"]["controller_regressions"][4].pop(
            "tests_run"
        )
        mutations.append(("missing nested tests_run", payloads))
        payloads = copy.deepcopy(originals)
        payloads["red-result-closeout.json"]["controller_regressions"][4][
            "unexpected"
        ] = 0
        mutations.append(("extra nested key", payloads))
        payloads = copy.deepcopy(originals)
        payloads["red-result-closeout.json"]["controller_regressions"][4][
            "errors"
        ] = True
        mutations.append(("nested errors=true", payloads))

        for label, payloads in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="phase5-red-schema-"
            ) as directory:
                evidence = Path(directory)
                for name, payload in payloads.items():
                    (evidence / name).write_text(json.dumps(payload), encoding="utf-8")
                failures: list[str] = []
                with patch.object(phase5_validator, "EVIDENCE", evidence):
                    check_red_evidence(failures)
                self.assertTrue(failures, f"{label} false-greened")

    def test_phase5_workflow_splits_heavy_gates_into_independent_jobs(self) -> None:
        workflow = (ROOT / ".github/workflows/phase5.yml").read_text(encoding="utf-8")
        job_starts = tuple(re.finditer(r"(?m)^  ([a-z0-9-]+):\n", workflow))
        jobs = {
            match.group(1): workflow[
                match.start() : (
                    job_starts[index + 1].start()
                    if index + 1 < len(job_starts)
                    else len(workflow)
                )
            ]
            for index, match in enumerate(job_starts)
        }
        workloads = {
            "full-suite": "python3 -m unittest discover -s tests -v",
            "properties": "python3 scripts/run_phase5_properties.py --cases 20000",
            "fault-restart-contention": "python3 scripts/run_phase5_faults.py",
            "mutations": "python3 scripts/run_phase5_mutations.py",
        }
        required = {"static-validation", *workloads, "phase5-gate"}
        self.assertTrue(required.issubset(jobs), sorted(jobs))
        for job_name in required:
            self.assertIn("timeout-minutes: 15", jobs[job_name])
        for owner, command in workloads.items():
            self.assertIn(command, jobs[owner])
            self.assertEqual(workflow.count(command), 1)
            for other_name, other_job in jobs.items():
                if other_name != owner:
                    self.assertNotIn(command, other_job)
        self.assertIn(
            "needs: [static-validation, full-suite, properties, fault-restart-contention, mutations]",
            jobs["phase5-gate"],
        )
        self.assertNotIn("if: always()", jobs["phase5-gate"])

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
            for path in sorted((ROOT / "reservation_execution").rglob("*.py"))
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
        self.assertIn(
            "property evidence diverges from the closed operational contract",
            failures,
        )

        payload = property_payload()
        payload["report"]["outcome_counts"]["effect_confirmed"] -= 1
        failures = []
        check_property_payload(failures, payload)
        self.assertIn(
            "property evidence diverges from the closed operational contract",
            failures,
        )

    def test_fault_validator_closes_manifest_restart_and_contention_oracles(self) -> None:
        fault, restart, concurrency = fault_payloads()
        failures: list[str] = []
        check_fault_payloads(failures, fault, restart, concurrency)
        self.assertEqual(failures, [])

        restart["configuration"]["schedules"] = 1_999
        concurrency["round_results"][0]["provider_calls"] = 2
        failures = []
        check_fault_payloads(failures, fault, restart, concurrency)
        self.assertIn(
            "restart evidence diverges from exact identities and postconditions",
            failures,
        )
        self.assertIn(
            "contention evidence diverges from exact bilateral round oracles",
            failures,
        )

    def test_mutation_validator_requires_exact_catalog_and_valid_kills(self) -> None:
        payload = mutation_payload()
        failures: list[str] = []
        check_mutation_payload(failures, payload)
        self.assertEqual(failures, [])

        payload["mutants"][0]["loader_error"] = True
        failures = []
        check_mutation_payload(failures, payload)
        self.assertIn(
            "mutation evidence diverges from the independent closed catalog",
            failures,
        )

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
