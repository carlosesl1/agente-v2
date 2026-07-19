#!/usr/bin/env python3
"""Validate Phase 3 lookup boundaries, identity, evidence and purity."""

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

from scripts.generate_phase3_manifest import (  # noqa: E402
    build_fixture_manifest,
    build_lookup_manifest,
)
from scripts.run_phase3_mutations import MUTANTS  # noqa: E402
from scripts.validate_phase0 import (  # noqa: E402
    check_markdown_links,
    check_secrets_and_pii,
)

PHASE = "phase-03-lookups-and-offer-snapshots"
PACKAGE = ROOT / "reservation_lookup"
EVIDENCE = ROOT / "docs" / "refactor" / "evidence" / "phase-03"
REQUIRED = (
    "reservation_lookup/README.md",
    "reservation_lookup/__init__.py",
    "reservation_lookup/_common.py",
    "reservation_lookup/types.py",
    "reservation_lookup/identity.py",
    "reservation_lookup/cloudbeds.py",
    "reservation_lookup/bokun.py",
    "reservation_lookup/selection.py",
    "reservation_lookup/properties.py",
    "tests/test_phase3_lookup_types.py",
    "tests/test_phase3_cloudbeds_adapter.py",
    "tests/test_phase3_bokun_adapter.py",
    "tests/test_phase3_selection.py",
    "tests/test_phase3_properties.py",
    "tests/test_phase3_mutation_runner.py",
    "tests/fixtures/phase3/cloudbeds/available-room-types.json",
    "tests/fixtures/phase3/cloudbeds/rate-plans.json",
    "tests/fixtures/phase3/cloudbeds/no-availability.json",
    "tests/fixtures/phase3/cloudbeds/missing-rate-plan.json",
    "tests/fixtures/phase3/bokun/activity.json",
    "tests/fixtures/phase3/bokun/availabilities.json",
    "tests/fixtures/phase3/bokun/no-availability.json",
    "tests/fixtures/phase3/bokun/mismatched-activity.json",
    "scripts/run_phase3_properties.py",
    "scripts/run_phase3_mutations.py",
    "scripts/generate_phase3_manifest.py",
    "scripts/validate_phase3.py",
    ".github/workflows/phase3.yml",
    "docs/refactor/phases/phase-03-lookups-and-offer-snapshots.md",
    "docs/superpowers/specs/2026-07-18-phase-3-lookup-adapters-design.md",
    "docs/superpowers/plans/2026-07-18-phase-3-lookup-adapters.md",
    "docs/refactor/evidence/phase-03/README.md",
    "docs/refactor/evidence/phase-03/entry-baseline.json",
    "docs/refactor/evidence/phase-03/red-result-types.json",
    "docs/refactor/evidence/phase-03/red-result-cloudbeds.json",
    "docs/refactor/evidence/phase-03/red-result-bokun.json",
    "docs/refactor/evidence/phase-03/red-result-selection.json",
    "docs/refactor/evidence/phase-03/red-result-properties.json",
    "docs/refactor/evidence/phase-03/property-result.json",
    "docs/refactor/evidence/phase-03/mutation-result.json",
    "docs/refactor/evidence/phase-03/performance-result.json",
    "docs/refactor/evidence/phase-03/source-map.json",
    "docs/refactor/evidence/phase-03/lookup-manifest.json",
    "docs/refactor/evidence/phase-03/fixture-manifest.json",
    "docs/refactor/evidence/phase-03/adversarial-review.md",
    "docs/refactor/evidence/phase-03/validation-result.json",
    "docs/refactor/evidence/phase-03/SHA256SUMS",
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
HASH_RE = re.compile(r"^[a-f0-9]{64}$")


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
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        failures.append("cannot inspect git index")
        return
    indexed = set(completed.stdout.splitlines())
    for relative in sorted(set(REQUIRED) - indexed):
        failures.append(f"required Phase 3 file is not tracked/staged: {relative}")


def check_previous_validators(failures: list[str]) -> dict[str, str]:
    commands = (
        ("phase0", [sys.executable, "scripts/validate_phase0.py"], {}),
        (
            "phase1",
            [sys.executable, "scripts/validate_phase1.py"],
            {"PHASE1_LEGACY_SOURCE": "/path-not-present-in-ci"},
        ),
        ("phase2", [sys.executable, "scripts/validate_phase2.py"], {}),
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
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {}
        status = str(payload.get("status") or "invalid_output")
        statuses[name] = status
        if completed.returncode != 0 or status != "ok":
            failures.append(f"previous validator failed: {name}:{status}")
    return statuses


def check_package_purity(failures: list[str]) -> dict[str, Any]:
    files = sorted(PACKAGE.glob("*.py"))
    imported: set[str] = set()
    forbidden_calls: list[str] = []
    transport_sends: list[str] = []
    transport_classes: list[str] = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.lstrip(".").split(".", 1)[0]
                if module:
                    imported.add(module)
            elif isinstance(node, ast.ClassDef) and node.name.endswith("Transport"):
                transport_classes.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                    forbidden_calls.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.id}"
                    )
                if isinstance(node.func, ast.Attribute) and node.func.attr == "send":
                    transport_sends.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    forbidden_imports = sorted(imported.intersection(FORBIDDEN_IMPORTS))
    if forbidden_imports:
        failures.append(f"external capability imports in lookup package: {forbidden_imports}")
    if forbidden_calls:
        failures.append(f"I/O or dynamic calls in lookup package: {forbidden_calls}")
    if (
        len(transport_classes) != 1
        or not transport_classes[0].startswith("reservation_lookup/types.py:")
        or not transport_classes[0].endswith(":ReadTransport")
    ):
        failures.append(f"unexpected transport implementations: {transport_classes}")
    if len(transport_sends) != 1 or not transport_sends[0].startswith(
        "reservation_lookup/_common.py:"
    ):
        failures.append(f"transport send owner mismatch: {transport_sends}")
    forbidden_markers = (
        "chapada-leads-hermes",
        "http://",
        "https://",
        "bearer ",
        "x-api-key",
        "api_key",
        "access_token",
    )
    marker_hits = [
        marker
        for marker in forbidden_markers
        if any(marker in path.read_text(encoding="utf-8").lower() for path in files)
    ]
    if marker_hits:
        failures.append(f"runtime/network/auth markers in lookup package: {marker_hits}")
    return {
        "python_files": len(files),
        "imports": sorted(imported),
        "forbidden_imports": forbidden_imports,
        "forbidden_calls": forbidden_calls,
        "transport_classes": transport_classes,
        "transport_send_owners": transport_sends,
    }


