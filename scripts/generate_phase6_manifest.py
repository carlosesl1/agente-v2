#!/usr/bin/env python3
"""Generate and verify deterministic Phase 6 manifests and checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE = "phase-06-handoff-and-payments"
EVIDENCE_RELATIVE = Path("docs/refactor/evidence/phase-06")
SCHEMA_MANIFEST_NAME = "schema-manifest.json"
PACKAGE_MANIFEST_NAME = "package-manifest.json"
SUMS_NAME = "SHA256SUMS"
_RUNTIME_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log"}
MUTATION_TARGETS = (
    "reservation_followup/handoff.py",
    "reservation_followup/payment.py",
    "reservation_followup/reconciliation.py",
    "reservation_followup/schema.py",
    "reservation_followup/sqlite_store.py",
    "reservation_followup/types.py",
    "reservation_followup/workers.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(path: Path, *, root: Path, **extra: object) -> dict[str, object]:
    return {
        "path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        **extra,
    }


def build_schema_manifest(*, root: Path = ROOT) -> dict[str, object]:
    files = tuple(
        (dialect, root / "schemas" / "phase6" / f"{dialect}.sql")
        for dialect in ("postgresql", "sqlite")
    )
    missing = [str(path.relative_to(root)) for _, path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing schema files: {missing}")
    return {
        "schema_version": 1,
        "phase": PHASE,
        "hash_algorithm": "sha256",
        "postgresql_executed": False,
        "sqlite_executed": True,
        "files": [
            _entry(path, root=root, dialect=dialect) for dialect, path in files
        ],
    }


def build_package_manifest(*, root: Path = ROOT) -> dict[str, object]:
    package = root / "reservation_followup"
    paths = tuple(sorted(package.rglob("*.py"))) if package.is_dir() else ()
    if not paths:
        raise FileNotFoundError("reservation_followup package is absent or empty")
    return {
        "schema_version": 1,
        "phase": PHASE,
        "hash_algorithm": "sha256",
        "package": "reservation_followup",
        "python_file_count": len(paths),
        "files": [_entry(path, root=root) for path in paths],
    }


def _is_runtime_artifact(path: Path) -> bool:
    return (
        path.suffix.lower() in _RUNTIME_SUFFIXES
        or path.name.endswith(("-wal", "-shm"))
        or "__pycache__" in path.parts
        or path.suffix.lower() in {".pyc", ".pyo"}
    )


def checksum_paths(*, root: Path = ROOT) -> tuple[Path, ...]:
    evidence = root / EVIDENCE_RELATIVE
    fixed = {
        root / "README.md",
        root / "docs/refactor/README.md",
        root / "docs/refactor/evidence/README.md",
        root / "docs/refactor/06-risk-register.md",
        root / "docs/refactor/phases/phase-06-handoff-and-payments.md",
        root / "docs/superpowers/specs/2026-07-19-phase-6-handoff-payments-design.md",
        root / "docs/superpowers/plans/2026-07-19-phase-6-handoff-payments.md",
        root / ".github/workflows/phase6.yml",
        root / "schemas/phase6/sqlite.sql",
        root / "schemas/phase6/postgresql.sql",
        root / "scripts/generate_phase6_schema.py",
        root / "scripts/generate_phase6_manifest.py",
        root / "scripts/run_phase6_properties.py",
        root / "scripts/run_phase6_faults.py",
        root / "scripts/run_phase6_mutations.py",
        root / "scripts/validate_phase6.py",
        root / "tests/phase6_helpers.py",
        root / "tests/test_phase6_closeout.py",
    }
    package = root / "reservation_followup"
    tests = root / "tests"
    for relative_text in MUTATION_TARGETS:
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe mutation target: {relative_text}")
        target = root / relative
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"mutation target escapes root: {relative_text}") from exc
        fixed.add(target)
    if package.is_dir():
        fixed.update(package.rglob("*.py"))
    if tests.is_dir():
        fixed.update(tests.rglob("test_phase6_*.py"))
    if evidence.is_dir():
        fixed.update(
            path
            for path in evidence.rglob("*")
            if path.is_file() and path.name != SUMS_NAME
        )
    forbidden = sorted(
        str(path.relative_to(root)) for path in fixed if _is_runtime_artifact(path)
    )
    if forbidden:
        raise ValueError(f"runtime artifacts cannot be checksummed: {forbidden}")
    paths = tuple(sorted(fixed, key=lambda path: str(path.relative_to(root))))
    missing = tuple(str(path.relative_to(root)) for path in paths if not path.is_file())
    if missing:
        raise FileNotFoundError(f"missing checksum targets: {missing}")
    return paths


def render_sums(*, root: Path = ROOT) -> str:
    return "".join(
        f"{sha256(path)}  {path.relative_to(root)}\n"
        for path in checksum_paths(root=root)
    )


def _render_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_manifests(*, root: Path = ROOT) -> None:
    evidence = root / EVIDENCE_RELATIVE
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / SCHEMA_MANIFEST_NAME).write_text(
        _render_json(build_schema_manifest(root=root)), encoding="utf-8"
    )
    (evidence / PACKAGE_MANIFEST_NAME).write_text(
        _render_json(build_package_manifest(root=root)), encoding="utf-8"
    )
    (evidence / SUMS_NAME).write_text(render_sums(root=root), encoding="utf-8")


def check_manifests(*, root: Path = ROOT) -> tuple[str, ...]:
    evidence = root / EVIDENCE_RELATIVE
    expected = {
        evidence / SCHEMA_MANIFEST_NAME: _render_json(
            build_schema_manifest(root=root)
        ),
        evidence / PACKAGE_MANIFEST_NAME: _render_json(
            build_package_manifest(root=root)
        ),
        evidence / SUMS_NAME: render_sums(root=root),
    }
    failures = []
    for path, rendered in expected.items():
        if not path.is_file():
            failures.append(f"missing generated artifact: {path.relative_to(root)}")
        elif path.read_text(encoding="utf-8") != rendered:
            failures.append(f"stale generated artifact: {path.relative_to(root)}")
    return tuple(failures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
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
            sort_keys=True,
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
