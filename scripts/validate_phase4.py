#!/usr/bin/env python3
"""Validate Phase 4 summary/confirmation contracts, evidence and purity."""

from __future__ import annotations

import ast
import hashlib
import inspect
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

from reservation_confirmation import DecisionCandidate, classify_and_bind  # noqa: E402
from reservation_domain import transition_matrix  # noqa: E402
from scripts.generate_phase4_manifest import (  # noqa: E402
    build_fixture_manifest,
    build_package_manifest,
)
from scripts.run_phase4_mutations import MUTANTS  # noqa: E402
from scripts.validate_phase0 import check_markdown_links, check_secrets_and_pii  # noqa: E402

PHASE = "phase-04-single-summary-and-confirmation"
PACKAGE = ROOT / "reservation_confirmation"
EVIDENCE = ROOT / "docs" / "refactor" / "evidence" / "phase-04"
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
RED_NAMES = (
    "red-result-types.json",
    "red-result-renderer.json",
    "red-result-classifier.json",
    "red-result-adjustment.json",
    "red-result-replays.json",
    "red-result-properties.json",
    "red-result-mutations.json",
    "red-result-late-classifier.json",
    "red-result-property-baseline-review.json",
    "red-result-confirmation-identity-review.json",
    "red-result-public-property-api-review.json",
)
REQUIRED = (
    "reservation_confirmation/README.md",
    "reservation_confirmation/__init__.py",
    "reservation_confirmation/types.py",
    "reservation_confirmation/renderer.py",
    "reservation_confirmation/presentation.py",
    "reservation_confirmation/classifier.py",
    "reservation_confirmation/binding.py",
    "reservation_confirmation/properties.py",
    "tests/test_phase4_types.py",
    "tests/test_phase4_renderer.py",
    "tests/test_phase4_classifier.py",
    "tests/test_phase4_adjustment_state.py",
    "tests/test_phase4_replays.py",
    "tests/test_phase4_properties.py",
    "tests/test_phase4_mutation_runner.py",
    "tests/fixtures/phase4/confirmation-corpus.json",
    "scripts/run_phase4_properties.py",
    "scripts/run_phase4_mutations.py",
    "scripts/generate_phase4_manifest.py",
    "scripts/validate_phase4.py",
    ".github/workflows/phase4.yml",
    "docs/refactor/phases/phase-04-single-summary-and-confirmation.md",
    "docs/superpowers/specs/2026-07-19-phase-4-summary-confirmation-design.md",
    "docs/superpowers/plans/2026-07-19-phase-4-summary-confirmation.md",
    "docs/refactor/evidence/phase-04/README.md",
    "docs/refactor/evidence/phase-04/entry-baseline.json",
    *tuple(f"docs/refactor/evidence/phase-04/{name}" for name in RED_NAMES),
    "docs/refactor/evidence/phase-04/property-result.json",
    "docs/refactor/evidence/phase-04/mutation-result.json",
    "docs/refactor/evidence/phase-04/performance-result.json",
    "docs/refactor/evidence/phase-04/source-map.json",
    "docs/refactor/evidence/phase-04/package-manifest.json",
    "docs/refactor/evidence/phase-04/fixture-manifest.json",
    "docs/refactor/evidence/phase-04/adversarial-review.md",
    "docs/refactor/evidence/phase-04/validation-result.json",
    "docs/refactor/evidence/phase-04/SHA256SUMS",
)
FORBIDDEN_IMPORTS = {
    "aiohttp",
    "anthropic",
    "asyncio",
    "boto3",
    "fastapi",
    "http",
    "openai",
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


def check_required(failures: list[str]) -> None:
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            failures.append(f"missing required file: {relative}")


def check_git_index(failures: list[str]) -> None:
    completed = subprocess.run(
        ["git", "ls-files", "--cached"], cwd=ROOT, capture_output=True, text=True
    )
    if completed.returncode:
        failures.append("cannot inspect git index")
        return
    indexed = set(completed.stdout.splitlines())
    for relative in sorted(set(REQUIRED) - indexed):
        failures.append(f"required Phase 4 file is not tracked/staged: {relative}")


def check_previous_validators(failures: list[str]) -> dict[str, str]:
    commands = (
        ("phase0", [sys.executable, "scripts/validate_phase0.py"], {}),
        (
            "phase1",
            [sys.executable, "scripts/validate_phase1.py"],
            {"PHASE1_LEGACY_SOURCE": "/path-not-present-in-ci"},
        ),
        ("phase2", [sys.executable, "scripts/validate_phase2.py"], {}),
        ("phase3", [sys.executable, "scripts/validate_phase3.py"], {}),
    )
    statuses: dict[str, str] = {}
    for name, command, override in commands:
        env = dict(os.environ)
        env.update(override)
        completed = subprocess.run(
            command,
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
        statuses[name] = status
        if completed.returncode or status != "ok":
            failures.append(f"previous validator failed: {name}:{status}")
    return statuses


def check_purity(failures: list[str]) -> dict[str, Any]:
    files = sorted(PACKAGE.glob("*.py"))
    imports: set[str] = set()
    calls: list[str] = []
    marker_hits: list[str] = []
    markers = ("openai", "anthropic", "manychat", "http://", "https://", "api_key", "access_token")
    for path in files:
        source = path.read_text(encoding="utf-8")
        lower = source.lower()
        marker_hits.extend(
            f"{path.relative_to(ROOT)}:{marker}" for marker in markers if marker in lower
        )
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.lstrip(".").split(".", 1)[0]
                if module:
                    imports.add(module)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_CALLS:
                    calls.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.id}")
    forbidden = sorted(imports.intersection(FORBIDDEN_IMPORTS))
    if forbidden:
        failures.append(f"external capability imports in confirmation package: {forbidden}")
    if calls:
        failures.append(f"I/O or dynamic calls in confirmation package: {calls}")
    if marker_hits:
        failures.append(f"runtime/auth markers in confirmation package: {marker_hits}")
    candidate_fields = tuple(DecisionCandidate.__dataclass_fields__)
    if candidate_fields != (
        "decision",
        "classifier_id",
        "classifier_version",
        "confidence_basis_points",
        "evidence_codes",
    ):
        failures.append("DecisionCandidate field universe changed")
    forbidden_parameters = {
        "target_draft_version",
        "subject_signature",
        "offer_id",
        "provider_ref",
        "operation",
    }
    binding_parameters = set(inspect.signature(classify_and_bind).parameters)
    if binding_parameters.intersection(forbidden_parameters):
        failures.append("untrusted binding accepts commercial target parameters")
    return {
        "python_files": len(files),
        "imports": sorted(imports),
        "forbidden_imports": forbidden,
        "forbidden_calls": calls,
        "runtime_marker_hits": marker_hits,
        "decision_candidate_fields": list(candidate_fields),
    }


def check_manifests(failures: list[str]) -> dict[str, int]:
    outputs = {}
    for name, expected, key in (
        ("package-manifest.json", build_package_manifest(), "python_file_count"),
        ("fixture-manifest.json", build_fixture_manifest(), "fixture_count"),
    ):
        try:
            actual = _load_json(EVIDENCE / name)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"cannot read {name}: {exc}")
            continue
        if actual != expected:
            failures.append(f"{name} is stale; regenerate it")
        outputs[key] = int(actual.get(key, 0))
    fixture = build_fixture_manifest()
    if (
        fixture.get("synthetic_sanitized_only") is not True
        or fixture.get("fixture_count") != 1
        or fixture.get("case_count") != 32
        or fixture.get("locales") != ["en", "pt_BR"]
        or fixture.get("categories")
        != ["adjust", "ambiguous", "colloquial", "contextual", "explicit", "negative"]
    ):
        failures.append("fixture corpus coverage mismatch")
    return outputs