def check_manifests(failures: list[str]) -> dict[str, int]:
    outputs: dict[str, int] = {}
    for name, expected, count_key in (
        ("lookup-manifest.json", build_lookup_manifest(), "python_file_count"),
        ("fixture-manifest.json", build_fixture_manifest(), "fixture_count"),
    ):
        path = EVIDENCE / name
        try:
            actual = _load_json_strict(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"cannot read {name}: {exc}")
            continue
        if actual != expected:
            failures.append(f"{name} is stale; regenerate it")
        outputs[count_key] = int(actual.get(count_key, 0))
    fixture = build_fixture_manifest()
    if fixture.get("provider_counts") != {"bokun": 4, "cloudbeds": 4}:
        failures.append("fixture provider counts must be exactly four per provider")
    if fixture.get("synthetic_sanitized_only") is not True:
        failures.append("fixture manifest must assert synthetic sanitized data only")
    return outputs


def check_property_result(failures: list[str]) -> dict[str, Any]:
    try:
        payload = _load_json_strict(EVIDENCE / "property-result.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read property result: {exc}")
        return {}
    if payload.get("schema_version") != 1 or payload.get("phase") != PHASE:
        failures.append("property result envelope mismatch")
    if payload.get("mode") != "gate" or payload.get("result") != "passed":
        failures.append("property evidence must be a passed gate run")
    configuration = payload.get("configuration", {})
    if configuration != {
        "cases": 50_000,
        "minimum_gate_cases": 50_000,
        "seed": 20_260_718,
    }:
        failures.append("property gate configuration mismatch")
    report = payload.get("report", {})
    for key in (
        "cases",
        "positive_authorizations",
        "label_equivalence_cases",
        "executable_mutation_cases",
        "expired_cases",
        "zero_match_cases",
        "multiple_match_cases",
    ):
        if report.get(key) != 50_000:
            failures.append(f"property case counter mismatch: {key}")
    for key in (
        "false_authorizations",
        "missed_invalidations",
        "unexpected_exceptions",
    ):
        if report.get(key) != 0:
            failures.append(f"property safety counter must be zero: {key}")
    mutation_counts = report.get("mutation_counts", {})
    expected_mutations = {
        "amount": 6250,
        "availability": 6250,
        "currency": 6250,
        "date": 6250,
        "party": 6250,
        "provider": 6250,
        "provider_ref": 6250,
        "time": 6250,
    }
    if mutation_counts != expected_mutations:
        failures.append("property mutation coverage mismatch")
    if report.get("violations") != []:
        failures.append("property evidence contains violations")
    return report


