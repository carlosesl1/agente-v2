#!/usr/bin/env python3
"""Closed validator for Phase 5 durable command execution evidence and purity."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_execution.schema import render_postgresql, render_sqlite  # noqa: E402
from scripts.generate_phase5_manifest import (  # noqa: E402
    build_package_manifest,
    build_schema_manifest,
    render_sums,
)
from scripts.run_phase5_faults import FAULT_POINTS  # noqa: E402
from scripts.run_phase5_mutations import MUTANTS  # noqa: E402
from scripts.validate_phase0 import check_markdown_links, check_secrets_and_pii  # noqa: E402

PHASE = "phase-05-durable-command-execution"
SEED = 2_026_071_905
MINIMUM_PROPERTY_CASES = 20_000
MINIMUM_RESTART_SCHEDULES = 2_000
MINIMUM_CONTENTION_ROUNDS = 50
PACKAGE = ROOT / "reservation_execution"
EVIDENCE = ROOT / "docs" / "refactor" / "evidence" / "phase-05"
OPERATIONAL_CONTRACT_PATH = EVIDENCE / "operational-gate-contract.json"
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
POSITIVE_PROPERTY_COUNTERS = (
    "authorized_commands",
    "terminal_commands",
    "summary_outboxes",
    "final_outboxes",
    "expired_lease_recoveries",
    "stale_token_rejections",
    "post_fence_unknowns",
    "manual_reviews",
    "delivery_retries",
    "duplicate_probes",
    "conflict_probes",
    "recovered_command_matches",
    "delivery_target_matches",
    "consistency_probes",
)
SAFETY_PROPERTY_COUNTERS = (
    "unauthorized_commands",
    "second_commands",
    "second_dispatch_slots",
    "second_provider_calls",
    "unknown_redispatches",
    "outbox_provider_retries",
    "partial_transactions",
    "stale_token_writes",
    "missing_terminals",
    "unexpected_exceptions",
    "wrong_command_claims",
    "wrong_delivery_targets",
)
OUTCOMES = {
    "called_no_effect",
    "called_unknown",
    "effect_confirmed",
    "not_called",
}
REQUIRED = (
    "README.md",
    "docs/refactor/README.md",
    "docs/refactor/evidence/README.md",
    "docs/refactor/06-risk-register.md",
    "docs/refactor/phases/phase-05-durable-command-execution.md",
    "docs/superpowers/specs/2026-07-19-phase-5-durable-command-execution-design.md",
    "docs/superpowers/plans/2026-07-19-phase-5-durable-command-execution.md",
    "reservation_execution/__init__.py",
    "reservation_execution/adapter.py",
    "reservation_execution/outbox.py",
    "reservation_execution/projection.py",
    "reservation_execution/properties.py",
    "reservation_execution/reconciliation.py",
    "reservation_execution/schema.py",
    "reservation_execution/sqlite_store.py",
    "reservation_execution/types.py",
    "reservation_execution/worker.py",
    "scripts/generate_phase5_schema.py",
    "scripts/generate_phase5_manifest.py",
    "scripts/run_phase5_properties.py",
    "scripts/run_phase5_faults.py",
    "scripts/run_phase5_mutations.py",
    "scripts/validate_phase5.py",
    "schemas/phase5/sqlite.sql",
    "schemas/phase5/postgresql.sql",
    "tests/phase5_helpers.py",
    "tests/test_phase5_closeout.py",
    ".github/workflows/phase5.yml",
    "docs/refactor/evidence/phase-05/README.md",
    "docs/refactor/evidence/phase-05/entry-baseline.json",
    "docs/refactor/evidence/phase-05/operational-gate-contract.json",
    "docs/refactor/evidence/phase-05/property-result.json",
    "docs/refactor/evidence/phase-05/fault-matrix.json",
    "docs/refactor/evidence/phase-05/restart-result.json",
    "docs/refactor/evidence/phase-05/concurrency-result.json",
    "docs/refactor/evidence/phase-05/mutation-result.json",
    "docs/refactor/evidence/phase-05/schema-manifest.json",
    "docs/refactor/evidence/phase-05/package-manifest.json",
    "docs/refactor/evidence/phase-05/performance-result.json",
    "docs/refactor/evidence/phase-05/validation-result.json",
    "docs/refactor/evidence/phase-05/adversarial-review.md",
    "docs/refactor/evidence/phase-05/SHA256SUMS",
)
FORBIDDEN_IMPORTS = {
    "aiohttp",
    "anthropic",
    "boto3",
    "fastapi",
    "http",
    "httpx",
    "openai",
    "psycopg",
    "redis",
    "requests",
    "socket",
    "sqlalchemy",
    "subprocess",
    "supabase",
    "urllib",
}
RUNTIME_MARKERS = (
    "api_key",
    "access_token",
    "client_secret",
    "http://",
    "https://",
    "manychat",
    "supabase",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if type(payload) is not dict:
        raise ValueError("JSON root must be an object")
    return payload


def _exact_json(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return set(actual) == set(expected) and all(
            _exact_json(actual[key], value) for key, value in expected.items()
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _exact_json(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _exact_int(value: object, expected: int | None = None) -> bool:
    return type(value) is int and (expected is None or value == expected)


OPERATIONAL_CONTRACT = _load_json(OPERATIONAL_CONTRACT_PATH)


def check_operational_contract(failures: list[str]) -> dict[str, Any]:
    contract = OPERATIONAL_CONTRACT
    if (
        not _exact_int(contract.get("schema_version"), 1)
        or contract.get("phase") != PHASE
        or not _exact_int(contract.get("seed"), SEED)
    ):
        failures.append("operational gate contract envelope mismatch")
    fault_points = tuple(contract.get("fault_gate", {}).get("fault_points", ()))
    restart_points = tuple(contract.get("fault_gate", {}).get("restart_points", ()))
    mutation_catalog = contract.get("mutation_catalog", [])
    if fault_points != FAULT_POINTS or len(fault_points) != 17:
        failures.append("runner fault catalog diverges from independent contract")
    expected_restart_points = (
        "after_commit_before_claim",
        "after_claim_before_prepare",
        "during_prepare",
        "after_prepare_before_fence",
        "after_fence_before_dispatch",
        "during_dispatch",
        "after_dispatch_before_outcome",
        "during_delivery",
        "after_delivery_before_receipt",
    )
    if restart_points != expected_restart_points:
        failures.append("restart catalog diverges from independent contract")
    grouped_points = [
        point
        for group in contract.get("fault_gate", {}).get("expectation_groups", [])
        for point in group.get("points", [])
    ]
    schedule_keys = set(contract.get("fault_gate", {}).get("schedule_keys", []))
    expected_schedule_keys = set(
        contract.get("fault_gate", {}).get("schedule_defaults", {})
    ) | {"fault_point", "schedule"}
    if grouped_points != list(fault_points) or len(set(grouped_points)) != 17:
        failures.append("fault expectation groups do not partition the closed catalog")
    if schedule_keys != expected_schedule_keys or len(schedule_keys) != 25:
        failures.append("fault schedule schema diverges from the closed contract")
    contention_gate = contract.get("contention_gate", {})
    if (
        not _exact_int(contention_gate.get("rounds"), 50)
        or set(contention_gate.get("command_row_keys", []))
        != {
            "child_errors", "kind", "nonzero_child_exits", "partial_transactions",
            "provider_calls", "round", "violations", "winners", "winning_tokens",
        }
        or set(contention_gate.get("outbox_row_keys", []))
        != {
            "child_errors", "kind", "nonzero_child_exits", "partial_transactions",
            "provider_calls", "provider_calls_baseline", "provider_calls_final",
            "round", "violations", "winners", "winning_tokens",
        }
    ):
        failures.append("contention row schemas diverge from the closed contract")
    runner_mutants = [
        {"name": mutant.name, "path": mutant.path, "test": mutant.test}
        for mutant in MUTANTS
    ]
    if not _exact_json(mutation_catalog, runner_mutants) or len(mutation_catalog) != 20:
        failures.append("runner mutation catalog diverges from independent contract")
    return {
        "fault_points": len(fault_points),
        "restart_points": len(restart_points),
        "mutants": len(mutation_catalog),
    }


def check_property_payload(failures: list[str], payload: dict[str, Any]) -> None:
    gate = OPERATIONAL_CONTRACT["property_gate"]
    expected = {
        "schema_version": 1,
        "phase": PHASE,
        "mode": "gate",
        "configuration": gate["configuration"],
        "result": "passed",
        "report": gate["report"],
    }
    if not _exact_json(payload, expected):
        failures.append("property evidence diverges from the closed operational contract")


def _expected_fault_schedule(point: str, schedule: int) -> dict[str, Any]:
    gate = OPERATIONAL_CONTRACT["fault_gate"]
    expected = dict(gate["schedule_defaults"])
    matches = [
        group for group in gate["expectation_groups"] if point in group["points"]
    ]
    if len(matches) != 1:
        raise ValueError(f"fault point must have exactly one expectation group: {point}")
    expected.update(matches[0]["values"])
    expected["fault_point"] = point
    expected["schedule"] = schedule
    return expected


def _expected_fault_matrix() -> dict[str, Any]:
    points = OPERATIONAL_CONTRACT["fault_gate"]["fault_points"]
    return {
        "schema_version": 1,
        "phase": PHASE,
        "kind": "fault-matrix",
        "configuration": {"seed": SEED, "fault_point_count": 17},
        "fault_points": points,
        "result": "passed",
        "violations": 0,
        "schedules": [
            _expected_fault_schedule(point, index)
            for index, point in enumerate(points)
        ],
    }


def _expected_restart_report() -> dict[str, Any]:
    points = OPERATIONAL_CONTRACT["fault_gate"]["restart_points"]
    generator = random.Random(SEED)
    selected = [points[generator.randrange(len(points))] for _ in range(2_000)]
    counts = {point: selected.count(point) for point in points}
    return {
        "schema_version": 1,
        "phase": PHASE,
        "kind": "restart-schedules",
        "configuration": {"seed": SEED, "schedules": 2_000},
        "result": "passed",
        "violations": 0,
        "fault_point_counts": counts,
        "schedules": [
            _expected_fault_schedule(point, index)
            for index, point in enumerate(selected)
        ],
    }


def _expected_contention_report() -> dict[str, Any]:
    rows = []
    for index in range(50):
        rows.append(
            {
                "kind": "command",
                "round": index,
                "winners": 1,
                "winning_tokens": [1],
                "provider_calls": 1,
                "partial_transactions": 0,
                "child_errors": 0,
                "nonzero_child_exits": 0,
                "violations": [],
            }
        )
        rows.append(
            {
                "kind": "outbox",
                "round": index,
                "winners": 1,
                "winning_tokens": [1],
                "provider_calls": 0,
                "provider_calls_baseline": 1,
                "provider_calls_final": 1,
                "partial_transactions": 0,
                "child_errors": 0,
                "nonzero_child_exits": 0,
                "violations": [],
            }
        )
    return {
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
        "max_provider_calls_per_round": 1,
        "partial_transactions": 0,
        "round_results": rows,
    }


def check_fault_payloads(
    failures: list[str],
    fault: dict[str, Any],
    restart: dict[str, Any],
    concurrency: dict[str, Any],
) -> None:
    if not _exact_json(fault, _expected_fault_matrix()):
        failures.append("fault matrix diverges from exact per-boundary postconditions")
    if not _exact_json(restart, _expected_restart_report()):
        failures.append("restart evidence diverges from exact identities and postconditions")
    if not _exact_json(concurrency, _expected_contention_report()):
        failures.append("contention evidence diverges from exact bilateral round oracles")


def check_mutation_payload(failures: list[str], payload: dict[str, Any]) -> None:
    catalog = OPERATIONAL_CONTRACT["mutation_catalog"]
    expected_rows = [
        {
            **item,
            "target_count": 1,
            "baseline_exit_code": 0,
            "exit_code": 1,
            "loader_error": False,
            "killed": True,
        }
        for item in catalog
    ]
    expected = {
        "schema_version": 1,
        "phase": PHASE,
        "scope": "temporary repository copies only; working tree unchanged",
        "catalog_count": 20,
        "mutant_count": 20,
        "all_killed": True,
        "mutants": expected_rows,
    }
    if not _exact_json(payload, expected):
        failures.append("mutation evidence diverges from the independent closed catalog")


def check_package_purity(failures: list[str], *, root: Path = ROOT) -> dict[str, Any]:
    package = root / "reservation_execution"
    imports: set[str] = set()
    marker_hits: list[str] = []
    environment_calls: list[str] = []
    reconciler_capabilities: list[str] = []
    outbox_ledger_references: list[str] = []
    files = tuple(sorted(package.rglob("*.py")))
    for path in files:
        source = path.read_text(encoding="utf-8")
        lower = source.lower()
        marker_hits.extend(
            f"{path.relative_to(root)}:{marker}" for marker in RUNTIME_MARKERS if marker in lower
        )
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.lstrip(".").split(".", 1)[0]
                if module:
                    imports.add(module)
            elif isinstance(node, ast.Attribute):
                if node.attr in {"environ", "getenv"}:
                    environment_calls.append(f"{path.relative_to(root)}:{node.lineno}:{node.attr}")
                if path.name == "reconciliation.py" and node.attr in {
                    "dispatch",
                    "prepare",
                    "deliver",
                }:
                    reconciler_capabilities.append(
                        f"{path.relative_to(root)}:{node.lineno}:{node.attr}"
                    )
        if path.name == "outbox.py" and "execution_ledger" in source:
            outbox_ledger_references.append(str(path.relative_to(root)))
        if path.name == "sqlite_store.py":
            functions = {
                node.name: node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            calls: dict[str, set[str]] = {}
            ledger_writers: set[str] = set()
            for name, function in functions.items():
                calls[name] = set()
                literals = " ".join(
                    node.value
                    for node in ast.walk(function)
                    if isinstance(node, ast.Constant) and type(node.value) is str
                ).lower()
                if re.search(
                    r"(?:update|insert\s+into|delete\s+from)\s+execution_ledger",
                    literals,
                ):
                    ledger_writers.add(name)
                for call in (
                    node for node in ast.walk(function) if isinstance(node, ast.Call)
                ):
                    target = None
                    if isinstance(call.func, ast.Name):
                        target = call.func.id
                    elif isinstance(call.func, ast.Attribute):
                        target = call.func.attr
                    if target in functions:
                        calls[name].add(target)
            for entry in sorted(name for name in functions if "outbox" in name):
                pending = [entry]
                visited: set[str] = set()
                while pending:
                    current = pending.pop()
                    if current in visited:
                        continue
                    visited.add(current)
                    if current in ledger_writers:
                        suffix = current if current == entry else f"{entry}->{current}"
                        outbox_ledger_references.append(
                            f"{path.relative_to(root)}:{suffix}"
                        )
                    pending.extend(sorted(calls.get(current, ())))
    forbidden = sorted(imports.intersection(FORBIDDEN_IMPORTS))
    if forbidden:
        failures.append(f"external capability imports in execution package: {forbidden}")
    if marker_hits:
        failures.append(f"runtime/auth markers in execution package: {marker_hits}")
    if environment_calls:
        failures.append(f"environment access in execution package: {environment_calls}")
    if reconciler_capabilities:
        failures.append(f"reconciler has external capability: {reconciler_capabilities}")
    if outbox_ledger_references:
        failures.append(f"outbox API writes commercial ledger: {outbox_ledger_references}")
    return {
        "python_files": len(files),
        "imports": sorted(imports),
        "forbidden_imports": forbidden,
        "runtime_marker_hits": marker_hits,
        "environment_calls": environment_calls,
        "reconciler_capabilities": reconciler_capabilities,
        "outbox_ledger_references": outbox_ledger_references,
    }


def check_live_execution_claims(
    failures: list[str],
    *,
    evidence: Path = EVIDENCE,
) -> dict[str, Any]:
    positive: list[str] = []
    json_files = tuple(sorted(evidence.rglob("*.json")))
    markdown_files = tuple(sorted(evidence.rglob("*.md")))
    domains = ("postgresql", "docker", "supabase", "provider", "delivery", "llm", "network")
    actions = ("executed", "live", "run")

    def is_positive(value: object) -> bool:
        if value is True:
            return True
        if type(value) in {int, float} and value != 0:
            return True
        if type(value) is str and value.strip().lower() in {"true", "yes", "sim", "executed", "live"}:
            return True
        return False

    def visit(value: object, *, file_name: str) -> None:
        if type(value) is dict:
            for key, item in value.items():
                normalized = key.lower()
                tokens = set(re.split(r"[^a-z0-9]+", normalized))
                live_alias = (
                    key != "sqlite_executed"
                    and any(domain in tokens for domain in domains)
                    and any(action in tokens for action in actions)
                )
                known_counter = key in {
                    "live_provider_calls",
                    "live_delivery_calls",
                    "live_database_calls",
                    "network_calls",
                }
                if (live_alias or known_counter) and is_positive(item):
                    positive.append(f"{file_name}:{key}")
                visit(item, file_name=file_name)
        elif type(value) is list:
            for item in value:
                visit(item, file_name=file_name)

    for path in json_files:
        name = str(path.relative_to(evidence))
        try:
            payload = _load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"cannot scan live claims in {name}: {exc}")
            continue
        if not _exact_int(payload.get("schema_version"), 1) or payload.get("phase") != PHASE:
            failures.append(f"evidence JSON envelope mismatch: {name}")
        visit(payload, file_name=name)
    positive_markdown = re.compile(
        r"(?i)\b(postgresql|docker|supabase|provider|delivery|llm|network)\w*\b"
        r"[^\n]*\b(executed|run|live|calls?|writes?|reads?)\b\s*[:=]\s*"
        r"(true|yes|sim|[1-9][0-9]*)\b"
    )
    for path in markdown_files:
        name = str(path.relative_to(evidence))
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if positive_markdown.search(line):
                positive.append(f"{name}:{line_number}")
    for claim in positive:
        failures.append(f"evidence overclaims live execution: {claim}")
    return {
        "files_scanned": len(json_files) + len(markdown_files),
        "positive_claims": positive,
    }


def _read_evidence(failures: list[str], name: str) -> dict[str, Any]:
    try:
        return _load_json(EVIDENCE / name)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read {name}: {exc}")
        return {}


def check_required(failures: list[str]) -> None:
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            failures.append(f"missing required file: {relative}")


def check_git_index(failures: list[str]) -> set[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached"], cwd=ROOT, capture_output=True, text=True
    )
    if completed.returncode:
        failures.append("cannot inspect git index")
        return set()
    indexed = set(completed.stdout.splitlines())
    for relative in sorted(set(REQUIRED) - indexed):
        failures.append(f"required Phase 5 file is not tracked/staged: {relative}")
    forbidden = sorted(
        relative
        for relative in indexed
        if relative.lower().endswith((".db", ".sqlite", ".sqlite3", ".log", "-wal", "-shm"))
        or (
            relative.startswith("docs/refactor/evidence/phase-05/")
            and Path(relative).name.endswith(("-wal", "-shm"))
        )
    )
    if forbidden:
        failures.append(f"tracked runtime artifacts: {forbidden}")
    return indexed


def check_previous_validators(failures: list[str]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for phase in range(5):
        env = dict(os.environ)
        if phase == 1:
            env["PHASE1_LEGACY_SOURCE"] = "/path-not-present-in-ci"
        completed = subprocess.run(
            [sys.executable, f"scripts/validate_phase{phase}.py"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            status = str(json.loads(completed.stdout).get("status") or "invalid_output")
        except json.JSONDecodeError:
            status = "invalid_output"
        statuses[f"phase{phase}"] = status
        if completed.returncode or status != "ok":
            failures.append(f"previous validator failed: phase{phase}:{status}")
    return statuses


def check_schemas_and_manifests(failures: list[str]) -> dict[str, Any]:
    if (ROOT / "schemas/phase5/sqlite.sql").read_text(encoding="utf-8") != render_sqlite():
        failures.append("SQLite DDL diverges from generator")
    if (ROOT / "schemas/phase5/postgresql.sql").read_text(encoding="utf-8") != render_postgresql():
        failures.append("PostgreSQL DDL diverges from generator")
    expected = {
        "schema-manifest.json": build_schema_manifest(),
        "package-manifest.json": build_package_manifest(),
    }
    for name, value in expected.items():
        actual = _read_evidence(failures, name)
        if not _exact_json(actual, value):
            failures.append(f"{name} is stale; regenerate it")
    schema = expected["schema-manifest.json"]
    if schema.get("postgresql_executed") is not False:
        failures.append("schema manifest overclaims PostgreSQL execution")
    return {
        "package_python_files": expected["package-manifest.json"]["python_file_count"],
        "schema_files": len(schema["files"]),
        "postgresql_executed": schema["postgresql_executed"],
    }


def check_red_evidence(failures: list[str]) -> dict[str, int]:
    files = tuple(sorted(EVIDENCE.glob("red-result-*.json")))
    entries = 0
    for path in files:
        try:
            payload = _load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"cannot read RED evidence {path.name}: {exc}")
            continue
        candidates = [payload]
        candidates.extend(payload.get("controller_regressions", []))
        review = payload.get("review_regression")
        if type(review) is dict:
            candidates.append(review)
        for item in candidates:
            if type(item) is not dict:
                failures.append(f"RED evidence entry must be an object: {path.name}")
                entries += 1
                continue
            if not _exact_int(item.get("exit_code")) or item["exit_code"] == 0:
                failures.append(f"RED evidence must record nonzero integer exit: {path.name}")
            for field in ("tests_run", "failures", "errors", "failure_count", "error_count"):
                if field in item and not _exact_int(item[field]):
                    failures.append(f"RED evidence integer field invalid: {path.name}:{field}")
            if not HASH_RE.fullmatch(str(item.get("output_sha256", ""))):
                failures.append(f"RED evidence hash invalid: {path.name}")
            if item.get("raw_output_versioned") is not False:
                failures.append(f"raw RED output must not be versioned: {path.name}")
            entries += 1
        if payload.get("phase") != PHASE or payload.get("rollout") not in {None, "NO-GO"}:
            failures.append(f"RED evidence envelope invalid: {path.name}")
    return {"files": len(files), "entries": entries}


def check_metrics(failures: list[str]) -> dict[str, Any]:
    validation = _read_evidence(failures, "validation-result.json")
    performance = _read_evidence(failures, "performance-result.json")
    expected_keys = {
        "validation-result.json": {
            "schema_version", "phase", "result", "command", "exit_code",
            "tests_run", "unittest_elapsed_seconds", "elapsed_seconds",
            "max_rss_kb", "output_sha256", "raw_output_versioned",
            "network_calls", "live_provider_calls", "live_delivery_calls",
            "live_database_calls", "rollout",
        },
        "performance-result.json": {
            "schema_version", "phase", "result", "measurement", "command",
            "exit_code", "tests_run", "elapsed_seconds", "max_rss_kb",
            "ci_timeout_seconds", "output_sha256", "raw_output_versioned",
            "nondeterministic_metrics_local_only", "postgresql_executed",
            "live_capabilities_executed", "rollout",
        },
    }
    for name, payload in (("validation-result.json", validation), ("performance-result.json", performance)):
        elapsed = payload.get("elapsed_seconds")
        rss = payload.get("max_rss_kb")
        if (
            set(payload) != expected_keys[name]
            or not _exact_int(payload.get("schema_version"), 1)
            or payload.get("phase") != PHASE
            or not _exact_int(payload.get("exit_code"), 0)
            or payload.get("result") != "passed"
            or not _exact_int(payload.get("tests_run"))
            or payload.get("tests_run", 0) < 326
            or isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or elapsed <= 0
            or not _exact_int(rss)
            or rss < 1
            or not HASH_RE.fullmatch(str(payload.get("output_sha256", "")))
            or payload.get("raw_output_versioned") is not False
        ):
            failures.append(f"metrics envelope mismatch: {name}")
    if not _exact_int(performance.get("ci_timeout_seconds"), 900):
        failures.append("performance evidence must record the 15-minute CI timeout")
    return {
        "tests_run": validation.get("tests_run"),
        "elapsed_seconds": validation.get("elapsed_seconds"),
        "max_rss_kb": validation.get("max_rss_kb"),
    }


def check_entry_claims(failures: list[str]) -> dict[str, Any]:
    payload = _read_evidence(failures, "entry-baseline.json")
    for key in (
        "postgresql_executed",
        "docker_executed",
        "supabase_executed",
        "provider_reads_executed",
        "provider_writes_executed",
        "message_delivery_executed",
        "llm_executed",
    ):
        if payload.get(key) is not False:
            failures.append(f"entry evidence overclaims live execution: {key}")
    if payload.get("rollout") != "NO-GO":
        failures.append("rollout must remain NO-GO")
    return {"rollout": payload.get("rollout")}


def check_workflow(failures: list[str]) -> None:
    workflow = OPERATIONAL_CONTRACT.get("workflow", {})
    relative = workflow.get("path")
    expected_hash = workflow.get("sha256")
    if relative != ".github/workflows/phase5.yml" or not HASH_RE.fullmatch(
        str(expected_hash or "")
    ):
        failures.append("operational contract workflow identity is invalid")
        return
    path = ROOT / relative
    if not path.is_file() or sha256(path) != expected_hash:
        failures.append("Phase 5 workflow diverges from the canonical reviewed workflow")


def check_adversarial_review(failures: list[str]) -> None:
    path = EVIDENCE / "adversarial-review.md"
    if not path.is_file():
        failures.append("missing adversarial-review.md")
        return
    source = path.read_text(encoding="utf-8")
    for index in range(1, 16):
        if f"### {index}." not in source:
            failures.append(f"adversarial review missing answer {index}")
    if "Critical: nenhum" not in source or "Important: nenhum" not in source:
        failures.append("adversarial review has unresolved material findings")


def check_sums(failures: list[str]) -> int:
    path = EVIDENCE / "SHA256SUMS"
    if not path.is_file():
        failures.append("missing Phase 5 SHA256SUMS")
        return 0
    actual = path.read_text(encoding="utf-8")
    expected = render_sums()
    if actual != expected:
        failures.append("Phase 5 SHA256SUMS is stale")
    return len(actual.splitlines())


def check_diff(failures: list[str]) -> None:
    for command in (["git", "diff", "--check"], ["git", "diff", "--cached", "--check"]):
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        if completed.returncode:
            failures.append(f"whitespace check failed: {' '.join(command)}")


def main() -> int:
    failures: list[str] = []
    check_required(failures)
    indexed = check_git_index(failures)
    contract = check_operational_contract(failures)
    previous = check_previous_validators(failures)
    purity = check_package_purity(failures)
    manifests = check_schemas_and_manifests(failures)
    property_payload = _read_evidence(failures, "property-result.json")
    check_property_payload(failures, property_payload)
    fault = _read_evidence(failures, "fault-matrix.json")
    restart = _read_evidence(failures, "restart-result.json")
    concurrency = _read_evidence(failures, "concurrency-result.json")
    check_fault_payloads(failures, fault, restart, concurrency)
    mutation = _read_evidence(failures, "mutation-result.json")
    check_mutation_payload(failures, mutation)
    metrics = check_metrics(failures)
    red = check_red_evidence(failures)
    claims = check_entry_claims(failures)
    live_claims = check_live_execution_claims(failures)
    check_workflow(failures)
    check_adversarial_review(failures)
    sums = check_sums(failures)
    db_artifacts = [
        str(path.relative_to(ROOT))
        for path in EVIDENCE.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".log"}
            or path.name.endswith(("-wal", "-shm"))
        )
    ]
    if db_artifacts:
        failures.append(f"runtime artifacts in evidence: {db_artifacts}")
    scanned = check_secrets_and_pii(failures)
    links = check_markdown_links(failures)
    check_diff(failures)
    summary = {
        "status": "failed" if failures else "ok",
        "phase": PHASE,
        "schema_version": 1,
        "previous_validators": previous,
        "operational_contract": contract,
        "purity": purity,
        "manifests": manifests,
        "properties": {
            "cases": property_payload.get("report", {}).get("cases"),
            "passed": property_payload.get("report", {}).get("passed"),
        },
        "faults": {
            "fault_points": len(fault.get("fault_points", [])),
            "restart_schedules": restart.get("configuration", {}).get("schedules"),
            "contention_rounds": concurrency.get("configuration", {}).get("rounds"),
        },
        "mutations": {
            "catalog_count": mutation.get("catalog_count"),
            "all_killed": mutation.get("all_killed"),
        },
        "metrics": metrics,
        "red": red,
        "claims": claims,
        "live_claims": live_claims,
        "indexed_files": len(indexed),
        "checksum_entries": sums,
        "db_artifacts": db_artifacts,
        "text_files_scanned": scanned,
        "relative_links_checked": links,
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