def check_properties(failures: list[str]) -> dict[str, Any]:
    try:
        payload = _load_json(EVIDENCE / "property-result.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read property result: {exc}")
        return {}
    if (
        payload.get("schema_version") != 1
        or payload.get("phase") != PHASE
        or payload.get("mode") != "gate"
        or payload.get("result") != "passed"
        or payload.get("configuration")
        != {"cases": 50_000, "minimum_gate_cases": 50_000, "seed": 20_260_719}
    ):
        failures.append("property gate envelope mismatch")
    report = payload.get("report", {})
    if (
        report.get("cases") != 50_000
        or report.get("seed") != 20_260_719
        or report.get("passed") is not True
        or report.get("authorized_accepts") != 12_500
        or report.get("commands_emitted") != 12_500
        or report.get("cloudbeds_cases", 0) + report.get("bokun_cases", 0) != 50_000
        or report.get("pt_cases", 0) + report.get("en_cases", 0) != 50_000
        or sum(report.get("locale_counts", {}).values()) != 50_000
        or sum(report.get("decision_counts", {}).values()) != 50_000
    ):
        failures.append("property coverage/counter mismatch")
    for key in (
        "premature_commands",
        "second_commands",
        "duplicate_reemissions",
        "stale_confirmation_acceptances",
        "adjustment_disarm_failures",
        "context_failure_events",
        "false_commands",
        "missing_required_commands",
        "unexpected_exceptions",
    ):
        if report.get(key) != 0:
            failures.append(f"property safety counter must be zero: {key}")
    for key in (
        "duplicate_probes",
        "adjustment_probes",
        "context_failure_probes",
        "artifact_tamper_probes",
        "classifier_failure_probes",
        "cloudbeds_cases",
        "bokun_cases",
        "explicit_cases",
        "colloquial_cases",
        "contextual_cases",
        "negative_cases",
        "ambiguous_cases",
        "adjust_cases",
        "deterministic_summaries",
        "private_field_safe_summaries",
        "posterior_accept_commands",
        "same_time_rejections",
        "stale_version_rejections",
        "context_free_rejections",
        "adjustment_disarms",
        "semantic_version_increments",
        "noop_adjustment_rejections",
        "duplicate_zero_additional",
        "classifier_error_rejections",
    ):
        if not isinstance(report.get(key), int) or report.get(key) < 1:
            failures.append(f"property coverage counter must be positive: {key}")
    if report.get("violations") != []:
        failures.append("property evidence contains violations")
    return report