def check_mutation_result(failures: list[str]) -> dict[str, Any]:
    try:
        payload = _load_json_strict(EVIDENCE / "mutation-result.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read mutation result: {exc}")
        return {}
    mutants = payload.get("mutants", [])
    expected_catalog = [
        {
            "name": mutant.name,
            "path": mutant.path,
            "test": mutant.test,
            "exit_code": 1,
            "killed": True,
        }
        for mutant in MUTANTS
    ]
    if mutants != expected_catalog:
        failures.append("mutation evidence does not match the closed catalog")
    if payload.get("all_killed") is not True or payload.get("mutant_count") != 13:
        failures.append("mutation evidence must contain thirteen killed mutants")
    if len(mutants) != 13 or any(
        item.get("killed") is not True or item.get("exit_code") == 0
        for item in mutants
    ):
        failures.append("every Phase 3 mutant must be killed")
    return payload


def check_performance_result(failures: list[str]) -> dict[str, Any]:
    try:
        payload = _load_json_strict(EVIDENCE / "performance-result.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read performance result: {exc}")
        return {}
    if (
        payload.get("schema_version") != 1
        or payload.get("phase") != PHASE
        or payload.get("exit_code") != 0
        or payload.get("result") != "passed"
        or payload.get("cases") != 50_000
        or payload.get("seed") != 20_260_718
    ):
        failures.append("performance evidence envelope mismatch")
    elapsed = payload.get("elapsed_seconds")
    rss = payload.get("max_rss_kb")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not 0 < elapsed <= 600
    ):
        failures.append("performance duration must be within CI timeout")
    if isinstance(rss, bool) or not isinstance(rss, int) or rss < 1:
        failures.append("performance RSS must be positive")
    return payload


def check_red_results(failures: list[str]) -> dict[str, int]:
    names = (
        "red-result-types.json",
        "red-result-cloudbeds.json",
        "red-result-bokun.json",
        "red-result-selection.json",
        "red-result-properties.json",
    )
    valid = 0
    for name in names:
        try:
            payload = _load_json_strict(EVIDENCE / name)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"cannot read {name}: {exc}")
            continue
        if (
            payload.get("schema_version") != 1
            or payload.get("phase") != PHASE
            or payload.get("exit_code") == 0
            or payload.get("result") != "red_confirmed"
            or "ModuleNotFoundError" not in str(payload.get("expected_failure"))
        ):
            failures.append(f"invalid RED evidence: {name}")
        else:
            valid += 1
    return {"expected": len(names), "valid": valid}


