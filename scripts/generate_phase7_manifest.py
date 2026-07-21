#!/usr/bin/env python3
"""Generate deterministic Phase 7 package/checksum manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = Path("docs/refactor/evidence/phase-07")
MANIFEST_PATH = EVIDENCE_DIR / "manifest.json"
SHA256SUMS_PATH = EVIDENCE_DIR / "SHA256SUMS"

TERMINAL_ARTIFACTS = frozenset(
    {
        "candidate.json",
        "local-integration-result.json",
        "properties-result.json",
        "faults-result.json",
        "mutation-result.json",
        "review-result.json",
        "ci-result.json",
    }
)


def _paths() -> tuple[str, ...]:
    paths: set[str] = {
        ".github/workflows/phase7.yml",
        "pyproject.toml",
        "README.md",
        "docs/refactor/README.md",
        "docs/refactor/06-risk-register.md",
        "docs/refactor/evidence/README.md",
        "docs/refactor/phases/phase-07-boundary-migration.md",
        "docs/superpowers/plans/2026-07-20-phase-7-boundary-migration.md",
        "docs/superpowers/specs/2026-07-20-phase-7-boundary-migration-design.md",
        "scripts/build_phase7_wheel.py",
        "scripts/capture_phase7_runtime.py",
        "scripts/generate_phase7_manifest.py",
        "scripts/generate_phase7_schema.py",
        "scripts/run_phase7_faults.py",
        "scripts/run_phase7_mutations.py",
        "scripts/run_phase7_properties.py",
        "scripts/validate_phase7.py",
        "tests/phase7_helpers.py",
    }
    for pattern in (
        "reservation_boundary/*.py",
        "schemas/phase7/*",
        "tests/test_phase7_*.py",
        "docs/refactor/evidence/phase-07/*",
        "docs/refactor/evidence/phase-07/**/*",
    ):
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT).as_posix()
            if path.name in {"manifest.json", "SHA256SUMS"} | TERMINAL_ARTIFACTS:
                continue
            paths.add(relative)
    missing = sorted(path for path in paths if not (ROOT / path).is_file())
    if missing:
        raise FileNotFoundError(f"missing Phase 7 manifest inputs: {missing}")
    return tuple(sorted(paths))


def build_manifest() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for relative in _paths():
        payload = (ROOT / relative).read_bytes()
        rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
    aggregate_material = json.dumps(
        rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return {
        "aggregate_sha256": hashlib.sha256(aggregate_material).hexdigest(),
        "file_count": len(rows),
        "files": rows,
        "package_version": "0.7.0",
        "phase": 7,
        "phase8_started": False,
        "rollout": "NO-GO",
        "schema_version": 1,
    }


def render_sha256sums(manifest: dict[str, object]) -> str:
    rows = manifest.get("files")
    if type(rows) is not list:
        raise TypeError("manifest files must be a list")
    return "".join(f"{row['sha256']}  {row['path']}\n" for row in rows)


def _render_manifest(manifest: dict[str, object]) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_outputs() -> dict[str, object]:
    manifest = build_manifest()
    manifest_target = ROOT / MANIFEST_PATH
    sums_target = ROOT / SHA256SUMS_PATH
    manifest_target.parent.mkdir(parents=True, exist_ok=True)
    manifest_target.write_text(_render_manifest(manifest), encoding="utf-8")
    sums_target.write_text(render_sha256sums(manifest), encoding="utf-8")
    return manifest


def check_outputs() -> dict[str, object]:
    manifest = build_manifest()
    expected_manifest = _render_manifest(manifest)
    expected_sums = render_sha256sums(manifest)
    failures: list[str] = []
    for relative, expected in (
        (MANIFEST_PATH, expected_manifest),
        (SHA256SUMS_PATH, expected_sums),
    ):
        target = ROOT / relative
        if not target.is_file():
            failures.append(f"missing generated artifact: {relative}")
        elif target.read_text(encoding="utf-8") != expected:
            failures.append(f"stale generated artifact: {relative}")
    return {"failures": failures, "manifest": manifest, "result": "passed" if not failures else "failed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write:
        manifest = write_outputs()
        print(json.dumps({"file_count": manifest["file_count"], "result": "written"}, sort_keys=True))
        return 0
    result = check_outputs()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