def check_mutations(failures: list[str]) -> dict[str, Any]:
    try:
        payload = _load_json(EVIDENCE / "mutation-result.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read mutation result: {exc}")
        return {}
    expected = [
        {
            "name": mutant.name,
            "path": mutant.path,
            "test": mutant.test,
            "exit_code": 1,
            "killed": True,
        }
        for mutant in MUTANTS
    ]
    if (
        payload.get("phase") != PHASE
        or payload.get("all_killed") is not True
        or payload.get("mutant_count") != len(MUTANTS)
        or payload.get("mutants") != expected
    ):
        failures.append("mutation evidence does not match closed 19-mutant catalog")
    return payload


def check_performance(failures: list[str]) -> dict[str, Any]:
    try:
        payload = _load_json(EVIDENCE / "performance-result.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read performance result: {exc}")
        return {}
    elapsed = payload.get("elapsed_seconds")
    rss = payload.get("max_rss_kb")
    expected_hashes = {
        "package_manifest_sha256": sha256(EVIDENCE / "package-manifest.json"),
        "phase3_lookup_manifest_sha256": sha256(
            ROOT / "docs/refactor/evidence/phase-03/lookup-manifest.json"
        ),
        "runner_sha256": sha256(ROOT / "scripts/run_phase4_properties.py"),
    }
    if (
        payload.get("phase") != PHASE
        or payload.get("exit_code") != 0
        or payload.get("result") != "passed"
        or payload.get("cases") != 50_000
        or payload.get("seed") != 20_260_719
        or isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not 0 < elapsed <= 600
        or isinstance(rss, bool)
        or not isinstance(rss, int)
        or rss < 1
        or payload.get("ci_timeout_seconds") != 600
        or payload.get("input_hashes") != expected_hashes
    ):
        failures.append("performance evidence envelope mismatch")
    return payload


def check_red(failures: list[str]) -> dict[str, int]:
    valid = 0
    for name in RED_NAMES:
        try:
            payload = _load_json(EVIDENCE / name)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"cannot read {name}: {exc}")
            continue
        if (
            payload.get("schema_version") != 1
            or payload.get("phase") != PHASE
            or payload.get("exit_code") == 0
            or payload.get("expected_failure") is not True
            or not HASH_RE.fullmatch(str(payload.get("output_sha256", "")))
        ):
            failures.append(f"invalid RED evidence: {name}")
        else:
            valid += 1
    return {"expected": len(RED_NAMES), "valid": valid}