def check_source_map(failures: list[str]) -> dict[str, Any]:
    try:
        payload = _load_json_strict(EVIDENCE / "source-map.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read source map: {exc}")
        return {}
    if payload.get("phase") != PHASE or payload.get("classification") != "source_informed_contract":
        failures.append("source map envelope mismatch")
    legacy = payload.get("legacy_readonly", {})
    if legacy != {
        "head": "57408d8b2040399bc25ee7957505208079458884",
        "status_entries": 80,
        "status_sha256": "77c02eb09d415e01f45515ccacf9bc7b93f34d1d8a66aafc0af905d8734c940b",
    }:
        failures.append("source map legacy baseline mismatch")
    symbols = payload.get("symbols", [])
    if len(symbols) != 6:
        failures.append("source map must contain six symbols")
    for item in symbols:
        if (
            not isinstance(item.get("line"), int)
            or item.get("line", 0) < 1
            or not HASH_RE.fullmatch(str(item.get("source_sha256", "")))
            or str(item.get("source_path", "")).startswith("/")
        ):
            failures.append("invalid source map symbol")
    claims = payload.get("claims", {})
    expected_false = (
        "legacy_imported",
        "legacy_runtime_executed",
        "provider_network_executed",
        "provider_payload_captured",
    )
    if any(claims.get(key) is not False for key in expected_false):
        failures.append("source map overclaims external execution")
    if claims.get("fixtures_synthetic_sanitized") is not True:
        failures.append("source map must classify fixtures as synthetic")
    return {"symbols": len(symbols), "legacy_head": legacy.get("head")}


def check_validation_result(failures: list[str]) -> str | None:
    try:
        payload = _load_json_strict(EVIDENCE / "validation-result.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read validation result: {exc}")
        return None
    if payload.get("phase") != PHASE or payload.get("status") != "ok":
        failures.append("validation-result.json must record an ok Phase 3 run")
    return payload.get("status")


def check_sums(failures: list[str]) -> int:
    path = EVIDENCE / "SHA256SUMS"
    if not path.is_file():
        failures.append("missing Phase 3 SHA256SUMS")
        return 0
    checked = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            failures.append(f"malformed Phase 3 SHA256SUMS line {line_number}")
            continue
        target = ROOT / relative
        if not target.is_file():
            failures.append(f"Phase 3 hash target missing: {relative}")
            continue
        if sha256(target) != expected:
            failures.append(f"Phase 3 hash mismatch: {relative}")
        checked += 1
    if checked < 35:
        failures.append("Phase 3 SHA256SUMS must cover at least 35 artifacts")
    return checked


def _load_json_strict(path: Path) -> dict[str, Any]:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if type(payload) is not dict:
        raise ValueError("JSON root must be an object")
    return payload


def main() -> int:
    failures: list[str] = []
    check_required(failures)
    check_git_index(failures)
    previous = check_previous_validators(failures)
    purity = check_package_purity(failures)
    manifests = check_manifests(failures)
    properties = check_property_result(failures)
    mutation = check_mutation_result(failures)
    performance = check_performance_result(failures)
    red = check_red_results(failures)
    source_map = check_source_map(failures)
    validation_record = check_validation_result(failures)
    sums = check_sums(failures)
    scanned = check_secrets_and_pii(failures)
    links = check_markdown_links(failures)
    summary = {
        "status": "failed" if failures else "ok",
        "phase": PHASE,
        "previous_validators": previous,
        "purity": purity,
        "manifests": manifests,
        "properties": {
            key: properties.get(key)
            for key in (
                "cases",
                "positive_authorizations",
                "label_equivalence_cases",
                "executable_mutation_cases",
                "expired_cases",
                "zero_match_cases",
                "multiple_match_cases",
                "false_authorizations",
                "missed_invalidations",
                "unexpected_exceptions",
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
        "red": red,
        "source_map": source_map,
        "validation_record": validation_record,
        "evidence_hashes_checked": sums,
        "text_files_scanned": scanned,
        "relative_links_checked": links,
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
