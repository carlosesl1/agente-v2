#!/usr/bin/env python3
"""Independent pre-freeze and terminal validator for Phase 7."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_boundary import CATALOG, __version__
from reservation_boundary.schema import render_postgresql, render_sqlite
from scripts.generate_phase7_manifest import (
    MANIFEST_PATH,
    SHA256SUMS_PATH,
    TERMINAL_ARTIFACTS,
    build_manifest,
    render_sha256sums,
)

EVIDENCE_DIR = ROOT / "docs/refactor/evidence/phase-07"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_REMOTE_JOBS = frozenset(
    {
        "static",
        "full-suite",
        "phase7-properties",
        "phase7-faults",
        "phase7-mutations",
        "gate",
    }
)
EXPECTED_RUNTIME_SOURCE = {
    "runtime_contract_manifest_sha256": "4dbdcedaea80452f9ce6d4f031ef77d15cb06a144b55eb623a513a8ec203f013",
    "source_head": "57408d8b2040399bc25ee7957505208079458884",
    "source_status_entries": 86,
    "source_status_hash": "e299a15f0336646ef62d5e88a4989d46ef46d6865c5d3163e092969fa9a8ef7a",
    "source_tracked_diff_hash": "7f5248f9b98425be3a1ee53985d83af89c7f687e88991f9c30e993394adaae69",
    "source_tree": "67b5fe18d4685281778e41cd61cd584dd063ea60",
    "synthetic_baseline_commit": "3da1f41103fd25bc0c1c0242a7e05cf9da22ad82",
    "synthetic_baseline_tree": "72c19010aaa6e1263384c20315cb7f4d8eeb111e",
}


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _loads_json_strict(payload: str) -> object:
    if type(payload) is not str:
        raise TypeError("payload must be an exact str")
    return json.loads(payload, object_pairs_hook=_reject_duplicate_keys)


def _json(relative: str) -> dict[str, object]:
    payload = _loads_json_strict((ROOT / relative).read_text(encoding="utf-8"))
    if type(payload) is not dict:
        raise TypeError(f"{relative} must contain an object")
    return payload


def _claims_are_closed(payload: dict[str, object]) -> bool:
    return payload.get("rollout") == "NO-GO" and payload.get("phase8_started") is False


def _runtime_source_is_authentic(source: dict[str, object]) -> bool:
    if type(source) is not dict:
        return False
    if source.get("schema_version") != 1 or source.get("source_unchanged") is not True:
        return False
    if source.get("live_capabilities_executed") != []:
        return False
    if any(
        source.get(field) != expected
        for field, expected in EXPECTED_RUNTIME_SOURCE.items()
    ):
        return False
    for field in (
        "source_head",
        "source_tree",
        "synthetic_baseline_commit",
        "synthetic_baseline_tree",
    ):
        value = source.get(field)
        if type(value) is not str or HEX40.fullmatch(value) is None:
            return False
    for field in (
        "runtime_contract_manifest_sha256",
        "source_status_hash",
        "source_tracked_diff_hash",
    ):
        value = source.get(field)
        if type(value) is not str or HEX64.fullmatch(value) is None:
            return False
    entries = source.get("source_status_entries")
    included = source.get("included_paths")
    excluded = source.get("excluded_paths")
    untracked = source.get("untracked_paths")
    if type(entries) is not int or isinstance(entries, bool) or entries < 0:
        return False
    if type(included) is not list or type(excluded) is not list or type(untracked) is not list:
        return False
    if entries != len(included) + len(excluded) + len(untracked):
        return False
    paths: list[str] = []
    for row in included:
        if type(row) is not dict or type(row.get("path")) is not str:
            return False
        if row.get("status") not in {"present", "deleted"}:
            return False
        digest = row.get("sha256")
        if digest is not None and (type(digest) is not str or HEX64.fullmatch(digest) is None):
            return False
        paths.append(row["path"])
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        return False
    if any(type(path) is not str for path in excluded):
        return False
    untracked_paths: list[str] = []
    for row in untracked:
        if type(row) is not dict or row.get("kind") != "file":
            return False
        path = row.get("path")
        digest = row.get("sha256")
        size = row.get("size")
        if type(path) is not str or type(digest) is not str or HEX64.fullmatch(digest) is None:
            return False
        if type(size) is not int or isinstance(size, bool) or size < 0:
            return False
        untracked_paths.append(path)
    if untracked_paths != sorted(untracked_paths) or len(untracked_paths) != len(set(untracked_paths)):
        return False
    return True


def _remote_ci_is_authentic(ci: dict[str, object], candidate_commit: str) -> bool:
    if type(ci) is not dict or type(candidate_commit) is not str:
        return False
    if HEX40.fullmatch(candidate_commit) is None or ci.get("head_sha") != candidate_commit:
        return False
    run_id = ci.get("run_id")
    if type(run_id) is not int or isinstance(run_id, bool) or run_id <= 0:
        return False
    if ci.get("status") != "completed" or ci.get("conclusion") != "success":
        return False
    if ci.get("workflow_path") != ".github/workflows/phase7.yml":
        return False
    run_url = ci.get("run_url")
    if type(run_url) is not str:
        return False
    match = re.fullmatch(
        r"https://github\.com/[^/]+/[^/]+/actions/runs/(?P<run_id>[1-9][0-9]*)",
        run_url,
    )
    if match is None or int(match.group("run_id")) != run_id:
        return False
    jobs = ci.get("jobs")
    if type(jobs) is not list or len(jobs) != len(EXPECTED_REMOTE_JOBS):
        return False
    names: set[str] = set()
    for job in jobs:
        if type(job) is not dict:
            return False
        job_id = job.get("id")
        name = job.get("name")
        url = job.get("url")
        if type(job_id) is not int or isinstance(job_id, bool) or job_id <= 0:
            return False
        if type(name) is not str or type(url) is not str:
            return False
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            return False
        if re.fullmatch(re.escape(run_url) + rf"/job/{job_id}", url) is None:
            return False
        names.add(name)
    return names == EXPECTED_REMOTE_JOBS


def _closed_imports() -> bool:
    forbidden = {"requests", "httpx", "socket", "urllib", "subprocess"}
    for path in sorted((ROOT / "reservation_boundary").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden:
                        return False
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".")[0]
                if module in forbidden:
                    return False
    return True


def _terminal_artifact_checks() -> tuple[list[str], list[str]]:
    missing = sorted(name for name in TERMINAL_ARTIFACTS if not (EVIDENCE_DIR / name).is_file())
    failures: list[str] = []
    if missing:
        return missing, failures
    candidate = _json("docs/refactor/evidence/phase-07/candidate.json")
    local = _json("docs/refactor/evidence/phase-07/local-integration-result.json")
    properties = _json("docs/refactor/evidence/phase-07/properties-result.json")
    faults = _json("docs/refactor/evidence/phase-07/faults-result.json")
    mutations = _json("docs/refactor/evidence/phase-07/mutation-result.json")
    review = _json("docs/refactor/evidence/phase-07/review-result.json")
    ci = _json("docs/refactor/evidence/phase-07/ci-result.json")
    candidate_commit = candidate.get("commit")
    if (
        candidate.get("frozen") is not True
        or type(candidate_commit) is not str
        or HEX40.fullmatch(candidate_commit) is None
        or not _claims_are_closed(candidate)
    ):
        failures.append("candidate not frozen")
    if local.get("passed") is not True:
        failures.append("local integration gate failed")
    if properties.get("passed") is not True or properties.get("total") != 20_000:
        failures.append("property gate incomplete")
    if faults.get("passed") is not True or faults.get("restart_schedules") != 2_000 or faults.get("contention_rows") != 200:
        failures.append("fault/restart/contention gate incomplete")
    if mutations.get("passed") is not True or mutations.get("killed") != 12:
        failures.append("mutation gate incomplete")
    if (
        review.get("approved") != 3
        or review.get("rejected") != 0
        or not _claims_are_closed(review)
    ):
        failures.append("review gate incomplete")
    if not _remote_ci_is_authentic(ci, candidate_commit):
        failures.append("remote CI is not green")
    return missing, failures


def validate_phase7(*, terminal: bool) -> dict[str, object]:
    if type(terminal) is not bool:
        raise TypeError("terminal must be an exact bool")
    checks: dict[str, bool] = {}
    expected_manifest = build_manifest()
    manifest_path = ROOT / MANIFEST_PATH
    sums_path = ROOT / SHA256SUMS_PATH
    checks["manifest_current"] = manifest_path.is_file() and json.loads(manifest_path.read_text()) == expected_manifest
    checks["sha256sums_current"] = sums_path.is_file() and sums_path.read_text() == render_sha256sums(expected_manifest)
    checks["package_version"] = __version__ == "0.7.0"
    checks["catalog_count"] = len(CATALOG) == 13
    checks["sqlite_schema_current"] = (
        ROOT / "schemas/phase7/sqlite.sql"
    ).read_text() == render_sqlite()
    checks["postgresql_schema_current"] = (
        ROOT / "schemas/phase7/postgresql.sql"
    ).read_text() == render_postgresql()
    checks["closed_imports"] = _closed_imports()

    runtime = _json("docs/refactor/evidence/phase-07/runtime-integration-manifest.json")
    patch_path = ROOT / str(runtime["patch_path"])
    patch = patch_path.read_bytes() if patch_path.is_file() else b""
    checks["runtime_patch"] = (
        runtime.get("patch_applies") is True
        and hashlib.sha256(patch).hexdigest() == runtime.get("patch_sha256")
        and len(patch) == runtime.get("patch_bytes")
        and runtime.get("live_capabilities_executed") == []
    )
    source = _json("docs/refactor/evidence/phase-07/runtime-source-manifest.json")
    checks["runtime_source"] = _runtime_source_is_authentic(source)
    red = _json("docs/refactor/evidence/phase-07/red-results.json")
    checks["red_coverage"] = {row["task"] for row in red["entries"]} == set(range(1, 17))
    workflow = (ROOT / ".github/workflows/phase7.yml").read_text()
    checks["workflow_scope"] = (
        "- phase7-boundary-migration" in workflow
        and "pull_request:" not in workflow
        and "  - main" not in workflow
    )
    phase = (ROOT / "docs/refactor/phases/phase-07-boundary-migration.md").read_text()
    checks["rollout_closed"] = "rollout=NO-GO" in phase and "phase8_started=false" in phase

    missing, terminal_failures = _terminal_artifact_checks()
    terminal_ready = not missing and not terminal_failures
    failures = sorted(name for name, passed in checks.items() if not passed)
    if terminal:
        failures.extend(terminal_failures)
        failures.extend(f"missing terminal artifact: {name}" for name in missing)
    result = "passed" if not failures else "failed"
    return {
        "checks": checks,
        "failures": failures,
        "live_capabilities_executed": [],
        "missing_terminal_artifacts": missing,
        "phase": 7,
        "phase8_started": False,
        "result": result,
        "rollout": "NO-GO",
        "terminal": terminal,
        "terminal_ready": terminal_ready,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal", action="store_true")
    args = parser.parse_args()
    result = validate_phase7(terminal=args.terminal)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
