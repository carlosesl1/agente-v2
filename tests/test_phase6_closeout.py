from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scripts.validate_phase6 as phase6_validator
from scripts.generate_phase6_manifest import (
    EVIDENCE_RELATIVE,
    MUTATION_TARGETS,
    build_package_manifest,
    build_schema_manifest,
    check_manifests,
    checksum_paths,
    render_sums,
)
from scripts.validate_phase6 import (
    CONTENTION_DOMAINS,
    FAULT_POINTS,
    MUTATION_CATALOG,
    PROPERTY_MODES,
    RESTART_POINTS,
    check_fault_payloads,
    check_ci_payload,
    check_closeout_payload,
    check_live_execution_claims,
    check_metrics,
    check_mutation_payload,
    check_package_purity,
    check_property_payload,
    check_required_files,
    check_workflow,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/refactor/evidence/phase-06"
PHASE = "phase-06-handoff-and-payments"
SEED = 2_026_071_906


def load_json(name: str) -> dict[str, object]:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


class Phase6CloseoutContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.property_payload = load_json("property-result.json")
        cls.fault_payload = load_json("fault-matrix.json")
        cls.restart_payload = load_json("restart-result.json")
        cls.concurrency_payload = load_json("concurrency-result.json")
        cls.mutation_payload = load_json("mutation-result.json")

    def test_independent_catalogs_are_closed_and_not_runner_derived(self) -> None:
        self.assertEqual(len(PROPERTY_MODES), 16)
        self.assertEqual(len(FAULT_POINTS), 27)
        self.assertEqual(len(RESTART_POINTS), 12)
        self.assertEqual(
            CONTENTION_DOMAINS,
            (
                "handoff_incident",
                "payment_command",
                "global_evidence_claim",
                "payment_outbox",
            ),
        )
        self.assertEqual(len(MUTATION_CATALOG), 12)
        self.assertEqual(
            set(MUTATION_TARGETS),
            {str(item["path"]) for item in MUTATION_CATALOG},
        )
        source = (ROOT / "scripts/validate_phase6.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertFalse(
            any(name.startswith("scripts.run_phase6_") for name in imported),
            imported,
        )

    def test_property_validator_reconstructs_rows_and_rejects_hollow_or_bad_types(self) -> None:
        failures: list[str] = []
        check_property_payload(failures, self.property_payload)
        self.assertEqual(failures, [])

        def hollow_rows(payload: dict[str, object]) -> None:
            payload["report"]["rows"] = [
                {"mode": row["mode"], "violations": []}
                for row in payload["report"]["rows"]
            ]

        def duplicate_confirmed_identity(payload: dict[str, object]) -> None:
            payload["report"]["rows"][17]["reservation_workflow_id"] = (
                payload["report"]["rows"][1]["reservation_workflow_id"]
            )

        mutations = [
            ("hollow rows", hollow_rows),
            (
                "duplicate row identity",
                lambda payload: payload["report"]["rows"][16].__setitem__("index", 0),
            ),
            (
                "malformed confirmed identity",
                lambda payload: payload["report"]["rows"][1].__setitem__(
                    "reservation_workflow_id", "x"
                ),
            ),
            ("duplicate confirmed workflow identity", duplicate_confirmed_identity),
            (
                "reduced mode schedule",
                lambda payload: payload["report"]["rows"][1].__setitem__(
                    "mode", PROPERTY_MODES[0]
                ),
            ),
            (
                "false safety aggregate",
                lambda payload: payload["report"]["counters"].__setitem__(
                    "proof_reuses", 1
                ),
            ),
            (
                "hollow audit",
                lambda payload: payload["report"]["audits"][0].__setitem__(
                    "quick_check", "not ok"
                ),
            ),
        ]
        for bad_cases in (True, 20_000.0, "20000"):
            mutations.append(
                (
                    f"invalid cases type {bad_cases!r}",
                    lambda payload, value=bad_cases: payload["configuration"].__setitem__(
                        "cases", value
                    ),
                )
            )

        for label, mutate in mutations:
            with self.subTest(label=label):
                payload = copy.deepcopy(self.property_payload)
                mutate(payload)
                failures = []
                check_property_payload(failures, payload)
                self.assertTrue(failures, f"{label} false-greened")

    def test_fault_restart_and_contention_reject_hollow_duplicate_and_reduced_catalogs(self) -> None:
        failures: list[str] = []
        check_fault_payloads(
            failures,
            self.fault_payload,
            self.restart_payload,
            self.concurrency_payload,
        )
        self.assertEqual(failures, [])

        mutations = []
        fault = copy.deepcopy(self.fault_payload)
        restart = copy.deepcopy(self.restart_payload)
        concurrency = copy.deepcopy(self.concurrency_payload)
        fault["fault_points"] = fault["fault_points"][:-1]
        fault["schedules"] = fault["schedules"][:-1]
        fault["configuration"]["fault_point_count"] = 26
        mutations.append(("reduced fault catalog", fault, restart, concurrency))

        fault = copy.deepcopy(self.fault_payload)
        restart = copy.deepcopy(self.restart_payload)
        concurrency = copy.deepcopy(self.concurrency_payload)
        restart["schedules"] = [{"violations": []} for _ in range(2_000)]
        mutations.append(("hollow restart rows", fault, restart, concurrency))

        fault = copy.deepcopy(self.fault_payload)
        restart = copy.deepcopy(self.restart_payload)
        concurrency = copy.deepcopy(self.concurrency_payload)
        restart["schedules"][1] = copy.deepcopy(restart["schedules"][0])
        mutations.append(("duplicate restart identity", fault, restart, concurrency))

        fault = copy.deepcopy(self.fault_payload)
        restart = copy.deepcopy(self.restart_payload)
        concurrency = copy.deepcopy(self.concurrency_payload)
        for row in concurrency["round_results"]:
            row["domain"] = "handoff_incident"
        mutations.append(("collapsed contention domains", fault, restart, concurrency))

        fault = copy.deepcopy(self.fault_payload)
        restart = copy.deepcopy(self.restart_payload)
        concurrency = copy.deepcopy(self.concurrency_payload)
        concurrency["round_results"][0]["winners"] = True
        mutations.append(("bool protocol integer", fault, restart, concurrency))

        concurrency = copy.deepcopy(self.concurrency_payload)
        concurrency["round_results"][0]["durable_owners"] = ["other"]
        mutations.append(("bilateral oracle mismatch", self.fault_payload, self.restart_payload, concurrency))
        concurrency = copy.deepcopy(self.concurrency_payload)
        concurrency["round_results"][0]["winning_owners"] = ["x"]
        concurrency["round_results"][0]["durable_owners"] = ["x"]
        mutations.append(("forged bilateral owner", self.fault_payload, self.restart_payload, concurrency))

        for label, fault, restart, concurrency in mutations:
            with self.subTest(label=label):
                failures = []
                check_fault_payloads(failures, fault, restart, concurrency)
                self.assertTrue(failures, f"{label} false-greened")

    def test_mutation_validator_rejects_reduced_hollow_loader_and_wrong_types(self) -> None:
        failures: list[str] = []
        check_mutation_payload(failures, self.mutation_payload)
        self.assertEqual(failures, [])

        mutations = []
        payload = copy.deepcopy(self.mutation_payload)
        payload["mutants"] = payload["mutants"][:-1]
        payload["catalog_count"] = payload["mutant_count"] = 11
        mutations.append(("reduced catalog", payload))
        payload = copy.deepcopy(self.mutation_payload)
        payload["mutants"] = [{"killed": True} for _ in range(12)]
        mutations.append(("hollow rows", payload))
        payload = copy.deepcopy(self.mutation_payload)
        payload["mutants"][0]["loader_error"] = True
        mutations.append(("loader false kill", payload))
        for bad_target_count in (True, 1.0, "1"):
            payload = copy.deepcopy(self.mutation_payload)
            payload["mutants"][0]["target_count"] = bad_target_count
            mutations.append((f"invalid target count {bad_target_count!r}", payload))
        payload = copy.deepcopy(self.mutation_payload)
        payload["mutants"][1]["name"] = payload["mutants"][0]["name"]
        mutations.append(("duplicate mutant identity", payload))

        for label, payload in mutations:
            with self.subTest(label=label):
                failures = []
                check_mutation_payload(failures, payload)
                self.assertTrue(failures, f"{label} false-greened")

    def test_manifests_scan_recursively_include_mutation_targets_and_exclude_runtime_artifacts(self) -> None:
        relatives = {str(path.relative_to(ROOT)) for path in checksum_paths()}
        self.assertTrue({item["path"] for item in MUTATION_CATALOG}.issubset(relatives))
        self.assertIn(".github/workflows/phase6.yml", relatives)
        self.assertIn("tests/test_phase6_closeout.py", relatives)
        self.assertFalse(
            any(
                path.endswith((".db", ".sqlite", ".sqlite3", ".log", "-wal", "-shm"))
                for path in relatives
            )
        )
        self.assertNotIn("docs/refactor/evidence/phase-06/SHA256SUMS", relatives)
        self.assertTrue(set(MUTATION_TARGETS).issubset(relatives))

        with tempfile.TemporaryDirectory(prefix="phase6-missing-package-") as directory:
            with self.assertRaises(FileNotFoundError):
                build_package_manifest(root=Path(directory))

        with tempfile.TemporaryDirectory(prefix="phase6-recursive-package-") as directory:
            root = Path(directory)
            package = root / "reservation_followup/nested"
            package.mkdir(parents=True)
            (root / "reservation_followup/__init__.py").write_text("", encoding="utf-8")
            (package / "capability.py").write_text("import requests\n", encoding="utf-8")
            manifest = build_package_manifest(root=root)
            self.assertEqual(
                [item["path"] for item in manifest["files"]],
                [
                    "reservation_followup/__init__.py",
                    "reservation_followup/nested/capability.py",
                ],
            )
            failures: list[str] = []
            check_package_purity(failures, root=root)
            self.assertTrue(any("external capability imports" in item for item in failures))

    def test_schema_and_package_manifests_are_exact_and_current(self) -> None:
        schema = build_schema_manifest()
        package = build_package_manifest()
        self.assertEqual(schema["phase"], PHASE)
        self.assertFalse(schema["postgresql_executed"])
        self.assertTrue(schema["sqlite_executed"])
        self.assertEqual({item["dialect"] for item in schema["files"]}, {"sqlite", "postgresql"})
        self.assertEqual(package["package"], "reservation_followup")
        self.assertEqual(package["python_file_count"], len(package["files"]))
        self.assertEqual(check_manifests(), ())
        sums = render_sums()
        self.assertEqual(sums, (EVIDENCE / "SHA256SUMS").read_text(encoding="utf-8"))
        with patch(
            "scripts.generate_phase6_manifest.render_sums",
            return_value=sums + ("0" * 64) + "  unexpected\n",
        ):
            self.assertTrue(
                any("stale generated artifact" in item for item in check_manifests())
            )

    def test_purity_scan_follows_outbox_helpers_and_rejects_cross_workflow_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-outbox-callgraph-") as directory:
            root = Path(directory)
            package = root / "reservation_followup"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "sqlite_store.py").write_text(
                "def complete_payment_outbox():\n"
                "    _neutral_helper()\n\n"
                "def _neutral_helper():\n"
                "    _write_financial()\n\n"
                "def _write_financial():\n"
                "    sql = 'UPDATE ' + 'payment_ledger SET status=queued'\n\n"
                "def complete_handoff_outbox():\n"
                "    sql = 'UPDATE payment_workflows SET status=paid'\n\n"
                "def create_payment_workflow():\n"
                "    sql = 'UPDATE reservation_commands SET status=done'\n",
                encoding="utf-8",
            )
            (package / "reconciliation.py").write_text(
                "def reconcile(settlement):\n    settlement.dispatch()\n",
                encoding="utf-8",
            )
            (package / "auth.py").write_text(
                "import os\ndef load_token():\n    return os.getenv('PAYMENT_TOKEN')\n",
                encoding="utf-8",
            )
            (package / "processes.py").write_text(
                "import asyncio as aio\n"
                "import concurrent.futures as futures\n"
                "import multiprocessing as mp\n"
                "import os\n"
                "import subprocess as sp\n"
                "from asyncio import create_subprocess_shell as create_shell\n"
                "from concurrent.futures import ProcessPoolExecutor as Pool\n"
                "from multiprocessing import Process as Worker\n"
                "from os import execv as execute_exec\n"
                "from os import posix_spawn as execute_posix_spawn\n"
                "from os import spawnv as execute_spawn\n"
                "from os import system as launch\n"
                "from subprocess import run as run_command\n\n"
                "def execute_external():\n"
                "    os.popen('curl https://example.invalid')\n"
                "    os.system('curl https://example.invalid')\n"
                "    launch('curl https://example.invalid')\n"
                "    os.execv('/bin/false', ['/bin/false'])\n"
                "    execute_exec('/bin/false', ['/bin/false'])\n"
                "    os.spawnv(0, '/bin/false', ['/bin/false'])\n"
                "    execute_spawn(0, '/bin/false', ['/bin/false'])\n"
                "    os.posix_spawn('/bin/false', ['/bin/false'], {})\n"
                "    execute_posix_spawn('/bin/false', ['/bin/false'], {})\n"
                "    sp.run(['curl', 'https://example.invalid'])\n"
                "    run_command(['curl', 'https://example.invalid'])\n"
                "    mp.Process()\n"
                "    Worker()\n"
                "    aio.create_subprocess_exec('/bin/false')\n"
                "    aio.create_subprocess_shell('/bin/false')\n"
                "    create_shell('/bin/false')\n"
                "    futures.ProcessPoolExecutor()\n"
                "    Pool()\n",
                encoding="utf-8",
            )
            failures: list[str] = []
            summary = check_package_purity(failures, root=root)
            self.assertTrue(summary["outbox_ledger_references"])
            self.assertGreaterEqual(len(summary["cross_workflow_writes"]), 2)
            self.assertTrue(summary["reconciler_capabilities"])
            self.assertTrue(summary["environment_reads"])
            self.assertTrue(
                any(item.endswith(":subprocess") for item in summary["external_imports"]),
                summary["external_imports"],
            )
            self.assertTrue(
                any(item.endswith(":multiprocessing") for item in summary["external_imports"]),
                summary["external_imports"],
            )
            self.assertEqual(len(summary["process_executions"]), 18)
            for marker in (
                "os.popen",
                "os.system",
                "launch",
                "os.execv",
                "execute_exec",
                "os.spawnv",
                "execute_spawn",
                "os.posix_spawn",
                "execute_posix_spawn",
                "sp.run",
                "run_command",
                "mp.Process",
                "Worker",
                "aio.create_subprocess_exec",
                "aio.create_subprocess_shell",
                "create_shell",
                "futures.ProcessPoolExecutor",
                "Pool",
            ):
                self.assertTrue(
                    any(item.endswith(f":{marker}") for item in summary["process_executions"]),
                    (marker, summary["process_executions"]),
                )
            self.assertTrue(
                any("process execution" in failure for failure in failures),
                failures,
            )
            self.assertTrue(failures)

    def test_real_package_purity_baseline_has_no_capability_lists(self) -> None:
        failures: list[str] = []
        summary = check_package_purity(failures)
        self.assertEqual(failures, [])
        self.assertEqual(summary["python_files"], 11)
        self.assertEqual(
            {key: value for key, value in summary.items() if key != "python_files"},
            {
                "external_imports": [],
                "environment_reads": [],
                "process_executions": [],
                "reconciler_capabilities": [],
                "outbox_ledger_references": [],
                "cross_workflow_writes": [],
            },
        )

    def test_live_claim_scan_is_recursive_and_requires_exact_false_or_zero(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-live-claims-") as directory:
            evidence = Path(directory)
            nested = evidence / "nested"
            nested.mkdir()
            (nested / "claim.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "phase": PHASE,
                        "network_calls": True,
                        "postgresql_executed": True,
                    }
                ),
                encoding="utf-8",
            )
            (nested / "claim.md").write_text(
                "Docker executed: yes\nPix provider live: true\n",
                encoding="utf-8",
            )
            failures: list[str] = []
            summary = check_live_execution_claims(failures, evidence=evidence)
            self.assertGreaterEqual(len(summary["positive_claims"]), 3)
            self.assertTrue(failures)

    def test_workflow_requires_parallel_jobs_real_commands_and_no_always_gate(self) -> None:
        failures: list[str] = []
        summary = check_workflow(failures)
        self.assertEqual(failures, [])
        self.assertEqual(
            set(summary["checkout_jobs"]),
            {"static-validation", "full-suite", "properties", "fault-restart-contention", "mutations"},
        )
        workflow_text = (ROOT / ".github/workflows/phase6.yml").read_text(encoding="utf-8")
        active = "\n".join(
            line.split("#", 1)[0]
            for line in workflow_text.splitlines()
            if line.split("#", 1)[0].strip()
        )
        self.assertEqual(active.count("scripts/run_phase6_mutations.py --write"), 1)
        self.assertEqual(active.count("tests.test_phase6_mutation_runner"), 2)

        markers = (
            "name: phase-6-handoff-and-payments",
            "timeout-minutes: 15",
            "static-validation:",
            "full-suite:",
            "properties:",
            "fault-restart-contention:",
            "mutations:",
            "phase6-gate:",
            "--cases 20000",
            "--restart-schedules 2000",
            "--contention-rounds 50",
            "validate_phase6.py",
            "generate_phase6_manifest.py --check",
        )
        with tempfile.TemporaryDirectory(prefix="phase6-comment-workflow-") as directory:
            root = Path(directory)
            workflow = root / ".github/workflows/phase6.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: inert\non: workflow_dispatch\njobs: {}\n"
                + "".join(f"# {marker}\n" for marker in markers),
                encoding="utf-8",
            )
            failures = []
            check_workflow(failures, root=root)
            self.assertTrue(failures, "comment-only workflow false-greened")

    def test_ci_validator_requires_exact_seven_successful_workflows_and_phase6_jobs(self) -> None:
        names = (
            "phase-0-validation",
            "phase-1-characterization",
            "phase-2-domain",
            "phase-3-lookups",
            "phase-4-confirmation",
            "phase-5-durable-execution",
            "phase-6-handoff-and-payments",
        )
        workflows = [
            {
                "id": 100 + index,
                "name": name,
                "conclusion": "success",
                "url": f"https://github.com/example/agente-v2/actions/runs/{100 + index}",
            }
            for index, name in enumerate(names)
        ]
        workflows[-1]["jobs"] = [
            {"id": 200 + index, "name": name, "conclusion": "success"}
            for index, name in enumerate(
                (
                    "static-validation",
                    "full-suite",
                    "properties",
                    "fault-restart-contention",
                    "mutations",
                    "phase6-gate",
                )
            )
        ]
        payload = {
            "schema_version": 1,
            "phase": PHASE,
            "implementation_commit": "a" * 40,
            "checked_at_utc": "2026-07-20T00:00:00Z",
            "all_success": True,
            "workflow_count": 7,
            "workflows": workflows,
            "phase7_authorized_after_closeout": False,
            "phase7_started": False,
            "rollout": "NO-GO",
        }
        failures: list[str] = []
        check_ci_payload(failures, payload)
        self.assertEqual(failures, [])
        for label, mutate in (
            ("reduced workflows", lambda value: value.__setitem__("workflow_count", 6)),
            ("failed workflow", lambda value: value["workflows"][0].__setitem__("conclusion", "failure")),
            ("missing phase6 job", lambda value: value["workflows"][-1].__setitem__("jobs", value["workflows"][-1]["jobs"][:-1])),
            ("phase7 started", lambda value: value.__setitem__("phase7_started", True)),
        ):
            with self.subTest(label=label):
                candidate = copy.deepcopy(payload)
                mutate(candidate)
                failures = []
                check_ci_payload(failures, candidate)
                self.assertTrue(failures, f"{label} false-greened")

    def test_closeout_rejects_any_gate_over_the_ci_budget(self) -> None:
        payload = load_json("red-result-closeout.json")
        payload["status"] = "local_terminal_gates_passed"
        payload["gates"]["properties"]["elapsed_seconds"] = 899.999
        failures: list[str] = []
        check_closeout_payload(failures, payload)
        self.assertEqual(failures, [])

        payload["gates"]["properties"]["elapsed_seconds"] = 900.000001
        failures = []
        check_closeout_payload(failures, payload)
        self.assertTrue(
            any("budget" in item.lower() for item in failures),
            failures,
        )

    def test_metrics_require_closed_schema_exact_types_and_no_live_claims(self) -> None:
        validation = {
            "schema_version": 1,
            "phase": PHASE,
            "result": "passed",
            "command": "python3 -m unittest discover -s tests -v",
            "exit_code": 0,
            "tests_run": 616,
            "unittest_elapsed_seconds": 1.0,
            "elapsed_seconds": 1.1,
            "max_rss_kb": 1,
            "output_sha256": "a" * 64,
            "raw_output_versioned": False,
            "network_calls": 0,
            "live_provider_calls": 0,
            "live_delivery_calls": 0,
            "live_database_calls": 0,
            "rollout": "NO-GO",
        }
        performance = {
            "schema_version": 1,
            "phase": PHASE,
            "result": "passed",
            "measurement": "fresh full unittest suite",
            "command": "python3 -m unittest discover -s tests -v",
            "exit_code": 0,
            "tests_run": 616,
            "elapsed_seconds": 1.1,
            "max_rss_kb": 1,
            "ci_timeout_seconds": 900,
            "output_sha256": "a" * 64,
            "raw_output_versioned": False,
            "nondeterministic_metrics_local_only": True,
            "postgresql_executed": False,
            "live_capabilities_executed": False,
            "rollout": "NO-GO",
        }
        failures: list[str] = []
        check_metrics(failures, validation=validation, performance=performance)
        self.assertEqual(failures, [])
        for label, mutate in (
            ("bool zero", lambda v, p: v.__setitem__("network_calls", False)),
            ("string int", lambda v, p: v.__setitem__("tests_run", "616")),
            ("below test floor", lambda v, p: v.__setitem__("tests_run", 615)),
            ("extra key", lambda v, p: p.__setitem__("unexpected", 0)),
            ("false live claim", lambda v, p: p.__setitem__("postgresql_executed", 0)),
        ):
            with self.subTest(label=label):
                v = copy.deepcopy(validation)
                p = copy.deepcopy(performance)
                mutate(v, p)
                failures = []
                check_metrics(failures, validation=v, performance=p)
                self.assertTrue(failures, f"{label} false-greened")

    def test_required_files_reject_missing_extra_runtime_artifacts_and_checksums(self) -> None:
        failures: list[str] = []
        check_required_files(failures)
        self.assertEqual(failures, [])
        with tempfile.TemporaryDirectory(prefix="phase6-required-files-") as directory:
            root = Path(directory)
            for relative in phase6_validator.REQUIRED:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic\n", encoding="utf-8")
            evidence = root / "docs/refactor/evidence/phase-06"
            (evidence / "leak.sqlite").write_bytes(b"SQLite format 3\x00")
            (evidence / "unexpected.json").write_text("{}\n", encoding="utf-8")
            failures = []
            check_required_files(failures, root=root)
            self.assertTrue(any("runtime artifact" in item for item in failures))
            self.assertTrue(any("unexpected evidence" in item for item in failures))
            (root / phase6_validator.REQUIRED[0]).unlink()
            failures = []
            check_required_files(failures, root=root)
            self.assertTrue(any("missing required" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
