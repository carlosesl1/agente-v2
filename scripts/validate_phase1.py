#!/usr/bin/env python3
"""Validate Phase 1 corpus, reports, safety gates and evidence."""

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

from characterization.harness import (  # noqa: E402
    FAULT_POINTS,
    INCIDENT_IDS,
    load_and_replay_all,
    scenario_paths,
)
from scripts.generate_phase1_reports import build_corpus_manifest, build_reports  # noqa: E402
from scripts.validate_phase0 import (  # noqa: E402
    check_markdown_links,
    check_secrets_and_pii,
)

CHARACTERIZATION = ROOT / "characterization"
EVIDENCE = ROOT / "docs" / "refactor" / "evidence" / "phase-01"
LEGACY = Path(os.environ.get("PHASE1_LEGACY_SOURCE", "/home/ubuntu/chapada-leads-hermes"))
REQUIRED = (
    "characterization/__init__.py",
    "characterization/README.md",
    "characterization/harness.py",
    "characterization/schema/scenario.schema.json",
    "characterization/fixtures/manychat/hostel-reservation.json",
    "characterization/fixtures/manychat/agency-reservation.json",
    "characterization/fixtures/manychat/handoff.json",
    "characterization/fixtures/manychat/duplicate-webhook.json",
    "characterization/fixtures/provider/hostel-available.json",
    "characterization/fixtures/provider/agency-available.json",
    "characterization/fixtures/provider/provider-rejects-synthetic-option.json",
    "characterization/fixtures/provider/composite-outcome.json",
    "tests/test_characterization_harness.py",
    "scripts/generate_phase1_reports.py",
    "scripts/validate_phase1.py",
    ".github/workflows/phase1.yml",
    "docs/refactor/phases/phase-01-incident-characterization.md",
    "docs/refactor/evidence/phase-01/README.md",
    "docs/refactor/evidence/phase-01/corpus-manifest.json",
    "docs/refactor/evidence/phase-01/incident-coverage.json",
    "docs/refactor/evidence/phase-01/source-map.json",
    "docs/refactor/evidence/phase-01/behavior-baseline.md",
    "docs/refactor/evidence/phase-01/classification-method.md",
    "docs/refactor/evidence/phase-01/source-readonly-verification.json",
    "docs/refactor/evidence/phase-01/validation-result.json",
    "docs/refactor/evidence/phase-01/SHA256SUMS",
)
FORBIDDEN_IMPORTS = {
    "aiohttp",
    "asyncio",
    "http",
    "requests",
    "socket",
    "sqlite3",
    "subprocess",
    "urllib",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_required(failures: list[str]) -> None:
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            failures.append(f"missing required file: {relative}")


def check_git_index(failures: list[str]) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--cached"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if tracked.returncode != 0:
        failures.append("cannot inspect git index")
        return
    indexed = set(tracked.stdout.splitlines())
    required = set(REQUIRED)
    required.update(str(path.relative_to(ROOT)) for path in scenario_paths(CHARACTERIZATION))
    for relative in sorted(required - indexed):
        failures.append(f"required Phase 1 file is not tracked/staged: {relative}")


def check_scenarios(failures: list[str]) -> dict[str, Any]:
    try:
        results = load_and_replay_all(CHARACTERIZATION)
    except Exception as exc:  # validator must summarize all failures
        failures.append(f"scenario replay failed: {exc}")
        return {}
    incident_ids = {result.incident_id for result in results}
    if incident_ids != set(INCIDENT_IDS):
        failures.append(f"incident coverage mismatch: {sorted(incident_ids)}")
    if len(results) < 30:
        failures.append("expected at least 30 scenarios including fault boundaries")
    fault_points = {point for result in results for point in result.fault_points}
    if fault_points != set(FAULT_POINTS):
        failures.append(f"fault boundary coverage mismatch: {sorted(fault_points)}")
    if any(not result.violations for result in results):
        failures.append("every characterization witness must detect at least one violation")
    return {
        "scenario_count": len(results),
        "incident_count": len(incident_ids),
        "reproduced": sum(result.classification == "reproduced" for result in results),
        "contract_characterized": sum(
            result.classification == "contract_characterized" for result in results
        ),
        "fault_points": sorted(fault_points),
    }


def check_generated_reports(failures: list[str]) -> None:
    try:
        expected_coverage, expected_source_map = build_reports()
        expected_corpus_manifest = build_corpus_manifest()
        actual_coverage = json.loads(
            (EVIDENCE / "incident-coverage.json").read_text(encoding="utf-8")
        )
        actual_source_map = json.loads(
            (EVIDENCE / "source-map.json").read_text(encoding="utf-8")
        )
        actual_corpus_manifest = json.loads(
            (EVIDENCE / "corpus-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        failures.append(f"cannot validate generated reports: {exc}")
        return
    if actual_coverage != expected_coverage:
        failures.append("incident-coverage.json is stale; regenerate it")
    if actual_source_map != expected_source_map:
        failures.append("source-map.json is stale; regenerate it")
    if actual_corpus_manifest != expected_corpus_manifest:
        failures.append("corpus-manifest.json is stale; regenerate it")


def check_fixtures(failures: list[str]) -> int:
    checked = 0
    for folder in ("manychat", "provider"):
        for path in sorted((CHARACTERIZATION / "fixtures" / folder).glob("*.json")):
            try:
                fixture = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                failures.append(f"invalid fixture {path.relative_to(ROOT)}: {exc}")
                continue
            if fixture.get("schema_version") != 1:
                failures.append(f"fixture schema mismatch: {path.relative_to(ROOT)}")
            if fixture.get("synthetic") is not True:
                failures.append(f"fixture must be explicitly synthetic: {path.relative_to(ROOT)}")
            checked += 1
    if checked < 8:
        failures.append("expected at least eight sanitized ManyChat/provider fixtures")
    return checked


def check_harness_imports(failures: list[str]) -> None:
    for path in sorted(CHARACTERIZATION.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        forbidden = imported.intersection(FORBIDDEN_IMPORTS)
        if forbidden:
            failures.append(
                f"external capability import in {path.relative_to(ROOT)}: {sorted(forbidden)}"
            )


def check_source_references(failures: list[str]) -> dict[str, Any]:
    if not LEGACY.is_dir():
        return {"status": "skipped", "reason": "legacy source unavailable"}
    paths_checked: set[str] = set()
    symbols_checked = 0
    symbol_failures = 0
    for path in scenario_paths(CHARACTERIZATION):
        scenario = json.loads(path.read_text(encoding="utf-8"))
        for reference in scenario["source_refs"]:
            relative = str(reference["path"])
            target = LEGACY / relative
            paths_checked.add(relative)
            if not target.is_file():
                failures.append(f"legacy source reference missing: {relative}")
                continue
            text = target.read_text(encoding="utf-8", errors="ignore")
            if target.suffix != ".py":
                continue
            for symbol in reference["symbols"]:
                symbol_text = str(symbol)
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol_text):
                    continue
                symbols_checked += 1
                if symbol_text not in text:
                    symbol_failures += 1
                    failures.append(f"legacy source symbol missing: {relative}:{symbol_text}")
    return {
        "status": "ok" if symbol_failures == 0 else "failed",
        "paths_checked": len(paths_checked),
        "symbols_checked": symbols_checked,
        "symbol_failures": symbol_failures,
    }


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
            failures.append(f"malformed Phase 1 SHA256SUMS line {line_number}")
            continue
        target = ROOT / relative
        if not target.is_file():
            failures.append(f"Phase 1 hash target missing: {relative}")
            continue
        if sha256(target) != expected:
            failures.append(f"Phase 1 hash mismatch: {relative}")
        checked += 1
    if checked < 10:
        failures.append("Phase 1 SHA256SUMS must cover at least ten artifacts")
    return checked


def main() -> int:
    failures: list[str] = []
    check_required(failures)
    check_git_index(failures)
    replay = check_scenarios(failures)
    check_generated_reports(failures)
    fixtures = check_fixtures(failures)
    check_harness_imports(failures)
    source = check_source_references(failures)
    hashes = check_sums(failures)
    scanned = check_secrets_and_pii(failures)
    links = check_markdown_links(failures)
    summary = {
        "status": "failed" if failures else "ok",
        "phase": "phase-01-incident-characterization",
        "replay": replay,
        "fixtures_checked": fixtures,
        "source_references": source,
        "evidence_hashes_checked": hashes,
        "text_files_scanned": scanned,
        "relative_links_checked": links,
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
