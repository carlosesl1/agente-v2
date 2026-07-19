#!/usr/bin/env python3
"""Closed validator for Phase 5 durable command execution evidence and purity."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
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


def check_property_payload(failures: list[str], payload: dict[str, Any]) -> None:
    expected_configuration = {
        "cases": MINIMUM_PROPERTY_CASES,
        "minimum_gate_cases": MINIMUM_PROPERTY_CASES,
        "seed": SEED,
    }
    if (
        payload.get("schema_version") != 1
        or payload.get("phase") != PHASE
        or payload.get("mode") != "gate"
        or payload.get("configuration") != expected_configuration
        or payload.get("result") != "passed"
    ):
        failures.append("property gate envelope mismatch")
    report = payload.get("report")
    if type(report) is not dict:
        failures.append("property report must be an object")
        return
    cases = report.get("cases")
    if cases != MINIMUM_PROPERTY_CASES or report.get("seed") != SEED:
        failures.append("property workload/seed mismatch")
    if report.get("passed") is not True or report.get("violations") != []:
        failures.append("property report must pass without violations")
    if report.get("cloudbeds_cases", 0) + report.get("bokun_cases", 0) != cases:
        failures.append("property provider totals must equal cases")
    outcome_counts = report.get("outcome_counts")
    if (
        type(outcome_counts) is not dict
        or set(outcome_counts) != OUTCOMES
        or sum(outcome_counts.values()) != cases
        or any(type(value) is not int or value < 1 for value in outcome_counts.values())
    ):
        failures.append("property outcome totals must equal cases")
    for key in ("authorized_commands", "terminal_commands", "summary_outboxes", "final_outboxes"):
        if report.get(key) != cases:
            failures.append(f"property structural total must equal cases: {key}")
    for key in POSITIVE_PROPERTY_COUNTERS:
        value = report.get(key)
        if type(value) is not int or value < 1:
            failures.append(f"property coverage counter must be positive: {key}")
    for key in SAFETY_PROPERTY_COUNTERS:
        if report.get(key) != 0:
            failures.append(f"property safety counter must be zero: {key}")


def _passed_report(payload: dict[str, Any], *, kind: str) -> bool:
    return (
        payload.get("schema_version") == 1
        and payload.get("phase") == PHASE
        and payload.get("kind") == kind
        and payload.get("result") == "passed"
        and payload.get("violations") == 0
    )


def check_fault_payloads(
    failures: list[str],
    fault: dict[str, Any],
    restart: dict[str, Any],
    concurrency: dict[str, Any],
) -> None:
    if (
        not _passed_report(fault, kind="fault-matrix")
        or fault.get("configuration")
        != {"seed": SEED, "fault_point_count": len(FAULT_POINTS)}
        or fault.get("fault_points") != list(FAULT_POINTS)
        or len(fault.get("schedules", [])) != len(FAULT_POINTS)
        or any(item.get("violations") != [] for item in fault.get("schedules", []))
    ):
        failures.append("fault matrix must cover the exact 17-point manifest")
    if (
        not _passed_report(restart, kind="restart-schedules")
        or restart.get("configuration")
        != {"seed": SEED, "schedules": MINIMUM_RESTART_SCHEDULES}
        or len(restart.get("schedules", [])) != MINIMUM_RESTART_SCHEDULES
    ):
        failures.append("restart workload must contain exactly 2000 schedules")
    if any(item.get("violations") != [] for item in restart.get("schedules", [])):
        failures.append("restart schedules contain violations")
    rounds = concurrency.get("round_results", [])
    if (
        not _passed_report(concurrency, kind="multiprocess-contention")
        or concurrency.get("configuration")
        != {"seed": SEED, "rounds": MINIMUM_CONTENTION_ROUNDS}
        or concurrency.get("command_rounds") != MINIMUM_CONTENTION_ROUNDS
        or concurrency.get("outbox_rounds") != MINIMUM_CONTENTION_ROUNDS
        or concurrency.get("command_claim_winners") != MINIMUM_CONTENTION_ROUNDS
        or concurrency.get("outbox_claim_winners") != MINIMUM_CONTENTION_ROUNDS
        or concurrency.get("partial_transactions") != 0
        or len(rounds) != MINIMUM_CONTENTION_ROUNDS * 2
    ):
        failures.append("contention workload must contain exactly 50 command/outbox races")
    if any(
        item.get("winners") != 1
        or item.get("winning_tokens") != [1]
        or item.get("provider_calls") != (1 if item.get("kind") == "command" else 0)
        or item.get("partial_transactions") != 0
        or item.get("child_errors") != 0
        or item.get("nonzero_child_exits") != 0
        or item.get("violations") != []
        or item.get("kind") not in {"command", "outbox"}
        for item in rounds
    ):
        failures.append("contention round oracle mismatch")


def check_mutation_payload(failures: list[str], payload: dict[str, Any]) -> None:
    expected = [
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
    ]
    if (
        payload.get("schema_version") != 1
        or payload.get("phase") != PHASE
        or payload.get("scope")
        != "temporary repository copies only; working tree unchanged"
        or payload.get("catalog_count") != len(MUTANTS)
        or payload.get("mutant_count") != len(MUTANTS)
        or payload.get("all_killed") is not True
        or payload.get("mutants") != expected
    ):
        failures.append("mutation evidence does not match the closed catalog")


def check_package_purity(failures: list[str], *, root: Path = ROOT) -> dict[str, Any]:
    package = root / "reservation_execution"
    imports: set[str] = set()
    marker_hits: list[str] = []
    environment_calls: list[str] = []
    reconciler_capabilities: list[str] = []
    outbox_ledger_references: list[str] = []
    files = tuple(sorted(package.glob("*.py")))
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
            for function in (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and "outbox" in node.name
            ):
                literals = "\n".join(
                    node.value
                    for node in ast.walk(function)
                    if isinstance(node, ast.Constant) and type(node.value) is str
                ).lower()
                if re.search(
                    r"(?:update|insert\s+into|delete\s+from)\s+execution_ledger",
                    literals,
                ):
                    outbox_ledger_references.append(
                        f"{path.relative_to(root)}:{function.name}"
                    )
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
    files = tuple(sorted(evidence.glob("*.json")))

    def visit(value: object, *, file_name: str) -> None:
        if type(value) is dict:
            for key, item in value.items():
                if (
                    key != "sqlite_executed"
                    and key.endswith("_executed")
                    and item is True
                ):
                    positive.append(f"{file_name}:{key}")
                if (
                    key in {
                        "live_provider_calls",
                        "live_delivery_calls",
                        "live_database_calls",
                        "network_calls",
                    }
                    and type(item) is int
                    and item != 0
                ):
                    positive.append(f"{file_name}:{key}")
                visit(item, file_name=file_name)
        elif type(value) is list:
            for item in value:
                visit(item, file_name=file_name)

    for path in files:
        try:
            payload = _load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"cannot scan live claims in {path.name}: {exc}")
            continue
        visit(payload, file_name=path.name)
    for claim in positive:
        failures.append(f"evidence overclaims live execution: {claim}")
    return {"files_scanned": len(files), "positive_claims": positive}


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
        if actual != value:
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
            if type(item) is not dict or item.get("exit_code") == 0:
                failures.append(f"RED evidence must record nonzero exit: {path.name}")
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
    for name, payload in (("validation-result.json", validation), ("performance-result.json", performance)):
        elapsed = payload.get("elapsed_seconds")
        rss = payload.get("max_rss_kb")
        if (
            payload.get("phase") != PHASE
            or payload.get("exit_code") != 0
            or payload.get("result") != "passed"
            or type(payload.get("tests_run")) is not int
            or payload.get("tests_run", 0) < 326
            or isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or elapsed <= 0
            or isinstance(rss, bool)
            or type(rss) is not int
            or rss < 1
            or not HASH_RE.fullmatch(str(payload.get("output_sha256", "")))
            or payload.get("raw_output_versioned") is not False
        ):
            failures.append(f"metrics envelope mismatch: {name}")
    if performance.get("ci_timeout_seconds") != 900:
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
    source = (ROOT / ".github/workflows/phase5.yml").read_text(encoding="utf-8")
    required = (
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
    for marker in required:
        if marker not in source:
            failures.append(f"Phase 5 workflow missing marker: {marker}")


def check_adversarial_review(failures: list[str]) -> None:
    path = EVIDENCE / "adversarial-review.md"
    if not path.is_file():
        failures.append("missing adversarial-review.md")
        return
    source = path.read_text(encoding="utf-8")
    for index in range(1, 13):
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
