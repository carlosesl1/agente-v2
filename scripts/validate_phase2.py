#!/usr/bin/env python3
"""Validate Phase 2 domain purity, generated evidence and safety gates."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_domain import (  # noqa: E402
    EVENT_TYPES,
    STATE_TYPES,
    SCHEMA_VERSION,
    transition_matrix,
)
from scripts.generate_phase2_matrix import (  # noqa: E402
    build_domain_manifest,
    render_matrix,
)
from scripts.validate_phase0 import (  # noqa: E402
    check_markdown_links,
    check_secrets_and_pii,
)

DOMAIN = ROOT / "reservation_domain"
EVIDENCE = ROOT / "docs" / "refactor" / "evidence" / "phase-02"
MATRIX = ROOT / "docs" / "refactor" / "domain" / "phase2-state-event-matrix.md"
REQUIRED = (
    "reservation_domain/__init__.py",
    "reservation_domain/README.md",
    "reservation_domain/types.py",
    "reservation_domain/signature.py",
    "reservation_domain/reducer.py",
    "reservation_domain/serialization.py",
    "reservation_domain/properties.py",
    "tests/test_phase2_domain.py",
    "tests/test_phase2_serialization.py",
    "tests/test_phase2_properties.py",
    "scripts/generate_phase2_matrix.py",
    "scripts/run_phase2_properties.py",
    "scripts/validate_phase2.py",
    ".github/workflows/phase2.yml",
    "docs/refactor/domain/phase2-domain-contract.md",
    "docs/refactor/domain/phase2-state-event-matrix.md",
    "docs/refactor/phases/phase-02-typed-domain-and-reducer.md",
    "docs/refactor/evidence/phase-02/README.md",
    "docs/refactor/evidence/phase-02/red-test-plan.md",
    "docs/refactor/evidence/phase-02/red-result.json",
    "docs/refactor/evidence/phase-02/property-result.json",
    "docs/refactor/evidence/phase-02/performance-result.json",
    "docs/refactor/evidence/phase-02/mutation-result.json",
    "docs/refactor/evidence/phase-02/domain-manifest.json",
    "docs/refactor/evidence/phase-02/adversarial-review.md",
    "docs/refactor/evidence/phase-02/validation-result.json",
    "docs/refactor/evidence/phase-02/SHA256SUMS",
)
FORBIDDEN_IMPORTS = {
    "aiohttp",
    "asyncio",
    "boto3",
    "fastapi",
    "http",
    "os",
    "pathlib",
    "psycopg",
    "redis",
    "requests",
    "socket",
    "sqlite3",
    "sqlalchemy",
    "subprocess",
    "supabase",
    "urllib",
}
FORBIDDEN_CALLS = {"compile", "eval", "exec", "input", "open", "print"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_required(failures: list[str]) -> None:
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            failures.append(f"missing required file: {relative}")


def check_git_index(failures: list[str]) -> None:
    completed = subprocess.run(
        ["git", "ls-files", "--cached"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        failures.append("cannot inspect git index")
        return
    indexed = set(completed.stdout.splitlines())
    for relative in sorted(set(REQUIRED) - indexed):
        failures.append(f"required Phase 2 file is not tracked/staged: {relative}")


def check_domain_purity(failures: list[str]) -> dict[str, Any]:
    files = sorted(DOMAIN.glob("*.py"))
    imported: set[str] = set()
    forbidden_calls: list[str] = []
    command_constructors: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.lstrip(".").split(".", 1)[0]
                if module:
                    imported.add(module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                    forbidden_calls.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.id}"
                    )
                if isinstance(node.func, ast.Name) and node.func.id == "ReservationCommand":
                    command_constructors.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    forbidden_imports = sorted(imported.intersection(FORBIDDEN_IMPORTS))
    if forbidden_imports:
        failures.append(f"external capability imports in domain: {forbidden_imports}")
    if forbidden_calls:
        failures.append(f"I/O or dynamic calls in domain: {forbidden_calls}")
    if (
        len(command_constructors) != 1
        or not command_constructors[0].startswith("reservation_domain/reducer.py:")
    ):
        failures.append(
            "ReservationCommand construction must have exactly one owner in reducer.py; "
            f"found {command_constructors}"
        )
    types_source = (DOMAIN / "types.py").read_text(encoding="utf-8")
    if "Any" in types_source or "dict[str, Any]" in types_source:
        failures.append("domain public types must not contain Any metadata bags")
    legacy_markers = ("chapada-leads-hermes", "manychat", "cloudbeds", "bokun")
    marker_hits = [
        marker
        for marker in legacy_markers
        if any(marker in path.read_text(encoding="utf-8").lower() for path in files)
    ]
    if marker_hits:
        failures.append(f"domain is coupled to runtime/provider markers: {marker_hits}")
    return {
        "python_files": len(files),
        "imports": sorted(imported),
        "forbidden_imports": forbidden_imports,
        "forbidden_calls": forbidden_calls,
        "command_constructor_owners": command_constructors,
    }


def check_matrix(failures: list[str]) -> dict[str, int]:
    matrix = transition_matrix()
    states = {item.TYPE for item in STATE_TYPES}
    events = {item.TYPE for item in EVENT_TYPES}
    if set(matrix) != states:
        failures.append("transition matrix state set mismatch")
    for state_tag, row in matrix.items():
        if set(row) != events:
            failures.append(f"transition matrix event set mismatch for {state_tag}")
        invalid = {value for value in row.values() if value not in {"evaluate", "ignore"}}
        if invalid:
            failures.append(f"invalid matrix policies for {state_tag}: {sorted(invalid)}")
    expected = render_matrix()
    if not MATRIX.is_file() or MATRIX.read_text(encoding="utf-8") != expected:
        failures.append("Phase 2 state/event matrix is stale; regenerate it")
    if len(STATE_TYPES) < 15:
        failures.append("expected at least 15 discriminated states")
    if len(EVENT_TYPES) < 12:
        failures.append("expected at least 12 discriminated events")
    return {
        "states": len(STATE_TYPES),
        "events": len(EVENT_TYPES),
        "pairs": len(STATE_TYPES) * len(EVENT_TYPES),
        "evaluate_pairs": sum(
            value == "evaluate" for row in matrix.values() for value in row.values()
        ),
        "ignore_pairs": sum(
            value == "ignore" for row in matrix.values() for value in row.values()
        ),
    }


def check_manifest(failures: list[str]) -> dict[str, Any]:
    path = EVIDENCE / "domain-manifest.json"
    try:
        actual = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read domain manifest: {exc}")
        return {}
    expected = build_domain_manifest()
    if actual != expected:
        failures.append("domain-manifest.json is stale; regenerate it")
    return {
        "files": len(actual.get("files", [])),
        "state_count": actual.get("state_count"),
        "event_count": actual.get("event_count"),
        "state_event_pairs": actual.get("state_event_pairs"),
    }


def check_property_result(failures: list[str]) -> dict[str, Any]:
    path = EVIDENCE / "property-result.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read property result: {exc}")
        return {}
    expected = {
        "schema_version": 1,
        "phase": "phase-02-typed-domain-and-reducer",
        "mode": "gate",
        "sequences": 100_000,
        "max_events": 20,
        "seed": 20_260_718,
        "transitions": 2_000_000,
        "exceptions": 0,
        "premature_commands": 0,
        "second_commands": 0,
        "duplicate_reemissions": 0,
        "conflicting_duplicate_acceptances": 0,
        "missing_authorized_commands": 0,
        "out_of_order_policy_violations": 0,
        "violations": [],
        "gate_failures": [],
        "result": "passed",
    }
    for key, value in expected.items():
        if report.get(key) != value:
            failures.append(
                f"property result mismatch for {key}: expected {value!r}, got {report.get(key)!r}"
            )
    if sum(int(report.get(key, 0)) for key in ("applied", "ignored", "rejected")) != report.get(
        "transitions"
    ):
        failures.append("property transition classification counts do not sum to total")
    for key in (
        "authorized_accepts",
        "out_of_order_probes",
        "lookup_positive_cases",
        "lookup_negative_cases",
        "lookup_expired_cases",
        "lookup_unavailable_cases",
        "lookup_multi_offer_cases",
    ):
        value = report.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            failures.append(f"property coverage counter must be positive: {key}")
    return report


def check_mutation_result(failures: list[str]) -> dict[str, Any]:
    path = EVIDENCE / "mutation-result.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read mutation result: {exc}")
        return {}
    mutants = report.get("mutants", [])
    if report.get("all_killed") is not True or len(mutants) != 11:
        failures.append("mutation evidence must contain eleven killed mutants")
    if any(
        item.get("killed") is not True or item.get("exit_code") == 0
        for item in mutants
    ):
        failures.append("every mutation must be killed by a non-zero test result")
    return report


def check_performance_result(failures: list[str]) -> dict[str, Any]:
    path = EVIDENCE / "performance-result.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read performance result: {exc}")
        return {}
    if (
        report.get("schema_version") != 1
        or report.get("phase") != "phase-02-typed-domain-and-reducer"
        or report.get("exit_code") != 0
        or report.get("result") != "passed"
    ):
        failures.append("performance evidence has an invalid result envelope")
    elapsed = report.get("elapsed_seconds")
    timeout = report.get("ci_timeout_seconds")
    rss = report.get("max_rss_kb")
    if (
        not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not 0 < elapsed <= 600
    ):
        failures.append("performance duration must be positive and within CI timeout")
    if (
        timeout != 600
        or not isinstance(rss, int)
        or isinstance(rss, bool)
        or rss < 1
    ):
        failures.append("performance evidence must record timeout and positive RSS")
    hashes = report.get("hashes", {})
    expected_hashes = {
        "domain_manifest_sha256": sha256(EVIDENCE / "domain-manifest.json"),
        "runner_sha256": sha256(ROOT / "scripts/run_phase2_properties.py"),
        "properties_sha256": sha256(ROOT / "reservation_domain/properties.py"),
    }
    if hashes != expected_hashes:
        failures.append("performance evidence hashes do not match measured sources")
    return report


def check_red_evidence(failures: list[str]) -> dict[str, Any]:
    try:
        result = json.loads((EVIDENCE / "red-result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read RED result: {exc}")
        return {}
    if result.get("exit_code") == 0 or result.get("expected_failure") is not True:
        failures.append("RED evidence must record an expected non-zero test run")
    if result.get("missing_module") != "reservation_domain":
        failures.append("RED evidence does not prove the domain package was absent")
    return result


def check_sums(failures: list[str]) -> int:
    path = EVIDENCE / "SHA256SUMS"
    if not path.is_file():
        return 0
    checked = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            failures.append(f"malformed Phase 2 SHA256SUMS line {line_number}")
            continue
        target = ROOT / relative
        if not target.is_file():
            failures.append(f"Phase 2 hash target missing: {relative}")
            continue
        if sha256(target) != expected:
            failures.append(f"Phase 2 hash mismatch: {relative}")
        checked += 1
    if checked < 12:
        failures.append("Phase 2 SHA256SUMS must cover at least twelve artifacts")
    return checked


def main() -> int:
    failures: list[str] = []
    check_required(failures)
    check_git_index(failures)
    purity = check_domain_purity(failures)
    matrix = check_matrix(failures)
    manifest = check_manifest(failures)
    properties = check_property_result(failures)
    mutation = check_mutation_result(failures)
    performance = check_performance_result(failures)
    red = check_red_evidence(failures)
    hashes = check_sums(failures)
    scanned = check_secrets_and_pii(failures)
    links = check_markdown_links(failures)
    if SCHEMA_VERSION != 1:
        failures.append("unexpected domain schema version")
    summary = {
        "status": "failed" if failures else "ok",
        "phase": "phase-02-typed-domain-and-reducer",
        "schema_version": SCHEMA_VERSION,
        "purity": purity,
        "matrix": matrix,
        "manifest": manifest,
        "properties": {
            key: properties.get(key)
            for key in (
                "sequences",
                "transitions",
                "applied",
                "ignored",
                "rejected",
                "exceptions",
                "premature_commands",
                "second_commands",
                "duplicate_reemissions",
                "conflicting_duplicate_acceptances",
                "authorized_accepts",
                "missing_authorized_commands",
                "out_of_order_probes",
                "out_of_order_policy_violations",
                "lookup_positive_cases",
                "lookup_negative_cases",
                "lookup_expired_cases",
                "lookup_unavailable_cases",
                "lookup_multi_offer_cases",
                "result",
            )
        },
        "mutation": {
            "mutants": len(mutation.get("mutants", [])),
            "all_killed": mutation.get("all_killed"),
        },
        "performance": {
            key: performance.get(key)
            for key in ("elapsed_seconds", "max_rss_kb", "exit_code", "result")
        },
        "red": {
            "expected_failure": red.get("expected_failure"),
            "exit_code": red.get("exit_code"),
            "failure_class": red.get("failure_class"),
        },
        "evidence_hashes_checked": hashes,
        "text_files_scanned": scanned,
        "relative_links_checked": links,
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
