#!/usr/bin/env python3
"""Generate and verify deterministic Phase 5 manifests and checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE = "phase-05-durable-command-execution"
EVIDENCE = ROOT / "docs" / "refactor" / "evidence" / "phase-05"
SCHEMA_MANIFEST = EVIDENCE / "schema-manifest.json"
PACKAGE_MANIFEST = EVIDENCE / "package-manifest.json"
SUMS = EVIDENCE / "SHA256SUMS"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(path: Path, *, root: Path = ROOT, **extra: object) -> dict[str, object]:
    return {
        "path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        **extra,
    }


def build_schema_manifest() -> dict[str, object]:
    files = tuple(
        (dialect, ROOT / "schemas" / "phase5" / f"{dialect}.sql")
        for dialect in ("postgresql", "sqlite")
    )
    return {
        "schema_version": 1,
        "phase": PHASE,
        "hash_algorithm": "sha256",
        "postgresql_executed": False,
        "sqlite_executed": True,
        "files": [_entry(path, dialect=dialect) for dialect, path in files],
    }


def build_package_manifest(*, root: Path = ROOT) -> dict[str, object]:
    paths = tuple(sorted((root / "reservation_execution").rglob("*.py")))
    return {
        "schema_version": 1,
        "phase": PHASE,
        "hash_algorithm": "sha256",
        "package": "reservation_execution",
        "python_file_count": len(paths),
        "files": [_entry(path, root=root) for path in paths],
    }


def _operational_contract(root: Path) -> dict[str, object]:
    path = root / "docs/refactor/evidence/phase-05/operational-gate-contract.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if type(payload) is not dict:
        raise ValueError("operational gate contract must be an object")
    return payload


def checksum_paths(*, root: Path = ROOT) -> tuple[Path, ...]:
    evidence = root / "docs/refactor/evidence/phase-05"
    fixed = {
        root / "README.md",
        root / "docs" / "refactor" / "README.md",
        root / "docs" / "refactor" / "evidence" / "README.md",
        root / "docs" / "refactor" / "06-risk-register.md",
        root / "docs" / "refactor" / "phases" / "phase-05-durable-command-execution.md",
        root / "docs" / "superpowers" / "specs" / "2026-07-19-phase-5-durable-command-execution-design.md",
        root / "docs" / "superpowers" / "plans" / "2026-07-19-phase-5-durable-command-execution.md",
        root / ".github" / "workflows" / "phase5.yml",
        root / "schemas" / "phase5" / "sqlite.sql",
        root / "schemas" / "phase5" / "postgresql.sql",
        root / "scripts" / "generate_phase5_schema.py",
        root / "scripts" / "generate_phase5_manifest.py",
        root / "scripts" / "run_phase5_properties.py",
        root / "scripts" / "run_phase5_faults.py",
        root / "scripts" / "run_phase5_mutations.py",
        root / "scripts" / "validate_phase5.py",
        root / "tests" / "phase5_helpers.py",
        root / "tests" / "test_phase5_closeout.py",
    }
    contract_path = evidence / "operational-gate-contract.json"
    if contract_path.is_file():
        contract = _operational_contract(root)
        fixed.update(root / item["path"] for item in contract["mutation_catalog"])
    fixed.update((root / "reservation_execution").rglob("*.py"))
    fixed.update((root / "tests").glob("test_phase5_*.py"))
    if evidence.is_dir():
        fixed.update(
            path
            for path in evidence.rglob("*")
            if path.is_file() and path.name != "SHA256SUMS"
        )
    paths = tuple(sorted(fixed, key=lambda path: str(path.relative_to(root))))
    missing = tuple(str(path.relative_to(root)) for path in paths if not path.is_file())
    if missing:
        raise FileNotFoundError(f"missing checksum targets: {missing}")
    forbidden = tuple(
        str(path.relative_to(root))
        for path in paths
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".log"}
        or path.name.endswith(("-wal", "-shm"))
    )
    if forbidden:
        raise ValueError(f"runtime artifacts cannot be checksummed: {forbidden}")
    return paths


def render_sums(*, root: Path = ROOT) -> str:
    return "".join(
        f"{sha256(path)}  {path.relative_to(root)}\n"
        for path in checksum_paths(root=root)
    )


def _render_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_manifests() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    SCHEMA_MANIFEST.write_text(_render_json(build_schema_manifest()), encoding="utf-8")
    PACKAGE_MANIFEST.write_text(_render_json(build_package_manifest()), encoding="utf-8")
    SUMS.write_text(render_sums(), encoding="utf-8")


def check_manifests() -> tuple[str, ...]:
    expected = {
        SCHEMA_MANIFEST: _render_json(build_schema_manifest()),
        PACKAGE_MANIFEST: _render_json(build_package_manifest()),
        SUMS: render_sums(),
    }
    failures = []
    for path, rendered in expected.items():
        if not path.is_file():
            failures.append(f"missing generated artifact: {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8") != rendered:
            failures.append(f"stale generated artifact: {path.relative_to(ROOT)}")
    return tuple(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write:
        write_manifests()
        failures: tuple[str, ...] = ()
    else:
        failures = check_manifests()
    print(
        json.dumps(
            {
                "phase": PHASE,
                "result": "passed" if not failures else "failed",
                "failures": list(failures),
                "package_manifest": build_package_manifest(),
                "schema_manifest": build_schema_manifest(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