def check_source_map(failures: list[str]) -> dict[str, Any]:
    try:
        payload = _load_json(EVIDENCE / "source-map.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read source map: {exc}")
        return {}
    legacy = payload.get("legacy_readonly", {})
    dependency = payload.get("phase3_dependency", {})
    if (
        payload.get("phase") != PHASE
        or payload.get("classification") != "source_informed_contract"
        or legacy
        != {
            "head": "57408d8b2040399bc25ee7957505208079458884",
            "status_entries": 80,
            "status_sha256": "77c02eb09d415e01f45515ccacf9bc7b93f34d1d8a66aafc0af905d8734c940b",
        }
        or dependency
        != {
            "path": "docs/refactor/evidence/phase-03/lookup-manifest.json",
            "sha256": sha256(
                ROOT / "docs/refactor/evidence/phase-03/lookup-manifest.json"
            ),
            "purpose": "Cloudbeds/Bokun in-memory property baseline",
            "validated_by": "scripts/validate_phase3.py",
        }
    ):
        failures.append("source map envelope/baseline mismatch")
    symbols = payload.get("symbols", [])
    if len(symbols) != 4 or any(
        not isinstance(item.get("line"), int)
        or item.get("line", 0) < 1
        or not HASH_RE.fullmatch(str(item.get("source_sha256", "")))
        or str(item.get("source_path", "")).startswith("/")
        for item in symbols
    ):
        failures.append("source map symbols invalid")
    claims = payload.get("claims", {})
    for key in (
        "legacy_imported",
        "legacy_runtime_executed",
        "provider_network_executed",
        "provider_write_executed",
        "llm_provider_executed",
        "hermes_runtime_integrated",
    ):
        if claims.get(key) is not False:
            failures.append(f"source map overclaims external execution: {key}")
    if claims.get("fixtures_synthetic_sanitized") is not True:
        failures.append("source map must assert synthetic sanitized fixtures")
    return {"symbols": len(symbols), "legacy_head": legacy.get("head")}


def check_domain_matrix(failures: list[str]) -> dict[str, int]:
    matrix = transition_matrix()
    states = len(matrix)
    pairs = sum(len(row) for row in matrix.values())
    if states != 16 or pairs != 192:
        failures.append("domain matrix must contain 16 states and 192 explicit pairs")
    if matrix.get("awaiting_adjustment", {}).get("confirmation_received") != "ignore":
        failures.append("awaiting_adjustment must ignore confirmation_received")
    return {"states": states, "pairs": pairs}


def check_validation_record(failures: list[str]) -> str | None:
    try:
        payload = _load_json(EVIDENCE / "validation-result.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read validation result: {exc}")
        return None
    if payload.get("phase") != PHASE or payload.get("status") != "ok":
        failures.append("validation-result.json must record an ok Phase 4 run")
    return payload.get("status")


def check_sums(failures: list[str]) -> int:
    path = EVIDENCE / "SHA256SUMS"
    if not path.is_file():
        failures.append("missing Phase 4 SHA256SUMS")
        return 0
    checked = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            failures.append(f"malformed Phase 4 SHA256SUMS line {line_number}")
            continue
        target = ROOT / relative
        if not target.is_file():
            failures.append(f"Phase 4 hash target missing: {relative}")
        elif sha256(target) != expected:
            failures.append(f"Phase 4 hash mismatch: {relative}")
        checked += 1
    if checked < 45:
        failures.append("Phase 4 SHA256SUMS must cover at least 45 artifacts")
    return checked


def main() -> int:
    failures: list[str] = []
    check_required(failures)
    check_git_index(failures)
    previous = check_previous_validators(failures)
    purity = check_purity(failures)
    manifests = check_manifests(failures)
    properties = check_properties(failures)
    mutations = check_mutations(failures)
    performance = check_performance(failures)
    red = check_red(failures)
    source_map = check_source_map(failures)
    matrix = check_domain_matrix(failures)
    record = check_validation_record(failures)
    sums = check_sums(failures)
    scanned = check_secrets_and_pii(failures)
    links = check_markdown_links(failures)
    summary = {
        "schema_version": 1,
        "phase": PHASE,
        "status": "failed" if failures else "ok",
        "previous_validators": previous,
        "purity": purity,
        "manifests": manifests,
        "properties": {
            key: properties.get(key)
            for key in (
                "cases",
                "authorized_accepts",
                "commands_emitted",
                "duplicate_probes",
                "adjustment_probes",
                "context_failure_probes",
                "premature_commands",
                "second_commands",
                "stale_confirmation_acceptances",
                "unexpected_exceptions",
            )
        },
        "mutation": {
            "mutants": len(mutations.get("mutants", [])),
            "all_killed": mutations.get("all_killed"),
        },
        "performance": {
            key: performance.get(key)
            for key in ("elapsed_seconds", "max_rss_kb", "exit_code", "result")
        },
        "red": red,
        "source_map": source_map,
        "domain_matrix": matrix,
        "validation_record": record,
        "evidence_hashes_checked": sums,
        "text_files_scanned": scanned,
        "relative_links_checked": links,
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
